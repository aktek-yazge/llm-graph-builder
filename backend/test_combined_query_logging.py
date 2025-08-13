#!/usr/bin/env python3
"""
Combined Query Logging Test
Bu test, LLM ve VECTOR_GRAPH_SEARCH_QUERY'nin nasıl birleştiğini ve 
tam query'nin nasıl loglandığını test eder.
"""

import os
import sys
sys.path.append('/Users/mehmeterdogan/python-projects/llm-graph-builder/backend/src')

from custom_neo4j_vector import CustomNeo4jVector
from langchain_openai import OpenAIEmbeddings
from langchain_neo4j import Neo4jGraph
from shared.constants import VECTOR_GRAPH_SEARCH_QUERY
from langchain_openai import ChatOpenAI

def test_combined_query_logging():
    """Test combined query generation and logging"""
    
    print("🚀 COMBINED QUERY LOGGING TEST BAŞLATILIYOR...")
    print("=" * 80)
    
    # Neo4j connection
    graph = Neo4jGraph(
        url="bolt://localhost:7687",
        username="neo4j", 
        password="password"
    )
    
    # Embeddings
    embeddings = OpenAIEmbeddings(
        model="text-embedding-3-small",
        openai_api_key=os.getenv("OPENAI_API_KEY")
    )
    
    # LLM
    llm = ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0,
        openai_api_key=os.getenv("OPENAI_API_KEY")
    )
    
    # CustomNeo4jVector oluştur
    vector_store = CustomNeo4jVector.from_existing_graph_with_llm(
        embedding=embeddings,
        graph=graph,
        llm=llm,
        node_label="Chunk",
        embedding_node_property="embedding",
        text_node_properties=["text"],
        retrieval_query=VECTOR_GRAPH_SEARCH_QUERY,
        index_name="vector"
    )
    
    print("✅ CustomNeo4jVector oluşturuldu")
    print(f"Retrieval Query Set: {hasattr(vector_store, 'retrieval_query') and vector_store.retrieval_query is not None}")
    
    # Test query
    test_query = "Ayça Dinçkök 2020 yılı poliçesi"
    
    print(f"\n🔍 Test Query: {test_query}")
    print("=" * 80)
    
    # Similarity search çalıştır (bu combined query'yi tetikleyecek)
    results = vector_store.similarity_search(
        query=test_query,
        k=5
    )
    
    print("=" * 80)
    print("🎯 SONUÇLAR:")
    print("=" * 80)
    print(f"Toplam Sonuç: {len(results)}")
    
    for i, doc in enumerate(results):
        print(f"\nSonuç {i+1}:")
        print(f"  Retrieval Method: {doc.metadata.get('retrieval_method', 'N/A')}")
        print(f"  Combined Score: {doc.metadata.get('combined_score', 'N/A')}")
        print(f"  Source: {doc.metadata.get('source', 'N/A')}")
        print(f"  Text Preview: {doc.page_content[:100]}...")

if __name__ == "__main__":
    test_combined_query_logging()
