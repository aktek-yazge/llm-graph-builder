"""
ReAct Agent with Multi-Provider Support (OpenAI & Anthropic)

Bu modül, OpenAI (GPT-5, GPT-4o) ve Anthropic (Claude Opus 4.5, Sonnet) 
modellerini destekleyen, Prompt Caching optimizasyonlu ReAct agent sağlar.

Desteklenen Modeller:
┌────────────────────────────────────────────────────────────┐
│  OPENAI                                                    │
│  - gpt-5 (reasoning: low/medium/high)                      │
│  - gpt-4o, gpt-4-turbo                                     │
│  - o1-preview, o1-mini, o3-mini                            │
├────────────────────────────────────────────────────────────┤
│  ANTHROPIC                                                 │
│  - claude-opus-4-5 (extended thinking: budget_tokens)      │
│  - claude-sonnet-4-5 (extended thinking: budget_tokens)    │
│  - claude-3-5-sonnet, claude-3-opus                        │
└────────────────────────────────────────────────────────────┘

Model Seçimi (Environment Variables):
    REACT_MODEL=gpt-5                → OpenAI GPT-5
    REACT_MODEL=claude-opus-4-5      → Anthropic Claude Opus 4.5
    REACT_MODEL=claude-sonnet-4-5    → Anthropic Claude Sonnet 4.5

Extended Thinking:
    GPT-5:  REACT_REASONING_EFFORT=low|medium|high
    Claude: REACT_THINKING_BUDGET=10000 (token sayısı, 0=kapalı)

API Keys:
    OPENAI_API_KEY     → OpenAI modelleri için
    ANTHROPIC_API_KEY  → Claude modelleri için

Prompt Caching Stratejisi (her iki provider için geçerli):
┌─────────────────────────────────────────────┐
│         CACHED PREFIX (~4000-5000 token)    │ ← %50-90 indirim
│  - System Instructions                       │
│  - Tool Descriptions                         │
│  - Cypher Rules                              │
│  - ReAct Guidelines                          │
│  - Schema Info                               │
└─────────────────────────────────────────────┘
┌─────────────────────────────────────────────┐
│         DYNAMIC SUFFIX (~500-2000 token)    │ ← Normal fiyat
│  - Conversation History                      │
│  - User Question                             │
└─────────────────────────────────────────────┘
"""

import asyncio
import json
import logging
import os
import re
import time
from typing import AsyncGenerator, Dict, Any, Optional, List, TYPE_CHECKING
from datetime import datetime
from dataclasses import dataclass, field

from dotenv import load_dotenv
load_dotenv()

# ============ DEBUG: Environment Variables Check ============
print("\n" + "="*60)
print("🔑 ENVIRONMENT VARIABLES CHECK (react_agent.py)")
print("="*60)
_debug_keys = ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "REACT_MODEL", "REACT_TOOL_MODE"]
for _key in _debug_keys:
    _val = os.environ.get(_key)
    if _val:
        # Mask API keys for security (show first 10 and last 4 chars)
        if "API_KEY" in _key and len(_val) > 20:
            _masked = _val[:10] + "..." + _val[-4:]
        elif "API_KEY" in _key:
            _masked = _val[:4] + "..." if len(_val) > 4 else "***"
        else:
            _masked = _val  # Non-sensitive values shown as-is
        print(f"  ✅ {_key}: {_masked}")
    else:
        print(f"  ❌ {_key}: NOT SET")
print("="*60 + "\n")
# ============================================================

# Context for logging (Grafana/Loki)
from src.shared.context import set_request_context, clear_request_context

# Global Schema Cache import
from src.shared.schema_cache import get_cached_schema, get_schema_cache, get_raw_schema

# Langfuse LLM Observability + Prompt Management + Sessions
from src.shared.langfuse_client import (
    get_langfuse,
    get_langfuse_callback_handler,
    trace_llm_call,
    log_llm_usage,
    flush_langfuse,
    get_prompt,
    create_prompt,
    langfuse_session,  # Session grouping for all traces
)

# Query-level Semantic Cache
from src.shared.query_cache import get_query_cache, get_cache_metrics

# Guardrails - LLM output validation
from src.shared.guardrails import (
    validate_cypher_query,
    validate_output,
    validate_user_input,
    check_hallucination,
    GUARDRAILS_ENABLED,
    GUARDRAILS_HALLUCINATION_CHECK,
)

# Feedback & Few-shot Learning
from src.shared.feedback import (
    get_few_shot_examples,
    get_corrections_for_fewshot,
    format_few_shot_prompt,
    evaluate_cypher_queries,
    evaluate_from_blackboard,
    get_blackboard_dir,
    FEEDBACK_ENABLED,
    FEEDBACK_FEW_SHOT_ENABLED,
    LLM_JUDGE_ENABLED,
)

# Graph DSL - Ontology-driven query generation
# LLM doğrudan Cypher yazmak yerine DSL üretir, DSL validate edilip Cypher'a derlenir
try:
    from src.ontology_agent.graph_dsl import GraphDSL, QueryIntent
    from src.ontology_agent.dsl_compiler import DSLCompiler, compile_dsl
    from src.ontology_agent.dsl_validator import DSLValidator, SchemaInfo, validate_dsl
    from src.ontology_agent.llm_cypher_generator import generate_cypher as llm_generate_cypher, LLMCompilationResult
    GRAPH_DSL_AVAILABLE = True
    LLM_CYPHER_AVAILABLE = True
    logging.info("✅ Graph DSL modules imported")
    logging.info("✅ LLM Cypher generator imported")
except ImportError as e:
    logging.warning(f"⚠️ Graph DSL modules not available: {e}")
    GRAPH_DSL_AVAILABLE = False
    LLM_CYPHER_AVAILABLE = False
    GraphDSL = None
    QueryIntent = None
    DSLCompiler = None
    compile_dsl = None
    DSLValidator = None
    SchemaInfo = None
    validate_dsl = None
    llm_generate_cypher = None
    LLMCompilationResult = None

# DSL Compiler Mode: "llm" (GPT-5-mini) veya "rule" (DSLCompiler)
# Default: llm - LLM-based generation
DSL_COMPILER_MODE = os.getenv("DSL_COMPILER_MODE", "llm")

# Tool Mode: "dsl" veya "cypher"
# dsl (default): Model DSL üretir, DSL Cypher'a derlenir (önerilen)
# cypher: Model doğrudan Cypher yazar (ileri düzey kullanıcılar için)
REACT_TOOL_MODE = os.getenv("REACT_TOOL_MODE", "dsl")

# Logging ayarları
logger = logging.getLogger(__name__)

# Verbose logları sustur
logging.getLogger("mcp").setLevel(logging.WARNING)
logging.getLogger("mcp.client").setLevel(logging.WARNING)
logging.getLogger("langchain_mcp_adapters").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def _log(msg: str, level: str = "info"):
    """Minimal log helper"""
    if level == "debug":
        logging.debug(msg)
    elif level == "warning":
        logging.warning(msg)
    elif level == "error":
        logging.error(msg)
    else:
        logging.info(msg)


# ============================================================================
# TOKEN TRACKING
# ============================================================================

@dataclass
class StepStats:
    """Tek bir aşamanın istatistikleri"""
    step_name: str
    step_type: str  # "llm_call", "tool_call", "tool_result"
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0  # cache_read - cache'den okunan tokenlar
    cache_creation_tokens: int = 0  # cache_creation - yeni cache'lenen tokenlar
    tool_name: Optional[str] = None
    tool_params: Optional[Dict[str, Any]] = None
    tool_result_preview: Optional[str] = None
    duration_ms: float = 0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class TokenTracker:
    """Token kullanımını takip eder"""
    steps: List[StepStats] = field(default_factory=list)
    
    # Kümülatif sayaçlar
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cached_tokens: int = 0  # cache_read - cache'den okunan tokenlar
    total_cache_creation_tokens: int = 0  # cache_creation - yeni cache'lenen tokenlar
    total_llm_calls: int = 0
    total_tool_calls: int = 0
    
    # Model adı (maliyet hesabı için) - add_llm_step ile set edilir
    model_name: str = "unknown"
    
    # Langfuse parent span referansı (tool span'ları bağlamak için)
    _langfuse_parent_span: Any = field(default=None, repr=False)
    _langfuse_parent_span_id: Optional[str] = field(default=None, repr=False)
    # Tool spans - tool_call_id ile key'lenir (aynı tool birden fazla kez çağrılabilir)
    _tool_spans: Dict[str, Any] = field(default_factory=dict, repr=False)
    
    def set_langfuse_parent(self, span: Any):
        """Langfuse parent span'ı set et - tool span'ları bu span'a bağlanır"""
        self._langfuse_parent_span = span
        self._langfuse_parent_span_id = getattr(span, 'id', None)
    
    def add_llm_step(self, step_name: str, input_tokens: int, output_tokens: int, 
                     cached_tokens: int = 0, cache_creation_tokens: int = 0,
                     reasoning_tokens: int = 0, duration_ms: float = 0, 
                     session_id: str = "", model: str = "unknown", 
                     llm_input: Any = None, llm_output: Any = None):
        """LLM çağrısı istatistiği ekle
        
        Args:
            step_name: Step adı
            input_tokens: Input token sayısı
            output_tokens: Output token sayısı
            cached_tokens: Cache'den okunan token sayısı (cache_read)
            cache_creation_tokens: Yeni cache'lenen token sayısı (cache_creation)
            reasoning_tokens: GPT-5 reasoning için harcanan token sayısı
            duration_ms: Süre (ms)
            session_id: Session ID
            model: Model adı (claude-opus-4-5, gpt-5, vb.)
            llm_input: LLM'e gönderilen mesajlar (Langfuse'da görünür)
            llm_output: LLM'den gelen cevap (Langfuse'da görünür)
        """
        # Model adını sakla (maliyet hesabı için)
        self.model_name = model
        
        step = StepStats(
            step_name=step_name,
            step_type="llm_call",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            cache_creation_tokens=cache_creation_tokens,
            duration_ms=duration_ms
        )
        self.steps.append(step)
        
        # Kümülatif güncelle
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_cached_tokens += cached_tokens
        self.total_cache_creation_tokens += cache_creation_tokens
        self.total_llm_calls += 1
        
        # Cache durumu analizi
        cache_pct = round(cached_tokens / max(input_tokens, 1) * 100, 1)
        if cached_tokens > 0:
            cache_status = f"🟢 CACHE HIT {cache_pct}%"
        elif cache_creation_tokens > 0:
            cache_status = f"📝 CACHE WRITE"
        else:
            cache_status = "🔴 CACHE MISS"
        
        # Log token kullanımı (reasoning tokens dahil)
        session_info = f"[Session: {session_id[:8]}]" if session_id else ""
        _log(f"📊 [TOKEN] {session_info} {step_name}")
        _log(f"   ├─ Input: {input_tokens:,} tokens")
        _log(f"   ├─ Output: {output_tokens:,} tokens")
        if reasoning_tokens > 0:
            _log(f"   ├─ Reasoning: {reasoning_tokens:,} tokens (düşünce süreci)")
        _log(f"   ├─ Cache Read: {cached_tokens:,} tokens ({cache_status})")
        if cache_creation_tokens > 0:
            _log(f"   ├─ Cache Creation: {cache_creation_tokens:,} tokens (yeni cache)")
        _log(f"   └─ Kümülatif: in={self.total_input_tokens:,} out={self.total_output_tokens:,} cache_read={self.total_cached_tokens:,} cache_create={self.total_cache_creation_tokens:,}")
        
        # 📊 Langfuse'a gönder - PARENT SPAN altında child generation olarak
        try:
            if self._langfuse_parent_span:
                # Parent span üzerinden child generation oluştur (ayrı trace değil!)
                generation = self._langfuse_parent_span.start_generation(
                    name=step_name,
                    model=model,
                    input=llm_input,  # 🔑 Dashboard'da görünür
                    output=llm_output,  # 🔑 Dashboard'da görünür
                    metadata={
                        "session_id": session_id,
                        "input_tokens": input_tokens,  # 📊 Token sayıları
                        "output_tokens": output_tokens,
                        "cached_tokens": cached_tokens,
                        "reasoning_tokens": reasoning_tokens,
                        "cache_hit_rate": cache_pct,
                        "cumulative_input": self.total_input_tokens,
                        "cumulative_output": self.total_output_tokens,
                    },
                )
                # Langfuse model'e göre farklı token alan isimleri bekliyor:
                # - Claude: cache_read_input_tokens, cache_creation_input_tokens
                # - OpenAI: input_cached_tokens
                # uncached_input = toplam input - cache_read - cache_creation
                uncached_input = max(0, input_tokens - cached_tokens - cache_creation_tokens)
                
                # Model tipine göre doğru alan isimlerini belirle
                model_lower = model.lower()
                is_anthropic = "claude" in model_lower
                
                if is_anthropic:
                    # Anthropic Claude - Langfuse Settings > Models > claude-opus-4-5 Pricing'e göre:
                    # - input: uncached input tokens (tam fiyat)
                    # - cache_read_input_tokens: cache'den okunan tokens (10x ucuz)
                    # - cache_creation_input_tokens: yeni cache'lenen tokens (%25 pahalı)
                    # - output: output tokens
                    generation.update(
                        usage_details={
                            "input": uncached_input,  # Cache'lenmemiş input (tam fiyat)
                            "cache_read_input_tokens": cached_tokens,  # ✅ Cache hit (10x ucuz)
                            "cache_creation_input_tokens": cache_creation_tokens,  # ✅ Cache write (%25 pahalı)
                            "output": output_tokens,
                            "total": input_tokens + output_tokens,
                        },
                    )
                else:
                    # OpenAI GPT-5 - Langfuse Settings > Models > gpt-5 Pricing'e göre:
                    # - input: uncached input tokens
                    # - input_cached_tokens: cached input tokens (10x ucuz)
                    # - output: output tokens
                    # - output_reasoning_tokens: reasoning tokens (GPT-5 için)
                    generation.update(
                        usage_details={
                            "input": uncached_input,  # Sadece cache'lenmemiş input
                            "input_cached_tokens": cached_tokens,  # ✅ OpenAI için doğru isim
                            "output": output_tokens,
                            "output_reasoning_tokens": reasoning_tokens,  # ✅ GPT-5 reasoning tokens
                            "total": input_tokens + output_tokens,
                        },
                    )
                generation.end()
                _log(f"📊 Langfuse generation created: {step_name} (child of parent)")
            else:
                # Fallback: parent yoksa standalone generation (eski davranış)
                log_llm_usage(
                    session_id=session_id,
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cached_tokens=cached_tokens,
                    reasoning_tokens=reasoning_tokens,
                    cost_usd=self._estimate_cost(model),
                    latency_ms=duration_ms,
                    step_name=step_name,
                    metadata={
                        "cache_hit_rate": cache_pct,
                        "cumulative_input": self.total_input_tokens,
                        "cumulative_output": self.total_output_tokens,
                        "reasoning_tokens": reasoning_tokens,
                    },
                    llm_input=llm_input,
                    llm_output=llm_output,
                )
        except Exception as e:
            _log(f"⚠️ Langfuse logging failed: {e}", "warning")
    
    def add_tool_call(self, tool_name: str, params: Dict[str, Any], tool_call_id: str = ""):
        """Tool çağrısı ekle
        
        Args:
            tool_name: Tool adı
            params: Tool parametreleri
            tool_call_id: Unique tool call ID (parallel calls için gerekli)
        """
        # Tam parametreleri sakla (Langfuse için)
        step = StepStats(
            step_name=f"tool_{tool_name}",
            step_type="tool_call",
            tool_name=tool_name,
            tool_params=params  # Tam parametreler
        )
        self.steps.append(step)
        self.total_tool_calls += 1
        
        # Log için kısaltılmış preview
        _log(f"🔧 [TOOL CALL] {tool_name}")
        for k, v in params.items():
            v_str = str(v)
            log_val = v_str[:100] + "..." if len(v_str) > 100 else v_str
            _log(f"   ├─ {k}: {log_val}")
        
        # Input boyutunu hesapla (tiktoken ile gerçek token sayısı)
        import json
        try:
            import tiktoken
            encoding = tiktoken.encoding_for_model("gpt-4")  # GPT-4/5 için aynı encoding
        except Exception:
            encoding = None
        
        input_str = json.dumps(params, ensure_ascii=False) if params else ""
        input_chars = len(input_str)
        
        # Gerçek token sayısı (tiktoken) veya yaklaşık (fallback)
        if encoding:
            input_tokens = len(encoding.encode(input_str))
        else:
            input_tokens = input_chars // 4  # Fallback: yaklaşık hesap
        
        # Langfuse span başlat - parent span üzerinden child span oluştur
        try:
            if self._langfuse_parent_span:
                # Parent span'ın start_span() metodu ile child span oluştur
                span = self._langfuse_parent_span.start_span(
                    name=f"tool:{tool_name}",
                    input=params,  # TAM parametreler
                    metadata={
                        "tool_name": tool_name,
                        "tool_call_id": tool_call_id,
                        "step_type": "tool_call",
                        "input_chars": input_chars,  # 📊 Input karakter sayısı
                        "input_tokens": input_tokens,  # 📊 Gerçek input token (tiktoken)
                    }
                )
                # Span'ı tool_call_id ile sakla (aynı tool birden fazla kez çağrılabilir)
                span_key = tool_call_id if tool_call_id else tool_name
                self._tool_spans[span_key] = span
                _log(f"📊 Langfuse tool span started: {tool_name} (key={span_key[:8] if span_key else 'none'})")
        except Exception as e:
            _log(f"⚠️ Langfuse tool span failed: {e}", "warning")
    
    def add_tool_result(self, tool_name: str, result: str, success: bool = True, tool_call_id: str = ""):
        """Tool sonucu ekle
        
        Args:
            tool_name: Tool adı
            result: Tool sonucu
            success: Başarılı mı?
            tool_call_id: Unique tool call ID (span'ı bulmak için)
        """
        step = StepStats(
            step_name=f"tool_{tool_name}_result",
            step_type="tool_result",
            tool_name=tool_name,
            tool_result_preview=result  # TAM sonuç (limit yok)
        )
        self.steps.append(step)
        
        # Log - 3 durum: success (✅), empty (⚪), failed (❌)
        if "⚪" in result:
            status = "⚪"
        elif success:
            status = "✅"
        else:
            status = "❌"
        _log(f"📥 [TOOL RESULT] {status} {tool_name}")
        
        # Langfuse span'ı bitir - TAM sonuçla
        try:
            # Span'ı tool_call_id ile bul (yoksa tool_name ile dene - backward compat)
            span_key = tool_call_id if tool_call_id else tool_name
            span = self._tool_spans.get(span_key)
            
            if span:
                try:
                    # Output boyutunu hesapla (tiktoken ile gerçek token sayısı)
                    try:
                        import tiktoken
                        encoding = tiktoken.encoding_for_model("gpt-4")
                    except Exception:
                        encoding = None
                    
                    output_chars = len(result)
                    if encoding:
                        output_tokens = len(encoding.encode(result))
                    else:
                        output_tokens = output_chars // 4  # Fallback
                    
                    # Output olarak sonucu ekle
                    span.update(
                        output=result,  # TAM sonuç
                        metadata={
                            "success": success,
                            "status": status,
                            "output_chars": output_chars,  # 📊 Output karakter sayısı
                            "output_tokens": output_tokens,  # 📊 Gerçek output token (LLM'e gidecek)
                        }
                    )
                except Exception as update_err:
                    _log(f"⚠️ Langfuse span update failed: {update_err}", "warning")
                
                # Span'ı kapat
                span.end()
                del self._tool_spans[span_key]
                _log(f"📊 Langfuse tool span ended: {tool_name} ({status})")
        except Exception as e:
            _log(f"⚠️ Langfuse tool span end failed: {e}", "warning")
        
        # Sonucu satır satır göster (max 5 satır - sadece log için)
        lines = result.split('\n')[:5]
        for line in lines:
            if line.strip():
                _log(f"   │ {line[:120]}")
        if len(result.split('\n')) > 5:
            _log(f"   │ ... ({len(result.split(chr(10)))} satır)")
    
    def get_summary(self) -> Dict[str, Any]:
        """İstatistik özeti döndür"""
        return {
            "model": self.model_name,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cached_tokens": self.total_cached_tokens,  # cache_read
            "total_cache_creation_tokens": self.total_cache_creation_tokens,  # cache_creation
            "total_tokens": self.total_input_tokens + self.total_output_tokens,
            "cache_hit_rate": round(self.total_cached_tokens / max(self.total_input_tokens, 1) * 100, 1),
            "total_llm_calls": self.total_llm_calls,
            "total_tool_calls": self.total_tool_calls,
            "steps": len(self.steps),
            "estimated_cost_usd": self._estimate_cost(self.model_name)
        }
    
    def _estimate_cost(self, model: str = "unknown") -> float:
        """Tahmini maliyet hesapla
        
        OpenAI Fiyatları: https://platform.openai.com/docs/pricing
        Anthropic Fiyatları: https://www.anthropic.com/pricing
        
        GPT-5 Standard (per 1M tokens):
        - Input: $1.25
        - Cached Input (read): $0.125 (10x cheaper!)
        - Output: $10.00
        
        GPT-4o Standard (per 1M tokens):
        - Input: $2.50
        - Cached Input (read): $1.25
        - Output: $10.00
        
        Claude Opus 4.5 (per 1M tokens):
        - Input: $15.00
        - Cache Read: $1.50 (10x cheaper!)
        - Cache Creation: $18.75 (25% more expensive!)
        - Output: $75.00
        
        Claude Sonnet 4.5 (per 1M tokens):
        - Input: $3.00
        - Cache Read: $0.30
        - Cache Creation: $3.75
        - Output: $15.00
        
        Claude 3.5 Sonnet (per 1M tokens):
        - Input: $3.00
        - Cache Read: $0.30
        - Cache Creation: $3.75
        - Output: $15.00
        """
        model_lower = model.lower()
        
        # Claude Opus 4.5
        if "claude-opus-4" in model_lower or "claude-opus-4-5" in model_lower:
            input_price = 15.00
            cache_read_price = 1.50  # 10x ucuz
            cache_creation_price = 18.75  # %25 pahalı
            output_price = 75.00
        # Claude Sonnet 4.5 / 3.5
        elif "claude-sonnet" in model_lower or "claude-3-5-sonnet" in model_lower or "claude-3.5-sonnet" in model_lower:
            input_price = 3.00
            cache_read_price = 0.30
            cache_creation_price = 3.75
            output_price = 15.00
        # Claude 3 Opus (eski)
        elif "claude-3-opus" in model_lower:
            input_price = 15.00
            cache_read_price = 1.50
            cache_creation_price = 18.75
            output_price = 75.00
        # Claude (diğer)
        elif "claude" in model_lower:
            input_price = 3.00
            cache_read_price = 0.30
            cache_creation_price = 3.75
            output_price = 15.00
        # GPT-5
        elif "gpt-5" in model_lower:
            input_price = 1.25
            cache_read_price = 0.125
            cache_creation_price = 1.25  # GPT-5 için cache creation ücretsiz (normal input fiyatı)
            output_price = 10.00
        # OpenAI diğer (gpt-4o, o1, o3, vb.)
        else:
            input_price = 2.50
            cache_read_price = 1.25
            cache_creation_price = 2.50  # Normal input fiyatı
            output_price = 10.00
        
        # uncached_input = toplam - cache_read - cache_creation
        uncached_input = self.total_input_tokens - self.total_cached_tokens - self.total_cache_creation_tokens
        uncached_input = max(0, uncached_input)  # Negatif olmaması için
        
        input_cost = uncached_input * input_price / 1_000_000
        cache_read_cost = self.total_cached_tokens * cache_read_price / 1_000_000
        cache_creation_cost = self.total_cache_creation_tokens * cache_creation_price / 1_000_000
        output_cost = self.total_output_tokens * output_price / 1_000_000
        
        return round(input_cost + cache_read_cost + cache_creation_cost + output_cost, 6)
    
    def print_summary(self, session_id: str = ""):
        """Özeti logla"""
        summary = self.get_summary()
        _log("\n" + "=" * 70)
        _log("📈 [REACT AGENT İSTATİSTİKLERİ]")
        if session_id:
            _log(f"   Session ID: {session_id[:8]}")
        _log("=" * 70)
        _log(f"   LLM Çağrıları: {summary['total_llm_calls']}")
        _log(f"   Tool Çağrıları: {summary['total_tool_calls']}")
        _log(f"   Toplam Adım: {summary['steps']}")
        _log("-" * 70)
        _log(f"   Input Token: {summary['total_input_tokens']:,}")
        _log(f"   Output Token: {summary['total_output_tokens']:,}")
        _log(f"   Cache Read Token: {summary['total_cached_tokens']:,} (10x ucuz)")
        _log(f"   Cache Creation Token: {summary['total_cache_creation_tokens']:,} (%25 pahalı)")
        
        # Cache durumu açıklaması (Organization bazlı - session'dan bağımsız)
        cache_rate = summary['cache_hit_rate']
        if cache_rate >= 70:
            cache_emoji = "🟢"
            cache_note = "Mükemmel! Prompt Caching çalışıyor."
        elif cache_rate >= 30:
            cache_emoji = "🟡"
            cache_note = "Kısmi cache hit. Prompt içeriği değişmiş olabilir."
        else:
            cache_emoji = "🔴"
            cache_note = "Cache miss! Prompt içeriği veya sırası değişmiş olabilir."
        
        _log(f"   Cache Hit Rate: {cache_rate}% {cache_emoji}")
        _log(f"   └─ Not: {cache_note}")
        _log("-" * 70)
        _log(f"   Toplam Token: {summary['total_tokens']:,}")
        _log(f"   Tahmini Maliyet: ${summary['estimated_cost_usd']:.6f}")
        _log("=" * 70)
        
        # Model'e göre Prompt Caching bilgisi
        model_name = summary.get('model', 'unknown').lower()
        if "claude" in model_name:
            _log("ℹ️  Anthropic Claude Prompt Caching Bilgisi:")
            _log("   • Cache ORGANIZATION bazlı - aynı org içinde paylaşılır")
            _log("   • Session ID'den BAĞIMSIZ - farklı session'lar cache'i paylaşır")
            _log("   • Exact match gerekli: System prompt + Tools + prefix %100 aynı olmalı")
            _log("   • Cache TTL: 5 dakika (ephemeral), 1 saat (extended + ek maliyet)")
            _log("   • Minimum cache: 1024 token")
            _log(f"   • Model: {summary.get('model', 'claude')}")
        else:
            _log("ℹ️  OpenAI Prompt Caching Bilgisi:")
            _log("   • Cache ORGANIZATION bazlı - aynı org içinde paylaşılır")
            _log("   • Session ID'den BAĞIMSIZ - farklı session'lar cache'i paylaşır")
            _log("   • Aynı prefix (system prompt + schema) = cache hit")
            _log("   • Cache TTL: ~5-10 dakika")
            _log("   • Minimum prefix: 1024 token")
            _log(f"   • Model: {summary.get('model', 'unknown')}")
        _log("")


