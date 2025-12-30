"""
Policyholder → Customer Migrasyon Script'i

Bu script Policyholder node'larını Customer node'larına dönüştürür:
1. STEP 0: Policyholder duplicate'larını merge et (Cypher ile gelişmiş temizleme)
2. STEP 1: Eşleşen Policyholder'lar için ilişkiyi mevcut Customer'a taşır
3. STEP 2: Eşleşmeyen Policyholder'lar için yeni Customer oluşturur
4. STEP 3: Policyholder'ın özel property'lerini Customer'a ekler
5. STEP 4: Eski Policyholder node'larına :Migrated label ekler (geri dönüş için)

Kullanım:
    python migrate_policyholder_to_customer.py --dry-run  # Sadece analiz
    python migrate_policyholder_to_customer.py --execute  # Gerçek migrasyon
    python migrate_policyholder_to_customer.py --rollback # Geri al
"""

import os
import sys
import argparse
from datetime import datetime
from typing import Optional

# .env dosyasını yükle (backend/.env)
from dotenv import load_dotenv
load_dotenv()

# Neo4j driver
from neo4j import GraphDatabase

# Environment variables - .env'den okunur
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")


# =============================================================================
# NORMALIZATION FUNCTION (Cypher UDF gibi davranır)
# =============================================================================
NORMALIZE_NAME_CYPHER = """
// Gelişmiş isim normalizasyonu
// 1. apoc.text.clean() - boşluk, özel karakter, case temizleme
// 2. Türkçe karakter normalizasyonu (ı -> i)
// 3. A.Ş. / ANONİM ŞİRKETİ birleştirme
// 4. Sayı prefix temizleme

WITH ph,
     replace(
       replace(
         replace(
           replace(
             replace(
               replace(
                 apoc.text.clean(ph.name),
                 'ı', 'i'
               ),
               'anosimsirketi', 'as'
             ),
             'anonimsirketi', 'as'
           ),
           'limitedsirketi', 'ltd'
         ),
         'limitedsti', 'ltd'
       ),
       'sti', ''
     ) AS cleaned_step1
WITH ph, cleaned_step1,
     // Sayı prefix'lerini temizle (baştaki sayıları kaldır)
     CASE 
         WHEN cleaned_step1 =~ '^[0-9]+.*'
         THEN substring(cleaned_step1, 
              size(head(apoc.text.regexGroups(cleaned_step1, '^([0-9]+)')[0])))
         ELSE cleaned_step1
     END AS normalized_name
"""


