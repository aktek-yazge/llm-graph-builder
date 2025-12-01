#!/bin/bash
# Stop Celery Workers in Docker
#
# Usage: ./stop-docker.sh [service_name]
# Examples:
#   ./stop-docker.sh              # Stop all services
#   ./stop-docker.sh main_worker  # Stop only main worker

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo "🛑 Stopping Celery Workers (Docker)..."

if [ -n "$1" ]; then
    echo "   Service: $1"
    docker-compose stop "$1"
else
    docker-compose down
fi

echo ""
echo "✅ Stopped!"

