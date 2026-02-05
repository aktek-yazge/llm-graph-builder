#!/bin/bash
# Prompts'u volume'a senkronize et
# Kullanım: ./scripts/sync-prompts.sh

echo "🔄 Prompts senkronize ediliyor..."
docker run --rm -v akkok_sicil_prompts:/prompts alpine rm -rf /prompts/*
docker cp /workspace/celery_worker/prompts/. temp_prompts_copy:/prompts/ 2>/dev/null || {
    docker run -d --name temp_prompts_copy -v akkok_sicil_prompts:/prompts alpine tail -f /dev/null
    docker cp /workspace/celery_worker/prompts/. temp_prompts_copy:/prompts/
    docker rm -f temp_prompts_copy
}
echo "✅ Prompts senkronize edildi!"
