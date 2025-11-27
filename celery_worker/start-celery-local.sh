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



# Generate a unique worker ID to allow multiple instances
WORKER_ID="worker-${RANDOM}"

echo "🚀 Starting Celery Worker (${WORKER_ID}) with MPS support..."
echo "📊 Flower dashboard will be available at: http://localhost:5555"
echo ""

# Start Celery worker in background
# Start Celery worker with autoscaling
# Min 2 processes, Max 10 processes (since tasks are I/O bound and GPU-free)
uv run python -m celery -A src.celery_app worker --loglevel=info --autoscale=10,2 -E -n "${WORKER_ID}@%h" &
WORKER_PID=$!

# Start Flower only if port 5555 is not in use
if ! nc -z localhost 5555 2>/dev/null; then
    uv run python -m celery -A src.celery_app flower --port=5555 &
    FLOWER_PID=$!
    echo "✅ Flower started (PID: $FLOWER_PID)"
else
    echo "🌸 Flower is already running on port 5555 (skipping)"
    FLOWER_PID=""
fi

echo "✅ Celery Worker PID: $WORKER_PID"
echo ""
echo "Press Ctrl+C to stop..."

# Trap Ctrl+C and kill processes
cleanup() {
    echo ""
    echo "🛑 Stopping services..."
    kill $WORKER_PID 2>/dev/null
    if [ -n "$FLOWER_PID" ]; then
        kill $FLOWER_PID 2>/dev/null
    fi
    exit
}
trap cleanup INT

# Wait for both processes
wait
