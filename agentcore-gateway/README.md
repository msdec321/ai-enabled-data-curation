# agentcore-gateway — AgentCore Gateway as the MCP broker (experiment)

Experiment: stand up an [Amazon Bedrock AgentCore Gateway](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
as the MCP front door for AutoDQA's tools, per the MCP Broker box in the target
architecture. The Gateway is a managed MCP server whose implementation is pure
routing: it authenticates callers (Cognito OAuth), evaluates policy, and
dispatches `tools/call` to a Lambda target. One Lambda backs the `sandbox`
target and exposes two tools, both of which execute in the Cloudflare sandbox:

- **`run_python`** — arbitrary Python in the sandbox (no data access).
- **`query_cdw`** — read-only T-SQL against a registered dataset. The broker
  fetches the DB login from the vault and injects it into the sandbox, which
  connects to the database via **pytds** (pure-Python TDS — no ODBC driver, so
  no custom sandbox image needed).

```
notebook (LangGraph) ── MCP/HTTP + OAuth ──▶ AgentCore Gateway
                                                  │  Cedar policy (caller auth)
                                                  ▼
                                  Lambda autodqa-run-python  ── reads ──▶ AWS Secrets Manager
                                  (broker: router/registry/vault)         sandbox bearer + CDW login
                                                  │  code + injected env (per call)
                                                  ▼
                                       dqa-sandbox-runner (Cloudflare)
                                                  │  raw TCP (query_cdw only)
                                                  ▼
                                       TCP tunnel ─▶ synthetic SQL Server (your LAN)
```

Scope: Tier-0/1 experiment, synthetic data. The broker's credential half is
wired: the Lambda holds **no secret in its env** — it fetches what each call
needs from the vault (AWS Secrets Manager, via the Lambda's IAM role). AWS
Secrets Manager stands in for Keeper, which the org isn't licensed for; the
vault is behind a one-class interface so the backend is swappable.

## Files

| File | Purpose |
|------|---------|
| `setup_gateway.py` | One-time provisioning: secrets + Lambda + IAM, Cognito authorizer, Gateway, target |
| `resume_setup.py` | Finish/refresh a setup run (re-vaults secrets, redeploys the Lambda, updates the target's tool schema, rewrites the config) |
| `lambda/tool_router.py` | Lambda entry point; routes a `tools/call` to the right tool handler by name |
| `lambda/tool_run_python.py` | `run_python` — forward code to the sandbox |
| `lambda/tool_query_cdw.py` | `query_cdw` — authorize, fetch DB login, run a pytds query in the sandbox |
| `lambda/sandbox_client.py` | Shared sandbox caller; fetches the sandbox bearer (transport cred) from the vault |
| `lambda/registry.py` | `DATASETS` + `GRANTS`: dataset → connection + credential *reference*; `(tool,dataset)` allowlist. Deny by default |
| `lambda/vault.py` | Swappable secret backend; AWS Secrets Manager today (Cloudflare Secrets Store / KSM = new class) |
| `test_gateway.py` | End-to-end smoke test (token → tools/list → tools/call run_python → sandbox) |
| `.gateway_config.json` | Written by setup; gateway URL + Cognito client secret (gitignored) |

## Two kinds of credential

The design separates them deliberately:

- **Transport credential** — the sandbox bearer secret, needed to reach the
  sandbox backend at all. Both tools use it; `sandbox_client` fetches it from
  the vault. Source: `../.env`'s `SANDBOX_SHARED_SECRET`, vaulted as
  `autodqa/sandbox-shared-secret`.
- **Dataset credential** — the CDW read-only login, needed only by `query_cdw`
  and only after `registry.authorize(tool, dataset)` passes. Owned by the
  dataset in the registry; the *login* is in the vault as
  `autodqa/cdw-readonly-login` (populated from `../config.yaml`'s connection
  block). Connection coordinates (tunnel host/port, database) are non-secret and
  live in the registry/Lambda env.

`query_cdw` flow: gateway authenticates the caller → Lambda runs
`registry.authorize("query_cdw", dataset)` (deny if no grant) → `vault.fetch`
the DB login → run the pytds script in the sandbox with the login + endpoint
**injected as env vars** (never in the code body, never through the gateway,
never persisted in the sandbox) → return rows.

## Connecting the sandbox to your LAN database

The Cloudflare sandbox is in the cloud; your SQL Server is behind your LAN's
NAT. A **dumb TCP tunnel** (no credentials, no logic) bridges them — it only
makes the DB reachable; the login still comes from the vault.

`cloudflared` is awkward for raw TCP to a generic client, so use a raw-TCP
passthrough:

```bash
ngrok tcp 1433        # -> forwarding tcp://N.tcp.ngrok.io:PORT -> localhost:1433
```

Then point the Lambda at that endpoint (these become the registry's
`connection.server`/`port`) and redeploy:

```bash
export CDW_TUNNEL_ENDPOINT=N.tcp.ngrok.io CDW_TUNNEL_PORT=PORT
AWS_PROFILE=autodqa-admin AWS_DEFAULT_REGION=us-east-1 ../.venv/bin/python resume_setup.py
```

Notes: the DB port becomes internet-reachable behind the tunnel — acceptable
for synthetic data with a read-only login, but a reason this exact tunnel is
PoC-only. The tunneled path requires **SQL auth** (uid/pwd), not Windows auth.
For the real institutional CDW, the sandbox moves in-network instead of
tunneling out — but the vault/registry/pytds code here carries forward
unchanged; only `connection` changes.

## Setup

**1. AWS credentials.** Setup (not consumption) needs to create IAM roles,
Lambda functions, **Secrets Manager secrets**, Cognito user pools, and AgentCore
gateways — `bedrock-user`'s `AmazonBedrockFullAccess` is not enough. Use an
admin-ish profile, e.g. `aws configure --profile autodqa-admin`. If scoped
rather than `AdministratorAccess`, it also needs `SecretsManagerReadWrite`. The
notebook never needs these credentials; it consumes the gateway with an OAuth
token only.

**2. Install and run** (reads `SANDBOX_*` from `../.env` and the DB login from
`../config.yaml`):

```bash
cd agentcore-gateway
../.venv/bin/pip install "bedrock-agentcore-starter-toolkit>=0.1.10" boto3
AWS_PROFILE=autodqa-admin AWS_DEFAULT_REGION=us-east-1 ../.venv/bin/python setup_gateway.py
```

**3. Smoke test** (no AWS credentials needed — the consumer path):

```bash
../.venv/bin/python test_gateway.py
# tools/list: ['sandbox___query_cdw', 'sandbox___run_python']
# tools/call sandbox___run_python -> 42
```

`test_gateway.py` exercises `run_python` (no tunnel needed). `query_cdw` works
once the tunnel is up and `CDW_TUNNEL_ENDPOINT/PORT` are set (step above). The
Gateway prefixes each tool with its target name (`sandbox___<tool>`).

## Pointing the notebook at the gateway

Load the gateway's MCP catalog via `langchain-mcp-adapters` (installed in the
venv) and drop the matching local tools. In `autodqa_agent.ipynb`:

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
gateway_tools = await mcp_client.get_tools()   # run_python + query_cdw

TOOLS = [search_etl, read_etl_file, *gateway_tools]  # drop local run_python AND query_cdw
agent = create_agent(model=llm, tools=TOOLS, system_prompt=SYSTEM)
```

`search_etl`/`read_etl_file` stay local for now (the ETL repo is on this
host/LAN); moving them behind the gateway is a later step.

## Teardown

```bash
aws bedrock-agentcore-control delete-gateway-target --gateway-identifier <gateway_id> --target-id <target_id>
aws bedrock-agentcore-control delete-gateway --gateway-identifier <gateway_id>
aws lambda delete-function --function-name autodqa-run-python
aws secretsmanager delete-secret --secret-id autodqa/sandbox-shared-secret --force-delete-without-recovery
aws secretsmanager delete-secret --secret-id autodqa/cdw-readonly-login --force-delete-without-recovery
aws iam delete-role-policy --role-name autodqa-gateway-lambda-role --policy-name ... && aws iam delete-role --role-name autodqa-gateway-lambda-role
# plus the Cognito user pool the toolkit created (console: Cognito -> user pools)
```

## Caveats

- The starter toolkit's API has drifted between releases; pinned loosely
  (`>=0.1.10`). If `create_mcp_gateway` complains, check the
  [toolkit quickstart](https://aws.github.io/bedrock-agentcore-starter-toolkit/user-guide/gateway/quickstart.html)
  against your installed version.
- Cognito client-credentials tokens expire (~1h); re-fetch before long runs.
- `pytds` is pip-installed in the sandbox on first `query_cdw` per session
  (~10–20s once, then cached for the session's life).
- The DB login materializes inside the Lambda and the sandbox (both cloud) at
  call time. Acceptable for synthetic Tier-0/1; for a real CDW login that must
  not transit the cloud, the executor moves in-network.
- Cedar policy on the gateway is not configured yet — every authenticated caller
  can invoke every tool. The registry already keys on `(tool, dataset)`; a Cedar
  policy that discriminates on the `dataset` argument is the natural next step.
