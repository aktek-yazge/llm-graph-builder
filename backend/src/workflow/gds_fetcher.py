import asyncio

from fast_agent import FastAgent, RequestParams

# Create the application
fast = FastAgent("Agent Chaining")


@fast.agent(
    "neo4j_query",
    instruction="""Sen graph veritabanından ilgili tooları kullanarak ilgili soruya ait kayıtları bulmaya çalışan bir ajansın.
    **STRING NORMALİZASYON**: Execute queries exactly as reasoner provides:
   ```cypher
   toLower(apoc.text.clean(field)) CONTAINS toLower(apoc.text.clean('value'))
   ```
   
   Elde ettiğin bilgilerde Chunk, Document ve Policy nodleraında belge bilgilieri var. Bunları cevabın sonuna ekle.
    """,
    servers=["neo4j-database"],
    request_params=RequestParams(max_iterations=3)
)
@fast.agent(
    "analyser",
    instruction="""
    Sen cypher querysinden gelen sonuçları yorumlayan ve cevaplayan bir ajansın.
    """,
    use_history=True,
)
@fast.chain(
    name="query_analyser",
    sequence=["neo4j_query", "analyser"],
)
async def main() -> None:
    async with fast.run() as agent:
        # using chain workflow
        await agent.query_analyser.send("Ayça hanımın 2020 d5 konut poliçesinin primi ne kadar?")


# alternative syntax for above is result = agent["post_writer"].send(message)
# alternative syntax for above is result = agent["post_writer"].prompt()


if __name__ == "__main__":
    asyncio.run(main())
