#!/bin/bash
# Build Celery Worker Docker Image - DEV Environment
# Usage: ./build-docker.sh [--no-cache]

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

COMPOSE_FILE="docker-compose.yml"

echo "🔨 Building Celery Worker Docker Image (DEV)..."

if [ "$1" == "--no-cache" ]; then
    echo "   (no-cache mode)"
    docker compose -f "$COMPOSE_FILE" build --no-cache celery_base
else
    docker compose -f "$COMPOSE_FILE" build celery_base
fi

echo ""
echo "✅ Build complete!"
echo ""
echo "📦 Image: celery-worker:latest"
echo ""
echo "Next steps:"
echo "  ./start-docker.sh    # Start all workers (dev)"
echo "  ./stop-docker.sh     # Stop all workers (dev)"

