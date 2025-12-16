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


def create_sequential_model(model_name: str):
    """
    Paralel tool çağrılarını devre dışı bırakan model oluşturur.
    
    LangChain dokümantasyonu: model.bind_tools([tools], parallel_tool_calls=False)
    https://python.langchain.com/docs/how_to/tool_calling_parallel/
    
    ChatOpenAI subclass kullanarak bind_tools çağrılarında
    parallel_tool_calls=False parametresini explicit olarak geçiriyoruz.
    """
    try:
        from langchain_openai import ChatOpenAI
        
        # Model adından gerçek model adını çıkar
        # "openai:gpt-4o-mini" -> "gpt-4o-mini"
        actual_model = model_name
        if ":" in model_name:
            actual_model = model_name.split(":", 1)[1]
        
        class SequentialChatOpenAI(ChatOpenAI):
            """
            ChatOpenAI subclass - bind_tools her zaman parallel_tool_calls=False kullanır.
            
            OpenAI'nin ChatOpenAI.bind_tools metodu:
            - parallel_tool_calls parametresini explicit alır
            - None ise kwargs'a eklemez
            - Değer varsa kwargs'a ekler ve super().bind()'a geçirir
            
            Bu subclass her zaman parallel_tool_calls=False geçirir.
            """
            
            def bind_tools(self, tools, **kwargs):
                # CRITICAL: parallel_tool_calls=False olarak explicit geçir
                # Bu ChatOpenAI.bind_tools'un signature'ına uygun
                return super().bind_tools(
                    tools,
                    parallel_tool_calls=False,  # ← Explicit parametre
                    **kwargs
                )
        
        model = SequentialChatOpenAI(model=actual_model)
        print(f"🔧 [create_sequential_model] Model: {actual_model}, SequentialChatOpenAI (parallel_tool_calls=False)")
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
# CUSTOM TOOLS - Agent için özel araçlar
# ============================================================================

# Tool tanımları - sadece import başarılıysa tanımlanır
think_tool = None
write_finding = None
read_finding = None

if LANGCHAIN_AGENT_AVAILABLE and tool is not None:
    @tool
    def _think_tool(reflection: str) -> str:
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
    def _read_finding(session_id: str, step_name: str) -> str:
        """
        Önceki araştırma bulgularını oku.
        
        Args:
            session_id: Oturum ID'si (kısa versiyon)
            step_name: Adım adı (örn: step_1_entity_search)
        
        Returns:
            Dosya içeriği veya hata mesajı
        """
        findings_dir = os.path.join(os.getcwd(), "agent_findings", "findings", session_id)
        file_path = os.path.join(findings_dir, f"{step_name}.md")
        
        if not os.path.exists(file_path):
            return f"Dosya bulunamadı: {file_path}"
        
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        _log(f"📖 Finding read: {file_path}")
        return content

    # Global isimlere ata
    think_tool = _think_tool
    write_finding = _write_finding
    read_finding = _read_finding


# ============================================================================
# SYSTEM PROMPTS - ORCHESTRATOR & SUB AGENTS
# ============================================================================

