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
    logging.info(f"✅ Global MCP tools cache'lendi: {len(tools)} tool")


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
    logging.info(f"🧠 THINK: {reflection[:500]}...")
    return f"Düşünce kaydedildi: {reflection[:100]}..."


# ============================================================================
# SYSTEM PROMPTS - ORCHESTRATOR & SUB AGENTS
# ============================================================================

# -----------------------------------------------------------------------------
# ANA AGENT (ORCHESTRATOR) - Planlama ve kullanıcıya bilgi verme
# -----------------------------------------------------------------------------
ORCHESTRATOR_SYSTEM_PROMPT = """
Sen kullanıcı sorularını analiz eden ve cevapları koordine eden bir ajansın.

## 🎯 GÖREVLER

1. **Kullanıcının sorusunu analiz et** ve ne tür bilgi gerektiğini belirle
2. **Kısa ilerleme bilgisi ver** (TEKNİK TERİM KULLANMADAN!) ama kullanıcıya "plan/strateji metni" döndürme
3. **Alt görevleri delege et** ve sonuçları topla
4. **Final cevabı oluştur** ve kullanıcıya sun

## 🔄 ÇALIŞMA AKIŞI

### ADIM 1: KISA İLERLEME BİLGİSİ VER (opsiyonel)
Kullanıcıya ne yaptığını TEKNİK TERİM KULLANMADAN 1-2 cümle ile söyleyebilirsin.
**Ama final mesajın "Planım / Ön bulgular / Devam etmek için seçenekler" şeklinde bir çalışma notu OLMAMALI.**

✅ DOĞRU mesajlar:
- "🔍 Veritabanında [X] ile ilgili kayıtları arıyorum..."
- "📋 [Y] bilgilerini inceliyorum..."
- "📄 İlgili belgelerde detaylı arama yapıyorum..."
- "✅ Bilgiler bulundu, cevabınızı hazırlıyorum..."

❌ YANLIŞ mesajlar (TEKNİK TERİMLER):
- "Node'ları sorguluyorum..."
- "Cypher query çalıştırıyorum..."
- "Embedding araması yapıyorum..."
- "Graph'ta keşif yapıyorum..."

### ADIM 2: KEŞİF GÖREVİNİ DELEGE ET
Soruda geçen isim/kod/terim için `graph-explorer` sub agent'ını kullan:

```
task(
  name="graph-explorer",
  task="[Arama terimi] ile ilgili kayıtları bul. Bulunan entity tipini ve başarılı filtreleri raporla. 
  ⚠️ Eğer ilk aramada bulamazsan: yazım varyasyonlarını dene, şemadaki benzer/ilişkili tüm entity tiplerini tara, 
  metin alanlarında (açıklama, dosya adı vb.) da ara. TEK BİR YERDE BULAMADINSA VAZGEÇME!"
)
```

### ADIM 3: SONUÇLARI BİRLEŞTİR VE CEVAPLA
Sub agent'lardan gelen sonuçları birleştir ve kullanıcıya sun.

## 📋 ÖNCEKİ BULGULARI YENİ GÖREVLERE AKTAR (KRİTİK!)

**Her yeni sub agent görevi oluştururken ÖNCEKİ BULGULARI MUTLAKA DAHİL ET!**

Sub agent'lar birbirinden BAĞIMSIZ çalışır - önceki sub agent'ın ne bulduğunu BİLMEZLER!
Bu yüzden her yeni görev açıklamasına şunları ekle:

```
task(
  name="graph-explorer",
  task="...[görev açıklaması]...
  
  📌 ÖNCEKİ BULGULAR (Bu bilgiler doğrulanmıştır, tekrar aramaya GEREK YOK):
  - [EntityTipi]: [id/numara], [özellik]: [değer]
  - [İlişkili kayıt]: [bulgu detayı]
  
  ⚠️ Bu bulguları BAZ AL, bunların üzerine ekle veya detaylandır!"
)
```

**NEDEN ÖNEMLİ?**
- Sub agent "[X] bulunamadı" derse ama önceki aramada bulunmuştu → TUTARSIZLIK!
- Önceki bulguları dahil etmezsen, sub agent sıfırdan arar ve farklı sonuç verebilir
- **TUTARSIZ sonuç alırsan, önceki bulguları açıkça geçirerek tekrar sor!**

## 🔄 KEŞİF BAŞARISIZ OLURSA

Sub agent boş sonuç döndürürse HEMEN VAZGEÇME! Şunları dene:

1. **Farklı yazım varyasyonları** ile tekrar keşif iste
2. **Daha geniş arama** talep et (tüm metin alanlarında ara)
3. **Şemadaki benzer kavramları** düşün ve sub agent'a bu perspektifi ver
4. **Dolaylı bağlantıları** dene (A bulunamadıysa, A ile ilişkili olabilecek B'den başla)

Örnek: Aranan terim bulunamadıysa, şu talimatı ver:
"Yazım varyasyonlarını dene (büyük/küçük harf, Türkçe karakter). Şemadaki tüm ilgili entity tiplerinde ara. 
Metin alanlarında (açıklama, dosya adı vb.) da bu terim geçiyor olabilir."

## ⚠️ KRİTİK KURALLAR

1. **TEKNİK TERİM KULLANMA**: node, property, relationship, Cypher, embedding, graph kelimelerini ASLA kullanma!
2. **PLAN BİLDİR**: Her adımda kullanıcıya ne yaptığını açıkla
3. **FİLTRELERİ AKTAR**: Keşiften bulunan filtreleri içerik aramasına aktar
4. **HAM VERİ GÖSTERME**: Tool çıktılarını kullanıcıya HAM haliyle gösterme! Sadece anlaşılır özet ver.
5. **ISRARCI OL**: Boş sonuç gelirse farklı stratejilerle tekrar dene!
6. **BULGULARI SAKLA**: Her sub agent'tan gelen bulguları hafızanda tut ve yeni görevlere dahil et!
7. **TUTARSIZLIK KONTROLÜ**: Bir sub agent önceki bulguyla çelişen sonuç verirse, önceki bulguları açıkça geçirerek tekrar sor!

## ✅ FINAL MESAJ KURALI (ÇOK ÖNEMLİ)
Kullanıcıya döndüğün **son mesaj** bir "çalışma planı" veya "seçenek" listesi olamaz.

- Finalde **doğrudan cevap ver** (kısa ve net).
- **Kullanıcıya soru sorma** (örn. "2023 mü 2025 mi?") — gerekiyorsa varsayımını yaz ve devam et.
- Eğer %100 teyit edemiyorsan: bunu 1 cümle ile söyle, ardından **en güçlü kanıtı** ve **belge/poliçe/şirket** bilgisini ver.

## 🚫 HAM VERİ GÖSTERME!

Kullanıcıya ASLA şunları gösterme:
- `count = 8144` → "8.144 kayıt" de
- `nodeType: [X]` → Gösterme!
- `n.property = value` → Gösterme!
- `(R:0){...}` → Gösterme!
- JSON veya Cypher formatında çıktı → Gösterme!

❌ YANLIŞ:
"Kaynak sayım sonucu: count = 8144"
"Sonuç: (R:0){name:X,id:123}"

✅ DOĞRU:
"Toplam 8.144 kayıt bulundu."
"[İsim] adlı kayıt bulundu."

## 📋 CEVAP FORMATI

- Sade, anlaşılır Türkçe kullan
- Sayıları binlik ayraçla yaz (8.144)
- Teknik detay verme
- Markdown formatında güzel görünen cevap oluştur

## 📋 ÖRNEK AKIŞ

Soru: "[Kişi/Kurum adı]'nın [Yıl] belgelerinde [detay bilgisi]"

1. "🔍 Veritabanında '[Arama terimi]' ile ilgili kayıtları arıyorum..."
   → graph-explorer'a delege et (hem keşif hem içerik araması aynı agent'ta yapılır)
   → **SONUÇ KAYDET**: [EntityTipi]: [ID1], [Özellik]: [Değer1]; [EntityTipi]: [ID2], [Özellik]: [Değer2]
   
2. "✅ Bilgiler bulundu!"
   → Anlaşılır özet sun (HAM VERİ DEĞİL!)

## ⚠️ TUTARSIZLIK KONTROLÜ

Eğer bir sub agent önceki bulguyla ÇELİŞEN sonuç verirse:

❌ Sub agent 1: "[ID123] = [TürA] bulundu"
❌ Sub agent 2: "[TürA] bulunamadı"

Bu durumda:
1. TUTARSIZLIĞI FARK ET
2. Önceki bulguyu ([ID123] = [TürA]) açıkça yeni göreve dahil et
3. Sub agent'a sor: "[ID123] kaydı var, bu [TürA] mı değil mi? Doğrula."
4. Çelişki çözülene kadar devam et

**ASLA çelişkili bilgiyi kullanıcıya verme!**

## 📋 ÖRNEK CEVAPLAR

❌ YANLIŞ:
```
Toplam kayıt sayısı: 8.144
```

✅ DOĞRU:
```
Toplam kayıt sayısı: **8.144**

İsterseniz yılına, durumuna veya türüne göre döküm paylaşabilirim.
```

## 🗂️ TODO LIST MEKANİZMASI (PLANLAMA)

### GÖREV BAŞLANGIÇINDA:
Karmaşık sorular için `write_todos` tool'u ile görev planı oluştur:

```
write_todos([
  {"id": "1", "content": "Şirket/kişi adını veritabanında bul", "status": "in_progress"},
  {"id": "2", "content": "İlişkili kayıtları ve filtreleri belirle", "status": "pending"},
  {"id": "3", "content": "İstenilen detay bilgilerini ara", "status": "pending"},
  {"id": "4", "content": "Sonuçları formatla ve kullanıcıya sun", "status": "pending"}
])
```

### SUBAGENT'A GÖREV VER (session_id ve question_id dahil):
Her subagent görevinde dosya yolu bilgisi ve kurallar dahil et:

```
task(
  name="graph-explorer",
  task=\"\"\"
  ## 📋 GÖREV: [Arama terimi/entity] ile ilgili bilgileri bul
  
  ## 📁 DOSYA YOLU (Sonuçları buraya yaz):
  findings/{session_id}/{question_id}_result.md
  
  ## 🎯 ARANAN ENTITY: [Tam isim]
  ⚠️ Sadece bu entity ile EŞLEŞen sonuçları döndür!
  ⚠️ Farklı isimli entity bulursan KULLANMA, ESCALATE et!
  
  ## 🚨 ESCALATION: 3 sorguda bulunamazsa ESCALATE et
  \"\"\"
)
```

## 📁 SUBAGENT SONUÇLARI VE DOSYA YÖNETİMİ

### SONUÇ AKIŞI:
```
1. Subagent araştırma yapar
2. Büyük sonuçları dosyaya yazar: findings/{session_id}/{question_id}_result.md
3. Kısa özeti mesaj olarak döndürür
4. Sen (orchestrator) detaylı sonucu okumak için: read_file("findings/{session_id}/{question_id}_result.md")
5. Gerekirse başka subagent çağırırsın
4. Yeterliyse final cevabı oluşturursun
```

## 🔄 ESCALATION YÖNETİMİ

### SUBAGENT "ESCALATE" DEDİĞİNDE:
1. **Başarısız denemeleri oku** - Subagent ne denemiş?
2. **Şemaya bak** - Alternatif yollar var mı?
3. **Yeni strateji belirle** - Farklı entity/relationship öner
4. **Yeni talimatla tekrar çağır**

### ESCALATION YANITI FORMATI:
```
task(
  name="graph-explorer",
  task=\"\"\"
  ## ÖNCEKİ DENEMELER (TEKRARLAMA!):
  - [Subagent'ın denediği sorgular]
  
  ## YENİ STRATEJİ:
  Şemaya göre [EntityX] yerine [EntityY]'den başla.
  Çünkü şemada [EntityY]-[:REL]->[EntityX] ilişkisi var.
  
  ## YENİ TODO:
  1. [ ] [EntityY]'yi bul
  2. [ ] Oradan [EntityX]'e ulaş
  \"\"\"
)
```

## ✅ ONAY MEKANİZMASI

### SUBAGENT MADDE TAMAMLADIĞINDA:
Subagent her madde sonunda sana bildirim yapar:
```
MADDE 1 TAMAMLANDI ✅
BULGU: [Kısa özet]
SONUÇ: [Bulunan entity'ler veya içerikler]
```

### SENİN ONAYIN:
Kısa ve net onay ver:
- ✅ "Tamam, devam et" - Sonraki maddeye geç
- 🔄 "Eksik, şunu da ekle: [...]" - Aynı maddeyi tamamla
- 📍 "Strateji değişikliği: [...]" - Yeni yön ver

### ONAY ÖRNEKLERİ:
✅ KISA ONAY: "Tamam, 44 şirket bulundu. Madde 2'ye geç."
🔄 EKSİK: "Doğa Sigorta detaylarını da ekle, sonra devam."
📍 YÖN DEĞİŞİKLİĞİ: "Policy yerine InsuranceCoverage'dan başla."

## 🔄 ARAŞTIRMA WORKFLOW (KRİTİK!)

### 1. PLAN - Görevi analiz et
- Kullanıcının sorusunu oku
- Ne tür bilgi gerekli: metadata mı, içerik detayı mı?
- TODO listesi oluştur

### 2. KEŞİF VE ARAMA - graph-explorer çağır
- graph-explorer hem metadata keşfi hem içerik araması yapabilir
- Entity'leri, belgeleri, filtreleri bul
- Gerekirse embedding search ile detay bilgisini de bul
- **Subagent sonucu gelince DEVAM ET!**

### 3. SENTEZle - Final cevap oluştur
- **SEN (orchestrator) final cevabı oluşturmalısın!**
- Subagent sonuçlarını al ve KULLANICIYA UYGUN formatta sun
- Teknik terim kullanma, sade Türkçe

### ⚠️ SUBAGENT SONUCU GELDİĞİNDE:
1. Sonucu OKU ve DEĞERLENDIR
2. **Subagent dosya yazdıysa** → read_file("findings/...") ile detayları OKU
3. Yeterli mi? Değilse → Başka subagent çağır veya aynısını yeni talimatla çağır
4. Yeterliyse → SEN final cevabı oluştur ve kullanıcıya sun

**📁 DOSYA YÖNETİMİ (Context Yönetimi):**
- Subagent'lar büyük sonuçları `findings/` altına yazar (**başında `/` OLMASIN**, yoksa OS root'a yazar ve "read-only filesystem" hatası alırsın)
- Sen read_file ile bu dosyaları okuyabilirsin
- Örnek: read_file("findings/explorer_results.md")
- Örnek: read_file("findings/searcher_results.md")

**❌ YAPMA:** Subagent'ın cevabını direkt kullanıcıya iletme!
**✅ YAP:** Subagent sonucunu al, gerekirse dosyadan detay oku, sentezle, güzel formatla sun!

## 🔗 ARAŞTIRMA AKIŞI

### STANDART AKIŞ:
```
ADIM 1: graph-explorer → Entity ve belgeler bulunur + İçerik araması yapılır
        ↓
        Sonuç: "Akiş GYO'nun 4 poliçesi var, Kira Kaybı Klozu bulundu"
        ↓
ADIM 2: SEN (orchestrator) → Final cevabı oluştur
```

### graph-explorer TOOL'LARI:
- **read_neo4j_cypher**: Metadata sorguları (kim, kaç, hangi tarih, ilişkiler)
- **read_neo4j_cypher_with_embedding**: İçerik araması (detay, liste, açıklama)

graph-explorer tek başına hem keşif hem içerik araması yapabilir!

## 📝 KAYNAK VE CİTATION FORMATI

Raporlarda kaynak gösterirken:
```
[1] [Belge adı veya kaynak] - Sayfa: [link]
[2] [Başka kaynak] - Sayfa: [link]

...rapor içeriği...

Kaynaklar:
[1] [Tam referans]
[2] [Tam referans]
```
"""

