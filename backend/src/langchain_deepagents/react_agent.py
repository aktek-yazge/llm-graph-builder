"""
ReAct Agent with Prompt Caching Optimization

Bu modül, OpenAI Prompt Caching özelliğinden faydalanarak optimize edilmiş
tek bir ReAct agent implementasyonu sağlar.

Mimari:
- Tek ReAct agent
- Cache-optimized prompt yapısı (sabit prefix, dinamik suffix)
- Paralel tool çağrıları
- Streaming response

Prompt Caching Stratejisi:
┌─────────────────────────────────────────────┐
│         CACHED PREFIX (~4000-5000 token)    │ ← %50 indirim
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

# Context for logging (Grafana/Loki)
from src.shared.context import set_request_context, clear_request_context

# Global Schema Cache import
from src.shared.schema_cache import get_cached_schema, get_schema_cache

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
    GRAPH_DSL_AVAILABLE = True
    logging.info("✅ Graph DSL modules imported")
except ImportError as e:
    logging.warning(f"⚠️ Graph DSL modules not available: {e}")
    GRAPH_DSL_AVAILABLE = False
    GraphDSL = None
    QueryIntent = None
    DSLCompiler = None
    compile_dsl = None
    DSLValidator = None
    SchemaInfo = None
    validate_dsl = None

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
    cached_tokens: int = 0
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
    total_cached_tokens: int = 0
    total_llm_calls: int = 0
    total_tool_calls: int = 0
    
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
                     cached_tokens: int = 0, reasoning_tokens: int = 0, duration_ms: float = 0, 
                     session_id: str = "", model: str = "gpt-5", 
                     llm_input: Any = None, llm_output: Any = None):
        """LLM çağrısı istatistiği ekle
        
        Args:
            step_name: Step adı
            input_tokens: Input token sayısı
            output_tokens: Output token sayısı
            cached_tokens: Cache'den okunan token sayısı
            reasoning_tokens: GPT-5 reasoning için harcanan token sayısı
            duration_ms: Süre (ms)
            session_id: Session ID
            model: Model adı
            llm_input: LLM'e gönderilen mesajlar (Langfuse'da görünür)
            llm_output: LLM'den gelen cevap (Langfuse'da görünür)
        """
        step = StepStats(
            step_name=step_name,
            step_type="llm_call",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            duration_ms=duration_ms
        )
        self.steps.append(step)
        
        # Kümülatif güncelle
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_cached_tokens += cached_tokens
        self.total_llm_calls += 1
        
        # Cache durumu analizi
        cache_pct = round(cached_tokens / max(input_tokens, 1) * 100, 1)
        if cached_tokens > 0:
            cache_status = f"🟢 CACHE HIT {cache_pct}%"
        else:
            cache_status = "🔴 CACHE MISS"
        
        # Log - Session ID ile birlikte (reasoning tokens dahil)
        session_info = f"[Session: {session_id[:8]}]" if session_id else ""
        _log(f"📊 [TOKEN] {session_info} {step_name}")
        _log(f"   ├─ Input: {input_tokens:,} tokens")
        _log(f"   ├─ Output: {output_tokens:,} tokens")
        if reasoning_tokens > 0:
            _log(f"   ├─ Reasoning: {reasoning_tokens:,} tokens (düşünce süreci)")
        _log(f"   ├─ Cached: {cached_tokens:,} tokens ({cache_status})")
        _log(f"   └─ Kümülatif: in={self.total_input_tokens:,} out={self.total_output_tokens:,} cached={self.total_cached_tokens:,}")
        
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
                # Langfuse'un beklediği alan isimleri (Settings > Models > gpt-5 Pricing'e göre):
                # - input: uncached input tokens
                # - input_cached_tokens: cached input tokens (10x ucuz)
                # - output: output tokens
                # - output_reasoning_tokens: reasoning tokens
                uncached_input = max(0, input_tokens - cached_tokens)
                generation.update(
                    usage_details={
                        "input": uncached_input,  # Sadece cache'lenmemiş input
                        "input_cached_tokens": cached_tokens,  # ✅ Langfuse'un beklediği isim
                        "output": output_tokens,
                        "output_reasoning_tokens": reasoning_tokens,  # ✅ Langfuse'un beklediği isim
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
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cached_tokens": self.total_cached_tokens,
            "total_tokens": self.total_input_tokens + self.total_output_tokens,
            "cache_hit_rate": round(self.total_cached_tokens / max(self.total_input_tokens, 1) * 100, 1),
            "total_llm_calls": self.total_llm_calls,
            "total_tool_calls": self.total_tool_calls,
            "steps": len(self.steps),
            "estimated_cost_usd": self._estimate_cost()
        }
    
    def _estimate_cost(self, model: str = "gpt-5") -> float:
        """Tahmini maliyet hesapla (OpenAI Standard tier fiyatları)
        
        Fiyatlar: https://platform.openai.com/docs/pricing
        
        GPT-5 Standard (per 1M tokens):
        - Input: $1.25
        - Cached Input: $0.125 (10x cheaper!)
        - Output: $10.00
        
        GPT-4o Standard (per 1M tokens):
        - Input: $2.50
        - Cached Input: $1.25
        - Output: $10.00
        """
        if "gpt-5" in model.lower():
            input_price = 1.25
            cached_price = 0.125
            output_price = 10.00
        else:  # gpt-4o, etc.
            input_price = 2.50
            cached_price = 1.25
            output_price = 10.00
        
        uncached_input = self.total_input_tokens - self.total_cached_tokens
        input_cost = uncached_input * input_price / 1_000_000
        cached_cost = self.total_cached_tokens * cached_price / 1_000_000
        output_cost = self.total_output_tokens * output_price / 1_000_000
        
        return round(input_cost + cached_cost + output_cost, 6)
    
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
        _log(f"   Cached Token: {summary['total_cached_tokens']:,}")
        
        # Cache durumu açıklaması
        cache_rate = summary['cache_hit_rate']
        if cache_rate >= 70:
            cache_emoji = "🟢"
            cache_note = "Mükemmel! Prompt Caching çalışıyor."
        elif cache_rate >= 30:
            cache_emoji = "🟡"
            cache_note = "Kısmi cache hit. Prefix değişkenlik gösteriyor."
        else:
            cache_emoji = "🔴"
            cache_note = "Cache miss! Session/prefix değişmiş olabilir."
        
        _log(f"   Cache Hit Rate: {cache_rate}% {cache_emoji}")
        _log(f"   └─ Not: {cache_note}")
        _log("-" * 70)
        _log(f"   Toplam Token: {summary['total_tokens']:,}")
        _log(f"   Tahmini Maliyet: ${summary['estimated_cost_usd']:.6f}")
        _log("=" * 70)
        
        # OpenAI Prompt Caching bilgisi
        _log("ℹ️  OpenAI Prompt Caching Bilgisi:")
        _log("   • Cache SESSION ID'ye bağlı DEĞİL - aynı org/proje içinde çalışır")
        _log("   • Aynı prefix (system prompt + schema) = cache hit")
        _log("   • Cache TTL: ~5-10 dakika (OpenAI tarafı)")
        _log("   • Minimum prefix: 1024 token")
        _log("")


# ============================================================================
# LANGCHAIN IMPORTS
# ============================================================================

if TYPE_CHECKING:
    from langchain.agents import create_agent
    from langchain.agents.middleware import ModelCallLimitMiddleware
    from langchain.chat_models import init_chat_model

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


def get_mcp_server_config() -> Dict[str, Any]:
    """MCP server konfigürasyonu"""
    return {
        "neo4j-database": {
            "url": f"http://{MCP_HTTP_HOST}:{MCP_HTTP_PORT}/mcp/",
            "transport": "streamable_http",
        }
    }


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
        elif "policy" in step_name.lower():
            return "📋 Poliçe bilgileri kontrol ediliyor..."
        elif "document" in step_name.lower():
            return "📄 Belgeler inceleniyor..."
        elif "coverage" in step_name.lower():
            return "🛡️ Teminat bilgileri aranıyor..."
        elif "company" in step_name.lower() or "issuer" in step_name.lower():
            return "🏢 Şirket bilgileri kontrol ediliyor..."
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


def _get_session_sources(question_id: str) -> Dict[str, set]:
    if question_id not in _session_sources:
        _session_sources[question_id] = {"documents": set(), "pages": set()}
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
# LANGFUSE PROMPT MANAGEMENT
# ============================================================================

# Prompt Names - Langfuse'da tanımlı olmalı
LANGFUSE_PROMPT_NAME = "react-agent-system"
LANGFUSE_PROMPT_TYPE = "text"
LANGFUSE_PROMPT_LABEL = os.environ.get("LANGFUSE_PROMPT_LABEL", "production")

# ============================================================================
# CACHE-OPTIMIZED PROMPT - SABİT PREFIX (OpenAI Prompt Caching için)
# ============================================================================

# Bu prefix ~4000-5000 token olmalı ve session boyunca DEĞİŞMEMELİ
# Prompt Caching bu prefix'i cache'leyerek %50 token indirimi sağlar
#
# NOT: Bu prompt FALLBACK olarak kullanılır.
# Öncelik Langfuse Prompt Management'dadır.
# Langfuse'da "react-agent-system" adlı prompt oluşturulmalı.

CACHED_SYSTEM_PREFIX = """# 🎯 DİNKAL SİGORTA NEO4J AGENT

