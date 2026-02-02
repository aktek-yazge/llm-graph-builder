#!/bin/bash
# Start Celery Worker with Infisical Secrets
# 
# Kullanım:
#   ./start-celery-infisical.sh [tenant] [worker_type]
#
# Örnekler:
#   ./start-celery-infisical.sh akkok-sicil main
#   ./start-celery-infisical.sh akkok-sicil db_writer
#   ./start-celery-infisical.sh bakim main
#
# Worker Types:
#   main      - Ana worker (chunking, graph, embeddings)
#   db_writer - PostgreSQL yazma worker'ı
#   all       - Tüm queue'ları dinle (development için)
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
CYAN='\033[0;36m'
NC='\033[0m'

# Parametreler
TENANT="${1:-akkok-sicil}"
WORKER_TYPE="${2:-main}"

echo -e "${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║         🐝 Infisical Celery Worker Starter                   ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "Tenant:      ${YELLOW}$TENANT${NC}"
echo -e "Worker Type: ${CYAN}$WORKER_TYPE${NC}"

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
    echo ""
    echo "Token oluşturmak için:"
    echo "  infisical login"
    echo "  infisical token create --name=celery-dev > $TOKEN_FILE"
    exit 1
fi

# Infisical konfigürasyonu - agent-graph-builder (backend ile aynı proje)
INFISICAL_DOMAIN="${INFISICAL_DOMAIN:-http://infisical-server:8080}"
INFISICAL_PROJECT_ID="${INFISICAL_PROJECT_ID:-1d1e766c-b94d-4ff2-8bba-4c4188b74410}"
SECRET_PATH="/tenants/$TENANT"

echo -e "Secret Path: ${YELLOW}$SECRET_PATH${NC}"
echo -e "Project ID:  ${YELLOW}$INFISICAL_PROJECT_ID${NC}"
echo -e "Domain:      ${YELLOW}$INFISICAL_DOMAIN${NC}"
echo ""

# Worker konfigürasyonu
cd "$SCRIPT_DIR"
export PYTHONPATH="$SCRIPT_DIR:$WORKSPACE_DIR"
export ENV="${ENV:-development}"
export LANG=C.UTF-8
export LC_ALL=C.UTF-8
export PYTHONIOENCODING=utf-8

# Worker type'a göre celery komutunu belirle
case "$WORKER_TYPE" in
    main)
        QUEUE="celery"
        WORKER_NAME="main-worker"
        CONCURRENCY="${MAIN_CONCURRENCY:-8}"
        AUTOSCALE="8,2"
        echo -e "${CYAN}📦 Main Worker - Chunking, Graph, Embeddings${NC}"
        ;;
    db_writer)
        QUEUE="db_write"
        WORKER_NAME="db-writer"
        CONCURRENCY="${WRITER_CONCURRENCY:-2}"
        AUTOSCALE=""
        echo -e "${CYAN}💾 DB Writer - PostgreSQL Writes${NC}"
        ;;
    neo4j_writer)
        QUEUE="neo4j_write"
        WORKER_NAME="neo4j-writer"
        CONCURRENCY="${WRITER_CONCURRENCY:-2}"
        AUTOSCALE=""
        echo -e "${CYAN}🔷 Neo4j Writer - Graph Writes${NC}"
        ;;
    all)
        QUEUE="celery,db_write,neo4j_write"
        WORKER_NAME="all-worker"
        CONCURRENCY="${MAIN_CONCURRENCY:-4}"
        AUTOSCALE=""
        echo -e "${CYAN}🌐 All Queues Worker (Development)${NC}"
        ;;
    *)
        echo -e "${RED}❌ Bilinmeyen worker type: $WORKER_TYPE${NC}"
        echo "Geçerli tipler: main, db_writer, neo4j_writer, all"
        exit 1
        ;;
esac

echo ""
echo -e "${GREEN}🚀 Starting Celery Worker with Infisical...${NC}"
echo -e "   Queue(s):     $QUEUE"
echo -e "   Worker Name:  $WORKER_NAME@%h"
echo -e "   Concurrency:  $CONCURRENCY"
if [ -n "$AUTOSCALE" ]; then
    echo -e "   Autoscale:    $AUTOSCALE"
fi
echo ""

# Celery komutunu oluştur
if [ -n "$AUTOSCALE" ]; then
    CELERY_CMD="python -m celery -A src.celery_app worker \
        --loglevel=${LOG_LEVEL:-info} \
        --pool=prefork \
        --autoscale=$AUTOSCALE \
        -Q $QUEUE \
        -E \
        -n $WORKER_NAME@%h"
else
    CELERY_CMD="python -m celery -A src.celery_app worker \
        --loglevel=${LOG_LEVEL:-info} \
        --pool=prefork \
        --concurrency=$CONCURRENCY \
        -Q $QUEUE \
        -E \
        -n $WORKER_NAME@%h"
fi

# Infisical ile çalıştır
infisical run \
    --env=dev \
    --path="$SECRET_PATH" \
    --domain="$INFISICAL_DOMAIN" \
    --projectId="$INFISICAL_PROJECT_ID" \
    -- $CELERY_CMD
