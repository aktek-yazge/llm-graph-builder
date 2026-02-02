#!/bin/bash
# =============================================================================
# .ENV DOSYALARINDAN INFISICAL'A SECRET IMPORT
# =============================================================================
#
# Kullanım:
#   ./scripts/export-secrets-to-infisical.sh [env-file] [environment] [path]
#
# Örnekler:
#   # Backend .env'i production'a import et
#   ./scripts/export-secrets-to-infisical.sh backend/.env production /
#
#   # Celery worker secrets
#   ./scripts/export-secrets-to-infisical.sh celery_worker/.env production /
#
#   # Tenant-specific secrets
#   ./scripts/export-secrets-to-infisical.sh backend/.env.akkok-sicil preview /tenants/akkok-sicil
#
# =============================================================================

# Renkli output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Token dosyasından oku (eğer INFISICAL_TOKEN set edilmemişse)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(dirname "$SCRIPT_DIR")"
TOKEN_FILE="$WORKSPACE_DIR/.infisical-token"

if [ -z "$INFISICAL_TOKEN" ] && [ -f "$TOKEN_FILE" ]; then
    export INFISICAL_TOKEN=$(cat "$TOKEN_FILE" | tr -d '\n')
    echo -e "${GREEN}Token dosyasından yüklendi${NC}"
fi

ENV_FILE=${1:-backend/.env}
ENVIRONMENT=${2:-dev}
SECRET_PATH=${3:-/}

# Self-hosted Infisical configuration
INFISICAL_DOMAIN=${INFISICAL_DOMAIN:-http://infisical-server:8080}
INFISICAL_PROJECT_ID=${INFISICAL_PROJECT_ID:-1d1e766c-b94d-4ff2-8bba-4c4188b74410}

echo -e "${GREEN}=== Infisical Secret Importer ===${NC}"
echo -e "Source: ${YELLOW}$ENV_FILE${NC}"
echo -e "Target Environment: ${YELLOW}$ENVIRONMENT${NC}"
echo -e "Target Path: ${YELLOW}$SECRET_PATH${NC}"
echo -e "Infisical Domain: ${YELLOW}$INFISICAL_DOMAIN${NC}"
echo -e "Project ID: ${YELLOW}$INFISICAL_PROJECT_ID${NC}"
echo ""

# Dosya kontrolü
if [ ! -f "$ENV_FILE" ]; then
    echo -e "${RED}Dosya bulunamadı: $ENV_FILE${NC}"
    exit 1
fi

# Infisical CLI kontrolü
if ! command -v infisical &> /dev/null; then
    echo -e "${RED}Infisical CLI bulunamadı!${NC}"
    exit 1
fi

# Login kontrolü
if [ -z "$INFISICAL_TOKEN" ]; then
    echo -e "${YELLOW}INFISICAL_TOKEN bulunamadı, interaktif login deneniyor...${NC}"
    if ! infisical login --silent 2>/dev/null; then
        echo -e "${RED}Infisical'a login yapılamadı.${NC}"
        exit 1
    fi
fi

echo -e "${GREEN}Secretlar import ediliyor...${NC}"
echo ""

# .env dosyasını parse et ve import et
IMPORTED=0
SKIPPED=0

while IFS= read -r line || [ -n "$line" ]; do
    # Boş satırları ve yorumları atla
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    
    # KEY=VALUE formatını parse et
    if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
        KEY="${BASH_REMATCH[1]}"
        VALUE="${BASH_REMATCH[2]}"
        
        # Tırnak işaretlerini kaldır
        VALUE="${VALUE#\"}"
        VALUE="${VALUE%\"}"
        VALUE="${VALUE#\'}"
        VALUE="${VALUE%\'}"
        
        # Boş değerleri atla
        if [ -z "$VALUE" ]; then
            echo -e "  ${YELLOW}SKIP${NC} $KEY (boş değer)"
            SKIPPED=$((SKIPPED+1))
            continue
        fi
        
        # Infisical'a ekle (output'u gizle, sadece sonucu göster)
        if infisical secrets set "$KEY=$VALUE" --env="$ENVIRONMENT" --path="$SECRET_PATH" --domain="$INFISICAL_DOMAIN" --projectId="$INFISICAL_PROJECT_ID" >/dev/null 2>&1; then
            echo -e "  ${GREEN}OK${NC}   $KEY"
            IMPORTED=$((IMPORTED+1))
        else
            echo -e "  ${RED}FAIL${NC} $KEY"
        fi
    fi
done < "$ENV_FILE"

echo ""
echo -e "${GREEN}Import tamamlandı!${NC}"
echo -e "Imported: ${GREEN}$IMPORTED${NC}"
echo -e "Skipped: ${YELLOW}$SKIPPED${NC}"
