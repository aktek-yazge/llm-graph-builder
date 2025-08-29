#!/usr/bin/env python3
"""
OpenAI embedding kullanarak spesifik sorgu testi
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

def search_chunks(query_vector, threshold=0.6, limit=10):
    """Neo4j'de embedding ile chunk arama"""
    
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    
    with driver.session() as session:
        # Embedding araması
        result = session.run("""
            MATCH (d:Document)<-[:PART_OF]-(c:Chunk)
            WHERE c.embedding IS NOT NULL
            WITH c, d, vector.similarity.cosine($query_vector, c.embedding) AS score
            WHERE score > $threshold
            RETURN 
                d.fileName AS document_name,
                c.text AS chunk_text,
                score,
                c.position AS chunk_position
            ORDER BY score DESC
            LIMIT $limit
        """, query_vector=query_vector, threshold=threshold, limit=limit)
        
        return list(result)
    
    driver.close()

def enhanced_search_with_keywords(query_vector, keywords, threshold=0.5, limit=20):
    """Embedding araması + keyword filtreleme kombinasyonu"""
    
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    
    with driver.session() as session:
        # Önce embedding ile geniş arama yap
        result = session.run("""
            MATCH (d:Document)<-[:PART_OF]-(c:Chunk)
            WHERE c.embedding IS NOT NULL
            WITH c, d, vector.similarity.cosine($query_vector, c.embedding) AS score
            WHERE score > $threshold
            RETURN 
                d.fileName AS document_name,
                c.text AS chunk_text,
                score,
                c.position AS chunk_position
            ORDER BY score DESC
            LIMIT $limit
        """, query_vector=query_vector, threshold=threshold, limit=limit)
        
        all_results = list(result)
        
        # Keyword filtrelemesi yap
        filtered_results = []
        for record in all_results:
            chunk_text = str(record['chunk_text']).lower()
            doc_name = str(record['document_name']).lower()
            
            # Tüm keyword'ler metinde veya dosya adında var mı?
            keyword_matches = 0
            for keyword in keywords:
                if keyword.lower() in chunk_text or keyword.lower() in doc_name:
                    keyword_matches += 1
            
            # En az 2 keyword eşleşmesi varsa dahil et
            if keyword_matches >= 2:
                record['keyword_matches'] = keyword_matches
                filtered_results.append(record)
        
        # Keyword match sayısına göre sırala
        filtered_results.sort(key=lambda x: (x['keyword_matches'], x['score']), reverse=True)
        
        return filtered_results
    
    driver.close()

def advanced_keyword_search_with_apoc(query_vector, threshold=0.4, limit=20):
    """APOC text clean kullanarak fileName ve text içinde keyword arama"""
    
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    
    with driver.session() as session:
        # APOC ile temizlenmiş text ve fileName'de arama
        result = session.run("""
            MATCH (d:Document)<-[:PART_OF]-(c:Chunk)
            WHERE c.embedding IS NOT NULL
            
            // Document fileName'de ayça ve 2020 arama (APOC clean ile)
            WITH d, c, 
                apoc.text.clean(toLower(d.fileName)) AS cleanFileName,
                apoc.text.clean(toLower(toString(c.text))) AS cleanText,
                vector.similarity.cosine($query_vector, c.embedding) AS score
            
            WHERE score > $threshold
            AND (cleanFileName CONTAINS 'ayça' OR cleanText CONTAINS 'ayça')
            AND (cleanFileName CONTAINS '2020' OR cleanText CONTAINS '2020')
            
            RETURN 
                d.fileName AS document_name,
                c.text AS chunk_text,
                score,
                c.position AS chunk_position,
                cleanFileName,
                (CASE WHEN cleanFileName CONTAINS 'ayça' THEN 1 ELSE 0 END +
                 CASE WHEN cleanFileName CONTAINS '2020' THEN 1 ELSE 0 END +
                 CASE WHEN cleanText CONTAINS 'ayça' THEN 1 ELSE 0 END +
                 CASE WHEN cleanText CONTAINS '2020' THEN 1 ELSE 0 END) AS keyword_match_count
                 
            ORDER BY keyword_match_count DESC, score DESC
            LIMIT $limit
        """, query_vector=query_vector, threshold=threshold, limit=limit)
        
        return list(result)
    
    driver.close()

