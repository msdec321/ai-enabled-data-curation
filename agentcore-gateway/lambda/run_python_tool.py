"""Lambda target for the AgentCore Gateway: implements the run_python tool.

The Gateway invokes this function for tools/call requests routed to the
"sandbox" target. The tool name arrives in the Lambda client context as
"<targetName>___<toolName>"; the tool arguments arrive as the event payload.

Broker behaviour: the handler holds NO tool credentials of its own. Per call it
(1) resolves the credential the (tool, dataset) pair is allowed to use via the
registry, then (2) fetches that credential from the vault (AWS Secrets Manager,
reached through this Lambda's IAM role). Only then does it call the Cloudflare
sandbox worker (dqa-sandbox-runner) with the fetched bearer secret.

Env vars (set by setup_gateway.py):
  SANDBOX_WORKER_URL   https://dqa-sandbox-runner.<account>.workers.dev
  SANDBOX_SECRET_ARN   ARN of the Secrets Manager secret holding the bearer token
  AWS_REGION           set automatically by the Lambda runtime
"""
import json
import os
import urllib.error
import urllib.request

import registry
import vault

SANDBOX_URL = os.environ["SANDBOX_WORKER_URL"]
_vault = vault.AwsSecretsManagerVault(os.environ.get("AWS_REGION", "us-east-1"))


def _tool_name(context) -> str:
    ctx = getattr(context, "client_context", None)
    custom = getattr(ctx, "custom", None) or {}
    return custom.get("bedrockAgentCoreToolName", "").split("___")[-1]


def handler(event, context):
    tool = _tool_name(context)
    if tool != "run_python":
        return {"error": f"unknown tool: {tool!r}"}

    # Broker steps: which credential may this (tool, dataset) use, then fetch it.
    try:
        ref = registry.resolve(tool, event.get("dataset"))
        sandbox_secret = _vault.fetch(ref)
    except registry.NotAuthorized as e:
        return {"error": f"not authorized: {e}"}
    except Exception as e:
        return {"error": f"vault fetch failed: {type(e).__name__}: {e}"}

    body = json.dumps({
        "code": event.get("code", ""),
        "language": "python",
        "session": event.get("session", "agentcore"),
    }).encode()
    req = urllib.request.Request(
        SANDBOX_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {sandbox_secret}",
            "Content-Type": "application/json",
            # Cloudflare's Browser Integrity Check 403s the default
            # Python-urllib UA (error 1010) — send an identifiable one.
            "User-Agent": "autodqa-gateway-lambda/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=110) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": f"sandbox HTTP {e.code}: {e.read().decode(errors='replace')[:300]}"}
    except urllib.error.URLError as e:
        return {"error": f"sandbox unreachable: {e.reason}"}

    if data.get("error"):
        e = data["error"]
        if isinstance(e, dict):
            return f"Error: {e.get('name')}: {e.get('value') or e.get('message')}"
        return f"Error: {e}"
    texts = [r["text"] for r in data.get("results", []) if r.get("text")]
    return "\n".join(filter(None, [data.get("stdout", ""), *texts])).strip() or "(no output)"
