"""
FastAgent Integration Module for Chat Bot Stream - With Conversation History

Bu modül FastAgent'i chat_bot_stream endpoint'ine entegre eder.
Session ID'ye göre conversation history özelliği ile birlikte.
"""

import asyncio
import json
import logging
import os
import re
import urllib.parse
from typing import AsyncGenerator, Dict, Any, Optional, List, Set
from datetime import datetime

# Logging ayarları
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# FastAgent import'u yapalım
try:
    from fast_agent import FastAgent, RequestParams, Prompt

    FAST_AGENT_AVAILABLE = True
    logging.info("FastAgent successfully imported")
except ImportError as e:
    logging.warning(f"FastAgent not available: {e}")
    FAST_AGENT_AVAILABLE = False

    # Dummy classes for graceful fallback
    class FastAgent:
        def __init__(self, name):
            pass

    class RequestParams:
        def __init__(self, **kwargs):
            pass

    class Prompt:
        @staticmethod
        def user(text):
            return text


def create_fast_agent_app(model: str = "gpt-5-mini.low") -> FastAgent:
    """gds_fetcher.py ile aynı basit yaklaşımla FastAgent uygulaması oluştur"""

    app = FastAgent("Neo4j Query Agent")

    @app.agent(
        "neo4j_intelligence",  # Daha anlamlı isim
        instruction="""
        Sen Dinkal Sigortaya ait poliçeler hakkında sorulan sorulara cevap veren bir ajansın. 
        
        Neo4j veritabanı şema bilgisi prompt'a eklenmiştir. Bu şema bilgisini kullanarak tool çağrıları yap.
        
        **STRING NORMALİZASYON**: String karşılaştırmalarında mutlaka kullan: toLower(apoc.text.clean(field)) CONTAINS toLower(apoc.text.clean('value'))
       
        Şema bilgisine göre tool çağrıları yaparak sonuca ulaşmaya çalış.
        
        Şema dışına çıkma sorgularında.
        
        Şemada olmayan alanları kullanamazsın. Alanlar hakkında tahminleme yapamazsın. 

        Sorudan çıkarım yaparak field tahminlemesi yapme. Db veri yapısını öğrenmek için soruyu tek kelimeli parçalara bölerek her seferinde bir odak kelimeyi aratarak limitli sorgular ile anlamaya çalış.
        
        Genel query aramaları yapmaktan kaçın.

        Doğru sorguyu yapabilmek için limitli(1-5) sorgular atarak örnek datalara bakman herzaman daha iyidir.
        
        Mesela diyelimki 5 tane kayıt buldun ve içinde soruyu cevaplayan kayıt yok ama sana örnek kayıtlara gözatma imkanı sunduğu için tahmin yürüterek sonuçlara ulaşmaya çalışabilirsin. Bunlara keşif sorguları diyebiliriz.

        Elde ettiğin keşif sorguları cevap bulunamadı manasına gelmez. Bunlar sadece tablo veri yapısını anlamanı sağlar.

        Keşif sorguları yaparken özne ve nesneye odaklanarak tekil kelimeler ile arama yapmalısın.
        

        **ARAMA STRATEJİSİ (KRİTİK - ŞEMADAN ÖĞREN)**:
        
        **TEMEL PRENSİP**: Şemadan öğren! Prompt'ta kod örneği yok, şemadan node türlerini ve relationship'leri öğrenerek kendi sorgunu oluştur!
        
        **ENTITY SORULARI için relationship-based arama (ÖNCELİKLİ)**: 
        - Soruda geçen terimlerin şemada hangi node türlerine karşılık geldiğini kontrol et!
        - Şemadan ilgili node türlerini ve relationship'leri bul!
        - Şemada node türü ve relationship varsa, direkt relationship üzerinden sorgula!
        - Chunk'larda text araması YAPMA! Entity'ler relationship üzerinden sorgulanır!
        - Şemadan öğrendiğin node türleri ve relationship'leri kullanarak sorgunu oluştur!
        
        **CONTENT SORULARI için APOC ile text araması (SADECE İÇERİK TERİMLERİ)**: 
        - Soruda "plan", "tablo", "detay", "açıklama", "tutar" gibi içerik terimleri varsa VE şemada bu terimlere karşılık gelen node türü YOKSA
        - Chunk node'larında text alanında APOC ile clean contains araması yap
        - STRING NORMALİZASYON kullan: toLower(apoc.text.clean(field)) CONTAINS toLower(apoc.text.clean('value'))
        
        **ÖNEMLİ KURALLAR**:
        1. **ŞEMADAN ÖĞREN (EN ÖNEMLİSİ)**: 
           - Sorudaki terimlerin şemada hangi node türlerine karşılık geldiğini ÖNCE kontrol et!
           - Şemada node türü varsa MUTLAKA relationship üzerinden sorgula!
           - Şemada node türü varsa Chunk'larda text araması YAPMA!
           - Şemada node türü yoksa ve içerik terimleri varsa Chunk'larda text araması yapabilirsin!
        2. **ÖNCELİK SIRASI**: 
           - ÖNCE şemadan entity node'larını ve relationship'leri kontrol et!
           - Entity varsa relationship üzerinden sorgula, Chunk'larda arama yapma!
           - Entity yoksa ve içerik terimleri varsa Chunk'larda APOC text araması yap!
        3. **YANLIŞ YAKLAŞIM - KESİNLİKLE YAPMA**: 
           - Şemada node türü varsa Chunk'larda text araması yapmak YANLIŞ!
           - Örnek: Şemada Endorsement node'u varsa, "zeyilname" veya "endorsement" kelimelerini Chunk'larda aramak YANLIŞ!
           - Şemadan relationship'leri öğren ve direkt relationship üzerinden sorgula!
        4. **KOD ÖRNEĞİ YOK**: 
           - Prompt'ta kod örneği yok! Şemadan öğrenerek kendi sorgunu oluştur!
           - Akıl yürüt ve şemadan öğrendiklerini kullan!
        
        **PAGE_LINK EKLEME (KRİTİK)**: 
        - Cypher query sonuçlarında Chunk node'ları bulduğunda ve bu chunk'larda `page_link` alanı varsa
        - Cypher query sonuçlarında `page_link` alanı görürsen, bu değerleri cevabının sonunda listele
        - Her bulduğun page_link'i ayrı ayrı listele
        
        **KRİTİK KURAL**: Şemada node türü varsa relationship üzerinden sorgula! Chunk'larda text araması yapma!
        - Şemada Endorsement node'u varsa, "zeyilname" veya "endorsement" kelimelerini Chunk'larda aramak YANLIŞ!
        - Şemada Policy node'u varsa, "poliçe" kelimesini Chunk'larda aramak YANLIŞ!
        - Şemada Customer node'u varsa, "müşteri" kelimesini Chunk'larda aramak YANLIŞ!
        - İçerik bilgileri (plan, tablo, detay, açıklama vb.) şemada node türü olmayan bilgilerdir ve Chunk nodelarında text alanında saklıdır. SADECE bu tür içerik soruları için (**STRING NORMALİZASYON**) ile Chunk'larda text araması yap!

        Eğer Chunk araması yaptıysan ve chunklarda kesik veya eksik bilgi olabilir. Bir sonraki 2 chunka bakarak bu bilgiyi tamamlamaya çalış.

        **RELATIONSHIP-BASED ARAMALAR (DOMAIN AGNOSTIC - KRİTİK)**:
        
        **GENEL PRENSİP**: İki node arasındaki ilişkiyi sorgularken, önce şemadan relationship'leri kontrol et! Şemada hangi relationship'ler varsa sadece onları kullan!
        
        **ŞEMA KONTROLÜ (KRİTİK)**: 
        - Herhangi bir node türü arasında ilişki sorgularken, ÖNCE şemadan relationship'leri kontrol et!
        - Şemada hangi relationship'ler varsa sadece onları kullan!
        - Şemada olmayan relationship'leri KULLANMA!
        - Şemada birden fazla relationship varsa, hepsini OPTIONAL MATCH ile kontrol edebilirsin
        - Şemada sadece bir relationship varsa, sadece onu kullan!
        
        **SORGU YAPISI (DOMAIN AGNOSTIC)**:
        - İlişki sorgularında gereksiz OPTIONAL MATCH kullanma!
        - Önce ana node'u bul (MATCH), sonra ilişkili node'ları ara (MATCH veya OPTIONAL MATCH)
        - Eğer ilişki zorunlu ise MATCH kullan, opsiyonel ise OPTIONAL MATCH kullan
        - Çok fazla OPTIONAL MATCH kullanmak sorguyu yavaşlatır ve gereksiz karmaşık hale getirir!
        
        **GENEL YAKLAŞIM (ŞEMADAN ÖĞREN)**:
        - Şemadan node türlerini ve relationship'leri kontrol et
        - Sorudaki terim hangi node türüne karşılık geliyor?
        - O node türüne nasıl ulaşılır? (relationship'ler)
        - Şemada hangi relationship'ler varsa onları kullan!
        - Akıl yürüt ve şemadan öğrendiklerini kullanarak sorgunu oluştur!
        
        **ÖNEMLİ KURALLAR (DOMAIN AGNOSTIC)**:
        1. **ŞEMA KONTROLÜ (EN ÖNEMLİSİ)**: 
           - Herhangi bir node türü arasında ilişki sorgularken, ÖNCE şemadan relationship'leri kontrol et!
           - Şemada hangi relationship'ler varsa sadece onları kullan!
           - Şemada olmayan relationship'leri KULLANMA!
           - Şemada birden fazla relationship varsa, hepsini kontrol edebilirsin ama gereksiz OPTIONAL MATCH kullanma!
        2. **SORGU YAPISI**: 
           - Gereksiz OPTIONAL MATCH kullanma! Sorguyu gereksiz karmaşık hale getirir!
           - İlişki zorunlu ise MATCH kullan, opsiyonel ise OPTIONAL MATCH kullan
           - Önce ana node'u bul (MATCH), sonra ilişkili node'ları ara
           - Çok fazla OPTIONAL MATCH zinciri sorguyu yavaşlatır!
        3. **İLK SORGU HATASI**: İlk sorguda bulduğun node ID'leri yanlış olabilir veya veritabanında olmayabilir! Bu ID'leri kullanarak ilişki araması yapma!
        4. **DOĞRU YAKLAŞIM**: İlişki araması yaparken MUTLAKA ana node'dan başla! ID'lere güvenme!
        5. **SORGU SONUCU KONTROLÜ**: 
           - Eğer sorgu sonucunda ilişkili node bulunamazsa, başka path'ler olabilir!
           - Tüm olası path'leri kontrol et!
           - Eğer node ID değeri veritabanında yoksa, bu yanlış bir ID'dir! Ana node'dan tekrar sorgula!
        6. **DOMAIN AGNOSTIC YAKLAŞIM**: 
           - Spesifik domain bilgisi olmadan, şemadan öğrenerek sorgu yap!
           - Şemada hangi relationship'ler varsa onları kullan!
           - Şemada olmayan relationship'leri tahmin etme!

        **CEVAP FORMATI (KRİTİK)**:
        - Verdiğin son cevabı mutlaka markdown formatında düzenle!
        - Başlıklar için `##` veya `###` kullan
        - Liste için `-` veya `*` kullan
        - Önemli bilgileri **kalın** veya *italik* yap
        - Tablo varsa markdown table formatında göster
        - Kod veya teknik terimler için `backtick` kullan
        - Cevabı düzenli, okunabilir ve profesyonel bir şekilde formatla!
        - Teknik bilgilerden bahsetme, sadece son kullanıcıya yönelik sade ve anlaşılır cevaplar ver!
        """,
        servers=["neo4j-database", "embedding"],
        request_params=RequestParams(
            max_iterations=15,
        ),
        use_history=True,  # Test: multi-tool calls için gerekli mi?
        model=model,
    )
    async def neo4j_intelligence():
        """Neo4j Intelligence Agent - placeholder function"""
        pass

    # Chain kaldırıldı - Basic Agent pattern kullanılıyor
    # Artık direkt agent.neo4j_intelligence.send() kullanacağız

    return app


