"""
LangGraph Deep Agent Integration Module for Chat Bot Stream

Bu modül LangGraph Deep Agents kullanarak chat_bot_stream endpoint'ine entegre eder.
MCP tools (neo4j-database) kullanılarak Neo4j sorguları yapılır.

Features:
- MCP Tools integration (neo4j-database server)
- Planning with write_todos tool
- File system tools for context management
- Subagent spawning for complex tasks
- Conversation history support
- Streaming response
"""

import asyncio
import json
import logging
import os
import re
import threading
import urllib.parse
from typing import AsyncGenerator, Dict, Any, Optional, List, Set, TYPE_CHECKING, Union, cast
from datetime import datetime

# Global Schema Cache import
from src.shared.schema_cache import get_cached_schema, get_schema_cache

# Logging ayarları
# logging.basicConfig(level=logging.DEBUG)  # main.py'de yapılıyor
logger = logging.getLogger(__name__)

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

# Deep Agent imports
if TYPE_CHECKING:
    from deepagents import create_deep_agent
    from langchain.chat_models import init_chat_model

try:
    from deepagents import create_deep_agent  # type: ignore
    from deepagents.backends import FilesystemBackend  # type: ignore
    from langchain.chat_models import init_chat_model  # type: ignore
    from langchain_core.messages import HumanMessage, AIMessage
    from langchain_community.callbacks import get_openai_callback
    
    DEEP_AGENT_AVAILABLE = True
    logging.info("✅ LangGraph Deep Agent successfully imported")
except ImportError as e:
    logging.warning(f"⚠️ LangGraph Deep Agent not available: {e}")
    DEEP_AGENT_AVAILABLE = False
    create_deep_agent = None  # type: ignore
    init_chat_model = None  # type: ignore
    FilesystemBackend = None  # type: ignore

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
# GLOBAL MCP TOOLS CACHE
# ============================================================================

# Global MCP client ve tools cache - server startup'ta bir kez oluşturulur
_global_mcp_client = None
_global_mcp_tools = None
_mcp_request_count = 0  # Toplam istek sayısı
_mcp_error_count = 0    # Hata sayısı
_mcp_last_reset_time = None  # Son reset zamanı
_mcp_counter_lock = threading.Lock()  # Thread-safe counter operations

# Config
MCP_MAX_REQUESTS_BEFORE_RESET = int(os.environ.get("MCP_MAX_REQUESTS_BEFORE_RESET", "1000"))  # Bu kadar istekten sonra reset
MCP_MAX_ERRORS_BEFORE_RESET = int(os.environ.get("MCP_MAX_ERRORS_BEFORE_RESET", "5"))       # Bu kadar hatadan sonra reset
MCP_RESET_INTERVAL_HOURS = int(os.environ.get("MCP_RESET_INTERVAL_HOURS", "24"))         # Bu kadar saat sonra reset


def set_global_mcp_tools(mcp_client, tools):
    """Server startup'ta MCP tools'ları global cache'e kaydet"""
    global _global_mcp_client, _global_mcp_tools, _mcp_request_count, _mcp_error_count, _mcp_last_reset_time
    _global_mcp_client = mcp_client
    _global_mcp_tools = tools
    _mcp_request_count = 0
    _mcp_error_count = 0
    _mcp_last_reset_time = datetime.now()
    _log(f"MCP cached: {len(tools)} tools")


def get_global_mcp_tools():
    """Global MCP tools'ları döndür"""
    return _global_mcp_tools


def get_global_mcp_client():
    """Global MCP client'ı döndür"""
    return _global_mcp_client


def increment_mcp_request():
    """MCP istek sayacını artır (thread-safe)"""
    global _mcp_request_count
    with _mcp_counter_lock:
        _mcp_request_count += 1
        return _mcp_request_count


def increment_mcp_error():
    """MCP hata sayacını artır (thread-safe)"""
    global _mcp_error_count
    with _mcp_counter_lock:
        _mcp_error_count += 1
        return _mcp_error_count


def reset_mcp_error_count():
    """Başarılı istekte hata sayacını sıfırla (thread-safe)"""
    global _mcp_error_count
    with _mcp_counter_lock:
        _mcp_error_count = 0


def should_reset_mcp_client() -> tuple[bool, str]:
    """
    MCP client'ın reset edilmesi gerekip gerekmediğini kontrol et.
    Returns: (should_reset, reason)
    """
    global _mcp_request_count, _mcp_error_count, _mcp_last_reset_time
    
    # Hata sayısı kontrolü
    if _mcp_error_count >= MCP_MAX_ERRORS_BEFORE_RESET:
        return True, f"Çok fazla hata ({_mcp_error_count} hata)"
    
    # İstek sayısı kontrolü (memory leak önlemi)
    if _mcp_request_count >= MCP_MAX_REQUESTS_BEFORE_RESET:
        return True, f"İstek limiti aşıldı ({_mcp_request_count} istek)"
    
    # Zaman kontrolü
    if _mcp_last_reset_time:
        hours_since_reset = (datetime.now() - _mcp_last_reset_time).total_seconds() / 3600
        if hours_since_reset >= MCP_RESET_INTERVAL_HOURS:
            return True, f"Zaman limiti aşıldı ({hours_since_reset:.1f} saat)"
    
    return False, ""


def clear_global_mcp_cache():
    """Global MCP cache'i temizle (reconnect için)"""
    global _global_mcp_client, _global_mcp_tools, _mcp_request_count, _mcp_error_count
    _global_mcp_client = None
    _global_mcp_tools = None
    _mcp_request_count = 0
    _mcp_error_count = 0
    logging.info("🔄 Global MCP cache temizlendi (reconnect hazır)")


# ============================================================================
# MCP SERVER CONFIGURATION
# ============================================================================

