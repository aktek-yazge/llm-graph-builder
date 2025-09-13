#!/bin/bash

# 🍎 Native Apple Silicon Backend Starter
# Host'ta direkt backend çalıştırır - MPS kullanabilir

echo "🍎 Apple Silicon Native Backend başlatılıyor..."
echo "📍 Working directory: $(pwd)"

# Python virtual environment kontrol
if [ ! -d "venv" ]; then
    echo "🔧 Python virtual environment oluşturuluyor..."
    python3 -m venv venv
fi

# Virtual environment aktivasyonu
echo "⚡ Virtual environment aktive ediliyor..."
source venv/bin/activate

# Requirements kurulumu
echo "📦 Dependencies kuruluyor..."
pip install -r requirements.txt

# Environment dosyası yükle
if [ -f ".env" ]; then
    echo "📄 .env dosyası yükleniyor..."
    # Use python-dotenv to properly load .env with validation
    python -c "
from dotenv import load_dotenv
import os
import re
load_dotenv()
for key, value in os.environ.items():
    if any(key.startswith(prefix) for prefix in ['OPENAI_', 'GEMINI_', 'USER_AGENT', 'NEO4J_', 'QDRANT_', 'LLM_', 'EMBEDDING_', 'CHUNKS_', 'NUMBER_', 'UPDATE_', 'GCP_', 'BASE_URL', 'PYTORCH_', 'ACCELERATE_']):
        # Only export valid shell variable names
        if re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', key):
            print(f'export {key}=\"{value}\"')
" | bash
fi

# Apple Silicon optimization
export PYTORCH_ENABLE_MPS_FALLBACK=1
export ACCELERATE_USE_MPS=1
export OMP_NUM_THREADS=1

echo "🚀 Backend başlatılıyor (Native Apple Silicon)..."
echo "🔗 Backend URL: http://localhost:8000"
echo "🍎 MPS GPU acceleration: ENABLED"
echo ""

# Backend'i başlat
uvicorn score:app --host 0.0.0.0 --port 8000 --reload