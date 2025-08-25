#!/usr/bin/env python3
"""
Neo4j'de chunk'ları kontrol etmek için script
"""

from src.graph_query import create_graph_database_connection, execute_graph_query
from src.shared.constants import QUERY_TO_GET_CHUNKS
from src.utf8_utils import normalize_file_name

def check_chunks_for_file(file_name):
    try:
        # Neo4j bağlantısı
        graph = create_graph_database_connection('bolt://localhost:7687', 'neo4j', 'qwerty5555', 'neo4j')
        print(f"✅ Neo4j bağlantısı kuruldu")

        # Dosya adını normalize et
        normalized_name = normalize_file_name(file_name)
        print(f"📄 Original file name: {file_name}")
        print(f"📄 Normalized file name: {normalized_name}")

        # Chunk'ları sorgula
        print(f"\n🔍 QUERY_TO_GET_CHUNKS sorgusu:")
        print(f"Query: {QUERY_TO_GET_CHUNKS}")
        print(f"Params: {{'filename': '{normalized_name}'}}")
        
        chunks = execute_graph_query(graph, QUERY_TO_GET_CHUNKS, params={'filename': normalized_name})
        print(f"\n📊 Found {len(chunks) if chunks else 0} chunks")

        if chunks:
            print("\n✅ First chunk details:")
            for key, value in chunks[0].items():
                if len(str(value)) > 100:
                    print(f"  {key}: {str(value)[:100]}...")
                else:
                    print(f"  {key}: {value}")
        else:
            print("\n❌ No chunks found")
            
        # Document node kontrol et
        print(f"\n🔍 Document node kontrolü:")
        doc_query = "MATCH (d:Document {fileName: $filename}) RETURN d.fileName, d.file_type, d.chunkNodeCount"
        doc_result = execute_graph_query(graph, doc_query, params={'filename': normalized_name})
        print(f"Document node result: {doc_result}")

        # Chunk node'ları direkt sorgula
        print(f"\n🔍 Chunk node'ları direkt sorgula:")
        chunk_query = "MATCH (c:Chunk)-[:PART_OF]->(d:Document {fileName: $filename}) RETURN c.id, c.text[..100] as preview, c.position ORDER BY c.position"
        chunk_result = execute_graph_query(graph, chunk_query, params={'filename': normalized_name})
        print(f"Direct chunk query result: {len(chunk_result) if chunk_result else 0} chunks found")
        
        if chunk_result:
            for i, chunk in enumerate(chunk_result[:3]):  # İlk 3 chunk'ı göster
                print(f"  Chunk {i+1}: ID={chunk.get('c.id')}, Position={chunk.get('c.position')}")
                print(f"    Preview: {chunk.get('preview')}...")

        # Tüm Document node'ları listele (benzer isimli dosyalar var mı?)
        print(f"\n🔍 Benzer isimli dosyalar:")
        similar_docs_query = "MATCH (d:Document) WHERE d.fileName CONTAINS 'Asude' OR d.fileName CONTAINS 'Poliçesi' RETURN d.fileName ORDER BY d.fileName"
        similar_docs = execute_graph_query(graph, similar_docs_query)
        print(f"Found {len(similar_docs) if similar_docs else 0} similar documents:")
        for doc in (similar_docs or [])[:10]:
            print(f"  - {doc['d.fileName']}")

        graph._driver.close()
        print(f"\n✅ Neo4j bağlantısı kapatıldı")

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    file_name = "Asude Sitesi Yönetimi Ortak Alan Poliçesi 2020.pdf"
    check_chunks_for_file(file_name)
