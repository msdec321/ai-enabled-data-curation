# AutoDQA — Autonomous Data Quality Assessment

An agent for autonomous data quality assessment of a clinical data warehouse (CDW): it profiles tables, detects data quality issues, and traces them through ETL code to root causes. Targets PCORnet CDM databases on SQL Server.

The design is **code-mode**: the agent reasons by writing code, and all model-authored code runs in an isolated sandbox rather than on the orchestrating host.

- **Reasoning** — Claude on **Amazon Bedrock** (`ChatBedrockConverse`), routed through a **Cloudflare AI Gateway** that holds the Bedrock credentials (BYOK) and signs the upstream requests
- **Orchestration** — **LangGraph** (`create_react_agent`): a trusted local loop that holds no DB credentials and runs no model-written code
- **Execution** — a **Cloudflare Sandbox** container that runs the agent's Python in isolation

## Architecture

<img src="docs/autodqa_architecture.svg" alt="AutoDQA architecture" width="700">

The agent loop:

1. The user submits a task through the Zero Trust front door (SSO/MFA); the LangGraph orchestrator sends the conversation context to Bedrock via the Cloudflare AI Gateway (access policies, audit logging, DLP).
2. Bedrock replies — either "run this code" or a final answer.
3. The orchestrator passes the execution request to the MCP Broker.
4. The broker enforces tool and dataset allowlists (consulting the dataset registry), retrieves credentials from the secrets vault (AWS Secrets Manager — the org is not licensed for Keeper, which the architecture diagram still names), and dispatches the code to the Cloudflare sandbox with credentials injected.
5. The sandbox code queries the CDW with read-only T-SQL and reads the documentation store (ETL code, docs).
6. Result rows are pulled back into the sandbox for analysis.
7. stdout/results return through the broker.
8. The orchestrator appends the results and loops back to (1) until Bedrock produces a final answer.

See [`docs/target_architecture.md`](docs/target_architecture.md) for the trust model in detail.

Trust boundaries:

- **Orchestrator = trusted** — no model-authored code, no DB credentials.
- **Sandbox = untrusted** — all model-written code is contained in a Firecracker microVM, with egress allowlisted.
- **Read-only is enforced at the DB login** (`db_datareader`), not by keyword filtering — once the agent writes arbitrary code, only the grant level guarantees read-only.

**Current vs. target state:** the notebook today runs the DB query (`query_cdw`) and ETL tools (`search_etl`, `read_etl_file`) locally, and uses the sandbox for compute (`run_python`). The target moves the DB connection and ETL repo *inside* an in-network sandbox image — see the prerequisites note in [`docs/target_architecture.md`](docs/target_architecture.md).

## Repository layout

