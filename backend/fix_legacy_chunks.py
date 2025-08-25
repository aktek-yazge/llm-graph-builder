#!/usr/bin/env python3
"""
Eski chunk'lar için eksik PART_OF ve FIRST_CHUNK ilişkilerini oluştur
"""

from src.graph_query import create_graph_database_connection, execute_graph_query

def fix_legacy_chunk_relationships(file_name):
    try:
        # Neo4j bağlantısı
        graph = create_graph_database_connection('bolt://localhost:7687', 'neo4j', 'qwerty5555', 'neo4j')
        print(f"✅ Neo4j bağlantısı kuruldu")

        # 1. PART_OF ilişkilerini oluştur
        part_of_query = """
        MATCH (d:Document {fileName: $file_name})
        MATCH (c:Chunk {fileName: $file_name})
        MERGE (c)-[:PART_OF]->(d)
        RETURN count(*) as created_part_of
        """
        part_of_result = execute_graph_query(graph, part_of_query, params={'file_name': file_name})
        print(f"✅ Created {part_of_result[0]['created_part_of']} PART_OF relationships")

        # 2. FIRST_CHUNK ilişkisini oluştur (position = 0 olan chunk)
        first_chunk_query = """
        MATCH (d:Document {fileName: $file_name})
        MATCH (c:Chunk {fileName: $file_name, position: 0})
        MERGE (d)-[:FIRST_CHUNK]->(c)
        RETURN count(*) as created_first_chunk
        """
        first_result = execute_graph_query(graph, first_chunk_query, params={'file_name': file_name})
        print(f"✅ Created {first_result[0]['created_first_chunk']} FIRST_CHUNK relationship")

        # 3. İlişkileri doğrula
        verify_query = """
        MATCH (d:Document {fileName: $file_name})
        OPTIONAL MATCH (d)<-[:PART_OF]-(c1:Chunk)
        OPTIONAL MATCH (d)-[:FIRST_CHUNK]->(c2:Chunk)
        RETURN count(DISTINCT c1) as part_of_count, count(DISTINCT c2) as first_chunk_count
        """
        verify_result = execute_graph_query(graph, verify_query, params={'file_name': file_name})
        print(f"📊 Verification: {verify_result[0]['part_of_count']} PART_OF, {verify_result[0]['first_chunk_count']} FIRST_CHUNK")

        graph._driver.close()
        print(f"✅ Neo4j bağlantısı kapatıldı")

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    file_name = "Asude Sitesi Yönetimi Ortak Alan Poliçesi 2020.pdf"
    fix_legacy_chunk_relationships(file_name)
