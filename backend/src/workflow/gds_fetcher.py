"""
GDS (Graph Database Service) Fetcher Module

This module implements a FastAgent-based workflow for querying Neo4j graph databases.
It provides intelligent querying capabilities with string normalization and document
information extraction for insurance policy related queries.

The module contains:
- A neo4j_query agent that performs graph database queries with schema discovery
- A query_analyser chain that orchestrates the querying workflow
- Main execution function for processing insurance policy queries
"""

import asyncio

from fast_agent import FastAgent, RequestParams

# Create the application
fast = FastAgent("Agent Chaining")


@fast.agent(
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
    servers=["neo4j-database","embedding"],
    request_params=RequestParams(max_iterations=15,),
    use_history=True,  # keep conversation history
    # model="gpt-4o-mini",
    model="gpt-5",
    
)
# @fast.agent(
#     "analyser",
#     instruction="""
#     Sen cypher querysinden gelen sonuçları yorumlayan ve cevaplayan bir ajansın.
#     """,
#     request_params=RequestParams(temperature=0.3),
#     use_history=True,
# )
@fast.chain(
    name="query_analyser",
    sequence=["neo4j_query"]
    
)
async def main() -> None:
    """Execute the query_analyser chain workflow to process Neo4j queries."""
    async with fast.run() as agent:
        # using chain workflow
        await agent.query_analyser.send(
            # "SavedAnswer nodlarını sil"
            # "Ayça hanımın 2020 d2 konut poliçesinin takistleri ne kadar?"
            # "Birkan Akdoğan ın kayıtlarda kaç poliçesi var ?"
            # "34ERA50 NİN KASKO POLİÇESİ MEVCUT MU? 2022 ve 2023 te varmı"
            "amasyalı soy adı olan sigortalımız var mı? hangi poliçeleri var?"
            # "Ayça Dinçkök Çeşme Adresinde konut poliçesi mevcut mu?"
            # "2021-2022 kaç poliçe yapıldı?"
        )


# alternative syntax for above is result = agent["post_writer"].send(message)
# alternative syntax for above is result = agent["post_writer"].prompt()


if __name__ == "__main__":
    asyncio.run(main())
