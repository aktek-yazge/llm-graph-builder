#!/bin/bash
# Start Celery Workers and Flower on host machine (for MPS access on Mac)
#
# Architecture:
#   - Main Worker: Handles chunking, graph creation, embeddings (CPU/GPU intensive)
#   - DB Writer: Dedicated worker for PostgreSQL writes (single connection, batch commits)
#   - Neo4j Writer: Dedicated worker for Neo4j writes (single connection, batch writes)
#
# This architecture prevents connection pool exhaustion and ensures data persistence.

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Set environment variables
export PYTHONPATH="$SCRIPT_DIR"
export ENV=development
# Fix for Mac fork safety with prefork pool
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
export QUEUE_DB_URL=postgresql://postgres:postgres@localhost:5432/llm_graph_builder
export CELERY_BROKER_URL=amqp://guest:guest@localhost:5672//
export CELERY_RESULT_BACKEND=db+postgresql://postgres:postgres@localhost:5432/llm_graph_builder

# Change to the script directory
cd "$SCRIPT_DIR"

# Pool configuration
# Note: 'prefork' supports Grow/Shrink but crashes on Mac with Python 3.13
# 'threads' is more stable on Mac but doesn't support runtime pool resizing
# 'gevent' supports Grow/Shrink and is stable (requires: pip install gevent)
POOL_TYPE="${CELERY_POOL:-threads}"
MAIN_CONCURRENCY="${CELERY_CONCURRENCY:-8}"
# DB/Neo4j writers use low concurrency to prevent connection issues
WRITER_CONCURRENCY="${CELERY_WRITER_CONCURRENCY:-2}"

echo "🚀 Starting Celery Workers with DB Write Queue Architecture..."
echo "📊 Pool: ${POOL_TYPE}"
echo "📊 Main Worker Concurrency: ${MAIN_CONCURRENCY}"
echo "📊 DB/Neo4j Writer Concurrency: ${WRITER_CONCURRENCY}"
echo "📊 Flower dashboard: http://localhost:5555"
echo ""

# Generate unique worker IDs
MAIN_WORKER_ID="main-worker-${RANDOM}"
DB_WRITER_ID="db-writer-${RANDOM}"
NEO4J_WRITER_ID="neo4j-writer-${RANDOM}"

# Start Main Worker - handles celery and default queues (chunking, graph, embeddings)
echo "🔧 Starting Main Worker (${MAIN_WORKER_ID})..."
uv run python -m celery -A src.celery_app worker \
    --loglevel=info \
    --pool=${POOL_TYPE} \
    --concurrency=${MAIN_CONCURRENCY} \
    -Q celery,default \
    -E \
    -n "${MAIN_WORKER_ID}@%h" &
MAIN_WORKER_PID=$!
echo "✅ Main Worker PID: $MAIN_WORKER_PID"

# Start DB Writer Worker - dedicated for PostgreSQL writes
echo "🗄️ Starting DB Writer (${DB_WRITER_ID})..."
uv run python -m celery -A src.celery_app worker \
    --loglevel=info \
    --pool=${POOL_TYPE} \
    --concurrency=${WRITER_CONCURRENCY} \
    -Q db_write \
    -E \
    -n "${DB_WRITER_ID}@%h" &
DB_WRITER_PID=$!
echo "✅ DB Writer PID: $DB_WRITER_PID"

# Start Neo4j Writer Worker - dedicated for Neo4j writes
echo "🔗 Starting Neo4j Writer (${NEO4J_WRITER_ID})..."
uv run python -m celery -A src.celery_app worker \
    --loglevel=info \
    --pool=${POOL_TYPE} \
    --concurrency=${WRITER_CONCURRENCY} \
    -Q neo4j_write \
    -E \
    -n "${NEO4J_WRITER_ID}@%h" &
NEO4J_WRITER_PID=$!
echo "✅ Neo4j Writer PID: $NEO4J_WRITER_PID"

# Start Flower only if port 5555 is not in use
# Enable unauthenticated API for Grow/Shrink pool controls
export FLOWER_UNAUTHENTICATED_API=true
if ! nc -z localhost 5555 2>/dev/null; then
    uv run python -m celery -A src.celery_app flower --port=5555 &
    FLOWER_PID=$!
    echo "✅ Flower started (PID: $FLOWER_PID)"
else
    echo "🌸 Flower is already running on port 5555 (skipping)"
    FLOWER_PID=""
fi

echo ""
echo "📋 Worker Summary:"
echo "   Main Worker:   PID=$MAIN_WORKER_PID, Queues=celery,default, Concurrency=$MAIN_CONCURRENCY"
echo "   DB Writer:     PID=$DB_WRITER_PID, Queue=db_write, Concurrency=$WRITER_CONCURRENCY"
echo "   Neo4j Writer:  PID=$NEO4J_WRITER_PID, Queue=neo4j_write, Concurrency=$WRITER_CONCURRENCY"
echo ""
echo "Press Ctrl+C to stop all workers..."

# Trap Ctrl+C and kill all processes
cleanup() {
    echo ""
    echo "🛑 Stopping all workers..."
    kill $MAIN_WORKER_PID 2>/dev/null
    kill $DB_WRITER_PID 2>/dev/null
    kill $NEO4J_WRITER_PID 2>/dev/null
    if [ -n "$FLOWER_PID" ]; then
        kill $FLOWER_PID 2>/dev/null
    fi
    echo "✅ All workers stopped."
    exit
}
trap cleanup INT

# Wait for all processes
wait