def main():
    # Test sorusu
    query = "ayça hanımın 2020 yılı D4 konut projesinin taksitleri ne kadar"
    
    print("=== APOC TEXT CLEAN + KEYWORD ARAMA TESTI ===")
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
    
    # 2. APOC ile gelişmiş arama
    print("\n2. APOC text.clean ile keyword + embedding araması...")
    
    # Farklı threshold'lar dene
    thresholds = [0.2, 0.3, 0.4, 0.5]
    
    best_results = []
    
    for threshold in thresholds:
        print(f"\n--- Threshold: {threshold} ---")
        
        try:
            results = advanced_keyword_search_with_apoc(
                query_embedding, 
                threshold=threshold, 
                limit=15
            )
            
            if not results:
                print(f"❌ Threshold {threshold}'da sonuç bulunamadı")
                continue
                
            print(f"✅ {len(results)} sonuç bulundu")
            
            # Sonuçları göster
            for i, record in enumerate(results[:5], 1):
                doc_name = record['document_name'] 
                clean_filename = record['cleanFileName']
                keyword_count = record['keyword_match_count']
                
                print(f"\n{i}. Sonuç (Keyword Match: {keyword_count}):")
                print(f"   📄 Dokuman: {doc_name}")
                print(f"   🧹 Temiz Dosya Adı: {clean_filename}")
                print(f"   📊 Similarity Score: {record['score']:.4f}")
                print(f"   📍 Position: {record['chunk_position']}")
                
                # D4 ve Galata kontrolleri
                if 'galata' in clean_filename and 'd4' in clean_filename and '2020' in clean_filename:
                    print(f"   🎯 HEDEF BELGE BULUNDU: Galata D4 2020!")
                
                if 'konut' in clean_filename and '2020' in clean_filename:
                    print(f"   🏠 2020 Konut Belgesi tespit edildi!")
                
                # Chunk text'te taksit var mı?
                chunk_text = str(record['chunk_text'])
                if isinstance(chunk_text, list) and len(chunk_text) > 0:
                    chunk_text = chunk_text[0]
                
                if 'taksit' in chunk_text.lower():
                    print(f"   💰 TAKSİT BİLGİSİ BULUNDU!")
                
                print(f"   📝 Text (ilk 250 karakter):")
                print(f"      {chunk_text[:250]}...")
                
                # En iyi sonuçları kaydet
                best_results.append({
                    'document_name': doc_name,
                    'score': record['score'],
                    'keyword_matches': keyword_count,
                    'threshold': threshold,
                    'chunk_text': chunk_text
                })
            
            # Galata D4 2020 bulunduysa dur
            galata_d4_found = any('galata' in r.get('cleanFileName', '').lower() and 
                                'd4' in r.get('cleanFileName', '').lower() and 
                                '2020' in r.get('cleanFileName', '').lower() 
                                for r in results[:5])
            
            if galata_d4_found:
                print(f"\n🎯 Galata D4 2020 belgesi bulundu! Arama tamamlandı.")
                break
                
        except Exception as e:
            print(f"❌ APOC sorgusu hatası: {e}")
            continue
    
    # 3. Özet raporu
    print(f"\n{'='*60}")
    print(f"📊 APOC KEYWORD ARAMA ÖZETİ")
    print(f"{'='*60}")
    
    if best_results:
        # Keyword match sayısına göre sırala
        best_results.sort(key=lambda x: (x['keyword_matches'], x['score']), reverse=True)
        
        print(f"🏆 EN İYİ SONUÇLAR:")
        
        for i, result in enumerate(best_results[:5], 1):
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
        print("❌ APOC keyword araması ile hiç sonuç bulunamadı")
    
    for query in test_queries:
        print(f"\n{'='*60}")
        print(f"🔍 Sorgu: {query}")
        print()
        
        # 1. Embedding oluştur
        print("1. OpenAI ile embedding oluşturuluyor...")
        query_embedding = get_embedding(query)
        
        if not query_embedding:
            print("❌ Embedding oluşturulamadı!")
            continue
        
        print(f"✅ Embedding oluşturuldu (boyut: {len(query_embedding)})")
        
        # 2. Farklı threshold'larla test et
        thresholds = [0.3, 0.4, 0.5, 0.6]
        
        for threshold in thresholds:
            print(f"\n--- Threshold: {threshold} ---")
            
            results = search_chunks(query_embedding, threshold=threshold, limit=10)
            
            if not results:
                print(f"❌ Threshold {threshold}'da sonuç bulunamadı")
                continue
                
            print(f"✅ {len(results)} sonuç bulundu")
            
            # En iyi 3 sonucu göster
            for i, record in enumerate(results[:3], 1):
                doc_name = record['document_name']
                
                print(f"\n{i}. Sonuç:")
                print(f"   📄 Dokuman: {doc_name}")
                print(f"   📊 Similarity Score: {record['score']:.4f}")
                print(f"   📍 Position: {record['chunk_position']}")
                
                # Ayça ve 2020 içeren belgeleri işaretle
                if 'ayça' in doc_name.lower() and '2020' in doc_name:
                    print(f"   ⭐ AYÇA 2020 BELGESİ TESPİT EDİLDİ!")
                    
                    # D4 varsa ekstra işaretle
                    if 'D4' in doc_name:
                        print(f"   🎯 D4 KONUT BELGESİ BULUNDU!")
                
                chunk_text = str(record['chunk_text'])
                if isinstance(chunk_text, list) and len(chunk_text) > 0:
                    chunk_text = chunk_text[0]
                
                # Taksit bilgilerini vurgula
                if 'taksit' in chunk_text.lower():
                    print(f"   💰 TAKSİT BİLGİSİ VAR!")
                
                print(f"   📝 Text (ilk 300 karakter):")
                print(f"      {chunk_text[:300]}...")
                
                # En iyi sonuçları kaydet
                result_dict = {
                    'document_name': record['document_name'],
                    'score': record['score'],
                    'chunk_position': record['chunk_position'],
                    'chunk_text': chunk_text,
                    'query': query,
                    'threshold': threshold
                }
                best_results.append(result_dict)
            
            # Ayça 2020 belgesi bulunduysa bir sonraki threshold'a geç
            ayca_2020_found = any('ayça' in r['document_name'].lower() and '2020' in r['document_name'] 
                                 for r in results[:3])
            if ayca_2020_found:
                print(f"   ✅ Ayça 2020 belgesi bulundu, sonraki threshold'a geçiliyor...")
                break
        
        # Ayça 2020 belgesi bulunduysa diğer sorguları test etmeye gerek yok
        ayca_found_overall = any('ayça' in r['document_name'].lower() and '2020' in r['document_name'] 
                               for r in best_results if r.get('query') == query)
        if ayca_found_overall:
            print(f"\n🎯 Bu sorgu ile Ayça 2020 belgesi bulundu! Diğer sorgular atlanıyor...")
            break
    
    print(f"\n{'='*60}")
    print(f"📊 EN İYİ SONUÇLAR ÖZETİ:")
    print(f"{'='*60}")
    
    # Ayça 2020 belgelerini filtrele
    ayca_results = [r for r in best_results 
                   if 'ayça' in r['document_name'].lower() and '2020' in r['document_name']]
    
    if ayca_results:
        # Score'a göre sırala
        ayca_results.sort(key=lambda x: x['score'], reverse=True)
        
        print(f"🎯 AYÇA 2020 BELGELERİ BULUNDU ({len(ayca_results)} sonuç):")
        
        for i, record in enumerate(ayca_results[:5], 1):
            print(f"\n{i}. En İyi Ayça 2020 Sonucu:")
            print(f"   📄 Belge: {record['document_name']}")
            print(f"   📊 Score: {record['score']:.4f}")
            print(f"   🔍 Sorgu: {record['query']}")
            print(f"   📈 Threshold: {record['threshold']}")
            
            if 'D4' in record['document_name']:
                print(f"   � D4 KONUT BELGESİ - HEDEF BULUNDU!")
    
    else:
        print("❌ Ayça 2020 belgesi embedding ile bulunamadı")
        print("🔍 En yüksek score'lu sonuçlar:")
        
        # Tüm sonuçları score'a göre sırala
        best_results.sort(key=lambda x: x['score'], reverse=True)
        
        for i, record in enumerate(best_results[:5], 1):
            print(f"\n{i}. Genel En İyi Sonuç:")
            print(f"   📄 Belge: {record['document_name']}")  
            print(f"   📊 Score: {record['score']:.4f}")
            print(f"   🔍 Sorgu: {record['query']}")
            print(f"   📈 Threshold: {record['threshold']}")

if __name__ == "__main__":
    main()