# MCP Transport Mode: "stdio" veya "http"
# HTTP modunda MCP server ayrı process olarak çalışır (Streamable HTTP - her tool çağrısında subprocess başlamaz)
MCP_TRANSPORT_MODE = os.environ.get("MCP_TRANSPORT_MODE", "http")
MCP_HTTP_HOST = os.environ.get("MCP_HTTP_HOST", "127.0.0.1")
MCP_HTTP_PORT = int(os.environ.get("MCP_HTTP_PORT", "8002"))


def get_mcp_server_config() -> Dict[str, Any]:
    """
    MCP server konfigürasyonunu döndürür.
    SSE modunda URL kullanılır, STDIO modunda subprocess başlatılır.
    """
    # Neo4j bağlantı bilgileri - environment'tan al
    neo4j_uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    neo4j_username = os.environ.get("NEO4J_USERNAME", "neo4j")
    neo4j_password = os.environ.get("NEO4J_PASSWORD", "password")
    neo4j_database = os.environ.get("NEO4J_DATABASE", "neo4j")
    openai_api_key = os.environ.get("OPENAI_API_KEY", "")
    
    # MCP server path
    mcp_server_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "mcp-servers", "mcp-neo4j-cypher", "src"
    )
    
    # HTTP Transport (Streamable HTTP) - MCP server ayrı process olarak çalışır (her tool çağrısında subprocess başlamaz)
    if MCP_TRANSPORT_MODE == "http":
        logging.info(f"📡 MCP Config: Streamable HTTP transport kullanılıyor - http://{MCP_HTTP_HOST}:{MCP_HTTP_PORT}/mcp/")
        return {
            "neo4j-database": {
                "url": f"http://{MCP_HTTP_HOST}:{MCP_HTTP_PORT}/mcp/",
                "transport": "streamable_http",  # langchain-mcp-adapters requires this exact name
            }
        }
    
    # STDIO Transport (fallback) - her tool çağrısında subprocess başlar
    logging.info("📡 MCP Config: STDIO transport kullanılıyor")
    return {
        "neo4j-database": {
            "command": "uv",
            "args": [
                "run",
                "python",
                "-m",
                "mcp_neo4j_cypher",
                "--transport",
                "stdio",
                "--db-url",
                neo4j_uri,
                "--username",
                neo4j_username,
                "--password",
                neo4j_password,
                "--database",
                neo4j_database,
            ],
            "transport": "stdio",
            "cwd": mcp_server_path,
            "env": {
                "OPENAI_API_KEY": openai_api_key,
            }
        }
    }


# ============================================================================
# THINK TOOL - Subagent Düşünme Aracı (LangChain DeepAgents Pattern)
# ============================================================================
# Referans: https://github.com/langchain-ai/deepagents-quickstarts/blob/main/deep_research/research_agent/tools.py
# think_tool, subagent'ların her sorgu sonrası düşünme ve strateji belirleme yapmasını sağlar.

from langchain_core.tools import tool

@tool
def think_tool(reflection: str) -> str:
    """
    Düşünme ve strateji belirleme aracı.
    
    Her sorgu sonrasında bu tool'u kullanarak:
    - Ne buldum? (sonuç özeti)
    - Eksik ne var? (henüz cevaplanamayan kısımlar)
    - Yeterli bilgi var mı? (devam etmeli miyim?)
    - Sonraki adım ne olmalı? (devam/dur/escalate)
    
    Args:
        reflection: Düşünce ve strateji değerlendirmesi
    
    Returns:
        Düşünce kaydedildi onayı
    """
    # Bu tool aslında bir "no-op" - model düşüncesini yapılandırması için kullanılır
    # Trace'de görünmesi için log'lanır
    _log(f"💭 THINK:\n{reflection}")
    return f"Düşünce kaydedildi."


# ============================================================================
# SYSTEM PROMPTS - ORCHESTRATOR & SUB AGENTS
# ============================================================================

