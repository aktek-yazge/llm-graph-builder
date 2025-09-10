#!/usr/bin/env python3
"""
Vector Search Test Script - Basit test
"""

import sys
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add the backend directory to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from neo4j import GraphDatabase
    from src.llm import get_llm
    from src.shared.common_fn import load_embedding_model
    print("✅ Tüm import'lar başarılı")
except ImportError as e:
    print(f"❌ Import hatası: {e}")
    sys.exit(1)

def test_basic_connections():
    """Temel bağlantıları test et"""
    print("\n🔍 Temel Bağlantı Testleri")
    print("-" * 40)
    
    # Environment variables kontrolü
    neo4j_uri = os.getenv('NEO4J_URI')
    neo4j_username = os.getenv('NEO4J_USERNAME')
    neo4j_password = os.getenv('NEO4J_PASSWORD')
    openai_api_key = os.getenv('OPENAI_API_KEY')
    
    print(f"NEO4J_URI: {'✅ OK' if neo4j_uri else '❌ Eksik'}")
    print(f"NEO4J_USERNAME: {'✅ OK' if neo4j_username else '❌ Eksik'}")
    print(f"NEO4J_PASSWORD: {'✅ OK' if neo4j_password else '❌ Eksik'}")
    print(f"OPENAI_API_KEY: {'✅ OK' if openai_api_key else '❌ Eksik'}")
    
    if not all([neo4j_uri, neo4j_username, neo4j_password, openai_api_key]):
        print("❌ Environment variables eksik!")
        return False
    
    # Neo4j bağlantısı test
    try:
        driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_username, neo4j_password))
        with driver.session() as session:
            result = session.run("RETURN 1 as test")
            test_result = result.single()["test"]
            print(f"Neo4j bağlantısı: {'✅ OK' if test_result == 1 else '❌ Hata'}")
        driver.close()
    except Exception as e:
        print(f"❌ Neo4j bağlantı hatası: {e}")
        return False
    
    # OpenAI bağlantısı test - load_embedding_model kullan
    try:
        embedding_model = os.getenv('EMBEDDING_MODEL', 'openai')
        embedding_function, embedding_dimension = load_embedding_model(embedding_model)
        
        # Basit bir embedding testi
        test_embedding = embedding_function.embed_query("test")
        embedding_size = len(test_embedding)
        print(f"OpenAI bağlantısı: ✅ OK (embedding size: {embedding_size})")
    except Exception as e:
        print(f"❌ OpenAI bağlantı hatası: {e}")
        return False
    
    # Embedding model testi
    try:
        embedding_model = os.getenv('EMBEDDING_MODEL', 'openai')
        embedding_function, embedding_dimension = load_embedding_model(embedding_model)
        print(f"Embedding model: ✅ OK ({embedding_model}, dimension: {embedding_dimension})")
    except Exception as e:
        print(f"❌ Embedding model hatası: {e}")
        return False
    
    return True

def test_vector_index():
    """Vector index durumunu kontrol et"""
    print("\n🔍 Vector Index Testi")
    print("-" * 40)
    
    try:
        neo4j_uri = os.getenv('NEO4J_URI')
        neo4j_username = os.getenv('NEO4J_USERNAME')
        neo4j_password = os.getenv('NEO4J_PASSWORD')
        
        driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_username, neo4j_password))
        
        with driver.session() as session:
            # Index'leri listele
            result = session.run("SHOW INDEXES")
            indexes = [record for record in result]
            
            print(f"Toplam index sayısı: {len(indexes)}")
            
            # Vector index'leri ara
            vector_indexes = []
            for index in indexes:
                index_dict = dict(index)
                if 'vector' in str(index_dict.get('type', '')).lower():
                    vector_indexes.append(index_dict)
                    print(f"✅ Vector Index bulundu: {index_dict.get('name')}")
                    print(f"   - Type: {index_dict.get('type')}")
                    print(f"   - State: {index_dict.get('state')}")
            
            if not vector_indexes:
                print("❌ Vector index bulunamadı!")
                return False
            
            # Chunk sayısını kontrol et
            result = session.run("MATCH (c:Chunk) RETURN count(c) as total")
            total_chunks = result.single()["total"]
            
            # Embedding'li chunk sayısı
            result = session.run("MATCH (c:Chunk) WHERE c.embedding IS NOT NULL RETURN count(c) as with_embedding")
            chunks_with_embedding = result.single()["with_embedding"]
            
            print(f"Toplam chunk sayısı: {total_chunks}")
            print(f"Embedding'li chunk sayısı: {chunks_with_embedding}")
            
            if chunks_with_embedding > 0:
                print("✅ Vector index ve chunk'lar hazır")
                return True
            else:
                print("❌ Hiç embedding'li chunk yok!")
                return False
                
        driver.close()
        
    except Exception as e:
        print(f"❌ Vector index test hatası: {e}")
        return False

