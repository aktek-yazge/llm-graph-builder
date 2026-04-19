# -*- coding: utf-8 -*-
"""
Rate Limiting Module - API Protection Layer

Bu modül FastAPI endpoint'leri ve LLM API çağrıları için rate limiting sağlar.

LAYER 4 Features:
- Per-endpoint rate limits (upload, chat, query)
- Per-IP rate limiting
- LLM API global rate limiting (OpenAI, Gemini)
- Custom rate limit exceeded responses
- Langfuse metrics integration

Kullanım:
    from src.shared.rate_limiter import limiter, rate_limit_exceeded_handler
    
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    
    @app.get("/api/chat")
    @limiter.limit("60/minute")
    async def chat_endpoint(request: Request):
        ...

Environment Variables:
    RATE_LIMIT_ENABLED: Enable/disable rate limiting (default: true)
    RATE_LIMIT_DEFAULT: Default rate limit (default: "100/minute")
    RATE_LIMIT_UPLOAD: Upload endpoint limit (default: "10/minute")
    RATE_LIMIT_CHAT: Chat endpoint limit (default: "60/minute")
    RATE_LIMIT_QUERY: Query endpoint limit (default: "120/minute")
    RATE_LIMIT_STORAGE: "memory" or "redis" (default: memory)
"""

import os
import logging
import time
from typing import Callable, Optional
from functools import wraps
from datetime import datetime

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# Environment configuration
RATE_LIMIT_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "true").lower() in ("true", "1", "yes")
RATE_LIMIT_DEFAULT = os.getenv("RATE_LIMIT_DEFAULT", "100/minute")
RATE_LIMIT_UPLOAD = os.getenv("RATE_LIMIT_UPLOAD", "10/minute")
RATE_LIMIT_CHAT = os.getenv("RATE_LIMIT_CHAT", "60/minute")
RATE_LIMIT_QUERY = os.getenv("RATE_LIMIT_QUERY", "120/minute")
RATE_LIMIT_STORAGE = os.getenv("RATE_LIMIT_STORAGE", "memory")

# Rate limit metrics
_rate_limit_metrics = {
    "total_requests": 0,
    "rate_limited_requests": 0,
    "by_endpoint": {},
}


def get_rate_limit_metrics() -> dict:
    """Get current rate limit metrics"""
    return _rate_limit_metrics.copy()


# Initialize SlowAPI limiter
limiter = None

if RATE_LIMIT_ENABLED:
    try:
        from slowapi import Limiter, _rate_limit_exceeded_handler
        from slowapi.util import get_remote_address
        from slowapi.errors import RateLimitExceeded
        
        # Configure storage backend
        if RATE_LIMIT_STORAGE == "redis":
            redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
            try:
                from slowapi.middleware import SlowAPIMiddleware
                limiter = Limiter(
                    key_func=get_remote_address,
                    default_limits=[RATE_LIMIT_DEFAULT],
                    storage_uri=redis_url,
                )
                logger.info(f"✅ Rate limiter initialized with Redis: {redis_url}")
            except Exception as e:
                logger.warning(f"⚠️ Redis storage failed, falling back to memory: {e}")
                limiter = Limiter(
                    key_func=get_remote_address,
                    default_limits=[RATE_LIMIT_DEFAULT],
                )
        else:
            limiter = Limiter(
                key_func=get_remote_address,
                default_limits=[RATE_LIMIT_DEFAULT],
            )
            logger.info("✅ Rate limiter initialized with in-memory storage")
        
    except ImportError:
        logger.warning("⚠️ slowapi not installed. Run: pip install slowapi")
        RATE_LIMIT_ENABLED = False
else:
    logger.info("⏭️ Rate limiting disabled (RATE_LIMIT_ENABLED=false)")


