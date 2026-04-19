#!/usr/bin/env python3
"""
Önce APOC ile document filtrele, sonra embedding araması yap
"""

import os
import openai
from neo4j import GraphDatabase
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Neo4j bağlantı bilgileri
NEO4J_URI = os.getenv('NEO4J_URI', 'neo4j://localhost:7687')
NEO4J_USER = os.getenv('NEO4J_USERNAME', 'neo4j')
NEO4J_PASSWORD = os.getenv('NEO4J_PASSWORD', 'qwerty5555')

# OpenAI API key
openai.api_key = os.getenv('OPENAI_API_KEY')

def get_embedding(text):
    """OpenAI ile text embedding oluştur"""
    try:
        response = openai.embeddings.create(
            model="text-embedding-ada-002",
            input=text
        )
        return response.data[0].embedding
    except Exception as e:
        print(f"Embedding oluşturma hatası: {e}")
        return None

def filter_documents_with_apoc():
    """APOC ile önce document'ları filtrele"""
    
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    
    with driver.session() as session:
        # User'in istediği query
        result = session.run("""
            MATCH (d:Document)
            WHERE (apoc.text.clean(d.fileName) CONTAINS apoc.text.clean("Ayça")) 
              AND (d.year = "2020" OR d.fileName CONTAINS "2020")
            RETURN d AS node
        """)
        
        documents = []
        for record in result:
            node = record['node']
            documents.append({
                'fileName': node.get('fileName', ''),
                'year': node.get('year', ''),
                'properties': dict(node)
            })
        
        return documents
    
    driver.close()

def search_in_filtered_documents(query_vector, filtered_documents, threshold=0.05, limit=20):
    """Filtrelenmiş document'lar içinde embedding araması"""
    
    if not filtered_documents:
        return []
    
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    
    # Document fileName'lerini al
    document_names = [doc['fileName'] for doc in filtered_documents]
    
    with driver.session() as session:
        # Filtrelenmiş document'lar içinde embedding araması
        result = session.run("""
            MATCH (d:Document)<-[:PART_OF]-(c:Chunk)
            WHERE c.embedding IS NOT NULL 
            AND d.fileName IN $document_names
            
            WITH c, d, vector.similarity.cosine($query_vector, c.embedding) AS score
            WHERE score > $threshold
            
            RETURN 
                d.fileName AS document_name,
                c.text AS chunk_text,
                score,
                c.position AS chunk_position
                
            ORDER BY score DESC
            LIMIT $limit
        """, query_vector=query_vector, document_names=document_names, threshold=threshold, limit=limit)
        
        return list(result)
    
    driver.close()