def test_simple_vector_search():
    """Basit vector search testi"""
    print("\n🔍 Basit Vector Search Testi")
    print("-" * 40)
    
    try:
        neo4j_uri = os.getenv('NEO4J_URI')
        neo4j_username = os.getenv('NEO4J_USERNAME')
        neo4j_password = os.getenv('NEO4J_PASSWORD')
        
        driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_username, neo4j_password))
        
        # load_embedding_model kullan
        embedding_model = os.getenv('EMBEDDING_MODEL', 'openai')
        embedding_function, embedding_dimension = load_embedding_model(embedding_model)
        
        # Test query
        test_query = "taksit tutarları"
        print(f"Test sorgusu: '{test_query}'")
        print(f"Embedding modeli: {embedding_model}")
        
        # Embedding oluştur - load_embedding_model kullan
        query_embedding = embedding_function.embed_query(test_query)
        print(f"Embedding boyutu: {len(query_embedding)}")
        
        # Vector search
        with driver.session() as session:
            result = session.run("""
                CALL db.index.vector.queryNodes('vector', 3, $queryEmbedding)
                YIELD node, score
                RETURN node.text as text, node.fileName as fileName, score
                ORDER BY score DESC
            """, queryEmbedding=query_embedding)
            
            results = [record for record in result]
            
            print(f"Bulunan sonuç sayısı: {len(results)}")
            
            for i, record in enumerate(results):
                print(f"  {i+1}. Score: {record['score']:.4f}")
                print(f"     Dosya: {record['fileName']}")
                print(f"     Metin: {record['text'][:100]}...")
                print()
            
            if len(results) > 0:
                print("✅ Vector search başarılı!")
                return True
            else:
                print("❌ Vector search sonuç bulamadı!")
                return False
        
        driver.close()
        
    except Exception as e:
        print(f"❌ Vector search test hatası: {e}")
        return False

