# -*- coding: utf-8 -*-
"""
Langfuse Client - Centralized LLM Observability (Celery Worker Version)

Bu modül, Celery worker'daki LLM çağrılarının izlenmesi için Langfuse entegrasyonu sağlar.

Kullanım:
    from src.shared.langfuse_client import trace_llm_call, log_llm_usage

    # Context manager ile
    with trace_llm_call("gemini_ocr", session_id="file_123") as span:
        result = gemini_call(...)
        span.update(output=result, usage={"input": 100, "output": 50})

Environment Variables:
    LANGFUSE_PUBLIC_KEY: Langfuse public key
    LANGFUSE_SECRET_KEY: Langfuse secret key
    LANGFUSE_HOST: Langfuse host URL (default: https://cloud.langfuse.com)
    LANGFUSE_ENABLED: Enable/disable tracing (default: true)
"""

import os
import logging
from typing import Optional, Dict, Any, Callable
from functools import wraps
from contextlib import contextmanager
from datetime import datetime

logger = logging.getLogger(__name__)

# Global Langfuse instance
_langfuse_instance = None
_langfuse_enabled = None


def is_langfuse_enabled() -> bool:
    """Check if Langfuse is enabled via environment variable"""
    global _langfuse_enabled
    
    if _langfuse_enabled is None:
        enabled_str = os.environ.get("LANGFUSE_ENABLED", "true").lower()
        _langfuse_enabled = enabled_str in ("true", "1", "yes", "on")
        
        if not _langfuse_enabled:
            logger.info("📊 Langfuse disabled via LANGFUSE_ENABLED env var")
    
    return _langfuse_enabled


def get_langfuse():
    """
    Get or create global Langfuse client instance.
    
    Returns:
        Langfuse client or None if not configured/disabled
    """
    global _langfuse_instance
    
    if not is_langfuse_enabled():
        return None
    
    if _langfuse_instance is not None:
        return _langfuse_instance
    
    try:
        from langfuse import Langfuse
        
        public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
        secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
        host = os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")
        
        if not public_key or not secret_key:
            logger.warning(
                "⚠️ Langfuse keys not configured. Set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY"
            )
            return None
        
        _langfuse_instance = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=host,
        )
        
        logger.info(f"✅ Langfuse initialized (worker): {host}")
        return _langfuse_instance
        
    except ImportError:
        logger.warning("⚠️ Langfuse package not installed. Run: pip install langfuse")
        return None
    except Exception as e:
        logger.error(f"❌ Langfuse initialization failed: {e}")
        return None


class DummySpan:
    """Dummy span for when Langfuse is not available"""
    def update(self, **kwargs):
        pass
    
    def end(self, **kwargs):
        pass
    
    @property
    def trace_id(self):
        return None


@contextmanager
def trace_llm_call(
    name: str,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    input_data: Optional[Any] = None,
    metadata: Optional[Dict[str, Any]] = None,
):
    """
    Context manager for tracing LLM calls in Celery worker.
    
    Usage:
        with trace_llm_call("gemini_ocr", session_id="file_123") as span:
            result = gemini_call(prompt)
            span.update(
                output=result,
                usage={"input_tokens": 100, "output_tokens": 50},
                model="gemini-2.0-flash"
            )
    
    Args:
        name: Span/generation name
        session_id: Session ID (usually file_id)
        user_id: User ID
        input_data: Input to the LLM
        metadata: Additional metadata
    
    Yields:
        Span object with update() method, or DummySpan if Langfuse not available
    """
    if not is_langfuse_enabled():
        yield DummySpan()
        return
    
    langfuse = get_langfuse()
    if not langfuse:
        yield DummySpan()
        return
    
    try:
        # Create trace
        trace = langfuse.trace(
            name=name,
            session_id=session_id,
            user_id=user_id,
            input=input_data,
            metadata=metadata or {},
        )
        
        # Create generation span
        generation = trace.generation(
            name=name,
            input=input_data,
            metadata=metadata or {},
        )
        
        class SpanWrapper:
            def __init__(self, gen, tr):
                self._generation = gen
                self._trace = tr
                self._start_time = datetime.now()
            
            def update(
                self,
                output: Any = None,
                usage: Optional[Dict[str, int]] = None,
                model: Optional[str] = None,
                level: str = "DEFAULT",
                status_message: Optional[str] = None,
                **kwargs
            ):
                """Update generation with output and usage"""
                update_kwargs = {}
                
                if output is not None:
                    update_kwargs["output"] = output
                
                if usage:
                    update_kwargs["usage"] = {
                        "input": usage.get("input_tokens", usage.get("input", 0)),
                        "output": usage.get("output_tokens", usage.get("output", 0)),
                        "total": usage.get("total_tokens", usage.get("total", 0)),
                    }
                
                if model:
                    update_kwargs["model"] = model
                
                if level:
                    update_kwargs["level"] = level
                
                if status_message:
                    update_kwargs["status_message"] = status_message
                
                update_kwargs.update(kwargs)
                
                try:
                    self._generation.end(**update_kwargs)
                except Exception as e:
                    logger.warning(f"⚠️ Langfuse generation update failed: {e}")
            
            def end(self, **kwargs):
                """End the generation span"""
                try:
                    self._generation.end(**kwargs)
                except Exception as e:
                    logger.warning(f"⚠️ Langfuse generation end failed: {e}")
            
            @property
            def trace_id(self):
                return self._trace.id if self._trace else None
        
        span = SpanWrapper(generation, trace)
        
        try:
            yield span
        except Exception as e:
            span.update(
                level="ERROR",
                status_message=str(e),
            )
            raise
        
    except Exception as e:
        logger.warning(f"⚠️ Langfuse trace creation failed: {e}")
        yield DummySpan()