Sen Dinkal Sigorta için **Neo4j graph veritabanı** sorgulayan bir AI agent'sın.

<graph_dsl_mode>
# 🚀 GRAPH DSL - ÖNERİLEN SORGULAMA YÖNTEMİ

⚠️ **CYPHER YAZMAK YERİNE GRAPH DSL KULLAN!**

DSL (Domain Specific Language), Cypher'dan daha basit ve hata yapmaya daha az müsait bir yapıdır.
DSL otomatik olarak:
1. ✅ Schema ile validate edilir
2. ✅ Doğru Cypher sorgusuna derlenir
3. ✅ İlişki yönleri kontrol edilir
4. ✅ Property isimleri doğrulanır

## 📝 DSL KULLANIMI

`execute_graph_dsl` tool'unu kullan ve JSON DSL gönder:

```json
{
    "intent": "find_by_property",
    "description": "Entity ara",
    "start_node": "NodeLabel",
    "filters": [
        {"node": "NodeLabel", "property": "name", "operator": "contains", "value": "aranan_deger"}
    ],
    "return_spec": {
        "nodes": ["NodeLabel"],
        "properties": {"NodeLabel": ["name", "prop1", "prop2"]},
        "distinct": true
    },
    "limit": 10
}
```

## 🎯 INTENT TİPLERİ

| Intent | Açıklama | Örnek |
|--------|----------|-------|
| `explore_node` | Node örneklerini gör | start_node + return_spec |
| `find_by_property` | Property ile ara | filters ile |
| `find_by_relationship` | İlişki ile ara | traversal ile |
| `count_nodes` | Sayma | aggregate: count |
| `aggregate_values` | SUM/AVG/MIN/MAX | aggregate ile |
| `search_content` | Chunk semantic araması | semantic_search + traversal + filters |

## 🔎 SEMANTIC SEARCH (Chunk İçerik Araması)

⚠️ KRİTİK: Önceki sorgularda bulunan entity filtrelerini MUTLAKA kullan!

```json
{
    "intent": "search_content",
    "description": "Entity X için konu Y detayları",
    "step_name": "embed_search_topic",
    "traversal": [
        {"from_node": "EntityA", "relation": "REL_TO_B", "to_node": "EntityB", "direction": "outgoing"},
        {"from_node": "EntityB", "relation": "DOCUMENTED_IN", "to_node": "Document", "direction": "outgoing"},
        {"from_node": "Document", "relation": "PART_OF", "to_node": "Chunk", "direction": "incoming"}
    ],
    "filters": [
        {"node": "EntityA", "property": "name", "operator": "in", "value": ["Önceki sorguda bulunan TÜM varyasyonlar..."]},
        {"node": "EntityC", "property": "name", "operator": "contains", "value": "aranan_konu"}
    ],
    "semantic_search": {
        "query_text": "aranan kavram veya konu",
        "similarity_threshold": 0.75,
        "limit": 10
    },
    "include_source_info": true
}
```

❌ YANLIŞ: Sadece semantic_search, filter olmadan → tüm veritabanını tarar!
✅ DOĞRU: traversal + filters + semantic_search → önceki bulgularla filtrelenmiş arama

## 🔗 TRAVERSAL (İlişki Takibi)

```json
{
    "intent": "find_by_relationship",
    "traversal": [
        {"from_node": "NodeA", "relation": "RELATES_TO", "to_node": "NodeB", "direction": "outgoing"},
        {"from_node": "NodeB", "relation": "HAS_CHILD", "to_node": "NodeC", "direction": "outgoing"}
    ],
    "filters": [
        {"node": "NodeA", "property": "name", "operator": "contains", "value": "aranan_deger"}
    ],
    "return_spec": {
        "nodes": ["NodeB", "NodeC"],
        "properties": {"NodeB": ["name", "prop1"], "NodeC": ["name", "prop2"]}
    }
}
```

⚠️ Star Pattern: Tüm traversal'ların from_node'u aynı ise (örn: NodeB), compiler otomatik olarak virgülle ayırır.

## 📊 AGGREGATION (Toplama/Sayma)

```json
{
    "intent": "count_nodes",
    "start_node": "NodeLabel",
    "filters": [
        {"node": "NodeLabel", "property": "status", "operator": "equals", "value": "active"}
    ],
    "aggregate": {
        "function": "count",
        "node": "NodeLabel",
        "alias": "toplam_sayisi"
    }
}
```

