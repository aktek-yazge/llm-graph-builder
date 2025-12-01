#!/bin/bash
# Celery Worker Docker Image Build Script
# Usage: ./build.sh [--no-cache]

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo "🔨 Building Celery Worker Docker Image..."

if [ "$1" == "--no-cache" ]; then
    echo "   (no-cache mode)"
    docker-compose build --no-cache celery_base
else
    docker-compose build celery_base
fi

echo ""
echo "✅ Build complete!"
echo ""
echo "📦 Image: celery-worker:latest"
echo ""
echo "Next steps:"
echo "  ./start-docker.sh    # Start all workers"
echo "  ./stop-docker.sh     # Stop all workers"

