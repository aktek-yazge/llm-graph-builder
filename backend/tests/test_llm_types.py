#!/usr/bin/env python3

"""
LLM'in güncellenmiş prompt ile doğru node/relationship tiplerini kullanıp kullanmadığını test eder
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

def test_llm_with_correct_types():
    """LLM'in doğru node/relationship tiplerini kullanıp kullanmadığını test et"""
    
    print("🧪 LLM Node/Relationship Tipi Testi Başlıyor...")
    
    # Allowed types tanımla
    allowed_nodes = [
        "Policy", "Customer", "PolicyType", "InsuredItem", 
        "PolicyYear", "Document", "Company", "Location", "Date"
    ]
    
    allowed_relationships = [
        "HAS_POLICY", "HAS_ENTITY", "HAS_TYPE", "HAS_INSURED_ITEM", 
        "HAS_YEAR", "DOCUMENTED_IN", "HAS_DOC", "EXTRACTED_FROM"
    ]
    
    # LLMGraphTransformer oluştur
    transformer = LLMGraphTransformer(
        llm=llm,
        allowed_nodes=allowed_nodes,
        allowed_relationships=allowed_relationships,
        strict_mode=True,
        graph=graph
    )
    
    # Test metni - context ile beraber
    test_text = """MEVCUT CONTEXT:
MEVCUT POLİÇE: Asude Sitesi Yönetimi Ortak Alan Poliçesi 2020
POLİÇE SAHİBİ: Asude Sitesi Yönetimi
MÜŞTERİ: Asude Sitesi Yönetimi
POLİÇE TÜRÜ: Ortak Alan Poliçesi
SİGORTALANAN NESNE: Asude Sitesi

METİN:
Adres: İstanbul, Beşiktaş, Galata Mahallesi
Acente: Dinkal Sigorta Acenteliği A.Ş.
Poliçe Başlangıç Tarihi: 01.01.2020
Poliçe Bitiş Tarihi: 31.12.2020"""
    
    # Document oluştur
    document = Document(
        page_content=test_text,
        metadata={"chunk_id": "test_chunk_123"}
    )
    
    print(f"📝 Test metni uzunluğu: {len(test_text)} karakter")
    print(f"🎯 Allowed nodes: {allowed_nodes}")
    print(f"🔗 Allowed relationships: {allowed_relationships}")
    
    # LLM'i çağır
    try:
        result = transformer.process_response(document)
        
        print(f"\n✅ LLM Sonuçları:")
        print(f"📊 Node sayısı: {len(result.nodes)}")
        print(f"📊 Relationship sayısı: {len(result.relationships)}")
        
        print(f"\n🏷️ Çıkarılan Node Tipleri:")
        node_types = list(set([node.type for node in result.nodes]))
        for node_type in node_types:
            nodes_of_type = [node.id for node in result.nodes if node.type == node_type]
            print(f"  - {node_type}: {nodes_of_type}")
            
            # Allowed kontrolü
            if node_type in allowed_nodes:
                print(f"    ✅ İzin verilen tip")
            else:
                print(f"    ❌ İZİN VERİLMEYEN TİP!")
        
        print(f"\n🔗 Çıkarılan Relationship Tipleri:")
        rel_types = list(set([rel.type for rel in result.relationships]))
        for rel_type in rel_types:
            rels_of_type = [(rel.source.id, rel.target.id) for rel in result.relationships if rel.type == rel_type]
            print(f"  - {rel_type}: {len(rels_of_type)} adet")
            for source, target in rels_of_type[:3]:  # İlk 3'ünü göster
                print(f"    {source} --[{rel_type}]--> {target}")
            
            # Allowed kontrolü
            if rel_type in allowed_relationships:
                print(f"    ✅ İzin verilen tip")
            else:
                print(f"    ❌ İZİN VERİLMEYEN TİP!")
        
        # Başarı oranı hesapla
        valid_nodes = sum(1 for node in result.nodes if node.type in allowed_nodes)
        valid_rels = sum(1 for rel in result.relationships if rel.type in allowed_relationships)
        
        node_success_rate = (valid_nodes / len(result.nodes)) * 100 if result.nodes else 0
        rel_success_rate = (valid_rels / len(result.relationships)) * 100 if result.relationships else 0
        
        print(f"\n📈 Başarı Oranları:")
        print(f"  Node başarı oranı: {node_success_rate:.1f}% ({valid_nodes}/{len(result.nodes)})")
        print(f"  Relationship başarı oranı: {rel_success_rate:.1f}% ({valid_rels}/{len(result.relationships)})")
        
        if node_success_rate == 100 and rel_success_rate == 100:
            print(f"🎉 MÜKEMMEL! LLM tüm tipleri doğru kullandı!")
        elif node_success_rate >= 80 and rel_success_rate >= 80:
            print(f"👍 İYİ! LLM çoğu tipi doğru kullandı.")
        else:
            print(f"⚠️ SORUN VAR! LLM yanlış tipler kullanıyor.")
        
    except Exception as e:
        print(f"❌ Test hatası: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_llm_with_correct_types()