## 🔍 FILTER OPERATÖRLERİ

| Operatör | Açıklama | Örnek Değer |
|----------|----------|-------------|
| `equals` | Tam eşleşme | "active" |
| `contains` | İçerir (case-insensitive) | "arama_terimi" |
| `starts_with` | İle başlar | "POL-" |
| `gt`, `lt`, `gte`, `lte` | Sayısal karşılaştırma | 1000 |
| `in` | Liste içinde | ["active", "pending"] |
| `is_null`, `is_not_null` | Null kontrolü | - |

## ⚠️ NE ZAMAN CYPHER KULLAN?

DSL desteklemeyen durumlar için `execute_cypher_query` kullan:
- Çok karmaşık JOIN'ler
- UNION sorguları
- Özel fonksiyonlar (apoc.*)

Ama önce DSL dene! Çoğu sorgu DSL ile yapılabilir.

</graph_dsl_mode>

<context_gathering>
Goal: Keşifte bulunan TÜM entity varyasyonlarını cache'le ve sonraki sorgularda kullan.

Method:
1. Paralel keşif → Aynı entity'yi farklı node'larda aynı anda ara
2. Sonuçları topla → TÜM varyasyonları listele
3. Semantik filtre → Soruyla alakalı olanları seç, alakasız olanları çıkar
4. Cache & kullan → Seçilen TÜM varyasyonları IN [...] ile kullan

