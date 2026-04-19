#!/usr/bin/env python3
"""
Quick Test for Combined Query Fix
"""

import sys
sys.path.append('/Users/mehmeterdogan/python-projects/llm-graph-builder/backend/src')

from langchain_neo4j import Neo4jGraph

def test_data_exists():
    """Test if Ayça data exists in Neo4j"""
    
    print("🔍 VERİ KONTROL TESİ BAŞLATILIYOR...")
    
    # Neo4j connection
    graph = Neo4jGraph(
        url="bolt://localhost:7687",
        username="neo4j", 
        password="password"
    )
    
    # Ayça ile ilgili dökümanları kontrol et
    test_query = """
    MATCH (d:Document)
    WHERE apoc.text.clean(d.fileName) CONTAINS apoc.text.clean("Ayça")
    RETURN d.fileName, d.year, count(*) as doc_count
    """
    
    print("📊 Ayça ile ilgili dökümanlar kontrol ediliyor...")
    results = graph.query(test_query)
    
    print("=" * 60)
    print("📋 AYÇA DÖKÜMANLARİ:")
    print("=" * 60)
    
    if results:
        for result in results:
            print(f"File: {result.get('d.fileName', 'N/A')}")
            print(f"Year: {result.get('d.year', 'N/A')}")
            print(f"Count: {result.get('doc_count', 'N/A')}")
            print("-" * 40)
    else:
        print("❌ Ayça ile ilgili hiç döküman bulunamadı!")
    
    # Chunk sayısını kontrol et
    chunk_query = """
    MATCH (d:Document)
    WHERE apoc.text.clean(d.fileName) CONTAINS apoc.text.clean("Ayça")
    MATCH (c:Chunk)-[:PART_OF]->(d)
    RETURN count(c) as total_chunks
    """
    
    chunk_results = graph.query(chunk_query)
    print("=" * 60)
    print("📊 CHUNK SAYISI:")
    print("=" * 60)
    
    if chunk_results:
        total_chunks = chunk_results[0].get('total_chunks', 0)
        print(f"Toplam Ayça chunk'ları: {total_chunks}")
    else:
        print("❌ Hiç chunk bulunamadı!")

if __name__ == "__main__":
    test_data_exists()
