#!/bin/bash

# LLM Graph Builder Backend Server Starter Script
# Bu script uvicorn server'ı nohup ile arka planda çalıştırır

echo "=== LLM Graph Builder Backend Server Starter ==="
echo "Timestamp: $(date)"

# Conda environment'ı etkinleştir
echo "Activating conda environment: graph-builder"
source ~/miniconda3/etc/profile.d/conda.sh
conda activate graph-builder

# Environment kontrolü
if [[ "$CONDA_DEFAULT_ENV" != "graph-builder" ]]; then
    echo "ERROR: Failed to activate graph-builder environment"
    echo "Current environment: $CONDA_DEFAULT_ENV"
    exit 1
fi

echo "✅ Conda environment activated: $CONDA_DEFAULT_ENV"

# Backend dizinine geç
BACKEND_DIR="/home/ubuntu/llm-graph-builder/backend"
cd "$BACKEND_DIR"

echo "📁 Working directory: $(pwd)"

# Eski nohup.out dosyasını yedekle (varsa)
if [ -f nohup.out ]; then
    BACKUP_FILE="nohup_backup_$(date +%Y%m%d_%H%M%S).out"
    echo "🔄 Backing up existing nohup.out to $BACKUP_FILE"
    mv nohup.out "$BACKUP_FILE"
fi

# Mevcut uvicorn process'lerini kontrol et
EXISTING_PROCESS=$(pgrep -f "uvicorn score:app")
if [ ! -z "$EXISTING_PROCESS" ]; then
    echo "⚠️  Warning: Found existing uvicorn process(es): $EXISTING_PROCESS"
    echo "You may want to stop them first with: pkill -f 'uvicorn score:app'"
    read -p "Continue anyway? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "❌ Cancelled by user"
        exit 1
    fi
fi

# Server'ı nohup ile başlat
echo "🚀 Starting uvicorn server with nohup..."
echo "Command: nohup uvicorn score:app --reload --host 0.0.0.0 --port 8000"

nohup uvicorn score:app --reload --host 0.0.0.0 --port 8000 > nohup.out 2>&1 &

# Process ID'yi al
SERVER_PID=$!

echo "✅ Server started successfully!"
echo "📋 Process ID: $SERVER_PID"
echo "🌐 Server URL: http://localhost:8000"
echo "🏥 Health Check: http://localhost:8000/health"
echo "💬 Chat Endpoint: http://localhost:8000/chat_bot"
echo "📄 Log file: $BACKEND_DIR/nohup.out"

# Birkaç saniye bekle ve process'in çalışıp çalışmadığını kontrol et
sleep 3

if ps -p $SERVER_PID > /dev/null; then
    echo "✅ Server is running (PID: $SERVER_PID)"
    echo "📊 To monitor logs in real-time: tail -f $BACKEND_DIR/nohup.out"
    echo "🛑 To stop server: pkill -f 'uvicorn score:app' or kill $SERVER_PID"
else
    echo "❌ Server failed to start or crashed immediately"
    echo "📄 Check logs: cat $BACKEND_DIR/nohup.out"
    exit 1
fi

echo "=== Server startup completed ==="
