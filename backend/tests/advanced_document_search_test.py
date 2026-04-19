#!/usr/bin/env python3
"""
Document fileName'leri için embedding tabanlı arama testi
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

def create_document_filename_embeddings():
    """Document fileName'leri için embedding oluştur ve kaydet"""
    
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    
    with driver.session() as session:
        # Önce embedding'i olmayan document'ları al
        result = session.run("""
            MATCH (d:Document)
            WHERE d.fileName IS NOT NULL AND d.fileNameEmbedding IS NULL
            RETURN d.fileName AS fileName, d
            LIMIT 50
        """)
        
        documents = list(result)
        print(f"🏗️ {len(documents)} document için fileName embedding oluşturulacak...")
        
        for i, record in enumerate(documents, 1):
            fileName = record['fileName']
            doc = record['d']
            
            print(f"{i}. {fileName}")
            
            # FileName için embedding oluştur
            embedding = get_embedding(fileName)
            
            if embedding:
                # Embedding'i Neo4j'ye kaydet
                session.run("""
                    MATCH (d:Document)
                    WHERE d.fileName = $fileName
                    SET d.fileNameEmbedding = $embedding
                """, fileName=fileName, embedding=embedding)
                
                print(f"   ✅ Embedding kaydedildi")
            else:
                print(f"   ❌ Embedding oluşturulamadı")
    
    driver.close()

def search_documents_by_filename_embedding(query, threshold=0.7, limit=10):
    """FileName embedding ile document arama"""
    
    # Query için embedding oluştur
    query_embedding = get_embedding(query)
    if not query_embedding:
        print("❌ Query embedding oluşturulamadı")
        return []
    
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    
    with driver.session() as session:
        # FileName embedding similarity ile arama
        result = session.run("""
            MATCH (d:Document)
            WHERE d.fileNameEmbedding IS NOT NULL
            
            WITH d, vector.similarity.cosine($query_vector, d.fileNameEmbedding) AS filename_score
            WHERE filename_score > $threshold
            
            RETURN 
                d.fileName AS document_name,
                d.year AS year,
                filename_score,
                d
                
            ORDER BY filename_score DESC
            LIMIT $limit
        """, query_vector=query_embedding, threshold=threshold, limit=limit)
        
        return list(result)
    
    driver.close()

def hybrid_document_search(query, filename_threshold=0.7, content_threshold=0.05, limit=20):
    """Hybrid: FileName embedding + chunk content embedding"""
    
    # Query için embedding oluştur
    query_embedding = get_embedding(query)
    if not query_embedding:
        print("❌ Query embedding oluşturulamadı")
        return []
    
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    
    with driver.session() as session:
        # Hybrid arama: FileName embedding + chunk content embedding
        result = session.run("""
            // Önce fileName embedding ile document'ları filtrele
            MATCH (d:Document)
            WHERE d.fileNameEmbedding IS NOT NULL
            
            WITH d, vector.similarity.cosine($query_vector, d.fileNameEmbedding) AS filename_score
            WHERE filename_score > $filename_threshold
            
            // Bu document'ların chunk'larında da arama yap
            MATCH (d)<-[:PART_OF]-(c:Chunk)
            WHERE c.embedding IS NOT NULL
            
            WITH d, filename_score, c, vector.similarity.cosine($query_vector, c.embedding) AS content_score
            WHERE content_score > $content_threshold
            
            // Hybrid score hesapla (FileName %40, Content %60)
            WITH d, filename_score, c, content_score, 
                 (filename_score * 0.4 + content_score * 0.6) AS hybrid_score
            
            RETURN 
                d.fileName AS document_name,
                d.year AS year,
                filename_score,
                content_score,
                hybrid_score,
                c.text AS chunk_text,
                c.position AS chunk_position
                
            ORDER BY hybrid_score DESC
            LIMIT $limit
        """, 
        query_vector=query_embedding, 
        filename_threshold=filename_threshold,
        content_threshold=content_threshold,
        limit=limit)
        
        return list(result)
    
    driver.close()

