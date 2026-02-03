#!/bin/bash
# =============================================================================
# Infrastructure Secrets Export Script
# =============================================================================
# Bu script .env dosyasındaki değişkenleri Infisical'a yükler.
#
# KULLANIM:
#   # Önce .env dosyasını oluşturun
#   cp .env.example .env
#   # Değerleri doldurun
#   nano .env
#   # Script'i çalıştırın
#   ./export-secrets-to-infisical.sh
#
# ÖN KOŞULLAR:
#   - Infisical CLI kurulu olmalı
#   - infisical login yapılmış olmalı
#   - INFISICAL_PROJECT_ID set edilmiş olmalı
# =============================================================================

set -e

# Renk kodları
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Infisical ayarları
INFISICAL_ENV="${INFISICAL_ENV:-staging}"
INFISICAL_PATH="${INFISICAL_PATH:-/infrastructure}"

echo -e "${YELLOW}Infrastructure Secrets Export${NC}"
echo "========================================"
echo "Environment: $INFISICAL_ENV"
echo "Path: $INFISICAL_PATH"
echo ""

# .env dosyasını kontrol et
if [ ! -f .env ]; then
    echo -e "${RED}HATA: .env dosyası bulunamadı!${NC}"
    echo "Önce .env.example dosyasını kopyalayın:"
    echo "  cp .env.example .env"
    exit 1
fi

# Infisical CLI kontrolü
if ! command -v infisical &> /dev/null; then
    echo -e "${RED}HATA: Infisical CLI kurulu değil!${NC}"
    echo "Kurulum: https://infisical.com/docs/cli/overview"
    exit 1
fi

# .env dosyasını yükle
source .env

# Export edilecek değişkenler
VARIABLES=(
    "BASE_DOMAIN"
    "RABBITMQ_USER"
    "RABBITMQ_PASSWORD"
    "LOKI_URL"
    "TEMPO_URL"
    "PROMETHEUS_REMOTE_WRITE_URL"
)

echo -e "${GREEN}Değişkenler Infisical'a yükleniyor...${NC}"
echo ""

for var in "${VARIABLES[@]}"; do
    value="${!var}"
    if [ -n "$value" ]; then
        echo -n "  $var... "
        infisical secrets set "$var=$value" \
            --env="$INFISICAL_ENV" \
            --path="$INFISICAL_PATH" \
            --silent 2>/dev/null && echo -e "${GREEN}✓${NC}" || echo -e "${RED}✗${NC}"
    else
        echo -e "  $var... ${YELLOW}(boş, atlandı)${NC}"
    fi
done

echo ""
echo -e "${GREEN}Tamamlandı!${NC}"
echo ""
echo "Kullanım:"
echo "  infisical run --env=$INFISICAL_ENV --path=$INFISICAL_PATH -- docker compose up -d"
