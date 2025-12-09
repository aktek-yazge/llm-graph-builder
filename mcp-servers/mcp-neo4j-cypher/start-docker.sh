#!/bin/bash
# MCP Neo4j Cypher Server - Docker Compose başlatma scripti

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
BACKEND_DIR="$( cd "$SCRIPT_DIR/../.." && pwd )"
CONTAINER_NAME="mcp-neo4j-cypher"

echo "🐳 MCP Neo4j Cypher Server - Docker"

# Backend .env dosyasını kontrol et ve değişkenleri yükle
if [ -f "$BACKEND_DIR/.env" ]; then
    echo "📁 Backend .env dosyası bulundu: $BACKEND_DIR/.env"
    
    # .env dosyasından gerekli değişkenleri export et
    export NEO4J_URI=$(grep '^NEO4J_URI=' "$BACKEND_DIR/.env" | cut -d '=' -f2- | tr -d '"' | tr -d "'")
    export NEO4J_USERNAME=$(grep '^NEO4J_USERNAME=' "$BACKEND_DIR/.env" | cut -d '=' -f2- | tr -d '"' | tr -d "'")
    export NEO4J_PASSWORD=$(grep '^NEO4J_PASSWORD=' "$BACKEND_DIR/.env" | cut -d '=' -f2- | tr -d '"' | tr -d "'")
    export NEO4J_DATABASE=$(grep '^NEO4J_DATABASE=' "$BACKEND_DIR/.env" | cut -d '=' -f2- | tr -d '"' | tr -d "'")
    export OPENAI_API_KEY=$(grep '^OPENAI_API_KEY=' "$BACKEND_DIR/.env" | cut -d '=' -f2- | tr -d '"' | tr -d "'")
    
    echo "   ✅ Neo4j URI: ${NEO4J_URI:-bolt://host.docker.internal:7687}"
    echo "   ✅ Neo4j Database: ${NEO4J_DATABASE:-neo4j}"
fi

# Lokal .env dosyası varsa onu da yükle
if [ -f "$SCRIPT_DIR/.env" ]; then
    echo "📁 Lokal .env dosyası bulundu: $SCRIPT_DIR/.env"
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi

# Port ayarı
MCP_HTTP_PORT="${MCP_HTTP_PORT:-8002}"

cd "$SCRIPT_DIR"

echo ""
echo "🔧 Ayarlar:"
echo "   📡 MCP Server Port: $MCP_HTTP_PORT"
echo "   🗄️  Neo4j URI: ${NEO4J_URI:-bolt://host.docker.internal:7687}"
echo ""

# Docker compose çalıştır
case "${1:-up}" in
    up|start)
        echo "🚀 Container başlatılıyor..."
        # Image yoksa otomatik build yapacak
        docker compose up -d
        echo ""
        echo "✅ MCP Server başlatıldı!"
        echo "📡 Endpoint: http://localhost:${MCP_HTTP_PORT}/mcp/"
        echo ""
        echo "💡 Development için:"
        echo "   ./start-docker.sh dev    # Watch mode (otomatik sync)"
        echo ""
        echo "📋 Logları görmek için: ./start-docker.sh logs"
        ;;
    
    dev|watch)
        echo "🔥 DEVELOPMENT MODE - Docker Compose Watch"
        echo "   Dosya değişiklikleri otomatik sync edilecek!"
        echo ""
        echo "   📂 ./src değişirse -> sync + restart"
        echo "   📦 pyproject.toml değişirse -> rebuild"
        echo ""
        echo "   📋 Logları görmek için başka bir terminalde:"
        echo "      cd $SCRIPT_DIR && docker compose logs -f mcp-neo4j-cypher"
        echo ""
        echo "   Çıkmak için: Ctrl+C"
        echo ""
        # Container zaten çalışıyorsa sadece watch başlat, rebuild yapma
        if docker compose ps | grep -q "mcp-neo4j-cypher.*Up"; then
            echo "   ✅ Container zaten çalışıyor, rebuild yapılmayacak"
        else
            echo "   🚀 Container başlatılıyor (ilk kez ise build yapılacak)..."
            docker compose up -d
        fi
        
        # Sonra watch modunu ayrı çalıştır (logları karıştırmamak için)
        # Dokümantasyona göre: docker compose watch ayrı komut olarak kullanılabilir
        echo "   🔍 Watch modu başlatılıyor..."
        docker compose watch
        ;;
    
    down|stop)
        echo "🛑 Container durduruluyor..."
        docker compose down
        echo "✅ Container durduruldu!"
        ;;
    
    logs)
        docker compose logs -f
        ;;
    
    reload|restart)
        echo "🔄 Container yeniden başlatılıyor..."
        docker compose restart
        echo "✅ Container yeniden başlatıldı!"
        ;;
    
    rebuild)
        echo "🔨 Container yeniden build ediliyor..."
        docker compose down
        docker compose up -d --build
        echo "✅ Container yeniden build edildi ve başlatıldı!"
        ;;
    
    status)
        docker compose ps
        ;;
    
    shell)
        echo "🐚 Container shell'e bağlanılıyor..."
        docker exec -it ${CONTAINER_NAME} /bin/bash
        ;;
    
    *)
        echo "Kullanım: $0 {up|dev|down|logs|reload|rebuild|status|shell}"
        echo ""
        echo "  up/start  - Container'ı arka planda başlat"
        echo "  dev/watch - 🔥 Development mode (dosya değişikliklerini izle)"
        echo "  down/stop - Container'ı durdur"
        echo "  logs      - Logları göster"
        echo "  reload    - Container'ı yeniden başlat"
        echo "  rebuild   - Container'ı yeniden build et"
        echo "  status    - Container durumunu göster"
        echo "  shell     - Container içine bash ile bağlan"
        exit 1
        ;;
esac
