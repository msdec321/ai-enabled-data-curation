"""Clone the ETL repo from GitLab into the sandbox over SSH.

Replaces the baked /opt/etl snapshot once proven: instead of shipping the ETL inside
the microVM image (and rebuilding the image whenever it changes), the sandbox pulls it
from GitLab at run time with a service-account key.

Credential handling matches query_cdw exactly, which is the whole point of routing it
through the broker rather than letting run_python do it:
  * the private key lives in AWS Secrets Manager, never in the image, repo or Lambda env
  * only GITLAB_SECRET_ARN reaches the Lambda; the value is fetched per call, uncached
  * it is injected into the ephemeral VM as an env var, never embedded in a code body
    (a code body would travel through the gateway and into logs)
  * the runner writes it 0600, clones, and removes it in a finally block; the VM is
    per-run and torn down regardless

Host key is PINNED. gitlab_known_hosts ships beside this module (a public host key is
not a secret) and is injected alongside, with StrictHostKeyChecking=yes. The tempting
shortcut -- StrictHostKeyChecking=no -- would turn a man-in-the-middle into a silent
success, which matters more than usual here because we are handing a private key to a
VM that runs model-written code. Fingerprint confirmed out-of-band with the GitLab
team: SHA256:ndHkUL/QNyX5dp3qIgMpltHqCuJlRL2BgCnCBCeQ4Jk (ED25519).

Only the ed25519 host key is pinned. The server also offers ssh-rsa (2048-bit, SHA-1
signatures) and ecdsa; the sandbox client is OpenSSH 8.7, which disables ssh-rsa by
default anyway. One modern key beats carrying three.
"""
import json
import os
from pathlib import Path

import sandbox_client
import vault

# The SYNTHETIC ETL repo -- NOT big-arc/clinical-data-warehouse/cdw. The production
# repo's SQL does not match what the synthetic warehouse runs: object references are
# localized (CDWStaging.* vs Clarity.*, 131 of which do not resolve here) and the
# synthetic ETL additionally carries the deliberately injected defects. Pointing the
# agent at production would have it reading code that contradicts the database it is
# profiling, producing findings that are artifacts of the mismatch.
#
# Set GITLAB_ETL_REPO on the Lambda; no default, so a misconfiguration fails loudly
# rather than silently cloning the wrong repo.
DEFAULT_REPO = os.environ.get("GITLAB_ETL_REPO", "")
DEFAULT_REF = os.environ.get("GITLAB_ETL_REF", "main")
CLONE_DIR = "/tmp/etl-repo"

_KNOWN_HOSTS = (Path(__file__).resolve().parent / "gitlab_known_hosts")

# Shared bootstrap, embedded by BOTH this module's runner and etl_sandbox.py's, so the
# credential handling exists in exactly one place. Defines ensure_clone(), which is a
# no-op when the repo is already present -- the sandbox is one VM per run, so the clone
# happens once per instance and every later ETL tool call reuses it.
BOOTSTRAP = r'''
import json, os, shutil, subprocess

KEY = "/root/.ssh/autodqa_gitlab"
KH  = "/root/.ssh/autodqa_known_hosts"

def _sh(cmd, **kw):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=300, **kw)
    return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()

def ensure_clone(dest):
    """Clone the ETL repo if it is not already in this VM. Returns a status dict."""
    if os.path.isdir(os.path.join(dest, ".git")):
        return {"cloned": False, "reused": True}
    st = {"cloned": True, "reused": False}
    try:
        os.makedirs("/root/.ssh", mode=0o700, exist_ok=True)
        # Restrictive perms BEFORE any key material lands in the file.
        fd = os.open(KEY, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(os.environ["GITLAB_KEY"].rstrip("\n") + "\n")
        with open(KH, "w") as f:
            f.write(os.environ["GITLAB_KNOWN_HOSTS"].rstrip("\n") + "\n")
        os.chmod(KH, 0o644)
        ssh_cmd = ("ssh -i %s -o UserKnownHostsFile=%s -o StrictHostKeyChecking=yes "
                   "-o IdentitiesOnly=yes -o PasswordAuthentication=no "
                   "-o BatchMode=yes -o ConnectTimeout=20" % (KEY, KH))
        env = dict(os.environ, GIT_SSH_COMMAND=ssh_cmd, GIT_TERMINAL_PROMPT="0")
        if os.path.isdir(dest):
            shutil.rmtree(dest)
        rc, so, se = _sh(["git", "clone", "--depth", "1", "--branch", os.environ["GIT_REF"],
                          os.environ["GIT_REPO"], dest], env=env)
        st["clone_rc"] = rc
        if rc != 0:
            st["error"] = (se or so)[:600]
        else:
            _, head, _ = _sh(["git", "-C", dest, "log", "-1", "--pretty=%H %ci %s"], env=env)
            st["head"] = head
    except Exception as e:
        st["error"] = "%s: %s" % (type(e).__name__, e)
    finally:
        # The VM is per-run and torn down anyway, but do not leave the key on disk
        # beyond the clone that needs it.
        try:
            os.remove(KEY); st["key_removed"] = True
        except Exception:
            st["key_removed"] = False
    return st
'''

