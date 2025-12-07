#!/bin/bash
# Start Backend API Server (local development)

# Determine project root (directory containing this script)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"

# Export required environment variables
export PYTHONPATH="$SCRIPT_DIR:$PROJECT_ROOT"
export ENV=development
# Use the same postgres database as celery_worker
export QUEUE_DB_URL="postgresql://postgres:postgres@3.76.55.209:5432/llm_graph_builder"
export CELERY_BROKER_URL="amqp://guest:guest@3.76.55.209:5672//"
export CELERY_RESULT_BACKEND="db+postgresql://postgres:postgres@3.76.55.209:5432/llm_graph_builder"

# Load specific variables from .env file (safer than export all)
if [ -f "$SCRIPT_DIR/.env" ]; then
    # Read only specific needed variables
    NEO4J_URI=$(grep '^NEO4J_URI=' "$SCRIPT_DIR/.env" | cut -d '=' -f2- | tr -d '"' | tr -d "'")
    NEO4J_USERNAME=$(grep '^NEO4J_USERNAME=' "$SCRIPT_DIR/.env" | cut -d '=' -f2- | tr -d '"' | tr -d "'")
    NEO4J_PASSWORD=$(grep '^NEO4J_PASSWORD=' "$SCRIPT_DIR/.env" | cut -d '=' -f2- | tr -d '"' | tr -d "'")
    NEO4J_DATABASE=$(grep '^NEO4J_DATABASE=' "$SCRIPT_DIR/.env" | cut -d '=' -f2- | tr -d '"' | tr -d "'")
fi

# Change to backend directory
cd "$SCRIPT_DIR"

# ==============================
# MCP Server (Neo4j Cypher)
# ==============================
MCP_HOST="${MCP_HTTP_HOST:-127.0.0.1}"
MCP_PORT="${MCP_HTTP_PORT:-8002}"

# Neo4j connection (from .env or defaults)
NEO4J_URI="${NEO4J_URI:-bolt://localhost:7687}"
NEO4J_USERNAME="${NEO4J_USERNAME:-neo4j}"
NEO4J_PASSWORD="${NEO4J_PASSWORD:-password}"
NEO4J_DATABASE="${NEO4J_DATABASE:-neo4j}"

echo "🔌 Starting MCP Server (Neo4j Cypher)..."
echo "   📡 MCP Server: http://${MCP_HOST}:${MCP_PORT}/mcp/"

# Start MCP server in background (logs to same terminal as backend)
cd "$SCRIPT_DIR/mcp-servers/mcp-neo4j-cypher/src"
uv run python -m mcp_neo4j_cypher \
    --transport http \
    --server-host "$MCP_HOST" \
    --server-port "$MCP_PORT" \
    --db-url "$NEO4J_URI" \
    --username "$NEO4J_USERNAME" \
    --password "$NEO4J_PASSWORD" \
    --database "$NEO4J_DATABASE" 2>&1 &

MCP_PID=$!
echo "   ✅ MCP Server started (PID: $MCP_PID)"

# Wait for MCP server to be ready
echo "   ⏳ Waiting for MCP server to be ready..."
sleep 3

# Back to backend directory
cd "$SCRIPT_DIR"

# Cleanup function - kill MCP server when script exits
cleanup() {
    echo ""
    echo "🛑 Shutting down..."
    if [ -n "$MCP_PID" ] && kill -0 "$MCP_PID" 2>/dev/null; then
        echo "   Stopping MCP Server (PID: $MCP_PID)..."
        kill "$MCP_PID" 2>/dev/null
        wait "$MCP_PID" 2>/dev/null
        echo "   ✅ MCP Server stopped"
    fi
    exit 0
}

# Trap SIGINT (Ctrl+C) and SIGTERM
trap cleanup SIGINT SIGTERM

echo ""
echo "🚀 Starting Backend API Server..."
echo "📡 API will be available at: http://0.0.0.0:8000"
echo "📊 API docs will be available at: http://0.0.0.0:8000/docs"
echo ""

# Worker count for parallel request handling
# Higher = more parallel uploads, but more memory usage
WORKERS="${BACKEND_WORKERS:-1}"

echo "👥 Workers: ${WORKERS}"
echo ""

# Start uvicorn with multiple workers for parallel upload handling
# NOTE: --reload is incompatible with --workers, so we use --workers only
# For development with hot-reload, comment out --workers line and uncomment --reload line
uv run uvicorn score:app --host 0.0.0.0 --port 8000 --workers ${WORKERS} --log-level info

# Wait for cleanup
cleanup

# Development mode with hot-reload (single worker):
# uv run uvicorn score:app --host 0.0.0.0 --port 8000 --reload --log-level debug






