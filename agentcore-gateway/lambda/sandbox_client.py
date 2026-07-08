"""Calls the Cloudflare sandbox worker (dqa-sandbox-runner). Shared by every
tool that executes code in the sandbox (run_python, query_cdw).

The sandbox bearer secret is *transport* infrastructure — how the broker reaches
the sandbox backend — so it's fetched here from the vault, not via the dataset
registry. `env` is set as os.environ inside the sandbox run (the worker injects
it via a prelude), so secrets like a DB password reach the code without being
embedded in the code body.
"""
import json
import os
import urllib.error
import urllib.request

import vault

SANDBOX_URL = os.environ["SANDBOX_WORKER_URL"]
_BEARER_REF = {"vault": "aws_sm", "id": os.environ["SANDBOX_SECRET_ARN"], "key": "shared_secret"}


def _post(payload: dict, timeout: int) -> dict:
    bearer = vault.default().fetch(_BEARER_REF)
    req = urllib.request.Request(
        SANDBOX_URL,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {bearer}",
            "Content-Type": "application/json",
            # Cloudflare's Browser Integrity Check 403s the default urllib UA.
            "User-Agent": "autodqa-gateway-lambda/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": f"sandbox HTTP {e.code}: {e.read().decode(errors='replace')[:300]}"}
    except urllib.error.URLError as e:
        return {"error": f"sandbox unreachable: {e.reason}"}


def run_in_sandbox(code: str, session: str, env: dict | None = None) -> dict:
    """POST code to the sandbox; return the parsed JSON response, or {'error': ...}."""
    payload = {"code": code, "language": "python", "session": session}
    if env:
        payload["env"] = env
    # Headroom for a cold sandbox container: first run pip-installs python-tds
    # (~20s) on top of container cold-start. Kept under the Lambda's own timeout.
    # (Removed once the sandbox bakes in its deps / we move to AWS MicroVMs.)
    return _post(payload, timeout=240)


def destroy_sandbox(session: str) -> dict:
    """Destroy the container for a run's session (ephemeral teardown)."""
    return _post({"action": "destroy", "session": session}, timeout=30)
