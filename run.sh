#!/usr/bin/env bash
# =============================================================================
# AutoDQA — Launch Script
# =============================================================================
# Launches the coordinator agent for autonomous data quality assessment.
#
# Usage:
#   ./run.sh --config config.yaml
#   ./run.sh --config config.yaml --tables DEMOGRAPHIC,ENCOUNTER
#   ./run.sh --config config.yaml --resume-from-report
#   ./run.sh --config config.yaml 75  # max turns
#
# Prerequisites:
#   - Claude Code CLI installed (npm install -g @anthropic-ai/claude-code)
#   - Python 3.11+ with: pip install mcp pyodbc pyyaml
#   - ANTHROPIC_API_KEY set in environment
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Activate venv ──

if [[ -d "$SCRIPT_DIR/.venv" ]]; then
  source "$SCRIPT_DIR/.venv/bin/activate"
elif [[ -d "$SCRIPT_DIR/venv" ]]; then
  source "$SCRIPT_DIR/venv/bin/activate"
fi

# ── Preflight checks ──

if ! command -v claude &>/dev/null; then
  echo "Error: Claude Code CLI not found." >&2
  echo "Install with: npm install -g @anthropic-ai/claude-code" >&2
  exit 1
fi

PYTHON="${SCRIPT_DIR}/.venv/bin/python3"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3)"
fi

if ! "$PYTHON" -c "import mcp, pyodbc, yaml" 2>/dev/null; then
  echo "Error: MCP server dependencies missing." >&2
  echo "Set up the venv with:" >&2
  echo "  python3 -m venv .venv && .venv/bin/pip install mcp[cli] pyodbc pyyaml" >&2
  exit 1
fi

# ── Parse arguments ──

CONFIG=""
TABLES=""
RESUME_MODE=""
MAX_TURNS="50"
CUSTOM_TASK=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      CONFIG="$2"; shift 2
      ;;
    --tables)
      TABLES="$2"; shift 2
      ;;
    --resume-from-analysis)
      RESUME_MODE="analysis"; shift
      ;;
    --resume-from-investigation)
      RESUME_MODE="investigation"; shift
      ;;
    --resume-from-report)
      RESUME_MODE="report"; shift
      ;;
    --task)
      CUSTOM_TASK="$2"; shift 2
      ;;
    --help|-h)
      echo "Usage: ./run.sh --config <path> [--tables TABLE,TABLE,...] [--resume-from-*] [--task \"...\"] [max_turns]"
      echo ""
      echo "Options:"
      echo "  --config <path>            Path to YAML config file (required)"
      echo "  --tables <list>            Comma-separated tables to profile"
      echo "                             (default: from config file)"
      echo "  --task <prompt>            Custom task instead of full DQA pipeline"
      echo "  --resume-from-analysis     Skip profiling, start from issue detection"
      echo "  --resume-from-investigation  Skip to root cause investigation"
      echo "  --resume-from-report       Skip to report generation"
      echo "  <number>                   Max turns per sub-agent (default: 50)"
      exit 0
      ;;
    [0-9]*)
      MAX_TURNS="$1"; shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ -z "$CONFIG" ]]; then
  echo "Error: --config is required." >&2
  echo "Usage: ./run.sh --config config.yaml" >&2
  exit 2
fi

if [[ ! -f "$CONFIG" ]]; then
  echo "Error: Config file not found: $CONFIG" >&2
  exit 2
fi

# ── Load config ──

