# -*- coding: utf-8 -*-
"""
Langfuse Client - Centralized LLM Observability

Bu modül, tüm LLM çağrılarının izlenmesi için merkezi Langfuse entegrasyonu sağlar.

Kullanım:
    from src.shared.langfuse_client import get_langfuse, trace_llm_call, observe

    # Decorator ile
    @observe(name="my_function")
    def my_function():
        ...

    # Manuel trace ile
    with trace_llm_call("step_name", session_id="xxx") as span:
        result = llm.invoke(...)
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

# Langfuse session propagation helper
_propagate_attributes: Optional[Callable] = None
_propagate_attributes_checked = False


def _get_propagate_attributes() -> Optional[Callable]:
    """Get propagate_attributes function lazily"""
    global _propagate_attributes, _propagate_attributes_checked
    if not _propagate_attributes_checked:
        _propagate_attributes_checked = True
        try:
            from langfuse import propagate_attributes
            _propagate_attributes = propagate_attributes
        except ImportError:
            _propagate_attributes = None  # Mark as unavailable
    return _propagate_attributes

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
        
        logger.info(f"✅ Langfuse initialized: {host}")
        return _langfuse_instance
        
    except ImportError:
        logger.warning("⚠️ Langfuse package not installed. Run: pip install langfuse")
        return None
    except Exception as e:
        logger.error(f"❌ Langfuse initialization failed: {e}")
        return None


def get_langfuse_callback_handler(
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    trace_name: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
):
    """
    Get LangChain callback handler for automatic tracing.
    
    Usage:
        from langchain_openai import ChatOpenAI
        
        handler = get_langfuse_callback_handler(session_id="xxx")
        llm = ChatOpenAI(callbacks=[handler] if handler else [])
    
    Args:
        session_id: Session ID for grouping traces (stored in metadata)
        user_id: User ID for attribution (stored in metadata)
        trace_name: Custom trace name (stored in metadata)
        metadata: Additional metadata
    
    Returns:
        CallbackHandler or None
    
    Note:
        Langfuse SDK v3+ CallbackHandler has simplified parameters.
        Session/user info is passed via trace_context or metadata.
    """
    if not is_langfuse_enabled():
        return None
    
    try:
        from langfuse.langchain import CallbackHandler
        
        langfuse = get_langfuse()
        if not langfuse:
            return None
        
        # Langfuse SDK v3+: CallbackHandler has simplified API
        # Session info goes into update_trace callback or is set externally
        handler = CallbackHandler()
        
        # Note: session_id, user_id, trace_name, metadata are not directly supported
        # in SDK v3. They should be set via trace_context or langfuse.update_current_trace()
        # For now, we create a basic handler. The caller can set trace context separately.
        
        return handler
        
    except ImportError:
        logger.warning("⚠️ Langfuse callback handler not available")
        return None
    except Exception as e:
        logger.error(f"❌ Langfuse callback handler creation failed: {e}")
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
    Context manager for tracing LLM calls.
    
    Usage:
        with trace_llm_call("entity_extraction", session_id="xxx") as span:
            result = llm.invoke(prompt)
            span.update(
                output=result,
                usage={"input_tokens": 100, "output_tokens": 50},
                model="gpt-4o"
            )
    
    Args:
        name: Span/generation name
        session_id: Session ID
        user_id: User ID
        input_data: Input to the LLM
        metadata: Additional metadata
    
    Yields:
        Span object with update() method, or DummySpan if Langfuse not available
    """
    
    class DummySpan:
        """Dummy span for when Langfuse is not available"""
        def update(self, **kwargs):
            pass
        
        def end(self, **kwargs):
            pass
    
    if not is_langfuse_enabled():
        yield DummySpan()
        return
    
    langfuse = get_langfuse()
    if not langfuse:
        yield DummySpan()
        return
    
    try:
        # Langfuse SDK v3+ uses start_span/start_generation instead of trace()
        # session_id and user_id go into metadata
        span_metadata = {
            **(metadata or {}),
            "session_id": session_id,
            "user_id": user_id,
        }
        
        # Create a span for the LLM call
        span = langfuse.start_span(
            name=name,
            input=input_data,
            metadata=span_metadata,
        )
        
        # Create generation
        generation = langfuse.start_generation(
            name=name,
            input=input_data,
            metadata=span_metadata,
        )
        
        class SpanWrapper:
            def __init__(self, gen, parent_span):
                self._generation = gen
                self._span = parent_span
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
                try:
                    # End generation with update
                    if self._generation:
                        end_kwargs = {}
                        
                        if output is not None:
                            end_kwargs["output"] = output
                        
                        if usage:
                            # Langfuse expects specific keys
                            end_kwargs["usage"] = {
                                "input": usage.get("input_tokens", usage.get("input", 0)),
                                "output": usage.get("output_tokens", usage.get("output", 0)),
                                "total": usage.get("total_tokens", usage.get("total", 0)),
                            }
                            if "cached_tokens" in usage:
                                end_kwargs["usage"]["cached"] = usage["cached_tokens"]
                        
                        if model:
                            end_kwargs["model"] = model
                        
                        if level:
                            end_kwargs["level"] = level
                        
                        if status_message:
                            end_kwargs["status_message"] = status_message
                        
                        end_kwargs.update(kwargs)
                        self._generation.end(**end_kwargs)
                    
                    # Also end parent span
                    if self._span:
                        self._span.end(output=output)
                        
                except Exception as e:
                    logger.warning(f"⚠️ Langfuse update failed: {e}")
            
            def end(self, **kwargs):
                """End the generation and span"""
                try:
                    if self._generation:
                        self._generation.end(**kwargs)
                    if self._span:
                        self._span.end()
                except Exception as e:
                    logger.warning(f"⚠️ Langfuse end failed: {e}")
            
            @property
            def trace_id(self):
                return self._span.id if self._span else None
        
        wrapper = SpanWrapper(generation, span)
        
        try:
            yield wrapper
        except Exception as e:
            # Record error in span
            wrapper.update(
                level="ERROR",
                status_message=str(e),
            )
            raise
        
    except Exception as e:
        logger.warning(f"⚠️ Langfuse trace creation failed: {e}")
        yield DummySpan()