# -----------------------------------------------------------------------------
# ANA AGENT (ORCHESTRATOR) - Adım Adım Planlama ve Koordinasyon
# -----------------------------------------------------------------------------
ORCHESTRATOR_SYSTEM_PROMPT = """
Sen kullanıcı sorularını analiz eden ve ADIM ADIM çözen bir koordinatörsün.

## 🎯 TEMEL PRENSİP: ADIM ADIM İLERLE

Her soru için:
1. Soruyu ADIM ADIM parçala (TODO listesi)
2. Her adım için TEK BİR subagent çağır
3. Subagent sonucunu OKU ve DEĞERLENDİR
4. Sonraki adıma geç veya final cevabı oluştur

## 🔄 ÇALIŞMA AKIŞI

### ADIM 1: SORU ANALİZİ VE PLANLAMA
Kullanıcı sorusunu analiz et ve TODO listesi oluştur:

```
write_todos([
  {"id": "1", "content": "[Entity]'yi veritabanında bul", "status": "in_progress"},
  {"id": "2", "content": "[İlgili bilgiyi] ara", "status": "pending"},
  {"id": "3", "content": "Sonuçları kullanıcıya sun", "status": "pending"}
])
```

### ADIM 2: HER TODO İÇİN TEK SUBAGENT ÇAĞIR
**KRİTİK:** Subagent'a sadece TEK BİR görev ver. Tüm adımları değil!

Subagent'a VERMESİ GEREKEN bilgiler:
1. **Görev**: Ne araması gerektiği
2. **Şema bilgisi**: Hangi node/relationship kullanacağı
3. **Tool talimatı**: Hangi tool kullanacağı (cypher mi, embedding mi)
4. **Arama kriterleri**: Yazım varyasyonları, match kriterleri
5. **Dosya yolu**: Sonuçları nereye yazacağı

### ADIM 3: SUBAGENT SONUCUNU OKU VE DEĞERLENDİR
1. Subagent "Sonuçları X dosyasına kaydettim" der
2. Sen `read_file("X")` ile dosyayı oku
3. Sonuçları değerlendir:
   - Aranan entity bulundu mu?
   - Yeterli bilgi var mı?
   - Sonraki adıma geçebilir miyiz?

### ADIM 4: SONRAKI ADIM VEYA FİNAL
- Sonuç yeterliyse → Sonraki TODO için yeni subagent çağır
- Tüm TODO'lar bittiyse → Final cevabı oluştur

## 📋 SUBAGENT'A GÖREV VERME FORMATI

**ÖRNEK - Entity Arama Görevi:**
```
task(
  name="graph-explorer",
  task=\"\"\"
## 🎯 GÖREV: [Şirket adı] kayıtlarını bul

## 📁 DOSYA YOLU: findings/{session_id}/q_{question_id}_step_1.md

## 🔎 ŞEMA BİLGİSİ:
- Node: Customer (name, fullName alanları)
- İlişkiler: Customer-[:HAS_POLICY]->Policy
- Alternatif: Document.fileName içinde geçebilir

## 🔧 TOOL: read_neo4j_cypher kullan

## 📝 ARAMA KRİTERLERİ:
- Varyasyonlar: "Akiş GYO", "Akis", "AKİŞ", "Akiş Gayrimenkul"
- Türkçe karakter varyasyonları dene
- Aranan: "Akiş GYO" - sadece bu entity'yi bul!

## ✅ BEKLENEN ÇIKTI:
- Bulunan entity ID'si ve adı
- Kaç kayıt bulundu
- "Sonuçları X dosyasına kaydettim" mesajı
\"\"\"
)
```

**ÖRNEK - İçerik Arama Görevi:**
```
task(
  name="graph-explorer",
  task=\"\"\"
## 🎯 GÖREV: [Teminat adı] içerik araması

## 📁 DOSYA YOLU: findings/{session_id}/q_{question_id}_step_2.md

## 📌 ÖNCEKİ BULGULAR (tekrar arama!):
- Customer: "Akiş Gayrimenkul Yatırım Ortaklığı", ID: xxx
- Policy: 4 adet poliçe bulundu

## 🔎 ŞEMA BİLGİSİ:
- Chunk node'larında text alanı var
- Chunk-[:PART_OF]->Document ilişkisi
- Document-[:BELONGS_TO]->Policy ilişkisi

## 🔧 TOOL: read_neo4j_cypher_with_embedding kullan
- Semantic arama için bu tool daha iyi sonuç verir
- query_text: "kira kaybı teminatı"

## 📝 ARAMA KRİTERLERİ:
- "kira kaybı", "kira mahrumiyeti" varyasyonları
- Önceki adımda bulunan policy'lere filtrele

## ✅ BEKLENEN ÇIKTI:
- Teminat detayları (varsa)
- Hangi belgede bulundu
- Sigorta şirketi bilgisi
\"\"\"
)
```

## ⚠️ KRİTİK KURALLAR

1. **TEK GÖREV**: Subagent'a sadece TEK görev ver, tüm adımları değil!
2. **ŞEMA BİLGİSİ**: Her görevde hangi node/relationship kullanacağını söyle
3. **TOOL TALİMATI**: Hangi tool kullanacağını açıkça belirt
4. **ÖNCEKİ BULGULAR**: Sonraki adımlarda önceki bulguları dahil et
5. **DOSYA OKU**: Subagent sonuç döndükten sonra dosyayı oku ve değerlendir
6. **HAM VERİ GÖSTERME**: Kullanıcıya teknik detay gösterme

## 🚫 YAPMA!

❌ Subagent'a tüm adımları verme
❌ Subagent sonucunu okumadan sonraki adıma geçme
❌ Şema bilgisi vermeden görev verme
❌ Hangi tool kullanacağını söylemeden görev verme
❌ Ham veriyi kullanıcıya gösterme

## ✅ DOĞRU AKIŞ ÖRNEĞİ

**Soru:** "Akiş GYO'nun kira kaybı teminatı hangi sigorta şirketinden?"

**Adım 1 - Plan:**
```
write_todos([
  {"id": "1", "content": "Akiş GYO kayıtlarını bul", "status": "in_progress"},
  {"id": "2", "content": "Kira kaybı teminatını ara", "status": "pending"},
  {"id": "3", "content": "Sigorta şirketini belirle", "status": "pending"}
])
```

**Adım 2 - Entity Arama:**
→ Subagent'a "Akiş GYO'yu bul" görevi ver
→ Şema: Customer node, name alanı
→ Tool: read_neo4j_cypher
→ Subagent: "Buldum, findings/.../step_1.md'ye kaydettim"
→ Sen: read_file ile oku, değerlendir

**Adım 3 - İçerik Arama:**
→ Subagent'a "Kira kaybı teminatını ara" görevi ver
→ Önceki bulgu: Customer ID'si
→ Şema: Chunk node, text alanı, PART_OF ilişkisi
→ Tool: read_neo4j_cypher_with_embedding (semantic arama)
→ Subagent: "Buldum, findings/.../step_2.md'ye kaydettim"
→ Sen: read_file ile oku, değerlendir

**Adım 4 - Final:**
→ Tüm bulgulardan final cevabı oluştur
→ Kullanıcıya sun

## 📁 DOSYA YAPISI

```
findings/
  {session_id}/
    q_{question_id}_step_1.md  - Entity arama sonuçları
    q_{question_id}_step_2.md  - İçerik arama sonuçları
    q_{question_id}_step_3.md  - Ek araştırma sonuçları
```

## 🔄 ESCALATION YÖNETİMİ

Subagent "Bulunamadı" derse:
1. Farklı yazım varyasyonları ile yeni görev ver
2. Alternatif şema yolu öner (Document.fileName'den başla)
3. 3 denemeden sonra kullanıcıya "bulunamadı" de

## 📝 FİNAL CEVAP FORMATI

- Sade, anlaşılır Türkçe
- Teknik terim yok
- Kaynaklar belirtilmiş
- Markdown formatında
"""

