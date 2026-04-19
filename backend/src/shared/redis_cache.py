# -*- coding: utf-8 -*-
"""
Redis Semantic Cache Module

LLM yanıtlarını cache'leyerek token tasarrufu sağlar.
Benzer sorular geldiğinde LLM'e gitmeden cache'den cevap döner.

Kullanım:
- setup_semantic_cache() - Uygulama başlangıcında çağır
- Cache otomatik olarak LLM çağrılarını yakalar

⚠️ TOOL CALLS FIX:
- Agent tool calls içeren AIMessage'lar serialize/deserialize edilirken sorun çıkıyor
- AgentAwareSemanticCache wrapper'ı bu sorunu çözer:
  - Tool calls içeren response'ları cache'lemez (agent esnekliği için)
  - Sadece final text response'ları cache'ler (token tasarrufu için)
"""

import os
import json
import logging
from typing import Optional, Sequence, Any
from dotenv import load_dotenv

# .env dosyasını yükle
load_dotenv()

logger = logging.getLogger(__name__)

# Redis availability flag
REDIS_CACHE_AVAILABLE = False
_semantic_cache_initialized = False

# Environment variables
# Semantic Cache için Redis Stack gerekli (RediSearch modülü)
# Normal Redis kullanıyorsanız REDIS_SEMANTIC_URL'i Redis Stack'e yönlendirin
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
REDIS_SEMANTIC_URL = os.getenv("REDIS_SEMANTIC_URL", os.getenv("REDIS_URL", "redis://localhost:6381"))
REDIS_CACHE_ENABLED = os.getenv("REDIS_CACHE_ENABLED", "true").lower() == "true"
REDIS_CACHE_TTL = int(os.getenv("REDIS_CACHE_TTL", "86400"))  # 24 saat default
REDIS_SIMILARITY_THRESHOLD = float(os.getenv("REDIS_SIMILARITY_THRESHOLD", "0.95"))  # Çok benzer sorular için

# Cache mode: "all" = her şeyi cache'le, "final_only" = sadece tool_calls olmayan response'ları cache'le
CACHE_MODE = os.getenv("REDIS_CACHE_MODE", "final_only")


# =============================================================================
# AGENT-AWARE SEMANTIC CACHE WRAPPER
# =============================================================================

try:
    from langchain_core.caches import BaseCache
    _BASE_CACHE_AVAILABLE = True
except ImportError:
    BaseCache = object  # type: ignore[misc, assignment]
    _BASE_CACHE_AVAILABLE = False


