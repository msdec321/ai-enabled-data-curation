#!/usr/bin/env python3
"""Regenerate agentcore-runtime/.env from a freshly-provisioned gateway's
agentcore-gateway/.gateway_config.json, PRESERVING the Cloudflare AI Gateway
settings + token (CF_*) and MODEL_ID from the existing .env.

Run after setup_gateway.py — on an account migration, or after any gateway
redeploy that mints a new Cognito client. The CF_* values are Cloudflare-side
and account-independent, so they carry across accounts unchanged; only the
AgentCore gateway URL + Cognito OAuth values change.

    ../.venv/bin/python sync_runtime_env.py     # run from agentcore-runtime/
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
ENV = HERE / ".env"
GW = REPO / "agentcore-gateway" / ".gateway_config.json"

DEFAULT_MODEL = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"


def parse_env(path):
    d = {}
    if path.exists():
        for line in path.read_text().splitlines():
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k, _, v = s.partition("=")
                d[k.strip()] = v.strip()
    return d


old = parse_env(ENV)
missing = [k for k in ("CF_ACCOUNT_ID", "CF_AIG_GATEWAY", "CF_AIG_TOKEN") if not old.get(k)]
if missing:
    raise SystemExit(
        f"missing {missing} in {ENV} — the Cloudflare AI Gateway values must already "
        f"be present to preserve them. Set them (reuse the data-curation-gateway token) first.")

cfg = json.loads(GW.read_text())
c = cfg["cognito"]
url = cfg["gateway_url"]
if not url.rstrip("/").endswith("/mcp"):
    url = url.rstrip("/") + "/mcp"
region = cfg.get("region", "us-east-1")

ENV.write_text("\n".join([
    "# agentcore-runtime env — CF_* preserved; AGENTCORE_*/COGNITO_* synced from",
    "# agentcore-gateway/.gateway_config.json by sync_runtime_env.py. Gitignored.",
    "",
    "# --- Bedrock via Cloudflare AI Gateway (BYOK; account-independent) ---",
    f"CF_ACCOUNT_ID={old['CF_ACCOUNT_ID']}",
    f"CF_AIG_GATEWAY={old['CF_AIG_GATEWAY']}",
    f"AWS_REGION={region}",
    f"MODEL_ID={old.get('MODEL_ID', DEFAULT_MODEL)}",
    f"CF_AIG_TOKEN={old['CF_AIG_TOKEN']}",
    "",
    "# --- AgentCore Gateway (MCP) + Cognito OAuth (synced from this account's gateway) ---",
    f"AGENTCORE_GATEWAY_URL={url}",
    f"COGNITO_TOKEN_ENDPOINT={c['token_endpoint']}",
    f"COGNITO_CLIENT_ID={c['client_id']}",
    f"COGNITO_SCOPE={c['scope']}",
    f"COGNITO_CLIENT_SECRET={c['client_secret']}",
    "",
]))
print(f"wrote {ENV}: CF_* preserved, gateway/cognito synced from {GW.name}")
