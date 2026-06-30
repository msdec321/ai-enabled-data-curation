#!/usr/bin/env bash
# Deploy the AutoDQA agent to Bedrock AgentCore Runtime.
#
# Wires every var from .env into `agentcore launch --env` flags, so secrets stay
# in the gitignored .env (never duplicated into a command or shell history).
# Uses the autodqa-admin profile + us-east-1.
#
# Usage:  ./deploy.sh            # configure (first run) + launch
#         ./deploy.sh invoke '<json>'   # convenience: invoke after deploy
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$SCRIPT_DIR"

# The agentcore CLI lives in the project venv, not on the system PATH (and the
# WSL PATH leaks Windows binaries) — call it explicitly. Fall back to PATH.
AGENTCORE="$REPO_ROOT/.venv/bin/agentcore"
[ -x "$AGENTCORE" ] || AGENTCORE="agentcore"

export AWS_PROFILE="${AWS_PROFILE:-autodqa-admin}"
export AWS_REGION="${AWS_REGION:-us-east-1}"
export AGENTCORE_SUPPRESS_RECOMMENDATION=1   # silence the @aws/agentcore CLI nag

[ -f .env ] || { echo "ERROR: agentcore-runtime/.env not found"; exit 1; }

if [ "${1:-}" = "invoke" ]; then
  exec "$AGENTCORE" invoke "${2:?usage: ./deploy.sh invoke '<json payload>'}"
fi

# Build --env flags from .env (skip comments/blank lines and unset values).
envargs=()
while IFS= read -r line || [ -n "$line" ]; do
  line="${line%$'\r'}"
  case "$line" in ''|\#*) continue ;; esac
  [[ "$line" == *=* ]] || continue
  key="${line%%=*}"; val="${line#*=}"
  key="${key//[[:space:]]/}"
  [ -z "$val" ] && continue          # e.g. a still-blank secret
  envargs+=( --env "$key=$val" )
done < .env

# One-time setup (creates the execution role + ECR repo + local config). Safe to
# re-run; skipped once .bedrock_agentcore.yaml exists. May prompt on first run.
if [ ! -f .bedrock_agentcore.yaml ]; then
  echo ">> agentcore configure (one-time) ..."
  "$AGENTCORE" configure -e entrypoint.py -n autodqa -r "$AWS_REGION" \
    --non-interactive --disable-memory
fi

echo ">> agentcore launch  (CodeBuild ARM64 in the cloud — creates billable AWS resources)"
echo "   profile=$AWS_PROFILE region=$AWS_REGION  env vars wired: $(( ${#envargs[@]} / 2 ))"
"$AGENTCORE" launch -a autodqa --auto-update-on-conflict "${envargs[@]}"

cat <<'EOF'

Done. Smoke-test it with:
  ./deploy.sh invoke '{"task": "Use run_python to compute 6*7 and report it."}'
EOF
