#!/bin/bash
# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║          Docker Compose with Infisical Secrets                               ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
#
# Kullanım:
#   ./docker-compose-infisical.sh [tenant] [command]
#
# Örnekler:
#   ./docker-compose-infisical.sh akkok-sicil up -d
#   ./docker-compose-infisical.sh akkok-sicil down
#   ./docker-compose-infisical.sh akkok-sicil logs -f main_worker
#   ./docker-compose-infisical.sh akkok-sicil build
#
# Gereksinimler:
#   - Host'ta Infisical CLI kurulu olmalı
#   - .infisical-token dosyası workspace root'ta olmalı
#

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
WORKSPACE_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"

# Renkler
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

# Parametreler
TENANT="${1:-akkok-sicil}"
shift || true  # İlk parametreyi kaldır, geri kalanı docker-compose'a geç
COMPOSE_ARGS="${@:-up -d}"

echo -e "${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║     🐳 Docker Compose + Infisical                            ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "Tenant:  ${YELLOW}$TENANT${NC}"
echo -e "Command: ${CYAN}docker compose $COMPOSE_ARGS${NC}"

# Infisical CLI kontrolü
if ! command -v infisical &> /dev/null; then
    echo -e "${RED}❌ Infisical CLI bulunamadı!${NC}"
    echo "Kurulum: apt-get install infisical"
    exit 1
fi

# Token dosyasından oku
TOKEN_FILE="$WORKSPACE_DIR/.infisical-token"
if [ -z "$INFISICAL_TOKEN" ] && [ -f "$TOKEN_FILE" ]; then
    export INFISICAL_TOKEN=$(cat "$TOKEN_FILE" | tr -d '\n')
    echo -e "${GREEN}✅ Token yüklendi${NC}"
elif [ -z "$INFISICAL_TOKEN" ]; then
    echo -e "${RED}❌ INFISICAL_TOKEN bulunamadı!${NC}"
    echo "Token dosyası: $TOKEN_FILE"
    exit 1
fi

# Infisical konfigürasyonu
INFISICAL_DOMAIN="${INFISICAL_DOMAIN:-http://infisical-server:8080}"
INFISICAL_PROJECT_ID="${INFISICAL_PROJECT_ID:-1d1e766c-b94d-4ff2-8bba-4c4188b74410}"
SECRET_PATH="/tenants/$TENANT"

echo -e "Project:     ${YELLOW}agent-graph-builder${NC}"
echo -e "Secret Path: ${YELLOW}$SECRET_PATH${NC}"
echo ""

cd "$SCRIPT_DIR"

# Docker Compose'u Infisical ile çalıştır
# infisical run env'leri export eder, docker compose bunları kullanır
echo -e "${GREEN}🚀 Starting Docker Compose with Infisical secrets...${NC}"
echo ""

# Celery için ayrı proje adı (infra ile çakışmasın)
CELERY_PROJECT="${TENANT}-celery"

infisical run \
    --env=dev \
    --path="$SECRET_PATH" \
    --domain="$INFISICAL_DOMAIN" \
    --projectId="$INFISICAL_PROJECT_ID" \
    -- docker compose -p "$CELERY_PROJECT" $COMPOSE_ARGS
