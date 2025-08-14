#!/usr/bin/env python3
"""
CustomNeo4jVector parametre loglarını test etmek için
"""

import os
import sys
sys.path.append('/Users/mehmeterdogan/python-projects/llm-graph-builder/backend/src')

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_neo4j import Neo4jGraph
from custom_neo4j_vector import CustomNeo4jVector
from shared.constants import VECTOR_GRAPH_SEARCH_QUERY

# Load environment variables
load_dotenv()

def test_custom_neo4j_vector_params():
    """CustomNeo4jVector'daki parametre loglarını test et"""
    
    print("🧪 CUSTOM NEO4J VECTOR PARAMETRE TEST BAŞLATILIYOR...")
    
    # 1. Bağlantıları kur
    print("\n1. Bağlantılar kuruluyor...")
    
    # OpenAI LLM
    llm = ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0,
        openai_api_key=os.getenv('OPENAI_API_KEY')
    )
    print("✅ OpenAI LLM hazır")
    
    # OpenAI Embeddings
    embeddings = OpenAIEmbeddings(
        model="text-embedding-ada-002",
        openai_api_key=os.getenv('OPENAI_API_KEY')
    )
    print("✅ OpenAI Embeddings hazır")
    
    # Neo4j Graph
    graph = Neo4jGraph(
        url=os.getenv('NEO4J_URI', 'neo4j://localhost:7687'),
        username=os.getenv('NEO4J_USERNAME', 'neo4j'),
        password=os.getenv('NEO4J_PASSWORD', 'qwerty5555')
    )
    print("✅ Neo4j Graph bağlantısı hazır")
    
    # 2. CustomNeo4jVector oluştur
    print("\n2. CustomNeo4jVector oluşturuluyor...")
    
    try:
        vector_store = CustomNeo4jVector.from_existing_graph_with_llm(
            embedding=embeddings,
            node_label="Chunk",
            embedding_node_property="embedding",
            text_node_properties=["text"],
            llm=llm,
            graph=graph,
            retrieval_query=VECTOR_GRAPH_SEARCH_QUERY  # Constants'tan al
        )
        print("✅ CustomNeo4jVector oluşturuldu")
        
    except Exception as e:
        print(f"❌ CustomNeo4jVector oluşturma hatası: {e}")
        return
    
    # 3. Test sorgusu çalıştır
    print("\n3. Test sorgusu çalıştırılıyor...")
    print("=" * 80)
    print("🔍 TEST SORGUSU: 'ayça hanımın 2020 yılı D4 konut projesinin taksitleri ne kadar'")
    print("=" * 80)
    
    test_query = "ayça hanımın 2020 yılı D4 konut projesinin taksitleri ne kadar"
    
    try:
        # Bu çağrı custom_neo4j_vector.py'deki tüm parametre loglarını tetikleyecek
        results = vector_store.similarity_search(
            query=test_query,
            k=5
        )
        
        print("\n4. TEST SONUÇLARI:")
        print("=" * 80)
        print(f"📊 Toplam Sonuç: {len(results)}")
        
        for i, doc in enumerate(results, 1):
            print(f"\n📄 Sonuç {i}:")
            print(f"   Method: {doc.metadata.get('retrieval_method', 'N/A')}")
            print(f"   Score: {doc.metadata.get('combined_score', 'N/A')}")
            print(f"   Source: {doc.metadata.get('source', 'N/A')}")
            print(f"   Content (ilk 150 char): {doc.page_content[:150]}...")
            
        print("=" * 80)
        print("✅ Test tamamlandı!")
        
    except Exception as e:
        print(f"❌ Test sorgusu hatası: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_custom_neo4j_vector_params()
