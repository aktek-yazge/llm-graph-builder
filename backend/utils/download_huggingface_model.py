#!/usr/bin/env python3
"""
HuggingFace embedding modelini local'e indirmek için script
Bu script modeli indirip cache klasörüne kaydeder, böylece internet bağlantısı olmadan kullanılabilir.
"""

import os
import sys
from pathlib import Path

# Backend path'i ekle
backend_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

from langchain_huggingface import HuggingFaceEmbeddings


def download_huggingface_model(
    model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
    cache_folder: str = None,
):
    """
    HuggingFace modelini local'e indirir ve cache'ler
    
    Args:
        model_name: İndirilecek model adı (default: all-MiniLM-L6-v2)
        cache_folder: Cache klasörü yolu (None ise default kullanılır)
    """
    # Cache klasörünü belirle
    if cache_folder is None:
        cache_folder = os.getenv(
            "HUGGINGFACE_CACHE_FOLDER",
            os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "models")
        )
    
    # Cache klasörünü oluştur
    Path(cache_folder).mkdir(parents=True, exist_ok=True)
    
    print(f"📦 Model indiriliyor: {model_name}")
    print(f"📁 Cache klasörü: {cache_folder}")
    
    try:
        # Model'i indir ve yükle
        # HuggingFaceEmbeddings otomatik olarak modeli indirir ve cache'ler
        embeddings = HuggingFaceEmbeddings(
            model_name=model_name,
            cache_folder=cache_folder,
            model_kwargs={
                "cache_dir": cache_folder,
            },
            encode_kwargs={
                "normalize_embeddings": True,
            }
        )
        
        # Test embedding oluştur (model'in düzgün yüklendiğini doğrula)
        test_text = "Test embedding"
        test_embedding = embeddings.embed_query(test_text)
        
        print(f"✅ Model başarıyla indirildi ve cache'lendi!")
        print(f"📊 Embedding boyutu: {len(test_embedding)}")
        print(f"📁 Model dosyaları: {cache_folder}")
        print("\n💡 Artık internet bağlantısı olmadan modeli kullanabilirsiniz!")
        print("   Environment variable olarak ayarlayın:")
        print(f"   export HUGGINGFACE_CACHE_FOLDER={cache_folder}")
        print(f"   export HUGGINGFACE_MODEL_NAME={model_name}")
        
        return True
        
    except Exception as e:
        print(f"❌ Model indirme hatası: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description="HuggingFace embedding modelini local'e indir"
    )
    parser.add_argument(
        "--model",
        type=str,
        default="sentence-transformers/all-MiniLM-L6-v2",
        help="İndirilecek model adı (default: sentence-transformers/all-MiniLM-L6-v2)",
    )
    parser.add_argument(
        "--cache-folder",
        type=str,
        default=None,
        help="Cache klasörü yolu (default: ~/.cache/huggingface/models)",
    )
    
    args = parser.parse_args()
    
    success = download_huggingface_model(
        model_name=args.model,
        cache_folder=args.cache_folder,
    )
    
    sys.exit(0 if success else 1)