def test_filtered_vector_search():
    """Filtered vector search testi - belirli dokümanda arama"""
    print("\n🔍 Filtered Vector Search Testi")
    print("-" * 40)
    
    try:
        neo4j_uri = os.getenv('NEO4J_URI')
        neo4j_username = os.getenv('NEO4J_USERNAME')
        neo4j_password = os.getenv('NEO4J_PASSWORD')
        
        driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_username, neo4j_password))
        
        # load_embedding_model kullan
        embedding_model = os.getenv('EMBEDDING_MODEL', 'openai')
        embedding_function, embedding_dimension = load_embedding_model(embedding_model)
        
        # Test query ve hedef dosya
        test_query = "taksit tutarları"
        target_document = "Ayça Dinçkök Galata Residance D4 Konut_2020"
        
        print(f"Test sorgusu: '{test_query}'")
        print(f"Hedef doküman: '{target_document}'")
        print(f"Embedding modeli: {embedding_model}")
        
        # Embedding oluştur - load_embedding_model kullan
        query_embedding = embedding_function.embed_query(test_query)
        print(f"Embedding boyutu: {len(query_embedding)}")
        
        # Filtered vector search - queryNodes FIRST yapısı
        with driver.session() as session:
            result = session.run("""
                CALL db.index.vector.queryNodes('vector', 100, $embedding_vector) YIELD node, score
                WITH node, score
                MATCH (node:Chunk)-[:PART_OF]->(d:Document)
                WHERE toLower(apoc.text.clean(d.fileName)) CONTAINS toLower(apoc.text.clean($target_document))
                RETURN node.text, node.chunkId, node.page_number, node.position, score
                ORDER BY score DESC
                LIMIT 10
            """, embedding_vector=query_embedding, target_document=target_document)
            
            results = [record for record in result]
            
            print(f"Bulunan sonuç sayısı: {len(results)}")
            
            for i, record in enumerate(results):
                print(f"  {i+1}. Score: {record['score']:.4f}")
                print(f"     Chunk ID: {record.get('chunkId', 'N/A')}")
                print(f"     Sayfa: {record.get('page_number', 'N/A')}")
                print(f"     Pozisyon: {record.get('position', 'N/A')}")
                print(f"     Metin: {record['text'][:150] if record.get('text') else 'N/A'}...")
                print()
            
            if len(results) > 0:
                print("✅ Filtered vector search başarılı!")
                return True
            else:
                print("❌ Filtered vector search sonuç bulamadı!")
                
                # Debug: Dokümanda hiç chunk var mı kontrol et
                debug_result = session.run("""
                    MATCH (c:Chunk)-[:PART_OF]->(d:Document)
                    WHERE toLower(apoc.text.clean(d.fileName)) CONTAINS toLower(apoc.text.clean($target_document))
                    RETURN count(c) as chunk_count, d.fileName as fileName
                """, target_document=target_document)
                
                debug_data = [record for record in debug_result]
                if debug_data:
                    for debug_record in debug_data:
                        print(f"     Debug: Dokümanda {debug_record['chunk_count']} chunk bulundu")
                        print(f"     Dosya adı: {debug_record['fileName']}")
                else:
                    print("     Debug: Hiç eşleşen doküman bulunamadı!")
                
                return False
        
        driver.close()
        
    except Exception as e:
        print(f"❌ Filtered vector search test hatası: {e}")
        return False

def test_threshold_behavior():
    """Farklı threshold değerleri ile vector search davranışını test et"""
    print("\n🔍 Threshold Behavior Testi")
    print("-" * 40)
    
    try:
        neo4j_uri = os.getenv('NEO4J_URI')
        neo4j_username = os.getenv('NEO4J_USERNAME')
        neo4j_password = os.getenv('NEO4J_PASSWORD')
        knn_min_score = float(os.getenv('KNN_MIN_SCORE', '0.7'))
        
        driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_username, neo4j_password))
        
        # load_embedding_model kullan
        embedding_model = os.getenv('EMBEDDING_MODEL', 'openai')
        embedding_function, embedding_dimension = load_embedding_model(embedding_model)
        
        # Test query
        test_query = "taksit tutarları ödeme planı"
        target_document = "Ayça Dinçkök Galata Residance D4 Konut_2020"
        
        print(f"Test sorgusu: '{test_query}'")
        print(f"Hedef doküman: '{target_document}'")
        print(f"Environment KNN_MIN_SCORE: {knn_min_score}")
        
        # Embedding oluştur
        query_embedding = embedding_function.embed_query(test_query)
        print(f"Embedding boyutu: {len(query_embedding)}")
        
        # Test farklı threshold değerleri
        thresholds = [0.0, 0.3, 0.5, 0.7, 0.8, 0.9, 0.94, 0.95]
        
        with driver.session() as session:
            for threshold in thresholds:
                print(f"\n--- Threshold: {threshold} ---")
                
                # Threshold ile filtered vector search
                result = session.run("""
                    MATCH (c:Chunk)-[:PART_OF]->(d:Document)
                    WHERE toLower(apoc.text.clean(d.fileName)) CONTAINS toLower(apoc.text.clean($target_document))
                    WITH c
                    CALL db.index.vector.queryNodes('vector', 10, $embedding_vector) YIELD node, score
                    WHERE node = c AND score >= $threshold
                    RETURN node.text, node.chunkId, node.page_number, node.position, score
                    ORDER BY score DESC
                    LIMIT 5
                """, embedding_vector=query_embedding, target_document=target_document, threshold=threshold)
                
                results = [record for record in result]
                print(f"Sonuç sayısı: {len(results)}")
                
                if results:
                    best_score = results[0]['score']
                    worst_score = results[-1]['score']
                    print(f"En yüksek score: {best_score:.4f}")
                    print(f"En düşük score: {worst_score:.4f}")
                    
                    # İlk sonucun kısa önizlemesi
                    first_text = results[0]['text'][:100]
                    print(f"En iyi sonuç: {first_text}...")
                else:
                    print("Hiç sonuç bulunamadı!")
        
        driver.close()
        return True
        
    except Exception as e:
        print(f"❌ Threshold behavior test hatası: {e}")
        return False

