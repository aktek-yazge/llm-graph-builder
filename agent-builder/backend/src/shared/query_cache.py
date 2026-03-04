# -*- coding: utf-8 -*-
"""
Query-Level Semantic Cache Module

ReAct Agent sorgularını cache'ler. Benzer sorular geldiğinde
tüm agent akışını atlar ve cache'den cevap döner.

Bu modül Redis semantic cache'den farklıdır:
- redis_cache.py: LangChain LLM çağrılarını cache'ler (düşük seviye)
- query_cache.py: Tam agent yanıtlarını cache'ler (yüksek seviye)

Kullanım:
    from src.shared.query_cache import QueryCache
    
    cache = QueryCache()
    
    # Cache'de ara
    cached = await cache.get_similar(question, session_id)
    if cached:
        return cached["response"]
    
    # Agent çalıştır...
    
    # Cache'e yaz
    await cache.set(question, response, session_id, metrics)

Environment Variables:
    QUERY_CACHE_ENABLED: Enable/disable (default: true)
    QUERY_CACHE_TTL: Cache TTL in seconds (default: 3600 = 1 hour)
    QUERY_CACHE_SIMILARITY: Similarity threshold (default: 0.92)
    QUERY_CACHE_BACKEND: "redis" or "postgres" (default: redis)
"""

import os
import json
import hashlib
import logging
from typing import Optional, Dict, Any, List, cast
from datetime import datetime, timezone
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)

# Environment configuration
QUERY_CACHE_ENABLED = os.getenv("QUERY_CACHE_ENABLED", "true").lower() in ("true", "1", "yes")
QUERY_CACHE_TTL = int(os.getenv("QUERY_CACHE_TTL", "3600"))  # 1 hour default
QUERY_CACHE_SIMILARITY = float(os.getenv("QUERY_CACHE_SIMILARITY", "0.92"))
QUERY_CACHE_BACKEND = os.getenv("QUERY_CACHE_BACKEND", "redis")


@dataclass
class CacheEntry:
    """Cache entry structure"""
    question: str
    response: str
    sources: Dict[str, List[str]]
    metrics: Dict[str, Any]
    session_id: str
    created_at: str
    embedding: Optional[List[float]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CacheEntry":
        return cls(**data)


class QueryCacheMetrics:
    """Query cache metrics for monitoring"""
    
    def __init__(self):
        self.total_queries = 0
        self.cache_hits = 0
        self.cache_misses = 0
        self.cache_writes = 0
        self.cache_errors = 0
    
    @property
    def hit_rate(self) -> float:
        if self.total_queries == 0:
            return 0.0
        return round(self.cache_hits / self.total_queries * 100, 2)
    
    def record_hit(self):
        self.total_queries += 1
        self.cache_hits += 1
    
    def record_miss(self):
        self.total_queries += 1
        self.cache_misses += 1
    
    def record_write(self):
        self.cache_writes += 1
    
    def record_error(self):
        self.cache_errors += 1
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_queries": self.total_queries,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "cache_writes": self.cache_writes,
            "cache_errors": self.cache_errors,
            "hit_rate_percent": self.hit_rate,
        }


# Global metrics instance
_cache_metrics = QueryCacheMetrics()


def get_cache_metrics() -> QueryCacheMetrics:
    """Get global cache metrics instance"""
    return _cache_metrics


