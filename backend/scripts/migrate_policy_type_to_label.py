"""
Policy Tip İlişkilerini Label'a Migrate Et

MEVCUT YAPI:
    (Customer)-[:HAS_POLICY]->(Policy)
    (Customer)-[:IS_TRAFIK_POLICY]->(Policy)  ← Duplicate ilişki!

YENİ YAPI:
    (Customer)-[:HAS_POLICY]->(Policy:TrafikPolicy)  ← Tek ilişki + Label

AVANTAJLAR:
1. Duplicate sorgu sorunu çözülür
2. Tip bilgisi şemada görünür (db.labels())
3. LLM doğru sorgu yazabilir
4. Daha temiz graph yapısı

KULLANIM:
    python migrate_policy_type_to_label.py --analyze
    python migrate_policy_type_to_label.py --migrate
    python migrate_policy_type_to_label.py --rollback
"""

import os
from datetime import datetime

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


# Label mapping - IS_X_POLICY → Label (XPolicy formatında)
LABEL_MAPPING = {
    "IS_BURO_PAKET_POLICY": "BuroPaketPolicy",
    "IS_DASK_POLICY": "DaskPolicy",
    "IS_DIRECTORS_AND_OFFICERS_POLICY": "DirectorsAndOfficersPolicy",
    "IS_ELEKTRONIK_CIHAZ_POLICY": "ElektronikCihazPolicy",
    "IS_ELEMENTER_POLICY": "ElementerPolicy",
    "IS_EMNIYETI_SUISTIMAL_POLICY": "EmniyetiSuistimalPolicy",
    "IS_EMTIA_POLICY": "EmtiaPolicy",
    "IS_FERDI_KAZA_POLICY": "FerdiKazaPolicy",
    "IS_GREV_LOKAVT_KARGASALIK_HALK_HAREKETLERI_TEROR_VE_SIYASI_SIDDET_POLICY": "GrevLokavtPolicy",
    "IS_GUNES_ENERJISI_SANTRALLERI_ARAZI_TIPI_POLICY": "GunesEnerjisiPolicy",
    "IS_HAYAT_POLICY": "HayatPolicy",
    "IS_INSAAT_POLICY": "InsaatPolicy",
    "IS_ISYERI_POLICY": "IsyeriPolicy",
    "IS_KASKO_POLICY": "KaskoPolicy",
    "IS_KOBI_TICARI_POLICY": "KobiTicariPolicy",
    "IS_KONUT_POLICY": "KonutPolicy",
    "IS_MAKINE_KIRILMASI_POLICY": "MakineKirilmasiPolicy",
    "IS_MONTAJ_POLICY": "MontajPolicy",
    "IS_NAKLIYAT_POLICY": "NakliyatPolicy",
    "IS_ORTAK_ALAN_POLICY": "OrtakAlanPolicy",
    "IS_SAGLIK_POLICY": "SaglikPolicy",
    "IS_SANAT_ESERLERI_POLICY": "SanatEserleriPolicy",
    "IS_SORUMLULUK_POLICY": "SorumlulukPolicy",
    "IS_TASINAN_PARA_POLICY": "TasinanParaPolicy",
    "IS_TEHLIKELI_ATIK_POLICY": "TehlikeliAtikPolicy",
    "IS_TEKNE_VE_YAT_POLICY": "TekneVeYatPolicy",
    "IS_TRAFIK_POLICY": "TrafikPolicy",
    "IS_YANGIN_POLICY": "YanginPolicy",
}