def test_different_search_terms():
    """Farklı arama terimleri ile test et"""
    print("\n🔍 Farklı Arama Terimleri Testi")
    print("-" * 40)
    
    try:
        neo4j_uri = os.getenv('NEO4J_URI')
        neo4j_username = os.getenv('NEO4J_USERNAME')
        neo4j_password = os.getenv('NEO4J_PASSWORD')
        
        driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_username, neo4j_password))
        
        # load_embedding_model kullan
        embedding_model = os.getenv('EMBEDDING_MODEL', 'openai')
        embedding_function, embedding_dimension = load_embedding_model(embedding_model)
        
        target_document = "Ayça Dinçkök Galata Residance D4 Konut_2020"
        
        # Farklı arama terimleri test et
        search_terms = [
            "taksit tutarları",
            "ödeme planı",
            "aylık ödeme",
            "vade",
            "prim",
            "bedel",
            "miktar",
            "ödeme tutarı",
            "aylık prim",
            "taksit miktarı"
        ]
        
        print(f"Hedef doküman: '{target_document}'")
        
        with driver.session() as session:
            for search_term in search_terms:
                print(f"\n--- Arama terimi: '{search_term}' ---")
                
                # Embedding oluştur
                query_embedding = embedding_function.embed_query(search_term)
                
                # Vector search (threshold olmadan)
                result = session.run("""
                    MATCH (c:Chunk)-[:PART_OF]->(d:Document)
                    WHERE toLower(apoc.text.clean(d.fileName)) CONTAINS toLower(apoc.text.clean($target_document))
                    WITH c
                    CALL db.index.vector.queryNodes('vector', 5, $embedding_vector) YIELD node, score
                    WHERE node = c
                    RETURN node.text, node.chunkId, node.page_number, node.position, score
                    ORDER BY score DESC
                    LIMIT 3
                """, embedding_vector=query_embedding, target_document=target_document)
                
                results = [record for record in result]
                print(f"Sonuç sayısı: {len(results)}")
                
                if results:
                    for i, record in enumerate(results):
                        score = record['score']
                        text_preview = record['text'][:80]
                        print(f"  {i+1}. Score: {score:.4f} - {text_preview}...")
                else:
                    print("  Hiç sonuç bulunamadı!")
        
        driver.close()
        return True
        
    except Exception as e:
        print(f"❌ Farklı arama terimleri test hatası: {e}")
        return False