def log_llm_usage(
    session_id: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int = 0,
    cost_usd: float = 0.0,
    latency_ms: float = 0.0,
    step_name: str = "llm_call",
    metadata: Optional[Dict[str, Any]] = None,
):
    """
    Log LLM usage to Langfuse.
    
    Args:
        session_id: Session ID (file_id for worker)
        model: Model name
        input_tokens: Input token count
        output_tokens: Output token count
        cached_tokens: Cached token count
        cost_usd: Estimated cost in USD
        latency_ms: Latency in milliseconds
        step_name: Name of the step
        metadata: Additional metadata
    """
    if not is_langfuse_enabled():
        return
    
    langfuse = get_langfuse()
    if not langfuse:
        return
    
    try:
        trace = langfuse.trace(
            name=step_name,
            session_id=session_id,
            metadata={
                "model": model,
                "cost_usd": cost_usd,
                "latency_ms": latency_ms,
                **(metadata or {}),
            },
        )
        
        trace.generation(
            name=step_name,
            model=model,
            usage={
                "input": input_tokens,
                "output": output_tokens,
                "total": input_tokens + output_tokens,
            },
            metadata={
                "cost_usd": cost_usd,
                "latency_ms": latency_ms,
            },
        ).end()
        
    except Exception as e:
        logger.warning(f"⚠️ Langfuse usage logging failed: {e}")


def trace_document_processing(
    file_id: int,
    file_name: str,
    step: str,  # "chunking", "graph_creation", "embedding"
    status: str,  # "started", "completed", "failed"
    metadata: Optional[Dict[str, Any]] = None,
):
    """
    Log document processing steps to Langfuse.
    
    Args:
        file_id: File ID
        file_name: File name
        step: Processing step
        status: Step status
        metadata: Additional metadata
    """
    if not is_langfuse_enabled():
        return
    
    langfuse = get_langfuse()
    if not langfuse:
        return
    
    try:
        level = "DEFAULT"
        if status == "failed":
            level = "ERROR"
        elif status == "completed":
            level = "DEFAULT"
        
        langfuse.trace(
            name=f"document_{step}",
            session_id=f"file_{file_id}",
            input={"file_name": file_name, "step": step},
            metadata={
                "file_id": file_id,
                "file_name": file_name,
                "status": status,
                **(metadata or {}),
            },
            level=level,
        )
        
    except Exception as e:
        logger.warning(f"⚠️ Langfuse document trace failed: {e}")


def flush_langfuse():
    """Flush pending Langfuse events"""
    langfuse = get_langfuse()
    if langfuse:
        try:
            langfuse.flush()
        except Exception as e:
            logger.warning(f"⚠️ Langfuse flush failed: {e}")


def shutdown_langfuse():
    """Shutdown Langfuse client gracefully"""
    global _langfuse_instance
    
    if _langfuse_instance:
        try:
            _langfuse_instance.flush()
            _langfuse_instance.shutdown()
            logger.info("✅ Langfuse shutdown complete")
        except Exception as e:
            logger.warning(f"⚠️ Langfuse shutdown failed: {e}")
        finally:
            _langfuse_instance = None

