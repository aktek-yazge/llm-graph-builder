#!/usr/bin/env python3
"""
Test CV matching action in ReAct format
"""

import sys
import os
sys.path.append('/workspace/backend')

from dotenv import load_dotenv
load_dotenv()

from src.intelligent_agent import IntelligentAgent
import logging

# Logging yapılandırması
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

def test_cv_matching_action():
    """Test match_cvs action through ReAct format"""
    
    try:
        # Neo4j connection
        from langchain_neo4j import Neo4jGraph
        
        # Neo4j connection parametreleri - environment'dan al
        NEO4J_URI = os.getenv("NEO4J_URI")
        NEO4J_USER = os.getenv("NEO4J_USERNAME")
        NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
        
        if not all([NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD]):
            raise ValueError("Neo4j environment variables (NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD) are not set!")
        
        # Neo4j Graph bağlantısı oluştur
        graph = Neo4jGraph(
            url=NEO4J_URI,
            username=NEO4J_USER,
            password=NEO4J_PASSWORD
        )
        
        # Agent'ı oluştur
        agent = IntelligentAgent(graph=graph)
        
        print("🔥 Test: CV Matching Action through ReAct")
        print("=" * 50)
        
        # İş ilanı soru
        job_question = """
        Yazılım geliştirici pozisyonu için aday arıyoruz. 
        Gereksinimler:
        - Python programlama
        - 3+ yıl deneyim
        - İngilizce bilgisi
        - Veri analizi becerisi
        
        Bu kriterlere uygun adayları listele.
        """
        
        print(f"📝 Test Sorusu: {job_question}")
        print("\n" + "="*50)
        
        # Agent'ın soruyu çözmesini bekle
        result = agent.solve_question(job_question, session_id="test_cv_action")
        
        print("🎯 SONUÇ:")
        print("=" * 30)
        
        print(f"📊 Result keys: {list(result.keys())}")
        
        if "final_answer" in result:
            print(f"📋 Final Answer: {result['final_answer']}")
            print(f"🔄 İterasyonlar: {result['iterations']}")
            print(f"🧩 Chunk Sayısı: {result['discovered_chunks']}")
            print(f"📊 Token Kullanımı: {result['token_usage']}")
            
            # Chunk detayları
            if result.get('chunk_details'):
                print(f"\n📚 Chunk Detayları:")
                for i, chunk in enumerate(result['chunk_details'][:3], 1):
                    print(f"  {i}. {chunk['document']} (Sayfa {chunk['page']}) - Relevance: {chunk['relevance']:.3f}")
                    print(f"     Preview: {chunk['preview']}")
        else:
            print("❌ Final answer bulunamadı!")
            print(f"🔄 İterasyonlar: {result.get('iterations', 'N/A')}")
            print(f"🧩 Bulunan Chunk: {result.get('discovered_chunks', 'N/A')}")
            
            if result.get('successful_findings'):
                print(f"\n✅ Başarılı Bulgular:")
                for finding in result['successful_findings']:
                    print(f"  - İterasyon {finding['iteration']}: {finding['finding']}")
            
            if result.get('conversation_history'):
                print(f"\n📜 Conversation History:")
                for i, entry in enumerate(result.get('conversation_history', [])[-3:], 1):  # Son 3 entry
                    print(f"  {i}. {entry}")
        
        return result
        
    except Exception as e:
        print(f"❌ Test hatası: {e}")
        import traceback
        traceback.print_exc()
        return None

if __name__ == "__main__":
    test_cv_matching_action()