def test_text_contains_search():
    """Belgedeki chunk'larda direkt text CONTAINS araması yap"""
    print("\n🔍 Text CONTAINS Arama Testi")
    print("-" * 40)
    
    try:
        neo4j_uri = os.getenv('NEO4J_URI')
        neo4j_username = os.getenv('NEO4J_USERNAME')
        neo4j_password = os.getenv('NEO4J_PASSWORD')
        
        driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_username, neo4j_password))
        
        target_document = "Ayça Dinçkök Galata Residance D4 Konut_2020"
        
        # Farklı arama terimleri test et
        search_terms = [
            "taksit",
            "ödeme",
            "planı", 
            "vade",
            "prim",
            "bedel",
            "miktar",
            "tutarı",
            "aylık",
            "monthly",
            "installment",
            "payment",
            "plan",
            "amount"
        ]
        
        print(f"Hedef doküman: '{target_document}'")
        
        with driver.session() as session:
            for search_term in search_terms:
                print(f"\n--- Text arama: '{search_term}' ---")
                
                # Direkt text alanında CONTAINS araması
                result = session.run("""
                    MATCH (c:Chunk)-[:PART_OF]->(d:Document)
                    WHERE toLower(apoc.text.clean(d.fileName)) CONTAINS toLower(apoc.text.clean($target_document))
                    AND toLower(c.text) CONTAINS toLower($search_term)
                    RETURN c.chunkId, COALESCE(c.position, 0) as position, c.page_number, 
                           substring(c.text, 0, 200) as text_preview,
                           size(c.text) as text_length
                    ORDER BY COALESCE(c.position, 0)
                    LIMIT 5
                """, target_document=target_document, search_term=search_term)
                
                results = [record for record in result]
                print(f"Bulunan chunk sayısı: {len(results)}")
                
                if results:
                    for i, record in enumerate(results):
                        position = record['position']
                        page_number = record.get('page_number', 'N/A')
                        text_preview = record['text_preview']
                        text_length = record['text_length']
                        print(f"  {i+1}. Pozisyon: {position}, Sayfa: {page_number}, Length: {text_length}")
                        print(f"     Metin: {text_preview}...")
                        print()
                else:
                    print("  Hiç eşleşme bulunamadı!")
        
        # Tüm chunk'ların genel bilgilerini de göster
        print(f"\n--- Belge Chunk İstatistikleri ---")
        with driver.session() as session:
            stats_result = session.run("""
                MATCH (c:Chunk)-[:PART_OF]->(d:Document)
                WHERE toLower(apoc.text.clean(d.fileName)) CONTAINS toLower(apoc.text.clean($target_document))
                RETURN count(c) as total_chunks,
                       min(COALESCE(c.position, 0)) as min_position,
                       max(COALESCE(c.position, 0)) as max_position,
                       avg(size(c.text)) as avg_text_length,
                       min(size(c.text)) as min_text_length,
                       max(size(c.text)) as max_text_length
            """, target_document=target_document)
            
            stats = [record for record in stats_result]
            if stats:
                stat = stats[0]
                print(f"Toplam chunk: {stat['total_chunks']}")
                print(f"Pozisyon aralığı: {stat['min_position']} - {stat['max_position']}")
                print(f"Ortalama text uzunluğu: {stat['avg_text_length']:.1f}")
                print(f"Min text uzunluğu: {stat['min_text_length']}")
                print(f"Max text uzunluğu: {stat['max_text_length']}")
        
        # İlk 3 chunk'ın tam içeriğini göster
        print(f"\n--- İlk 3 Chunk İçeriği (Örnek) ---")
        with driver.session() as session:
            sample_result = session.run("""
                MATCH (c:Chunk)-[:PART_OF]->(d:Document)
                WHERE toLower(apoc.text.clean(d.fileName)) CONTAINS toLower(apoc.text.clean($target_document))
                RETURN c.chunkId, COALESCE(c.position, 0) as position, c.page_number, c.text
                ORDER BY COALESCE(c.position, 0)
                LIMIT 3
            """, target_document=target_document)
            
            samples = [record for record in sample_result]
            for i, record in enumerate(samples):
                position = record['position']
                page_number = record.get('page_number', 'N/A')
                text = record['text']
                print(f"\nChunk {i+1} (Pozisyon: {position}, Sayfa: {page_number}):")
                print(f"'{text[:500]}{'...' if len(text) > 500 else ''}'")
                print("-" * 80)
        
        driver.close()
        return True
        
    except Exception as e:
        print(f"❌ Text CONTAINS arama test hatası: {e}")
        return False

