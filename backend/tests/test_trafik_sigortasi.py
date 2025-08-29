#!/usr/bin/env python3
"""
Trafik Sigortası Entity Çıkarımı Testi

Bu test, LLM'in trafik sigortası metinlerinden spesifik entity'leri (vehicle plate, marka, model vb.) 
çıkarıp çıkaramadığını test eder.

Beklenen:
- Müşteri: Customer
- Araç plakası: VehiclePlate  
- Araç markası: VehicleBrand
- Araç modeli: VehicleModel
- Sigorta şirketi: Company
- Polis no: PolicyNumber
- Tarih: Date
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

def test_trafik_sigortasi_entity_cikarimi():
    """
    Trafik sigortası metni ile entity çıkarımı testi
    """
    print("🚗 TRAFİK SİGORTASI ENTITY ÇIKARIMI TESTİ")
    print("=" * 60)
    
    # Test metni - gerçek trafik sigortası verisi
    test_text = """
    Müşteri: Mehmet Özkan
    Araç Plakası: 34 ABC 123
    Marka: Toyota
    Model: Corolla
    Model Yılı: 2020
    Sigorta Şirketi: Axa Sigorta A.Ş.
    Poliçe No: TR-2024-567890
    Başlangıç Tarihi: 15.01.2024
    Bitiş Tarihi: 15.01.2025
    Prim Tutarı: 2.450 TL
    Acente: Özkan Sigorta Acentesi
    Acente Kodu: 45678
    """
    
    print("🎯 Allowed nodes: ['Policy', 'Customer']")
    print("🔗 Allowed relationships: ['HAS_POLICY', 'HAS_ENTITY']")
    print(f"📝 Test metni (trafik sigortası):")
    print(f"   {test_text.strip()}")
    
    print("\n🎯 BEKLENEN:")
    print("   • Mehmet Özkan → Customer")
    print("   • 34 ABC 123 → VehiclePlate")
    print("   • Toyota → VehicleBrand")  
    print("   • Corolla → VehicleModel")
    print("   • 2020 → VehicleYear")
    print("   • Axa Sigorta A.Ş. → Company")
    print("   • TR-2024-567890 → PolicyNumber")
    print("   • 15.01.2024 → Date")
    print("   • Özkan Sigorta Acentesi → Agency")
    
    # LLM'i başlat
    llm = ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0
    )
    
    # Transformer'ı başlat - sadece allowed types ile
    transformer = LLMGraphTransformer(
        llm=llm,
        allowed_nodes=['Policy', 'Customer'],  # Sınırlı allowed types
        allowed_relationships=['HAS_POLICY', 'HAS_ENTITY'],
        use_db_schema=False  # DB schema kullanma
    )
    
    # Document oluştur
    doc = Document(page_content=test_text)
    
    print("\n🚀 LLM çağrılıyor...")
    
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
        expected_vehicle_types = ['VehiclePlate', 'VehicleBrand', 'VehicleModel', 'VehicleYear', 'Company', 'PolicyNumber', 'Date', 'Agency']
        found_vehicle_types = [t for t in unique_types if t not in ['Policy', 'Customer']]
        
        print(f"  Beklenen araç/sigorta tipleri: {len(expected_vehicle_types)}")
        print(f"  Bulunan yeni tipler: {len(found_vehicle_types)}")
        print(f"  Bulunan tipler: {found_vehicle_types}")
        
        if len(found_vehicle_types) > 0:
            print(f"  ✅ BAŞARILI: Yeni node tipleri yaratıldı!")
            print(f"  🎯 Yaratılan spesifik tipler: {found_vehicle_types}")
        else:
            print(f"  ❌ BAŞARISIZ: Yeni node tipleri yaratılmadı!")
            
        # Araç plakası kontrolü
        vehicle_plates = [node for node in graph_doc.nodes if "34 ABC 123" in node.id]
        if vehicle_plates:
            plate_type = vehicle_plates[0].type
            print(f"  🚗 Araç plakası tipi: {plate_type}")
            if plate_type != 'Customer':
                print(f"  ✅ Araç plakası doğru tipte çıkarıldı: {plate_type}")
            else:
                print(f"  ❌ Araç plakası yanlış tipte: {plate_type}")
    else:
        print("❌ Sonuç alınamadı!")

if __name__ == "__main__":
    test_trafik_sigortasi_entity_cikarimi()