# ============================================================================
# LANGCHAIN IMPORTS
# ============================================================================

if TYPE_CHECKING:
    from langchain.agents import create_agent
    from langchain.agents.middleware import ModelCallLimitMiddleware
    from langchain.chat_models import init_chat_model
    from langchain_anthropic import ChatAnthropic, convert_to_anthropic_tool

try:
    from langchain.agents import create_agent  # type: ignore
    from langchain.agents.middleware import ModelCallLimitMiddleware  # type: ignore
    from langchain.chat_models import init_chat_model  # type: ignore
    from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
    from langchain_core.tools import tool
    from langchain_community.callbacks import get_openai_callback
    
    LANGCHAIN_AVAILABLE = True
    logging.info("✅ LangChain Agent imports successful")
except ImportError as e:
    logging.warning(f"⚠️ LangChain imports failed: {e}")
    LANGCHAIN_AVAILABLE = False
    create_agent = None
    init_chat_model = None
    ModelCallLimitMiddleware = None
    tool = None
    HumanMessage = None
    AIMessage = None
    SystemMessage = None

# Anthropic Claude import
try:
    from langchain_anthropic import ChatAnthropic, convert_to_anthropic_tool  # type: ignore
    ANTHROPIC_AVAILABLE = True
    logging.info("✅ LangChain Anthropic (Claude) imported")
except ImportError as e:
    logging.warning(f"⚠️ LangChain Anthropic not available: {e}")
    ANTHROPIC_AVAILABLE = False
    ChatAnthropic = None  # type: ignore
    convert_to_anthropic_tool = None  # type: ignore

# Anthropic Prompt Caching Middleware - Cache için ZORUNLU!
# https://docs.langchain.com/oss/python/integrations/middleware/anthropic
try:
    from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware  # type: ignore
    ANTHROPIC_CACHING_MIDDLEWARE_AVAILABLE = True
    logging.info("✅ AnthropicPromptCachingMiddleware imported")
except ImportError as e:
    logging.warning(f"⚠️ AnthropicPromptCachingMiddleware not available: {e}")
    ANTHROPIC_CACHING_MIDDLEWARE_AVAILABLE = False
    AnthropicPromptCachingMiddleware = None  # type: ignore

# MCP Adapters import
if TYPE_CHECKING:
    from langchain_mcp_adapters.client import MultiServerMCPClient

try:
    from langchain_mcp_adapters.client import MultiServerMCPClient  # type: ignore
    MCP_AVAILABLE = True
    logging.info("✅ LangChain MCP Adapters imported")
except ImportError as e:
    logging.warning(f"⚠️ MCP Adapters not available: {e}")
    MCP_AVAILABLE = False
    MultiServerMCPClient = None

# Redis LLM Semantic Cache import
if TYPE_CHECKING:
    from src.shared.redis_cache import setup_semantic_cache, is_cache_available, get_cache_stats as get_redis_cache_stats

try:
    from src.shared.redis_cache import (
        setup_semantic_cache,
        is_cache_available as is_redis_cache_available,
        get_cache_stats as get_redis_cache_stats,
        REDIS_CACHE_ENABLED,
    )
    REDIS_CACHE_IMPORTED = True
except ImportError as e:
    logging.warning(f"⚠️ Redis cache module not available: {e}")
    REDIS_CACHE_IMPORTED = False
    setup_semantic_cache = None  # type: ignore
    is_redis_cache_available = None  # type: ignore
    get_redis_cache_stats = None  # type: ignore
    REDIS_CACHE_ENABLED = False


# ============================================================================
# MCP CONFIGURATION
# ============================================================================

MCP_HTTP_HOST = os.environ.get("MCP_HTTP_HOST", "127.0.0.1")
MCP_HTTP_PORT = int(os.environ.get("MCP_HTTP_PORT", "8002"))

# MCP sunucusunun (Docker konteyneri) Neo4j'ye bağlanmak için kullanacağı URL.
# MCP konteynerin içinde "localhost" konteynerin kendisidir; host'taki Neo4j'ye
# erişmek için host.docker.internal gerekir. Backend host'ta çalışırken NEO4J_URI
# localhost olabilir, ama MCP tool'una geçilecek db_url MCP_NEO4J_URI olmalı.
# Üretimde (backend+MCP aynı ağda) MCP_NEO4J_URI tanımsızsa NEO4J_URI'ye düşer.
MCP_DB_URL = os.environ.get("MCP_NEO4J_URI") or os.environ.get("NEO4J_URI")

# Stdio MCP servers toggle
MCP_STDIO_ENABLED = os.environ.get("MCP_STDIO_ENABLED", "true").lower() == "true"

# Pagination - LLM'e gösterilecek kayıt sayısı (prompt'larda da kullanılır)
DEFAULT_RECORDS_PER_PAGE = int(os.environ.get("DEFAULT_RECORDS_PER_PAGE", "5"))


def _check_uv_installed() -> bool:
    """
    uv/uvx kurulu olup olmadığını kontrol et.
    
    uvx = uv tool run - Python paketlerini indirip çalıştırır (npx benzeri)
    
    Returns:
        True: uvx kullanılabilir
        False: uvx kurulu değil
    """
    import shutil
    
    uvx_path = shutil.which("uvx")
    if uvx_path:
        return True
    
    # uvx yoksa uv'yi kontrol et (uvx = uv tool run)
    uv_path = shutil.which("uv")
    if uv_path:
        return True
    
    return False


# Modül yüklenirken uv kontrolü yap
_UV_AVAILABLE = _check_uv_installed()

if not _UV_AVAILABLE and MCP_STDIO_ENABLED:
    logging.warning("=" * 70)
    logging.warning("⚠️  UV/UVX KURULU DEĞİL!")
    logging.warning("=" * 70)
    logging.warning("   Stdio MCP sunucuları (time, sequential-thinking) kullanılamayacak.")
    logging.warning("   ")
    logging.warning("   Kurmak için:")
    logging.warning("   curl -LsSf https://astral.sh/uv/install.sh | sh")
    logging.warning("   ")
    logging.warning("   veya:")
    logging.warning("   pip install uv")
    logging.warning("   ")
    logging.warning("   Stdio'yu devre dışı bırakmak için: MCP_STDIO_ENABLED=false")
    logging.warning("=" * 70)


def get_mcp_server_config() -> Dict[str, Any]:
    """
    MCP server konfigürasyonu - Sadece Stdio
    
    HTTP Transport: Devre dışı (Neo4j tool'ları custom olarak ekleniyor)
    Stdio Transport: Hafif/Stateless tool'lar (Time, Sequential Thinking)
    
    Stdio sunucuları için uv/uvx gereklidir.
    Devre dışı bırakmak için: MCP_STDIO_ENABLED=false
    """
    config: Dict[str, Any] = {}
    
    # ========== HTTP SERVERS - DEVRE DIŞI ==========
    # Neo4j tool'ları custom olarak ekleniyor (execute_cypher_query, execute_graph_dsl)

    
    # ========== STDIO SERVERS (Hafif, Stateless) ==========
    # uvx (Python) gerektirir - MCP_STDIO_ENABLED=false ile devre dışı bırakılabilir
    # uvx = uv tool run (pip paketlerini çalıştırır)
    
    # uv kurulu değilse stdio'yu atla
    if not _UV_AVAILABLE:
        logging.warning("⚠️ Stdio MCP sunucuları atlandı (uv/uvx kurulu değil)")
        return config
    
    # Neo4j MCP server (internal kullanım - agent'a sunulmaz, custom tool'lar kullanır)
    config.update({
        "neo4j-database": {
            "url": f"http://{MCP_HTTP_HOST}:{MCP_HTTP_PORT}/mcp/",
            "transport": "streamable_http",
        },
    })
    
    if MCP_STDIO_ENABLED:
        config.update({
            # 🕐 Time Server - Zaman ve timezone işlemleri
            # https://github.com/modelcontextprotocol/servers/tree/main/src/time
            # Tools: get_current_time, convert_time
            "time": {
                "command": "uvx",
                "args": ["mcp-server-time", "--local-timezone=Europe/Istanbul"],
                "transport": "stdio",
            },
            
            # 🧠 Sequential Thinking - Adım adım düşünme ve problem çözme
            # https://github.com/modelcontextprotocol/servers/tree/main/src/sequentialthinking
            # Tools: sequentialthinking
            # "sequential-thinking": {
            #     "command": "npx",
            #     "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"],
            #     "transport": "stdio",
            # }
        })
        logging.info(f"📡 MCP Config: neo4j-database (internal) + time + sequential-thinking")
    else:
        logging.info(f"📡 MCP Config: Empty (stdio disabled)")
    
    return config


# ============================================================================
# USER-FRIENDLY MESSAGES FOR STREAMING
# ============================================================================

def _get_user_friendly_tool_message(tool_name: str, tool_args: Dict[str, Any]) -> Optional[str]:
    """
    Tool çağrıları için kullanıcı dostu mesaj oluştur.
    
    Teknik detaylar yerine, kullanıcının anlayabileceği mesajlar döndürür.
    """
    if tool_name == "execute_graph_dsl":
        step_name = tool_args.get("step_name", "")
        
        # step_name'e göre farklı mesajlar
        if "discover" in step_name.lower():
            return "🔍 İlgili kayıtlar araştırılıyor..."
        elif "find" in step_name.lower():
            return "📊 Veritabanında eşleşmeler aranıyor..."
        elif "embed" in step_name.lower() or "search" in step_name.lower():
            return "📖 Belge içerikleri taranıyor..."
        elif "entity" in step_name.lower() or "record" in step_name.lower():
            return "📋 Kayıt bilgileri kontrol ediliyor..."
        elif "document" in step_name.lower():
            return "📄 Belgeler inceleniyor..."
        elif "attribute" in step_name.lower() or "property" in step_name.lower():
            return "🛡️ Özellik bilgileri aranıyor..."
        elif "company" in step_name.lower() or "organization" in step_name.lower():
            return "🏢 Organizasyon bilgileri kontrol ediliyor..."
        elif "date" in step_name.lower():
            return "📅 Tarih bilgileri alınıyor..."
        else:
            return "🔄 Bilgiler sorgulanıyor..."
    
    elif tool_name == "execute_cypher_query":
        return "🔍 Veritabanında arama yapılıyor..."
    
    elif tool_name == "add_source":
        return "📎 Kaynak belgeler ekleniyor..."
    
    elif tool_name == "read_neo4j_cypher":
        return "📊 Grafik veritabanı sorgulanıyor..."
    
    elif tool_name == "read_neo4j_cypher_with_embedding":
        return "🧠 Semantik arama yapılıyor..."
    
    # ========== STDIO MCP TOOLS ==========
    # Time Server tools
    elif tool_name == "get_current_time":
        timezone = tool_args.get("timezone", "UTC")
        return f"🕐 {timezone} için güncel saat alınıyor..."
    
    elif tool_name == "convert_time":
        return "🕐 Saat dönüşümü yapılıyor..."
    
    # Sequential Thinking tools
    elif tool_name == "sequentialthinking":
        return "🧠 Adım adım düşünme süreci başlatılıyor..."
    
    elif tool_name == "create_thinking_session":
        return "🧠 Düşünme oturumu oluşturuluyor..."
    
    elif tool_name == "add_thought":
        return "💭 Düşünce ekleniyor..."
    
    elif tool_name == "get_thinking_summary":
        return "📝 Düşünce özeti hazırlanıyor..."
    
    else:
        # Bilinmeyen tool - generic mesaj
        return None


# Global sayaç: Her session için kaçıncı başarılı sonuç olduğunu takip et
_session_result_counters: Dict[str, int] = {}


def _get_user_friendly_result_message(tool_content: str, result_status: str, session_id: str = "") -> Optional[str]:
    """
    Tool sonuçları için kullanıcı dostu, süreç odaklı mesaj oluştur.
    
    Teknik detaylar (kayıt sayıları) yerine, doğal akış mesajları döndürür.
    """
    global _session_result_counters
    
    if result_status == "success":
        # Session için sayacı artır
        if session_id not in _session_result_counters:
            _session_result_counters[session_id] = 0
        _session_result_counters[session_id] += 1
        step_num = _session_result_counters[session_id]
        
        # Chunk sonucu (embedding arama) - özel mesaj
        if "chunk" in tool_content.lower() or "text:" in tool_content.lower():
            return "📖 İlgili belge içerikleri bulundu"
        
        # Adım numarasına göre farklı mesajlar
        if step_num == 1:
            return "💡 Bazı ipuçlarına ulaştım, detaylı arıyorum..."
        elif step_num == 2:
            return "🔎 Derinlemesine tarıyorum..."
        elif step_num == 3:
            return "📊 Yeni bilgiler edindim"
        elif step_num == 4:
            return "🧩 Bilgileri birleştiriyorum..."
        elif step_num == 5:
            return "📝 Sonuçları değerlendiriyorum..."
        elif step_num >= 6:
            return "✨ Yanıtınızı hazırlıyorum..."
        
        return None
    
    elif result_status == "empty":
        # Boş sonuç - sessiz kal, agent devam edecek
        return None
    
    elif result_status == "failed":
        # Hata durumu - sessiz kal, agent düzeltmeye çalışacak
        return None
    
    return None


