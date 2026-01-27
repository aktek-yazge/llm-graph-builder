#!/bin/bash
set -e

echo "🚀 Full-Stack DevContainer kurulum başlıyor..."

# Python environment hazırlığı (uv ile pyproject.toml kullanarak)
echo "🐍 Python environment hazırlanıyor..."
cd /workspace/backend

# Cache kontrolü - pyproject.toml hash ve paket varlığını kontrol et
PYPROJECT_HASH=$(sha256sum pyproject.toml 2>/dev/null | cut -d' ' -f1 || echo "")
CACHE_DIR="/workspace/.devcontainer/.cache"
CACHE_FILE="$CACHE_DIR/pyproject_hash"
INSTALLED_PACKAGES=$(python3 -m pip list --format=freeze 2>/dev/null | wc -l)

# Cache dizinini oluştur (workspace içinde - rebuild'de korunur)
mkdir -p "$CACHE_DIR"

# Cache geçerli mi kontrol et: hash eşleşmeli + en az 50 paket yüklü olmalı
if [ -f "$CACHE_FILE" ] && [ "$(cat $CACHE_FILE)" = "$PYPROJECT_HASH" ] && [ "$INSTALLED_PACKAGES" -gt "50" ]; then
    echo "✅ Python paketleri cache'den kullanılıyor ($INSTALLED_PACKAGES paket mevcut)"
else
    if [ -f "$CACHE_FILE" ] && [ "$(cat $CACHE_FILE)" = "$PYPROJECT_HASH" ]; then
        echo "⚠️  Hash eşleşiyor ama paketler eksik ($INSTALLED_PACKAGES/~250). Yeniden yükleniyor..."
    else
        echo "📦 pyproject.toml değişti veya cache yok. Python paketleri yükleniyor..."
    fi
    
    # uv ile pyproject.toml'dan paketleri yükle
    if [ -f "pyproject.toml" ]; then
        uv pip install -e . --system --python /usr/bin/python3.13
        echo "$PYPROJECT_HASH" > "$CACHE_FILE"
        FINAL_COUNT=$(python3 -m pip list --format=freeze 2>/dev/null | wc -l)
        echo "✅ Python paketleri yüklendi ve cache'lendi ($FINAL_COUNT paket)"
    else
        echo "⚠️  pyproject.toml bulunamadı"
    fi
fi

# Node.js environment hazırlığı
echo "📦 Node.js environment hazırlanıyor..."
cd /workspace/frontend

# Cache kontrolü - hash ve node_modules varlığını kontrol et
PACKAGE_HASH=$(sha256sum package.json 2>/dev/null | cut -d' ' -f1 || echo "")
NODE_CACHE_FILE="/workspace/frontend/node_modules/.package_hash"

# node_modules var mı ve paket sayısı yeterli mi kontrol et
if [ -d "node_modules" ]; then
    NODE_MODULE_COUNT=$(find node_modules -maxdepth 1 -type d | wc -l)
else
    NODE_MODULE_COUNT=0
fi

if [ -f "$NODE_CACHE_FILE" ] && [ "$(cat $NODE_CACHE_FILE)" = "$PACKAGE_HASH" ] && [ "$NODE_MODULE_COUNT" -gt "100" ]; then
    echo "✅ Node.js paketleri cache'den kullanılıyor ($NODE_MODULE_COUNT modül mevcut)"
else
    if [ -f "$NODE_CACHE_FILE" ] && [ "$(cat $NODE_CACHE_FILE)" = "$PACKAGE_HASH" ]; then
        echo "⚠️  Hash eşleşiyor ama node_modules eksik ($NODE_MODULE_COUNT/~800). Yeniden yükleniyor..."
    else
        echo "📦 Package.json değişti veya cache yok. Node.js paketleri yükleniyor..."
    fi
    
    if [ -f "package.json" ]; then
        yarn install
        echo "$PACKAGE_HASH" > "$NODE_CACHE_FILE"
        FINAL_NODE_COUNT=$(find node_modules -maxdepth 1 -type d | wc -l)
        echo "✅ Node.js paketleri yüklendi ve cache'lendi ($FINAL_NODE_COUNT modül)"
    else
        echo "⚠️  package.json bulunamadı"
    fi
fi

echo ""
echo "✅ Full-Stack DevContainer kurulum tamamlandı!"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🌐 TRAEFIK İLE ERİŞİM (Önerilen)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "⚠️  Önce /etc/hosts dosyasına şu satırı ekleyin:"
echo "   127.0.0.1  dev.local"
echo ""
echo "   Otomatik eklemek için: sudo /workspace/setup-hosts.sh"
echo "   macOS/Linux: sudo nano /etc/hosts"
echo "   Windows: C:\\Windows\\System32\\drivers\\etc\\hosts (Admin olarak)"
echo ""
echo "🔗 WAT Projesi Erişim Noktaları:"
echo "   Frontend:         http://dev.local/wat/ui"
echo "   Backend API:      http://dev.local/wat/server"
echo "   MCP Server:       http://dev.local/wat/mcp"
echo "   Neo4j Browser:    http://dev.local/wat/neo4j (neo4j/Watmotor!654*)"
echo "   Qdrant:           http://dev.local/wat/qdrant"
echo ""
echo "🔗 Altyapı Servisleri:"
echo "   RabbitMQ:         http://dev.local/rabbitmq"
echo "   Traefik Dashboard: http://localhost:8090/dashboard/"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🎯 DİREKT ERİŞİM (Traefik bypass)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "   Backend:          http://localhost:8000"
echo "   Frontend:         http://localhost:5173"
echo "   Neo4j Browser:    http://localhost:7474"
echo "   MCP Server:       http://localhost:8002"
echo "   RabbitMQ:         http://localhost:15672"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🚀 BAŞLATMA KOMUTLARI"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Backend başlatmak için:  /workspace/.devcontainer/start-backend.sh"
echo "Frontend başlatmak için: /workspace/.devcontainer/start-frontend.sh"
echo ""
echo "🎨 VS Code Tasks kullanabilirsin:"
echo "   Ctrl+Shift+P → Tasks: Run Task → 🚀 Start Backend"
echo "   Ctrl+Shift+P → Tasks: Run Task → 🎨 Start Frontend"
echo ""
echo "💡 Tek container'da hem Python hem Node.js var!"
echo "   Backend: /workspace/backend/"
echo "   Frontend: /workspace/frontend/"
echo ""