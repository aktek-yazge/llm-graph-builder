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

# ============================================================================
# LOGGING SETUP - Kalıcı log dosyaları
# ============================================================================
LOG_DIR="${SCRIPT_DIR}/logs"
mkdir -p "$LOG_DIR"

# Log dosyaları - tarih ve saat ile (okunabilir format)
DATE_STAMP=$(date +"%Y-%m-%d_%H-%M-%S")
MAIN_LOG="${LOG_DIR}/main-worker_${DATE_STAMP}.log"
DB_LOG="${LOG_DIR}/db-writer_${DATE_STAMP}.log"
# NEO4J_LOG="${LOG_DIR}/neo4j-writer_${DATE_STAMP}.log"
# FLOWER_LOG="${LOG_DIR}/flower_${DATE_STAMP}.log"
MASTER_LOG="${LOG_DIR}/master_${DATE_STAMP}.log"

# Log fonksiyonu
log() {
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $1"
    echo "$msg"
    echo "$msg" >> "$MASTER_LOG"
}

log "=========================================="
log "🚀 CELERY WORKERS STARTING"
log "==========================================" 

# Set environment variables
export PYTHONPATH="$SCRIPT_DIR"
export ENV=development
# Fix for Mac fork safety with prefork pool
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
export QUEUE_DB_URL=postgresql://postgres:Ekdmjweu483i@3.76.55.209:5432/llm_graph_builder
export CELERY_BROKER_URL=amqp://guest:guest@3.76.55.209:5672//
export CELERY_RESULT_BACKEND=db+postgresql://postgres:Ekdmjweu483i@3.76.55.209:5432/llm_graph_builder

# Neo4j Timeout & Performance Settings
# Uzak Neo4j sunucuları için timeout değerleri artırıldı
export NEO4J_CONNECTION_TIMEOUT="${NEO4J_CONNECTION_TIMEOUT:-60}"
export NEO4J_READ_TIMEOUT="${NEO4J_READ_TIMEOUT:-300}"
export NEO4J_WRITE_TIMEOUT="${NEO4J_WRITE_TIMEOUT:-300}"
export NEO4J_MAX_RETRIES="${NEO4J_MAX_RETRIES:-5}"
export NEO4J_RETRY_DELAY="${NEO4J_RETRY_DELAY:-3}"

# Chunk işleme batch boyutu - küçük değer = daha az timeout riski
export CHUNK_CREATION_BATCH_SIZE="${CHUNK_CREATION_BATCH_SIZE:-20}"

# Change to the script directory
cd "$SCRIPT_DIR"

# Pool configuration
# Note: 'prefork' supports Grow/Shrink but crashes on Mac with Python 3.13
# 'threads' is more stable on Mac but doesn't support runtime pool resizing
# 'gevent' supports Grow/Shrink and is stable (requires: pip install gevent)
POOL_TYPE="${CELERY_POOL:-threads}"
MAIN_CONCURRENCY="${CELERY_CONCURRENCY:-16}"
# DB/Neo4j writers use low concurrency to prevent connection issues
WRITER_CONCURRENCY="${CELERY_WRITER_CONCURRENCY:-1}"

log "🚀 Starting Celery Workers with DB Write Queue Architecture..."
log "📊 Pool: ${POOL_TYPE}"
log "📊 Main Worker Concurrency: ${MAIN_CONCURRENCY}"
log "📊 DB/Neo4j Writer Concurrency: ${WRITER_CONCURRENCY}"
# log "📊 Flower dashboard: http://localhost:5555"
log "📁 Log directory: ${LOG_DIR}"
log ""

# Generate unique worker IDs
MAIN_WORKER_ID="main-worker-${RANDOM}"
DB_WRITER_ID="db-writer-${RANDOM}"
NEO4J_WRITER_ID="neo4j-writer-${RANDOM}"

# Start Main Worker - handles celery and default queues (chunking, graph, embeddings)
log "🔧 Starting Main Worker (${MAIN_WORKER_ID})..."
uv run python -m celery -A src.celery_app worker \
    --loglevel=info \
    --pool=${POOL_TYPE} \
    --concurrency=${MAIN_CONCURRENCY} \
    -Q celery \
    -E \
    -n "${MAIN_WORKER_ID}@%h" 2>&1 | tee -a "$MAIN_LOG" &
MAIN_WORKER_PID=$!
log "✅ Main Worker PID: $MAIN_WORKER_PID, Log: $MAIN_LOG"

# Start DB Writer Worker - dedicated for PostgreSQL writes
log "🗄️ Starting DB Writer (${DB_WRITER_ID})..."
uv run python -m celery -A src.celery_app worker \
    --loglevel=info \
    --pool=${POOL_TYPE} \
    --concurrency=${WRITER_CONCURRENCY} \
    -Q db_write \
    -E \
    -n "${DB_WRITER_ID}@%h" 2>&1 | tee -a "$DB_LOG" &
DB_WRITER_PID=$!
log "✅ DB Writer PID: $DB_WRITER_PID, Log: $DB_LOG"

