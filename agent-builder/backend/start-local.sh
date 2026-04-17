#!/bin/bash
# Start Agent Builder Backend on host machine (development mode)
#
# Prerequisites:
#   - uv installed
#   - .env file configured (NEO4J, PostgreSQL, MCP Gateway, LLM keys)
#   - PostgreSQL running (EVENT_STORE_DSN)
#   - Neo4j running (ONTOLOGY_NEO4J_URI)
#
# Usage:
#   ./start-local.sh          # default port 8001
#   ./start-local.sh 8002     # custom port

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
WORKSPACE_DIR="$( cd "$SCRIPT_DIR/../.." && pwd )"
FRONTEND_DIR="$( cd "$SCRIPT_DIR/../frontend" && pwd )"

# ============================================================================
# LOGGING
# ============================================================================
LOG_DIR="${SCRIPT_DIR}/logs"
mkdir -p "$LOG_DIR"
DATE_STAMP=$(date +"%Y-%m-%d_%H-%M-%S")
LOG_FILE="${LOG_DIR}/agent-builder_${DATE_STAMP}.log"

log() {
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $1"
    echo "$msg"
    echo "$msg" >> "$LOG_FILE"
}

# ============================================================================
# CONFIGURATION
# ============================================================================
PORT="${1:-${AGENT_BUILDER_PORT:-8001}}"
HOST="${AGENT_BUILDER_HOST:-0.0.0.0}"
FRONTEND_PORT="${FRONTEND_PORT:-3001}"

export PYTHONPATH="$SCRIPT_DIR:$WORKSPACE_DIR"
export ENV="${ENV:-development}"

log "=========================================="
log "AGENT BUILDER BACKEND STARTING"
log "=========================================="
log "Port: ${PORT}"
log "Host: ${HOST}"
log "Dir:  ${SCRIPT_DIR}"
log "Log:  ${LOG_FILE}"
log ""

# ============================================================================
# DEPENDENCY CHECK
# ============================================================================
if ! command -v uv &> /dev/null; then
    log "ERROR: uv not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

if [ ! -f "$SCRIPT_DIR/.env" ]; then
    log "WARNING: .env file not found at $SCRIPT_DIR/.env"
fi

# ============================================================================
# START SERVER
# ============================================================================
cd "$SCRIPT_DIR"

log "Starting uvicorn (reload mode)..."

uv run uvicorn main:app \
    --host "$HOST" \
    --port "$PORT" \
    --reload \
    --log-level info 2>&1 | tee -a "$LOG_FILE" &

SERVER_PID=$!
log "Server PID: $SERVER_PID"
log ""
log "API:  http://localhost:${PORT}"
log "Docs: http://localhost:${PORT}/docs"

# ============================================================================
# START FRONTEND (skip if already running)
# ============================================================================
FRONTEND_PID=""
FRONTEND_STARTED=0

is_port_in_use() {
    local p="$1"
    if command -v lsof &> /dev/null; then
        lsof -iTCP:"$p" -sTCP:LISTEN -nP 2>/dev/null | grep -q LISTEN
    else
        (echo > /dev/tcp/127.0.0.1/"$p") >/dev/null 2>&1
    fi
}

log ""
if is_port_in_use "$FRONTEND_PORT"; then
    log "Frontend already running on port ${FRONTEND_PORT} (skipped)"
else
    if [ -d "$FRONTEND_DIR" ]; then
        log "Starting frontend (vite) on port ${FRONTEND_PORT}..."
        if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
            log "node_modules missing, running npm install..."
            (cd "$FRONTEND_DIR" && npm install 2>&1 | tee -a "$LOG_FILE")
        fi
        (cd "$FRONTEND_DIR" && npm run dev -- --port "$FRONTEND_PORT" 2>&1 | tee -a "$LOG_FILE") &
        FRONTEND_PID=$!
        FRONTEND_STARTED=1
        log "Frontend PID: $FRONTEND_PID"
        log "UI:   http://localhost:${FRONTEND_PORT}"
    else
        log "WARNING: Frontend dir not found at $FRONTEND_DIR (skipped)"
    fi
fi

log ""
log "Press Ctrl+C to stop..."

# ============================================================================
# CLEANUP
# ============================================================================
cleanup() {
    log ""
    log "Stopping Agent Builder..."
    kill $SERVER_PID 2>/dev/null
    pkill -P $SERVER_PID 2>/dev/null
    sleep 0.5
    pkill -9 -f "uvicorn main:app.*${PORT}" 2>/dev/null

    if [ "$FRONTEND_STARTED" = "1" ] && [ -n "$FRONTEND_PID" ]; then
        log "Stopping frontend..."
        kill $FRONTEND_PID 2>/dev/null
        pkill -P $FRONTEND_PID 2>/dev/null
        sleep 0.5
        pkill -9 -f "vite.*--port ${FRONTEND_PORT}" 2>/dev/null
    fi

    log "Stopped."
    exit 0
}
trap cleanup INT TERM HUP

wait