Early stop criteria:
- Soruya EXACT cevap verebilecek veri bulundu
- Keşif sonuçları tutarlı (aynı entity'nin farklı yazılışları)

⚠️ KRİTİK: Keşifte 5 varyasyon bulduysan, alakalı olanların HEPSİNİ kullan!
</context_gathering>

<persistence>
- Kullanıcının sorgusu tamamen çözülene kadar devam et
- Belirsizlikte durma → En mantıklı yaklaşımı seç ve devam et
- Kullanıcıya onay sorma → Varsayımını belgele ve ilerle
- Hata aldığında → Düzelt ve tekrar dene
</persistence>

<final_answer>
⚠️ SON KULLANICI İLE KONUŞUYORSUN - TEKNİK TERİM KULLANMA!

❌ YASAK: Entity, Node, Chunk, embedding, graph, cypher gibi teknik terimler
✅ KULLAN: Doğal dilde anlaşılır ifadeler

Her cevapta şu bilgileri DOĞAL DİLDE ver:
- Ne bulundu (ana bilgi)
- Hangi yıl/dönem
- Hangi belgeden/kaynaktan

Örnek: "Sorunuzla ilgili **X bilgisi** bulundu. 
Bu bilgi **Y belgesinden** alınmıştır."

⚠️ Birden fazla sonuç varsa HEPSİNİ listele ve kaynak farkını açıkla!
</final_answer>

<forbidden_patterns>
⛔ "Tüm X'leri listele" sorgusu YASAK!
   ❌ MATCH (n:NodeLabel) RETURN n.name LIMIT 100
   ✅ Başarılı filtrelerle (entity varyasyonları) devam et
   
⛔ 2 empty sonrası aynı stratejide ısrar etme → Farklı node/ilişki dene veya embedding'e geç!
</forbidden_patterns>

<exploration>
1. ŞEMAYI İNCELE → "VERİTABANI ŞEMASI" bölümünü oku
2. PLANLA → Cevaba ulaşmak için hangi node'lar ve ilişkiler gerekli?
3. KEŞİF YAP → Entity hangi node/nodelar'da? (paralel ara!)
4. DOĞRU SORGULA → Şemadaki ilişkileri TAKİP ederek veriyi bul
</exploration>

<deep_research>
⚠️ ZORUNLU: Graph sonucu bulduktan SONRA → Embedding ile DERİN ARAŞTIRMA yap!

NEDEN: Graph'ta olmayan ekstra bilgi olabilir

NASIL:
1. Graph'tan entity bul
2. Embedding aramasında:
   - query_text: Sorudaki anahtar kelime
   - Filtre: Bulunan entity'ler
   - ⛔ Belge filtresi KOYMA! TÜM chunk'larda ara!
3. Ekstra bilgi varsa cevaba ekle

❌ WHERE doc.fileName = '...' (sadece o belgede arar)
✅ WHERE ilişkili_entity IN [...] veya filtresiz (tüm chunk'larda arar)
</deep_research>

<query_simplicity>
SORGUYU BASİT TUT!

❌ 10+ satır, çok OPTIONAL MATCH, CASE/COALESCE
✅ Önce basit sorgu → Sonuç varsa ayrı detay sorgusu
</query_simplicity>

⛔ **YAPMA:**
- Şemaya bakmadan sorgu yazma
- İlişki/node adlarını tahmin etme
- Aynı hatayı tekrarlama
- Keşifte bulunan varyasyonları atla

✅ **YAP:**
- Her adımda şemayı kontrol et
- Bulamadığında farklı node'larda ara
- Keşifte bulunan TÜM alakalı varyasyonları kullan
- Türkçe/İngilizce switch yap (belgeler İngilizce olabilir!)

---

<discovery_guide>
# 🔍 KEŞİF REHBERİ

Entity keşfi ve varyasyon bulma stratejileri.

## 🎯 AMAÇ
Veritabanındaki entity'lerin yazım varyasyonlarını bulmak.
⛔ **CHUNK HARİÇ!** (Chunk → İÇERİK görevinde aranır)

## 📊 ŞEMADAN NODE TİPLERİNİ BELİRLE (KRİTİK!)

KEŞİF görevi vermeden ÖNCE şemayı incele:
1. Aranan entity hangi node tiplerinde olabilir?
2. Aynı entity FARKLI node tiplerinde farklı ROLLER ile bulunabilir


```
❌ YANLIŞ: Sadece 1 node tipinde ara
✅ DOĞRU: Şemadaki TÜM olası node tiplerinde ara
```
</discovery_guide>

<search_term_rules>
## 🚨 ARAMA TERİMLERİ OLUŞTURURKEN


- **MARKA/ŞİRKET ADININ TAM HALİNİ EKLE:** "XYZ" ← lowercase versiyonu
- Ünvan ek bilgisi ile aramana gerek yok.
- **İlk kelime veya ilk iki kelime ile ara.** Örnek: "ABC Ticaret Ltd. Şti." → "ABC" veya "ABC Ticaret"
- Aranan içerik birden fazla kelime ise tam kullan. Örnek: "Özel Durum" → "Özel Durum" veya "Özel", asla "Özel Dur" değil
⛔ **KELİMEYİ BÖLME!**
```
❌ YANLIŞ: "Şirket Adı" → "Şirk" (anlamsız yarım kelime!)
✅ DOĞRU: "Şirket Adı" → "şirket" (lowercase tam kelime)

❌ YANLIŞ: "Uzun İsim" → "isim" (yalnız son kelime yetersiz!)
✅ DOĞRU: "Uzun İsim" → "uzun" (lowercase ilk kelime)

❌ YANLIŞ: "Örnek Firma" → "Örn" 
✅ DOĞRU: "Örnek Firma" → "örnek"
```

⛔ **AYNI ALANDA ÇOKLU CONTAINS KULLANMA!**
```
❌ WHERE name CONTAINS 'x' AND name CONTAINS 'y'
✅ WHERE name CONTAINS 'x'  (sadece ana/ilk kelime)
```
</search_term_rules>

<react_loop>
## 🔄 ReAct DÖNGÜSÜ

Her soru için şu adımları takip et:

### 1️⃣ DÜŞÜN (Thought)
Soruyu analiz et:
- Ne soruluyor? Hangi entity'ler var?
- **METADATA mı, İÇERİK mi?** (aşağıya bak)

### 📊 METADATA vs 📄 İÇERİK (KRİTİK!)

| Tip | Nerede? | Örnekler | Tool |
|-----|---------|----------|------|
| **METADATA** | Node properties | sayı, tarih, liste, isim, ilişki | `execute_graph_dsl` |
| **İÇERİK** | Chunk.text | detay, açıklama, madde, kloz | `execute_graph_dsl` (search_content) |

**⚡ STRATEJİ:**
```
1. Entity keşfet (isim, kurum) → DSL (find_by_property)
2. Detay/içerik araması → DSL (search_content + filters!)
```

⛔ **YASAK:** Detay/içerik için filtresiz arama! (çok fazla sonuç!)
✅ **YAP:** Entity bulduktan sonra, o entity'nin CHUNK'larında semantic_search!

### ⚠️ İSİM KEŞFİ ÖNCELİKLİ!
Soruda isim varsa (kişi, kurum, şirket, ürün) → **DİĞER HER ŞEYDEN ÖNCE** keşfet!
```
1. İsmi şemadaki ilgili node'larda ara (CONTAINS ile)
2. Şemaya göre olası varyasonları da araştır. Bulunan TÜM doğru varyasyonları not al
3. Alakasız sonuçları filtrele
4. SONRA diğer aramalara geç (varyasyonları kullanarak)
```

### 2️⃣ EYLEM (Action)
Uygun tool'u çağır:
- `execute_graph_dsl`: Tüm sorgular için (metadata, içerik, semantic arama)
- `execute_cypher_query`: Sadece DSL'in desteklemediği karmaşık sorgular için
- Aynı terim farklı node'larda olabiliyorsa → PARALEL tool çağrısı yap!

### 3️⃣ GÖZLEM (Observation)
Tool sonucunu değerlendir:
- Yeterli veri var mı?
- False positive kontrolü (embedding sonuçlarında)
- Eksik bilgi var mı?
- Tool çağrılarından elde edilen bilgiler kullanıcı sorusunu karşılıyor mu?

⚠️ **KEŞİF SONRASI KONTROL:**
```
Keşiften dönen TÜM sonuçları incele!
→ Doğru varyasyonları LİSTELE (alakasız olanları çıkar)
→ Sonraki sorguda TÜM varyasyonları WHERE...IN ile kullan!
```

### 4️⃣ TEKRARLA veya CEVAPLA
- Eksik varsa → Farklı strateji dene
- Yeterli varsa → **ÖNCE** add_source çağır (fileName/page_link varsa), **SONRA** kullanıcıya cevap ver

## 🎯 2 AŞAMALI ARAMA (KRİTİK!)

**Birden fazla entity içeren sorgularda ÖNCE her entity'yi ayrı ayrı keşfet!**

```
⛔ YANLIŞ: Tek sorguda çoklu CONTAINS
   WHERE name CONTAINS 'X' AND type CONTAINS 'Y'  → Yanlış eşleşmeler!

✅ DOĞRU: Önce keşif, sonra EXACT değerlerle sorgu
   1. KEŞİF: X'i bul → EXACT değer: "X Tam Adı"
   2. KEŞİF: Y'yi bul → EXACT değer: "Y Tam Adı"  
   3. ANA SORGU: WHERE name = 'X Tam Adı' AND type = 'Y Tam Adı'
```

**KURAL:** Metin araması gerektiren HER ALAN için önce KEŞİF yap, EXACT değer bul!

⛔ **KEŞİF'ten sonra CONTAINS EKLEME!** Bulunan değerleri kullan:
```
❌ WHERE name = 'X' OR name CONTAINS 'x'  → Gereksiz CONTAINS!
✅ WHERE name = 'X'  → Tek sonuç varsa
✅ WHERE name IN ['X Var1', 'X Var2', ...]  → Çoklu varyasyon varsa
```
</react_loop>

<use_all_variations>
⚠️ TÜM VARYASYONLARI KULLAN! (KRİTİK)

ADIM 1: Tool sonuçlarından TÜM varyasyonları listele
   Keşif 1 (NodeA) → ['Entity X Var1...', 'Entity Y...']
   Keşif 2 (NodeB) → ['Entity X Alt Var...', 'Entity X...']
   
ADIM 2: Alakasız olanları ÇIKAR
   Soru: "Entity X" → Entity Y farklı → ÇIKAR
   Kalan: ['Entity X Var1...', 'Entity X Alt Var...', 'Entity X...']
   
ADIM 3: KALAN TÜM varyasyonları ANA SORGUDA kullan!
   WHERE name IN ['Entity X Var1...', 'Entity X Alt Var...', 'Entity X...']

❌ YANLIŞ: Sadece 1-2 varyasyonu kullanmak
✅ DOĞRU: Alakalı TÜM varyasyonları WHERE...IN ile kullanmak
</use_all_variations>

<result_validation>
### ⚠️ SONUÇ DOĞRULAMA

```
Aranan: "X Y"
Bulunan: "X-Z Y" veya "X Z Y" → FAZLADAN kelime var → TAM EŞLEŞMEDEĞİL!
→ Belge içeriğinde (Chunk) de ara!
```
</result_validation>

<content_search_strategy>
### 🔍 İÇERİK ARAMASI STRATEJİSİ

İçerik (detay, açıklama, kloz, madde) araması:
```
1. Entity keşfet → Şemadaki ilgili node'da bul
2. ⚡ EMBEDDING → Entity FİLTRELİ chunk araması
3. Empty → TEXT CONTAINS fallback
```
</content_search_strategy>

---

<tools>
## 🔧 ARAÇLAR (Neo4j Cypher)

### execute_cypher_query(cypher, step_name)
**NE ZAMAN:** Metadata sorguları, entity keşfi, ilişki takibi, sayısal bilgiler
⚠️ `cypher` parametresi **Neo4j Cypher** syntax'ı olmalı!

```cypher
-- KEŞİF: Şemadaki node'larda arama (node label'ı ŞEMADAN al!)
MATCH (n:NodeLabel) 
WHERE apoc.text.clean(n.propertyName) CONTAINS apoc.text.clean('arama_terimi')
RETURN DISTINCT n.propertyName LIMIT 10

-- METADATA: KEŞİF'ten bulunan EXACT değerle sorgula
MATCH (a:NodeA)-[:RELATIONSHIP]->(b:NodeB)
WHERE a.name = 'Keşifte Bulunan Exact Değer'
RETURN b.property1, b.property2 LIMIT 20
```

### add_source(source_type, value) - KAYNAK EKLEME
Cevaba kaynak eklemek için - **ZORUNLU KURALLAR:**

⚠️ **NE ZAMAN ÇAĞIRMALISIN?**
- Sorgu sonucunda `fileName` veya `file` varsa → `add_source("document", fileName)` çağır!
- Sorgu sonucunda `page_link` varsa → `add_source("page", page_link)` çağır!
- Cevabında PDF dosya adı geçecekse → ÖNCE add_source çağır!

```
source_type="document" → PDF dosya adı (örn: "Rapor_2024.pdf")
source_type="page"     → Sayfa görseli (örn: "Rapor_2024_page_001.png")
```

⛔ **KURAL:** add_source çağırmadan dosya adı/page_link YAZMA!
✅ **SADECE** add_source çağır, cevabında dosya adı/kaynak YAZMA! (Link otomatik eklenir)

### read_finding(step_name, start_record, end_record) - PAGINATION
Sorgu sonuçlarının devamını görmek için:

⚠️ **NE ZAMAN KULLAN?**
- execute_graph_dsl veya execute_cypher_query ilk 10 kaydı gösterir
- "Toplam: 150 kayıt, Gösterilen: 0-10" görürsen daha fazlası var demektir
- Doğru cevabın 10. kayıttan sonra olabileceğini düşünüyorsan bu tool'u kullan

```
Örnekler:
read_finding("step_1_search", start_record=10, end_record=20)  → 10-20 arası
read_finding("step_1_search", start_record=20, end_record=50)  → 20-50 arası
```
</tools>

<cypher_rules>
## ŞEMA-TABANLI SORGULAMA
1. Node label'larını ŞEMADAN al → Tahmin ETME!
2. İlişki adlarını ŞEMADAN al → Uydurma!
3. Property isimlerini ŞEMADAN al → Varsayma!
4. İlişki yönlerini ŞEMADAN al → Ters yazma!

## NEO4J 5.x SYNTAX
❌ [:REL1, :REL2]        →  ✅ [:REL1|REL2]
❌ WITH x, x as y        →  ✅ WITH x, x as z
❌ [:REL*1:5]            →  ✅ [:REL*1..5]
❌ exists(n.prop)        →  ✅ n.prop IS NOT NULL
❌ ORDER BY x NULLS LAST →  ✅ ORDER BY x DESC (NULLS yok!)

## ⛔⛔⛔ WHERE SIRALAMA (EN KRİTİK - MUTLAKA UYGULA!)
Neo4j'de WHERE sadece HEMEN ÖNCESİNDEKİ MATCH/OPTIONAL MATCH'e uygulanır!
OPTIONAL MATCH'ten SONRA WHERE yazarsan FİLTRE ÇALIŞMAZ, TÜM SATIRLAR DÖNER!

✅ DOĞRU - Filtreleri OPTIONAL MATCH'ten ÖNCE yaz:
MATCH (a:A)-[:REL]->(b:B)
WHERE a.name IN ['X']  -- ← MATCH'ten hemen sonra!
MATCH (b)-[:REL2]->(c:C)
WHERE c.type = 'Y'  -- ← MATCH'ten hemen sonra!
OPTIONAL MATCH (c)-[:DATE]->(d:Date)  -- Filtre yok, sadece opsiyonel veri
RETURN ...

✅ DOĞRU - WITH ile ayır:
MATCH (a:A)-[:REL]->(b:B)-[:REL2]->(c:C)
WHERE a.name IN ['X'] AND c.type = 'Y'
WITH a, b, c  -- ← Filtrelenmiş sonuçları kilitle
OPTIONAL MATCH (c)-[:DATE]->(d:Date)
RETURN ...

❌ YANLIŞ (TÜM SATIRLAR DÖNER, FİLTRE ÇALIŞMAZ!):
MATCH (a:A)-[:REL]->(b:B)-[:REL2]->(c:C)
OPTIONAL MATCH (c)-[:DATE]->(d:Date)
WHERE a.name IN ['X'] AND c.type = 'Y'  -- ⛔ ÇOK GEÇ! WHERE sadece OPTIONAL MATCH'e uygulanır!

## STRING ARAMASI
KEŞİF: apoc.text.clean() ile fuzzy ara
WHERE apoc.text.clean(n.name) CONTAINS apoc.text.clean('terim')

ANA SORGU: Keşiften bulunan EXACT değer
WHERE n.name = 'Keşifte Bulunan Tam Değer'

## İLİŞKİ YÖNÜ
Şemada (A)-[:REL]->(B) ise:
✅ MATCH (a:A)-[:REL]->(b:B)
❌ MATCH (b:B)-[:REL]->(a:A)

## PARALEL SORGULAR
✅ PARALEL: Aynı terim, farklı node'larda → Paralel tool call
❌ PARALEL DEĞİL: Farklı terimler → Sıralı keşif

## AGGREGATE
Toplam: SUM(n.field) | Ortalama: AVG(n.field) | Sayı: COUNT(DISTINCT n)
</cypher_rules>

<critical_rules>
## ⚠️ KRİTİK KURALLAR (NEO4J CYPHER!)

0. ⚠️ **Neo4j Cypher syntax kullan** → SQL DEĞİL! Yukarıdaki "NEO4J 5.x SYNTAX" kurallarına uy!
1. ⛔ **Şemada olmayan node/ilişki/property YAZMA** → ŞEMAYI KONTROL ET!
2. ⛔ **Tüm Chunk'larda arama YASAK** → Her zaman filtrelenmiş sorgu!
3. ⛔ **Kullanıcıdan onay İSTEME** → Veri varsa direkt CEVAPLA
4. ⛔ **Teknik terim kullanıcıya GÖSTERME** → Node, property, Cypher yok!
5. ✅ **Paralel tool çağrıları KULLAN** → Sadece AYNI TERİM farklı node'larda ise!
6. ✅ **Embedding sonuçlarını DOĞRULA** → False positive kontrolü
7. ✅ **KAYNAK EKLE** → Sonuçta fileName/page_link varsa add_source ÇAĞIR!
8. ✅ **PARALEL KAYNAK** → Birden fazla kaynak ekleyeceksen TEK ADIMDA hepsini paralel çağır!
</critical_rules>

<fallback_strategy>
## 🔄 HIZLI FALLBACK STRATEJİSİ

### ⚡ 2 BOŞ GRAPH SORGUSU → EMBEDDING → TEXT FALLBACK

⚠️ **KURAL:** 2 boş graph sorgusu sonrası daha fazla graph deneme, embedding'e geç!

```
1. Keşif → Varyasyonları bul
2. Graph 1 → empty
3. Graph 2 → empty  
4. ⚡ EMBEDDING (daha fazla graph deneme!)
5. Embedding empty → TEXT CONTAINS fallback
```

### Embedding 0 Sonuç Döndürürse → TEXT CONTAINS Fallback

⚠️ **Embedding araması 0 sonuç döndürdüğünde, `execute_cypher_query` ile c.text CONTAINS ara!**

```cypher
-- Embedding başarısız oldu, text-based arama dene:
-- ⚠️ chunk.text için toLower() kullan (boşlukları korur!)
MATCH (entity:EntityNode)-[:REL1]->(doc:Document)-[:PART_OF]->(c:Chunk)
WHERE entity.name = 'Keşifte Bulunan Exact Değer'
AND (toLower(c.text) CONTAINS 'türkçe terim' 
     OR toLower(c.text) CONTAINS 'english term')
RETURN c.text, c.page_link, doc.fileName
LIMIT 10
```

### Embedding Sonuç Döndü ama FALSE POSITIVE Riski

⚠️ **Yüksek embedding skoru (>0.80) ≠ Doğru sonuç!**

Embedding alan benzerliği yakalar ama kavramsal farklılığı yakalayamaz.

**DOĞRULAMA ADIMLARI:**
1. Dönen `chunk.text` içinde aranan terim GEÇİYOR MU?
2. GEÇMİYORSA → FALSE POSITIVE! Text CONTAINS ile tekrar ara
3. GEÇİYORSA → Doğru sonuç, devam et

**FALSE POSITIVE Örneği:**
```
Arama: "aranan konu X"
Embedding sonucu: "farklı konu Y" (score: 0.87)
Kontrol: "aranan konu X" chunk.text'te geçiyor mu? → HAYIR
Karar: ❌ FALSE POSITIVE! Text CONTAINS ile "aranan konu X" ara
```

### Chunk İlişki Yolu - ÖNEMLİ!

⚠️ **FIRST_CHUNK vs PART_OF farkı:**
- `FIRST_CHUNK`: Sadece belgenin İLK chunk'ını getirir (genellikle başlık)
- `PART_OF`: Belgenin TÜM chunk'larını getirir (içerik araması için)

```cypher
-- İçerik araması için PART_OF kullan:
MATCH (entity)-[:REL]->(doc:Document)<-[:PART_OF]-(c:Chunk)
-- VEYA şemada varsa:
MATCH (entity)-[:REL]->(doc:Document)-[:PART_OF]->(c:Chunk)
```

### ÇOK DİLLİ ARAMA - KRİTİK!

⚠️ **Belgeler farklı dillerde olabilir!**
- Hem Türkçe hem İngilizce karşılığı ile ara
- Örnek: "kira kaybı" VE "loss of rent" birlikte dene
</fallback_strategy>

<output_rules>
⚠️ **YASAK:** Node isimleri, Cypher sorguları, teknik açıklamalar
</output_rules>

---

## 📊 VERİTABANI ŞEMASI

"""

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

def create_react_tools(mcp_tools: List, session_id: str, question_id: str, user_question: str = ""):
    """
    ReAct agent için tool'ları oluşturur.
    
    Args:
        mcp_tools: MCP'den alınan tool listesi
        session_id: Oturum ID'si
        question_id: Soru ID'si  
        user_question: Kullanıcının sorduğu orijinal soru
    
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
    # PAGINATION CONFIG
    # =========================================================================
    DEFAULT_RECORDS_PER_PAGE = 10  # İlk gösterilecek kayıt sayısı
    
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
            more_info = f"\n\n💡 Daha fazla görmek için: read_more_results(\"{step_name}\", start_record={end}, end_record={min(end + DEFAULT_RECORDS_PER_PAGE, total_count)})"
        else:
            more_info = ""
        
        return f"{pagination_info}\n\n{result_lines}{more_info}"
    
    # =========================================================================
    # EXECUTE CYPHER QUERY TOOL
    # =========================================================================
    @tool
    async def execute_cypher_query(cypher: str, step_name: str) -> str:
        """
        Cypher sorgusunu çalıştır ve sonucu döndür.
        
        USE FOR:
        - Entity keşfi (varyasyon bulma)
        - Metadata sorguları (sayı, tarih, liste)
        - İlişki takibi
        
        Args:
            cypher: Cypher sorgusu
            step_name: Adım adı (örn: step_1_entity_search)
        
        Returns:
            İlk 10 kayıt + pagination bilgisi. Daha fazla için read_more_results kullan.
        """
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
            
            result = await mcp_read.ainvoke({"query": cypher})
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
    # EXECUTE GRAPH DSL TOOL - Ontology-Driven Query
    # =========================================================================
    @tool
    async def execute_graph_dsl(dsl_json: str, step_name: str) -> str:
        """
        Graph DSL ile sorgu çalıştır - ÖNERİLEN YOL!
        
        DSL, Cypher'dan daha basit ve hata yapmaya daha az müsait bir yapıdır.
        DSL otomatik olarak validate edilir ve Cypher'a derlenir.
        
        DSL FORMATI (JSON):
        {
            "intent": "find_by_property",  // veya: explore_node, search_content, count_nodes, aggregate_values
            "description": "Entity ara",
            "step_name": "entity_search",
            "start_node": "NodeLabel",  // Başlangıç node (traversal yoksa)
            "traversal": [  // İlişki yolu (opsiyonel)
                {"from_node": "NodeA", "relation": "RELATES_TO", "to_node": "NodeB", "direction": "outgoing"}
            ],
            "filters": [  // Filtreler
                {"node": "NodeA", "property": "name", "operator": "contains", "value": "aranan_deger"}
            ],
            "return_spec": {  // Döndürülecek alanlar
                "nodes": ["NodeB"],
                "properties": {"NodeB": ["name", "prop1"]},
                "distinct": true
            },
            "aggregate": null,  // veya: {"function": "count", "node": "NodeB", "alias": "toplam"}
            "limit": 10
        }
        
        INTENT TİPLERİ:
        - explore_node: Node örneklerini gör
        - find_by_property: Property ile ara
        - find_by_relationship: İlişki ile ara
        - search_content: Chunk semantic araması (filters + traversal + semantic_search)
        - count_nodes: Sayma
        - aggregate_values: SUM, AVG, MIN, MAX
        
        FILTER OPERATÖRLERİ:
        - equals, not_equals, contains, starts_with, ends_with
        - gt, lt, gte, lte (sayısal)
        - in (liste içinde)
        - is_null, is_not_null
        
        Args:
            dsl_json: Graph DSL (JSON string)
            step_name: Adım adı
        
        Returns:
            Sorgu sonucu veya hata mesajı
        """
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
            
            # 4. DSL'i Cypher'a derle
            compiler = DSLCompiler()
            result = compiler.compile(dsl)
            
            cypher = result.cypher
            params = result.params
            
            _log(f"🔷 DSL → Cypher compiled:")
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
                
                # MCP embedding tool çağır
                result_data = await mcp_embedding.ainvoke({
                    "query_text": query_text,
                    "cypher_query": cypher
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
            
            result_data = await mcp_read.ainvoke({"query": cypher})
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
    def add_source(source_type: str, value: str) -> str:
        """
        Cevaba kaynak ekle.
        
        Args:
            source_type: "document" (PDF) veya "page" (sayfa görseli)
            value: Dosya adı veya sayfa linki
        
        Returns:
            Ekleme onayı
        """
        sources = _get_session_sources(question_id)
        
        if source_type == "document":
            sources["documents"].add(value)
            _log(f"📎 Document: {value}")
            return f"✅ Belge kaynağı eklendi: {value}"
        elif source_type == "page":
            sources["pages"].add(value)
            _log(f"🖼️ Page: {value}")
            return f"✅ Sayfa kaynağı eklendi: {value}"
        else:
            return f"❌ Geçersiz source_type. 'document' veya 'page' olmalı."
    
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
        """
        Daha önce kaydedilen sorgu sonuçlarını oku - PAGINATION destekli!
        
        İlk sorgu sonucunda 10/150 kayıt gösterilir. Daha fazla görmek için bu tool'u kullan.
        
        Args:
            step_name: Adım adı (örn: step_1_entity_search)
            result_type: "success" veya "failed"
            start_record: Başlangıç kayıt numarası (0'dan başlar)
            end_record: Bitiş kayıt numarası (0 = tümü)
        
        Örnekler:
            İlk 10 kayıt: start_record=0, end_record=10
            10-20 arası:  start_record=10, end_record=20
            20-50 arası:  start_record=20, end_record=50
        
        Returns:
            İstenen aralıktaki kayıtlar
        """
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
        
        pagination_info = f"📊 Gösterilen: {start_record}-{effective_end} / Toplam: {total_records} kayıt"
        
        if effective_end < total_records:
            next_end = min(effective_end + DEFAULT_RECORDS_PER_PAGE, total_records)
            more_info = f"\n\n💡 Sonraki sayfa: read_finding(\"{step_name}\", start_record={effective_end}, end_record={next_end})"
        else:
            more_info = "\n\n✅ Tüm kayıtlar gösterildi."
        
        _log(f"📖 read_finding: {step_name} [{start_record}-{effective_end}/{total_records}]")
        
        return f"{pagination_info}\n\n{result_lines}{more_info}"
    
    # DSL tool'unu ekle (eğer modül yüklüyse)
    tools = [execute_cypher_query, add_source, read_finding]
    if GRAPH_DSL_AVAILABLE:
        tools.insert(0, execute_graph_dsl)  # DSL'i öne koy (önerilen yol)
    return tools


# ============================================================================
# REACT AGENT CLASS
# ============================================================================

class ReactAgent:
    """
    OpenAI Prompt Caching optimizasyonlu ReAct Agent
    
    Özellikler:
    - Tek agent 
    - Cache-optimized prompt (sabit prefix + dinamik suffix)
    - Paralel tool çağrıları
    - Streaming response
    """
    
    def __init__(self, graph, model_name: Optional[str] = None, reasoning_effort: Optional[str] = None):
        """
        Args:
            graph: Neo4j graph connection
            model_name: Model adı (default: env REACT_MODEL veya gpt-5)
            reasoning_effort: GPT-5 için reasoning effort (none, low, medium, high)
        """
        self.graph = graph
        self.model_name = model_name or os.environ.get("REACT_MODEL", "gpt-5")
        self.reasoning_effort = reasoning_effort or os.environ.get("REACT_REASONING_EFFORT", "low")
        self.agent: Optional[Dict[str, Any]] = None
        self.mcp_client: Optional[Any] = None
        self.mcp_tools: Optional[List[Any]] = None
        self._schema_cache: Dict[str, str] = {}
        self.redis_cache_active = False
        
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
    
    def _get_conversation_history(self, session_id: str) -> List[Dict[str, str]]:
        """PostgreSQL'den conversation history al"""
        if not session_id:
            return []
        
        try:
            from src.shared.postgres_chat_history import create_postgres_chat_message_history
            
            conversation_history = create_postgres_chat_message_history(
                session_id=session_id, write_access=True
            )
            
            if conversation_history and hasattr(conversation_history, "messages"):
                messages: List[Dict[str, str]] = []
                recent_messages = conversation_history.messages[-20:] if len(conversation_history.messages) > 20 else conversation_history.messages
                
                for msg in recent_messages:
                    if hasattr(msg, "content"):
                        role = "user" if (hasattr(msg, "type") and msg.type == "human") else "assistant"
                        content = str(msg.content) if msg.content else ""
                        messages.append({"role": role, "content": content})
                
                _log(f"History: {len(messages)} msgs")
                return messages
        except Exception as e:
            _log(f"⚠️ History fetch error: {e}", "warning")
        
        return []
    
    def _save_to_history(self, session_id: str, role: str, content: str) -> None:
        """PostgreSQL'e mesaj kaydet"""
        if not session_id:
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
    
    def _build_system_prompt(self, schema_info: str, session_id: str = "") -> str:
        """
        Cache-optimized system prompt oluştur.
        
        Prompt Caching için:
        - Sabit prefix (instructions + schema) → Cache'lenir
        - Dinamik suffix ayrı tutulur
        
        Langfuse Prompt Management:
        - Önce Langfuse'dan "react-agent-system" prompt'u çekilir
        - Langfuse erişilemezse CACHED_SYSTEM_PREFIX fallback olarak kullanılır
        - Prompt'ta {{schema_info}} placeholder'ı değişken olarak compile edilir
        """
        # 1. Langfuse'dan prompt al
        langfuse_prompt = get_prompt(
            name=LANGFUSE_PROMPT_NAME,
            prompt_type=LANGFUSE_PROMPT_TYPE,
            label=LANGFUSE_PROMPT_LABEL,
            fallback=CACHED_SYSTEM_PREFIX,  # Fallback: kod içindeki prompt
        )
        
        if langfuse_prompt:
            try:
                # Langfuse prompt'u compile et - {{schema_info}} → gerçek şema
                compiled_prompt = langfuse_prompt.compile(schema_info=schema_info)
                
                # Version bilgisini logla
                version = getattr(langfuse_prompt, 'version', 'unknown')
                labels = getattr(langfuse_prompt, 'labels', [])
                _log(f"📋 Langfuse prompt loaded: {LANGFUSE_PROMPT_NAME} v{version} {labels}")
                
                return compiled_prompt
                
            except Exception as e:
                _log(f"⚠️ Langfuse prompt compile failed: {e}, using fallback", "warning")
        
        # 2. Fallback: Kod içindeki prompt
        _log(f"📋 Using fallback prompt (Langfuse unavailable)")
        full_prefix = CACHED_SYSTEM_PREFIX + schema_info
        return full_prefix
    
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
        prompt_with_placeholder = CACHED_SYSTEM_PREFIX + "{{schema_info}}"
        
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
            _log(f"✅ MCP connected, {tool_count} tools available")
        
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
        """Model instance oluştur"""
        from langchain_openai import ChatOpenAI
        from pydantic import SecretStr
        
        actual_model = self.model_name
        if ":" in self.model_name:
            actual_model = self.model_name.split(":", 1)[1]
        
        api_key = os.environ.get("OPENAI_API_KEY")
        
        # GPT-5 için reasoning_effort + summary
        # AgentAwareSemanticCache wrapper tool_calls serialize sorununu çözer
        # Artık cache=False gerekmez
        # summary: "auto" | "concise" | "detailed" - düşünce süreçlerini gösterir
        if "gpt-5" in actual_model.lower() and self.reasoning_effort:
            _log(f"🔧 Model: {actual_model}, reasoning={self.reasoning_effort}, summary=auto")
            return ChatOpenAI(
                model=actual_model,
                api_key=SecretStr(api_key) if api_key else None,
                reasoning={"effort": self.reasoning_effort, "summary": "auto"},
            )
        else:
            _log(f"🔧 Model: {actual_model}")
            return ChatOpenAI(
                model=actual_model,
                api_key=SecretStr(api_key) if api_key else None,
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
            
            # History al
            conversation_history = self._get_conversation_history(session_id)
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
                user_question=question
            )
            
            # Gerçek agent'ı oluştur
            if create_agent is None:
                yield {
                    "type": "error",
                    "message": "LangChain create_agent not available.",
                    "session_id": session_id,
                }
                return
            
            # 📚 Few-shot learning: Başarılı örnekleri system prompt'a ekle
            few_shot_examples: List[Dict[str, Any]] = []
            # Her soru için orijinal system_prompt kullan (cache'lenmiş few-shot'u önle)
            # agent_config["system_prompt"] cache'lenmiş olabilir, yeniden oluştur
            original_system_prompt = agent_config.get("original_system_prompt") or agent_config["system_prompt"]
            final_system_prompt = original_system_prompt
            
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
                        final_system_prompt = final_system_prompt + "\n\n" + few_shot_prompt
                        _log(f"📚 Few-shot: {len(few_shot_examples)} örnek, {len(corrections)} düzeltme eklendi")
                        
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
            
            agent = create_agent(
                model=agent_config["model"],
                tools=react_tools,
                system_prompt=final_system_prompt,
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
            agent_input: Dict[str, Any] = {"messages": [{"role": "user", "content": question}]}
            
            # History varsa ekle
            if conversation_history:
                agent_input["messages"] = conversation_history + [{"role": "user", "content": question}]
            
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
                            cached_tokens = 0
                            reasoning_tokens = 0  # GPT-5 reasoning token sayısı
                            
                            # DEBUG: usage_metadata yapısını logla
                            if usage_metadata:
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
                                
                                token_tracker.add_llm_step(
                                    step_name=f"llm_step_{llm_step_count}",
                                    input_tokens=input_tokens,
                                    output_tokens=output_tokens,
                                    cached_tokens=cached_tokens,
                                    reasoning_tokens=reasoning_tokens,  # 🧠 GPT-5 reasoning
                                    session_id=session_id,
                                    llm_input={"question": question, "step": llm_step_count},
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
                
                # Dosya linkleri ekle
                if sources["documents"]:
                    file_markdown = _generate_file_links_markdown(sources["documents"])
                    final_response += file_markdown
                    _log(f"📎 {len(sources['documents'])} belge kaynağı eklendi")
                
                # Sayfa görselleri ekle
                if sources["pages"]:
                    page_markdown = _generate_page_links_markdown(sources["pages"])
                    final_response += page_markdown
                    _log(f"🖼️ {len(sources['pages'])} sayfa görseli eklendi")
                
                # Hallucination uyarısı ekle
                if hallucination_warning:
                    final_response += hallucination_warning
                
                # Markdown kaynakları stream et (message_chunk olarak)
                source_markdown = ""
                if sources["documents"]:
                    source_markdown += _generate_file_links_markdown(sources["documents"])
                if sources["pages"]:
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
                        sources={
                            "documents": list(sources["documents"]),
                            "pages": list(sources["pages"]),
                        },
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
                    "sources": {
                        "documents": list(sources["documents"]),
                        "pages": list(sources["pages"]),
                    },
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
    reasoning_effort: Optional[str] = None
) -> ReactAgent:
    """
    Session bazlı ReactAgent al veya oluştur.
    
    Her session için tek agent instance tutulur - MCP bağlantısı reuse edilir.
    """
    global _react_session_agents, _react_session_access_times
    
    # Cleanup
    _cleanup_old_react_sessions()
    
    # Mevcut agent varsa döndür
    if session_id in _react_session_agents:
        _react_session_access_times[session_id] = datetime.now()
        _log(f"React session {session_id[:8]}: reusing cached agent")
        return _react_session_agents[session_id]
    
    # Yeni agent oluştur
    agent = ReactAgent(
        graph=graph,
        model_name=model,
        reasoning_effort=reasoning_effort
    )
    
    _react_session_agents[session_id] = agent
    _react_session_access_times[session_id] = datetime.now()
    
    _log(f"React session {session_id[:8]}: new agent created (cache={len(_react_session_agents)})")
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
    **kwargs: Any,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    ReAct Agent ile streaming cevap üret.
    
    OpenAI Prompt Caching optimizasyonlu tek agent kullanır.
    
    Args:
        question: Kullanıcının sorusu
        model: LLM modeli (default: env REACT_MODEL veya gpt-5)
        session_id: Oturum ID'si (ZORUNLU)
        question_id: Soru ID'si
        graph: Neo4j graph connection
        reasoning_effort: GPT-5 için reasoning seviyesi (none, low, medium, high)
        user_id: Kullanıcı ID (email veya unique identifier) - Langfuse User Tracking için
        **kwargs: Ek parametreler
    
    Yields:
        Dict: Streaming chunk'ları
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
    
    try:
        # Session bazlı agent al veya oluştur
        agent = await get_or_create_react_session_agent(
            session_id, model, graph, reasoning_effort
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

