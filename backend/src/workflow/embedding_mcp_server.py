#!/usr/bin/env python3
"""
Embedding MCP Server for Fast Agent
Provides embedding generation functionality for Cypher queries
"""

import logging
import sys
import os
from typing import Tuple, Any
from mcp.server.fastmcp import FastMCP

from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Backend modüllerini import etmek için path'i ayarla
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

# Terminal loglama için logger'ı configure et
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],  # Terminal'e output
)

try:
    from src.utf8_utils import normalize_unicode_text
    from src.shared.common_fn import load_embedding_model

    # Logger'ı intelligent_agent.py ile aynı şekilde kullan
    logger = logging.getLogger(__name__)

except ImportError as e:
    # Fallback imports if modules not found
    print(f"Warning: Could not import backend modules: {e}", file=sys.stderr)

    def normalize_unicode_text(text: str) -> str:
        """Fallback normalize function"""
        return text.strip()

    def load_embedding_model(model_name: str):
        """Fallback embedding model loader"""
        try:
            from langchain_openai import OpenAIEmbeddings

            return OpenAIEmbeddings(), None
        except ImportError:
            raise ImportError("OpenAI embeddings not available")

    # Simple logger fallback
    logger = logging.getLogger(__name__)

# Initialize FastMCP app
app = FastMCP(name="Embedding Server")

# Global embedding model instance
_embedding_model = None


def _initialize_embedding_model():
    """Initialize embedding model on first use"""
    global _embedding_model
    if _embedding_model is None:
        try:
            logger.info("🧠 Embedding model başlatılıyor...")
            _embedding_model, _ = load_embedding_model("openai")
            logger.info("✅ Embedding model başarıyla yüklendi")
        except Exception as e:
            logger.error(f"❌ Embedding model yüklenemedi: {e}")
            raise
    return _embedding_model


@app.tool(
    name="generate_embeddings_for_cypher",
    description="Text'ten embedding oluşturur ve sonraki Cypher sorgularında $embedding_vector parametresi olarak kullanılmak üzere hazırlar. SADECE İÇERİK KELİMELERİNDEN embedding oluştur (müşteri adı, tarih, vb. metadata değil).",
)
def generate_embeddings_for_cypher(text: str) -> dict:
    """
    Cypher sorgularında kullanmak üzere text'ten embedding oluşturur

    Args:
        text: Embedding oluşturulacak text (sadece içerik kavramları)

    Returns:
        dict: Başarı durumu ve embedding vektörü
    """
    try:
        logger.info(f"🧠 Cypher için embedding oluşturuluyor: {text}")

        # Embedding model'i initialize et
        embedding_model = _initialize_embedding_model()

        # Text'i normalize et
        normalized_text = normalize_unicode_text(text)
        logger.info(f"🧹 Normalize edilmiş text: {normalized_text}")

        # OpenAI embedding oluştur
        embedding_vector = embedding_model.embed_query(normalized_text)

        logger.info(
            f"✅ Cypher embedding oluşturuldu: {len(embedding_vector)} boyutlu vektör"
        )

        return {
            "success": True,
            "embedding_vector": embedding_vector,
            "text": normalized_text,
            "dimensions": len(embedding_vector),
            "cypher_parameter": "embedding_vector",
            "usage_note": "Bu embedding'i Cypher sorgusunda $embedding_vector parametresi olarak kullanın",
            "message": f"✅ Embedding hazır: {len(embedding_vector)} boyut, Cypher'da $embedding_vector olarak kullanılabilir",
        }

    except Exception as e:
        error_msg = f"❌ Cypher embedding oluşturma hatası: {e}"
        logger.error(error_msg)
        return {"success": False, "error": str(e), "message": error_msg}


# Resources - Embedding server hakkında bilgi
@app.resource("resource://embedding/info")
def embedding_info():
    """Embedding server hakkında bilgi"""
    return {
        "name": "Embedding MCP Server",
        "description": "Cypher sorguları için embedding oluşturma servisi",
        "version": "1.0.0",
        "supported_models": ["openai"],
        "features": [
            "Text embedding generation",
            "Unicode text normalization",
            "Cypher query integration",
        ],
    }


# Prompts - Embedding kullanım örnekleri
@app.prompt("embedding_usage_guide")
def embedding_usage_guide(query_type: str = "general") -> str:
    """Embedding kullanım kılavuzu"""
    return f"""
# Embedding Server Kullanım Kılavuzu

## {query_type.title()} Sorguları için Embedding

### Doğru Kullanım:
- SADECE içerik kavramlarını kullanın
- Örnek: "taksit tablosu ödeme planı", "prim bilgileri", "poliçe detayları"

### Yanlış Kullanım:
- Müşteri adları: "ayça hanım", "mehmet bey"
- Tarihler: "2020", "2021 yılı"  
- Spesifik kodlar: "POL123", "TK456"

### Cypher ile Kullanım:
```cypher
// 1. Önce embedding oluştur (MCP tool ile)
// 2. Sonra Cypher'da kullan:
MATCH (c:Chunk)
WHERE gds.similarity.cosine(c.embedding, $embedding_vector) > 0.8
RETURN c.text, gds.similarity.cosine(c.embedding, $embedding_vector) as score
ORDER BY score DESC
```
"""


if __name__ == "__main__":
    # Environment değişkenlerini kontrol et ve log'la
    logger.info("🚀 Embedding MCP Server başlatılıyor...")
    logger.info("🔧 Environment değişkenleri kontrol ediliyor...")

    # OpenAI API key kontrol
    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key:
        logger.info(f"✅ OPENAI_API_KEY bulundu: {openai_key[:10]}...{openai_key[-4:]}")
    else:
        logger.warning("⚠️ OPENAI_API_KEY environment değişkeni bulunamadı!")

    # Python path kontrol
    python_path = os.getenv("PYTHONPATH")
    if python_path:
        logger.info(f"✅ PYTHONPATH bulundu: {python_path}")
    else:
        logger.info("ℹ️ PYTHONPATH environment değişkeni yok")

    # Current working directory
    logger.info(f"📂 Current working directory: {os.getcwd()}")
    logger.info(f"📂 Script directory: {os.path.dirname(os.path.abspath(__file__))}")

    # Run in stdio mode
    logger.info("📡 MCP Server stdio modunda başlatılıyor...")
    app.run()
