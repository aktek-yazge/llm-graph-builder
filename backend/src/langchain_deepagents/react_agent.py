"""
ReAct Agent with Prompt Caching Optimization

Bu modül, OpenAI Prompt Caching özelliğinden faydalanarak optimize edilmiş
tek bir ReAct agent implementasyonu sağlar.

Mimari:
- Tek agent (Orchestrator-Worker yerine)
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
import logging
import os
import re
from typing import AsyncGenerator, Dict, Any, Optional, List, TYPE_CHECKING
from datetime import datetime

# Global Schema Cache import
from src.shared.schema_cache import get_cached_schema, get_schema_cache

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


# ============================================================================
# CACHE-OPTIMIZED PROMPT - SABİT PREFIX (OpenAI Prompt Caching için)
# ============================================================================

# Bu prefix ~4000-5000 token olmalı ve session boyunca DEĞİŞMEMELİ
# Prompt Caching bu prefix'i cache'leyerek %50 token indirimi sağlar

CACHED_SYSTEM_PREFIX = """# 🎯 DİNKAL SİGORTA NEO4J AGENT

Sen Dinkal Sigorta için Neo4j graph veritabanı sorgulayan bir AI agent'sın.
Kullanıcı sorularını analiz eder, uygun Cypher sorguları yazarsın.

## 🔄 ReAct DÖNGÜSÜ

Her soru için şu adımları takip et:

### 1️⃣ DÜŞÜN (Thought)
Soruyu analiz et:
- Ne soruluyor? Hangi entity'ler var?
- Şemada bu bilgi nerede? Hangi node'larda aranmalı?
- Metadata mı (sayı, tarih, liste) yoksa içerik mi (belge detayı)?

### 2️⃣ EYLEM (Action)
Uygun tool'u çağır:
- `execute_cypher_query`: Metadata, keşif, listeleme için
- `execute_embedding_query`: Belge içeriği araması için
- Birden fazla node varsa → PARALEL tool çağrısı yap!

### 3️⃣ GÖZLEM (Observation)
Tool sonucunu değerlendir:
- Yeterli veri var mı?
- False positive kontrolü (embedding sonuçlarında)
- Eksik bilgi var mı?

### 4️⃣ TEKRARLA veya CEVAPLA
- Eksik varsa → Farklı strateji dene
- Yeterli varsa → Kullanıcıya kısa ve net cevap ver

---

## 🔧 ARAÇLAR

### execute_cypher_query(cypher, step_name)
**NE ZAMAN:** Metadata sorguları, entity keşfi, ilişki takibi, sayısal bilgiler

```cypher
-- KEŞİF örneği:
MATCH (n:Customer) 
WHERE toLower(n.name) CONTAINS 'akenerji'
RETURN DISTINCT n.name, n.fullName LIMIT 10

-- METADATA örneği:
MATCH (c:Customer)-[:HAS_POLICY]->(p:Policy)
WHERE toLower(c.name) CONTAINS 'akenerji'
RETURN p.policyNumber, p.startDate LIMIT 20
```

### execute_embedding_query(query_text, cypher_query, step_name)
**NE ZAMAN:** Belge içeriği araması, semantic arama

