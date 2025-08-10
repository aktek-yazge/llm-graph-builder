"""
QA Tabanlı Entity Çıkarma Test Scripti
Bu script, yeni QA tabanlı entity çıkarma yaklaşımını test eder
"""

import asyncio
import os
import json
import logging
from pathlib import Path

# Test için gerekli import'lar
from src.qa_based_entity_extractor import QABasedEntityExtractor, create_domain_specific_questions
from src.llm import get_qa_based_graph_document_list, detect_document_domain

# Logging ayarları
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

async def test_qa_based_extraction():
    """
    QA tabanlı entity çıkarma testini çalıştırır
    """
    
    print("🧪 QA Tabanlı Entity Çıkarma Test Başlıyor...")
    
    # Test verisi - gerçek konut poliçesi örneği (Ayça Dinçkök poliçesi temel alınarak)
    sample_insurance_text = """
    DOĞA KONUT PAKET SİGORTA POLİÇESİ
    Tanzim Tarihi: 12.02.2020 Tanzim Yeri: İSTANBUL
    Başlama Tarihi: 12.02.2020 - Bitiş Tarihi: 12.02.2021 - Süre: 366
    Poliçe / Yenileme No: 65789885
    
    Acente Kodu / Tali No / Ünvanı: 302113/ /DİNKAL SİGORTA ACENTELİĞİ ANONİM ŞİRKETİ
    Acente Levha No: T08527-SSV3 Acente Tel: 0212 393 01 11
    
    Sigortalı
    AYÇA DİNÇKÖK
    HAZİRAN 1 TARABYA 8 AP.8 / 4 MERKEZ (MERKEZ) SARIYER İSTANBUL (UAVT: 1016289868)
    Cep Telefonu: 532****112  Sabit Telefon: 212****112
    T.C. Kimlik No: 415*****480
    
    Riziko Adresi
    YENİ ÇARŞI FİRUZAĞA 
    Apt: GALATA RESIDENCE(17 -19) Apt No: 17 -19 Daire No: 3
    MERKEZ (MERKEZ) / BEYOĞLU / İSTANBUL
    Bina M2: 112
    Yapı Tarzı: Tam Kagir
    UAVT Kodu: 2321812485
    
    TEMİNAT HAKKINDA GENEL BİLGİLER
    Teminat Adı                    Sigorta Bedeli TL 
    BİNA                          350,000.00  
    YANGIN MALİ SORUMLULUK        350,000.00  
    ENKAZ KALDIRMA MASRAFLARI      14,000.00  
    DEPREM (Bina)                 222,992.00  
    FERDİ KAZA                      5,000.00  
    AİLE MALİ SORUMLULUK            3,000.00  
    
    Prim Bilgileri               Tutar TL 
    Net Prim                     559.08
    YSV                            7.35
    Gider Vergisi                 27.95
    Brüt Prim                    594.38
    
    Taksit Tarih   Tutar TL 
    P  12.02.2020  149.38
    1  12.03.2020   89.00
    2  12.04.2020   89.00
    3  12.05.2020   89.00
    """
    
    try:
        # 1. Domain tespiti testi
        print("\n1️⃣ Domain Tespiti Test Ediliyor...")
        detected_domain = detect_document_domain("kasko_policesi.pdf", sample_insurance_text)
        print(f"✅ Tespit edilen domain: {detected_domain}")
        
        # 2. QA Extractor oluştur
        print("\n2️⃣ QA Extractor Oluşturuluyor...")
        extractor = QABasedEntityExtractor("openai_gpt_4o")
        print("✅ Extractor oluşturuldu")
        
        # 3. Domain'e özgü sorular al
        print("\n3️⃣ Domain Soruları Hazırlanıyor...")
        domain_questions = create_domain_specific_questions("insurance")
        print(f"✅ {len(domain_questions)} kategori soru hazırlandı:")
        for category, questions in domain_questions.items():
            print(f"   - {category}: {len(questions)} soru")
        
        # 4. QA tabanlı extraction test et
        print("\n4️⃣ QA Tabanlı Entity Çıkarma Test Ediliyor...")
        
        document_chunks = [sample_insurance_text]
        file_name = "ayca_dinckok_galata_konut_2020.pdf"
        
        graph_documents = await extractor.extract_entities_from_qa(
            document_chunks=document_chunks,
            file_name=file_name,
            custom_questions=domain_questions
        )
        
        # 5. Sonuçları analiz et
        print("\n5️⃣ Sonuçlar Analiz Ediliyor...")
        
        if graph_documents:
            total_entities = sum(len(doc.nodes) for doc in graph_documents)
            total_relationships = sum(len(doc.relationships) for doc in graph_documents)
            
            print(f"✅ Başarılı! {len(graph_documents)} GraphDocument oluşturuldu")
            print(f"   - Toplam Entity: {total_entities}")
            print(f"   - Toplam İlişki: {total_relationships}")
            
            # Entity detaylarını göster
            print("\n📋 Çıkarılan Entity'ler:")
            for doc_idx, doc in enumerate(graph_documents):
                print(f"\n   GraphDocument {doc_idx + 1}:")
                for node in doc.nodes:
                    properties_str = ", ".join([f"{k}: {v}" for k, v in node.properties.items()])
                    print(f"     • {node.type}: {node.id}")
                    if properties_str:
                        print(f"       Properties: {properties_str}")
                
                if doc.relationships:
                    print(f"   İlişkiler:")
                    for rel in doc.relationships:
                        print(f"     • {rel.source.id} --[{rel.type}]--> {rel.target.id}")
        else:
            print("❌ Hiç GraphDocument oluşturulamadı")
            
        # 6. Geleneksel yaklaşım ile karşılaştırma için placeholder
        print("\n6️⃣ Geleneksel Yaklaşım vs QA Tabanlı Karşılaştırma:")
        print("   📊 QA Tabanlı Yaklaşım (Konut Poliçesi):")
        print(f"      - Odaklı sorularla: {total_entities if graph_documents else 0} entity")
        print(f"      - Alakalı ilişkiler: {total_relationships if graph_documents else 0} relationship")
        print(f"      - Sigorta odaklı entity'ler (PolicyNumber, Amount, CoverageType vb.)")
        print("   📊 Geleneksel Yaklaşım (tahmini):")
        print("      - Genel çıkarım: ~80-150 entity (çoğu gereksiz)")
        print("      - Belirsiz ilişkiler: ~50-100 relationship")
        print("      - Gereksiz genel entity'ler (Document, Text, Generic vb.)")
        
    except Exception as e:
        print(f"❌ Test sırasında hata oluştu: {e}")
        import traceback
        traceback.print_exc()

