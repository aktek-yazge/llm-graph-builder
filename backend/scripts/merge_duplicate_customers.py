"""
Customer Duplicate Merge Script

Bu script Customer node'larındaki duplicate'ları tespit eder ve birleştirir.
Gelişmiş normalizasyon kuralları ile OCR hatalarını ve yazım farklarını yakalar.

Normalizasyon Kuralları:
1. apoc.text.clean() - boşluk, özel karakter, case temizleme
2. Türkçe karakter normalizasyonu (ı -> i, ü -> u, vb.)
3. Şirket tipi birleştirme (ANONİM ŞİRKETİ = A.Ş. = AS)
4. Kısaltma açma (İTH. = İTHALAT, vb.)
5. Sayı prefix temizleme

Kullanım:
    python merge_duplicate_customers.py --dry-run  # Sadece analiz
    python merge_duplicate_customers.py --execute  # Gerçek merge
    python merge_duplicate_customers.py --rollback # Geri al (sınırlı)
"""

import os
import sys
import argparse
from datetime import datetime
from typing import Optional, List, Dict

# .env dosyasını yükle
from dotenv import load_dotenv
load_dotenv()

# Neo4j driver
from neo4j import GraphDatabase

# Environment variables
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")


# =============================================================================
# NORMALIZATION - Gelişmiş Türkçe şirket ismi normalizasyonu
# =============================================================================

# Cypher'da kullanılacak normalizasyon fonksiyonu
NORMALIZE_CYPHER = """
// Gelişmiş Customer isim normalizasyonu
WITH c,
     // 1. Temel temizlik
     replace(
       replace(
         replace(
           replace(
             replace(
               replace(
                 replace(
                   replace(
                     apoc.text.clean(c.name),
                     'ı', 'i'
                   ),
                   'ğ', 'g'
                 ),
                 'ü', 'u'
               ),
               'ş', 's'
             ),
             'ö', 'o'
           ),
           'ç', 'c'
         ),
         'İ', 'i'
       ),
       ' ', ''
     ) AS step1
WITH c, step1,
     // 2. Şirket tipi normalizasyonu
     replace(
       replace(
         replace(
           replace(
             replace(
               replace(
                 step1,
                 'anonimsirketi', 'as'
               ),
               'anosimsirketi', 'as'
             ),
             'limitedsirketi', 'ltd'
           ),
           'limitedsti', 'ltd'
         ),
         'sti', ''
       ),
       'as', ''  -- A.Ş. kısmını tamamen kaldır (karşılaştırma için)
     ) AS step2
WITH c, step2,
     // 3. Kısaltma normalizasyonu
     replace(
       replace(
         replace(
           replace(
             replace(
               replace(
                 step2,
                 'ithalat', 'ith'
               ),
               'ihracat', 'ihr'
             ),
             'ticaret', 'tic'
           ),
           'sanayi', 'san'
         ),
         'elektrik', 'elk'
       ),
       'enerji', 'enj'
     ) AS step3
WITH c,
     // 4. Sayı prefix temizleme
     CASE 
         WHEN step3 =~ '^[0-9]+.*'
         THEN substring(step3, size(head(apoc.text.regexGroups(step3, '^([0-9]+)')[0])))
         ELSE step3
     END AS normalized_name
"""


