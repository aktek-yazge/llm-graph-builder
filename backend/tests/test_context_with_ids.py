#!/usr/bin/env python3

"""
Context fonksiyonunu ID'lerle test et
"""

import sys
import os
from dotenv import load_dotenv

# .env dosyasını yükle
load_dotenv()

sys.path.append('/Users/mehmeterdogan/python-projects/llm-graph-builder/backend')
sys.path.append('/Users/mehmeterdogan/python-projects/llm-graph-builder/backend/src')

# Database'e bağlan
from langchain_neo4j import Neo4jGraph

print("🔍 CONTEXT ID TESTİ")
print("=" * 50)

# Neo4j bağlantısı
graph = Neo4jGraph(
    url=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
    username=os.getenv("NEO4J_USERNAME", "neo4j"), 
    password=os.getenv("NEO4J_PASSWORD", "password")
)

# Önce mevcut chunk'ları listele
chunks_query = """
MATCH (c:Chunk)-[:PART_OF]->(d:Document)
RETURN c.id as chunk_id, d.fileName as doc_name
LIMIT 5
"""

chunks = graph.query(chunks_query)
print(f"📄 Mevcut chunk'lar ({len(chunks)} adet):")
for chunk in chunks:
    print(f"  - Chunk ID: {chunk['chunk_id']}, Document: {chunk['doc_name']}")

if chunks:
    # İlk chunk'ı test et
    test_chunk_id = chunks[0]['chunk_id']
    print(f"\n🎯 Test chunk ID: {test_chunk_id}")
    
    # Context fonksiyonunu test et
    from src.graph_transformer.transformer import get_existing_context_for_prompt
    
    context = get_existing_context_for_prompt(graph, test_chunk_id)
    
    print("\n📋 CONTEXT SONUCU:")
    print("=" * 50)
    if context:
        print(context)
        print("=" * 50)
        print(f"✅ Context uzunluğu: {len(context)} karakter")
    else:
        print("❌ Context bulunamadı")
else:
    print("❌ Hiç chunk bulunamadı")
