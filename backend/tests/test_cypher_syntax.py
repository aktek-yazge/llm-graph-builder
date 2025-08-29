#!/usr/bin/env python3

"""
Cypher syntax'ının doğru olup olmadığını test edelim
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

def test_cypher_syntax():
    """Güncellenmiş Cypher sorgusunun syntax'ını test et"""
    
    print("🧪 Cypher Syntax Test Başlıyor...")
    
    # Test data
    test_data = [
        {
            'chunk_id': 'test_chunk_123',
            'node_type': 'Company',
            'node_id': 'Test Company'
        }
    ]
    
    # Güncellenmiş sorgu
    business_query = """
    UNWIND $batch_data AS data
    MATCH (c:Chunk {id: data.chunk_id})-[:PART_OF]->(d:Document)
    
    // Entity node'unu oluştur veya bul
    CALL apoc.merge.node([data.node_type], {id: data.node_id}) YIELD node AS entity
    SET entity:__Entity__
    
    // EXTRACTED_FROM ilişkisini kur (technical tracking)
    MERGE (entity)-[:EXTRACTED_FROM]->(c)
    
    WITH entity, d, data
    
    // Business Logic: Mevcut Policy node'unu bul
    OPTIONAL MATCH (policy:Policy)-[:DOCUMENTED_IN]->(d)
    
    // Business Logic: Mevcut Customer node'unu bul
    OPTIONAL MATCH (customer:Customer)-[:HAS_DOC]->(d)
    
    // Business relationships kur
    FOREACH (_ IN CASE WHEN policy IS NOT NULL AND entity <> policy THEN [1] ELSE [] END |
        MERGE (policy)-[:HAS_ENTITY]->(entity)
    )
    
    FOREACH (_ IN CASE WHEN customer IS NOT NULL AND entity <> customer AND data.node_type IN ['Person', 'Location', 'Organization'] THEN [1] ELSE [] END |
        MERGE (customer)-[:HAS_ENTITY]->(entity)
    )
    
    RETURN count(DISTINCT entity) as entities_created,
           count(DISTINCT policy) as policies_linked,
           count(DISTINCT customer) as customers_linked
    """
    
    try:
        # Gerçek chunk ID'si al
        chunk_query = "MATCH (c:Chunk) RETURN c.id as chunk_id LIMIT 1"
        chunks = graph.query(chunk_query)
        
        if chunks:
            real_chunk_id = chunks[0]['chunk_id']
            test_data[0]['chunk_id'] = real_chunk_id
            print(f"📋 Gerçek chunk ID kullanılıyor: {real_chunk_id}")
        else:
            print("⚠️ Chunk bulunamadı, test data ile devam ediliyor")
        
        print("🔍 Cypher sorgusu test ediliyor...")
        result = graph.query(business_query, params={"batch_data": test_data})
        
        print("✅ Cypher syntax doğru!")
        print(f"📊 Sonuç: {result}")
        
        if result:
            entities_created = result[0].get('entities_created', 0)
            policies_linked = result[0].get('policies_linked', 0) 
            customers_linked = result[0].get('customers_linked', 0)
            
            print(f"📈 İstatistikler:")
            print(f"  - Entity'ler oluşturuldu: {entities_created}")
            print(f"  - Policy'lere bağlandı: {policies_linked}")
            print(f"  - Customer'lara bağlandı: {customers_linked}")
        
    except Exception as e:
        print(f"❌ Cypher syntax hatası: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_cypher_syntax()
