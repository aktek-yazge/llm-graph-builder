#!/usr/bin/env python3
"""
Extract Validation Script
Bu script, extract işlemi sonrasında yaygın hataları kontrol eder ve raporlar.
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

def validate_chunk_relationships(graph, file_name=None):
    """Chunk-Document ilişkilerini doğrula"""
    logging.info("Chunk-Document ilişkileri doğrulanıyor...")
    
    # 1. Yanlış bağlantıları kontrol et
    wrong_connections_query = """
    MATCH (c:Chunk)-[:PART_OF]->(d:Document)
    WHERE c.fileName <> d.fileName
    RETURN count(*) AS wrong_connections,
           collect(DISTINCT {chunk_file: c.fileName, document_file: d.fileName})[0..5] AS examples
    """
    
    if file_name:
        wrong_connections_query = """
        MATCH (c:Chunk)-[:PART_OF]->(d:Document)
        WHERE d.fileName = $file_name AND c.fileName <> d.fileName
        RETURN count(*) AS wrong_connections,
               collect(DISTINCT {chunk_file: c.fileName, document_file: d.fileName})[0..5] AS examples
        """
    
    wrong_result = execute_graph_query(graph, wrong_connections_query, 
                                     params={"file_name": file_name} if file_name else {})
    
    # 2. Duplicate chunk ID'leri kontrol et
    duplicate_chunks_query = """
    MATCH (c:Chunk)
    WITH c.chunkId AS chunkId, count(*) AS cnt
    WHERE cnt > 1
    RETURN count(*) AS duplicate_chunk_ids
    """
    
    duplicate_result = execute_graph_query(graph, duplicate_chunks_query)
    
    # 3. Orphan chunk'ları kontrol et
    orphan_chunks_query = """
    MATCH (c:Chunk)
    WHERE NOT (c)-[:PART_OF]->(:Document)
    RETURN count(*) AS orphan_chunks
    """
    
    orphan_result = execute_graph_query(graph, orphan_chunks_query)
    
    validation_result = {
        'wrong_connections': wrong_result[0]['wrong_connections'] if wrong_result else 0,
        'wrong_connection_examples': wrong_result[0]['examples'] if wrong_result else [],
        'duplicate_chunk_ids': duplicate_result[0]['duplicate_chunk_ids'] if duplicate_result else 0,
        'orphan_chunks': orphan_result[0]['orphan_chunks'] if orphan_result else 0
    }
    
    # Raporlama
    if validation_result['wrong_connections'] > 0:
        logging.error(f"❌ {validation_result['wrong_connections']} yanlış Chunk-Document bağlantısı bulundu!")
        for example in validation_result['wrong_connection_examples']:
            logging.error(f"  Örnek: Chunk {example['chunk_file']} -> Document {example['document_file']}")
    else:
        logging.info("✅ Chunk-Document bağlantıları doğru")
    
    if validation_result['duplicate_chunk_ids'] > 0:
        logging.error(f"❌ {validation_result['duplicate_chunk_ids']} duplicate chunk ID bulundu!")
    else:
        logging.info("✅ Chunk ID'leri unique")
    
    if validation_result['orphan_chunks'] > 0:
        logging.error(f"❌ {validation_result['orphan_chunks']} orphan chunk bulundu!")
    else:
        logging.info("✅ Orphan chunk yok")
    
    return validation_result

def validate_policy_nodes(graph, file_name=None):
    """Policy node'larını doğrula"""
    logging.info("Policy node'ları doğrulanıyor...")
    
    # 1. Orphan Policy node'larını kontrol et
    orphan_policy_query = """
    MATCH (p:Policy)
    WHERE NOT (p)-[:DOCUMENTED_IN]-(:Document) 
    AND NOT (p)-[:CONTAINS_ENTITY]-(:Document)
    AND NOT (:Chunk)-[:HAS_ENTITY]->(p)
    RETURN count(*) AS orphan_policies
    """
    
    orphan_result = execute_graph_query(graph, orphan_policy_query)
    
    # 2. Property eksikliklerini kontrol et
    missing_properties_query = """
    MATCH (p:Policy)-[:DOCUMENTED_IN]->(d:Document)
    WHERE p.fileName IS NULL OR p.policyNumber IS NULL
    RETURN count(*) AS missing_properties,
           collect(DISTINCT p.id)[0..5] AS examples
    """
    
    if file_name:
        missing_properties_query = """
        MATCH (p:Policy)-[:DOCUMENTED_IN]->(d:Document)
        WHERE d.fileName = $file_name AND (p.fileName IS NULL OR p.policyNumber IS NULL)
        RETURN count(*) AS missing_properties,
               collect(DISTINCT p.id)[0..5] AS examples
        """
    
    missing_result = execute_graph_query(graph, missing_properties_query,
                                       params={"file_name": file_name} if file_name else {})
    
    # 3. Generic Policy node'larını kontrol et
    generic_policy_query = """
    MATCH (p:Policy)
    WHERE p.id IN ['Policy', 'Poliçe', 'Sigorta', 'Insurance']
    RETURN count(*) AS generic_policies,
           collect(p.id) AS generic_ids
    """
    
    generic_result = execute_graph_query(graph, generic_policy_query)
    
    validation_result = {
        'orphan_policies': orphan_result[0]['orphan_policies'] if orphan_result else 0,
        'missing_properties': missing_result[0]['missing_properties'] if missing_result else 0,
        'missing_property_examples': missing_result[0]['examples'] if missing_result else [],
        'generic_policies': generic_result[0]['generic_policies'] if generic_result else 0,
        'generic_ids': generic_result[0]['generic_ids'] if generic_result else []
    }
    
    # Raporlama
    if validation_result['orphan_policies'] > 0:
        logging.error(f"❌ {validation_result['orphan_policies']} orphan Policy node bulundu!")
    else:
        logging.info("✅ Orphan Policy node yok")
    
    if validation_result['missing_properties'] > 0:
        logging.warning(f"⚠️ {validation_result['missing_properties']} Policy node'unda eksik property var")
        for example in validation_result['missing_property_examples']:
            logging.warning(f"  Örnek: {example}")
    else:
        logging.info("✅ Policy node property'leri tamam")
    
    if validation_result['generic_policies'] > 0:
        logging.warning(f"⚠️ {validation_result['generic_policies']} generic Policy node bulundu: {validation_result['generic_ids']}")
    else:
        logging.info("✅ Generic Policy node yok")
    
    return validation_result

