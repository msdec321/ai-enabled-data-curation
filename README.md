# AutoDQA — Autonomous Data Quality Assessment

A multi-agent system that autonomously profiles a clinical data warehouse (CDW), detects data quality issues, traces them through ETL code to identify root causes, and produces an actionable report.

Built on [Claude Code](https://docs.anthropic.com/en/docs/claude-code) headless mode (`claude -p`). Targets PCORnet CDM databases on SQL Server.

## Architecture

A **coordinator agent** orchestrates a team of specialist sub-agents through seven phases:

| Phase | Agent | Output |
|-------|-------|--------|
| 0 — Context Ingestion | Coordinator | `etl_index.json`, `expectations.json` |
| 1 — Profiling | Profiler | `profile_<TABLE>.json` per table |
| 2 — Issue Detection | Analyst | `issues.json` |
| 3 — Issue Clustering | Coordinator | `clusters.json` |
| 4 — Root Cause Investigation | Investigator | `investigation_cluster_<ID>.json` per cluster |
| 5 — Review | Reviewer | `review.json` |
| 6 — Reporting | Report Writer | `dqa_report.md` |

Each sub-agent is an independent `claude -p` session that communicates only through JSON files on disk. The coordinator never runs queries itself — it orchestrates, clusters issues, and makes priority decisions.

### Agent Roles

- **Profiler** — Runs statistical SQL queries (null rates, cardinality, distributions, FK orphan counts) and writes structured JSON profiles
- **Analyst** — Compares profiles against PCORnet column specifications to flag severity-graded data quality issues
- **Investigator** — Reads ETL SQL (source views, load procs, mapping functions) to identify the root cause of each issue cluster
- **Reviewer** — Independently spot-checks queries and ETL code citations to verify accuracy before reporting
- **Report Writer** — Synthesizes all findings into a human-readable markdown report

### MCP Tool Servers

Four MCP servers (in `tools/`) give agents access to external data:

| Server | Purpose |
|--------|---------|
| `sql_executor` | Read-only T-SQL execution against the CDW (10k row limit, keyword blocklist enforced) |
| `etl_reader` | Search and read files in the ETL codebase |
| `spec_query` | Query PCORnet metadata tables for column specs, value sets, and constraints |
| `doc_search` | Search and read ETL documentation |

## Prerequisites

- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) (`npm install -g @anthropic-ai/claude-code`)
- Python 3.11+
- ODBC Driver 18 for SQL Server
- `ANTHROPIC_API_KEY` environment variable set

## Setup

### 1. Clone and create the virtual environment

```bash
git clone <repo-url>
cd ai-enabled-data-curation
python3 -m venv .venv
.venv/bin/pip install mcp[cli] pyodbc pyyaml
```

### 2. Install ODBC Driver 18 (Ubuntu/Debian)

```bash
curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | sudo gpg --dearmor --yes -o /usr/share/keyrings/microsoft-prod.gpg

echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/ubuntu/$(lsb_release -rs)/prod $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/mssql-release.list

sudo apt update && sudo ACCEPT_EULA=Y apt install -y msodbcsql18
```

### 3. Configure your database connection

```bash
cp databases/cdw_example.yaml databases/cdw.yaml
```

Edit `databases/cdw.yaml` with your connection details:

```yaml
id: "cdw"
name: "Clinical Data Warehouse"
engine: "mssql"
cdm: "pcornet"

connection:
  driver: "ODBC Driver 18 for SQL Server"
  server: "<SERVER_NAME>"
  database: "<DATABASE_NAME>"
  uid: "<USERNAME>"
  pwd: "<PASSWORD>"
  TrustServerCertificate: "yes"

etl_repo: "<PATH_TO_ETL_REPO>"

tables:
  - DEMOGRAPHIC
  - ENCOUNTER
  - DIAGNOSIS
```

**Notes:**
- For Windows Authentication, replace `uid`/`pwd` with `trusted_connection: "yes"`
- If connecting from WSL2 to a local SQL Server, use `127.0.0.1` as the server (WSL2 mirrored networking) or the Windows host IP from `cat /etc/resolv.conf`
- `etl_repo` should point to the local path of your ETL codebase
- `tables` lists the CDM tables to assess

## Usage

### Full DQA pipeline

```bash
./run.sh --db-config databases/cdw.yaml
```

### Custom task

Bypass the full pipeline and give the agent a specific task:

```bash
./run.sh --db-config databases/cdw.yaml --task "Query the DEMOGRAPHIC table and tell me how many rows there are"
```

### Options

| Flag | Description |
|------|-------------|
| `--db-config <path>` | Path to database YAML config (required) |
| `--tables <list>` | Comma-separated tables to profile (default: from config) |
| `--task "<prompt>"` | Custom task instead of full DQA pipeline |
| `--resume-from-analysis` | Skip profiling, start from issue detection |
| `--resume-from-investigation` | Skip to root cause investigation |
| `--resume-from-report` | Skip to report generation |
| `<number>` | Max turns per sub-agent (default: 50) |

### Resume from a checkpoint

If a run is interrupted, resume from the last completed phase:

```bash
./run.sh --db-config databases/cdw.yaml --resume-from-analysis
./run.sh --db-config databases/cdw.yaml --resume-from-investigation
./run.sh --db-config databases/cdw.yaml --resume-from-report
```

### Output

All artifacts are written to `results/<YYYY-MM-DD>_<db_id>_dqa/`. The final deliverable is `dqa_report.md`.
