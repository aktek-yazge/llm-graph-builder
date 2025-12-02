#!/bin/bash
# Start Celery Workers in Docker - PREVIEW Environment
# Uses docker-compose.preview.yml with .preview.env
# Connects to rabbitmq-dev, postgres-dev in Docker network
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

echo "🚀 Starting Celery Workers (PREVIEW)..."
echo "   Compose: $COMPOSE_FILE"
echo "   Env: $ENV_FILE"
echo ""

if [ -n "$1" ]; then
    echo "   Service: $1"
    docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d "$1"
else
    echo "   Services: main_worker, db_writer, neo4j_writer, flower"
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