# ⚠️ DISABLED: main_worker doğrudan Neo4j'ye yazıyor
# Neo4j Writer Worker - dedicated for Neo4j writes
# log "🔗 Starting Neo4j Writer (${NEO4J_WRITER_ID})..."
# uv run python -m celery -A src.celery_app worker \
#     --loglevel=info \
#     --pool=${POOL_TYPE} \
#     --concurrency=${WRITER_CONCURRENCY} \
#     -Q neo4j_write \
#     -E \
#     -n "${NEO4J_WRITER_ID}@%h" 2>&1 | tee -a "$NEO4J_LOG" &
# NEO4J_WRITER_PID=$!
# log "✅ Neo4j Writer PID: $NEO4J_WRITER_PID, Log: $NEO4J_LOG"
NEO4J_WRITER_PID=""

# Start Flower only if port 5555 is not in use
# Enable unauthenticated API for Grow/Shrink pool controls
# export FLOWER_UNAUTHENTICATED_API=true
# if ! nc -z localhost 5555 2>/dev/null; then
#     uv run python -m celery -A src.celery_app flower --port=5555 2>&1 | tee -a "$FLOWER_LOG" &
#     FLOWER_PID=$!
#     log "✅ Flower started (PID: $FLOWER_PID), Log: $FLOWER_LOG"
# else
#     log "🌸 Flower is already running on port 5555 (skipping)"
#     FLOWER_PID=""
# fi

# log ""
# log "📋 Worker Summary:"
# log "   Main Worker:   PID=$MAIN_WORKER_PID, Queue=celery, Concurrency=$MAIN_CONCURRENCY"
# log "   DB Writer:     PID=$DB_WRITER_PID, Queue=db_write, Concurrency=$WRITER_CONCURRENCY"
# # log "   Neo4j Writer:  PID=$NEO4J_WRITER_PID, Queue=neo4j_write, Concurrency=$WRITER_CONCURRENCY"
# log "   Neo4j Writer:  DISABLED (main_worker handles Neo4j writes)"
# log ""
# log "Press Ctrl+C to stop all workers..."

# ============================================================================
# WORKER MONITORING - Worker ölümlerini tespit et
# ============================================================================
monitor_workers() {
    while true; do
        sleep 30  # Her 30 saniyede kontrol et
        
        # Main Worker kontrolü
        if ! kill -0 $MAIN_WORKER_PID 2>/dev/null; then
            log "💀 ALERT: Main Worker (PID: $MAIN_WORKER_PID) DIED!"
            log "   Check log: $MAIN_LOG"
            log "   Last 20 lines:"
            tail -20 "$MAIN_LOG" >> "$MASTER_LOG" 2>/dev/null
        fi
        
        # DB Writer kontrolü
        if ! kill -0 $DB_WRITER_PID 2>/dev/null; then
            log "💀 ALERT: DB Writer (PID: $DB_WRITER_PID) DIED!"
            log "   Check log: $DB_LOG"
        fi
        
        # Neo4j Writer kontrolü (disabled - main_worker handles Neo4j writes)
        # if [ -n "$NEO4J_WRITER_PID" ] && ! kill -0 $NEO4J_WRITER_PID 2>/dev/null; then
        #     log "💀 ALERT: Neo4j Writer (PID: $NEO4J_WRITER_PID) DIED!"
        #     log "   Check log: $NEO4J_LOG"
        # fi
    done
}

# Monitoring'i arka planda başlat
monitor_workers &
MONITOR_PID=$!

# Trap Ctrl+C and kill all processes
cleanup() {
    log ""
    log "🛑 Stopping all workers..."
    
    # Monitor'u durdur
    kill $MONITOR_PID 2>/dev/null
    
    # 1. Önce worker isimlerine göre öldür (en güvenilir yöntem)
    # Bu, uv run'ın spawn ettiği child process'leri de yakalar
    pkill -9 -f "celery.*${MAIN_WORKER_ID}" 2>/dev/null
    pkill -9 -f "celery.*${DB_WRITER_ID}" 2>/dev/null
    # pkill -9 -f "celery.*${NEO4J_WRITER_ID}" 2>/dev/null
    # pkill -9 -f "celery.*flower.*5555" 2>/dev/null
    
    # 2. Kayıtlı PID'leri ve child'larını öldür (yedek)
    for pid in $MAIN_WORKER_PID $DB_WRITER_PID; do
        if [ -n "$pid" ]; then
            # Child process'leri öldür
            pkill -9 -P $pid 2>/dev/null
            # Parent'ı öldür
            kill -9 $pid 2>/dev/null
        fi
    done
    
    # 3. Kısa bekle ve kalan varsa temizle
    sleep 0.5
    
    # Bu script'in başlattığı tüm uv/celery process'lerini temizle
    pkill -9 -f "uv run.*celery.*${MAIN_WORKER_ID}" 2>/dev/null
    pkill -9 -f "uv run.*celery.*${DB_WRITER_ID}" 2>/dev/null
    # pkill -9 -f "uv run.*celery.*${NEO4J_WRITER_ID}" 2>/dev/null
    
    log "✅ All workers stopped."
    log "📁 Logs saved in: $LOG_DIR"
    exit 0
}
trap cleanup INT TERM HUP  # HUP = terminal kapandığında da cleanup çalışsın

# Wait for all processes
wait
