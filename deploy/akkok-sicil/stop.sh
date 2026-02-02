#!/bin/bash
# =============================================================================
# AKKOK-SİCİL - Stop Script
# =============================================================================
# Kullanım:
#   ./stop.sh              # Durdur (verileri koru)
#   ./stop.sh --clean      # Durdur ve volume'ları sil (DİKKAT!)
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

COMPOSE_PROJECT="akkok-sicil"
COMPOSE_FILE="docker-compose.staging.yml"
CLEAN=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --clean)
            CLEAN=true
            shift
            ;;
        --help)
            echo "Kullanım: ./stop.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --clean   Volume'ları da sil (DİKKAT: Veri kaybı!)"
            echo "  --help    Bu yardım mesajını göster"
            exit 0
            ;;
        *)
            echo -e "${RED}Bilinmeyen parametre: $1${NC}"
            exit 1
            ;;
    esac
done

echo -e "${YELLOW}🛑 AKKOK-SİCİL durduruluyor...${NC}"

if [ "$CLEAN" = true ]; then
    echo -e "${RED}⚠️  UYARI: Volume'lar silinecek! Tüm veriler kaybolacak!${NC}"
    read -p "Devam etmek istiyor musunuz? (yes/no): " confirm
    if [ "$confirm" != "yes" ]; then
        echo -e "${YELLOW}İptal edildi.${NC}"
        exit 0
    fi
    docker compose -p $COMPOSE_PROJECT -f $COMPOSE_FILE down -v
    echo -e "${GREEN}✅ Sistem durduruldu ve volume'lar silindi.${NC}"
else
    docker compose -p $COMPOSE_PROJECT -f $COMPOSE_FILE down
    echo -e "${GREEN}✅ Sistem durduruldu. Veriler korundu.${NC}"
fi