def _reset_session_result_counter(session_id: str):
    """Session başlangıcında sayacı sıfırla"""
    global _session_result_counters
    if session_id in _session_result_counters:
        del _session_result_counters[session_id]


# ============================================================================
# SESSION SOURCE MANAGEMENT
# ============================================================================

_session_sources: Dict[str, Dict[str, set]] = {}


def _get_session_sources(question_id: str) -> Dict[str, Any]:
    if question_id not in _session_sources:
        # documents/pages: geriye-uyum (sigorta/bakım). items: ticaret yapılandırılmış
        # kaynaklar; (filename, page) ile dedup, page_link/thumbnail taşır.
        _session_sources[question_id] = {"documents": set(), "pages": set(), "items": {}}
    return _session_sources[question_id]


def _clear_session_sources(question_id: str):
    if question_id in _session_sources:
        del _session_sources[question_id]


def _generate_file_links_markdown(file_names: set) -> str:
    """fileName'lerden markdown formatında dosya linkleri oluşturur"""
    import urllib.parse
    
    if not file_names:
        return ""
    
    base_url = os.getenv("BASE_URL", "http://localhost:8000")
    markdown_section = "\n\n## 📎 Kaynak Belgeler\n\n"
    
    for file_name in sorted(file_names):
        try:
            encoded_file_name = urllib.parse.quote(file_name, safe="", encoding="utf-8")
            file_url = f"{base_url}/files/{encoded_file_name}"
            
            # Dosya adını kısalt (çok uzunsa)
            display_name = file_name
            if len(display_name) > 60:
                display_name = display_name[:57] + "..."
            
            # Markdown link
            markdown_section += f"- 📄 [{display_name}]({file_url})\n"
        except Exception as e:
            _log(f"❌ fileName markdown hatası: {e}", "error")
            continue
    
    return markdown_section


def _generate_page_links_markdown(page_links: set) -> str:
    """Page link'lerden markdown formatında görsel linkler oluşturur"""
    import urllib.parse
    
    if not page_links:
        return ""
    
    base_url = os.getenv("BASE_URL", "http://localhost:8000")
    markdown_section = "\n\n## 📄 İlgili Sayfa Görselleri\n\n"
    
    for page_link in sorted(page_links):
        try:
            encoded_page_link = urllib.parse.quote(page_link, safe="", encoding="utf-8")
            image_url = f"{base_url}/images/{encoded_page_link}"
            
            page_info = "Sayfa Görseli"
            if "_page_" in page_link:
                try:
                    page_num = page_link.split("_page_")[1].split(".")[0]
                    page_info = f"Sayfa {page_num}"
                except:
                    pass
            
            # Markdown image
            markdown_section += f"![{page_info}]({image_url})\n\n"
        except Exception as e:
            _log(f"❌ page_link markdown hatası: {e}", "error")
            continue

    return markdown_section


# ============================================================================
# TİCARET — Yapılandırılmış kaynak belge çıktısı (LangGraph entegrasyonu)
# ============================================================================
def _parse_ticaret_filename(filename: str) -> Dict[str, Optional[str]]:
    """Ticaret belge adından firma/yıl/gazete no/tip ayıkla.

    Beklenen format: '{firma}-{GG.AA.YYYY}-{gazete_no}-{tip}'
    Örn: 'Aksa-21.04.1980-382-ANONİM ŞİRKET (YÖNETİM - TEMSİL VE DİĞER)'
    → firm=Aksa, year=1980, gazette_no=382, gazette_type='ANONİM ŞİRKET (...)'
    Parse edilemezse alanlar None döner (firm fallback: ilk '-' öncesi).
    """
    import re

    name = (filename or "").strip()
    for ext in (".pdf", ".PDF", ".md", ".png"):
        if name.endswith(ext):
            name = name[: -len(ext)]
            break

    m = re.match(
        r"^(?P<firm>.+?)-(?P<date>\d{1,2}\.\d{1,2}\.\d{4})-(?P<no>\d+)-(?P<type>.+)$",
        name,
    )
    if m:
        return {
            "firm": m.group("firm").strip() or None,
            "year": m.group("date").split(".")[-1],
            "gazette_no": m.group("no"),
            "gazette_type": m.group("type").strip() or None,
        }

    # Kısmi fallback: en azından firma (ilk '-' öncesi) ve varsa 4 haneli yıl
    firm = name.split("-")[0].strip() if "-" in name else (name or None)
    year_match = re.search(r"(19|20)\d{2}", name)
    return {
        "firm": firm,
        "year": year_match.group(0) if year_match else None,
        "gazette_no": None,
        "gazette_type": None,
    }


def _build_ticaret_file_link(page_link: Optional[str]) -> Optional[str]:
    """Chunk.page_link'ten görsel URL.

    page_link Chunk property'sinde zaten TAM URL olarak tutuluyor → doğrudan kullanılır
    (ekstra base-URL birleştirmesi yok). Boşsa None döner.
    """
    if not page_link:
        return None
    return str(page_link).strip() or None


