#!/bin/bash
# =============================================================================
# Infrastructure Stack - Start Script
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Renk kodları
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}Infrastructure Stack başlatılıyor...${NC}"
echo ""

# .env kontrolü
if [ ! -f .env ]; then
    echo "UYARI: .env dosyası bulunamadı, .env.example kullanılacak"
    cp .env.example .env
fi

# Traefik network kontrolü
if ! docker network ls | grep -q traefik-public; then
    echo "traefik-public network oluşturuluyor..."
    docker network create traefik-public
fi

# Docker Compose up
docker compose -p infrastructure up -d

echo ""
echo -e "${GREEN}Infrastructure Stack başlatıldı!${NC}"
echo ""
echo "Servisler:"
echo "  - RabbitMQ:     http://localhost:15672"
echo "  - Alloy UI:     http://localhost:12345"
echo "  - OTLP gRPC:    localhost:4317"
echo "  - OTLP HTTP:    localhost:4318"
