#!/usr/bin/env python3
"""
Konu Dışı Node/Relationship Testi

Bu test, konu ile hiç ilgisi olmayan allowed node/relationship tipleri verip 
LLM'in nasıl davrandığını test eder.

Allowed Nodes: İlgisiz ['Fruit', 'Animal'] 
Allowed Relationships: İlgisiz ['EATS', 'SLEEPS']
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

def test_ilgisiz_node_relationship():
    """
    Konu ile hiç ilgisi olmayan allowed types ile test
    """
    print("🍎 KONU DIŞI NODE/RELATIONSHIP TESTİ")
    print("=" * 50)
    
    # Test metni - trafik sigortası
    test_text = """
    Müşteri: Kemal Demir
    Araç Plakası: 35 XYZ 456
    Marka: Mercedes
    Model: C-Class
    Sigorta Şirketi: Zurich Sigorta
    Acente: Güven Acentesi
    Poliçe No: ZUR-2024-12345
    """
    
    print("🎯 Allowed nodes: ['Fruit', 'Animal'] (KONU DIŞI!)")
    print("🔗 Allowed relationships: ['EATS', 'SLEEPS'] (KONU DIŞI!)")
    print(f"📝 Test metni (trafik sigortası):")
    print(f"   {test_text.strip()}")
    
    print("\n🎯 BEKLENEN (allowed types uygun değil):")
    print("   • Kemal Demir → Customer (Fruit/Animal uygun değil)")
    print("   • 35 XYZ 456 → VehiclePlate (Fruit/Animal uygun değil)")
    print("   • Mercedes → VehicleBrand (Fruit/Animal uygun değil)")  
    print("   • C-Class → VehicleModel (Fruit/Animal uygun değil)")
    print("   • Zurich Sigorta → Company (Fruit/Animal uygun değil)")
    print("   • Güven Acentesi → Agency (Fruit/Animal uygun değil)")
    print("   • ZUR-2024-12345 → PolicyNumber (Fruit/Animal uygun değil)")
    
    # LLM'i başlat
    llm = ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0
    )
    
    # Transformer'ı başlat - KONU DIŞI allowed types
    transformer = LLMGraphTransformer(
        llm=llm,
        allowed_nodes=['Fruit', 'Animal'],  # KONU DIŞI!
        allowed_relationships=['EATS', 'SLEEPS'],  # KONU DIŞI!
        use_db_schema=False
    )
    
    # Document oluştur
    doc = Document(page_content=test_text)
    
    print("\n🚀 LLM çağrılıyor (KONU DIŞI MOD)...")
    
    # Graph'a dönüştür
    result = transformer.convert_to_graph_documents([doc])
    
    print("\n✅ SONUÇLAR:")
    if result and len(result) > 0:
        graph_doc = result[0]
        
        print(f"📊 Node sayısı: {len(graph_doc.nodes)}")
        print(f"📊 Relationship sayısı: {len(graph_doc.relationships)}")
        
        print(f"\n🏷️ ÇIKARILAN NODE'LAR:")
        fruit_animal_count = 0
        new_types = []
        for node in graph_doc.nodes:
            print(f"  • {node.id} → {node.type}")
            if node.type in ['Fruit', 'Animal']:
                fruit_animal_count += 1
            else:
                new_types.append(node.type)
        
        print(f"\n🔗 ÇIKARILAN RELATIONSHIP'LER:")
        eats_sleeps_count = 0
        for rel in graph_doc.relationships:
            print(f"  • {rel.source.id} --[{rel.type}]--> {rel.target.id}")
            if rel.type in ['EATS', 'SLEEPS']:
                eats_sleeps_count += 1
        
        # Değerlendirme
        print(f"\n📊 DEĞERLENDİRME:")
        print(f"  Fruit/Animal kullanan node: {fruit_animal_count}")
        print(f"  Yeni tip yaratan node: {len(new_types)}")
        print(f"  Yaratılan yeni tipler: {new_types}")
        print(f"  EATS/SLEEPS kullanan relationship: {eats_sleeps_count}")
        
        if len(new_types) > fruit_animal_count:
            print(f"  ✅ BAŞARILI: LLM konu dışı tiplerden kaçınıp yeni tipler yaratıyor!")
        else:
            print(f"  ❌ SORUNLU: LLM konu dışı tipleri zorla kullanıyor!")
            
        if eats_sleeps_count == 0:
            print(f"  ✅ RELATIONSHIP DURUMU: Konu dışı relationship kullanılmadı")
        else:
            print(f"  ❌ RELATIONSHIP SORUNU: Konu dışı relationship zorlandı!")
            
    else:
        print("❌ Sonuç alınamadı!")

if __name__ == "__main__":
    test_ilgisiz_node_relationship()