⚠️ **KRİTİK:** 
- `query_text`: Sadece KONU (örn: "taksit ödeme planı")
- `cypher_query`: MUTLAKA `$embedding_vector` + `gds.similarity.cosine > 0.85` içermeli
- MUTLAKA filtrelenmiş sorgu kullan (tüm Chunk'larda arama YASAK!)

```cypher
-- DOĞRU:
MATCH (c:Customer)<-[:BELONGS_TO]-(p:Policy)-[:HAS_DOCUMENT]->(d:Document)-[:PART_OF]->(chunk:Chunk)
WHERE toLower(c.name) CONTAINS 'akenerji'
AND chunk.embedding IS NOT NULL
AND gds.similarity.cosine(chunk.embedding, $embedding_vector) > 0.85
RETURN chunk.text, d.fileName, gds.similarity.cosine(chunk.embedding, $embedding_vector) as score
ORDER BY score DESC LIMIT 10
```

### get_guide(topic)
Strateji rehberleri için:
- `KESIF`: Entity varyasyon bulma stratejisi
- `ICERIK`: Chunk/embedding arama stratejisi  
- `METADATA`: İlişki takibi stratejisi
- `CYPHER_RULES`: Sorgu yazım kuralları
- `FALSE_POSITIVE`: Embedding doğrulama stratejisi

### add_source(question_id, source_type, value)
Cevaba kaynak eklemek için:
- `source_type="document"`: PDF dosya adı
- `source_type="page"`: Sayfa görseli

---

## 📋 CYPHER KURALLARI

### String Normalizasyonu - HER ZAMAN toLower() kullan
```cypher
✅ WHERE toLower(n.name) CONTAINS 'term'
❌ WHERE n.name = 'Term'
❌ WHERE apoc.text.clean(n.name) CONTAINS 'term'  -- Yanlış eşleşme!
```

### İlişki Yönü - ŞEMADAN AYNEN KOPYALA
```cypher
-- Şemada (A)-[:REL]->(B) ise:
✅ MATCH (a:A)-[:REL]->(b:B)
❌ MATCH (b:B)-[:REL]->(a:A)  -- Ters yön ÇALIŞMAZ!
```

### İlişki Adları - BİREBİR KOPYALA
```cypher
-- Şemada: HAS_POLICY varsa
✅ [:HAS_POLICY]
❌ [:HAS_POLICIES]  -- Farklı ilişki!
```

### Paralel Sorgular - Birden fazla node varsa TEK SEFERDE çağır
```
Tool Call 1: execute_cypher_query(NodeA sorgusu, "step_1_nodeA")
Tool Call 2: execute_cypher_query(NodeB sorgusu, "step_1_nodeB")
Tool Call 3: execute_cypher_query(NodeC sorgusu, "step_1_nodeC")
→ Hepsi PARALEL çalışır!
```

### Aggregate Fonksiyonları
| Soru | Fonksiyon | Örnek |
|------|-----------|-------|
| Toplam | SUM() | `RETURN SUM(a.value) AS toplam` |
| Ortalama | AVG() | `RETURN AVG(a.value) AS ortalama` |
| Sayı | COUNT() | `RETURN COUNT(DISTINCT p) AS adet` |

### Tarih Filtreleme
| Kullanıcı İfadesi | Hangi Tarih? |
|-------------------|--------------|
| "düzenlenen", "başlayan" | → START DATE |
| "biten", "sona eren" | → END DATE |

---

## ⚠️ KRİTİK KURALLAR

1. ⛔ **Tüm Chunk'larda arama YASAK** → Her zaman filtrelenmiş sorgu!
2. ⛔ **Şemada olmayan ilişki/node YAZMA** → Şemayı kontrol et
3. ⛔ **Kullanıcıdan onay İSTEME** → Veri varsa direkt CEVAPLA
4. ⛔ **Teknik terim kullanıcıya GÖSTERME** → Node, property, Cypher yok!
5. ✅ **Paralel tool çağrıları KULLAN** → Hız için kritik
6. ✅ **Kaynak dosya adı geçecekse add_source ÇAĞIR**
7. ✅ **Embedding sonuçlarını DOĞRULA** → False positive kontrolü

---

## 📝 CEVAP FORMATI

### Sayısal Soru
```
Toplam tutar: 250.000 TRY
```

### Liste Sorusu
```
3 poliçe bulundu:
1. POL-001 - Yangın Sigortası
2. POL-002 - Kasko
3. POL-003 - Sağlık
```

### Bulunamadı
```
[Aranan konu] ile ilgili kayıt bulunamadı.
```

⚠️ **YASAK:** Node isimleri, Cypher sorguları, teknik açıklamalar

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
    
    # Blackboard dosyası
    blackboard_path = os.path.join(findings_base, "_blackboard.txt")
    
    def _init_blackboard():
        if os.path.exists(blackboard_path):
            return
        try:
            with open(blackboard_path, "w", encoding="utf-8") as f:
                f.write(f"# 📋 ReAct Agent Blackboard\n")
                f.write(f"# Session: {session_id} | Question: {question_id}\n\n")
                if user_question:
                    f.write(f"## 💬 SORU\n{user_question}\n\n")
                f.write(f"## 📊 SONUÇLAR\n")
        except Exception as e:
            _log(f"⚠️ Blackboard init error: {e}")
    
    _init_blackboard()
    
    def _append_to_blackboard(step_name: str, record_count: int, success: bool):
        try:
            status = "✅" if success else "❌"
            with open(blackboard_path, "a", encoding="utf-8") as f:
                f.write(f"{status} {step_name}: {record_count} kayıt\n")
        except Exception as e:
            _log(f"⚠️ Blackboard append error: {e}")
    
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
            step_name: Adım adı (örn: step_1_customer_search)
        
        Returns:
            Sorgu sonucu veya hata mesajı
        """
        mcp_read = mcp_tool_map.get("read_neo4j_cypher")
        if not mcp_read:
            return '{"error": "MCP read_neo4j_cypher tool not found"}'
        
        try:
            result = await mcp_read.ainvoke({"query": cypher})
            result_str = str(result) if result else ""
            
            # Hata kontrolü
            is_error = "HATA:" in result_str or "ERROR:" in result_str or "❌" in result_str
            
            # Kayıt sayısı
            records = []
            if result_str and not is_error:
                lines = [l.strip() for l in result_str.split('\n') if l.strip() and l.strip().startswith('(')]
                records = lines
            
            record_count = len(records)
            success = record_count > 0 and not is_error
            
            # Dosyaya kaydet
            suffix = "success" if success else "failed"
            file_path = os.path.join(findings_base, f"{step_name}_{suffix}.txt")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(f"<query>\n{cypher}\n</query>\n\n")
                f.write(f"<result>\n{result_str}\n</result>\n")
            
            _log(f"📁 Cypher: {step_name} → {record_count} records")
            _append_to_blackboard(step_name, record_count, success)
            
            # Sonuç döndür - tool'un çıktısı modele gider
            if success:
                return f"""✅ {record_count} kayıt bulundu.

{result_str[:3000]}{"..." if len(result_str) > 3000 else ""}"""
            else:
                return f"""❌ Sonuç bulunamadı veya hata oluştu.

{result_str[:1000]}"""
                
        except Exception as e:
            _log(f"❌ Cypher error: {e}", "error")
            return f'{{"error": "{str(e)}"}}'
    
    # =========================================================================
    # EXECUTE EMBEDDING QUERY TOOL
    # =========================================================================
    @tool
    async def execute_embedding_query(query_text: str, cypher_query: str, step_name: str) -> str:
        """
        Embedding (semantic) araması yap.
        
        USE FOR:
        - Belge içeriği araması
        - Semantic arama (anlam bazlı)
        
        IMPORTANT:
        - query_text: Sadece KONU (örn: "taksit planı"), varyasyon DEĞİL!
        - cypher_query: MUTLAKA $embedding_vector ve gds.similarity.cosine içermeli
        - MUTLAKA filtrelenmiş sorgu kullan!
        
        Args:
            query_text: Aranacak konu (semantic search için)
            cypher_query: Cypher sorgusu ($embedding_vector içermeli)
            step_name: Adım adı
        
        Returns:
            Arama sonucu veya hata mesajı
        """
        mcp_embedding = mcp_tool_map.get("read_neo4j_cypher_with_embedding")
        if not mcp_embedding:
            return '{"error": "MCP embedding tool not found"}'
        
        try:
            result = await mcp_embedding.ainvoke({
                "query_text": query_text,
                "cypher_query": cypher_query
            })
            result_str = str(result) if result else ""
            
            # Hata kontrolü
            is_error = "HATA:" in result_str or "ERROR:" in result_str or "❌" in result_str
            
            # Kayıt sayısı
            records = []
            if result_str and not is_error:
                lines = [l.strip() for l in result_str.split('\n') if l.strip()]
                records = [l for l in lines if 'score' in l.lower() or l.startswith('(')]
            
            record_count = len(records)
            success = record_count > 0 and not is_error
            
            # Dosyaya kaydet
            suffix = "success" if success else "failed"
            file_path = os.path.join(findings_base, f"{step_name}_{suffix}.txt")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(f"<query>\nSEARCH: {query_text}\n{cypher_query}\n</query>\n\n")
                f.write(f"<result>\n{result_str}\n</result>\n")
            
            _log(f"📁 Embedding: {step_name} → {record_count} records")
            _append_to_blackboard(step_name, record_count, success)
            
            if success:
                return f"""✅ {record_count} içerik bulundu.

{result_str[:4000]}{"..." if len(result_str) > 4000 else ""}

⚠️ FALSE POSITIVE KONTROLÜ: Dönen chunk.text'lerde "{query_text}" geçiyor mu kontrol et!"""
            else:
                return f"""❌ İçerik bulunamadı.

Öneriler:
1. Text CONTAINS ile fallback dene: execute_cypher_query ile toLower(c.text) CONTAINS 'terim'
2. Farklı terimler dene (Türkçe/İngilizce)
3. İlişki yolunu kontrol et (PART_OF mu FIRST_CHUNK mu?)"""
                
        except Exception as e:
            _log(f"❌ Embedding error: {e}", "error")
            return f'{{"error": "{str(e)}"}}'
    
    # =========================================================================
    # GET GUIDE TOOL
    # =========================================================================
    @tool
    def get_guide(topic: str) -> str:
        """
        Strateji rehberi al.
        
        Topics:
        - KESIF: Entity varyasyon bulma
        - ICERIK: Chunk/embedding arama
        - METADATA: İlişki takibi
        - CYPHER_RULES: Sorgu yazım kuralları
        - FALSE_POSITIVE: Embedding doğrulama
        
        Args:
            topic: Rehber konusu
        
        Returns:
            Rehber içeriği
        """
        prompts_dir = os.path.join(os.path.dirname(__file__), "prompts")
        
        topic_lower = topic.lower().replace("_", "")
        topic_map = {
            "kesif": "kesif.md",
            "keşif": "kesif.md",
            "icerik": "icerik.md",
            "içerik": "icerik.md",
            "metadata": "metadata.md",
            "falsepositive": "false_positive.md",
            "false_positive": "false_positive.md",
            "cypherrules": "cypher_rules.md",
            "cypher_rules": "cypher_rules.md",
            "cypher": "cypher_rules.md",
            "finalcevap": "final_cevap.md",
            "final_cevap": "final_cevap.md",
            "cevap": "final_cevap.md",
        }
        
        filename = topic_map.get(topic_lower)
        if not filename:
            available = ", ".join(["KESIF", "ICERIK", "METADATA", "FALSE_POSITIVE", "CYPHER_RULES"])
            return f"❌ Bilinmeyen rehber: {topic}. Mevcut: {available}"
        
        guide_path = os.path.join(prompts_dir, filename)
        if not os.path.exists(guide_path):
            return f"❌ Rehber bulunamadı: {guide_path}"
        
        with open(guide_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        _log(f"📖 Guide loaded: {topic}")
        return content
    
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
    
    return [execute_cypher_query, execute_embedding_query, get_guide, add_source]


# ============================================================================
# REACT AGENT CLASS
# ============================================================================

class ReactAgent:
    """
    OpenAI Prompt Caching optimizasyonlu ReAct Agent
    
    Özellikler:
    - Tek agent (Orchestrator-Worker yerine)
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
        """Neo4j URL'ini al"""
        uri = os.getenv("NEO4J_URI", "")
        database = os.getenv("NEO4J_DATABASE", "neo4j")
        return f"{uri}/{database}"
    
    def _get_conversation_history(self, session_id: str) -> List[Dict[str, str]]:
        """PostgreSQL'den conversation history al"""
        try:
            import importlib
            postgres_module = importlib.import_module("src.shared.postgres_chat_history")
            PostgresChatHistory = getattr(postgres_module, "PostgresChatHistory")
            
            pg_history = PostgresChatHistory()
            messages = pg_history.get_messages(session_id, limit=10)
            
            history: List[Dict[str, str]] = []
            for msg in messages:
                role = "user" if msg.get("role") == "Human" else "assistant"
                content = msg.get("content", "")
                if content:
                    history.append({"role": role, "content": content})
            
            return history
        except Exception as e:
            _log(f"⚠️ History fetch error: {e}", "warning")
            return []
    
    def _save_to_history(self, session_id: str, role: str, content: str) -> None:
        """PostgreSQL'e mesaj kaydet"""
        try:
            import importlib
            postgres_module = importlib.import_module("src.shared.postgres_chat_history")
            PostgresChatHistory = getattr(postgres_module, "PostgresChatHistory")
            
            pg_history = PostgresChatHistory()
            pg_history.add_message(session_id, role, content)
        except Exception as e:
            _log(f"⚠️ History save error: {e}", "warning")
    
    def _build_system_prompt(self, schema_info: str) -> str:
        """
        Cache-optimized system prompt oluştur.
        
        Prompt Caching için:
        - Sabit prefix (instructions + schema) → Cache'lenir
        - Dinamik suffix ayrı tutulur
        """
        # Şema bilgisini prefix'e ekle
        full_prefix = CACHED_SYSTEM_PREFIX + schema_info
        return full_prefix
    
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
        if self.mcp_client is None:
            if MultiServerMCPClient is None:
                raise ImportError("MCP Adapters not available")
            config = get_mcp_server_config()
            _log(f"📡 MCP connecting to: {config}")
            self.mcp_client = MultiServerMCPClient(config)
            await self.mcp_client.__aenter__()
            tools = self.mcp_client.get_tools()
            # get_tools() might be async in some versions
            if asyncio.iscoroutine(tools):
                self.mcp_tools = await tools
            else:
                self.mcp_tools = tools
            tool_count = len(self.mcp_tools) if self.mcp_tools else 0
            _log(f"✅ MCP connected, {tool_count} tools available")
        
        # System prompt oluştur (cache-optimized)
        system_prompt = self._build_system_prompt(schema_info)
        _log(f"📜 System prompt: {len(system_prompt)} chars")
        
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
        
        # GPT-5 için reasoning_effort
        if "gpt-5" in actual_model.lower() and self.reasoning_effort:
            _log(f"🔧 Model: {actual_model}, reasoning={self.reasoning_effort}")
            return ChatOpenAI(
                model=actual_model,
                api_key=SecretStr(api_key) if api_key else None,
                reasoning={"effort": self.reasoning_effort}
            )
        else:
            _log(f"🔧 Model: {actual_model}")
            return ChatOpenAI(
                model=actual_model,
                api_key=SecretStr(api_key) if api_key else None
            )
    
    async def stream_query_response(
        self, 
        question: str, 
        session_id: str = "", 
        question_id: str = "", 
        **kwargs
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        ReAct Agent ile streaming cevap üret.
        
        Args:
            question: Kullanıcı sorusu
            session_id: Oturum ID'si
            question_id: Soru ID'si
        
        Yields:
            Streaming response chunks
        """
        import time
        import uuid
        
        # Question ID
        if not question_id:
            question_id = str(uuid.uuid4())[:8]
        else:
            question_id = question_id[:8]
        
        # Session sources temizle
        _clear_session_sources(question_id)
        
        # Timing
        total_start = time.time()
        
        try:
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
                question_id,
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
            
            agent = create_agent(
                model=agent_config["model"],
                tools=react_tools,
                system_prompt=agent_config["system_prompt"],
                middleware=agent_config["middleware"],
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
            
            async for chunk in agent.astream(agent_input, stream_mode="updates"):  # type: ignore[arg-type]
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
                        
                        # Tool calls
                        if hasattr(message, "tool_calls") and message.tool_calls:
                            for tc in message.tool_calls:
                                tool_call_count += 1
                                tool_name = tc.get("name", "unknown") if isinstance(tc, dict) else getattr(tc, "name", "unknown")
                                tool_args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
                                
                                _log(f"[REACT] Tool #{tool_call_count}: {tool_name}")
                                
                                # Kullanıcıya göster
                                thinking_msg = None
                                if tool_name == "execute_cypher_query":
                                    thinking_msg = "🔍 Veritabanında arama yapılıyor..."
                                elif tool_name == "execute_embedding_query":
                                    query_text = tool_args.get("query_text", "")
                                    thinking_msg = f"📄 İçerik araması: {query_text[:40]}..."
                                elif tool_name == "get_guide":
                                    topic = tool_args.get("topic", "")
                                    thinking_msg = f"📚 {topic} rehberi yükleniyor..."
                                elif tool_name == "add_source":
                                    thinking_msg = "📎 Kaynak ekleniyor..."
                                
                                if thinking_msg:
                                    yield {
                                        "type": "thinking_step",
                                        "message": thinking_msg,
                                        "session_id": session_id,
                                    }
                        
                        # Tool results
                        if msg_type == "ToolMessage":
                            tool_content = getattr(message, "content", "")
                            if "✅" in tool_content and "kayıt" in tool_content:
                                match = re.search(r'(\d+)\s*kayıt', tool_content)
                                if match:
                                    yield {
                                        "type": "thinking_step",
                                        "message": f"✅ {match.group(1)} kayıt bulundu!",
                                        "result_type": "success",
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
                self._save_to_history(session_id, "AI", response_text)
                
                # Kaynakları al
                sources = _get_session_sources(question_id)
                
                total_time = time.time() - total_start
                
                yield {
                    "type": "final_response",
                    "content": response_text,
                    "sources": {
                        "documents": list(sources["documents"]),
                        "pages": list(sources["pages"]),
                    },
                    "metrics": {
                        "total_time": round(total_time, 2),
                        "tool_calls": tool_call_count,
                    },
                    "session_id": session_id,
                    "timestamp": datetime.now().isoformat(),
                }
            else:
                yield {
                    "type": "error",
                    "message": "Cevap oluşturulamadı.",
                    "session_id": session_id,
                }
                
        except Exception as e:
            _log(f"❌ Stream error: {e}", "error")
            import traceback
            traceback.print_exc()
            yield {
                "type": "error",
                "message": f"Bir hata oluştu: {str(e)}",
                "session_id": session_id,
            }
    
    async def close(self):
        """Kaynakları temizle"""
        if self.mcp_client:
            try:
                await self.mcp_client.__aexit__(None, None, None)
            except:
                pass
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