# Explicit-clone runner: bootstrap, then report. Used by the (currently internal)
# clone_etl tool and for diagnosing the SSH path; the ETL read tools bootstrap the same
# way via etl_sandbox.py, so a normal run never needs this.
_RUNNER = BOOTSTRAP + r'''
DEST = os.environ["CLONE_DIR"]
out = {}
try:
    # Identity check first. GitLab answers `ssh -T git@host` with
    # "Welcome to GitLab, @user!" when the key is recognised, which cleanly separates
    # "key rejected" from "key fine, no access to that project" -- two failures that
    # otherwise both surface as an opaque clone error.
    if os.environ.get("GITLAB_KEY"):
        os.makedirs("/root/.ssh", mode=0o700, exist_ok=True)
        fd = os.open(KEY, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(os.environ["GITLAB_KEY"].rstrip("\n") + "\n")
        with open(KH, "w") as f:
            f.write(os.environ["GITLAB_KNOWN_HOSTS"].rstrip("\n") + "\n")
        os.chmod(KH, 0o644)
        ssh_cmd = ("ssh -i %s -o UserKnownHostsFile=%s -o StrictHostKeyChecking=yes "
                   "-o IdentitiesOnly=yes -o PasswordAuthentication=no "
                   "-o BatchMode=yes -o ConnectTimeout=20" % (KEY, KH))
        host = os.environ["GIT_REPO"].split("@", 1)[1].split(":", 1)[0]
        _, so_a, se_a = _sh(ssh_cmd.split() + ["-T", "git@" + host])
        greeting = (se_a or so_a).strip()
        out["ssh_auth"] = greeting[:200]
        out["ssh_authenticated"] = "Welcome to GitLab" in greeting

    if os.path.isdir(DEST):
        shutil.rmtree(DEST)                 # explicit clone always takes a fresh copy
    out.update(ensure_clone(DEST))
    if not out.get("error"):
        n = 0
        for root, dirs, files in os.walk(DEST):
            if ".git" in dirs:
                dirs.remove(".git")
            n += len(files)
        out["files"] = n
        out["dest"] = DEST
except Exception as e:
    out["error"] = "%s: %s" % (type(e).__name__, e)
print(json.dumps(out, default=str))
'''


class GitLabError(Exception):
    """The clone could not be performed."""


def clone(session: str = "etl", repo: str = None, ref: str = None) -> dict:
    """Fetch the key from the vault and clone the ETL repo inside the run's sandbox."""
    arn = os.environ.get("GITLAB_SECRET_ARN", "")
    if not arn:
        raise GitLabError("GITLAB_SECRET_ARN not set on the Lambda — no GitLab key was "
                          "provisioned (see ensure_gitlab_secret in setup_gateway.py)")
    try:
        key = vault.default().fetch({"vault": "aws_sm", "id": arn, "key": "private_key"})
    except Exception as e:
        raise GitLabError(f"vault fetch failed: {type(e).__name__}: {e}")
    target = repo or DEFAULT_REPO
    if not target:
        raise GitLabError("GITLAB_ETL_REPO not set on the Lambda — point it at the "
                          "SYNTHETIC ETL repo (not the production cdw repo)")
    if not _KNOWN_HOSTS.is_file():
        raise GitLabError(f"pinned host key missing: {_KNOWN_HOSTS.name} must ship with the Lambda")

    data = sandbox_client.run_in_sandbox(_RUNNER, session=session, env={
        "GITLAB_KEY": key,
        "GITLAB_KNOWN_HOSTS": _KNOWN_HOSTS.read_text(),
        "GIT_REPO": target,
        "GIT_REF": ref or DEFAULT_REF,
        "CLONE_DIR": CLONE_DIR,
    })
    if data.get("error"):
        e = data["error"]
        raise GitLabError(f"sandbox: {e if not isinstance(e, dict) else e.get('message') or e}")
    out = (data.get("stdout") or "").strip()
    if not out:
        raise GitLabError(f"no output from sandbox (stderr: {(data.get('stderr') or '')[:300]})")
    try:
        return json.loads(out)
    except ValueError:
        raise GitLabError(f"unexpected sandbox output: {out[:300]}")