def observe(
    name: Optional[str] = None,
    session_id_arg: Optional[str] = None,
    capture_input: bool = True,
    capture_output: bool = True,
):
    """
    Decorator for observing functions with Langfuse.
    
    Usage:
        @observe(name="process_chunk")
        def process_chunk(chunk_id: str, content: str):
            ...
        
        @observe(session_id_arg="session_id")
        async def handle_query(question: str, session_id: str):
            ...
    
    Args:
        name: Custom name for the trace (default: function name)
        session_id_arg: Argument name to extract session_id from
        capture_input: Whether to capture function input
        capture_output: Whether to capture function output
    """
    def decorator(func: Callable):
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            if not is_langfuse_enabled():
                return func(*args, **kwargs)
            
            trace_name = name or func.__name__
            
            # Extract session_id from kwargs if specified
            session_id = None
            if session_id_arg and session_id_arg in kwargs:
                session_id = kwargs[session_id_arg]
            
            # Capture input
            input_data = None
            if capture_input:
                input_data = {"args": str(args), "kwargs": str(kwargs)}
            
            with trace_llm_call(
                name=trace_name,
                session_id=session_id,
                input_data=input_data,
            ) as span:
                result = func(*args, **kwargs)
                
                if capture_output:
                    output_str = str(result) if result else None
                    span.update(output=output_str)
                
                return result
        
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            if not is_langfuse_enabled():
                return await func(*args, **kwargs)
            
            trace_name = name or func.__name__
            
            # Extract session_id from kwargs if specified
            session_id = None
            if session_id_arg and session_id_arg in kwargs:
                session_id = kwargs[session_id_arg]
            
            # Capture input
            input_data = None
            if capture_input:
                input_data = {"args": str(args), "kwargs": str(kwargs)}
            
            with trace_llm_call(
                name=trace_name,
                session_id=session_id,
                input_data=input_data,
            ) as span:
                result = await func(*args, **kwargs)
                
                if capture_output:
                    output_str = str(result) if result else None
                    span.update(output=output_str)
                
                return result
        
        # Return appropriate wrapper based on function type
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper
    
    return decorator


