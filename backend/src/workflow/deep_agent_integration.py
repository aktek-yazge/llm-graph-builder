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
from typing import AsyncGenerator, Dict, Any, Optional, List, Set
from datetime import datetime

# Global Schema Cache import
from src.shared.schema_cache import get_cached_schema, get_schema_cache

# Logging ayarları
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Deep Agent imports
try:
    from deepagents import create_deep_agent
    from langchain.chat_models import init_chat_model
    from langchain_core.messages import HumanMessage, AIMessage
    
    DEEP_AGENT_AVAILABLE = True
    logging.info("✅ LangGraph Deep Agent successfully imported")
except ImportError as e:
    logging.warning(f"⚠️ LangGraph Deep Agent not available: {e}")
    DEEP_AGENT_AVAILABLE = False

# MCP Adapters import
try:
    from langchain_mcp_adapters.client import MultiServerMCPClient
    MCP_ADAPTERS_AVAILABLE = True
    logging.info("✅ LangChain MCP Adapters successfully imported")
except ImportError as e:
    logging.warning(f"⚠️ LangChain MCP Adapters not available: {e}")
    MCP_ADAPTERS_AVAILABLE = False


# ============================================================================
# MCP SERVER CONFIGURATION
# ============================================================================

def get_mcp_server_config() -> Dict[str, Any]:
    """
    MCP server konfigürasyonunu döndürür.
    Environment variables veya default değerler kullanılır.
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
Sen Neo4j veritabanındaki verileri sorgulayan bir ajansın. Veritabanında her şey var. 
Sen Dinkal Sigortaya ait poliçeler hakkında sorulan sorulara cevap veren bir ajansın. 

Neo4j veritabanı şema bilgisi prompt'a eklenmiştir. ŞEMAYI DİKKATLİCE İNCELE ve sorgularını şemaya göre oluştur.

## TEMEL PRENSİP: ŞEMADAN ÖĞREN

Şemada node türleri, property'ler ve relationship'ler tanımlı. Soru sorulduğunda:
1. Sorudaki terimlerin şemada hangi NODE TÜRÜ ve PROPERTY'ye karşılık geldiğini bul
2. İlgili node'lara hangi RELATIONSHIP'ler ile ulaşılacağını şemadan öğren
3. Şemada tanımlı path'leri kullanarak sorgu oluştur

❌ Şemada olmayan node, property veya relationship KULLANMA!
❌ Tahmin yapma, varsayımda bulunma!

## İKİ ADIMLI ARAMA STRATEJİSİ

### ADIM 1: KEŞİF SORGUSU (read_neo4j_cypher)

Amaç: Veri yapısını anla, doğru filtreleri öğren
- Şemadaki node'ları ve relationship'leri kullanarak keşif sorgusu yap
- Sorudaki terimlerin veritabanında nasıl temsil edildiğini öğren

Öncelik sırası (şemaya göre):
1. Şemada soruya uygun NODE türü varsa → O node'u ve relationship'lerini kullan
2. Sonuç boş gelirse → Alternatif yazımları dene (sadece soyisim, farklı case vb.)
3. Hala bulunamazsa → Kullanıcıya sor veya alternatif öner

⛔ METADATA vs İÇERİK AYRIMI:

METADATA SORULARI → Şemadaki node property'lerini kullan, Embedding KULLANMA!
- İsim, numara, tarih, sayı, kod gibi yapısal veriler
- "Kaç tane?", "Kim?", "Hangi?", "Listele" türü sorular
- Boş sonuç gelirse → Alternatif yazım dene, kullanıcıya sor

İÇERİK SORULARI → Embedding KULLAN!
- "Ne yazıyor?", "Açıklaması ne?", "Detayları neler?" türü sorular
- Chunk.text içinde anlamsal arama gerektiren durumlar

### ADIM 2: EMBEDDING ARAMASI (read_neo4j_cypher_with_embedding TOOL'U)

⚠️ SADECE İÇERİK/ANLAM SORULARI İÇİN KULLAN!

`read_neo4j_cypher_with_embedding` tool'u iki parametre alır:
- `query_text`: Aranacak içerik kavramları (zengin terimler)
- `cypher_query`: $embedding_vector parametresi içeren Cypher sorgusu

Ne zaman kullanılır:
- Şemada node olarak temsil EDİLMEYEN detay bilgileri (tablo, plan, açıklama, içerik)
- Entity bulunduktan sonra o entity'ye ait detaylı içerik aranıyorsa
- Chunk'larda semantic/anlamsal arama yapılacaksa

Kullanım kuralları:
- `query_text`: SADECE içerik kavramları, metadata KOYMA (isim, tarih, kod)
- `cypher_query`: Adım 1'den öğrenilen FİLTRELERİ + şemadaki path'leri kullanarak alanı daralt
- `cypher_query` içinde $embedding_vector parametresi ve gds.similarity.cosine() ZORUNLU

⚠️ ÖNEMLİ: Embedding araması yapmadan önce MUTLAKA alanı daralt!
- Adım 1'deki keşif sorgusunda bulduğun node'ları ve relationship'leri embedding sorgusunda da kullan
- Şemadan öğrendiğin path'i Chunk node'una kadar uzat
- Tüm veritabanında embedding araması YAPMA!

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

    def __init__(self, model: str = "claude-sonnet-4-5-20250929", graph=None):
        self.model = model
        self.graph = graph
        self.agent = None
        self.mcp_client = None
        self.mcp_tools = None
        self.collected_page_links: Set[str] = set()
        self.intelligent_agent = None
        
        # IntelligentAgent'ı initialize et (page_link handling için)
        if self.graph:
            try:
                from src.intelligent_agent import IntelligentAgent
                self.intelligent_agent = IntelligentAgent(self.graph)
                logging.info("✅ DeepAgent: IntelligentAgent initialized for page_link handling")
            except Exception as e:
                logging.warning(f"⚠️ DeepAgent: IntelligentAgent initialization failed: {e}")

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
                    if self.intelligent_agent:
                        try:
                            self.intelligent_agent.add_page_resource(original_link)
                        except Exception:
                            pass

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
        """MCP server'dan tools'ları al"""
        if not MCP_ADAPTERS_AVAILABLE:
            logging.warning("⚠️ MCP Adapters not available, no tools loaded")
            return []
        
        try:
            mcp_config = get_mcp_server_config()
            self.mcp_client = MultiServerMCPClient(mcp_config)
            
            # MCP tools'ları al
            tools = await self.mcp_client.get_tools()
            logging.info(f"✅ DeepAgent: {len(tools)} MCP tool yüklendi")
            
            for tool in tools:
                logging.info(f"   - {tool.name}: {tool.description[:50]}...")
            
            return tools
            
        except Exception as e:
            logging.error(f"❌ DeepAgent: MCP tools yüklenemedi: {e}", exc_info=True)
            return []

    async def _create_agent(self, schema_info: str = ""):
        """Deep Agent oluştur - MCP tools ile"""
        if not DEEP_AGENT_AVAILABLE:
            raise ImportError("deepagents package is not installed")

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

        # Model oluştur
        try:
            model = init_chat_model(self.model)
        except Exception as e:
            logging.warning(f"⚠️ Model {self.model} yüklenemedi, fallback: {e}")
            model = init_chat_model("openai:gpt-4o")

        # Deep Agent oluştur
        agent = create_deep_agent(
            tools=tools,
            model=model,
            system_prompt=full_system_prompt,
        )

        return agent

    async def stream_query_response(
        self, question: str, session_id: str = None, **kwargs
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Deep Agent kullanarak streaming cevap üret - MCP tools ile"""
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
            schema_info = self._get_schema_for_session(session_id)

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
            history_messages = self._get_conversation_history(session_id)

            # Kullanıcı sorusunu history'ye kaydet
            self._save_to_history(session_id, "Human", question)

            # Agent oluştur (MCP tools ile)
            agent = await self._create_agent(schema_info)

            # Messages oluştur (history + yeni soru)
            messages = []
            for msg in history_messages:
                messages.append(msg)
            messages.append({"role": "user", "content": question})

            # Agent'ı çalıştır
            yield {
                "type": "status",
                "message": "🔍 Deep Agent araştırma yapıyor...",
                "status": "agent_working",
                "session_id": session_id,
                "timestamp": datetime.now().isoformat(),
            }

            # Streaming ile çalıştır
            response_text = ""
            async for chunk in agent.astream(
                {"messages": messages},
                stream_mode="values"
            ):
                if "messages" in chunk and chunk["messages"]:
                    last_message = chunk["messages"][-1]
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

            # Page link'leri extract et
            if self.intelligent_agent:
                resources = self.intelligent_agent.resource_manager.get_all_resources()
                if resources["total_count"] > 0:
                    for page_resource in resources["pages"]:
                        page_link = page_resource.get("page_link")
                        if page_link:
                            self.collected_page_links.add(page_link)
                    self.intelligent_agent.resource_manager.clear_resources()

            if not self.collected_page_links:
                extracted_links = self._extract_page_links_from_response(response_text)
                if extracted_links:
                    self.collected_page_links.update(extracted_links)

            # Final response
            final_response = response_text
            if self.collected_page_links:
                page_links_markdown = self._generate_page_links_markdown(self.collected_page_links)
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

            # Tamamlanma durumu
            yield {
                "type": "complete",
                "message": final_response,
                "status": "finished",
                "session_id": session_id,
                "info": {
                    "agent_type": "langgraph_deep_agent",
                    "model": self.model,
                    "page_links_count": len(self.collected_page_links),
                    "mcp_tools_used": True,
                },
                "timestamp": datetime.now().isoformat(),
            }

            # Page link'leri temizle
            self.collected_page_links.clear()
            
            # MCP client'ı temizle
            if self.mcp_client:
                try:
                    await self.mcp_client.__aexit__(None, None, None)
                except Exception:
                    pass

        except Exception as e:
            error_message = f"Deep Agent error: {str(e)}"
            logging.error(error_message, exc_info=True)

            yield {
                "type": "error",
                "message": error_message,
                "status": "failed",
                "session_id": session_id,
                "timestamp": datetime.now().isoformat(),
            }


