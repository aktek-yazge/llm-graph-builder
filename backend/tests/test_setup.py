"""
Test Environment Setup
Bu dosyayı test dosyalarının başında import edin: `from tests.test_setup import *`
"""

import sys
import os
from dotenv import load_dotenv

# Backend klasörünü Python path'ine ekle
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

# .env dosyasını yükle
env_path = os.path.join(backend_dir, '.env')
if os.path.exists(env_path):
    load_dotenv(env_path)

# Test environment hazır
print(f"🔧 Test environment ready: Backend path '{backend_dir}' added to sys.path")
