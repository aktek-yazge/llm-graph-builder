#!/bin/bash
# Start Backend API Server (local development)

# Determine project root (directory containing this script)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"

# Export required environment variables
export PYTHONPATH="$SCRIPT_DIR:$PROJECT_ROOT"
export ENV=development
# Use the same postgres database as celery_worker
export QUEUE_DB_URL="postgresql://postgres:postgres@localhost:5432/llm_graph_builder"
export CELERY_BROKER_URL="amqp://guest:guest@localhost:5672//"
export CELERY_RESULT_BACKEND="db+postgresql://postgres:postgres@localhost:5432/llm_graph_builder"

# Change to backend directory
cd "$SCRIPT_DIR"

echo "🚀 Starting Backend API Server..."
echo "📡 API will be available at: http://0.0.0.0:8000"
echo "📊 API docs will be available at: http://0.0.0.0:8000/docs"
echo ""

# Start uvicorn
uv run uvicorn score:app --host 0.0.0.0 --port 8000 --reload --log-level debug




