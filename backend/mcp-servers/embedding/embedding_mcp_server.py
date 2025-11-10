#!/usr/bin/env python3
"""
Embedding MCP Server for Fast Agent
Provides embedding generation functionality for Cypher queries
"""

import logging
import sys
import os
from typing import Tuple, Any
from fastmcp import Client, Context, FastMCP
from fastmcp.prompts.prompt import Message, PromptMessage, TextContent


from fastmcp.tools.tool import ToolResult

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
mcp = FastMCP(name="Embedding Server")

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


@mcp.tool(
    name="generate_embeddings_for_cypher",
    description=(
        "Verilen text'ten embedding oluşturur ve sonraki Cypher sorgularında "
        "$embedding_vector parametresi olarak kullanılmak üzere hazırlar. "
        "Sadece içerik kelimelerinden embedding oluştur (müşteri adı, tarih, vb. metadata değil)."
    ),
)
def generate_embeddings_for_cypher(text: str, context) -> ToolResult:
    """
    Cypher sorgularında kullanılacak embedding'i oluşturur.
    """
    try:
        logger.info(f"🧠 Cypher için embedding oluşturuluyor: {text}")

        # Modeli yükle
        embedding_model = _initialize_embedding_model()

        # Normalize et
        normalized_text = normalize_unicode_text(text)
        logger.info(f"🧹 Normalize edilmiş text: {normalized_text}")

        # Embedding oluştur
        embedding_vector = embedding_model.embed_query(normalized_text)
        dim = len(embedding_vector)

        logger.info(f"✅ {dim} boyutlu embedding oluşturuldu.")

        # 🔹 MCP 2025 uyumlu ToolResult
        return ToolResult(
            content=[
                TextContent(
                    type="text",
                    text="Semantic arama için 'structuredContent.embedding_vector' değişkenini kullanmalısın.",
                )
            ],
            structuredContent={
                "embedding_vector": embedding_vector,
                "dimensions": dim,
                "text": normalized_text,
                "success": True,
            },
        )

    except Exception as e:
        error_msg = f"❌ Embedding oluşturma hatası: {e}"
        logger.exception(error_msg)
        return ToolResult(
            content=[TextContent(type="text", text=error_msg)],
            structuredContent={
                "success": False,
                "error": str(e),
            },
        )


# Resources - Embedding server hakkında bilgi
@mcp.resource("resource://embedding/info")
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
@mcp.prompt("embedding_usage_guide")
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
WITH $embedding_vector AS queryVec
MATCH (c:Chunk)
WHERE gds.similarity.cosine(c.embedding, queryVec) > 0.8
RETURN c.text, gds.similarity.cosine(c.embedding, queryVec) as score
ORDER BY score DESC
```
"""


if __name__ == "__main__":
    import argparse

    # Command line arguments
    parser = argparse.ArgumentParser(description="Embedding MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="Transport mode: stdio or http",
    )
    parser.add_argument(
        "--port", type=int, default=8001, help="HTTP port (default: 8001)"
    )
    parser.add_argument(
        "--host", default="0.0.0.0", help="HTTP host (default: 0.0.0.0)"
    )
    args = parser.parse_args()

    # Environment değişkenlerini kontrol et ve log'la
    logger.info("🚀 Embedding MCP Server başlatılıyor...")
    logger.info(f"🌐 Transport mode: {args.transport}")
    if args.transport == "http":
        logger.info(f"🌐 HTTP Server: http://{args.host}:{args.port}")

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

    # Run server based on transport mode
    if args.transport == "http":
        logger.info("📡 MCP Server HTTP modunda başlatılıyor...")
        mcp.run(transport="http", host=args.host, port=args.port)
    else:
        logger.info("📡 MCP Server stdio modunda başlatılıyor...")
        mcp.run()
