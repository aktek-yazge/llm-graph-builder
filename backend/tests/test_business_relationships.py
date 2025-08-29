#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Business Relationship Testing
Gerçek chunk ve entity ile business relationship'lerin doğru kurulup kurulmadığını test eder
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

def test_business_relationships():
    print("📊 BUSINESS RELATIONSHIP TESTİ")
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
    
    # Test için chunk ve entity'leri mockla
    print("\n🔧 TEST VERİLERİ HAZIRLANIYOR...")
    
    # Test chunk'ı oluştur
    test_chunk_id = "test_chunk_12345"
    test_file_name = "test_policy_document.pdf"
    
    # Önce test verilerini temizle
    cleanup_query = """
    MATCH (n) WHERE n.id IN ['test_chunk_12345', 'TEST_CUSTOMER_001', 'TEST_POLICY_001', 'TEST_LOCATION_001', 'TEST_PHONE_001'] 
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
    SET c.text = 'Test chunk content for business relationships', 
        c.fileName = $file_name,
        c.position = 1
    MERGE (c)-[:PART_OF]->(d)
    
    // Test Customer (business node)
    MERGE (customer:Customer {id: 'TEST_CUSTOMER_001'})
    SET customer.name = 'Test Customer'
    MERGE (customer)-[:HAS_DOC]->(d)
    
    // Test Policy (business node)  
    MERGE (policy:Policy {id: 'TEST_POLICY_001'})
    SET policy.policyNumber = 'TEST-POL-001'
    MERGE (policy)-[:DOCUMENTED_IN]->(d)
    
    RETURN d.fileName as doc, c.id as chunk, customer.id as cust, policy.id as pol
    """
    
    setup_result = execute_graph_query(graph, setup_query, params={
        "file_name": test_file_name,
        "chunk_id": test_chunk_id
    })
    
    if setup_result:
        print(f"✅ Test verileri hazırlandı: {setup_result[0]}")
    
    # Test entity'leri için mock graph_documents oluştur
    from collections import namedtuple
    
    Node = namedtuple('Node', ['id', 'type'])
    GraphDocument = namedtuple('GraphDocument', ['nodes', 'relationships'])
    
    # Test entity'leri
    test_entities = [
        Node(id='TEST_LOCATION_001', type='Location'),
        Node(id='TEST_PHONE_001', type='Phone'),
        Node(id='TEST_EMAIL_001', type='Email')
    ]
    
    graph_doc = GraphDocument(nodes=test_entities, relationships=[])
    
    graph_documents_chunk_chunk_Id = [{
        'chunk_id': test_chunk_id,
        'graph_doc': graph_doc
    }]
    
    print("🚀 BUSINESS RELATIONSHIP OLUŞTURMA...")
    
    # Business relationship'leri oluştur
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
    
    # 2. Policy → Entity business ilişkilerini kontrol et
    policy_business_check = """
    MATCH (p:Policy {id: 'TEST_POLICY_001'})-[:HAS_ENTITY]->(e:__Entity__)-[:EXTRACTED_FROM]->(c:Chunk {id: $chunk_id})
    RETURN p.id as policy_id, e.id as entity_id, e.entity_type as entity_type
    ORDER BY e.id
    """
    policy_result = execute_graph_query(graph, policy_business_check, params={"chunk_id": test_chunk_id})
    
    print(f"🏢 Policy → Entity business ilişkileri: {len(policy_result) if policy_result else 0}")
    if policy_result:
        for rel in policy_result:
            print(f"  - {rel['policy_id']} → {rel['entity_id']} ({rel['entity_type']})")
    
    # 3. Customer → Entity business ilişkilerini kontrol et
    customer_business_check = """
    MATCH (cust:Customer {id: 'TEST_CUSTOMER_001'})-[:HAS_ENTITY]->(e:__Entity__)-[:EXTRACTED_FROM]->(c:Chunk {id: $chunk_id})
    RETURN cust.id as customer_id, e.id as entity_id, e.entity_type as entity_type
    ORDER BY e.id
    """
    customer_result = execute_graph_query(graph, customer_business_check, params={"chunk_id": test_chunk_id})
    
    print(f"👤 Customer → Entity business ilişkileri: {len(customer_result) if customer_result else 0}")
    if customer_result:
        for rel in customer_result:
            print(f"  - {rel['customer_id']} → {rel['entity_id']} ({rel['entity_type']})")
    
    # 4. Tüm relationship'leri görselleştir
    full_graph_check = """
    MATCH path = (start)-[r]->(end)
    WHERE start.id IN ['TEST_CUSTOMER_001', 'TEST_POLICY_001'] + ['TEST_LOCATION_001', 'TEST_PHONE_001', 'TEST_EMAIL_001']
    OR end.id IN ['TEST_CUSTOMER_001', 'TEST_POLICY_001'] + ['TEST_LOCATION_001', 'TEST_PHONE_001', 'TEST_EMAIL_001']
    RETURN 
        start.id as start_id, 
        labels(start)[0] as start_type,
        type(r) as relationship,
        end.id as end_id,
        labels(end)[0] as end_type
    ORDER BY start_id, relationship, end_id
    """
    full_result = execute_graph_query(graph, full_graph_check)
    
    print(f"\n🌐 TÜM TEST RELATIONSHIP'LERİ ({len(full_result) if full_result else 0}):")
    if full_result:
        for rel in full_result:
            print(f"  {rel['start_id']} ({rel['start_type']}) --[{rel['relationship']}]--> {rel['end_id']} ({rel['end_type']})")
    
    # Başarı metrikleri
    success_metrics = {
        'extracted_from_count': len(extracted_result) if extracted_result else 0,
        'policy_business_count': len(policy_result) if policy_result else 0,
        'customer_business_count': len(customer_result) if customer_result else 0,
        'total_relationships': len(full_result) if full_result else 0
    }
    
    print(f"\n📊 BAŞARI METRİKLERİ:")
    print(f"  ✅ EXTRACTED_FROM: {success_metrics['extracted_from_count']}")
    print(f"  🏢 Policy Business: {success_metrics['policy_business_count']} ")
    print(f"  👤 Customer Business: {success_metrics['customer_business_count']}")
    print(f"  🌐 Toplam İlişki: {success_metrics['total_relationships']}")
    
    # Beklenen sonuçlar
    expected_extracted = 3  # 3 entity olmalı
    expected_policy_business = 3  # Policy hepsine bağlanmalı
    expected_customer_business = 3  # Customer da Person/Location/Organization'a bağlanmalı (Phone ve Email dahil edildi)
    
    print(f"\n🎯 BAŞARI DURUMU:")
    print(f"  EXTRACTED_FROM: {'✅' if success_metrics['extracted_from_count'] == expected_extracted else '❌'} ({success_metrics['extracted_from_count']}/{expected_extracted})")
    print(f"  Policy Business: {'✅' if success_metrics['policy_business_count'] == expected_policy_business else '❌'} ({success_metrics['policy_business_count']}/{expected_policy_business})")
    print(f"  Customer Business: {'✅' if success_metrics['customer_business_count'] == expected_customer_business else '❌'} ({success_metrics['customer_business_count']}/{expected_customer_business})")
    
    if (success_metrics['extracted_from_count'] == expected_extracted and 
        success_metrics['policy_business_count'] == expected_policy_business and
        success_metrics['customer_business_count'] == expected_customer_business):
        print("\n🎉 TÜM TESTLER BAŞARILI!")
    else:
        print("\n⚠️ Bazı testler beklenenden farklı sonuç verdi")
    
    # Test verilerini temizle
    execute_graph_query(graph, cleanup_query)
    print("\n🧹 Test verileri temizlendi")

if __name__ == "__main__":
    test_business_relationships()