| Path | What it is |
|------|------------|
| `autodqa_agent.ipynb` | The main agent — Bedrock + LangGraph loop with `query_cdw`, `search_etl`/`read_etl_file`, and `run_python` tools |
| `sandbox-worker/` | `dqa-sandbox-runner` — Cloudflare Worker that executes untrusted code in a [Cloudflare Sandbox](https://developers.cloudflare.com/sandbox/) container |
| `orchestrator-worker/` | Cloudflare-hosted streaming web console: submit a task and watch the agent reason/call tools/answer live. Reasoning on Bedrock; tools driven through the AgentCore gateway |
| `docs/target_architecture.md` | The code-mode architecture (diagram + trust model) |
| `docs/` | Project Mermaid security proposal, provider evaluations, PHI data-flow inventory |
| `config.yaml` | DB connection, ETL repo path, documentation paths, tables to assess |
| `agentcore-gateway/` | Experiment: AWS AgentCore Gateway as the MCP broker front door (see its README) |
| `agent.ipynb` | Earlier scratch notebook (superseded by `autodqa_agent.ipynb`) |
| `legacy/` | The deprecated Claude Code multi-agent pipeline (see below) |

## Prerequisites

- Python 3.11+
- ODBC Driver 18 for SQL Server (for the local `query_cdw` tool)
- A Cloudflare AI Gateway with Bedrock BYOK credentials (IAM SigV4 keys for a Claude-enabled Bedrock account) stored in it — no AWS credentials are needed locally
- A Cloudflare account on the Workers Paid plan (Containers), for the sandbox worker
- Node.js + `wrangler` (for deploying the Workers)

## Setup

### 1. Clone and create the virtual environment

```bash
git clone <repo-url>
cd ai-enabled-data-curation
python3 -m venv .venv
.venv/bin/pip install "langchain>=1.0" "langchain-aws>=1.4.5" "langgraph>=0.6" "langchain-core>=0.3" "boto3>=1.35.87" pyodbc pyyaml requests jupyter
```

### 2. Install ODBC Driver 18 (Ubuntu/Debian)

```bash
curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | sudo gpg --dearmor --yes -o /usr/share/keyrings/microsoft-prod.gpg

echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/ubuntu/$(lsb_release -rs)/prod $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/mssql-release.list

sudo apt update && sudo ACCEPT_EULA=Y apt install -y msodbcsql18
```

### 3. Deploy the sandbox worker

```bash
cd sandbox-worker
npm install
npx wrangler secret put SANDBOX_SHARED_SECRET   # any strong shared value
npm run deploy
```

Copy the printed `*.workers.dev` URL — it goes in `.env` below. See [`sandbox-worker/README.md`](sandbox-worker/README.md) for details (no local Docker needed; it deploys Cloudflare's pre-built `sandbox:*-python` image).

### 4. Configure the database and ETL sources

```bash
cp config.example.yaml config.yaml
```

Edit `config.yaml` with your connection details and source paths:

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

sources:
  etl:
    path: "<PATH_TO_ETL_REPO>"
  documentation:
    paths:
      - "<PATH_TO_ETL_REPO>/Documentation"

tables:
  - DEMOGRAPHIC
  - ENCOUNTER
  - DIAGNOSIS
```

**Notes:**
- Use a **read-only** DB login (`db_datareader`, no write/DDL).
- For Windows Authentication, replace `uid`/`pwd` with `trusted_connection: "yes"`.
- If connecting from WSL2 to a local SQL Server, use `127.0.0.1` (mirrored networking) or the Windows host IP from `cat /etc/resolv.conf`.

### 5. Create `.env` (gitignored)

```bash
# Cloudflare AI Gateway — handles Bedrock auth (BYOK keys are stored in the gateway)
CF_ACCOUNT_ID=<account-id>
CF_AIG_GATEWAY=<gateway-name>
CF_AIG_TOKEN=<gateway-token>

# Bedrock region (baked into the gateway endpoint path; must match the BYOK keys)
export AWS_DEFAULT_REGION=us-east-1

# Sandbox worker (from step 3)
SANDBOX_WORKER_URL=https://dqa-sandbox-runner.<account>.workers.dev
SANDBOX_SHARED_SECRET=<same value as the worker secret>
```

All Bedrock calls go through the gateway — the notebook builds an unsigned boto3 client against the gateway endpoint and authenticates with `CF_AIG_TOKEN`; the gateway does the SigV4 signing.

## Usage

Run the agent from the notebook:

```bash
./.venv/bin/jupyter lab autodqa_agent.ipynb
```

Execute the cells top to bottom, then set `task` in the final cells and re-run. The agent streams each reason → act → observe step, so you can watch the tool calls and results as it works.

### Hosted web console

`orchestrator-worker/` deploys the agent as a Cloudflare Worker with a streaming web console — submit a data-quality task and watch the agent reason, call tools, and answer in real time. It reasons on Bedrock (via the AI Gateway) and drives all tools through the AgentCore gateway (`agentcore-gateway/`), so `query_cdw` reaches the real synthetic SQL Server and `run_python` runs in the sandbox. See [`orchestrator-worker/README.md`](orchestrator-worker/README.md) for setup and the security posture.

It is **Tier-0/1 by design: synthetic data only**. Do not point it at real CDW data — that requires the Project Mermaid Tier 2+ controls (DLP, redacted logs, BAA-covered services).

## Legacy: Claude Code pipeline (deprecated)

The original AutoDQA implementation was a multi-agent pipeline built on Claude Code headless mode (`claude -p`): a coordinator agent orchestrated profiler, analyst, investigator, reviewer, and report-writer sub-agents through seven phases, communicating via JSON files and reaching the CDW through MCP tool servers. It has been superseded by the code-mode agent above and now lives in [`legacy/`](legacy/README.md), but remains runnable from the repo root:

```bash
./legacy/run.sh --config config.yaml
```

Its pieces: `legacy/run.sh` (launcher), the role instruction files (`legacy/COORDINATOR.md`, `PROFILER.md`, `ANALYST.md`, `INVESTIGATOR.md`, `REVIEW.md`, `REPORT_WRITER.md`), the MCP servers in `legacy/tools/`, and the full design in [`docs/architecture.md`](docs/architecture.md). Output from a pipeline run lands in `legacy/results/<YYYY-MM-DD>_<db_id>_dqa/`.