def _build_ticaret_documents(sources: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Ticaret final_response için yapılandırılmış kaynak belge listesi üret.

    LangGraph tarafı DocFilters (firm/year/gazette) + MultimodalDocItem (file_link/
    thumbnail) üretebilsin diye her kaynak için tam alan seti döndürür.
    firm/year/gazette_no/gazette_type belge adından parse edilir; file_link
    Chunk.page_link'ten gelir (add_source ile taşınır).
    """
    items = sources.get("items") or {}
    if items:
        records = list(items.values())
    else:
        # add_source yapılandırılmış veri taşımadıysa en azından belge adlarını döndür
        records = [
            {"filename": fn, "page": None, "page_link": None}
            for fn in sources.get("documents", [])
        ]

    docs: List[Dict[str, Any]] = []
    seen = set()
    for rec in records:
        filename = rec.get("filename")
        if not filename:
            continue
        page = rec.get("page")
        key = (filename, page)
        if key in seen:
            continue
        seen.add(key)
        parsed = _parse_ticaret_filename(filename)
        docs.append(
            {
                "filename": filename,
                "page": page,
                "file_link": _build_ticaret_file_link(rec.get("page_link")),
                "thumbnail": _build_ticaret_file_link(rec.get("thumbnail")),
                "firm": parsed["firm"],
                "year": parsed["year"],
                "gazette_type": parsed["gazette_type"],
                "gazette_no": parsed["gazette_no"],
            }
        )
    return docs


# ============================================================================
# LANGFUSE PROMPT MANAGEMENT
# ============================================================================

# Prompt Names - Langfuse'da tanımlı olmalı
LANGFUSE_PROMPT_NAME = "react-agent-system"
LANGFUSE_PROMPT_TYPE = "text"
LANGFUSE_PROMPT_LABEL = os.environ.get("LANGFUSE_PROMPT_LABEL", "production")

# ============================================================================
# PROMPT MODÜLÜNDEN IMPORT
# ============================================================================
# Prompt'lar artık prompts/ modülünde organize edildi.
# Sigorta ve Bakım domain'leri için ayrı prompt'lar mevcut.
#
# Kullanım:
#   from .prompts import get_domain_prompts, build_full_prompt
#   prompts = get_domain_prompts("sigorta", "cypher")  # veya "bakim"
#
# NOT: Bu prompt'lar FALLBACK olarak kullanılır.
# Öncelik Langfuse Prompt Management'dadır.

from .prompts import (
    get_domain_prompts,
    build_full_prompt,
    # Backward compatibility - aynı isimlerle export edilir
    SHARED_SYSTEM_BASE,
    DSL_TOOL_USAGE,
    CYPHER_TOOL_USAGE,
    DSL_THINKING_GUIDE,
    SHARED_CONTENT,
)

# =============================================================================
# PROMPT CONSTANTS - prompts/ modülünden import edildi
# =============================================================================
# Artık burada tanımlı değil, yukarıdaki import'tan geliyor:
# - SHARED_SYSTEM_BASE
# - DSL_TOOL_USAGE  
# - CYPHER_TOOL_USAGE
# - DSL_THINKING_GUIDE
# - SHARED_CONTENT
#
# Domain bazlı prompt'lar için:
#   prompts = get_domain_prompts("sigorta", "cypher")
#   prompts = get_domain_prompts("bakim", "cypher")  # WAT Motor
# =============================================================================

# Dinamik suffix template - her istekte değişir
DYNAMIC_SUFFIX_TEMPLATE = """
---

## 💬 KONUŞMA GEÇMİŞİ
{conversation_history}

## ❓ KULLANICI SORUSU
{user_question}
"""


# ============================================================================
# TOOL DEFINITIONS
# ============================================================================

def create_react_tools(mcp_tools: List, session_id: str, question_id: str, user_question: str = "", token_tracker: Optional['TokenTracker'] = None):
    """
    ReAct agent için tool'ları oluşturur.
    
    Args:
        mcp_tools: MCP'den alınan tool listesi
        session_id: Oturum ID'si
        question_id: Soru ID'si  
        user_question: Kullanıcının sorduğu orijinal soru
        token_tracker: Token tracking için (Langfuse parent span erişimi)
    
    Returns:
        Tool listesi
    """
    if not LANGCHAIN_AVAILABLE or tool is None:
        return []
    
    # MCP tool'larını isimle eşle
    mcp_tool_map = {t.name: t for t in mcp_tools}
    
    # Findings dizinini hazırla
    findings_base = os.path.join(os.getcwd(), "agent_findings", "react", session_id, question_id)
    os.makedirs(findings_base, exist_ok=True)
    
    # Tool çağrı sıra numarası (aynı anda çağrılan tool'lar aynı sayıyı alır)
    tool_call_counter = {"value": 0}  # Mutable container for closure
    
    # Blackboard dosyası
    blackboard_path = os.path.join(findings_base, "_blackboard.txt")
    
    def _init_blackboard():
        if os.path.exists(blackboard_path):
            _log(f"📋 Blackboard: {blackboard_path}")
            return
        try:
            with open(blackboard_path, "w", encoding="utf-8") as f:
                f.write(f"# 📋 ReAct Agent Blackboard\n")
                f.write(f"# Session: {session_id} | Question: {question_id}\n\n")
                if user_question:
                    f.write(f"## 💬 SORU\n{user_question}\n\n")
                f.write(f"## 📊 SONUÇLAR\n")
            _log(f"📋 Blackboard: {blackboard_path}")
        except Exception as e:
            _log(f"⚠️ Blackboard init error: {e}")
    
    _init_blackboard()
    
    def _append_to_blackboard(step_name: str, record_count: int, success: bool, seq_num: int = 0):
        try:
            status = "✅" if success else "❌"
            with open(blackboard_path, "a", encoding="utf-8") as f:
                f.write(f"{status} [{seq_num:02d}] {step_name}: {record_count} kayıt\n")
        except Exception as e:
            _log(f"⚠️ Blackboard append error: {e}")
    
    # =========================================================================
    # PAGINATION HELPERS
    # =========================================================================
    def _parse_records(result_str: str) -> List[str]:
        """Sonuç string'inden kayıtları parse et - (R:N){...} formatı"""
        import re
        # Her kayıt (R:N){ ile başlıyor
        record_pattern = r'\(R:\d+\)\{[^}]*(?:\{[^}]*\}[^}]*)*\}'
        records = re.findall(record_pattern, result_str, re.DOTALL)
        return records
    
    def _format_paginated_result(records: List[str], total_count: int, start: int, end: int, step_name: str) -> str:
        """Pagination bilgisi ile sonuç formatla"""
        shown_records = records[start:end]
        shown_count = len(shown_records)
        
        result_lines = "\n".join(shown_records)
        
        pagination_info = f"📊 Gösterilen: {start}-{start + shown_count} / Toplam: {total_count} kayıt"
        
        if end < total_count:
            more_info = f"\n\n💡 Daha fazla görmek için: read_finding(\"{step_name}\", start_record={end}, end_record={min(end + DEFAULT_RECORDS_PER_PAGE, total_count)})"
        else:
            more_info = ""
        
        return f"{pagination_info}\n\n{result_lines}{more_info}"
    
    # =========================================================================
    # EXECUTE CYPHER QUERY TOOL
    # =========================================================================
    @tool
    async def execute_cypher_query(cypher: str, step_name: str) -> str:
        """Neo4j Cypher sorgusu çalıştır. Detaylar prompt'ta."""
        mcp_read = mcp_tool_map.get("read_neo4j_cypher")
        if not mcp_read:
            return '{"error": "MCP read_neo4j_cypher tool not found"}'
        
        try:
            # 🛡️ Guardrails: Cypher injection validation
            if GUARDRAILS_ENABLED:
                is_safe, sanitized_cypher, violations = validate_cypher_query(cypher)
                if not is_safe:
                    _log(f"⚠️ Cypher blocked: {violations}", "warning")
                    return f'{{"error": "Query rejected for security: {", ".join(violations[:2])}"}}'
                cypher = sanitized_cypher
            
            # Multi-tenant: Add db credentials from environment
            db_params = {
                "query": cypher,
                "db_url": MCP_DB_URL,
                "db_username": os.environ.get("NEO4J_USERNAME"),
                "db_password": os.environ.get("NEO4J_PASSWORD"),
                "db_database": os.environ.get("NEO4J_DATABASE", "neo4j"),
            }
            result = await mcp_read.ainvoke(db_params)
            result_str = str(result) if result else ""
            
            # Hata kontrolü
            is_error = "HATA:" in result_str or "ERROR:" in result_str or "❌" in result_str
            
            # Kayıtları parse et
            records = _parse_records(result_str)
            record_count = len(records)
            
            # Eğer parse edilemezse eski yönteme fallback
            if record_count == 0 and result_str and not is_error:
                lines = [l.strip() for l in result_str.split('\n') if l.strip() and l.strip().startswith('(')]
                records = lines
                record_count = len(records)
            
            # 3 durum: success (kayıt var), empty (kayıt yok), failed (hata var)
            if is_error:
                status = "failed"
            elif record_count > 0:
                status = "success"
            else:
                status = "empty"
            
            # Sıra numarasını artır ve dosya ismine ekle
            tool_call_counter["value"] += 1
            seq_num = tool_call_counter["value"]
            
            # Dosyaya TÜM sonucu kaydet (pagination için)
            # Format: 01_step_name_status.txt (sıralı görünüm için)
            file_path = os.path.join(findings_base, f"{seq_num:02d}_{step_name}_{status}.txt")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(f"<query>\n{cypher}\n</query>\n\n")
                f.write(f"<result>\n{result_str}\n</result>\n")
            
            _log(f"📁 Cypher: [{seq_num:02d}] {step_name} → {record_count} records ({status})")
            _append_to_blackboard(step_name, record_count, status == "success", seq_num)
            
            # Sonuç döndür
            if status == "success":
                end_idx = min(DEFAULT_RECORDS_PER_PAGE, record_count)
                paginated_result = _format_paginated_result(records, record_count, 0, end_idx, step_name)
                return f"✅ {record_count} kayıt bulundu.\n\n{paginated_result}"
            elif status == "empty":
                return f"⚪ Sonuç bulunamadı (0 kayıt). Farklı bir strateji dene."
            else:
                return f"""❌ Hata oluştu.

{result_str[:1000]}"""
                
        except Exception as e:
            _log(f"❌ Cypher error: {e}", "error")
            return f'{{"error": "{str(e)}"}}'
    
    # =========================================================================
    # EXECUTE CYPHER QUERY WITH EMBEDDING TOOL - Semantic Search
    # =========================================================================
    @tool
    async def execute_cypher_query_with_embedding(query_text: str, cypher: str, step_name: str) -> str:
        """Vector index ile semantik arama. query_text=kavram, cypher=$embedding_vector içermeli. Detaylar prompt'ta."""
        mcp_embedding = mcp_tool_map.get("read_neo4j_cypher_with_embedding")
        if not mcp_embedding:
            return '{"error": "MCP read_neo4j_cypher_with_embedding tool not found"}'
        
        try:
            # Validate: $embedding_vector parametresi zorunlu
            if "$embedding_vector" not in cypher:
                return """❌ HATA: Cypher sorgusu $embedding_vector parametresi içermiyor!

✅ DOĞRU KULLANIM (db.index.vector.queryNodes):
execute_cypher_query_with_embedding(
    query_text="aranan kavram",
    cypher=\"\"\"
    CALL db.index.vector.queryNodes('vector', 50, $embedding_vector)
    YIELD node AS c, score
    WHERE score > 0.75
    RETURN c.text AS text, score, c.page_link AS page_link, c.fileName AS fileName
    ORDER BY score DESC
    \"\"\",
    step_name="semantic_search"
)

🔍 FİLTRELİ ARAMA (ilişki ve node adlarını ŞEMADAN al!):
execute_cypher_query_with_embedding(
    query_text="aranan kavram",
    cypher=\"\"\"
    CALL db.index.vector.queryNodes('vector', 100, $embedding_vector)
    YIELD node AS c, score
    WHERE score > 0.70
    MATCH (c)-[:PART_OF]->(d:Document)<-[:HAS_DOCUMENT]-(e:Entity)
    WHERE e.property CONTAINS 'değer'
    RETURN c.text AS text, score, d.fileName AS fileName
    ORDER BY score DESC
    \"\"\",
    step_name="filtered_search"
)

⚠️ NOT: İlişki adları (PART_OF, HAS_DOCUMENT vb.) ve node label'ları (Entity) ŞEMAYA GÖRE DEĞİŞİR!
Lütfen sorguyu düzelt ve tekrar dene."""
            
            # 🛡️ Guardrails: Cypher injection validation
            if GUARDRAILS_ENABLED:
                is_safe, sanitized_cypher, violations = validate_cypher_query(cypher)
                if not is_safe:
                    _log(f"⚠️ Cypher blocked: {violations}", "warning")
                    return f'{{"error": "Query rejected for security: {", ".join(violations[:2])}"}}'
                cypher = sanitized_cypher
            
            # Multi-tenant: Add db credentials from environment
            result = await mcp_embedding.ainvoke({
                "query_text": query_text,
                "cypher_query": cypher,
                "params": {},
                "db_url": MCP_DB_URL,
                "db_username": os.environ.get("NEO4J_USERNAME"),
                "db_password": os.environ.get("NEO4J_PASSWORD"),
                "db_database": os.environ.get("NEO4J_DATABASE", "neo4j"),
            })
            result_str = str(result) if result else ""
            
            # Hata kontrolü
            is_error = "HATA:" in result_str or "ERROR:" in result_str or "❌" in result_str
            
            # Kayıtları parse et
            records = _parse_records(result_str)
            record_count = len(records)
            
            # Eğer parse edilemezse eski yönteme fallback
            if record_count == 0 and result_str and not is_error:
                lines = [l.strip() for l in result_str.split('\n') if l.strip() and l.strip().startswith('(')]
                records = lines
                record_count = len(records)
            
            # Status belirleme
            if is_error:
                status = "failed"
            elif record_count > 0:
                status = "success"
            else:
                status = "empty"
            
            # Sıra numarasını artır ve dosya ismine ekle
            tool_call_counter["value"] += 1
            seq_num = tool_call_counter["value"]
            
            # Dosyaya TÜM sonucu kaydet
            file_path = os.path.join(findings_base, f"{seq_num:02d}_{step_name}_{status}.txt")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(f"<query_text>\n{query_text}\n</query_text>\n\n")
                f.write(f"<cypher>\n{cypher}\n</cypher>\n\n")
                f.write(f"<result>\n{result_str}\n</result>\n")
            
            _log(f"📁 Semantic: [{seq_num:02d}] {step_name} → {record_count} records ({status})")
            _append_to_blackboard(step_name, record_count, status == "success", seq_num)
            
            # Sonuç döndür
            if status == "success":
                end_idx = min(DEFAULT_RECORDS_PER_PAGE, record_count)
                paginated_result = _format_paginated_result(records, record_count, 0, end_idx, step_name)
                return f"✅ Semantic search: {record_count} sonuç bulundu.\n\n{paginated_result}"
            elif status == "empty":
                return f"⚪ Semantic search sonuç bulunamadı. Farklı query_text dene veya threshold'u düşür (0.75 → 0.6)."
            else:
                return f"""❌ Semantic search hatası.

{result_str[:1000]}"""
                
        except Exception as e:
            _log(f"❌ Semantic search error: {e}", "error")
            return f'{{"error": "{str(e)}"}}'
    
    # =========================================================================
    # EXECUTE GRAPH DSL TOOL - Ontology-Driven Query
    # =========================================================================
    @tool
    async def execute_graph_dsl(dsl_json: str, step_name: str, compiler_mode: str = "") -> str:
        """Graph DSL ile sorgu. dsl_json=JSON, compiler_mode=llm/rule. Detaylar prompt'ta."""
        if not GRAPH_DSL_AVAILABLE:
            return '{"error": "Graph DSL modülü yüklü değil. execute_cypher_query kullanın."}'
        
        mcp_read = mcp_tool_map.get("read_neo4j_cypher")
        if not mcp_read:
            return '{"error": "MCP read_neo4j_cypher tool not found"}'
        
        try:
            # 1. DSL'i parse et
            # Type assertion: GRAPH_DSL_AVAILABLE kontrolü yukarıda yapıldı
            assert GraphDSL is not None, "GraphDSL should be available"
            assert DSLCompiler is not None, "DSLCompiler should be available"
            
            try:
                dsl = GraphDSL.from_json(dsl_json)
            except Exception as parse_error:
                return f"""❌ DSL Parse Hatası: {parse_error}

DSL JSON formatı hatalı. Örnek format:
{{
    "intent": "find_by_property",
    "start_node": "NodeLabel",
    "filters": [{{"node": "NodeLabel", "property": "name", "operator": "contains", "value": "aranan_deger"}}],
    "return_spec": {{"nodes": ["NodeLabel"], "properties": {{"NodeLabel": ["name"]}}}},
    "limit": 10
}}"""
            
            # 2. Basit validation
            basic_errors = dsl.validate_basic()
            if basic_errors:
                return f"""❌ DSL Validation Hatası:
{chr(10).join(f"  • {e}" for e in basic_errors)}

Lütfen DSL'i düzelt ve tekrar dene."""
            
            # 3. Schema validation (global cache'den raw schema ile)
            validation_warnings = []
            try:
                from src.ontology_agent.dsl_validator import create_validator_from_cache
                
                validator = create_validator_from_cache()
                if validator:
                    validation_result = validator.validate(dsl, auto_correct=True)
                    
                    if validation_result.errors:
                        error_msgs = "\n".join(f"  • {e.message}" for e in validation_result.errors)
                        suggestions = [e.suggestion for e in validation_result.errors if e.suggestion]
                        suggestion_text = "\n".join(f"  💡 {s}" for s in suggestions) if suggestions else ""
                        return f"""❌ Schema Validation Hatası:
{error_msgs}
{suggestion_text}

Lütfen schema'ya uygun node/property/relationship kullanın."""
                    
                    if validation_result.warnings:
                        validation_warnings = [w.message for w in validation_result.warnings]
                    
                    # Auto-corrected DSL varsa kullan
                    if validation_result.validated_dsl:
                        dsl = validation_result.validated_dsl
                        _log("📋 DSL auto-corrected by validator")
                    
                    _log(f"📋 DSL validation: ✅ passed ({len(validation_result.errors)} errors, {len(validation_result.warnings)} warnings)")
                else:
                    _log("📋 DSL validation: skipped (no schema in cache)")
            except Exception as val_error:
                _log(f"📋 DSL validation: skipped ({val_error})", "warning")
            
            # 4. DSL'i Cypher'a derle - LLM veya Rule-based
            # Compiler mode: parametre > env variable > default (llm)
            effective_mode = compiler_mode if compiler_mode else DSL_COMPILER_MODE
            
            if effective_mode == "llm" and LLM_CYPHER_AVAILABLE and llm_generate_cypher:
                # === LLM-BASED GENERATION (GPT-5-mini) ===
                _log(f"🤖 Using LLM Cypher generator (gpt-5-mini)")
                
                # Schema bilgisini al (opsiyonel)
                schema_info = None
                try:
                    raw_schema = get_raw_schema()
                    if raw_schema:
                        schema_info = str(raw_schema)[:2000]  # İlk 2000 karakter
                except:
                    pass
                
                # Parent span'ı geçir - Langfuse'da child generation olarak görünsün
                parent_span = token_tracker._langfuse_parent_span if token_tracker else None
                result = await llm_generate_cypher(dsl_json, schema_info, model="gpt-5-mini", parent_span=parent_span)
                
                cypher = result.cypher
                params = result.params
                
                if not cypher:
                    # LLM başarısız oldu, fallback to rule-based
                    _log(f"⚠️ LLM failed, falling back to rule-based compiler")
                    compiler = DSLCompiler()
                    result = compiler.compile(dsl)
                    cypher = result.cypher
                    params = result.params
                else:
                    _log(f"🤖 LLM generated in {result.latency_ms:.0f}ms")
            else:
                # === RULE-BASED COMPILATION (DSLCompiler) ===
                _log(f"📐 Using rule-based DSLCompiler")
                compiler = DSLCompiler()
                result = compiler.compile(dsl)
                cypher = result.cypher
                params = result.params
            
            _log(f"🔷 DSL → Cypher compiled ({effective_mode}):")
            _log(f"   DSL intent: {dsl.intent}")
            _log(f"   Cypher: {cypher[:200]}...")
            if params:
                _log(f"   Params: {params}")
            if validation_warnings:
                _log(f"   ⚠️ Warnings: {validation_warnings}")
            
            # 5. Parametreleri inline'a çevir
            if params:
                for param_name, param_value in params.items():
                    if isinstance(param_value, str):
                        cypher = cypher.replace(f"${param_name}", f"'{param_value}'")
                    elif isinstance(param_value, (int, float)):
                        cypher = cypher.replace(f"${param_name}", str(param_value))
                    elif isinstance(param_value, list):
                        list_str = "[" + ", ".join(f"'{v}'" if isinstance(v, str) else str(v) for v in param_value) + "]"
                        cypher = cypher.replace(f"${param_name}", list_str)
            
            # 6. Embedding mi yoksa normal sorgu mu?
            if result.requires_embedding:
                # === EMBEDDING QUERY ===
                mcp_embedding = mcp_tool_map.get("read_neo4j_cypher_with_embedding")
                if not mcp_embedding:
                    return '{"error": "MCP embedding tool not found"}'
                
                query_text = result.query_text or ""
                _log(f"🔍 DSL Semantic Search: '{query_text}'")
                _log(f"   Cypher (with filters): {cypher[:300]}...")
                
                # MCP embedding tool çağır (multi-tenant)
                result_data = await mcp_embedding.ainvoke({
                    "query_text": query_text,
                    "cypher_query": cypher,
                    "db_url": MCP_DB_URL,
                    "db_username": os.environ.get("NEO4J_USERNAME"),
                    "db_password": os.environ.get("NEO4J_PASSWORD"),
                    "db_database": os.environ.get("NEO4J_DATABASE", "neo4j"),
                })
                result_str = str(result_data) if result_data else ""
                
                # Hata kontrolü
                is_error = "HATA:" in result_str or "ERROR:" in result_str or "❌" in result_str
                
                # Kayıt sayısı
                records = []
                if result_str and not is_error:
                    lines = [l.strip() for l in result_str.split('\n') if l.strip()]
                    records = [l for l in lines if 'score' in l.lower() or l.startswith('(')]
                record_count = len(records)
                
                # Status
                if is_error:
                    status = "failed"
                elif record_count > 0:
                    status = "success"
                else:
                    status = "empty"
                
                # Dosyaya kaydet
                tool_call_counter["value"] += 1
                seq_num = tool_call_counter["value"]
                
                file_path = os.path.join(findings_base, f"{seq_num:02d}_{step_name}_{status}.txt")
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(f"<dsl>\n{dsl_json}\n</dsl>\n\n")
                    f.write(f"<semantic_search>\nQUERY: {query_text}\n</semantic_search>\n\n")
                    f.write(f"<cypher>\n{cypher}\n</cypher>\n\n")
                    f.write(f"<result>\n{result_str}\n</result>\n")
                
                _log(f"📁 DSL Semantic: [{seq_num:02d}] {step_name} → {record_count} records ({status})")
                _append_to_blackboard(step_name, record_count, status == "success", seq_num)
                
                if status == "success":
                    parsed_records = _parse_records(result_str)
                    if len(parsed_records) == 0:
                        parsed_records = records
                    
                    end_idx = min(DEFAULT_RECORDS_PER_PAGE, len(parsed_records))
                    paginated_result = _format_paginated_result(parsed_records, len(parsed_records), 0, end_idx, step_name)
                    
                    return f"""✅ {record_count} içerik bulundu (DSL Semantic Search).

{paginated_result}

📋 Kullanılan DSL filtreleri uygulandı:
- Traversal: {len(dsl.traversal)} adım
- Filters: {len(dsl.filters)} filtre

⚠️ FALSE POSITIVE KONTROLÜ: Dönen chunk.text'lerde "{query_text}" geçiyor mu kontrol et!"""
                elif status == "empty":
                    return f"""❌ Semantic aramada sonuç bulunamadı.

🔍 Aranan: "{query_text}"
📋 DSL filtreleri: {len(dsl.filters)} filtre uygulandı
💡 Öneriler:
- Similarity threshold düşürülebilir (varsayılan: 0.75)
- Filter'lar çok kısıtlayıcı olabilir
- Farklı terimlerle arama yapılabilir"""
                else:
                    return f"❌ Hata: {result_str}"
            
            # === NORMAL CYPHER QUERY ===
            # 🛡️ Guardrails: Cypher injection validation
            if GUARDRAILS_ENABLED:
                is_safe, sanitized_cypher, violations = validate_cypher_query(cypher)
                if not is_safe:
                    _log(f"⚠️ DSL-generated Cypher blocked: {violations}", "warning")
                    return f'{{"error": "Query rejected for security: {", ".join(violations[:2])}"}}'
                cypher = sanitized_cypher
            
            # Multi-tenant: Add db credentials from environment
            result_data = await mcp_read.ainvoke({
                "query": cypher,
                "db_url": MCP_DB_URL,
                "db_username": os.environ.get("NEO4J_USERNAME"),
                "db_password": os.environ.get("NEO4J_PASSWORD"),
                "db_database": os.environ.get("NEO4J_DATABASE", "neo4j"),
            })
            result_str = str(result_data) if result_data else ""
            
            # Hata kontrolü
            is_error = "HATA:" in result_str or "ERROR:" in result_str or "❌" in result_str
            
            # Kayıtları parse et
            records = _parse_records(result_str)
            record_count = len(records)
            
            if record_count == 0 and result_str and not is_error:
                lines = [l.strip() for l in result_str.split('\n') if l.strip() and l.strip().startswith('(')]
                records = lines
                record_count = len(records)
            
            # Status
            if is_error:
                status = "failed"
            elif record_count > 0:
                status = "success"
            else:
                status = "empty"
            
            # Dosyaya kaydet
            tool_call_counter["value"] += 1
            seq_num = tool_call_counter["value"]
            
            file_path = os.path.join(findings_base, f"{seq_num:02d}_{step_name}_{status}.txt")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(f"<dsl>\n{dsl_json}\n</dsl>\n\n")
                f.write(f"<cypher>\n{cypher}\n</cypher>\n\n")
                f.write(f"<result>\n{result_str}\n</result>\n")
            
            _log(f"📁 DSL: [{seq_num:02d}] {step_name} → {record_count} records ({status})")
            _append_to_blackboard(f"DSL:{step_name}", record_count, status == "success", seq_num)
            
            # Sonuç döndür
            warning_text = ""
            if validation_warnings:
                warning_text = f"\n⚠️ Uyarılar: {', '.join(validation_warnings)}"
            
            if status == "success":
                end_idx = min(DEFAULT_RECORDS_PER_PAGE, record_count)
                paginated_result = _format_paginated_result(records, record_count, 0, end_idx, step_name)
                return f"✅ {record_count} kayıt bulundu.{warning_text}\n\n📋 Kullanılan Cypher:\n{cypher[:300]}...\n\n{paginated_result}"
            elif status == "empty":
                return f"""⚪ Sonuç bulunamadı (0 kayıt).{warning_text}

📋 Kullanılan Cypher:
{cypher}

💡 Öneriler:
- Filtre değerlerini kontrol et (büyük/küçük harf, yazım)
- Farklı node'larda ara
- Daha geniş bir arama dene (limit artır, filter gevşet)"""
            else:
                return f"""❌ Hata oluştu.{warning_text}

📋 Kullanılan Cypher:
{cypher}

{result_str[:1000]}"""
                
        except Exception as e:
            _log(f"❌ DSL error: {e}", "error")
            import traceback
            traceback.print_exc()
            return f'{{"error": "{str(e)}"}}'
    
    
    # =========================================================================
    # GET GUIDE TOOL
    # =========================================================================
    # @tool
    # def get_guide(topic: str) -> str:
    #     """
    #     Strateji rehberi al.
        
    #     Topics:
    #     - KESIF: Entity varyasyon bulma
    #     - ICERIK: Chunk/embedding arama
    #     - METADATA: İlişki takibi
    #     - CYPHER_RULES: Sorgu yazım kuralları
    #     - FALSE_POSITIVE: Embedding doğrulama
        
    #     Args:
    #         topic: Rehber konusu
        
    #     Returns:
    #         Rehber içeriği
    #     """
    #     prompts_dir = os.path.join(os.path.dirname(__file__), "prompts")
        
    #     topic_lower = topic.lower().replace("_", "")
    #     topic_map = {
    #         "kesif": "kesif.md",
    #         "keşif": "kesif.md",
    #         "icerik": "icerik.md",
    #         "içerik": "icerik.md",
    #         "metadata": "metadata.md",
    #         "falsepositive": "false_positive.md",
    #         "false_positive": "false_positive.md",
    #         "cypherrules": "cypher_rules.md",
    #         "cypher_rules": "cypher_rules.md",
    #         "cypher": "cypher_rules.md",
    #         "finalcevap": "final_cevap.md",
    #         "final_cevap": "final_cevap.md",
    #         "cevap": "final_cevap.md",
    #     }
        
    #     filename = topic_map.get(topic_lower)
    #     if not filename:
    #         available = ", ".join(["KESIF", "ICERIK", "METADATA", "FALSE_POSITIVE", "CYPHER_RULES"])
    #         return f"❌ Bilinmeyen rehber: {topic}. Mevcut: {available}"
        
    #     guide_path = os.path.join(prompts_dir, filename)
    #     if not os.path.exists(guide_path):
    #         return f"❌ Rehber bulunamadı: {guide_path}"
        
    #     with open(guide_path, "r", encoding="utf-8") as f:
    #         content = f.read()
        
    #     _log(f"📖 Guide loaded: {topic}")
    #     return content
    
    # =========================================================================
    # ADD SOURCE TOOL
    # =========================================================================
    @tool
    def add_source(
        source_type: str,
        value: str,
        page: Optional[int] = None,
        page_link: Optional[str] = None,
    ) -> str:
        """Kaynak ekle. source_type=document/page, value=belge adı.
        Ticaret: yapılandırılmış kaynak için page (Chunk.page) ve page_link
        (Chunk.page_link, sorgu sonucundan) da geç. Detaylar prompt'ta."""
        sources = _get_session_sources(question_id)

        if source_type == "document":
            sources["documents"].add(value)
            # Yapılandırılmış kaynak (LangGraph: file_link/firm/year/gazette) için
            # page + page_link sakla; (belge, sayfa) ile dedup et.
            norm_page = page
            try:
                if page is not None:
                    norm_page = int(page)
            except (ValueError, TypeError):
                norm_page = page
            key = (value, norm_page)
            existing = sources["items"].get(key, {})
            sources["items"][key] = {
                "filename": value,
                "page": norm_page,
                "page_link": page_link or existing.get("page_link"),
            }
            _log(f"📎 Document: {value} (page={norm_page}, link={'✓' if page_link else '✗'})")
            return f"✅ Belge kaynağı eklendi: {value}"
        elif source_type == "page":
            sources["pages"].add(value)
            _log(f"🖼️ Page: {value}")
            return f"✅ Sayfa kaynağı eklendi: {value}"
        else:
            return f"❌ Geçersiz source_type. 'document' veya 'page' olmalı."

    @tool
    def add_sources(documents: List[Dict[str, Any]]) -> str:
        """Birden çok kaynak belgeyi TEK çağrıda ekle (ÖNERİLEN — add_source'u tek
        tek çağırma). Token'dan tasarruf için dayanak belgelerini topla ve cevaptan
        ÖNCE bir kez bununla ekle.

        documents: liste; her öğe {"filename": belge_adı, "page": sayfa, "page_link": sayfa_link}.
        (page/page_link opsiyonel; sorgu sonucundaki belge/sayfa/sayfa_link'ten al.)
        """
        sources = _get_session_sources(question_id)
        added = 0
        for doc in documents or []:
            if not isinstance(doc, dict):
                continue
            filename = doc.get("filename") or doc.get("value") or doc.get("belge")
            if not filename:
                continue
            page = doc.get("page", doc.get("sayfa"))
            page_link = doc.get("page_link") or doc.get("sayfa_link")
            norm_page = page
            try:
                if page is not None:
                    norm_page = int(page)
            except (ValueError, TypeError):
                norm_page = page
            sources["documents"].add(filename)
            key = (filename, norm_page)
            existing = sources["items"].get(key, {})
            sources["items"][key] = {
                "filename": filename,
                "page": norm_page,
                "page_link": page_link or existing.get("page_link"),
            }
            added += 1
        _log(f"📎 add_sources: {added} kaynak TEK çağrıda eklendi")
        return f"✅ {added} belge kaynağı eklendi (tek çağrı)."

    # =========================================================================
    # READ FINDING TOOL - Pagination destekli sonuç okuma
    # =========================================================================
    @tool
    def read_finding(
        step_name: str,
        result_type: str = "success",
        start_record: int = 0,
        end_record: int = 0
    ) -> str:
        """Sorgu sonuçlarının devamını oku. Pagination için start_record/end_record kullan."""
        import re
        import glob as glob_module
        
        # Sıra numaralı dosya formatını destekle: NN_step_name_status.txt
        pattern = os.path.join(findings_base, f"*_{step_name}_{result_type}.txt")
        matching_files = glob_module.glob(pattern)
        
        if not matching_files:
            # Eski format da dene (backward compatibility)
            file_path = os.path.join(findings_base, f"{step_name}_{result_type}.txt")
            if not os.path.exists(file_path):
                return f"❌ Dosya bulunamadı: {step_name}_{result_type}.txt"
        else:
            file_path = matching_files[0]  # İlk eşleşen dosyayı al
        
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        # Result kısmını ayıkla
        result_match = content.find("<result>")
        result_end = content.find("</result>")
        
        if result_match == -1 or result_end == -1:
            return f"❌ Sonuç formatı geçersiz"
        
        result_content = content[result_match + 8:result_end]
        
        # Kayıtları parse et
        records = _parse_records(result_content)
        total_records = len(records)
        
        if total_records == 0:
            # Parse edilemezse eski yönteme fallback
            lines = [l.strip() for l in result_content.split('\n') if l.strip() and l.strip().startswith('(')]
            records = lines
            total_records = len(records)
        
        if total_records == 0:
            return f"❌ Kayıt bulunamadı"
        
        # Pagination uygula
        effective_end = end_record if end_record > 0 else total_records
        effective_end = min(effective_end, total_records)
        
        if start_record >= total_records:
            return f"❌ Başlangıç kayıt numarası ({start_record}) toplam kayıt sayısından ({total_records}) büyük"
        
        selected_records = records[start_record:effective_end]
        
        result_lines = "\n".join(selected_records)
        
        pagination_info = f"✅ 📊 Gösterilen: {start_record}-{effective_end} / Toplam: {total_records} kayıt"
        
        if effective_end < total_records:
            next_end = min(effective_end + DEFAULT_RECORDS_PER_PAGE, total_records)
            more_info = f"\n\n💡 Sonraki sayfa: read_finding(\"{step_name}\", start_record={effective_end}, end_record={next_end})"
        else:
            more_info = "\n\n✅ Tüm kayıtlar gösterildi."
        
        _log(f"📖 read_finding: {step_name} [{start_record}-{effective_end}/{total_records}]")
        
        return f"{pagination_info}\n\n{result_lines}{more_info}"
    
    # =========================================================================
    # TOOL MODE: DSL vs CYPHER
    # =========================================================================
    # REACT_TOOL_MODE=dsl   → execute_graph_dsl (DSL → Cypher derleme)
    # REACT_TOOL_MODE=cypher → execute_cypher_query + execute_cypher_query_with_embedding (doğrudan Cypher)
    
    base_tools = [add_source, add_sources, read_finding]  # Her iki modda da ortak
    
    if REACT_TOOL_MODE == "cypher":
        # CYPHER MODE: Model doğrudan Cypher yazar
        # Primary tool: execute_cypher_query
        # Embedding tool'u sadece REACT_EMBEDDING=true ise eklenir (vektör index olmayan
        # DB'lerde, örn. ticaret sicili grafiği, kapatılmalıdır → REACT_EMBEDDING=false)
        embedding_enabled = os.getenv("REACT_EMBEDDING", "true").lower() == "true"
        if embedding_enabled:
            tools = [execute_cypher_query, execute_cypher_query_with_embedding] + base_tools
            _log(f"🔧 TOOL MODE: cypher (direct Cypher + embedding)")
        else:
            tools = [execute_cypher_query] + base_tools
            _log(f"🔧 TOOL MODE: cypher (direct Cypher, embedding DISABLED)")
    else:
        # DSL MODE (default): Model DSL üretir, sistem Cypher'a derler
        # Primary tool: execute_graph_dsl, fallback: execute_cypher_query
        tools = [execute_cypher_query] + base_tools
        if GRAPH_DSL_AVAILABLE:
            tools.insert(0, execute_graph_dsl)  # DSL'i öne koy (önerilen yol)
        _log(f"🔧 TOOL MODE: dsl (DSL → Cypher compilation)")
    
    # MCP tool'larını filtrele - DISALLOW listesindekiler agent'a sunulmaz
    # Neden: Bu tool'lar internal kullanım içindir, custom tool'lar (execute_graph_dsl vb.) 
    # bunları wrapper olarak kullanır. Model doğrudan çağırmamalı.
    # Yeni MCP tool'ları otomatik olarak agent'a eklenir, sadece engellemek istediklerinizi buraya ekleyin.
    AGENT_DISALLOWED_MCP_TOOLS = {
        "read_neo4j_cypher",              # Internal: execute_cypher_query kullanır
        "read_neo4j_cypher_with_embedding", # Internal: execute_cypher_query_with_embedding kullanır
    }
    agent_mcp_tools = [t for t in mcp_tools if t.name not in AGENT_DISALLOWED_MCP_TOOLS]
    
    for mcp_tool in agent_mcp_tools:
        tools.append(mcp_tool)
    
    custom_count = len(tools) - len(agent_mcp_tools)
    _log(f"🔧 Total tools: {len(tools)} (custom: {custom_count}, mcp_agent: {len(agent_mcp_tools)}, mcp_internal: {len(mcp_tools) - len(agent_mcp_tools)})")
    
    return tools


# ============================================================================
# REACT AGENT CLASS
# ============================================================================

class ReactAgent:
    """
    Multi-Provider ReAct Agent - OpenAI ve Anthropic Desteği
    
    Özellikler:
    - OpenAI (GPT-5, GPT-4o, o1, o3) ve Anthropic (Claude Opus 4.5, Sonnet) desteği
    - Prompt Caching optimizasyonu (hem OpenAI hem Anthropic)
    - Extended Thinking desteği (GPT-5: reasoning_effort, Claude: thinking budget)
    - Paralel tool çağrıları
    - Streaming response
    
    Model Seçimi (Environment Variables):
        REACT_MODEL=gpt-5                    → OpenAI GPT-5
        REACT_MODEL=claude-opus-4-5          → Anthropic Claude Opus 4.5
        REACT_MODEL=claude-sonnet-4-5        → Anthropic Claude Sonnet 4.5
    
    Extended Thinking:
        GPT-5:  REACT_REASONING_EFFORT=low|medium|high
        Claude: REACT_THINKING_BUDGET=10000 (token sayısı, 0=kapalı)
    
    API Keys:
        OPENAI_API_KEY     → OpenAI modelleri için
        ANTHROPIC_API_KEY  → Claude modelleri için
    """
    
    # Geçerli domain'ler
    VALID_DOMAINS = {"sigorta", "bakim", "ticaret"}
    
    def __init__(self, graph, model_name: Optional[str] = None, reasoning_effort: Optional[str] = None, domain: Optional[str] = None):
        """
        Args:
            graph: Neo4j graph connection
            model_name: Model adı. Desteklenen modeller:
                - OpenAI: gpt-5, gpt-4o, o1-preview, o3-mini vb.
                - Anthropic: claude-opus-4-5, claude-sonnet-4-5, claude-3-5-sonnet vb.
                Default: env REACT_MODEL veya gpt-5
            reasoning_effort: GPT-5 için reasoning effort (none, low, medium, high)
                Claude için REACT_THINKING_BUDGET env variable kullanılır
            domain: Prompt domain'i. ZORUNLU parametre.
                - "sigorta": Sigorta poliçeleri, müşteriler, teminatlar
                - "bakim": WAT Motor bakım/arıza yönetimi (CMMS)
                - "ticaret": Ticaret Sicili Gazetesi belgeleri (Company→Document→Chunk→Entity)

        Raises:
            ValueError: domain parametresi belirtilmemişse veya geçersizse
        """
        # Domain validation - ZORUNLU
        if domain is None:
            raise ValueError(
                f"domain parametresi zorunlu. Geçerli değerler: {self.VALID_DOMAINS}"
            )
        if domain not in self.VALID_DOMAINS:
            raise ValueError(
                f"Geçersiz domain: '{domain}'. Geçerli değerler: {self.VALID_DOMAINS}"
            )
        self.domain = domain
        
        self.graph = graph
        self.model_name = model_name or os.environ.get("REACT_MODEL", "gpt-5")
        self.reasoning_effort = reasoning_effort or os.environ.get("REACT_REASONING_EFFORT", "low")
        self.agent: Optional[Dict[str, Any]] = None
        self.mcp_client: Optional[Any] = None
        self.mcp_tools: Optional[List[Any]] = None
        self._schema_cache: Dict[str, str] = {}
        self.redis_cache_active = False
        
        _log(f"🔧 ReactAgent initialized: domain={domain}, model={self.model_name}")
        
        # Redis LLM Semantic Cache kurulumu
        if REDIS_CACHE_IMPORTED and REDIS_CACHE_ENABLED and setup_semantic_cache:
            try:
                cache_ok = setup_semantic_cache()
                self.redis_cache_active = cache_ok
                if cache_ok:
                    logging.info("✅ Redis LLM Semantic Cache aktif (ReactAgent)")
            except Exception as e:
                logging.warning(f"⚠️ Redis cache setup failed: {e}")
    
    def _get_schema_for_session(self, session_id: str) -> str:
        """Session için şema bilgisini al (cache'li)"""
        if session_id in self._schema_cache:
            return self._schema_cache[session_id]
        
        try:
            database_url = self._get_neo4j_url()
            schema = get_cached_schema(database_url, self.graph)
            self._schema_cache[session_id] = schema
            return schema
        except Exception as e:
            _log(f"⚠️ Schema fetch error: {e}", "warning")
            return ""
    
    def _get_neo4j_url(self) -> str:
        """Neo4j URL'ini al - sunucu başlangıcıyla aynı format kullan"""
        # NOT: Sunucu başlangıcında sadece neo4j_uri kullanılıyor (database eklenmeden)
        # Cache key eşleşmesi için aynı formatı kullanmalıyız
        return os.getenv("NEO4J_URI", "")
    
    def _get_conversation_history(self, session_id: str, cache_enabled: bool = True) -> List[Dict[str, Any]]:
        """
        PostgreSQL'den conversation history al.
        
        Anthropic Prompt Caching için:
        - Son human mesajına cache_control eklenir
        - Claude otomatik olarak önceki cache'lenmiş prefix'i kullanır
        - https://docs.langchain.com/oss/python/integrations/chat/anthropic#incremental-caching-in-conversational-applications
        
        Args:
            session_id: Session identifier
            cache_enabled: True ise son human mesajına cache_control ekler
            
        Returns:
            List of message dicts with cache_control on last human message
        """
        if not session_id:
            return []

        # Tek-history modu: Bu instance kendi sohbet geçmişini TUTMAZ.
        # (Örn. LangGraph/Mongo tek doğru kaynak; takip bağlamını çağıran taşır.)
        # Cache'i bozmaz: sistem prompt'u prefix'i değişmez, yalnızca history boş kalır.
        if os.environ.get("REACT_DISABLE_HISTORY", "false").lower() == "true":
            return []

        # History limit: 100 mesaj (Anthropic cache için yeterli context)
        HISTORY_LIMIT = 100
        
        try:
            from src.shared.postgres_chat_history import create_postgres_chat_message_history
            
            conversation_history = create_postgres_chat_message_history(
                session_id=session_id, write_access=True
            )
            
            if conversation_history and hasattr(conversation_history, "messages"):
                messages: List[Dict[str, Any]] = []
                recent_messages = (
                    conversation_history.messages[-HISTORY_LIMIT:] 
                    if len(conversation_history.messages) > HISTORY_LIMIT 
                    else conversation_history.messages
                )
                
                # Son human mesajın index'ini bul (cache_control için)
                last_human_idx = -1
                for i in range(len(recent_messages) - 1, -1, -1):
                    if hasattr(recent_messages[i], "type") and recent_messages[i].type == "human":
                        last_human_idx = i
                        break
                
                for idx, msg in enumerate(recent_messages):
                    if hasattr(msg, "content"):
                        role = "user" if (hasattr(msg, "type") and msg.type == "human") else "assistant"
                        content = str(msg.content) if msg.content else ""
                        
                        # Cache-enabled format: content as list for cache_control support
                        if cache_enabled and role == "user":
                            # Son human mesajına cache_control ekle
                            content_block: Dict[str, Any] = {"type": "text", "text": content}
                            if idx == last_human_idx:
                                content_block["cache_control"] = {"type": "ephemeral"}
                            messages.append({"role": role, "content": [content_block]})
                        else:
                            # Assistant mesajları veya cache disabled: basit format
                            messages.append({"role": role, "content": content})
                
                _log(f"📜 History: {len(messages)} msgs (limit: {HISTORY_LIMIT}, cache: {cache_enabled})")
                return messages
        except Exception as e:
            _log(f"⚠️ History fetch error: {e}", "warning")
        
        return []
    
    def _save_to_history(self, session_id: str, role: str, content: str) -> None:
        """PostgreSQL'e mesaj kaydet"""
        if not session_id:
            return

        # Tek-history modu: yazma da kapalı (bkz. _get_conversation_history).
        if os.environ.get("REACT_DISABLE_HISTORY", "false").lower() == "true":
            return

        try:
            from src.shared.postgres_chat_history import create_postgres_chat_message_history
            from langchain_core.messages import HumanMessage, AIMessage
            
            conversation_history = create_postgres_chat_message_history(
                session_id=session_id, write_access=True
            )
            
            if conversation_history:
                if role == "Human":
                    conversation_history.add_message(HumanMessage(content=content))
                else:
                    conversation_history.add_message(AIMessage(content=content))
        except Exception as e:
            _log(f"⚠️ History save error: {e}", "warning")

    def _normalize_external_history(self, raw: Any) -> List[Dict[str, Any]]:
        """
        İstekle gelen (LangGraph state.messages) sohbet geçmişini agent mesaj
        formatına çevir: [{"role": "user"/"assistant", "content": "..."}].

        Tek-history (A2): Geçmişin tek kaynağı LangGraph/Mongo'dur. LangGraph kendi
        state.messages'ından son N soru/cevabı bu alanda gönderir; backend onu yeni
        sorunun ÖNÜNE ekler (bkz. agent_input["messages"] = history + [soru]) → LLM
        tüm geçmişi görür, tıpkı eski Postgres mantığı gibi.

        Cache: content düz string'e indirgenir ve history en sona eklenir; cache'lenen
        prefix = sistem prompt + şema (create_agent(system_prompt=...)) sabit kalır →
        token cache BOZULMAZ.

        - Kabul edilen roller: user/human → user, assistant/ai → assistant (system atlanır).
        - Defansif tavan: son REACT_EXTERNAL_HISTORY_LIMIT mesaj (varsayılan 40).
        """
        if not raw:
            return []
        try:
            import json as _json
            items = _json.loads(raw) if isinstance(raw, str) else raw
        except Exception as e:
            _log(f"⚠️ External history parse error: {e}", "warning")
            return []
        if not isinstance(items, list):
            return []

        role_map = {"user": "user", "human": "user", "assistant": "assistant", "ai": "assistant"}
        messages: List[Dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            role = role_map.get(str(item.get("role", "")).lower())
            if role is None:
                continue  # system vb. atla
            content = item.get("content", "")
            if not isinstance(content, str):
                content = str(content)
            if not content.strip():
                continue
            messages.append({"role": role, "content": content})

        try:
            limit = int(os.environ.get("REACT_EXTERNAL_HISTORY_LIMIT", "40"))
        except ValueError:
            limit = 40
        if limit > 0 and len(messages) > limit:
            messages = messages[-limit:]
        return messages

    def _get_langfuse_prompt_name(self) -> str:
        """Domain'e göre Langfuse prompt adını döndür."""
        # Domain bazlı Langfuse prompt isimleri
        prompt_names = {
            "sigorta": "react-agent-system",
            "bakim": "react-agent-wat-motor",
            "ticaret": "react-agent-ticaret-sicili",
        }
        return prompt_names.get(self.domain, LANGFUSE_PROMPT_NAME)
    
    def _build_system_prompt(self, schema_info: str, session_id: str = "") -> str:
        """
        Cache-optimized system prompt oluştur.
        
        Prompt Caching için:
        - Sabit prefix (instructions + schema) → Cache'lenir
        - Dinamik suffix ayrı tutulur
        
        Langfuse Prompt Management:
        - Domain'e göre farklı Langfuse prompt kullanılır
        - Langfuse erişilemezse kod içindeki prompt fallback olarak kullanılır
        - Prompt'ta {{schema_info}} placeholder'ı değişken olarak compile edilir
        
        Domain:
        - sigorta: Sigorta domain'i prompt'ları (DSL destekli)
        - bakim: WAT Motor bakım prompt'ları (sadece Cypher)
        
        Tool Mode:
        - REACT_TOOL_MODE=dsl → DSL araçları için talimatlar (sadece sigorta)
        - REACT_TOOL_MODE=cypher → Doğrudan Cypher yazma talimatları
        """
        # Domain'e göre prompt al
        _log(f"📋 Domain: {self.domain}, Tool mode: {REACT_TOOL_MODE.upper()}")
        
        # Bakım ve Ticaret domain'leri sadece Cypher mode destekler (DSL yok)
        effective_mode = REACT_TOOL_MODE
        if self.domain in ("bakim", "ticaret"):
            effective_mode = "cypher"  # Bu domain'ler için DSL desteklenmiyor
            if REACT_TOOL_MODE == "dsl":
                _log(f"⚠️ '{self.domain}' domain'i DSL desteklemiyor, cypher mode'a geçiliyor", "warning")
        
        # Domain ve mode'a göre prompt'ları al
        prompts = get_domain_prompts(self.domain, effective_mode)
        
        # Base prompt oluştur
        base_prompt = (
            prompts["system_base"] +
            prompts["tool_usage"] +
            prompts.get("thinking_guide", "") +
            prompts["content"]
        )
        
        # Pagination placeholder'larını değerlerle değiştir
        base_prompt = base_prompt.replace("{records_per_page}", str(DEFAULT_RECORDS_PER_PAGE))
        base_prompt = base_prompt.replace("{records_per_page_double}", str(DEFAULT_RECORDS_PER_PAGE * 2))
        
        # 1. Langfuse'dan prompt al (opsiyonel override)
        langfuse_prompt_name = self._get_langfuse_prompt_name()
        langfuse_prompt = get_prompt(
            name=langfuse_prompt_name,
            prompt_type=LANGFUSE_PROMPT_TYPE,
            label=LANGFUSE_PROMPT_LABEL,
            fallback=base_prompt,  # Fallback: kod içindeki prompt
        )
        
        if langfuse_prompt:
            try:
                # Langfuse prompt'u compile et - {{schema_info}} → gerçek şema
                compiled_prompt = langfuse_prompt.compile(schema_info=schema_info)
                
                # Pagination placeholder'larını Langfuse prompt'unda da değiştir
                compiled_prompt = compiled_prompt.replace("{records_per_page}", str(DEFAULT_RECORDS_PER_PAGE))
                compiled_prompt = compiled_prompt.replace("{records_per_page_double}", str(DEFAULT_RECORDS_PER_PAGE * 2))
                
                # Version bilgisini logla
                version = getattr(langfuse_prompt, 'version', 'unknown')
                labels = getattr(langfuse_prompt, 'labels', [])
                _log(f"📋 Langfuse prompt loaded: {langfuse_prompt_name} v{version} {labels}")
                
                return compiled_prompt
                
            except Exception as e:
                _log(f"⚠️ Langfuse prompt compile failed: {e}, using fallback", "warning")
        
        # 2. Fallback: Kod içindeki prompt
        _log(f"📋 Using fallback prompt for domain: {self.domain}")
        full_prompt = base_prompt + schema_info
        return full_prompt
    
    def _ensure_prompt_in_langfuse(self) -> bool:
        """
        Langfuse'da prompt yoksa oluştur (migration/bootstrap için).
        
        Bu metod sadece ilk kurulumda veya migration sırasında çağrılmalı.
        Normal kullanımda Langfuse UI tercih edilir.
        """
        # Önce prompt var mı kontrol et
        existing = get_prompt(
            name=LANGFUSE_PROMPT_NAME,
            prompt_type=LANGFUSE_PROMPT_TYPE,
            label=LANGFUSE_PROMPT_LABEL,
        )
        
        if existing:
            _log(f"📋 Langfuse prompt already exists: {LANGFUSE_PROMPT_NAME}")
            return True
        
        # Prompt yok, oluştur
        # NOT: {{schema_info}} placeholder olarak kalmalı
        # DSL mode için tam prompt (en kapsamlı versiyon)
        full_base_prompt = SHARED_SYSTEM_BASE + DSL_TOOL_USAGE + DSL_THINKING_GUIDE + SHARED_CONTENT
        prompt_with_placeholder = full_base_prompt + "{{schema_info}}"
        
        success = create_prompt(
            name=LANGFUSE_PROMPT_NAME,
            prompt=prompt_with_placeholder,
            prompt_type=LANGFUSE_PROMPT_TYPE,
            labels=[LANGFUSE_PROMPT_LABEL],
            config={
                "model": self.model_name,
                "description": "ReAct Agent system prompt for Neo4j graph queries",
            },
        )
        
        if success:
            _log(f"✅ Langfuse prompt created: {LANGFUSE_PROMPT_NAME}")
        else:
            _log(f"⚠️ Failed to create Langfuse prompt, will use fallback", "warning")
        
        return success
    
    def _build_messages(
        self, 
        system_prompt: str, 
        conversation_history: List[Dict[str, str]], 
        user_question: str
    ) -> List[Dict[str, str]]:
        """
        Prompt Caching için optimize edilmiş mesaj listesi oluştur.
        
        Yapı:
        1. System message (cached prefix + schema)
        2. Conversation history (dinamik)
        3. User question (dinamik)
        """
        messages = [{"role": "system", "content": system_prompt}]
        
        # Conversation history ekle
        for msg in conversation_history:
            messages.append(msg)
        
        # User question ekle
        messages.append({"role": "user", "content": user_question})
        
        return messages
    
    async def _create_agent(self, schema_info: str, session_id: str):
        """ReAct agent oluştur"""
        if not LANGCHAIN_AVAILABLE or create_agent is None:
            raise ImportError("LangChain not available")
        
        # MCP client başlat
        # langchain-mcp-adapters 0.1.0+ API: context manager kullanılmıyor
        if self.mcp_client is None:
            if MultiServerMCPClient is None:
                raise ImportError("MCP Adapters not available")
            config = get_mcp_server_config()
            _log(f"📡 MCP connecting to: {config}")
            self.mcp_client = MultiServerMCPClient(config)
            # Yeni API: doğrudan get_tools() çağır (async)
            self.mcp_tools = await self.mcp_client.get_tools()
            tool_count = len(self.mcp_tools) if self.mcp_tools else 0
            tool_names = [t.name for t in self.mcp_tools] if self.mcp_tools else []
            _log(f"✅ MCP connected, {tool_count} tools available: {tool_names}")
        
        # System prompt oluştur (cache-optimized, Langfuse Prompt Management)
        system_prompt = self._build_system_prompt(schema_info, session_id)
        _log(f"📜 System prompt: {len(system_prompt)} chars")
        
        # Debug: Tam prompt'u loga yaz (limitsiz)
        # _log(f"📜 [FULL SYSTEM PROMPT START]\n{system_prompt}\n📜 [FULL SYSTEM PROMPT END]")
        
        # Debug: Tam prompt'u dosyaya yaz
        prompt_log_path = os.path.join(os.getcwd(), "agent_findings", "react", "_system_prompt.txt")
        try:
            with open(prompt_log_path, "w", encoding="utf-8") as f:
                f.write(system_prompt)
            _log(f"📝 System prompt saved: {prompt_log_path}")
        except Exception as e:
            _log(f"⚠️ Prompt save error: {e}", "warning")
        
        # Model oluştur
        model = self._create_model()
        
        # Middleware
        middleware = []
        if ModelCallLimitMiddleware is not None:
            middleware.append(ModelCallLimitMiddleware(run_limit=25))
        
        # Agent'ı önce MCP tools olmadan oluştur - tool'lar stream_query_response'da eklenir
        # çünkü her soru için farklı question_id ile tool'lar oluşturulmalı
        
        return {
            "model": model,
            "system_prompt": system_prompt,
            "original_system_prompt": system_prompt,  # Few-shot cache'lemesini önlemek için
            "middleware": middleware,
            "schema_info": schema_info,
        }
    
    def _create_model(self) -> Any:
        """
        Model instance oluştur.
        
        Desteklenen modeller:
        - OpenAI: gpt-5, gpt-4o, gpt-4-turbo, o1-*, o3-* vb.
        - Anthropic: claude-opus-4-5, claude-sonnet-4-5, claude-3-5-sonnet, claude-3-opus vb.
        
        Model seçimi REACT_MODEL env variable ile yapılır:
        - REACT_MODEL=gpt-5                    → OpenAI GPT-5
        - REACT_MODEL=claude-opus-4-5          → Anthropic Claude Opus 4.5
        - REACT_MODEL=claude-sonnet-4-5        → Anthropic Claude Sonnet 4.5
        
        Extended Thinking (Reasoning):
        - GPT-5: REACT_REASONING_EFFORT=low|medium|high
        - Claude: REACT_THINKING_BUDGET=10000 (token sayısı, 0=kapalı)
        """
        from langchain_openai import ChatOpenAI
        from pydantic import SecretStr
        
        actual_model = self.model_name
        if ":" in self.model_name:
            actual_model = self.model_name.split(":", 1)[1]
        
        # ========== ANTHROPIC CLAUDE ==========
        if actual_model.lower().startswith("claude"):
            if not ANTHROPIC_AVAILABLE or ChatAnthropic is None:
                raise ImportError("langchain-anthropic paketi kurulu değil. `uv add langchain-anthropic` ile kurun.")
            
            anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not anthropic_api_key:
                raise ValueError("ANTHROPIC_API_KEY environment variable gerekli.")
            
            # Claude Extended Thinking (budget_tokens ile kontrol edilir)
            # REACT_THINKING_BUDGET=10000 → 10K token thinking bütçesi
            # REACT_THINKING_BUDGET=0 → Extended thinking kapalı
            thinking_budget = int(os.environ.get("REACT_THINKING_BUDGET", "10000"))
            
            # Claude model parametreleri
            model_kwargs: dict[str, Any] = {
                "model": actual_model,
                "api_key": SecretStr(anthropic_api_key),
                "max_tokens": 16384,  # Claude için max output token
            }
            
            # Extended thinking etkinleştir (budget > 0 ise)
            if thinking_budget > 0:
                model_kwargs["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": thinking_budget,
                }
                _log(f"🔧 Model: {actual_model} (Claude), extended_thinking={thinking_budget} tokens")
            else:
                _log(f"🔧 Model: {actual_model} (Claude), extended_thinking=disabled")
            
            # 📦 Anthropic Prompt Caching
            # https://docs.langchain.com/oss/python/integrations/chat/anthropic#prompt-caching
            # NOT: beta_cache parametresi deprecate edildi, artık cache_control ile yapılıyor
            # Tool caching için bind_tools kullanılacak (create_agent'da)
            
            return ChatAnthropic(**model_kwargs)
        
        # ========== OPENAI GPT ==========
        openai_api_key = os.environ.get("OPENAI_API_KEY")
        
        # GPT-5 için reasoning_effort + summary
        # summary: "auto" | "concise" | "detailed" - düşünce süreçlerini gösterir
        if "gpt-5" in actual_model.lower() and self.reasoning_effort:
            _log(f"🔧 Model: {actual_model} (OpenAI), reasoning={self.reasoning_effort}, summary=auto")
            return ChatOpenAI(
                model=actual_model,
                api_key=SecretStr(openai_api_key) if openai_api_key else None,
                reasoning={"effort": self.reasoning_effort, "summary": "auto"},
            )
        else:
            _log(f"🔧 Model: {actual_model} (OpenAI)")
            return ChatOpenAI(
                model=actual_model,
                api_key=SecretStr(openai_api_key) if openai_api_key else None,
            )
    
    async def stream_query_response(
        self, 
        question: str, 
        session_id: str = "", 
        question_id: str = "",
        user_id: Optional[str] = None,  # Langfuse User Tracking
        **kwargs
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        ReAct Agent ile streaming cevap üret.
        
        Args:
            question: Kullanıcı sorusu
            session_id: Oturum ID'si
            question_id: Soru ID'si
            user_id: Kullanıcı ID (email) - Langfuse User Tracking için
        
        Yields:
            Streaming response chunks
        """
        import time
        import uuid
        
        # Question ID - tam ID log için, kısa ID dosya path'leri için
        original_question_id = question_id
        if not question_id:
            original_question_id = str(uuid.uuid4())
        short_question_id = original_question_id[:8]
        
        # Logging context'i set et (Grafana/Loki için) - TAM ID
        set_request_context(session_id=session_id, question_id=original_question_id)
        
        # Session sources temizle - kısa ID
        _clear_session_sources(short_question_id)
        
        # Token tracking başlat
        token_tracker = TokenTracker()
        _log(f"\n{'='*60}")
        _log(f"🚀 [REACT] Yeni sorgu: {question[:80]}...")
        _log(f"   Session: {session_id[:8] if session_id else 'N/A'}, Question: {short_question_id}")
        _log(f"{'='*60}")
        
        # 📊 Langfuse session & trace başlat
        # Sessions: Tüm trace'ler aynı session altında gruplanır
        # See: https://langfuse.com/docs/observability/features/sessions
        langfuse_trace = None
        langfuse_session_ctx = None
        langfuse = get_langfuse()
        
        if langfuse:
            try:
                # 1. Session context'i başlat - tüm child trace'ler bu session'a bağlanır
                # user_id: Gerçek kullanıcı email/ID kullanılır (Langfuse User Tracking için)
                # See: https://langfuse.com/docs/observability/features/users
                from langfuse import propagate_attributes
                effective_user_id = user_id or (session_id[:8] if session_id else None)
                langfuse_session_ctx = propagate_attributes(
                    session_id=session_id,
                    user_id=effective_user_id,
                    metadata={
                        "question_id": original_question_id,
                    }
                )
                langfuse_session_ctx.__enter__()
                _log(f"📊 Langfuse session started: {session_id[:8] if session_id else 'N/A'}, user_id param: {user_id}, effective_user_id: {effective_user_id}")
                
                # 2. Ana span başlat (Langfuse SDK - propagate_attributes context'i içinde)
                # NOT: propagate_attributes session/user bilgisini set ediyor
                # Bu context içindeki tüm span'lar otomatik olarak trace'e eklenir
                langfuse_trace = langfuse.start_span(
                    name="react_agent_query",
                    input={"question": question},
                    metadata={
                        "model": self.model_name,
                        "reasoning_effort": self.reasoning_effort,
                        "question_id": original_question_id,
                        "session_id": session_id,
                        "user_id": effective_user_id,
                    }
                )
                trace_id = getattr(langfuse_trace, 'id', 'unknown')
                _log(f"📊 Langfuse span started: {trace_id}")
                
                # TokenTracker'a parent span'ı bağla (tool span'ları bu span'a child olarak eklenir)
                token_tracker.set_langfuse_parent(langfuse_trace)
            except Exception as e:
                _log(f"⚠️ Langfuse session/trace start failed: {e}", "warning")
        
        # 🎯 Query Cache check - benzer sorular için cache'den cevap dön
        try:
            query_cache = get_query_cache()
            cached_response = await query_cache.get_similar(question, session_id)
            
            if cached_response:
                _log(f"🎯 CACHE HIT! Similarity: {cached_response.get('similarity', 0):.3f}")
                
                # Cache hit metrikleri
                cache_metrics = get_cache_metrics()
                
                # Langfuse'a cache hit logla
                if langfuse_trace:
                    try:
                        # Langfuse SDK: span.update() ile cache hit bilgisi ekle, sonra end()
                        langfuse_trace.update(
                            output={"response": cached_response["response"], "cache_hit": True},
                            metadata={
                                "cache_hit": True,
                                "similarity": cached_response.get("similarity", 0),
                                "cache_hit_rate": cache_metrics.hit_rate,
                                "total_time_seconds": 0.1,
                            }
                        )
                        langfuse_trace.end()
                        flush_langfuse()
                        _log(f"📊 Langfuse span (cache hit) completed")
                    except Exception as cache_trace_err:
                        _log(f"⚠️ Langfuse cache span failed: {cache_trace_err}", "warning")
                
                # Cache'den gelen cevabı döndür
                yield {
                    "type": "cache_hit",
                    "content": f"🎯 Cache hit (similarity: {cached_response.get('similarity', 0):.2f})",
                }
                
                # Frontend için message_chunk gönder (chat ekranında görünsün)
                cached_content = cached_response["response"]
                yield {
                    "type": "message_chunk",
                    "content": cached_content,
                    "full_message": cached_content,
                    "is_final_answer": True,
                    "session_id": session_id,
                }
                
                yield {
                    "type": "final_response",
                    "content": cached_content,
                    "sources": cached_response.get("sources", {"documents": [], "pages": []}),
                    "metrics": {
                        "total_time": 0.1,  # Cache hit çok hızlı
                        "tool_calls": 0,
                        "llm_calls": 0,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cached_tokens": 0,
                        "total_tokens": 0,
                        "cache_hit": True,
                        "cache_similarity": cached_response.get("similarity", 0),
                        "cache_hit_rate": cache_metrics.hit_rate,
                    },
                    "session_id": session_id,
                    "timestamp": datetime.now().isoformat(),
                }
                return  # Cache hit - agent çalıştırma
                
        except Exception as cache_error:
            _log(f"⚠️ Cache check error: {cache_error}", "warning")
        
        # Timing
        total_start = time.time()
        llm_step_count = 0
        
        try:
            # Session sayacını sıfırla (kullanıcı dostu mesajlar için)
            _reset_session_result_counter(session_id)
            
            # Başlangıç
            yield {
                "type": "thinking_step",
                "message": "🚀 Sorgunuz alındı...",
                "session_id": session_id,
                "timestamp": datetime.now().isoformat(),
            }
            
            # Şema al
            schema_info = self._get_schema_for_session(session_id)
            if not schema_info:
                yield {
                    "type": "error",
                    "message": "Veritabanı şema bilgisi alınamadı.",
                    "session_id": session_id,
                }
                return
            
            yield {
                "type": "thinking_step",
                "message": "📊 Veritabanı yapısı yüklendi",
                "session_id": session_id,
            }
            
            # History al.
            # Tek-history (A2): LangGraph kendi state.messages'ından son N soru/cevabı
            # istekte 'chat_history' olarak gönderir → onu kullan (Postgres'e bakma).
            # Gönderilmezse Postgres mantığına düş (REACT_DISABLE_HISTORY=true ise boş döner).
            external_history = kwargs.get("chat_history")
            if external_history:
                conversation_history = self._normalize_external_history(external_history)
                _log(f"📜 External history (LangGraph): {len(conversation_history)} msgs")
            else:
                conversation_history = self._get_conversation_history(session_id)
            # Backend kendi geçmişini TUTMAZ; kaynak LangGraph/Mongo (REACT_DISABLE_HISTORY ile no-op).
            self._save_to_history(session_id, "Human", question)
            
            # Agent config al veya oluştur
            if self.agent is None:
                self.agent = await self._create_agent(schema_info, session_id)
            
            agent_config = self.agent
            
            # Tool'ları oluştur (her soru için yeni question_id ile)
            if self.mcp_tools is None:
                yield {
                    "type": "error",
                    "message": "MCP tools başlatılamadı.",
                    "session_id": session_id,
                }
                return
            
            react_tools = create_react_tools(
                self.mcp_tools,
                session_id[:8] if session_id else "default",
                short_question_id,
                user_question=question,
                token_tracker=token_tracker
            )
            
            # Gerçek agent'ı oluştur
            if create_agent is None:
                yield {
                    "type": "error",
                    "message": "LangChain create_agent not available.",
                    "session_id": session_id,
                }
                return
            
            # 📚 Few-shot learning: User message'a ekle (System prompt'u değiştirmeden)
            # ⚠️ CACHE İÇİN ÖNEMLİ: System prompt SABİT kalmalı, few-shot user message'da olmalı
            few_shot_examples: List[Dict[str, Any]] = []
            few_shot_prompt: str = ""  # User message'a eklenecek
            
            # Her soru için orijinal system_prompt kullan (cache için SABİT)
            original_system_prompt = agent_config.get("original_system_prompt") or agent_config["system_prompt"]
            final_system_prompt = original_system_prompt  # ⚠️ DEĞİŞTİRME - Cache için sabit
            
            if FEEDBACK_ENABLED and FEEDBACK_FEW_SHOT_ENABLED and question:
                try:
                    # Başarılı örnekler
                    few_shot_examples = get_few_shot_examples(
                        question=question,
                        limit=2,  # Max 2 örnek
                        min_score=1,
                        similarity_threshold=0.75,
                    )
                    
                    # Düzeltilmiş hatalar (learning from mistakes)
                    corrections = get_corrections_for_fewshot(
                        question=question,
                        limit=2,
                        similarity_threshold=0.70,
                    )
                    
                    if few_shot_examples or corrections:
                        few_shot_prompt = format_few_shot_prompt(few_shot_examples, corrections)
                        # ⚠️ ESKİ: final_system_prompt = final_system_prompt + "\n\n" + few_shot_prompt
                        # ✅ YENİ: few_shot_prompt user message'a eklenecek (aşağıda)
                        _log(f"📚 Few-shot: {len(few_shot_examples)} örnek, {len(corrections)} düzeltme (user message'a eklenecek)")
                        
                        # Few-shot içeriğini logla (ilk 1000 karakter)
                        _log(f"📋 Few-shot strateji: {few_shot_prompt[:1000]}...")
                    else:
                        _log(f"📚 Few-shot: 0 örnek bulundu (threshold: 0.75)")
                except Exception as e:
                    _log(f"⚠️ Few-shot examples error: {e}", "warning")
            
            # 🔧 Her sorgu için YENİ middleware instance oluştur
            # (cached middleware'in run_model_call_count'u sıfırlanmıyor!)
            query_middleware = []
            if ModelCallLimitMiddleware is not None:
                query_middleware.append(ModelCallLimitMiddleware(run_limit=25))
            
            # 📦 Anthropic Prompt Caching Middleware - Cache için ZORUNLU!
            # https://docs.langchain.com/oss/python/integrations/middleware/anthropic
            actual_model = self.model_name
            if ":" in self.model_name:
                actual_model = self.model_name.split(":", 1)[1]
            
            is_claude = actual_model.lower().startswith("claude")
            if is_claude and ANTHROPIC_CACHING_MIDDLEWARE_AVAILABLE and AnthropicPromptCachingMiddleware is not None:
                try:
                    # AnthropicPromptCachingMiddleware otomatik olarak:
                    # - System prompt'u cache'ler
                    # - Tool definitions'ı cache'ler
                    # - Conversation prefix'i cache'ler
                    caching_middleware = AnthropicPromptCachingMiddleware(
                        ttl="5m",  # 5 dakika cache (default)
                        unsupported_model_behavior="warn",  # Claude dışı modellerde uyar
                    )
                    query_middleware.append(caching_middleware)
                    _log(f"📦 AnthropicPromptCachingMiddleware added (ttl=5m)")
                except Exception as cache_mw_err:
                    _log(f"⚠️ AnthropicPromptCachingMiddleware failed: {cache_mw_err}", "warning")
            
            model_for_agent = agent_config["model"]
            tools_for_agent = react_tools
            system_prompt_for_agent: Any = final_system_prompt
            
            # 🔍 DEBUG: System prompt hash'ini logla - cache için SABİT olmalı!
            import hashlib
            prompt_hash = hashlib.md5(final_system_prompt.encode()).hexdigest()[:12]
            _log(f"🔍 [CACHE DEBUG] System prompt hash: {prompt_hash}")
            _log(f"🔍 [CACHE DEBUG] System prompt length: {len(final_system_prompt)} chars")
            _log(f"🔍 [CACHE DEBUG] System prompt first 200: {final_system_prompt[:200]}...")
            _log(f"🔍 [CACHE DEBUG] System prompt last 200: ...{final_system_prompt[-200:]}")
            
            # Cache uygunluk kontrolü için değişkenler
            cache_eligible = True
            cache_warning: str | None = None
            estimated_prompt_tokens = len(final_system_prompt) // 4  # Ortalama ~4 karakter = 1 token
            
            if actual_model.lower().startswith("claude"):
                # Claude için prompt caching:
                # 1. Tool caching: bind_tools ile cache_control
                # 2. System prompt caching: SystemMessage ile cache_control
                # NOT: bind_tools cache için, ama create_agent'a da tools geçmeli (graph için)
                # ⚠️ ÖNEMLİ: convert_to_anthropic_tool KULLANMA! bind_tools zaten dönüşümü yapıyor.
                #    Manuel dönüşüm cache mekanizmasını bozuyor.
                
                # 📊 Minimum Token Kontrolü (Anthropic Cache Limitations)
                # - Claude Opus 4.5: minimum 4096 token
                # - Claude Sonnet 4.5/4: minimum 1024 token
                # - Claude Haiku 4.5: minimum 4096 token
                is_opus = "opus" in actual_model.lower()
                is_haiku_45 = "haiku-4-5" in actual_model.lower() or "haiku-4.5" in actual_model.lower()
                min_tokens_required = 4096 if (is_opus or is_haiku_45) else 1024
                
                if estimated_prompt_tokens < min_tokens_required:
                    cache_eligible = False
                    cache_warning = (
                        f"⚠️ CACHE UYUMSUZ: {actual_model} için minimum {min_tokens_required} token gerekli, "
                        f"mevcut prompt ~{estimated_prompt_tokens} token ({len(final_system_prompt)} karakter). "
                        f"Cache çalışmayacak!"
                    )
                    _log(cache_warning, "warning")
                else:
                    _log(f"✅ Cache uyumlu: ~{estimated_prompt_tokens} token >= {min_tokens_required} minimum")
                
                # 📊 Langfuse'a cache eligibility metadata'sı ekle
                if langfuse_trace:
                    try:
                        langfuse_trace.update(
                            metadata={
                                "model": self.model_name,
                                "cache_eligible": cache_eligible,
                                "cache_min_tokens_required": min_tokens_required,
                                "cache_estimated_prompt_tokens": estimated_prompt_tokens,
                                "cache_prompt_chars": len(final_system_prompt),
                                "cache_warning": cache_warning,
                            }
                        )
                        _log(f"📊 Langfuse cache metadata updated: eligible={cache_eligible}")
                    except Exception as lf_err:
                        _log(f"⚠️ Langfuse cache metadata update failed: {lf_err}", "warning")
                
                try:
                    # Tool'ları cache için işaretle (orijinal LangChain tool'ları kullan)
                    model_for_agent = agent_config["model"].bind_tools(
                        react_tools,
                        cache_control={"type": "ephemeral"},
                    )
                    # ⚠️ tools_for_agent hala react_tools olmalı - create_agent graph için gerekli!
                    # bind_tools sadece cache için, agent'ın tool node'u tools listesine bakıyor
                    tools_for_agent = react_tools
                    _log(f"📦 Anthropic tool caching enabled ({len(react_tools)} tools)")
                    
                    # 🔍 DEBUG: Tool isimlerini logla
                    tool_names = [getattr(t, 'name', 'unknown') for t in react_tools]
                    _log(f"🔍 [CACHE DEBUG] Tool names: {tool_names}")
                    
                    # System prompt'u cache için işaretle
                    # SystemMessage ile content block formatı kullan
                    if SystemMessage is not None:
                        system_prompt_for_agent = SystemMessage(
                            content=[
                                {
                                    "type": "text",
                                    "text": final_system_prompt,
                                    "cache_control": {"type": "ephemeral"},
                                }
                            ]
                        )
                        _log(f"📦 Anthropic system prompt caching enabled (ephemeral)")
                    
                except Exception as cache_err:
                    _log(f"⚠️ Anthropic caching setup failed: {cache_err}, using default", "warning")
                    model_for_agent = agent_config["model"]
                    tools_for_agent = react_tools
                    system_prompt_for_agent = final_system_prompt
            
            # 🔍 DEBUG: Agent oluşturma öncesi kontrol
            _log(f"🔍 [CACHE DEBUG] Creating agent with:")
            _log(f"🔍 [CACHE DEBUG]   - model_for_agent type: {type(model_for_agent).__name__}")
            _log(f"🔍 [CACHE DEBUG]   - tools_for_agent count: {len(tools_for_agent)}")
            _log(f"🔍 [CACHE DEBUG]   - system_prompt_for_agent type: {type(system_prompt_for_agent).__name__}")
            if isinstance(system_prompt_for_agent, str):
                _log(f"🔍 [CACHE DEBUG]   - system_prompt hash: {hashlib.md5(system_prompt_for_agent.encode()).hexdigest()[:12]}")
            else:
                # SystemMessage ise content'i hash'le
                content = getattr(system_prompt_for_agent, 'content', None)
                if content:
                    if isinstance(content, list) and len(content) > 0:
                        text = content[0].get('text', '') if isinstance(content[0], dict) else str(content[0])
                        _log(f"🔍 [CACHE DEBUG]   - SystemMessage content hash: {hashlib.md5(text.encode()).hexdigest()[:12]}")
                    else:
                        _log(f"🔍 [CACHE DEBUG]   - SystemMessage content: {str(content)[:100]}")
            
            agent = create_agent(
                model=model_for_agent,
                tools=tools_for_agent,
                system_prompt=system_prompt_for_agent,
                middleware=query_middleware,  # Her sorguda yeni instance
                name="react_agent",
            )
            
            # Messages oluştur - unused but kept for reference
            _ = self._build_messages(
                agent_config["system_prompt"],
                conversation_history,
                question
            )
            
            # Sadece user messages'ı agent'a gönder (system prompt zaten agent'ta)
            # Anthropic Incremental Cache: Yeni soru mesajı da cache_control ile işaretlenmeli
            # Bu sayede bu soru da cache'e eklenir ve sonraki sorularda okunur
            is_claude = actual_model.lower().startswith("claude")
            
            # 📚 Few-shot'u user message'a ekle (cache'i bozmamak için system prompt'ta DEĞİL)
            # Few-shot varsa question'ın önüne ekle
            user_question_with_context = question
            if few_shot_prompt:
                user_question_with_context = f"{few_shot_prompt}\n\n---\n\n**Kullanıcı Sorusu:**\n{question}"
                _log(f"📚 Few-shot user message'a eklendi ({len(few_shot_prompt)} chars)")
            
            if is_claude:
                # Cache-enabled format: content as list with cache_control
                new_user_message: Dict[str, Any] = {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": user_question_with_context,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ]
                }
            else:
                # Non-Claude models: simple format
                new_user_message = {"role": "user", "content": user_question_with_context}
            
            agent_input: Dict[str, Any] = {"messages": [new_user_message]}
            
            # History varsa ekle
            # NOT: History'deki son human mesajın cache_control'ü var,
            # ama yeni soru "en son" human mesaj olduğu için onun cache_control'ü önemli
            if conversation_history:
                # History'den cache_control'ü kaldır (yeni soru artık "son" mesaj)
                history_for_input = []
                for msg in conversation_history:
                    if msg.get("role") == "user" and isinstance(msg.get("content"), list):
                        # User mesajından cache_control'ü kaldır
                        cleaned_content = []
                        for block in msg["content"]:
                            if isinstance(block, dict):
                                block_copy = {k: v for k, v in block.items() if k != "cache_control"}
                                cleaned_content.append(block_copy)
                            else:
                                cleaned_content.append(block)
                        history_for_input.append({"role": "user", "content": cleaned_content})
                    else:
                        history_for_input.append(msg)
                
                agent_input["messages"] = history_for_input + [new_user_message]
            
            yield {
                "type": "thinking_step",
                "message": "🧠 Soru analiz ediliyor...",
                "session_id": session_id,
            }
            
            # Streaming
            response_text = ""
            tool_call_count = 0
            logged_msg_ids: set[Any] = set()
            pending_tool_names: Dict[str, str] = {}  # tool_call_id -> tool_name
            collected_graph_facts: List[str] = []  # Hallucination check için tool sonuçları
            
            # 📚 Tool calls collection for feedback/learning
            collected_tool_calls: List[Dict[str, Any]] = []
            pending_tool_inputs: Dict[str, Dict[str, Any]] = {}  # tool_call_id -> {tool_name, tool_input, start_time}
            
            # 📊 Langfuse için: LLM'e giden tüm context'i biriktir
            accumulated_tool_results: List[Dict[str, Any]] = []  # [{tool_name, input, output}, ...]
            
            chunk_count = 0
            async for chunk in agent.astream(agent_input, stream_mode="updates"):  # type: ignore[arg-type]
                chunk_count += 1
                
                # 🔍 DEBUG: Her chunk'ı logla
                if isinstance(chunk, dict):
                    for node_name, node_output in chunk.items():
                        _log(f"🔄 [CHUNK {chunk_count}] Node: {node_name}, Keys: {list(node_output.keys()) if isinstance(node_output, dict) else type(node_output).__name__}")
                
                if not isinstance(chunk, dict):
                    continue
                
                for node_name, node_output in chunk.items():
                    if not isinstance(node_output, dict):
                        continue
                    
                    messages_out = node_output.get("messages", [])
                    for message in messages_out:
                        msg_id = getattr(message, "id", None) or hash(str(getattr(message, "content", ""))[:100])
                        if msg_id in logged_msg_ids:
                            continue
                        logged_msg_ids.add(msg_id)
                        
                        msg_type = type(message).__name__
                        
                        # 🔍 DEBUG: Her mesaj tipini logla
                        content_preview = str(getattr(message, "content", ""))[:100]
                        has_tool_calls = hasattr(message, "tool_calls") and message.tool_calls
                        _log(f"📨 [MSG] Type: {msg_type}, HasToolCalls: {has_tool_calls}, Content: {content_preview}...")
                        
                        # AIMessage'dan token kullanımı çıkar
                        if msg_type == "AIMessage":
                            usage_metadata = getattr(message, "usage_metadata", None)
                            response_metadata = getattr(message, "response_metadata", None)
                            
                            input_tokens = 0
                            output_tokens = 0
                            cached_tokens = 0  # cache_read
                            cache_creation_tokens = 0  # cache_creation
                            reasoning_tokens = 0  # GPT-5 reasoning token sayısı
                            
                            # 📦 Anthropic Cache Verification Logging
                            # https://docs.langchain.com/oss/python/integrations/chat/anthropic#caching-tools
                            if usage_metadata:
                                input_details = usage_metadata.get("input_token_details", {})
                                if input_details and isinstance(input_details, dict):
                                    cache_creation_tokens = input_details.get("cache_creation", 0)
                                    cache_read = input_details.get("cache_read", 0)
                                    ephemeral_5m = input_details.get("ephemeral_5m_input_tokens", 0)
                                    ephemeral_1h = input_details.get("ephemeral_1h_input_tokens", 0)
                                    
                                    # Cache durumu özeti
                                    if cache_creation_tokens > 0 or cache_read > 0:
                                        cache_status = "✅ CACHE_HIT" if cache_read > 0 else "📝 CACHE_WRITE"
                                        _log(f"📦 [CACHE] {cache_status} | creation: {cache_creation_tokens}, read: {cache_read}, ephemeral_5m: {ephemeral_5m}")
                                        
                                        # Tool cache çalışıyor mu?
                                        if cache_read > 0:
                                            _log(f"✅ [CACHE VERIFIED] System prompt + Tool definitions reading from cache ({cache_read} tokens)")
                                        elif cache_creation_tokens > 0:
                                            _log(f"📝 [CACHE CREATED] System prompt + Tool definitions cached ({cache_creation_tokens} tokens)")
                                
                                # Full debug log
                                _log(f"🔍 [DEBUG] usage_metadata: {usage_metadata}")
                            
                            if response_metadata:
                                # Sadece token ile ilgili kısımları logla
                                token_related = {k: v for k, v in response_metadata.items() 
                                               if 'token' in k.lower() or 'usage' in k.lower() or 'cache' in k.lower()}
                                if token_related:
                                    _log(f"🔍 [DEBUG] response_metadata (token): {token_related}")
                            
                            # usage_metadata varsa (LangChain 0.3+)
                            if usage_metadata:
                                input_tokens = usage_metadata.get("input_tokens", 0)
                                output_tokens = usage_metadata.get("output_tokens", 0)
                                
                                # OpenAI cached tokens - input_token_details içinde
                                input_details = usage_metadata.get("input_token_details", {})
                                if input_details and isinstance(input_details, dict):
                                    # OpenAI GPT-5 format: cache_read (not cached_tokens!)
                                    cached_tokens = input_details.get("cache_read", 0)
                                    # Fallback: eski format
                                    if cached_tokens == 0:
                                        cached_tokens = input_details.get("cached_tokens", 0)
                                
                                # GPT-5 reasoning tokens - output_token_details içinde
                                output_details = usage_metadata.get("output_token_details", {})
                                if output_details and isinstance(output_details, dict):
                                    reasoning_tokens = output_details.get("reasoning", 0)
                                
                                # Anthropic format
                                if cached_tokens == 0:
                                    cached_tokens = usage_metadata.get("cache_read_input_tokens", 0)
                            
                            # response_metadata'dan da bakılabilir
                            if (input_tokens == 0 or cached_tokens == 0) and response_metadata:
                                token_usage = response_metadata.get("token_usage", {})
                                if token_usage:
                                    if input_tokens == 0:
                                        input_tokens = token_usage.get("prompt_tokens", 0)
                                    if output_tokens == 0:
                                        output_tokens = token_usage.get("completion_tokens", 0)
                                    
                                    # OpenAI API format: prompt_tokens_details.cached_tokens
                                    prompt_details = token_usage.get("prompt_tokens_details", {})
                                    if prompt_details and isinstance(prompt_details, dict):
                                        cached_tokens = prompt_details.get("cached_tokens", 0)
                            
                            if input_tokens > 0 or output_tokens > 0:
                                llm_step_count += 1
                                
                                # LLM output - AI mesajının içeriği
                                # GPT-5 reasoning response: content = [{'type': 'reasoning', 'summary': [...]}, {'type': 'text', 'text': '...'}]
                                llm_output_data = None
                                reasoning_summary = None
                                text_content = None
                                
                                if hasattr(message, "content") and message.content:
                                    content = message.content
                                    
                                    # GPT-5 reasoning format: content liste olabilir
                                    if isinstance(content, list):
                                        for item in content:
                                            if isinstance(item, dict):
                                                if item.get("type") == "reasoning":
                                                    # Reasoning summary - düşünce süreci
                                                    reasoning_summary = item.get("summary", [])
                                                elif item.get("type") == "text":
                                                    # Metin cevabı
                                                    text_content = item.get("text", "")
                                    elif isinstance(content, str):
                                        text_content = content
                                    
                                    llm_output_data = {
                                        "text": text_content,
                                        "reasoning_summary": reasoning_summary if reasoning_summary else None,
                                        "tool_calls": [
                                            {"name": tc.get("name", "unknown") if isinstance(tc, dict) else getattr(tc, "name", "unknown"),
                                             "args": tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})}
                                            for tc in (message.tool_calls or [])
                                        ] if hasattr(message, "tool_calls") and message.tool_calls else None
                                    }
                                elif hasattr(message, "tool_calls") and message.tool_calls:
                                    llm_output_data = {
                                        "tool_calls": [
                                            {"name": tc.get("name", "unknown") if isinstance(tc, dict) else getattr(tc, "name", "unknown"),
                                             "args": tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})}
                                            for tc in message.tool_calls
                                        ]
                                    }
                                
                                # 📊 Langfuse için: Tam context oluştur (soru + önceki tool sonuçları)
                                llm_input_data: Dict[str, Any] = {
                                    "question": question,
                                    "step": llm_step_count,
                                }
                                
                                # Tool sonuçları varsa ekle (LLM'in gördüğü context)
                                if accumulated_tool_results:
                                    llm_input_data["previous_tool_results"] = accumulated_tool_results.copy()
                                    llm_input_data["tool_count"] = len(accumulated_tool_results)
                                
                                token_tracker.add_llm_step(
                                    step_name=f"llm_step_{llm_step_count}",
                                    input_tokens=input_tokens,
                                    output_tokens=output_tokens,
                                    cached_tokens=cached_tokens,  # cache_read
                                    cache_creation_tokens=cache_creation_tokens,  # cache_creation
                                    reasoning_tokens=reasoning_tokens,  # 🧠 GPT-5 reasoning
                                    session_id=session_id,
                                    model=self.model_name,  # 📌 Gerçek model adı (claude-opus-4-5, gpt-5, vb.)
                                    llm_input=llm_input_data,
                                    llm_output=llm_output_data,
                                )
                        
                        # Tool calls
                        if hasattr(message, "tool_calls") and message.tool_calls:
                            for tc in message.tool_calls:
                                tool_call_count += 1
                                tool_name = tc.get("name", "unknown") if isinstance(tc, dict) else getattr(tc, "name", "unknown")
                                tool_args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
                                tool_call_id = tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", "")
                                
                                # Token tracker'a ekle (tool_call_id ile - Langfuse span'ı için)
                                token_tracker.add_tool_call(tool_name, tool_args, tool_call_id)
                                
                                # Tool call ID'yi sakla (result için)
                                if tool_call_id:
                                    pending_tool_names[tool_call_id] = tool_name
                                    # 📚 Feedback için tool input'u sakla
                                    pending_tool_inputs[tool_call_id] = {
                                        "tool_name": tool_name,
                                        "tool_input": json.dumps(tool_args, ensure_ascii=False) if tool_args else "",
                                        "start_time": time.time(),
                                    }
                                
                                # Kullanıcıya TEKNIK OLMAYAN, anlaşılır mesaj göster
                                thinking_msg = _get_user_friendly_tool_message(tool_name, tool_args)
                                
                                if thinking_msg:
                                    yield {
                                        "type": "thinking_step",
                                        "message": thinking_msg,
                                        "session_id": session_id,
                                    }
                        
                        # Tool results
                        if msg_type == "ToolMessage":
                            tool_content = getattr(message, "content", "")
                            tool_call_id = getattr(message, "tool_call_id", "")
                            tool_name = pending_tool_names.get(tool_call_id, "unknown")
                            
                            # Durum kontrolü: success (✅), empty (⚪), failed (❌)
                            if "✅" in tool_content:
                                result_status = "success"
                                # 🔍 Hallucination check için başarılı sonuçları topla
                                if GUARDRAILS_HALLUCINATION_CHECK and tool_content:
                                    collected_graph_facts.append(str(tool_content)[:2000])
                            elif "⚪" in tool_content:
                                result_status = "empty"
                            else:
                                result_status = "failed"
                            
                            # Token tracker'a ekle (tool_call_id ile - Langfuse span'ı bulmak için)
                            token_tracker.add_tool_result(tool_name, str(tool_content), result_status == "success", tool_call_id)
                            
                            # 📚 Feedback için tool call'ı kaydet
                            if tool_call_id and tool_call_id in pending_tool_inputs:
                                pending_info = pending_tool_inputs[tool_call_id]
                                duration_ms = int((time.time() - pending_info.get("start_time", time.time())) * 1000)
                                collected_tool_calls.append({
                                    "tool_name": pending_info.get("tool_name", tool_name),
                                    "tool_input": pending_info.get("tool_input", ""),
                                    "tool_output": str(tool_content),
                                    "duration_ms": duration_ms,
                                    "success": result_status == "success",
                                })
                                
                                # 📊 Langfuse için: Tool sonucunu accumulated context'e ekle
                                # Output'u 2000 karakterle sınırla (çok uzun olmasın)
                                output_preview = str(tool_content)[:2000]
                                if len(str(tool_content)) > 2000:
                                    output_preview += f"... ({len(str(tool_content))} karakter)"
                                accumulated_tool_results.append({
                                    "tool": pending_info.get("tool_name", tool_name),
                                    "input": pending_info.get("tool_input", "")[:500],  # Input özeti
                                    "output": output_preview,
                                    "status": result_status,
                                })
                            
                            # Kullanıcıya dostu sonuç mesajı göster
                            result_msg = _get_user_friendly_result_message(tool_content, result_status, session_id)
                            if result_msg:
                                yield {
                                    "type": "thinking_step",
                                    "message": result_msg,
                                    "result_type": result_status,
                                    "session_id": session_id,
                                }
                        
                        # Final content
                        if msg_type == "AIMessage" and hasattr(message, "content") and message.content:
                            has_tool_calls = hasattr(message, "tool_calls") and message.tool_calls
                            if not has_tool_calls:
                                # Bu final cevap
                                new_content = message.content
                                if isinstance(new_content, list):
                                    new_content = " ".join([
                                        c.get("text", "") if isinstance(c, dict) else str(c)
                                        for c in new_content
                                    ])
                                
                                if new_content and new_content != response_text:
                                    delta = new_content[len(response_text):] if len(new_content) > len(response_text) else new_content
                                    response_text = new_content
                                    
                                    if delta.strip():
                                        yield {
                                            "type": "message_chunk",
                                            "content": delta,
                                            "full_message": response_text,
                                            "is_final_answer": True,
                                            "session_id": session_id,
                                        }
            
            # Final mesaj
            if response_text:
                # Kaynakları al
                sources = _get_session_sources(short_question_id)
                
                # 🛡️ Guardrails: Output validation (PII masking)
                if GUARDRAILS_ENABLED:
                    sanitized_response, validation_info = validate_output(
                        response_text,
                        mask_pii_enabled=True,
                        check_cypher=False,  # Cypher zaten tool'da kontrol edildi
                    )
                    if validation_info.get("pii_masked", 0) > 0:
                        _log(f"🔒 PII masked in response: {validation_info['pii_masked']} items")
                    response_text = sanitized_response
                
                # 🔍 Guardrails: Hallucination detection
                hallucination_warning = ""
                if GUARDRAILS_HALLUCINATION_CHECK and collected_graph_facts:
                    try:
                        # Graph facts'ı dict listesine dönüştür
                        graph_facts_list = [{"content": fact} for fact in collected_graph_facts]
                        
                        is_valid, confidence, issues = check_hallucination(
                            response=response_text,
                            graph_facts=graph_facts_list,
                            threshold=0.7,
                        )
                        
                        if not is_valid:
                            _log(f"⚠️ Hallucination risk detected! Confidence: {confidence:.2f}, Issues: {issues}", "warning")
                            
                            # Kullanıcıya uyarı ekle
                            hallucination_warning = "\n\n---\n⚠️ **Doğrulama Notu:** Bu cevapta bazı bilgiler veritabanından tam olarak doğrulanamamıştır. Lütfen kritik kararlar için kaynak belgelerden teyit ediniz."
                            
                            # Langfuse'a logla
                            if langfuse_trace:
                                try:
                                    langfuse_trace.update(metadata={
                                        "hallucination_detected": True,
                                        "hallucination_confidence": confidence,
                                        "hallucination_issues": issues[:3],
                                    })
                                except:
                                    pass
                        else:
                            _log(f"✅ Hallucination check passed (confidence: {confidence:.2f})")
                    except Exception as hallucination_error:
                        _log(f"⚠️ Hallucination check error: {hallucination_error}", "warning")
                
                # Markdown formatında kaynakları cevaba ekle
                final_response = response_text

                # NOT: Ticaret'te kaynak markdown'u content'e EKLENMEZ. Cevap LangGraph'a
                # gider ve panel yapılandırılmış `sources.documents`'tan üretilir; ayrıca
                # eklenen linkler bu kurulumda kırık (localhost) olur. Diğer domain'lerde
                # (sigorta/bakım) eski davranış korunur.
                append_source_markdown = self.domain != "ticaret"

                # Dosya linkleri ekle
                if append_source_markdown and sources["documents"]:
                    file_markdown = _generate_file_links_markdown(sources["documents"])
                    final_response += file_markdown
                    _log(f"📎 {len(sources['documents'])} belge kaynağı eklendi")

                # Sayfa görselleri ekle
                if append_source_markdown and sources["pages"]:
                    page_markdown = _generate_page_links_markdown(sources["pages"])
                    final_response += page_markdown
                    _log(f"🖼️ {len(sources['pages'])} sayfa görseli eklendi")

                # Hallucination uyarısı ekle (her domain'de)
                if hallucination_warning:
                    final_response += hallucination_warning

                # Markdown kaynakları stream et (message_chunk olarak) — ticaret'te yok
                source_markdown = ""
                if append_source_markdown and sources["documents"]:
                    source_markdown += _generate_file_links_markdown(sources["documents"])
                if append_source_markdown and sources["pages"]:
                    source_markdown += _generate_page_links_markdown(sources["pages"])
                if hallucination_warning:
                    source_markdown += hallucination_warning

                if source_markdown:
                    yield {
                        "type": "message_chunk",
                        "content": source_markdown,
                        "full_message": final_response,
                        "is_final_answer": True,
                        "session_id": session_id,
                    }
                
                self._save_to_history(session_id, "AI", final_response)

                # Kaynak payload'u: ticaret → yapılandırılmış belge nesneleri (LangGraph
                # DocFilters + MultimodalDocItem için); diğer domain'ler → eski düz şekil.
                if self.domain == "ticaret":
                    sources_payload: Dict[str, Any] = {
                        "documents": _build_ticaret_documents(sources)
                    }
                else:
                    sources_payload = {
                        "documents": list(sources["documents"]),
                        "pages": list(sources["pages"]),
                    }

                total_time = time.time() - total_start
                
                # İstatistik özetini logla
                token_tracker.print_summary(session_id=session_id)
                token_stats = token_tracker.get_summary()
                
                # 💾 Query cache'e yaz - sonraki benzer sorular için
                try:
                    query_cache = get_query_cache()
                    await query_cache.set(
                        question=question,
                        response=final_response,
                        session_id=session_id,
                        sources=sources_payload,
                        metrics=token_stats,
                    )
                except Exception as cache_write_error:
                    _log(f"⚠️ Cache write error: {cache_write_error}", "warning")
                
                # 🧑‍⚖️ LLM-as-Judge: Background task olarak çalıştır (kullanıcıyı bekletme)
                async def _run_llm_judge_background(
                    bb_dir: str,
                    q: str,
                    resp: str,
                    sess_id: str,
                    q_id: str,
                    lf: Any,
                    lf_trace: Any,
                ):
                    """Background'da LLM-Judge değerlendirmesi yapar."""
                    try:
                        result = await evaluate_from_blackboard(
                            blackboard_dir=bb_dir,
                            question=q,
                            response=resp,
                            session_id=sess_id,
                            question_id=q_id,
                            similarity_threshold=0.95,
                        )
                        
                        if result.get("skipped_duplicate"):
                            _log(f"⏭️ [BG] LLM-Judge: Duplicate skipped")
                        elif result.get("evaluated"):
                            judge_score = result.get("overall_score", 0.5)
                            is_correct = judge_score >= 0.7
                            _log(f"🧑‍⚖️ [BG] LLM-Judge: score={judge_score:.2f}, correct={is_correct}")
                            
                            # Her sorgu için kısa değerlendirme
                            for qe in result.get("query_evaluations", [])[:3]:
                                _log(f"   {qe.get('verdict', '?')} {qe.get('step', '?')}: {qe.get('short_note', '')}")
                            
                            if result.get("strategy"):
                                strategy = result["strategy"]
                                # Strateji dict (yeni format) veya string (eski format) olabilir
                                if isinstance(strategy, dict):
                                    summary = strategy.get("summary", "")
                                    _log(f"   📋 Strateji: {summary[:100]}..." if len(summary) > 100 else f"   📋 Strateji: {summary}")
                                else:
                                    strategy_str = str(strategy)
                                    _log(f"   📋 Strateji: {strategy_str[:100]}..." if len(strategy_str) > 100 else f"   📋 Strateji: {strategy_str}")
                            
                            if result.get("feedback_id"):
                                _log(f"   📝 Feedback: {result['feedback_id'][:8]}...")
                            
                            # Langfuse'a score kaydet
                            if lf and lf_trace:
                                try:
                                    trace_id = lf_trace.id if hasattr(lf_trace, 'id') else None
                                    if trace_id:
                                        try:
                                            lf.create_score(  # type: ignore[union-attr]
                                                trace_id=trace_id,
                                                name="llm_judge_score",
                                                value=judge_score,
                                                comment=result.get("strategy", "")[:500],
                                                data_type="NUMERIC",
                                            )
                                        except AttributeError:
                                            lf.score(  # type: ignore[union-attr]
                                                trace_id=trace_id,
                                                name="llm_judge_score",
                                                value=judge_score,
                                                comment=result.get("strategy", "")[:500],
                                            )
                                except Exception:
                                    pass
                    except Exception as e:
                        _log(f"⚠️ [BG] LLM-Judge error: {e}", "warning")
                
                # Background task başlat (kullanıcıyı bekletmez)
                if FEEDBACK_ENABLED and LLM_JUDGE_ENABLED and collected_tool_calls:
                    blackboard_dir = get_blackboard_dir(session_id, original_question_id)
                    asyncio.create_task(
                        _run_llm_judge_background(
                            bb_dir=blackboard_dir,
                            q=question,
                            resp=final_response,
                            sess_id=session_id,
                            q_id=original_question_id,
                            lf=langfuse,
                            lf_trace=langfuse_trace,
                        )
                    )
                    _log(f"🚀 LLM-Judge started in background")
                
                # 📊 Langfuse span sonlandır
                cache_stats = get_cache_metrics().to_dict()
                if langfuse_trace:
                    try:
                        # Langfuse SDK: span.update() ile output ekle, sonra end()
                        langfuse_trace.update(
                            output={"response": final_response},
                            metadata={
                                "total_time_seconds": round(total_time, 2),
                                "tool_calls": tool_call_count,
                                "llm_calls": token_stats["total_llm_calls"],
                                "total_tokens": token_stats["total_tokens"],
                                "input_tokens": token_stats["total_input_tokens"],
                                "output_tokens": token_stats["total_output_tokens"],
                                "cached_tokens": token_stats["total_cached_tokens"],
                                "cache_hit_rate": token_stats["cache_hit_rate"],
                                "estimated_cost_usd": token_stats["estimated_cost_usd"],
                                "sources_count": len(sources["documents"]) + len(sources["pages"]),
                                "query_cache_hit_rate": cache_stats.get("hit_rate_percent", 0),
                                "llm_judge": "background",  # LLM-Judge runs in background
                            }
                        )
                        # Span'ı kapat
                        langfuse_trace.end()
                        # Langfuse async flush
                        flush_langfuse()
                        _log(f"📊 Langfuse span completed: {tool_call_count} tool calls logged")
                    except Exception as e:
                        _log(f"⚠️ Langfuse span update failed: {e}", "warning")
                
                yield {
                    "type": "final_response",
                    "content": final_response,
                    "sources": sources_payload,
                    "metrics": {
                        "total_time": round(total_time, 2),
                        "tool_calls": tool_call_count,
                        "llm_calls": token_stats["total_llm_calls"],
                        "input_tokens": token_stats["total_input_tokens"],
                        "output_tokens": token_stats["total_output_tokens"],
                        "cached_tokens": token_stats["total_cached_tokens"],
                        "total_tokens": token_stats["total_tokens"],
                        "cache_hit_rate": token_stats["cache_hit_rate"],
                        "estimated_cost_usd": token_stats["estimated_cost_usd"],
                        "hallucination_warning": bool(hallucination_warning),
                        "redis_cache_active": self.redis_cache_active,
                        "few_shot_examples_used": len(few_shot_examples) if 'few_shot_examples' in dir() else 0,
                        "llm_judge": "background",  # LLM-Judge runs in background
                    },
                    # 📚 Tool calls for feedback learning
                    "tool_calls_detail": collected_tool_calls,
                    "session_id": session_id,
                    "timestamp": datetime.now().isoformat(),
                }
            else:
                # Cevap oluşturulamadı - yine de istatistikleri göster
                token_tracker.print_summary(session_id=session_id)
                yield {
                    "type": "error",
                    "message": "Cevap oluşturulamadı.",
                    "session_id": session_id,
                }
                
        except Exception as e:
            _log(f"❌ Stream error: {e}", "error")
            import traceback
            traceback.print_exc()
            
            # Hata durumunda da istatistikleri göster
            token_tracker.print_summary(session_id=session_id)
            
            # 📊 Langfuse span hata ile sonlandır
            if langfuse_trace:
                try:
                    # Langfuse SDK: span.update() ile hata bilgisi ekle, sonra end()
                    langfuse_trace.update(
                        output={"error": str(e)},
                        metadata={
                            "error": True,
                            "error_message": str(e)[:500],
                            "error_type": type(e).__name__,
                        },
                        level="ERROR",
                    )
                    langfuse_trace.end()
                    flush_langfuse()
                except Exception as lf_error:
                    _log(f"⚠️ Langfuse error span failed: {lf_error}", "warning")
            
            yield {
                "type": "error",
                "message": f"Bir hata oluştu: {str(e)}",
                "session_id": session_id,
            }
        finally:
            # Langfuse session context'i kapat
            if langfuse_session_ctx:
                try:
                    langfuse_session_ctx.__exit__(None, None, None)
                    _log(f"📊 Langfuse session ended: {session_id[:8] if session_id else 'N/A'}")
                except Exception as e:
                    _log(f"⚠️ Langfuse session end failed: {e}", "warning")
            
            # Logging context'i temizle
            clear_request_context()
    
    async def close(self):
        """Kaynakları temizle"""
        # langchain-mcp-adapters 0.1.0+ artık context manager kullanmıyor
        # Sadece referansları temizle
        self.mcp_client = None
        self.mcp_tools = None


# ============================================================================
# SESSION MANAGEMENT - Session bazlı agent cache
# ============================================================================

_react_session_agents: Dict[str, ReactAgent] = {}
_react_session_access_times: Dict[str, datetime] = {}

# Cache limitleri
REACT_SESSION_MAX_COUNT = int(os.environ.get("REACT_SESSION_MAX_COUNT", "50"))
REACT_SESSION_MAX_AGE_HOURS = float(os.environ.get("REACT_SESSION_MAX_AGE_HOURS", "2.0"))


def _cleanup_old_react_sessions():
    """Eski session'ları temizle"""
    global _react_session_agents, _react_session_access_times
    
    now = datetime.now()
    max_age = REACT_SESSION_MAX_AGE_HOURS * 3600  # saniye
    
    sessions_to_remove = []
    for session_id, access_time in _react_session_access_times.items():
        age = (now - access_time).total_seconds()
        if age > max_age:
            sessions_to_remove.append(session_id)
    
    # LRU: Limit aşılırsa en eski session'ları da temizle
    while len(_react_session_agents) - len(sessions_to_remove) > REACT_SESSION_MAX_COUNT:
        if not _react_session_access_times:
            break
        oldest_session = min(_react_session_access_times.keys(), key=lambda k: _react_session_access_times[k])
        if oldest_session not in sessions_to_remove:
            sessions_to_remove.append(oldest_session)
    
    for session_id in sessions_to_remove:
        if session_id in _react_session_agents:
            del _react_session_agents[session_id]
        if session_id in _react_session_access_times:
            del _react_session_access_times[session_id]
    
    if sessions_to_remove:
        _log(f"React cache cleanup: {len(sessions_to_remove)} sessions removed")


def clear_react_session_agent(session_id: str):
    """Belirli bir session'ın agent'ını temizle"""
    global _react_session_agents, _react_session_access_times
    
    if session_id in _react_session_agents:
        del _react_session_agents[session_id]
    if session_id in _react_session_access_times:
        del _react_session_access_times[session_id]
    
    _log(f"React session {session_id[:8]} cleared")


async def get_or_create_react_session_agent(
    session_id: str,
    model: Optional[str] = None,
    graph: Any = None,
    reasoning_effort: Optional[str] = None,
    domain: Optional[str] = None,
) -> ReactAgent:
    """
    Session bazlı ReactAgent al veya oluştur.
    
    Her session için tek agent instance tutulur - MCP bağlantısı reuse edilir.
    
    Args:
        session_id: Session identifier
        model: LLM model adı
        graph: Neo4j graph connection
        reasoning_effort: GPT-5 reasoning effort
        domain: Prompt domain'i (ZORUNLU). Geçerli değerler: sigorta, bakim
    
    Returns:
        ReactAgent instance
    
    Raises:
        ValueError: domain parametresi belirtilmemişse veya geçersizse
    """
    global _react_session_agents, _react_session_access_times
    
    # Cleanup
    _cleanup_old_react_sessions()
    
    # Session key: session_id + domain (farklı domain'ler farklı agent'lar)
    cache_key = f"{session_id}:{domain}"
    
    # Mevcut agent varsa döndür
    if cache_key in _react_session_agents:
        _react_session_access_times[cache_key] = datetime.now()
        _log(f"React session {session_id[:8]} (domain={domain}): reusing cached agent")
        return _react_session_agents[cache_key]
    
    # Yeni agent oluştur (domain validation constructor'da yapılır)
    agent = ReactAgent(
        graph=graph,
        model_name=model,
        reasoning_effort=reasoning_effort,
        domain=domain,
    )
    
    _react_session_agents[cache_key] = agent
    _react_session_access_times[cache_key] = datetime.now()
    
    _log(f"React session {session_id[:8]} (domain={domain}): new agent created (cache={len(_react_session_agents)})")
    return agent


def get_react_session_stats() -> Dict[str, Any]:
    """Session cache istatistikleri"""
    return {
        "total_sessions": len(_react_session_agents),
        "max_sessions": REACT_SESSION_MAX_COUNT,
        "max_age_hours": REACT_SESSION_MAX_AGE_HOURS,
        "sessions": list(_react_session_agents.keys())[:10],
    }


# ============================================================================
# MAIN STREAMING FUNCTION
# ============================================================================

async def stream_react_agent_response(
    question: str,
    model: Optional[str] = None,
    session_id: str = "",
    question_id: str = "",
    graph: Any = None,
    reasoning_effort: Optional[str] = None,
    user_id: Optional[str] = None,  # Kullanıcı ID (email veya unique ID)
    domain: Optional[str] = None,  # Prompt domain'i (ZORUNLU)
    **kwargs: Any,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    ReAct Agent ile streaming cevap üret.
    
    Multi-provider desteği: OpenAI (GPT-5, GPT-4o) ve Anthropic (Claude Opus 4.5, Sonnet)
    
    Args:
        question: Kullanıcının sorusu
        model: LLM modeli. Desteklenen modeller:
            - OpenAI: gpt-5, gpt-4o, o1-preview, o3-mini vb.
            - Anthropic: claude-opus-4-5, claude-sonnet-4-5, claude-3-5-sonnet vb.
            Default: env REACT_MODEL veya gpt-5
        session_id: Oturum ID'si (ZORUNLU)
        question_id: Soru ID'si
        graph: Neo4j graph connection
        reasoning_effort: GPT-5 için reasoning seviyesi (none, low, medium, high)
            Claude için REACT_THINKING_BUDGET env variable kullanılır
        user_id: Kullanıcı ID (email veya unique identifier) - Langfuse User Tracking için
        domain: Prompt domain'i (ZORUNLU). Geçerli değerler:
            - "sigorta": Sigorta poliçeleri, müşteriler, teminatlar
            - "bakim": WAT Motor bakım/arıza yönetimi (CMMS)
        **kwargs: Ek parametreler
    
    Environment Variables:
        REACT_MODEL: Model seçimi (gpt-5, claude-opus-4-5 vb.)
        REACT_REASONING_EFFORT: GPT-5 reasoning seviyesi (low, medium, high)
        REACT_THINKING_BUDGET: Claude extended thinking token bütçesi (default: 10000)
        OPENAI_API_KEY: OpenAI API key
        ANTHROPIC_API_KEY: Anthropic API key
    
    Yields:
        Dict: Streaming chunk'ları
    
    Raises:
        ValueError: domain parametresi belirtilmemişse veya geçersizse
    """
    if not LANGCHAIN_AVAILABLE:
        yield {
            "type": "error",
            "message": "LangChain kurulu değil.",
            "status": "not_available",
            "session_id": session_id,
            "timestamp": datetime.now().isoformat(),
        }
        return
    
    if not session_id:
        yield {
            "type": "error",
            "message": "Session ID gerekli.",
            "status": "missing_session",
            "timestamp": datetime.now().isoformat(),
        }
        return
    
    # Domain validation - hatayı yield ile döndür
    if not domain:
        yield {
            "type": "error",
            "message": f"domain parametresi zorunlu. Geçerli değerler: {ReactAgent.VALID_DOMAINS}",
            "status": "missing_domain",
            "timestamp": datetime.now().isoformat(),
        }
        return
    
    if domain not in ReactAgent.VALID_DOMAINS:
        yield {
            "type": "error",
            "message": f"Geçersiz domain: '{domain}'. Geçerli değerler: {ReactAgent.VALID_DOMAINS}",
            "status": "invalid_domain",
            "timestamp": datetime.now().isoformat(),
        }
        return
    
    try:
        # Session bazlı agent al veya oluştur
        agent = await get_or_create_react_session_agent(
            session_id, model, graph, reasoning_effort, domain
        )
        
        async for chunk in agent.stream_query_response(
            question=question,
            session_id=session_id,
            question_id=question_id,
            user_id=user_id,  # Langfuse User Tracking için
            **kwargs
        ):
            yield chunk
    
    except Exception as e:
        logging.error(f"ReAct Agent streaming failed: {e}", exc_info=True)
        yield {
            "type": "error",
            "message": f"ReAct Agent hatası: {str(e)}",
            "status": "failed",
            "session_id": session_id,
            "timestamp": datetime.now().isoformat(),
        }


# ============================================================================
# FACTORY FUNCTION
# ============================================================================

def create_react_agent(graph, **kwargs) -> ReactAgent:
    """
    ReactAgent factory function.
    
    Args:
        graph: Neo4j graph connection
        **kwargs: ReactAgent constructor arguments
    
    Returns:
        ReactAgent instance
    """
    return ReactAgent(graph, **kwargs)

