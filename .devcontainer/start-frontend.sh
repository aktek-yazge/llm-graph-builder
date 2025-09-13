#!/bin/bash
# Frontend başlatma script'i

cd /workspace/frontend

echo "🚀 Frontend başlatılıyor..."
echo "📍 Working directory: $(pwd)"
echo "📦 Node version: $(node --version)"
echo "🔗 Dev Server URL: http://localhost:5173"
echo ""

# Environment variables'ı load et
if [ -f ".env" ]; then
    echo "📄 .env dosyası yükleniyor..."
    export $(cat .env | grep -v '^#' | xargs)
fi

# Package.json varlığını kontrol et
if [ -f "package.json" ]; then
    echo "🎯 Frontend başlatılıyor (Ctrl+C ile durdur)..."
    yarn dev --host 0.0.0.0 --port 5173
else
    echo "❌ package.json dosyası bulunamadı!"
    echo "📂 Mevcut dosyalar:"
    ls -la
fi