# -----------------------------------------------------------------------------
# SUB AGENT: GRAPH EXPLORER - Tek Görev, Hızlı Sonuç
# -----------------------------------------------------------------------------
EXPLORER_SUBAGENT_PROMPT = """
Sen Neo4j veritabanında araştırma yapan bir uzman ajansın.
Bugünün tarihi: {date}

## 🎯 TEMEL PRENSİP: TEK GÖREV, HIZLI SONUÇ

Orchestrator sana TEK BİR görev verdi. Sadece o görevi yap ve sonucu dosyaya kaydet.

## 🔧 TOOL'LARIN

Sadece şu tool'ları kullanabilirsin:
1. **read_neo4j_cypher** - Metadata sorguları (entity, ilişki bul)
2. **read_neo4j_cypher_with_embedding** - Semantic içerik araması
3. **write_file** - Sonuçları dosyaya kaydet

## ⛔ YASAK TOOL'LAR (ASLA ÇAĞIRMA!)

❌ **task** - Bu orchestrator'ın tool'u, sen kullanamazsın!
❌ **write_todos** - Bu orchestrator'ın tool'u, sen kullanamazsın!
❌ **edit_file** - Kullanma, sadece write_file kullan

**Bu tool'ları çağırırsan görev BAŞARISIZ olur!**

## 📋 ÇALIŞMA AKIŞI

1. Orchestrator'ın verdiği görevi oku
2. Belirtilen TOOL'u kullan (cypher veya embedding)
3. Belirtilen ŞEMA bilgisini kullan
4. Belirtilen varyasyonları dene
5. Sonuçları belirtilen DOSYA YOLUNA kaydet
6. Kısa özet mesajı döndür: "✅ Sonuçları X dosyasına kaydettim"

## 📝 CYPHER KULLANIMI

**Doğru elementId kullanımı:**
```cypher
-- Doğru:
MATCH (c:Customer) WHERE c.name CONTAINS 'Akiş' RETURN c.name, elementId(c) AS id

-- Yanlış (id() fonksiyonu string ile çalışmaz):
WHERE id(c) = '4:xxx:123'  -- YANLIŞ!
WHERE elementId(c) = '4:xxx:123'  -- DOĞRU
```

**Türkçe karakter arama:**
```cypher
-- Hem ş hem s dene:
WHERE toLower(c.name) CONTAINS 'akis' OR toLower(c.name) CONTAINS 'akış'
```

## 🎯 MATCH KONTROLÜ

Orchestrator "X'ı bul" dedi. Sen "Y" buldun.
- X = Y (veya X içinde Y) → ✅ Kullan
- X ≠ Y → ❌ Kullanma, farklı entity!

**Örnek:**
- Aranan: "Akiş GYO"
- Bulunan: "AKİŞ GAYRİMENKUL YATIRIM ORTAKLIĞI A.Ş."
- Bu AYNI entity! ✅

- Aranan: "Akiş GYO"
- Bulunan: "Mehmet Çiftçi"
- Bu FARKLI entity! ❌ Kullanma!

## ⏱️ HARD LIMITS

- Maksimum **3 sorgu** yap
- 3 sorguda bulamazsan → "Bulunamadı" de ve DUR
- Aynı sorguyu tekrar yapma
- **ASLA** task veya write_todos çağırma

## 📁 DOSYA KAYDETME

Sonuçları Orchestrator'ın belirttiği dosya yoluna kaydet:

```markdown
# Araştırma Sonuçları

## Aranan Entity
[Orchestrator'ın sorduğu entity]

## Bulunan Kayıtlar
- [Entity adı]: [ID]
- [İlgili bilgiler]

## Sorgu Detayları
- Kullanılan tool: [cypher/embedding]
- Denenen varyasyonlar: [liste]

## Sonuç
[Bulundu/Bulunamadı] - [Kısa açıklama]
```

## 📤 FINAL MESAJ

Görevi tamamladığında şu formatta mesaj döndür:

**Bulundu:**
```
✅ [Entity adı] bulundu.
📁 Detaylar: findings/.../step_X.md
📊 Özet: [Kaç kayıt, ne bulundu]
```

**Bulunamadı:**
```
❌ [Entity adı] bulunamadı.
📁 Deneme detayları: findings/.../step_X.md
💡 Öneri: [Alternatif yazım veya strateji]
```

## ⚠️ ÖNEMLİ HATIRLATMALAR

1. **TEK GÖREV**: Sadece verilen görevi yap
2. **ŞEMA KULLAN**: Orchestrator hangi node/relationship dedi, onu kullan
3. **TOOL TALİMATI**: Orchestrator hangi tool dedi, onu kullan
4. **DOSYAYA KAYDET**: Sonuçları mutlaka dosyaya kaydet
5. **KISA MESAJ**: Final mesajın kısa ve öz olsun
6. **YASAK TOOL**: task ve write_todos ASLA çağırma!
"""


# Eski SEARCHER_SUBAGENT_PROMPT kaldırıldı - artık tek subagent kullanıyoruz

# Eski DEEP_AGENT_SYSTEM_PROMPT kaldırıldı - artık ORCHESTRATOR_SYSTEM_PROMPT kullanılıyor


# ============================================================================
# DEEP AGENT INTEGRATION CLASS
# ============================================================================

