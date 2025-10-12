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
    
    Bu bilgilere nasıl erişebileceğini bilmiyorsun. İlgili toollar sana yol gösterecek. Düşünmene gerek yok. Toolları kullan.

    İlk önce first SavedAnswer ve şema keşfi yapman herzaman en iyisisidr.
    
    SavedAnswer ilgili cevapları içerebilri. Eğer SavedAnswer de ilgili kayıt yok ise Keşif yapman her zaman iyidir. SavedAnswer araması bir sefer yeterlidir genelde.

    **STRING NORMALİZASYON**: Execute queries exactly as reasoner provides:
   ```cypher
   toLower(apoc.text.clean(field)) CONTAINS toLower(apoc.text.clean('value'))
   ```
   
   Eğer chunk araması yaptıysan ve chunklarda kesik veya eksik bilgi olabilir. Bir sonraki 2 chunka bakarak bu bilgiyi tamamlamaya çalış.

   Cevap verdiğin başarılı bilgiyi SavedAnswer ye kaydet.
   
""",
    servers=["neo4j-database"],
    # request_params=RequestParams(max_iterations=5),
    use_history=True,  # keep conversation history
    model="gpt-5-mini",
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
    sequence=["neo4j_query"],
)
async def main() -> None:
    """Execute the query_analyser chain workflow to process Neo4j queries."""
    async with fast.run() as agent:
        # using chain workflow
        await agent.query_analyser.send(
            "Ayça hanımın 2020 d2 konut poliçesinin takistleri ne kadar?"
        )


# alternative syntax for above is result = agent["post_writer"].send(message)
# alternative syntax for above is result = agent["post_writer"].prompt()


if __name__ == "__main__":
    asyncio.run(main())
