#!/bin/bash
set -e

echo "🚀 Full-Stack DevContainer kurulum başlıyor..."

# Python environment hazırlığı
echo "🐍 Python environment hazırlanıyor..."
cd /workspace/backend

# Cache kontrolü - sadece requirements değiştiyse yükle
REQUIREMENTS_HASH=$(sha256sum requirements.txt 2>/dev/null | cut -d' ' -f1 || echo "")
CACHE_FILE="/root/.cache/pip/requirements_hash"

if [ -f "$CACHE_FILE" ] && [ "$(cat $CACHE_FILE)" = "$REQUIREMENTS_HASH" ]; then
    echo "✅ Python paketleri cache'den kullanılıyor (değişiklik yok)"
else
    echo "📦 Python paketleri yükleniyor..."
    pip install --upgrade pip
    if [ -f "requirements.txt" ]; then
        pip install -r requirements.txt
        echo "$REQUIREMENTS_HASH" > "$CACHE_FILE"
        echo "✅ Python paketleri yüklendi ve cache'lendi"
    else
        echo "⚠️  requirements.txt bulunamadı"
    fi
fi

# Node.js environment hazırlığı
echo "📦 Node.js environment hazırlanıyor..."
cd /workspace/frontend

# Cache kontrolü - sadece package.json değiştiyse yükle
PACKAGE_HASH=$(sha256sum package.json 2>/dev/null | cut -d' ' -f1 || echo "")
NODE_CACHE_FILE="/workspace/frontend/node_modules/.package_hash"

if [ -f "$NODE_CACHE_FILE" ] && [ "$(cat $NODE_CACHE_FILE)" = "$PACKAGE_HASH" ]; then
    echo "✅ Node.js paketleri cache'den kullanılıyor (değişiklik yok)"
else
    echo "📦 Node.js paketleri yükleniyor..."
    if [ -f "package.json" ]; then
        yarn install
        echo "$PACKAGE_HASH" > "$NODE_CACHE_FILE"
        echo "✅ Node.js paketleri yüklendi ve cache'lendi"
    else
        echo "⚠️  package.json bulunamadı"
    fi
fi

echo ""
echo "✅ Full-Stack DevContainer kurulum tamamlandı!"
echo ""
echo "🎯 Kullanım:"
echo "Backend başlatmak için: /workspace/.devcontainer/start-backend.sh"
echo "Frontend başlatmak için: /workspace/.devcontainer/start-frontend.sh"
echo ""
echo "🔗 Otomatik başlayan servisler:"
echo "- Neo4j Browser: http://localhost:7474 (neo4j/qwerty5555)"
echo "- Qdrant API: http://localhost:6333"
echo ""
echo "🎨 VS Code Tasks kullanabilirsin:"
echo "- Ctrl+Shift+P → Tasks: Run Task → 🚀 Start Backend"
echo "- Ctrl+Shift+P → Tasks: Run Task → 🎨 Start Frontend"
echo ""
echo "💡 Tek container'da hem Python hem Node.js var!"
echo "   Backend: /workspace/backend/"
echo "   Frontend: /workspace/frontend/"
echo ""