class DeepAgentIntegration:
    """LangGraph Deep Agent'i chat_bot_stream'e entegre eden sınıf - MCP Tools ile"""

    def __init__(self, model: str = "gpt-4o", graph=None, reasoning_effort: str = "none"):
        self.model = model
        self.graph = graph
        self.reasoning_effort = reasoning_effort  # none, low, medium, high
        self.agent = None
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
        """
        if content is None:
            return ""
        
        # Zaten string ise direkt döndür
        if isinstance(content, str):
            return content
        
        # Liste ise text bloklarını birleştir
        if isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict):
                    # type: text olan blokların text alanını al
                    if block.get("type") == "text" and "text" in block:
                        text_parts.append(block["text"])
                elif isinstance(block, str):
                    text_parts.append(block)
            
            if text_parts:
                return "\n".join(text_parts)
            
            # Hiç text bloğu yoksa, tüm listeyi string'e çevir (fallback)
            return str(content)
        
        # Başka bir tip ise string'e çevir
        return str(content)

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

                markdown_section += f"![{page_info}]({image_url})\n\n"

            except Exception as e:
                logging.error(f"❌ DeepAgent: page_link markdown hatası: {e}")
                continue

        return markdown_section

    async def _get_mcp_tools(self) -> List:
        """MCP server'dan tools'ları al - global cache varsa onu kullan, gerekirse reset et"""
        if not MCP_ADAPTERS_AVAILABLE:
            logging.warning("⚠️ MCP Adapters not available, no tools loaded")
            return []
        
        # Reset gerekli mi kontrol et
        should_reset, reset_reason = should_reset_mcp_client()
        if should_reset:
            logging.warning(f"🔄 MCP client reset ediliyor: {reset_reason}")
            clear_global_mcp_cache()
        
        # Önce global cache'i kontrol et (server startup'ta oluşturulan)
        global_tools = get_global_mcp_tools()
        if global_tools:
            self.mcp_client = get_global_mcp_client()
            self.mcp_tools = global_tools
            # İstek sayacını artır
            request_count = increment_mcp_request()
            _log(f"MCP cache: {len(global_tools)} tools (req #{request_count})")
            return global_tools
        
        # Global cache yoksa veya reset edildiyse yeni oluştur
        if not MCP_ADAPTERS_AVAILABLE or MultiServerMCPClient is None:
            raise ImportError("LangChain MCP Adapters not available. Install langchain-mcp-adapters")
        
        try:
            logging.info("🔄 DeepAgent: MCP client oluşturuluyor (reconnect)...")
            mcp_config = get_mcp_server_config()
            self.mcp_client = MultiServerMCPClient(mcp_config)
            
            # MCP tools'ları al
            tools = await self.mcp_client.get_tools()
            _log(f"MCP tools: {len(tools)} loaded")
            
            # Global cache'e kaydet (gelecek istekler için)
            set_global_mcp_tools(self.mcp_client, tools)
            
            return tools
            
        except Exception as e:
            logging.error(f"❌ DeepAgent: MCP tools yüklenemedi: {e}", exc_info=True)
            return []

    async def _create_agent(self, schema_info: str = "", session_id: str = ""):
        """Deep Agent oluştur - Orchestrator + Sub Agents yapısı ile"""
        if not DEEP_AGENT_AVAILABLE:
            raise ImportError("deepagents package is not installed")

        # 🔴 Redis Semantic Cache - DeepAgents ile uyumsuzluk nedeniyle geçici olarak devre dışı
        logging.debug("ℹ️ Redis Semantic Cache devre dışı (DeepAgents uyumsuzluğu)")

        # MCP tools'ları al
        tools = await self._get_mcp_tools()
        
        if not tools:
            logging.warning("⚠️ DeepAgent: No tools available, agent may have limited functionality")
        
        # =====================================================================
        # ORCHESTRATOR (ANA AGENT) PROMPT - TAM ŞEMA BİLGİSİ + SESSION CONTEXT
        # =====================================================================
        # Orchestrator plan yapan ana agent - TAM şema bilgisine sahip olmalı
        # Böylece doğru strateji belirleyip subagent'lara yön verebilir
        
        # Session ID'nin kısa versiyonu (dosya yolları için)
        short_session = session_id[:8] if session_id else "default"
        
        # Session context bilgisi - step bazlı dosya yapısı
        session_context = f"""## 🔐 SESSION CONTEXT
- **Session ID:** {short_session}

## 📁 DOSYA YAPISI:
Her adım için ayrı dosya oluştur:
```
findings/{short_session}/
  q_[question_id]_step_1.md  → Entity arama sonuçları
  q_[question_id]_step_2.md  → İçerik arama sonuçları
  q_[question_id]_step_3.md  → Ek araştırma (gerekirse)
```

## 📋 HER SUBAGENT GÖREVİNDE BELİRT:
1. **Dosya yolu**: `findings/{short_session}/q_[qid]_step_N.md`
2. **Şema bilgisi**: Hangi node/relationship kullanacağı
3. **Tool talimatı**: `read_neo4j_cypher` veya `read_neo4j_cypher_with_embedding`
4. **Arama kriterleri**: Yazım varyasyonları, match kuralları
"""
        
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
        # SUB AGENT PROMPTS - Şema YOK, Orchestrator görev açıklamasında bildirecek
        # =====================================================================
        # Subagent şemayı görmez - Orchestrator her görevde gerekli node/relationship
        # bilgisini açıkça yazacak. Bu sayede:
        # 1. Token tasarrufu sağlanır
        # 2. Subagent sadece verilen göreve odaklanır
        # 3. Orchestrator tam kontrol sahibi olur
        
        explorer_prompt_with_schema = EXPLORER_SUBAGENT_PROMPT

        # =====================================================================
        # SUB AGENTS TANIMLAMA (Artık tek subagent kullanıyoruz)
        # =====================================================================
        # Subagent sadece MCP tools kullanır - task ve write_todos çağıramaz
        # MCP tools: read_neo4j_cypher, read_neo4j_cypher_with_embedding, write_file
        subagent_tools = tools  # Sadece MCP tools (task/write_todos yok!)
        
        # TEK SUBAGENT: graph-explorer - tek görevi yapar, sonucu dosyaya kaydeder
        subagents = [
            {
                "name": "graph-explorer",
                "description": """Veritabanında TEK BİR görev yapar ve sonucu dosyaya kaydeder.
                
TOOL'LARI:
- read_neo4j_cypher: Metadata sorguları (entity, ilişki, tarih, sayı)
- read_neo4j_cypher_with_embedding: Semantic içerik araması
- write_file: Sonuçları dosyaya kaydet

