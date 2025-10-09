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
    Sen graph veritabanından ilgili tooları kullanarak sorulan soruya ait kayıtları bulmaya çalışan bir ajansın.
    
    Soruya cevap bulabilmek için önce şemada keşif yapmalısın. Çok uzun sonuçlar dönüp max token limitine takılabileceğin için sonuçların olabilidiğince limitli olmasına uğraş.
    
    **STRING NORMALİZASYON**: Execute queries exactly as reasoner provides:
   ```cypher
   toLower(apoc.text.clean(field)) CONTAINS toLower(apoc.text.clean('value'))
   ```
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
            "Ayça hanımın 2020 d5 konut poliçesinin primi ne kadar?"
        )


# alternative syntax for above is result = agent["post_writer"].send(message)
# alternative syntax for above is result = agent["post_writer"].prompt()


if __name__ == "__main__":
    asyncio.run(main())
