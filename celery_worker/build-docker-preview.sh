#!/bin/bash
# Build Celery Worker Docker Image
# Usage: ./build-docker-preview.sh [--no-cache]

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

COMPOSE_FILE="docker-compose.preview.yml"

echo "🔨 Building Celery Worker Docker Image..."

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
echo "  ./start-docker-preview.sh    # Start all workers (preview)"
echo "  ./stop-docker-preview.sh     # Stop all workers (preview)"