def log_llm_usage(
    session_id: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int = 0,
    reasoning_tokens: int = 0,
    cost_usd: float = 0.0,
    latency_ms: float = 0.0,
    step_name: str = "llm_call",
    metadata: Optional[Dict[str, Any]] = None,
    llm_input: Optional[Any] = None,
    llm_output: Optional[Any] = None,
):
    """
    Log LLM usage to Langfuse without full trace.
    Useful for logging from TokenTracker or similar.
    
    Args:
        session_id: Session ID
        model: Model name
        input_tokens: Input token count
        output_tokens: Output token count
        cached_tokens: Cached token count
        reasoning_tokens: GPT-5 reasoning token count
        cost_usd: Estimated cost in USD
        latency_ms: Latency in milliseconds
        step_name: Name of the step
        metadata: Additional metadata
        llm_input: LLM input (messages/prompt) - SHOWS IN DASHBOARD
        llm_output: LLM output (response) - SHOWS IN DASHBOARD
    """
    if not is_langfuse_enabled():
        return
    
    langfuse = get_langfuse()
    if not langfuse:
        return
    
    try:
        # Debug: input/output değerlerini logla
        logger.info(f"📊 Langfuse log_llm_usage: step={step_name}, input={llm_input is not None}, output={llm_output is not None}")
        if llm_output:
            logger.info(f"   └─ output type: {type(llm_output)}, keys: {llm_output.keys() if isinstance(llm_output, dict) else 'N/A'}")
        
        # Langfuse SDK v3+: start_generation -> update -> end
        # INPUT/OUTPUT are critical for dashboard visibility!
        generation = langfuse.start_generation(
            name=step_name,
            model=model,
            input=llm_input,  # 🔑 Dashboard'da görünür
            output=llm_output,  # 🔑 Dashboard'da görünür
            metadata={
                "session_id": session_id,
                "model": model,
                "cost_usd": cost_usd,
                "latency_ms": latency_ms,
                "cache_hit_rate": round(cached_tokens / max(input_tokens, 1) * 100, 1),
                "reasoning_tokens": reasoning_tokens,  # 🧠 GPT-5 reasoning
                **(metadata or {}),
            },
        )
        
        # Update with usage details, then end
        # reasoning_tokens dahil - GPT-5'in düşünce süreci için harcanan tokenlar
        generation.update(
            usage_details={
                "input": input_tokens,
                "output": output_tokens,
                "cached": cached_tokens,
                "reasoning": reasoning_tokens,  # 🧠 GPT-5 reasoning tokens
                "total": input_tokens + output_tokens,
            },
        )
        generation.end()
        
        logger.info(f"✅ Langfuse generation created: {step_name}")
        
    except Exception as e:
        logger.warning(f"⚠️ Langfuse usage logging failed: {e}")


# ============================================================================
# SESSION MANAGEMENT - Langfuse Sessions for grouping traces
# ============================================================================

