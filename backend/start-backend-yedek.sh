#!/bin/bash
# Start Backend API Server (local development)
# 
# Kullanım:
#   ./start-backend-yedek.sh [sigorta|bakim]
#
# Örnekler:
#   ./start-backend-yedek.sh           # Default: sigorta
#   ./start-backend-yedek.sh sigorta   # Sigorta ortamı
#   ./start-backend-yedek.sh bakim     # WAT Motor bakım ortamı

# Determine project root (directory containing this script)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"

# ==============================
# Domain/Environment Selection
# ==============================
DOMAIN="${1:-sigorta}"

# Domain validation
if [[ "$DOMAIN" != "sigorta" && "$DOMAIN" != "bakim" ]]; then
    echo "❌ Geçersiz domain: $DOMAIN"
    echo "   Kullanım: ./start-backend-yedek.sh [sigorta|bakim]"
    exit 1
fi

# İlgili .env dosyasını yükle
ENV_FILE="${SCRIPT_DIR}/.env.${DOMAIN}"
if [[ -f "$ENV_FILE" ]]; then
    echo "📁 Loading environment: $ENV_FILE"
    set -a  # Export all variables
    source "$ENV_FILE"
    set +a
    echo "🎯 Domain: ${REACT_DOMAIN:-$DOMAIN}"
else
    echo "⚠️  Dosya bulunamadı: $ENV_FILE"
    echo "   Default .env kullanılacak (varsa)"
    # Fallback: REACT_DOMAIN'i manuel set et
    export REACT_DOMAIN="$DOMAIN"
fi

echo ""

# Export required environment variables
export PYTHONPATH="$SCRIPT_DIR:$PROJECT_ROOT"
export ENV=development
# Use the same postgres database as celery_worker (override edilmemişse)
export QUEUE_DB_URL="${QUEUE_DB_URL:-postgresql://postgres:Ekdmjweu483i@3.76.55.209:5432/llm_graph_builder}"
export CELERY_BROKER_URL="${CELERY_BROKER_URL:-amqp://guest:guest@3.76.55.209:5672//}"
export CELERY_RESULT_BACKEND="${CELERY_RESULT_BACKEND:-db+postgresql://postgres:Ekdmjweu483i@3.76.55.209:5432/llm_graph_builder}"



# ==============================
# MCP Server (Docker Container)
# Domain'e göre farklı port ve container
# - sigorta: mcp-neo4j-cypher (port 8002)
# - bakim:   mcp-neo4j-cypher-bakim (port 8003)
# ==============================
MCP_HOST="${MCP_HTTP_HOST:-127.0.0.1}"

# Domain'e göre MCP port ve container belirle
if [[ "$DOMAIN" == "bakim" ]]; then
    MCP_PORT="${MCP_HTTP_PORT:-8003}"
    MCP_CONTAINER_NAME="mcp-neo4j-cypher-bakim"
    # WAT Motor Neo4j defaults
    DEFAULT_NEO4J_URI="bolt://host.docker.internal:7688"
    DEFAULT_NEO4J_USER="neo4j"
    DEFAULT_NEO4J_PASS="Watmotor!654*"
else
    MCP_PORT="${MCP_HTTP_PORT:-8002}"
    MCP_CONTAINER_NAME="mcp-neo4j-cypher"
    # Sigorta Neo4j defaults (uzak sunucu)
    DEFAULT_NEO4J_URI="bolt://3.76.55.209:7688"
    DEFAULT_NEO4J_USER="neo4j"
    DEFAULT_NEO4J_PASS="qwerty5555"
fi

echo "🐳 Checking MCP Server (Docker) for domain: $DOMAIN..."
echo "   📡 MCP Server: http://${MCP_HOST}:${MCP_PORT}/mcp/"
echo "   🏷️  Container: ${MCP_CONTAINER_NAME}"

# Check if Docker container is running
if docker ps --format '{{.Names}}' | grep -q "^${MCP_CONTAINER_NAME}$"; then
    echo "   ✅ MCP Server container is already running"
else
    echo "   ⚠️  MCP Server container is not running"
    echo "   🚀 Starting MCP Server container..."
    
    # MCP container için localhost -> host.docker.internal çevir
    # (Docker container içinden host'a erişmek için)
    MCP_NEO4J_URI="${NEO4J_URI:-$DEFAULT_NEO4J_URI}"
    MCP_NEO4J_URI="${MCP_NEO4J_URI//localhost/host.docker.internal}"
    MCP_NEO4J_URI="${MCP_NEO4J_URI//127.0.0.1/host.docker.internal}"
    
    # MCP için ayrı env variable export et (compose bunu kullanır)
    export MCP_NEO4J_URI="$MCP_NEO4J_URI"
    export NEO4J_USERNAME="${NEO4J_USERNAME:-$DEFAULT_NEO4J_USER}"
    export NEO4J_PASSWORD="${NEO4J_PASSWORD:-$DEFAULT_NEO4J_PASS}"
    export NEO4J_DATABASE="${NEO4J_DATABASE:-neo4j}"
    export MCP_PORT="$MCP_PORT"
    export MCP_CONTAINER_NAME="$MCP_CONTAINER_NAME"
    
    echo "   🔗 Neo4j URI (MCP): ${MCP_NEO4J_URI}"
    
    # Start the Docker container with domain-specific settings
    cd "$PROJECT_ROOT/mcp-servers/mcp-neo4j-cypher"
    
    # Domain'e göre farklı compose project name kullan
    COMPOSE_PROJECT="${MCP_CONTAINER_NAME}"
    docker compose -p "$COMPOSE_PROJECT" up -d --build
    
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

# MCP HTTP URL'i backend için export et
export MCP_HTTP_URL="http://${MCP_HOST}:${MCP_PORT}/mcp/"

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
