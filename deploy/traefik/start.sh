#!/bin/bash
# =============================================================================
# Traefik - Start Script
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}Traefik başlatılıyor...${NC}"

# .env kontrolü
if [ ! -f .env ]; then
    echo "UYARI: .env dosyası bulunamadı"
    cp .env.example .env
    echo "HATA: .env dosyasını düzenleyin (özellikle TRAEFIK_DASHBOARD_AUTH)"
    exit 1
fi

# Network kontrolü
if ! docker network ls | grep -q traefik-public; then
    echo "traefik-public network oluşturuluyor..."
    docker network create traefik-public
fi

# acme.json oluştur (Let's Encrypt için)
mkdir -p /opt/traefik-certs
touch /opt/traefik-certs/acme.json
chmod 600 /opt/traefik-certs/acme.json

# Start
docker compose -p traefik up -d

echo ""
echo -e "${GREEN}Traefik başlatıldı!${NC}"
echo ""
echo "Dashboard: https://traefik.\${BASE_DOMAIN}"
echo ""
docker compose -p traefik ps
