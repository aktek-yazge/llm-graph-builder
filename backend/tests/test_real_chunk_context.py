#!/usr/bin/env python3

"""
Gerçek chunk ile context ID testi
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
from src.graph_transformer.transformer import LLMGraphTransformer
from langchain_openai import ChatOpenAI
from langchain_core.documents import Document

print("🎯 GERÇEK CHUNK İLE CONTEXT TEST")
print("=" * 60)

# Neo4j bağlantısı
graph = Neo4jGraph(
    url=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
    username=os.getenv("NEO4J_USERNAME", "neo4j"), 
    password=os.getenv("NEO4J_PASSWORD", "password")
)

# Gerçek bir chunk'ı al
chunk_query = """
MATCH (c:Chunk)-[:PART_OF]->(d:Document)
WHERE d.fileName CONTAINS "Asude"
RETURN c.id as chunk_id, c.text as text, d.fileName as doc_name
LIMIT 1
"""

chunks = graph.query(chunk_query)
if not chunks:
    print("❌ Asude belgesi chunk'ı bulunamadı")
    exit(1)

chunk = chunks[0]
chunk_id = chunk['chunk_id']
chunk_text = chunk['text']
doc_name = chunk['doc_name']

print(f"📄 Document: {doc_name}")
print(f"🆔 Chunk ID: {chunk_id}")
print(f"📝 Chunk text ({len(chunk_text)} karakter):")
print(f"   {chunk_text[:200]}...")

# LLM setup
llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0
)

# Transformer setup - DB'den schema çeksin
transformer = LLMGraphTransformer(
    llm=llm,
    allowed_nodes=[],  # DB'den çeksin
    allowed_relationships=[],  # DB'den çeksin
    use_db_schema=True,
    graph=graph  # Context için gerekli
)

# Document oluştur - chunk_id metadata'sında olsun
document = Document(
    page_content=chunk_text,
    metadata={"chunk_id": chunk_id}
)

print(f"\n🚀 LLM çağrılıyor...")
print(f"🎯 Allowed nodes: {transformer.allowed_nodes}")
print(f"🔗 Allowed relationships: {transformer.allowed_relationships}")

# Entity extraction
graph_doc = transformer.process_response(document)

print(f"\n✅ SONUÇLAR:")
print(f"📊 Node sayısı: {len(graph_doc.nodes)}")
print(f"📊 Relationship sayısı: {len(graph_doc.relationships)}")

print(f"\n🏷️ ÇIKARILAN NODE'LAR:")
for i, node in enumerate(graph_doc.nodes, 1):
    print(f"  {i}. {node.id} → {node.type}")

print(f"\n🔗 ÇIKARILAN RELATIONSHIP'LER:")
for i, rel in enumerate(graph_doc.relationships, 1):
    print(f"  {i}. {rel.source.id} --[{rel.type}]--> {rel.target.id}")

print(f"\n📋 CONTEXT KULLANIMI:")
print("Context ile mevcut node'ları tekrar oluşturdu mu?")
existing_policy_names = ["Asude Sitesi Yönetimi Ortak Alan Poliçesi 2020"]
for node in graph_doc.nodes:
    if node.id in existing_policy_names:
        print(f"❌ MEVCUT NODE TEKRAR OLUŞTURULDU: {node.id}")
    elif node.type == "Policy" and "Asude" in node.id:
        print(f"❌ MEVCUT POLICY BENZERI OLUŞTURULDU: {node.id}")

print("✅ Test tamamlandı!")