def test_gds_similarity_algorithms():
    """Farklı GDS similarity algoritmalarını karşılaştır"""
    print("\n🔍 GDS Similarity Algorithms Comparison")
    print("-" * 50)
    
    try:
        neo4j_uri = os.getenv('NEO4J_URI')
        neo4j_username = os.getenv('NEO4J_USERNAME') 
        neo4j_password = os.getenv('NEO4J_PASSWORD')
        
        driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_username, neo4j_password))
        
        # load_embedding_model kullan
        embedding_model = os.getenv('EMBEDDING_MODEL', 'openai')
        embedding_function, embedding_dimension = load_embedding_model(embedding_model)
        
        test_query = "taksit tutarları"
        target_document = "Ayça Dinçkök Galata Residance D4 Konut_2020"
        print(f"Test Query: '{test_query}'")
        print(f"Target Document: '{target_document}'")
        print(f"Embedding model: {embedding_model}, dimension: {embedding_dimension}")
        
        # Embedding oluştur
        query_embedding = embedding_function.embed_query(test_query)
        print(f"Query embedding boyutu: {len(query_embedding)}")
        
        # Test edilecek GDS algoritmaları
        algorithms = [
            ("Cosine Similarity", "gds.similarity.cosine"),
            ("Euclidean Distance", "gds.similarity.euclidean"), 
            ("Manhattan Distance", "gds.similarity.manhattan"),
            ("Jaccard Similarity", "gds.similarity.jaccard"),
            ("Overlap Similarity", "gds.similarity.overlap")
        ]
        
        with driver.session() as session:
            print(f"\n{'='*70}")
            print("GDS SIMILARITY ALGORITHMS COMPARISON")
            print(f"{'='*70}")
            
            for algo_name, algo_function in algorithms:
                print(f"\n🔍 {algo_name} ({algo_function}):")
                print("-" * 40)
                
                try:
                    # Algorithm-specific query adjustments
                    if "jaccard" in algo_function.lower() or "overlap" in algo_function.lower():
                        # Jaccard ve Overlap için binary/set-based approach
                        print("   ⚠️ Bu algoritma set-based data için tasarlanmış, vector embedding'ler için uygun değil")
                        continue
                    
                    # Cosine, Euclidean, Manhattan için query - MATCH kodu ile filtered
                    cypher_query = f"""
                    MATCH (c:Chunk)-[:PART_OF]->(d:Document)
                    WHERE toLower(apoc.text.clean(d.fileName)) CONTAINS toLower(apoc.text.clean($target_document))
                    AND c.embedding IS NOT NULL
                    WITH c, d, {algo_function}(c.embedding, $query_embedding) AS score
                    WHERE score IS NOT NULL
                    RETURN 
                        c.chunkId as chunkId,
                        c.text as text, 
                        c.position as position,
                        c.page_number as page_number,
                        d.fileName as file_name,
                        score
                    ORDER BY score DESC
                    LIMIT 5
                    """
                    
                    result = session.run(cypher_query, query_embedding=query_embedding, target_document=target_document)
                    results = [record for record in result]
                    
                    if results:
                        print(f"   📊 Sonuç sayısı: {len(results)}")
                        print("   🎯 En iyi sonuçlar:")
                        
                        for i, record in enumerate(results[:3]):
                            score = record['score']
                            position = record['position'] or 'N/A'
                            page_number = record['page_number'] or 'N/A'
                            file_name = record['file_name'] or 'N/A'
                            text = record['text'] or ''
                            
                            # Taksit ile ilgili chunk'ları özel göster
                            if "taksit" in text.lower() or "tutar tl" in text.lower():
                                text_preview = text[:200] + "..." if len(text) > 200 else text
                                special_mark = "🎯"
                            else:
                                text_preview = text[:100] + "..." if len(text) > 100 else text
                                special_mark = "  "
                            
                            print(f"      {special_mark} {i+1}. Score: {score:.4f}, Pos: {position}, Page: {page_number}")
                            print(f"         Dosya: {file_name}")
                            print(f"         Text: {text_preview}")
                            print()
                    else:
                        print("   ❌ Hiç sonuç bulunamadı")
                        
                except Exception as e:
                    print(f"   ❌ {algo_name} test hatası: {e}")
                    continue
        
        driver.close()
        
        print(f"\n{'='*70}")
        print("ALGORITHM COMPARISON SUMMARY")
        print(f"{'='*70}")
        print("✅ Cosine Similarity: En yaygın kullanılan, normalize edilmiş vektörler için ideal")
        print("⚠️ Euclidean Distance: Mutlak mesafe, büyük boyutlarda cosine'den farklı sonuçlar")
        print("⚠️ Manhattan Distance: L1 norm, sparse vektörler için uygun olabilir")
        print("❌ Jaccard/Overlap: Set-based, dense vector embedding'ler için uygun değil")
        
        return True
        
    except Exception as e:
        print(f"❌ GDS algorithms test hatası: {e}")
        return False

