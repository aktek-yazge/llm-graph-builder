#!/bin/sh
# =============================================================================
# MCP Gateway Initialization Script
# Registers MCP servers with the gateway on startup
# =============================================================================

set -e

GATEWAY_URL="${GATEWAY_URL:-http://mcp-gateway:4444}"
MAX_RETRIES=30
RETRY_INTERVAL=5

echo "=== MCP Gateway Initialization ==="
echo "Gateway URL: $GATEWAY_URL"

# Wait for gateway to be ready
echo "Waiting for MCP Gateway to be ready..."
retry_count=0
until curl -s "${GATEWAY_URL}/health" > /dev/null 2>&1; do
    retry_count=$((retry_count + 1))
    if [ $retry_count -ge $MAX_RETRIES ]; then
        echo "ERROR: Gateway did not become ready after ${MAX_RETRIES} retries"
        exit 1
    fi
    echo "Waiting for gateway... (${retry_count}/${MAX_RETRIES})"
    sleep $RETRY_INTERVAL
done

echo "Gateway is ready!"

# =============================================================================
# Register Neo4j Documents Server
# =============================================================================
echo "Registering neo4j-documents server..."
curl -X POST "${GATEWAY_URL}/api/servers" \
    -H "Content-Type: application/json" \
    -d '{
        "server": {
            "name": "neo4j-documents",
            "description": "Neo4j Document Database - Tenant-specific document storage",
            "server_type": "sse",
            "url": "'"${NEO4J_DOCUMENTS_URL:-bolt://neo4j-documents:7687}"'",
            "metadata": {
                "category": "database",
                "capabilities": ["cypher_query", "graph_traversal", "fulltext_search"]
            }
        }
    }' || echo "Warning: neo4j-documents registration may have failed or already exists"

# =============================================================================
# Register Neo4j Ontology Server
# =============================================================================
echo "Registering neo4j-ontology server..."
curl -X POST "${GATEWAY_URL}/api/servers" \
    -H "Content-Type: application/json" \
    -d '{
        "server": {
            "name": "neo4j-ontology",
            "description": "Neo4j Ontology Database - Agent metadata, Goals, Skills, Schemas",
            "server_type": "sse",
            "url": "'"${NEO4J_ONTOLOGY_URL:-bolt://neo4j-ontology:7688}"'",
            "metadata": {
                "category": "ontology",
                "capabilities": ["goal_management", "skill_registry", "schema_storage"]
            }
        }
    }' || echo "Warning: neo4j-ontology registration may have failed or already exists"

# =============================================================================
# Register Embedding Server
# =============================================================================
echo "Registering embedding server..."
curl -X POST "${GATEWAY_URL}/api/servers" \
    -H "Content-Type: application/json" \
    -d '{
        "server": {
            "name": "embedding",
            "description": "Embedding Server - Semantic search and vector operations",
            "server_type": "sse",
            "url": "'"${EMBEDDING_SERVER_URL:-http://embedding-server:8003}"'",
            "metadata": {
                "category": "ml",
                "capabilities": ["text_embedding", "semantic_search", "similarity"]
            }
        }
    }' || echo "Warning: embedding server registration may have failed or already exists"

# =============================================================================
# Register Agent Builder API
# =============================================================================
echo "Registering agent-builder-api server..."
curl -X POST "${GATEWAY_URL}/api/servers" \
    -H "Content-Type: application/json" \
    -d '{
        "server": {
            "name": "agent-builder",
            "description": "Agent Builder API - Create and manage custom agents",
            "server_type": "sse",
            "url": "http://agent-builder-api:8001",
            "metadata": {
                "category": "agent",
                "capabilities": ["agent_creation", "skill_management", "goal_tracking"]
            }
        }
    }' || echo "Warning: agent-builder registration may have failed or already exists"

echo ""
echo "=== MCP Gateway Initialization Complete ==="
echo "Registered servers:"
curl -s "${GATEWAY_URL}/api/servers" | head -100 || echo "Could not list servers"

echo ""
echo "Gateway UI available at: ${GATEWAY_URL}"
