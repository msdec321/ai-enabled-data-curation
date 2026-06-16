# agentcore-gateway — AgentCore Gateway as the MCP broker (experiment)

Experiment: stand up an [Amazon Bedrock AgentCore Gateway](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
as the MCP front door for AutoDQA's tools, per the MCP Broker box in the target
architecture. The Gateway is a managed MCP server whose implementation is pure
routing: it authenticates callers (Cognito OAuth), evaluates policy, and
dispatches `tools/call` to targets — here a Lambda that forwards `run_python`
to the existing Cloudflare sandbox worker.

```
notebook (LangGraph) ── MCP/HTTP + OAuth ──▶ AgentCore Gateway
                                                  │  Cedar policy (caller auth)
                                                  ▼
                                       Lambda autodqa-run-python  ── reads ──▶ AWS Secrets Manager
                                       (broker: registry + vault)             autodqa/sandbox-shared-secret
                                                  │  bearer secret (fetched per call)
                                                  ▼
                                       dqa-sandbox-runner (Cloudflare)
```

Scope: Tier-0/1 experiment. One tool (`run_python`). The broker's
credential half is now wired: the Lambda holds **no secret in its env** — per
call it resolves which credential the `(tool, dataset)` pair may use (registry)
and fetches it from the vault (AWS Secrets Manager, via the Lambda's IAM role).
AWS Secrets Manager stands in for Keeper, which the org isn't licensed for; the
vault is behind a one-class interface so the backend is swappable.

## Files

| File | Purpose |
|------|---------|
| `setup_gateway.py` | One-time provisioning: secret + Lambda + IAM, Cognito authorizer, Gateway, target |
| `resume_setup.py` | Finish a partially failed setup run (re-vaults the secret, redeploys the Lambda, retries the target, rewrites the config) |
| `lambda/run_python_tool.py` | The Lambda target — resolves+fetches the credential, then forwards run_python to the sandbox |
| `lambda/registry.py` | `(tool, dataset) → secret reference` map. Missing entry = deny. Stores references, never secrets |
| `lambda/vault.py` | Swappable secret backend; AWS Secrets Manager today (Cloudflare Secrets Store / KSM = new class) |
| `test_gateway.py` | End-to-end smoke test (token → tools/list → tools/call → sandbox) |
| `.gateway_config.json` | Written by setup; gateway URL + Cognito client secret (gitignored) |

## The credential flow

1. The agent calls `sandbox___run_python`; the gateway authenticates the caller
   and dispatches to the Lambda.
2. The Lambda calls `registry.resolve("run_python", dataset)` → a secret
   *reference* `{vault, id, key}`. No entry would mean deny.
3. `vault.fetch(ref)` reads the secret from AWS Secrets Manager using the
   Lambda's IAM role (scoped by policy to only this secret) — no bootstrap
   credential, because the role's identity *is* the auth.
4. The Lambda calls the sandbox with the fetched bearer secret and returns the
   result. The secret never lives in the Lambda's env, the gateway, or the
   model context.

`../.env`'s `SANDBOX_SHARED_SECRET` stays the dev source of truth (the notebook
calls the sandbox directly); `setup_gateway.py` copies it into Secrets Manager
as `autodqa/sandbox-shared-secret`. Rotate = update both, or make Secrets
Manager canonical and have the notebook read from it too.

## Setup

**1. AWS credentials.** Setup (not consumption) needs permissions to create
IAM roles, Lambda functions, **Secrets Manager secrets**, Cognito user pools,
and AgentCore gateways — `bedrock-user`'s `AmazonBedrockFullAccess` is not
enough. Use an admin-ish profile, e.g. `aws configure --profile autodqa-admin`.
If that profile is scoped rather than `AdministratorAccess`, it also needs
`SecretsManagerReadWrite` (for `CreateSecret`/`PutSecretValue`) on top of the
IAM/Lambda/Cognito/AgentCore permissions. The notebook never needs these
credentials; it consumes the gateway with an OAuth token only.

**2. Install and run** (uses `SANDBOX_WORKER_URL`/`SANDBOX_SHARED_SECRET`
from the repo `.env` for the Lambda):

```bash
cd agentcore-gateway
../.venv/bin/pip install "bedrock-agentcore-starter-toolkit>=0.1.10" boto3
AWS_PROFILE=autodqa-admin AWS_DEFAULT_REGION=us-east-1 ../.venv/bin/python setup_gateway.py
```

**3. Smoke test** (no AWS credentials needed — this is the consumer path):

```bash
../.venv/bin/python test_gateway.py
# tools/list: ['sandbox___run_python']
# tools/call sandbox___run_python -> 42
```

Note the naming: the Gateway prefixes each tool with its target name
(`<target>___<tool>`), so `run_python` on the `sandbox` target appears as
`sandbox___run_python` in the catalog.

## Pointing the notebook at the gateway

Replace the in-process `run_python` tool with the gateway's MCP catalog via
`langchain-mcp-adapters` (installed in the venv). In `autodqa_agent.ipynb`:

```python
import json, urllib.parse, urllib.request
from pathlib import Path
from langchain_mcp_adapters.client import MultiServerMCPClient

cfg = json.loads((Path.cwd() / "agentcore-gateway/.gateway_config.json").read_text())
cog = cfg["cognito"]
data = {"grant_type": "client_credentials", "client_id": cog["client_id"],
        "client_secret": cog["client_secret"]}
if cog.get("scope"):
    data["scope"] = cog["scope"]
req = urllib.request.Request(cog["token_endpoint"],
    data=urllib.parse.urlencode(data).encode(),
    headers={"Content-Type": "application/x-www-form-urlencoded"})
with urllib.request.urlopen(req, timeout=30) as resp:
    token = json.loads(resp.read())["access_token"]   # expires in ~1h

mcp_client = MultiServerMCPClient({
    "autodqa_gateway": {
        "transport": "streamable_http",
        "url": cfg["gateway_url"],
        "headers": {"Authorization": f"Bearer {token}"},
    }
})
gateway_tools = await mcp_client.get_tools()   # notebooks support top-level await

TOOLS = [query_cdw, search_etl, read_etl_file, *gateway_tools]  # drop local run_python
agent = create_agent(model=llm, tools=TOOLS, system_prompt=SYSTEM)
```

`query_cdw`/`search_etl`/`read_etl_file` stay local for now (the CDW and ETL
repo are on this host/LAN, unreachable from a Lambda) — migrating them behind
the gateway is the VPC-connectivity question, not an MCP question.

## Teardown

```bash
aws bedrock-agentcore-control delete-gateway-target --gateway-identifier <gateway_id> --target-id <target_id>
aws bedrock-agentcore-control delete-gateway --gateway-identifier <gateway_id>
aws lambda delete-function --function-name autodqa-run-python
aws secretsmanager delete-secret --secret-id autodqa/sandbox-shared-secret --force-delete-without-recovery
aws iam delete-role-policy --role-name autodqa-gateway-lambda-role --policy-name ... && aws iam delete-role --role-name autodqa-gateway-lambda-role
# plus the Cognito user pool the toolkit created (console: Cognito -> user pools)
```

## Caveats

- The starter toolkit's API has drifted between releases; this is pinned
  loosely (`>=0.1.10`). If `create_mcp_gateway` signatures complain, check
  the [toolkit quickstart](https://aws.github.io/bedrock-agentcore-starter-toolkit/user-guide/gateway/quickstart.html)
  against your installed version.
- Cognito client-credentials tokens expire (~1h); re-fetch before long runs.
- Tool traffic (including sandbox results) now transits AWS — fine at this
  tier; for Tier 2+ revisit alongside the Mermaid controls and PrivateLink.
- The vaulted secret materializes inside the Lambda (in AWS) at fetch time.
  Acceptable here; for a real CDW login that must not transit AWS, swap
  `vault.py` for a Cloudflare-side backend (Secrets Store), fetched by the
  sandbox worker rather than the Lambda.
- Cedar policy on the gateway is not configured yet — every authenticated
  caller can invoke every tool. Adding a `dataset` argument to `run_python`'s
  schema and a Cedar policy that discriminates on it is the natural next
  experiment; the registry already keys on `(tool, dataset)` to receive it.
