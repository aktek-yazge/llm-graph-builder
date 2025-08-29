#!/usr/bin/env python3

"""
Basit test: LLM'e sadece YENİ entity'leri çıkartmasını söyleyerek daha net bir test yapalım
"""

import os
from dotenv import load_dotenv
from langchain_neo4j import Neo4jGraph
from langchain_openai import ChatOpenAI
from langchain_core.documents import Document
import sys
sys.path.append('.')

from src.graph_transformer.transformer import LLMGraphTransformer

# .env dosyasını yükle
load_dotenv()

# Neo4j bağlantısını kur
graph = Neo4jGraph(
    url=os.getenv('NEO4J_URI'),
    username=os.getenv('NEO4J_USERNAME'),
    password=os.getenv('NEO4J_PASSWORD'),
    database=os.getenv('NEO4J_DATABASE')
)

# LLM'i kur
llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0,
    openai_api_key=os.getenv('OPENAI_API_KEY')
)

def test_context_only_new_entities():
    """LLM'in sadece yeni entity'leri çıkarmasını test et"""
    
    print("🧪 Sadece Yeni Entity Test'i Başlıyor...")
    
    # Allowed types tanımla
    allowed_nodes = [
        "Company", "Location", "Date"  # Sadece yeni entity'ler için izin ver
    ]
    
    allowed_relationships = [
        "HAS_ENTITY", "EXTRACTED_FROM"  # Sadece bağlama ilişkileri
    ]
    
    # LLMGraphTransformer oluştur
    transformer = LLMGraphTransformer(
        llm=llm,
        allowed_nodes=allowed_nodes,
        allowed_relationships=allowed_relationships,
        strict_mode=True,
        graph=graph
    )
    
    # Test metni - çok net
    test_text = """MEVCUT CONTEXT:
MEVCUT POLİÇE: Asude Sitesi Yönetimi Ortak Alan Poliçesi 2020 (Policy)
MÜŞTERİ: Asude Sitesi Yönetimi (Customer)

METİN:
Sadece bu bilgilerden yeni entity'leri çıkar:
Acente: Dinkal Sigorta Acenteliği A.Ş.
Adres: İstanbul, Beşiktaş
Tarih: 01.01.2020"""
    
    # Document oluştur
    document = Document(
        page_content=test_text,
        metadata={"chunk_id": "test_chunk_456"}
    )
    
    print(f"📝 Test metni uzunluğu: {len(test_text)} karakter")
    print(f"🎯 SADECE izin verilen nodes: {allowed_nodes}")
    print(f"🔗 SADECE izin verilen relationships: {allowed_relationships}")
    
    # LLM'i çağır
    try:
        result = transformer.process_response(document)
        
        print(f"\n✅ LLM Sonuçları:")
        print(f"📊 Node sayısı: {len(result.nodes)}")
        print(f"📊 Relationship sayısı: {len(result.relationships)}")
        
        # Bu test'te SADECE Company, Location, Date node'ları olmalı
        expected_nodes = ["Company", "Location", "Date"]
        expected_rels = ["HAS_ENTITY", "EXTRACTED_FROM"]
        
        print(f"\n🔍 Node Analizi:")
        for node in result.nodes:
            print(f"  - {node.id} ({node.type})")
            if node.type in expected_nodes:
                print(f"    ✅ Beklenen tip")
            else:
                print(f"    ❌ BEKLENMEYEN TİP!")
        
        print(f"\n🔍 Relationship Analizi:")
        for rel in result.relationships:
            print(f"  - {rel.source.id} --[{rel.type}]--> {rel.target.id}")
            if rel.type in expected_rels:
                print(f"    ✅ Beklenen tip")
            else:
                print(f"    ❌ BEKLENMEYEN TİP!")
        
        # Beklenen node'lar var mı?
        node_types = [node.type for node in result.nodes]
        has_company = "Company" in node_types
        has_location = "Location" in node_types
        has_date = "Date" in node_types
        
        print(f"\n📋 Beklenen Entity'ler:")
        print(f"  Company (Acente): {'✅' if has_company else '❌'}")
        print(f"  Location (Adres): {'✅' if has_location else '❌'}")
        print(f"  Date (Tarih): {'✅' if has_date else '❌'}")
        
        if has_company and has_location and has_date:
            print(f"🎉 MÜKEMMEL! Tüm yeni entity'ler çıkarıldı!")
        else:
            print(f"⚠️ Eksik entity'ler var")
        
        # Forbidden entity var mı kontrol et
        forbidden_found = any(node.type in ["Policy", "Customer", "PolicyType", "InsuredItem"] for node in result.nodes)
        if forbidden_found:
            print(f"❌ HATA: Mevcut context'ten node'lar yeniden yaratılmış!")
        else:
            print(f"✅ İYİ: Mevcut context node'ları tekrar yaratılmamış")
        
    except Exception as e:
        print(f"❌ Test hatası: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_context_only_new_entities()
