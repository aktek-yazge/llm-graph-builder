#!/bin/bash
# =============================================================================
# Traefik - Stop Script
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Traefik durduruluyor..."
docker compose -p traefik down

echo "Durduruldu!"