def test_gds_cosine_similarity():
    """GDS cosine similarity ile vector search test et"""
    print("\n🔍 GDS Cosine Similarity Test")
    print("-" * 40)
    
    # Environment variables
    neo4j_uri = os.getenv('NEO4J_URI')
    neo4j_username = os.getenv('NEO4J_USERNAME') 
    neo4j_password = os.getenv('NEO4J_PASSWORD')
    embedding_model = os.getenv('EMBEDDING_MODEL')
    
    if not all([neo4j_uri, neo4j_username, neo4j_password]):
        print("❌ Environment variables eksik!")
        return False
    
    # Embedding model yükle
    try:
        embedding_function, embedding_dimension = load_embedding_model(embedding_model)
        print(f"Embedding model: {embedding_model}, dimension: {embedding_dimension}")
    except Exception as e:
        print(f"❌ Embedding model yüklenemedi: {e}")
        return False
    
    # Neo4j driver
    driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_username, neo4j_password))
    
    target_document = "Ayça Dinçkök Galata Residance D4 Konut_2020"
    test_queries = [
        "taksit tutarları", 
        # "ödeme planı",
        # "prim bedeli",
        # "sigorta poliçesi",
        # "Ayça Dinçkök"
    ]
    
    try:
        with driver.session() as session:
            # Önce GDS cosine similarity fonksiyonunun mevcut olup olmadığını kontrol et
            print("🔍 GDS fonksiyonu kontrol ediliyor...")
            gds_check = session.run("RETURN gds.similarity.cosine([1,2,3], [1,2,3]) as test")
            gds_result = gds_check.single()
            print(f"GDS cosine test sonucu: {gds_result['test']}")
            
            for test_query in test_queries:
                print(f"\n🔍 Test Query: '{test_query}'")
                print("-" * 30)
                
                # Embedding oluştur
                query_embedding = embedding_function.embed_query(test_query)
                print(f"Query embedding boyutu: {len(query_embedding)}")
                
                # GDS cosine similarity ile arama (WHERE filtreleme sonrası)
                print("🔍 GDS Cosine Similarity Search:")
                result = session.run("""
                    WITH $queryVec AS queryVec
                    MATCH (c:Chunk)-[:PART_OF]->(d:Document)
                    WHERE toLower(apoc.text.clean(d.fileName)) CONTAINS toLower(apoc.text.clean($target_document))
                    AND c.embedding IS NOT NULL
                    WITH c, d, gds.similarity.cosine(c.embedding, queryVec) AS score
                    WHERE score >= 0.5
                    RETURN c.text as text, 
                           c.position as position, 
                           c.page_number as page_number, 
                           d.fileName as file_name, 
                           score
                    ORDER BY score DESC
                    LIMIT 10
                """, queryVec=query_embedding, target_document=target_document)
                
                results = [record for record in result]
                print(f"GDS Sonuç sayısı: {len(results)}")
                
                if results:
                    print("🎯 En iyi sonuçlar:")
                    for i, record in enumerate(results[:5]):
                        score = record['score']
                        position = record['position'] or 'N/A'
                        page_number = record['page_number'] or 'N/A'
                        file_name = record['file_name'] or 'N/A'
                        # Taksit ile ilgili chunk'ları tam göster
                        text = record['text']
                        print(f"   {i+1}. Score: {score:.4f}, Pos: {position}, Page: {page_number}")
                        print(f"      Dosya: {file_name}")
                        print(f"      Text: {text}")
                        print()
                else:
                    print("   ⚠️ Hiç sonuç bulunamadı")
                
                # Düşük threshold ile de deneyelim
                print("\n🔍 GDS Lower Threshold (0.3):")
                low_result = session.run("""
                    WITH $queryVec AS queryVec
                    MATCH (c:Chunk)-[:PART_OF]->(d:Document)
                    WHERE toLower(apoc.text.clean(d.fileName)) CONTAINS toLower(apoc.text.clean($target_document))
                    AND c.embedding IS NOT NULL
                    WITH c, d, gds.similarity.cosine(c.embedding, queryVec) AS score
                    WHERE score >= 0.3
                    RETURN c.text as text, 
                           c.position as position, 
                           c.page_number as page_number, 
                           d.fileName as file_name, 
                           score
                    ORDER BY score DESC
                    LIMIT 10
                """, queryVec=query_embedding, target_document=target_document)
                
                low_results = [record for record in low_result]
                print(f"Düşük threshold sonuç sayısı: {len(low_results)}")
                
                if low_results:
                    print("🎯 Düşük threshold sonuçları:")
                    for i, record in enumerate(low_results[:3]):
                        score = record['score']
                        position = record['position'] or 'N/A'
                        file_name = record['file_name'] or 'N/A'
                        text_preview = (record['text'] or '')[:100] + "..." if record['text'] else 'N/A'
                        print(f"   {i+1}. Score: {score:.4f}, Pos: {position}")
                        print(f"      Dosya: {file_name}")
                        print(f"      Text: {text_preview}")
                
                print("\n" + "="*50)
                
    except Exception as e:
        print(f"❌ GDS test hatası: {e}")
        import traceback
        print(traceback.format_exc())
        return False
    
    finally:
        driver.close()
    
    return True

