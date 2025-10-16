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
logging.basicConfig(level=logging.INFO)
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

def create_fast_agent_app(model: str = "gpt-5-mini") -> FastAgent:
    """gds_fetcher.py ile aynı basit yaklaşımla FastAgent uygulaması oluştur"""
    
    app = FastAgent("Neo4j Query Agent")

    @app.agent(
        "neo4j_query",
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

        Şema Keşif yaptıktan sonra Cypher query ile *SavedAnswer* nodlarına bakman herzaman en iyisisidr.
        
        Doğru sorguyu yapabilmek için limitli(1-5) sorgular atarak örnek datalara bakman herzaman daha iyidir.
        
        Mesela diyelimki 5 tane kayıt buldun ve içinde soruyu cevaplayan kayıt yok ama sana örnek kayıtlara gözatma imkanı sunduğu için tahmin yürüterek sonuçlara ulaşmaya çalışabilirsin. Bunlara keşif sorguları diyebiliriz.

        Elde ettiğin keşif sorguları cevap bulunamadı manasına gelmez. Bunlar sadece tablo veri yapısını anlamanı sağlar.

        Keşif sorguları yaparken özne ve nesneye odaklanarak tekil kelimeler ile arama yapmalısın.
        
        SavedAnswer araması bir sefer yeterlidir genelde.

        İçerik, Konu, bağlam hakkındaki bilgiler Chunk nodelarında text alanında saklıdır. İlgili belgeleri bulduktan sonra (**STRING NORMALİZASYON**) ile içerik araması yap ve ilgili aramalara metadata filtreleri ile tekil keywordler ile aranmalı.

        Eğer Chunk araması yaptıysan ve chunklarda kesik veya eksik bilgi olabilir. Bir sonraki 2 chunka bakarak bu bilgiyi tamamlamaya çalış.

        Sadece doğru Cevap bulunmuş ise bilgiyi sessizce SavedAnswer ye kaydet.
       
        """,
        servers=["neo4j-database","embedding"],
        request_params=RequestParams(max_iterations=15,),
        use_history=True,
        model=model
    )

    @app.chain(
        name="query_analyser",
        sequence=["neo4j_query"]
    )
    async def main():
        """Placeholder main function - gds_fetcher.py'daki gibi"""
        pass
    
    return app

class FastAgentIntegration:
    """FastAgent'i chat_bot_stream'e entegre eden sınıf - Conversation History ile"""
    
    def __init__(self, model: str = "gpt-5-mini", graph=None):
        self.model = model
        self.fast_agent_app = None
        self.conversation_histories = {}  # session_id -> local conversation history (fallback)
        self.graph = graph  # Neo4j graph connection for persistent history
        
    def _get_conversation_history(self, session_id: str) -> str:
        """
        Session ID'ye göre conversation history string'ini oluştur
        önce Neo4j'den dener, yoksa local fallback kullanır
        """
        if not session_id:
            return ""
            
        conversation_context = ""
        previous_messages = []
        
        # Neo4j'den history almaya çalış (intelligent_agent'taki gibi)
        if self.graph:
            try:
                from src.QA_integration import get_history_by_session_id
                
                conversation_history = get_history_by_session_id(
                    session_id, self.graph, write_access=True
                )
                
                if conversation_history and hasattr(conversation_history, "messages"):
                    logging.info(f"FastAgent: Neo4j'den conversation history alındı: {len(conversation_history.messages)} mesaj")
                    
                    # Son mesajı hariç tut (henüz işlenen soruyu dahil etme)
                    all_messages = (
                        conversation_history.messages[:-1]
                        if conversation_history.messages
                        else []
                    )
                    
                    # Son 20 mesajı al (performans için)
                    recent_messages = (
                        all_messages[-20:] if len(all_messages) > 20 else all_messages
                    )
                    
                    # İlk mesajın Human olduğundan emin ol
                    if (
                        recent_messages
                        and recent_messages[0].type != "human"
                        and "Human" not in str(type(recent_messages[0]))
                    ):
                        # Eğer ilk mesaj AI ise, bir önceki Human mesajından başla
                        for i in range(len(recent_messages)):
                            if recent_messages[i].type == "human" or "Human" in str(
                                type(recent_messages[i])
                            ):
                                recent_messages = recent_messages[i:]
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
                    
                    # Conversation context string oluştur
                    if previous_messages:
                        conversation_context = f"""
## 📝 ÖNCEKI KONUŞMA (Neo4j):
{chr(10).join(previous_messages)}

"""
                        logging.info(f"FastAgent: Neo4j conversation context oluşturuldu: {len(previous_messages)} mesaj")
                        return conversation_context
                
            except Exception as e:
                logging.warning(f"FastAgent: Neo4j history alınamadı, local fallback kullanılacak: {e}")
        
        # Local fallback
        if session_id in self.conversation_histories:
            history_messages = self.conversation_histories[session_id]
            if history_messages:
                # Son 20 mesajı al (performans için)
                recent_messages = history_messages[-20:] if len(history_messages) > 20 else history_messages
                
                # Conversation context string oluştur
                formatted_messages = []
                for msg in recent_messages:
                    formatted_messages.append(f"{msg['role']}: {msg['content']}")
                    
                if formatted_messages:
                    return f"""
## 📝 ÖNCEKI KONUŞMA (Local):
{chr(10).join(formatted_messages)}

"""
        
        return ""
    
    def _save_to_history(self, session_id: str, role: str, content: str):
        """
        Mesajı conversation history'ye kaydet
        önce Neo4j'ye dener, yoksa local fallback kullanır
        """
        if not session_id:
            return
        
        # Neo4j'ye kaydetmeye çalış
        if self.graph:
            try:
                from src.QA_integration import get_history_by_session_id
                from langchain_core.messages import HumanMessage, AIMessage
                
                conversation_history = get_history_by_session_id(
                    session_id, self.graph, write_access=True
                )
                
                if conversation_history:
                    # Doğru mesaj tipini oluştur
                    if role.lower() in ["human", "user"]:
                        message = HumanMessage(content=content)
                    else:
                        message = AIMessage(content=content)
                    
                    conversation_history.add_message(message)
                    logging.info(f"FastAgent: Mesaj Neo4j'ye kaydedildi - {role}: {len(content)} karakter")
                    return
                    
            except Exception as e:
                logging.warning(f"FastAgent: Neo4j'ye mesaj kaydedilemedi, local fallback kullanılacak: {e}")
        
        # Local fallback
        if session_id not in self.conversation_histories:
            self.conversation_histories[session_id] = []
            
        self.conversation_histories[session_id].append({
            'role': role,
            'content': content,
            'timestamp': datetime.now().isoformat()
        })
        
        # History'yi maksimum 100 mesajla sınırla (50 soru-cevap çifti)
        if len(self.conversation_histories[session_id]) > 100:
            self.conversation_histories[session_id] = self.conversation_histories[session_id][-100:]
        
        logging.info(f"FastAgent: Mesaj local history'ye kaydedildi - {role}: {len(content)} karakter")

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
            conversation_context = self._get_conversation_history(session_id)
            
            # Soruyu history ile birleştir
            enhanced_question = question
            if conversation_context:
                enhanced_question = f"""{conversation_context}

## 🤖 YENİ SORU:
{question}

Lütfen önceki konuşma bağlamını da dikkate alarak cevapla."""
                logging.info(f"Session {session_id}: Conversation history eklendi ({len(conversation_context)} karakter)")
            
            # Kullanıcı sorusunu history'ye kaydet
            self._save_to_history(session_id, "Human", question)
            
            # FastAgent'i doğru şekilde kullan
            if not self.fast_agent_app:
                self.fast_agent_app = create_fast_agent_app(self.model)
            
            async with self.fast_agent_app.run() as agent:
                # Enhanced question ile sorguyu gönder
                response = await agent.query_analyser.send(enhanced_question)
                
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
                self._save_to_history(session_id, "AI", streamed_content.strip())
                
                # Tamamlanma durumu
                yield {
                    'type': 'complete',
                    'message': streamed_content.strip(),
                    'status': 'finished',
                    'session_id': session_id,
                    'info': {
                        'agent_type': 'fast_agent_simplified',
                        'model': self.model,
                        'total_tokens': len(words),
                        'chain_used': 'query_analyser',
                        'has_conversation_context': bool(conversation_context),
                        'history_messages_count': len(self.conversation_histories.get(session_id, []))
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

async def get_or_create_fast_agent(model: str = "gpt-5-mini", graph=None) -> FastAgentIntegration:
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
    model: str = "gpt-5-mini", 
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
        agent = await get_or_create_fast_agent(model, graph)
        
        # Conversation history özellikli streaming
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