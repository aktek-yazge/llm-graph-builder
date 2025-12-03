"""
Neo4j Intelligence Agent as MCP Server

Bu modül, Neo4j veritabanı sorguları için FastAgent'ı MCP server olarak çalıştırır.
"""

import asyncio
import logging
from typing import Optional, List, Dict, Any
from pydantic import BaseModel
from fast_agent.core.fastagent import FastAgent
from fast_agent import RequestParams

# Logging ayarları
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Structured output modeli
class QueryResult(BaseModel):
    """Cypher sorgu sonucu için structured model"""
    answer: str
    cypher_query: Optional[str] = None
    result_count: Optional[int] = None
    raw_data: Optional[List[Dict[str, Any]]] = None

fast = FastAgent("Neo4j Intelligence Agent")

# Define the agent
@fast.agent(
    instruction="""
    Sen Dinkal Sigortaya ait poliçeler hakkında sorulan sorulara cevap veren bir ajansın. 
        
    Neo4j veritabanı şema bilgisi prompt'a eklenmiştir. Bu şema bilgisini kullanarak tool çağrıları yap.
    
    **STRING NORMALİZASYON**: String karşılaştırmalarında sadece toLower() kullan (apoc.text.clean KULLANMA - yanlış eşleşmelere sebep olur!):
    - ✅ Doğru: toLower(field) CONTAINS toLower('value')
    - ❌ Yanlış: apoc.text.clean() - boşlukları kaldırır ve yanlış substring eşleşmelerine sebep olur
    
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
    
    If the tool call gives a valid or complete result,
    return that result directly as the final output without generating any text.
    Do not describe or explain the result.
    """,
    servers=["neo4j-database", "embedding"],
    request_params=RequestParams(
        max_iterations=15,  # Daha az iteration
    ),
    use_history=True,     # History'yi kapatıyoruz
    model="gpt-5-mini.low",   # Daha hızlı model
    
)
async def main():
    # Start as a server programmatically
    await fast.start_server(
        transport="http",  # SSE yerine streamable-http kullanıyoruz
        host="0.0.0.0",
        port=8011,
        server_name="neo4j_intelligence_agent",
        server_description="Provides Neo4j database query capabilities for Dinkal Sigorta policies"
    )

if __name__ == "__main__":
    asyncio.run(main())