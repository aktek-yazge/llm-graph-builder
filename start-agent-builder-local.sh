#!/bin/bash
# Start Agent Builder backend from the repository root.
#
# Usage:
#   ./start-agent-builder-local.sh
#   ./start-agent-builder-local.sh 8002
#   AGENT_BUILDER_HOST=127.0.0.1 ./start-agent-builder-local.sh

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
BACKEND_START_SCRIPT="${SCRIPT_DIR}/agent-builder/backend/start-local.sh"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    cat <<'EOF'
Start Agent Builder backend in local development mode.

Usage:
  ./start-agent-builder-local.sh [port]

Examples:
  ./start-agent-builder-local.sh
  ./start-agent-builder-local.sh 8002
  AGENT_BUILDER_PORT=8003 ./start-agent-builder-local.sh
  AGENT_BUILDER_HOST=127.0.0.1 ./start-agent-builder-local.sh

This wrapper delegates to:
  agent-builder/backend/start-local.sh
EOF
    exit 0
fi

if [[ ! -f "$BACKEND_START_SCRIPT" ]]; then
    echo "ERROR: Agent Builder backend start script not found: $BACKEND_START_SCRIPT" >&2
    exit 1
fi

exec bash "$BACKEND_START_SCRIPT" "$@"
