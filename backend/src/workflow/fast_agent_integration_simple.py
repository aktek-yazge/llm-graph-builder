"""
FastAgent Integration Module for Chat Bot Stream - With Conversation History

Bu modül FastAgent'i chat_bot_stream endpoint'ine entegre eder.
Session ID'ye göre conversation history özelliği ile birlikte.
"""

import asyncio
import json
import logging
import os
from typing import AsyncGenerator, Dict, Any, Optional
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
        def __init__(self, name): pass
    class RequestParams:
        def __init__(self, **kwargs): pass
    class Prompt:
        @staticmethod
        def user(text): return text

def create_fast_agent_app(model: str = "gpt-5-mini.low") -> FastAgent:
    """gds_fetcher.py ile aynı basit yaklaşımla FastAgent uygulaması oluştur"""
    
    app = FastAgent("Neo4j Query Agent")

    @app.agent(
        "neo4j_intelligence",  # Daha anlamlı isim
        instruction="""
        Sen Dinkal Sigortaya ait poliçeler hakkında sorulan sorulara cevap veren bir ajansın. 
        
        Bu bilgilere nasıl erişebileceğini bilmiyorsun. Öğrenmek için get_neo4j_schema sana yol gösterecek.
        
        **STRING NORMALİZASYON**: Execute queries exactly as reasoner provides:
        ```cypher
        toLower(apoc.text.clean(field)) CONTAINS toLower(apoc.text.clean('value'))
        ```
       
        Şema bilgisine göre tool çağrıları yaparak sonuca ulaşmaya çalış.
        
        Şema dışına çıkma sorgularında.
        
        Şemada olmayan alanları kullanamazsın. Alanlar hakkında tahminleme yapamazsın. 

        Sorudan çıkarım yaparak field tahminlemesi yapme. Db veri yapısını öğrenmek için soruyu tek kelimeli parçalara bölerek her seferinde bir odak kelimeyi aratarak limitli sorgular ile anlamaya çalış.
        
        Genel query aramaları yapmaktan kaçın.

        Doğru sorguyu yapabilmek için limitli(1-5) sorgular atarak örnek datalara bakman herzaman daha iyidir.
        
        Mesela diyelimki 5 tane kayıt buldun ve içinde soruyu cevaplayan kayıt yok ama sana örnek kayıtlara gözatma imkanı sunduğu için tahmin yürüterek sonuçlara ulaşmaya çalışabilirsin. Bunlara keşif sorguları diyebiliriz.

        Elde ettiğin keşif sorguları cevap bulunamadı manasına gelmez. Bunlar sadece tablo veri yapısını anlamanı sağlar.

        Keşif sorguları yaparken özne ve nesneye odaklanarak tekil kelimeler ile arama yapmalısın.
        

        İçerik, Konu, bağlam hakkındaki bilgiler Chunk nodelarında text alanında saklıdır. İlgili belgeleri bulduktan sonra (**STRING NORMALİZASYON**) ile içerik araması yap ve ilgili aramalara metadata filtreleri ile tekil keywordler ile aranmalı.

        Eğer Chunk araması yaptıysan ve chunklarda kesik veya eksik bilgi olabilir. Bir sonraki 2 chunka bakarak bu bilgiyi tamamlamaya çalış.

        Verdiğin son cevapta teknik bilgilerden bahsetmeni istemiyorum. Sadece son kullanıcıya yönelik sade ve anlaşılır cevaplar ver.
        """,
        servers=["neo4j-database","embedding"],
        request_params=RequestParams(max_iterations=15,),
        use_history=True,  # Test: multi-tool calls için gerekli mi?
        model=model
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
        
    def _get_conversation_history(self, session_id: str) -> str:
        """
        Session ID'ye göre conversation history string'ini oluştur - sadece Neo4j kullan (intelligent_agent gibi)
        """
        logging.info(f"FastAgent GET HISTORY: session_id={session_id}, has_graph={self.graph is not None}")
        
        if not session_id or not self.graph:
            logging.warning(f"FastAgent GET HISTORY: Boş session_id veya graph yok - session_id:{session_id}, graph:{self.graph is not None}")
            return ""
            
        conversation_context = ""
        previous_messages = []
        
        try:
            logging.info(f"FastAgent GET HISTORY: create_neo4j_chat_message_history çağrılıyor...")
            from src.QA_integration import create_neo4j_chat_message_history
            
            # Neo4j chat history'sini al
            conversation_history = create_neo4j_chat_message_history(
                graph=self.graph, 
                session_id=session_id, 
                write_access=True
            )
            
            logging.info(f"FastAgent GET HISTORY: conversation_history objesi oluşturuldu: {type(conversation_history)}")
            
            if conversation_history and hasattr(conversation_history, "messages"):
                total_messages = len(conversation_history.messages)
                logging.info(f"FastAgent GET HISTORY: Neo4j'den {total_messages} mesaj bulundu")
                
                # Mesaj detaylarını logla
                for i, msg in enumerate(conversation_history.messages):
                    msg_type = getattr(msg, 'type', 'unknown')
                    content_preview = msg.content[:50] if hasattr(msg, 'content') else 'no_content'
                    logging.info(f"FastAgent GET HISTORY: Mesaj {i+1}/{total_messages}: type={msg_type}, content_preview='{content_preview}...'")
                
                # Son mesajı hariç tut (henüz işlenen soruyu dahil etme)
                all_messages = (
                    conversation_history.messages[:-1]
                    if conversation_history.messages
                    else []
                )
                
                logging.info(f"FastAgent GET HISTORY: Son mesaj hariç tutuldu, kalan mesaj sayısı: {len(all_messages)}")
                
                # Son 40 mesajı al (intelligent_agent ile aynı)
                recent_messages = (
                    all_messages[-40:] if len(all_messages) > 40 else all_messages
                )
                
                logging.info(f"FastAgent GET HISTORY: Son 40 mesajdan {len(recent_messages)} mesaj seçildi")
                
                # İlk mesajın Human olduğundan emin ol
                if (
                    recent_messages
                    and recent_messages[0].type != "human"
                    and "Human" not in str(type(recent_messages[0]))
                ):
                    logging.info(f"FastAgent GET HISTORY: İlk mesaj Human değil, Human mesajından başlatılıyor...")
                    # Eğer ilk mesaj AI ise, bir önceki Human mesajından başla
                    for i in range(len(recent_messages)):
                        if recent_messages[i].type == "human" or "Human" in str(
                            type(recent_messages[i])
                        ):
                            recent_messages = recent_messages[i:]
                            logging.info(f"FastAgent GET HISTORY: {i}. indeksten başlatıldı, yeni mesaj sayısı: {len(recent_messages)}")
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
                
                logging.info(f"FastAgent GET HISTORY: {len(previous_messages)} mesaj formatlandı")
                
                # Conversation context string oluştur
                if previous_messages:
                    conversation_context = f"""
## 📝 ÖNCEKI KONUŞMA:
{chr(10).join(previous_messages)}

"""
                    logging.info(f"FastAgent GET HISTORY: Conversation context başarıyla oluşturuldu: {len(conversation_context)} karakter")
                    return conversation_context
                else:
                    logging.info(f"FastAgent GET HISTORY: Formatlanmış mesaj yok, boş context döndürülüyor")
                    
            else:
                logging.warning(f"FastAgent GET HISTORY: conversation_history boş veya messages attribute'u yok")
                    
        except Exception as e:
            logging.error(f"FastAgent GET HISTORY: Hata oluştu: {e}", exc_info=True)
        
        logging.info(f"FastAgent GET HISTORY: Boş string döndürülüyor")
        return ""
    
    def _save_to_history(self, session_id: str, role: str, content: str):
        """
        Mesajı conversation history'ye kaydet - sadece Neo4j kullan (intelligent_agent gibi)
        """
        logging.info(f"FastAgent SAVE HISTORY: session_id={session_id}, role={role}, content_length={len(content) if content else 0}")
        
        if not session_id or not self.graph:
            logging.warning(f"FastAgent SAVE HISTORY: Kayıt atlandı - session_id:{session_id}, has_graph:{self.graph is not None}")
            return
        
        try:
            logging.info(f"FastAgent SAVE HISTORY: create_neo4j_chat_message_history çağrılıyor...")
            from src.QA_integration import create_neo4j_chat_message_history
            from langchain_core.messages import HumanMessage, AIMessage
            
            # Neo4j chat history'sini al (write_access=True ile)
            conversation_history = create_neo4j_chat_message_history(
                graph=self.graph, 
                session_id=session_id, 
                write_access=True
            )
            
            logging.info(f"FastAgent SAVE HISTORY: conversation_history objesi oluşturuldu: {type(conversation_history)}")
            
            # Kayıt öncesi mevcut mesaj sayısını kontrol et
            current_message_count = len(conversation_history.messages) if hasattr(conversation_history, 'messages') else 0
            logging.info(f"FastAgent SAVE HISTORY: Kayıt öncesi mevcut mesaj sayısı: {current_message_count}")
            
            # Doğru mesaj tipini oluştur
            if role.lower() in ["human", "user"]:
                message = HumanMessage(content=content)
                logging.info(f"FastAgent SAVE HISTORY: HumanMessage oluşturuldu")
            else:
                message = AIMessage(content=content)
                logging.info(f"FastAgent SAVE HISTORY: AIMessage oluşturuldu")
            
            # Content preview'u logla
            content_preview = content[:100] if content else "boş"
            logging.info(f"FastAgent SAVE HISTORY: Mesaj içeriği (ilk 100 kar): '{content_preview}...'")
            
            # Neo4j'ye kaydet (otomatik olarak)
            logging.info(f"FastAgent SAVE HISTORY: add_message() çağrılıyor...")
            conversation_history.add_message(message)
            
            # Kayıt sonrası mesaj sayısını kontrol et
            new_message_count = len(conversation_history.messages) if hasattr(conversation_history, 'messages') else 0
            logging.info(f"FastAgent SAVE HISTORY: Kayıt sonrası mesaj sayısı: {new_message_count}")
            
            success_message = f"FastAgent SAVE HISTORY: BAŞARILI - {role} mesajı kaydedildi ({current_message_count} -> {new_message_count})"
            logging.info(success_message)
                    
        except Exception as e:
            logging.error(f"FastAgent SAVE HISTORY: HATA - Neo4j'ye mesaj kaydedilemedi: {e}", exc_info=True)
            # Local fallback artık yok - hata durumunda logla ama devam et

    async def stream_query_response(
        self,
        question: str,
        session_id: str = None,
        **kwargs
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        FastAgent kullanarak streaming cevap üret - Conversation history ile
        """
        try:
            # Başlangıç durumu
            yield {
                'type': 'status',
                'message': 'FastAgent ile sorgunuz işleniyor...',
                'status': 'processing',
                'session_id': session_id,
                'timestamp': datetime.now().isoformat()
            }
            
            # Conversation history'yi al
            logging.info(f"FastAgent STREAM: session_id={session_id} için history alınıyor...")
            conversation_context = self._get_conversation_history(session_id)
            
            # Soruyu history ile birleştir
            enhanced_question = question
            if conversation_context:
                enhanced_question = f"""{conversation_context}

## 🤖 YENİ SORU:
{question}

Lütfen önceki konuşma bağlamını da dikkate al ve yeni soru ile ilişkisi var ise ona göre cevap bulmaya çalış."""
                logging.info(f"FastAgent STREAM: Session {session_id} için conversation history eklendi ({len(conversation_context)} karakter)")
                logging.info(f"FastAgent STREAM: Enhanced question oluşturuldu: {len(enhanced_question)} karakter")
            else:
                logging.info(f"FastAgent STREAM: Session {session_id} için conversation history bulunamadı, sadece yeni soru kullanılacak")
            
            # Kullanıcı sorusunu history'ye kaydet
            logging.info(f"FastAgent STREAM: Kullanıcı sorusu history'ye kaydediliyor...")
            self._save_to_history(session_id, "Human", question)
            
            # FastAgent'i doğru şekilde kullan
            if not self.fast_agent_app:
                self.fast_agent_app = create_fast_agent_app(self.model)
            
            async with self.fast_agent_app.run() as agent:
                # Enhanced question ile Basic Agent'a gönder (Chain yerine)
                response = await agent.neo4j_intelligence.send(enhanced_question)
                
                # Cevabı kelime kelime stream et
                words = str(response).split()
                streamed_content = ""
                
                for i, word in enumerate(words):
                    streamed_content += word + " "
                    
                    # Her kelimeyi ayrı chunk olarak gönder
                    yield {
                        'type': 'message_chunk',
                        'content': word + " ",
                        'full_message': streamed_content.strip(),
                        'session_id': session_id,
                        'timestamp': datetime.now().isoformat()
                    }
                    
                    # Streaming gecikmesi
                    await asyncio.sleep(0.05)
                
                # AI cevabını history'ye kaydet
                logging.info(f"FastAgent STREAM: AI cevabı history'ye kaydediliyor...")
                final_ai_response = streamed_content.strip()
                self._save_to_history(session_id, "AI", final_ai_response)
                logging.info(f"FastAgent STREAM: AI cevabı kaydedildi: {len(final_ai_response)} karakter")
                
                # Tamamlanma durumu
                yield {
                    'type': 'complete',
                    'message': streamed_content.strip(),
                    'status': 'finished',
                    'session_id': session_id,
                    'info': {
                        'agent_type': 'fast_agent_basic',
                        'model': self.model,
                        'total_tokens': len(words),
                        'agent_used': 'neo4j_intelligence',  # Chain yerine Basic Agent
                        'has_conversation_context': bool(conversation_context)
                    },
                    'timestamp': datetime.now().isoformat()
                }
            
        except Exception as e:
            error_message = f"FastAgent query error: {str(e)}"
            logging.error(error_message)
            
            yield {
                'type': 'error',
                'message': error_message,
                'status': 'failed',
                'session_id': session_id,
                'timestamp': datetime.now().isoformat()
            }

# Global FastAgent instance
_global_fast_agent = None

async def get_or_create_fast_agent(model: str = "gpt-5-mini.low", graph=None) -> FastAgentIntegration:
    """Global FastAgent instance'ını al veya oluştur"""
    global _global_fast_agent
    
    if _global_fast_agent is None or _global_fast_agent.model != model:
        _global_fast_agent = FastAgentIntegration(model=model, graph=graph)
    elif graph and _global_fast_agent.graph != graph:
        # Graph connection değişmişse güncelle
        _global_fast_agent.graph = graph
    
    return _global_fast_agent

async def stream_fast_agent_response(
    question: str,
    model: str = "gpt-5-mini.low", 
    session_id: str = None,
    graph = None,
    **kwargs
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
            'type': 'error',
            'message': 'FastAgent kurulu değil. Lütfen fast-agent-mcp paketini kurun.',
            'status': 'not_available',
            'session_id': session_id,
            'timestamp': datetime.now().isoformat()
        }
        return
    
    try:
        # FastAgent instance'ını al (graph connection ile)
        logging.info(f"FastAgent MAIN: get_or_create_fast_agent çağrılıyor - model={model}, has_graph={graph is not None}, session_id={session_id}")
        agent = await get_or_create_fast_agent(model, graph)
        logging.info(f"FastAgent MAIN: Agent oluşturuldu, graph connection={agent.graph is not None}")
        
        # Conversation history özellikli streaming
        logging.info(f"FastAgent MAIN: stream_query_response başlatılıyor...")
        async for chunk in agent.stream_query_response(
            question=question,
            session_id=session_id,
            **kwargs
        ):
            yield chunk
                
    except Exception as e:
        logging.error(f"FastAgent streaming failed: {e}")
        yield {
            'type': 'error',
            'message': f"FastAgent hatası: {str(e)}",
            'status': 'failed',
            'session_id': session_id,
            'timestamp': datetime.now().isoformat()
        }