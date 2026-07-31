# AutoDQA — Autonomous Data Quality Assessment

A code-mode agent for autonomous data quality assessment of a clinical data
warehouse (PCORnet CDM on SQL Server): Claude on **Amazon Bedrock** for
reasoning, a **LangGraph** loop hosted on **Bedrock AgentCore Runtime** for
orchestration, and an **AWS Lambda MicroVM** for executing all model-written
code. (Bedrock calls still route through the Cloudflare AI Gateway.)

Key places:

- `agentcore-runtime/` — the main agent: the LangGraph loop on Bedrock AgentCore
  Runtime (`entrypoint.py`, `agent.py`) plus its gateway-sourced tools
- `agentcore-gateway/` — AgentCore Gateway + broker Lambda exposing the tools over
  MCP (the registry / vault / sandbox plumbing)
- `sandbox-microvm/` — AWS Lambda MicroVM image that runs untrusted model-written
  code (pytds + git/openssh baked in); replaced the retired Cloudflare sandbox worker.
  No ETL is baked in: the sandbox clones the **synthetic** ETL repo from GitLab per run
  (`GITLAB_ETL_REPO` → `/tmp/etl-repo`), so updating the ETL is a git push, not an
  image rebuild. Never point it at the production `cdw` repo — the synthetic warehouse
  runs different SQL.
- `frontend/` — browser UI (Cognito login; calls the Runtime directly), served from
  the institutional Cloudflare Pages behind Access via the GitHub mirror repo
  `mdecaro-uth/autodqa-frontend`; the CloudFront/S3 hosting was decommissioned
- `orchestrator-worker/` — earlier Cloudflare-hosted PoC (TS Worker) on synthetic data (Tier-0/1 only)
- `docs/target_architecture.md` — the architecture and trust model
- `config.yaml` — DB connection, ETL repo path, tables (gitignored; see `config.example.yaml`)

`legacy/` holds superseded code kept for reference — don't build on it:
`legacy/notebook-agent/` (the original notebook agent, which ran on the now-retired
Cloudflare sandbox) and the deprecated Claude Code multi-agent pipeline
(`legacy/CLAUDE.md`, runnable via `./legacy/run.sh`).
