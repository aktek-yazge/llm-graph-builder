"""
FastAgent Integration Module for Chat Bot Stream - Simplified Version

Bu modül FastAgent'i chat_bot_stream endpoint'ine entegre eder.
gds_fetcher.py ile aynı basit yaklaşımı kullanır.
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
    """FastAgent'i chat_bot_stream'e entegre eden sınıf - Basitleştirilmiş"""
    
    def __init__(self, model: str = "gpt-5-mini"):
        self.model = model
        self.fast_agent_app = None
        
    async def stream_query_response(
        self,
        question: str,
        session_id: str = None,
        **kwargs
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        FastAgent kullanarak streaming cevap üret - gds_fetcher.py mantığı
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
            
            # FastAgent'i doğru şekilde kullan - gds_fetcher.py'daki gibi
            if not self.fast_agent_app:
                self.fast_agent_app = create_fast_agent_app(self.model)
            
            async with self.fast_agent_app.run() as agent:
                # gds_fetcher.py'daki gibi query_analyser chain'ini kullan
                response = await agent.query_analyser.send(question)
                
                # Cevabı parçalayıp stream et
                words = str(response).split()
                streamed_content = ""
                
                for i, word in enumerate(words):
                    streamed_content += word + " "
                    
                    # Her 3 kelimede bir chunk gönder
                    if i % 3 == 0 or i == len(words) - 1:
                        yield {
                            'type': 'token',
                            'content': word + " ",
                            'full_message': streamed_content.strip(),
                            'session_id': session_id,
                            'timestamp': datetime.now().isoformat()
                        }
                        
                        # Streaming gecikmesi
                        await asyncio.sleep(0.05)
                
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
                        'chain_used': 'query_analyser'
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

async def get_or_create_fast_agent(model: str = "gpt-5-mini") -> FastAgentIntegration:
    """Global FastAgent instance'ını al veya oluştur"""
    global _global_fast_agent
    
    if _global_fast_agent is None or _global_fast_agent.model != model:
        _global_fast_agent = FastAgentIntegration(model=model)
    
    return _global_fast_agent

async def stream_fast_agent_response(
    question: str,
    model: str = "gpt-5-mini", 
    session_id: str = None,
    **kwargs
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    FastAgent kullanarak streaming cevap üret - Ana fonksiyon (Basitleştirilmiş)
    
    Args:
        question: Kullanıcının sorusu
        model: Kullanılacak LLM modeli
        session_id: Oturum ID'si
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
        
        # Basit streaming - gds_fetcher.py mantığı
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