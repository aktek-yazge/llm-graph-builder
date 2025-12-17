"""
LangChain Agent Integration Module for Chat Bot Stream

Bu modül LangChain create_agent kullanarak chat_bot_stream endpoint'ine entegre eder.
MCP tools (neo4j-database) kullanılarak Neo4j sorguları yapılır.

Features:
- LangChain native create_agent yapısı
- Middleware tabanlı planlama (TodoListMiddleware)
- MCP Tools integration (neo4j-database server)
- Subagent as separate agent graphs
- Conversation history support
- Streaming response
"""

import asyncio
import logging
import os
import re
import urllib.parse
from typing import AsyncGenerator, Dict, Any, Optional, List, Set, TYPE_CHECKING, Sequence, Callable
from datetime import datetime

# Global Schema Cache import
from src.shared.schema_cache import get_cached_schema, get_schema_cache

# Logging ayarları
logger = logging.getLogger(__name__)

# MCP client'ın verbose log'larını sustur (Negotiated protocol version vb.)
logging.getLogger("mcp").setLevel(logging.WARNING)
logging.getLogger("mcp.client").setLevel(logging.WARNING)
logging.getLogger("langchain_mcp_adapters").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

def _log(msg: str, level: str = "info"):
    """Minimal log helper - timestamp logging framework'ten gelir"""
    if level == "debug":
        logging.debug(msg)
    elif level == "warning":
        logging.warning(msg)
    elif level == "error":
        logging.error(msg)
    else:
        logging.info(msg)


def create_worker_model(model_name: str, reasoning_effort: Optional[str] = None):
    """
    Worker için ChatOpenAI model oluşturur.
    
    - Paralel tool çağrıları AÇIK (LLM yeteneği varsa kullanır)
    - GPT-5 modelleri için reasoning_effort destekler
    - GPT-4o-mini gibi modeller de sorunsuz çalışır
    
    Args:
        model_name: Model adı (örn: "gpt-4o-mini", "openai:gpt-5-mini")
        reasoning_effort: GPT-5 modelleri için reasoning effort ("minimal", "low", "medium", "high")
    """
    try:
        from langchain_openai import ChatOpenAI
        from pydantic import SecretStr
        
        # Model adından gerçek model adını çıkar
        # "openai:gpt-4o-mini" -> "gpt-4o-mini"
        actual_model = model_name
        if ":" in model_name:
            actual_model = model_name.split(":", 1)[1]
        
        # Model kwargs oluştur
        model_kwargs: Dict[str, Any] = {"model": actual_model}
        
        # API key
        api_key = os.environ.get("OPENAI_API_KEY")
        if api_key:
            model_kwargs["api_key"] = SecretStr(api_key)
        
        # GPT-5 modelleri için reasoning_effort ekle
        if "gpt-5" in actual_model.lower() and reasoning_effort:
            model_kwargs["reasoning"] = {"effort": reasoning_effort}
            print(f"🔧 [create_worker_model] Model: {actual_model}, reasoning={reasoning_effort}, parallel_tools=ON")
        else:
            print(f"🔧 [create_worker_model] Model: {actual_model}, parallel_tools=ON")
        
        model = ChatOpenAI(**model_kwargs)
        return model
        
    except ImportError:
        # Fallback: normal model döndür
        if init_chat_model is not None:
            return init_chat_model(model_name)
        return None

# Redis Semantic Cache import
if TYPE_CHECKING:
    from src.shared.redis_cache import setup_semantic_cache, is_cache_available, get_cache_stats

try:
    from src.shared.redis_cache import setup_semantic_cache, is_cache_available, get_cache_stats  # type: ignore
    REDIS_CACHE_IMPORTED = True
except ImportError as e:
    logging.warning(f"⚠️ Redis cache module not available: {e}")
    REDIS_CACHE_IMPORTED = False
    setup_semantic_cache = None  # type: ignore
    is_cache_available = None  # type: ignore
    get_cache_stats = None  # type: ignore

# LangChain Agent imports
if TYPE_CHECKING:
    from langchain.agents import create_agent
    from langchain.agents.middleware import AgentMiddleware, TodoListMiddleware
    from langchain.chat_models import init_chat_model

try:
    from langchain.agents import create_agent  # type: ignore
    from langchain.agents.middleware import (  # type: ignore
        AgentMiddleware,
        TodoListMiddleware,
        ModelCallLimitMiddleware,
    )
    from langchain.chat_models import init_chat_model  # type: ignore
    from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
    from langchain_core.tools import BaseTool, tool
    from langchain_community.callbacks import get_openai_callback
    
    LANGCHAIN_AGENT_AVAILABLE = True
    logging.info("✅ LangChain Agent (create_agent) successfully imported")
except ImportError as e:
    logging.warning(f"⚠️ LangChain Agent not available: {e}")
    LANGCHAIN_AGENT_AVAILABLE = False
    create_agent = None  # type: ignore
    init_chat_model = None  # type: ignore
    AgentMiddleware = None  # type: ignore
    TodoListMiddleware = None  # type: ignore
    ModelCallLimitMiddleware = None  # type: ignore
    tool = None  # type: ignore
    BaseTool = None  # type: ignore
    HumanMessage = None  # type: ignore
    AIMessage = None  # type: ignore
    SystemMessage = None  # type: ignore

# MCP Adapters import
if TYPE_CHECKING:
    from langchain_mcp_adapters.client import MultiServerMCPClient

try:
    from langchain_mcp_adapters.client import MultiServerMCPClient  # type: ignore
    MCP_ADAPTERS_AVAILABLE = True
    logging.info("✅ LangChain MCP Adapters successfully imported")
except ImportError as e:
    logging.warning(f"⚠️ LangChain MCP Adapters not available: {e}")
    MCP_ADAPTERS_AVAILABLE = False
    MultiServerMCPClient = None  # type: ignore


# ============================================================================
# MCP SERVER CONFIGURATION - HTTP Transport Only
# ============================================================================

MCP_HTTP_HOST = os.environ.get("MCP_HTTP_HOST", "127.0.0.1")
MCP_HTTP_PORT = int(os.environ.get("MCP_HTTP_PORT", "8002"))


def get_mcp_server_config() -> Dict[str, Any]:
    """
    MCP server konfigürasyonunu döndürür.
    Sadece HTTP transport kullanılır - MCP server ayrı process olarak çalışır.
    """
    logging.info(f"📡 MCP Config: HTTP transport - http://{MCP_HTTP_HOST}:{MCP_HTTP_PORT}/mcp/")
    return {
        "neo4j-database": {
            "url": f"http://{MCP_HTTP_HOST}:{MCP_HTTP_PORT}/mcp/",
            "transport": "streamable_http",
        }
    }


# ============================================================================
# SESSION-BASED SOURCE MANAGEMENT - Kaynak yönetimi
# ============================================================================

# Session bazlı kaynaklar - {question_id: {"documents": set(), "pages": set()}}
_session_sources: Dict[str, Dict[str, set]] = {}

def _get_session_sources(question_id: str) -> Dict[str, set]:
    """Session için kaynak dict'ini al veya oluştur"""
    if question_id not in _session_sources:
        _session_sources[question_id] = {"documents": set(), "pages": set()}
    return _session_sources[question_id]

def _clear_session_sources(question_id: str):
    """Session kaynaklarını temizle"""
    if question_id in _session_sources:
        del _session_sources[question_id]

# ============================================================================
# CUSTOM TOOLS - Agent için özel araçlar
# ============================================================================

# Tool tanımları - sadece import başarılıysa tanımlanır
think_tool = None
write_finding = None
read_finding = None
add_source = None

