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
import logging
import os
import re
from typing import AsyncGenerator, Dict, Any, Optional, List, TYPE_CHECKING
from datetime import datetime
from dataclasses import dataclass, field

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
    
    def add_llm_step(self, step_name: str, input_tokens: int, output_tokens: int, 
                     cached_tokens: int = 0, duration_ms: float = 0, session_id: str = ""):
        """LLM çağrısı istatistiği ekle"""
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
        
        # Log - Session ID ile birlikte
        session_info = f"[Session: {session_id[:8]}]" if session_id else ""
        _log(f"📊 [TOKEN] {session_info} {step_name}")
        _log(f"   ├─ Input: {input_tokens:,} tokens")
        _log(f"   ├─ Output: {output_tokens:,} tokens")
        _log(f"   ├─ Cached: {cached_tokens:,} tokens ({cache_status})")
        _log(f"   └─ Kümülatif: in={self.total_input_tokens:,} out={self.total_output_tokens:,} cached={self.total_cached_tokens:,}")
    
    def add_tool_call(self, tool_name: str, params: Dict[str, Any]):
        """Tool çağrısı ekle"""
        # Parametreleri kısalt (çok uzun olabilir)
        params_preview = {}
        for k, v in params.items():
            if isinstance(v, str) and len(v) > 100:
                params_preview[k] = v[:100] + "..."
            else:
                params_preview[k] = v
        
        step = StepStats(
            step_name=f"tool_{tool_name}",
            step_type="tool_call",
            tool_name=tool_name,
            tool_params=params_preview
        )
        self.steps.append(step)
        self.total_tool_calls += 1
        
        # Log
        _log(f"🔧 [TOOL CALL] {tool_name}")
        for k, v in params_preview.items():
            _log(f"   ├─ {k}: {v}")
    
    def add_tool_result(self, tool_name: str, result: str, success: bool = True):
        """Tool sonucu ekle"""
        # Sonucu kısalt
        result_preview = result[:500] + "..." if len(result) > 500 else result
        
        step = StepStats(
            step_name=f"tool_{tool_name}_result",
            step_type="tool_result",
            tool_name=tool_name,
            tool_result_preview=result_preview
        )
        self.steps.append(step)
        
        # Log
        status = "✅" if success else "❌"
        _log(f"📥 [TOOL RESULT] {status} {tool_name}")
        # Sonucu satır satır göster (max 5 satır)
        lines = result_preview.split('\n')[:5]
        for line in lines:
            if line.strip():
                _log(f"   │ {line[:120]}")
        if len(result_preview.split('\n')) > 5:
            _log(f"   │ ... ({len(result_preview.split(chr(10)))} satır)")
    
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
# CACHE-OPTIMIZED PROMPT - SABİT PREFIX (OpenAI Prompt Caching için)
# ============================================================================

# Bu prefix ~4000-5000 token olmalı ve session boyunca DEĞİŞMEMELİ
# Prompt Caching bu prefix'i cache'leyerek %50 token indirimi sağlar