# -----------------------------------------------------------------------------
# ANA AGENT (ORCHESTRATOR) - Planlama, Koordinasyon, Değerlendirme
# -----------------------------------------------------------------------------
ORCHESTRATOR_SYSTEM_PROMPT = """
Sen kullanıcı sorularını analiz eden, plan yapan ve araştırma koordine eden bir stratejistsin.

## 🎯 SENİN GÖREVLER

1. **Plan yap** - write_todos ile adım adım TODO listesi oluştur
2. **Strateji belirle** - Şemayı analiz et, olasılıkları belirle
3. **Subagent'a görev ver** - spawn_graph_explorer ile araştırma yaptır
4. **Sonucu değerlendir** - read_finding ile oku, TODO'yu güncelle
5. **Final cevap oluştur** - Tüm bulgulardan kullanıcıya cevap ver

## 🔧 TOOL'LAR

1. **write_todos** - Plan oluştur ve güncelle
2. **spawn_graph_explorer** - Subagent'a araştırma görevi ver
3. **read_finding** - Subagent bulgularını oku
4. **think_tool** - Strateji değerlendir

⛔ **SEN HİÇBİR SORGU ÇALIŞTIRMA!** Tüm veritabanı sorguları subagent'a verilir.

## 🔀 TÜM SORGULAR SUBAGENT'A VERİLİR

Subagent'a verilecek görevler:
- Varyasyon araması (KEŞİF)
- Metadata sorguları
- İçerik araması (embedding)
- İlişki takibi
- Sayım ve kontrol sorguları

**⛔ Kendim sorgu çalıştırma!** Subagent başarısız olursa:
- Farklı terimlerle yeni KEŞİF görevi ver
- Farklı node'larla yeni KEŞİF görevi ver
- Asla kendim sorgu yazıp çalıştırma!

## 📋 SUBAGENT'A GÖREV FORMATI

Her görevde **GÖREV TİPİ** belirt! Subagent buna göre araç seçer.

### GÖREV TİPİ: KEŞİF
Varyasyon bulma, entity keşfi, metadata sorgusu

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
spawn_graph_explorer(task_description=\"\"\"
## 🏷️ GÖREV TİPİ: KEŞİF
## 🎯 GÖREV: [Entity]'nin veritabanındaki yazım varyasyonlarını bul

## 🔎 MUHTEMEL NODE'LAR:
- [NodeLabel] (property: name, fullName, title)

## 📝 ARAMA TERİMLERİ:
- "[tam_ifade]"
- "[kısaltmalı_versiyon]"
- "[kök_kelime]"  ← MUTLAKA EKLE!

## 🔧 TEKNİK NOT:
Tüm terimleri TEK SORGUDA OR ile birleştir (her node için):
WHERE toLower(n.name) CONTAINS 'term1' OR toLower(n.name) CONTAINS 'term2' OR ...

## 📁 KAYIT:
- session_id: "[session]"
- step_name: "[step_adı]"
\"\"\")
```

### GÖREV TİPİ: İÇERİK
Chunk'larda semantic arama (embedding)

**ÖNEMLİ:** KEŞİF'ten gelen varyasyonları değerlendir!
- Varyasyonlar aranan entity ile eşleşiyor mu? → EVET ise İÇERİK'e geç
- Eşleşmiyor mu? → Yeni KEŞİF görevi ver

🌍 **ÇOK DİLLİ ARAMA - KRİTİK!**
Belgeler farklı dillerde olabilir! EMBEDDING QUERY'de HER İKİ DİLİ de ver:
- Türkçe terim + İngilizce karşılık (veya tersi)
- Örnek format: "türkçe_terim", "english_equivalent"
Subagent önce embedding dener, 0 sonuç gelirse text CONTAINS ile arar.

**DARALTMA TİPLERİ:**
- **ENTITY**: KEŞİF'te bulunan varyasyonlar + node tipi + ilişki yolu
- **FİLTRE**: Şemadan çıkardığın property/pattern (tarih, tip, vb.)
- **TÜM VERİ**: Daraltma yok, tüm Chunk'lar taranacak (yavaş - bilinçli seç!)

```
spawn_graph_explorer(task_description=\"\"\"
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

## 📝 EMBEDDING QUERY (sadece konu):
- "[aranan konu - Türkçe]"
- "[aranan konu - İngilizce karşılık]"  ← ÖNEMLİ: Belgeler İngilizce olabilir!

## 📁 KAYIT:
- session_id: "[session]"
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
```
spawn_graph_explorer(task_description=\"\"\"
## 🏷️ GÖREV TİPİ: METADATA
## 🎯 GÖREV: [Entity]'nin [ilişkili entity]'lerini listele

## 🔎 MUHTEMEL NODE'LAR ve İLİŞKİLER:
- [NodeA]-[:REL]->[NodeB]

## 📁 KAYIT:
- session_id: "[session]"
- step_name: "[step_adı]"
\"\"\")
```

## 🔄 ÇALIŞMA AKIŞI

```
1. write_todos → Adım adım plan (in_progress, pending, completed)
2. spawn_graph_explorer → Subagent'a olasılıklar + teknikler ver
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

## ⚠️ KRİTİK KURALLAR

1. **KESİN SÖYLEME**: "X node'unda ara" değil, "X veya Y node'larında olabilir"
2. **PROPERTY VER**: Hangi field'larda aranabilir
3. **TEKNİK ÖNERİ VER**: toLower, CONTAINS, embedding
4. **VARYASYON VER**: Türkçe karakter, kısaltma, tam isim
5. **TEK GÖREV**: Her subagent çağrısı TEK iş
6. **DEĞERLENDİR**: Subagent sonucunu oku, TODO'yu güncelle

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

KEŞİF tamamlandığında subagent'ın döndürdüğü varyasyonları değerlendir:

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

## 🚫 YAPMA

❌ Karmaşık sorguları kendin yapma (subagent'a ver)
❌ Kesin node belirtme (olasılık ver)
❌ Subagent sonucunu okumadan ilerle
❌ TODO güncellemeden sonraki adıma geç
❌ Ham veriyi kullanıcıya gösterme

## 📝 FİNAL CEVAP

- Sade, anlaşılır dil
- Teknik detay yok
- Kaynaklar belirtilmiş
- Markdown formatında
"""

