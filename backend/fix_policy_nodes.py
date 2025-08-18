#!/usr/bin/env python3
"""
Orphan Policy Node Temizleme Script'i
Bu script, Document'lara bağlı olmayan orphan Policy node'larını temizler.
"""

import os
import logging
from datetime import datetime
from dotenv import load_dotenv
from src.shared.common_fn import create_graph_database_connection, execute_graph_query

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def get_graph_connection():
    """Neo4j bağlantısını oluştur"""
    uri = os.getenv('NEO4J_URI')
    username = os.getenv('NEO4J_USERNAME') 
    password = os.getenv('NEO4J_PASSWORD')
    database = os.getenv('NEO4J_DATABASE')
    
    return create_graph_database_connection(uri, username, password, database)

def analyze_orphan_policies(graph):
    """Orphan Policy node'larını analiz et"""
    
    # Toplam Policy sayısı
    total_query = "MATCH (p:Policy) RETURN count(*) AS total_policies"
    total_result = execute_graph_query(graph, total_query)
    total_policies = total_result[0]['total_policies'] if total_result else 0
    
    # Document'lara bağlı Policy sayısı
    connected_query = """
    MATCH (p:Policy)
    WHERE (p)-[:DOCUMENTED_IN]-(:Document) 
    OR (p)-[:CONTAINS_ENTITY]-(:Document)
    OR (:Chunk)-[:HAS_ENTITY]->(p)
    RETURN count(*) AS connected_policies
    """
    connected_result = execute_graph_query(graph, connected_query)
    connected_policies = connected_result[0]['connected_policies'] if connected_result else 0
    
    # Orphan Policy sayısı
    orphan_query = """
    MATCH (p:Policy)
    WHERE NOT (p)-[:DOCUMENTED_IN]-(:Document) 
    AND NOT (p)-[:CONTAINS_ENTITY]-(:Document)
    AND NOT (:Chunk)-[:HAS_ENTITY]->(p)
    RETURN count(*) AS orphan_policies
    """
    orphan_result = execute_graph_query(graph, orphan_query)
    orphan_policies = orphan_result[0]['orphan_policies'] if orphan_result else 0
    
    # Orphan Policy detayları
    orphan_details_query = """
    MATCH (p:Policy)
    WHERE NOT (p)-[:DOCUMENTED_IN]-(:Document) 
    AND NOT (p)-[:CONTAINS_ENTITY]-(:Document)
    AND NOT (:Chunk)-[:HAS_ENTITY]->(p)
    WITH p, [(p)-[r]-() | type(r)] AS all_relationships
    RETURN p.id AS policy_id, 
           all_relationships,
           size(all_relationships) AS total_connections
    ORDER BY total_connections DESC
    LIMIT 20
    """
    orphan_details = execute_graph_query(graph, orphan_details_query)
    
    logging.info(f"Policy Node Analizi:")
    logging.info(f"  Toplam Policy: {total_policies}")
    logging.info(f"  Document'lara bağlı: {connected_policies}")
    logging.info(f"  Orphan Policy: {orphan_policies}")
    logging.info("")
    
    if orphan_details:
        logging.info("Orphan Policy örnekleri:")
        for i, policy in enumerate(orphan_details[:10], 1):
            logging.info(f"  {i}. {policy['policy_id']} ({policy['total_connections']} bağlantı)")
            if policy['all_relationships']:
                rel_types = list(set(policy['all_relationships']))[:5]
                logging.info(f"     İlişkiler: {', '.join(rel_types)}")
        logging.info("")
    
    return {
        'total_policies': total_policies,
        'connected_policies': connected_policies,
        'orphan_policies': orphan_policies,
        'orphan_details': orphan_details
    }