class PolicyholderMigrator:
    def __init__(self, uri: str, username: str, password: str, database: str):
        self.driver = GraphDatabase.driver(uri, auth=(username, password))
        self.database = database
        self.migration_timestamp = datetime.now().isoformat()
    
    def close(self):
        self.driver.close()
    
    def analyze(self) -> dict:
        """Migrasyon öncesi analiz"""
        with self.driver.session(database=self.database) as session:
            # Genel istatistikler
            stats = session.run("""
                MATCH (c:Customer)
                WITH count(c) as totalCustomers
                
                MATCH (ph:Policyholder)
                OPTIONAL MATCH (p:Policy)-[:HAS_POLICYHOLDER]->(ph)
                WITH totalCustomers, ph, count(p) as policyCount
                
                OPTIONAL MATCH (c:Customer) WHERE toLower(c.name) = toLower(ph.name)
                
                RETURN 
                    totalCustomers,
                    count(ph) as totalPolicyholders,
                    sum(policyCount) as totalRelations,
                    count(c) as exactMatches,
                    count(CASE WHEN c IS NULL THEN 1 END) as noMatchPolicyholders
            """).single()
            
            # Duplicate analizi
            duplicate_stats = session.run("""
                MATCH (ph:Policyholder)
                WITH ph,
                     replace(
                       replace(
                         replace(
                           replace(
                             apoc.text.clean(ph.name),
                             'ı', 'i'
                           ),
                           'anosimsirketi', 'as'
                         ),
                         'anonimsirketi', 'as'
                       ),
                       'limitedsirketi', 'ltd'
                     ) AS cleaned_step1
                WITH ph,
                     CASE 
                         WHEN cleaned_step1 =~ '^[0-9]+.*'
                         THEN substring(cleaned_step1, 
                              size(head(apoc.text.regexGroups(cleaned_step1, '^([0-9]+)')[0])))
                         ELSE cleaned_step1
                     END AS normalized_name
                WITH normalized_name, COLLECT(ph) AS duplicates
                RETURN 
                    count(*) as uniqueGroups,
                    sum(CASE WHEN size(duplicates) > 1 THEN size(duplicates) - 1 ELSE 0 END) as duplicatesToMerge,
                    max(size(duplicates)) as maxDuplicates
            """).single()
            
            # Özel property'ler
            props = session.run("""
                MATCH (ph:Policyholder)
                RETURN 
                    count(CASE WHEN ph.tcIdentityNumber IS NOT NULL THEN 1 END) as withTcId,
                    count(CASE WHEN ph.email IS NOT NULL THEN 1 END) as withEmail,
                    count(CASE WHEN ph.mobilePhone IS NOT NULL THEN 1 END) as withPhone,
                    count(CASE WHEN ph.landlinePhone IS NOT NULL THEN 1 END) as withLandline,
                    count(CASE WHEN ph.nationality IS NOT NULL THEN 1 END) as withNationality,
                    count(CASE WHEN ph.role IS NOT NULL THEN 1 END) as withRole
            """).single()
            
            if stats is None or duplicate_stats is None or props is None:
                return {
                    "error": "Query returned no results",
                    "total_customers": 0,
                    "total_policyholders": 0,
                    "total_relations": 0,
                    "exact_matches": 0,
                    "no_match": 0,
                    "duplicate_analysis": {"unique_groups": 0, "duplicates_to_merge": 0, "max_duplicates": 0},
                    "properties": {"tcIdentityNumber": 0, "email": 0, "mobilePhone": 0, "landlinePhone": 0, "nationality": 0, "role": 0}
                }
            
            return {
                "total_customers": stats["totalCustomers"],
                "total_policyholders": stats["totalPolicyholders"],
                "total_relations": stats["totalRelations"],
                "exact_matches": stats["exactMatches"],
                "no_match": stats["noMatchPolicyholders"],
                "duplicate_analysis": {
                    "unique_groups": duplicate_stats["uniqueGroups"],
                    "duplicates_to_merge": duplicate_stats["duplicatesToMerge"],
                    "max_duplicates": duplicate_stats["maxDuplicates"],
                },
                "properties": {
                    "tcIdentityNumber": props["withTcId"],
                    "email": props["withEmail"],
                    "mobilePhone": props["withPhone"],
                    "landlinePhone": props["withLandline"],
                    "nationality": props["withNationality"],
                    "role": props["withRole"],
                }
            }
    
    def execute_migration(self, batch_size: int = 500) -> dict:
        """
        Migrasyonu çalıştır
        
        ADIMLAR:
        0. Policyholder duplicate'larını merge et
        1. Eşleşen Policyholder'ları mevcut Customer'a bağla
        2. Eşleşmeyen Policyholder'lar için yeni Customer oluştur
        3. HAS_POLICYHOLDER ilişkilerini temizle
        
        YENİ YAPI:
            Customer -[:HAS_POLICY]-> Policy  (mevcut pattern ile aynı)
        """
        results = {
            "duplicates_merged": 0,
            "matched_migrated": 0,
            "new_customers_created": 0,
            "relations_updated": 0,
            "errors": []
        }
        
        with self.driver.session(database=self.database) as session:
            # ================================================================
            # STEP 0: Policyholder duplicate'larını merge et
            # ================================================================
            print("=" * 60)
            print("📌 STEP 0: Policyholder Duplicate Merge")
            print("=" * 60)
            
            # Önce duplicate gruplarını bul
            duplicate_groups = session.run("""
                MATCH (ph:Policyholder)
                WHERE NOT ph:MergedPolicyholder
                WITH ph,
                     replace(
                       replace(
                         replace(
                           replace(
                             apoc.text.clean(ph.name),
                             'ı', 'i'
                           ),
                           'anosimsirketi', 'as'
                         ),
                         'anonimsirketi', 'as'
                       ),
                       'limitedsirketi', 'ltd'
                     ) AS cleaned_step1
                WITH ph,
                     CASE 
                         WHEN cleaned_step1 =~ '^[0-9]+.*'
                         THEN substring(cleaned_step1, 
                              size(head(apoc.text.regexGroups(cleaned_step1, '^([0-9]+)')[0])))
                         ELSE cleaned_step1
                     END AS normalized_name
                WITH normalized_name, COLLECT(ph) AS duplicates
                WHERE size(duplicates) > 1
                RETURN normalized_name, size(duplicates) as cnt
                ORDER BY cnt DESC
            """).data()
            
            print(f"   📊 {len(duplicate_groups)} duplicate grup bulundu")
            
            # Her grup için merge işlemi
            total_merged = 0
            for i, group in enumerate(duplicate_groups):
                norm_name = group["normalized_name"]
                cnt = group["cnt"]
                
                if i < 5 or i % 100 == 0:  # İlk 5 ve her 100'de bir göster
                    print(f"   🔄 [{i+1}/{len(duplicate_groups)}] {norm_name[:40]}... ({cnt} duplicate)")
                
                # Bu grup için merge - basitleştirilmiş versiyon
                merge_result = session.run("""
                    // 1. Bu normalized_name için tüm Policyholder'ları bul
                    MATCH (ph:Policyholder)
                    WHERE NOT ph:MergedPolicyholder
                    WITH ph,
                         replace(
                           replace(
                             replace(
                               replace(
                                 apoc.text.clean(ph.name),
                                 'ı', 'i'
                               ),
                               'anosimsirketi', 'as'
                             ),
                             'anonimsirketi', 'as'
                           ),
                           'limitedsirketi', 'ltd'
                         ) AS cleaned_step1
                    WITH ph,
                         CASE 
                             WHEN cleaned_step1 =~ '^[0-9]+.*'
                             THEN substring(cleaned_step1, 
                                  size(head(apoc.text.regexGroups(cleaned_step1, '^([0-9]+)')[0])))
                             ELSE cleaned_step1
                         END AS normalized_name
                    WHERE normalized_name = $norm_name
                    WITH COLLECT(ph) AS duplicates
                    WHERE size(duplicates) > 1
                    
                    // 2. İlk elemanı canonical olarak al (en basit yaklaşım)
                    WITH duplicates, duplicates[0] AS canonical
                    
                    // 3. Diğer duplicate'ler için işlem yap
                    UNWIND duplicates AS dup
                    WITH canonical, dup
                    WHERE dup <> canonical
                    
                    // 4. Duplicate'in Policy ilişkilerini bul
                    OPTIONAL MATCH (p:Policy)-[r:HAS_POLICYHOLDER]->(dup)
                    
                    // 5. İlişkiyi canonical'a taşı
                    WITH canonical, dup, p, r
                    WHERE p IS NOT NULL
                    MERGE (p)-[:HAS_POLICYHOLDER]->(canonical)
                    DELETE r
                    
                    // 6. Property'leri zenginleştir
                    WITH canonical, dup
                    SET canonical.mergedFrom = coalesce(canonical.mergedFrom, []) + [dup.id],
                        canonical.mergedNames = coalesce(canonical.mergedNames, []) + [dup.name],
                        canonical.tcIdentityNumber = coalesce(canonical.tcIdentityNumber, dup.tcIdentityNumber),
                        canonical.email = coalesce(canonical.email, dup.email),
                        canonical.mobilePhone = coalesce(canonical.mobilePhone, dup.mobilePhone),
                        canonical.landlinePhone = coalesce(canonical.landlinePhone, dup.landlinePhone),
                        canonical.nationality = coalesce(canonical.nationality, dup.nationality)
                    
                    // 7. Duplicate'i işaretle
                    SET dup:MergedPolicyholder,
                        dup.mergedInto = canonical.id,
                        dup.mergeTimestamp = $timestamp
                    
                    RETURN count(dup) as merged
                """, norm_name=norm_name, timestamp=self.migration_timestamp).single()
                
                if merge_result:
                    total_merged += merge_result["merged"]
            
            results["duplicates_merged"] = total_merged
            print(f"\n   ✅ Toplam {total_merged} duplicate merge edildi")
            
            # ================================================================
            # STEP 1: Customer schema'sına yeni property'ler ekle
            # ================================================================
            print("\n" + "=" * 60)
            print("📌 STEP 1: Customer Schema Güncelleme")
            print("=" * 60)
            try:
                session.run("CREATE INDEX customer_tcid IF NOT EXISTS FOR (c:Customer) ON (c.tcIdentityNumber)")
                session.run("CREATE INDEX customer_email IF NOT EXISTS FOR (c:Customer) ON (c.email)")
                print("   ✅ Index'ler oluşturuldu")
            except Exception as e:
                print(f"   ⚠️ Index oluşturma uyarısı: {e}")
            
            # ================================================================
            # STEP 2: Eşleşen Policyholder'ları mevcut Customer'a bağla
            # ================================================================
            print("\n" + "=" * 60)
            print("📌 STEP 2: Eşleşen Policyholder'ları Customer'a Bağla")
            print("=" * 60)
            
            matched_result = session.run("""
                MATCH (ph:Policyholder)
                WHERE NOT ph:MergedPolicyholder AND NOT ph:MigratedPolicyholder
                
                // Mevcut Customer ile eşleştir
                MATCH (c:Customer) 
                WHERE apoc.text.clean(c.name) = apoc.text.clean(ph.name)
                   OR apoc.text.clean(coalesce(c.fullName, '')) = apoc.text.clean(ph.name)
                
                // Policy ilişkilerini bul
                MATCH (p:Policy)-[r:HAS_POLICYHOLDER]->(ph)
                
                // Customer'a property'leri ekle
                SET c.tcIdentityNumber = COALESCE(c.tcIdentityNumber, ph.tcIdentityNumber),
                    c.email = COALESCE(c.email, ph.email),
                    c.mobilePhone = COALESCE(c.mobilePhone, ph.mobilePhone),
                    c.landlinePhone = COALESCE(c.landlinePhone, ph.landlinePhone),
                    c.nationality = COALESCE(c.nationality, ph.nationality),
                    c.policyholderRole = COALESCE(c.policyholderRole, ph.role),
                    c.migratedFromPolicyholder = true,
                    c.migrationTimestamp = $timestamp
                
                // YENİ İLİŞKİ: Customer -> Policy (mevcut pattern ile aynı!)
                MERGE (c)-[:HAS_POLICY]->(p)
                
                // Eski Policy -> Policyholder ilişkisini sil
                DELETE r
                
                // Policyholder'a migrated label ekle
                SET ph:MigratedPolicyholder,
                    ph.migratedTo = c.id,
                    ph.migrationTimestamp = $timestamp
                
                RETURN count(DISTINCT ph) as migrated, count(DISTINCT p) as relations
            """, timestamp=self.migration_timestamp).single()
            
            results["matched_migrated"] = matched_result["migrated"] if matched_result else 0
            results["relations_updated"] = matched_result["relations"] if matched_result else 0
            print(f"   ✅ {results['matched_migrated']} Policyholder mevcut Customer'a bağlandı")
            print(f"   ✅ {results['relations_updated']} Customer -[:HAS_POLICY]-> Policy ilişkisi oluşturuldu")
            
            # ================================================================
            # STEP 3: Eşleşmeyen Policyholder'lar için yeni Customer oluştur
            # ================================================================
            print("\n" + "=" * 60)
            print("📌 STEP 3: Yeni Customer'lar Oluştur")
            print("=" * 60)
            
            unmatched_result = session.run("""
                MATCH (ph:Policyholder)
                WHERE NOT ph:MergedPolicyholder 
                  AND NOT ph:MigratedPolicyholder
                  AND NOT EXISTS {
                      MATCH (c:Customer) 
                      WHERE apoc.text.clean(c.name) = apoc.text.clean(ph.name)
                  }
                
                // Yeni Customer oluştur
                CREATE (c:Customer {
                    id: 'customer_from_ph_' + coalesce(ph.id, randomUUID()),
                    name: ph.name,
                    fullName: ph.name,
                    type: CASE 
                        WHEN ph.tcIdentityNumber IS NOT NULL AND size(coalesce(ph.tcIdentityNumber, '')) = 11 
                        THEN 'Individual' 
                        ELSE 'Corporate' 
                    END,
                    tcIdentityNumber: ph.tcIdentityNumber,
                    email: ph.email,
                    mobilePhone: ph.mobilePhone,
                    landlinePhone: ph.landlinePhone,
                    nationality: ph.nationality,
                    policyholderRole: ph.role,
                    createdAt: datetime(),
                    updatedAt: datetime(),
                    createdFromPolicyholder: true,
                    migrationTimestamp: $timestamp
                })
                
                // Policy ilişkilerini taşı
                WITH ph, c
                MATCH (p:Policy)-[r:HAS_POLICYHOLDER]->(ph)
                
                // YENİ İLİŞKİ: Customer -> Policy
                MERGE (c)-[:HAS_POLICY]->(p)
                
                // Eski ilişkiyi sil
                DELETE r
                
                // Policyholder'a migrated label ekle
                SET ph:MigratedPolicyholder,
                    ph.migratedTo = c.id,
                    ph.migrationTimestamp = $timestamp
                
                RETURN count(DISTINCT c) as created, count(DISTINCT p) as relations
            """, timestamp=self.migration_timestamp).single()
            
            results["new_customers_created"] = unmatched_result["created"] if unmatched_result else 0
            results["relations_updated"] += unmatched_result["relations"] if unmatched_result else 0
            print(f"   ✅ {results['new_customers_created']} yeni Customer oluşturuldu")
            print(f"   ✅ {unmatched_result['relations'] if unmatched_result else 0} Customer -[:HAS_POLICY]-> Policy ilişkisi oluşturuldu")
            
            # ================================================================
            # STEP 4: Kalan HAS_POLICYHOLDER ilişkilerini temizle
            # ================================================================
            print("\n" + "=" * 60)
            print("📌 STEP 4: Temizlik")
            print("=" * 60)
            
            cleanup_result = session.run("""
                MATCH ()-[r:HAS_POLICYHOLDER]->()
                DELETE r
                RETURN count(r) as deleted
            """).single()
            print(f"   ✅ {cleanup_result['deleted'] if cleanup_result else 0} eski HAS_POLICYHOLDER ilişkisi silindi")
            
            # ================================================================
            # STEP 5: Merged Policyholder node'larını sil (opsiyonel)
            # ================================================================
            print("\n" + "=" * 60)
            print("📌 STEP 5: Doğrulama")
            print("=" * 60)
            
            # Doğrulama
            verification = session.run("""
                MATCH (ph:Policyholder)
                WHERE NOT ph:MergedPolicyholder AND NOT ph:MigratedPolicyholder
                RETURN count(ph) as remaining
            """).single()
            
            if verification and verification["remaining"] > 0:
                results["errors"].append(f"⚠️ {verification['remaining']} Policyholder migrate edilemedi!")
                print(f"   ⚠️ {verification['remaining']} Policyholder migrate edilemedi!")
            else:
                print("   ✅ Tüm Policyholder'lar başarıyla işlendi!")
            
            # Yeni yapıyı doğrula
            new_structure = session.run("""
                MATCH (c:Customer)-[:HAS_POLICY]->(p:Policy)
                RETURN count(DISTINCT c) as customers, count(DISTINCT p) as policies, count(*) as relations
            """).single()
            
            print(f"\n📊 YENİ YAPI:")
            if new_structure is not None:
                print(f"   Customer sayısı: {new_structure['customers']}")
                print(f"   Policy sayısı: {new_structure['policies']}")
                print(f"   Customer -[:HAS_POLICY]-> Policy ilişkisi: {new_structure['relations']}")
            else:
                print("   Yeni yapı sorgusu sonuç döndürmedi.")
        
        return results
    
    def rollback(self) -> dict:
        """
        Migrasyonu geri al
        
        DİKKAT: Bu işlem migrasyondan sonra yapılan değişiklikleri etkileyebilir!
        Sadece migrasyon hemen ardından kullanılmalıdır.
        """
        results = {
            "restored": 0,
            "relations_restored": 0,
            "customers_deleted": 0,
            "merged_restored": 0
        }
        
        with self.driver.session(database=self.database) as session:
            # Step 1: MigratedPolicyholder'ların ilişkilerini geri al
            print("📌 Step 1: Migrated Policyholder'ların ilişkileri geri yükleniyor...")
            
            restore_result = session.run("""
                MATCH (ph:MigratedPolicyholder)
                WHERE ph.migratedTo IS NOT NULL
                
                // Migrate edildiği Customer'ı bul
                MATCH (c:Customer)
                WHERE c.id = ph.migratedTo OR c.id CONTAINS ph.migratedTo
                
                // Customer'ın Policy ilişkilerini Policyholder'a geri taşı
                MATCH (c)-[r:HAS_POLICY]->(p:Policy)
                
                // Eski ilişkiyi geri oluştur
                MERGE (p)-[:HAS_POLICYHOLDER]->(ph)
                
                // Label'ı kaldır
                REMOVE ph:MigratedPolicyholder
                REMOVE ph.migratedTo
                REMOVE ph.migrationTimestamp
                
                // Migrasyon ile eklenen ilişkiyi sil
                // (Dikkat: Sadece migrasyon timestamp'ına sahip Customer'ların ilişkileri)
                WITH ph, c, p, r
                WHERE c.migrationTimestamp IS NOT NULL
                DELETE r
                
                RETURN count(DISTINCT ph) as restored
            """).single()
            
            results["restored"] = restore_result["restored"] if restore_result else 0
            print(f"   ✅ {results['restored']} Policyholder geri yüklendi")
            
            # Step 2: MergedPolicyholder'ları geri yükle
            print("📌 Step 2: Merged Policyholder'lar geri yükleniyor...")
            
            merged_restore = session.run("""
                MATCH (ph:MergedPolicyholder)
                REMOVE ph:MergedPolicyholder
                REMOVE ph.mergedInto
                REMOVE ph.mergeTimestamp
                RETURN count(ph) as restored
            """).single()
            
            results["merged_restored"] = merged_restore["restored"] if merged_restore else 0
            print(f"   ✅ {results['merged_restored']} merged Policyholder geri yüklendi")
            
            # Step 3: Migrasyon ile oluşturulan Customer'ları sil
            print("📌 Step 3: Migrasyon ile oluşturulan Customer'lar siliniyor...")
            
            delete_result = session.run("""
                MATCH (c:Customer)
                WHERE c.createdFromPolicyholder = true
                DETACH DELETE c
                RETURN count(c) as deleted
            """).single()
            
            results["customers_deleted"] = delete_result["deleted"] if delete_result else 0
            print(f"   ✅ {results['customers_deleted']} Customer silindi")
            
            # Step 4: Mevcut Customer'lardaki ek property'leri temizle
            print("📌 Step 4: Customer property'leri temizleniyor...")
            
            session.run("""
                MATCH (c:Customer)
                WHERE c.migratedFromPolicyholder = true
                REMOVE c.migratedFromPolicyholder
                REMOVE c.migrationTimestamp
            """)
            print("   ✅ Property'ler temizlendi")
            
            # Doğrulama
            print("\n📊 ROLLBACK SONRASI DURUM:")
            verification = session.run("""
                MATCH (ph:Policyholder)
                OPTIONAL MATCH (p:Policy)-[:HAS_POLICYHOLDER]->(ph)
                RETURN count(DISTINCT ph) as policyholders, count(DISTINCT p) as connectedPolicies
            """).single()
            if verification is not None:
                print(f"   Policyholder sayısı: {verification['policyholders']}")
                print(f"   Policy'ye bağlı Policyholder: {verification['connectedPolicies']}")
            else:
                print("   Doğrulama sorgusu sonuç döndürmedi.")
        
        return results
    
    def cleanup_merged(self) -> dict:
        """Merged ve Migrated Policyholder node'larını tamamen sil"""
        with self.driver.session(database=self.database) as session:
            result = session.run("""
                MATCH (ph)
                WHERE ph:MergedPolicyholder OR ph:MigratedPolicyholder
                DETACH DELETE ph
                RETURN count(ph) as deleted
            """).single()
            
            return {"deleted": result["deleted"] if result else 0}


