#!/usr/bin/env python3
"""
Test match_cvs action specifically by asking for job posting CV matching
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

def test_match_cvs_action_directly():
    """Test match_cvs action by explicitly asking for it"""
    
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
        
        print("🔥 Test: match_cvs Action Directly")
        print("=" * 50)
        
        # İş ilanı ile CV eşleştirme sorusu - match_cvs action kullanmasını öneren bir soru
        job_question = """
        Şu iş ilanına uygun CV'leri match_cvs action kullanarak bul:
        
        İş İlanı:
        Senior Python Developer
        - 5+ years experience in Python
        - Data science and machine learning skills
        - English proficiency required
        - Flask/Django framework knowledge
        - SQL and database skills
        
        Bu iş ilanı için uygun adayları match_cvs action ile eşleştir.
        """
        
        print(f"📝 Test Sorusu: {job_question}")
        print("\n" + "="*50)
        
        # Agent'ın soruyu çözmesini bekle
        result = agent.solve_question(job_question, session_id="test_match_cvs_direct")
        
        print("🎯 SONUÇ:")
        print("=" * 30)
        
        print(f"📊 Result keys: {list(result.keys())}")
        
        # Match type kontrolü
        if result.get("match_type") == "cv_matching_action_result":
            print("✅ CV Matching Action Result döndü!")
            print(f"📋 Answer: {result.get('answer', 'N/A')}")
            print(f"🔄 İterasyonlar: {result.get('iterations', 'N/A')}")
            print(f"👥 Bulunan Adaylar: {len(result.get('matched_candidates', []))}")
            print(f"📊 Token Kullanımı: {result.get('token_usage', {})}")
            
            # Resource links
            resource_links = result.get('resource_links', [])
            if resource_links:
                print(f"\n📎 Resource Links ({len(resource_links)} adet):")
                for i, link in enumerate(resource_links[:3], 1):
                    print(f"  {i}. {link.get('title', 'N/A')}")
                    print(f"     URL: {link.get('url', 'N/A')}")
            
            # İş gereksinimleri
            job_reqs = result.get('job_requirements', {})
            if job_reqs:
                print(f"\n💼 İş Gereksinimleri:")
                for key, value in job_reqs.items():
                    print(f"  - {key}: {value}")
                    
        elif "final_answer" in result:
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
                    print(f"  - İterasyon {finding['iteration']} ({finding['action']}): {finding['finding']}")
            
            if result.get('conversation_history'):
                print(f"\n📜 Conversation History (son 3):")
                for i, entry in enumerate(result.get('conversation_history', [])[-3:], 1):
                    print(f"  {i}. {entry}")
        
        return result
        
    except Exception as e:
        print(f"❌ Test hatası: {e}")
        import traceback
        traceback.print_exc()
        return None

if __name__ == "__main__":
    test_match_cvs_action_directly()