def combined_search_test(query_text):
    """Kombine arama testi: önce document filtre, sonra embedding"""
    
    print(f"=== KOMBİNE ARAMA TESTİ ===")
    print(f"🔍 Sorgu: {query_text}")
    print(f"📋 Adım 1: APOC ile document filtrele")
    print(f"📋 Adım 2: Filtrelenmiş document'larda embedding ara")
    print()
    
    # 1. APOC ile document filtrele
    print("1. APOC ile document filtreleniyor...")
    filtered_docs = filter_documents_with_apoc()
    
    if not filtered_docs:
        print("❌ APOC filtresi sonucu hiç document bulunamadı!")
        return
    
    print(f"✅ APOC ile {len(filtered_docs)} document bulundu:")
    
    for i, doc in enumerate(filtered_docs, 1):
        print(f"   {i}. {doc['fileName']} (year: {doc.get('year', 'N/A')})")
        
        # D4 kontrolü
        if 'd4' in doc['fileName'].lower() and 'galata' in doc['fileName'].lower():
            print(f"      🎯 HEDEF BELGE TESPİT EDİLDİ!")
    
    print()
    
    # 2. Sorgu için embedding oluştur
    print("2. OpenAI ile embedding oluşturuluyor...")
    query_embedding = get_embedding(query_text)
    
    if not query_embedding:
        print("❌ Embedding oluşturulamadı!")
        return
    
    print(f"✅ Embedding oluşturuldu (boyut: {len(query_embedding)})")
    
    # 3. Filtrelenmiş document'larda embedding araması
    print("\n3. Filtrelenmiş document'larda embedding araması...")
    
    # Farklı threshold'ları dene
    thresholds = [0.05, 0.1, 0.15, 0.2]
    
    found_target = False
    best_results = []
    
    for threshold in thresholds:
        print(f"\n--- Threshold: {threshold} ---")
        
        try:
            results = search_in_filtered_documents(
                query_embedding, 
                filtered_docs, 
                threshold=threshold, 
                limit=20
            )
            
            if not results:
                print(f"❌ Threshold {threshold}'da sonuç bulunamadı")
                continue
                
            print(f"✅ {len(results)} chunk bulundu")
            
            # Sonuçları göster
            for i, record in enumerate(results[:10], 1):
                doc_name = record['document_name']
                score = record['score']
                position = record['chunk_position']
                chunk_text = str(record['chunk_text'])
                
                print(f"\n{i}. Chunk (Score: {score:.4f}):")
                print(f"   📄 Document: {doc_name}")
                print(f"   📍 Position: {position}")
                
                # Hedef belge kontrolü
                doc_lower = doc_name.lower()
                if 'galata' in doc_lower and 'd4' in doc_lower and '2020' in doc_lower:
                    print(f"   🎯 HEDEF BELGE CHUNK'U!")
                    found_target = True
                
                # Taksit kontrolü
                if 'taksit' in chunk_text.lower():
                    print(f"   💰 TAKSİT BİLGİSİ MEVCUT!")
                
                # İlk 200 karakter
                print(f"   📝 Text (ilk 200 karakter):")
                print(f"      {chunk_text[:200]}...")
                
                best_results.append({
                    'document': doc_name,
                    'score': score,
                    'threshold': threshold,
                    'chunk_text': chunk_text,
                    'position': position
                })
            
            # Hedef bulunduysa dur
            if found_target:
                print(f"\n🎯 Hedef belge chunk'u bulundu! Arama tamamlandı.")
                break
                
        except Exception as e:
            print(f"❌ Embedding arama hatası: {e}")
            continue
    
    # 4. Özet raporu
    print(f"\n{'='*70}")
    print(f"📊 KOMBİNE ARAMA ÖZETİ")
    print(f"{'='*70}")
    
    print(f"🏷️  APOC Document Filtresi: {len(filtered_docs)} belge")
    print(f"🔍 Embedding Araması: {len(best_results)} chunk bulundu")
    
    if found_target:
        print(f"🎯 Hedef Belge Durumu: ✅ BULUNDU")
    else:
        print(f"🎯 Hedef Belge Durumu: ❌ BULUNAMADI")
    
    if best_results:
        print(f"\n🏆 EN İYİ SONUÇLAR:")
        # Score'a göre sırala
        best_results.sort(key=lambda x: x['score'], reverse=True)
        
        for i, result in enumerate(best_results[:5], 1):
            print(f"\n{i}. En İyi Chunk:")
            print(f"   📄 Belge: {result['document']}")
            print(f"   📊 Score: {result['score']:.4f}")
            print(f"   📈 Threshold: {result['threshold']}")
            print(f"   📍 Position: {result['position']}")
            
            # Galata D4 işaretleme
            doc_lower = result['document'].lower()
            if 'galata' in doc_lower and 'd4' in doc_lower and '2020' in doc_lower:
                print(f"   ⭐ HEDEF BELGE: Galata Residance D4 2020!")
    
    print(f"\n✅ Kombine arama (APOC + Embedding) tamamlandı.")

def main():
    # Test sorusu
    query = "ayça hanımın 2020 yılı D4 konut projesinin taksitleri ne kadar"
    
    combined_search_test(query)

if __name__ == "__main__":
    main()
