# -*- coding: utf-8 -*-
"""
Redis Semantic Cache Module

LLM yanıtlarını cache'leyerek token tasarrufu sağlar.
Benzer sorular geldiğinde LLM'e gitmeden cache'den cevap döner.

Kullanım:
- setup_semantic_cache() - Uygulama başlangıcında çağır
- Cache otomatik olarak LLM çağrılarını yakalar
"""

import os
import logging
from typing import Optional
from dotenv import load_dotenv

# .env dosyasını yükle
load_dotenv()

logger = logging.getLogger(__name__)

# Redis availability flag
REDIS_CACHE_AVAILABLE = False
_semantic_cache_initialized = False

# Environment variables
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
REDIS_CACHE_ENABLED = os.getenv("REDIS_CACHE_ENABLED", "true").lower() == "true"
REDIS_CACHE_TTL = int(os.getenv("REDIS_CACHE_TTL", "86400"))  # 24 saat default
REDIS_SIMILARITY_THRESHOLD = float(os.getenv("REDIS_SIMILARITY_THRESHOLD", "0.95"))  # Çok benzer sorular için


def setup_semantic_cache(
    embeddings=None,
    redis_url: Optional[str] = None,
    ttl: Optional[int] = None,
    similarity_threshold: Optional[float] = None
) -> bool:
    """
    Redis Semantic Cache'i kur ve LangChain'e bağla.
    
    Args:
        embeddings: LangChain embeddings instance (opsiyonel, otomatik oluşturulur)
        redis_url: Redis bağlantı URL'i
        ttl: Cache süresi (saniye)
        similarity_threshold: Benzerlik eşiği (0.0-1.0, yüksek = daha strict)
    
    Returns:
        bool: Cache başarıyla kuruldu mu
    """
    global REDIS_CACHE_AVAILABLE, _semantic_cache_initialized
    
    if not REDIS_CACHE_ENABLED:
        logger.info("⏭️ Redis Semantic Cache devre dışı (REDIS_CACHE_ENABLED=false)")
        return False
    
    if _semantic_cache_initialized:
        logger.debug("♻️ Redis Semantic Cache zaten kurulu")
        return REDIS_CACHE_AVAILABLE
    
    # Parametreleri ayarla
    redis_url = redis_url or REDIS_URL
    ttl = ttl or REDIS_CACHE_TTL
    similarity_threshold = similarity_threshold or REDIS_SIMILARITY_THRESHOLD
    
    try:
        from langchain_redis import RedisSemanticCache
        from langchain_core.globals import set_llm_cache
        import redis
        
        # Redis bağlantısını test et
        logger.info(f"🔌 Redis'e bağlanılıyor: {redis_url[:30]}...")
        redis_client = redis.from_url(redis_url)
        redis_client.ping()
        logger.info("✅ Redis bağlantısı başarılı")
        
        # Embeddings oluştur (verilmediyse)
        if embeddings is None:
            embeddings = _create_embeddings()
            if embeddings is None:
                logger.warning("⚠️ Embeddings oluşturulamadı, Semantic Cache kullanılamaz")
                return False
        
        # Semantic Cache oluştur
        semantic_cache = RedisSemanticCache(
            redis_url=redis_url,
            embeddings=embeddings,
            distance_threshold=1 - similarity_threshold,  # Redis distance = 1 - similarity
            ttl=ttl
        )
        
        # LangChain global cache'e bağla
        set_llm_cache(semantic_cache)
        
        REDIS_CACHE_AVAILABLE = True
        _semantic_cache_initialized = True
        
        logger.info(f"✅ Redis Semantic Cache kuruldu!")
        logger.info(f"   📍 URL: {redis_url}")
        logger.info(f"   ⏰ TTL: {ttl} saniye ({ttl/3600:.1f} saat)")
        logger.info(f"   🎯 Benzerlik Eşiği: {similarity_threshold}")
        
        return True
        
    except ImportError as e:
        logger.warning(f"⚠️ Redis paketleri yüklü değil: {e}")
        logger.info("   Yüklemek için: pip install langchain-redis redis redisvl")
        return False
        
    except Exception as e:
        # Handles redis.ConnectionError and other errors
        if "Connection" in str(type(e).__name__) or "connection" in str(e).lower():
            logger.warning(f"⚠️ Redis'e bağlanılamadı: {e}")
            logger.info(f"   Redis'in çalıştığından emin olun: docker run -d -p 6379:6379 redis")
        else:
            logger.error(f"❌ Redis Semantic Cache kurulum hatası: {e}", exc_info=True)
        return False


def _create_embeddings():
    """
    Embeddings instance oluştur.
    Öncelik: OpenAI > HuggingFace > None
    """
    try:
        # OpenAI dene
        openai_key = os.getenv("OPENAI_API_KEY")
        if openai_key:
            from langchain_openai import OpenAIEmbeddings
            embeddings = OpenAIEmbeddings(
                model="text-embedding-3-small",  # Daha hızlı ve ucuz
                api_key=openai_key  # type: ignore[arg-type]
            )
            logger.info("✅ OpenAI Embeddings kullanılacak (semantic cache için)")
            return embeddings
        
        # HuggingFace dene
        try:
            from langchain_huggingface import HuggingFaceEmbeddings
            embeddings = HuggingFaceEmbeddings(
                model_name="sentence-transformers/all-MiniLM-L6-v2"
            )
            logger.info("✅ HuggingFace Embeddings kullanılacak (semantic cache için)")
            return embeddings
        except:
            pass
        
        logger.warning("⚠️ Embeddings oluşturulamadı")
        return None
        
    except Exception as e:
        logger.error(f"❌ Embeddings oluşturma hatası: {e}")
        return None


def clear_cache() -> bool:
    """
    Redis cache'i temizle.
    
    Returns:
        bool: Başarılı mı
    """
    if not REDIS_CACHE_AVAILABLE:
        logger.warning("⚠️ Redis cache aktif değil")
        return False
    
    try:
        import redis
        redis_client = redis.from_url(REDIS_URL)
        
        # LangChain cache key pattern'ini temizle
        keys = list(redis_client.keys("langchain:*"))  # type: ignore[arg-type]
        if keys:
            redis_client.delete(*keys)
            logger.info(f"🗑️ {len(keys)} cache key silindi")
        else:
            logger.info("ℹ️ Cache zaten boş")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Cache temizleme hatası: {e}")
        return False


def get_cache_stats() -> dict:
    """
    Cache istatistiklerini döndür.
    
    Returns:
        dict: Cache stats
    """
    if not REDIS_CACHE_AVAILABLE:
        return {"status": "disabled", "message": "Redis cache aktif değil"}
    
    try:
        import redis
        redis_client = redis.from_url(REDIS_URL)
        
        # Key sayısı
        keys = list(redis_client.keys("langchain:*"))  # type: ignore[arg-type]
        
        # Redis info
        info = redis_client.info("memory")  # type: ignore[union-attr]
        
        return {
            "status": "active",
            "cached_queries": len(keys),
            "memory_used": info.get("used_memory_human", "unknown") if isinstance(info, dict) else "unknown",
            "redis_url": REDIS_URL[:30] + "...",
            "ttl_seconds": REDIS_CACHE_TTL,
            "similarity_threshold": REDIS_SIMILARITY_THRESHOLD
        }
        
    except Exception as e:
        return {"status": "error", "message": str(e)}


def is_cache_available() -> bool:
    """Cache kullanılabilir mi?"""
    return REDIS_CACHE_AVAILABLE


# Convenience alias
init_semantic_cache = setup_semantic_cache

