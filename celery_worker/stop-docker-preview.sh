#!/bin/bash
# Stop Celery Workers in Docker - PREVIEW Environment
#
# Usage: ./stop-docker-preview.sh [service_name]
# Examples:
#   ./stop-docker-preview.sh              # Stop all services
#   ./stop-docker-preview.sh main_worker  # Stop only main worker

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

COMPOSE_FILE="docker-compose.preview.yml"

echo "🛑 Stopping Celery Workers (PREVIEW)..."

if [ -n "$1" ]; then
    echo "   Service: $1"
    docker compose -f "$COMPOSE_FILE" stop "$1"
else
    docker compose -f "$COMPOSE_FILE" down
fi

echo ""
echo "✅ Stopped!"
echo ""
echo "📊 Status:"
docker compose -f "$COMPOSE_FILE" ps
echo ""
echo "📋 To restart:"
echo "  ./start-docker-preview.sh    # Start all workers"