class PolicyTypeMigrator:
    def __init__(self, uri: str, username: str, password: str, database: str):
        self.driver = GraphDatabase.driver(uri, auth=(username, password))
        self.database = database
        self.migration_timestamp = datetime.now().isoformat()
    
    def close(self):
        self.driver.close()
    
    def analyze(self) -> dict:
        """Migration öncesi analiz"""
        with self.driver.session(database=self.database) as session:
            # Mevcut IS_X_POLICY ilişkileri
            rel_stats = session.run("""
                MATCH (c:Customer)-[r]->(p:Policy)
                WHERE type(r) STARTS WITH 'IS_' AND type(r) ENDS WITH '_POLICY'
                WITH type(r) AS rel_type, count(DISTINCT p) AS policy_count
                RETURN rel_type, policy_count
                ORDER BY policy_count DESC
            """).data()
            
            # Toplam sayılar
            totals = session.run("""
                MATCH (c:Customer)-[r]->(p:Policy)
                WHERE type(r) STARTS WITH 'IS_' AND type(r) ENDS WITH '_POLICY'
                RETURN count(DISTINCT p) AS total_policies,
                       count(r) AS total_relations,
                       count(DISTINCT type(r)) AS total_types
            """).single()
            
            # HAS_POLICY durumu
            has_policy_stats = session.run("""
                MATCH (c:Customer)-[:HAS_POLICY]->(p:Policy)
                RETURN count(p) AS has_policy_count
            """).single()
            
            # Zaten label'ı olan Policy'ler var mı?
            existing_labels = session.run("""
                MATCH (p:Policy)
                WITH p, labels(p) AS lbls
                WHERE size(lbls) > 1
                RETURN lbls, count(p) AS cnt
                LIMIT 10
            """).data()
            
            if totals is None or has_policy_stats is None:
                return {"error": "Query returned no results"}
            
            return {
                "relation_stats": rel_stats,
                "total_policies_to_migrate": totals["total_policies"],
                "total_relations_to_delete": totals["total_relations"],
                "total_policy_types": totals["total_types"],
                "has_policy_relations": has_policy_stats["has_policy_count"],
                "existing_multi_label_policies": existing_labels,
                "label_mapping": LABEL_MAPPING
            }
    
    def migrate(self, batch_size: int = 500, dry_run: bool = False) -> dict:
        """
        Migration işlemi:
        1. Her IS_X_POLICY ilişkisi için Policy node'una label ekle
        2. IS_X_POLICY ilişkilerini sil
        """
        results = {
            "labels_added": {},
            "relations_deleted": 0,
            "errors": [],
            "dry_run": dry_run
        }
        
        with self.driver.session(database=self.database) as session:
            for rel_type, label in LABEL_MAPPING.items():
                try:
                    # Kaç policy etkilenecek?
                    count_result = session.run(f"""
                        MATCH (c:Customer)-[r:{rel_type}]->(p:Policy)
                        RETURN count(DISTINCT p) AS cnt
                    """).single()  # type: ignore[arg-type]
                    
                    if count_result is None or count_result["cnt"] == 0:
                        continue
                    
                    policy_count = count_result["cnt"]
                    
                    if dry_run:
                        print(f"[DRY RUN] {rel_type} → :{label} ({policy_count} policies)")
                        results["labels_added"][label] = policy_count
                        continue
                    
                    # 1. Label ekle
                    session.run(f"""
                        MATCH (c:Customer)-[r:{rel_type}]->(p:Policy)
                        SET p:{label}
                        SET p._migrated_from = '{rel_type}'
                        SET p._migration_timestamp = $timestamp
                    """, timestamp=self.migration_timestamp)  # type: ignore[arg-type]
                    
                    # 2. İlişkiyi sil
                    delete_result = session.run(f"""
                        MATCH (c:Customer)-[r:{rel_type}]->(p:Policy)
                        DELETE r
                        RETURN count(r) AS deleted
                    """).single()  # type: ignore[arg-type]
                    
                    deleted = delete_result["deleted"] if delete_result else 0
                    
                    print(f"✅ {rel_type} → :{label} ({policy_count} policies, {deleted} relations deleted)")
                    results["labels_added"][label] = policy_count
                    results["relations_deleted"] += deleted
                    
                except Exception as e:
                    error_msg = f"Error migrating {rel_type}: {str(e)}"
                    print(f"❌ {error_msg}")
                    results["errors"].append(error_msg)
            
            # Doğrulama
            if not dry_run:
                verification = session.run("""
                    MATCH (p:Policy)
                    WHERE p._migrated_from IS NOT NULL
                    WITH labels(p) AS lbls, count(p) AS cnt
                    RETURN lbls, cnt
                    ORDER BY cnt DESC
                    LIMIT 10
                """).data()
                
                remaining_rels = session.run("""
                    MATCH (c:Customer)-[r]->(p:Policy)
                    WHERE type(r) STARTS WITH 'IS_' AND type(r) ENDS WITH '_POLICY'
                    RETURN count(r) AS remaining
                """).single()
                
                results["verification"] = {
                    "migrated_labels": verification,
                    "remaining_is_policy_relations": remaining_rels["remaining"] if remaining_rels else 0
                }
                
                print(f"\n📊 MIGRATION COMPLETE")
                print(f"   Labels added: {sum(results['labels_added'].values())}")
                print(f"   Relations deleted: {results['relations_deleted']}")
                print(f"   Remaining IS_X_POLICY: {results['verification']['remaining_is_policy_relations']}")
        
        return results
    
    def rollback(self) -> dict:
        """
        Migration'ı geri al:
        1. Label'dan ilişki tipini belirle (_migrated_from property)
        2. IS_X_POLICY ilişkisini tekrar oluştur
        3. Ek label'ı kaldır
        """
        results = {
            "relations_recreated": 0,
            "labels_removed": 0,
            "errors": []
        }
        
        with self.driver.session(database=self.database) as session:
            # Migration yapılmış policy'leri bul
            migrated = session.run("""
                MATCH (c:Customer)-[:HAS_POLICY]->(p:Policy)
                WHERE p._migrated_from IS NOT NULL
                RETURN DISTINCT p._migrated_from AS rel_type, count(p) AS cnt
            """).data()
            
            print(f"Found {len(migrated)} relation types to rollback")
            
            for item in migrated:
                rel_type = item["rel_type"]
                label = LABEL_MAPPING.get(rel_type)
                
                if not label:
                    results["errors"].append(f"Unknown relation type: {rel_type}")
                    continue
                
                try:
                    # 1. İlişkiyi yeniden oluştur
                    session.run(f"""
                        MATCH (c:Customer)-[:HAS_POLICY]->(p:Policy)
                        WHERE p._migrated_from = $rel_type
                        MERGE (c)-[:{rel_type}]->(p)
                    """, rel_type=rel_type)  # type: ignore[arg-type]
                    
                    # 2. Label'ı kaldır
                    session.run(f"""
                        MATCH (p:Policy:{label})
                        WHERE p._migrated_from = $rel_type
                        REMOVE p:{label}
                        REMOVE p._migrated_from
                        REMOVE p._migration_timestamp
                    """, rel_type=rel_type)  # type: ignore[arg-type]
                    
                    print(f"✅ Rolled back: {rel_type} ({item['cnt']} policies)")
                    results["relations_recreated"] += item["cnt"]
                    results["labels_removed"] += item["cnt"]
                    
                except Exception as e:
                    error_msg = f"Error rolling back {rel_type}: {str(e)}"
                    print(f"❌ {error_msg}")
                    results["errors"].append(error_msg)
            
            # Doğrulama
            verification = session.run("""
                MATCH (c:Customer)-[r]->(p:Policy)
                WHERE type(r) STARTS WITH 'IS_' AND type(r) ENDS WITH '_POLICY'
                RETURN count(r) AS recreated_relations
            """).single()
            
            results["verification"] = {
                "recreated_relations": verification["recreated_relations"] if verification else 0
            }
            
            print(f"\n📊 ROLLBACK COMPLETE")
            print(f"   Relations recreated: {results['relations_recreated']}")
            print(f"   Labels removed: {results['labels_removed']}")
        
        return results
    
    def verify(self) -> dict:
        """Migration sonrası doğrulama"""
        with self.driver.session(database=self.database) as session:
            # Yeni label'lar
            new_labels = session.run("""
                CALL db.labels() YIELD label
                WHERE label IN $labels
                RETURN label
                ORDER BY label
            """, labels=list(LABEL_MAPPING.values())).data()
            
            # Label bazlı policy sayıları
            label_counts = session.run("""
                MATCH (p:Policy)
                WITH labels(p) AS lbls, p
                UNWIND lbls AS lbl
                WHERE lbl <> 'Policy'
                RETURN lbl AS label, count(p) AS policy_count
                ORDER BY policy_count DESC
            """).data()
            
            # Kalan IS_X_POLICY ilişkileri
            remaining_rels = session.run("""
                MATCH (c:Customer)-[r]->(p:Policy)
                WHERE type(r) STARTS WITH 'IS_' AND type(r) ENDS WITH '_POLICY'
                RETURN type(r) AS rel_type, count(r) AS cnt
            """).data()
            
            # Örnek sorgu testi
            sample_query = session.run("""
                MATCH (c:Customer)-[:HAS_POLICY]->(p:TrafikPolicy)
                RETURN count(p) AS trafik_policies
                LIMIT 1
            """).single()
            
            return {
                "new_labels_in_schema": [l["label"] for l in new_labels],
                "label_policy_counts": label_counts,
                "remaining_is_policy_relations": remaining_rels,
                "sample_query_result": sample_query["trafik_policies"] if sample_query else 0
            }


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Policy Type Migration Tool")
    parser.add_argument("--analyze", action="store_true", help="Analyze current state")
    parser.add_argument("--migrate", action="store_true", help="Execute migration")
    parser.add_argument("--dry-run", action="store_true", help="Dry run (no changes)")
    parser.add_argument("--rollback", action="store_true", help="Rollback migration")
    parser.add_argument("--verify", action="store_true", help="Verify migration")
    parser.add_argument("--batch-size", type=int, default=500, help="Batch size")
    
    args = parser.parse_args()
    
    # Global environment variables kullan (load_dotenv ile yüklendi)
    migrator = PolicyTypeMigrator(NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD, NEO4J_DATABASE)
    
    print(f"🔗 Neo4j: {NEO4J_URI} (database: {NEO4J_DATABASE})")
    
    try:
        if args.analyze:
            print("📊 MIGRATION ANALYSIS")
            print("=" * 50)
            result = migrator.analyze()
            
            print(f"\nTotal policies to migrate: {result.get('total_policies_to_migrate', 0)}")
            print(f"Total IS_X_POLICY relations to delete: {result.get('total_relations_to_delete', 0)}")
            print(f"Total policy types: {result.get('total_policy_types', 0)}")
            print(f"Existing HAS_POLICY relations: {result.get('has_policy_relations', 0)}")
            
            print("\n📋 RELATION STATS:")
            for stat in result.get("relation_stats", []):
                label = LABEL_MAPPING.get(stat["rel_type"], "?")
                print(f"   {stat['rel_type']} → :{label} ({stat['policy_count']} policies)")
            
            if result.get("existing_multi_label_policies"):
                print("\n⚠️  WARNING: Some policies already have multiple labels:")
                for item in result["existing_multi_label_policies"]:
                    print(f"   {item['lbls']}: {item['cnt']}")
        
        elif args.migrate:
            print("🚀 EXECUTING MIGRATION")
            print("=" * 50)
            result = migrator.migrate(batch_size=args.batch_size, dry_run=args.dry_run)
            
            if result.get("errors"):
                print("\n❌ ERRORS:")
                for err in result["errors"]:
                    print(f"   {err}")
        
        elif args.rollback:
            print("⏪ ROLLING BACK MIGRATION")
            print("=" * 50)
            confirm = input("Are you sure? This will recreate IS_X_POLICY relations. (yes/no): ")
            if confirm.lower() == "yes":
                result = migrator.rollback()
                
                if result.get("errors"):
                    print("\n❌ ERRORS:")
                    for err in result["errors"]:
                        print(f"   {err}")
            else:
                print("Cancelled.")
        
        elif args.verify:
            print("✅ VERIFICATION")
            print("=" * 50)
            result = migrator.verify()
            
            print(f"\nNew labels in schema: {result.get('new_labels_in_schema', [])}")
            print(f"\nLabel policy counts:")
            for item in result.get("label_policy_counts", []):
                print(f"   :{item['label']} → {item['policy_count']} policies")
            
            remaining = result.get("remaining_is_policy_relations", [])
            if remaining:
                print(f"\n⚠️  Remaining IS_X_POLICY relations:")
                for item in remaining:
                    print(f"   {item['rel_type']}: {item['cnt']}")
            else:
                print("\n✅ No remaining IS_X_POLICY relations")
            
            print(f"\nSample query (TrafikPolicy): {result.get('sample_query_result', 0)} policies")
        
        else:
            parser.print_help()
    
    finally:
        migrator.close()


if __name__ == "__main__":
    main()

