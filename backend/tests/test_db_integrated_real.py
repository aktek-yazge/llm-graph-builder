#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DB ENTEGRELİ GERÇEK TEST
========================
Gerçek Neo4j veritabanı ile simple JSON + Türkçe prompt modu testi
"""

import os
import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

from langchain_openai import ChatOpenAI
from langchain_core.documents import Document
from src.graph_transformer.transformer import LLMGraphTransformer
from langchain_community.graphs import Neo4jGraph

def test_db_integrated_simple_json_mode():
    print("🗄️ DB ENTEGRELİ SIMPLE JSON + TÜRKÇe PROMPT TESTİ")
    print("=" * 60)
    
    # Neo4j bağlantısını oluştur
    try:
        graph = Neo4jGraph(
            url=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            username=os.getenv("NEO4J_USERNAME", "neo4j"),
            password=os.getenv("NEO4J_PASSWORD", "password")
        )
        print("✅ Neo4j bağlantısı başarılı")
        
        # DB'deki mevcut schema'yı göster
        print("\n🔍 MEVCUT NEO4J SCHEMA:")
        labels_result = graph.query("CALL db.labels()")
        node_labels = [record["label"] for record in labels_result]
        print(f"📊 Node labels: {node_labels}")
        
        rel_types_result = graph.query("CALL db.relationshipTypes()")
        relationship_types = [record["relationshipType"] for record in rel_types_result]
        print(f"🔗 Relationship types: {relationship_types}")
        
    except Exception as e:
        print(f"❌ Neo4j bağlantı hatası: {e}")
        return
    
    # LLM'i oluştur
    try:
        llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0,
            api_key=os.getenv("OPENAI_API_KEY")
        )
        print("✅ OpenAI LLM oluşturuldu")
    except Exception as e:
        print(f"❌ LLM oluşturma hatası: {e}")
        return
    
    # Transformer'ı DB entegreli olarak oluştur
    print("\n🚀 Transformer oluşturuluyor (DB entegreli)...")
    transformer = LLMGraphTransformer(
        llm=llm,
        allowed_nodes=[],  # DB'den otomatik çekilecek
        allowed_relationships=[],  # DB'den otomatik çekilecek
        ignore_tool_usage=True,  # Unstructured mode
        use_simple_json_mode=True,  # Simple JSON + Türkçe mod
        use_db_schema=True,  # DB schema'sını kullan
        graph=graph  # Graph instance'ını ver
    )
    
    print(f"🎯 DB'den çekilen allowed nodes: {transformer.allowed_nodes}")
    print(f"🔗 DB'den çekilen allowed relationships: {transformer.allowed_relationships}")
    
    # Gerçek chunk metni (mevcut belge parçası)
    test_text = """
Poliçe Sahibi: Ayça Dinçkök
Adres: Galata Residans D4 No:15 Beyoğlu/İSTANBUL
Telefon: 0532 123 4567
E-posta: ayca.dinckol@email.com
Meslek: Mimar

Sigorta Şirketi: Doğa Sigorta A.Ş.
Acente: Galata Sigorta Acentesi
Poliçe Numarası: DS-KNT-2024-001
Poliçe Türü: Konut Sigortası
Teminat Başlangıcı: 15.01.2024
Teminat Bitişi: 15.01.2025

Sigortalanan Konut: Galata Residans D4 Daire No:15
Adres: Galata Mahallesi Bankalar Caddesi No:25 D:15 Beyoğlu/İSTANBUL
Yapım Yılı: 2018
Daire Alanı: 85 m²
Kat: 4. Kat
"""

    print(f"\n🔍 TEST METNİ UZUNLUĞU: {len(test_text)} karakter")
    print(f"📝 Test metni başlangıcı: {test_text[:100]}...")

    print(f"\n🚀 LLM çağrılıyor (DB ENTEGRELİ + SIMPLE JSON)...")

    # Document oluştur ve metadata ekle (chunk ID simülasyonu)
    doc = Document(
        page_content=test_text.strip(),
        metadata={
            "chunk_id": "chunk_test_001",
            "document_id": "doc_ayca_konut",
            "source": "test"
        }
    )
    
    try:
        # Transform et
        result = transformer.process_response(doc)
        
        print("\n✅ SONUÇLAR:")
        print(f"📊 Node sayısı: {len(result.nodes)}")
        print(f"📊 Relationship sayısı: {len(result.relationships)}")
        
        print(f"\n🏷️ ÇIKARILAN NODE'LAR:")
        for i, node in enumerate(result.nodes, 1):
            node_props = f" (props: {list(node.properties.keys())})" if node.properties else ""
            print(f"  {i}. {node.id} → {node.type}{node_props}")
        
        print(f"\n🔗 ÇIKARILAN RELATIONSHIP'LER:")
        for i, rel in enumerate(result.relationships, 1):
            rel_props = f" (props: {list(rel.properties.keys())})" if rel.properties else ""
            print(f"  {i}. {rel.source.id} --[{rel.type}]--> {rel.target.id}{rel_props}")
        
        # DB'deki allowed tipler ile karşılaştır
        print(f"\n📈 ANALİZ:")
        extracted_node_types = list(set([node.type for node in result.nodes]))
        extracted_rel_types = list(set([rel.type for rel in result.relationships]))
        
        print(f"🎯 Çıkarılan node tipleri: {extracted_node_types}")
        print(f"🔗 Çıkarılan relationship tipleri: {extracted_rel_types}")
        
        # Allowed tiplerle karşılaştır
        node_type_compliance = []
        for node_type in extracted_node_types:
            if node_type in transformer.allowed_nodes:
                node_type_compliance.append(f"✅ {node_type} (allowed)")
            else:
                node_type_compliance.append(f"🆕 {node_type} (yeni tip)")
        
        rel_type_compliance = []
        for rel_type in extracted_rel_types:
            if rel_type in transformer.allowed_relationships:
                rel_type_compliance.append(f"✅ {rel_type} (allowed)")
            else:
                rel_type_compliance.append(f"❌ {rel_type} (allowed dışı)")
        
        print(f"\n📊 NODE TİP UYGUNLUĞU:")
        for compliance in node_type_compliance:
            print(f"  {compliance}")
            
        print(f"\n📊 RELATIONSHIP TİP UYGUNLUĞU:")
        for compliance in rel_type_compliance:
            print(f"  {compliance}")
            
    except Exception as e:
        print(f"❌ Transform hatası: {e}")
        import traceback
        traceback.print_exc()

    print("\n✅ DB entegreli test tamamlandı!")

if __name__ == "__main__":
    test_db_integrated_simple_json_mode()
