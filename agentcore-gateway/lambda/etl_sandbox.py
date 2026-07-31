"""Access layer for the ETL source, cloned from GitLab into the sandbox.

Replaces two earlier designs: the ngrok-published bridge (etl_client.py, retired) and
the snapshot baked into the microVM image at /opt/etl (also retired). The ETL is now
cloned from the SYNTHETIC ETL repo on first use in a run and read from /tmp/etl-repo.

Why the clone beats the baked snapshot: the image captured the ETL in its Firecracker
snapshot, so every ETL change meant a full cloud image rebuild before the served source
matched what the database actually executes. Miss that step and an ETL-origin defect
becomes unattributable -- the agent reads code that does not contain it. With the clone,
updating the ETL is a git push.

The clone happens ONCE PER SANDBOX INSTANCE. The sandbox is one VM per run, so the
first ETL tool call bootstraps it (~1s) and every later call in that run reuses it;
ensure_clone() is a no-op when .git is already present.

The uniform trust model is preserved: like query_cdw, the work still runs INSIDE the
sandbox rather than in the Lambda. Only the transport changed (filesystem instead of
HTTP), not where execution happens.

Tool contracts are unchanged across all three designs, so the agent's prompt never
moved.

Safety logic is ported verbatim from etl-bridge/etl_server.py rather than dropped. It
matters more now than it did: the source is a real git repo rather than a generated
tree, so the path jail, suffix allowlist and secret denylist are what stop a checked-in
credential or a traversal path from being served. Note DENY_DIRS also excludes .git, so
the agent cannot read packed refs or objects to reconstruct history the --depth 1 clone
deliberately withheld.
"""
import json
import os
from pathlib import Path

import gitlab_clone
import sandbox_client
import vault

# Where the repo is cloned inside the sandbox (see gitlab_clone.CLONE_DIR).
ETL_ROOT = "/tmp/etl-repo"

# Pure-stdlib; runs INSIDE the sandbox. Bootstraps the clone, then reads the operation
# and its arguments from env (injected, never embedded in the code body), walks the
# cloned repo, and prints one JSON envelope to stdout.
_RUNNER = gitlab_clone.BOOTSTRAP + r'''
import fnmatch, re
from pathlib import Path

# Clone on first use in this VM; a no-op on every later call in the same run.
_boot = ensure_clone(os.environ["ETL_ROOT"])

ROOT = Path(os.environ["ETL_ROOT"]).resolve()

# --- ported verbatim from etl-bridge/etl_server.py ---------------------------
ALLOWED_SUFFIXES = {".sql", ".txt", ".md", ".json", ".yaml", ".yml"}
DENY_DIRS = {"datavant", ".git"}
DENY_GLOBS = [
    "*load_hash_token*", "*hash_token*", "*token*",
    "*.env", "*secret*", "*password*", "*cred*",
    "*.pem", "*.pfx", "*.key", "*.p12", "*.pgp", "*.gpg",
]
MAX_READ_BYTES = 1_000_000
MAX_GREP_MATCHES = 200
MAX_LINE_LEN = 300


def is_served(rel):
    parts = rel.split("/")
    if any(p in DENY_DIRS for p in parts[:-1]):
        return False
    name = parts[-1].lower()
    if any(fnmatch.fnmatch(name, g) for g in DENY_GLOBS):
        return False
    return Path(name).suffix in ALLOWED_SUFFIXES


def resolve(rel):
    """Map a client path to a real file inside ROOT, or None if it escapes the jail."""
    try:
        target = (ROOT / rel).resolve()
    except Exception:
        return None
    if target != ROOT and ROOT not in target.parents:
        return None                      # escaped (resolve() follows symlinks)
    relposix = target.relative_to(ROOT).as_posix()
    return target if is_served(relposix) else None


def walk():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in DENY_DIRS]
        for fn in filenames:
            full = Path(dirpath) / fn
            rel = full.relative_to(ROOT).as_posix()
            if is_served(rel):
                yield full, rel


def op_list():
    files = [{"path": rel, "size": full.stat().st_size} for full, rel in walk()]
    files.sort(key=lambda f: f["path"])
    return {"root": str(ROOT), "count": len(files), "files": files}


def op_read():
    """Whole file, or a line window when ETL_START_LINE / ETL_MAX_LINES are set.

    The line-window form backs read_etl_file, whose contract (path, start_line,
    max_lines -> total_lines/lines_returned/truncated) predates this reader and is
    preserved so the tool schema and the agent's prompt do not change.
    """
    rel = os.environ.get("ETL_PATH", "")
    if not rel:
        return {"error": "no path provided"}
    target = resolve(rel)
    if target is None or not target.is_file():
        return {"error": "file not found or not served: " + rel}
    data = target.read_bytes()
    text = data[:MAX_READ_BYTES].decode("utf-8", "replace")
    out = {"path": target.relative_to(ROOT).as_posix(), "bytes": len(data)}

    if os.environ.get("ETL_START_LINE") is None and os.environ.get("ETL_MAX_LINES") is None:
        out["truncated"] = len(data) > MAX_READ_BYTES
        out["content"] = text
        return out

    lines = text.splitlines(keepends=True)
    start = max(0, int(os.environ.get("ETL_START_LINE") or 0))
    nmax = int(os.environ.get("ETL_MAX_LINES") or 400)
    sel = lines[start:start + nmax]
    out.update({"total_lines": len(lines), "start_line": start,
                "lines_returned": len(sel),
                "truncated": start + nmax < len(lines),
                "content": "".join(sel)})
    return out


def op_grep():
    q = os.environ.get("ETL_Q", "")
    if not q:
        return {"error": "no search pattern provided"}
    cap = int(os.environ.get("ETL_MAX") or MAX_GREP_MATCHES)
    cap = max(1, min(cap, MAX_GREP_MATCHES))
    if os.environ.get("ETL_REGEX"):
        try:
            rx = re.compile(q, re.I)
        except re.error as e:
            return {"error": "bad regex: " + str(e)}
        hit = lambda line: rx.search(line) is not None
    else:
        needle = q.lower()
        hit = lambda line: needle in line.lower()
    matches, truncated = [], False
    for full, rel in walk():
        try:
            lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        for i, line in enumerate(lines, start=1):
            if hit(line):
                matches.append({"path": rel, "line": i, "text": line.strip()[:MAX_LINE_LEN]})
                if len(matches) >= cap:
                    truncated = True
                    break
        if truncated:
            break
    return {"query": q, "count": len(matches), "truncated": truncated, "matches": matches}


OPS = {"list": op_list, "read": op_read, "grep": op_grep}
op = os.environ.get("ETL_OP", "list")
try:
    if _boot.get("error"):
        out = {"error": "could not clone the ETL repo: " + str(_boot["error"])}
    elif not ROOT.is_dir():
        out = {"error": "ETL repo not present at " + str(ROOT) + " after clone"}
    else:
        out = OPS.get(op, lambda: {"error": "unknown op: " + op})()
except Exception as e:
    out = {"error": type(e).__name__ + ": " + str(e)}
if isinstance(out, dict):
    out["_clone"] = {k: v for k, v in _boot.items() if k in ("cloned", "reused", "head")}
print(json.dumps(out, default=str))
'''