@contextmanager
def langfuse_session(
    session_id: str,
    user_id: Optional[str] = None,
    trace_name: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    tags: Optional[list] = None,
):
    """
    Context manager for Langfuse Session tracking.
    
    All traces/observations created within this context will be grouped
    into the same session in Langfuse UI.
    
    Usage:
        with langfuse_session(session_id="user-session-123", user_id="user-456"):
            # All langfuse traces here will be grouped under this session
            result = agent.run(question)
    
    Args:
        session_id: Unique session identifier (max 200 chars)
        user_id: Optional user identifier for attribution
        trace_name: Optional trace name
        metadata: Additional metadata to propagate
        tags: Optional list of tags
    
    See: https://langfuse.com/docs/observability/features/sessions
    """
    if not is_langfuse_enabled():
        yield
        return
    
    propagate_fn = _get_propagate_attributes()
    if not propagate_fn:
        logger.debug("propagate_attributes not available, session tracking disabled")
        yield
        return
    
    try:
        # Use Langfuse's propagate_attributes to set session context
        with propagate_fn(
            session_id=session_id,
            user_id=user_id,
            trace_name=trace_name,
            metadata=metadata,
            tags=tags,
        ):
            logger.debug(f"📊 Langfuse session started: {session_id}")
            yield
            logger.debug(f"📊 Langfuse session ended: {session_id}")
    except Exception as e:
        logger.warning(f"⚠️ Langfuse session context failed: {e}")
        yield


def start_langfuse_session(
    session_id: str,
    user_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
):
    """
    Start propagating session attributes (non-context-manager version).
    
    Call this at the beginning of a request/interaction.
    All subsequent Langfuse observations will be grouped under this session.
    
    Args:
        session_id: Unique session identifier
        user_id: Optional user identifier
        metadata: Additional metadata
    
    Returns:
        Context object or None
    """
    if not is_langfuse_enabled():
        return None
    
    propagate_fn = _get_propagate_attributes()
    if not propagate_fn:
        return None
    
    try:
        # Enter the propagation context
        ctx = propagate_fn(
            session_id=session_id,
            user_id=user_id,
            metadata=metadata,
        )
        ctx.__enter__()
        logger.debug(f"📊 Langfuse session propagation started: {session_id}")
        return ctx
    except Exception as e:
        logger.warning(f"⚠️ Langfuse session start failed: {e}")
        return None


def end_langfuse_session(ctx):
    """
    End session attribute propagation.
    
    Args:
        ctx: Context object returned by start_langfuse_session
    """
    if ctx:
        try:
            ctx.__exit__(None, None, None)
            logger.debug("📊 Langfuse session propagation ended")
        except Exception as e:
            logger.warning(f"⚠️ Langfuse session end failed: {e}")


def flush_langfuse():
    """Flush pending Langfuse events (call before shutdown)"""
    langfuse = get_langfuse()
    if langfuse:
        try:
            langfuse.flush()
            logger.info("✅ Langfuse flushed")
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


# ============================================================================
# PROMPT MANAGEMENT
# ============================================================================

# In-memory prompt cache
_prompt_cache: Dict[str, Any] = {}
_prompt_cache_ttl: Dict[str, datetime] = {}
PROMPT_CACHE_TTL_SECONDS = int(os.environ.get("LANGFUSE_PROMPT_CACHE_TTL", "300"))  # 5 min


