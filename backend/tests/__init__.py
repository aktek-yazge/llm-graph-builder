# Test package for llm-graph-builder backend

import sys
import os

# Backend klasörünü Python path'ine ekle
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

print(f"🔧 Test environment: Backend directory '{backend_dir}' added to Python path")
