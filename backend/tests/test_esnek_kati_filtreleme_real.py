#!/usr/bin/env python3

"""
ESNEK NODE - KATI RELATIONSHIP FİLTRELEME TEST
Bu test, yeni filtreleme yaklaşımını gerçek LLM ile doğrular:
- Node tipleri: Esnek (allowed listeden seçmeyi tercih et, ama yeni tipleri de kabul et)
- Relationship tipleri: Katı (sadece allowed liste)
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

def test_esnek_kati_filtreleme():
    """Esnek node / katı relationship filtreleme mantığını test et"""
    
    print("🧪 ESNEK NODE - KATI RELATIONSHIP FİLTRELEME TEST")
    print("=" * 60)
    
    # Allowed types tanımla
    allowed_nodes = [
        "Policy", "Customer", "PolicyType", "InsuredItem", 
        "PolicyYear", "Document"  # Kısıtlı liste - yeni tipler de kabul edilmeli
    ]
    
    allowed_relationships = [
        "HAS_POLICY", "HAS_ENTITY", "HAS_TYPE", "HAS_INSURED_ITEM", 
        "HAS_YEAR", "DOCUMENTED_IN", "HAS_DOC", "EXTRACTED_FROM"  # Katı liste
    ]
    
    # LLMGraphTransformer oluştur
    transformer = LLMGraphTransformer(
        llm=llm,
        allowed_nodes=allowed_nodes,
        allowed_relationships=allowed_relationships,
        strict_mode=True,
        graph=graph
    )
    
    # Test metni - LLM'in kendiliğinden yeni node tipleri ve izinsiz relationship'lar üretmesini sağlamak için
    test_text = """Mevcut Context:
MEVCUT BELGELER: Ahmet Yılmaz - Kasko Poliçesi 2023
POLİÇE SAHİBİ: Ahmet Yılmaz  
MÜŞTERİ: Ahmet Yılmaz
ADRES: İstanbul Beşiktaş Ortaköy Mah.

METİN:
Ahmet Yılmaz'ın 2023 yılında aldığı kasko poliçesi bulunmaktadır.
Poliçe sahibi Ahmet Yılmaz'dır. 
Aracın markası BMW, modeli 520i'dir.
Plaka: 34ABC123
Acente Kodu: 12345
Acentenin adı: Dinkal Sigorta Acentesi
Acentenin adresi: İstanbul Şişli
Poliçe türü: Kasko sigortası
Sigortalanan: BMW 520i araç
Poliçe numarası: POL-2023-456789
Başlangıç tarihi: 01.01.2023
Bitiş tarihi: 31.12.2023
Prim tutarı: 15.000 TL
"""
    
    # Document oluştur
    document = Document(
        page_content=test_text,
        metadata={"chunk_id": "test_chunk_esnek_kati"}
    )
    
    print(f"🎯 ALLOWED NODES (esnek): {allowed_nodes}")
    print(f"🔗 ALLOWED RELATIONSHIPS (katı): {allowed_relationships}")
    print(f"📝 Test metni uzunluğu: {len(test_text)} karakter")
    
    print("\n📋 BEKLENEN SONUÇLAR:")
    print("🏷️ Node Beklentileri (ESNEK):")
    print("   ✅ Allowed tipler: Policy, Customer, PolicyType, InsuredItem, PolicyYear, Document")
    print("   🆕 Yeni tipler kabul edilmeli: Vehicle, Brand, Model, Agent, Address, Amount, Date, vb.")
    print("   📊 Sonuç: Hem allowed hem yeni tipler olmalı")
    
    print("\n🔗 Relationship Beklentileri (KATI):")
    print("   ✅ Sadece allowed tipler kabul edilmeli")
    print("   ❌ Yeni tipler reddedilmeli (OWNS, LOCATED_AT, HAS_BRAND, vb.)")
    
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
            for node_id in nodes_of_type[:3]:
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
        test1_pass = len(preferred_types) > 0 and len(new_types) > 0
        print(f"  Test 1 - Node çeşitliliği: Tercih edilen {len(preferred_types)}, Yeni {len(new_types)} {'✅' if test1_pass else '❌'}")
        
        # Test 2: Esnek node kabul (tüm node'lar kabul edilmeli)
        total_expected_nodes = len([item for item in test_text.split() if any(c.isupper() for c in item)])  # Rough estimate
        test2_pass = len(result.nodes) > 3  # En az birkaç node olmalı
        print(f"  Test 2 - Esnek node kabul: {len(result.nodes)} node kabul edildi {'✅' if test2_pass else '❌'}")
        
        # Test 3: Katı relationship filtresi (sadece allowed olanlar kalmalı)
        test3_pass = rejected_rel_count == 0
        print(f"  Test 3 - Katı relationship filtresi: {rejected_rel_count} izinsiz relationship {'✅' if test3_pass else '❌'}")
        
        # Test 4: İzinli relationship'lar korunmalı
        test4_pass = allowed_rel_count > 0
        print(f"  Test 4 - İzinli relationship koruma: {allowed_rel_count} izinli relationship {'✅' if test4_pass else '❌'}")
        
        # Genel başarı durumu
        all_tests_pass = test1_pass and test2_pass and test3_pass and test4_pass
        print(f"\n🎯 GENEL SONUÇ:")
        if all_tests_pass:
            print(f"  🎉 TÜM TESTLER BAŞARILI! Esnek node + Katı relationship filtresi çalışıyor.")
        else:
            print(f"  ⚠️ BAZI TESTLER BAŞARISIZ! Filtreleme mantığı gözden geçirilmeli.")
        
        # Detaylı istatistikler
        print(f"\n📈 DETAYLI İSTATİSTİKLER:")
        print(f"  📊 Toplam node: {len(result.nodes)}")
        print(f"  📊 Tercih edilen node tipleri: {len(preferred_types)} tip")
        print(f"  📊 Yeni node tipleri: {len(new_types)} tip")
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
    success = test_esnek_kati_filtreleme()
    print(f"\n{'🎉 TEST BAŞARILI' if success else '❌ TEST BAŞARISIZ'}")
