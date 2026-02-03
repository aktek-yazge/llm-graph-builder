#!/bin/bash
# =============================================================================
# Initial Setup Script
# =============================================================================
# İlk kurulum için .env dosyalarını oluşturur ve ne yapılması gerektiğini gösterir.
#
# KULLANIM:
#   ./setup.sh
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Renk kodları
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║           STAGING ENVIRONMENT SETUP                           ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""

# =============================================================================
# Networks
# =============================================================================
echo -e "${BLUE}[1/4] Network'ler oluşturuluyor...${NC}"

for network in traefik-public observability; do
    if ! docker network ls --format '{{.Name}}' | grep -q "^${network}$"; then
        docker network create "$network"
        echo -e "  ${GREEN}✓${NC} $network oluşturuldu"
    else
        echo -e "  ${BLUE}○${NC} $network zaten var"
    fi
done

# =============================================================================
# .env Dosyaları
# =============================================================================
echo ""
echo -e "${BLUE}[2/4] .env dosyaları oluşturuluyor...${NC}"

NEEDS_EDIT=()

for dir in traefik infrastructure akkok-sicil; do
    if [ -f "$dir/.env.example" ]; then
        if [ ! -f "$dir/.env" ]; then
            cp "$dir/.env.example" "$dir/.env"
            echo -e "  ${YELLOW}!${NC} $dir/.env oluşturuldu (DÜZENLEME GEREKLİ)"
            NEEDS_EDIT+=("$dir/.env")
        else
            echo -e "  ${GREEN}✓${NC} $dir/.env mevcut"
        fi
    fi
done

# =============================================================================
# Dosya İzinleri
# =============================================================================
echo ""
echo -e "${BLUE}[3/4] Script izinleri ayarlanıyor...${NC}"

find . -name "*.sh" -exec chmod +x {} \;
echo -e "  ${GREEN}✓${NC} Tüm .sh dosyaları çalıştırılabilir"

# =============================================================================
# Sonuç
# =============================================================================
echo ""
echo -e "${BLUE}[4/4] Kurulum tamamlandı${NC}"
echo ""

if [ ${#NEEDS_EDIT[@]} -gt 0 ]; then
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${YELLOW}  DÜZENLENMESİ GEREKEN DOSYALAR:${NC}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    for file in "${NEEDS_EDIT[@]}"; do
        echo "  nano $SCRIPT_DIR/$file"
    done
    echo ""
    echo -e "${YELLOW}Şifreleri ve API key'leri doldurun, sonra:${NC}"
    echo ""
    echo "  ./deploy.sh"
    echo ""
else
    echo -e "${GREEN}Tüm .env dosyaları hazır!${NC}"
    echo ""
    echo "Deployment başlatmak için:"
    echo ""
    echo "  ./deploy.sh"
    echo ""
fi
