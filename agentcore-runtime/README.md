# autodqa-agentcore-runtime

The AutoDQA agent (Bedrock + LangGraph) packaged for **Amazon Bedrock AgentCore
Runtime**. This is the AWS-native re-host of the orchestrator: it consolidates
the Python notebook (`../autodqa_agent.ipynb`, the LangGraph core) and the TS
Worker (`../orchestrator-worker/`, the operational plumbing) into one deployable
service. Milestone 1 = a "walking skeleton" that answers a data-quality task
through the AgentCore Gateway.

```
agentcore invoke ──▶ AgentCore Runtime (this package)
                        ├─ reasoning: Bedrock via Cloudflare AI Gateway (BYOK, v1)
                        └─ tools:     AgentCore Gateway (MCP + Cognito OAuth)
                                         └─ Lambda broker → sandbox → tunnel → SQL Server
```

## Layout

| File | Role |
|---|---|
| `agent.py` | LangGraph `create_agent` + the Bedrock-via-CF-gateway client (ported from notebook cell 6). |
| `gateway_tools.py` | Cognito OAuth + MCP tool catalog + per-run `session` injection + `destroy_sandbox` lifecycle (ported from `index.ts`). |
| `entrypoint.py` | `BedrockAgentCoreApp` + `@app.entrypoint` async generator → `agent.astream`. |
| `requirements.txt` | Python deps (no pyodbc — tools are remote). |
| `.env.example` | Local-test env; copy to `.env`. |

## What's in v1 vs. deferred

**In:** LangGraph agent, tools via Gateway MCP, per-run session injection +
best-effort teardown, Bedrock via your **personal** CF AI Gateway, invoke via
`agentcore invoke`.

**Deferred:** report turn → S3 (the Worker's R2 step), extended thinking,
rich trace events, a native UI, token-refresh on warm instances.

## Prerequisites

- AgentCore **Runtime** available in your region (`us-east-1`, same as the Gateway).
- An AgentCore **Gateway** deployed **in the same (personal) AWS account** —
  see `../agentcore-gateway/`. Confirm its URL/Cognito values match `.env`.
- The AgentCore CLI. The widely-documented path is the starter toolkit
  (`pip install bedrock-agentcore-starter-toolkit`); AWS now also offers a newer
  `agentcore-cli` — pick one before deploying.
- Python deps in a venv: `./.venv/bin/pip install -r requirements.txt`
  (use the venv's pip explicitly — Windows Python leaks into the WSL PATH).

## Secrets

Two secrets must reach the running container: `CF_AIG_TOKEN` and
`COGNITO_CLIENT_SECRET`. Reuse the existing AWS Secrets Manager pattern
(`../agentcore-gateway/lambda/vault.py`):

1. Put both in a Secrets Manager secret in your personal account.
2. Grant the Runtime execution role `secretsmanager:GetSecretValue` on it.
3. Load them into the environment at startup (a small loader, or inject as env
   vars at deploy). For a first local run you can just put them in `.env`.

The non-secret identifiers (`AGENTCORE_GATEWAY_URL`, `COGNITO_*`, `CF_ACCOUNT_ID`,
`CF_AIG_GATEWAY`) are safe as plain runtime env vars.

## Deploy (no local Docker)

`agentcore launch` builds the required **ARM64** image via **CodeBuild** in the
cloud by default — no Docker engine needed locally (Docker is only used for
`--local`). It also provisions the ECR repo, S3 bucket, and IAM roles.

```bash
cd agentcore-runtime
agentcore configure --entrypoint entrypoint.py     # generates Dockerfile + config
agentcore launch                                   # CodeBuild → ECR → Runtime
agentcore invoke '{"task": "How many rows are in DEMOGRAPHIC?"}'
```

Local smoke test before deploying: `agentcore launch -l` (needs Docker) or run
`python entrypoint.py` with `.env` exported and hit `POST /invocations`.

## Verify on first live deploy (the spots I couldn't de-risk by reading)

1. **`langchain-mcp-adapters` connection shape** — confirm `transport:
   "streamable_http"` + custom `headers` are honored by the installed version
   (`gateway_tools.load_gateway_tools`).
2. **`session` in the tool inputSchema** — we inject/override it; confirm whether
   to also strip it from the model-facing schema (`gateway_tools._with_session`).
3. **`@app.entrypoint` signature / streaming** — confirm the generator's yielded
   dicts serialize as expected and whether a `context` arg is passed
   (`entrypoint.invoke`).

## Next milestones

2. Report turn → **S3** (port `persistReport`, R2→S3).
3. Drop the CF AI Gateway → IAM-role-signed Bedrock + Bedrock Guardrails (migration point 3).
4. Native UI: CloudFront + API Gateway + Cognito (replaces the CF Worker + Access).
