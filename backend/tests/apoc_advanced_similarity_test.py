#!/usr/bin/env python3
"""
Doğru Unicode encoding ile APOC similarity test
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

def test_apoc_similarity_with_correct_encoding():
    """Doğru Unicode encoding ile APOC similarity test"""
    
    print("🧪 APOC SIMILARITY - DOĞRU UNICODE ENCODING TESTİ")
    print("=" * 70)
    
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    
    # Gerçek database'deki encoding
    search_name = "Ayça"  # Unicode normal form
    search_year = "2020"
    
    print(f"🔍 Aranacak: '{search_name}' + '{search_year}'")
    
    test_methods = [
        {
            'name': 'sorensenDiceSimilarity (Recommended)',
            'query': '''
                MATCH (d:Document)
                WHERE apoc.text.sorensenDiceSimilarity(apoc.text.clean(d.fileName), apoc.text.clean($search_name)) >= 0.7
                AND (d.year = $search_year OR d.fileName CONTAINS $search_year)
                RETURN 
                    d.fileName AS fileName,
                    apoc.text.sorensenDiceSimilarity(apoc.text.clean(d.fileName), apoc.text.clean($search_name)) AS similarity_score,
                    d.year AS year
                ORDER BY similarity_score DESC
                LIMIT 10
            ''',
            'threshold': 0.7
        },
        {
            'name': 'jaroWinklerDistance (Names optimized)',
            'query': '''
                MATCH (d:Document)
                WHERE apoc.text.jaroWinklerDistance(apoc.text.clean(d.fileName), apoc.text.clean($search_name)) >= 0.8
                AND (d.year = $search_year OR d.fileName CONTAINS $search_year)
                RETURN 
                    d.fileName AS fileName,
                    apoc.text.jaroWinklerDistance(apoc.text.clean(d.fileName), apoc.text.clean($search_name)) AS similarity_score,
                    d.year AS year
                ORDER BY similarity_score DESC
                LIMIT 10
            ''',
            'threshold': 0.8
        },
        {
            'name': 'levenshteinDistance (Character diff)',
            'query': '''
                MATCH (d:Document)
                WHERE apoc.text.distance(apoc.text.clean(d.fileName), apoc.text.clean($search_name)) <= 3
                AND (d.year = $search_year OR d.fileName CONTAINS $search_year)
                RETURN 
                    d.fileName AS fileName,
                    apoc.text.distance(apoc.text.clean(d.fileName), apoc.text.clean($search_name)) AS distance_score,
                    d.year AS year
                ORDER BY distance_score ASC
                LIMIT 10
            ''',
            'threshold': 3
        },
        {
            'name': 'CONTAINS (Baseline)',
            'query': '''
                MATCH (d:Document)
                WHERE apoc.text.clean(d.fileName) CONTAINS apoc.text.clean($search_name)
                AND (d.year = $search_year OR d.fileName CONTAINS $search_year)
                RETURN 
                    d.fileName AS fileName,
                    1.0 AS contains_score,
                    d.year AS year
                ORDER BY d.fileName
                LIMIT 10
            ''',
            'threshold': 'exact'
        }
    ]
    
    with driver.session() as session:
        results_summary = {}
        
        for method in test_methods:
            print(f"\n🔍 TEST: {method['name']} (threshold: {method['threshold']})")
            print("-" * 60)
            
            try:
                result = session.run(method['query'], {
                    'search_name': search_name,
                    'search_year': search_year
                })
                records = list(result)
                
                if not records:
                    print(f"❌ {method['name']} - Sonuç bulunamadı")
                    results_summary[method['name']] = {'count': 0, 'found_target': False}
                    continue
                
                print(f"✅ {method['name']} - {len(records)} sonuç bulundu")
                
                found_target = False
                best_score = 0
                
                for i, record in enumerate(records[:8], 1):
                    file_name = record['fileName']
                    
                    # Score değerini al (farklı method'lar farklı field isimleri kullanır)
                    score_field = [key for key in record.keys() if 'score' in key or 'distance' in key][0]
                    score = record[score_field]
                    year = record.get('year', 'N/A')
                    
                    print(f"\n{i}. {file_name}")
                    print(f"   📊 {score_field}: {score}")
                    print(f"   📅 Year: {year}")
                    
                    # Hedef belge kontrolü
                    if 'galata' in file_name.lower() and 'd4' in file_name.lower() and 'konut' in file_name.lower():
                        print(f"   🎯 HEDEF BELGE TESPİT EDİLDİ!")
                        found_target = True
                        
                        # Best score'u güncelle
                        if method['name'] == 'levenshteinDistance':
                            best_score = score  # Distance için düşük değer iyi
                        else:
                            best_score = max(best_score, score)  # Similarity için yüksek değer iyi
                
                results_summary[method['name']] = {
                    'count': len(records),
                    'found_target': found_target,
                    'best_score': best_score
                }
                        
            except Exception as e:
                print(f"❌ {method['name']} hatası: {e}")
                results_summary[method['name']] = {'count': 0, 'found_target': False, 'error': str(e)}
    
    # Hybrid approach testi
    print(f"\n🚀 HYBRID APPROACH - Multi-Method Combination")
    print("-" * 70)
    
    hybrid_query = '''
        MATCH (d:Document)
        WHERE (
            // Method 1: Sorensen Dice Similarity (en güçlü)
            apoc.text.sorensenDiceSimilarity(apoc.text.clean(d.fileName), apoc.text.clean($search_name)) >= 0.6
            OR
            // Method 2: Jaro-Winkler Distance (isimler için optimize)
            apoc.text.jaroWinklerDistance(apoc.text.clean(d.fileName), apoc.text.clean($search_name)) >= 0.7
            OR
            // Method 3: Levenshtein Distance (typo tolerance)
            apoc.text.distance(apoc.text.clean(d.fileName), apoc.text.clean($search_name)) <= 4
            OR
            // Method 4: Fallback CONTAINS
            apoc.text.clean(d.fileName) CONTAINS apoc.text.clean($search_name)
        )
        AND (d.year = $search_year OR d.fileName CONTAINS $search_year)
        
        RETURN 
            d.fileName AS fileName,
            apoc.text.sorensenDiceSimilarity(apoc.text.clean(d.fileName), apoc.text.clean($search_name)) AS sorensen,
            apoc.text.jaroWinklerDistance(apoc.text.clean(d.fileName), apoc.text.clean($search_name)) AS jaro,
            apoc.text.distance(apoc.text.clean(d.fileName), apoc.text.clean($search_name)) AS levenshtein,
            CASE 
                WHEN apoc.text.clean(d.fileName) CONTAINS apoc.text.clean($search_name) THEN 1.0
                ELSE 0.0
            END AS contains,
            d.year AS year
        ORDER BY sorensen DESC, jaro DESC
        LIMIT 15
    '''
    
    with driver.session() as session:
        try:
            result = session.run(hybrid_query, {
                'search_name': search_name,
                'search_year': search_year
            })
            records = list(result)
            
            print(f"✅ Hybrid approach - {len(records)} sonuç bulundu")
            
            hybrid_target_found = False
            
            for i, record in enumerate(records[:10], 1):
                file_name = record['fileName']
                sorensen = record['sorensen']
                jaro = record['jaro']
                levenshtein = record['levenshtein']
                contains = record['contains']
                
                print(f"\n{i}. {file_name}")
                print(f"   🎯 Sorensen: {sorensen:.4f}")
                print(f"   🎯 Jaro-Winkler: {jaro:.4f}")
                print(f"   🎯 Levenshtein: {levenshtein}")
                print(f"   🎯 Contains: {contains}")
                
                # Combined score hesapla (weighted average)
                combined = (sorensen * 0.4) + (jaro * 0.3) + ((5 - min(levenshtein, 5)) / 5 * 0.2) + (contains * 0.1)
                print(f"   📊 Combined Score: {combined:.4f}")
                
                # Hedef belge kontrolü
                if 'galata' in file_name.lower() and 'd4' in file_name.lower() and 'konut' in file_name.lower():
                    print(f"   🎯 HEDEF BELGE BULUNDU!")
                    hybrid_target_found = True
            
            results_summary['Hybrid Approach'] = {
                'count': len(records),
                'found_target': hybrid_target_found
            }
                    
        except Exception as e:
            print(f"❌ Hybrid query hatası: {e}")
    
    # Final summary
    print("\n" + "=" * 70)
    print("📊 APOC SIMILARITY METHODS - FINAL SUMMARY")
    print("=" * 70)
    
    for method_name, stats in results_summary.items():
        status = "✅" if stats['found_target'] else "❌"
        error_info = f" (ERROR: {stats.get('error', '')})" if 'error' in stats else ""
        
        print(f"{status} {method_name}:")
        print(f"   📊 Sonuç Sayısı: {stats['count']}")
        print(f"   🎯 Hedef Belge: {'BULDU' if stats['found_target'] else 'BULAMADI'}")
        if 'best_score' in stats:
            print(f"   📈 Best Score: {stats['best_score']}")
        if error_info:
            print(f"   ⚠️ {error_info}")
        print()
    
    print("🏆 ÖNERİLER:")
    print("   1. sorensenDiceSimilarity: Unicode toleransı yüksek, genel amaçlı")
    print("   2. jaroWinklerDistance: İsimler için optimize, prefix odaklı")
    print("   3. Hybrid Approach: En kapsamlı, birden fazla method kombinasyonu")
    print("   4. CONTAINS: Baseline, exact match gereken durumlarda")
    print("=" * 70)
    
    driver.close()

if __name__ == "__main__":
    test_apoc_similarity_with_correct_encoding()