async def rate_limit_exceeded_handler(request: Request, exc) -> JSONResponse:
    """
    Custom handler for rate limit exceeded errors.
    Returns a user-friendly JSON response with retry information.
    """
    _rate_limit_metrics["rate_limited_requests"] += 1
    
    # Get endpoint info
    endpoint = request.url.path
    if endpoint not in _rate_limit_metrics["by_endpoint"]:
        _rate_limit_metrics["by_endpoint"][endpoint] = {"limited": 0}
    _rate_limit_metrics["by_endpoint"][endpoint]["limited"] += 1
    
    # Log the rate limit hit
    client_ip = request.client.host if request.client else "unknown"
    logger.warning(f"⚠️ Rate limit exceeded: {endpoint} from {client_ip}")
    
    # Try to log to Langfuse
    try:
        from src.shared.langfuse_client import log_llm_usage
        log_llm_usage(
            session_id="rate_limit",
            model="rate_limiter",
            input_tokens=0,
            output_tokens=0,
            step_name="rate_limit_exceeded",
            metadata={
                "endpoint": endpoint,
                "client_ip": client_ip,
            }
        )
    except:
        pass
    
    # Calculate retry-after (try to get from exception)
    retry_after = 60  # Default 60 seconds
    try:
        if hasattr(exc, 'detail') and 'retry' in str(exc.detail).lower():
            # Extract retry time from exception
            import re
            match = re.search(r'(\d+)\s*second', str(exc.detail))
            if match:
                retry_after = int(match.group(1))
    except:
        pass
    
    return JSONResponse(
        status_code=429,
        content={
            "error": "Rate limit exceeded",
            "message": "Çok fazla istek gönderdiniz. Lütfen biraz bekleyin.",
            "detail": "Too many requests. Please slow down.",
            "retry_after_seconds": retry_after,
            "timestamp": datetime.now().isoformat(),
        },
        headers={
            "Retry-After": str(retry_after),
            "X-RateLimit-Limit": RATE_LIMIT_DEFAULT,
        }
    )


def track_request(endpoint: str):
    """Track a request for metrics"""
    _rate_limit_metrics["total_requests"] += 1
    
    if endpoint not in _rate_limit_metrics["by_endpoint"]:
        _rate_limit_metrics["by_endpoint"][endpoint] = {"total": 0, "limited": 0}
    _rate_limit_metrics["by_endpoint"][endpoint]["total"] += 1


# ============================================================================
# LLM API RATE LIMITING
# ============================================================================

class LLMRateLimiter:
    """
    Rate limiter for LLM API calls (OpenAI, Gemini, etc.)
    
    Uses token bucket algorithm to prevent API quota exhaustion.
    """
    
    def __init__(
        self,
        requests_per_minute: int = 60,
        tokens_per_minute: int = 100000,
    ):
        self.requests_per_minute = requests_per_minute
        self.tokens_per_minute = tokens_per_minute
        
        # Token bucket state
        self._request_tokens = requests_per_minute
        self._token_tokens = tokens_per_minute
        self._last_refill = time.time()
        
        # Metrics
        self._total_requests = 0
        self._total_tokens = 0
        self._throttled_requests = 0
    
    def _refill_tokens(self):
        """Refill tokens based on elapsed time"""
        now = time.time()
        elapsed = now - self._last_refill
        
        # Refill proportionally to elapsed time
        request_refill = int(elapsed * (self.requests_per_minute / 60))
        token_refill = int(elapsed * (self.tokens_per_minute / 60))
        
        self._request_tokens = min(self.requests_per_minute, self._request_tokens + request_refill)
        self._token_tokens = min(self.tokens_per_minute, self._token_tokens + token_refill)
        
        if request_refill > 0 or token_refill > 0:
            self._last_refill = now
    
    def can_proceed(self, estimated_tokens: int = 1000) -> tuple[bool, float]:
        """
        Check if request can proceed.
        
        Args:
            estimated_tokens: Estimated token count for the request
        
        Returns:
            (can_proceed, wait_time_seconds)
        """
        self._refill_tokens()
        
        if self._request_tokens < 1:
            wait_time = 60 / self.requests_per_minute
            self._throttled_requests += 1
            return False, wait_time
        
        if self._token_tokens < estimated_tokens:
            wait_time = (estimated_tokens - self._token_tokens) / (self.tokens_per_minute / 60)
            self._throttled_requests += 1
            return False, wait_time
        
        return True, 0
    
    def consume(self, tokens_used: int = 1000):
        """Consume tokens after request completes"""
        self._request_tokens -= 1
        self._token_tokens -= tokens_used
        self._total_requests += 1
        self._total_tokens += tokens_used
    
    async def wait_if_needed(self, estimated_tokens: int = 1000):
        """Wait if rate limit would be exceeded"""
        import asyncio
        
        can_proceed, wait_time = self.can_proceed(estimated_tokens)
        
        if not can_proceed:
            logger.info(f"⏳ LLM rate limit: waiting {wait_time:.2f}s...")
            await asyncio.sleep(wait_time)
    
    def get_stats(self) -> dict:
        """Get rate limiter stats"""
        return {
            "requests_per_minute": self.requests_per_minute,
            "tokens_per_minute": self.tokens_per_minute,
            "available_requests": self._request_tokens,
            "available_tokens": self._token_tokens,
            "total_requests": self._total_requests,
            "total_tokens": self._total_tokens,
            "throttled_requests": self._throttled_requests,
        }


