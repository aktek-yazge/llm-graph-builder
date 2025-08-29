#!/usr/bin/env python3
"""
SERBEST Entity Çıkarımı Testi

Bu test, LLM'e hiçbir allowed node tipi vermeyerek tamamen serbest node tipi yaratmasını test eder.

Beklenen:
- LLM kendi node tiplerini yaratmalı
- Vehicle, Company, PolicyNumber gibi spesifik tipler çıkmalı
"""

import sys
import os
sys.path.append('/Users/mehmeterdogan/python-projects/llm-graph-builder/backend/src')

from src.graph_transformer.transformer import LLMGraphTransformer
from langchain_openai import ChatOpenAI  
from langchain_core.documents import Document
from dotenv import load_dotenv

# .env dosyasını yükle
load_dotenv()

def test_serbest_entity_cikarimi():
    """
    Hiçbir allowed node vermeyerek tamamen serbest entity çıkarımı testi
    """
    print("🆓 SERBEST ENTITY ÇIKARIMI TESTİ")
    print("=" * 50)
    
    # Test metni - trafik sigortası verisi
    test_text = """
    Müşteri: Ali Yılmaz
    Araç Plakası: 06 XYZ 789
    Marka: BMW
    Model: 3 Series
    Sigorta Şirketi: Allianz Türkiye
    Acente: Güven Sigorta
    """
    
    print("🎯 Allowed nodes: BOŞ [] (tamamen serbest)")
    print("🔗 Allowed relationships: ['HAS_ENTITY']")
    print(f"📝 Test metni:")
    print(f"   {test_text.strip()}")
    
    print("\n🎯 BEKLENEN (tamamen serbest):")
    print("   • Ali Yılmaz → Customer/Person")
    print("   • 06 XYZ 789 → VehiclePlate/Plate")
    print("   • BMW → VehicleBrand/Brand")  
    print("   • 3 Series → VehicleModel/Model")
    print("   • Allianz Türkiye → Company/Insurer")
    print("   • Güven Sigorta → Agency/Agent")
    
    # LLM'i başlat
    llm = ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0
    )
    
    # Transformer'ı başlat - ALLOWED NODES BOŞ!
    transformer = LLMGraphTransformer(
        llm=llm,
        allowed_nodes=[],  # BOŞ - tamamen serbest
        allowed_relationships=['HAS_ENTITY'],
        use_db_schema=False  # DB schema kullanma
    )
    
    # Document oluştur
    doc = Document(page_content=test_text)
    
    print("\n🚀 LLM çağrılıyor (SERBEST MOD)...")
    
    # Graph'a dönüştür
    result = transformer.convert_to_graph_documents([doc])
    
    print("\n✅ SONUÇLAR:")
    if result and len(result) > 0:
        graph_doc = result[0]
        
        print(f"📊 Node sayısı: {len(graph_doc.nodes)}")
        print(f"📊 Relationship sayısı: {len(graph_doc.relationships)}")
        
        print(f"\n🏷️ ÇIKARILAN NODE'LAR:")
        unique_types = set()
        for node in graph_doc.nodes:
            print(f"  • {node.id} → {node.type}")
            unique_types.add(node.type)
        
        print(f"\n🔗 ÇIKARILAN RELATIONSHIP'LER:")
        for rel in graph_doc.relationships:
            print(f"  • {rel.source.id} --[{rel.type}]--> {rel.target.id}")
        
        # Değerlendirme
        print(f"\n📊 DEĞERLENDİRME:")
        print(f"  Çıkarılan unique tipler: {len(unique_types)}")
        print(f"  Tip çeşitliliği: {list(unique_types)}")
        
        # Spesifik kontroller
        if any('Vehicle' in t or 'Plate' in t or 'Brand' in t for t in unique_types):
            print(f"  ✅ Araç ile ilgili spesifik tipler bulundu!")
        
        if any('Company' in t or 'Agency' in t or 'Insurer' in t for t in unique_types):
            print(f"  ✅ Şirket ile ilgili spesifik tipler bulundu!")
            
        if len(unique_types) > 2:
            print(f"  ✅ BAŞARILI: Çeşitli node tipleri yaratıldı!")
        else:
            print(f"  ❌ BAŞARISIZ: Yeterli çeşitlilik yok!")
            
    else:
        print("❌ Sonuç alınamadı!")

if __name__ == "__main__":
    test_serbest_entity_cikarimi()
