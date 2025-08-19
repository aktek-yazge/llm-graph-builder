#!/bin/bash

# LLM Graph Builder Backend Server Stopper Script
# Bu script çalışan uvicorn server'ları durdurur

echo "=== LLM Graph Builder Backend Server Stopper ==="
echo "Timestamp: $(date)"

# Çalışan uvicorn process'lerini bul
UVICORN_PROCESSES=$(pgrep -f "uvicorn score:app")

if [ -z "$UVICORN_PROCESSES" ]; then
    echo "ℹ️  No running uvicorn processes found"
    exit 0
fi

echo "🔍 Found running uvicorn process(es):"
ps -p $UVICORN_PROCESSES -o pid,ppid,cmd

echo "🛑 Stopping uvicorn server(s)..."

# Graceful shutdown (SIGTERM)
echo "Sending SIGTERM signal..."
pkill -TERM -f "uvicorn score:app"

# 5 saniye bekle
sleep 5

# Hala çalışıyor mu kontrol et
REMAINING_PROCESSES=$(pgrep -f "uvicorn score:app")

if [ ! -z "$REMAINING_PROCESSES" ]; then
    echo "⚠️  Some processes still running, sending SIGKILL..."
    pkill -KILL -f "uvicorn score:app"
    sleep 2
    
    # Son kontrol
    FINAL_CHECK=$(pgrep -f "uvicorn score:app")
    if [ ! -z "$FINAL_CHECK" ]; then
        echo "❌ Failed to stop some processes: $FINAL_CHECK"
        exit 1
    fi
fi

echo "✅ All uvicorn processes stopped successfully"

# Log dosyasının durumunu göster
BACKEND_DIR="/home/ubuntu/llm-graph-builder/backend"
if [ -f "$BACKEND_DIR/nohup.out" ]; then
    echo "📄 Log file still available at: $BACKEND_DIR/nohup.out"
    echo "📊 Log file size: $(du -h $BACKEND_DIR/nohup.out | cut -f1)"
    echo "🕐 Last log entries:"
    tail -5 "$BACKEND_DIR/nohup.out"
fi

echo "=== Server shutdown completed ==="
