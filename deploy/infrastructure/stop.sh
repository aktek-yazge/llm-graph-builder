#!/bin/bash
# =============================================================================
# Infrastructure Stack - Stop Script
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Infrastructure Stack durduruluyor..."
docker compose -p infrastructure down

echo "Durduruldu!"