def fuzzy_string_search(query):
    """APOC ile fuzzy string matching"""
    
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    
    with driver.session() as session:
        # APOC fuzzy search
        result = session.run("""
            MATCH (d:Document)
            WHERE d.fileName IS NOT NULL
            
            // Fuzzy string matching (Levenshtein distance)
            WITH d, apoc.text.distance(apoc.text.clean(d.fileName), apoc.text.clean($search_query)) AS distance,
                 apoc.text.fuzzyMatch(apoc.text.clean(d.fileName), apoc.text.clean($search_query)) AS fuzzy_score
            
            WHERE distance <= 5 OR fuzzy_score > 0.7
            
            RETURN 
                d.fileName AS document_name,
                d.year AS year,
                distance,
                fuzzy_score
                
            ORDER BY fuzzy_score DESC, distance ASC
            LIMIT 20
        """, search_query=query)
        
        return list(result)
    
    driver.close()

def test_all_methods():
    """Tüm arama yöntemlerini test et"""
    
    query = "ayça 2020 D4 konut"
    
    print("🧪 DOCUMENT ARAMA YÖNTEMLERİ KARŞILAŞTIRMASI")
    print("=" * 80)
    print(f"🔍 Test Query: {query}")
    print("=" * 80)
    
    # 1. Mevcut embedding'leri kontrol et
    print("\n1. Mevcut FileName Embedding'leri Kontrol Ediliyor...")
    
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    with driver.session() as session:
        result = session.run("""
            MATCH (d:Document)
            WHERE d.fileNameEmbedding IS NOT NULL
            RETURN count(d) AS embedding_count
        """)
        
        embedding_count = list(result)[0]['embedding_count']
        print(f"✅ {embedding_count} document'ta fileName embedding mevcut")
    driver.close()
    
    # 2. FileName embedding ile arama
    print("\n2. FileName Embedding Araması...")
    filename_results = search_documents_by_filename_embedding(query, threshold=0.6)
    
    print(f"✅ FileName Embedding: {len(filename_results)} sonuç")
    for i, result in enumerate(filename_results[:5], 1):
        print(f"   {i}. {result['document_name']} (score: {result['filename_score']:.4f})")
    
    # 3. Hybrid arama
    print("\n3. Hybrid Arama (FileName + Content)...")
    hybrid_results = hybrid_document_search(query, filename_threshold=0.5, content_threshold=0.05)
    
    print(f"✅ Hybrid Search: {len(hybrid_results)} sonuç")
    for i, result in enumerate(hybrid_results[:5], 1):
        print(f"   {i}. {result['document_name']}")
        print(f"      FileName Score: {result['filename_score']:.4f}")
        print(f"      Content Score: {result['content_score']:.4f}")
        print(f"      Hybrid Score: {result['hybrid_score']:.4f}")
    
    # 4. Fuzzy string search
    print("\n4. Fuzzy String Search...")
    fuzzy_results = fuzzy_string_search(query)
    
    print(f"✅ Fuzzy Search: {len(fuzzy_results)} sonuç")
    for i, result in enumerate(fuzzy_results[:5], 1):
        print(f"   {i}. {result['document_name']}")
        print(f"      Distance: {result['distance']}")
        print(f"      Fuzzy Score: {result['fuzzy_score']:.4f}")
    
    print("\n" + "=" * 80)
    print("📊 YÖNTEMLERİN KARŞILAŞTIRMASI:")
    print("=" * 80)
    
    print(f"🎯 FileName Embedding: {len(filename_results)} sonuç - En hassas")
    print(f"🎯 Hybrid Search: {len(hybrid_results)} sonuç - En kapsamlı") 
    print(f"🎯 Fuzzy Search: {len(fuzzy_results)} sonuç - En esnek")
    
    # Hedef belgeyi arıyor muyuz?
    target_found = {
        'filename': False,
        'hybrid': False,
        'fuzzy': False
    }
    
    for result in filename_results:
        if 'galata' in result['document_name'].lower() and 'd4' in result['document_name'].lower():
            target_found['filename'] = True
            break
    
    for result in hybrid_results:
        if 'galata' in result['document_name'].lower() and 'd4' in result['document_name'].lower():
            target_found['hybrid'] = True
            break
    
    for result in fuzzy_results:
        if 'galata' in result['document_name'].lower() and 'd4' in result['document_name'].lower():
            target_found['fuzzy'] = True
            break
    
    print(f"\n🎯 HEDEF BELGE (Galata D4) BULUNDU MU?")
    print(f"   FileName Embedding: {'✅' if target_found['filename'] else '❌'}")
    print(f"   Hybrid Search: {'✅' if target_found['hybrid'] else '❌'}")
    print(f"   Fuzzy Search: {'✅' if target_found['fuzzy'] else '❌'}")

if __name__ == "__main__":
    test_all_methods()