class QueryCache:
    """
    Query-level semantic cache for ReAct agent responses.
    
    Features:
    - Semantic similarity matching via embeddings
    - TTL-based expiration
    - Session-aware caching
    - Metrics tracking
    """
    
    def __init__(
        self,
        enabled: Optional[bool] = None,
        ttl: Optional[int] = None,
        similarity_threshold: Optional[float] = None,
        backend: Optional[str] = None,
    ):
        self.enabled = enabled if enabled is not None else QUERY_CACHE_ENABLED
        self.ttl = ttl or QUERY_CACHE_TTL
        self.similarity_threshold = similarity_threshold or QUERY_CACHE_SIMILARITY
        self.backend = backend or QUERY_CACHE_BACKEND
        
        self._embeddings = None
        self._redis_client = None
        self._initialized = False
        
        if self.enabled:
            self._initialize()
    
    def _initialize(self):
        """Initialize cache backend and embeddings"""
        if self._initialized:
            return
        
        try:
            # Initialize embeddings for semantic similarity
            self._embeddings = self._create_embeddings()
            
            if self.backend == "redis":
                self._init_redis()
            else:
                logger.info(f"ℹ️ Using in-memory cache (backend: {self.backend})")
                self._memory_cache: Dict[str, CacheEntry] = {}
            
            self._initialized = True
            logger.info(f"✅ QueryCache initialized (backend: {self.backend}, TTL: {self.ttl}s)")
            
        except Exception as e:
            logger.warning(f"⚠️ QueryCache initialization failed: {e}")
            self.enabled = False
    
    def _init_redis(self):
        """Initialize Redis client"""
        try:
            import redis
            
            redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
            self._redis_client = redis.from_url(redis_url)
            self._redis_client.ping()
            logger.info(f"✅ Redis connected for QueryCache")
            
        except ImportError:
            logger.warning("⚠️ redis package not installed, falling back to memory cache")
            self.backend = "memory"
            self._memory_cache = {}
        except Exception as e:
            logger.warning(f"⚠️ Redis connection failed: {e}, falling back to memory cache")
            self.backend = "memory"
            self._memory_cache = {}
    
    def _create_embeddings(self):
        """Create embeddings model for semantic similarity"""
        try:
            # Try OpenAI first (faster for queries)
            openai_key = os.getenv("OPENAI_API_KEY")
            if openai_key:
                from langchain_openai import OpenAIEmbeddings
                from pydantic import SecretStr
                return OpenAIEmbeddings(
                    model="text-embedding-3-small",
                    api_key=SecretStr(openai_key)
                )
            
            # Fallback to HuggingFace (local)
            from langchain_huggingface import HuggingFaceEmbeddings
            return HuggingFaceEmbeddings(
                model_name="sentence-transformers/all-MiniLM-L6-v2"
            )
            
        except Exception as e:
            logger.warning(f"⚠️ Embeddings creation failed: {e}")
            return None
    
    def _compute_embedding(self, text: str) -> Optional[List[float]]:
        """Compute embedding for text"""
        if not self._embeddings:
            return None
        
        try:
            return self._embeddings.embed_query(text)
        except Exception as e:
            logger.warning(f"⚠️ Embedding computation failed: {e}")
            return None
    
    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """Compute cosine similarity between two vectors"""
        import math
        
        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        norm1 = math.sqrt(sum(a * a for a in vec1))
        norm2 = math.sqrt(sum(b * b for b in vec2))
        
        if norm1 == 0 or norm2 == 0:
            return 0.0
        
        return dot_product / (norm1 * norm2)
    
    def _get_cache_key(self, question: str) -> str:
        """Generate cache key from question"""
        # Normalize question
        normalized = question.lower().strip()
        hash_val = hashlib.md5(normalized.encode()).hexdigest()[:12]
        return f"query_cache:{hash_val}"
    
    async def get_similar(
        self,
        question: str,
        session_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Find similar cached query.
        
        Args:
            question: The question to search for
            session_id: Optional session ID for filtering
        
        Returns:
            Cached response dict or None
        """
        if not self.enabled:
            return None
        
        try:
            # Compute question embedding
            query_embedding = self._compute_embedding(question)
            if not query_embedding:
                _cache_metrics.record_miss()
                return None
            
            # Search cache
            if self.backend == "redis" and self._redis_client:
                return await self._search_redis(question, query_embedding)
            else:
                return self._search_memory(question, query_embedding)
                
        except Exception as e:
            logger.warning(f"⚠️ Cache search error: {e}")
            _cache_metrics.record_error()
            return None
    
    async def _search_redis(
        self,
        question: str,
        query_embedding: List[float]
    ) -> Optional[Dict[str, Any]]:
        """Search Redis for similar cached queries"""
        try:
            if not self._redis_client:
                _cache_metrics.record_miss()
                return None
            
            # Get all cache keys
            keys_result = self._redis_client.keys("query_cache:*")
            keys = cast(List[bytes], keys_result) if keys_result else []
            if not keys:
                _cache_metrics.record_miss()
                return None
            
            best_match = None
            best_similarity = 0.0
            
            for key in keys:
                try:
                    data = self._redis_client.get(key)
                    if not data:
                        continue
                    
                    data_str = data if isinstance(data, (str, bytes)) else str(data)
                    entry_dict = json.loads(data_str)
                    cached_embedding = entry_dict.get("embedding")
                    
                    if not cached_embedding:
                        continue
                    
                    similarity = self._cosine_similarity(query_embedding, cached_embedding)
                    
                    if similarity >= self.similarity_threshold and similarity > best_similarity:
                        best_similarity = similarity
                        best_match = entry_dict
                        
                except Exception as e:
                    logger.debug(f"Skip invalid cache entry: {e}")
                    continue
            
            if best_match:
                _cache_metrics.record_hit()
                logger.info(f"🎯 Cache HIT (similarity: {best_similarity:.3f})")
                
                # Log to Langfuse
                try:
                    from src.shared.langfuse_client import log_llm_usage
                    log_llm_usage(
                        session_id=best_match.get("session_id", "unknown"),
                        model="cache",
                        input_tokens=0,
                        output_tokens=0,
                        step_name="query_cache_hit",
                        metadata={
                            "similarity": best_similarity,
                            "original_question": best_match.get("question", "")[:100],
                        }
                    )
                except:
                    pass
                
                return {
                    "response": best_match["response"],
                    "sources": best_match.get("sources", {"documents": [], "pages": []}),
                    "metrics": best_match.get("metrics", {}),
                    "cache_hit": True,
                    "similarity": best_similarity,
                }
            
            _cache_metrics.record_miss()
            return None
            
        except Exception as e:
            logger.warning(f"⚠️ Redis search error: {e}")
            _cache_metrics.record_error()
            return None
    
    def _search_memory(
        self,
        question: str,
        query_embedding: List[float]
    ) -> Optional[Dict[str, Any]]:
        """Search in-memory cache for similar queries"""
        best_match = None
        best_similarity = 0.0
        
        for key, entry in list(self._memory_cache.items()):
            if entry.embedding is None:
                continue
            
            similarity = self._cosine_similarity(query_embedding, entry.embedding)
            
            if similarity >= self.similarity_threshold and similarity > best_similarity:
                best_similarity = similarity
                best_match = entry
        
        if best_match:
            _cache_metrics.record_hit()
            logger.info(f"🎯 Cache HIT (memory, similarity: {best_similarity:.3f})")
            
            return {
                "response": best_match.response,
                "sources": best_match.sources,
                "metrics": best_match.metrics,
                "cache_hit": True,
                "similarity": best_similarity,
            }
        
        _cache_metrics.record_miss()
        return None
    
    async def set(
        self,
        question: str,
        response: str,
        session_id: str,
        sources: Optional[Dict[str, List[str]]] = None,
        metrics: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Cache a query response.
        
        Args:
            question: The question
            response: The response
            session_id: Session ID
            sources: Source documents/pages
            metrics: Response metrics
        
        Returns:
            Success status
        """
        if not self.enabled:
            return False
        
        try:
            # Compute embedding
            embedding = self._compute_embedding(question)
            
            entry = CacheEntry(
                question=question,
                response=response,
                sources=sources or {"documents": [], "pages": []},
                metrics=metrics or {},
                session_id=session_id,
                created_at=datetime.now(timezone.utc).isoformat(),
                embedding=embedding,
            )
            
            cache_key = self._get_cache_key(question)
            
            if self.backend == "redis" and self._redis_client:
                self._redis_client.setex(
                    cache_key,
                    self.ttl,
                    json.dumps(entry.to_dict())
                )
            else:
                self._memory_cache[cache_key] = entry
            
            _cache_metrics.record_write()
            logger.info(f"💾 Cached query (key: {cache_key[:20]}...)")
            
            return True
            
        except Exception as e:
            logger.warning(f"⚠️ Cache write error: {e}")
            _cache_metrics.record_error()
            return False
    
    def clear(self) -> bool:
        """Clear all cached queries"""
        try:
            if self.backend == "redis" and self._redis_client:
                keys_result = self._redis_client.keys("query_cache:*")
                keys = cast(List[bytes], keys_result) if keys_result else []
                if keys:
                    self._redis_client.delete(*keys)
                    logger.info(f"🗑️ Cleared {len(keys)} cached queries")
            else:
                self._memory_cache.clear()
                logger.info("🗑️ Memory cache cleared")
            
            return True
            
        except Exception as e:
            logger.warning(f"⚠️ Cache clear error: {e}")
            return False
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        stats = _cache_metrics.to_dict()
        stats["backend"] = self.backend
        stats["enabled"] = self.enabled
        stats["ttl"] = self.ttl
        stats["similarity_threshold"] = self.similarity_threshold
        
        try:
            if self.backend == "redis" and self._redis_client:
                keys_result = self._redis_client.keys("query_cache:*")
                keys = cast(List[bytes], keys_result) if keys_result else []
                stats["cached_queries"] = len(keys)
            else:
                stats["cached_queries"] = len(self._memory_cache)
        except:
            stats["cached_queries"] = "unknown"
        
        return stats


# Global cache instance
_query_cache: Optional[QueryCache] = None


def get_query_cache() -> QueryCache:
    """Get or create global query cache instance"""
    global _query_cache
    
    if _query_cache is None:
        _query_cache = QueryCache()
    
    return _query_cache

