#!/usr/bin/env python3
"""
Basic keyword arama + embedding testi (APOC olmadan)
"""

import os
import openai
from neo4j import GraphDatabase
import numpy as np
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

def basic_keyword_search(query_vector, threshold=0.1, limit=20):
    """Basic keyword arama (APOC olmadan)"""
    
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    
    with driver.session() as session:
        # Basic string matching ile arama
        result = session.run("""
            MATCH (d:Document)<-[:PART_OF]-(c:Chunk)
            WHERE c.embedding IS NOT NULL
            
            // Basic toLower ile arama
            WITH d, c, 
                toLower(d.fileName) AS lowerFileName,
                toLower(toString(c.text)) AS lowerText,
                vector.similarity.cosine($query_vector, c.embedding) AS score
            
            WHERE score > $threshold
            AND (lowerFileName CONTAINS 'ayça' OR lowerText CONTAINS 'ayça')
            AND (lowerFileName CONTAINS '2020' OR lowerText CONTAINS '2020')
            
            RETURN 
                d.fileName AS document_name,
                c.text AS chunk_text,
                score,
                c.position AS chunk_position,
                lowerFileName,
                (CASE WHEN lowerFileName CONTAINS 'ayça' THEN 1 ELSE 0 END +
                 CASE WHEN lowerFileName CONTAINS '2020' THEN 1 ELSE 0 END +
                 CASE WHEN lowerText CONTAINS 'ayça' THEN 1 ELSE 0 END +
                 CASE WHEN lowerText CONTAINS '2020' THEN 1 ELSE 0 END) AS keyword_match_count
                 
            ORDER BY keyword_match_count DESC, score DESC
            LIMIT $limit
        """, query_vector=query_vector, threshold=threshold, limit=limit)
        
        return list(result)
    
    driver.close()

def main():
    # Test sorusu
    query = "ayça hanımın 2020 yılı D4 konut projesinin taksitleri ne kadar"
    
    print("=== BASIC KEYWORD + EMBEDDING ARAMA TESTI ===")
    print(f"🔍 Sorgu: {query}")
    print(f"🎯 Aranacak Keyword'ler: ayça, 2020")
    print()
    
    # 1. Embedding oluştur
    print("1. OpenAI ile embedding oluşturuluyor...")
    query_embedding = get_embedding(query)
    
    if not query_embedding:
        print("❌ Embedding oluşturulamadı!")
        return
    
    print(f"✅ Embedding oluşturuldu (boyut: {len(query_embedding)})")
    
    # 2. Basic keyword arama
    print("\n2. Basic keyword + embedding araması...")
    
    # Çok düşük threshold'lar dene
    thresholds = [0.05, 0.1, 0.15, 0.2]
    
    best_results = []
    found_target = False
    
    for threshold in thresholds:
        print(f"\n--- Threshold: {threshold} ---")
        
        try:
            results = basic_keyword_search(
                query_embedding, 
                threshold=threshold, 
                limit=20
            )
            
            if not results:
                print(f"❌ Threshold {threshold}'da sonuç bulunamadı")
                continue
                
            print(f"✅ {len(results)} sonuç bulundu")
            
            # Sonuçları göster
            for i, record in enumerate(results[:10], 1):
                doc_name = record['document_name'] 
                lower_filename = record['lowerFileName']
                keyword_count = record['keyword_match_count']
                
                print(f"\n{i}. Sonuç (Keyword Match: {keyword_count}):")
                print(f"   📄 Dokuman: {doc_name}")
                print(f"   📊 Similarity Score: {record['score']:.4f}")
                print(f"   📍 Position: {record['chunk_position']}")
                
                # D4 ve Galata kontrolleri
                if 'galata' in lower_filename and 'd4' in lower_filename and '2020' in lower_filename:
                    print(f"   🎯 HEDEF BELGE BULUNDU: Galata D4 2020!")
                    found_target = True
                
                if 'konut' in lower_filename and '2020' in lower_filename:
                    print(f"   🏠 2020 Konut Belgesi tespit edildi!")
                
                # Chunk text'te taksit var mı?
                chunk_text = str(record['chunk_text'])
                if isinstance(chunk_text, list) and len(chunk_text) > 0:
                    chunk_text = chunk_text[0]
                
                if 'taksit' in chunk_text.lower():
                    print(f"   💰 TAKSİT BİLGİSİ BULUNDU!")
                
                print(f"   📝 Text (ilk 200 karakter):")
                print(f"      {chunk_text[:200]}...")
                
                # En iyi sonuçları kaydet
                best_results.append({
                    'document_name': doc_name,
                    'score': record['score'],
                    'keyword_matches': keyword_count,
                    'threshold': threshold,
                    'chunk_text': chunk_text
                })
            
            # Hedef belge bulunduysa dur
            if found_target:
                print(f"\n🎯 Hedef belge bulundu! Arama tamamlandı.")
                break
                
        except Exception as e:
            print(f"❌ Sorgu hatası: {e}")
            continue
    
    # 3. Özet raporu
    print(f"\n{'='*60}")
    print(f"📊 BASIC KEYWORD ARAMA ÖZETİ")
    print(f"{'='*60}")
    
    if best_results:
        # Keyword match sayısına göre sırala
        best_results.sort(key=lambda x: (x['keyword_matches'], x['score']), reverse=True)
        
        print(f"🏆 EN İYİ SONUÇLAR:")
        
        for i, result in enumerate(best_results[:8], 1):
            print(f"\n{i}. En İyi Sonuç:")
            print(f"   📄 Belge: {result['document_name']}")
            print(f"   🎯 Keyword Eşleşme: {result['keyword_matches']}")
            print(f"   📊 Similarity Score: {result['score']:.4f}")
            print(f"   📈 Threshold: {result['threshold']}")
            
            # Galata D4 2020 işaretleme
            doc_lower = result['document_name'].lower()
            if 'galata' in doc_lower and 'd4' in doc_lower and '2020' in doc_lower:
                print(f"   ⭐ HEDEF BELGE: Galata Residance D4 Konut 2020!")
    else:
        print("❌ Basic keyword araması ile hiç sonuç bulunamadı")
    
    print(f"\n🔍 Basic keyword arama (toLower) ile fileName ve chunk text'te arama tamamlandı.")

if __name__ == "__main__":
    main()
