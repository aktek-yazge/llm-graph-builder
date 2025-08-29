#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from langchain_openai import ChatOpenAI
from langchain_core.documents import Document

from src.graph_transformer.transformer import LLMGraphTransformer

def test_context_siz_entity_cikarimi():
    """
    Context'siz basit entity çıkarımı testi
    """
    print("🧪 CONTEXT'SİZ ENTITY ÇIKARIMI TESTİ")
    print("=" * 60)
    
    # LLM oluştur
    llm = ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0.1,
        api_key=os.environ.get("OPENAI_API_KEY")
    )
    
    # Transformer oluştur - çok esnek modda
    transformer = LLMGraphTransformer(
        llm=llm,
        allowed_nodes=['Policy', 'Customer'],  # Çok dar liste
        allowed_relationships=['HAS_POLICY', 'HAS_ENTITY'],
        strict_mode=True,
        use_db_schema=False,  # DB schema kullanma
        graph=None  # Context yok
    )
    
    # Basit tıbbi metin - context yok
    simple_text = """
Dr. Ayşe Demir, Boğaziçi Üniversitesi'nde çalışmaktadır.
Hastanesi: Acıbadem Maslak Hastanesi.
Kullandığı cihaz: EKG Cihazı.
Yayınladığı kitap: "Kalp Hastalıkları Atlası".
Aldığı ödül: Tıp Ödülü 2023.
"""
    
    print(f"📝 Test metni (context YOK):")
    print(f"   {simple_text.strip()}")
    print()
    print("🎯 BEKLENEN:")
    print("   • Dr. Ayşe Demir → Doctor")
    print("   • Boğaziçi Üniversitesi → University") 
    print("   • Acıbadem Maslak Hastanesi → Hospital")
    print("   • EKG Cihazı → Device")
    print("   • Kalp Hastalıkları Atlası → Book")
    print("   • Tıp Ödülü 2023 → Award")
    print()
    
    # Document oluştur (metadata yok)
    document = Document(
        page_content=simple_text,
        metadata={}  # Boş metadata
    )
    
    # Transform et
    print("🚀 LLM çağrılıyor...")
    result = transformer.process_response(document)
    
    # Sonuçları analiz et
    print()
    print("✅ SONUÇLAR:")
    print(f"📊 Node sayısı: {len(result.nodes)}")
    print(f"📊 Relationship sayısı: {len(result.relationships)}")
    print()
    
    print("🏷️ ÇIKARILAN NODE'LAR:")
    for node in result.nodes:
        print(f"  • {node.id} → {node.type}")
    print()
    
    print("🔗 ÇIKARILAN RELATIONSHIP'LER:")
    for rel in result.relationships:
        print(f"  • {rel.source.id} --[{rel.type}]--> {rel.target.id}")
    print()
    
    # Değerlendirme
    node_types = [node.type for node in result.nodes]
    medical_types = ['Doctor', 'University', 'Hospital', 'Device', 'Book', 'Award']
    found_medical = [t for t in medical_types if t in node_types]
    
    print("📊 DEĞERLENDİRME:")
    print(f"  Beklenen tıbbi tipler: {len(medical_types)}")
    print(f"  Bulunan tıbbi tipler: {len(found_medical)}")
    print(f"  Bulunan tipler: {found_medical}")
    
    if len(found_medical) >= 3:
        print("  ✅ BAŞARILI: Yeni node tipleri yaratıldı!")
    else:
        print("  ❌ BAŞARISIZ: Yeni node tipleri yaratılmadı!")

if __name__ == "__main__":
    test_context_siz_entity_cikarimi()