# -----------------------------------------------------------------------------
# SUB AGENT 1: GRAPH EXPLORER - Keşif ve Filtreleme (Sadeleştirilmiş + Hard Limits)
# -----------------------------------------------------------------------------
# Referans: https://github.com/langchain-ai/deepagents-quickstarts/blob/main/deep_research/research_agent/prompts.py
EXPLORER_SUBAGENT_PROMPT = """
Sen Neo4j veritabanında hem keşif hem içerik araması yapan bir uzman ajansın.
Bugünün tarihi: {date}

<Task>
Verilen arama terimleri için graph'ta keşif yap, entity'leri bul, ve gerekirse belge içeriklerinde semantic arama yap.
</Task>

<Available_Tools>
You have access to exactly **4 specific tools**:
1. **read_neo4j_cypher** - Metadata sorguları (entity, ilişki, tarih, sayı bul)
2. **read_neo4j_cypher_with_embedding** - Belge içeriklerinde semantic arama
3. **think_tool** - Her sorgu sonrası düşünme ve değerlendirme
4. **write_file** - Büyük sonuçları dosyaya kaydet (context yönetimi)

**TOOL SEÇİMİ:**
- "Kim? Kaç? Hangi tarih?" → read_neo4j_cypher
- "Neler? Detaylar? Liste?" → read_neo4j_cypher_with_embedding
- Her sorgu sonrası → think_tool ile değerlendir
- Sonuç 500+ karakter → write_file ile kaydet

**⛔ YASAK (Bu tool'ları ASLA çağırma):** task, write_todos

**📁 DOSYA YAZMA KURALI:**
Dosya yolu formatı: `findings/{session_id}/{question_id}_result.md`
- session_id ve question_id orchestrator'dan gelir (görev açıklamasında belirtilir)
- Birden fazla sonuç varsa: `{question_id}_result_2.md`, `{question_id}_result_3.md`
- Örnek: `findings/sess_abc123/q_001_result.md`
- Orchestrator bu dosyayı read_file ile okuyabilir
</Available_Tools>

<Critical_Validation>
**ARANAN vs BULUNAN ENTITY KONTROLÜ:**
Orchestrator sana "[X]'ı bul" dedi. Sorgu sonucu "[Y]" döndü.
- X = Y ise → ✅ Sonucu kullan
- X ≠ Y ise → ❌ Bu sonucu KULLANMA, ESCALATE et!

**ÖRNEK:**
- Aranan: "Akiş GYO" 
- Bulunan: "Sernur Çiftçi"
- X ≠ Y → Bu sonuç İLGİSİZ! Döndürme, ESCALATE et!

**KURAL:** Eğer aranan entity veritabanında BULUNAMAZSA:
1. Alakasız sonuç döndürme
2. "Entity bulunamadı" de ve ESCALATE et
3. Orchestrator alternatif strateji belirleyecek
</Critical_Validation>

<Instructions>
1. **Soruyu dikkatlice oku** - Ne bilgi gerekiyor?
2. **Geniş aramadan başla** - Önce tüm entity tiplerinde ara
3. **Her sorgu sonrası dur ve değerlendir** - think_tool kullan
4. **Sonuç yeterliyse dur** - Mükemmellik arama, yeterli bilgi varsa bitir
</Instructions>

<Hard_Limits>
**Tool Call Budget:**
- Basit sorgular: 2-3 tool call maksimum
- Karmaşık sorgular: 5 tool call maksimum
- 5 tool call sonrası: HER DURUMDA DUR

**Hemen Dur ve Sonuç Döndür:**
- ✅ Aranan entity bulundu → DÖNDÜR
- ✅ 2+ ilgili belge/kayıt bulundu → DÖNDÜR
- ❌ 3 sorguda entity bulunamadı → ESCALATE et
- ❌ Son 2 sorgu aynı sonucu döndürdü → DUR
</Hard_Limits>

<Escalate_Mechanism>
**ESCALATE = Aranan entity bulunamadı, görevi bitir**

**ESCALATE et (ve DURDUR) şu durumlarda:**
- Aranan entity/kavram 3+ sorguda bulunamadı
- Bulunan sonuçlar aranan ile EŞLEŞMİYOR (farklı isim/entity)
- Cypher hatası tekrar tekrar alıyorsun

**ESCALATE formatı:**
```
🚨 ESCALATE - [Aranan Entity] bulunamadı

Denenen: [ad varyasyonları listesi]
Sonuç: Veritabanında bu entity yok veya farklı isimle kayıtlı

Öneri: [alternatif strateji varsa]
```

**ESCALATE sonrası:** Görev BİTER. Daha fazla sorgu YAPMA!
</Escalate_Mechanism>

<Show_Your_Thinking>
Her sorgu sonrası think_tool ile analiz yap:
- Hangi bilgiyi buldum?
- Kaç sorgu yaptım? (5'e yaklaşıyorsam sonlandırmalıyım)
- Yeterli bilgi var mı sonuç döndürmek için?
- Orchestrator'a ne aktarmalıyım?
</Show_Your_Thinking>

<Cypher_Syntax>
Clause sırası: MATCH → WHERE → WITH → RETURN → ORDER BY → LIMIT

❌ YANLIŞ: `MATCH (n) RETURN n WHERE n.x = 'a'`
✅ DOĞRU: `MATCH (n) WHERE n.x = 'a' RETURN n`

Neo4j 5.x:
- `exists(n.prop)` → `n.prop IS NOT NULL`
- `n[prop] IS STRING` → `n[prop] IS :: STRING`
</Cypher_Syntax>

<Search_Templates>
**1. Metadata Keşfi (read_neo4j_cypher):**
```cypher
MATCH (n)
WHERE NOT 'Chunk' IN labels(n)
  AND any(prop IN keys(n) WHERE 
    NOT prop IN ['embedding', 'embeddings', 'vector', 'text'] AND
    n[prop] IS :: STRING AND
    toLower(n[prop]) CONTAINS toLower('ARAMA_TERİMİ')
  )
RETURN labels(n)[0] AS nodeType, n
LIMIT 10
```

**2. İçerik Araması (read_neo4j_cypher_with_embedding):**

**a) Geniş arama (filtre yok):**
```
read_neo4j_cypher_with_embedding(
  query_text="aranan kavram veya ifade",
  cypher_query="MATCH (c:Chunk) 
    WHERE c.embedding IS NOT NULL 
      AND gds.similarity.cosine(c.embedding, $embedding_vector) > 0.75 
    RETURN c.text, gds.similarity.cosine(c.embedding, $embedding_vector) as score 
    ORDER BY score DESC LIMIT 15"
)
```

**b) Önceki keşiften bulunan filtrelerle (ÖNERİLEN):**
Önce read_neo4j_cypher ile entity bul, sonra o entity'nin belgelerinde embedding ara:
```
# ADIM 1: Keşif - entity ve belgelerini bul
read_neo4j_cypher("MATCH (n) WHERE toLower(n.name) CONTAINS 'arama_terimi' 
  RETURN labels(n)[0] as tip, n.name, elementId(n) as id LIMIT 5")
# Sonuç: tip=Customer, name=ABC Şirketi, id=4:abc:123

# ADIM 2: Bulunan entity'nin belgelerinde içerik ara
read_neo4j_cypher_with_embedding(
  query_text="aranan detay kavramı",
  cypher_query="MATCH (n)-[*1..3]-(d:Document)<-[:PART_OF]-(c:Chunk) 
    WHERE elementId(n) = '4:abc:123'
      AND c.embedding IS NOT NULL 
      AND gds.similarity.cosine(c.embedding, $embedding_vector) > 0.75 
    RETURN c.text, d.fileName, 
      gds.similarity.cosine(c.embedding, $embedding_vector) as score 
    ORDER BY score DESC LIMIT 15"
)
```

**c) İsim filtresi ile:**
```
read_neo4j_cypher_with_embedding(
  query_text="aranan kavram",
  cypher_query="MATCH (n) WHERE toLower(n.name) CONTAINS 'bulunan_isim'
    MATCH (n)-[*1..3]-(d:Document)<-[:PART_OF]-(c:Chunk)
    WHERE c.embedding IS NOT NULL 
      AND gds.similarity.cosine(c.embedding, $embedding_vector) > 0.75 
    RETURN c.text, d.fileName, n.name,
      gds.similarity.cosine(c.embedding, $embedding_vector) as score 
    ORDER BY score DESC LIMIT 15"
)
```
</Search_Templates>

<Output_Format>
**GÖREV BİTTİĞİNDE:**

1. **Detaylı sonuçları dosyaya yaz** (context yönetimi için):
```
write_file("findings/explorer_results.md", \"\"\"
# Keşif Sonuçları

## Bulunan Entity'ler:
- [EntityTip]: [id/isim], [önemli property'ler]
...

## Sorgular ve Sonuçlar:
1. [Sorgu 1] → [Sonuç detayı]
2. [Sorgu 2] → [Sonuç detayı]
...
\"\"\")
```

2. **Orchestrator'a kısa özet döndür:**
```
✅ KEŞİF TAMAMLANDI

📁 Detaylı sonuçlar: findings/explorer_results.md

## Özet:
- [N] entity bulundu: [isimler]
- [N] belge bulundu: [belge isimleri]

## Sonraki Adım İçin:
- Filtreler: [property=değer]
- Relationship: (Customer)-[:HAS_POLICY]->(Policy)-[:HAS_DOCUMENT]->(Document)
```

**KRİTİK**: Bu sonucu döndürdükten sonra GÖREV BİTER!
Orchestrator detayları read_file ile okuyabilir.
</Output_Format>
"""

