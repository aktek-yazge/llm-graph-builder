#!/bin/bash
# Start Celery Workers in Docker - DEV Environment
# Uses docker-compose.yml with .env
# Connects to rabbitmq, postgres-dev in dev Docker network
#
# Usage: ./start-docker.sh [service_name]
# Examples:
#   ./start-docker.sh              # Start all services
#   ./start-docker.sh main_worker  # Start only main worker
#   ./start-docker.sh flower       # Start only flower

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

COMPOSE_FILE="docker-compose.yml"
ENV_FILE=".env"
NETWORK_NAME="workspace_llm-graph-builder_net"

echo "🚀 Starting Celery Workers (DEV)..."
echo "   Compose: $COMPOSE_FILE"
echo "   Env: $ENV_FILE"
echo "   Network: $NETWORK_NAME"
echo ""

# Check if dev network exists
if ! docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
    echo "❌ Error: Network '$NETWORK_NAME' not found!"
    echo ""
    echo "   Please ensure the dev environment is running:"
    echo "   - rabbitmq and postgres-dev should be running"
    echo ""
    exit 1
fi

# Check if .env exists
if [ ! -f "$ENV_FILE" ]; then
    echo "❌ Error: $ENV_FILE not found!"
    echo "   Please create .env from .env.example"
    exit 1
fi

if [ -n "$1" ]; then
    echo "   Service: $1"
    docker compose -f "$COMPOSE_FILE" up -d "$1"
else
    echo "   Services: main_worker, db_writer, flower"
    docker compose -f "$COMPOSE_FILE" up -d
fi

echo ""
echo "✅ Started!"
echo ""
echo "📊 Status:"
docker compose -f "$COMPOSE_FILE" ps
echo ""
echo "🌸 Flower Dashboard: http://localhost:5555"
echo "🐰 RabbitMQ Management: http://localhost:15672 (guest/guest)"
echo ""
echo "📋 Useful commands:"
echo "  docker compose logs -f              # View all logs"
echo "  docker compose logs -f main_worker  # View main worker logs"
echo "  ./stop-docker.sh                    # Stop all workers"
