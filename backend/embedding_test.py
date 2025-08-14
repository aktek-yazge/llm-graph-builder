#!/usr/bin/env python3
"""
Embedding sisteminin çalışıp çalışmadığını test eden script
"""

import os
import sys
from neo4j import GraphDatabase

# Neo4j bağlantı bilgileri
NEO4J_URI = os.getenv('NEO4J_URI', 'neo4j://localhost:7687')
NEO4J_USER = os.getenv('NEO4J_USERNAME', 'neo4j')  # Backend'de NEO4J_USERNAME kullanılıyor
NEO4J_PASSWORD = os.getenv('NEO4J_PASSWORD', 'qwerty5555')

def test_embedding_system():
    """Embedding sistemini test et"""
    
    # Neo4j'ye bağlan
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    
    with driver.session() as session:
        
        print("=== EMBEDDING SİSTEMİ TEST RAPORU ===\n")
        
        # 1. Toplam chunk sayısı
        result = session.run("MATCH (c:Chunk) RETURN count(c) AS total_chunks")
        total_chunks = result.single()["total_chunks"]
        print(f"1. Toplam Chunk Sayısı: {total_chunks}")
        
        # 2. Embedding'li chunk sayısı
        result = session.run("MATCH (c:Chunk) WHERE c.embedding IS NOT NULL RETURN count(c) AS chunks_with_embedding")
        chunks_with_embedding = result.single()["chunks_with_embedding"] 
        print(f"2. Embedding'li Chunk Sayısı: {chunks_with_embedding}")
        
        # 3. Embedding boyutu kontrol
        result = session.run("MATCH (c:Chunk) WHERE c.embedding IS NOT NULL RETURN size(c.embedding) AS embedding_size LIMIT 1")
        record = result.single()
        embedding_size = record["embedding_size"] if record else 0
        print(f"3. Embedding Boyutu: {embedding_size}")
        
        # 4. Sample chunk text ve embedding değerleri
        result = session.run("""
            MATCH (c:Chunk) 
            WHERE c.embedding IS NOT NULL 
            RETURN c.text[0..100] AS sample_text, c.embedding[0..3] AS sample_embedding 
            LIMIT 1
        """)
        record = result.single()
        if record:
            print(f"4. Örnek Chunk Text: {record['sample_text'][:50]}...")
            print(f"5. Örnek Embedding Değerleri: {record['sample_embedding']}")
        
        # 5. Document-chunk ilişkilerini kontrol et
        result = session.run("""
            MATCH (d:Document)<-[:PART_OF]-(c:Chunk)
            WHERE c.embedding IS NOT NULL
            RETURN d.fileName, count(c) AS chunk_count
            ORDER BY chunk_count DESC
            LIMIT 5
        """)
        print(f"\n6. Dokümanlara göre embedding'li chunk dağılımı:")
        for record in result:
            print(f"   - {record['d.fileName']}: {record['chunk_count']} chunk")
        
        # 6. Vector similarity test - rastgele iki chunk arası similarity
        result = session.run("""
            MATCH (c1:Chunk), (c2:Chunk)
            WHERE c1.embedding IS NOT NULL AND c2.embedding IS NOT NULL
            AND elementId(c1) < elementId(c2)
            WITH c1, c2, vector.similarity.cosine(c1.embedding, c2.embedding) AS similarity
            RETURN min(similarity) AS min_sim, max(similarity) AS max_sim, avg(similarity) AS avg_sim
        """)
        record = result.single()
        if record:
            print(f"\n7. Similarity İstatistikleri:")
            print(f"   - Minimum Similarity: {record['min_sim']:.4f}")
            print(f"   - Maksimum Similarity: {record['max_sim']:.4f}") 
            print(f"   - Ortalama Similarity: {record['avg_sim']:.4f}")
        
        # 7. Threshold test - farklı threshold'larda kaç chunk eşleşir
        print(f"\n8. Threshold Testleri (rastgele bir chunk ile):")
        result = session.run("""
            MATCH (c1:Chunk) 
            WHERE c1.embedding IS NOT NULL
            WITH c1 LIMIT 1
            MATCH (c2:Chunk) 
            WHERE c2.embedding IS NOT NULL AND c2 <> c1
            WITH c1, c2, vector.similarity.cosine(c1.embedding, c2.embedding) AS score
            RETURN 
                count(CASE WHEN score > 0.1 THEN 1 END) AS threshold_01,
                count(CASE WHEN score > 0.3 THEN 1 END) AS threshold_03,
                count(CASE WHEN score > 0.5 THEN 1 END) AS threshold_05
        """)
        record = result.single()
        if record:
            print(f"   - Threshold 0.1: {record['threshold_01']} chunk")
            print(f"   - Threshold 0.3: {record['threshold_03']} chunk") 
            print(f"   - Threshold 0.5: {record['threshold_05']} chunk")
            
            # Eğer threshold 0.1'de çok fazla sonuç varsa uyarı ver
            if record['threshold_01'] > total_chunks * 0.8:
                print(f"   ⚠️  UYARI: Threshold 0.1 çok düşük olabilir!")
                print(f"   💡 ÖNERİ: Threshold'u 0.3 veya daha yükseğe çıkarın.")
        
        print(f"\n=== TEST TAMAMLANDI ===")
        
        # Sonuç değerlendirmesi
        print(f"\n=== DEĞERLENDİRME ===")
        if chunks_with_embedding == 0:
            print("❌ Hiçbir chunk'ta embedding yok! Embedding oluşturma işlemi çalışmamış.")
        elif chunks_with_embedding < total_chunks:
            print(f"⚠️  {total_chunks - chunks_with_embedding} chunk'ta embedding eksik!")
        else:
            print("✅ Tüm chunk'larda embedding mevcut.")
            
        if embedding_size != 1536:
            print(f"⚠️  Embedding boyutu beklenen değil! Beklenen: 1536, Mevcut: {embedding_size}")
        else:
            print("✅ Embedding boyutu doğru (1536).")
    
    driver.close()

if __name__ == "__main__":
    test_embedding_system()
