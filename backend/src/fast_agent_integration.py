"""
FastAgent Integration Module for Chat Bot Stream

Bu modül FastAgent'i chat_bot_stream endpoint'ine entegre eder.
FastAgent workflow'unu streaming olarak çalıştırır.
"""

import asyncio
import json
import logging
import os
from typing import AsyncGenerator, Dict, Any, Optional
from datetime import datetime

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

def create_fast_agent_app(model: str = "gpt-4o-mini") -> FastAgent:
    """Model parametresi ile FastAgent uygulaması oluştur"""
    
    app = FastAgent("Neo4j Query Agent")

    @app.agent(
        "neo4j_intelligence",
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

    Sorudan çıkarım yaparak field tahminlemesi yapma. Db veri yapısını öğrenmek için soruyu tek kelimeli parçalara bölerek her seferinde bir odak kelimeyi aratarak limitli sorgular ile anlamaya çalış.
    
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
        servers=["neo4j-database", "embedding"],
        request_params=RequestParams(
            max_iterations=15,
            # temperature=0.3,
            maxTokens=4096
        ),
        use_history=True,
        model="gpt-5-mini.low",
    )

    @app.chain(
        name="insurance_query_chain",
        sequence=["neo4j_intelligence"],
        instruction="Sigorta poliçeleri hakkında soru-cevap zinciri"
    )
    async def main():
        """Placeholder main function"""
        pass
    
    return app

