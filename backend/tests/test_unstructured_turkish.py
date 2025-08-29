#!/usr/bin/env python3

"""
Unstructured mode ile Türkçe prompt test
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

print("🇹🇷 UNSTRUCTURED TÜRKÇE PROMPT TESTİ")
print("=" * 60)

# Neo4j bağlantısı
graph = Neo4jGraph(
    url=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
    username=os.getenv("NEO4J_USERNAME", "neo4j"), 
    password=os.getenv("NEO4J_PASSWORD", "password")
)

# Test metni
test_text = """
Müşteri: Kemal Demir
Araç Plakası: 35 XYZ 456
Marka: Mercedes
Model: C-Class
Sigorta Şirketi: Zurich Sigorta
Acente: Güven Acentesi
Poliçe No: ZUR-2024-12345
"""

# LLM setup
llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0
)

# Transformer setup - UNSTRUCTURED MODE
transformer = LLMGraphTransformer(
    llm=llm,
    allowed_nodes=['Customer', 'Policy', 'Company'],
    allowed_relationships=['HAS_POLICY', 'HAS_ENTITY'],
    ignore_tool_usage=True,  # UNSTRUCTURED MODE!
    graph=graph
)

print(f"⚙️ Function call modu: {transformer._function_call}")
print(f"🎯 Allowed nodes: {transformer.allowed_nodes}")
print(f"🔗 Allowed relationships: {transformer.allowed_relationships}")

# Document oluştur
document = Document(page_content=test_text)

print(f"\n🚀 LLM çağrılıyor (UNSTRUCTURED MODE)...")

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

print("✅ Unstructured test tamamlandı!")
