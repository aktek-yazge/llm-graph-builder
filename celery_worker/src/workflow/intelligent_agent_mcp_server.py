#!/usr/bin/env python3
"""
Intelligent Agent MCP Server
Bu server intelligent_agent'ın 2 tool'unu MCP protokolü üzerinden sunar.
"""

import os
import sys
import json
from typing import Dict, Any

from mcp.server.fastmcp import FastMCP

# Add backend path for imports
sys.path.append(os.path.join(os.path.dirname(__file__), "..", ".."))
from src.intelligent_agent import IntelligentAgent
from langchain_neo4j import Neo4jGraph
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Create FastMCP server
app = FastMCP(name="Intelligent Agent Tools")

# Setup Neo4j connection
def setup_neo4j_graph():
    """Neo4j bağlantısını kur"""
    NEO4J_URI = os.getenv("NEO4J_URI", "bolt://neo4j:7687")
    NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
    NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "langchain")
    NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")
    
    return Neo4jGraph(
        url=NEO4J_URI,
        username=NEO4J_USERNAME,
        password=NEO4J_PASSWORD,
        database=NEO4J_DATABASE,
        refresh_schema=False
    )

# Initialize intelligent agent instance with Neo4j graph
graph = setup_neo4j_graph()
intelligent_agent = IntelligentAgent(graph)

@app.tool(
    name="generate_embeddings_for_cypher",
    description="Generate embeddings for semantic search in Cypher queries. Use ONLY content keywords, no metadata!",
)
def generate_embeddings_for_cypher(text: str) -> Dict[str, Any]:
    """Generate embeddings for text to use in Cypher semantic search
    
    Args:
        text: Content keywords only (no customer names, years, policy types)
        
    Returns:
        Dict with success status, text, embedding_length, and message
    """
    try:
        success, embedding = intelligent_agent.generate_embeddings_for_cypher(text)
        return {
            "success": success,
            "text": text,
            "embedding_length": len(embedding) if success else 0,
            "message": "Embedding generated successfully" if success else str(embedding)
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "text": text
        }

@app.tool(
    name="add_page_resource", 
    description="Add page image resources from chunks to resource list. Mandatory for chunk results!",
)
def add_page_resource(page_link: str) -> Dict[str, Any]:
    """Add page resource to the intelligent agent's resource manager
    
    Args:
        page_link: Page image link from Cypher query results
        
    Returns:
        Dict with success status, page_link, and message
    """
    try:
        result = intelligent_agent.add_page_resource(page_link)
        return {
            "success": True,
            "page_link": page_link,
            "message": "Page resource added successfully",
            "result": result
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "page_link": page_link
        }

@app.tool(
    name="execute_cypher_query",
    description="Execute Cypher queries on Neo4j database. Handles embedding vectors automatically if query contains $embedding_vector parameter.",
)
def execute_cypher_query(query: str) -> Dict[str, Any]:
    """Execute Cypher query on Neo4j database
    
    Args:
        query: Cypher query string (can include $embedding_vector parameter)
        
    Returns:
        Dict with success status, result count, and query results
    """
    try:
        success, result = intelligent_agent.execute_cypher_query(query)
        
        if success:
            return {
                "success": True,
                "query": query,
                "result_count": len(result) if result else 0,
                "results": result,
                "message": f"Query executed successfully. Found {len(result) if result else 0} results."
            }
        else:
            return {
                "success": False,
                "query": query,
                "error": str(result),
                "message": "Query execution failed"
            }
    except Exception as e:
        return {
            "success": False,
            "query": query,
            "error": str(e),
            "message": "Exception during query execution"
        }

# Add resource for intelligent agent capabilities
@app.resource("resource://intelligent_agent/capabilities")
def intelligent_agent_capabilities() -> str:
    """Return intelligent agent capabilities and usage guide"""
    return """# Intelligent Agent Capabilities

## Available Tools:

### generate_embeddings_for_cypher
- **Purpose**: Generate embeddings for semantic search in Cypher queries
- **Input**: Content keywords only (no metadata like names, years, types)
- **Example**: "insurance policy details" ✅, "john doe 2020 policy" ❌
- **Usage**: Call before running semantic Cypher queries with vector similarity

### execute_cypher_query
- **Purpose**: Execute Cypher queries on Neo4j database
- **Input**: Cypher query string (supports $embedding_vector parameter)
- **Usage**: Run queries to find entities, relationships, and data
- **Result**: Returns query results with success status and result count

### add_page_resource  
- **Purpose**: Add page references from chunk results to resource manager
- **Input**: page_link from Cypher query results
- **Usage**: Mandatory when working with chunk data that has page references
- **Result**: Page images become available as resources for final answers

## ReAct Integration:
- Observer: Can query database schema and current state via execute_cypher_query
- Reasoner: Can analyze results and plan semantic searches with embeddings
- Executor: Can generate embeddings, execute queries, and add page resources

## Test Case:
Question: "Ayça Hanım'ın D5 poliçesinin primi ne kadar?"
Strategy:
1. Use execute_cypher_query to find Ayça Hanım's entities
2. Look for D5 policy information
3. Extract premium (prim) information from results
4. Use add_page_resource for any page references found

## Best Practices:
1. Start with entity queries to find specific persons/policies
2. Use generate_embeddings_for_cypher for content-based searches
3. Always use add_page_resource when chunk results have page links
4. Content keywords should be domain-agnostic and semantic
5. Page resources enhance final answer quality with visual references
"""

if __name__ == "__main__":
    # Run the MCP server
    app.run()