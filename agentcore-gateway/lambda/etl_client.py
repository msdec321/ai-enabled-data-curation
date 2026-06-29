"""Access layer for the ETL bridge — a read-only file server over the ETL repo,
published from the trusted side through an ngrok tunnel (etl-bridge/etl_server.py).
Shared by list_etl, read_etl, grep_etl.

Like query_cdw, the outbound request runs INSIDE the sandbox container, not the
Lambda — so every tool executes in the run's ephemeral container (uniform trust
model). The broker authorizes the call and fetches the bridge token from the vault
here; the token is injected as a sandbox env var (never embedded in the code body)
and the sandbox makes the GET.
"""
import json
import urllib.parse

import registry
import sandbox_client
import vault

# Pure-stdlib; runs INSIDE the sandbox. Reads URL + token from env (injected),
# GETs the bridge, prints a single JSON envelope to stdout.
_RUNNER = r"""
import json, os, urllib.request, urllib.error
req = urllib.request.Request(os.environ["ETL_URL"], headers={
    "Authorization": "Bearer " + os.environ["ETL_TOKEN"],
    # ngrok free tier shows an HTML interstitial to browser-like requests.
    "ngrok-skip-browser-warning": "true",
    "User-Agent": "autodqa-sandbox/1.0",
})
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        print(json.dumps({"ok": True, "status": getattr(r, "status", 200),
                          "body": r.read().decode("utf-8", "replace")}))
except urllib.error.HTTPError as e:
    print(json.dumps({"ok": False, "status": e.code,
                      "body": e.read().decode("utf-8", "replace")[:300]}))
except urllib.error.URLError as e:
    print(json.dumps({"ok": False, "status": 0, "body": str(e.reason)}))
"""


class EtlBridgeError(Exception):
    """The ETL bridge was unreachable or returned a non-2xx response."""


def call(tool: str, path: str, params: dict | None = None, session: str = "etl") -> str:
    """Authorize an ETL tool (deny-by-default), fetch the bridge token from the
    vault, then GET the endpoint FROM THE SANDBOX (URL + token injected as env).
    Returns the response body text; raises registry.NotAuthorized or EtlBridgeError."""
    grant = registry.authorize(tool, "ETL")
    base = grant["connection"]["base_url"]
    if not base or base.startswith("<"):
        raise EtlBridgeError("ETL_BRIDGE_URL not set on the Lambda (start the bridge + tunnel)")
    token = vault.default().fetch(grant["credential"])
    url = base.rstrip("/") + path
    if params:
        url += "?" + urllib.parse.urlencode(params)

    data = sandbox_client.run_in_sandbox(_RUNNER, session=session,
                                         env={"ETL_URL": url, "ETL_TOKEN": token})
    if data.get("error"):
        e = data["error"]
        raise EtlBridgeError(f"sandbox: {e if not isinstance(e, dict) else e.get('message') or e}")
    out = (data.get("stdout") or "").strip()
    if not out:
        raise EtlBridgeError(f"no output from sandbox (stderr: {(data.get('stderr') or '')[:200]})")
    try:
        resp = json.loads(out)
    except ValueError:
        raise EtlBridgeError(f"unexpected sandbox output: {out[:200]}")
    if not resp.get("ok"):
        raise EtlBridgeError(f"HTTP {resp.get('status')}: {str(resp.get('body'))[:300]}")
    return resp["body"]
