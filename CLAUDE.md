# AutoDQA — Autonomous Data Quality Assessment

A code-mode agent for autonomous data quality assessment of a clinical data
warehouse (PCORnet CDM on SQL Server): Claude on **Amazon Bedrock** for
reasoning, a trusted local **LangGraph** loop for orchestration, and a
**Cloudflare Sandbox** for executing all model-written code.

Key places:

- `autodqa_agent.ipynb` — the main agent (Bedrock + LangGraph loop and its tools)
- `sandbox-worker/` — Cloudflare Worker that runs untrusted code in a sandbox container
- `orchestrator-worker/` — Cloudflare-hosted PoC on synthetic data (Tier-0/1 only)
- `docs/target_architecture.md` — the architecture and trust model
- `config.yaml` — DB connection, ETL repo path, tables (gitignored; see `config.example.yaml`)

`legacy/` contains the deprecated Claude Code multi-agent pipeline (see
`legacy/CLAUDE.md`). Don't build on it; it's kept for reference and can still
be run via `./legacy/run.sh`.