CACHED_SYSTEM_PREFIX = """# 🎯 DİNKAL SİGORTA NEO4J AGENT

Sen Dinkal Sigorta için Neo4j graph veritabanı sorgulayan bir AI agent'sın.

## ⚠️ ÖNEMLİ: SEN CEVABI BİLMİYORSUN!

Sen kullanıcının sorusunun cevabını **bilmiyorsun**. Cevabı bulmak için:

1. **ŞEMAYI İNCELE** - Aşağıdaki "VERİTABANI ŞEMASI" bölümünü oku
2. **PLANLA** - Cevaba ulaşmak için hangi node'lar ve ilişkiler gerekli?
3. **KEŞİF YAP** - Entity hangi node/nodelar'da?
4. **DOĞRU SORGULA** - Şemadaki ilişkileri TAKİP ederek veriyi bul

⛔ **YAPMA:**
- Şemaya bakmadan sorgu yazma
- İlişki/node adlarını tahmin etme
- Aynı hatayı tekrarlama

✅ **YAP:**
- Her adımda şemayı kontrol et
- Bulamadığında farklı node'larda ara
- Contains text aramalarında sonuç bulamazsan Türkçe/İngilizce switch yapıp arama yap (belgeler İngilizce olabilir!)

---

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

<search_term_rules>
## 🚨 ARAMA TERİMLERİ OLUŞTURURKEN


- **MARKA/ŞİRKET ADININ TAM HALİNİ EKLE:** "XYZ" ← lowercase versiyonu
- Ünvan ek bilgi ile aramana gerek yok.
- **Aranan node isimleri ilk kelime veya ilk iki kelime ili birlikte aramalısın.** Örnek: "ABC Ticaret Gayrimenkul Anonim şirketi" ise -> "ABC" veya "ABC Ticaret"
- Aranan içerik Sokak Lambası ise -> "Sokak Lambası", "Sokak" Asla "Sokak lamb" değil
⛔ **KELİMEYİ BÖLME!**
```
❌ YANLIŞ: "Akenerji" → "Aken" (anlamsız yarım kelime!)
✅ DOĞRU: "Akenerji" → "akenerji" (lowercase tam kelime)

❌ YANLIŞ: "Akiş Gyo" → "gyo" (anlamsız kelime!)
✅ DOĞRU: "Akiş Gyo" → "akis" (lowercase tam kelime)

❌ YANLIŞ: "Microsoft" → "Micro" 
✅ DOĞRU: "Microsoft" → "microsoft"
```

⛔ **AYNI ALANDA ÇOKLU CONTAINS KULLANMA!**
```
❌ WHERE name CONTAINS 'x' AND name CONTAINS 'y'
✅ WHERE name CONTAINS 'x'  (sadece ana/ilk kelime)
```
</search_term_rules>



## 🔄 ReAct DÖNGÜSÜ

Her soru için şu adımları takip et:

### 1️⃣ DÜŞÜN (Thought)
Soruyu analiz et:
- Ne soruluyor? Hangi entity'ler var? 
- **ŞEMADA** bu bilgi nerede? Hangi node'larda aranmalı?
- Metadata mı (sayı, tarih, liste) yoksa içerik mi (belge detayı)?

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
- `execute_cypher_query`: Metadata, keşif, listeleme için
- `execute_embedding_query`: Belge içeriği araması için
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

---

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

⛔ **TÜM VARYASYONLARI KULLAN! (KRİTİK)**
```
ADIM 1: Tool sonuçlarından TÜM doğru varyasyonları listele
   Keşif 1 (NodeA) → ['Var1', 'Var2']
   Keşif 2 (NodeB) → ['Var3', 'Var4', 'Var5']
   
ADIM 2: Alakasız olanları ÇIKAR (farklı entity, yanlış eşleşme)
   
ADIM 3: KALAN TÜM varyasyonları ANA SORGUDA kullan!
   WHERE name IN ['Var1','Var2','Var3','Var4','Var5']
```
❌ YANLIŞ: Sadece bir varyasyonu kullanmak!
✅ DOĞRU: TÜM varyasyonları WHERE...IN ile kullanmak!

### ⚠️ SONUÇ DOĞRULAMA

```
Aranan: "X Y"
Bulunan: "X-Z Y" veya "X Z Y" → FAZLADAN kelime var → TAM EŞLEŞMEDEĞİL!
→ Belge içeriğinde (Chunk) de ara!
```

### 🔍 İÇERİK ARAMASINDA İKİ KAYNAK!

İçerik ararken (konu, terim, detay) → **HEM node'larda HEM Chunk'larda ara!**
```
1. Node'larda ara (yapılandırılmış veri - hızlı)
2. Chunk'larda ara (belge içeriği - detaylı)
   → Önce embedding, sonuç yoksa text fallback
```
⚠️ Node'da bulsan bile Chunk'ta da doğrula!

---

## 🔧 ARAÇLAR

### execute_cypher_query(cypher, step_name)
**NE ZAMAN:** Metadata sorguları, entity keşfi, ilişki takibi, sayısal bilgiler

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

### execute_embedding_query(query_text, cypher_query, step_name)
**NE ZAMAN:** Belge içeriği araması, semantic arama

⚠️ **KRİTİK:** 
- `query_text`: Sadece KONU (örn: "ödeme planı", "teminat detayları")
- `cypher_query`: MUTLAKA `$embedding_vector` + `gds.similarity.cosine > 0.85` içermeli
- MUTLAKA filtrelenmiş sorgu kullan (tüm Chunk'larda arama YASAK!)
- **⚠️ RETURN'de MUTLAKA `page_link` ve `fileName` ekle!** (kaynak için gerekli)

```cypher
-- Chunk araması (KEŞİF'ten bulunan EXACT değerle filtrele!)
MATCH (entity:EntityNode)-[:REL1]->(doc:Document)-[:FIRST_CHUNK|PART_OF*]->(chunk:Chunk)
WHERE apoc.text.clean(entity.name) CONTAINS apoc.text.clean('filtre_terimi')
AND chunk.embedding IS NOT NULL
AND gds.similarity.cosine(chunk.embedding, $embedding_vector) > 0.85
RETURN chunk.text, chunk.page_link, doc.fileName, 
       gds.similarity.cosine(chunk.embedding, $embedding_vector) as score
ORDER BY score DESC LIMIT 10
```

⚠️ **RETURN ZORUNLU ALANLAR:**
- `chunk.text` → İçerik
- `chunk.page_link` → Sayfa görseli için (add_source)
- `doc.fileName` → Belge adı için (add_source)
- `score` → Sıralama için

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
✅ **ÖNCE** add_source çağır, **SONRA** cevabında dosya adını kullan!

### read_finding(step_name, start_record, end_record) - PAGINATION
Sorgu sonuçlarının devamını görmek için:

⚠️ **NE ZAMAN KULLAN?**
- execute_cypher_query veya execute_embedding_query ilk 10 kaydı gösterir
- "Toplam: 150 kayıt, Gösterilen: 0-10" görürsen daha fazlası var demektir
- Doğru cevabın 10. kayıttan sonra olabileceğini düşünüyorsan bu tool'u kullan

```
Örnekler:
read_finding("step_1_search", start_record=10, end_record=20)  → 10-20 arası
read_finding("step_1_search", start_record=20, end_record=50)  → 20-50 arası
```

---

## 📋 CYPHER KURALLARI

### ⚠️ ŞEMA-TABANLI SORGULAMA (EN ÖNEMLİ!)
```
1. Node label'larını ŞEMADAN al → Tahmin ETME!
2. İlişki adlarını ŞEMADAN al → Uydurma!
3. Property isimlerini ŞEMADAN al → Varsayma!
4. İlişki yönlerini ŞEMADAN al → Ters yazma!
```

### String Araması - apoc.text.clean() kullan
```cypher
-- KEŞİF: apoc.text.clean() ile ara
✅ WHERE apoc.text.clean(n.name) CONTAINS apoc.text.clean('terim')

-- ANA SORGU: KEŞİF'ten bulunan EXACT değeri kullan
✅ WHERE n.name = 'Keşifte Bulunan Tam Değer'
```

### İlişki Yönü - ŞEMADAN AYNEN KOPYALA
```cypher
-- Şemada (A)-[:REL]->(B) ise:
✅ MATCH (a:A)-[:REL]->(b:B)
❌ MATCH (b:B)-[:REL]->(a:A)  -- Ters yön ÇALIŞMAZ!
```

### Paralel Sorgular - AYNI TERİM farklı node'larda ise
```
✅ PARALEL: Aynı terim, farklı node'lar
   Tool Call 1: "terim1" → NodeA'da ara
   Tool Call 2: "terim1" → NodeB'de ara

❌ PARALEL DEĞİL: Farklı terimler
   İlk: "terim1" keşfet → tüm doğru sonuçları al
   Sonra: tüm doğru sonuçlar ile akışa devam et
```

### Aggregate Fonksiyonları
| Soru | Fonksiyon |
|------|-----------|
| Toplam | `SUM(n.field)` |
| Ortalama | `AVG(n.field)` |
| Sayı | `COUNT(DISTINCT n)` |

---

## ⚠️ KRİTİK KURALLAR

1. ⛔ **Şemada olmayan node/ilişki/property YAZMA** → ŞEMAYI KONTROL ET!
2. ⛔ **Tüm Chunk'larda arama YASAK** → Her zaman filtrelenmiş sorgu!
3. ⛔ **Kullanıcıdan onay İSTEME** → Veri varsa direkt CEVAPLA
4. ⛔ **Teknik terim kullanıcıya GÖSTERME** → Node, property, Cypher yok!
5. ✅ **Paralel tool çağrıları KULLAN** → Sadece AYNI TERİM farklı node'larda ise!
6. ✅ **Embedding sonuçlarını DOĞRULA** → False positive kontrolü
7. ✅ **KAYNAK EKLE** → Sonuçta fileName/page_link varsa add_source ÇAĞIR!

---

## 🔄 EMBEDDING FALLBACK STRATEJİSİ

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

⚠️ **Yüksek embedding skoru (>0.85) ≠ Doğru sonuç!**

Embedding alan benzerliği yakalar ama kavramsal farklılığı yakalayamaz.

**DOĞRULAMA ADIMLARI:**
1. Dönen `chunk.text` içinde aranan terim GEÇİYOR MU?
2. GEÇMİYORSA → FALSE POSITIVE! Text CONTAINS ile tekrar ara
3. GEÇİYORSA → Doğru sonuç, devam et

**FALSE POSITIVE Örneği:**
```
Arama: "kira kaybı"
Embedding sonucu: "deprem hasarı teminatı" (score: 0.87)
Kontrol: "kira kaybı" chunk.text'te geçiyor mu? → HAYIR
Karar: ❌ FALSE POSITIVE! Text CONTAINS ile "kira kaybı" ara
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

---

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
            step_name: Adım adı (örn: step_1_customer_search)
        
        Returns:
            İlk 10 kayıt + pagination bilgisi. Daha fazla için read_more_results kullan.
        """
        mcp_read = mcp_tool_map.get("read_neo4j_cypher")
        if not mcp_read:
            return '{"error": "MCP read_neo4j_cypher tool not found"}'
        
        try:
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
            
            success = record_count > 0 and not is_error
            
            # Dosyaya TÜM sonucu kaydet (pagination için)
            suffix = "success" if success else "failed"
            file_path = os.path.join(findings_base, f"{step_name}_{suffix}.txt")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(f"<query>\n{cypher}\n</query>\n\n")
                f.write(f"<result>\n{result_str}\n</result>\n")
            
            _log(f"📁 Cypher: {step_name} → {record_count} records")
            _append_to_blackboard(step_name, record_count, success)
            
            # Sonuç döndür - PAGINATION ile ilk N kayıt
            if success:
                # İlk N kaydı göster
                end_idx = min(DEFAULT_RECORDS_PER_PAGE, record_count)
                paginated_result = _format_paginated_result(records, record_count, 0, end_idx, step_name)
                return f"✅ {record_count} kayıt bulundu.\n\n{paginated_result}"
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
        - RETURN'de MUTLAKA chunk.page_link ve doc.fileName ekle! (kaynak için)
        
        Args:
            query_text: Aranacak konu (semantic search için)
            cypher_query: Cypher sorgusu ($embedding_vector + page_link + fileName içermeli)
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
                # Kayıtları parse et
                parsed_records = _parse_records(result_str)
                if len(parsed_records) == 0:
                    # Parse edilemezse eski yönteme fallback
                    parsed_records = records
                
                # İlk N kaydı göster
                end_idx = min(DEFAULT_RECORDS_PER_PAGE, len(parsed_records))
                paginated_result = _format_paginated_result(parsed_records, len(parsed_records), 0, end_idx, step_name)
                
                return f"""✅ {record_count} içerik bulundu.

{paginated_result}

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
            step_name: Adım adı (örn: step_1_customer_search)
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
        
        file_path = os.path.join(findings_base, f"{step_name}_{result_type}.txt")
        
        if not os.path.exists(file_path):
            return f"❌ Dosya bulunamadı: {step_name}_{result_type}.txt"
        
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
    
    return [execute_cypher_query, execute_embedding_query, add_source, read_finding]


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
        
        # Token tracking başlat
        token_tracker = TokenTracker()
        _log(f"\n{'='*60}")
        _log(f"🚀 [REACT] Yeni sorgu: {question[:80]}...")
        _log(f"   Session: {session_id[:8] if session_id else 'N/A'}, Question: {question_id}")
        _log(f"{'='*60}")
        
        # Timing
        total_start = time.time()
        llm_step_count = 0
        
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
            pending_tool_names: Dict[str, str] = {}  # tool_call_id -> tool_name
            
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
                        
                        # AIMessage'dan token kullanımı çıkar
                        if msg_type == "AIMessage":
                            usage_metadata = getattr(message, "usage_metadata", None)
                            response_metadata = getattr(message, "response_metadata", None)
                            
                            input_tokens = 0
                            output_tokens = 0
                            cached_tokens = 0
                            
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
                                token_tracker.add_llm_step(
                                    step_name=f"llm_step_{llm_step_count}",
                                    input_tokens=input_tokens,
                                    output_tokens=output_tokens,
                                    cached_tokens=cached_tokens,
                                    session_id=session_id
                                )
                        
                        # Tool calls
                        if hasattr(message, "tool_calls") and message.tool_calls:
                            for tc in message.tool_calls:
                                tool_call_count += 1
                                tool_name = tc.get("name", "unknown") if isinstance(tc, dict) else getattr(tc, "name", "unknown")
                                tool_args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
                                tool_call_id = tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", "")
                                
                                # Token tracker'a ekle
                                token_tracker.add_tool_call(tool_name, tool_args)
                                
                                # Tool call ID'yi sakla (result için)
                                if tool_call_id:
                                    pending_tool_names[tool_call_id] = tool_name
                                
                                # Kullanıcıya göster
                                thinking_msg = None
                                if tool_name == "execute_cypher_query":
                                    cypher = tool_args.get("cypher", "")[:60]
                                    thinking_msg = f"🔍 Veritabanında arama: {cypher}..."
                                elif tool_name == "execute_embedding_query":
                                    query_text = tool_args.get("query_text", "")
                                    thinking_msg = f"📄 İçerik araması: {query_text[:40]}..."
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
                            tool_call_id = getattr(message, "tool_call_id", "")
                            tool_name = pending_tool_names.get(tool_call_id, "unknown")
                            
                            # Başarı kontrolü
                            success = "✅" in tool_content or "kayıt" in tool_content.lower()
                            
                            # Token tracker'a ekle
                            token_tracker.add_tool_result(tool_name, str(tool_content), success)
                            
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
                # Kaynakları al
                sources = _get_session_sources(question_id)
                
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
                
                # Markdown kaynakları stream et (message_chunk olarak)
                source_markdown = ""
                if sources["documents"]:
                    source_markdown += _generate_file_links_markdown(sources["documents"])
                if sources["pages"]:
                    source_markdown += _generate_page_links_markdown(sources["pages"])
                
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
                    },
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
            
            yield {
                "type": "error",
                "message": f"Bir hata oluştu: {str(e)}",
                "session_id": session_id,
            }
    
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

