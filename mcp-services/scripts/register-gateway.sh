#!/bin/sh
# =============================================================================
# Register the unified MCP server with ContextForge Gateway
# =============================================================================

set -e

GATEWAY_URL="${GATEWAY_URL:-http://localhost:4444}"
GATEWAY_EMAIL="${GATEWAY_EMAIL:-admin@example.com}"
GATEWAY_PASSWORD="${GATEWAY_PASSWORD:-}"
MCP_UNIFIED_URL="${MCP_UNIFIED_URL:-http://mcp-unified:8010/mcp/}"

echo "=== Register Unified MCP Server with Gateway ==="
echo "Gateway:  $GATEWAY_URL"
echo "MCP URL:  $MCP_UNIFIED_URL"

# ---------------------------------------------------------------------------
# Step 1: Authenticate (if password provided)
# ---------------------------------------------------------------------------
AUTH_HEADER=""
if [ -n "$GATEWAY_PASSWORD" ]; then
    echo ""
    echo "Authenticating with gateway..."
    TOKEN=$(curl -s -X POST "${GATEWAY_URL}/auth/email/login" \
        -H "Content-Type: application/json" \
        -d "{\"email\": \"${GATEWAY_EMAIL}\", \"password\": \"${GATEWAY_PASSWORD}\"}" \
        | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)

    if [ -n "$TOKEN" ]; then
        AUTH_HEADER="Authorization: Bearer ${TOKEN}"
        echo "Authenticated successfully."
    else
        echo "WARNING: Authentication failed, proceeding without token..."
    fi
fi

# ---------------------------------------------------------------------------
# Step 2: Register the unified MCP server
# ---------------------------------------------------------------------------
echo ""
echo "Registering llm-graph-builder unified MCP server..."

if [ -n "$AUTH_HEADER" ]; then
    RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${GATEWAY_URL}/admin/gateways" \
        -H "$AUTH_HEADER" \
        -F "name=llm-graph-builder" \
        -F "url=${MCP_UNIFIED_URL}" \
        -F "description=LLM Graph Builder Unified MCP - Neo4j, Embedding, Storage, Prompts" \
        -F "transport=STREAMABLEHTTP" \
        -F "tags=unified,neo4j,embedding,storage,prompts" \
        -F "visibility=public")
else
    RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${GATEWAY_URL}/admin/gateways" \
        -F "name=llm-graph-builder" \
        -F "url=${MCP_UNIFIED_URL}" \
        -F "description=LLM Graph Builder Unified MCP - Neo4j, Embedding, Storage, Prompts" \
        -F "transport=STREAMABLEHTTP" \
        -F "tags=unified,neo4j,embedding,storage,prompts" \
        -F "visibility=public")
fi

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')

if echo "$BODY" | grep -q '"success": true'; then
    echo "Registration successful"
else
    echo "Registration response (HTTP $HTTP_CODE): $BODY"
fi

# ---------------------------------------------------------------------------
# Step 3: List registered servers
# ---------------------------------------------------------------------------
echo ""
echo "=== Registered Gateways ==="
if [ -n "$AUTH_HEADER" ]; then
    curl -s "${GATEWAY_URL}/admin/gateways" -H "$AUTH_HEADER" | head -200
else
    curl -s "${GATEWAY_URL}/admin/gateways" | head -200
fi

echo ""
echo "=== Done ==="
