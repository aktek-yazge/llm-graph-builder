#!/bin/bash
# =============================================================================
# AKKOK-SİCİL - Staging Start Script
# =============================================================================
# Kullanım:
#   ./start.sh                    # Infisical ile başlat
#   ./start.sh --env-file .env    # Manuel .env dosyası ile
#   ./start.sh --build            # Rebuild ile başlat
#   ./start.sh --logs             # Başlat ve logları takip et
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Defaults
USE_INFISICAL=true
BUILD=false
FOLLOW_LOGS=false
ENV_FILE=""
INFISICAL_ENV="staging"
INFISICAL_PATH="/tenants/akkok-sicil"
COMPOSE_PROJECT="akkok-sicil"
COMPOSE_FILE="docker-compose.staging.yml"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --env-file)
            ENV_FILE="$2"
            USE_INFISICAL=false
            shift 2
            ;;
        --build)
            BUILD=true
            shift
            ;;
        --logs)
            FOLLOW_LOGS=true
            shift
            ;;
        --help)
            echo "Kullanım: ./start.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --env-file FILE   Manuel .env dosyası kullan"
            echo "  --build           Container'ları rebuild et"
            echo "  --logs            Başlattıktan sonra logları takip et"
            echo "  --help            Bu yardım mesajını göster"
            exit 0
            ;;
        *)
            echo -e "${RED}Bilinmeyen parametre: $1${NC}"
            exit 1
            ;;
    esac
done

echo -e "${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║          AKKOK-SİCİL - Staging Deployment                    ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"

# Check prerequisites
echo -e "\n${YELLOW}🔍 Önkoşullar kontrol ediliyor...${NC}"

# Check Docker
if ! command -v docker &> /dev/null; then
    echo -e "${RED}❌ Docker bulunamadı. Lütfen Docker kurun.${NC}"
    exit 1
fi
echo -e "${GREEN}✅ Docker mevcut${NC}"

# Check Docker Compose
if ! docker compose version &> /dev/null; then
    echo -e "${RED}❌ Docker Compose bulunamadı.${NC}"
    exit 1
fi
echo -e "${GREEN}✅ Docker Compose mevcut${NC}"

# Check networks
for network in traefik-public observability; do
    if ! docker network inspect $network &> /dev/null; then
        echo -e "${YELLOW}⚠️  Network '$network' bulunamadı, oluşturuluyor...${NC}"
        docker network create $network
    fi
done
echo -e "${GREEN}✅ Docker network'ler mevcut${NC}"

# Build command
COMPOSE_CMD="docker compose -p $COMPOSE_PROJECT -f $COMPOSE_FILE"

if [ "$BUILD" = true ]; then
    COMPOSE_CMD="$COMPOSE_CMD build --no-cache &&"
fi

COMPOSE_CMD="$COMPOSE_CMD up -d"

# Execute
echo -e "\n${YELLOW}🚀 Sistem başlatılıyor...${NC}"

if [ "$USE_INFISICAL" = true ]; then
    # Check Infisical
    if ! command -v infisical &> /dev/null; then
        echo -e "${RED}❌ Infisical CLI bulunamadı.${NC}"
        echo -e "${YELLOW}Kurulum için: curl -1sLf 'https://dl.cloudsmith.io/public/infisical/infisical-cli/setup.deb.sh' | sudo -E bash && sudo apt-get install -y infisical${NC}"
        exit 1
    fi
    echo -e "${GREEN}✅ Infisical CLI mevcut${NC}"
    
    echo -e "${YELLOW}📦 Infisical secrets yükleniyor (env=$INFISICAL_ENV, path=$INFISICAL_PATH)...${NC}"
    
    if [ "$BUILD" = true ]; then
        infisical run --env=$INFISICAL_ENV --path=$INFISICAL_PATH -- \
            docker compose -p $COMPOSE_PROJECT -f $COMPOSE_FILE build --no-cache
    fi
    
    infisical run --env=$INFISICAL_ENV --path=$INFISICAL_PATH -- \
        docker compose -p $COMPOSE_PROJECT -f $COMPOSE_FILE up -d
else
    if [ -z "$ENV_FILE" ]; then
        ENV_FILE=".env"
    fi
    
    if [ ! -f "$ENV_FILE" ]; then
        echo -e "${RED}❌ .env dosyası bulunamadı: $ENV_FILE${NC}"
        echo -e "${YELLOW}Örnek: cp .env.example .env${NC}"
        exit 1
    fi
    
    echo -e "${GREEN}✅ ENV dosyası: $ENV_FILE${NC}"
    
    if [ "$BUILD" = true ]; then
        docker compose -p $COMPOSE_PROJECT -f $COMPOSE_FILE --env-file $ENV_FILE build --no-cache
    fi
    
    docker compose -p $COMPOSE_PROJECT -f $COMPOSE_FILE --env-file $ENV_FILE up -d
fi

# Wait for services
echo -e "\n${YELLOW}⏳ Servisler başlatılıyor...${NC}"
sleep 5

# Check status
echo -e "\n${YELLOW}📊 Servis durumları:${NC}"
docker compose -p $COMPOSE_PROJECT ps

# Health check
echo -e "\n${YELLOW}🏥 Sağlık kontrolü...${NC}"
sleep 10

HEALTHY=true
for service in postgres redis rabbitmq; do
    CONTAINER="akkok-sicil-$service"
    STATUS=$(docker inspect --format='{{.State.Health.Status}}' $CONTAINER 2>/dev/null || echo "unknown")
    if [ "$STATUS" = "healthy" ]; then
        echo -e "${GREEN}✅ $CONTAINER: healthy${NC}"
    elif [ "$STATUS" = "unknown" ]; then
        echo -e "${YELLOW}⚠️  $CONTAINER: no healthcheck${NC}"
    else
        echo -e "${RED}❌ $CONTAINER: $STATUS${NC}"
        HEALTHY=false
    fi
done

if [ "$HEALTHY" = true ]; then
    echo -e "\n${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║                    ✅ BAŞARILI!                              ║${NC}"
    echo -e "${GREEN}╠══════════════════════════════════════════════════════════════╣${NC}"
    echo -e "${GREEN}║ Frontend:  https://akkok-sicil.yazge.aktekbilisim.com        ║${NC}"
    echo -e "${GREEN}║ Backend:   https://akkok-sicil.yazge.aktekbilisim.com/server ║${NC}"
    echo -e "${GREEN}║ Neo4j:     https://akkok-sicil.yazge.aktekbilisim.com/neo4j  ║${NC}"
    echo -e "${GREEN}║ Flower:    https://akkok-sicil.yazge.aktekbilisim.com/flower ║${NC}"
    echo -e "${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"
else
    echo -e "\n${RED}⚠️  Bazı servisler henüz hazır değil. Logları kontrol edin:${NC}"
    echo -e "${YELLOW}docker compose -p $COMPOSE_PROJECT logs -f${NC}"
fi

# Follow logs if requested
if [ "$FOLLOW_LOGS" = true ]; then
    echo -e "\n${YELLOW}📋 Loglar takip ediliyor...${NC}"
    docker compose -p $COMPOSE_PROJECT logs -f
fi