# Global LLM rate limiters
_openai_limiter: Optional[LLMRateLimiter] = None
_gemini_limiter: Optional[LLMRateLimiter] = None


def get_openai_limiter() -> LLMRateLimiter:
    """Get or create OpenAI rate limiter"""
    global _openai_limiter
    
    if _openai_limiter is None:
        rpm = int(os.getenv("OPENAI_RATE_LIMIT_RPM", "60"))
        tpm = int(os.getenv("OPENAI_RATE_LIMIT_TPM", "100000"))
        _openai_limiter = LLMRateLimiter(requests_per_minute=rpm, tokens_per_minute=tpm)
        logger.info(f"✅ OpenAI rate limiter: {rpm} RPM, {tpm} TPM")
    
    return _openai_limiter


def get_gemini_limiter() -> LLMRateLimiter:
    """Get or create Gemini rate limiter"""
    global _gemini_limiter
    
    if _gemini_limiter is None:
        rpm = int(os.getenv("GEMINI_RATE_LIMIT_RPM", "30"))
        tpm = int(os.getenv("GEMINI_RATE_LIMIT_TPM", "50000"))
        _gemini_limiter = LLMRateLimiter(requests_per_minute=rpm, tokens_per_minute=tpm)
        logger.info(f"✅ Gemini rate limiter: {rpm} RPM, {tpm} TPM")
    
    return _gemini_limiter


def llm_rate_limit(provider: str = "openai"):
    """
    Decorator for LLM API calls with rate limiting.
    
    Usage:
        @llm_rate_limit("openai")
        async def call_openai(prompt: str) -> str:
            ...
    """
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Get appropriate limiter
            if provider == "openai":
                limiter = get_openai_limiter()
            elif provider == "gemini":
                limiter = get_gemini_limiter()
            else:
                # No rate limiting for unknown providers
                return await func(*args, **kwargs)
            
            # Wait if needed
            await limiter.wait_if_needed()
            
            # Execute function
            result = await func(*args, **kwargs)
            
            # Track usage (estimate tokens from result length)
            estimated_tokens = len(str(result)) // 4 if result else 100
            limiter.consume(estimated_tokens)
            
            return result
        
        return wrapper
    return decorator


# ============================================================================
# FASTAPI MIDDLEWARE SETUP
# ============================================================================

def setup_rate_limiting(app):
    """
    Setup rate limiting for FastAPI app.
    
    Call this in your FastAPI app initialization:
        from src.shared.rate_limiter import setup_rate_limiting
        setup_rate_limiting(app)
    """
    if not RATE_LIMIT_ENABLED or limiter is None:
        logger.info("⏭️ Rate limiting not enabled")
        return
    
    try:
        from slowapi import _rate_limit_exceeded_handler
        from slowapi.errors import RateLimitExceeded
        from slowapi.middleware import SlowAPIMiddleware
        
        app.state.limiter = limiter
        app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
        app.add_middleware(SlowAPIMiddleware)
        
        logger.info("✅ Rate limiting middleware installed")
        
    except ImportError as e:
        logger.warning(f"⚠️ Could not setup rate limiting middleware: {e}")
    except Exception as e:
        logger.error(f"❌ Rate limiting setup error: {e}")


# Predefined rate limit decorators for common endpoints
def upload_limit():
    """Rate limit for file upload endpoints"""
    if limiter:
        return limiter.limit(RATE_LIMIT_UPLOAD)
    return lambda f: f


def chat_limit():
    """Rate limit for chat/query endpoints"""
    if limiter:
        return limiter.limit(RATE_LIMIT_CHAT)
    return lambda f: f


def query_limit():
    """Rate limit for database query endpoints"""
    if limiter:
        return limiter.limit(RATE_LIMIT_QUERY)
    return lambda f: f

