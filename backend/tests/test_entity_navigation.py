#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Navigation Test - Entity'den Policy/Customer'a Ulaşma
Entity → Chunk → Document → Policy/Customer navigation'ı test eder
"""

import sys
import os
from dotenv import load_dotenv

# .env dosyasını yükle
load_dotenv()

sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from langchain_neo4j import Neo4jGraph
from src.shared.common_fn import execute_graph_query

def test_entity_navigation():
    print("🧭 ENTITY NAVIGATION TESTİ")
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
    
    # Gerçek sistemdeki entity'lerden bir tane bulalım
    print("\n🔍 Sistemdeki entity'leri listeliyoruz...")
    
    entity_list_query = """
    MATCH (e:__Entity__)-[:EXTRACTED_FROM]->(c:Chunk)-[:PART_OF]->(d:Document)
    RETURN e.id as entity_id, 
           labels(e) as entity_labels,
           c.id as chunk_id,
           d.fileName as document_name
    LIMIT 5
    """
    
    entity_list = execute_graph_query(graph, entity_list_query)
    
    if not entity_list:
        print("❌ Sistemde hiç entity bulunamadı")
        return
    
    print(f"📋 Sistemde {len(entity_list)} entity bulundu:")
    for i, entity in enumerate(entity_list):
        print(f"  {i+1}. {entity['entity_id']} ({entity['entity_labels']}) → {entity['document_name']}")
    
    # İlk entity ile navigation testini yapalım
    test_entity = entity_list[0]
    test_entity_id = test_entity['entity_id']
    test_document = test_entity['document_name']
    
    print(f"\n🎯 Test Entity: {test_entity_id}")
    print(f"📄 Test Document: {test_document}")
    
    # Navigation: Entity → Policy
    print("\n🔍 Entity'den Policy'ye Navigation:")
    entity_to_policy_query = """
    MATCH (e:__Entity__ {id: $entity_id})-[:EXTRACTED_FROM]->(c:Chunk)-[:PART_OF]->(d:Document)<-[:DOCUMENTED_IN]-(p:Policy)
    RETURN e.id as entity_id,
           c.id as chunk_id,
           d.fileName as document_name,
           p.id as policy_id,
           p.policyNumber as policy_number
    """
    
    policy_result = execute_graph_query(graph, entity_to_policy_query, params={"entity_id": test_entity_id})
    
    if policy_result:
        for result in policy_result:
            print(f"  ✅ {result['entity_id']} → Chunk({result['chunk_id'][:8]}...) → {result['document_name']} → Policy({result['policy_id']})")
            if result['policy_number']:
                print(f"     Policy Number: {result['policy_number']}")
    else:
        print("  ❌ Bu entity'den Policy'ye ulaşılamadı")
    
    # Navigation: Entity → Customer
    print("\n🔍 Entity'den Customer'a Navigation:")
    entity_to_customer_query = """
    MATCH (e:__Entity__ {id: $entity_id})-[:EXTRACTED_FROM]->(c:Chunk)-[:PART_OF]->(d:Document)<-[:HAS_DOC]-(cust:Customer)
    RETURN e.id as entity_id,
           c.id as chunk_id,
           d.fileName as document_name,
           cust.id as customer_id,
           cust.name as customer_name
    """
    
    customer_result = execute_graph_query(graph, entity_to_customer_query, params={"entity_id": test_entity_id})
    
    if customer_result:
        for result in customer_result:
            print(f"  ✅ {result['entity_id']} → Chunk({result['chunk_id'][:8]}...) → {result['document_name']} → Customer({result['customer_id']})")
            if result['customer_name']:
                print(f"     Customer Name: {result['customer_name']}")
    else:
        print("  ❌ Bu entity'den Customer'a ulaşılamadı")
    
    # Aynı document'taki diğer entity'leri bulma
    print("\n🔍 Aynı Document'taki Diğer Entity'ler:")
    same_document_query = """
    MATCH (d:Document {fileName: $document_name})<-[:PART_OF]-(c:Chunk)<-[:EXTRACTED_FROM]-(e:__Entity__)
    WHERE e.id <> $entity_id
    RETURN e.id as entity_id,
           labels(e) as entity_labels,
           c.position as chunk_position
    ORDER BY c.position
    LIMIT 10
    """
    
    same_doc_result = execute_graph_query(graph, same_document_query, params={
        "document_name": test_document,
        "entity_id": test_entity_id
    })
    
    if same_doc_result:
        print(f"  📋 Aynı document'ta {len(same_doc_result)} diğer entity bulundu:")
        for result in same_doc_result:
            print(f"    • {result['entity_id']} ({result['entity_labels']}) [Chunk pos: {result['chunk_position']}]")
    else:
        print("  ℹ️ Aynı document'ta başka entity bulunamadı")
    
    # Full navigation path'i gösterelim
    print(f"\n🌐 FULL NAVIGATION PATH ({test_entity_id}):")
    full_path_query = """
    MATCH path = (e:__Entity__ {id: $entity_id})-[:EXTRACTED_FROM]->(c:Chunk)-[:PART_OF]->(d:Document)
    OPTIONAL MATCH (d)<-[:DOCUMENTED_IN]-(p:Policy)
    OPTIONAL MATCH (d)<-[:HAS_DOC]-(cust:Customer)
    RETURN e.id as entity_id,
           c.position as chunk_position,
           d.fileName as document_name,
           p.id as policy_id,
           cust.id as customer_id
    """
    
    full_path_result = execute_graph_query(graph, full_path_query, params={"entity_id": test_entity_id})
    
    if full_path_result:
        result = full_path_result[0]
        print(f"  Entity: {result['entity_id']}")
        print(f"  ↓ [EXTRACTED_FROM]")
        print(f"  Chunk: Position {result['chunk_position']}")
        print(f"  ↓ [PART_OF]")
        print(f"  Document: {result['document_name']}")
        
        if result['policy_id']:
            print(f"  ↑ [DOCUMENTED_IN]")
            print(f"  Policy: {result['policy_id']}")
        
        if result['customer_id']:
            print(f"  ↑ [HAS_DOC]")
            print(f"  Customer: {result['customer_id']}")
    
    print(f"\n✅ Navigation test tamamlandı!")
    print("\n💡 SONUÇ: Entity'ler sadece chunk'a bağlı, oradan document üzerinden business node'lara ulaşılabiliyor!")

if __name__ == "__main__":
    test_entity_navigation()
