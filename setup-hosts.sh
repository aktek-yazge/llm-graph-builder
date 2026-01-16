#!/bin/bash
# =============================================================================
# HOSTS DOSYASI AYARLAMA - Traefik Lokal Domain Yapılandırması
# =============================================================================
# Bu script /etc/hosts dosyasına gerekli lokal domain kayıtlarını ekler.
# macOS ve Linux'ta çalışır. DevContainer veya doğrudan compose ile kullanılabilir.
#
# Kullanım:
#   sudo ./setup-hosts.sh        # Kayıtları ekle
#   sudo ./setup-hosts.sh remove # Kayıtları kaldır
#   ./setup-hosts.sh status      # Mevcut durumu göster (sudo gerekmez)
# =============================================================================

set -e

HOSTS_FILE="/etc/hosts"
MARKER_START="# === WAT Dev Environment - START ==="
MARKER_END="# === WAT Dev Environment - END ==="

HOSTS_ENTRIES="127.0.0.1   dev.local"

remove_entries() {
    echo "Mevcut WAT hosts kayitlari kaldiriliyor..."
    if grep -q "$MARKER_START" "$HOSTS_FILE"; then
        # macOS ve Linux uyumlu sed komutu
        if [[ "$OSTYPE" == "darwin"* ]]; then
            sed -i '' "/$MARKER_START/,/$MARKER_END/d" "$HOSTS_FILE"
        else
            sed -i "/$MARKER_START/,/$MARKER_END/d" "$HOSTS_FILE"
        fi
        echo "Eski kayitlar kaldirildi."
    else
        echo "Kaldirilacak kayit bulunamadi."
    fi
}

add_entries() {
    echo "WAT hosts kayitlari ekleniyor..."
    
    # Önce eski kayıtları kaldır
    remove_entries
    
    # Yeni kayıtları ekle
    echo "" >> "$HOSTS_FILE"
    echo "$MARKER_START" >> "$HOSTS_FILE"
    echo "$HOSTS_ENTRIES" >> "$HOSTS_FILE"
    echo "$MARKER_END" >> "$HOSTS_FILE"
    
    echo ""
    echo "Hosts kayitlari basariyla eklendi!"
    echo ""
    echo "WAT Projesi:"
    echo "  http://dev.local/wat/ui          -> Frontend"
    echo "  http://dev.local/wat/server      -> Backend API"
    echo "  http://dev.local/wat/mcp         -> MCP Server"
    echo "  http://dev.local/wat/neo4j       -> Neo4j Browser (neo4j/Watmotor!654*)"
    echo "  http://dev.local/wat/qdrant      -> Qdrant API"
    echo ""
    echo "Altyapi Servisleri:"
    echo "  http://dev.local/rabbitmq        -> RabbitMQ Management"
    echo "  http://localhost:8090/dashboard/ -> Traefik Dashboard"
    echo ""
}

show_status() {
    echo "Mevcut WAT hosts kayitlari:"
    echo ""
    if grep -q "$MARKER_START" "$HOSTS_FILE"; then
        grep -A 5 "$MARKER_START" "$HOSTS_FILE" | grep -B 5 "$MARKER_END" || true
        echo ""
        echo "Durum: Aktif"
    else
        echo "Kayit bulunamadi."
        echo ""
        echo "Eklemek icin: sudo ./setup-hosts.sh"
    fi
    echo ""
}

# Sudo kontrolü (status hariç)
if [ "$EUID" -ne 0 ] && [ "$1" != "status" ]; then
    echo "Bu script root yetkisi gerektirir."
    echo "Kullanim: sudo $0 [add|remove|status]"
    exit 1
fi

# Komut işleme
case "${1:-add}" in
    add)
        add_entries
        ;;
    remove)
        remove_entries
        ;;
    status)
        show_status
        ;;
    *)
        echo "Kullanim: sudo $0 [add|remove|status]"
        echo ""
        echo "  add     - Hosts kayitlarini ekle (varsayilan)"
        echo "  remove  - Hosts kayitlarini kaldir"
        echo "  status  - Mevcut kayitlari goster (sudo gerekmez)"
        exit 1
        ;;
esac
