"""Runs sandbox code in a per-session AWS Lambda MicroVM — the AWS-native
replacement for the Cloudflare sandbox-worker. Shared by every tool that executes
code (run_python, query_cdw).

run_in_sandbox launches — or reuses — ONE microVM per run `session`, then POSTs
{code, env} to its HTTPS endpoint; the server (../../sandbox-microvm/server.py) runs
the Python with `env` injected as os.environ and returns {stdout, stderr, results,
error}. pytds is baked into the image, so there is NO runtime install to wait on
(the cold-start that forced the old timeout bump + serialization lock). destroy_sandbox
terminates the VM.

Per-session keying uses run-microvm's clientToken: the same token returns the same
microVM on repeat calls (idempotent, server-side), so concurrent tool calls in a run
share one VM — no tag scan, no state table, and no race to create two.

Requires on the broker Lambda (wired in setup_gateway.py):
  - env MICROVM_IMAGE_ARN (from sandbox-microvm/.microvm_config.json),
  - IAM lambda-microvms perms (RunMicrovm, GetMicrovm, CreateMicrovmAuthToken,
    TerminateMicrovm) on the Lambda role,
  - a boto3 new enough to know the lambda-microvms service (bundle it — the managed
    Lambda runtime's boto3 may predate the service).
"""
import json
import os
import time
import urllib.error
import urllib.request

import boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")
IMAGE_ARN = os.environ["MICROVM_IMAGE_ARN"]
_NC = f"arn:aws:lambda:{REGION}:aws:network-connector:aws-network-connector:"
INGRESS = [_NC + "ALL_INGRESS"]
# Egress: comma-separated connector ARNs via env (setup_vpc_egress.py sets a
# customer VPC connector so the sandbox can reach the institutional network);
# default is the AWS-managed public-internet connector (ngrok-tunnel era).
EGRESS = ([a.strip() for a in os.environ["MICROVM_EGRESS_CONNECTORS"].split(",") if a.strip()]
          if os.environ.get("MICROVM_EGRESS_CONNECTORS") else [_NC + "INTERNET_EGRESS"])
IDLE_POLICY = {"autoResumeEnabled": True, "maxIdleDurationSeconds": 900,
               "suspendedDurationSeconds": 300}

_mv = boto3.client("lambda-microvms", region_name=REGION)


class MicrovmGone(Exception):
    """Idempotent replay rejected: the session token's previous VM is dead and
    run_microvm throws instead of returning it with a dead state."""


def _run_microvm(client_token: str):
    try:
        r = _mv.run_microvm(imageIdentifier=IMAGE_ARN, clientToken=client_token,
                            ingressNetworkConnectors=INGRESS, egressNetworkConnectors=EGRESS,
                            idlePolicy=IDLE_POLICY)
    except _mv.exceptions.ValidationException as e:
        # Replaying the token of an already-failed/terminated VM can be refused
        # at the API ("MicroVM microvm-... failed ...") rather than handed back
        # as a dead-state VM (the case _get_or_launch already guards).
        if "microvm-" in str(e) and "failed" in str(e).lower():
            raise MicrovmGone(str(e)) from e
        raise
    return r["microvmId"], r["endpoint"], r["state"], r.get("stateReason")


def _get_or_launch(session: str, wait: bool = True, timeout: int = 60):
    """Idempotent per-session VM: clientToken=session -> the same VM on repeat calls."""
    try:
        mid, endpoint, state, reason = _run_microvm(session)
    except MicrovmGone:
        if not wait:
            raise  # teardown of an already-dead VM: nothing to terminate
        mid, endpoint, state, reason = _run_microvm(f"{session}-{int(time.time())}")
    # Idempotency can hand back a VM from an earlier use of this session that has since
    # idle-terminated (idlePolicy: suspend at 900s, terminate 300s later). A dead VM
    # can't be revived, so relaunch under a fresh token — a brand-new token always
    # cold-starts a live VM. Only when we actually need it running (wait): teardown
    # passes wait=False and must NOT resurrect a VM just to terminate it. (Real runs
    # use a unique per-run session, so this only fires for reused sessions.)
    if wait and state in ("TERMINATED", "FAILED"):
        mid, endpoint, state, reason = _run_microvm(f"{session}-{int(time.time())}")
    deadline = time.time() + timeout
    while wait and state != "RUNNING":
        if state in ("TERMINATED", "FAILED"):
            raise RuntimeError(f"microvm {state}: {reason}")
        if time.time() > deadline:
            raise TimeoutError("microvm did not reach RUNNING")
        time.sleep(2)
        state = _mv.get_microvm(microvmIdentifier=mid)["state"]
    return mid, endpoint


def run_in_sandbox(code: str, session: str, env: dict = None) -> dict:
    """Launch/reuse the session's microVM, run `code`, return its JSON result."""
    try:
        mid, endpoint = _get_or_launch(session)
    except Exception as e:
        return {"error": f"microvm launch failed: {type(e).__name__}: {e}"}

    # Short-lived auth token; the map is {header-name: value} -> spread as headers.
    auth = _mv.create_microvm_auth_token(microvmIdentifier=mid, expirationInMinutes=15,
                                         allowedPorts=[{"allPorts": {}}])["authToken"]
    payload = {"code": code, "language": "python"}
    if env:
        payload["env"] = env
    req = urllib.request.Request(
        f"https://{endpoint}/", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **auth})
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": f"sandbox HTTP {e.code}: {e.read().decode(errors='replace')[:300]}"}
    except urllib.error.URLError as e:
        return {"error": f"sandbox unreachable: {e.reason}"}


def destroy_sandbox(session: str) -> dict:
    """Terminate the run's microVM (ephemeral teardown)."""
    try:
        mid, _ = _get_or_launch(session, wait=False)
        _mv.terminate_microvm(microvmIdentifier=mid)
        return {"ok": True}
    except MicrovmGone:
        return {"ok": True, "note": "microvm already gone"}
    except Exception as e:
        return {"error": f"terminate failed: {type(e).__name__}: {e}"}