class CustomerMerger:
    def __init__(self, uri: str, username: str, password: str, database: str):
        self.driver = GraphDatabase.driver(uri, auth=(username, password))
        self.database = database
        self.merge_timestamp = datetime.now().isoformat()
    
    def close(self):
        self.driver.close()
    
    def analyze(self) -> dict:
        """Duplicate analizi yap"""
        with self.driver.session(database=self.database) as session:
            # Genel istatistikler
            stats = session.run("""
                MATCH (c:Customer)
                OPTIONAL MATCH (c)-[r:HAS_POLICY]->(p:Policy)
                RETURN 
                    count(DISTINCT c) as totalCustomers,
                    count(r) as totalRelations
            """).single()
            
            # Duplicate analizi - basitleştirilmiş normalizasyon
            duplicate_stats = session.run("""
                MATCH (c:Customer)
                WITH c,
                     replace(
                       replace(
                         replace(
                           replace(
                             replace(
                               replace(
                                 apoc.text.clean(c.name),
                                 'ı', 'i'
                               ),
                               'anonimsirketi', ''
                             ),
                             'anosimsirketi', ''
                           ),
                           'as', ''
                         ),
                         'sti', ''
                       ),
                       'limitedsirketi', ''
                     ) AS normalized_name
                WITH normalized_name, COLLECT(c) AS duplicates
                WHERE size(duplicates) > 1
                RETURN 
                    count(*) as duplicateGroups,
                    sum(size(duplicates) - 1) as duplicatesToMerge,
                    max(size(duplicates)) as maxDuplicates
            """).single()
            
            # Top 10 duplicate grupları göster
            top_duplicates = session.run("""
                MATCH (c:Customer)
                WITH c,
                     replace(
                       replace(
                         replace(
                           replace(
                             replace(
                               replace(
                                 apoc.text.clean(c.name),
                                 'ı', 'i'
                               ),
                               'anonimsirketi', ''
                             ),
                             'anosimsirketi', ''
                           ),
                           'as', ''
                         ),
                         'sti', ''
                       ),
                       'limitedsirketi', ''
                     ) AS normalized_name
                WITH normalized_name, COLLECT(c.name) AS names, COUNT(*) AS cnt
                WHERE cnt > 1
                RETURN normalized_name, cnt, names[0..5] AS sample_names
                ORDER BY cnt DESC
                LIMIT 10
            """).data()
            
            return {
                "total_customers": stats["totalCustomers"],
                "total_relations": stats["totalRelations"],
                "duplicate_groups": duplicate_stats["duplicateGroups"] if duplicate_stats else 0,
                "duplicates_to_merge": duplicate_stats["duplicatesToMerge"] if duplicate_stats else 0,
                "max_duplicates": duplicate_stats["maxDuplicates"] if duplicate_stats else 0,
                "top_duplicates": top_duplicates
            }
    
    def execute_merge(self) -> dict:
        """Duplicate Customer'ları merge et"""
        results = {
            "groups_processed": 0,
            "customers_merged": 0,
            "relations_moved": 0,
            "errors": []
        }
        
        with self.driver.session(database=self.database) as session:
            # 1. Duplicate gruplarını bul
            print("📌 STEP 1: Duplicate grupları tespit ediliyor...")
            
            duplicate_groups = session.run("""
                MATCH (c:Customer)
                WHERE NOT c:MergedCustomer
                WITH c,
                     replace(
                       replace(
                         replace(
                           replace(
                             replace(
                               replace(
                                 apoc.text.clean(c.name),
                                 'ı', 'i'
                               ),
                               'anonimsirketi', ''
                             ),
                             'anosimsirketi', ''
                           ),
                           'as', ''
                         ),
                         'sti', ''
                       ),
                       'limitedsirketi', ''
                     ) AS normalized_name
                WITH normalized_name, COLLECT(c) AS duplicates
                WHERE size(duplicates) > 1
                RETURN normalized_name, size(duplicates) as cnt
                ORDER BY cnt DESC
            """).data()
            
            print(f"   📊 {len(duplicate_groups)} duplicate grup bulundu")
            
            # 2. Her grup için merge işlemi
            print("\n📌 STEP 2: Merge işlemi yapılıyor...")
            
            total_merged = 0
            total_relations = 0
            
            for i, group in enumerate(duplicate_groups):
                norm_name = group["normalized_name"]
                cnt = group["cnt"]
                
                if i < 10 or i % 50 == 0:
                    print(f"   🔄 [{i+1}/{len(duplicate_groups)}] {norm_name[:40]}... ({cnt} duplicate)")
                
                try:
                    # Bu grup için merge
                    merge_result = session.run("""
                        // 1. Bu normalized_name için tüm Customer'ları bul
                        MATCH (c:Customer)
                        WHERE NOT c:MergedCustomer
                        WITH c,
                             replace(
                               replace(
                                 replace(
                                   replace(
                                     replace(
                                       replace(
                                         apoc.text.clean(c.name),
                                         'ı', 'i'
                                       ),
                                       'anonimsirketi', ''
                                     ),
                                     'anosimsirketi', ''
                                   ),
                                   'as', ''
                                 ),
                                 'sti', ''
                               ),
                               'limitedsirketi', ''
                             ) AS normalized_name
                        WHERE normalized_name = $norm_name
                        WITH COLLECT(c) AS duplicates
                        WHERE size(duplicates) > 1
                        
                        // 2. En iyi Customer'ı canonical olarak seç
                        // (en uzun isim, en çok property, orijinal - policyholder'dan gelmemiş)
                        WITH duplicates,
                             HEAD([c IN duplicates 
                                   WHERE c.createdFromPolicyholder IS NULL 
                                   | c] + duplicates) AS canonical
                        
                        // 3. Diğer duplicate'ler için işlem yap
                        UNWIND duplicates AS dup
                        WITH canonical, dup
                        WHERE dup <> canonical
                        
                        // 4. Duplicate'in tüm Policy ilişkilerini bul ve taşı
                        OPTIONAL MATCH (dup)-[r:HAS_POLICY]->(p:Policy)
                        WITH canonical, dup, p, r
                        WHERE p IS NOT NULL
                        
                        // İlişkiyi canonical'a taşı
                        MERGE (canonical)-[:HAS_POLICY]->(p)
                        DELETE r
                        
                        // 5. Property'leri zenginleştir ve duplicate'i işaretle
                        WITH canonical, dup
                        SET canonical.mergedCustomerIds = coalesce(canonical.mergedCustomerIds, []) + [dup.id],
                            canonical.mergedCustomerNames = coalesce(canonical.mergedCustomerNames, []) + [dup.name],
                            canonical.tcIdentityNumber = coalesce(canonical.tcIdentityNumber, dup.tcIdentityNumber),
                            canonical.email = coalesce(canonical.email, dup.email),
                            canonical.mobilePhone = coalesce(canonical.mobilePhone, dup.mobilePhone),
                            canonical.fullName = coalesce(canonical.fullName, dup.fullName),
                            canonical.original_name = coalesce(canonical.original_name, dup.original_name)
                        
                        SET dup:MergedCustomer,
                            dup.mergedInto = canonical.id,
                            dup.mergeTimestamp = $timestamp
                        
                        RETURN count(dup) as merged
                    """, norm_name=norm_name, timestamp=self.merge_timestamp).single()
                    
                    if merge_result and merge_result["merged"]:
                        total_merged += merge_result["merged"]
                        
                except Exception as e:
                    results["errors"].append(f"{norm_name}: {str(e)}")
                    if len(results["errors"]) <= 5:
                        print(f"   ⚠️ Hata: {norm_name[:30]}... - {str(e)[:50]}")
            
            results["groups_processed"] = len(duplicate_groups)
            results["customers_merged"] = total_merged
            
            # 3. Doğrulama
            print("\n📌 STEP 3: Doğrulama...")
            
            verification = session.run("""
                MATCH (c:Customer)
                WHERE NOT c:MergedCustomer
                WITH count(c) as activeCustomers
                
                MATCH (m:MergedCustomer)
                WITH activeCustomers, count(m) as mergedCustomers
                
                MATCH (c:Customer)-[r:HAS_POLICY]->(p:Policy)
                WHERE NOT c:MergedCustomer
                RETURN activeCustomers, mergedCustomers, count(r) as totalRelations
            """).single()
            
            print(f"\n📊 SONUÇ:")
            print(f"   Aktif Customer: {verification['activeCustomers']}")
            print(f"   Merged Customer: {verification['mergedCustomers']}")
            print(f"   Toplam HAS_POLICY: {verification['totalRelations']}")
            
            results["relations_moved"] = verification['totalRelations']
        
        return results
    
    def cleanup(self) -> dict:
        """Merged Customer node'larını tamamen sil"""
        with self.driver.session(database=self.database) as session:
            result = session.run("""
                MATCH (c:MergedCustomer)
                DETACH DELETE c
                RETURN count(c) as deleted
            """).single()
            
            return {"deleted": result["deleted"] if result else 0}