def get_prompt(
    name: str,
    prompt_type: str = "text",
    label: str = "production",
    version: Optional[int] = None,
    fallback: Optional[str] = None,
    cache_enabled: bool = True,
) -> Optional[Any]:
    """
    Langfuse'dan prompt al.
    
    Langfuse Prompt Management sayesinde:
    - Kod değişikliği olmadan prompt güncellenebilir
    - Version control ve rollback yapılabilir
    - A/B testing için farklı label'lar kullanılabilir
    - Production/staging ayrımı yapılabilir
    
    Args:
        name: Prompt adı (Langfuse'da tanımlı)
        prompt_type: "text" veya "chat"
        label: "production", "staging", "latest" vb.
        version: Spesifik versiyon (None = label'a göre)
        fallback: Langfuse erişilemezse kullanılacak prompt
        cache_enabled: In-memory cache kullan
    
    Returns:
        Langfuse Prompt object veya None
    
    Usage:
        prompt = get_prompt("react-agent-system", prompt_type="text")
        if prompt:
            compiled = prompt.compile(schema_info=schema, user_question=question)
    """
    if not is_langfuse_enabled():
        logger.debug(f"Langfuse disabled, returning fallback for prompt: {name}")
        return _create_fallback_prompt(fallback) if fallback else None
    
    # Cache key
    cache_key = f"{name}:{prompt_type}:{label}:{version}"
    
    # Check cache
    if cache_enabled and cache_key in _prompt_cache:
        cache_time = _prompt_cache_ttl.get(cache_key)
        if cache_time and (datetime.now() - cache_time).total_seconds() < PROMPT_CACHE_TTL_SECONDS:
            logger.debug(f"📋 Prompt cache hit: {name}")
            return _prompt_cache[cache_key]
    
    langfuse = get_langfuse()
    if not langfuse:
        logger.warning(f"⚠️ Langfuse not available, returning fallback for prompt: {name}")
        return _create_fallback_prompt(fallback) if fallback else None
    
    try:
        # Get prompt from Langfuse
        kwargs: Dict[str, Any] = {"type": prompt_type}
        
        if version is not None:
            kwargs["version"] = version
        else:
            kwargs["label"] = label
        
        prompt = langfuse.get_prompt(name, **kwargs)
        
        logger.info(f"✅ Prompt loaded from Langfuse: {name} (v{getattr(prompt, 'version', '?')})")
        
        # Cache it
        if cache_enabled:
            _prompt_cache[cache_key] = prompt
            _prompt_cache_ttl[cache_key] = datetime.now()
        
        return prompt
        
    except Exception as e:
        logger.warning(f"⚠️ Failed to get prompt '{name}' from Langfuse: {e}")
        
        # Return fallback if provided
        if fallback:
            logger.info(f"📋 Using fallback prompt for: {name}")
            return _create_fallback_prompt(fallback)
        
        return None


def _create_fallback_prompt(content: str) -> Any:
    """Fallback prompt wrapper oluştur - Langfuse API'sine benzer interface"""
    
    class FallbackPrompt:
        """Langfuse Prompt API'sine benzer fallback wrapper"""
        
        def __init__(self, template: str):
            self._template = template
            self.version = "fallback"
            self.name = "fallback"
            self.labels = ["fallback"]
        
        def compile(self, **variables) -> str:
            """
            Template'deki {{variable}} placeholder'larını değerlerle değiştir.
            Langfuse Mustache syntax kullanır: {{variable_name}}
            """
            result = self._template
            for key, value in variables.items():
                placeholder = "{{" + key + "}}"
                result = result.replace(placeholder, str(value) if value else "")
            return result
        
        @property
        def prompt(self) -> str:
            """Raw prompt template"""
            return self._template
    
    return FallbackPrompt(content)