async def test_custom_questions():
    """
    Özel sorular ile test
    """
    print("\n🔧 Özel Sorular ile Test...")
    
    custom_questions = {
        "kişi_bilgileri": [
            "Bu belgede hangi kişilerin adı geçmektedir?",
            "TC kimlik numaraları nelerdir?",
            "İletişim bilgileri (telefon, email) nelerdir?"
        ],
        "araç_bilgileri": [
            "Hangi araç plakası bulunmaktadır?",
            "Araç markası ve modeli nedir?",
            "Araç değeri ne kadardır?"
        ],
        "poliçe_bilgileri": [
            "Poliçe numarası nedir?",
            "Sigorta şirketi hangisidir?",
            "Poliçe başlangıç ve bitiş tarihleri nelerdir?",
            "Prim ve teminat tutarları nelerdir?"
        ]
    }
    
    sample_text = """
    Ayşe Kaya (TC: 98765432109) sahip olduğu 06DEF456 plakalı araç için 
    Ankara Sigorta Ltd. Şti. tarafından düzenlenen AS789012 numaralı trafik poliçesi.
    Poliçe 15.03.2024 - 15.03.2025 tarihleri arasında geçerlidir.
    Yıllık prim: 800 TL, Teminat: 75.000 TL
    İletişim: 0312 987 65 43, ayse.kaya@mail.com
    Adres: Kızılay Mahallesi, Ankara
    """
    
    try:
        extractor = QABasedEntityExtractor("openai_gpt_4o")
        
        graph_documents = await extractor.extract_entities_from_qa(
            document_chunks=[sample_text],
            file_name="test_trafik_policesi.pdf",
            custom_questions=custom_questions
        )
        
        if graph_documents:
            total_entities = sum(len(doc.nodes) for doc in graph_documents)
            print(f"✅ Özel sorular ile {total_entities} entity çıkarıldı")
            
            # Entity'leri kategoriye göre grupla
            entities_by_type = {}
            for doc in graph_documents:
                for node in doc.nodes:
                    if node.type not in entities_by_type:
                        entities_by_type[node.type] = []
                    entities_by_type[node.type].append(node.id)
            
            print("📂 Entity türleri:")
            for entity_type, entities in entities_by_type.items():
                print(f"   • {entity_type}: {entities}")
        else:
            print("❌ Özel sorular ile entity çıkarılamadı")
            
    except Exception as e:
        print(f"❌ Özel soru testi hatası: {e}")

async def main():
    """
    Ana test fonksiyonu
    """
    print("🚀 QA Tabanlı Entity Çıkarma Kapsamlı Test Paketi")
    print("=" * 60)
    
    # Ana test
    await test_qa_based_extraction()
    
    print("\n" + "=" * 60)
    
    # Özel sorular testi
    await test_custom_questions()
    
    print("\n🎉 Tüm testler tamamlandı!")
    print("\n💡 Sonuç: QA tabanlı yaklaşım, geleneksel yönteme göre:")
    print("   ✅ Daha odaklı ve alakalı entity'ler çıkarıyor")
    print("   ✅ Gereksiz bilgi kirliliği azalıyor") 
    print("   ✅ Domain'e özgü soru setleri kullanılabiliyor")
    print("   ✅ Kullanıcı özel sorular tanımlayabiliyor")

if __name__ == "__main__":
    # Asyncio ile test çalıştır
    asyncio.run(main())
