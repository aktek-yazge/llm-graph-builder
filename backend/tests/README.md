# Tests Klasörü

Bu klasör backend'deki tüm test dosyalarını içerir.

## Kullanım

Test dosyalarını çalıştırmak için, dosyanın başında şu import'u ekleyin:

```python
# Test environment setup
from tests.test_setup import *
```

Bu import:

- Backend klasörünü Python path'ine ekler
- .env dosyasını yükler
- Tüm src modüllerinin import edilebilmesini sağlar

## Örnek Kullanım

```python
#!/usr/bin/env python3
"""
Test dosyası örneği
"""

# Test environment setup
from tests.test_setup import *

# Artık backend modüllerini import edebilirsiniz
from src.shared.common_fn import load_embedding_model
from src.main import extract_graph_from_file_local_file

def test_something():
    # Test kodunuz
    pass

if __name__ == "__main__":
    test_something()
```

## Test Çalıştırma

Tests klasöründeki dosyaları backend klasöründen çalıştırın:

```bash
cd backend
python tests/test_dosya_adi.py
```

veya

```bash
cd backend
python -m tests.test_dosya_adi
```

