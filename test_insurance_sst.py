#!/usr/bin/env python3
"""
Insurance SST Test Script
Sigorta dokümanları ile SST extraction'ı test eder
"""

import os
import sys
sys.path.append('/Users/mehmeterdogan/python-projects/llm-graph-builder/backend')

from src.graph_transformer.transformer import LLMGraphTransformer
from src.llm import get_llm
from langchain.schema import Document
import json

def test_insurance_sst_extraction():
    """Insurance SST extraction test"""
    
    # Test dokümanı - sigorta poliçesi örneği
    test_insurance_text = """
    Ahmet Cemal Dördüncü adına düzenlenen KIRAZ tekne poliçesi 01/07/2020 tarihinde başlamaktadır. 
    Poliçe DOGA SIGORTA A.Ş. tarafından düzenlenmiş olup, sigortalı tekne 2019 yılında inşa edilmiştir.
    
    Ödeme planı:
    - Peşinat: 475,00 EUR (01/07/2020)
    - 1. Taksit: 285,00 EUR (01/08/2020)  
    - 2. Taksit: 285,00 EUR (01/09/2020)
    - 3. Taksit: 285,00 EUR (01/10/2020)
    - 4. Taksit: 285,00 EUR (01/11/2020)
    - 5. Taksit: 285,00 EUR (01/12/2020)
    
    Toplam prim: 1.900,00 EUR
    Motor gücü: 2 x VOLVO 75 HP
    Kullanım amacı: Özel amaçlı
    Muafiyet oranı: %0,5
    """
    
    print("🔬 Insurance SST Extraction Test Başlıyor...")
    print(f"📄 Test dokümanı uzunluğu: {len(test_insurance_text)} karakter")
    
    # LLM ve transformer hazırlığı
    llm, _ = get_llm("openai_gpt_4o")
    
    print("🔧 LLMGraphTransformer SST modu ile başlatılıyor...")
    transformer = LLMGraphTransformer(
        llm=llm,
        use_sst_mode=True,  # SST mode aktif
        enable_llm_logging=True,
        ignore_tool_usage=False,
        additional_instructions="Focus on insurance policy details, payment schedules, and coverage information."
    )
    
    # Document oluştur
    doc = Document(
        page_content=test_insurance_text,
        metadata={
            "source": "test_insurance_document.pdf",
            "document_type": "marine_insurance_policy"
        }
    )
    
    print("🚀 SST extraction başlıyor...")
    
    try:
        # Graph extraction
        graph_docs = transformer.convert_to_graph_documents([doc])
        
        if graph_docs and len(graph_docs) > 0:
            graph_doc = graph_docs[0]
            
            print("\n✅ SST EXTRACTION SONUÇLARI:")
            print(f"📊 Statement node sayısı: {len(graph_doc.nodes)}")
            print(f"🔗 Relationship sayısı: {len(graph_doc.relationships)}")
            
            print("\n📝 EXTRACTED STATEMENTS:")
            for i, node in enumerate(graph_doc.nodes):
                print(f"{i+1}. [{node.type}] {node.id}")
            
            print(f"\n🔗 EXTRACTED RELATIONSHIPS:")
            for i, rel in enumerate(graph_doc.relationships):
                print(f"{i+1}. {rel.source.id} --[{rel.type}]--> {rel.target.id}")
            
            # JSON formatında kaydet
            result = {
                "statements": [{"id": node.id, "type": node.type} for node in graph_doc.nodes],
                "relationships": [
                    {
                        "source": rel.source.id,
                        "relation": rel.type, 
                        "target": rel.target.id
                    } for rel in graph_doc.relationships
                ]
            }
            
            with open('insurance_sst_test_result.json', 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            
            print(f"\n💾 Sonuçlar insurance_sst_test_result.json dosyasına kaydedildi")
            
            # Kalite analizi
            print(f"\n📈 KALİTE ANALİZİ:")
            
            # Statement kalitesi analizi
            insurance_keywords = ['poliçe', 'prim', 'taksit', 'sigorta', 'EUR', 'tarih', 'motor', 'muafiyet']
            keyword_statements = []
            for node in graph_doc.nodes:
                for keyword in insurance_keywords:
                    if keyword.lower() in node.id.lower():
                        keyword_statements.append(node.id)
                        break
            
            print(f"🎯 Insurance keyword içeren statements: {len(keyword_statements)}/{len(graph_doc.nodes)}")
            
            # Temporal relationship analizi  
            temporal_relations = [rel for rel in graph_doc.relationships if rel.type in ['LEADS_TO']]
            print(f"⏰ Temporal relationships (LEADS_TO): {len(temporal_relations)}")
            
            # Amount/money statements
            amount_statements = [node for node in graph_doc.nodes if 'EUR' in node.id or 'euro' in node.id.lower()]
            print(f"💰 Amount içeren statements: {len(amount_statements)}")
            
            return True
            
        else:
            print("❌ Hiç graph document oluşturulamadı!")
            return False
            
    except Exception as e:
        print(f"❌ Extraction hatası: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_insurance_sst_extraction()
    if success:
        print("\n🎉 Test başarıyla tamamlandı!")
    else:
        print("\n💥 Test başarısız!")
