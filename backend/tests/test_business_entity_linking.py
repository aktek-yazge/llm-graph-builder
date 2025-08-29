#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Business Entity Linking Test
PolicyType, InsuredItem, PolicyYear, Customer entity'lerinin business node'lara bağlanmasını test eder
"""

import sys
import os
from dotenv import load_dotenv

# .env dosyasını yükle
load_dotenv()

sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from langchain_neo4j import Neo4jGraph
from src.make_relationships import merge_relationship_between_chunk_and_entites
from src.shared.common_fn import execute_graph_query
import json

def test_business_entity_linking():
    print("🔗 BUSINESS ENTITY LINKING TESTİ")
    print("=" * 60)
    
    # Neo4j bağlantısı
    try:
        graph = Neo4jGraph(
            url=os.getenv('NEO4J_URI', 'bolt://localhost:7687'),
            username=os.getenv('NEO4J_USERNAME', 'neo4j'),
            password=os.getenv('NEO4J_PASSWORD', 'password')
        )
        print("✅ Neo4j bağlantısı başarılı")
    except Exception as e:
        print(f"❌ Neo4j bağlantı hatası: {e}")
        return
    
    # Test için chunk ve business entity'leri mockla
    print("\n🔧 TEST VERİLERİ HAZIRLANIYOR...")
    
    # Test chunk'ı oluştur
    test_chunk_id = "test_business_chunk_12345"
    test_file_name = "test_business_policy_document.pdf"
    
    # Önce test verilerini temizle
    cleanup_query = """
    MATCH (n) WHERE n.id IN ['test_business_chunk_12345', 'Konut Sigortası', 'Ev Eşyaları', '2024', 'Mehmet Erdogan TEST'] 
    OR (n:PolicyYear AND n.name = '2024')
    OR (n:Customer AND n.name = 'Mehmet Erdogan TEST')
    DETACH DELETE n
    """
    execute_graph_query(graph, cleanup_query)
    
    # Test Document ve Chunk oluştur
    setup_query = """
    // Test Document
    MERGE (d:Document {fileName: $file_name})
    SET d.status = 'Completed', d.createdAt = datetime()
    
    // Test Chunk
    MERGE (c:Chunk {id: $chunk_id})
    SET c.text = 'Test chunk content for business entity linking', 
        c.fileName = $file_name,
        c.position = 1
    MERGE (c)-[:PART_OF]->(d)
    
    RETURN d.fileName as doc, c.id as chunk
    """
    
    setup_result = execute_graph_query(graph, setup_query, params={
        "file_name": test_file_name,
        "chunk_id": test_chunk_id
    })
    
    if setup_result:
        print(f"✅ Test verileri hazırlandı: {setup_result[0]}")
    
    # Test business entity'leri için mock graph_documents oluştur
    from collections import namedtuple
    
    Node = namedtuple('Node', ['id', 'type'])
    GraphDocument = namedtuple('GraphDocument', ['nodes', 'relationships'])
    
    # Test business entity'leri
    test_business_entities = [
        Node(id='Konut Sigortası', type='PolicyType'),
        Node(id='Ev Eşyaları', type='InsuredItem'),
        Node(id='2024', type='PolicyYear'),
        Node(id='Mehmet Erdogan TEST', type='Customer'),
        Node(id='invalid_year_abc', type='PolicyYear'),  # Geçersiz yıl formatı
        Node(id='Telefon Numarası', type='Phone')  # Normal entity
    ]
    
    graph_doc = GraphDocument(nodes=test_business_entities, relationships=[])
    
    graph_documents_chunk_chunk_Id = [{
        'chunk_id': test_chunk_id,
        'graph_doc': graph_doc
    }]
    
    print("🚀 BUSINESS ENTITY LINKING...")
    
    # Business entity linking'i çalıştır
    merge_relationship_between_chunk_and_entites(graph, graph_documents_chunk_chunk_Id)
    
    print("\n🔍 SONUÇLARI KONTROL EDİYORUZ...")
    
    # 1. EXTRACTED_FROM ilişkilerini kontrol et
    extracted_check = """
    MATCH (e:__Entity__)-[:EXTRACTED_FROM]->(c:Chunk {id: $chunk_id})
    RETURN e.id as entity_id, e.entity_type as entity_type, labels(e) as labels
    ORDER BY e.id
    """
    extracted_result = execute_graph_query(graph, extracted_check, params={"chunk_id": test_chunk_id})
    
    print(f"📋 EXTRACTED_FROM ilişkileri: {len(extracted_result) if extracted_result else 0}")
    if extracted_result:
        for entity in extracted_result:
            print(f"  - {entity['entity_id']} ({entity['entity_type']}) Labels: {entity['labels']}")
    
    # 2. PolicyType → Policy bağlantısını kontrol et
    policy_type_check = """
    MATCH (d:Document {fileName: $file_name})
    MATCH (policy:Policy)-[:DOCUMENTED_IN]->(d)
    MATCH (policy)-[:HAS_TYPE]->(pt:__Entity__ {id: 'Konut Sigortası'})
    RETURN policy.id as policy_id, pt.id as policy_type_id
    """
    policy_type_result = execute_graph_query(graph, policy_type_check, params={"file_name": test_file_name})
    
    print(f"🏠 PolicyType → Policy bağlantıları: {len(policy_type_result) if policy_type_result else 0}")
    if policy_type_result:
        for rel in policy_type_result:
            print(f"  - Policy({rel['policy_id']}) → PolicyType({rel['policy_type_id']})")
    
    # 3. InsuredItem → Policy bağlantısını kontrol et
    insured_item_check = """
    MATCH (d:Document {fileName: $file_name})
    MATCH (policy:Policy)-[:DOCUMENTED_IN]->(d)
    MATCH (policy)-[:HAS_INSURED_ITEM]->(ii:__Entity__ {id: 'Ev Eşyaları'})
    RETURN policy.id as policy_id, ii.id as insured_item_id
    """
    insured_item_result = execute_graph_query(graph, insured_item_check, params={"file_name": test_file_name})
    
    print(f"🏠 InsuredItem → Policy bağlantıları: {len(insured_item_result) if insured_item_result else 0}")
    if insured_item_result:
        for rel in insured_item_result:
            print(f"  - Policy({rel['policy_id']}) → InsuredItem({rel['insured_item_id']})")
    
    # 4. PolicyYear → Policy bağlantısını kontrol et
    policy_year_check = """
    MATCH (d:Document {fileName: $file_name})
    MATCH (policy:Policy)-[:DOCUMENTED_IN]->(d)
    MATCH (policy)-[:HAS_YEAR]->(py:PolicyYear {name: '2024'})
    RETURN policy.id as policy_id, py.year as policy_year, py.name as policy_year_name
    """
    policy_year_result = execute_graph_query(graph, policy_year_check, params={"file_name": test_file_name})
    
    print(f"📅 PolicyYear → Policy bağlantıları: {len(policy_year_result) if policy_year_result else 0}")
    if policy_year_result:
        for rel in policy_year_result:
            print(f"  - Policy({rel['policy_id']}) → PolicyYear({rel['policy_year']} / {rel['policy_year_name']})")
    
    # 5. Customer → Document bağlantısını kontrol et
    customer_check = """
    MATCH (d:Document {fileName: $file_name})
    MATCH (customer:Customer {name: 'Mehmet Erdogan TEST'})-[:HAS_DOC]->(d)
    RETURN customer.name as customer_name, customer.fullName as customer_full_name, d.fileName as document_name
    """
    customer_result = execute_graph_query(graph, customer_check, params={"file_name": test_file_name})
    
    print(f"👤 Customer → Document bağlantıları: {len(customer_result) if customer_result else 0}")
    if customer_result:
        for rel in customer_result:
            print(f"  - Customer({rel['customer_name']}) → Document({rel['document_name']})")
    
    # 6. Geçersiz PolicyYear'ın bağlanmadığını kontrol et
    invalid_year_check = """
    MATCH (entity:__Entity__ {id: 'invalid_year_abc'})-[:EXTRACTED_FROM]->(c:Chunk {id: $chunk_id})
    OPTIONAL MATCH (py:PolicyYear {name: 'invalid_year_abc'})
    RETURN entity.id as entity_id, py.name as policy_year_name
    """
    invalid_year_result = execute_graph_query(graph, invalid_year_check, params={"chunk_id": test_chunk_id})
    
    print(f"❌ Geçersiz PolicyYear kontrolü: {len(invalid_year_result) if invalid_year_result else 0}")
    if invalid_year_result:
        for rel in invalid_year_result:
            policy_year_exists = rel['policy_year_name'] is not None
            print(f"  - Entity({rel['entity_id']}) → PolicyYear created: {policy_year_exists}")
    
    # Başarı metrikleri
    success_metrics = {
        'extracted_from_count': len(extracted_result) if extracted_result else 0,
        'policy_type_links': len(policy_type_result) if policy_type_result else 0,
        'insured_item_links': len(insured_item_result) if insured_item_result else 0,
        'policy_year_links': len(policy_year_result) if policy_year_result else 0,
        'customer_links': len(customer_result) if customer_result else 0
    }
    
    print(f"\n📊 BAŞARI METRİKLERİ:")
    print(f"  ✅ EXTRACTED_FROM: {success_metrics['extracted_from_count']}")
    print(f"  🏠 PolicyType Links: {success_metrics['policy_type_links']}")
    print(f"  🏠 InsuredItem Links: {success_metrics['insured_item_links']}")
    print(f"  📅 PolicyYear Links: {success_metrics['policy_year_links']}")
    print(f"  👤 Customer Links: {success_metrics['customer_links']}")
    
    # Beklenen sonuçlar
    expected_extracted = 6  # 6 entity olmalı
    expected_policy_type = 1  # PolicyType bağlanmalı
    expected_insured_item = 1  # InsuredItem bağlanmalı
    expected_policy_year = 1  # Geçerli PolicyYear bağlanmalı
    expected_customer = 1  # Customer bağlanmalı
    
    print(f"\n🎯 BAŞARI DURUMU:")
    print(f"  EXTRACTED_FROM: {'✅' if success_metrics['extracted_from_count'] == expected_extracted else '❌'} ({success_metrics['extracted_from_count']}/{expected_extracted})")
    print(f"  PolicyType Links: {'✅' if success_metrics['policy_type_links'] == expected_policy_type else '❌'} ({success_metrics['policy_type_links']}/{expected_policy_type})")
    print(f"  InsuredItem Links: {'✅' if success_metrics['insured_item_links'] == expected_insured_item else '❌'} ({success_metrics['insured_item_links']}/{expected_insured_item})")
    print(f"  PolicyYear Links: {'✅' if success_metrics['policy_year_links'] == expected_policy_year else '❌'} ({success_metrics['policy_year_links']}/{expected_policy_year})")
    print(f"  Customer Links: {'✅' if success_metrics['customer_links'] == expected_customer else '❌'} ({success_metrics['customer_links']}/{expected_customer})")
    
    if (success_metrics['extracted_from_count'] == expected_extracted and 
        success_metrics['policy_type_links'] == expected_policy_type and
        success_metrics['insured_item_links'] == expected_insured_item and
        success_metrics['policy_year_links'] == expected_policy_year and
        success_metrics['customer_links'] == expected_customer):
        print("\n🎉 TÜM BUSINESS ENTITY LINKING TESTLERİ BAŞARILI!")
    else:
        print("\n⚠️ Bazı business entity linking testleri beklenenden farklı sonuç verdi")
    
    # Test verilerini temizle
    execute_graph_query(graph, cleanup_query)
    print("\n🧹 Test verileri temizlendi")

if __name__ == "__main__":
    test_business_entity_linking()
