#!/usr/bin/env python3

"""
ESNEK NODE - KATI RELATIONSHIP FİLTRELEME TEST V2
Bu test, LLM'i allowed olmayan node tipleri üretmeye zorlar
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

def test_esnek_node_zorlu():
    """LLM'i allowed olmayan node tipleri üretmeye zorla"""
    
    print("🧪 ESNEK NODE FİLTRELEME - ZORLU TEST")
    print("=" * 60)
    
    # Çok kısıtlı allowed types - LLM'i yeni tip oluşturmaya zorlamak için
    allowed_nodes = [
        "Policy", "Customer"  # Sadece 2 tip - diğerleri allowed değil
    ]
    
    allowed_relationships = [
        "HAS_POLICY", "HAS_ENTITY"  # Çok kısıtlı liste
    ]
    
    # LLMGraphTransformer oluştur
    transformer = LLMGraphTransformer(
        llm=llm,
        allowed_nodes=allowed_nodes,
        allowed_relationships=allowed_relationships,
        strict_mode=True,
        graph=graph
    )
    
    # Test metni - LLM'in birçok farklı entity türü çıkarması gereken metin
    test_text = """MEVCUT CONTEXT:
POLİÇE SAHİBİ: Ahmet Yılmaz
MÜŞTERİ: Ahmet Yılmaz

METİN:
Ahmet Yılmaz'ın BMW marka araç poliçesi mevcuttur.
Aracın markası: BMW
Aracın modeli: 320i
Aracın rengi: Beyaz
Araç plakası: 34ABC123
Acente adı: Dinkal Sigorta Acentesi
Acente adresi: İstanbul Şişli
Acente telefonu: 0212-555-1234
Poliçe başlangıç tarihi: 15 Ocak 2023
Poliçe bitiş tarihi: 15 Ocak 2024
Prim tutarı: 15.000 TL
Poliçe türü: Kasko sigortası
Sigortalanan: BMW 320i
Hasarsızlık indirimi: %40
Önceki sigorta şirketi: Allianz
Yeni sigorta şirketi: Axa
Ekspertiz raporu: Onaylandı
Ekspertiz tarihi: 10 Ocak 2023
Ekspertiz şirketi: SGK Ekspertiz
"""
    
    # Document oluştur
    document = Document(
        page_content=test_text,
        metadata={"chunk_id": "test_chunk_zorlu"}
    )
    
    print(f"🎯 ALLOWED NODES (çok kısıtlı): {allowed_nodes}")
    print(f"🔗 ALLOWED RELATIONSHIPS (çok kısıtlı): {allowed_relationships}")
    print(f"📝 Test metni - birçok farklı entity içeriyor")
    
    print("\n📋 BEKLENEN SONUÇLAR:")
    print("🏷️ Node Beklentileri (ESNEK):")
    print("   ✅ Allowed tipler: Policy, Customer")
    print("   🆕 Yeni tipler oluşturulmalı: Vehicle, Brand, Model, Color, LicensePlate, Agent, Address, Phone, Date, Amount, PolicyType, InsuredItem, Company, ExpertReport, vb.")
    print("   📊 Sonuç: Hem allowed (2 tip) hem çok sayıda yeni tip olmalı")
    
    print("\n🔗 Relationship Beklentileri (KATI):")
    print("   ✅ Sadece: HAS_POLICY, HAS_ENTITY")
    print("   ❌ Diğer tipler reddedilmeli")
    
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
        preferred_types = []
        new_types = []
        
        for node_type in node_types:
            nodes_of_type = [node.id for node in result.nodes if node.type == node_type]
            
            if node_type in allowed_nodes:
                preferred_types.append(node_type)
                print(f"  ✅ {node_type}: {len(nodes_of_type)} adet (tercih edilen)")
            else:
                new_types.append(node_type)
                print(f"  🆕 {node_type}: {len(nodes_of_type)} adet (yeni tip - esnek kabul)")
            
            # İlk birkaç örneği göster
            for node_id in nodes_of_type[:2]:
                print(f"    └─ {node_id}")
        
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
            
            # İlk birkaç örneği göster
            for source, target in rels_of_type[:2]:
                print(f"    └─ {source} --[{rel_type}]--> {target}")
        
        # Test sonuçları değerlendirmesi
        print(f"\n📊 TEST DEĞERLENDİRMESİ:")
        
        # Test 1: Node çeşitliliği (hem preferred hem yeni tipler olmalı)
        test1_pass = len(preferred_types) > 0 and len(new_types) >= 3  # En az 3 yeni tip bekliyoruz
        print(f"  Test 1 - Node çeşitliliği: Tercih edilen {len(preferred_types)}, Yeni {len(new_types)} {'✅' if test1_pass else '❌'}")
        
        # Test 2: Esnek node kabul (çok sayıda node olmalı)
        test2_pass = len(result.nodes) >= 8  # En az 8 node bekliyoruz
        print(f"  Test 2 - Esnek node kabul: {len(result.nodes)} node kabul edildi {'✅' if test2_pass else '❌'}")
        
        # Test 3: Katı relationship filtresi (sadece allowed olanlar kalmalı)
        test3_pass = rejected_rel_count == 0
        print(f"  Test 3 - Katı relationship filtresi: {rejected_rel_count} izinsiz relationship {'✅' if test3_pass else '❌'}")
        
        # Test 4: İzinli relationship'lar korunmalı
        test4_pass = allowed_rel_count > 0
        print(f"  Test 4 - İzinli relationship koruma: {allowed_rel_count} izinli relationship {'✅' if test4_pass else '❌'}")
        
        # Test 5: Yeni node tiplerinin anlamlı olması
        meaningful_new_types = [t for t in new_types if len(t) > 2 and t != 'Node']  # Anlamlı tipler
        test5_pass = len(meaningful_new_types) >= 2
        print(f"  Test 5 - Anlamlı yeni tipler: {len(meaningful_new_types)} anlamlı tip {'✅' if test5_pass else '❌'}")
        
        # Genel başarı durumu
        all_tests_pass = test1_pass and test2_pass and test3_pass and test4_pass and test5_pass
        print(f"\n🎯 GENEL SONUÇ:")
        if all_tests_pass:
            print(f"  🎉 TÜM TESTLER BAŞARILI! Esnek node + Katı relationship filtresi mükemmel çalışıyor.")
        elif test3_pass and test4_pass:  # En azından relationship filtresi çalışıyor
            print(f"  🔄 KISMİ BAŞARI! Relationship filtresi çalışıyor, node esnekliği geliştirilmeli.")
        else:
            print(f"  ❌ BAŞARISIZ! Filtreleme mantığı düzeltilmeli.")
        
        # Detaylı anlamlı tipler listesi
        if meaningful_new_types:
            print(f"\n🆕 ANLAMLI YENİ TİPLER:")
            for typ in meaningful_new_types:
                print(f"    • {typ}")
        
        # Detaylı istatistikler
        print(f"\n📈 DETAYLI İSTATİSTİKLER:")
        print(f"  📊 Toplam node: {len(result.nodes)}")
        print(f"  📊 Tercih edilen node tipleri: {len(preferred_types)} tip")
        print(f"  📊 Yeni node tipleri: {len(new_types)} tip")
        print(f"  📊 Anlamlı yeni tipler: {len(meaningful_new_types)} tip")
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
    success = test_esnek_node_zorlu()
    print(f"\n{'🎉 TEST BAŞARILI' if success else '❌ TEST BAŞARISIZ'}")
