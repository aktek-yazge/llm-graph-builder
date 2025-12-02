#!/bin/bash
# Start Celery Workers in Docker - PREVIEW Environment
# Uses docker-compose.preview.yml with .preview.env
# Connects to rabbitmq, postgres in preview Docker network
#
# Usage: ./start-docker-preview.sh [service_name]
# Examples:
#   ./start-docker-preview.sh              # Start all services
#   ./start-docker-preview.sh main_worker  # Start only main worker
#   ./start-docker-preview.sh flower       # Start only flower

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

COMPOSE_FILE="docker-compose.preview.yml"
ENV_FILE=".preview.env"
NETWORK_NAME="llm-graph-builder_preview"

echo "🚀 Starting Celery Workers (PREVIEW)..."
echo "   Compose: $COMPOSE_FILE"
echo "   Env: $ENV_FILE"
echo "   Network: $NETWORK_NAME"
echo ""

# Check if preview network exists
if ! docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
    echo "❌ Error: Network '$NETWORK_NAME' not found!"
    echo ""
    echo "   Please start the preview environment first:"
    echo "   cd .. && docker-compose -f docker-compose.preview.yml up -d rabbitmq postgres"
    echo ""
    exit 1
fi

# Check if .preview.env exists
if [ ! -f "$ENV_FILE" ]; then
    echo "❌ Error: $ENV_FILE not found!"
    echo "   Please create .preview.env from .preview.env.example"
    exit 1
fi

if [ -n "$1" ]; then
    echo "   Service: $1"
    docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d "$1"
else
    echo "   Services: main_worker, db_writer, flower"
    docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d
fi

echo ""
echo "✅ Started!"
echo ""
echo "📊 Status:"
docker compose -f "$COMPOSE_FILE" ps
echo ""
echo "🌸 Flower Dashboard: http://localhost:5556"
echo "🐰 RabbitMQ Management: http://localhost:15672 (guest/guest)"
echo ""
echo "📋 Useful commands:"
echo "  docker compose -f $COMPOSE_FILE logs -f              # View all logs"
echo "  docker compose -f $COMPOSE_FILE logs -f main_worker  # View main worker logs"
echo "  ./stop-docker-preview.sh                             # Stop all workers"
