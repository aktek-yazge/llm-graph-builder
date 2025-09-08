#!/usr/bin/env python3
"""
Zeyilname extraction test script
"""
import sys
import os
sys.path.append('/Users/mehmeterdogan/python-projects/llm-graph-builder/backend')

from src.graphDB_dataAccess import graphDBdataAccess
from langchain_neo4j import Neo4jGraph
from dotenv import load_dotenv

# .env dosyasını yükle
load_dotenv('/Users/mehmeterdogan/python-projects/llm-graph-builder/backend/.env')

def test_extraction():
    try:
        # Neo4j bağlantısı
        graph = Neo4jGraph(
            url=os.getenv('NEO4J_URI'),
            username=os.getenv('NEO4J_USERNAME'), 
            password=os.getenv('NEO4J_PASSWORD')
        )
        
        db = graphDBdataAccess(graph)
        
        # Test dosyaları
        test_files = [
            'Asude Sitesi Yönetimi Ortak Alan Poliçesi_2020.pdf',  # Ana poliçe
            'Asude Sitesi Yönetimi Ortak Alan YMM İlave Zeyli_2020.pdf',  # Zeyilname
            'Mehmet Yılmaz BMW X5 Kasko Zeyilname 2023.pdf',  # Başka zeyilname
            'Ayça Dinçkök Galata Residance D6 Konut Ek Teminat Zeyli 2024.pdf'  # Başka zeyilname
        ]
        
        print("=" * 80)
        print("ZEYILNAME EXTRACTION TEST")
        print("=" * 80)
        
        for file_name in test_files:
            print(f"\n📄 Test dosyası: {file_name}")
            print("-" * 60)
            
            try:
                # Extract policy info
                policy_info = db.extract_policy_info_from_filename(file_name)
                
                if policy_info:
                    print(f"✅ Extraction başarılı:")
                    print(f"   Customer: {policy_info.get('customer_name', 'N/A')}")
                    print(f"   Policy Type: {policy_info.get('policy_type', 'N/A')}")
                    print(f"   Document Type: {policy_info.get('document_type', 'N/A')}")
                    print(f"   Policy Number: {policy_info.get('policy_number', 'N/A')}")
                    print(f"   Year: {policy_info.get('year', 'N/A')}")
                    print(f"   Insured Item: {policy_info.get('insured_item', 'N/A')}")
                    print(f"   Extraction Method: {policy_info.get('extraction_method', 'N/A')}")
                    
                    # Document node test (sadece gerçek dosyalar için)
                    if 'Asude' in file_name:
                        print(f"\n   📋 Document Node Test:")
                        doc_query = f"""
                        MATCH (d:Document {{fileName: '{file_name}'}})
                        RETURN d.docType, d.policyYear, d.linkedMainPolicy
                        """
                        try:
                            # Bu test için DB bağlantısını kullan
                            result = db.graph.query(doc_query)
                            if result:
                                doc_info = result[0]
                                print(f"      Doc Type: {doc_info.get('d.docType', 'N/A')}")
                                print(f"      Policy Year: {doc_info.get('d.policyYear', 'N/A')}")
                                print(f"      Linked Main Policy: {doc_info.get('d.linkedMainPolicy', 'N/A')}")
                            else:
                                print(f"      Document node bulunamadı")
                        except Exception as e:
                            print(f"      Document test hatası: {e}")
                    
                    # Document type kontrolü
                    doc_type = policy_info.get('document_type', 'UNKNOWN')
                    if 'zeyl' in file_name.lower() or 'ek' in file_name.lower():
                        if doc_type == 'ENDORSEMENT':
                            print(f"   ✅ Zeyilname doğru tanındı: {doc_type}")
                        else:
                            print(f"   ❌ Zeyilname yanlış tanındı: {doc_type} (ENDORSEMENT olmalıydı)")
                    elif doc_type == 'MAIN_POLICY':
                        print(f"   ✅ Ana poliçe doğru tanındı: {doc_type}")
                    else:
                        print(f"   ⚠️  Belirsiz document type: {doc_type}")
                        
                else:
                    print(f"❌ Extraction başarısız - policy_info boş")
                    
            except Exception as e:
                print(f"❌ Extraction hatası: {e}")
        
        print("\n" + "=" * 80)
        print("TEST TAMAMLANDI")
        print("=" * 80)
        
        # LLM prompt test
        print(f"\n🔍 LLM Prompt Test - Sadece dosya ismi extraction:")
        test_filenames = [
            'Asude Sitesi Yönetimi Ortak Alan YMM İlave Zeyli_2020',
            'Mehmet Yılmaz BMW X5 Kasko Zeyilname 2023',
            'Ayça Dinçkök Galata Residance D6 Konut Ek Teminat Zeyli 2024'
        ]
        
        for test_filename in test_filenames:
            print(f"\n📝 Test: {test_filename}")
            try:
                filename_info = db._extract_policy_info_with_llm(test_filename)
                print(f"   LLM sonucu: {filename_info}")
                
                doc_type = filename_info.get('document_type', 'UNKNOWN') if filename_info else 'FAILED'
                if doc_type == 'ENDORSEMENT':
                    print(f"   ✅ LLM zeyilnameyi doğru tanıdı: {doc_type}")
                elif doc_type == 'MAIN_POLICY':
                    print(f"   ❌ LLM zeyilnameyi yanlış tanıdı: {doc_type} (ENDORSEMENT olmalıydı)")
                else:
                    print(f"   ⚠️ LLM belirsiz sonuç: {doc_type}")
                    
            except Exception as e:
                print(f"   ❌ LLM test hatası: {e}")
            
    except Exception as e:
        print(f"❌ Test setup hatası: {e}")

if __name__ == "__main__":
    test_extraction()
