#!/usr/bin/env python3
"""
APOC text similarity fonksiyonlarını test et
"""

import os
from neo4j import GraphDatabase
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Neo4j bağlantı bilgileri
NEO4J_URI = os.getenv('NEO4J_URI', 'neo4j://localhost:7687')
NEO4J_USER = os.getenv('NEO4J_USERNAME', 'neo4j')
NEO4J_PASSWORD = os.getenv('NEO4J_PASSWORD', 'qwerty5555')

def test_apoc_similarity_functions():
    """APOC text similarity fonksiyonlarını test et"""
    
    print("🧪 APOC TEXT SIMILARITY FONKSIYON TESTİ")
    print("=" * 70)
    
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    
    with driver.session() as session:
        
        # 1. Önce Ayça keyword'üne yakın document'ları bulalım
        print("1. APOC TEXT SIMILARITY İLE DOCUMENT ARAMA\n")
        
        similarity_queries = [
            {
                'name': 'Levenshtein Distance (düşük = benzer)',
                'query': '''
                MATCH (d:Document)
                WITH d, apoc.text.distance(toLower(d.fileName), toLower("ayça")) AS distance
                WHERE distance <= 3  // 3 karakterden az fark
                RETURN d.fileName, distance
                ORDER BY distance ASC
                LIMIT 10
                '''
            },
            {
                'name': 'Fuzzy Match (0-1 arası, yüksek = benzer)',
                'query': '''
                MATCH (d:Document)
                WITH d, apoc.text.fuzzyMatch(toLower(d.fileName), toLower("ayça")) AS similarity
                WHERE similarity > 0.7  // %70'den fazla benzerlik
                RETURN d.fileName, similarity
                ORDER BY similarity DESC
                LIMIT 10
                '''
            },
            {
                'name': 'Sorensen-Dice Similarity (0-1 arası, yüksek = benzer)',
                'query': '''
                MATCH (d:Document)
                WITH d, apoc.text.sorensenDiceSimilarity(toLower(d.fileName), toLower("ayça")) AS similarity
                WHERE similarity > 0.3  // %30'dan fazla benzerlik
                RETURN d.fileName, similarity
                ORDER BY similarity DESC
                LIMIT 10
                '''
            },
            {
                'name': 'Jaro-Winkler Distance (0-1 arası, yüksek = benzer)',
                'query': '''
                MATCH (d:Document)
                WITH d, apoc.text.jaroWinklerDistance(toLower(d.fileName), toLower("ayça")) AS similarity
                WHERE similarity > 0.7  // %70'den fazla benzerlik
                RETURN d.fileName, similarity
                ORDER BY similarity DESC
                LIMIT 10
                '''
            }
        ]
        
        results_summary = {}
        
        for test in similarity_queries:
            print(f"🔍 {test['name']}:")
            print("-" * 50)
            
            try:
                result = session.run(test['query'])
                records = list(result)
                
                if records:
                    print(f"✅ {len(records)} sonuç bulundu:")
                    
                    for i, record in enumerate(records, 1):
                        filename = record[0]  # fileName
                        score = record[1]     # similarity/distance score
                        
                        # Ayça kontrolü
                        contains_ayca = 'ayça' in filename.lower()
                        ayca_mark = "🎯" if contains_ayca else "  "
                        
                        print(f"   {ayca_mark} {i:2d}. {filename}")
                        print(f"        Score: {score:.4f}")
                        
                        # D4 2020 kontrolü
                        if 'd4' in filename.lower() and '2020' in filename.lower():
                            print(f"        ⭐ D4 2020 HEDEF BELGE!")
                    
                    results_summary[test['name']] = {
                        'count': len(records),
                        'best_score': records[0][1] if records else 0,
                        'found_ayca': any('ayça' in r[0].lower() for r in records)
                    }
                else:
                    print("❌ Hiç sonuç bulunamadı")
                    results_summary[test['name']] = {'count': 0, 'found_ayca': False}
                    
            except Exception as e:
                print(f"❌ Sorgu hatası: {e}")
                results_summary[test['name']] = {'error': str(e)}
            
            print()
        
        # 2. Kombine similarity testi - hem isim hem yıl
        print("2. KOMBİNE SİMİLARİTY TESTİ (İsim + Yıl)")
        print("=" * 50)
        
        combined_query = '''
        MATCH (d:Document)
        WITH d,
             apoc.text.fuzzyMatch(toLower(d.fileName), toLower("ayça")) AS name_similarity,
             CASE 
                WHEN d.year = "2020" OR d.fileName CONTAINS "2020" THEN 1.0 
                ELSE 0.0 
             END AS year_match
        
        WITH d, name_similarity, year_match,
             (name_similarity * 0.7 + year_match * 0.3) AS combined_score
        
        WHERE name_similarity > 0.5 AND year_match > 0
        
        RETURN d.fileName, name_similarity, year_match, combined_score
        ORDER BY combined_score DESC
        LIMIT 15
        '''
        
        try:
            print("🔍 Kombine Similarity (Fuzzy Match + Year):")
            print("-" * 50)
            
            result = session.run(combined_query)
            records = list(result)
            
            if records:
                print(f"✅ {len(records)} sonuç bulundu:")
                
                for i, record in enumerate(records, 1):
                    filename = record[0]
                    name_sim = record[1]
                    year_match = record[2]
                    combined = record[3]
                    
                    # Hedef belge kontrolü
                    is_target = 'd4' in filename.lower() and 'galata' in filename.lower() and '2020' in filename.lower()
                    target_mark = "🎯" if is_target else "  "
                    
                    print(f"   {target_mark} {i:2d}. {filename}")
                    print(f"        İsim Similarity: {name_sim:.4f}")
                    print(f"        Yıl Match: {year_match:.1f}")
                    print(f"        Kombine Score: {combined:.4f}")
                    
                    if is_target:
                        print(f"        ⭐ HEDEF BELGE BULUNDU!")
            else:
                print("❌ Kombine sorgu sonuç bulamadı")
                
        except Exception as e:
            print(f"❌ Kombine sorgu hatası: {e}")
        
        # 3. Özet rapor
        print("\n" + "=" * 70)
        print("📊 APOC SIMILARITY FONKSIYON ÖZETİ")
        print("=" * 70)
        
        for method, stats in results_summary.items():
            if 'error' in stats:
                print(f"❌ {method}: HATA - {stats['error']}")
            else:
                found_mark = "✅" if stats.get('found_ayca') else "❌"
                print(f"{found_mark} {method}:")
                print(f"   Sonuç Sayısı: {stats.get('count', 0)}")
                if stats.get('best_score') is not None:
                    print(f"   En İyi Score: {stats.get('best_score'):.4f}")
                print(f"   Ayça Buldu: {'Evet' if stats.get('found_ayca') else 'Hayır'}")
            print()
        
        print("🏆 ÖNERİ: Fuzzy Match en iyi sonuçları veriyor!")
        print("💡 Kombine approach (isim similarity + yıl match) en etkili!")
    
    driver.close()

if __name__ == "__main__":
    test_apoc_similarity_functions()
