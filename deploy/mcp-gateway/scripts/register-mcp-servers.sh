#!/bin/bash
# =============================================================================
# MCP Gateway - Register MCP Servers Script
# =============================================================================
# Bu script MCP sunucularını gateway'e kaydeder.
#
# KULLANIM:
#   ./register-mcp-servers.sh
#
# ÖN KOŞUL:
#   - MCP Gateway çalışıyor olmalı
#   - MCP_GATEWAY_JWT_SECRET environment variable tanımlı olmalı
# =============================================================================

set -e

# Configuration
GATEWAY_HOST="${MCP_GATEWAY_HOST:-mcp-gateway}"
GATEWAY_PORT="${MCP_GATEWAY_PORT:-4444}"
GATEWAY_URL="http://${GATEWAY_HOST}:${GATEWAY_PORT}"
ADMIN_EMAIL="${MCP_GATEWAY_ADMIN_EMAIL:-admin@example.com}"
JWT_SECRET="${MCP_GATEWAY_JWT_SECRET}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check prerequisites
if [ -z "$JWT_SECRET" ]; then
    log_error "MCP_GATEWAY_JWT_SECRET is not set"
    exit 1
fi

# Wait for gateway to be healthy
log_info "Waiting for MCP Gateway to be healthy..."
max_attempts=30
attempt=0
while [ $attempt -lt $max_attempts ]; do
    if curl -sf "${GATEWAY_URL}/health" > /dev/null 2>&1; then
        log_info "MCP Gateway is healthy"
        break
    fi
    attempt=$((attempt + 1))
    log_warn "Gateway not ready, waiting... (attempt $attempt/$max_attempts)"
    sleep 2
done

if [ $attempt -eq $max_attempts ]; then
    log_error "Gateway did not become healthy in time"
    exit 1
fi

# Generate JWT token
log_info "Generating JWT token..."
TOKEN=$(docker compose exec -T mcp-gateway python3 -m mcpgateway.utils.create_jwt_token \
    --username "$ADMIN_EMAIL" \
    --exp 10080 \
    --secret "$JWT_SECRET" 2>/dev/null | tr -d '\r\n')

if [ -z "$TOKEN" ]; then
    log_error "Failed to generate JWT token"
    exit 1
fi

log_info "JWT token generated successfully"

# Function to register a gateway
register_gateway() {
    local name="$1"
    local url="$2"
    local description="${3:-}"
    
    log_info "Registering gateway: $name ($url)"
    
    local payload="{\"name\":\"$name\",\"url\":\"$url\""
    if [ -n "$description" ]; then
        payload="$payload,\"description\":\"$description\""
    fi
    payload="$payload}"
    
    response=$(curl -sf -X POST \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        -d "$payload" \
        "${GATEWAY_URL}/gateways" 2>&1) || {
        log_warn "Failed to register $name (may already exist)"
        return 0
    }
    
    log_info "Registered: $name"
    echo "$response" | python3 -m json.tool 2>/dev/null || echo "$response"
}

# =============================================================================
# REGISTER MCP SERVERS
# =============================================================================

log_info "Starting MCP server registration..."

# MCP Neo4j Cypher (Development - docker-compose.shared.yml)
register_gateway \
    "neo4j-cypher" \
    "http://mcp-neo4j-cypher:8002/mcp/" \
    "Neo4j Cypher Query MCP Server"

# Add more MCP servers here as needed:
# register_gateway "server-name" "http://server-host:port/mcp/" "Description"

log_info "MCP server registration complete!"

# List registered gateways
log_info "Listing registered gateways..."
curl -sf -H "Authorization: Bearer $TOKEN" "${GATEWAY_URL}/gateways" | python3 -m json.tool 2>/dev/null || true

# List available tools
log_info "Listing available tools..."
curl -sf -H "Authorization: Bearer $TOKEN" "${GATEWAY_URL}/tools" | python3 -m json.tool 2>/dev/null || true

echo ""
log_info "========================================"
log_info "MCP Gateway Admin UI: ${GATEWAY_URL}/admin"
log_info "MCP Endpoint: ${GATEWAY_URL}/mcp"
log_info "========================================"
