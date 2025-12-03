#!/bin/bash
# Start Celery Workers in Docker - PREVIEW Environment
# Uses docker-compose.preview.yml with .preview.env
# Connects to rabbitmq, postgres in preview Docker network
#
# Usage: ./start-docker-preview.sh [service_name] [--recreate]
# Examples:
#   ./start-docker-preview.sh              # Start all services
#   ./start-docker-preview.sh main_worker  # Start only main worker
#   ./start-docker-preview.sh flower       # Start only flower
#   ./start-docker-preview.sh --recreate   # Force recreate all (env changes)
#   ./start-docker-preview.sh main_worker --recreate  # Force recreate specific service

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

# --force-recreate: Env değişiklikleri için container'ları yeniden oluştur
RECREATE_FLAG=""
if [ "$2" == "--recreate" ] || [ "$1" == "--recreate" ]; then
    RECREATE_FLAG="--force-recreate"
    echo "   Mode: Force recreate"
fi

if [ -n "$1" ] && [ "$1" != "--recreate" ]; then
    echo "   Service: $1"
    docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d $RECREATE_FLAG "$1"
else
    echo "   Services: main_worker, db_writer, flower"
    docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d $RECREATE_FLAG
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
