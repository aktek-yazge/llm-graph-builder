"""
This demonstrates creating multiple agents and an orchestrator to coordinate them.
"""

import asyncio

from fast_agent import FastAgent
# Create the application
fast = FastAgent("Orchestrator-Workers")


@fast.agent(
    "observer",
    instruction="""You are an OBSERVER agent - the 'Observation' phase of ReAct pattern.
    You explore the database based on the user's question and produce an output for the next agent.
    
    **MCP SERVER CALLING**:
   - neo4j-gds mcp server üzerindeki toolları kullanarak Graph db keşif yap.
        
Your ONLY responsibility is objective observation:
    - Report what data sources and tools EXISTS
    - Identify current database state and available resources
    - Graph database structure, node types, relationships
    

Focus on OBSERVING database, not making decisions.

""",
    servers=["neo4j-gds"],
    model="gpt-5-mini",
)
# Define worker agents
@fast.agent(
    name="reasoner",
    instruction="""You are a REASONER agent - You take the output of the observer agent, determine which strategy to use, and based on the following instructions, define the boundaries within which the executor agent can perform the graph search.
    
## MANDATORY CYPHER RULES (KRİTİK):
1. **STRING NORMALİZASYON ZORUNLU**: 
   ```cypher
   WHERE toLower(apoc.text.clean(field)) CONTAINS toLower(apoc.text.clean('value'))
   ```
   NEVER use direct CONTAINS without normalization!

2. **NO FULL NODE RETURNS**: Only return primitive properties (strings, numbers)
   ```cypher
   RETURN c.name, p.policyNumber  ✅
   RETURN p, c  ❌ (causes DateTime serialization errors)
   ```

3. **KEŞİF-FIRST APPROACH**: Always explore first, then filter
   - First: General discovery queries
   - Then: Specific targeted queries

## STRATEGY:
- **DISCOVERY PHASE**: Broad exploration first
- **ENTITY PHASE**: Customer/Policy identification  
- **CONTENT PHASE**: Document/Chunk search if needed

Plan queries following these rules EXACTLY as intelligent_agent does.""",
    servers=[ "intelligent_agent"],
    model="gpt-5-mini",
)
@fast.agent(
    name="executor",
    instruction="""You are an EXECUTOR agent - implementing intelligent_agent's TOOL EXECUTION RULES.
    
## CRITICAL ERROR HANDLING:
1. **DateTime Serialization**: If query fails with serialization error:
   - Modify query to return only primitive properties
   - Remove full node returns (p, c) → use (p.name, c.name)
   - Report error and suggest corrected query to reasoner

2. **STRING NORMALİZASYON**: Execute queries exactly as reasoner provides:
   ```cypher
   toLower(apoc.text.clean(field)) CONTAINS toLower(apoc.text.clean('value'))
   ```

3. **TOOL CALLING**:
   - generate_embeddings_for_cypher: Use ONLY content terms, no metadata
   - add_page_resource: Call for ANY page_link found
   - execute_cypher_query: Queries are executed through this tool
   
Execute tools and handle errors intelligently, providing feedback for strategy adjustment.""",
    servers=["intelligent_agent", "neo4j-gds"],
    model="gpt-5-mini",
)
# Define the 3-Agent ReAct iterative planner
@fast.iterative_planner(
    name="orchestrate",
    agents=["observer", "reasoner", "executor"],
    model="gpt-5-mini",
    plan_iterations=2,
    instruction="""You are a 3-Agent ReAct Pattern Orchestrator implementing clean Reason + Act cycles. 
    You should rewrite the user’s question for the retriever search by combining it with any previous related questions to make it more meaningful, and then select the appropriate agent.
    Try to find the answer to the question using the agents listed below.
    kullanılabilir agentları listele
    
    
    If the retrieved data already satisfies the user's question, you generate a user-friendly response and do not continue the iteration.

    The end-user message must be in Turkish.
    """,
)
async def main() -> None:
    async with fast.run() as agent:
        # await agent.author(
        #     "write a 250 word short story about kittens discovering a castle, and save it to short_story.md"
        # )

        # Test intelligent_agent integration with the same test question
        task = """Ayça Hanım'ın D5 poliçesinin primi ne kadar?"""

        await agent.orchestrate(task)


if __name__ == "__main__":
    asyncio.run(main())
