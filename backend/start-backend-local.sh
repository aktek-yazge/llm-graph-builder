#!/bin/bash
# Start Backend API Server (local development)

# Determine project root (directory containing this script)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"

# Export required environment variables
export PYTHONPATH="$SCRIPT_DIR:$PROJECT_ROOT"
export ENV=development
# PostgreSQL/RabbitMQ bağlantıları (.env'den gelir - fallback yok, eksikse hata verir)

# Change to backend directory
cd "$SCRIPT_DIR"

echo "🚀 Starting Backend API Server..."
echo "📡 API will be available at: http://0.0.0.0:8000"
echo "📊 API docs will be available at: http://0.0.0.0:8000/docs"
echo ""

# Worker count for parallel request handling
# Higher = more parallel uploads, but more memory usage
WORKERS="${BACKEND_WORKERS:-1}"

echo "👥 Workers: ${WORKERS}"
echo ""

# Start uvicorn with multiple workers for parallel upload handling
# NOTE: --reload is incompatible with --workers, so we use --workers only
# For development with hot-reload, comment out --workers line and uncomment --reload line
uv run uvicorn score:app --host 0.0.0.0 --port 8000 --workers ${WORKERS} --log-level info

# Development mode with hot-reload (single worker):
# uv run uvicorn score:app --host 0.0.0.0 --port 8000 --reload --log-level debug