DB_ID=$(python3 -c "import yaml; print(yaml.safe_load(open('$CONFIG'))['id'])")
DB_NAME=$(python3 -c "import yaml; print(yaml.safe_load(open('$CONFIG'))['name'])")
ETL_REPO=$(python3 -c "import yaml; print(yaml.safe_load(open('$CONFIG'))['sources']['etl']['path'])")
DOC_PATHS=$(python3 -c "
import yaml, json
cfg = yaml.safe_load(open('$CONFIG'))
print(json.dumps(cfg['sources']['documentation']['paths']))
")

if [[ -z "$TABLES" ]]; then
  TABLES=$(python3 -c "
import yaml
cfg = yaml.safe_load(open('$CONFIG'))
print(','.join(cfg.get('tables', ['DEMOGRAPHIC', 'ENCOUNTER', 'DIAGNOSIS'])))
")
fi

if [[ ! -d "$ETL_REPO" ]]; then
  echo "Error: ETL repo not found: $ETL_REPO" >&2
  exit 2
fi

# ── Set up results directory ──

RUN_DATE=$(date +%Y-%m-%d)
RESULTS_DIR="results/${RUN_DATE}_${DB_ID}_dqa"
mkdir -p "$RESULTS_DIR"

echo "============================================="
echo " AutoDQA — Data Quality Assessment"
echo "============================================="
echo " Database:    $DB_NAME ($DB_ID)"
echo " ETL Repo:    $ETL_REPO"
echo " Doc Paths:   $DOC_PATHS"
echo " Tables:      $TABLES"
echo " Results:     $RESULTS_DIR"
echo " Max turns:   $MAX_TURNS"
if [[ -n "$RESUME_MODE" ]]; then
  echo " Resume from: $RESUME_MODE"
fi
if [[ -n "$CUSTOM_TASK" ]]; then
  echo " Custom task: $CUSTOM_TASK"
fi
echo "============================================="
echo ""

# ── Configure tools ──

MCP_CONFIG="$SCRIPT_DIR/.mcp.json"

WORKER_TOOLS="Bash(read-only),Read,Write,Edit"
WORKER_TOOLS="$WORKER_TOOLS,mcp__sql_executor__execute_sql"
WORKER_TOOLS="$WORKER_TOOLS,mcp__etl_reader__search_etl,mcp__etl_reader__read_etl_file,mcp__etl_reader__list_etl_files"
WORKER_TOOLS="$WORKER_TOOLS,mcp__spec_query__get_table_spec,mcp__spec_query__get_valuesets,mcp__spec_query__get_constraints"
WORKER_TOOLS="$WORKER_TOOLS,mcp__doc_search__search_docs,mcp__doc_search__read_doc,mcp__doc_search__list_docs"

REVIEWER_TOOLS="Bash(read-only),Read"
REVIEWER_TOOLS="$REVIEWER_TOOLS,mcp__sql_executor__execute_sql"
REVIEWER_TOOLS="$REVIEWER_TOOLS,mcp__etl_reader__search_etl,mcp__etl_reader__read_etl_file"
REVIEWER_TOOLS="$REVIEWER_TOOLS,mcp__doc_search__search_docs,mcp__doc_search__read_doc"

# ── Export environment for coordinator and MCP servers ──

export CONFIG="$SCRIPT_DIR/$CONFIG"
export ETL_REPO="$ETL_REPO"
export DOC_PATHS="$DOC_PATHS"
export RESULTS_DIR="$RESULTS_DIR"
export TABLES="$TABLES"
export MAX_TURNS="$MAX_TURNS"
export MCP_CONFIG="$MCP_CONFIG"
export WORKER_TOOLS="$WORKER_TOOLS"
export REVIEWER_TOOLS="$REVIEWER_TOOLS"

# ── Build coordinator prompt ──

RESUME_INSTRUCTION=""
if [[ -n "$RESUME_MODE" ]]; then
  RESUME_INSTRUCTION="RESUME MODE: --resume-from-$RESUME_MODE was passed. Skip earlier phases and start from the $RESUME_MODE phase. Verify that required input files from previous phases exist before proceeding."
fi

if [[ -n "$CUSTOM_TASK" ]]; then
COORDINATOR_PROMPT=$(cat <<ENDPROMPT
You are an agent with access to a clinical data warehouse and its ETL codebase.

## Run Configuration

- Database: $DB_NAME (id: $DB_ID)
- ETL Repo: $ETL_REPO
- Tables available: $TABLES
- Results directory: $RESULTS_DIR
- Config: $CONFIG
- MCP config: $MCP_CONFIG

## Tools Available

You have MCP tools for:
- Executing SQL queries against the database (mcp__sql_executor__execute_sql)
- Reading ETL code (mcp__etl_reader__read_etl_file, mcp__etl_reader__search_etl, mcp__etl_reader__list_etl_files)
- Querying CDM specifications (mcp__spec_query__get_table_spec, mcp__spec_query__get_valuesets, mcp__spec_query__get_constraints)
- Searching documentation (mcp__doc_search__search_docs, mcp__doc_search__read_doc, mcp__doc_search__list_docs)

## Your Task

$CUSTOM_TASK
ENDPROMPT
)
else
COORDINATOR_PROMPT=$(cat <<ENDPROMPT
You are the coordinator agent for AutoDQA. Read COORDINATOR.md for your full instructions.

## Run Configuration

- Database: $DB_NAME (id: $DB_ID)
- ETL Repo: $ETL_REPO
- Tables to assess: $TABLES
- Results directory: $RESULTS_DIR
- Config: $CONFIG
- MCP config: $MCP_CONFIG
- Max turns per sub-agent: $MAX_TURNS

## Environment Variables Available

These are set in your environment and in sub-agent environments:
- \$RESULTS_DIR = $RESULTS_DIR
- \$ETL_REPO = $ETL_REPO
- \$DOC_PATHS = $DOC_PATHS
- \$CONFIG = $CONFIG
- \$MCP_CONFIG = $MCP_CONFIG
- \$MAX_TURNS = $MAX_TURNS
- \$TABLES = $TABLES
- \$WORKER_TOOLS = (tool allowlist for workers)
- \$REVIEWER_TOOLS = (tool allowlist for reviewers)

$RESUME_INSTRUCTION

Begin by reading COORDINATOR.md, then start Phase 0.
ENDPROMPT
)
fi

# ── Launch coordinator ──

echo "Launching coordinator agent..."
echo ""

echo "$COORDINATOR_PROMPT" | claude -p \
  --verbose \
  --max-turns 200 \
  --output-format stream-json \
  --mcp-config "$MCP_CONFIG" \
  --allowedTools "$WORKER_TOOLS,Write,Edit" \
  2>&1 | python3 tools/stream_viewer.py --label "Coordinator"

echo ""
echo "============================================="
echo " AutoDQA run complete."
echo " Results: $RESULTS_DIR"
echo "============================================="
