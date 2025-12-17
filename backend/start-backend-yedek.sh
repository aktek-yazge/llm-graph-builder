#!/bin/bash
# Start Backend API Server (local development)

# Determine project root (directory containing this script)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"

# Export required environment variables
export PYTHONPATH="$SCRIPT_DIR:$PROJECT_ROOT"
export ENV=development
# Use the same postgres database as celery_worker
export QUEUE_DB_URL="postgresql://postgres:Ekdmjweu483i@3.76.55.209:5432/llm_graph_builder"
export CELERY_BROKER_URL="amqp://guest:guest@3.76.55.209:5672//"
export CELERY_RESULT_BACKEND="db+postgresql://postgres:Ekdmjweu483i@3.76.55.209:5432/llm_graph_builder"

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
# MCP Server (Docker Container)
# ==============================
MCP_HOST="${MCP_HTTP_HOST:-127.0.0.1}"
MCP_PORT="${MCP_HTTP_PORT:-8002}"
MCP_CONTAINER_NAME="mcp-neo4j-cypher"

echo "🐳 Checking MCP Server (Docker)..."
echo "   📡 MCP Server: http://${MCP_HOST}:${MCP_PORT}/mcp/"

# Check if Docker container is running
if docker ps --format '{{.Names}}' | grep -q "^${MCP_CONTAINER_NAME}$"; then
    echo "   ✅ MCP Server container is already running"
else
    echo "   ⚠️  MCP Server container is not running"
    echo "   🚀 Starting MCP Server container..."
    
    # Export Neo4j variables for docker-compose
    export NEO4J_URI="${NEO4J_URI:-bolt://host.docker.internal:7688}"
    export NEO4J_USERNAME="${NEO4J_USERNAME:-neo4j}"
    export NEO4J_PASSWORD="${NEO4J_PASSWORD:-password}"
    export NEO4J_DATABASE="${NEO4J_DATABASE:-neo4j}"
    
    # Start the Docker container
    cd "$SCRIPT_DIR/mcp-servers/mcp-neo4j-cypher"
    docker compose up -d --build
    
    # Wait for container to be ready
    echo "   ⏳ Waiting for MCP server to be ready..."
    sleep 5
    
    # Verify container started
    if docker ps --format '{{.Names}}' | grep -q "^${MCP_CONTAINER_NAME}$"; then
        echo "   ✅ MCP Server container started successfully"
    else
        echo "   ❌ Failed to start MCP Server container"
        echo "   💡 Check logs with: docker logs ${MCP_CONTAINER_NAME}"
    fi
    
    cd "$SCRIPT_DIR"
fi

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

# Development mode with hot-reload (single worker):
# uv run uvicorn score:app --host 0.0.0.0 --port 8000 --reload --log-level debug
