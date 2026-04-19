#!/usr/bin/env python3
"""
Enhanced Intelligent Agent Test
Token tracking ve context memory özelliklerini test eder
"""

# Test environment setup
from tests.test_setup import *

from src.intelligent_agent import IntelligentAgent
from langchain_neo4j import Neo4jGraph

def main():
    print("🚀 Enhanced Intelligent Agent Test Başlatılıyor...")
    print("=" * 60)
    
    try:
        # Neo4j bağlantısını kur
        graph = Neo4jGraph(
            url=os.getenv('NEO4J_URI', 'bolt://localhost:7687'),
            username=os.getenv('NEO4J_USERNAME', 'neo4j'),
            password=os.getenv('NEO4J_PASSWORD', 'qwerty5555'),
            database=os.getenv('NEO4J_DATABASE', 'neo4j')
        )
        
        # Agent'ı başlat
        agent = IntelligentAgent(graph)
        
        # Test soruları
        test_questions = [
            "Ayça hanımın kaç adet poliçesi var?",
            "DASK poliçeleri hakkında bilgi ver"
        ]
        
        for i, query in enumerate(test_questions, 1):
            print(f"\n🔍 TEST {i}: {query}")
            print("-" * 50)
            
            result = agent.solve_question(query)
            
            # Sonuçları göster
            print(f"📋 CEVAP: {result['answer'][:150]}...")
            print(f"🔄 İTERASYON: {result['iterations']}")
            
            # Token kullanımı
            token_usage = result.get('token_usage', {})
            print(f"📊 TOKEN KULLANIMI:")
            print(f"   ├─ Input Tokens: {token_usage.get('input_tokens', 0)}")
            print(f"   ├─ Output Tokens: {token_usage.get('output_tokens', 0)}")
            print(f"   └─ Total Tokens: {token_usage.get('total_tokens', 0)}")
            
            # Başarılı bulgular
            findings = result.get('successful_findings', [])
            print(f"🎯 BAŞARILI BULGULAR ({len(findings)} adet):")
            for finding in findings:
                print(f"   ├─ Adım {finding['iteration']}: {finding['action']}")
                print(f"   │  └─ {finding['finding'][:60]}... (Score: {finding['relevance_score']:.3f})")
            
            # Context memory
            context_memory = result.get('context_memory', '')
            print(f"🧠 CONTEXT MEMORY ({len(context_memory)} karakter):")
            if context_memory:
                print(f"   └─ {context_memory[:200]}...")
            else:
                print("   └─ Boş")
            
            # Chunk detayları
            chunks = result.get('chunk_details', [])
            print(f"📄 EN İLGİLİ CHUNK'LAR ({len(chunks)} adet):")
            for chunk in chunks[:3]:
                print(f"   ├─ {chunk['document']} (Sayfa {chunk['page']}) - Score: {chunk['relevance']:.3f}")
                print(f"   │  └─ {chunk['preview'][:50]}...")
            
            print(f"\n✅ Test {i} tamamlandı!")
            
    except Exception as e:
        print(f"❌ HATA: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