class EtlSandboxError(Exception):
    """The sandbox could not serve the ETL snapshot."""


def call(op: str, env: dict | None = None, session: str = "etl") -> dict:
    """Run an ETL filesystem operation inside the run's sandbox and return its JSON.

    No registry authorization: the ETL is synthetic source, not warehouse data. There
    IS a vault lookup, unlike the retired baked-image design -- the GitLab key is
    fetched per call and injected as env so the first ETL tool call in a run can
    bootstrap the clone. Later calls in the same run still carry it (the runner is
    stateless about which call is first) but ensure_clone() short-circuits.

    Raises EtlSandboxError if the sandbox could not be reached or the clone failed.
    """
    arn = os.environ.get("GITLAB_SECRET_ARN", "")
    if not arn:
        raise EtlSandboxError("GITLAB_SECRET_ARN not set — the ETL now comes from GitLab; "
                              "provision the key via setup_gateway.py")
    if not gitlab_clone.DEFAULT_REPO:
        raise EtlSandboxError("GITLAB_ETL_REPO not set — point it at the SYNTHETIC ETL repo")
    try:
        key = vault.default().fetch({"vault": "aws_sm", "id": arn, "key": "private_key"})
    except Exception as e:
        raise EtlSandboxError(f"vault fetch failed: {type(e).__name__}: {e}")

    payload = {
        "ETL_OP": op,
        "ETL_ROOT": ETL_ROOT,
        # Credential travels as env, never in the code body (a code body would pass
        # through the gateway and into logs). Same contract as query_cdw.
        "GITLAB_KEY": key,
        "GITLAB_KNOWN_HOSTS": (Path(__file__).resolve().parent / "gitlab_known_hosts").read_text(),
        "GIT_REPO": gitlab_clone.DEFAULT_REPO,
        "GIT_REF": gitlab_clone.DEFAULT_REF,
    }
    payload.update({k: str(v) for k, v in (env or {}).items() if v is not None})
    data = sandbox_client.run_in_sandbox(_RUNNER, session=session, env=payload)
    if data.get("error"):
        e = data["error"]
        raise EtlSandboxError(f"sandbox: {e if not isinstance(e, dict) else e.get('message') or e}")
    out = (data.get("stdout") or "").strip()
    if not out:
        raise EtlSandboxError(f"no output from sandbox (stderr: {(data.get('stderr') or '')[:200]})")
    try:
        return json.loads(out)
    except ValueError:
        raise EtlSandboxError(f"unexpected sandbox output: {out[:200]}")