# -----------------------------------------------------------------------------
# SUB AGENT: GRAPH EXPLORER - Sorgu Yazıcı ve Uygulayıcı
# -----------------------------------------------------------------------------
EXPLORER_SUBAGENT_PROMPT = """
Sen Neo4j graph veritabanında Cypher sorguları yazan ve çalıştıran bir uzmansın.
Bugünün tarihi: {date}

## 🎯 SENİN GÖREVİN

Orchestrator sana şunları verir:
- **Muhtemel node'lar** ve property'leri
- **Muhtemel ilişkiler**
- **Teknik öneriler** (toLower, CONTAINS, embedding vb.)
- **Arama terimleri/varyasyonları** ← SADECE BUNLARI KULLAN!

Sen:
1. Orchestrator'ın verdiği terimlerle Cypher sorgusu OLUŞTUR
2. Çalıştır
3. Sonucu KAYDET
4. Kısa özet DÖNDÜR

⛔ **KENDİ TERİM TÜRETME!** Orchestrator ne verdiyse onu kullan, kısaltma/parçalama YAPMA!

## 🚨 NE ZAMAN KAYDET?

**BULUNDU** → Hemen `write_finding` ile kaydet!
```
write_finding(..., content="✅ Bulundu: [sonuç özeti]. Denenen: N sorgu.")
```

**TÜM DENEMELER BİTTİ, BULUNAMADI** → Özet kaydet
```
write_finding(..., content="❌ Bulunamadı. Denenen varyasyonlar: [...]. N sorgu yapıldı.")
```

**BOŞ SONUÇ** → Kaydetme, sonraki varyasyonu dene

## 🔧 TOOL'LAR

1. **read_neo4j_cypher** - Metadata/node sorgusu
2. **read_neo4j_cypher_with_embedding** - İçerik/semantic arama
3. **write_finding** - Sonuç bulunduğunda veya tüm denemeler bittiğinde

## 🏷️ GÖREV TİPİNE GÖRE ARAÇ SEÇ!

Orchestrator sana **GÖREV TİPİ** verir. Buna göre araç seç:

### KEŞİF → read_neo4j_cypher
```cypher
MATCH (n:Label) 
WHERE toLower(n.name) CONTAINS 'term1' OR toLower(n.name) CONTAINS 'term2'
RETURN DISTINCT n.name, n.fullName LIMIT 20
```
Amaç: Varyasyonları bul, entity keşfet

⚠️ **KEŞİF'TE TÜM NODE'LARDA ARA!**

Orchestrator sana MUHTEMEL NODE'LAR listesi verir:
```
## 🔎 MUHTEMEL NODE'LAR:
- Customer (property: name, fullName)
- Policyholder (property: name)
- InsuredPerson (property: name)
```

**YAPMAN GEREKEN:**
1. **İLK** node'da ara (örn: Customer)
2. Sonuç BOŞ ise → **İKİNCİ** node'da ara (örn: Policyholder)
3. Sonuç BOŞ ise → **ÜÇÜNCÜ** node'da ara
4. Sonuç BULUNDUYSA → HEMEN KAYDET VE DUR!

**AYNI NODE'DA TEKRAR TEKRAR ARAMA!**
- Her node'u EN FAZLA 1 kez ara (tüm property'leri OR ile birleştir)
- Bulamadıysan sonraki node'a geç
- Aynı sorguyu ASLA tekrarlama!

🚨 **AYNI SORGUYU TEKRARLAMA YASAĞI:**
- Bir sorguyu çalıştırdıysan, AYNI sorguyu tekrar çalıştırma!
- Sonuç geldi mi? → Kaydet ve bir sonraki node'a geç veya DUR
- Aynı sorguyu 2, 3, 4 kez çalıştırma - bu kaynak israfı!

⚠️ **OR İLE YAPILAN GENİŞ SORGU DAR SORGULARI KAPSAR!**
- `WHERE X OR Y OR Z` ile aradıysan:
  - Ayrıca `WHERE X` yapma - zaten dahil!
  - Ayrıca `WHERE Y` yapma - zaten dahil!
- Örnek: `CONTAINS 'abc' OR CONTAINS 'abc company'` yaptıysan
  - Sonra `CONTAINS 'abc'` tek başına YAPMA - gereksiz tekrar!

⛔ **YANLIŞ:**
```
Tool #1: Customer.name'de ara → boş
Tool #2: Customer.fullName'de ara → boş
Tool #3: Customer.name'de yine ara ← YANLIŞ! Tekrar aynı node!
Tool #4: Customer.original_name'de ara
...
Tool #10: Hala Customer'da! Policyholder'a hiç bakmadı! ← FELAKET!
```

✅ **DOĞRU:**
```
Tool #1: Customer.name'de ara → boş
Tool #2: Policyholder.name'de ara → 3 kayıt bulundu!
Tool #3: write_finding(varyasyonlar) ← KAYDET VE DUR!
```

🚨🚨🚨 **KRİTİK: ARAMA TERİMİ KURALLARI** 🚨🚨🚨

⛔⛔⛔ **MUTLAK YASAK: KENDİ TERİM TÜRETME!** ⛔⛔⛔

Orchestrator sana `## 📝 ARAMA TERİMLERİ:` listesi verir.
**SADECE O LİSTEDEKİ TERİMLERİ KULLAN!**

❌ **ASLA YAPMA:**
- Terimi parçalama: "XYZ Holding" → "XYZ" veya "Holding" ayrı ayrı
- Terimi kesme: "Example" → "Exam" veya "Ex"
- 3 karakterden kısa terim kullanma
- Orchestrator'ın VERMEDİĞİ terimleri kendin türetme
- Tam ifadeden kelime çıkarma: "ABC Real Estate Inc" → sadece "real estate" ❌

**Orchestrator verdi:** `"ABC Real Estate"`, `"ABC"`
❌ YASAK: `CONTAINS 'real estate'` tek başına - Orchestrator vermedi!
❌ YASAK: `CONTAINS 'estate'` tek başına - Orchestrator vermedi!
✅ İZİNLİ: `CONTAINS 'abc real estate'` - Orchestrator verdi
✅ İZİNLİ: `CONTAINS 'abc'` - Orchestrator verdi

✅ **İZİN VERİLEN:**
- Orchestrator'ın verdiği TAM terimleri kullan
- Türkçe karakter değişimi: ş→s, ı→i, ğ→g, ü→u, ö→o, ç→c
- Büyük/küçük: toLower() ile normalize

**ÖRNEK:**
Orchestrator verdi: `"ABC Company"`, `"ABC Ltd"`, `"ABC"`

✅ `CONTAINS 'abc company'` - tam terim
✅ `CONTAINS 'abc'` - Orchestrator verdi
❌ `CONTAINS 'ab'` - **YASAK! Kesik, Orchestrator vermedi!**
❌ `CONTAINS 'company'` tek başına - **Orchestrator vermedi!**

⚠️ **MİNİMUM 3 KARAKTER VE ORCHESTRATOR'IN VERDİĞİ TERİM OLMALI!**

### METADATA → read_neo4j_cypher
```cypher
MATCH (a:LabelA)-[:REL]->(b:LabelB)
WHERE a.prop = 'value'
RETURN b.name, b.prop LIMIT 50
```
Amaç: İlişki takibi, listeleme

### İÇERİK → read_neo4j_cypher_with_embedding

Orchestrator sana **DARALTMA** tipi ve detayları verir. Buna göre sorgu yaz:

⚠️ **KRİTİK:** query_text = Sadece aranan KONU (varyasyonlar Cypher filtresinde!)

#### DARALTMA: ENTITY
Orchestrator'dan gelen bilgiler:
- Varyasyonlar (KEŞİF'ten - RAW): ["exact_db_value_1", "exact_db_value_2"]
- Node tipi: Label
- İlişki yolu: Label<-[:REL]-(Node) veya Label-[:REL]->(Node)

⚠️ **İLİŞKİ YÖNÜ KRİTİK!** Orchestrator'ın verdiği ok yönünü AYNEN kullan:
- `A<-[:REL]-(B)` → Cypher: `(a:A)<-[:REL]-(b:B)` 
- `A-[:REL]->(B)` → Cypher: `(a:A)-[:REL]->(b:B)`

⚠️ **VARYASYONLAR RAW!** Yazım hataları dahil, veritabanındaki EXACT değerler!

```cypher
-- query_text: "aranan konu" (varyasyonlar DEĞİL!)
-- İlişki yönünü Orchestrator'dan AYNEN al!
MATCH (n:Label)<-[:REL]-(other)-[:REL2]->(d)-[:PART_OF]->(c:Chunk)
WHERE n.name IN ['exact_db_value_1', 'exact_db_value_2']  -- RAW varyasyonlar
AND c.embedding IS NOT NULL
AND gds.similarity.cosine(c.embedding, $embedding_vector) > 0.75
RETURN c.text, n.name AS source, gds.similarity.cosine(c.embedding, $embedding_vector) AS score
ORDER BY score DESC LIMIT 10
```

#### DARALTMA: FİLTRE
Orchestrator'dan gelen bilgiler:
- Filtre koşulu: property operator value
- İlişki yolu: Node-[:REL]->...-[:PART_OF]->(Chunk)

```cypher
-- query_text: "aranan konu"
MATCH (n:Label)-[:REL]->...-[:PART_OF]->(c:Chunk)
WHERE [Orchestrator'dan gelen filtre koşulu]  -- Örn: n.fileName STARTS WITH '2024'
AND c.embedding IS NOT NULL
AND gds.similarity.cosine(c.embedding, $embedding_vector) > 0.75
RETURN c.text, gds.similarity.cosine(c.embedding, $embedding_vector) AS score
ORDER BY score DESC LIMIT 10
```

#### DARALTMA: TÜM VERİ
```cypher
-- ⚠️ Orchestrator bilinçli olarak "TÜM VERİ" dedi
-- query_text: "aranan konu"
MATCH (c:Chunk)
WHERE c.embedding IS NOT NULL
AND gds.similarity.cosine(c.embedding, $embedding_vector) > 0.75
RETURN c.text, gds.similarity.cosine(c.embedding, $embedding_vector) AS score
ORDER BY score DESC LIMIT 10
```

#### DARALTMA: BELİRTİLMEMİŞ
```
⚠️ İÇERİK görevi ama daraltma tipi yok!
→ write_finding(..., content="⚠️ Daraltma bilgisi eksik") kaydet ve DUR!
```

## 🔄 EMBEDDING BAŞARISIZ → TEXT FALLBACK

⚠️ **Embedding araması 0 sonuç döndürürse:**

1. **Aynı Cypher'da text CONTAINS ile dene:**
```cypher
-- Embedding 0 sonuç döndü, text araması dene
MATCH (n:Label)<-[:REL]-(other)-[:REL2]->(d)-[:PART_OF]->(c:Chunk)
WHERE n.name IN ['exact_db_value_1', 'exact_db_value_2']
AND (toLower(c.text) CONTAINS 'terim_1' 
     OR toLower(c.text) CONTAINS 'terim_2')
RETURN c.text, n.name AS source
LIMIT 10
```

2. **Orchestrator'ın verdiği TÜM terimleri kullan** (tüm dillerdeki karşılıklar)

3. **Sonuç bulursan kaydet**, bulamazsan "Embedding ve text araması başarısız" olarak kaydet

**Akış:**
```
Embedding "[terim]" → 0 sonuç
Text CONTAINS "[terim_1]" OR "[terim_2]" → sonuç bulunabilir!
```

## ⛔ YASAK

❌ **spawn_graph_explorer** - Orchestrator'ın tool'u
❌ **write_todos** - Orchestrator'ın tool'u
❌ **Daraltma belirtilmeden embedding araması** - Orchestrator daraltma tipi vermeli!

## 📋 ÇALIŞMA AKIŞI

⚠️ **EN KRİTİK KURAL: İLK BAŞARILI SONUÇTA HEMEN KAYDET VE DUR!**

```
1. GÖREV TİPİ'ni oku (KEŞİF / METADATA / İÇERİK)
2. Tipe göre araç seç
3. Sorguyu çalıştır
4. ⭐ SONUÇ GELDİĞİNDE:
   
   EĞER sonuç BOŞ DEĞİLSE ([] değil, kayıt var):
   → HEMEN write_finding çağır (RAW değerlerle!)
   → HEMEN özet döndür
   → BAŞKA SORGU YAPMA!
   
   EĞER sonuç BOŞ ise ([]):
   → Sonraki varyasyonu dene
   → Maksimum 3-4 sorgu
   
5. Tüm denemeler bittiyse → write_finding ile "bulunamadı" kaydet
```

### ⛔ YASAK DAVRANIŞLAR

```
❌ Sonuç bulduktan sonra "emin olmak için" başka sorgu yapmak
❌ write_finding çağırmadan yeni sorgulara geçmek  
❌ 4'ten fazla sorgu yapmak (limit: 10 tool call, güvenlik: 4 sorgu)
❌ Limit aşılana kadar beklemek
```

### ✅ DOĞRU ÖRNEK

```
Tool #1: NodeA'da ara → [] (boş)
Tool #2: NodeB'de ara → 4 kayıt bulundu!
Tool #3: write_finding(raw varyasyonlar) ← HEMEN KAYDET!
→ Özet döndür, DUR!
```

### ❌ YANLIŞ ÖRNEK  

```
Tool #1: NodeA'da ara → [] (boş)
Tool #2: NodeB'de ara → 4 kayıt bulundu!
Tool #3: "Emin olmak için" başka arama ← YANLIŞ!
Tool #4: Başka arama ← YANLIŞ!
...
Tool #10: Limit aşıldı, write_finding çağrılmadı! ← FELAKET!
```

## 🔍 SONUÇ DEĞERLENDİRME (KRİTİK!)

**HER sorgu sonucunda bu adımları uygula:**

### 1. Sonuç boş mu?
```
[] veya "0 results" → BOŞ SONUÇ, sonraki varyasyonu dene
```

### 2. Sonuç döndüyse KAYIT SAYISINI SAY
```
(R:0)... → 1 kayıt
(R:0)... (R:1)... (R:2)... → 3 kayıt
(R:0)... (R:1)... (R:2)... (R:3)... → 4 kayıt
```

### 3. Dönen kayıtları ARANAN VARLIK ile KARŞILAŞTIR

Orchestrator'ın görevinde aranan varlık ne?
- Görevde "X" aranıyorsa
- Dönen kayıtlarda "X", "X ile başlayan", "X içeren" veya "X'in tam adı" var mı?

**Semantik Eşleştirme Kuralları:**
- Kısaltmalar genişletilmiş haliyle EŞLEŞİR
- Büyük/küçük harf farklılıkları EŞLEŞİR  
- Ek bilgi içeren kayıtlar EŞLEŞİR (örn: numara + isim)
- Hafif yazım farklılıkları EŞLEŞİR

**Örnek Eşleştirmeler (domain-agnostic):**
| Aranan | Dönen | Eşleşme |
|--------|-------|---------|
| "ABC Şirketi" | "ABC ŞİRKETİ A.Ş." | ✅ EVET |
| "ABC" | "ABC Holding Ltd." | ✅ EVET |
| "XYZ Ltd" | "12345 XYZ Ltd Şirketi" | ✅ EVET (numara + isim) |
| "DEF" | "DEF Anonim Şirketi" | ✅ EVET |
| "GHI Corp" | "Tamamen Farklı İsim" | ❌ HAYIR |

### 4. Eşleşme varsa → HEMEN KAYDET VE DUR!

```
⚠️ ARRAY BOŞ DEĞİLSE (en az 1 kayıt var):
   1. HEMEN write_finding çağır:
      - TÜM raw varyasyonları yaz (veritabanındaki gibi!)
      - Hangi node'da bulunduğunu belirt
   2. Kısa özet döndür
   3. BAŞKA SORGU YAPMA! DUR!

🛑 DURMA KOŞULU:
   Sonuç [] değilse → write_finding → DUR!
   Başka arama için sebep ARAMA!
```

### ⚠️ KRİTİK: RAW VARYASYONLARI AYNEN YAZ!

KEŞİF görevlerinde bulunan varyasyonları **AYNEN** (raw) yaz, temizleme/yorumlama YAPMA!

**Neden?** Sonraki embedding sorgusunda `WHERE n.name IN [...]` kullanılacak.
Veritabanındaki EXACT değer yazılmazsa sorgu 0 sonuç döner!

🚨 **TÜM SORGU SONUÇLARINI DAHİL ET!**
- Her sorguda dönen TÜM kayıtları final response'a ekle
- Numara prefix'li kayıtları ATLAMA: "12345 ABC..." → AYNEN yaz!
- Satır içi boşluklu kayıtları ATLAMA: "ABC\nXYZ..." → AYNEN yaz!
- "Alakasız" diye düşündüklerini de yaz, Orchestrator değerlendirir

```
❌ YANLIŞ (temizlenmiş/yorumlanmış):
   "ABC Şirketi A.Ş."  ← Kendin düzeltme yaptın
   
❌ YANLIŞ (eksik - bazı kayıtları atladın):
   Tool #2'de 4 kayıt döndü ama sadece 2'sini yazdın!

✅ DOĞRU (raw - veritabanındaki AYNEN):
   - "ABC Şirket i A.Ş." (yazım hatası/boşluk VAR - AYNEN yaz!)
   - "12345 ABC Şirket i A.Ş." (numara prefix VAR - AYNEN yaz!)
   - "ABC Şirketi A.Ş." (temiz versiyon da VAR - AYNEN yaz!)
   - "ABC\nŞirketi" (satır içi boşluk VAR - AYNEN yaz!)
```

**write_finding formatı (KEŞİF):**
```markdown
✅ Bulundu: [N] varyasyon

## RAW Varyasyonlar (AYNEN kopyala):
| n.name (Veritabanındaki değer) | Node Tipi |
|--------------------------------|-----------|
| [EXACT raw value 1]            | [Label]   |
| [EXACT raw value 2]            | [Label]   |

Notlar:
- Hariç tutulanlar: [homonim/alakasız kayıtlar]
```

### 5. Toplam Sorgu Sayısını DOĞRU BİLDİR

```
Yaptığın sorgu sayısını say:
- read_neo4j_cypher çağrısı = 1 sorgu
- 3 paralel çağrı = 3 sorgu
- 18 tool çağrısı = 18 sorgu (3 değil!)
```

### ⚠️ HATALI DEĞERLENDİRME ÖRNEKLERİ

```
❌ YANLIŞ: Sonuçta 4 kayıt var ama "1 kayıt bulundu" demek
❌ YANLIŞ: 18 sorgu yaptın ama "3 sorgu denendi" demek  
❌ YANLIŞ: "ABC Şirketi A.Ş." bulundu ama "ABC bulunamadı" demek
❌ YANLIŞ: İlk 3 sorgu boş dönünce sonrakileri görmezden gelmek

✅ DOĞRU: Tüm sorguları say
✅ DOĞRU: Tüm kayıtları say
✅ DOĞRU: Semantik eşleştirme yap
✅ DOĞRU: Eşleşen TÜM varyasyonları listele
```

## 📝 CYPHER YAZARKEN

Orchestrator'dan gelen teknik önerileri kullan:
- toLower() + CONTAINS önerildiyse → `WHERE toLower(n.prop) CONTAINS 'term'`
- Varyasyonlar verildiyse → OR ile birleştir
- İlişki verildiyse → MATCH pattern'ı kur

```cypher
-- elementId kullan (id() değil):
RETURN elementId(n) AS id, n.name, n.title

-- Birden fazla property:
WHERE toLower(n.name) CONTAINS 'x' OR toLower(n.title) CONTAINS 'x'

-- Birden fazla varyasyon:
WHERE toLower(n.name) CONTAINS 'term1' OR toLower(n.name) CONTAINS 'term2'

-- İlişki takibi:
MATCH (a:NodeA)-[:REL]->(b:NodeB) WHERE a.prop = 'X' RETURN b
```

## ❌ HATA ALDIĞINDA

Tool sonucunda hata mesajı görürsen:
1. **Hata mesajını OKU** - Neo4j hatanın nedenini söyler
2. **Sorguyu DÜZELT** - Hataya göre sorguyu değiştir
3. **TEKRAR DENE** - Düzeltilmiş sorguyu çalıştır

### Sık Yapılan Hatalar:

```cypher
-- ❌ YANLIŞ: İki RETURN kullanma
RETURN x, y, (p)-[:REL]->(n) RETURN n.name

-- ✅ DOĞRU: WITH ile ayır, tek RETURN
WITH p MATCH (p)-[:REL]->(n) RETURN n.name

-- ❌ YANLIŞ: RETURN içinde yeni değişken tanımlama
RETURN (p)-[:REL]->(ic:InsuranceCompany)

-- ✅ DOĞRU: Önce MATCH, sonra RETURN
MATCH (p)-[:REL]->(ic:InsuranceCompany) RETURN ic.name

-- ❌ YANLIŞ: Clause sırası yanlış
MATCH ... RETURN ... WHERE ...

-- ✅ DOĞRU: Clause sırası
MATCH ... WHERE ... WITH ... RETURN ... ORDER BY ... LIMIT
```

⚠️ Hata sayısı 3'ü geçerse → DUR ve "Sorgu hatası" olarak kaydet

## ⏱️ LİMİTLER

- **3 sorgu** maksimum
- Aynı sorguyu tekrarlama
- 3 sorguda bulamazsan → Özet kaydet ve DUR

## 📁 KAYIT FORMATI

**Başarılı:**
```
write_finding(
    session_id="[orchestrator'dan gelen]",
    step_name="[orchestrator'dan gelen]", 
    content="✅ Bulundu: [N kayıt]. [Kısa özet]. Denenen: M sorgu."
)
```

**Başarısız (tüm denemeler bitti):**
```
write_finding(
    session_id="[orchestrator'dan gelen]",
    step_name="[orchestrator'dan gelen]", 
    content="❌ Bulunamadı. Denenen: [varyasyon1, varyasyon2, ...]. M sorgu yapıldı."
)
```

## 📤 ÖZET

**Bulundu:** `✅ Bulundu. N kayıt. [Kısa bilgi]`
**Bulunamadı:** `❌ Bulunamadı. Denenen: [node'lar ve varyasyonlar]`
"""


