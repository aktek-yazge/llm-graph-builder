#!/bin/bash
# Start Celery Worker and Flower on the host (for MPS access on Mac)

# Determine project root (directory containing this script)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$SCRIPT_DIR"
CELERY_WORKER_DIR="$PROJECT_ROOT/celery_worker"

# Export required environment variables
export PYTHONPATH="$CELERY_WORKER_DIR:$PROJECT_ROOT"
export ENV=development
# Use the postgres-dev container as the database host
export QUEUE_DB_URL="postgresql://postgres:postgres@localhost:5432/llm_graph_builder"
export CELERY_BROKER_URL="amqp://guest:guest@localhost:5672//"
export CELERY_RESULT_BACKEND="db+postgresql://postgres:postgres@localhost:5432/llm_graph_builder"

# Change to celery_worker directory
cd "$CELERY_WORKER_DIR"

echo "🚀 Starting Celery Worker (with MPS support)..."
echo "📊 Flower dashboard will be available at: http://localhost:5555"

# Start Celery worker in background
uv run python -m celery -A src.celery_app worker --loglevel=info --concurrency=4 &
WORKER_PID=$!

# Start Flower in background
uv run python -m celery -A src.celery_app flower --port=5555 &
FLOWER_PID=$!

echo "✅ Celery Worker PID: $WORKER_PID"
echo "✅ Flower PID: $FLOWER_PID"

echo "Press Ctrl+C to stop both services..."

# Trap Ctrl+C and kill both processes
trap "echo ''; echo '🛑 Stopping services...'; kill $WORKER_PID $FLOWER_PID; exit" INT

# Wait for both processes
wait