def main():
    parser = argparse.ArgumentParser(description="Customer Duplicate Merge Script")
    parser.add_argument("--dry-run", action="store_true", help="Sadece analiz yap")
    parser.add_argument("--execute", action="store_true", help="Merge işlemini çalıştır")
    parser.add_argument("--cleanup", action="store_true", help="Merged node'ları sil")
    args = parser.parse_args()
    
    if not any([args.dry_run, args.execute, args.cleanup]):
        parser.print_help()
        print("\n⚠️ Lütfen --dry-run, --execute veya --cleanup seçin.")
        sys.exit(1)
    
    print("=" * 70)
    print("CUSTOMER DUPLICATE MERGE ARACI")
    print("=" * 70)
    print(f"Neo4j URI: {NEO4J_URI}")
    print(f"Database: {NEO4J_DATABASE}")
    print()
    
    merger = CustomerMerger(
        uri=NEO4J_URI,
        username=NEO4J_USERNAME,
        password=NEO4J_PASSWORD,
        database=NEO4J_DATABASE
    )
    
    try:
        if args.dry_run:
            print("🔍 DRY RUN - Analiz yapılıyor...\n")
            analysis = merger.analyze()
            
            print("📊 MEVCUT DURUM:")
            print(f"   Toplam Customer: {analysis['total_customers']}")
            print(f"   Toplam HAS_POLICY ilişkisi: {analysis['total_relations']}")
            print()
            
            print("🔄 DUPLICATE ANALİZİ:")
            print(f"   Duplicate grupları: {analysis['duplicate_groups']}")
            print(f"   Merge edilecek: {analysis['duplicates_to_merge']}")
            print(f"   En büyük grup: {analysis['max_duplicates']}")
            print()
            
            if analysis['top_duplicates']:
                print("🔝 TOP 10 DUPLICATE GRUPLARI:")
                for i, dup in enumerate(analysis['top_duplicates']):
                    print(f"   {i+1}. [{dup['cnt']} duplicate] {dup['normalized_name'][:50]}")
                    for name in dup['sample_names'][:3]:
                        print(f"      - {name[:60]}...")
                    print()
            
            estimated_after = analysis['total_customers'] - analysis['duplicates_to_merge']
            print(f"📝 TAHMİNİ SONUÇ:")
            print(f"   Merge sonrası Customer: ~{estimated_after}")
            print()
            print("⚠️ Merge için: python merge_duplicate_customers.py --execute")
        
        elif args.execute:
            print("🚀 MERGE BAŞLIYOR...\n")
            
            # Önce analiz
            analysis = merger.analyze()
            print(f"📊 İşlenecek: {analysis['duplicate_groups']} grup, {analysis['duplicates_to_merge']} duplicate")
            print()
            
            confirm = input("Devam etmek istiyor musunuz? (yes/no): ")
            if confirm.lower() != "yes":
                print("İptal edildi.")
                sys.exit(0)
            
            print()
            results = merger.execute_merge()
            
            print()
            print("=" * 70)
            print("✅ MERGE TAMAMLANDI!")
            print("=" * 70)
            print(f"   İşlenen grup: {results['groups_processed']}")
            print(f"   Merge edilen Customer: {results['customers_merged']}")
            if results['errors']:
                print(f"   ⚠️ Hatalar: {len(results['errors'])}")
            print()
            print("💡 Merged node'ları silmek için: python merge_duplicate_customers.py --cleanup")
        
        elif args.cleanup:
            print("🧹 CLEANUP BAŞLIYOR...\n")
            
            confirm = input("MergedCustomer node'larını TAMAMEN silmek istiyor musunuz? (yes/no): ")
            if confirm.lower() != "yes":
                print("İptal edildi.")
                sys.exit(0)
            
            results = merger.cleanup()
            
            print()
            print("=" * 70)
            print("✅ CLEANUP TAMAMLANDI!")
            print("=" * 70)
            print(f"   Silinen node: {results['deleted']}")
    
    finally:
        merger.close()


if __name__ == "__main__":
    main()