def validate_document_nodes(graph, file_name=None):
    """Document node'larını doğrula"""
    logging.info("Document node'ları doğrulanıyor...")
    
    # 1. Duplicate Document node'larını kontrol et
    duplicate_docs_query = """
    MATCH (d:Document)
    WITH d.fileName AS fileName, count(*) AS cnt
    WHERE cnt > 1
    RETURN count(*) AS duplicate_documents,
           collect(fileName)[0..5] AS examples
    """
    
    duplicate_result = execute_graph_query(graph, duplicate_docs_query)
    
    # 2. Orphan Document node'larını kontrol et (chunk'ı olmayan)
    orphan_docs_query = """
    MATCH (d:Document)
    WHERE NOT (d)<-[:PART_OF]-(:Chunk)
    RETURN count(*) AS orphan_documents,
           collect(d.fileName)[0..5] AS examples
    """
    
    orphan_result = execute_graph_query(graph, orphan_docs_query)
    
    validation_result = {
        'duplicate_documents': duplicate_result[0]['duplicate_documents'] if duplicate_result else 0,
        'duplicate_examples': duplicate_result[0]['examples'] if duplicate_result else [],
        'orphan_documents': orphan_result[0]['orphan_documents'] if orphan_result else 0,
        'orphan_examples': orphan_result[0]['examples'] if orphan_result else []
    }
    
    # Raporlama
    if validation_result['duplicate_documents'] > 0:
        logging.error(f"❌ {validation_result['duplicate_documents']} duplicate Document node bulundu!")
        for example in validation_result['duplicate_examples']:
            logging.error(f"  Örnek: {example}")
    else:
        logging.info("✅ Duplicate Document node yok")
    
    if validation_result['orphan_documents'] > 0:
        logging.warning(f"⚠️ {validation_result['orphan_documents']} orphan Document node bulundu")
        for example in validation_result['orphan_examples']:
            logging.warning(f"  Örnek: {example}")
    else:
        logging.info("✅ Orphan Document node yok")
    
    return validation_result

def run_full_validation(graph, file_name=None):
    """Tam validation çalıştır"""
    logging.info("=" * 60)
    logging.info(f"EXTRACT VALIDATION BAŞLANIYOR")
    if file_name:
        logging.info(f"Dosya: {file_name}")
    else:
        logging.info("Tüm database için")
    logging.info("=" * 60)
    
    start_time = datetime.now()
    
    # Validations
    chunk_validation = validate_chunk_relationships(graph, file_name)
    policy_validation = validate_policy_nodes(graph, file_name)
    document_validation = validate_document_nodes(graph, file_name)
    
    end_time = datetime.now()
    duration = end_time - start_time
    
    # Özet rapor
    logging.info("=" * 60)
    logging.info("VALIDATION ÖZET RAPORU")
    logging.info("=" * 60)
    
    total_errors = (
        chunk_validation['wrong_connections'] +
        chunk_validation['duplicate_chunk_ids'] +
        chunk_validation['orphan_chunks'] +
        policy_validation['orphan_policies'] +
        document_validation['duplicate_documents']
    )
    
    total_warnings = (
        policy_validation['missing_properties'] +
        policy_validation['generic_policies'] +
        document_validation['orphan_documents']
    )
    
    if total_errors == 0 and total_warnings == 0:
        logging.info("🎉 TÜM VALIDASYONLAR BAŞARILI!")
    elif total_errors == 0:
        logging.info(f"✅ Kritik hata yok, {total_warnings} uyarı var")
    else:
        logging.error(f"❌ {total_errors} kritik hata, {total_warnings} uyarı bulundu")
    
    logging.info(f"Validation süresi: {duration.total_seconds():.2f} saniye")
    logging.info("=" * 60)
    
    return {
        'total_errors': total_errors,
        'total_warnings': total_warnings,
        'chunk_validation': chunk_validation,
        'policy_validation': policy_validation,
        'document_validation': document_validation,
        'duration': duration.total_seconds()
    }

def main():
    """Ana fonksiyon"""
    graph = get_graph_connection()
    
    print("Extract Validation Araçları")
    print("1. Tüm database'i validate et")
    print("2. Belirli bir dosyayı validate et")
    print("0. Çıkış")
    
    choice = input("Seçiminiz: ")
    
    if choice == "1":
        run_full_validation(graph)
    elif choice == "2":
        file_name = input("Dosya adı girin: ")
        run_full_validation(graph, file_name)
    elif choice == "0":
        print("Çıkış yapılıyor...")
    else:
        print("Geçersiz seçim!")

if __name__ == "__main__":
    main()
