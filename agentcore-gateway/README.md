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
AgentCore Runtime (LangGraph) ── MCP/HTTP + OAuth ──▶ AgentCore Gateway
                                                  │  Cedar policy (caller auth)
                                                  ▼
                                  Lambda autodqa-run-python  ── reads ──▶ AWS Secrets Manager
                                  (broker: router/registry/vault)         CDW login
                                                  │  code + injected env (per call)
                                                  ▼
                                       Lambda MicroVM sandbox (per session)
                                                  │  raw TCP (query_cdw only)
                                                  ▼
                                       VPC egress ─▶ institutional synthetic SQL Server
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
| `lambda/sandbox_client.py` | Shared sandbox caller; launches/reuses a per-session AWS Lambda MicroVM and POSTs code to it |
| `lambda/registry.py` | `DATASETS` + `GRANTS`: dataset → connection + credential *reference*; `(tool,dataset)` allowlist. Deny by default |
| `lambda/vault.py` | Swappable secret backend; AWS Secrets Manager today (Cloudflare Secrets Store / KSM = new class) |
| `test_gateway.py` | End-to-end smoke test (token → tools/list → tools/call run_python → sandbox) |
| `.gateway_config.json` | Written by setup; gateway URL + Cognito client secret (gitignored) |

## Two kinds of credential

The design separates them deliberately:

- **Transport auth** — reaching the sandbox microVM uses a short-lived, per-VM
  token minted by `create_microvm_auth_token` (sent as the `X-aws-proxy-auth`
  header), IAM-gated by the Lambda role's `lambda:CreateMicrovmAuthToken` — so
  there's no stored bearer secret. (The retired Cloudflare sandbox used a vaulted
  shared secret here.)
- **Dataset credential** — the CDW read-only login, needed only by `query_cdw`
  and only after `registry.authorize(tool, dataset)` passes. Owned by the
  dataset in the registry; the *login* is in the vault as
  `autodqa/cdw-readonly-login` (populated from `../config.yaml`'s connection
  block). Connection coordinates (server/port, database) are non-secret and
  live in the registry/Lambda env.

`query_cdw` flow: gateway authenticates the caller → Lambda runs
`registry.authorize("query_cdw", dataset)` (deny if no grant) → `vault.fetch`
the DB login → run the pytds script in the sandbox with the login + endpoint
**injected as env vars** (never in the code body, never through the gateway,
never persisted in the sandbox) → return rows.

## Connecting the sandbox to the CDW

The sandbox MicroVM reaches the CDW **directly, in-network**: its egress runs
through a customer-managed **VPC egress connector**
(`agentcore-gateway/setup_vpc_egress.py`) whose ENIs live in the institutional
VPC/subnets the firewall was opened for, so outbound TCP to the DB sources from an
approved address. The login still comes from the vault; only the endpoint is here.

The connection coordinates come from `../config.yaml`'s `connection` block
(`server`, `port`, `database`) — baked into the Lambda at deploy as
`CDW_SERVER` / `CDW_PORT` / `CDW_DATABASE`. To point at a different DB, edit
config.yaml and redeploy:

```bash
AWS_PROFILE=bigarc-autodqa AWS_DEFAULT_REGION=us-east-1 ../.venv/bin/python resume_setup.py
```

Notes: the path requires **SQL auth** (uid/pwd), not Windows auth. Use a
**read-only** login (`db_datareader`) — the grant is the entire read-only
guarantee. If the server requires encrypted connections, pytds negotiates TLS and
today trusts the server cert without CA validation (fine for synthetic data; add a
validated CA cert before real CDW data).

> Retired (2026-07): the DB used to live on a LAN behind NAT, reached through an
> ngrok TCP tunnel (`ngrok tcp 1433`, `CDW_TUNNEL_ENDPOINT`/`PORT`). Moving
> in-network changed only `connection` — the vault/registry/pytds code carried
> forward unchanged.

## Setup

**1. AWS credentials.** Setup (not consumption) needs to create IAM roles,
Lambda functions, **Secrets Manager secrets**, Cognito user pools, and AgentCore
gateways — `bedrock-user`'s `AmazonBedrockFullAccess` is not enough. Use an
admin-ish profile, e.g. `aws configure --profile bigarc-autodqa`. If scoped
rather than `AdministratorAccess`, it also needs `SecretsManagerReadWrite`. The
notebook never needs these credentials; it consumes the gateway with an OAuth
token only.

**2. Install and run** (reads `SANDBOX_*` from `../.env` and the DB login from
`../config.yaml`):

```bash
cd agentcore-gateway
../.venv/bin/pip install "bedrock-agentcore-starter-toolkit>=0.1.10" boto3
AWS_PROFILE=bigarc-autodqa AWS_DEFAULT_REGION=us-east-1 ../.venv/bin/python setup_gateway.py
```

**3. Smoke test** (no AWS credentials needed — the consumer path):

```bash
../.venv/bin/python test_gateway.py
# tools/list: ['sandbox___query_cdw', 'sandbox___run_python']
# tools/call sandbox___run_python -> 42
```

`test_gateway.py` exercises `run_python` and `query_cdw` (the latter now hits the
institutional DB directly over the VPC egress connector, so a DB miss fails the
test rather than being tolerated). The Gateway prefixes each tool with its target
name (`sandbox___<tool>`).

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

agent = create_agent(model=llm, tools=gateway_tools, system_prompt=SYSTEM)
```

All nine tools now come from the gateway, including the five ETL readers.
They used to stay local because the ETL repo was only reachable on this
host/LAN; the sandbox now clones the synthetic ETL repo from GitLab itself
(`lambda/gitlab_clone.py`), so nothing about the ETL is host-bound any more.

## Teardown

```bash
aws bedrock-agentcore-control delete-gateway-target --gateway-identifier <gateway_id> --target-id <target_id>
aws bedrock-agentcore-control delete-gateway --gateway-identifier <gateway_id>
aws lambda delete-function --function-name autodqa-run-python
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