if LANGCHAIN_AGENT_AVAILABLE and tool is not None:
    @tool
    def _think_tool(reflection: str) -> str:
        """
        Strateji değerlendirme ve analiz aracı.
        
        ÖZELLİKLE ŞU DURUMLARDA KULLAN:
        1. Worker 0 sonuç döndürdüğünde - Neden bulamadı?
        2. Strateji değişikliği gerektiğinde - Farklı ne denenebilir?
        3. Sonuçları yorumlarken - Aranan entity ile eşleşiyor mu?
        
        BAŞARISIZ SONUÇ ANALİZİ:
        - Hangi node'larda arandı?
        - Hangi terimler denendi?
        - Arama terimleri yanlış olabilir mi?
        - Farklı property'ler denenebilir mi?
        - Varyasyonlar eksik olabilir mi?
        
        KARAR VER:
        - Farklı terimlerle yeni KEŞİF mi?
        - Farklı node'larla yeni KEŞİF mi?
        - İçerik aramasına geç mi?
        - Kullanıcıya "bulunamadı" mı?
        
        Args:
            reflection: Düşünce ve strateji değerlendirmesi
        
        Returns:
            Düşünce kaydedildi onayı
        """
        _log(f"💭 THINK:\n{reflection}")
        return "Düşünce kaydedildi."

    @tool
    def _write_finding(session_id: str, step_name: str, content: str) -> str:
        """
        Araştırma bulgularını dosyaya kaydet.
        
        Args:
            session_id: Oturum ID'si (kısa versiyon)
            step_name: Adım adı (örn: step_1_entity_search)
            content: Kaydedilecek içerik (markdown formatında)
        
        Returns:
            Dosya yolu
        """
        findings_dir = os.path.join(os.getcwd(), "agent_findings", "findings", session_id)
        os.makedirs(findings_dir, exist_ok=True)
        
        file_path = os.path.join(findings_dir, f"{step_name}.md")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        
        _log(f"📁 Finding saved: {file_path}")
        return f"Bulgular kaydedildi: {file_path}"

    @tool
    def _read_finding(session_id: str, question_id: str, step_name: str, result_type: str = "success") -> str:
        """
        Worker'ın kaydettiği sonuç dosyasını oku.
        
        Args:
            session_id: Oturum ID'si
            question_id: Soru ID'si
            step_name: Adım adı (örn: step_1_customer_search)
            result_type: "success", "failed" veya "error"
        
        Dosya Formatı:
            <query>
            MATCH ...
            </query>
            
            <result>
            (R:0){...}
            </result>
        """
        findings_dir = os.path.join(os.getcwd(), "agent_findings", "findings", session_id, question_id)
        
        # Önce .txt dene (yeni format)
        file_path = os.path.join(findings_dir, f"{step_name}_{result_type}.txt")
        
        if not os.path.exists(file_path):
            # Eski formatları dene (geriye uyumluluk)
            for ext in [".xml", ".md"]:
                alt_path = os.path.join(findings_dir, f"{step_name}_{result_type}{ext}")
                if os.path.exists(alt_path):
                    file_path = alt_path
                    break
            else:
                return f"Dosya bulunamadı: {file_path}"
        
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        _log(f"📖 Finding read: {file_path}")
        return content

    @tool
    def _read_blackboard(session_id: str, question_id: str) -> str:
        """
        Ortak tahta dosyasını oku - tüm bulgular burada!
        
        Worker her sorgu sonucunu blackboard'a yazar.
        Bu tool ile tüm KEŞİF sonuçlarını, varyasyonları ve dosya listesini görürsün.
        
        Args:
            session_id: Oturum ID'si
            question_id: Soru ID'si
        
        Returns:
            Blackboard içeriği (tüm bulgular, varyasyonlar, dosya listesi)
        
        ⚠️ İÇERİK görevi vermeden önce MUTLAKA blackboard'u oku!
        KEŞİF'te bulunan varyasyonları buradan al ve İÇERİK görevine aktar.
        """
        blackboard_path = os.path.join(
            os.getcwd(), "agent_findings", "findings", session_id, question_id, "_blackboard.txt"
        )
        
        if not os.path.exists(blackboard_path):
            return "Blackboard henüz oluşturulmadı - Worker henüz sorgu çalıştırmadı."
        
        with open(blackboard_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        _log(f"📋 Blackboard read: {blackboard_path}")
        return content

    @tool
    def _add_source(question_id: str, source_type: str, value: str) -> str:
        """
        Cevaba kaynak ekle. LLM doğru bilgiyi bulduğunda bu tool ile kaynağı kaydeder.
        
        Args:
            question_id: Soru ID'si (kısa versiyon, örn: "a1b2c3d4")
            source_type: Kaynak tipi - "document" veya "page"
                - "document": PDF dosya adı (örn: "Rapor_2024.pdf")
                - "page": Sayfa görseli (örn: "Rapor_2024_page_001.png")
            value: Kaynak değeri (dosya adı veya sayfa linki)
        
        Returns:
            Ekleme onayı
        
        Örnek:
            add_source("a1b2c3d4", "document", "Rapor_2024.pdf")
            add_source("a1b2c3d4", "page", "Rapor_2024_page_001.png")
        """
        sources = _get_session_sources(question_id)
        
        if source_type == "document":
            sources["documents"].add(value)
            _log(f"📎 Document source added: {value}")
            return f"✅ Belge kaynağı eklendi: {value}"
        elif source_type == "page":
            sources["pages"].add(value)
            _log(f"🖼️ Page source added: {value}")
            return f"✅ Sayfa kaynağı eklendi: {value}"
        else:
            return f"❌ Geçersiz source_type: {source_type}. 'document' veya 'page' olmalı."

    # Global isimlere ata
    think_tool = _think_tool
    write_finding = _write_finding
    read_finding = _read_finding
    read_blackboard = _read_blackboard
    add_source = _add_source


# ============================================================================
# ADAPTER TOOLS - Worker için MCP tool wrapper'ları
# ============================================================================
# Bu tool'lar LangChainAgent._create_agent içinde dinamik olarak oluşturulur
# çünkü MCP client instance'ına erişim gerekiyor.
# Aşağıdaki fonksiyonlar factory pattern ile tool oluşturur.

def create_adapter_tools(mcp_tools: List, session_id: str, question_id: str):
    """
    Worker için adapter tool'ları oluşturur.
    
    Her adapter:
    1. MCP tool'u çağırır
    2. Sonucu dosyaya yazar
    3. İstatistik döndürür (ham data değil!)
    
    Args:
        mcp_tools: MCP'den alınan tool listesi
        session_id: Oturum ID'si
        question_id: Soru ID'si
    
    Returns:
        [execute_cypher_query, execute_embedding_query] tool listesi
    """
    if not LANGCHAIN_AGENT_AVAILABLE or tool is None:
        return []
    
    # MCP tool'larını isimle eşle
    mcp_tool_map = {t.name: t for t in mcp_tools}
    
    # Findings dizinini hazırla
    findings_base = os.path.join(os.getcwd(), "agent_findings", "findings", session_id, question_id)
    os.makedirs(findings_base, exist_ok=True)
    
    # Blackboard dosyası - ortak tahta
    blackboard_path = os.path.join(findings_base, "_blackboard.txt")
    
    def _append_to_blackboard(step_name: str, file_path: str, record_count: int, success: bool):
        """Blackboard'a sadece dosya yolu ekle"""
        try:
            # Mevcut içeriği oku
            existing = ""
            if os.path.exists(blackboard_path):
                with open(blackboard_path, "r", encoding="utf-8") as f:
                    existing = f.read()
            
            # Yeni içerik - sadece dosya yolu
            status = "✅" if success else "❌"
            entry = f"{status} {step_name}: {record_count} kayıt → {os.path.basename(file_path)}\n"
            
            # Dosyaya yaz
            with open(blackboard_path, "w", encoding="utf-8") as f:
                if not existing:
                    f.write(f"# 📋 BLACKBOARD\n")
                    f.write(f"# Session: {session_id} | Question: {question_id}\n")
                    f.write(f"# Detaylar için: read_finding(session_id, question_id, step_name, result_type)\n\n")
                else:
                    f.write(existing)
                f.write(entry)
                
        except Exception as e:
            _log(f"⚠️ Blackboard yazma hatası: {e}")
    
    @tool
    async def execute_cypher_query(cypher: str, step_name: str) -> str:
        """
        Cypher sorgusunu çalıştır, sonucu dosyaya yaz, istatistik döndür.
        
        USE THIS TOOL FOR:
        - METADATA queries: names, numbers, dates, counts, IDs
        - Questions like: "Who?", "How many?", "Which date?", "What number?"
        - Finding entities and their properties from graph nodes
        
        Args:
            cypher: Çalıştırılacak Cypher sorgusu
            step_name: Adım adı (örn: step_1_customer_search)
        
        Returns:
            İstatistik özeti (kayıt sayısı, başarı durumu, dosya yolu)
        """
        mcp_read = mcp_tool_map.get("read_neo4j_cypher")
        if not mcp_read:
            return '{"success": false, "error": "MCP read_neo4j_cypher tool not found"}'
        
        try:
            # MCP tool'u çağır
            result = await mcp_read.ainvoke({"query": cypher})
            result_str = str(result) if result else ""
            
            # Hata kontrolü - MCP tool hata döndüyse
            is_error = "HATA:" in result_str or "ERROR:" in result_str or "❌" in result_str
            
            # Sonucu parse et
            records = []
            if result_str and result_str.strip() and not is_error:
                # Her satırı bir kayıt olarak say
                lines = [l.strip() for l in result_str.split('\n') if l.strip() and l.strip().startswith('(R:')]
                records = lines
            
            record_count = len(records)
            success = record_count > 0 and not is_error
            
            # Sadece 2 etiket: query ve result
            suffix = "success" if success else "failed"
            file_path = os.path.join(findings_base, f"{step_name}_{suffix}.txt")
            
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(f"<query>\n{cypher}\n</query>\n\n")
                f.write(f"<result>\n{result_str}\n</result>\n")
            
            _log(f"📁 Adapter wrote: {file_path} ({record_count} records)")
            
            # Blackboard'a dosya yolunu yaz
            _append_to_blackboard(step_name, file_path, record_count, success)
            
            # Detaylı istatistik döndür (HAM DATA YOK - kayıt içerikleri yok!)
            return f"""{{
  "success": {str(success).lower()},
  "record_count": {record_count},
  "step_name": "{step_name}",
  "result_type": "{suffix}",
  "file_path": "{file_path}",
  "query_type": "cypher"
}}"""
            
        except Exception as e:
            # Hata durumu
            file_path = os.path.join(findings_base, f"{step_name}_error.txt")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(f"<query>\n{cypher}\n</query>\n\n")
                f.write(f"<result>\nERROR: {str(e)}\n</result>\n")
            
            return f"""{{
  "success": false,
  "record_count": 0,
  "step_name": "{step_name}",
  "result_type": "error",
  "file_path": "{file_path}",
  "query_type": "cypher",
  "error": "{str(e)}"
}}"""
    
    @tool
    async def execute_embedding_query(query_text: str, cypher_query: str, step_name: str) -> str:
        """
        Embedding araması yap, sonucu dosyaya yaz, istatistik döndür.
        
        USE THIS TOOL FOR:
        - Content/detail questions: "What does it say?", "What are the details?"
        - Searching within document content using semantic similarity
        - Finding information that exists IN DOCUMENT CONTENT, not in graph nodes
        
        Args:
            query_text: Aranacak metin (semantic search için - sadece KONU, varyasyonlar DEĞİL!)
            cypher_query: ⛔ HER ZAMAN FİLTRELİ SORGU!
                ❌ KESİNLİKLE YASAK: MATCH (c:Chunk) WHERE ... (tüm chunk'lar - ASLA!)
                ✅ ZORUNLU: MATCH (n:Label)<-[:REL]-...->(c:Chunk) WHERE n.name IN [varyasyonlar] AND c.embedding...
                ⚠️ Orchestrator'ın verdiği varyasyon + ilişki yolunu MUTLAKA kullan!
                📄 RETURN: c.text, d.fileName, c.page_link, score (fileName + page_link ZORUNLU!)
            step_name: Adım adı (örn: step_2_content_search)
        
        Returns:
            İstatistik özeti (kayıt sayısı, başarı durumu, dosya yolu)
        """
        mcp_embedding = mcp_tool_map.get("read_neo4j_cypher_with_embedding")
        if not mcp_embedding:
            return '{"success": false, "error": "MCP read_neo4j_cypher_with_embedding tool not found"}'
        
        try:
            # MCP tool'u çağır
            result = await mcp_embedding.ainvoke({
                "query_text": query_text,
                "cypher_query": cypher_query
            })
            result_str = str(result) if result else ""
            
            # Hata kontrolü - MCP tool hata döndüyse
            is_error = "HATA:" in result_str or "ERROR:" in result_str or "❌" in result_str
            
            # Sonucu parse et
            records = []
            if result_str and result_str.strip() and not is_error:
                lines = [l.strip() for l in result_str.split('\n') if l.strip()]
                # Score içeren satırları say
                records = [l for l in lines if 'score' in l.lower() or l.startswith('(')]
            
            record_count = len(records)
            success = record_count > 0 and not is_error
            
            # Sadece 2 etiket: query ve result
            suffix = "success" if success else "failed"
            file_path = os.path.join(findings_base, f"{step_name}_{suffix}.txt")
            
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(f"<query>\nSEARCH: {query_text}\n{cypher_query}\n</query>\n\n")
                f.write(f"<result>\n{result_str}\n</result>\n")
            
            _log(f"📁 Adapter wrote: {file_path} ({record_count} records)")
            
            # Blackboard'a dosya yolunu yaz
            _append_to_blackboard(step_name, file_path, record_count, success)
            
            # Detaylı istatistik döndür (HAM DATA YOK - kayıt içerikleri yok!)
            return f"""{{
  "success": {str(success).lower()},
  "record_count": {record_count},
  "step_name": "{step_name}",
  "result_type": "{suffix}",
  "file_path": "{file_path}",
  "query_type": "embedding",
  "search_term": "{query_text}"
}}"""
            
        except Exception as e:
            file_path = os.path.join(findings_base, f"{step_name}_error.txt")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(f"<query>\nSEARCH: {query_text}\n{cypher_query}\n</query>\n\n")
                f.write(f"<result>\nERROR: {str(e)}\n</result>\n")
            
            return f"""{{
  "success": false,
  "record_count": 0,
  "step_name": "{step_name}",
  "result_type": "error",
  "file_path": "{file_path}",
  "query_type": "embedding",
  "search_term": "{query_text}",
  "error": "{str(e)}"
}}"""
    
    return [execute_cypher_query, execute_embedding_query]


# ============================================================================
# SYSTEM PROMPTS - ORCHESTRATOR & WORKER AGENT
# ============================================================================

# -----------------------------------------------------------------------------
# ANA AGENT (ORCHESTRATOR) - Planlama, Koordinasyon, Değerlendirme
# -----------------------------------------------------------------------------
ORCHESTRATOR_SYSTEM_PROMPT = """
Sen kullanıcı sorularını analiz eden, plan yapan ve araştırma koordine eden bir stratejistsin.

## 🎯 SENİN GÖREVLER

1. **Plan yap** - write_todos ile adım adım TODO listesi oluştur
2. **Strateji belirle** - Şemayı analiz et, olasılıkları belirle
3. **Worker'a görev ver** - spawn_worker ile araştırma görevi ver
4. **Sonuçları değerlendir** - İstatistik + tahtayı oku, TODO'yu güncelle
5. **Final cevap oluştur** - Tüm bulgulardan kullanıcıya cevap ver

## 🔧 TOOL'LAR

1. **write_todos** - Plan oluştur ve güncelle
2. **spawn_worker(queries)** - Worker'a araştırma görevi ver
3. **read_blackboard_dynamic()** - 📋 TÜM BULGULARI gör (session/question ID otomatik)
4. **read_finding_dynamic(step_name, result_type, include_query, start_record, end_record)** - Tek dosya oku
5. **think_tool** - Strateji değerlendir, başarısız sonuçları analiz et

## 💰 PAGINATION - Kayıtları sayfa sayfa oku

```python
read_finding_dynamic(step_name, result_type, include_query=True, start_record=0, end_record=0)
# start_record: başlangıç kayıt no, end_record: bitiş (0=tümü)
# Örnek: start_record=10, end_record=20 → R:10-R:19 arası
```

## 🏗️ AKIŞ

1. **spawn_worker(görev, question_id)** → Worker sorgu yazar, çalıştırır, dosyaya kaydeder
2. **İstatistik döner:**
   ```
   {success: true, record_count: 4, file_path: "findings/.../step_1_success.md"}
   ```
3. **Başarılı (success=true)?**
   → `read_blackboard_dynamic()` ile TÜM bulgulara bak
   → Varyasyonları al, sonraki adıma geç
4. **Başarısız (success=false)?**
   → `read_blackboard` ile önceki başarılı sonuçları kontrol et
   → `think_tool` ile strateji değiştir
   → `think_tool` ile analiz et: Neden bulamadı? Farklı ne denenebilir?
   → Yeni strateji ile tekrar dene

⛔ **SEN SORGU ÇALIŞTIRMA!** Görev ver, Worker halleder.

## 📋 WORKER'A GÖREV FORMATI

Her görevde **GÖREV TİPİ** belirt! Worker buna göre araç seçer.

### GÖREV TİPİ: KEŞİF
Varyasyon bulma, entity keşfi ⛔ **CHUNK HARİÇ!** (Chunk → İÇERİK görevi)

**🚨 ARAMA TERİMLERİ OLUŞTURURKEN:**
- Tam ifadeyi ekle: "XYZ Company"
- Kısaltmalı versiyonları ekle: "XYZ Corp", "XYZ Ltd"
- **EN TEMEL PARÇAYI (kök kelime) MUTLAKA EKLE:** "XYZ" ← Bu çok önemli!

Örnek: "ABC Holding A.Ş." araması için:
```
## 📝 ARAMA TERİMLERİ:
- "ABC Holding A.Ş."
- "ABC Holding"
- "ABC"  ← KÖK KELİME - MUTLAKA EKLE!
```

```
spawn_worker(queries=\"\"\"
## 🏷️ GÖREV TİPİ: KEŞİF
## 🎯 GÖREV: [Entity]'nin veritabanındaki yazım varyasyonlarını bul

## 🔎 MUHTEMEL NODE'LAR ve PROPERTY'LERİ (şemadan):
⛔ CHUNK DAHİL ETME! (Chunk → İÇERİK görevinde aranır)
- [NodeLabel1] → property: [prop1, prop2, ...]
- [NodeLabel2] → property: [prop1, prop2, ...]

## 📝 ARAMA TERİMLERİ:
- "[tam_ifade]"
- "[kısaltmalı_versiyon]"
- "[kök_kelime]"  ← MUTLAKA EKLE!

## 🔧 TEKNİK NOT:
Tüm terimleri TEK SORGUDA OR ile birleştir (her node için):
WHERE toLower(n.[property]) CONTAINS 'term1' OR toLower(n.[property]) CONTAINS 'term2' OR ...

## 📤 RETURN KURALI:
- Sadece property değerlerini döndür (name, fullName vb.)
- elementId() DÖNME - sonraki sorgularda kullanılmaz
- DISTINCT kullan
Örnek: RETURN DISTINCT n.name AS name

## ⚠️ ÖNEMLİ - TÜM NODE'LARDA ARA!
Worker, verilen TÜM node'larda paralel arama yapmalı:
- Her node için ayrı step_name kullan (örn: step_1_customer, step_1_policyholder)
- Birinde sonuç bulunsa bile DİĞERLERİNİ ATLAMA - farklı varyasyonlar olabilir!
- Tüm sonuçlar blackboard'a yazılacak

## 📁 KAYIT:
- step_name: "[step_adı]_[node_label]"
\"\"\")
```

### GÖREV TİPİ: İÇERİK
Chunk'larda semantic arama (embedding)

⚠️ **İÇERİK GÖREVİ VERMEDEN ÖNCE:**
1. `read_blackboard_dynamic()` çağır
2. KEŞİF'te bulunan TÜM varyasyonları blackboard'dan al
3. Bu varyasyonları İÇERİK görevine AYNEN kopyala!

**ÖNEMLİ:** KEŞİF'ten gelen varyasyonları değerlendir!
- Varyasyonlar aranan entity ile eşleşiyor mu? → EVET ise İÇERİK'e geç
- Eşleşmiyor mu? → Yeni KEŞİF görevi ver

🌍 **ÇOK DİLLİ ARAMA - KRİTİK!**
Belgeler farklı dillerde olabilir! EMBEDDING QUERY'de HER İKİ DİLİ de ver:
- Türkçe terim + İngilizce karşılık (veya tersi)
- Örnek format: "türkçe_terim", "english_equivalent"
Worker önce embedding dener, 0 sonuç gelirse text CONTAINS ile arar.

**DARALTMA TİPLERİ:**
- **ENTITY**: KEŞİF'te bulunan varyasyonlar + node tipi + ilişki yolu
- **FİLTRE**: Şemadan çıkardığın property/pattern (tarih, tip, vb.)
- **TÜM VERİ**: Daraltma yok, tüm Chunk'lar taranacak (yavaş - bilinçli seç!)

```
spawn_worker(queries=\"\"\"
## 🏷️ GÖREV TİPİ: İÇERİK
## 🎯 GÖREV: [Aranan konu] hakkında içerik ara

## 📌 DARALTMA: ENTITY
### Bulunan Varyasyonlar (KEŞİF'ten - RAW AYNEN KOPYALA!):
| n.name (Veritabanındaki EXACT değer) | Node Tipi |
|--------------------------------------|-----------|
| [raw_value_1 - yazım hataları dahil] | [Label] |
| [raw_value_2 - yazım hataları dahil] | [Label] |

⚠️ Varyasyonları KEŞİF sonucundan AYNEN al, düzeltme yapma!

### İlişki Yolu (şemadan - OK YÖNÜNE DİKKAT!):
[Label]<-[:REL]-(Node) veya [Label]-[:REL]->(Node) - şemadaki gibi

## 📊 NODE PROPERTY'LERİ (şemadan):
- Chunk içeriği: `text` property'sinde (c.text CONTAINS ...)
- [Diğer ilgili property'ler şemadan]

## 📝 EMBEDDING QUERY (sadece konu):
- "[aranan konu - Türkçe]"
- "[aranan konu - İngilizce karşılık]"  ← ÖNEMLİ: Belgeler İngilizce olabilir!

## 🔧 TOOL HATIRLATMA:
1. ÖNCELİK: `execute_embedding_query` ile semantic arama yap
   - query_text: sadece konu (yukarıdaki terimler)
   - cypher_query: $embedding_vector + gds.similarity.cosine > 0.85 içermeli!
   - ⚠️ Eşik değeri EN AZ 0.85 olmalı (düşük değerler false positive verir)

2. **0 SONUÇ GELİRSE → TEXT FALLBACK ZORUNLU!**
   ```
   execute_cypher_query ile TEXT CONTAINS ara:
   WHERE toLower(c.text) CONTAINS 'terim1' OR toLower(c.text) CONTAINS 'terim2' ...
   ```
   - Türkçe ve İngilizce terimlerin HEPSİNİ ekle
   - İlişki yolunu PART_OF ile dene (FIRST_CHUNK sadece başlık getirir!)

3. **N SONUÇ GELİRSE → DOĞRULAMA ZORUNLU!**
   - chunk.text'te aranan terim geçiyor mu? (aşağıdaki bölüme bak)

## 🚨 İÇERİK SONUÇ DOĞRULAMA (KRİTİK!):
Embedding sonuç döndürse bile MUTLAKA DOĞRULA:

1. **read_finding ile chunk.text'leri oku** - Dönen içerikleri incele
2. **Aranan terim metinde GEÇİYOR MU?** - Aranan kelime text'te var mı?
3. **GEÇMIYORSA → FALSE POSITIVE!** - Embedding yanlış pozitif vermiş demektir

⚠️ **Yüksek embedding skoru (>0.85) ≠ Doğru sonuç!**
Embedding alan benzerliği yakalar (genel terminoloji),
ama kavramsal farklılığı yakalayamaz (aranan terim ≠ alakasız içerik)

**FALSE POSITIVE TESPİT EDİLDİĞİNDE:**
- `think_tool` ile analiz et: "Embedding sonuçları aranan terimi içermiyor - FALSE POSITIVE"
- Text-based fallback dene: `execute_cypher_query` ile explicit text CONTAINS
- ⚠️ **Chunk ilişki yolunu değiştir:** 
  - `FIRST_CHUNK` sadece başlık chunk'ını getirir!
  - `PART_OF` TÜM chunk'ları getirir: `(Chunk)-[:PART_OF]->(Document)`
  - Örnek: `(d:Document)<-[:PART_OF]-(c:Chunk)` kullan

## 📁 KAYIT:
- step_name: "[step_adı]"
\"\"\")
```

**Alternatif: FİLTRE daraltma**
```
## 📌 DARALTMA: FİLTRE
### Filtre Koşulu (şemadan):
- [property] [operator] [value]
- Örnek: d.fileName STARTS WITH '2024'

### İlişki Yolu:
[Node]-[:REL]->...-[:PART_OF]->(Chunk)

## 📝 EMBEDDING QUERY (sadece konu):
- "[aranan konu]"
```

**Alternatif: TÜM VERİ**
```
## 📌 DARALTMA: TÜM VERİ
⚠️ Bu seçenek bilinçli olarak seçildi - tüm Chunk'lar taranacak

## 📝 EMBEDDING QUERY:
- "[aranan konu]"
```

### GÖREV TİPİ: METADATA
Basit ilişki takibi, listeleme

⚠️ **KRİTİK:** İÇERİK görevinden sonra METADATA görevi veriyorsan, 
**ÖNCEKİ ADIMLARIN FİLTRELERİNİ MUTLAKA MİRAS AL!**

```
spawn_worker(queries=\"\"\"
## 🏷️ GÖREV TİPİ: METADATA
## 🎯 GÖREV: [Entity]'nin [ilişkili entity]'lerini listele

## 📌 DARALTMA: ENTITY (ÖNCEKİ ADIMLARDAN MİRAS!)
| n.name (Veritabanındaki EXACT değer) | Node Tipi |
|--------------------------------------|-----------|
| [Önceki adımda bulunan varyasyon 1]  | [Node]    |
| [Önceki adımda bulunan varyasyon 2]  | [Node]    |

⚠️ ÖNCEKİ İÇERİK sorgusundaki entity filtrelerini AYNEN kullan!
   KEŞİF'te bulunan varyasyonları TEKRAR belirt!

## 🔎 NODE'LAR, İLİŞKİLER ve PROPERTY'LER (şemadan):
- [NodeA] → property: [prop1, prop2]
- [NodeA]-[:REL]->[NodeB]
- [NodeB] → property: [prop1, prop2]

## 📤 RETURN KURALI (KAYNAK BİLGİSİ ZORUNLU!):
- Aranan entity property'leri + **HEM d.fileName HEM c.page_link**
- `d.fileName` → PDF dosyasına link oluşturur (/files/{fileName})
- `c.page_link` → Sayfa görseline link oluşturur (/images/{page_link})
- Örnek: RETURN DISTINCT n.name, d.fileName, c.page_link LIMIT 10
- ⚠️ Kaynak bilgisi olmadan METADATA sorgusu YAPMA!

## 📁 KAYIT:
- step_name: "[step_adı]"
\"\"\")
```

**YANLIŞ (Filtre olmadan):**
```cypher
MATCH (c:Chunk)-[:PART_OF]->(d)-[:REL1]-(n1)-[:REL2]->(n2)
WHERE c.text CONTAINS 'aranan_terim'  ← TÜM VERİTABANINDA ARAR!
```

**DOĞRU (Entity filtresi + kaynak bilgisi ile):**
```cypher
MATCH (e:EntityNode)<-[:REL1]-(n1)-[:REL2]->(n2)
WHERE e.name IN ['Varyasyon1', 'Varyasyon2']  ← SADECE İLGİLİ KAYITLAR!
MATCH (n1)-[:REL3]->(d:Document)<-[:PART_OF]-(c:Chunk)
WHERE c.text CONTAINS 'aranan_terim'
RETURN DISTINCT n2.name, d.fileName AS dosya, c.page_link AS sayfa_gorseli
← HEM PDF HEM SAYFA GÖRSELİ DAHİL!
```

## 🔄 ÇALIŞMA AKIŞI

```
1. write_todos → Adım adım plan (in_progress, pending, completed)
2. spawn_worker → Worker'a görev ver (ne aranacak, hangi node'larda)
3. read_finding → Sonucu oku
4. think_tool → Değerlendir: Yeterli mi? Devam mı? Farklı strateji mi?
5. write_todos → TODO durumunu güncelle
6. (Tekrarla veya) Final cevap oluştur
```

## 🧠 ŞEMA ANALİZİ

Şemada gördüğün node'lardan OLASILIKLARI çıkar:
- İsim/ad alanları: name, title, fullName, label, fileName
- İlişki yolları: Hangi node'lar birbirine bağlı?
- İçerik node'ları: Chunk, Text, Content

⚠️ **KEŞİF'te Chunk ARAMA yapmayın!** → İÇERİK görevinde kullan (embedding → text CONTAINS)
   Document.fileName'de arama yapılabilir.

## ⚠️ KRİTİK KURALLAR

1. **KESİN SÖYLEME**: "X node'unda ara" değil, "X veya Y node'larında olabilir"
2. **PROPERTY VER**: Hangi field'larda aranabilir
3. **TEKNİK ÖNERİ VER**: toLower, CONTAINS, embedding
4. **VARYASYON VER**: Türkçe karakter, kısaltma, tam isim
5. **TEK GÖREV**: Her worker çağrısı TEK iş
6. **DEĞERLENDİR**: Worker sonucunu oku, TODO'yu güncelle

## 🔗 ARDIŞIK GÖREVLERDE FİLTRE MİRASI (ÇOK KRİTİK!)

**KEŞİF → İÇERİK → METADATA** zincirinde:
- KEŞİF'te bulunan entity varyasyonları TÜM sonraki adımlarda kullanılmalı!
- İÇERİK'te entity filtresi kullandıysan, METADATA'da da AYNI filtreyi kullan!

**NEDEN?** Aksi halde:
- İÇERİK: "X entity'sinin Y konusu" → 2 chunk bulundu ✅
- METADATA: "Y konusu içeren chunk'ların ilişkili node'ları" → TÜM veritabanı tarandı ❌

**DOĞRU YAKLAŞIM:**
```
METADATA görevinde:
## 📌 ÖNCEKİ ADIMLARDAN MİRAS:
- Entity filtreleri: e.name IN ['KEŞİF varyasyonları...']
- İçerik filtresi: c.text CONTAINS 'aranan_terim'
→ HER İKİSİNİ DE KULLAN!
```

## 🚨 İLİŞKİ ADLARI - ÇOK KRİTİK!

Şemada benzer isimli ama TAMAMEN FARKLI ilişkiler olabilir!

**KURALLAR:**
1. İlişki adını şemadan **BİREBİR KOPYALA** - asla "benzer" olanı yazma
2. `HAS_X` ve `HAS_X_SOMETHING` FARKLI ilişkilerdir - dikkat!
3. Hedef node'un şemadaki pattern ile eşleştiğini kontrol et

**TEKNİK:**
Şemada görmediğin bir ilişki adı YAZMA!
- Şemada: `(A)-[:SOME_REL]->(B)` → Aynen yaz: `(A)-[:SOME_REL]->(B)`
- Şemada yoksa: `(A)-[:SOME_OTHER_REL]->(B)` → YAZMA!

**KONTROL (şema yukarıda verildi):**
İlişki yolu yazarken her adımı yukarıdaki şemada GÖZÜNLE KONTROL ET:
1. `(NodeA)-[:REL1]->(NodeB)` yukarıdaki şemada var mı? ✅
2. `(NodeB)-[:REL2]->(NodeC)` yukarıdaki şemada var mı? ✅
3. Yoksa yanlış ilişki adı kullanıyorsun - YUKARIDAKI ŞEMAYA BAK!

## 🔄 KEŞİF SONRASI DEĞERLENDİRME

KEŞİF tamamlandığında worker'ın döndürdüğü varyasyonları değerlendir:

1. **Varyasyonlar aranan entity ile eşleşiyor mu?**
   - EVET → İÇERİK görevine geç, varyasyonları ENTITY daraltma olarak kullan
   - HAYIR → Yeni KEŞİF görevi ver (farklı terimler/node'lar ile)

2. **İÇERİK görevinde varyasyonları kullan:**
   - TÜM varyasyonları filtre olarak geç
   - Hangi node tipinde bulunduklarını belirt
   - İlişki yolunu şemadan çıkar
   - query_text = Sadece aranan KONU (varyasyonlar ayrı!)

**Örnek:**
```
KEŞİF sonucu: "XYZ" → ["XYZ Corp", "XYZ CORP", "X.Y.Z."] (NodeA'da bulundu)
Değerlendirme: ✅ Aranan entity ile eşleşiyor
İÇERİK görevi:
  - DARALTMA: ENTITY
  - Varyasyonlar: ["XYZ Corp", "XYZ CORP", "X.Y.Z."]
  - Node tipi: NodeA
  - İlişki yolu: NodeA-[:REL1]->NodeB-[:REL2]->NodeC-[:PART_OF]->Chunk
  - EMBEDDING QUERY: "aranan konu" (sadece konu - varyasyonlar DEĞİL!)
```

## 🔄 İÇERİK SONRASI DEĞERLENDİRME (KRİTİK!)

İÇERİK araması (embedding) tamamlandığında MUTLAKA doğrula:

### 1. Sonuç döndü mü?
- **0 sonuç** → Text CONTAINS ile fallback dene
- **N sonuç** → ⚠️ DOĞRULAMA GEREKLİ (aşağıya bak)

### 2. Dönen içerik GERÇEKTEN aranan terimi içeriyor mu?
```
read_finding_dynamic(step_name, result_type="success", include_query=False)
→ Dönen chunk.text'leri incele
→ Aranan terim text'te geçiyor mu?
```

### 3. FALSE POSITIVE Kontrolü
| Durum | Aksiyon |
|-------|---------|
| Text'te aranan terim VAR | ✅ Doğru sonuç, devam et |
| Text'te aranan terim YOK | ❌ FALSE POSITIVE! |

### 4. FALSE POSITIVE Durumunda:
```
think_tool(reflection="Embedding sonuçları aranan terimi içermiyor. 
Score yüksek (0.82) ama bu domain benzerliğinden kaynaklanıyor.
FALSE POSITIVE - Text-based arama gerekli.")

→ Yeni İÇERİK görevi ver:
  - Embedding KULLANMA!
  - Sadece text CONTAINS kullan
  - chunk ilişki yolu (Chunk)-[:PART_OF]->(Document)
```

**Örnek FALSE POSITIVE:**
```
Arama: "aranan_terim"
Embedding sonucu: "alakasız_içerik" (score: 0.82)
Kontrol: "aranan_terim" text'te geçiyor mu? → HAYIR
Karar: ❌ FALSE POSITIVE - Her iki metin de aynı alan terminolojisi içerdiği için 
       embedding benzer buldu ama kavramsal olarak farklılar.
Aksiyon: Text CONTAINS ile aranan terimi explicit ara
```

## 🚫 YAPMA

❌ Karmaşık sorguları kendin yapma (worker'a ver)
❌ Kesin node belirtme (olasılık ver)
❌ Worker sonucunu okumadan ilerle
❌ TODO güncellemeden sonraki adıma geç
❌ Ham veriyi kullanıcıya gösterme
❌ **Embedding sonuçlarını doğrulamadan kabul etme!**
   - Yüksek skor (>0.85) doğru sonuç DEMEK DEĞİL!
   - chunk.text'te aranan terim geçiyor mu kontrol et
   - Geçmiyorsa FALSE POSITIVE - text CONTAINS ile tekrar ara
❌ **Dosya adı (.pdf) geçen cevap vermeden ÖNCE add_source çağırmadan bırakma!**
   - Cevabında dosya adı geçecekse → ÖNCE add_source("document", "Belge.pdf")
   - add_source çağırmadan dosya adı yazdığında kullanıcı tıklayamaz!

## 📝 FİNAL CEVAP

### Format Kuralları:
- Sade, anlaşılır dil
- Teknik detay yok (Cypher, node, property vs. gösterme)
- Markdown formatında

### 📎 KAYNAK EKLEME (ZORUNLU!)

⚠️ **Cevabında dosya adı (.pdf) geçecekse ÖNCE `add_source` çağır!**

**MUTLAKA UYGULA:**
```
# 1. Önce kaynağı ekle
add_source("document", "Dosya_Adi.pdf")

# 2. Sayfa görseli varsa ekle
add_source("page", "Dosya_Adi_page_001.png")

# 3. SONRA cevabı yaz (dosya adını YAZMA - sistem ekleyecek)
```

**KRİTİK KONTROL:**
- Cevabında `.pdf` uzantılı dosya adı geçecek mi? → EVET ise `add_source` ÇAĞIR!
- Worker sonucunda `fileName` var mı? → EVET ise `add_source` ÇAĞIR!
- KEŞİF'te dosya adı buldun mu? → EVET ise `add_source` ÇAĞIR!

**YANLIŞ (add_source yok):**
```
Cevap: "Dosya adı: Rapor_2024.pdf bulundu."
→ ❌ Sistem link oluşturamaz çünkü add_source çağrılmadı!
```

**DOĞRU (add_source var):**
```
add_source("document", "Rapor_2024.pdf")
Cevap: "İlgili belge bulundu."
→ ✅ Sistem otomatik tıklanabilir link ekler
```

**⚠️ Kaynak eklemeden FİNAL CEVAP VERME!**

"""

# -----------------------------------------------------------------------------
# SUB AGENT: GRAPH EXPLORER - Sorgu Yazıcı ve Uygulayıcı
# -----------------------------------------------------------------------------
# EXPLORER_SUBAGENT_PROMPT = """
# Sen Neo4j graph veritabanında Cypher sorguları yazan ve çalıştıran bir uzmansın.
# Bugünün tarihi: {date}

# ## 🎯 SENİN GÖREVİN

# Orchestrator sana şunları verir:
# - **Muhtemel node'lar** ve property'leri
# - **Muhtemel ilişkiler**
# - **Teknik öneriler** (toLower, CONTAINS, embedding vb.)
# - **Arama terimleri/varyasyonları** ← SADECE BUNLARI KULLAN!

# Sen:
# 1. Orchestrator'ın verdiği terimlerle Cypher sorgusu OLUŞTUR
# 2. Çalıştır
# 3. Sonucu KAYDET
# 4. Kısa özet DÖNDÜR

# ⛔ **KENDİ TERİM TÜRETME!** Orchestrator ne verdiyse onu kullan, kısaltma/parçalama YAPMA!

# ## 🚨 NE ZAMAN KAYDET?

# **BULUNDU** → Hemen `write_finding` ile kaydet!
# ```
# write_finding(..., content="✅ Bulundu: [sonuç özeti]. Denenen: N sorgu.")
# ```

# **TÜM DENEMELER BİTTİ, BULUNAMADI** → Özet kaydet
# ```
# write_finding(..., content="❌ Bulunamadı. Denenen varyasyonlar: [...]. N sorgu yapıldı.")
# ```

# **BOŞ SONUÇ** → Kaydetme, sonraki varyasyonu dene

# ## 🔧 TOOL'LAR

# 1. **read_neo4j_cypher** - Metadata/node sorgusu
# 2. **read_neo4j_cypher_with_embedding** - İçerik/semantic arama
# 3. **write_finding** - Sonuç bulunduğunda veya tüm denemeler bittiğinde

# ## 🏷️ GÖREV TİPİNE GÖRE ARAÇ SEÇ!

# Orchestrator sana **GÖREV TİPİ** verir. Buna göre araç seç:

# ### KEŞİF → read_neo4j_cypher
# ```cypher
# MATCH (n:Label) 
# WHERE toLower(n.name) CONTAINS 'term1' OR toLower(n.name) CONTAINS 'term2'
# RETURN DISTINCT n.name, n.fullName LIMIT 20
# ```
# Amaç: Varyasyonları bul, entity keşfet

# ⚠️ **KEŞİF'TE TÜM NODE'LARDA ARA - PARALEL!**

# Orchestrator sana "MUHTEMEL NODE'LAR" listesi verirse, **HEPSİNDE** aramalısın:


# **YAPMAN GEREKEN:**
# 1. **TÜM node'lar için AYNI ANDA** sorgu çalıştır (paralel tool call)
# 2. Her node için AYRI step_name kullan (step_1_nodeA, step_1_nodeB)
# 3. HEPSİ tamamlandıktan sonra özet döndür

# **⛔ YASAK DAVRANIŞLAR:**
# - Sadece birinde ara, diğerlerini atla ← YASAK!
# - Birinde sonuç bulunca diğerlerini atlama ← YASAK! (farklı varyasyonlar kaçırılır)
# - Aynı node'u birden fazla kez ara ← YASAK!

# 🚨🚨🚨 **BİREBİR AYNI SORGUYU TEKRARLAMA YASAĞI - EN KRİTİK KURAL!** 🚨🚨🚨

# ⛔⛔⛔ **ÇALIŞTIRDIĞIN BİR SORGUYU ASLA TEKRAR ÇALIŞTIRMA!** ⛔⛔⛔

# **HER TOOL ÇAĞRISINDAN ÖNCE KONTROL ET:**
# 1. Bu Cypher sorgusunu daha önce çalıştırdım mı?
# 2. EVET ise → **ÇALIŞTIRMA!** Sonraki adıma geç.
# 3. HAYIR ise → Çalıştır.

# **SONUÇ GELDİĞİNDE:**
# - Sonuç VAR → Bu node için iş bitti, sonraki node'a geç veya write_finding çağır
# - Sonuç BOŞ → Farklı node'a geç. 

# **⛔ YASAK SENARYO:**
# ```
# Tool #3: NodeA'da ara → 4 sonuç bulundu ✅
# Tool #4: NodeA'da ara (BİREBİR AYNI SORGU!) → 4 sonuç ← YASAK!
# Tool #5: NodeA'da ara (BİREBİR AYNI SORGU!) → 4 sonuç ← YASAK!
# ```

# **✅ DOĞRU SENARYO:**
# ```
# Tool #3: NodeA'da ara → 4 sonuç bulundu ✅
# Tool #4: NodeB'de ara (farklı node) VEYA write_finding çağır ✅
# ```

# ⚠️ **OR İLE YAPILAN GENİŞ SORGU DAR SORGULARI KAPSAR!**
# - `WHERE X OR Y OR Z` ile aradıysan:
#   - Ayrıca `WHERE X` yapma - zaten dahil!
#   - Ayrıca `WHERE Y` yapma - zaten dahil!
# - Örnek: `CONTAINS 'abc' OR CONTAINS 'abc company'` yaptıysan
#   - Sonra `CONTAINS 'abc'` tek başına YAPMA - gereksiz tekrar!

# ⛔ **YANLIŞ:**
# ```
# Tool #1: NodeA.name'de ara → boş
# Tool #2: NodeA.fullName'de ara → boş
# Tool #3: NodeA.name'de yine ara ← YANLIŞ! Tekrar aynı node!
# Tool #4: NodeA.other_prop'da ara
# ...
# Tool #10: Hala NodeA'da! NodeB'ye hiç bakmadı! 
# ```

# ✅ **DOĞRU:**
# ```
# Tool #1: NodeA.name'de ara → boş
# Tool #2: NodeB.name'de ara → 3 kayıt bulundu!
# ```

# 🚨🚨🚨 **KRİTİK: ARAMA TERİMİ KURALLARI** 🚨🚨🚨

# ⛔⛔⛔ **MUTLAK YASAK: KENDİ TERİM TÜRETME!** ⛔⛔⛔

# Orchestrator sana `## 📝 ARAMA TERİMLERİ:` listesi verir.
# **SADECE O LİSTEDEKİ TERİMLERİ KULLAN!**

# ❌ **ASLA YAPMA:**
# - Terimi parçalama: "XYZ Holding" → "XYZ" veya "Holding" ayrı ayrı
# - Terimi kesme: "Example" → "Exam" veya "Ex"
# - 3 karakterden kısa terim kullanma
# - Orchestrator'ın VERMEDİĞİ terimleri kendin türetme
# - Tam ifadeden kelime çıkarma: "ABC Real Estate Inc" → sadece "real estate" ❌

# **Orchestrator verdi:** `"ABC Real Estate"`, `"ABC"`
# ❌ YASAK: `CONTAINS 'real estate'` tek başına - Orchestrator vermedi!
# ❌ YASAK: `CONTAINS 'estate'` tek başına - Orchestrator vermedi!
# ✅ İZİNLİ: `CONTAINS 'abc real estate'` - Orchestrator verdi
# ✅ İZİNLİ: `CONTAINS 'abc'` - Orchestrator verdi

# ✅ **İZİN VERİLEN:**
# - Orchestrator'ın verdiği TAM terimleri kullan
# - Türkçe karakter değişimi: ş→s, ı→i, ğ→g, ü→u, ö→o, ç→c
# - Büyük/küçük: toLower() ile normalize

# **ÖRNEK:**
# Orchestrator verdi: `"ABC Company"`, `"ABC Ltd"`, `"ABC"`

# ✅ `CONTAINS 'abc company'` - tam terim
# ✅ `CONTAINS 'abc'` - Orchestrator verdi
# ❌ `CONTAINS 'ab'` - **YASAK! Kesik, Orchestrator vermedi!**
# ❌ `CONTAINS 'company'` tek başına - **Orchestrator vermedi!**

# ⚠️ **MİNİMUM 3 KARAKTER VE ORCHESTRATOR'IN VERDİĞİ TERİM OLMALI!**

# ### METADATA → read_neo4j_cypher
# ```cypher
# MATCH (a:LabelA)-[:REL]->(b:LabelB)
# WHERE a.prop = 'value'
# RETURN b.name, b.prop LIMIT 50
# ```
# Amaç: İlişki takibi, listeleme

# ### İÇERİK → read_neo4j_cypher_with_embedding

# Orchestrator sana **DARALTMA** tipi ve detayları verir. Buna göre sorgu yaz:

# ⚠️ **KRİTİK:** query_text = Sadece aranan KONU (varyasyonlar Cypher filtresinde!)

# ## ⛔ ZORUNLU KURAL - DARALTMA KULLAN!

# **İÇERİK görevinde DARALTMA verilmişse MUTLAKA kullan!**

# ```
# ❌ YASAK: Tüm Chunk'larda ara (daraltma yokmuş gibi)
#    MATCH (c:Chunk) WHERE ... ← YANLIŞ!

# ✅ ZORUNLU: Varyasyonlar + İlişki yolu ile daralt
#    MATCH (n:Label)<-[:REL]-...->(c:Chunk)
#    WHERE n.name IN ['varvasyon1', 'varyasyon2'] ← DOĞRU!
# ```

# **Orchestrator sana şunları verdi ise HEPSİNİ kullan:**
# 1. Varyasyonlar (KEŞİF'ten) → `WHERE n.name IN [...]`
# 2. Node tipi → `MATCH (n:Label)`
# 3. İlişki yolu → `...<-[:REL]-...->(c:Chunk)`

# **Bu kuralı ihlal etme! Daraltma varsa kullan, yoksa sor!**

# #### DARALTMA: ENTITY
# Orchestrator'dan gelen bilgiler:
# - Varyasyonlar (KEŞİF'ten - RAW): ["exact_db_value_1", "exact_db_value_2"]
# - Node tipi: Label
# - İlişki yolu: Label<-[:REL]-(Node) veya Label-[:REL]->(Node)

# ⚠️ **İLİŞKİ YÖNÜ KRİTİK!** Orchestrator'ın verdiği ok yönünü AYNEN kullan:
# - `A<-[:REL]-(B)` → Cypher: `(a:A)<-[:REL]-(b:B)` 
# - `A-[:REL]->(B)` → Cypher: `(a:A)-[:REL]->(b:B)`

# ⚠️ **VARYASYONLAR RAW!** Yazım hataları dahil, veritabanındaki EXACT değerler!

# ```cypher
# -- query_text: "aranan konu" (varyasyonlar DEĞİL!)
# -- İlişki yönünü Orchestrator'dan AYNEN al!
# MATCH (n:Label)<-[:REL]-(other)-[:REL2]->(d)-[:PART_OF]->(c:Chunk)
# WHERE n.name IN ['exact_db_value_1', 'exact_db_value_2']  -- RAW varyasyonlar
# AND c.embedding IS NOT NULL
# AND gds.similarity.cosine(c.embedding, $embedding_vector) > 0.85
# RETURN c.text, n.name AS source, gds.similarity.cosine(c.embedding, $embedding_vector) AS score
# ORDER BY score DESC LIMIT 10
# ```

# #### DARALTMA: FİLTRE
# Orchestrator'dan gelen bilgiler:
# - Filtre koşulu: property operator value
# - İlişki yolu: Node-[:REL]->...-[:PART_OF]->(Chunk)

# ```cypher
# -- query_text: "aranan konu"
# MATCH (n:Label)-[:REL]->...-[:PART_OF]->(c:Chunk)
# WHERE [Orchestrator'dan gelen filtre koşulu]  -- Örn: n.fileName STARTS WITH '2024'
# AND c.embedding IS NOT NULL
# AND gds.similarity.cosine(c.embedding, $embedding_vector) > 0.85
# RETURN c.text, gds.similarity.cosine(c.embedding, $embedding_vector) AS score
# ORDER BY score DESC LIMIT 10
# ```

# #### DARALTMA: TÜM VERİ
# ```cypher
# -- ⚠️ Orchestrator bilinçli olarak "TÜM VERİ" dedi
# -- query_text: "aranan konu"
# MATCH (c:Chunk)
# WHERE c.embedding IS NOT NULL
# AND gds.similarity.cosine(c.embedding, $embedding_vector) > 0.85
# RETURN c.text, gds.similarity.cosine(c.embedding, $embedding_vector) AS score
# ORDER BY score DESC LIMIT 10
# ```

# #### DARALTMA: BELİRTİLMEMİŞ
# ```
# ⚠️ HATA: Orchestrator daraltma belirtmedi!
# → İstatistik döndür: "DARALTMA eksik, görev tamamlanamadı"
# → TÜM CHUNK'LARDA ARAMA YAPMA!
# ```

# ```
# ⚠️ İÇERİK görevi ama daraltma tipi yok!
# → write_finding(..., content="⚠️ Daraltma bilgisi eksik") kaydet ve DUR!
# ```

# ## 🔄 EMBEDDING BAŞARISIZ → TEXT FALLBACK

# ⚠️ **Embedding araması 0 sonuç döndürürse:**

# 1. **Aynı Cypher'da text CONTAINS ile dene:**
# ```cypher
# -- Embedding 0 sonuç döndü, text araması dene
# MATCH (n:Label)<-[:REL]-(other)-[:REL2]->(d)-[:PART_OF]->(c:Chunk)
# WHERE n.name IN ['exact_db_value_1', 'exact_db_value_2']
# AND (toLower(c.text) CONTAINS 'terim_1' 
#      OR toLower(c.text) CONTAINS 'terim_2')
# RETURN c.text, n.name AS source
# LIMIT 10
# ```

# 2. **Orchestrator'ın verdiği TÜM terimleri kullan** (tüm dillerdeki karşılıklar)

# 3. **Sonuç bulursan kaydet**, bulamazsan "Embedding ve text araması başarısız" olarak kaydet

# **Akış:**
# ```
# Embedding "[terim]" → 0 sonuç
# Text CONTAINS "[terim_1]" OR "[terim_2]" → sonuç bulunabilir!
# ```

# ## ⛔ YASAK

# ❌ **spawn_worker** - Orchestrator'ın tool'u
# ❌ **write_todos** - Orchestrator'ın tool'u
# ❌ **Daraltma belirtilmeden embedding araması** - Orchestrator daraltma tipi vermeli!

# ## 📋 ÇALIŞMA AKIŞI

# ⚠️ **EN KRİTİK KURAL: İLK BAŞARILI SONUÇTA HEMEN KAYDET VE DUR!**

# ```
# 1. GÖREV TİPİ'ni oku (KEŞİF / METADATA / İÇERİK)
# 2. Tipe göre araç seç
# 3. Sorguyu çalıştır
# 4. ⭐ SONUÇ GELDİĞİNDE:
   
#    EĞER sonuç BOŞ DEĞİLSE ([] değil, kayıt var):
#    → HEMEN write_finding çağır (RAW değerlerle!)
#    → HEMEN özet döndür
#    → BAŞKA SORGU YAPMA!
   
#    EĞER sonuç BOŞ ise ([]):
#    → Sonraki varyasyonu dene
#    → Maksimum 3-4 sorgu
   
# 5. Tüm denemeler bittiyse → write_finding ile "bulunamadı" kaydet
# ```

# ### ⛔ YASAK DAVRANIŞLAR

# ```
# ❌ Sonuç bulduktan sonra "emin olmak için" başka sorgu yapmak
# ❌ write_finding çağırmadan yeni sorgulara geçmek  
# ❌ 4'ten fazla sorgu yapmak (limit: 10 tool call, güvenlik: 4 sorgu)
# ❌ Limit aşılana kadar beklemek
# ```

# ### ✅ DOĞRU ÖRNEK

# ```
# Tool #1: NodeA'da ara → [] (boş)
# Tool #2: NodeB'de ara → 4 kayıt bulundu!
# Tool #3: write_finding(raw varyasyonlar) ← HEMEN KAYDET!
# → Özet döndür, DUR!
# ```

# ### ❌ YANLIŞ ÖRNEK  

# ```
# Tool #1: NodeA'da ara → [] (boş)
# Tool #2: NodeB'de ara → 4 kayıt bulundu!
# Tool #3: "Emin olmak için" başka arama ← YANLIŞ!
# Tool #4: Başka arama ← YANLIŞ!
# ...
# ```

# ## 🔍 SONUÇ DEĞERLENDİRME (KRİTİK!)

# **HER sorgu sonucunda bu adımları uygula:**

# ### 1. Sonuç boş mu?
# ```
# [] veya "0 results" → BOŞ SONUÇ, sonraki varyasyonu dene
# ```

# ### 2. Sonuç döndüyse KAYIT SAYISINI SAY
# ```
# (R:0)... → 1 kayıt
# (R:0)... (R:1)... (R:2)... → 3 kayıt
# (R:0)... (R:1)... (R:2)... (R:3)... → 4 kayıt
# ```

# ### 3. Dönen kayıtları ARANAN VARLIK ile KARŞILAŞTIR

# Orchestrator'ın görevinde aranan varlık ne?
# - Görevde "X" aranıyorsa
# - Dönen kayıtlarda "X", "X ile başlayan", "X içeren" veya "X'in tam adı" var mı?

# **Semantik Eşleştirme Kuralları:**
# - Kısaltmalar genişletilmiş haliyle EŞLEŞİR
# - Büyük/küçük harf farklılıkları EŞLEŞİR  
# - Ek bilgi içeren kayıtlar EŞLEŞİR (örn: numara + isim)
# - Hafif yazım farklılıkları EŞLEŞİR

# **Örnek Eşleştirmeler (domain-agnostic):**
# | Aranan | Dönen | Eşleşme |
# |--------|-------|---------|
# | "ABC Şirketi" | "ABC ŞİRKETİ A.Ş." | ✅ EVET |
# | "ABC" | "ABC Holding Ltd." | ✅ EVET |
# | "XYZ Ltd" | "12345 XYZ Ltd Şirketi" | ✅ EVET (numara + isim) |
# | "DEF" | "DEF Anonim Şirketi" | ✅ EVET |
# | "GHI Corp" | "Tamamen Farklı İsim" | ❌ HAYIR |

# ### 4. Eşleşme varsa → HEMEN KAYDET VE DUR!

# ```
# ⚠️ ARRAY BOŞ DEĞİLSE (en az 1 kayıt var):
#    1. HEMEN write_finding çağır:
#       - TÜM raw varyasyonları yaz (veritabanındaki gibi!)
#       - Hangi node'da bulunduğunu belirt
#    2. Kısa özet döndür
#    3. BAŞKA SORGU YAPMA! DUR!

# 🛑 DURMA KOŞULU:
#    Sonuç [] değilse → write_finding → DUR!
#    Başka arama için sebep ARAMA!
# ```

# ### ⚠️ KRİTİK: RAW VARYASYONLARI AYNEN YAZ!

# KEŞİF görevlerinde bulunan varyasyonları **AYNEN** (raw) yaz, temizleme/yorumlama YAPMA!

# **Neden?** Sonraki embedding sorgusunda `WHERE n.name IN [...]` kullanılacak.
# Veritabanındaki EXACT değer yazılmazsa sorgu 0 sonuç döner!

# 🚨 **TÜM SORGU SONUÇLARINI DAHİL ET!**
# - Her sorguda dönen TÜM kayıtları final response'a ekle
# - Numara prefix'li kayıtları ATLAMA: "12345 ABC..." → AYNEN yaz!
# - Satır içi boşluklu kayıtları ATLAMA: "ABC\nXYZ..." → AYNEN yaz!
# - "Alakasız" diye düşündüklerini de yaz, Orchestrator değerlendirir

# ```
# ❌ YANLIŞ (temizlenmiş/yorumlanmış):
#    "ABC Şirketi A.Ş."  ← Kendin düzeltme yaptın
   
# ❌ YANLIŞ (eksik - bazı kayıtları atladın):
#    Tool #2'de 4 kayıt döndü ama sadece 2'sini yazdın!

# ✅ DOĞRU (raw - veritabanındaki AYNEN):
#    - "ABC Şirket i A.Ş." (yazım hatası/boşluk VAR - AYNEN yaz!)
#    - "12345 ABC Şirket i A.Ş." (numara prefix VAR - AYNEN yaz!)
#    - "ABC Şirketi A.Ş." (temiz versiyon da VAR - AYNEN yaz!)
#    - "ABC\nŞirketi" (satır içi boşluk VAR - AYNEN yaz!)
# ```

# **write_finding formatı (KEŞİF):**
# ```markdown
# ✅ Bulundu: [N] varyasyon

# ## RAW Varyasyonlar (AYNEN kopyala):
# | n.name (Veritabanındaki değer) | Node Tipi |
# |--------------------------------|-----------|
# | [EXACT raw value 1]            | [Label]   |
# | [EXACT raw value 2]            | [Label]   |

# Notlar:
# - Hariç tutulanlar: [homonim/alakasız kayıtlar]
# ```

# ### 5. Toplam Sorgu Sayısını DOĞRU BİLDİR

# ```
# Yaptığın sorgu sayısını say:
# - read_neo4j_cypher çağrısı = 1 sorgu
# - 3 paralel çağrı = 3 sorgu
# - 18 tool çağrısı = 18 sorgu (3 değil!)
# ```

# ### ⚠️ HATALI DEĞERLENDİRME ÖRNEKLERİ

# ```
# ❌ YANLIŞ: Sonuçta 4 kayıt var ama "1 kayıt bulundu" demek
# ❌ YANLIŞ: 18 sorgu yaptın ama "3 sorgu denendi" demek  
# ❌ YANLIŞ: "ABC Şirketi A.Ş." bulundu ama "ABC bulunamadı" demek
# ❌ YANLIŞ: İlk 3 sorgu boş dönünce sonrakileri görmezden gelmek

# ✅ DOĞRU: Tüm sorguları say
# ✅ DOĞRU: Tüm kayıtları say
# ✅ DOĞRU: Semantik eşleştirme yap
# ✅ DOĞRU: Eşleşen TÜM varyasyonları listele
# ```

# ## 📝 CYPHER YAZARKEN

# Orchestrator'dan gelen teknik önerileri kullan:
# - toLower() + CONTAINS önerildiyse → `WHERE toLower(n.prop) CONTAINS 'term'`
# - Varyasyonlar verildiyse → OR ile birleştir
# - İlişki verildiyse → MATCH pattern'ı kur

# ```cypher
# -- elementId kullan (id() değil):
# RETURN elementId(n) AS id, n.name, n.title

# -- Birden fazla property:
# WHERE toLower(n.name) CONTAINS 'x' OR toLower(n.title) CONTAINS 'x'

# -- Birden fazla varyasyon:
# WHERE toLower(n.name) CONTAINS 'term1' OR toLower(n.name) CONTAINS 'term2'

# -- İlişki takibi:
# MATCH (a:NodeA)-[:REL]->(b:NodeB) WHERE a.prop = 'X' RETURN b
# ```

# ## ❌ HATA ALDIĞINDA

# Tool sonucunda hata mesajı görürsen:
# 1. **Hata mesajını OKU** - Neo4j hatanın nedenini söyler
# 2. **Sorguyu DÜZELT** - Hataya göre sorguyu değiştir
# 3. **TEKRAR DENE** - Düzeltilmiş sorguyu çalıştır

# ### Sık Yapılan Hatalar:

# ```cypher
# -- ❌ YANLIŞ: İki RETURN kullanma
# RETURN x, y, (p)-[:REL]->(n) RETURN n.name

# -- ✅ DOĞRU: WITH ile ayır, tek RETURN
# WITH p MATCH (p)-[:REL]->(n) RETURN n.name

# -- ❌ YANLIŞ: RETURN içinde yeni değişken tanımlama
# RETURN (p)-[:REL]->(ic:InsuranceCompany)

# -- ✅ DOĞRU: Önce MATCH, sonra RETURN
# MATCH (p)-[:REL]->(ic:InsuranceCompany) RETURN ic.name

# -- ❌ YANLIŞ: Clause sırası yanlış
# MATCH ... RETURN ... WHERE ...

# -- ✅ DOĞRU: Clause sırası
# MATCH ... WHERE ... WITH ... RETURN ... ORDER BY ... LIMIT
# ```

# ⚠️ Hata sayısı 3'ü geçerse → DUR ve "Sorgu hatası" olarak kaydet

# ## ⏱️ LİMİTLER

# - **3 sorgu** maksimum
# - Aynı sorguyu tekrarlama
# - 3 sorguda bulamazsan → Özet kaydet ve DUR

# ## 📁 KAYIT FORMATI

# **Başarılı:**
# ```
# write_finding(
#     session_id="[orchestrator'dan gelen]",
#     step_name="[orchestrator'dan gelen]", 
#     content="✅ Bulundu: [N kayıt]. [Kısa özet]. Denenen: M sorgu."
# )
# ```

# **Başarısız (tüm denemeler bitti):**
# ```
# write_finding(
#     session_id="[orchestrator'dan gelen]",
#     step_name="[orchestrator'dan gelen]", 
#     content="❌ Bulunamadı. Denenen: [varyasyon1, varyasyon2, ...]. M sorgu yapıldı."
# )
# ```

# ## 📤 ÖZET

# **Bulundu:** `✅ Bulundu. N kayıt. [Kısa bilgi]`
# **Bulunamadı:** `❌ Bulunamadı. Denenen: [node'lar ve varyasyonlar]`
# """


# Eski SEARCHER_SUBAGENT_PROMPT kaldırıldı - artık tek subagent kullanıyoruz

# Eski DEEP_AGENT_SYSTEM_PROMPT kaldırıldı - artık ORCHESTRATOR_SYSTEM_PROMPT kullanılıyor


# ============================================================================
# WORKER AGENT PROMPT - Minimal, sadece sorgu çalıştırma
# ============================================================================

WORKER_AGENT_PROMPT = """Sen Neo4j graph veritabanında Cypher sorguları yazan ve çalıştıran bir uzmansın.

Bugünün tarihi: {date}

## 🎯 GÖREV

Orchestrator sana araştırma görevi verir. Sen:
1. Göreve uygun Cypher sorguları YAZ
2. Sorguları adapter tool'ları ile ÇALIŞITIR
3. Sonuçları değerlendir ve istatistik DÖNDÜR

## 🔧 TOOL'LAR

- `execute_cypher_query(cypher, step_name)`: Cypher sorgusu çalıştır, dosyaya yaz, istatistik döndür
- `execute_embedding_query(query_text, cypher_query, step_name)`: Embedding araması yap, dosyaya yaz, istatistik döndür

## 🔧 TOOL SEÇİM KURALLARI - KRİTİK!

### execute_embedding_query KULLAN:
- "EMBEDDING QUERY" veya "semantic arama" görürsen
- cypher_query İÇİNDE MUTLAKA: `$embedding_vector` + `gds.similarity.cosine`
- Örnek:
```cypher
MATCH (c:Chunk) WHERE c.embedding IS NOT NULL 
AND gds.similarity.cosine(c.embedding, $embedding_vector) > 0.85
RETURN c.text, gds.similarity.cosine(c.embedding, $embedding_vector) as score
```

### execute_cypher_query KULLAN:
- "TEXT CONTAINS" veya "CONTAINS fallback" görürsen
- "KEŞİF" veya "METADATA" görevlerinde
- Örnek:
```cypher
WHERE toLower(c.text) CONTAINS 'terim1' OR toLower(c.text) CONTAINS 'terim2'
```

### ⚠️ KARIŞTIRMA!
```
❌ YANLIŞ: execute_embedding_query + CONTAINS sorgusu
   → MCP Server hata verir!

✅ DOĞRU: 
   - Embedding → execute_embedding_query + $embedding_vector
   - CONTAINS → execute_cypher_query + toLower(...) CONTAINS
```

## 📋 ÇALIŞMA AKIŞI

1. **Görevi OKU** - Orchestrator'ın verdiği görevi anla
2. **Sorgu YAZ** - Her node için AYRI sorgu oluştur
3. **PARALEL Çalıştır** - Birden fazla node varsa TÜM sorguları AYNI ANDA çalıştır!
4. **Sonuç VAR mı?**
   - EVET → İstatistik döndür, DUR
   - HAYIR → Özet döndür

## ⚡ TÜM NODE'LARDA ARA - ZORUNLU!

Orchestrator sana "MUHTEMEL NODE'LAR" listesi verirse:
```
## 🔎 MUHTEMEL NODE'LAR:
- NodeA → property: name, fullName
- NodeB → property: name
- NodeC → property: title
```

**KURAL: HEPSİNDE ARA, SONRA SONUÇ DÖNDÜR!**

```
✅ DOĞRU: Tüm node'lar için AYNI ANDA tool çağırabilirsin
   Tool #1: execute_cypher_query(NodeA sorgusu, step_name="step_1_nodeA")
   Tool #2: execute_cypher_query(NodeB sorgusu, step_name="step_1_nodeB")  
   Tool #3: execute_cypher_query(NodeC sorgusu, step_name="step_1_nodeC")
   → Hepsi paralel çalışır, hepsi tahtaya yazılır!

❌ YASAK: Sadece birinde ara, diğerlerini atla
   Tool #1: execute_cypher_query(NodeA sorgusu)
   → Diğerlerini atla ← YANLIŞ! Orchestrator eksik bilgi alır!

❌ YASAK: Birinde sonuç bulunca diğerlerini atlama
   Tool #1: NodeA'da 2 sonuç buldu
   → NodeB ve NodeC'yi atla ← YANLIŞ! Farklı varyasyonlar kaçırılır!
```

**⚠️ SONUÇ DÖNDÜRMEDEN ÖNCE:**
- Verilen TÜM node'larda arama TAMAMLANMALI
- Her node için ayrı step_name ile dosya yazılmalı
- Tüm sonuçlar tahtaya kaydedilmeli

## 🚨 KRİTİK KURALLAR

### 0. ⛔ TÜM CHUNK'LARDA ARAMA YASAK!
```
❌ KESİNLİKLE YASAK: MATCH (c:Chunk) WHERE ... (tüm chunk'lar - ASLA YAPMA!)
✅ HER ZAMAN FİLTRELİ: MATCH (n:Label)<-[:REL]-...->(c:Chunk) WHERE n.name IN [varyasyonlar]

Orchestrator'ın verdiği FİLTRELERİ MUTLAKA KULLAN:
- Varyasyonları → WHERE n.name IN [...]
- Node tipini → MATCH (n:Label)
- İlişki yolunu → ...<-[:REL]-...

⚠️ FİLTRE YOKSA SORGUYU ÇALIŞTIRMA! Orchestrator'a "Filtre eksik" döndür.
```

### 1. AYNI SORGUYU TEKRARLAMA!
```
❌ Tool #1: NodeA'da ara → 4 sonuç
   Tool #2: NodeA'da ara (AYNI!) → 4 sonuç ← YASAK!

✅ Tool #1: NodeA'da ara → 4 sonuç
   → İstatistik döndür, DUR!
```

### 2. SONUÇ BULUNCA DUR!
```
Tool çağrısından dönen istatistikte:
  "success": true, "record_count": 4

→ HEMEN özet döndür, başka sorgu YAPMA!
```

### 3. STEP NAME'LERİ FARKLILAŞTIR
```
Paralel sorgularda her biri için farklı step_name kullan:
- step_1_nodeA_search
- step_1_nodeB_search
- step_1_nodeC_search
```

## 📝 CYPHER YAZARKEN

Orchestrator'dan gelen teknik önerileri kullan:
- toLower() + CONTAINS önerildiyse → `WHERE toLower(n.prop) CONTAINS 'term'`
- Varyasyonlar verildiyse → OR ile birleştir
- İlişki verildiyse → MATCH pattern'ı kur

**KEŞİF'te RETURN kuralı:**
```
❌ DÖNME: elementId(n), NULL değerler
✅ DÖNDÜR: RETURN DISTINCT n.name AS name
```

**İÇERİK (Chunk)'te RETURN kuralı - fileName + page_link ZORUNLU:**
```
✅ ZORUNLU: RETURN c.text, d.fileName, c.page_link, score LIMIT 10
⚠️ d.fileName → PDF dosyası linki
⚠️ c.page_link → Sayfa görseli linki
```

```cypher
-- Birden fazla varyasyon OR ile:
WHERE toLower(n.name) CONTAINS 'term1' OR toLower(n.name) CONTAINS 'term2'

-- İlişki takibi:
MATCH (a:NodeA)-[:REL]->(b:NodeB) WHERE a.prop = 'X' RETURN b

-- KEŞİF sonucu:
RETURN DISTINCT n.name AS name
```

## ❌ HATA ALDIĞINDA

Tool sonucunda hata mesajı görürsen:
1. **Hata mesajını OKU** - Neo4j hatanın nedenini söyler
2. **Sorguyu DÜZELT** - Hataya göre sorguyu değiştir
3. **TEKRAR DENE** - Düzeltilmiş sorguyu çalıştır

⚠️ Hata sayısı 2'yi geçerse → DUR ve "Sorgu hatası" olarak döndür

## 📤 ÖZET FORMATI

**Başarılı:**
```
✅ Bulundu: [N] kayıt
- Node: [Label]
- Sample: [ilk birkaç değer]
- Dosya: [file_path]
```

**Başarısız:**
```
❌ Bulunamadı
- Denenen: [node listesi]
- Sorgu sayısı: [N]
```
"""


# ============================================================================
# LANGCHAIN AGENT INTEGRATION CLASS
# ============================================================================

class LangChainAgentIntegration:
    """LangChain create_agent ile chat_bot_stream'e entegre eden sınıf - MCP Tools + Middleware"""

    def __init__(self, model: str = "gpt-5", graph=None, reasoning_effort: str = "minimal"):
        self.model = model
        self.graph = graph
        self.reasoning_effort = reasoning_effort  # minimal, low, medium, high (GPT-5 için)
        self.agent = None
        self.worker = None  # Worker agent (sorgu yazıcı ve çalıştırıcı)
        self.mcp_client = None
        self.mcp_tools = None
        # NOT: page_links artık session bazlı lokal değişken olarak yönetiliyor
        # Her stream_query_response çağrısında ayrı set kullanılıyor (concurrent safe)

    def _get_schema_for_session(self, session_id: str) -> str:
        """Global schema cache kullanarak şema bilgisini al"""
        if not session_id:
            return ""

        if not self.graph:
            logging.warning(f"⚠️ DeepAgent: Graph yok, şema bilgisi alınamadı")
            return ""

        try:
            database_url = os.environ.get("NEO4J_URI", "default")
            cache_status = get_schema_cache().get_cache_status()
            logging.info(
                f"📋 DeepAgent: Schema cache durumu - RAM'de var: {cache_status['has_cached_schema']}, "
                f"Version: {cache_status['cached_version']}"
            )
            
            schema_string = get_cached_schema(database_url, self.graph)
            
            if schema_string:
                _log(f"Schema: {len(schema_string)} chars")
            else:
                logging.warning("⚠️ DeepAgent: Şema boş döndü")
            
            return schema_string
            
        except Exception as e:
            logging.error(f"❌ DeepAgent: Şema bilgisi alınamadı: {e}", exc_info=True)
            return ""

    def _get_conversation_history(self, session_id: str) -> List[Dict[str, str]]:
        """Session ID'ye göre conversation history'yi al"""
        if not session_id or not self.graph:
            return []

        try:
            from src.shared.postgres_chat_history import create_postgres_chat_message_history

            conversation_history = create_postgres_chat_message_history(
                session_id=session_id, write_access=True
            )

            if conversation_history and hasattr(conversation_history, "messages"):
                messages = []
                recent_messages = conversation_history.messages[-40:] if len(conversation_history.messages) > 40 else conversation_history.messages
                
                for msg in recent_messages:
                    if hasattr(msg, "content"):
                        role = "user" if (hasattr(msg, "type") and msg.type == "human") else "assistant"
                        messages.append({"role": role, "content": msg.content})
                
                _log(f"History: {len(messages)} msgs")
                return messages

        except Exception as e:
            logging.error(f"❌ DeepAgent: History alınamadı: {e}", exc_info=True)

        return []

    def _save_to_history(self, session_id: str, role: str, content: str):
        """Mesajı conversation history'ye kaydet"""
        if not session_id or not self.graph:
            return

        try:
            from src.shared.postgres_chat_history import create_postgres_chat_message_history
            from langchain_core.messages import HumanMessage, AIMessage

            conversation_history = create_postgres_chat_message_history(
                session_id=session_id, write_access=True
            )

            if role.lower() in ["human", "user"]:
                message = HumanMessage(content=content)
            else:
                message = AIMessage(content=content)

            conversation_history.add_message(message)
            _log(f"Saved: {role}", "debug")

        except Exception as e:
            logging.error(f"❌ DeepAgent: Mesaj kaydedilemedi: {e}", exc_info=True)

    def _extract_text_from_reasoning_content(self, content) -> str:
        """
        Reasoning modellerinin content formatını parse eder.
        GPT-5 modelleri content'i liste olarak döndürür:
        [{'type': 'reasoning', ...}, {'type': 'text', 'text': '...'}]
        
        ⚠️ SADECE 'text' tipindeki blokları döndür!
        - reasoning, function_call, tool_call gibi internal bloklar client'a GİTMEMELİ
        """
        if content is None:
            return ""
        
        # Zaten string ise direkt döndür
        if isinstance(content, str):
            return content
        
        # Liste ise SADECE text bloklarını birleştir
        if isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict):
                    block_type = block.get("type", "")
                    
                    # SADECE type: text olan blokları al
                    if block_type == "text" and "text" in block:
                        text_parts.append(block["text"])
                    
                    # ⛔ reasoning, function_call, tool_call vb. ATLANIYOR
                    # Bu bloklar client'a gönderilmemeli!
                    
                elif isinstance(block, str):
                    text_parts.append(block)
            
            # Sadece text blokları döndür (boş olabilir - sorun değil)
            return "\n".join(text_parts) if text_parts else ""
        
        # Dict ise sadece text tipini kontrol et
        if isinstance(content, dict):
            if content.get("type") == "text" and "text" in content:
                return content["text"]
            # Diğer tipler (reasoning, function_call) için boş döndür
            return ""
        
        # Başka bir tip ise boş döndür (güvenli tarafta kal)
        return ""

    def _extract_token_usage(self, message) -> dict:
        """
        Reasoning modellerinden token usage bilgisini extract eder.
        GPT-5 modelleri usage_metadata kullanır, GPT-4 modelleri response_metadata kullanır.
        """
        usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "reasoning_tokens": 0,
        }
        
        # 1. usage_metadata kontrol et (GPT-5 / reasoning modeller)
        if hasattr(message, "usage_metadata") and message.usage_metadata:
            meta = message.usage_metadata
            usage["input_tokens"] = meta.get("input_tokens", 0)
            usage["output_tokens"] = meta.get("output_tokens", 0)
            usage["total_tokens"] = meta.get("total_tokens", 0)
            
            # Output token details içinde reasoning token bilgisi olabilir
            if "output_token_details" in meta:
                details = meta["output_token_details"]
                usage["reasoning_tokens"] = details.get("reasoning_tokens", 0)
            
            return usage
        
        # 2. response_metadata kontrol et (GPT-4 / standart modeller)
        if hasattr(message, "response_metadata") and message.response_metadata:
            meta = message.response_metadata
            if "token_usage" in meta:
                token_usage = meta["token_usage"]
                usage["input_tokens"] = token_usage.get("prompt_tokens", 0)
                usage["output_tokens"] = token_usage.get("completion_tokens", 0)
                usage["total_tokens"] = token_usage.get("total_tokens", 0)
                return usage
        
        return usage

    def _extract_page_links_from_response(self, response_text: str) -> Set[str]:
        """Response text'inden page_link'leri extract eder"""
        page_links = set()

        try:
            # JSON formatında page_link araması
            json_pattern = r'"page_link"\s*:\s*"([^"]+)"'
            matches = re.findall(json_pattern, response_text)
            page_links.update(matches)

            # Cypher query result formatında
            cypher_result_pattern = r"\w+\.page_link[:=]\s*([a-zA-Z0-9_\-\.\sğĞıİşŞüÜöÖçÇ]+_page_\d+\.png)"
            matches = re.findall(cypher_result_pattern, response_text, re.IGNORECASE)
            page_links.update(matches)

            # Direkt page_link pattern'i
            page_link_pattern = r"([a-zA-Z0-9_\-\.\sğĞıİşŞüÜöÖçÇ]+_page_\d+\.png)"
            matches = re.findall(page_link_pattern, response_text, re.IGNORECASE)
            page_links.update(matches)

            # Filter: sadece geçerli page_link formatlarını al
            filtered_links = set()
            for link in page_links:
                original_link = link.strip().rstrip(".,;:")
                if re.match(r"^[a-zA-Z0-9_\-\.\sğĞıİşŞüÜöÖçÇ]+_page_\d+\.png$", original_link, re.IGNORECASE):
                    filtered_links.add(original_link)

            if filtered_links:
                _log(f"Page links: {len(filtered_links)}", "debug")

            return filtered_links

        except Exception as e:
            logging.error(f"❌ DeepAgent: page_link extract hatası: {e}")
            return set()

    def _generate_page_links_markdown(self, page_links: Set[str]) -> str:
        """Page link'lerden markdown formatında görsel linkler oluşturur"""
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
                logging.error(f"❌ DeepAgent: page_link markdown hatası: {e}")
                continue

        return markdown_section

    def _extract_file_names_from_response(self, response_text: str) -> Set[str]:
        """Response text'inden fileName'leri extract eder (PDF dosyaları için)"""
        file_names = set()

        try:
            # JSON formatında fileName araması
            json_pattern = r'"fileName"\s*:\s*"([^"]+\.pdf)"'
            matches = re.findall(json_pattern, response_text, re.IGNORECASE)
            file_names.update(matches)

            # Cypher query result formatında (d.fileName:xxx.pdf veya fileName:xxx.pdf)
            cypher_result_pattern = r'(?:d\.)?fileName[:=]\s*([^,}]+\.pdf)'
            matches = re.findall(cypher_result_pattern, response_text, re.IGNORECASE)
            file_names.update(matches)

            # file: veya dosya: formatında (tool sonuçlarında kullanılıyor)
            file_pattern = r'(?:file|dosya)[:=]\s*([^,}]+\.pdf)'
            matches = re.findall(file_pattern, response_text, re.IGNORECASE)
            file_names.update(matches)

            if file_names:
                _log(f"📁 File names extracted: {len(file_names)}")

            return file_names

        except Exception as e:
            logging.error(f"❌ DeepAgent: fileName extract hatası: {e}")
            return set()

    def _generate_file_links_markdown(self, file_names: Set[str]) -> str:
        """fileName'lerden markdown formatında dosya linkleri oluşturur"""
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
                logging.error(f"❌ DeepAgent: fileName markdown hatası: {e}")
                continue

        return markdown_section

    async def _get_mcp_tools(self) -> List:
        """MCP HTTP server'dan tools'ları al"""
        if not MCP_ADAPTERS_AVAILABLE or MultiServerMCPClient is None:
            logging.warning("⚠️ MCP Adapters not available, no tools loaded")
            return []
        
        # Instance'da zaten varsa kullan
        if self.mcp_tools:
            _log(f"MCP tools: {len(self.mcp_tools)} (cached)")
            return self.mcp_tools
        
        try:
            mcp_config = get_mcp_server_config()
            self.mcp_client = MultiServerMCPClient(mcp_config)
            
            # MCP tools'ları al
            tools = await self.mcp_client.get_tools()
            self.mcp_tools = tools
            _log(f"MCP tools: {len(tools)} loaded from HTTP server")
            
            return tools
            
        except Exception as e:
            logging.error(f"❌ MCP tools yüklenemedi: {e}", exc_info=True)
            return []

    async def _create_agent(self, schema_info: str = "", session_id: str = ""):
        """LangChain Agent oluştur - Orchestrator + Middleware yapısı ile"""
        if not LANGCHAIN_AGENT_AVAILABLE:
            raise ImportError("langchain.agents package is not installed or outdated")

        # MCP tools'ları al
        tools = await self._get_mcp_tools()
        
        if not tools:
            logging.warning("⚠️ LangChainAgent: No tools available, agent may have limited functionality")
        
        # =====================================================================
        # SESSION CONTEXT
        # =====================================================================
        short_session = session_id[:8] if session_id else "default"
        
        session_context = f"""## 🔐 SESSION CONTEXT
- **Session ID:** {short_session}

## 📁 DOSYA YAPISI:
Bulgularını kaydetmek için write_finding tool'unu kullan:
- session_id: "{short_session}"
- step_name: "step_1_entity_search", "step_2_content_search" vb.
"""
        
        # =====================================================================
        # ORCHESTRATOR PROMPT - TAM ŞEMA BİLGİSİ
        # =====================================================================
        orchestrator_prompt = ORCHESTRATOR_SYSTEM_PROMPT
        if schema_info:
            orchestrator_prompt = f"""{session_context}

## 📊 VERİTABANI ŞEMASI (TAM - PLANLAMA İÇİN):
{schema_info}

{ORCHESTRATOR_SYSTEM_PROMPT}"""
        else:
            orchestrator_prompt = f"""{session_context}

{ORCHESTRATOR_SYSTEM_PROMPT}"""

        # =====================================================================
        # MODEL OLUŞTUR
        # =====================================================================
        try:
            model_name = self.model
            
            # GPT-5 modelleri için özel handling (reasoning özellikleri ile)
            if "gpt-5" in model_name.lower():
                from langchain_openai import ChatOpenAI
                from pydantic import SecretStr
                
                api_key = os.environ.get("OPENAI_API_KEY")
                reasoning_effort = os.environ.get("OPENAI_REASONING_EFFORT", self.reasoning_effort)
                _log(f"Model: {model_name}, reasoning={reasoning_effort}")
                
                model_kwargs = {
                    "model": model_name,
                    "reasoning": {"effort": reasoning_effort}
                }
                if api_key:
                    model_kwargs["api_key"] = SecretStr(api_key)
                
                model = ChatOpenAI(**model_kwargs)
            else:
                # Standart modeller (gpt-4o, gpt-4o-mini, vb.)
                if not LANGCHAIN_AGENT_AVAILABLE or init_chat_model is None:
                    raise ImportError("LangChain Agent not available. Install langchain>=0.3")
                
                # init_chat_model OpenAI modelleri için "openai:" prefix'i bekler
                if not model_name.startswith("openai:") and "gpt" in model_name.lower():
                    model_name = f"openai:{model_name}"
                
                _log(f"Model: {model_name}")
                model = init_chat_model(model_name)
            
        except Exception as e:
            logging.warning(f"⚠️ Model {self.model} yüklenemedi, fallback gpt-4o: {e}")
            if not LANGCHAIN_AGENT_AVAILABLE or init_chat_model is None:
                raise ImportError("LangChain Agent not available. Install langchain>=0.3")
            model = init_chat_model("openai:gpt-4o")

        # =====================================================================
        # MIDDLEWARE YAPISI - Sadece belirtilen middleware'lar
        # =====================================================================
        middleware_list = []
        
        # 1. TodoListMiddleware - Planlama için write_todos tool'u sağlar
        if TodoListMiddleware is not None:
            todo_middleware = TodoListMiddleware()
            middleware_list.append(todo_middleware)
            _log("Middleware: TodoListMiddleware added")
        
        # 2. ModelCallLimitMiddleware - Sonsuz döngü önleme (opsiyonel)
        max_model_calls = int(os.environ.get("AGENT_MAX_MODEL_CALLS", "50"))
        if ModelCallLimitMiddleware is not None:
            limit_middleware = ModelCallLimitMiddleware(run_limit=max_model_calls)
            middleware_list.append(limit_middleware)
            _log(f"Middleware: ModelCallLimitMiddleware (run_limit={max_model_calls})")
        
        # =====================================================================
        # ORCHESTRATOR TOOLS - Sadece koordinasyon tool'ları (MCP YOK!)
        # =====================================================================
        # Orchestrator hiçbir veritabanı sorgusu çalıştırmaz!
        # Tüm sorgular worker'a delege edilir.
        all_tools = []  # MCP tools Orchestrator'a VERİLMEZ!
        
        if think_tool is not None:
            all_tools.append(think_tool)
        if write_finding is not None:
            all_tools.append(write_finding)
        
        # read_finding ve read_blackboard - self.current_question_id kullanacak dinamik versiyonlar
        # Orchestrator'ın geçtiği yanlış question_id'yi ignore et
        from langchain_core.tools import tool as tool_decorator
        
        @tool_decorator
        def read_finding_dynamic(
            step_name: str, 
            result_type: str = "success",
            include_query: bool = True,
            start_record: int = 0,
            end_record: int = 0
        ) -> str:
            """
            Worker'ın kaydettiği sonuç dosyasını oku - KAYIT KAYIT okuyabilirsin!
            Session ve question ID otomatik alınır.
            
            Args:
                step_name: Adım adı (örn: step_1_customer_search)
                result_type: "success", "failed" veya "error"
                include_query: True ise <query> kısmını dahil et
                start_record: Hangi kayıttan başla (0 = baştan)
                end_record: Hangi kayıtta bitir (0 = sınırsız, tümü)
            
            Örnekler:
                İlk 10 kayıt: start_record=0, end_record=10
                10-20 arası:  start_record=10, end_record=20
                Sadece query: include_query=True, end_record=0 (hiç kayıt yok)
            """
            import re
            
            q_id = self.current_question_id if self.current_question_id else "default"
            findings_dir = os.path.join(os.getcwd(), "agent_findings", "findings", short_session, q_id)
            
            file_path = os.path.join(findings_dir, f"{step_name}_{result_type}.txt")
            if not os.path.exists(file_path):
                for ext in [".xml", ".md"]:
                    alt_path = os.path.join(findings_dir, f"{step_name}_{result_type}{ext}")
                    if os.path.exists(alt_path):
                        file_path = alt_path
                        break
                else:
                    return f"Dosya bulunamadı: {file_path}"
            
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            _log(f"📖 Finding read: {file_path} (records {start_record}-{end_record if end_record > 0 else 'all'})")
            
            output_parts = []
            
            # Query kısmını ayıkla
            if include_query:
                query_match = content.find("<query>")
                query_end = content.find("</query>")
                if query_match != -1 and query_end != -1:
                    output_parts.append(content[query_match:query_end + 8])
            
            # Result kısmını ayıkla ve kayıt bazlı filtrele
            result_match = content.find("<result>")
            result_end = content.find("</result>")
            
            if result_match != -1 and result_end != -1:
                result_content = content[result_match + 8:result_end]  # <result> ve </result> hariç
                
                # Kayıtları parse et - (R:N) pattern'i ile
                # Her kayıt (R:N){ ile başlıyor
                record_pattern = r'\(R:(\d+)\)\{([^}]*(?:\{[^}]*\}[^}]*)*)\}'
                records = list(re.finditer(record_pattern, result_content, re.DOTALL))
                
                total_records = len(records)
                
                if total_records == 0:
                    # Pattern bulunamadı, raw content döndür (ama pagination uygulanamaz)
                    if end_record == 0:
                        output_parts.append(f"<result>\n{result_content.strip()}\n</result>")
                    else:
                        output_parts.append(f"<result>\n(Kayıt formatı tanınmadı - raw içerik)\n{result_content.strip()}\n</result>")
                else:
                    # Pagination uygula
                    effective_end = end_record if end_record > 0 else total_records
                    filtered_records = []
                    
                    for match in records:
                        record_num = int(match.group(1))
                        if start_record <= record_num < effective_end:
                            filtered_records.append(match.group(0))
                    
                    if filtered_records:
                        pagination_info = f"[Kayıtlar {start_record}-{min(effective_end, total_records)-1} / Toplam: {total_records}]"
                        output_parts.append(f"<result>\n{pagination_info}\n" + "\n".join(filtered_records) + "\n</result>")
                    else:
                        output_parts.append(f"<result>\n[Toplam {total_records} kayıt - gösterilen: 0 (aralık dışı)]\n</result>")
            
            return "\n\n".join(output_parts) if output_parts else "Dosya boş veya parse edilemedi."
        
        @tool_decorator
        def read_blackboard_dynamic() -> str:
            """
            Ortak tahta dosyasını oku - tüm bulgular burada!
            Session ve question ID otomatik alınır.
            """
            q_id = self.current_question_id if self.current_question_id else "default"
            blackboard_path = os.path.join(
                os.getcwd(), "agent_findings", "findings", short_session, q_id, "_blackboard.txt"
            )
            
            if not os.path.exists(blackboard_path):
                return "Blackboard henüz oluşturulmadı - Worker henüz sorgu çalıştırmadı."
            
            with open(blackboard_path, "r", encoding="utf-8") as f:
                content = f.read()
            _log(f"📋 Blackboard read: {blackboard_path}")
            return content
        
        all_tools.append(read_finding_dynamic)
        all_tools.append(read_blackboard_dynamic)
        
        # add_source - Dinamik versiyon (question_id otomatik)
        @tool_decorator
        def add_source_dynamic(source_type: str, value: str) -> str:
            """
            Cevaba kaynak ekle. Doğru bilgiyi bulduğunda bu tool ile kaynağı kaydet.
            
            Args:
                source_type: "document" (PDF dosya adı) veya "page" (sayfa görseli)
                value: Dosya adı veya sayfa linki
            
            Örnek:
                add_source("document", "Rapor_2024.pdf")
                add_source("page", "Rapor_2024_page_001.png")
            """
            q_id = self.current_question_id or short_session
            sources = _get_session_sources(q_id)
            
            if source_type == "document":
                sources["documents"].add(value)
                _log(f"📎 Document source added: {value}")
                return f"✅ Belge kaynağı eklendi: {value}"
            elif source_type == "page":
                sources["pages"].add(value)
                _log(f"🖼️ Page source added: {value}")
                return f"✅ Sayfa kaynağı eklendi: {value}"
            else:
                return f"❌ Geçersiz source_type. 'document' veya 'page' olmalı."
        
        all_tools.append(add_source_dynamic)
        
        _log(f"Orchestrator Tools: {len(all_tools)} coordination tools (NO MCP!)")

        # =====================================================================
        # WORKER AGENT - Sorgu çalıştırıcı (adapter tool'ları ile)
        # =====================================================================
        # Worker her soru için yeniden oluşturulur (question_id ile)
        # Adapter tool'ları session_id ve question_id ile dosya yapar
        
        # question_id stream_query_response'da set ediliyor (her soru için unique)
        # Eğer henüz set edilmediyse short_session kullan (fallback)
        if not hasattr(self, 'current_question_id') or not self.current_question_id:
            self.current_question_id = short_session
        self.mcp_tools_for_worker = tools
        self.short_session = short_session

        # =====================================================================
        # SPAWN WORKER TOOL - Orchestrator'ın worker çağırması için
        # =====================================================================
        if tool is None:
            raise ImportError("LangChain tool decorator not available")
        
        @tool
        async def spawn_worker(queries: str) -> str:
            """
            Worker agent'ı çağır - Verilen sorguları çalıştır, sonuçları dosyalara yaz.
            
            Worker:
            - Sorguları SIRASYLA çalıştırır
            - Sonuçları dosyalara YAZAR
            - İstatistik DÖNDÜRÜR (ham data değil!)
            
            Session ve Question ID otomatik alınır.
            
            Args:
                queries: Çalıştırılacak sorgular (yapılandırılmış format)
            
            Returns:
                Her sorgu için istatistik özeti
            """
            # Frontend'den gelen question_id'yi kullan (self.current_question_id)
            q_id = self.current_question_id if self.current_question_id else "default"
            
            try:
                # Worker için adapter tool'ları oluştur
                adapter_tools = create_adapter_tools(
                    self.mcp_tools_for_worker, 
                    self.short_session, 
                    q_id
                )
                
                if not adapter_tools:
                    return "❌ Adapter tools oluşturulamadı"
                
                # Worker agent oluştur
                worker = await self._create_worker(adapter_tools)
                
                if worker is None:
                    return "❌ Worker agent oluşturulamadı"
                
                _log(f"[WORKER] Starting with question_id={q_id}")
                
                # Worker'ı streaming ile çalıştır
                worker_tool_count = 0
                final_response = ""
                
                async for event in worker.astream_events(
                    {"messages": [{"role": "user", "content": queries}]},
                    version="v2"
                ):
                    kind = event.get("event", "")
                    
                    # Tool çağrısı başladığında logla
                    if kind == "on_tool_start":
                        tool_name = event.get("name", "?")
                        tool_input = event.get("data", {}).get("input", {})
                        
                        # Sadece adapter tool'larını logla, MCP tool'larını değil
                        if tool_name == "execute_cypher_query":
                            worker_tool_count += 1
                            cypher = tool_input.get("cypher", "")
                            step = tool_input.get("step_name", "")
                            _log(f"[WORKER] Tool #{worker_tool_count}: {tool_name}")
                            _log(f"   📝 STEP: {step}")
                            _log(f"   📝 CYPHER:\n{cypher}")
                        elif tool_name == "execute_embedding_query":
                            worker_tool_count += 1
                            query_text = tool_input.get('query_text', '')
                            cypher = tool_input.get('cypher_query', '')
                            step = tool_input.get("step_name", "")
                            _log(f"[WORKER] Tool #{worker_tool_count}: {tool_name}")
                            _log(f"   📝 STEP: {step}")
                            _log(f"   🔎 SEARCH TEXT: {query_text}")
                            _log(f"   📝 CYPHER:\n{cypher}")
                        # MCP tool'ları (read_neo4j_cypher vb.) loglanmaz - adapter içinden çağrılıyor
                    
                    # Tool sonucu geldiğinde logla - sadece adapter tool'ları
                    elif kind == "on_tool_end":
                        tool_name = event.get("name", "?")
                        # Sadece adapter tool sonuçlarını logla
                        if tool_name in ["execute_cypher_query", "execute_embedding_query"]:
                            output = event.get("data", {}).get("output", "")
                            if isinstance(output, str):
                                _log(f"[WORKER_RESULT] {tool_name}:\n{output}")
                            else:
                                _log(f"[WORKER_RESULT] {tool_name}: {output}")
                    
                    # Agent son yanıtı
                    elif kind == "on_chain_end":
                        event_name = event.get("name", "")
                        output = event.get("data", {}).get("output", {})
                        
                        if isinstance(output, dict) and "messages" in output:
                            messages = output["messages"]
                            if messages:
                                last_msg = messages[-1]
                                if hasattr(last_msg, "content") and last_msg.content:
                                    candidate = self._extract_text_from_reasoning_content(last_msg.content)
                                    if candidate and len(candidate) > len(final_response):
                                        final_response = candidate
                
                _log(f"← WORKER summary: {worker_tool_count} tool calls")
                
                if final_response:
                    _log(f"← worker completed ({len(final_response)} chars)")
                    return final_response
                
                return f"Worker {worker_tool_count} sorgu çalıştırdı"
                
            except Exception as e:
                logging.error(f"Worker error: {e}", exc_info=True)
                return f"❌ Worker hatası: {str(e)}"
        
        all_tools.append(spawn_worker)
        _log("Tool: spawn_worker added")

        # =====================================================================
        # ANA AGENT OLUŞTUR - create_agent ile
        # =====================================================================
        if not LANGCHAIN_AGENT_AVAILABLE or create_agent is None:
            raise ImportError("LangChain Agent not available. Install langchain>=0.3")
        
        agent = create_agent(
            model=model,
            tools=all_tools,
            system_prompt=orchestrator_prompt,
            middleware=middleware_list,
            debug=os.environ.get("AGENT_DEBUG", "0") == "1",
            name="orchestrator",
        )
        
        _log(f"Agent ready: {len(middleware_list)} middleware, {len(all_tools)} tools")

        return agent

    async def _create_worker(self, adapter_tools: List):
        """Worker Agent oluştur - Adapter tools ile (sadece sorgu çalıştırır)
        
        Environment Variables:
            WORKER_MODEL: Worker modeli (default: gpt-5-mini)
            WORKER_REASONING_EFFORT: GPT-5 için reasoning effort (default: none)
        """
        if not LANGCHAIN_AGENT_AVAILABLE or create_agent is None:
            return None
        
        # Worker için model - parametrik
        if init_chat_model is None:
            return None
            
        # Environment variables'dan model ve reasoning_effort al
        # GPT-5-mini için desteklenen değerler: minimal, low, medium, high (none desteklenmiyor!)
        worker_model_name = os.environ.get("WORKER_MODEL", "gpt-5-mini")
        worker_reasoning_effort = os.environ.get("WORKER_REASONING_EFFORT", "minimal")
        
        if not worker_model_name.startswith("openai:") and "gpt" in worker_model_name.lower():
            worker_model_name = f"openai:{worker_model_name}"
        
        # Paralel tool çağrıları AÇIK - worker birden fazla node'da aynı anda arama yapabilir
        # GPT-5 modelleri için reasoning_effort parametresi geç
        worker_model = create_worker_model(worker_model_name, reasoning_effort=worker_reasoning_effort)
        
        # Worker system prompt
        today = datetime.now().strftime("%Y-%m-%d")
        worker_prompt = WORKER_AGENT_PROMPT.format(date=today)
        
        # Worker tools - sadece adapter tools
        worker_tools = list(adapter_tools)
        
        # Worker middleware - minimal
        worker_middleware = []
        
        # ModelCallLimitMiddleware - worker için düşük limit (sadece verilen sorguları çalıştır)
        if ModelCallLimitMiddleware is not None:
            worker_middleware.append(ModelCallLimitMiddleware(run_limit=15))
        
        worker = create_agent(
            model=worker_model,  # type: ignore[arg-type]
            tools=worker_tools,
            system_prompt=worker_prompt,
            middleware=worker_middleware,
            name="worker",
        )
        
        _log(f"Worker ready (model={worker_model_name}, reasoning={worker_reasoning_effort})")
        return worker

    async def stream_query_response(
        self, question: str, session_id: str = "", question_id: str = "", **kwargs
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """LangChain Agent kullanarak streaming cevap üret - Middleware + MCP tools ile"""
        import time

        # Question ID - her soru için unique (dosya yapısı için)
        # session_id gibi ilk 8 karakter kullanılır
        if not question_id:
            import uuid
            question_id = str(uuid.uuid4())[:8]
        else:
            question_id = question_id[:8]  # session_id gibi kısa format
        self.current_question_id = question_id
        
        # Session sources'ı temizle (yeni soru için)
        _clear_session_sources(question_id)
        
        # ⏱️ Timing metrikleri
        timings = {}
        total_start = time.time()
        
        try:
            # Başlangıç durumu
            yield {
                "type": "status",
                "message": "🧠 LangChain Agent ile sorgunuz işleniyor...",
                "status": "processing",
                "session_id": session_id,
                "timestamp": datetime.now().isoformat(),
            }

            # Şema bilgisini al
            step_start = time.time()
            schema_info = self._get_schema_for_session(session_id)
            timings["schema_fetch"] = time.time() - step_start

            if not schema_info or schema_info.strip() == "":
                yield {
                    "type": "error",
                    "message": "Üzgünüm, bu oturumda veritabanı şema bilgisi mevcut değil. Lütfen yeni bir chat oluşturun ve tekrar deneyin.",
                    "status": "error",
                    "session_id": session_id,
                    "timestamp": datetime.now().isoformat(),
                }
                return

            yield {
                "type": "status",
                "message": "✅ Veritabanı şema bilgisi hazır, MCP tools yükleniyor...",
                "status": "processing",
                "session_id": session_id,
                "timestamp": datetime.now().isoformat(),
            }

            # Conversation history'yi al
            step_start = time.time()
            history_messages = self._get_conversation_history(session_id)
            timings["history_fetch"] = time.time() - step_start

            # Kullanıcı sorusunu history'ye kaydet
            self._save_to_history(session_id, "Human", question)

            # Agent'ı cache'den al veya oluştur (MCP subprocess tekrar başlatmamak için)
            step_start = time.time()
            if self.agent is None:
                self.agent = await self._create_agent(schema_info, session_id)
                timings["agent_create"] = time.time() - step_start
                _log(f"Agent created ({timings['agent_create']:.2f}s)")
            else:
                timings["agent_create"] = time.time() - step_start
                _log(f"Agent reused", "debug")
            
            agent = self.agent

            # Messages oluştur (history + yeni soru)
            messages = []
            for msg in history_messages:
                messages.append(msg)
            messages.append({"role": "user", "content": question})

            # LLM Messages
            _log(f"Messages: {len(messages)}")
            _log(f"Last message: {messages[-1].get('content', '')}")

            # Agent'ı çalıştır
            yield {
                "type": "status",
                "message": "🔍 LangChain Agent araştırma yapıyor...",
                "status": "agent_working",
                "session_id": session_id,
                "timestamp": datetime.now().isoformat(),
            }

            # Streaming ile çalıştır - Token metrikleri ile
            response_text = ""
            total_tokens = 0
            prompt_tokens = 0
            completion_tokens = 0
            reasoning_tokens_total = 0
            llm_calls = 0
            tool_calls = 0
            tool_call_count = 0  # Toplam tool call sayısı
            
            # Detaylı timing metrikleri
            llm_start = time.time()
            step_timings = []  # Her step için timing
            llm_thinking_time = 0.0  # Toplam LLM düşünme süresi
            tool_execution_time = 0.0  # Toplam tool çalışma süresi
            last_step_time = time.time()  # Son step zamanı
            
            # 🧠 Agent düşünme süreci için sayaç
            thinking_step = 0
            logged_message_ids = set()  # Daha önce loglanan mesajları takip et
            step_timings = []
            
            # LangChain create_agent stream_mode="updates" kullanır
            async for chunk in agent.astream(
                {"messages": messages},
                stream_mode="updates",
            ):
                # chunk format: {"node_name": {"messages": [...], ...}}
                if not isinstance(chunk, dict):
                    continue
                
                # Her node'un çıktısını işle
                for node_name, node_output in chunk.items():
                    if not isinstance(node_output, dict):
                        continue
                    
                    # messages varsa işle
                    if "messages" not in node_output or not node_output["messages"]:
                        continue
                    
                    for message in node_output["messages"]:
                        # Mesajın benzersiz ID'sini al
                        msg_id = getattr(message, "id", None) or hash(str(message.content)[:100] if hasattr(message, "content") else "")
                        
                        if msg_id in logged_message_ids:
                            continue
                        logged_message_ids.add(msg_id)
                        
                        # Step süresini hesapla
                        current_time = time.time()
                        step_duration = current_time - last_step_time
                        last_step_time = current_time
                        
                        thinking_step += 1
                        msg_type = type(message).__name__
                        
                        # Loglama
                        if msg_type != "ToolMessage":
                            _log(f"[{node_name}] Step {thinking_step}: {msg_type} ({step_duration:.2f}s)")
                        
                        # Step timing kaydet
                        step_info = {
                            "step": thinking_step,
                            "type": msg_type,
                            "node": node_name,
                            "duration": step_duration,
                        }
                        
                        # Mesaj tipine göre süreyi kategorize et
                        if msg_type == "AIMessage":
                            llm_thinking_time += step_duration
                            step_info["category"] = "llm"
                        elif msg_type == "ToolMessage":
                            tool_execution_time += step_duration
                            step_info["category"] = "tool"
                            # Tool sonucunu logla - TAM içerik
                            tool_content = getattr(message, "content", "")
                            tool_msg_name = getattr(message, "name", "unknown")
                            if tool_content:
                                _log(f"[TOOL_RESULT] {tool_msg_name}:\n{tool_content}")
                                # NOT: Kaynaklar artık add_source tool ile ekleniyor
                                # Regex extraction kaldırıldı - LLM explicit olarak kaynak ekler
                        else:
                            step_info["category"] = "other"
                        
                        # Tool calls loglama
                        if hasattr(message, "tool_calls") and message.tool_calls:
                            tool_calls += len(message.tool_calls)
                            for tc in message.tool_calls:
                                tool_call_count += 1
                                tool_name = tc.get("name", "unknown") if isinstance(tc, dict) else getattr(tc, "name", "unknown")
                                tool_args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
                                
                                _log(f"[ORCHESTRATOR] Tool #{tool_call_count}: {tool_name}")
                                
                                # Tüm tool'ları TAM detaylı logla
                                if tool_name == "read_neo4j_cypher":
                                    query = tool_args.get("query", "")
                                    _log(f"   📝 CYPHER QUERY:\n{query}")
                                elif tool_name == "read_neo4j_cypher_with_embedding":
                                    query_text = tool_args.get("query_text", "")
                                    cypher = tool_args.get("cypher_query", "")
                                    _log(f"   🔎 EMBEDDING SEARCH: {query_text}")
                                    _log(f"   📝 CYPHER:\n{cypher}")
                                elif tool_name == "spawn_worker":
                                    queries = tool_args.get("queries", "")
                                    q_id = tool_args.get("question_id", "")
                                    _log(f"   📋 WORKER TASK (q_id={q_id}):\n{queries}")
                                elif tool_name == "write_todos":
                                    todos = tool_args.get("todos", [])
                                    _log(f"   📋 TODOs: {len(todos)} items")
                                    for todo in todos:
                                        _log(f"      - [{todo.get('status', '?')}] {todo.get('content', '')}")
                                else:
                                    _log(f"   Args: {tool_args}")
                                
                                step_info["tool_name"] = tool_name
                        
                        # Token usage
                        usage = self._extract_token_usage(message)
                        if usage["total_tokens"] > 0:
                            total_tokens += usage["total_tokens"]
                            prompt_tokens += usage["input_tokens"]
                            completion_tokens += usage["output_tokens"]
                            reasoning_tokens_total += usage["reasoning_tokens"]
                            llm_calls += 1
                            step_info["tokens"] = usage
                            _log(f"💰 tokens: +{usage['total_tokens']} (total: {total_tokens:,})")
                        
                        step_timings.append(step_info)
                        
                        # Content streaming - sadece AIMessage için
                        if hasattr(message, "content") and message.content and msg_type == "AIMessage":
                            new_content = self._extract_text_from_reasoning_content(message.content)
                            if new_content and new_content != response_text:
                                # Yeni içerik varsa stream et
                                delta = new_content[len(response_text):] if len(new_content) > len(response_text) else new_content
                                response_text = new_content
                                
                                if delta.strip():
                                    yield {
                                        "type": "message_chunk",
                                        "content": delta,
                                        "full_message": response_text,
                                        "session_id": session_id,
                                        "timestamp": datetime.now().isoformat(),
                                    }
                    
                    # TODO listesi varsa logla
                    if "todos" in node_output and node_output["todos"]:
                        todos = node_output["todos"]
                        _log(f"📋 TODOs updated: {len(todos)} items")

            # LLM streaming tamamlandı
            timings["llm_streaming"] = time.time() - llm_start
            timings["llm_thinking"] = llm_thinking_time
            timings["tool_execution"] = tool_execution_time
            
            # Session sources'tan kaynakları al (add_source tool ile eklenenler)
            sources = _get_session_sources(question_id)
            session_file_names = sources["documents"]
            session_page_links = sources["pages"]
            
            _log(f"📎 Sources from add_source tool: {len(session_file_names)} docs, {len(session_page_links)} pages")

            # Final response
            final_response = response_text
            
            # Dosya linklerini ekle (PDF belgeler)
            if session_file_names:
                file_links_markdown = self._generate_file_links_markdown(session_file_names)
                final_response += file_links_markdown
                
                # Markdown'ı da stream et
                yield {
                    "type": "message_chunk",
                    "content": file_links_markdown,
                    "full_message": final_response,
                    "session_id": session_id,
                    "timestamp": datetime.now().isoformat(),
                }
            
            # Sayfa görsellerini ekle
            if session_page_links:
                page_links_markdown = self._generate_page_links_markdown(session_page_links)
                final_response += page_links_markdown
                
                # Markdown'ı da stream et
                yield {
                    "type": "message_chunk",
                    "content": page_links_markdown,
                    "full_message": final_response,
                    "session_id": session_id,
                    "timestamp": datetime.now().isoformat(),
                }

            # AI cevabını history'ye kaydet
            self._save_to_history(session_id, "AI", final_response)

            # Final response - minimal log
            _log(f"DONE | len={len(final_response)} chars | files={len(session_file_names)} | pages={len(session_page_links)}")

            # Toplam süre
            total_time = time.time() - total_start
            timings["total"] = total_time
            
            # Metrics - tek satır özet
            reasoning_display = reasoning_tokens_total if reasoning_tokens_total > 0 else 0
            _log(f"METRICS | time={total_time:.1f}s | tokens={total_tokens} (in={prompt_tokens},out={completion_tokens},reason={reasoning_display}) | llm={llm_calls} tools={tool_call_count} steps={thinking_step}")

            # Tamamlanma durumu
            include_debug_steps = os.environ.get("DEEPAGENT_INCLUDE_DEBUG_STEPS", "0") == "1"

            info_payload = {
                "agent_type": "langchain_create_agent",
                "model": self.model,
                "reasoning_effort": self.reasoning_effort,
                "file_names_count": len(session_file_names),
                "page_links_count": len(session_page_links),
                "mcp_tools_used": True,
                "middleware": ["TodoListMiddleware", "ModelCallLimitMiddleware"],
                "token_usage": {
                    "total_tokens": total_tokens,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "reasoning_tokens": reasoning_tokens_total,
                    "llm_calls": llm_calls,
                    "tool_calls": tool_call_count,
                },
                "timings": {
                    "schema_fetch_sec": round(timings.get("schema_fetch", 0), 2),
                    "history_fetch_sec": round(timings.get("history_fetch", 0), 2),
                    "agent_create_sec": round(timings.get("agent_create", 0), 2),
                    "llm_streaming_sec": round(timings.get("llm_streaming", 0), 2),
                    "llm_thinking_sec": round(timings.get("llm_thinking", 0), 2),
                    "tool_execution_sec": round(timings.get("tool_execution", 0), 2),
                    "total_sec": round(total_time, 2),
                },
            }

            # Tool/LLM step timeline frontend'de gürültü: sadece debug modunda gönder.
            if include_debug_steps:
                info_payload["steps"] = {
                    "total_steps": thinking_step,
                    "step_details": step_timings,
                }

            yield {
                "type": "complete",
                "message": final_response,
                "status": "finished",
                "session_id": session_id,
                "info": info_payload,
                "timestamp": datetime.now().isoformat(),
            }
            
        except Exception as e:
            error_message = f"LangChain Agent error: {str(e)}"
            logging.error(error_message, exc_info=True)

            # Partial result recovery: Eğer findings dosyası varsa kullanıcıya göster
            partial_result = None
            try:
                # agent_findings_dir backend çalışma dizininde
                agent_findings_dir = os.path.join(os.getcwd(), "agent_findings")
                findings_path = os.path.join(agent_findings_dir, "findings", "explorer_results.md")
                if os.path.exists(findings_path):
                    with open(findings_path, "r", encoding="utf-8") as f:
                        partial_findings = f.read().strip()
                    if partial_findings:
                        partial_result = (
                            "⚠️ Araştırma sırasında teknik bir sorun oluştu, "
                            "ancak o ana kadar bulunan bilgiler aşağıdadır:\n\n"
                            f"---\n\n{partial_findings}\n\n---\n\n"
                            "Daha detaylı bilgi için sorunuzu tekrar sorabilirsiniz."
                        )
                        _log(f"Partial recovered: {findings_path}")
            except Exception as recovery_error:
                logging.warning(f"⚠️ Partial result recovery failed: {recovery_error}")

            # Kullanıcı dostu mesaj oluştur
            if partial_result:
                user_message = partial_result
            else:
                user_message = (
                    "Araştırma sırasında beklenmeyen bir hata oluştu. "
                    "Lütfen sorunuzu tekrar sormayı deneyin veya farklı bir şekilde ifade edin."
                )

            yield {
                "type": "error",
                "message": user_message,
                "status": "failed",
                "session_id": session_id,
                "timestamp": datetime.now().isoformat(),
                "_debug_error": error_message,  # Debug için orjinal hata (frontend göstermesin)
            }


# ============================================================================
# SESSION BASED AGENT CACHE
# ============================================================================

# Session bazlı LangChainAgent cache - her session için ayrı agent
_session_agents: Dict[str, LangChainAgentIntegration] = {}
_session_access_times: Dict[str, datetime] = {}
# Async lock for session cache - lazy initialization (event loop gerektirir)
_session_agent_lock: Optional[asyncio.Lock] = None

# Config (env-overridable)
SESSION_AGENT_MAX_AGE_HOURS = int(os.environ.get("SESSION_AGENT_MAX_AGE_HOURS", "24"))  # Session agent'ı bu süreden sonra temizle
SESSION_AGENT_MAX_COUNT = int(os.environ.get("SESSION_AGENT_MAX_COUNT", "100"))    # Maksimum cache'deki session sayısı


def cleanup_old_session_agents():
    """Eski session agent'larını temizle (memory leak önlemi)"""
    global _session_agents, _session_access_times
    
    now = datetime.now()
    expired_sessions = []
    
    for session_id, access_time in _session_access_times.items():
        age_hours = (now - access_time).total_seconds() / 3600
        if age_hours > SESSION_AGENT_MAX_AGE_HOURS:
            expired_sessions.append(session_id)
    
    for session_id in expired_sessions:
        if session_id in _session_agents:
            del _session_agents[session_id]
        if session_id in _session_access_times:
            del _session_access_times[session_id]
    
    if expired_sessions:
        _log(f"Cleanup: {len(expired_sessions)} sessions")
    
    # Maksimum sayı kontrolü - en eski olanları sil
    if len(_session_agents) > SESSION_AGENT_MAX_COUNT:
        sorted_sessions = sorted(_session_access_times.items(), key=lambda x: x[1])
        sessions_to_remove = len(_session_agents) - SESSION_AGENT_MAX_COUNT
        
        for session_id, _ in sorted_sessions[:sessions_to_remove]:
            if session_id in _session_agents:
                del _session_agents[session_id]
            if session_id in _session_access_times:
                del _session_access_times[session_id]
        
        _log(f"Cache limit: {sessions_to_remove} removed")


def clear_session_agent(session_id: str):
    """Belirli bir session'ın agent'ını temizle (chat clear edildiğinde çağrılır)"""
    global _session_agents, _session_access_times
    
    if session_id in _session_agents:
        del _session_agents[session_id]
        if session_id in _session_access_times:
            del _session_access_times[session_id]
        _log(f"Session cleared: {session_id[:8]}")
        return True
    return False


async def get_or_create_session_agent(
    session_id: str, model: str = "gpt-5", graph=None, reasoning_effort: str = "minimal"
) -> LangChainAgentIntegration:
    """Session bazlı LangChainAgent al veya oluştur"""
    global _session_agents, _session_access_times, _session_agent_lock
    
    # Lazy lock initialization - event loop içinde olmalı
    if _session_agent_lock is None:
        _session_agent_lock = asyncio.Lock()
    
    async with _session_agent_lock:
        # Önce eski session'ları temizle
        cleanup_old_session_agents()
        
        # Session için agent var mı?
        if session_id in _session_agents:
            agent = _session_agents[session_id]
            _session_access_times[session_id] = datetime.now()
            
            # Model veya reasoning_effort değiştiyse güncelle
            if agent.model != model or agent.reasoning_effort != reasoning_effort:
                _log(f"Session {session_id[:8]}: model update {agent.model}→{model}")
                agent.model = model
                agent.reasoning_effort = reasoning_effort
                agent.agent = None  # Agent'ı yeniden oluşturulacak şekilde işaretle
                agent.worker = None  # Worker'ı da yeniden oluştur
            
            if graph and agent.graph != graph:
                _log(f"Session {session_id[:8]}: graph update", "debug")
                agent.graph = graph
            
            _log(f"Session {session_id[:8]}: reused", "debug")
            return agent
        
        # Yeni agent oluştur
        agent = LangChainAgentIntegration(model=model, graph=graph, reasoning_effort=reasoning_effort)
        _session_agents[session_id] = agent
        _session_access_times[session_id] = datetime.now()
        
        _log(f"Session {session_id[:8]}: new agent, model={model} (cache={len(_session_agents)})")
        return agent


def get_session_agent_stats() -> Dict[str, Any]:
    """Session agent cache istatistiklerini döndür"""
    return {
        "total_sessions": len(_session_agents),
        "max_sessions": SESSION_AGENT_MAX_COUNT,
        "max_age_hours": SESSION_AGENT_MAX_AGE_HOURS,
        "sessions": list(_session_agents.keys())[:10],  # İlk 10 session
    }


async def stream_agent_response(
    question: str,
    model: str = "gpt-5",
    session_id: str = "",
    question_id: str = "",
    graph=None,
    reasoning_effort: str = "minimal",
    **kwargs,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    LangChain Agent kullanarak streaming cevap üret - Middleware + MCP tools ile
    
    Session bazlı agent cache kullanır - her session için ayrı agent instance

    Args:
        question: Kullanıcının sorusu
        model: Kullanılacak LLM modeli (default: gpt-5-mini)
        session_id: Oturum ID'si (conversation history için) - ZORUNLU
        question_id: Soru ID'si (her soru için unique - dosya yapısı için)
        graph: Neo4j graph connection
        reasoning_effort: GPT-5 modelleri için reasoning seviyesi (minimal, low, medium, high) - default: minimal
        **kwargs: Ek parametreler

    Yields:
        Dict: Streaming chunk'ları
    """

    if not LANGCHAIN_AGENT_AVAILABLE:
        yield {
            "type": "error",
            "message": "LangChain Agent kurulu değil. 'pip install langchain>=0.3' ile kurun.",
            "status": "not_available",
            "session_id": session_id,
            "timestamp": datetime.now().isoformat(),
        }
        return
    
    if not session_id:
        yield {
            "type": "error",
            "message": "Session ID gerekli. Lütfen yeni bir chat oluşturun.",
            "status": "missing_session",
            "timestamp": datetime.now().isoformat(),
        }
        return

    try:
        # Session bazlı agent al veya oluştur
        agent = await get_or_create_session_agent(session_id, model, graph, reasoning_effort)
        
        async for chunk in agent.stream_query_response(
            question=question, session_id=session_id, question_id=question_id, **kwargs
        ):
            yield chunk

    except Exception as e:
        logging.error(f"LangChain Agent streaming failed: {e}", exc_info=True)
        yield {
            "type": "error",
            "message": f"LangChain Agent hatası: {str(e)}",
            "status": "failed",
            "session_id": session_id,
            "timestamp": datetime.now().isoformat(),
        }
