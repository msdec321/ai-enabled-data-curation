# Retired: notebook AutoDQA agent

These notebooks were the original AutoDQA agent — Claude on **Amazon Bedrock**
(via the Cloudflare AI Gateway) driving a local **LangGraph** loop, executing all
model-written code in the **Cloudflare sandbox worker** (`dqa-sandbox-runner`).

- `autodqa_agent.ipynb` — the main notebook agent (Bedrock + LangGraph loop + tools)
- `agent.ipynb` — an earlier iteration

## Superseded by the AWS-native stack

The orchestration moved off the notebook and onto AWS-managed services:

| Notebook role | Replaced by |
|---|---|
| LangGraph loop in the notebook | `agentcore-runtime/` — LangGraph on Bedrock AgentCore Runtime |
| Tool plumbing / broker | `agentcore-gateway/` — AgentCore Gateway + broker Lambda (MCP) |
| Cloudflare sandbox worker (`dqa-sandbox-runner`) | `sandbox-microvm/` — AWS Lambda MicroVM image |
| n/a | `frontend/` — CloudFront + S3 + Cognito browser UI |

## These will not run as-is

They depended on the Cloudflare sandbox worker (`SANDBOX_WORKER_URL` /
`SANDBOX_SHARED_SECRET`), which was **decommissioned** as part of the MicroVM
migration. Kept here for reference only — don't build on them.