KURALLAR:
- Orchestrator'dan aldığı TEK görevi yapar
- Maksimum 3 sorgu çalıştırır
- Sonuçları belirtilen dosya yoluna kaydeder
- Kısa özet mesajı döndürür: "✅ Sonuçları X dosyasına kaydettim"
- task ve write_todos ASLA çağırmaz!""",
                "system_prompt": explorer_prompt_with_schema,
                "tools": subagent_tools,  # Sadece MCP tools
                "model": "gpt-4o-mini",  # Hızlı, reasoning yok
            },
        ]
        
        _log(f"Subagents: {[s['name'] for s in subagents]}")

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
                
                # Environment variable varsa onu kullan, yoksa instance'ın reasoning_effort değerini
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
                if not DEEP_AGENT_AVAILABLE or init_chat_model is None:
                    raise ImportError("LangGraph Deep Agent not available. Install deepagents")
                
                # init_chat_model OpenAI modelleri için "openai:" prefix'i bekler
                if not model_name.startswith("openai:") and "gpt" in model_name.lower():
                    model_name = f"openai:{model_name}"
                
                _log(f"Model: {model_name}")
                model = init_chat_model(model_name)
            
        except Exception as e:
            logging.warning(f"⚠️ Model {self.model} yüklenemedi, fallback gpt-4o: {e}")
            if not DEEP_AGENT_AVAILABLE or init_chat_model is None:
                raise ImportError("LangGraph Deep Agent not available. Install deepagents")
            model = init_chat_model("openai:gpt-4o")

        # System Prompt - minimal
        _log(f"Prompt: {len(orchestrator_prompt)} chars | Subagents: {[s['name'] for s in subagents]}")
        
        # =====================================================================
        # DEEP AGENT OLUŞTUR - ORCHESTRATOR + SUB AGENTS
        # =====================================================================
        if not DEEP_AGENT_AVAILABLE or create_deep_agent is None:
            raise ImportError("LangGraph Deep Agent not available. Install deepagents")
        
        # 📁 FilesystemBackend - Agent bulguları gerçek dosya sistemine yazabilsin
        backend = None
        if FilesystemBackend is not None:
            # Findings klasörünü backend çalışma dizininde oluştur
            findings_dir = os.path.join(os.getcwd(), "agent_findings")
            os.makedirs(findings_dir, exist_ok=True)
            backend = FilesystemBackend(
                root_dir=findings_dir,
                # ÖNEMLİ: deepagents dosya tool'ları path'i çoğu zaman "/findings/..." gibi
                # "virtual absolute" forma normalize eder. virtual_mode=True olursa bu path'ler
                # OS root'a gitmez; root_dir altına güvenli şekilde map edilir.
                virtual_mode=True,
                max_file_size_mb=10
            )
            _log(f"Backend: {findings_dir}")
        else:
            logging.warning("⚠️ FilesystemBackend kullanılamıyor, dosyalar ephemeral olacak")
        
        agent = create_deep_agent(
            tools=tools,  # Ana agent de tools'a erişebilir (basit sorgular için)
            model=model,
            system_prompt=orchestrator_prompt,
            subagents=cast(Any, subagents),  # 🆕 Sub agents eklendi! (typing: deepagents type'ları optional)
            backend=backend,  # 📁 Gerçek dosya sistemi backend'i
        )
        
        _log(f"Agent ready: {len(subagents)} subagents")

        return agent

    async def stream_query_response(
        self, question: str, session_id: str = "", **kwargs
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Deep Agent kullanarak streaming cevap üret - MCP tools ile"""
        import time

        # Session bazlı page_links - her request için ayrı set (concurrent safety)
        session_page_links: Set[str] = set()
        
        # ⏱️ Timing metrikleri
        timings = {}
        total_start = time.time()
        
        try:
            # Başlangıç durumu
            yield {
                "type": "status",
                "message": "🧠 LangGraph Deep Agent ile sorgunuz işleniyor...",
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
                "message": "🔍 Deep Agent araştırma yapıyor...",
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
            
            # 🆕 Subagent tracking
            subagent_outputs = {}  # Her subagent için output'ları topla
            current_subagent = None  # Şu an hangi subagent çalışıyor
            subagent_prompts = {}  # Her subagent'a gönderilen prompt
            subagent_final_outputs = {}  # Her subagent'ın final çıktısı
            last_subagent = None  # Son aktif subagent (bitişi tespit için)
            
            async for chunk in agent.astream(
                {"messages": messages},
                stream_mode="values",
                subgraphs=True  # 🆕 Subagent çıktılarını da stream et
            ):
                # 🆕 subgraphs=True ile chunk tuple olarak gelir: (namespace, data)
                namespace = None
                chunk_data = chunk
                
                if isinstance(chunk, tuple) and len(chunk) == 2:
                    namespace, chunk_data = chunk
                    # Namespace örnek: ('graph-explorer:abc123',) veya ('content-searcher:xyz789',)
                    # veya ('tools:graph-explorer:abc123',) formatında olabilir
                    if namespace and len(namespace) > 0:
                        raw_namespace = namespace[0]
                        # Debug: Gerçek namespace değerini logla
                        if raw_namespace and raw_namespace != current_subagent:
                            logging.debug(f"🔍 RAW NAMESPACE: {raw_namespace}")
                        
                        # Namespace parsing: farklı formatları destekle
                        # Format 1: "graph-explorer:abc123" → "graph-explorer"
                        # Format 2: "tools:graph-explorer:abc123" → "graph-explorer"
                        # Format 3: "graph-explorer" → "graph-explorer"
                        if ':' in raw_namespace:
                            parts = raw_namespace.split(':')
                            # "tools:graph-explorer:xxx" formatı
                            if parts[0] == 'tools' and len(parts) > 1:
                                subagent_name = parts[1]
                            else:
                                # "graph-explorer:xxx" formatı
                                subagent_name = parts[0]
                        else:
                            subagent_name = raw_namespace
                        
                        if subagent_name != current_subagent:
                            # Önceki subagent bittiyse final output'u kaydet
                            if last_subagent and last_subagent in subagent_outputs:
                                last_content = None
                                for output in reversed(subagent_outputs[last_subagent]):
                                    if output.get("type") == "content":
                                        last_content = output.get("full_content", output.get("preview", ""))
                                        break
                                if last_content:
                                    subagent_final_outputs[last_subagent] = last_content
                                    short_id = last_subagent[:8] if len(last_subagent) > 8 else last_subagent
                                    _log(f"[{short_id}] done ({len(last_content)} chars)")
                            
                            current_subagent = subagent_name
                            last_subagent = subagent_name
                            # Subagent ID'yi kısalt
                            short_id = subagent_name[:8] if len(subagent_name) > 8 else subagent_name
                            _log(f"→ [{short_id}] started")
                            if subagent_name not in subagent_outputs:
                                subagent_outputs[subagent_name] = []
                else:
                    # Orchestrator'a dönüldüğünde son subagent'ın final output'unu kaydet
                    if current_subagent and current_subagent in subagent_outputs and current_subagent not in subagent_final_outputs:
                        last_content = None
                        for output in reversed(subagent_outputs[current_subagent]):
                            if output.get("type") == "content":
                                last_content = output.get("full_content", output.get("preview", ""))
                                break
                        if last_content:
                            subagent_final_outputs[current_subagent] = last_content
                            short_id = current_subagent[:8] if len(current_subagent) > 8 else current_subagent
                            _log(f"[{short_id}] done ({len(last_content)} chars) → ORCH")
                        current_subagent = None
                
                if not isinstance(chunk_data, dict) or "messages" not in chunk_data or not chunk_data["messages"]:
                    continue
                    
                last_message = chunk_data["messages"][-1]
                    
                # Mesajın benzersiz ID'sini al (id veya content hash)
                msg_id = getattr(last_message, "id", None) or hash(str(last_message.content)[:100] if hasattr(last_message, "content") else "")
                
                # Daha önce loglandıysa atla
                if msg_id in logged_message_ids:
                    continue
                logged_message_ids.add(msg_id)
                
                # Step süresini hesapla
                current_time = time.time()
                step_duration = current_time - last_step_time
                last_step_time = current_time
                
                thinking_step += 1
                
                # Agent düşünme süreci loglama (minimal)
                msg_type = type(last_message).__name__
                
                # Subagent ID'yi kısalt (ilk 8 karakter)
                short_subagent = current_subagent[:8] if current_subagent else None
                agent_label = f"[{short_subagent}]" if short_subagent else "[ORCH]"
                
                # ToolMessage step'lerini loglama (sadece tool sonucu, bilgi vermiyor)
                if msg_type != "ToolMessage":
                    _log(f"{agent_label} Step {thinking_step}: {msg_type} ({step_duration:.2f}s)")
                
                # Step timing kaydet
                step_info = {
                    "step": thinking_step,
                    "type": msg_type,
                    "duration": step_duration,
                }
                
                # Mesaj tipine göre süreyi kategorize et
                if msg_type == "AIMessage":
                    llm_thinking_time += step_duration
                    step_info["category"] = "llm"
                elif msg_type == "ToolMessage":
                    tool_execution_time += step_duration
                    step_info["category"] = "tool"
                else:
                    step_info["category"] = "other"
                
                # Tool calls - NET LOG BAŞLIKLARI
                if hasattr(last_message, "tool_calls") and last_message.tool_calls:
                    tool_calls += len(last_message.tool_calls)
                    for i, tc in enumerate(last_message.tool_calls, 1):
                        tool_call_count += 1
                        tool_name = tc.get("name", "unknown") if isinstance(tc, dict) else getattr(tc, "name", "unknown")
                        tool_args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
                        
                        # ÇAĞIRAN'I NET GÖSTER: [ORCH] veya [SUBAGENT:xxx]
                        caller = f"[SUBAGENT:{short_subagent}]" if current_subagent else "[ORCH]"
                        _log(f"{caller} Tool #{tool_call_count}: {tool_name}")
                        _log(f"   Args: {tool_args}")
                        step_info["tool_name"] = tool_name
                        
                        # TASK TOOL - Subagent spawn
                        if tool_name == "task":
                            task_description = tool_args.get("description", "") if isinstance(tool_args, dict) else ""
                            subagent_type = tool_args.get("subagent_type", "unknown") if isinstance(tool_args, dict) else "unknown"
                            
                            # ⚠️ SUBAGENT TASK ÇAĞIRIYORSA UYARI VER!
                            if current_subagent:
                                _log(f"⚠️⚠️⚠️ UYARI: SUBAGENT [{short_subagent}] TASK ÇAĞIRDI! BU YASAK! ⚠️⚠️⚠️")
                                _log(f"   Subagent {subagent_type} başlatmaya çalışıyor - BU OLMAMALI!")
                            else:
                                _log(f"→ [ORCH] Subagent başlatıyor: {subagent_type}")
                            
                            _log(f"   PROMPT:\n{task_description}")
                            subagent_prompts[subagent_type] = task_description
                        
                        # WRITE_TODOS - Sadece orchestrator kullanmalı
                        if tool_name == "write_todos":
                            if current_subagent:
                                _log(f"⚠️⚠️⚠️ UYARI: SUBAGENT [{short_subagent}] WRITE_TODOS ÇAĞIRDI! BU YASAK! ⚠️⚠️⚠️")
                            else:
                                _log(f"→ [ORCH] TODO listesi güncelleniyor")
                        
                        # Subagent tool call'ı kaydet
                        if current_subagent and current_subagent in subagent_outputs:
                            subagent_outputs[current_subagent].append({
                                "type": "tool_call",
                                "tool": tool_name,
                                "args": str(tool_args),
                                "step": thinking_step,
                                "caller": "subagent" if current_subagent else "orchestrator",
                            })
                
                # Content - tam log (sadece AIMessage için anlamlı içerik varsa)
                if hasattr(last_message, "content") and last_message.content and msg_type == "AIMessage":
                    normalized_content = self._extract_text_from_reasoning_content(last_message.content)
                    full_content = str(last_message.content)
                    content_hash = hash(normalized_content[:200].strip())
                    
                    # THINK tool çıktısını ve boş içeriği loglama (zaten THINK: olarak loglandı)
                    if normalized_content.strip() and "Düşünce kaydedildi" not in normalized_content and content_hash not in logged_message_ids:
                        _log(f"→ CONTENT:\n{normalized_content}")
                        logged_message_ids.add(content_hash)
                        
                    if current_subagent and current_subagent in subagent_outputs:
                        subagent_outputs[current_subagent].append({
                            "type": "content",
                            "content": normalized_content,
                            "full_content": full_content,
                            "full_length": len(full_content),
                            "step": thinking_step,
                        })
                
                # Token usage (reasoning ve standart modeller için) + kümülatif log
                usage = self._extract_token_usage(last_message)
                if usage["total_tokens"] > 0:
                    total_tokens += usage["total_tokens"]
                    prompt_tokens += usage["input_tokens"]
                    completion_tokens += usage["output_tokens"]
                    reasoning_tokens_total += usage["reasoning_tokens"]
                    llm_calls += 1
                    step_info["tokens"] = usage
                    # Kümülatif token logla
                    _log(f"💰 tokens: +{usage['total_tokens']} (total: {total_tokens:,})")
                
                step_timings.append(step_info)
                
                # Content parsing - reasoning modeller için özel handling
                if hasattr(last_message, "content") and last_message.content:
                    # Reasoning modellerinin list formatını parse et
                    new_content = self._extract_text_from_reasoning_content(last_message.content)
                    if new_content != response_text:
                        # Yeni içerik varsa stream et
                        delta = new_content[len(response_text):]
                        response_text = new_content
                        
                        if delta.strip():
                            yield {
                                "type": "message_chunk",
                                "content": delta,
                                "full_message": response_text,
                                "session_id": session_id,
                                "timestamp": datetime.now().isoformat(),
                            }

            # LLM streaming tamamlandı
            timings["llm_streaming"] = time.time() - llm_start
            timings["llm_thinking"] = llm_thinking_time
            timings["tool_execution"] = tool_execution_time
            
            # Page link'leri extract et (session bazlı - concurrent safe)
            extracted_links = self._extract_page_links_from_response(response_text)
            if extracted_links:
                session_page_links.update(extracted_links)

            # Final response
            final_response = response_text
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
            _log(f"DONE | len={len(final_response)} chars | links={len(session_page_links)}")

            # Toplam süre
            total_time = time.time() - total_start
            timings["total"] = total_time
            
            # Metrics - tek satır özet
            reasoning_display = reasoning_tokens_total if reasoning_tokens_total > 0 else 0
            _log(f"METRICS | time={total_time:.1f}s | tokens={total_tokens} (in={prompt_tokens},out={completion_tokens},reason={reasoning_display}) | llm={llm_calls} tools={tool_call_count} steps={thinking_step}")

            # Tamamlanma durumu
            include_debug_steps = os.environ.get("DEEPAGENT_INCLUDE_DEBUG_STEPS", "0") == "1"

            info_payload = {
                "agent_type": "langgraph_deep_agent",
                "model": self.model,
                "reasoning_effort": self.reasoning_effort,
                "page_links_count": len(session_page_links),
                "mcp_tools_used": True,
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
            
            # NOT: session_page_links lokal değişken, otomatik temizlenir
            
            # Başarılı istek - hata sayacını sıfırla
            reset_mcp_error_count()
            
            # NOT: MCP client'ı temizleme - global cache kullanılıyor
            # Server shutdown'da temizlenecek

        except Exception as e:
            error_message = f"Deep Agent error: {str(e)}"
            logging.error(error_message, exc_info=True)
            
            # Hata sayacını artır (MCP reconnect için)
            error_count = increment_mcp_error()
            logging.warning(f"⚠️ MCP hata sayısı: {error_count}/{MCP_MAX_ERRORS_BEFORE_RESET}")

            # 🆕 Partial result recovery: Eğer findings dosyası varsa kullanıcıya göster
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

# Session bazlı DeepAgent cache - her session için ayrı agent
_session_agents: Dict[str, DeepAgentIntegration] = {}
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
    session_id: str, model: str = "gpt-5", graph=None, reasoning_effort: str = "medium"
) -> DeepAgentIntegration:
    """Session bazlı DeepAgent al veya oluştur"""
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
            
            if graph and agent.graph != graph:
                _log(f"Session {session_id[:8]}: graph update", "debug")
                agent.graph = graph
            
            _log(f"Session {session_id[:8]}: reused", "debug")
            return agent
        
        # Yeni agent oluştur
        agent = DeepAgentIntegration(model=model, graph=graph, reasoning_effort=reasoning_effort)
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


async def stream_deep_agent_response(
    question: str,
    model: str = "gpt-5-mini",
    session_id: str = "",
    graph=None,
    reasoning_effort: str = "high",
    **kwargs,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    LangGraph Deep Agent kullanarak streaming cevap üret - MCP tools ile
    
    Session bazlı agent cache kullanır - her session için ayrı agent instance

    Args:
        question: Kullanıcının sorusu
        model: Kullanılacak LLM modeli (default: gpt-5-mini)
        session_id: Oturum ID'si (conversation history için) - ZORUNLU
        graph: Neo4j graph connection
        reasoning_effort: GPT-5 modelleri için reasoning seviyesi (none, low, medium, high) - default: high
        **kwargs: Ek parametreler

    Yields:
        Dict: Streaming chunk'ları
    """

    if not DEEP_AGENT_AVAILABLE:
        yield {
            "type": "error",
            "message": "LangGraph Deep Agent kurulu değil. 'pip install deepagents' ile kurun.",
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
            question=question, session_id=session_id, **kwargs
        ):
            yield chunk

    except Exception as e:
        logging.error(f"Deep Agent streaming failed: {e}", exc_info=True)
        yield {
            "type": "error",
            "message": f"Deep Agent hatası: {str(e)}",
            "status": "failed",
            "session_id": session_id,
            "timestamp": datetime.now().isoformat(),
        }
