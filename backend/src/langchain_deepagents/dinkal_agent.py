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
import urllib.parse
from typing import AsyncGenerator, Dict, Any, Optional, List, Set, TYPE_CHECKING
from datetime import datetime

# Global Schema Cache import
from src.shared.schema_cache import get_cached_schema, get_schema_cache

# Logging ayarları
logging.basicConfig(level=logging.DEBUG)
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

# Config
MCP_MAX_REQUESTS_BEFORE_RESET = 1000  # Bu kadar istekten sonra reset
MCP_MAX_ERRORS_BEFORE_RESET = 5       # Bu kadar hatadan sonra reset
MCP_RESET_INTERVAL_HOURS = 24         # Bu kadar saat sonra reset


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
    """MCP istek sayacını artır"""
    global _mcp_request_count
    _mcp_request_count += 1
    return _mcp_request_count


def increment_mcp_error():
    """MCP hata sayacını artır"""
    global _mcp_error_count
    _mcp_error_count += 1
    return _mcp_error_count


def reset_mcp_error_count():
    """Başarılı istekte hata sayacını sıfırla"""
    global _mcp_error_count
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
# SYSTEM PROMPT
# ============================================================================

DEEP_AGENT_SYSTEM_PROMPT = """
Sen Neo4j veritabanındaki verileri sorgulayan bir ajansın.
Kullanıcı sorularına veritabanından doğru bilgiyi bularak cevap veriyorsun.

Neo4j veritabanı şema bilgisi prompt'a eklenmiştir. ŞEMAYI DİKKATLİCE İNCELE.

## TEMEL PRENSİP: ŞEMADAN ÖĞREN

Şemada node türleri, property'ler ve relationship'ler tanımlı. Soru sorulduğunda:
1. Sorudaki terimlerin şemada hangi NODE TÜRÜ ve PROPERTY'ye karşılık geldiğini bul
2. İlgili node'lara hangi RELATIONSHIP'ler ile ulaşılacağını şemadan öğren
3. Şemada tanımlı path'leri kullanarak sorgu oluştur

❌ Şemada olmayan node, property veya relationship KULLANMA!
❌ Tahmin yapma, varsayımda bulunma!

## 🔍 BELİRSİZ TERİMLER İÇİN KEŞİF SORGUSU

Kullanıcı sorgusunda şemada hangi property'ye karşılık geldiği BELİRSİZ terimler varsa:

Örnek: "D4", "ABC123", "Galata" gibi kodlar/isimler
→ Bu policyNumber mı? fileName mı? type mı? Emin değilsin!

⚠️ ÖNCE KEŞİF SORGUSU YAP:

```cypher
MATCH (n)
WHERE any(prop IN keys(n) WHERE 
  NOT prop IN ['embedding', 'embeddings', 'vector'] AND
  n[prop] IS :: STRING AND
  toLower(n[prop]) CONTAINS toLower('D4')
)
RETURN labels(n)[0] AS nodeType, 
       [p IN keys(n) WHERE NOT p IN ['embedding', 'embeddings']] AS properties, 
       n
LIMIT 5
```

⚠️ DİKKAT: 
- `embedding` gibi array property'leri HARIÇ TUT (toString() ile çevrilemez!)
- Sadece STRING property'lerde ara
- Liste/array property'leri keşif sorgusunda KULLANMA

Bu sorgu sana:
1. Terimin hangi NODE türünde olduğunu
2. Hangi PROPERTY'de geçtiğini
3. Gerçek veriyi gösterir

→ Sonra hedefli sorgu yap!

## 🎯 FİLTRELEME PRENSİBİ

⚠️ KRİTİK: KEŞİF SORGUSUNDAN sonra TÜM kriterleri tek sorguda uygula!

❌ YANLIŞ: Tahmin et, boş sonuç al, tekrar dene
✅ DOĞRU: Önce keşfet, sonra hedefli sorgula

Prensip: Belirsizliği keşifle çöz, sonra hedefe direkt ulaş!

## 📊 SONUÇ SAYISI KONTROLÜ

- İlk sorgu ASLA 20'den fazla sonuç döndürmemeli
- LIMIT 20 kullan
- Çok sonuç gelirse → Filtreleri sıkılaştır
- Sonuç boş gelirse → Filtreleri TEK TEK gevşet

## 🧠 KARAR MANTIĞI: Hangi tool'u ne zaman kullanmalıyım?

Soruyu analiz et ve şu soruları sor:

**SORU TİPİ 1: METADATA SORULARI** → `read_neo4j_cypher`
- Cevap şemadaki bir NODE veya PROPERTY mi?
- "Kim?", "Kaç tane?", "Hangi tarihte?", "Numarası ne?" türünde mi?
- Yapısal, sayılabilir, listelenebilir veri mi?
→ EVET ise: Sadece Cypher yeterli

**SORU TİPİ 2: İÇERİK SORULARI** → `read_neo4j_cypher_with_embedding`

Şu tetikleyicilerden BİRİ varsa → MUTLAKA embedding kullan:

ÇOĞUL/LİSTE İSTEĞİ:
- "neler?", "hangileri?", "listele"
- "taksitler", "ödemeler", "teminatlar" (çoğul)

DETAY/AÇIKLAMA İSTEĞİ:
- "ne diyor?", "açıklaması?", "detayları?"
- "var mı?", "içeriyor mu?", "kapsamında mı?"

TABLO/PLAN İSTEĞİ:
- "tablo", "plan", "madde", "şart", "kloz"

⚠️ Bu tetikleyiciler varsa Cypher sonucu YETMEZ, embedding ZORUNLU!

## 🎯 AKIL YÜRÜTME SÜRECİ

Her soru için şu adımları izle:

1. **Şemayı kontrol et**: Sorudaki kavram şemada node olarak var mı?
   - VAR → Cypher ile direkt al
   - YOK → Bu bilgi belge içeriğinde, embedding gerekli

2. **Bilgi tipi**: 
   - ÖZELLİK (property) mi? → Cypher
   - İÇERİK (content) mi? → Embedding

3. **Cevap nerede?**
   - Graph node'larında → Cypher
   - PDF/belge içinde → Embedding

## ⚠️ KRİTİK KURAL: GRAPH vs BELGE

Graph (Cypher ile) → ÖZET, TEK DEĞER, REFERANS bilgisi tutar
Belge (Embedding ile) → DETAY, LİSTE, TABLO, AÇIKLAMA tutar

### ZORUNLU EMBEDDING DURUMLAR:

Soruda şu kelimeler varsa → MUTLAKA embedding kullan:
- "neler", "listele", "detaylar", "açıklama"
- "tablo", "plan", "madde", "şart"
- "ne yazıyor", "içeriği", "var mı"

### İKİ ADIMLI ZORUNLU STRATEJİ:

Graph'tan TEK/AZ sonuç geldiyse (≤3 satır):
1. Bu muhtemelen ÖZET bilgidir
2. DETAY için embedding araması YAP
3. Bulduğun entity'nin Chunk'larında ara

⛔ TEK SONUÇLA YETİNME! Detay her zaman belgede olabilir.

## EMBEDDING TOOL KULLANIMI

`read_neo4j_cypher_with_embedding` iki parametre alır:
- `query_text`: Aradığın KAVRAM/KONU (isim, tarih gibi metadata KOYMA!)
- `cypher_query`: $embedding_vector kullanan, ALANI DARALTAN Cypher

Kurallar:
- `query_text`: Sorunun KONUSU, aradığın BİLGİ TİPİ
- `cypher_query`: İlgili belgelere/chunk'lara giden path + similarity hesaplama
- MUTLAKA önce entity'yi bul (Policy, Document vs.), sonra o entity'nin chunk'larında ara
- Tüm veritabanında embedding araması YAPMA, her zaman alanı daralt!

## STRING ARAMA

Şemada uygun NODE varsa → Relationship ile o node'a ulaş, text araması yapma!
Node'un PROPERTY'sinde arama gerekiyorsa → toLower(field) CONTAINS toLower('value')

❌ apoc.text.clean() kullanma - yanlış eşleşmelere sebep olur

## SORGU YAPISI (Neo4j 5.x Uyumlu)

- Şemadan relationship'leri kontrol et, sadece şemada olanları kullan
- Gereksiz OPTIONAL MATCH kullanma
- İlişki zorunlu ise MATCH, opsiyonel ise OPTIONAL MATCH
- Önce ana node'u bul, sonra ilişkili node'ları ara

⚠️ Neo4j 5.x ZORUNLU KURALLAR:
- ❌ size((pattern)) KULLANMA - deprecated!
- ✅ COUNT { (pattern) } kullan (pattern sayma için)
- ❌ length(pattern) KULLANMA
- ✅ size(collection) sadece liste uzunluğu için kullan

Örnek:
- ❌ size((n)-[:REL]->()) → Hata verir!
- ✅ COUNT { (n)-[:REL]->() } → Doğru kullanım

## CHUNK ARAMASI

- Chunk node'larında `embedding` alanı semantic arama için kullanılır
- Chunk node'larında `text` alanı içerik bilgisini tutar
- Chunk node'larında `page_link` alanı varsa, sonuçlarla birlikte döndür
- Eksik bilgi varsa, komşu chunk'lara (bir önceki/sonraki) bakarak tamamla

## PAGE_LINK

Chunk sorgularında `page_link` alanı varsa:
- Bu değerleri cevabının sonunda listele
- Her page_link'i ayrı göster

## CEVAP FORMATI

Cevaplarını markdown formatında ver. Teknik detay verme, sadece sonucu göster.
"""