# Eski SEARCHER_SUBAGENT_PROMPT kaldırıldı - artık tek subagent kullanıyoruz

# Eski DEEP_AGENT_SYSTEM_PROMPT kaldırıldı - artık ORCHESTRATOR_SYSTEM_PROMPT kullanılıyor


# ============================================================================
# LANGCHAIN AGENT INTEGRATION CLASS
# ============================================================================

class LangChainAgentIntegration:
    """LangChain create_agent ile chat_bot_stream'e entegre eden sınıf - MCP Tools + Middleware"""

    def __init__(self, model: str = "gpt-4o", graph=None, reasoning_effort: str = "none"):
        self.model = model
        self.graph = graph
        self.reasoning_effort = reasoning_effort  # none, low, medium, high
        self.agent = None
        self.subagent = None  # Graph explorer subagent
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
        # Tüm sorgular subagent'a delege edilir.
        all_tools = []  # MCP tools Orchestrator'a VERİLMEZ!
        
        if think_tool is not None:
            all_tools.append(think_tool)
        if write_finding is not None:
            all_tools.append(write_finding)
        if read_finding is not None:
            all_tools.append(read_finding)
        
        _log(f"Orchestrator Tools: {len(all_tools)} coordination tools (NO MCP!)")

        # =====================================================================
        # SUBAGENT - Graph Explorer (ayrı bir agent graph olarak)
        # =====================================================================
        # Subagent'ı lazy olarak oluşturuyoruz - orchestrator'ın spawn_subagent tool'u ile çağrılacak
        self.subagent = await self._create_subagent(tools, short_session)

        # =====================================================================
        # SPAWN SUBAGENT TOOL - Orchestrator'ın subagent çağırması için
        # =====================================================================
        if tool is None:
            raise ImportError("LangChain tool decorator not available")
        
        @tool
        async def spawn_graph_explorer(task_description: str) -> str:
            """
            Graph Explorer subagent'ını çağır.
            
            Args:
                task_description: Subagent'a verilecek görev açıklaması
            
            Returns:
                Subagent'ın çalışma sonucu
            """
            if self.subagent is None:
                return "❌ Subagent mevcut değil"
            
            # Task zaten Orchestrator log'unda gösteriliyor, tekrar loglama
            
            try:
                # Subagent'ı streaming ile çalıştır - gerçek zamanlı loglama için
                subagent_tool_count = 0
                final_response = ""
                
                async for event in self.subagent.astream_events(
                    {"messages": [{"role": "user", "content": task_description}]},
                    version="v2"
                ):
                    kind = event.get("event", "")
                    
                    # Tool çağrısı başladığında logla
                    if kind == "on_tool_start":
                        subagent_tool_count += 1
                        tool_name = event.get("name", "?")
                        tool_input = event.get("data", {}).get("input", {})
                        
                        _log(f"[SUBAGENT] Tool #{subagent_tool_count}: {tool_name}")
                        
                        if tool_name == "read_neo4j_cypher":
                            query = tool_input.get("query", "")
                            _log(f"   📝 CYPHER:\n{query}")
                        elif tool_name == "read_neo4j_cypher_with_embedding":
                            query_text = tool_input.get('query_text', '')
                            cypher = tool_input.get('cypher_query', '')
                            _log(f"   🔎 SEARCH TEXT: {query_text}")
                            _log(f"   📝 CYPHER:\n{cypher}")
                        else:
                            _log(f"   Args: {tool_input}")
                    
                    # Tool sonucu geldiğinde logla
                    elif kind == "on_tool_end":
                        tool_name = event.get("name", "?")
                        output = event.get("data", {}).get("output", "")
                        if isinstance(output, str):
                            _log(f"[SUBAGENT_RESULT] {tool_name}:\n{output}")
                        else:
                            _log(f"[SUBAGENT_RESULT] {tool_name}: {output}")
                    
                    # Agent son yanıtı - çeşitli event isimlerini kontrol et
                    elif kind == "on_chain_end":
                        event_name = event.get("name", "")
                        output = event.get("data", {}).get("output", {})
                        
                        # messages içeren output'u yakala
                        if isinstance(output, dict) and "messages" in output:
                            messages = output["messages"]
                            if messages:
                                last_msg = messages[-1]
                                if hasattr(last_msg, "content") and last_msg.content:
                                    candidate = self._extract_text_from_reasoning_content(last_msg.content)
                                    if candidate and len(candidate) > len(final_response):
                                        final_response = candidate
                                        _log(f"[SUBAGENT] Final response captured from {event_name} ({len(final_response)} chars)")
                
                _log(f"← SUBAGENT summary: {subagent_tool_count} tool calls")
                
                if final_response:
                    _log(f"← graph-explorer completed ({len(final_response)} chars)")
                    _log(f"   📤 FULL RESPONSE:\n{final_response}")
                    return final_response
                
                return "Subagent sonuç döndürmedi"
                
            except Exception as e:
                logging.error(f"Subagent error: {e}", exc_info=True)
                return f"❌ Subagent hatası: {str(e)}"
        
        all_tools.append(spawn_graph_explorer)
        _log("Tool: spawn_graph_explorer added")

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

    async def _create_subagent(self, mcp_tools: List, session_id: str):
        """Graph Explorer Subagent oluştur - MCP tools ile"""
        if not LANGCHAIN_AGENT_AVAILABLE or create_agent is None:
            return None
        
        # Subagent için model - daha hızlı ve ucuz
        if init_chat_model is None:
            return None
            
        subagent_model_name = os.environ.get("SUBAGENT_MODEL", "gpt-4o-mini")
        if not subagent_model_name.startswith("openai:") and "gpt" in subagent_model_name.lower():
            subagent_model_name = f"openai:{subagent_model_name}"
        
        # Paralel tool çağrısını KAPAT - subagent sıralı çalışsın
        # Böylece her sorgu sonucunu değerlendirebilir ve "ilk başarılıda dur" kuralını uygulayabilir
        # 
        # LangChain dokümantasyonu: model.bind_tools([tools], parallel_tool_calls=False)
        # https://docs.langchain.com/oss/python/langchain/models#parallel-tool-calls
        subagent_model = create_sequential_model(subagent_model_name)
        
        # Subagent system prompt
        today = datetime.now().strftime("%Y-%m-%d")
        subagent_prompt = EXPLORER_SUBAGENT_PROMPT.format(date=today)
        
        # Subagent tools - sadece MCP tools + write_finding + think_tool
        subagent_tools = list(mcp_tools)
        if think_tool is not None:
            subagent_tools.append(think_tool)
        if write_finding is not None:
            subagent_tools.append(write_finding)
        if read_finding is not None:
            subagent_tools.append(read_finding)
        
        # Subagent middleware - minimal
        subagent_middleware = []
        
        # ModelCallLimitMiddleware - subagent için daha düşük limit
        if ModelCallLimitMiddleware is not None:
            subagent_middleware.append(ModelCallLimitMiddleware(run_limit=10))
        
        subagent = create_agent(
            model=subagent_model,  # type: ignore[arg-type]
            tools=subagent_tools,
            system_prompt=subagent_prompt,
            middleware=subagent_middleware,
            name="graph-explorer",
        )
        
        _log(f"Subagent ready: graph-explorer (model={subagent_model_name})")
        return subagent

    async def stream_query_response(
        self, question: str, session_id: str = "", **kwargs
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """LangChain Agent kullanarak streaming cevap üret - Middleware + MCP tools ile"""
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
                                elif tool_name == "spawn_graph_explorer":
                                    task = tool_args.get("task_description", "")
                                    _log(f"   📋 FULL TASK:\n{task}")
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
                "agent_type": "langchain_create_agent",
                "model": self.model,
                "reasoning_effort": self.reasoning_effort,
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
    session_id: str, model: str = "gpt-5", graph=None, reasoning_effort: str = "none"
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
                agent.subagent = None  # Subagent'ı da yeniden oluştur
            
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
    model: str = "gpt-5-mini",
    session_id: str = "",
    graph=None,
    reasoning_effort: str = "none",
    **kwargs,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    LangChain Agent kullanarak streaming cevap üret - Middleware + MCP tools ile
    
    Session bazlı agent cache kullanır - her session için ayrı agent instance

    Args:
        question: Kullanıcının sorusu
        model: Kullanılacak LLM modeli (default: gpt-5-mini)
        session_id: Oturum ID'si (conversation history için) - ZORUNLU
        graph: Neo4j graph connection
        reasoning_effort: GPT-5 modelleri için reasoning seviyesi (none, low, medium, high) - default: none
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
            question=question, session_id=session_id, **kwargs
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
