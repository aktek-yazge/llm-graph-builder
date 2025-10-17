"""
Neo4j Intelligence Agent as MCP Server

Bu modül, Neo4j veritabanı sorguları için FastAgent'ı MCP server olarak çalıştırır.
"""

import asyncio
import logging
from fast_agent import FastAgent, RequestParams, Prompt

# Logging ayarları
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create the application
fast = FastAgent("Neo4j Intelligence Agent")

# Define the agent
@fast.agent(
    name="neo4j_intelligence", 
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
    servers=["neo4j-database", "embedding"],
    request_params=RequestParams(max_iterations=15),
    use_history=True,
    model="gpt-5-mini.low",
)
async def main():
    async with fast.run() as agent:
        # If run as a server, it will listen for requests.
        # You can add interactive() here for local testing/debugging if needed,
        # but it won't be active when run as a server for the router.
        # await agent.interactive()
        pass  # The agent will wait for incoming MCP messages when run as a server

if __name__ == "__main__":
    # Start this agent as an MCP server
    asyncio.run(fast.start_server(
        transport="http",  # Or "sse"
        host="0.0.0.0",
        port=8011,  # Farklı port kullanıyorum
        server_name="neo4j_intelligence_agent",
        server_description="Provides Neo4j database query capabilities for Dinkal Sigorta policies"
    ))