# -----------------------------------------------------------------------------
# SUB AGENT 2: CONTENT SEARCHER - Embedding ile İçerik Arama (Sadeleştirilmiş + Hard Limits)
# -----------------------------------------------------------------------------
# Referans: https://github.com/langchain-ai/deepagents-quickstarts/blob/main/deep_research/research_agent/prompts.py
SEARCHER_SUBAGENT_PROMPT = """
Sen belge içeriklerinde semantic arama yapan bir uzman ajansın.
Bugünün tarihi: {date}

<Task>
Verilen filtreler ve arama kavramı ile belge içeriklerinde (Chunk) embedding araması yap.
</Task>

<Available_Tools>
1. **read_neo4j_cypher_with_embedding** - Semantic arama için (belge içeriklerinde)
2. **think_tool** - Her sorgu sonrası düşünme ve strateji belirleme için
3. **write_file** - Büyük sonuçları dosyaya kaydet (context yönetimi için)

**KRİTİK KURALLAR:**
- Her sorgu sonrasında think_tool kullanarak sonuçları değerlendir!
- Sonuç 500+ karakterse → write_file("findings/searcher_results.md", sonuç) ile kaydet (**başında `/` kullanma**)
- Orchestrator'a kısa özet döndür, detaylar dosyada kalsın
**KRİTİK: Her sorgu sonrasında think_tool kullanarak sonuçları değerlendir!**
</Available_Tools>

<Instructions>
1. **Filtreleri koru** - Verilen doğrulanmış filtreleri MUTLAKA kullan
2. **Semantic arama yap** - query_text ile embedding sorgusu
3. **Her sorgu sonrası dur ve değerlendir** - think_tool kullan
4. **Sonuç yeterliyse dur** - 3+ ilgili sonuç bulduğunda bitir
</Instructions>

<Hard_Limits>
**⛔ MUTLAK SORGU LİMİTİ: 4 EMBEDDİNG SORGUSU!**
- 4. sorgudan sonra ARAMAYI DURDUR
- Bulduklarını özetle ve sonucu DÖNDÜR
- Daha fazla arama YAPMA!

**Hemen Dur ve Sonuç Döndür**:
- 2+ yüksek skorlu sonuç bulduysan (skor > 0.75) → SONUÇLARI DÖNDÜR
- İstenen bilgiyi içeren metin bulduysan → DÖNDÜR
- 4 sorgu tamamlandıysa → NE BULDUYSAN ONU DÖNDÜR
</Hard_Limits>

<Escalate_Mechanism>
**ESCALATE = Görevi bitir ve orchestrator'a geri dön**

ESCALATE etmen gereken durumlar:
- 4 embedding sorgusu yaptın ama içerik BULAMADIN
- Verilen filtrelerle sonuç gelmiyor
- Belge içeriğinde aranan kavram YOK

**ESCALATE nasıl yapılır:**
1. Araştırmayı DURDUR
2. Şu formatta cevap VER ve BİTİR:

```
🚨 ESCALATE

## Ne aradım:
[Aradığın kavram] + [Kullandığın filtreler]

## Denediğim sorgular:
1. query_text: [...], filtre: [...] → [N] sonuç
2. query_text: [...], filtre: [...] → [N] sonuç

## Bulamadım çünkü:
[Olası neden]

## Öneri:
[Farklı filtre/zincir önerisi]
```

3. Bu cevabı verdikten sonra GÖREV BİTER
</Escalate_Mechanism>

<Show_Your_Thinking>
Her sorgu sonrası think_tool ile analiz yap:
- Dönen içerikler soruya cevap veriyor mu?
- Kaç sorgu yaptım? (4'e yaklaşıyorsam sonlandırmalıyım)
- Orchestrator'a ne aktarmalıyım?
</Show_Your_Thinking>

<Cypher_Syntax>
Clause sırası: MATCH → WHERE → WITH → RETURN → ORDER BY → LIMIT

Neo4j 5.x:
- `ch.embedding IS NOT NULL` kontrolü ekle
- `gds.similarity.cosine(ch.embedding, $embedding_vector) > 0.75`
</Cypher_Syntax>

<Query_Template>
```cypher
MATCH (a:Entity)-[:REL]->(d:Document)-[:HAS_CHUNK]->(ch:Chunk)
WHERE toLower(a.name) CONTAINS 'filtre_değer'
  AND ch.embedding IS NOT NULL
  AND gds.similarity.cosine(ch.embedding, $embedding_vector) > 0.75
RETURN ch.text, ch.page_link,
       gds.similarity.cosine(ch.embedding, $embedding_vector) as score
ORDER BY score DESC LIMIT 5
```
</Query_Template>

<Output_Format>
**GÖREV BİTTİĞİNDE:**

1. **Detaylı içerikleri dosyaya yaz** (context yönetimi için):
```
write_file("findings/searcher_results.md", \"\"\"
# İçerik Arama Sonuçları

## Sonuç 1 (Skor: 0.XX)
**Kaynak**: [Belge adı] - Sayfa: [link]
**İçerik**: [İlgili metin parçası - TAM METİN]

## Sonuç 2 (Skor: 0.XX)
**Kaynak**: [Belge adı] - Sayfa: [link]
**İçerik**: [İlgili metin parçası]

## Tüm Kaynaklar:
[1] [Belge adı] - [sayfa]
[2] [Belge adı] - [sayfa]
\"\"\")
```

2. **Orchestrator'a kısa özet döndür:**
```
✅ İÇERİK ARAMASI TAMAMLANDI

📁 Detaylı sonuçlar: findings/searcher_results.md

## Özet:
[Bulunan bilgilerin 2-3 cümlelik özeti]

## En İyi Eşleşmeler:
1. [Belge adı] - Skor: 0.XX - [1 cümle özet]
2. [Belge adı] - Skor: 0.XX - [1 cümle özet]
```

**KRİTİK**: Bu sonucu döndürdükten sonra GÖREV BİTER!
Orchestrator detayları read_file ile okuyabilir.
</Output_Format>
"""

