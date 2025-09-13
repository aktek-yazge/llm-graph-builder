#!/bin/bash

# 🛑 Backend Stop Script for DevContainer
# Gracefully stops the backend server

echo "🛑 Backend durdurma scripti başlatılıyor..."
echo "📍 Working directory: $(pwd)"
echo "⏰ Timestamp: $(date)"

# Backend directory'ye geç
cd /workspace/backend

# Backend stop script'ini çalıştır
if [ -f "stop_server.sh" ]; then
    echo "🔄 Backend stop script'i çalıştırılıyor..."
    bash stop_server.sh
else
    echo "⚠️  stop_server.sh bulunamadı, manuel durdurma yapılıyor..."
    
    # Manuel olarak uvicorn process'lerini durdur
    echo "🔍 Uvicorn process'leri aranıyor..."
    UVICORN_PIDS=$(pgrep -f "uvicorn.*score:app")
    
    if [ -n "$UVICORN_PIDS" ]; then
        echo "🛑 Uvicorn process'leri durduruluyor (PID: $UVICORN_PIDS)..."
        pkill -f "uvicorn.*score:app"
        sleep 2
        
        # Zorla durdurma gerekirse
        REMAINING_PIDS=$(pgrep -f "uvicorn.*score:app")
        if [ -n "$REMAINING_PIDS" ]; then
            echo "⚡ Zorla durdurma (SIGKILL)..."
            pkill -9 -f "uvicorn.*score:app"
        fi
        
        echo "✅ Backend başarıyla durduruldu"
    else
        echo "ℹ️  Çalışan uvicorn process'i bulunamadı"
    fi
fi

echo "🏁 Backend stop scripti tamamlandı"