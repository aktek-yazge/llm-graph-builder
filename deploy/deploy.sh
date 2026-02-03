#!/bin/bash
# =============================================================================
# Master Deployment Script
# =============================================================================
# Tüm stack'leri doğru sırayla ayağa kaldırır.
#
# KULLANIM:
#   ./deploy.sh              # Tüm stack'leri başlat
#   ./deploy.sh traefik      # Sadece traefik
#   ./deploy.sh infrastructure
#   ./deploy.sh akkok-sicil
#   ./deploy.sh stop         # Tümünü durdur
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

# =============================================================================
# Yardımcı Fonksiyonlar
# =============================================================================

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[OK]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Network oluştur (yoksa)
ensure_network() {
    local network=$1
    if ! docker network ls --format '{{.Name}}' | grep -q "^${network}$"; then
        log_info "Network oluşturuluyor: $network"
        docker network create "$network"
        log_success "Network oluşturuldu: $network"
    else
        log_info "Network zaten var: $network"
    fi
}

# .env dosyası oluştur (yoksa)
ensure_env() {
    local dir=$1
    if [ ! -f "$dir/.env" ]; then
        if [ -f "$dir/.env.example" ]; then
            cp "$dir/.env.example" "$dir/.env"
            log_warn ".env oluşturuldu: $dir/.env"
            log_warn "DÜZENLEME GEREKLİ: nano $dir/.env"
            return 1
        fi
    fi
    return 0
}

# Stack başlat
start_stack() {
    local name=$1
    local dir=$2
    local compose_file=${3:-docker-compose.yml}
    
    echo ""
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${YELLOW}  $name${NC}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    
    cd "$SCRIPT_DIR/$dir"
    
    # .env kontrolü
    if ! ensure_env "."; then
        log_error "$name için .env düzenlenmeli!"
        return 1
    fi
    
    # Build & Start
    log_info "$name başlatılıyor..."
    docker compose -p "$name" -f "$compose_file" up -d --build
    
    log_success "$name başlatıldı!"
    docker compose -p "$name" ps --format "table {{.Name}}\t{{.Status}}"
}

# Stack durdur
stop_stack() {
    local name=$1
    local dir=$2
    local compose_file=${3:-docker-compose.yml}
    
    log_info "$name durduruluyor..."
    cd "$SCRIPT_DIR/$dir"
    docker compose -p "$name" -f "$compose_file" down
    log_success "$name durduruldu"
}

# =============================================================================
# Stack Fonksiyonları
# =============================================================================

deploy_traefik() {
    ensure_network "traefik-public"
    start_stack "traefik" "traefik"
}

deploy_infrastructure() {
    ensure_network "traefik-public"
    ensure_network "observability"
    start_stack "infrastructure" "infrastructure"
}

deploy_akkok_sicil() {
    ensure_network "traefik-public"
    ensure_network "observability"
    ensure_network "infrastructure_infrastructure"
    start_stack "akkok-sicil" "akkok-sicil" "docker-compose.staging.yml"
}

stop_all() {
    echo -e "${YELLOW}Tüm stack'ler durduruluyor...${NC}"
    stop_stack "akkok-sicil" "akkok-sicil" "docker-compose.staging.yml" 2>/dev/null || true
    stop_stack "infrastructure" "infrastructure" 2>/dev/null || true
    stop_stack "traefik" "traefik" 2>/dev/null || true
    log_success "Tüm stack'ler durduruldu"
}

deploy_all() {
    echo ""
    echo -e "${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║           STAGING DEPLOYMENT                                  ║${NC}"
    echo -e "${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"
    
    # Networks
    ensure_network "traefik-public"
    ensure_network "observability"
    
    # Stacks (sırayla)
    deploy_traefik
    
    # Infrastructure başlamadan önce bekle
    log_info "Traefik hazır olana kadar bekleniyor..."
    sleep 5
    
    deploy_infrastructure
    
    # Infrastructure başlamadan önce bekle
    log_info "RabbitMQ hazır olana kadar bekleniyor..."
    sleep 10
    
    deploy_akkok_sicil
    
    echo ""
    echo -e "${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║           DEPLOYMENT TAMAMLANDI                               ║${NC}"
    echo -e "${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo "Servisler:"
    echo "  - Traefik:     https://traefik.\${BASE_DOMAIN}"
    echo "  - RabbitMQ:    https://rabbitmq.\${BASE_DOMAIN}"
    echo "  - Frontend:    https://akkok-sicil.\${BASE_DOMAIN}"
    echo "  - Backend:     https://akkok-sicil.\${BASE_DOMAIN}/server"
    echo ""
}

# =============================================================================
# Ana Komut
# =============================================================================

show_help() {
    echo "Kullanım: $0 [komut]"
    echo ""
    echo "Komutlar:"
    echo "  (boş)          Tüm stack'leri başlat"
    echo "  traefik        Sadece Traefik başlat"
    echo "  infrastructure Sadece Infrastructure başlat"
    echo "  akkok-sicil    Sadece Akkok-Sicil başlat"
    echo "  stop           Tüm stack'leri durdur"
    echo "  status         Tüm container'ların durumu"
    echo "  help           Bu yardım mesajı"
}

show_status() {
    echo -e "${BLUE}Tüm container'lar:${NC}"
    docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | head -30
}

case "${1:-all}" in
    traefik)
        deploy_traefik
        ;;
    infrastructure)
        deploy_infrastructure
        ;;
    akkok-sicil)
        deploy_akkok_sicil
        ;;
    stop)
        stop_all
        ;;
    status)
        show_status
        ;;
    help|--help|-h)
        show_help
        ;;
    all|"")
        deploy_all
        ;;
    *)
        log_error "Bilinmeyen komut: $1"
        show_help
        exit 1
        ;;
esac