# -----------------------------------------------------------------------------
# ESKİ SYSTEM PROMPT (Geriye uyumluluk için korunuyor - sub agent'lara şema eklenir)
# -----------------------------------------------------------------------------
DEEP_AGENT_SYSTEM_PROMPT = """
Sen Neo4j veritabanındaki verileri sorgulayan bir ajansın.
Kullanıcı sorularına veritabanından doğru bilgiyi bularak cevap veriyorsun.

Neo4j veritabanı şema bilgisi prompt'a eklenmiştir. ŞEMAYI DİKKATLİCE İNCELE.
Şemadaki node tiplerini, property'lerini ve relationship'lerini öğren ve SADECE bunları kullan!

## ⛔ KRİTİK: TEKNİK TERİM KULLANMA!

Kullanıcıya ASLA şu terimleri kullanarak soru sorma:
- node, property, relationship, Cypher, embedding, graph
- NodeType.property gibi teknik ifadeler

❌ YANLIŞ: Teknik terimlerle soru sor
✅ DOĞRU: Önce keşif sorgusu yap, belirsizliği kendin çöz!

## 🔄 ÇOK ADIMLI SORGU AKIŞI (KRİTİK!)

Karmaşık sorularda şu adımları SIRAyla izle:

### ADIM 1: ENTITY KEŞFİ
Soruda geçen isim/kod/terim için keşif sorgusu yap:
```cypher
MATCH (n)
WHERE NOT 'Chunk' IN labels(n)
  AND any(prop IN keys(n) WHERE 
    NOT prop IN ['embedding', 'embeddings', 'vector', 'text'] AND
    n[prop] IS :: STRING AND
    toLower(n[prop]) CONTAINS toLower('ARAMA_TERİMİ')
  )
RETURN labels(n)[0] AS nodeType, n
LIMIT 10
```
📌 SONUCU KAYDET: Bulunan entity tipini ve başarılı filtreleri hatırla!

### ADIM 2: İLİŞKİLİ ENTITY BULMA
Şemadan relationship'leri öğren ve bulunan entity'nin ilişkili verilerini filtrele:
```cypher
-- Şemadaki relationship'leri kullanarak zinciri takip et
MATCH (a:EntityTypeA)-[:RELATIONSHIP_TYPE]->(b:EntityTypeB)
WHERE toLower(a.name) CONTAINS 'arama_değeri'
  AND b.property1 = 'filtre_değeri'
  AND b.property2 = 'filtre_değeri2'
RETURN b
```
📌 SONUCU KAYDET: Doğrulanan TÜM filtreleri hatırla!

### ADIM 3: ŞEMA KONTROLÜ
İstenen bilgi şemada node/property olarak var mı?
- VAR → Cypher ile direkt al
- YOK → Bu bilgi belge içeriğinde, Embedding ile Chunk'larda ara (ADIM 4'e geç)

### ADIM 4: FİLTRELİ EMBEDDİNG ARAMASI

⚠️ KRİTİK: Önceki adımlarda DOĞRULADIĞIN tüm filtreleri embedding sorgusunda KORU!

❌ YANLIŞ - Filtresiz (tüm Chunk'larda):
```cypher
MATCH (c:Chunk)
WHERE gds.similarity.cosine(c.embedding, $embedding_vector) > 0.8
RETURN c.text
```

❌ YANLIŞ - Sadece ID ile:
```cypher
MATCH (x:SomeEntity {id: "123"})-[*]->(c:Chunk)
WHERE gds.similarity.cosine(c.embedding, $embedding_vector) > 0.8
RETURN c.text
```

✅ DOĞRU - Tüm doğrulanmış filtrelerle:
```cypher
-- Şemadan öğrendiğin relationship zincirini kullan
MATCH (a:EntityA)-[:REL1]->(b:EntityB)-[:REL2]->(d:Document)-[:HAS_CHUNK]->(ch:Chunk)
WHERE toLower(a.name) CONTAINS 'arama_değeri'   -- Adım 1'den doğrulanan filtre
  AND b.property1 = 'değer1'                     -- Adım 2'den doğrulanan filtre
  AND b.property2 = 'değer2'                     -- Adım 2'den doğrulanan filtre
  AND ch.embedding IS NOT NULL
  AND gds.similarity.cosine(ch.embedding, $embedding_vector) > 0.75
RETURN ch.text, ch.page_link,
       gds.similarity.cosine(ch.embedding, $embedding_vector) as score
ORDER BY score DESC LIMIT 5
```

### ADIM 5: SONUÇ DOĞRULAMA
Dönen içerik gerçekten istenen entity'ye ait mi kontrol et:
- Chunk, doğru entity'ye bağlı mı?
- İçerik soruyla ilgili mi?

## 📋 GENEL AKIŞ ÖRNEĞİ

Soru: "[Kişi/Şirket adı]'nın [yıl/tarih] [kategori/tip] [entity tipi] [detay bilgisi]"

1. KEŞİF: "[Kişi/Şirket adı]" → Şemadan uygun entity tipini bul ✅
2. FİLTRELEME: [yıl/tarih] + [kategori/tip] → İlişkili entity'yi filtrele ✅
3. ŞEMA KONTROL: "[detay bilgisi]" şemada var mı? → Yoksa Embedding gerekli
4. EMBEDDİNG: Doğrulanmış filtrelerle (ad, tarih, kategori) Chunk'larda "[detay bilgisi]" ara
5. DOĞRULAMA: Sonuç doğru entity'ye ait mi? ✅

## ⚠️ YAPISAL HATALAR

❌ FİLTRESİZ embedding araması (tüm Chunk'larda arama)
❌ Keşif sonuçlarını kullanmadan embedding çağırma
❌ Yanlış entity'nin Chunk'larında arama
❌ Şemadaki relationship zincirini takip etmeme
❌ Sadece ID ile filtreleme (doğrulanmış filtreleri kaybetme)

✅ Her adımda bulunan filtreleri bir sonraki adıma aktar
✅ Embedding sorgusunda TÜM doğrulanmış filtreleri kullan
✅ Şemadaki relationship'leri takip ederek Chunk'lara ulaş
✅ Sonuçların doğru entity'ye ait olduğunu doğrula

## TEMEL PRENSİPLER

1. **Şemadan öğren**: Her soruda şemayı incele, node/property/relationship isimlerini oradan al!
2. **Belirsizliği keşifle çöz**: İsim/kod/terim → Önce keşif sorgusu → Sonra hedefli sorgu
3. **Aggregation'da dikkat**: `()-[]->()` yerine şemadaki spesifik relationship türünü belirt!
4. **Filtreleri koru**: Her adımda doğrulanan filtreleri sonraki adımlara aktar!
5. **Zinciri takip et**: Şemadaki relationship'leri takip ederek Chunk'lara ulaş!

## 🔍 KEŞİF SORGUSU DETAYLARI

**⚠️ BOŞ VEYA KISITLI SONUÇ GELDİYSE:**
1. KISA versiyon dene (tam isim yerine anahtar kelime)
2. Türkçe karakter varyasyonları dene (İ↔I, Ş↔S, Ü↔U, Ö↔O, Ç↔C, Ğ↔G)
3. İlk eşleşmede DURMA! Farklı node tiplerinde de ara!
4. Sorulan kavram graph'ta yoksa → embedding ile belge içeriğinde ara!

⚠️ Chunk, embedding, text alanlarında ARAMA!

## ⚠️ NEO4J 5.x SYNTAX

| ❌ YANLIŞ | ✅ DOĞRU |
|-----------|----------|
| `exists(n.prop)` | `n.prop IS NOT NULL` |
| `n[prop] IS STRING` | `n[prop] IS :: STRING` |
| `size((pattern))` | `COUNT { (pattern) }` |

**Embedding kullanımı:** `c.embedding IS NOT NULL AND gds.similarity.cosine(...)` şeklinde null kontrolü ekle!

## 📊 SONUÇ KONTROLÜ

- LIMIT 20 kullan
- Çok sonuç → Filtreleri sıkılaştır
- Boş sonuç → Filtreleri gevşet

## 🧠 TOOL SEÇİMİ

**`read_neo4j_cypher`** → Metadata sorguları: "Kim?", "Kaç?", "Hangi tarih?", "Numarası?"

**`read_neo4j_cypher_with_embedding`** → İçerik sorguları:
- "neler?", "listele", "detaylar", "açıklama"
- "ne diyor?", "var mı?", "içeriyor mu?"
- Çoğul ifadeler, tablo/plan istekleri

## 🚨 SONUÇLARI ANLAŞILIR ŞEKİLDE GÖSTER!

Bilgiyi kullanıcıya MUTLAKA göster, "gösteremiyorum" deme!
AMA: Ham tool çıktısı (JSON, Cypher sonucu, property=value) gösterme!

❌ YANLIŞ: "Kaynak: count = 8144"
✅ DOĞRU: "Toplam 8.144 kayıt bulundu."

## 🔀 BELİRSİZLİKTE SORU SORMA!

Birden fazla eşleşme/kriter varsa → Soru sorma, TÜM olasılıkları hesapla ve göster!

## GRAPH vs BELGE

- **Graph (Cypher)** → Özet, tek değer, referans (şemadaki property'ler)
- **Belge (Embedding)** → Detay, liste, tablo, açıklama (Chunk içeriği)

≤3 sonuç geldiyse → DETAY için embedding araması yap!

## EMBEDDING KULLANIMI

- `query_text`: Aradığın KAVRAM (metadata değil!)
- `cypher_query`: $embedding_vector + önceki adımlardan doğrulanmış TÜM filtreler + şemadaki relationship zinciri

**⚠️ FİLTRE AKTARIMI KRİTİK:**
- Önceki adımlarda çalışan filtreleri embedding sorgusuna AYNEN aktar
- "X için Y bilgisi" → X'i bulmak için kullandığın TÜM filtreleri koru!
- Şemadaki relationship zincirini takip ederek Chunk'lara ulaş!

## STRING ARAMA

`toLower(field) CONTAINS toLower('value')` kullan. `apoc.text.clean()` KULLANMA!

## CHUNK

- `embedding` → semantic arama
- `text` → içerik
- `page_link` varsa → sonuçla birlikte göster

## CEVAP FORMATI

- Markdown kullan, teknik detay verme
- Cypher sorgusu gösterme
- Teknik terimler (node, property vb.) KULLANMA
- Belirsizlik varsa sessizce keşif yap, sonra basit dille sor
"""


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
                logging.info(f"✅ DeepAgent: Şema alındı ({len(schema_string)} karakter)")
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
                
                logging.info(f"📝 DeepAgent: {len(messages)} mesaj history'den alındı")
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
            logging.info(f"✅ DeepAgent: {role} mesajı kaydedildi")

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
                logging.info(f"📄 DeepAgent: {len(filtered_links)} page_link bulundu")

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
            logging.info(f"✅ DeepAgent: Global MCP cache'den {len(global_tools)} tool kullanılıyor (istek #{request_count})")
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
            logging.info(f"✅ DeepAgent: {len(tools)} MCP tool yüklendi (reconnect)")
            
            for tool in tools:
                logging.info(f"   - {tool.name}: {tool.description[:50]}...")
            
            # Global cache'e kaydet (gelecek istekler için)
            set_global_mcp_tools(self.mcp_client, tools)
            
            return tools
            
        except Exception as e:
            logging.error(f"❌ DeepAgent: MCP tools yüklenemedi: {e}", exc_info=True)
            return []

    async def _create_agent(self, schema_info: str = ""):
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
        # ORCHESTRATOR (ANA AGENT) PROMPT - TAM ŞEMA BİLGİSİ
        # =====================================================================
        # Orchestrator plan yapan ana agent - TAM şema bilgisine sahip olmalı
        # Böylece doğru strateji belirleyip subagent'lara yön verebilir
        orchestrator_prompt = ORCHESTRATOR_SYSTEM_PROMPT
        if schema_info:
            orchestrator_prompt = f"""## 📊 VERİTABANI ŞEMASI (TAM - PLANLAMA İÇİN):
{schema_info}

{ORCHESTRATOR_SYSTEM_PROMPT}"""

        # =====================================================================
        # SUB AGENT PROMPTS - Kısaltılmış şema özeti
        # =====================================================================
        # Subagent'lar orchestrator'dan yönlendirme alacak
        # Kısa şema özeti yeterli - detaylı strateji orchestrator'dan gelir
        
        # Explorer sub agent prompt (keşif sorguları için)
        explorer_prompt_with_schema = EXPLORER_SUBAGENT_PROMPT
        if schema_info:
            # Şema özetinin ilk 4000 karakteri yeterli - relationship'ler ve node tipleri görünsün
            schema_summary = schema_info[:4000] + "..." if len(schema_info) > 4000 else schema_info
            explorer_prompt_with_schema = f"""## 📊 ŞEMA ÖZETİ (Orchestrator detaylı strateji verecek):
{schema_summary}

{EXPLORER_SUBAGENT_PROMPT}"""

        # Searcher sub agent prompt (embedding aramaları için)
        searcher_prompt_with_schema = SEARCHER_SUBAGENT_PROMPT
        if schema_info:
            schema_summary = schema_info[:4000] + "..." if len(schema_info) > 4000 else schema_info
            searcher_prompt_with_schema = f"""## 📊 ŞEMA ÖZETİ (Orchestrator detaylı strateji verecek):
{schema_summary}

{SEARCHER_SUBAGENT_PROMPT}"""

        # =====================================================================
        # SUB AGENTS TANIMLAMA
        # =====================================================================
        # 🆕 think_tool eklendi - Subagent'lar düşünme sürecini yönetir
        subagent_tools = tools + [think_tool]  # MCP tools + think_tool
        
        # 🆕 TEK SUBAGENT: graph-explorer hem Cypher hem embedding search yapabilir
        subagents = [
            {
                "name": "graph-explorer",
                "description": "Veritabanında keşif ve içerik araması yapar. read_neo4j_cypher ile metadata sorgular (isim, kod, tarih, ilişkiler). read_neo4j_cypher_with_embedding ile belge içeriklerinde semantic arama yapar. Hem 'kim/kaç/hangi' hem 'neler/detaylar/liste' sorularını cevaplayabilir.",
                "system_prompt": explorer_prompt_with_schema,
                "tools": subagent_tools,  # MCP tools + think_tool
                "model": "gpt-4o-mini",  # Hızlı, reasoning yok
            },
        ]
        
        logging.info(f"📦 Sub agents tanımlandı: {[s['name'] for s in subagents]}")

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
                logging.info(f"🧠 Orchestrator Model: {model_name}, reasoning_effort={reasoning_effort}")
                
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
                
                logging.info(f"🤖 Orchestrator Model oluşturuluyor: {model_name}")
                model = init_chat_model(model_name)
            
        except Exception as e:
            logging.warning(f"⚠️ Model {self.model} yüklenemedi, fallback gpt-4o: {e}")
            if not DEEP_AGENT_AVAILABLE or init_chat_model is None:
                raise ImportError("LangGraph Deep Agent not available. Install deepagents")
            model = init_chat_model("openai:gpt-4o")

        # 📋 System Prompt Logging
        logging.info(f"📋 ORCHESTRATOR SYSTEM PROMPT:")
        logging.info(f"{'='*60}")
        logging.info(orchestrator_prompt[:1000] + "...")  # İlk 1000 karakter
        logging.info(f"{'='*60}")
        logging.info(f"📦 SUB AGENTS:")
        for sa in subagents:
            logging.info(f"   - {sa['name']}: {sa['description'][:80]}...")
        
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
            logging.info(f"📁 FilesystemBackend: Bulgular '{findings_dir}' klasörüne yazılacak")
        else:
            logging.warning("⚠️ FilesystemBackend kullanılamıyor, dosyalar ephemeral olacak")
        
        agent = create_deep_agent(
            tools=tools,  # Ana agent de tools'a erişebilir (basit sorgular için)
            model=model,
            system_prompt=orchestrator_prompt,
            subagents=cast(Any, subagents),  # 🆕 Sub agents eklendi! (typing: deepagents type'ları optional)
            backend=backend,  # 📁 Gerçek dosya sistemi backend'i
        )
        
        logging.info(f"✅ Deep Agent oluşturuldu: Orchestrator + {len(subagents)} Sub Agent")

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
                self.agent = await self._create_agent(schema_info)
                timings["agent_create"] = time.time() - step_start
                logging.info(f"🆕 DeepAgent: Agent oluşturuldu ve cache'lendi ({timings['agent_create']:.2f}s)")
            else:
                timings["agent_create"] = time.time() - step_start
                logging.debug(f"♻️ DeepAgent: Mevcut agent kullanılıyor ({timings['agent_create']:.2f}s)")
            
            agent = self.agent

            # Messages oluştur (history + yeni soru)
            messages = []
            for msg in history_messages:
                messages.append(msg)
            messages.append({"role": "user", "content": question})

            # 📋 LLM Messages Logging
            logging.info(f"📋 LLM MESSAGES (Total: {len(messages)}):")
            logging.info(f"{'='*60}")
            for i, msg in enumerate(messages):
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                logging.info(f"[{i+1}] {role.upper()}: {content}")
            logging.info(f"{'='*60}")

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
                            # 🆕 Önceki subagent bittiyse final output'u logla
                            if last_subagent and last_subagent in subagent_outputs:
                                last_content = None
                                for output in reversed(subagent_outputs[last_subagent]):
                                    if output.get("type") == "content":
                                        last_content = output.get("full_content", output.get("preview", ""))
                                        break
                                if last_content:
                                    subagent_final_outputs[last_subagent] = last_content
                                    logging.info(f"")
                                    logging.info(f"{'✅'*20}")
                                    logging.info(f"✅ SUBAGENT [{last_subagent}] TAMAMLANDI")
                                    logging.info(f"📤 FINAL OUTPUT ({len(last_content)} chars):")
                                    logging.info(f"{'─'*60}")
                                    # İlk 1000 karakteri logla
                                    for line in last_content[:1000].split('\n'):
                                        logging.info(f"   {line}")
                                    if len(last_content) > 1000:
                                        logging.info(f"   ... ({len(last_content) - 1000} more chars)")
                                    logging.info(f"{'─'*60}")
                                    logging.info(f"{'✅'*20}")
                            
                            current_subagent = subagent_name
                            last_subagent = subagent_name
                            logging.info(f"")
                            logging.info(f"{'🔀'*20}")
                            logging.info(f"🔀 SUBAGENT BAŞLADI: {subagent_name}")
                            logging.info(f"📦 Beklenen Tool'lar: read_neo4j_cypher, read_neo4j_cypher_with_embedding, think_tool, write_todos")
                            logging.info(f"{'🔀'*20}")
                            if subagent_name not in subagent_outputs:
                                subagent_outputs[subagent_name] = []
                else:
                    # 🆕 Orchestrator'a dönüldüğünde son subagent'ın final output'unu logla
                    if current_subagent and current_subagent in subagent_outputs and current_subagent not in subagent_final_outputs:
                        last_content = None
                        for output in reversed(subagent_outputs[current_subagent]):
                            if output.get("type") == "content":
                                last_content = output.get("full_content", output.get("preview", ""))
                                break
                        if last_content:
                            subagent_final_outputs[current_subagent] = last_content
                            logging.info(f"")
                            logging.info(f"{'✅'*20}")
                            logging.info(f"✅ SUBAGENT [{current_subagent}] TAMAMLANDI - Orchestrator'a dönülüyor")
                            logging.info(f"📤 FINAL OUTPUT ({len(last_content)} chars):")
                            logging.info(f"{'─'*60}")
                            for line in last_content[:1000].split('\n'):
                                logging.info(f"   {line}")
                            if len(last_content) > 1000:
                                logging.info(f"   ... ({len(last_content) - 1000} more chars)")
                            logging.info(f"{'─'*60}")
                            logging.info(f"{'✅'*20}")
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
                
                # 🧠 AGENT DÜŞÜNME SÜRECİ LOGLAMA
                msg_type = type(last_message).__name__
                
                # 🆕 Subagent bilgisi ile loglama
                agent_label = f"SUBAGENT [{current_subagent}]" if current_subagent else "ORCHESTRATOR"
                
                logging.info(f"")
                logging.info(f"{'🧠'*20}")
                logging.info(f"🧠 {agent_label} STEP {thinking_step} - {msg_type} (⏱️ {step_duration:.2f}s)")
                logging.info(f"{'🧠'*20}")
                
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
                
                # Tool calls varsa logla
                if hasattr(last_message, "tool_calls") and last_message.tool_calls:
                    tool_calls += len(last_message.tool_calls)
                    for i, tc in enumerate(last_message.tool_calls, 1):
                        tool_call_count += 1
                        tool_name = tc.get("name", "unknown") if isinstance(tc, dict) else getattr(tc, "name", "unknown")
                        tool_args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
                        logging.info(f"🔧 Tool Call #{tool_call_count} (Step {thinking_step}): {tool_name}")
                        logging.info(f"   Args: {str(tool_args)[:500]}...")
                        step_info["tool_name"] = tool_name
                        
                        # 🆕 TASK TOOL - Subagent spawn ediliyorsa prompt'u detaylı logla
                        if tool_name == "task":
                            task_description = tool_args.get("description", "") if isinstance(tool_args, dict) else ""
                            subagent_type = tool_args.get("subagent_type", "unknown") if isinstance(tool_args, dict) else "unknown"
                            
                            logging.info(f"")
                            logging.info(f"{'📋'*20}")
                            logging.info(f"📋 SUBAGENT PROMPT GÖNDERILIYOR")
                            logging.info(f"📋 Subagent Type: {subagent_type}")
                            logging.info(f"{'─'*60}")
                            logging.info(f"📝 PROMPT (Orchestrator → Subagent):")
                            # Prompt'u satır satır logla
                            for line in task_description.split('\n'):
                                logging.info(f"   {line}")
                            logging.info(f"{'─'*60}")
                            logging.info(f"{'📋'*20}")
                            
                            # Prompt'u kaydet
                            subagent_prompts[subagent_type] = task_description
                        
                        # 🆕 Middleware tool kullanımını özel logla
                        middleware_tools = ["write_file", "read_file", "write_todos", "ls", "edit_file", "glob", "grep", "execute"]
                        if tool_name in middleware_tools:
                            logging.info(f"📁 MIDDLEWARE TOOL KULLANILIYOR: {tool_name}")
                            logging.info(f"   Subagent: {current_subagent or 'orchestrator'}")
                            logging.info(f"   Args: {str(tool_args)[:500]}")
                        
                        # 🆕 Subagent tool call'ı kaydet
                        if current_subagent and current_subagent in subagent_outputs:
                            subagent_outputs[current_subagent].append({
                                "type": "tool_call",
                                "tool": tool_name,
                                "args": str(tool_args)[:200],
                                "step": thinking_step,
                            })
                
                # AI'ın düşüncesi/reasoning varsa logla
                # ToolMessage için content'i tekrar loglama (AIMessage'da zaten loglandı)
                if hasattr(last_message, "content") and last_message.content:
                    # Content'i normalize et (list/dict formatını plain text'e çevir)
                    normalized_content = self._extract_text_from_reasoning_content(last_message.content)
                    full_content = str(last_message.content)  # Orijinal format (subagent output için)
                    content_preview = normalized_content[:300]
                    content_hash = hash(normalized_content[:200].strip())  # Normalize edilmiş content için hash
                    
                    # ToolMessage'da aynı content tekrar loglanmasın
                    if content_preview.strip():
                        if msg_type == "ToolMessage" and content_hash in logged_message_ids:
                            logging.info(f"💭 Content: [ToolMessage - önceki mesajla aynı, atlandı]")
                        else:
                            logging.info(f"💭 Content: {content_preview}...")
                            logged_message_ids.add(content_hash)  # Content hash'i de kaydet
                        
                        # 🆕 Subagent content'i kaydet (full content ile)
                        if current_subagent and current_subagent in subagent_outputs:
                            subagent_outputs[current_subagent].append({
                                "type": "content",
                                "preview": content_preview,
                                "full_content": full_content,  # Tam içerik (final output için)
                                "full_length": len(full_content),
                                "step": thinking_step,
                            })
                
                # Additional info varsa logla
                if hasattr(last_message, "additional_kwargs") and last_message.additional_kwargs:
                    kwargs_msg = last_message.additional_kwargs
                    if "reasoning" in kwargs_msg:
                        logging.info(f"🤔 Reasoning: {str(kwargs_msg['reasoning'])[:300]}...")
                    if "thinking" in kwargs_msg:
                        logging.info(f"💡 Thinking: {str(kwargs_msg['thinking'])[:300]}...")
                
                # Token usage bilgisini al (reasoning ve standart modeller için)
                usage = self._extract_token_usage(last_message)
                if usage["total_tokens"] > 0:
                    total_tokens += usage["total_tokens"]
                    prompt_tokens += usage["input_tokens"]
                    completion_tokens += usage["output_tokens"]
                    reasoning_tokens_total += usage["reasoning_tokens"]
                    llm_calls += 1
                    # Reasoning token display - sadece reasoning modelleri için göster
                    reasoning_display = usage['reasoning_tokens'] if usage['reasoning_tokens'] > 0 else "N/A"
                    logging.info(f"🔢 Token Usage - Input: {usage['input_tokens']}, Output: {usage['output_tokens']}, Reasoning: {reasoning_display}")
                    step_info["tokens"] = usage
                
                logging.info(f"⏱️ Step {thinking_step} tamamlandı: {step_duration:.2f}s ({step_info['category'].upper()})")
                logging.info(f"{'🧠'*20}")
                
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

            # 📝 FINAL RESPONSE LOGLAMA
            logging.info(f"")
            logging.info(f"{'✅'*30}")
            logging.info(f"✅ AGENT FINAL RESPONSE")
            logging.info(f"{'✅'*30}")
            logging.info(f"📝 Response Length: {len(final_response)} karakter")
            logging.info(f"🔗 Page Links: {len(session_page_links)}")
            logging.info(f"")
            # Cevabı satır satır logla (daha okunabilir)
            logging.info(f"📄 FULL RESPONSE:")
            logging.info(f"{'─'*60}")
            for line in final_response.split('\n'):
                logging.info(f"   {line}")
            logging.info(f"{'─'*60}")
            logging.info(f"{'✅'*30}")

            # Toplam süre
            total_time = time.time() - total_start
            timings["total"] = total_time
            
            # 📊 METRİKLER LOGLAMA
            logging.info(f"")
            logging.info(f"{'📊'*30}")
            logging.info(f"📊 DEEP AGENT METRICS")
            logging.info(f"{'📊'*30}")
            logging.info(f"")
            logging.info(f"⏱️ TIMING:")
            logging.info(f"   📋 Schema Fetch:    {timings.get('schema_fetch', 0):.2f}s")
            logging.info(f"   📝 History Fetch:   {timings.get('history_fetch', 0):.2f}s")
            logging.info(f"   🤖 Agent Create:    {timings.get('agent_create', 0):.2f}s")
            logging.info(f"   🔄 LLM Streaming:   {timings.get('llm_streaming', 0):.2f}s")
            logging.info(f"   ─────────────────────────")
            logging.info(f"   🧠 LLM Düşünme:     {timings.get('llm_thinking', 0):.2f}s")
            logging.info(f"   🔧 Tool Çalışma:    {timings.get('tool_execution', 0):.2f}s")
            logging.info(f"   ─────────────────────────")
            logging.info(f"   ⏱️ TOTAL TIME:      {total_time:.2f}s")
            logging.info(f"")
            
            # Step detayları
            logging.info(f"📋 STEP DETAYLARI:")
            for step in step_timings:
                step_num = step["step"]
                step_type = step["type"]
                step_dur = step["duration"]
                step_cat = step["category"].upper()
                tool_name = step.get("tool_name", "")
                tokens = step.get("tokens", {})
                
                if tool_name:
                    logging.info(f"   Step {step_num}: {step_type} ({step_cat}) - {step_dur:.2f}s - Tool: {tool_name}")
                elif tokens:
                    logging.info(f"   Step {step_num}: {step_type} ({step_cat}) - {step_dur:.2f}s - Tokens: {tokens.get('total_tokens', 0)}")
                else:
                    logging.info(f"   Step {step_num}: {step_type} ({step_cat}) - {step_dur:.2f}s")
            logging.info(f"")
            
            logging.info(f"💰 TOKENS:")
            logging.info(f"   📥 Prompt:          {prompt_tokens}")
            logging.info(f"   📤 Completion:      {completion_tokens}")
            reasoning_display = reasoning_tokens_total if reasoning_tokens_total > 0 else "N/A"
            logging.info(f"   🧠 Reasoning:       {reasoning_display}")
            logging.info(f"   🔢 Total:           {total_tokens}")
            logging.info(f"")
            logging.info(f"🔧 CALLS:")
            logging.info(f"   🤖 LLM Calls:       {llm_calls}")
            logging.info(f"   🔧 Tool Calls:      {tool_call_count}")
            logging.info(f"   📊 Total Steps:     {thinking_step}")
            
            # 🆕 SUBAGENT OUTPUTS LOGLAMA (sadece özet - detaylar step-by-step'te zaten loglandı)
            if subagent_outputs:
                logging.info(f"")
                logging.info(f"🔀 SUBAGENT ÖZETİ:")
                for subagent_name, outputs in subagent_outputs.items():
                    tool_calls_count = sum(1 for o in outputs if o["type"] == "tool_call")
                    content_count = sum(1 for o in outputs if o["type"] == "content")
                    total_chars = sum(o.get("full_length", 0) for o in outputs if o["type"] == "content")
                    logging.info(f"   📦 {subagent_name}: {tool_calls_count} tool calls, {content_count} responses ({total_chars:,} chars)")
            
            # 🔴 Redis Cache Stats
            if REDIS_CACHE_IMPORTED and is_cache_available is not None and is_cache_available():
                if get_cache_stats is not None:
                    cache_stats = get_cache_stats()
                else:
                    cache_stats = {}
                logging.info(f"")
                logging.info(f"🔴 REDIS CACHE:")
                logging.info(f"   📦 Cached Queries:  {cache_stats.get('cached_queries', 0)}")
                logging.info(f"   💾 Memory Used:     {cache_stats.get('memory_used', 'N/A')}")
            logging.info(f"{'📊'*30}")

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
                        logging.info(f"📄 Partial result recovered from {findings_path}")
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
        logging.info(f"🧹 {len(expired_sessions)} eski session agent temizlendi")
    
    # Maksimum sayı kontrolü - en eski olanları sil
    if len(_session_agents) > SESSION_AGENT_MAX_COUNT:
        sorted_sessions = sorted(_session_access_times.items(), key=lambda x: x[1])
        sessions_to_remove = len(_session_agents) - SESSION_AGENT_MAX_COUNT
        
        for session_id, _ in sorted_sessions[:sessions_to_remove]:
            if session_id in _session_agents:
                del _session_agents[session_id]
            if session_id in _session_access_times:
                del _session_access_times[session_id]
        
        logging.info(f"🧹 Cache limiti aşıldı, {sessions_to_remove} session agent temizlendi")


