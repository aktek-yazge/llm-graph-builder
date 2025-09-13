#!/bin/bash
# Backend başlatma script'i

cd /workspace/backend

echo "🚀 Backend başlatılıyor..."
echo "📍 Working directory: $(pwd)"
echo "🐍 Python version: $(python --version)"
echo "🔗 API URL: http://localhost:8000"
echo ""

# Environment variables'ı load et
if [ -f ".env" ]; then
    echo "📄 .env dosyası yükleniyor..."
    export $(cat .env | grep -v '^#' | xargs)
fi

# Score.py ana dosyasına göre başlat
echo "🎯 Backend başlatılıyor (Ctrl+C ile durdur)..."
if [ -f "score.py" ]; then
    uvicorn score:app --host 0.0.0.0 --port 8000 --reload --log-level debug
elif [ -f "main.py" ]; then
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload --log-level debug
else
    echo "❌ score.py veya main.py dosyası bulunamadı!"
    echo "📂 Mevcut dosyalar:"
    ls -la *.py 2>/dev/null || echo "Python dosyası bulunamadı"
fi