#!/bin/bash
# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                Import Celery Worker Secrets to Infisical                     ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
#
# Kullanım:
#   ./import-to-infisical.sh [tenant] [env_file]
#
# Örnekler:
#   ./import-to-infisical.sh akkok-sicil .env
#   ./import-to-infisical.sh bakim .env.bakim
#
# Not: Infisical'da önce proje ve tenant folder'ı oluşturulmuş olmalı
#

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
CELERY_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"
WORKSPACE_DIR="$( cd "$CELERY_DIR/.." && pwd )"

# Renkler
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

# Parametreler
TENANT="${1:-akkok-sicil}"
ENV_FILE="${2:-.env}"

echo -e "${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║     📦 Celery Worker Secrets -> Infisical Import            ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "Tenant:   ${YELLOW}$TENANT${NC}"
echo -e "Env File: ${CYAN}$ENV_FILE${NC}"

# Infisical CLI kontrolü
if ! command -v infisical &> /dev/null; then
    echo -e "${RED}❌ Infisical CLI bulunamadı!${NC}"
    exit 1
fi

# Env dosyası kontrolü - mutlak veya göreceli path destekle
if [[ "$ENV_FILE" = /* ]]; then
    # Mutlak path
    ENV_PATH="$ENV_FILE"
elif [[ "$ENV_FILE" = ../* ]]; then
    # Göreceli path (../backend/.env.xxx gibi)
    ENV_PATH="$CELERY_DIR/$ENV_FILE"
else
    # Sadece dosya adı
    ENV_PATH="$CELERY_DIR/$ENV_FILE"
fi

if [ ! -f "$ENV_PATH" ]; then
    echo -e "${RED}❌ Env dosyası bulunamadı: $ENV_PATH${NC}"
    exit 1
fi

# Token kontrolü
TOKEN_FILE="$WORKSPACE_DIR/.infisical-token"
if [ -z "$INFISICAL_TOKEN" ] && [ -f "$TOKEN_FILE" ]; then
    export INFISICAL_TOKEN=$(cat "$TOKEN_FILE" | tr -d '\n')
    echo -e "${GREEN}✅ Token yüklendi${NC}"
elif [ -z "$INFISICAL_TOKEN" ]; then
    echo -e "${RED}❌ INFISICAL_TOKEN bulunamadı!${NC}"
    exit 1
fi

# Infisical konfigürasyonu - agent-graph-builder (backend ile aynı proje)
INFISICAL_DOMAIN="${INFISICAL_DOMAIN:-http://infisical-server:8080}"
INFISICAL_PROJECT_ID="${INFISICAL_PROJECT_ID:-1d1e766c-b94d-4ff2-8bba-4c4188b74410}"
SECRET_PATH="/tenants/$TENANT"

echo -e "Project ID:  ${YELLOW}$INFISICAL_PROJECT_ID${NC}"
echo -e "Secret Path: ${YELLOW}$SECRET_PATH${NC}"
echo ""

# Secret'ları oku ve import et
echo -e "${CYAN}📖 Reading secrets from $ENV_FILE...${NC}"

IMPORTED=0
SKIPPED=0

while IFS= read -r line || [ -n "$line" ]; do
    # Boş satırları ve yorumları atla
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    
    # KEY=VALUE formatını parse et
    if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
        KEY="${BASH_REMATCH[1]}"
        VALUE="${BASH_REMATCH[2]}"
        
        # Boş değerleri atla
        if [ -z "$VALUE" ]; then
            echo -e "  ⏭️  ${YELLOW}$KEY${NC} (empty, skipped)"
            ((SKIPPED++))
            continue
        fi
        
        # Secret'ı Infisical'a ekle
        echo -e "  📤 ${GREEN}$KEY${NC}"
        
        infisical secrets set "$KEY=$VALUE" \
            --env=dev \
            --path="$SECRET_PATH" \
            --domain="$INFISICAL_DOMAIN" \
            --projectId="$INFISICAL_PROJECT_ID" \
            --silent 2>/dev/null || {
                echo -e "     ${RED}Failed to set $KEY${NC}"
                continue
            }
        
        ((IMPORTED++))
    fi
done < "$ENV_PATH"

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                    ✅ Import Complete                        ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo -e "  Imported: ${GREEN}$IMPORTED${NC} secrets"
echo -e "  Skipped:  ${YELLOW}$SKIPPED${NC} (empty values)"
echo ""
echo -e "Verify with:"
echo -e "  ${CYAN}infisical secrets --env=dev --path=$SECRET_PATH --projectId=$INFISICAL_PROJECT_ID${NC}"
