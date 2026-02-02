#!/bin/bash
# =============================================================================
# INFISICAL ILE DOCKER COMPOSE CALISTIRMA
# =============================================================================
#
# Kullanım:
#   ./scripts/run-with-infisical.sh [environment] [compose-file] [project-name] [path]
#
# Örnekler:
#   # Production backend
#   ./scripts/run-with-infisical.sh production backend/docker-compose.yml
#
#   # Preview tenant
#   ./scripts/run-with-infisical.sh preview backend/docker-compose.preview.yml akkok-sicil /tenants/akkok-sicil
#
#   # Development
#   ./scripts/run-with-infisical.sh development backend/docker-compose.yml
#
# =============================================================================

# Renkli output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Token dosyasından oku (eğer INFISICAL_TOKEN set edilmemişse)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(dirname "$SCRIPT_DIR")"
TOKEN_FILE="$WORKSPACE_DIR/.infisical-token"

if [ -z "$INFISICAL_TOKEN" ] && [ -f "$TOKEN_FILE" ]; then
    export INFISICAL_TOKEN=$(cat "$TOKEN_FILE" | tr -d '\n')
    echo -e "${GREEN}Token dosyasından yüklendi${NC}"
fi

# Parametreler
ENV=${1:-dev}
COMPOSE_FILE=${2:-backend/docker-compose.yml}
PROJECT_NAME=${3:-}
SECRET_PATH=${4:-/}

echo -e "${GREEN}=== Infisical Docker Compose Runner ===${NC}"
echo -e "Environment: ${YELLOW}$ENV${NC}"
echo -e "Compose File: ${YELLOW}$COMPOSE_FILE${NC}"
echo -e "Secret Path: ${YELLOW}$SECRET_PATH${NC}"

# Infisical CLI kontrolü
if ! command -v infisical &> /dev/null; then
    echo -e "${RED}Infisical CLI bulunamadı!${NC}"
    echo "Kurulum için:"
    echo "  curl -1sLf 'https://dl.cloudsmith.io/public/infisical/infisical-cli/setup.deb.sh' | sudo -E bash"
    echo "  sudo apt-get update && sudo apt-get install -y infisical"
    exit 1
fi

# Token kontrolü
if [ -z "$INFISICAL_TOKEN" ]; then
    echo -e "${RED}INFISICAL_TOKEN bulunamadı!${NC}"
    echo -e "Token dosyası oluşturun: echo 'YOUR_TOKEN' > $TOKEN_FILE"
    exit 1
fi

# Self-hosted domain ve project ID
INFISICAL_DOMAIN=${INFISICAL_DOMAIN:-http://infisical-server:8080}
INFISICAL_PROJECT_ID=${INFISICAL_PROJECT_ID:-1d1e766c-b94d-4ff2-8bba-4c4188b74410}

# Docker Compose komutu oluştur
COMPOSE_CMD="docker compose -f $COMPOSE_FILE"

if [ -n "$PROJECT_NAME" ]; then
    COMPOSE_CMD="$COMPOSE_CMD -p $PROJECT_NAME"
    echo -e "Project Name: ${YELLOW}$PROJECT_NAME${NC}"
fi

# Infisical ile çalıştır
echo -e "${GREEN}Secretlar yükleniyor ve container'lar başlatılıyor...${NC}"
echo -e "Domain: ${YELLOW}$INFISICAL_DOMAIN${NC}"
echo -e "Project ID: ${YELLOW}$INFISICAL_PROJECT_ID${NC}"

infisical run --env="$ENV" --path="$SECRET_PATH" --domain="$INFISICAL_DOMAIN" --projectId="$INFISICAL_PROJECT_ID" -- $COMPOSE_CMD up -d

echo -e "${GREEN}Tamamlandı!${NC}"
echo ""
echo "Container durumu:"
$COMPOSE_CMD ps