def main():
    """Ana test fonksiyonu"""
    print("🚀 Vector Search Test Suite - Basit Versiyon")
    print("=" * 60)
    
    tests = [
        # ("Temel Bağlantılar", test_basic_connections),
        # ("Vector Index", test_vector_index),
        ("GDS Similarity Algorithms", test_gds_similarity_algorithms),
        ("GDS Cosine Similarity", test_gds_cosine_similarity),
        # ("Basit Vector Search", test_simple_vector_search),
        # ("Filtered Vector Search", test_filtered_vector_search),
        # ("Threshold Behavior", test_threshold_behavior),
        # ("Farklı Arama Terimleri", test_different_search_terms),
        # ("Text CONTAINS Arama", test_text_contains_search)
    ]
    
    results = []
    
    for test_name, test_func in tests:
        print(f"\n{'='*60}")
        print(f"TEST: {test_name}")
        print(f"{'='*60}")
        
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"❌ Test hatası: {e}")
            results.append((test_name, False))
    
    # Sonuçları özetle
    print(f"\n{'='*60}")
    print("TEST SONUÇLARI")
    print(f"{'='*60}")
    
    passed = 0
    total = len(results)
    
    for test_name, result in results:
        status = "✅ BAŞARILI" if result else "❌ BAŞARISIZ"
        print(f"{test_name}: {status}")
        if result:
            passed += 1
    
    print(f"\nToplam: {passed}/{total} test başarılı")
    
    if passed == total:
        print("\n🎉 Tüm testler başarılı!")
    else:
        print(f"\n⚠️ {total - passed} test başarısız!")

if __name__ == "__main__":
    main()
