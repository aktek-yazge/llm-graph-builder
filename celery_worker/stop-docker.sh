#!/bin/bash
# Stop Celery Workers in Docker - DEV Environment
#
# Usage: ./stop-docker.sh [service_name]
# Examples:
#   ./stop-docker.sh              # Stop all services
#   ./stop-docker.sh main_worker  # Stop only main worker

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

COMPOSE_FILE="docker-compose.yml"

echo "🛑 Stopping Celery Workers (DEV)..."

if [ -n "$1" ]; then
    echo "   Service: $1"
    docker compose -f "$COMPOSE_FILE" stop "$1"
else
    docker compose -f "$COMPOSE_FILE" down
fi

echo ""
echo "✅ Stopped!"
