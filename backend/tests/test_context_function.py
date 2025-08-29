#!/usr/bin/env python3

"""
Context fonksiyonunu test etmek için basit test script'i
"""

import os
from dotenv import load_dotenv
from langchain_neo4j import Neo4jGraph

# .env dosyasını yükle
load_dotenv()

# Neo4j bağlantısını kur
graph = Neo4jGraph(
    url=os.getenv('NEO4J_URI'),
    username=os.getenv('NEO4J_USERNAME'),
    password=os.getenv('NEO4J_PASSWORD'),
    database=os.getenv('NEO4J_DATABASE')
)

def get_existing_context_for_prompt(graph_instance, chunk_id):
    """
    Chunk'a ait mevcut Policy, Customer, PolicyType node'larını bul
    LLM'e bu context'i ver ki doğru bağlantıları kursun
    """
    if not graph_instance or not chunk_id:
        return ""
    
    context_query = """
    MATCH (c:Chunk {id: $chunk_id})-[:PART_OF]->(d:Document)
    
    // Mevcut Policy node'unu bul
    OPTIONAL MATCH (policy:Policy)-[:DOCUMENTED_IN]->(d)
    
    // Mevcut Customer node'unu bul  
    OPTIONAL MATCH (customer:Customer)-[:HAS_DOC]->(d)
    
    // Mevcut PolicyType node'unu bul
    OPTIONAL MATCH (policy)-[:HAS_TYPE]->(policyType:PolicyType)
    
    // Mevcut InsuredItem node'unu bul
    OPTIONAL MATCH (policy)-[:HAS_INSURED_ITEM]->(insuredItem:InsuredItem)
    
    RETURN 
        policy.id as policy_id,
        policy.customer as policy_customer,
        policy.type as policy_type,
        customer.name as customer_name,
        policyType.name as policy_type_name,
        insuredItem.name as insured_item_name
    """
    
    try:
        result = graph_instance.query(context_query, params={"chunk_id": chunk_id})
        
        if not result:
            print(f"❌ Chunk {chunk_id} için sonuç bulunamadı")
            return ""
        
        row = result[0]
        context_parts = []
        
        if row.get('policy_id'):
            context_parts.append(f"MEVCUT POLİÇE: {row['policy_id']}")
            
        if row.get('policy_customer'):
            context_parts.append(f"POLİÇE SAHİBİ: {row['policy_customer']}")
            
        if row.get('customer_name'):
            context_parts.append(f"MÜŞTERİ: {row['customer_name']}")
            
        if row.get('policy_type_name'):
            context_parts.append(f"POLİÇE TÜRÜ: {row['policy_type_name']}")
            
        if row.get('insured_item_name'):
            context_parts.append(f"SİGORTALANAN NESNE: {row['insured_item_name']}")
        
        if context_parts:
            context = "MEVCUT CONTEXT:\n" + "\n".join(context_parts) + "\n\n"
            print(f"✅ Context oluşturuldu: {len(context)} karakter")
            print(f"📋 Context içeriği:\n{context}")
            return context
        else:
            print(f"⚠️ Chunk {chunk_id} için context data bulunamadı")
            return ""
            
    except Exception as e:
        print(f"❌ Context sorgu hatası: {e}")
        return ""

def test_context_function():
    """Context fonksiyonunu test et"""
    
    print("🔍 Chunk listesini al...")
    
    # İlk chunk'ları al
    chunks_query = """
    MATCH (c:Chunk)
    RETURN c.id as chunk_id
    ORDER BY c.position
    LIMIT 5
    """
    
    chunks = graph.query(chunks_query)
    print(f"📊 Bulunan chunk sayısı: {len(chunks)}")
    
    for chunk in chunks:
        chunk_id = chunk['chunk_id']
        print(f"\n🧪 Testing chunk: {chunk_id}")
        
        context = get_existing_context_for_prompt(graph, chunk_id)
        
        if context:
            print(f"✅ Context başarılı: {len(context)} karakter")
        else:
            print(f"❌ Context boş")

if __name__ == "__main__":
    test_context_function()
