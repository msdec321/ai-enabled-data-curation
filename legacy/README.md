# AutoDQA — Legacy Claude Code Pipeline

> **⚠️ Deprecated.** This is the original AutoDQA implementation, superseded by
> the code-mode agent (Bedrock + LangGraph + Cloudflare) at the repo root — see
> the [main README](../README.md). It is kept for reference and remains
> runnable, but no new development happens here.

A multi-agent pipeline built on [Claude Code](https://docs.anthropic.com/en/docs/claude-code)
headless mode (`claude -p`) that profiles a clinical data warehouse (PCORnet CDM
on SQL Server), detects data quality issues, traces them through ETL code to
root causes, and produces a report.

## How it works

A **coordinator agent** drives seven phases, spawning each specialist as an
independent `claude -p` session. Agents communicate only through JSON files on
disk; the coordinator never runs queries itself — it orchestrates, clusters
issues, and makes priority decisions.

| Phase | Agent | Output |
|-------|-------|--------|
| 0 — Context Ingestion | Coordinator | `etl_index.json`, `expectations.json` |
| 1 — Profiling | Profiler | `profile_<TABLE>.json` per table |
| 2 — Issue Detection | Analyst | `issues.json` |
| 3 — Issue Clustering | Coordinator | `clusters.json` |
| 4 — Root Cause Investigation | Investigator (one per cluster) | `investigation_cluster_<ID>.json` |
| 5 — Review | Reviewer (independent session) | `review.json` |
| 6 — Reporting | Report Writer | `dqa_report.md` |

Each agent's instructions live in the role files here (`COORDINATOR.md`,
`PROFILER.md`, `ANALYST.md`, `INVESTIGATOR.md`, `REVIEW.md`,
`REPORT_WRITER.md`). Notable design points:

- The reviewer runs in a fresh session with no access to prior agents'
  reasoning, and its REVISE verdict loops back to investigation (not to the
  beginning) — specific clusters can be re-investigated while the rest are
  accepted.
- Clustering happens before investigation, so each investigator traces one
  coherent set of symptoms to a single root cause.

Agents reach external data through four MCP servers in `tools/`:

| Server | Purpose |
|--------|---------|
| `sql_executor` | Read-only T-SQL against the CDW (10k row limit, keyword blocklist) |
| `etl_reader` | Search and read files in the ETL codebase |
| `spec_query` | Query PCORnet metadata for column specs, value sets, constraints |
| `doc_search` | Search and read ETL documentation |

The full design with diagram is in [`../docs/architecture.md`](../docs/architecture.md).

## Running it

Prerequisites: Claude Code CLI, the root `.venv` with `mcp[cli] pyodbc pyyaml`,
ODBC Driver 18, and `ANTHROPIC_API_KEY` set. Uses the shared `config.yaml` at
the repo root.

```bash
# from the repo root
./legacy/run.sh --config config.yaml

# custom one-off task instead of the full pipeline
./legacy/run.sh --config config.yaml --task "How many rows are in DEMOGRAPHIC?"

# resume an interrupted run from a checkpoint
./legacy/run.sh --config config.yaml --resume-from-analysis
./legacy/run.sh --config config.yaml --resume-from-investigation
./legacy/run.sh --config config.yaml --resume-from-report
```

Other flags: `--tables LIST` (override config), a bare number for max turns per
sub-agent (default 50). Artifacts land in `legacy/results/<YYYY-MM-DD>_<db_id>_dqa/`;
the final deliverable is `dqa_report.md`.
