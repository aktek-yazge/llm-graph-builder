#!/usr/bin/env python3

"""
ESNEK NODE FİLTRELEME - GERÇEK ZORLU TEST
Bu test, LLM'in kesinlikle allowed listede olmayan node tipleri yaratması gereken metinler kullanır
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

def test_gercek_esnek_node():
    """LLM'in kesinlikle yeni node tipleri yaratması gereken test"""
    
    print("🧪 GERÇEK ESNEK NODE FİLTRELEME TEST")
    print("=" * 60)
    
    # Çok dar allowed types - metindeki entity'ler bunlara sığmaz
    allowed_nodes = [
        "Policy", "Customer"  # Sadece 2 tip - diğerleri imkansız
    ]
    
    allowed_relationships = [
        "HAS_POLICY", "HAS_ENTITY"  # Sadece 2 tip
    ]
    
    # LLMGraphTransformer oluştur
    transformer = LLMGraphTransformer(
        llm=llm,
        allowed_nodes=allowed_nodes,
        allowed_relationships=allowed_relationships,
        strict_mode=True,
        graph=graph
    )
    
    # Test metni - hiçbir şekilde allowed node tiplerinde olmayan entity'ler
    test_text = """MEVCUT CONTEXT:
POLİÇE SAHİBİ: Mehmet Özkan
MÜŞTERİ: Mehmet Özkan

METİN:
Dr. Ayşe Demir, Boğaziçi Üniversitesi Tıp Fakültesi'nde görev yapmaktadır.
Profesör Demir'in uzmanlık alanı Kardiyoloji'dir.
Hastanesi: Acıbadem Maslak Hastanesi
Çalıştığı bölüm: Kardiyoloji Kliniği
Asistanı: Dr. Can Kılıç
Hemşire: Zeynep Yılmaz
Kullandığı makine: EKG Cihazı
Yayınladığı kitap: "Kalp Hastalıkları Atlası"
Aldığı ödül: Tıp Ödülü 2023
Doğum yeri: Ankara
Yaşı: 45
Mezun olduğu üniversite: İstanbul Üniversitesi
Lisans derecesi: Tıp Doktoru
Uzmanlık alanı: Kardiyoloji
Çalıştığı yıl: 2023
Maaşı: 50.000 TL
Çalışma saatleri: 08:00-17:00
"""
    
    # Document oluştur
    document = Document(
        page_content=test_text,
        metadata={"chunk_id": "test_chunk_gercek_zorlu"}
    )
    
    print(f"🎯 ALLOWED NODES (çok dar): {allowed_nodes}")
    print(f"🔗 ALLOWED RELATIONSHIPS: {allowed_relationships}")
    print(f"📝 Test metni - hiçbiri allowed node tipine sığmaz:")
    print("   • Dr. Ayşe Demir (Doctor/Person - Customer DEĞİL)")
    print("   • Boğaziçi Üniversitesi (University - Policy/Customer DEĞİL)")
    print("   • Tıp Fakültesi (Faculty - Policy/Customer DEĞİL)")
    print("   • Kardiyoloji (MedicalSpecialty - Policy/Customer DEĞİL)")
    print("   • Acıbadem Maslak Hastanesi (Hospital - Policy/Customer DEĞİL)")
    print("   • EKG Cihazı (MedicalDevice - Policy/Customer DEĞİL)")
    print("   • Kalp Hastalıkları Atlası (Book - Policy/Customer DEĞİL)")
    print("   • Tıp Ödülü 2023 (Award - Policy/Customer DEĞİL)")
    
    print("\n📋 BEKLENEN SONUÇLAR:")
    print("🏷️ Node Beklentileri (ESNEK):")
    print("   ✅ Mevcut: Policy, Customer (az sayıda)")
    print("   🆕 YENİ TİPLER (çok sayıda): Doctor, University, Faculty, Hospital, MedicalSpecialty, MedicalDevice, Book, Award, City, Age, vb.")
    print("   📊 Sonuç: Çok sayıda yeni node tipi olmalı")
    
    print("\n🔗 Relationship Beklentileri (KATI):")
    print("   ✅ Sadece: HAS_POLICY, HAS_ENTITY")
    print("   ❌ WORKS_AT, SPECIALIZES_IN, LOCATED_AT vb. reddedilmeli")
    
    # LLM'i çağır
    try:
        print(f"\n🚀 LLM çağrılıyor...")
        result = transformer.process_response(document)
        
        print(f"\n✅ LLM SONUÇLARI:")
        print(f"📊 Node sayısı: {len(result.nodes)}")
        print(f"📊 Relationship sayısı: {len(result.relationships)}")
        
        # Node analizi
        print(f"\n🏷️ ÇIKARILAN NODE TİPLERİ (ESNEK FİLTRE):")
        node_types = list(set([node.type for node in result.nodes]))
        allowed_types_found = []
        new_types_found = []
        
        for node_type in node_types:
            nodes_of_type = [node.id for node in result.nodes if node.type == node_type]
            
            if node_type in allowed_nodes:
                allowed_types_found.append(node_type)
                print(f"  ✅ {node_type}: {len(nodes_of_type)} adet (allowed)")
            else:
                new_types_found.append(node_type)
                print(f"  🆕 {node_type}: {len(nodes_of_type)} adet (YENİ TİP - esnek kabul)")
            
            # Örnekleri göster
            for node_id in nodes_of_type[:2]:
                print(f"    └─ {node_id}")
        
        # Yeni tiplerin anlamlılığını kontrol et
        medical_related_types = [t for t in new_types_found if any(keyword in t.lower() for keyword in 
                                ['doctor', 'hospital', 'medical', 'university', 'faculty', 'cardio', 'clinic', 'device', 'book', 'award', 'person', 'professor', 'specialist'])]
        
        # Relationship analizi
        print(f"\n🔗 ÇIKARILAN RELATIONSHIP TİPLERİ (KATI FİLTRE):")
        rel_types = list(set([rel.type for rel in result.relationships]))
        allowed_rel_count = 0
        rejected_rel_count = 0
        
        for rel_type in rel_types:
            rels_of_type = [(rel.source.id, rel.target.id) for rel in result.relationships if rel.type == rel_type]
            
            if rel_type in allowed_relationships:
                allowed_rel_count += len(rels_of_type)
                print(f"  ✅ {rel_type}: {len(rels_of_type)} adet (izinli)")
            else:
                rejected_rel_count += len(rels_of_type)
                print(f"  ❌ {rel_type}: {len(rels_of_type)} adet (İZİNSİZ - FİLTRELENMELİYDİ!)")
            
            # Örnekleri göster
            for source, target in rels_of_type[:2]:
                print(f"    └─ {source} --[{rel_type}]--> {target}")
        
        # Test sonuçları değerlendirmesi
        print(f"\n📊 TEST DEĞERLENDİRMESİ:")
        
        # Test 1: Yeni node tipleri yaratıldı mı?
        test1_pass = len(new_types_found) >= 3  # En az 3 yeni tip
        print(f"  Test 1 - Yeni node yaratma: {len(new_types_found)} yeni tip {'✅' if test1_pass else '❌'}")
        
        # Test 2: Yeni tipler anlamlı mı?
        test2_pass = len(medical_related_types) >= 2  # En az 2 anlamlı tip
        print(f"  Test 2 - Anlamlı yeni tipler: {len(medical_related_types)} anlamlı tip {'✅' if test2_pass else '❌'}")
        
        # Test 3: Esnek node kabul (çok node olmalı)
        test3_pass = len(result.nodes) >= 6  # En az 6 node
        print(f"  Test 3 - Çok node kabul: {len(result.nodes)} node {'✅' if test3_pass else '❌'}")
        
        # Test 4: Katı relationship filtresi
        test4_pass = rejected_rel_count == 0
        print(f"  Test 4 - Katı relationship filtresi: {rejected_rel_count} izinsiz rel {'✅' if test4_pass else '❌'}")
        
        # Test 5: İzinli relationship'lar var mı?
        test5_pass = allowed_rel_count > 0
        print(f"  Test 5 - İzinli relationship var: {allowed_rel_count} izinli rel {'✅' if test5_pass else '❌'}")
        
        # Genel başarı durumu
        all_tests_pass = test1_pass and test2_pass and test3_pass and test4_pass and test5_pass
        print(f"\n🎯 GENEL SONUÇ:")
        if all_tests_pass:
            print(f"  🎉 TÜM TESTLER BAŞARILI! Gerçek esnek node filtreleme çalışıyor!")
        elif test1_pass and test4_pass:  # En azından temel hedefler
            print(f"  🔄 TEMEİ BAŞARI! Yeni node tipleri yaratıldı ve relationship filtresi çalışıyor.")
        elif test4_pass and test5_pass:  # Relationship filtresi çalışıyor
            print(f"  ⚠️ KISMİ BAŞARI! Relationship filtresi çalışıyor ama node esnekliği eksik.")
        else:
            print(f"  ❌ BAŞARISIZ! Hem node esnekliği hem relationship filtresi sorunlu.")
        
        # Detaylı sonuçlar
        print(f"\n🆕 YENİ NODE TİPLERİ DETAY:")
        if new_types_found:
            for typ in new_types_found:
                example_nodes = [n.id for n in result.nodes if n.type == typ]
                print(f"    • {typ}: {example_nodes[:2]}")
        else:
            print(f"    ❌ Hiç yeni tip yaratılmadı!")
        
        print(f"\n🏥 TIBBİ ALAN İLE İLGİLİ YENİ TİPLER:")
        if medical_related_types:
            for typ in medical_related_types:
                print(f"    • {typ} ✅")
        else:
            print(f"    ❌ Hiç tıbbi alan ile ilgili tip yaratılmadı!")
        
        # Detaylı istatistikler
        print(f"\n📈 DETAYLI İSTATİSTİKLER:")
        print(f"  📊 Toplam node: {len(result.nodes)}")
        print(f"  📊 Allowed node tipleri: {len(allowed_types_found)} tip")
        print(f"  📊 Yeni node tipleri: {len(new_types_found)} tip")
        print(f"  📊 Anlamlı yeni tipler: {len(medical_related_types)} tip")
        print(f"  📊 Toplam relationship: {len(result.relationships)}")
        print(f"  📊 İzinli relationship: {allowed_rel_count}")
        print(f"  📊 İzinsiz relationship: {rejected_rel_count}")
        
        return all_tests_pass
        
    except Exception as e:
        print(f"❌ Test hatası: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_gercek_esnek_node()
    print(f"\n{'🎉 TEST BAŞARILI' if success else '❌ TEST BAŞARISIZ'}")
