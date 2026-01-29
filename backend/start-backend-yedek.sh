#!/bin/bash
# Start Backend API Server (local development)
# 
# Kullanım:
#   ./start-backend-yedek.sh [domain]
#
# Örnekler:
#   ./start-backend-yedek.sh              # Default: sigorta
#   ./start-backend-yedek.sh sigorta      # Sigorta ortamı
#   ./start-backend-yedek.sh bakim        # WAT Motor bakım ortamı
#   ./start-backend-yedek.sh akkok-sicil  # Akkok Sicil ortamı
#
# NOT: Domain listesi src/config/domains.py'den okunur (merkezi registry)

# Determine project root (directory containing this script)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"

# ==============================
# Domain/Environment Selection
# ==============================
DOMAIN="${1:-sigorta}"

# Domain validation - Merkezi registry'den (src/config/domains.py)
# Python modülünden geçerli domain listesini al
VALID_DOMAINS=$(cd "$SCRIPT_DIR" && python -c "from src.config.domains import get_valid_domains; print(' '.join(sorted(get_valid_domains())))" 2>/dev/null)

# Eğer Python çağrısı başarısız olursa, fallback listesi kullan
if [[ -z "$VALID_DOMAINS" ]]; then
    echo "⚠️  Merkezi domain registry okunamadı, fallback kullanılıyor"
    VALID_DOMAINS="sigorta bakim akkok-sicil"
fi

# Domain geçerli mi kontrol et
DOMAIN_VALID=false
for valid_domain in $VALID_DOMAINS; do
    if [[ "$DOMAIN" == "$valid_domain" ]]; then
        DOMAIN_VALID=true
        break
    fi
done

if [[ "$DOMAIN_VALID" == "false" ]]; then
    echo "❌ Geçersiz domain: $DOMAIN"
    echo "   Geçerli domain'ler: $VALID_DOMAINS"
    echo "   Kullanım: ./start-backend-yedek.sh [domain]"
    exit 1
fi

