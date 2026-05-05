#!/usr/bin/env bash
# =============================================================================
# AutoDQA — Launch Script
# =============================================================================
# Launches the coordinator agent for autonomous data quality assessment.
#
# Usage:
#   ./run.sh --db-config databases/my_cdw.yaml
#   ./run.sh --db-config databases/my_cdw.yaml --tables DEMOGRAPHIC,ENCOUNTER
#   ./run.sh --db-config databases/my_cdw.yaml --resume-from-report
#   ./run.sh --db-config databases/my_cdw.yaml 75  # max turns
#
# Prerequisites:
#   - Claude Code CLI installed (npm install -g @anthropic-ai/claude-code)
#   - Python 3.11+ with: pip install mcp pyodbc pyyaml
#   - ANTHROPIC_API_KEY set in environment
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Preflight checks ──

if ! command -v claude &>/dev/null; then
  echo "Error: Claude Code CLI not found." >&2
  echo "Install with: npm install -g @anthropic-ai/claude-code" >&2
  exit 1
fi

if ! python -c "import mcp, pyodbc, yaml" 2>/dev/null; then
  PY_PATH="$(command -v python 2>/dev/null || echo '<python not on PATH>')"
  echo "Error: MCP server dependencies missing from $PY_PATH" >&2
  echo "Install with:" >&2
  echo "  $PY_PATH -m pip install mcp pyodbc pyyaml" >&2
  exit 1
fi

# ── Parse arguments ──

DB_CONFIG=""
TABLES=""
RESUME_MODE=""
MAX_TURNS="50"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --db-config)
      DB_CONFIG="$2"; shift 2
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
    --help|-h)
      echo "Usage: ./run.sh --db-config <path> [--tables TABLE,TABLE,...] [--resume-from-*] [max_turns]"
      echo ""
      echo "Options:"
      echo "  --db-config <path>         Path to database YAML config (required)"
      echo "  --tables <list>            Comma-separated tables to profile"
      echo "                             (default: from config file)"
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

if [[ -z "$DB_CONFIG" ]]; then
  echo "Error: --db-config is required." >&2
  echo "Usage: ./run.sh --db-config databases/my_cdw.yaml" >&2
  exit 2
fi

if [[ ! -f "$DB_CONFIG" ]]; then
  echo "Error: Config file not found: $DB_CONFIG" >&2
  exit 2
fi

# ── Load config ──

DB_ID=$(python -c "import yaml; print(yaml.safe_load(open('$DB_CONFIG'))['id'])")
DB_NAME=$(python -c "import yaml; print(yaml.safe_load(open('$DB_CONFIG'))['name'])")
ETL_REPO=$(python -c "import yaml; print(yaml.safe_load(open('$DB_CONFIG'))['etl_repo'])")

if [[ -z "$TABLES" ]]; then
  TABLES=$(python -c "
import yaml
cfg = yaml.safe_load(open('$DB_CONFIG'))
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
echo " Tables:      $TABLES"
echo " Results:     $RESULTS_DIR"
echo " Max turns:   $MAX_TURNS"
if [[ -n "$RESUME_MODE" ]]; then
  echo " Resume from: $RESUME_MODE"
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

export DB_CONFIG="$SCRIPT_DIR/$DB_CONFIG"
export ETL_REPO="$ETL_REPO"
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

COORDINATOR_PROMPT=$(cat <<ENDPROMPT
You are the coordinator agent for AutoDQA. Read COORDINATOR.md for your full instructions.

## Run Configuration

- Database: $DB_NAME (id: $DB_ID)
- ETL Repo: $ETL_REPO
- Tables to assess: $TABLES
- Results directory: $RESULTS_DIR
- DB config: $DB_CONFIG
- MCP config: $MCP_CONFIG
- Max turns per sub-agent: $MAX_TURNS

## Environment Variables Available

These are set in your environment and in sub-agent environments:
- \$RESULTS_DIR = $RESULTS_DIR
- \$ETL_REPO = $ETL_REPO
- \$DB_CONFIG = $DB_CONFIG
- \$MCP_CONFIG = $MCP_CONFIG
- \$MAX_TURNS = $MAX_TURNS
- \$TABLES = $TABLES
- \$WORKER_TOOLS = (tool allowlist for workers)
- \$REVIEWER_TOOLS = (tool allowlist for reviewers)

$RESUME_INSTRUCTION

Begin by reading COORDINATOR.md, then start Phase 0.
ENDPROMPT
)

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