def create_prompt(
    name: str,
    prompt: str,
    prompt_type: str = "text",
    labels: Optional[list] = None,
    config: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Langfuse'da yeni prompt oluştur veya mevcut olanı güncelle.
    
    Bu fonksiyon genellikle migration script'lerinde kullanılır.
    Günlük kullanımda Langfuse UI tercih edilir.
    
    Args:
        name: Prompt adı
        prompt: Prompt içeriği ({{variable}} syntax ile)
        prompt_type: "text" veya "chat"
        labels: ["production", "staging"] gibi label'lar
        config: Ek konfigürasyon (model, temperature vb.)
    
    Returns:
        True if successful, False otherwise
    
    Usage:
        create_prompt(
            name="react-agent-system",
            prompt="You are an AI agent. Schema: {{schema_info}}",
            labels=["production"]
        )
    """
    if not is_langfuse_enabled():
        logger.warning("Langfuse disabled, cannot create prompt")
        return False
    
    langfuse = get_langfuse()
    if not langfuse:
        logger.warning("Langfuse not available, cannot create prompt")
        return False
    
    try:
        # Langfuse SDK v3+ create_prompt signature
        # type must be "text" literal, not string variable
        langfuse.create_prompt(
            name=name,
            prompt=prompt,
            labels=labels or [],
            type="text" if prompt_type == "text" else None,  # type: ignore
            config=config or {},
        )
        
        logger.info(f"✅ Prompt created/updated in Langfuse: {name}")
        
        # Clear cache for this prompt
        keys_to_remove = [k for k in _prompt_cache.keys() if k.startswith(f"{name}:")]
        for key in keys_to_remove:
            del _prompt_cache[key]
            if key in _prompt_cache_ttl:
                del _prompt_cache_ttl[key]
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to create prompt '{name}' in Langfuse: {e}")
        return False


def clear_prompt_cache(name: Optional[str] = None):
    """
    Prompt cache'ini temizle.
    
    Args:
        name: Spesifik prompt adı (None = tümünü temizle)
    """
    global _prompt_cache, _prompt_cache_ttl
    
    if name:
        keys_to_remove = [k for k in _prompt_cache.keys() if k.startswith(f"{name}:")]
        for key in keys_to_remove:
            del _prompt_cache[key]
            if key in _prompt_cache_ttl:
                del _prompt_cache_ttl[key]
        logger.info(f"🗑️ Prompt cache cleared for: {name}")
    else:
        _prompt_cache.clear()
        _prompt_cache_ttl.clear()
        logger.info("🗑️ All prompt cache cleared")


def get_prompt_with_tracing(
    name: str,
    prompt_type: str = "text",
    label: str = "production",
    session_id: Optional[str] = None,
    **compile_vars,
) -> tuple[str, Optional[Any]]:
    """
    Prompt al, compile et ve trace'e bağla.
    
    Bu fonksiyon get_prompt + compile + trace linking yapar.
    
    Args:
        name: Prompt adı
        prompt_type: "text" veya "chat"
        label: "production", "staging" vb.
        session_id: Trace bağlamak için session ID
        **compile_vars: Prompt değişkenleri
    
    Returns:
        Tuple of (compiled_prompt, prompt_object)
    
    Usage:
        compiled, prompt = get_prompt_with_tracing(
            "react-agent-system",
            session_id="xxx",
            schema_info=schema,
            user_question=question
        )
    """
    prompt = get_prompt(name, prompt_type=prompt_type, label=label)
    
    if not prompt:
        return "", None
    
    try:
        compiled = prompt.compile(**compile_vars)
        
        # Log prompt usage with span (Langfuse SDK v3+)
        if session_id and is_langfuse_enabled():
            langfuse = get_langfuse()
            if langfuse:
                try:
                    span = langfuse.start_span(
                        name=f"prompt:{name}",
                        metadata={
                            "session_id": session_id,
                            "prompt_name": name,
                            "prompt_version": getattr(prompt, 'version', 'unknown'),
                            "prompt_labels": getattr(prompt, 'labels', []),
                            "compile_vars_keys": list(compile_vars.keys()),
                        },
                    )
                    span.end()  # Immediately end the span
                except Exception as e:
                    logger.debug(f"Prompt span logging skipped: {e}")
        
        return compiled, prompt
        
    except Exception as e:
        logger.error(f"❌ Failed to compile prompt '{name}': {e}")
        return "", prompt


# =============================================================================
# DATASET MANAGEMENT - Langfuse Datasets for Testing & Experiments
# =============================================================================

def create_dataset(
    name: str,
    description: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Any]:
    """
    Yeni bir Langfuse Dataset oluşturur.
    
    Dataset'ler test case'leri saklamak için kullanılır.
    Her dataset item, input + expected_output içerir.
    
    Args:
        name: Dataset adı (proje içinde benzersiz olmalı)
        description: Dataset açıklaması
        metadata: Ek metadata (author, type vb.)
    
    Returns:
        Dataset objesi veya None
        
    Usage:
        dataset = create_dataset(
            name="qa-test-cases",
            description="QA test soruları ve beklenen cevaplar",
            metadata={"author": "system", "type": "regression"}
        )
    """
    if not is_langfuse_enabled():
        return None
    
    langfuse = get_langfuse()
    if not langfuse:
        return None
    
    try:
        dataset = langfuse.create_dataset(
            name=name,
            description=description,
            metadata=metadata or {},
        )
        logger.info(f"✅ Dataset created: {name}")
        return dataset
    except Exception as e:
        # Dataset zaten varsa hata vermez, sadece log
        if "already exists" in str(e).lower():
            logger.debug(f"Dataset '{name}' already exists")
            return get_dataset(name)
        logger.error(f"❌ Failed to create dataset '{name}': {e}")
        return None


def get_dataset(name: str) -> Optional[Any]:
    """
    Mevcut bir dataset'i getirir.
    
    Args:
        name: Dataset adı
    
    Returns:
        Dataset objesi veya None
    """
    if not is_langfuse_enabled():
        return None
    
    langfuse = get_langfuse()
    if not langfuse:
        return None
    
    try:
        dataset = langfuse.get_dataset(name=name)
        return dataset
    except Exception as e:
        logger.debug(f"Dataset '{name}' not found: {e}")
        return None


def add_dataset_item(
    dataset_name: str,
    input_data: Any,
    expected_output: Optional[Any] = None,
    metadata: Optional[Dict[str, Any]] = None,
    source_trace_id: Optional[str] = None,
    source_observation_id: Optional[str] = None,
) -> Optional[Any]:
    """
    Dataset'e yeni bir test item ekler.
    
    Args:
        dataset_name: Hedef dataset adı
        input_data: Test input'u (soru, prompt vb.)
        expected_output: Beklenen çıktı (opsiyonel)
        metadata: Ek bilgiler (model, difficulty vb.)
        source_trace_id: Kaynak production trace ID (opsiyonel)
        source_observation_id: Kaynak observation ID (opsiyonel)
    
    Returns:
        Dataset item objesi veya None
        
    Usage:
        # Manuel test case ekleme
        add_dataset_item(
            dataset_name="qa-test-cases",
            input_data={"question": "Neo4j nedir?"},
            expected_output={"answer": "Neo4j bir graph veritabanıdır."},
            metadata={"difficulty": "easy", "category": "definition"}
        )
        
        # Production trace'den ekleme
        add_dataset_item(
            dataset_name="qa-test-cases",
            input_data={"question": question},
            expected_output={"answer": corrected_answer},
            source_trace_id="trace-xxx-123"
        )
    """
    if not is_langfuse_enabled():
        return None
    
    langfuse = get_langfuse()
    if not langfuse:
        return None
    
    try:
        item = langfuse.create_dataset_item(
            dataset_name=dataset_name,
            input=input_data,
            expected_output=expected_output,
            metadata=metadata or {},
            source_trace_id=source_trace_id,
            source_observation_id=source_observation_id,
        )
        logger.debug(f"✅ Dataset item added to '{dataset_name}'")
        return item
    except Exception as e:
        logger.error(f"❌ Failed to add item to dataset '{dataset_name}': {e}")
        return None


def add_trace_to_dataset(
    trace_id: str,
    dataset_name: str,
    expected_output: Optional[Any] = None,
    observation_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Any]:
    """
    Production trace'i dataset'e test case olarak ekler.
    
    Bu fonksiyon, hatalı veya düzeltilmesi gereken cevapları
    gelecekteki testler için kaydetmek için kullanılır.
    
    Args:
        trace_id: Production trace ID
        dataset_name: Hedef dataset adı
        expected_output: Düzeltilmiş/beklenen çıktı
        observation_id: Spesifik observation ID (opsiyonel)
        metadata: Ek bilgiler
    
    Returns:
        Dataset item objesi veya None
        
    Usage:
        # Hatalı bir cevabı düzeltip test case olarak ekle
        add_trace_to_dataset(
            trace_id="trace-abc-123",
            dataset_name="regression-tests",
            expected_output={"answer": "Düzeltilmiş cevap"},
            metadata={"error_type": "hallucination"}
        )
    """
    if not is_langfuse_enabled():
        return None
    
    langfuse = get_langfuse()
    if not langfuse:
        return None
    
    try:
        # Trace'den input'u al
        # Not: Bu API çağrısı trace detaylarını gerektirir
        item = langfuse.create_dataset_item(
            dataset_name=dataset_name,
            input=None,  # Trace'den otomatik alınacak
            expected_output=expected_output,
            metadata=metadata or {},
            source_trace_id=trace_id,
            source_observation_id=observation_id,
        )
        logger.info(f"✅ Trace '{trace_id}' added to dataset '{dataset_name}'")
        return item
    except Exception as e:
        logger.error(f"❌ Failed to add trace to dataset: {e}")
        return None


def run_experiment(
    dataset_name: str,
    run_name: str,
    run_function: Callable[[Any], Any],
    metadata: Optional[Dict[str, Any]] = None,
    max_items: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Dataset üzerinde experiment çalıştırır.
    
    Her dataset item için run_function'ı çalıştırır ve
    sonuçları Langfuse'a kaydeder.
    
    Args:
        dataset_name: Dataset adı
        run_name: Experiment run adı (örn: "gpt-5-v2")
        run_function: Her item için çalıştırılacak fonksiyon
        metadata: Experiment metadata
        max_items: Maksimum item sayısı (test için)
    
    Returns:
        Dict with experiment results
        
    Usage:
        async def test_agent(item):
            question = item.input["question"]
            result = await stream_react_agent_response(question)
            return {"answer": result["final_answer"]}
        
        results = run_experiment(
            dataset_name="qa-test-cases",
            run_name="react-agent-v2",
            run_function=test_agent,
            metadata={"model": "gpt-5", "prompt_version": "2.1"}
        )
    """
    if not is_langfuse_enabled():
        return {"status": "disabled", "items": 0, "errors": []}
    
    langfuse = get_langfuse()
    if not langfuse:
        return {"status": "no_client", "items": 0, "errors": []}
    
    try:
        # Dataset'i getir
        dataset = get_dataset(dataset_name)
        if not dataset:
            return {"status": "dataset_not_found", "items": 0, "errors": [f"Dataset '{dataset_name}' not found"]}
        
        results = {
            "status": "completed",
            "dataset": dataset_name,
            "run_name": run_name,
            "items": 0,
            "success": 0,
            "errors": [],
            "outputs": [],
        }
        
        items = dataset.items
        if max_items:
            items = items[:max_items]
        
        for item in items:
            try:
                # Run function ile item'ı işle
                output = run_function(item)
                
                # Dataset run olarak kaydet
                item.link(
                    trace_or_observation=langfuse.start_span(name=f"experiment:{run_name}"),
                    run_name=run_name,
                    run_metadata=metadata or {},
                )
                
                results["items"] += 1
                results["success"] += 1
                results["outputs"].append({
                    "item_id": item.id,
                    "input": item.input,
                    "expected": item.expected_output,
                    "actual": output,
                })
            except Exception as e:
                results["items"] += 1
                results["errors"].append({
                    "item_id": item.id,
                    "error": str(e),
                })
        
        logger.info(f"✅ Experiment '{run_name}' completed: {results['success']}/{results['items']} success")
        return results
        
    except Exception as e:
        logger.error(f"❌ Experiment failed: {e}")
        return {"status": "error", "items": 0, "errors": [str(e)]}


def get_dataset_items(dataset_name: str, limit: int = 100) -> list:
    """
    Dataset item'larını listeler.
    
    Args:
        dataset_name: Dataset adı
        limit: Maksimum item sayısı
    
    Returns:
        List of dataset items
    """
    if not is_langfuse_enabled():
        return []
    
    dataset = get_dataset(dataset_name)
    if not dataset:
        return []
    
    try:
        items = dataset.items[:limit]
        return [
            {
                "id": item.id,
                "input": item.input,
                "expected_output": item.expected_output,
                "metadata": getattr(item, 'metadata', {}),
                "source_trace_id": getattr(item, 'source_trace_id', None),
            }
            for item in items
        ]
    except Exception as e:
        logger.error(f"❌ Failed to get dataset items: {e}")
        return []

