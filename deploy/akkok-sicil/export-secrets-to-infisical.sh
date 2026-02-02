#!/bin/bash
# =============================================================================
# AKKOK-SİCİL - Export Local Secrets to Infisical
# =============================================================================
# Bu script, local .env dosyasındaki secrets'ları Infisical'a yükler.
#
# Kullanım:
#   ./export-secrets-to-infisical.sh /path/to/.env.akkok-sicil
#
# Önkoşullar:
#   - Infisical CLI kurulu olmalı
#   - infisical login yapılmış olmalı
# =============================================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Defaults
INFISICAL_ENV="staging"
INFISICAL_PATH="/tenants/akkok-sicil"

# Check arguments
if [ -z "$1" ]; then
    echo -e "${RED}Kullanım: $0 <.env-file>${NC}"
    echo -e "${YELLOW}Örnek: $0 /workspace/backend/.env.akkok-sicil${NC}"
    exit 1
fi

ENV_FILE="$1"

if [ ! -f "$ENV_FILE" ]; then
    echo -e "${RED}❌ Dosya bulunamadı: $ENV_FILE${NC}"
    exit 1
fi

# Check Infisical
if ! command -v infisical &> /dev/null; then
    echo -e "${RED}❌ Infisical CLI bulunamadı.${NC}"
    exit 1
fi

echo -e "${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║     AKKOK-SİCİL - Export Secrets to Infisical                ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${YELLOW}📁 Source: $ENV_FILE${NC}"
echo -e "${YELLOW}🎯 Target: env=$INFISICAL_ENV, path=$INFISICAL_PATH${NC}"
echo ""

# Critical secrets to export
CRITICAL_SECRETS=(
    "TENANT_DOMAIN"
    "POSTGRES_PASSWORD"
    "POSTGRES_USER"
    "POSTGRES_DB"
    "NEO4J_PASSWORD"
    "NEO4J_USERNAME"
    "NEO4J_DATABASE"
    "RABBITMQ_USER"
    "RABBITMQ_PASSWORD"
    "OPENAI_API_KEY"
    "GEMINI_API_KEY"
    "ANTHROPIC_API_KEY"
    "AWS_ACCESS_KEY_ID"
    "AWS_SECRET_ACCESS_KEY"
    "AWS_REGION"
    "S3_BUCKET_NAME"
    "JWT_SECRET_KEY"
    "JWT_EXPIRATION_HOURS"
    "ADMIN_EMAIL"
    "ADMIN_USERNAME"
    "ADMIN_PASSWORD"
    "LANGFUSE_ENABLED"
    "LANGFUSE_HOST"
    "LANGFUSE_PUBLIC_KEY"
    "LANGFUSE_SECRET_KEY"
    "USE_REACT_AGENT"
    "REACT_MODEL"
    "REACT_TOOL_MODE"
    "EMBEDDING_MODEL"
    "V2_BATCH_SIZE"
    "FLOWER_AUTH"
    "LLM_MODEL_CONFIG_openai_gpt_4o_mini"
    "LLM_MODEL_CONFIG_openai_gpt_4o"
    "LLM_MODEL_CONFIG_gemini_2.0_flash"
)

# Add TENANT_DOMAIN if not in file
echo -e "${YELLOW}📤 Exporting secrets...${NC}"

# First, add the TENANT_DOMAIN
infisical secrets set \
    --env=$INFISICAL_ENV \
    --path=$INFISICAL_PATH \
    TENANT_DOMAIN="akkok-sicil.yazge.aktekbilisim.com" 2>/dev/null || true

# Export each secret
SUCCESS=0
FAILED=0

for secret in "${CRITICAL_SECRETS[@]}"; do
    # Extract value from .env file
    VALUE=$(grep "^${secret}=" "$ENV_FILE" | cut -d'=' -f2- | head -1)
    
    if [ -n "$VALUE" ]; then
        # Export to Infisical
        if infisical secrets set \
            --env=$INFISICAL_ENV \
            --path=$INFISICAL_PATH \
            "${secret}=${VALUE}" 2>/dev/null; then
            echo -e "${GREEN}✅ $secret${NC}"
            ((SUCCESS++))
        else
            echo -e "${RED}❌ $secret (export failed)${NC}"
            ((FAILED++))
        fi
    else
        echo -e "${YELLOW}⚠️  $secret (not found in file)${NC}"
    fi
done

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                       TAMAMLANDI                             ║${NC}"
echo -e "${GREEN}╠══════════════════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║ Başarılı: $SUCCESS                                                   ║${NC}"
echo -e "${GREEN}║ Başarısız: $FAILED                                                    ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${YELLOW}📋 Sonraki adım: Infisical dashboard'dan kontrol edin${NC}"
echo -e "${YELLOW}   https://your-infisical-server.com/project/xxx${NC}"
