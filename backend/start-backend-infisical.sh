#!/bin/bash
# Start Backend API Server with Infisical Secrets
# 
# Kullanım:
#   ./start-backend-infisical.sh [tenant]
#
# Örnekler:
#   ./start-backend-infisical.sh akkok-sicil
#   ./start-backend-infisical.sh bakim
#   ./start-backend-infisical.sh sigorta
#
# Gereksinimler:
#   - Infisical CLI kurulu olmalı
#   - .infisical-token dosyası workspace root'ta olmalı
#

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
WORKSPACE_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"

# Renkler
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# Tenant parametresi
TENANT="${1:-akkok-sicil}"

echo -e "${GREEN}=== Infisical Backend Starter ===${NC}"
echo -e "Tenant: ${YELLOW}$TENANT${NC}"

# Infisical CLI kontrolü
if ! command -v infisical &> /dev/null; then
    echo -e "${RED}Infisical CLI bulunamadı!${NC}"
    echo "Kurulum: apt-get install infisical"
    exit 1
fi

# Token dosyasından oku
TOKEN_FILE="$WORKSPACE_DIR/.infisical-token"
if [ -z "$INFISICAL_TOKEN" ] && [ -f "$TOKEN_FILE" ]; then
    export INFISICAL_TOKEN=$(cat "$TOKEN_FILE" | tr -d '\n')
    echo -e "${GREEN}Token yüklendi${NC}"
elif [ -z "$INFISICAL_TOKEN" ]; then
    echo -e "${RED}INFISICAL_TOKEN bulunamadı!${NC}"
    echo "Token dosyası: $TOKEN_FILE"
    exit 1
fi

# Infisical konfigürasyonu
INFISICAL_DOMAIN="${INFISICAL_DOMAIN:-http://host.docker.internal:8085}"
INFISICAL_PROJECT_ID="${INFISICAL_PROJECT_ID:-1d1e766c-b94d-4ff2-8bba-4c4188b74410}"
SECRET_PATH="/tenants/$TENANT"

echo -e "Secret Path: ${YELLOW}$SECRET_PATH${NC}"
echo -e "Domain: ${YELLOW}$INFISICAL_DOMAIN${NC}"
echo ""

# Uvicorn'u Infisical ile çalıştır
cd "$SCRIPT_DIR"
export PYTHONPATH="$SCRIPT_DIR:$WORKSPACE_DIR"
export ENV=development
export REACT_DOMAIN="$TENANT"
echo -e "${GREEN}🚀 Starting Backend API Server with Infisical...${NC}"
echo -e "📡 API: http://0.0.0.0:8000"
echo -e "📊 Docs: http://0.0.0.0:8000/docs"
echo ""

infisical run \
    --env=dev \
    --path="$SECRET_PATH" \
    --domain="$INFISICAL_DOMAIN" \
    --projectId="$INFISICAL_PROJECT_ID" \
    -- uv run uvicorn score:app --host 0.0.0.0 --port 8000 --workers 1 --log-level info
