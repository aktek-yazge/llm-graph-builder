#!/usr/bin/env python3
"""
CustomNeo4jVector test script - LLM çağrısının gerçekten yapıldığını test eder
"""

import os
import sys
sys.path.append('src')

# Environment değişkenlerini yükle
from dotenv import load_dotenv
load_dotenv()

from src.custom_neo4j_vector import CustomNeo4jVector
from src.shared.constants import EMBEDDING_FUNCTION
from langchain_neo4j import Neo4jGraph

def test_custom_vector():
    print("=" * 60)
    print("🧪 CUSTOM NEO4J VECTOR TEST BAŞLIYOR")
    print("=" * 60)
    
    try:
        # Environment kontrol
        print(f"NEO4J_URI: {os.getenv('NEO4J_URI')}")
        print(f"NEO4J_USERNAME: {os.getenv('NEO4J_USERNAME')}")
        print(f"NEO4J_DATABASE: {os.getenv('NEO4J_DATABASE')}")
        
        # Neo4j bağlantısı
        graph = Neo4jGraph(
            url=os.getenv("NEO4J_URI"),
            username=os.getenv("NEO4J_USERNAME"), 
            password=os.getenv("NEO4J_PASSWORD"),
            database=os.getenv("NEO4J_DATABASE")
        )
        print("✅ Neo4j bağlantısı başarılı")
        
        # LLM import et
        from src.llm import get_llm
        llm, model_name = get_llm(model="gpt-4o-mini")
        print(f"✅ LLM başlatıldı: {model_name}")
        
        # Custom vector store oluştur
        custom_vector = CustomNeo4jVector.from_existing_graph_with_llm(
            embedding=EMBEDDING_FUNCTION,
            index_name="vector",
            graph=graph,
            llm=llm,
            node_label="Chunk",
            embedding_node_property="embedding",
            text_node_properties=["text"]
        )
        print("✅ CustomNeo4jVector oluşturuldu")
        
        # Test sorgusu
        test_query = "Ayça hanımın 2020 yılına ait kaç poliçesi var?"
        print(f"\n🔍 Test Sorusu: {test_query}")
        
        # Similarity search çağır
        results = custom_vector.similarity_search(test_query, k=5)
        
        print(f"\n📊 SONUÇLAR:")
        print(f"Toplam Sonuç: {len(results)}")
        for i, doc in enumerate(results):
            print(f"  {i+1}. Type: {doc.metadata.get('retrieval_type', 'unknown')}")
            print(f"      Score: {doc.metadata.get('combined_score', 'N/A')}")
            print(f"      Content: {doc.page_content[:100]}...")
        
    except Exception as e:
        print(f"❌ Hata: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_custom_vector()