class FastAgentIntegration:
    """FastAgent'i chat_bot_stream'e entegre eden sınıf - Conversation History ile"""

    def __init__(self, model: str = "gpt-5-mini.low", graph=None):
        self.model = model
        self.fast_agent_app = None
        self.graph = graph  # Neo4j graph connection for persistent history
        self.collected_page_links: Set[str] = set()  # Toplanan page_link'ler
        self.intelligent_agent = (
            None  # IntelligentAgent instance for page_link handling
        )
        # Session bazlı şema cache'i (session ID'ye göre cache'lenecek)
        self.schema_cache: Dict[str, str] = {}  # session_id -> schema_string

        # IntelligentAgent'ı initialize et (graph varsa)
        if self.graph:
            try:
                from src.intelligent_agent import IntelligentAgent

                self.intelligent_agent = IntelligentAgent(self.graph)
                logging.info(
                    "✅ FastAgent: IntelligentAgent initialized for page_link handling"
                )
            except Exception as e:
                logging.warning(
                    f"⚠️ FastAgent: IntelligentAgent initialization failed: {e}"
                )

    def _get_schema_for_session(self, session_id: str) -> str:
        """
        Session ID'ye göre şema bilgisini al veya cache'den döndür
        Aynı session için şema cache'den alınacak (graph instance değişse bile)
        """
        if not session_id:
            return ""

        # Graph yoksa şema alınamaz
        if not self.graph:
            logging.warning(f"⚠️ FastAgent: Graph yok, şema bilgisi alınamadı")
            return ""

        # Cache durumunu kontrol et (session ID'ye göre)
        cache_exists = session_id in self.schema_cache
        cache_size = len(self.schema_cache)

        logging.info(
            f"📋 FastAgent _get_schema_for_session: Session {session_id}, Cache'de var mı: {cache_exists}, Toplam cache: {cache_size}"
        )

        # Cache'de varsa döndür
        if cache_exists:
            logging.info(
                f"✅ FastAgent: Session {session_id} için şema cache'den alındı ({len(self.schema_cache[session_id])} karakter)"
            )
            return self.schema_cache[session_id]

        # Cache'de yok - şema bilgisini al ve cache'le
        try:
            logging.info(
                f"📋 FastAgent: Session {session_id} için şema bilgisi alınıyor (tool formatında)..."
            )

            # Tool'daki query'leri kullan (get_neo4j_schema tool'undan)
            get_nodes_query = """
            CALL db.labels() YIELD label
            WITH collect(label) as labels
            UNWIND labels as lbl
            CALL {
              WITH lbl
              MATCH (n) WHERE lbl IN labels(n)
              WITH count(n) as cnt, collect(properties(n))[0] as sample_props
              RETURN cnt, keys(sample_props) as props
            }
            RETURN lbl as nodeType, cnt as nodeCount, props as properties
            ORDER BY lbl
            """

            get_rels_query = """
            CALL db.relationshipTypes() YIELD relationshipType
            CALL {
              WITH relationshipType
              MATCH (a)-[r]->(b) WHERE type(r) = relationshipType
              WITH labels(a)[0] as from_node, labels(b)[0] as to_node, 
                   collect(properties(r))[0] as sample_props, count(*) as cnt
              ORDER BY cnt DESC
              LIMIT 1
              RETURN from_node, to_node, keys(sample_props) as rel_props
            }
            RETURN relationshipType, from_node, to_node, rel_props
            ORDER BY relationshipType
            """

            # Query'leri çalıştır
            nodes_result = self.graph.query(get_nodes_query)
            rels_result = self.graph.query(get_rels_query)

            # Tool'daki _create_direct_schema_format fonksiyonunu kullan
            schema_string = self._create_direct_schema_format(nodes_result, rels_result)

            # Session bazlı cache'le
            self.schema_cache[session_id] = schema_string
            logging.info(
                f"✅ FastAgent: Session {session_id} için şema cache'lendi ({len(schema_string)} karakter)"
            )
            return schema_string
        except Exception as e:
            logging.error(f"❌ FastAgent: Şema bilgisi alınamadı: {e}", exc_info=True)
            return ""

    def _create_direct_schema_format(self, nodes_result, rels_result) -> str:
        """
        Tool'daki _create_direct_schema_format fonksiyonunun aynısı
        Doğrudan Cypher sorgu sonuçlarından minimal şema formatı oluşturur
        """
        lines = []

        # Tip kısaltmaları (property tahmin için)
        type_mapping = {
            "createdAt": "dt",
            "updatedAt": "dt",
            "created_at": "dt",
            "updated_at": "dt",
            "amount": "float",
            "count": "int",
            "year": "int",
            "month": "int",
        }

        # Node'ları işle
        nodes_section = []
        if not nodes_result:
            logging.debug("_create_direct_schema_format: nodes_result empty or None")
            nodes_result = []

        for node_data in nodes_result:
            # Güvenlik: boş/None kayıtları atla
            if not node_data:
                logging.debug(
                    "_create_direct_schema_format: skipping empty node_data entry"
                )
                continue

            node_name = (
                node_data.get("nodeType") if isinstance(node_data, dict) else None
            )
            node_count = (
                node_data.get("nodeCount") if isinstance(node_data, dict) else None
            )
            properties = (
                node_data.get("properties", []) if isinstance(node_data, dict) else []
            )

            # İlk 6 property'yi kısa tip bilgisiyle al
            props_with_types = []
            for prop_name in properties[:6]:
                # Basit tip tahmin
                if prop_name in type_mapping:
                    prop_type = type_mapping[prop_name]
                elif "id" in prop_name.lower():
                    prop_type = "str"
                elif "name" in prop_name.lower():
                    prop_type = "str"
                elif "address" in prop_name.lower():
                    prop_type = "str"
                elif "content" in prop_name.lower():
                    prop_type = "str"
                else:
                    prop_type = "str"  # default

                props_with_types.append(f"{prop_name}:{prop_type}")

            nodes_section.append(
                f"({node_name}:{node_count}){{{','.join(props_with_types)}}}"
            )

        # Relationship'leri işle
        relationships_section = []
        if not rels_result:
            logging.debug("_create_direct_schema_format: rels_result empty or None")
            rels_result = []

        for rel_data in rels_result:
            if not rel_data:
                logging.debug(
                    "_create_direct_schema_format: skipping empty rel_data entry"
                )
                continue

            rel_name = (
                rel_data.get("relationshipType") if isinstance(rel_data, dict) else None
            )
            from_node = (
                rel_data.get("from_node") if isinstance(rel_data, dict) else None
            )
            to_node = rel_data.get("to_node") if isinstance(rel_data, dict) else None
            rel_props = (
                rel_data.get("rel_props", []) if isinstance(rel_data, dict) else []
            )

            # Relationship properties (varsa ilk 3'ü)
            rel_props_with_types = []
            for prop_name in rel_props[:3]:
                if prop_name in type_mapping:
                    prop_type = type_mapping[prop_name]
                else:
                    prop_type = "str"  # default
                rel_props_with_types.append(f"{prop_name}:{prop_type}")

            # Pattern oluştur
            if rel_props_with_types:
                pattern = f"({from_node})-[:{rel_name} {{{','.join(rel_props_with_types)}}}]->({to_node})"
            else:
                pattern = f"({from_node})-[:{rel_name}]->({to_node})"

            relationships_section.append(pattern)

        # Sonucu birleştir
        if nodes_section:
            lines.append("# NODES")
            lines.extend(nodes_section)

        if relationships_section:
            lines.append("")
            lines.append("# RELATIONSHIPS")
            lines.extend(relationships_section)

        return "\n".join(lines)

    def _get_conversation_history(self, session_id: str) -> str:
        """
        Session ID'ye göre conversation history string'ini oluştur - sadece Neo4j kullan (intelligent_agent gibi)
        """
        logging.info(
            f"FastAgent GET HISTORY: session_id={session_id}, has_graph={self.graph is not None}"
        )

        if not session_id or not self.graph:
            logging.warning(
                f"FastAgent GET HISTORY: Boş session_id veya graph yok - session_id:{session_id}, graph:{self.graph is not None}"
            )
            return ""

        conversation_context = ""
        previous_messages = []

        try:
            logging.info(
                f"FastAgent GET HISTORY: create_neo4j_chat_message_history çağrılıyor..."
            )
            from src.QA_integration import create_neo4j_chat_message_history

            # Neo4j chat history'sini al
            conversation_history = create_neo4j_chat_message_history(
                graph=self.graph, session_id=session_id, write_access=True
            )

            logging.info(
                f"FastAgent GET HISTORY: conversation_history objesi oluşturuldu: {type(conversation_history)}"
            )

            if conversation_history and hasattr(conversation_history, "messages"):
                total_messages = len(conversation_history.messages)
                logging.info(
                    f"FastAgent GET HISTORY: Neo4j'den {total_messages} mesaj bulundu"
                )

                # Mesaj detaylarını logla
                for i, msg in enumerate(conversation_history.messages):
                    msg_type = getattr(msg, "type", "unknown")
                    content_preview = (
                        msg.content[:50] if hasattr(msg, "content") else "no_content"
                    )
                    logging.info(
                        f"FastAgent GET HISTORY: Mesaj {i+1}/{total_messages}: type={msg_type}, content_preview='{content_preview}...'"
                    )

                # Son mesajı hariç tut (henüz işlenen soruyu dahil etme)
                all_messages = (
                    conversation_history.messages[:-1]
                    if conversation_history.messages
                    else []
                )

                logging.info(
                    f"FastAgent GET HISTORY: Son mesaj hariç tutuldu, kalan mesaj sayısı: {len(all_messages)}"
                )

                # Son 40 mesajı al (intelligent_agent ile aynı)
                recent_messages = (
                    all_messages[-40:] if len(all_messages) > 40 else all_messages
                )

                logging.info(
                    f"FastAgent GET HISTORY: Son 40 mesajdan {len(recent_messages)} mesaj seçildi"
                )

                # İlk mesajın Human olduğundan emin ol
                if (
                    recent_messages
                    and recent_messages[0].type != "human"
                    and "Human" not in str(type(recent_messages[0]))
                ):
                    logging.info(
                        f"FastAgent GET HISTORY: İlk mesaj Human değil, Human mesajından başlatılıyor..."
                    )
                    # Eğer ilk mesaj AI ise, bir önceki Human mesajından başla
                    for i in range(len(recent_messages)):
                        if recent_messages[i].type == "human" or "Human" in str(
                            type(recent_messages[i])
                        ):
                            recent_messages = recent_messages[i:]
                            logging.info(
                                f"FastAgent GET HISTORY: {i}. indeksten başlatıldı, yeni mesaj sayısı: {len(recent_messages)}"
                            )
                            break

                # Mesajları format et
                for msg in recent_messages:
                    if hasattr(msg, "content"):
                        msg_type = (
                            "Human"
                            if hasattr(msg, "type") and msg.type == "human"
                            else "AI"
                        )
                        if hasattr(msg, "type"):
                            if msg.type == "human" or "Human" in str(type(msg)):
                                msg_type = "Human"
                            else:
                                msg_type = "AI"
                        previous_messages.append(f"{msg_type}: {msg.content}")

                logging.info(
                    f"FastAgent GET HISTORY: {len(previous_messages)} mesaj formatlandı"
                )

                # Conversation context string oluştur
                if previous_messages:
                    conversation_context = f"""
## 📝 ÖNCEKI KONUŞMA:
{chr(10).join(previous_messages)}

"""
                    logging.info(
                        f"FastAgent GET HISTORY: Conversation context başarıyla oluşturuldu: {len(conversation_context)} karakter"
                    )
                    return conversation_context
                else:
                    logging.info(
                        f"FastAgent GET HISTORY: Formatlanmış mesaj yok, boş context döndürülüyor"
                    )

            else:
                logging.warning(
                    f"FastAgent GET HISTORY: conversation_history boş veya messages attribute'u yok"
                )

        except Exception as e:
            logging.error(f"FastAgent GET HISTORY: Hata oluştu: {e}", exc_info=True)

        logging.info(f"FastAgent GET HISTORY: Boş string döndürülüyor")
        return ""

    def _extract_page_links_from_response(self, response_text: str) -> Set[str]:
        """
        Response text'inden page_link'leri extract eder
        JSON formatında veya text formatında olabilir
        Cypher query sonuçlarını parse ederek page_link'leri bulur
        """
        page_links = set()

        try:
            # JSON formatında page_link araması
            # Örnek: {"page_link": "doc_name_page_001.png"} veya "page_link": "doc_name_page_001.png"
            json_pattern = r'"page_link"\s*:\s*"([^"]+)"'
            matches = re.findall(json_pattern, response_text)
            page_links.update(matches)

            # Cypher query result formatında page_link araması
            # Örnek: c.page_link:AHMET DİNÇ SAĞLIK_page_002.png veya c.page_link=AHMET DİNÇ SAĞLIK_page_002.png
            # Boşlukları ve Türkçe karakterleri de dahil et, ama sonunda _page_XXX.png pattern'i olmalı
            # Türkçe karakterler: ğĞıİşŞüÜöÖçÇ
            # Küçük harfleri de kabul et (a-z)
            cypher_result_pattern = (
                r"\w+\.page_link[:=]\s*([a-zA-Z0-9_\-\.\sğĞıİşŞüÜöÖçÇ]+_page_\d+\.png)"
            )
            matches = re.findall(cypher_result_pattern, response_text, re.IGNORECASE)
            page_links.update(matches)

            # Text formatında page_link araması (page_link: value veya page_link = value)
            # Boşlukları da kabul et, ama sonunda _page_XXX.png pattern'i olmalı
            # Örnek: page_link: AHMET DİNÇ SAĞLIK_page_002.png
            text_pattern = r'page_link["\s]*[:=]\s*["\']?([a-zA-Z0-9_\-\.\sğĞıİşŞüÜöÖçÇ]+_page_\d+\.png)'
            matches = re.findall(text_pattern, response_text, re.IGNORECASE)
            page_links.update(matches)

            # Direkt page_link pattern'i (doc_name_page_XXX.png formatı)
            # Boşlukları da kabul et, ama sonunda _page_XXX.png pattern'i olmalı
            # Türkçe karakterler: ğĞıİşŞüÜöÖçÇ
            # Örnek: AHMET DİNÇ SAĞLIK_page_002.png
            page_link_pattern = r"([a-zA-Z0-9_\-\.\sğĞıİşŞüÜöÖçÇ]+_page_\d+\.png)"
            matches = re.findall(page_link_pattern, response_text, re.IGNORECASE)
            page_links.update(matches)

            # Cypher query sonuçlarını parse et (JSON array formatında olabilir)
            # Örnek: [{"page_link": "doc_name_page_001.png"}, {"page_link": "doc_name_page_002.png"}]
            try:
                # JSON array pattern'i
                json_array_pattern = r"\[.*?\]"
                json_arrays = re.findall(json_array_pattern, response_text, re.DOTALL)
                for json_array_str in json_arrays:
                    try:
                        json_array = json.loads(json_array_str)
                        if isinstance(json_array, list):
                            for item in json_array:
                                if isinstance(item, dict) and "page_link" in item:
                                    page_links.add(item["page_link"])
                    except json.JSONDecodeError:
                        continue
            except Exception:
                pass

            # Filter: sadece geçerli page_link formatlarını al
            filtered_links = set()
            for link in page_links:
                # Link'i temizle (başında/sonunda whitespace, noktalama varsa)
                # Boşlukları koru - S3'te dosya adı boşluklu olabilir
                original_link = link.strip().rstrip(".,;:")

                # Geçerli page_link formatı: dosya_adı_page_XXX.png (Türkçe karakterler ve boşluklar dahil)
                # Türkçe karakterler: ğĞıİşŞüÜöÖçÇ
                # Boşlukları da kabul et - S3'te dosya adı boşluklu olabilir
                if re.match(
                    r"^[a-zA-Z0-9_\-\.\sğĞıİşŞüÜöÖçÇ]+_page_\d+\.png$",
                    original_link,
                    re.IGNORECASE,
                ):
                    # Orijinal link'i kullan (boşlukları ve Türkçe karakterleri koru)
                    # S3'te dosya adı orijinal formatında (boşluklu) olabilir
                    filtered_links.add(original_link)
                    # IntelligentAgent ile page_link'i kaydet
                    if self.intelligent_agent:
                        try:
                            self.intelligent_agent.add_page_resource(original_link)
                            logging.info(
                                f"📄 FastAgent: page_link kaydedildi: {original_link}"
                            )
                        except Exception as e:
                            logging.warning(
                                f"⚠️ FastAgent: page_link kaydetme hatası ({original_link}): {e}"
                            )

            if filtered_links:
                logging.info(
                    f"📄 FastAgent: Response'dan {len(filtered_links)} page_link bulundu: {filtered_links}"
                )
            else:
                logging.warning(
                    f"⚠️ FastAgent: Response'da page_link bulunamadı. Response preview: {response_text[:500]}"
                )

            return filtered_links

        except Exception as e:
            logging.error(f"❌ FastAgent: page_link extract hatası: {e}", exc_info=True)
            return set()

    def _generate_page_links_markdown(
        self, page_links: Set[str], base_url: str = None
    ) -> str:
        """
        Page link'lerden markdown formatında görsel linkler oluşturur
        Thumbnail ve tıklanınca açılacak şekilde
        """
        if not page_links:
            return ""

        if base_url is None:
            base_url = os.getenv("BASE_URL", "http://localhost:8000")

        markdown_section = "\n\n## 📄 İlgili Sayfa Görselleri\n\n"

        for page_link in sorted(page_links):
            try:
                # URL encode
                encoded_page_link = urllib.parse.quote(
                    page_link, safe="", encoding="utf-8"
                )

                # Image URL
                image_url = f"{base_url}/images/{encoded_page_link}"

                # Sayfa bilgilerini parse et (dosya adından sayfa numarasını çıkar)
                page_info = "Sayfa Görseli"
                if "_page_" in page_link:
                    try:
                        page_num = page_link.split("_page_")[1].split(".")[0]
                        page_info = f"Sayfa {page_num}"
                    except:
                        page_info = "Sayfa Görseli"

                # Markdown format: ![alt text](url) - Teams uyumlu format (önceki sürümdeki gibi)
                markdown_section += f"![{page_info}]({image_url})\n\n"

            except Exception as e:
                logging.error(
                    f"❌ FastAgent: page_link markdown oluşturma hatası ({page_link}): {e}"
                )
                continue

        return markdown_section

    def _save_to_history(self, session_id: str, role: str, content: str):
        """
        Mesajı conversation history'ye kaydet - sadece Neo4j kullan (intelligent_agent gibi)
        """
        logging.info(
            f"FastAgent SAVE HISTORY: session_id={session_id}, role={role}, content_length={len(content) if content else 0}"
        )

        if not session_id or not self.graph:
            logging.warning(
                f"FastAgent SAVE HISTORY: Kayıt atlandı - session_id:{session_id}, has_graph:{self.graph is not None}"
            )
            return

        try:
            logging.info(
                f"FastAgent SAVE HISTORY: create_neo4j_chat_message_history çağrılıyor..."
            )
            from src.QA_integration import create_neo4j_chat_message_history
            from langchain_core.messages import HumanMessage, AIMessage

            # Neo4j chat history'sini al (write_access=True ile)
            conversation_history = create_neo4j_chat_message_history(
                graph=self.graph, session_id=session_id, write_access=True
            )

            logging.info(
                f"FastAgent SAVE HISTORY: conversation_history objesi oluşturuldu: {type(conversation_history)}"
            )

            # Kayıt öncesi mevcut mesaj sayısını kontrol et
            current_message_count = (
                len(conversation_history.messages)
                if hasattr(conversation_history, "messages")
                else 0
            )
            logging.info(
                f"FastAgent SAVE HISTORY: Kayıt öncesi mevcut mesaj sayısı: {current_message_count}"
            )

            # Doğru mesaj tipini oluştur
            if role.lower() in ["human", "user"]:
                message = HumanMessage(content=content)
                logging.info(f"FastAgent SAVE HISTORY: HumanMessage oluşturuldu")
            else:
                message = AIMessage(content=content)
                logging.info(f"FastAgent SAVE HISTORY: AIMessage oluşturuldu")

            # Content preview'u logla
            content_preview = content[:100] if content else "boş"
            logging.info(
                f"FastAgent SAVE HISTORY: Mesaj içeriği (ilk 100 kar): '{content_preview}...'"
            )

            # Neo4j'ye kaydet (otomatik olarak)
            logging.info(f"FastAgent SAVE HISTORY: add_message() çağrılıyor...")
            conversation_history.add_message(message)

            # Kayıt sonrası mesaj sayısını kontrol et
            new_message_count = (
                len(conversation_history.messages)
                if hasattr(conversation_history, "messages")
                else 0
            )
            logging.info(
                f"FastAgent SAVE HISTORY: Kayıt sonrası mesaj sayısı: {new_message_count}"
            )

            success_message = f"FastAgent SAVE HISTORY: BAŞARILI - {role} mesajı kaydedildi ({current_message_count} -> {new_message_count})"
            logging.info(success_message)

        except Exception as e:
            logging.error(
                f"FastAgent SAVE HISTORY: HATA - Neo4j'ye mesaj kaydedilemedi: {e}",
                exc_info=True,
            )
            # Local fallback artık yok - hata durumunda logla ama devam et

    async def stream_query_response(
        self, question: str, session_id: str = None, **kwargs
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        FastAgent kullanarak streaming cevap üret - Conversation history ile
        """
        try:
            # Başlangıç durumu
            yield {
                "type": "status",
                "message": "FastAgent ile sorgunuz işleniyor...",
                "status": "processing",
                "session_id": session_id,
                "timestamp": datetime.now().isoformat(),
            }

            # Şema bilgisini al (session bazlı cache'den)
            schema_info = self._get_schema_for_session(session_id)

            # Şema bilgisi yoksa veya boşsa, LLM'e uyarı mesajı ekle
            schema_section = ""
            if not schema_info or schema_info.strip() == "":
                schema_section = """## ⚠️ ÖNEMLİ UYARI:
Veritabanı şema bilgisi bu oturum için mevcut değil. Lütfen yeni bir chat oluşturun (yeni session başlatın) ve tekrar deneyin.

**Kullanıcıya iletmen gereken mesaj:**
"Üzgünüm, bu oturumda veritabanı şema bilgisi mevcut değil. Lütfen yeni bir chat oluşturun ve tekrar deneyin."

Bu mesajı kullanıcıya ilet ve başka bir işlem yapma."""
                logging.warning(
                    f"⚠️ FastAgent STREAM: Session {session_id} için şema bilgisi yok - kullanıcıya uyarı gönderilecek"
                )
                # Şema yoksa hemen uyarıyı gönder
                yield {
                    "type": "error",
                    "message": "Üzgünüm, bu oturumda veritabanı şema bilgisi mevcut değil. Lütfen yeni bir chat oluşturun ve tekrar deneyin.",
                    "status": "error",
                    "session_id": session_id,
                    "timestamp": datetime.now().isoformat(),
                }
                return
            else:
                schema_section = f"""## 📊 NEO4J VERİTABANI ŞEMA BİLGİSİ:
{schema_info}"""
                logging.info(
                    f"📋 FastAgent STREAM: Şema bilgisi prompt'a eklendi ({len(schema_info)} karakter)"
                )
                # Şema bilgisi cache'den alındı, hemen kullanıcıya bilgi ver
                yield {
                    "type": "status",
                    "message": "✅ Veritabanı şema bilgisi hazır, sorgunuz işleniyor...",
                    "status": "processing",
                    "session_id": session_id,
                    "timestamp": datetime.now().isoformat(),
                }

            # Conversation history'yi al
            logging.info(
                f"FastAgent STREAM: session_id={session_id} için history alınıyor..."
            )
            conversation_context = self._get_conversation_history(session_id)

            # Soruyu şema bilgisi ve history ile birleştir
            enhanced_question = question
            if schema_section:
                enhanced_question = f"""{schema_section}

{enhanced_question}"""

            if conversation_context:
                enhanced_question = f"""{conversation_context}

## 🤖 YENİ SORU:
{enhanced_question}

Lütfen önceki konuşma bağlamını da dikkate al ve yeni soru ile ilişkisi var ise ona göre cevap bulmaya çalış."""
                logging.info(
                    f"FastAgent STREAM: Session {session_id} için conversation history eklendi ({len(conversation_context)} karakter)"
                )
                logging.info(
                    f"FastAgent STREAM: Enhanced question oluşturuldu: {len(enhanced_question)} karakter"
                )
            else:
                logging.info(
                    f"FastAgent STREAM: Session {session_id} için conversation history bulunamadı, sadece yeni soru kullanılacak"
                )

            # Kullanıcı sorusunu history'ye kaydet
            logging.info(
                f"FastAgent STREAM: Kullanıcı sorusu history'ye kaydediliyor..."
            )
            self._save_to_history(session_id, "Human", question)

            # FastAgent'i doğru şekilde kullan
            if not self.fast_agent_app:
                self.fast_agent_app = create_fast_agent_app(self.model)

            async with self.fast_agent_app.run() as agent:
                # Enhanced question ile Basic Agent'a gönder (Chain yerine)
                response = await agent.neo4j_intelligence.send(enhanced_question)

                # Response object'ini detaylı incele
                response_text = str(response)

                # IntelligentAgent'dan page_link'leri topla (resource_manager üzerinden)
                # Bu, execute_cypher_query tool çağrıları sırasında add_page_resource ile eklenen page_link'leri içerir
                if self.intelligent_agent:
                    resources = (
                        self.intelligent_agent.resource_manager.get_all_resources()
                    )
                    if resources["total_count"] > 0:
                        for page_resource in resources["pages"]:
                            page_link = page_resource.get("page_link")
                            if page_link:
                                self.collected_page_links.add(page_link)
                                logging.info(
                                    f"📄 FastAgent: ResourceManager'dan page_link toplandı: {page_link}"
                                )

                    # ResourceManager'ı temizle (bir sonraki sorgu için)
                    self.intelligent_agent.resource_manager.clear_resources()

                # Eğer resource_manager'da page_link yoksa, response text'inden extract et (fallback)
                if not self.collected_page_links:
                    extracted_links = self._extract_page_links_from_response(
                        response_text
                    )
                    if extracted_links:
                        self.collected_page_links.update(extracted_links)
                        logging.info(
                            f"📄 FastAgent: Response text'inden {len(extracted_links)} page_link toplandı: {extracted_links}"
                        )

                if self.collected_page_links:
                    logging.info(
                        f"📄 FastAgent: Toplam {len(self.collected_page_links)} page_link toplandı: {self.collected_page_links}"
                    )
                else:
                    logging.warning(
                        f"⚠️ FastAgent: Hiç page_link bulunamadı. Response length: {len(response_text)}"
                    )

                # Cevabı kelime kelime stream et
                words = str(response).split()
                streamed_content = ""

                for i, word in enumerate(words):
                    streamed_content += word + " "

                    # Her kelimeyi ayrı chunk olarak gönder
                    yield {
                        "type": "message_chunk",
                        "content": word + " ",
                        "full_message": streamed_content.strip(),
                        "session_id": session_id,
                        "timestamp": datetime.now().isoformat(),
                    }

                    # Streaming gecikmesi
                    await asyncio.sleep(0.05)

                # Page link'leri varsa cevabın sonuna markdown olarak ekle
                final_ai_response = streamed_content.strip()
                if self.collected_page_links:
                    page_links_markdown = self._generate_page_links_markdown(
                        self.collected_page_links
                    )
                    final_ai_response += page_links_markdown
                    logging.info(
                        f"📄 FastAgent: Page link'ler cevaba eklendi: {len(self.collected_page_links)} adet"
                    )

                    # Markdown'ı da stream et
                    markdown_words = page_links_markdown.split()
                    for word in markdown_words:
                        streamed_content += word + " "
                        yield {
                            "type": "message_chunk",
                            "content": word + " ",
                            "full_message": streamed_content.strip(),
                            "session_id": session_id,
                            "timestamp": datetime.now().isoformat(),
                        }
                        await asyncio.sleep(0.05)

                # AI cevabını history'ye kaydet (page_link'ler dahil)
                logging.info(f"FastAgent STREAM: AI cevabı history'ye kaydediliyor...")
                self._save_to_history(session_id, "AI", final_ai_response)
                logging.info(
                    f"FastAgent STREAM: AI cevabı kaydedildi: {len(final_ai_response)} karakter"
                )

                # Tamamlanma durumu
                yield {
                    "type": "complete",
                    "message": final_ai_response,
                    "status": "finished",
                    "session_id": session_id,
                    "info": {
                        "agent_type": "fast_agent_basic",
                        "model": self.model,
                        "total_tokens": len(words),
                        "agent_used": "neo4j_intelligence",  # Chain yerine Basic Agent
                        "has_conversation_context": bool(conversation_context),
                        "page_links_count": len(self.collected_page_links),
                    },
                    "timestamp": datetime.now().isoformat(),
                }

                # Page link'leri temizle (bir sonraki sorgu için)
                self.collected_page_links.clear()

        except Exception as e:
            error_message = f"FastAgent query error: {str(e)}"
            logging.error(error_message)

            yield {
                "type": "error",
                "message": error_message,
                "status": "failed",
                "session_id": session_id,
                "timestamp": datetime.now().isoformat(),
            }


# Global FastAgent instance
_global_fast_agent = None


async def get_or_create_fast_agent(
    model: str = "gpt-5-mini.low", graph=None
) -> FastAgentIntegration:
    """Global FastAgent instance'ını al veya oluştur"""
    global _global_fast_agent

    if _global_fast_agent is None:
        _global_fast_agent = FastAgentIntegration(model=model, graph=graph)
        logging.info(f"🆕 FastAgent: Yeni global instance oluşturuldu - Model: {model}")
    else:
        # Mevcut instance var - sadece model ve graph'ı güncelle (cache korunur)
        schema_cache_size = len(_global_fast_agent.schema_cache)

        if _global_fast_agent.model != model:
            # Model değişmişse sadece model'i güncelle (cache korunur - şema session'a bağlı)
            logging.info(
                f"🔄 FastAgent: Model güncelleniyor ({_global_fast_agent.model} -> {model}), cache korunuyor ({schema_cache_size} session)"
            )
            _global_fast_agent.model = model
            # FastAgent app'i None yap (lazy initialization ile yeni model ile oluşturulacak)
            _global_fast_agent.fast_agent_app = None

        if graph and _global_fast_agent.graph != graph:
            # Graph connection değişmişse güncelle (cache korunur - session bazlı cache)
            logging.info(
                f"🔄 FastAgent: Graph connection güncelleniyor (cache korunuyor - {schema_cache_size} session)"
            )
            _global_fast_agent.graph = graph
            # IntelligentAgent'ı yeniden initialize et
            if graph:
                try:
                    from src.intelligent_agent import IntelligentAgent

                    _global_fast_agent.intelligent_agent = IntelligentAgent(graph)
                    logging.info(
                        "✅ FastAgent: IntelligentAgent graph ile yeniden initialize edildi"
                    )
                except Exception as e:
                    logging.warning(
                        f"⚠️ FastAgent: IntelligentAgent initialization failed: {e}"
                    )

    return _global_fast_agent


async def stream_fast_agent_response(
    question: str,
    model: str = "gpt-5-mini.low",
    session_id: str = None,
    graph=None,
    **kwargs,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    FastAgent kullanarak streaming cevap üret - Conversation History ile

    Args:
        question: Kullanıcının sorusu
        model: Kullanılacak LLM modeli
        session_id: Oturum ID'si (conversation history için)
        graph: Neo4j graph connection (persistent history için)
        **kwargs: Ek parametreler

    Yields:
        Dict: Streaming chunk'ları
    """

    # FastAgent mevcut değilse fallback
    if not FAST_AGENT_AVAILABLE:
        yield {
            "type": "error",
            "message": "FastAgent kurulu değil. Lütfen fast-agent-mcp paketini kurun.",
            "status": "not_available",
            "session_id": session_id,
            "timestamp": datetime.now().isoformat(),
        }
        return

    try:
        # FastAgent instance'ını al (graph connection ile)
        logging.info(
            f"FastAgent MAIN: get_or_create_fast_agent çağrılıyor - model={model}, has_graph={graph is not None}, session_id={session_id}"
        )
        agent = await get_or_create_fast_agent(model, graph)
        logging.info(
            f"FastAgent MAIN: Agent oluşturuldu, graph connection={agent.graph is not None}"
        )

        # Conversation history özellikli streaming
        logging.info(f"FastAgent MAIN: stream_query_response başlatılıyor...")
        async for chunk in agent.stream_query_response(
            question=question, session_id=session_id, **kwargs
        ):
            yield chunk

    except Exception as e:
        logging.error(f"FastAgent streaming failed: {e}")
        yield {
            "type": "error",
            "message": f"FastAgent hatası: {str(e)}",
            "status": "failed",
            "session_id": session_id,
            "timestamp": datetime.now().isoformat(),
        }