def main():
    parser = argparse.ArgumentParser(description="Policyholder → Customer Migrasyon Script'i")
    parser.add_argument("--dry-run", action="store_true", help="Sadece analiz yap, değişiklik yapma")
    parser.add_argument("--execute", action="store_true", help="Migrasyonu çalıştır")
    parser.add_argument("--rollback", action="store_true", help="Migrasyonu geri al")
    parser.add_argument("--cleanup", action="store_true", help="Merged/Migrated node'ları tamamen sil")
    args = parser.parse_args()
    
    if not any([args.dry_run, args.execute, args.rollback, args.cleanup]):
        parser.print_help()
        print("\n⚠️ Lütfen --dry-run, --execute, --rollback veya --cleanup seçeneklerinden birini belirtin.")
        sys.exit(1)
    
    print("=" * 70)
    print("POLICYHOLDER → CUSTOMER MİGRASYON ARACI")
    print("=" * 70)
    print(f"Neo4j URI: {NEO4J_URI}")
    print(f"Database: {NEO4J_DATABASE}")
    print()
    
    migrator = PolicyholderMigrator(
        uri=NEO4J_URI,
        username=NEO4J_USERNAME,
        password=NEO4J_PASSWORD,
        database=NEO4J_DATABASE
    )
    
    try:
        if args.dry_run:
            print("🔍 DRY RUN - Analiz yapılıyor...\n")
            analysis = migrator.analyze()
            
            print("📊 MEVCUT DURUM:")
            print(f"   Toplam Customer: {analysis['total_customers']}")
            print(f"   Toplam Policyholder: {analysis['total_policyholders']}")
            print(f"   Toplam HAS_POLICYHOLDER ilişkisi: {analysis['total_relations']}")
            print()
            
            print("🔄 DUPLICATE ANALİZİ:")
            dup = analysis['duplicate_analysis']
            print(f"   Unique gruplar (merge sonrası): {dup['unique_groups']}")
            print(f"   Merge edilecek duplicate: {dup['duplicates_to_merge']}")
            print(f"   En büyük duplicate grubu: {dup['max_duplicates']} kayıt")
            print()
            
            print("📋 EŞLEŞME ANALİZİ:")
            print(f"   Customer ile eşleşen: {analysis['exact_matches']} ({analysis['exact_matches']/max(analysis['total_policyholders'],1)*100:.1f}%)")
            print(f"   Eşleşmeyen (yeni Customer olacak): {analysis['no_match']} ({analysis['no_match']/max(analysis['total_policyholders'],1)*100:.1f}%)")
            print()
            
            print("🔑 POLICYHOLDER PROPERTY'LERİ (Customer'a taşınacak):")
            for prop, count in analysis['properties'].items():
                print(f"   {prop}: {count} kayıt")
            print()
            
            print("📝 MİGRASYON PLANI:")
            print(f"   1. {dup['duplicates_to_merge']} Policyholder merge edilecek")
            print(f"   2. {dup['unique_groups']} unique Policyholder kalacak")
            print(f"   3. {analysis['exact_matches']} Policyholder → mevcut Customer'a bağlanacak")
            estimated_new = analysis['no_match'] - dup['duplicates_to_merge']
            print(f"   4. ~{max(0, estimated_new)} yeni Customer oluşturulacak (tahmini)")
            print(f"   5. Migrasyon sonrası toplam Customer: ~{analysis['total_customers'] + max(0, estimated_new)}")
            print()
            print("⚠️ Migrasyonu çalıştırmak için: python migrate_policyholder_to_customer.py --execute")
            print("⚠️ Geri almak için: python migrate_policyholder_to_customer.py --rollback")
        
        elif args.execute:
            print("🚀 MİGRASYON BAŞLIYOR...\n")
            
            # Önce analiz
            analysis = migrator.analyze()
            dup = analysis['duplicate_analysis']
            
            print(f"📊 İşlenecek veriler:")
            print(f"   - {analysis['total_policyholders']} Policyholder")
            print(f"   - {dup['duplicates_to_merge']} duplicate merge edilecek")
            print(f"   - {dup['unique_groups']} unique grup oluşacak")
            print()
            
            confirm = input("Devam etmek istiyor musunuz? (yes/no): ")
            if confirm.lower() != "yes":
                print("İptal edildi.")
                sys.exit(0)
            
            print()
            results = migrator.execute_migration()
            
            print()
            print("=" * 70)
            print("✅ MİGRASYON TAMAMLANDI!")
            print("=" * 70)
            print(f"   Merge edilen duplicate: {results['duplicates_merged']}")
            print(f"   Mevcut Customer'a bağlanan: {results['matched_migrated']}")
            print(f"   Yeni oluşturulan Customer: {results['new_customers_created']}")
            print(f"   Güncellenen ilişki: {results['relations_updated']}")
            if results['errors']:
                print(f"   ⚠️ Hatalar: {results['errors']}")
            print()
            print("💡 Merged/Migrated node'ları silmek için: python migrate_policyholder_to_customer.py --cleanup")
        
        elif args.rollback:
            print("🔄 ROLLBACK BAŞLIYOR...\n")
            
            confirm = input("Migrasyonu geri almak istiyor musunuz? (yes/no): ")
            if confirm.lower() != "yes":
                print("İptal edildi.")
                sys.exit(0)
            
            results = migrator.rollback()
            
            print()
            print("=" * 70)
            print("✅ ROLLBACK TAMAMLANDI!")
            print("=" * 70)
            print(f"   Geri yüklenen Policyholder: {results['restored']}")
            print(f"   Geri yüklenen merged: {results['merged_restored']}")
            print(f"   Silinen Customer: {results['customers_deleted']}")
        
        elif args.cleanup:
            print("🧹 CLEANUP BAŞLIYOR...\n")
            
            confirm = input("Merged/Migrated Policyholder node'larını TAMAMEN silmek istiyor musunuz? Bu işlem GERİ ALINAMAZ! (yes/no): ")
            if confirm.lower() != "yes":
                print("İptal edildi.")
                sys.exit(0)
            
            results = migrator.cleanup_merged()
            
            print()
            print("=" * 70)
            print("✅ CLEANUP TAMAMLANDI!")
            print("=" * 70)
            print(f"   Silinen node: {results['deleted']}")
    
    finally:
        migrator.close()


if __name__ == "__main__":
    main()