# Parse .env file and prepare for env command
# Bash'te nokta içeren değişken adları export edilemez,
# bu yüzden env komutuyla process'e geçiriyoruz
#
# İki işlem yapılır:
# 1. Nokta içermeyen değişkenler → export (script içinde kullanılabilir)
# 2. Tüm değişkenler → ENV_ARGS array'i (uvicorn'a env komutuyla geçilir)
parse_env_file() {
    local env_file="$1"
    ENV_ARGS=()
    while IFS= read -r line || [[ -n "$line" ]]; do
        # Skip empty lines
        [[ -z "$line" ]] && continue
        # Skip comment-only lines
        [[ "$line" =~ ^[[:space:]]*# ]] && continue
        # Skip lines that don't look like VAR=value
        [[ ! "$line" =~ ^[A-Za-z_][A-Za-z0-9_.\-]*= ]] && continue
        
        # Extract variable name and value
        local var_name="${line%%=*}"
        local var_value="${line#*=}"
        
        # Remove inline comments (but preserve # inside quotes)
        # First check if value starts with quote
        if [[ "$var_value" =~ ^\" ]]; then
            # Double quoted: extract content between quotes
            var_value="${var_value#\"}"      # Remove leading "
            var_value="${var_value%%\"*}"    # Remove trailing " and everything after
        elif [[ "$var_value" =~ ^\' ]]; then
            # Single quoted: extract content between quotes
            var_value="${var_value#\'}"      # Remove leading '
            var_value="${var_value%%\'*}"    # Remove trailing ' and everything after
        else
            # Unquoted: remove inline comment (# and everything after)
            var_value="${var_value%%#*}"
            # Trim trailing whitespace
            var_value="${var_value%"${var_value##*[![:space:]]}"}"
        fi
        
        # Build clean line
        local clean_line="${var_name}=${var_value}"
        
        # Add to array for env command
        ENV_ARGS+=("$clean_line")
        
        # Export only bash-compatible names (no dots) for script use
        if [[ ! "$var_name" =~ \. ]]; then
            export "$clean_line" 2>/dev/null || true
        fi
    done < "$env_file"
}

# Global array for env command arguments
declare -a ENV_ARGS

# İlgili .env dosyasını yükle
ENV_FILE="${SCRIPT_DIR}/.env.${DOMAIN}"
if [[ -f "$ENV_FILE" ]]; then
    echo "📁 Loading environment: $ENV_FILE"
    parse_env_file "$ENV_FILE"
    echo "🎯 Domain: ${REACT_DOMAIN:-$DOMAIN}"
    echo "📊 Loaded ${#ENV_ARGS[@]} environment variables"
else
    echo "⚠️  Dosya bulunamadı: $ENV_FILE"
    echo "   Default .env kullanılacak (varsa)"
    # Fallback: REACT_DOMAIN'i manuel set et
    export REACT_DOMAIN="$DOMAIN"
fi

echo ""

# Export required environment variables
export PYTHONPATH="$SCRIPT_DIR:$PROJECT_ROOT"
export ENV=development
# Use the same postgres database as celery_worker (override edilmemişse)
export QUEUE_DB_URL="${QUEUE_DB_URL:-postgresql://postgres:Ekdmjweu483i@18.153.150.114:5432/llm_graph_builder}"
export CELERY_BROKER_URL="${CELERY_BROKER_URL:-amqp://guest:guest@18.153.150.114:5672//}"
export CELERY_RESULT_BACKEND="${CELERY_RESULT_BACKEND:-db+postgresql://postgres:Ekdmjweu483i@18.153.150.114:5432/llm_graph_builder}"



# ==============================
# MCP Server (Docker Container) - DISABLED
# ==============================
# MCP Server artık multi-tenant modda çalışıyor ve ayrı yönetiliyor.
# Her tool çağrısında db bilgileri parametre olarak geçiliyor.
# MCP server'ı manuel olarak başlatın veya docker-compose ile yönetin.
#
# Eski kod yorum satırına alındı:
# ------------------------------
# MCP_HOST="${MCP_HTTP_HOST:-127.0.0.1}"
# 
# # Domain'e göre MCP port ve container belirle
# if [[ "$DOMAIN" == "bakim" ]]; then
#     MCP_PORT="${MCP_HTTP_PORT:-8003}"
#     MCP_CONTAINER_NAME="mcp-neo4j-cypher-bakim"
#     DEFAULT_NEO4J_URI="bolt://host.docker.internal:7688"
#     DEFAULT_NEO4J_USER="neo4j"
#     DEFAULT_NEO4J_PASS="Watmotor!654*"
# else
#     MCP_PORT="${MCP_HTTP_PORT:-8002}"
#     MCP_CONTAINER_NAME="mcp-neo4j-cypher"
#     DEFAULT_NEO4J_URI="bolt://18.153.150.114:7688"
#     DEFAULT_NEO4J_USER="neo4j"
#     DEFAULT_NEO4J_PASS="qwerty5555"
# fi
# 
# echo "🐳 Checking MCP Server (Docker) for domain: $DOMAIN..."
# docker compose -p "$COMPOSE_PROJECT" up -d --build
# ... (full code removed for brevity)

# MCP HTTP URL - .env dosyasından veya default
MCP_HOST="${MCP_HTTP_HOST:-127.0.0.1}"
MCP_PORT="${MCP_HTTP_PORT:-8002}"
export MCP_HTTP_URL="http://${MCP_HOST}:${MCP_PORT}/mcp/"
echo "📡 MCP Server URL: ${MCP_HTTP_URL} (ensure it's running separately)"

echo ""
echo "🚀 Starting Backend API Server..."
echo "📡 API will be available at: http://0.0.0.0:8000"
echo "📊 API docs will be available at: http://0.0.0.0:8000/docs"
echo ""

# Worker count for parallel request handling
# Higher = more parallel uploads, but more memory usage
WORKERS="${BACKEND_WORKERS:-1}"

echo "👥 Workers: ${WORKERS}"
echo ""

# Start uvicorn with multiple workers for parallel upload handling
# NOTE: --reload is incompatible with --workers, so we use --workers only
# For development with hot-reload, comment out --workers line and uncomment --reload line
#
# env komutu ile tüm değişkenleri (nokta içerenler dahil) process'e geçiriyoruz
if [[ ${#ENV_ARGS[@]} -gt 0 ]]; then
    env "${ENV_ARGS[@]}" uv run uvicorn score:app --host 0.0.0.0 --port 8000 --workers ${WORKERS} --log-level info
else
    uv run uvicorn score:app --host 0.0.0.0 --port 8000 --workers ${WORKERS} --log-level info
fi

# Development mode with hot-reload (single worker):
# env "${ENV_ARGS[@]}" uv run uvicorn score:app --host 0.0.0.0 --port 8000 --reload --log-level debug