def cleanup_orphan_policies(graph, dry_run=True):
    """Orphan Policy node'larını temizle"""
    
    # Orphan Policy'leri bul
    orphan_query = """
    MATCH (p:Policy)
    WHERE NOT (p)-[:DOCUMENTED_IN]-(:Document) 
    AND NOT (p)-[:CONTAINS_ENTITY]-(:Document)
    AND NOT (:Chunk)-[:HAS_ENTITY]->(p)
    RETURN p.id AS policy_id, elementId(p) AS element_id
    """
    
    orphan_policies = execute_graph_query(graph, orphan_query)
    
    if not orphan_policies:
        logging.info("Temizlenecek orphan Policy bulunamadı!")
        return
    
    logging.info(f"Temizlenecek orphan Policy sayısı: {len(orphan_policies)}")
    
    if dry_run:
        logging.info("DRY RUN modu - değişiklikler uygulanmayacak")
        for i, policy in enumerate(orphan_policies[:10], 1):
            logging.info(f"  {i}. Silinecek: {policy['policy_id']}")
        if len(orphan_policies) > 10:
            logging.info(f"  ... ve {len(orphan_policies) - 10} tane daha")
        return
    
    # Gerçek temizleme
    batch_size = 50
    deleted_count = 0
    
    for i in range(0, len(orphan_policies), batch_size):
        batch = orphan_policies[i:i+batch_size]
        
        # Orphan Policy'leri sil
        delete_query = """
        UNWIND $policies AS policy
        MATCH (p:Policy)
        WHERE elementId(p) = policy.element_id
        DETACH DELETE p
        RETURN count(*) AS deleted
        """
        
        result = execute_graph_query(graph, delete_query, params={'policies': batch})
        batch_deleted = result[0]['deleted'] if result else 0
        deleted_count += batch_deleted
        
        logging.info(f"Batch {i//batch_size + 1}/{(len(orphan_policies)-1)//batch_size + 1}: {batch_deleted} Policy silindi")
    
    logging.info(f"Toplam {deleted_count} orphan Policy temizlendi")
    return deleted_count

def fix_policy_properties(graph, dry_run=True):
    """Policy node'larının eksik özelliklerini düzelt"""
    
    # fileName ve policyNumber eksik olan Policy'leri bul
    query = """
    MATCH (p:Policy)-[:DOCUMENTED_IN]->(d:Document)
    WHERE p.fileName IS NULL OR p.policyNumber IS NULL
    RETURN p.id AS policy_id, 
           d.fileName AS document_file,
           p.fileName AS current_file_name,
           p.policyNumber AS current_policy_number,
           elementId(p) AS element_id
    LIMIT 20
    """
    
    policies_to_fix = execute_graph_query(graph, query)
    
    if not policies_to_fix:
        logging.info("Düzeltilecek Policy property'si bulunamadı")
        return
    
    logging.info(f"Düzeltilecek Policy sayısı: {len(policies_to_fix)}")
    
    if dry_run:
        logging.info("DRY RUN modu - değişiklikler uygulanmayacak")
        for policy in policies_to_fix[:5]:
            logging.info(f"  Policy: {policy['policy_id']}")
            logging.info(f"    Document: {policy['document_file']}")
            logging.info(f"    Mevcut fileName: {policy['current_file_name']}")
            logging.info(f"    Mevcut policyNumber: {policy['current_policy_number']}")
        return
    
    # Property'leri düzelt
    for policy in policies_to_fix:
        try:
            update_query = """
            MATCH (p:Policy)
            WHERE elementId(p) = $element_id
            SET p.fileName = CASE WHEN p.fileName IS NULL THEN $document_file ELSE p.fileName END,
                p.policyNumber = CASE WHEN p.policyNumber IS NULL THEN p.id ELSE p.policyNumber END
            """
            
            execute_graph_query(graph, update_query, params={
                'element_id': policy['element_id'],
                'document_file': policy['document_file']
            })
            
            logging.info(f"✅ Policy düzeltildi: {policy['policy_id']}")
            
        except Exception as e:
            logging.error(f"❌ Policy düzeltme hatası: {policy['policy_id']} - {e}")

def main():
    """Ana fonksiyon"""
    graph = get_graph_connection()
    
    print("Policy Node Temizleme Araçları")
    print("1. Policy node'larını analiz et")
    print("2. Orphan Policy'leri temizle (DRY RUN)")  
    print("3. Orphan Policy'leri temizle (GERÇEK)")
    print("4. Policy property'lerini düzelt (DRY RUN)")
    print("5. Policy property'lerini düzelt (GERÇEK)")
    print("0. Çıkış")
    
    choice = input("Seçiminiz: ")
    
    if choice == "1":
        analyze_orphan_policies(graph)
    elif choice == "2":
        cleanup_orphan_policies(graph, dry_run=True)
    elif choice == "3":
        confirm = input("Bu işlem geri alınamaz! Devam etmek istiyor musunuz? (evet/hayır): ")
        if confirm.lower() in ['evet', 'yes', 'y']:
            cleanup_orphan_policies(graph, dry_run=False)
        else:
            print("İşlem iptal edildi")
    elif choice == "4":
        fix_policy_properties(graph, dry_run=True)
    elif choice == "5":
        confirm = input("Policy property'lerini düzeltmek istiyor musunuz? (evet/hayır): ")
        if confirm.lower() in ['evet', 'yes', 'y']:
            fix_policy_properties(graph, dry_run=False)
        else:
            print("İşlem iptal edildi")
    elif choice == "0":
        print("Çıkış yapılıyor...")
    else:
        print("Geçersiz seçim!")

if __name__ == "__main__":
    main()