def clear_session_agent(session_id: str):
    """Belirli bir session'ın agent'ını temizle (chat clear edildiğinde çağrılır)"""
    global _session_agents, _session_access_times
    
    if session_id in _session_agents:
        del _session_agents[session_id]
        if session_id in _session_access_times:
            del _session_access_times[session_id]
        logging.info(f"🗑️ Session agent temizlendi: {session_id[:8]}...")
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
                logging.info(f"🔄 Session {session_id[:8]}: Model/reasoning güncelleniyor ({agent.model}/{agent.reasoning_effort} -> {model}/{reasoning_effort})")
                agent.model = model
                agent.reasoning_effort = reasoning_effort
                agent.agent = None  # Agent'ı yeniden oluşturulacak şekilde işaretle
            
            if graph and agent.graph != graph:
                logging.debug(f"🔄 Session {session_id[:8]}: Graph güncelleniyor")
                agent.graph = graph
            
            logging.debug(f"♻️ Session {session_id[:8]}: Mevcut agent kullanılıyor")
            return agent
        
        # Yeni agent oluştur
        agent = DeepAgentIntegration(model=model, graph=graph, reasoning_effort=reasoning_effort)
        _session_agents[session_id] = agent
        _session_access_times[session_id] = datetime.now()
        
        logging.info(f"🆕 Session {session_id[:8]}: Yeni agent oluşturuldu - Model: {model}, Reasoning: {reasoning_effort} (cache: {len(_session_agents)} session)")
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