class FastAgentIntegration:
    """FastAgent'i chat_bot_stream'e entegre eden sınıf"""
    
    def __init__(self, model: str = "gpt-4o-mini"):
        self.model = model
        self.agent_instance = None
        self.fast_agent_app = None
        
    async def initialize_agent(self) -> None:
        """FastAgent'i başlat"""
        try:
            if not self.agent_instance:
                # Model'e özgü FastAgent app'i oluştur
                self.fast_agent_app = create_fast_agent_app(self.model)
                # FastAgent'in doğru async context manager kullanımı
                self.agent_instance = await self.fast_agent_app.run().__aenter__()
                logging.info(f"FastAgent initialized with model: {self.model}")
        except Exception as e:
            logging.error(f"FastAgent initialization failed: {e}")
            raise
    
    async def cleanup_agent(self) -> None:
        """FastAgent'i temizle"""
        try:
            if self.agent_instance and self.fast_agent_app:
                await self.fast_agent_app.run().__aexit__(None, None, None)
                self.agent_instance = None
                self.fast_agent_app = None
                logging.info("FastAgent cleaned up")
        except Exception as e:
            logging.error(f"FastAgent cleanup failed: {e}")
    
    async def stream_query_response(
        self,
        question: str,
        session_id: str = None,
        **kwargs
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        FastAgent kullanarak streaming cevap üret
        
        Args:
            question: Kullanıcının sorusu
            session_id: Oturum ID'si
            **kwargs: Ek parametreler
            
        Yields:
            Dict: Streaming chunk'ları
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
            
            # FastAgent'i doğru şekilde kullan
            if not self.fast_agent_app:
                self.fast_agent_app = create_fast_agent_app(self.model)
            
            async with self.fast_agent_app.run() as agent:
                # Prompt'u hazırla
                user_prompt = Prompt.user(f"""
                Kullanıcı Sorusu: {question}
                
                Oturum ID: {session_id or 'unknown'}
                
                Lütfen bu soruyu Neo4j veritabanını kullanarak cevaplayın.
                """)
                
                # FastAgent'in generate metodunu kullan
                response = await agent.insurance_query_chain.generate([user_prompt])
                
                # Cevabı parçalayıp stream et
                full_response = response.last_text()
                
                # Kelime kelime streaming simülasyonu
                words = full_response.split()
                streamed_content = ""
                
                for i, word in enumerate(words):
                    streamed_content += word + " "
                    
                    # Her birkaç kelimede bir chunk gönder
                    if i % 3 == 0 or i == len(words) - 1:
                        yield {
                            'type': 'token',
                            'content': word + " ",
                            'full_message': streamed_content.strip(),
                            'session_id': session_id,
                            'timestamp': datetime.now().isoformat()
                        }
                        
                        # Gerçekçi streaming gecikmesi
                        await asyncio.sleep(0.05)
                
                # Tamamlanma durumu
                yield {
                    'type': 'complete',
                    'message': streamed_content.strip(),
                    'status': 'finished',
                    'session_id': session_id,
                    'info': {
                        'agent_type': 'fast_agent',
                        'model': self.model,
                        'total_tokens': len(words),  # Basit token tahmini
                        'chain_used': 'insurance_query_chain'
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
    
    async def stream_with_mcp_tools(
        self,
        question: str,
        session_id: str = None,
        **kwargs
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        MCP araçlarıyla birlikte streaming cevap üret
        """
        try:
            yield {
                'type': 'status',
                'message': 'FastAgent + MCP araçları ile analiz ediliyor...',
                'status': 'processing_with_tools',
                'session_id': session_id
            }
            
            # FastAgent'i doğru şekilde kullan
            if not self.fast_agent_app:
                self.fast_agent_app = create_fast_agent_app(self.model)
            
            async with self.fast_agent_app.run() as agent:
                # Daha detaylı bir prompt
                detailed_prompt = Prompt.user(f"""
                DETAYLI ANALIZ GEREKLİ:
                
                Soru: {question}
                Oturum: {session_id}
                
                1. Önce Neo4j şemasını incele
                2. İlgili SavedAnswer'ları kontrol et  
                3. Gerekirse Chunk nodlarında arama yap
                4. Sonuçları analiz et ve kapsamlı cevap ver
                
                MCP araçlarını etkin şekilde kullan.
                """)
                
                # Agent'ten cevap al
                response = await agent.neo4j_intelligence.generate([detailed_prompt])
            
            # Tool call bilgilerini kontrol et
            tool_calls_used = []
            if hasattr(response, 'content'):
                for content_part in response.content:
                    if hasattr(content_part, 'type') and content_part.type == 'tool_use':
                        tool_calls_used.append({
                            'tool_name': getattr(content_part, 'name', 'unknown'),
                            'tool_id': getattr(content_part, 'id', 'unknown')
                        })
            
            # Cevabı stream et
            full_text = response.last_text()
            words = full_text.split()
            streamed_content = ""
            
            for i, word in enumerate(words):
                streamed_content += word + " "
                
                if i % 4 == 0 or i == len(words) - 1:
                    yield {
                        'type': 'token',
                        'content': word + " ",
                        'full_message': streamed_content.strip(),
                        'session_id': session_id,
                        'tools_used': tool_calls_used if i == len(words) - 1 else [],
                        'timestamp': datetime.now().isoformat()
                    }
                    
                    await asyncio.sleep(0.08)
            
            yield {
                'type': 'complete',
                'message': streamed_content.strip(),
                'status': 'finished',
                'session_id': session_id,
                'info': {
                    'agent_type': 'fast_agent_with_mcp',
                    'model': self.model,
                    'tools_used': tool_calls_used,
                    'total_tokens': len(words)
                },
                'timestamp': datetime.now().isoformat()
            }
            
        except Exception as e:
            error_message = f"FastAgent MCP error: {str(e)}"
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

async def get_or_create_fast_agent(model: str = "gpt-4o-mini") -> FastAgentIntegration:
    """Global FastAgent instance'ını al veya oluştur"""
    global _global_fast_agent
    
    if _global_fast_agent is None or _global_fast_agent.model != model:
        if _global_fast_agent:
            await _global_fast_agent.cleanup_agent()
        
        _global_fast_agent = FastAgentIntegration(model=model)
        await _global_fast_agent.initialize_agent()
    
    return _global_fast_agent

async def stream_fast_agent_response(
    question: str,
    model: str = "gpt-4o-mini", 
    session_id: str = None,
    use_mcp_tools: bool = True,
    **kwargs
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    FastAgent kullanarak streaming cevap üret - Ana fonksiyon
    
    Args:
        question: Kullanıcının sorusu
        model: Kullanılacak LLM modeli
        session_id: Oturum ID'si
        use_mcp_tools: MCP araçlarını kullan
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
        # FastAgent instance'ını al
        agent = await get_or_create_fast_agent(model)
        
        # MCP araçları ile mi yoksa basit mi?
        if use_mcp_tools:
            async for chunk in agent.stream_with_mcp_tools(
                question=question,
                session_id=session_id,
                **kwargs
            ):
                yield chunk
        else:
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