# ============================================================================
# GLOBAL INSTANCE & HELPER FUNCTIONS
# ============================================================================

_global_deep_agent: Optional[DeepAgentIntegration] = None


async def get_or_create_deep_agent(
    model: str = "claude-sonnet-4-5-20250929", graph=None
) -> DeepAgentIntegration:
    """Global Deep Agent instance'ını al veya oluştur"""
    global _global_deep_agent

    if _global_deep_agent is None:
        _global_deep_agent = DeepAgentIntegration(model=model, graph=graph)
        logging.info(f"🆕 DeepAgent: Yeni global instance oluşturuldu - Model: {model}")
    else:
        if _global_deep_agent.model != model:
            logging.info(f"🔄 DeepAgent: Model güncelleniyor ({_global_deep_agent.model} -> {model})")
            _global_deep_agent.model = model
            _global_deep_agent.agent = None

        if graph and _global_deep_agent.graph != graph:
            logging.info(f"🔄 DeepAgent: Graph connection güncelleniyor")
            _global_deep_agent.graph = graph
            if graph:
                try:
                    from src.intelligent_agent import IntelligentAgent
                    _global_deep_agent.intelligent_agent = IntelligentAgent(graph)
                except Exception as e:
                    logging.warning(f"⚠️ DeepAgent: IntelligentAgent initialization failed: {e}")

    return _global_deep_agent


async def stream_deep_agent_response(
    question: str,
    model: str = "claude-sonnet-4-5-20250929",
    session_id: str = None,
    graph=None,
    **kwargs,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    LangGraph Deep Agent kullanarak streaming cevap üret - MCP tools ile

    Args:
        question: Kullanıcının sorusu
        model: Kullanılacak LLM modeli (default: claude-sonnet-4-5-20250929)
        session_id: Oturum ID'si (conversation history için)
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

    try:
        agent = await get_or_create_deep_agent(model, graph)
        
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