# ============================================================================
# DEEP AGENT INTEGRATION CLASS
# ============================================================================

class DeepAgentIntegration:
    """LangGraph Deep Agent'i chat_bot_stream'e entegre eden sınıf - MCP Tools ile"""

    def __init__(self, model: str = "gpt-5.1", graph=None):
        self.model = model
        self.graph = graph
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
        """Deep Agent oluştur - MCP tools ile"""
        if not DEEP_AGENT_AVAILABLE:
            raise ImportError("deepagents package is not installed")

        # 🔴 Redis Semantic Cache - DeepAgents ile uyumsuzluk nedeniyle geçici olarak devre dışı
        # TODO: LangGraph/DeepAgents SummarizationMiddleware hatası çözülünce aktifleştir
        # Hata: KeyError: 'SummarizationMiddleware.before_model'
        # if REDIS_CACHE_IMPORTED and not is_cache_available():
        #     logging.info("🔄 Redis Semantic Cache kuruluyor...")
        #     cache_ok = setup_semantic_cache()
        #     if cache_ok:
        #         logging.info("✅ Redis Semantic Cache aktif - benzer sorular cache'den gelecek!")
        #     else:
        #         logging.info("ℹ️ Redis cache kullanılamıyor, tüm sorgular LLM'e gidecek")
        logging.debug("ℹ️ Redis Semantic Cache devre dışı (DeepAgents uyumsuzluğu)")

        # MCP tools'ları al
        tools = await self._get_mcp_tools()
        
        if not tools:
            logging.warning("⚠️ DeepAgent: No tools available, agent may have limited functionality")
        
        # System prompt'a schema bilgisini ekle
        full_system_prompt = DEEP_AGENT_SYSTEM_PROMPT
        if schema_info:
            full_system_prompt = f"""## 📊 NEO4J VERİTABANI ŞEMA BİLGİSİ:
{schema_info}

{DEEP_AGENT_SYSTEM_PROMPT}"""

        # Model oluştur - GPT-5 reasoning modellerinde düşünmeyi minimize et
        try:
            model_name = self.model
            
            # GPT-5 / GPT-5.1 reasoning modelleri için özel handling
            if "gpt-5" in model_name.lower():
                from langchain_openai import ChatOpenAI
                from pydantic import SecretStr
                
                api_key = os.environ.get("OPENAI_API_KEY")
                
                # gpt-5.1 için reasoning tamamen kapatılabilir, gpt-5 için minimal
                if "gpt-5.1" in model_name.lower():
                    reasoning_effort = os.environ.get("OPENAI_REASONING_EFFORT", "none")  # Düşünme KAPALI
                else:
                    reasoning_effort = os.environ.get("OPENAI_REASONING_EFFORT", "minimal")  # Minimum düşünme
                
                logging.info(f"🧠 {model_name} reasoning: effort={reasoning_effort}")
                
                # ChatOpenAI will read from environment if api_key is None
                model_kwargs = {
                    "model": model_name,
                    "reasoning": {"effort": reasoning_effort}
                }
                if api_key:
                    model_kwargs["api_key"] = SecretStr(api_key)
                
                model = ChatOpenAI(**model_kwargs)
            else:
                if not DEEP_AGENT_AVAILABLE or init_chat_model is None:
                    raise ImportError("LangGraph Deep Agent not available. Install deepagents")
                model = init_chat_model(model_name)
        except Exception as e:
            logging.warning(f"⚠️ Model {self.model} yüklenemedi, fallback: {e}")
            if not DEEP_AGENT_AVAILABLE or init_chat_model is None:
                raise ImportError("LangGraph Deep Agent not available. Install deepagents")
            model = init_chat_model("openai:gpt-4o")

        # 📋 System Prompt Logging
        logging.info(f"📋 LLM SYSTEM PROMPT:")
        logging.info(f"{'='*60}")
        logging.info(full_system_prompt)
        logging.info(f"{'='*60}")
        
        # Deep Agent oluştur
        if not DEEP_AGENT_AVAILABLE or create_deep_agent is None:
            raise ImportError("LangGraph Deep Agent not available. Install deepagents")
        
        agent = create_deep_agent(
            tools=tools,
            model=model,
            system_prompt=full_system_prompt,
        )

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
            llm_calls = 0
            tool_calls = 0
            
            # LLM streaming timing
            llm_start = time.time()
            
            # 🧠 Agent düşünme süreci için sayaç
            thinking_step = 0
            logged_message_ids = set()  # Daha önce loglanan mesajları takip et
            
            async for chunk in agent.astream(
                {"messages": messages},
                stream_mode="values"
            ):
                if "messages" in chunk and chunk["messages"]:
                    last_message = chunk["messages"][-1]
                    
                    # Mesajın benzersiz ID'sini al (id veya content hash)
                    msg_id = getattr(last_message, "id", None) or hash(str(last_message.content)[:100] if hasattr(last_message, "content") else "")
                    
                    # Daha önce loglandıysa atla
                    if msg_id in logged_message_ids:
                        continue
                    logged_message_ids.add(msg_id)
                    
                    thinking_step += 1
                    
                    # 🧠 AGENT DÜŞÜNME SÜRECİ LOGLAMA
                    msg_type = type(last_message).__name__
                    logging.info(f"")
                    logging.info(f"{'🧠'*20}")
                    logging.info(f"🧠 AGENT STEP {thinking_step} - {msg_type}")
                    logging.info(f"{'🧠'*20}")
                    
                    # Tool calls varsa logla
                    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
                        tool_calls += len(last_message.tool_calls)
                        for tc in last_message.tool_calls:
                            tool_name = tc.get("name", "unknown") if isinstance(tc, dict) else getattr(tc, "name", "unknown")
                            tool_args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
                            logging.info(f"🔧 Tool Call: {tool_name}")
                            logging.info(f"   Args: {str(tool_args)[:500]}...")
                    
                    # AI'ın düşüncesi/reasoning varsa logla
                    if hasattr(last_message, "content") and last_message.content:
                        content_preview = str(last_message.content)[:300]
                        if content_preview.strip():
                            logging.info(f"💭 Content: {content_preview}...")
                    
                    # Additional info varsa logla
                    if hasattr(last_message, "additional_kwargs") and last_message.additional_kwargs:
                        kwargs = last_message.additional_kwargs
                        if "reasoning" in kwargs:
                            logging.info(f"🤔 Reasoning: {str(kwargs['reasoning'])[:300]}...")
                        if "thinking" in kwargs:
                            logging.info(f"💡 Thinking: {str(kwargs['thinking'])[:300]}...")
                    
                    logging.info(f"{'🧠'*20}")
                    
                    # Token usage bilgisini al (eğer varsa)
                    if hasattr(last_message, "response_metadata"):
                        metadata = last_message.response_metadata
                        if "token_usage" in metadata:
                            usage = metadata["token_usage"]
                            total_tokens += usage.get("total_tokens", 0)
                            prompt_tokens += usage.get("prompt_tokens", 0)
                            completion_tokens += usage.get("completion_tokens", 0)
                            llm_calls += 1
                    
                    if hasattr(last_message, "content") and last_message.content:
                        new_content = str(last_message.content)
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
            logging.info(f"   ⏱️ TOTAL TIME:      {total_time:.2f}s")
            logging.info(f"")
            logging.info(f"💰 TOKENS:")
            logging.info(f"   📥 Prompt:          {prompt_tokens}")
            logging.info(f"   📤 Completion:      {completion_tokens}")
            logging.info(f"   🔢 Total:           {total_tokens}")
            logging.info(f"")
            logging.info(f"🔧 CALLS:")
            logging.info(f"   🤖 LLM Calls:       {llm_calls}")
            logging.info(f"   🔧 Tool Calls:      {tool_calls}")
            
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
            yield {
                "type": "complete",
                "message": final_response,
                "status": "finished",
                "session_id": session_id,
                "info": {
                    "agent_type": "langgraph_deep_agent",
                    "model": self.model,
                    "page_links_count": len(session_page_links),
                    "mcp_tools_used": True,
                    "token_usage": {
                        "total_tokens": total_tokens,
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "llm_calls": llm_calls,
                        "tool_calls": tool_calls,
                    },
                    "timings": {
                        "schema_fetch_sec": round(timings.get("schema_fetch", 0), 2),
                        "history_fetch_sec": round(timings.get("history_fetch", 0), 2),
                        "agent_create_sec": round(timings.get("agent_create", 0), 2),
                        "llm_streaming_sec": round(timings.get("llm_streaming", 0), 2),
                        "total_sec": round(total_time, 2),
                    },
                },
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

            yield {
                "type": "error",
                "message": error_message,
                "status": "failed",
                "session_id": session_id,
                "timestamp": datetime.now().isoformat(),
            }


# ============================================================================
# SESSION BASED AGENT CACHE
# ============================================================================

# Session bazlı DeepAgent cache - her session için ayrı agent
_session_agents: Dict[str, DeepAgentIntegration] = {}
_session_access_times: Dict[str, datetime] = {}

# Config
SESSION_AGENT_MAX_AGE_HOURS = 24  # Session agent'ı bu süreden sonra temizle
SESSION_AGENT_MAX_COUNT = 100    # Maksimum cache'deki session sayısı


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
    session_id: str, model: str = "gpt-5.1", graph=None
) -> DeepAgentIntegration:
    """Session bazlı DeepAgent al veya oluştur"""
    global _session_agents, _session_access_times
    
    # Önce eski session'ları temizle
    cleanup_old_session_agents()
    
    # Session için agent var mı?
    if session_id in _session_agents:
        agent = _session_agents[session_id]
        _session_access_times[session_id] = datetime.now()
        
        # Model veya graph değiştiyse güncelle
        if agent.model != model:
            logging.info(f"🔄 Session {session_id[:8]}: Model güncelleniyor ({agent.model} -> {model})")
            agent.model = model
            agent.agent = None  # Agent'ı yeniden oluşturulacak şekilde işaretle
        
        if graph and agent.graph != graph:
            logging.debug(f"🔄 Session {session_id[:8]}: Graph güncelleniyor")
            agent.graph = graph
        
        logging.debug(f"♻️ Session {session_id[:8]}: Mevcut agent kullanılıyor")
        return agent
    
    # Yeni agent oluştur
    agent = DeepAgentIntegration(model=model, graph=graph)
    _session_agents[session_id] = agent
    _session_access_times[session_id] = datetime.now()
    
    logging.info(f"🆕 Session {session_id[:8]}: Yeni agent oluşturuldu - Model: {model} (cache: {len(_session_agents)} session)")
    
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
    model: str = "gpt-5.1",
    session_id: str = "",
    graph=None,
    **kwargs,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    LangGraph Deep Agent kullanarak streaming cevap üret - MCP tools ile
    
    Session bazlı agent cache kullanır - her session için ayrı agent instance

    Args:
        question: Kullanıcının sorusu
        model: Kullanılacak LLM modeli (default: gpt-5.1)
        session_id: Oturum ID'si (conversation history için) - ZORUNLU
        graph: Neo4j graph connection
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
        agent = await get_or_create_session_agent(session_id, model, graph)
        
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