class AgentAwareSemanticCache(BaseCache):  # type: ignore[misc]
    """
    RedisSemanticCache wrapper - Agent tool calls sorununu çözer.
    
    Problem:
    - AIMessage içindeki tool_calls serialize/deserialize edilirken bozuluyor
    - LangGraph bu bozuk response'u parse edemiyor → sonsuz döngü
    
    Çözüm (CACHE_MODE="final_only"):
    - Tool calls içeren response'ları cache'lemez
    - Sadece final text response'ları cache'ler
    - Agent esnekliğini korur, token tasarrufu sağlar
    
    Çözüm (CACHE_MODE="all"):
    - Tüm response'ları cache'ler
    - Tool calls JSON'a serialize edilir
    - Lookup'ta JSON'dan deserialize edilir
    """
    
    def __init__(self, base_cache: Any, mode: str = "final_only"):
        """
        Args:
            base_cache: RedisSemanticCache instance
            mode: "final_only" veya "all"
        """
        self._base_cache = base_cache
        self._mode = mode
        self._stats = {"hits": 0, "misses": 0, "skipped": 0}
        logger.info(f"🛡️ AgentAwareSemanticCache initialized (mode={mode})")
    
    def _has_tool_calls(self, generations: Sequence[Any]) -> bool:
        """Response'ta tool_calls var mı kontrol et"""
        for gen in generations:
            if hasattr(gen, "message"):
                msg = gen.message
                # AIMessage tool_calls kontrolü
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    return True
                # additional_kwargs içinde tool_calls kontrolü
                if hasattr(msg, "additional_kwargs"):
                    if msg.additional_kwargs.get("tool_calls"):
                        return True
        return False
    
    def _has_tool_results_in_prompt(self, prompt: str) -> bool:
        """Prompt'ta tool sonuçları var mı kontrol et"""
        # LangChain prompt formatında tool results genelde şu şekilde görünür
        tool_result_indicators = [
            "ToolMessage",
            "tool_result",
            "✅ kayıt bulundu",
            "⚪ Sonuç bulunamadı",
            "📊 Gösterilen:",
            "✅ içerik bulundu",
        ]
        return any(indicator in prompt for indicator in tool_result_indicators)
    
    def lookup(self, prompt: str, llm_string: str) -> Optional[Sequence[Any]]:
        """
        Cache'ten ara.
        
        Mode davranışları:
        - "final_only": Sadece final response'lar cache'lenir
        - "all": Tüm response'lar cache'lenir (tool_calls serialize edilir)
        - "tool_decision_only": Sadece ilk tool kararı cache'lenir, 
          tool sonuçları değerlendirmesi cache'lenmez
        
        Returns:
            Cached generations veya None
        """
        try:
            # 🎯 tool_decision_only: Tool results varsa cache'i bypass et
            if self._mode == "tool_decision_only" and self._has_tool_results_in_prompt(prompt):
                logger.debug(f"⏭️ Cache BYPASS (tool results in prompt, mode=tool_decision_only)")
                self._stats["skipped"] += 1
                return None  # Cache'i bypass et, gerçek LLM çağrısı yapsın
            
            result = self._base_cache.lookup(prompt, llm_string)
            
            if result is not None:
                self._stats["hits"] += 1
                logger.debug(f"🎯 Cache HIT (total: {self._stats['hits']})")
                
                # Mode "all" veya "tool_decision_only" ise ve tool_calls varsa, deserialize et
                if self._mode in ("all", "tool_decision_only"):
                    result = self._deserialize_tool_calls(result)
                
            else:
                self._stats["misses"] += 1
                logger.debug(f"❌ Cache MISS (total: {self._stats['misses']})")
            
            return result
            
        except Exception as e:
            logger.warning(f"⚠️ Cache lookup error: {e}")
            self._stats["misses"] += 1
            return None
    
    def update(self, prompt: str, llm_string: str, return_val: Sequence[Any]) -> None:
        """
        Cache'e kaydet.
        
        Mode davranışları:
        - mode="final_only": Tool calls içeren response'ları cache'lemez, sadece final
        - mode="all": Tüm response'ları cache'ler (tool_calls serialize edilir)
        - mode="tool_decision_only": SADECE tool calls içeren response'ları cache'ler,
          final response'ları cache'lemez (tam tersi!)
        """
        try:
            has_tools = self._has_tool_calls(return_val)
            has_tool_results = self._has_tool_results_in_prompt(prompt)
            
            if self._mode == "final_only" and has_tools:
                # Tool calls var, cache'leme
                self._stats["skipped"] += 1
                logger.debug(f"⏭️ Cache SKIP (tool_calls var, mode=final_only)")
                return
            
            if self._mode == "tool_decision_only":
                # Tool sonuçları değerlendirmesi cache'lenmez
                if has_tool_results:
                    self._stats["skipped"] += 1
                    logger.debug(f"⏭️ Cache SKIP (tool results in prompt, mode=tool_decision_only)")
                    return
                
                # Final response cache'lenmez (tool calls olmayanlar)
                if not has_tools:
                    self._stats["skipped"] += 1
                    logger.debug(f"⏭️ Cache SKIP (final response, mode=tool_decision_only)")
                    return
                
                # Sadece ilk tool decision cache'lenir
                return_val = self._serialize_tool_calls(return_val)
                logger.debug(f"💾 Cache SAVE (tool decision, mode=tool_decision_only)")
            
            if self._mode == "all" and has_tools:
                # Tool calls'ı serialize et
                return_val = self._serialize_tool_calls(return_val)
            
            self._base_cache.update(prompt, llm_string, return_val)
            logger.debug(f"💾 Cache SAVE")
            
        except Exception as e:
            logger.warning(f"⚠️ Cache update error: {e}")
    
    def _serialize_tool_calls(self, generations: Sequence[Any]) -> Sequence[Any]:
        """Tool calls'ı JSON string'e serialize et, session-specific block'ları kaldır"""
        try:
            from langchain_core.outputs import ChatGeneration
            from langchain_core.messages import AIMessage
            
            serialized = []
            for gen in generations:
                if hasattr(gen, "message") and hasattr(gen.message, "tool_calls") and gen.message.tool_calls:
                    msg = gen.message
                    # Tool calls'ı additional_kwargs'a JSON olarak kaydet
                    tool_calls_json = json.dumps(msg.tool_calls, default=str)
                    new_kwargs = dict(msg.additional_kwargs)
                    new_kwargs["_serialized_tool_calls"] = tool_calls_json
                    
                    # Content'ten session-specific block'ları kaldır
                    # OpenAI GPT-5/o1/o3 modelleri şunları kullanır:
                    # - reasoning (rs_...) - düşünme bloğu
                    # - function_call (fc_...) - fonksiyon çağrısı
                    # - tool_use - tool kullanımı
                    content = msg.content
                    if isinstance(content, list):
                        # Session-specific tüm block'ları filtrele
                        content = [
                            item for item in content 
                            if not (isinstance(item, dict) and item.get("type") in ("reasoning", "function_call", "tool_use"))
                        ]
                        # Eğer hepsi filtrelendiyse, boş string yap
                        if not content:
                            content = ""
                    
                    # Yeni AIMessage oluştur (tool_calls olmadan, JSON ile)
                    new_msg = AIMessage(
                        content=content,
                        additional_kwargs=new_kwargs,
                        response_metadata=getattr(msg, "response_metadata", {}),
                    )
                    serialized.append(ChatGeneration(message=new_msg))
                else:
                    serialized.append(gen)
            
            return serialized
            
        except Exception as e:
            logger.warning(f"⚠️ Tool calls serialize error: {e}")
            return generations
    
    def _deserialize_tool_calls(self, generations: Sequence[Any]) -> Sequence[Any]:
        """JSON string'den tool_calls'ı geri yükle"""
        try:
            import uuid
            from langchain_core.outputs import ChatGeneration
            from langchain_core.messages import AIMessage
            
            deserialized = []
            for gen in generations:
                if hasattr(gen, "message"):
                    msg = gen.message
                    additional = dict(getattr(msg, "additional_kwargs", {}))
                    
                    if "_serialized_tool_calls" in additional:
                        # JSON'dan tool_calls'ı geri yükle
                        tool_calls_json = additional.pop("_serialized_tool_calls")
                        tool_calls = json.loads(tool_calls_json)
                        
                        # Tool call ID'lerini yenile (duplicate sorununu önlemek için)
                        for tc in tool_calls:
                            if "id" in tc:
                                tc["id"] = f"call_{uuid.uuid4().hex[:24]}"
                        
                        # Content'ten session-specific block'ları kaldır (varsa)
                        content = msg.content
                        if isinstance(content, list):
                            content = [
                                item for item in content 
                                if not (isinstance(item, dict) and item.get("type") in ("reasoning", "function_call", "tool_use"))
                            ]
                            if not content:
                                content = ""
                        
                        # Yeni AIMessage oluştur (tool_calls ile)
                        new_msg = AIMessage(
                            content=content,
                            tool_calls=tool_calls,
                            additional_kwargs=additional,
                            response_metadata=getattr(msg, "response_metadata", {}),
                        )
                        deserialized.append(ChatGeneration(message=new_msg))
                    else:
                        deserialized.append(gen)
                else:
                    deserialized.append(gen)
            
            return deserialized
            
        except Exception as e:
            logger.warning(f"⚠️ Tool calls deserialize error: {e}")
            return generations
    
    def clear(self, **kwargs: Any) -> None:
        """Cache temizle"""
        if hasattr(self._base_cache, "clear"):
            self._base_cache.clear(**kwargs)
    
    def get_stats(self) -> dict:
        """Cache istatistikleri"""
        return {
            "mode": self._mode,
            "hits": self._stats["hits"],
            "misses": self._stats["misses"],
            "skipped": self._stats["skipped"],
            "hit_rate": f"{self._stats['hits'] / max(1, self._stats['hits'] + self._stats['misses']) * 100:.1f}%"
        }


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
    # Semantic cache için REDIS_SEMANTIC_URL kullan (Redis Stack gerekli)
    redis_url = redis_url or REDIS_SEMANTIC_URL
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
        base_cache = RedisSemanticCache(
            redis_url=redis_url,
            embeddings=embeddings,
            distance_threshold=1 - similarity_threshold,  # Redis distance = 1 - similarity
            ttl=ttl
        )
        
        # AgentAwareSemanticCache wrapper ile sar
        # Bu wrapper tool_calls serialize/deserialize sorununu çözer
        semantic_cache = AgentAwareSemanticCache(base_cache, mode=CACHE_MODE)
        
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

