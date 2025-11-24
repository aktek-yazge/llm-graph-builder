#!/bin/bash
# Start Celery Worker and Flower on host machine (for MPS access on Mac)

# Set environment variables
# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Set environment variables
export PYTHONPATH="$SCRIPT_DIR"
export ENV=development
export QUEUE_DB_URL=postgresql://postgres:postgres@localhost:5432/llm_graph_builder
export CELERY_BROKER_URL=amqp://guest:guest@localhost:5672//
export CELERY_RESULT_BACKEND=db+postgresql://postgres:postgres@localhost:5432/llm_graph_builder

# Change to the script directory
cd "$SCRIPT_DIR"



echo "🚀 Starting Celery Worker (with MPS support)..."
echo "📊 Flower dashboard will be available at: http://localhost:5555"
echo ""

# Start Celery worker in background
uv run python -m celery -A src.celery_app worker --loglevel=info --concurrency=4 &
WORKER_PID=$!

# Start Flower
uv run python -m celery -A src.celery_app flower --port=5555 &
FLOWER_PID=$!

echo "✅ Celery Worker PID: $WORKER_PID"
echo "✅ Flower PID: $FLOWER_PID"
echo ""
echo "Press Ctrl+C to stop both services..."

# Trap Ctrl+C and kill both processes
trap "echo ''; echo '🛑 Stopping services...'; kill $WORKER_PID $FLOWER_PID; exit" INT

# Wait for both processes
wait
