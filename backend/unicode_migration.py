#!/usr/bin/env python3
"""
Unicode Normalization Migration for existing Neo4j Documents
"""

import os
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from src.utf8_utils import normalize_unicode_text, normalize_file_name, validate_utf8_text
from neo4j import GraphDatabase
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Neo4j bağlantı bilgileri
NEO4J_URI = os.getenv('NEO4J_URI', 'neo4j://localhost:7687')
NEO4J_USER = os.getenv('NEO4J_USERNAME', 'neo4j')
NEO4J_PASSWORD = os.getenv('NEO4J_PASSWORD', 'qwerty5555')

def migrate_unicode_document_names():
    """Mevcut database'deki document fileName'lerini Unicode normalize et"""
    
    print("🔄 UNICODE NORMALIZATION MIGRATION BAŞLATILIYOR...")
    
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    
    with driver.session() as session:
        # 1. Tüm document'ları al
        print("\n1. Mevcut document'lar getiriliyor...")
        
        get_documents_query = """
        MATCH (d:Document)
        WHERE d.fileName IS NOT NULL
        RETURN d.fileName AS originalFileName,
               elementId(d) AS documentId
        ORDER BY d.fileName
        """
        
        documents = session.run(get_documents_query)
        document_list = list(documents)
        
        print(f"✅ {len(document_list)} document bulundu")
        
        # 2. Unicode normalization gerekenleri tespit et
        print("\n2. Normalization gereken document'lar kontrol ediliyor...")
        
        needs_normalization = []
        no_change_needed = []
        
        for doc in document_list:
            original = doc['originalFileName']
            normalized = normalize_file_name(original)  # Yeni utility fonksiyonunu kullan
            
            if original != normalized:
                needs_normalization.append({
                    'documentId': doc['documentId'],
                    'original': original,
                    'normalized': normalized
                })
                print(f"   🔄 Normalize edilecek: '{original}' -> '{normalized}'")
            else:
                no_change_needed.append(original)
        
        print(f"\n📊 Normalization Analizi:")
        print(f"   🔄 Normalize edilecek: {len(needs_normalization)} document")
        print(f"   ✅ Zaten normalize: {len(no_change_needed)} document")
        
        if len(needs_normalization) == 0:
            print("🎉 Tüm document'lar zaten normalize!")
            driver.close()
            return
        
        # 3. Normalization işlemini başlat
        print(f"\n3. {len(needs_normalization)} document normalize ediliyor...")
        
        update_query = """
        MATCH (d:Document)
        WHERE elementId(d) = $documentId
        SET d.fileName = $normalizedFileName
        RETURN d.fileName AS updatedFileName
        """
        
        updated_count = 0
        failed_count = 0
        
        for doc_info in needs_normalization:
            try:
                result = session.run(update_query, {
                    'documentId': doc_info['documentId'],
                    'normalizedFileName': doc_info['normalized']
                })
                
                updated = list(result)
                if updated:
                    updated_count += 1
                    print(f"   ✅ Updated: '{doc_info['original']}' -> '{doc_info['normalized']}'")
                else:
                    failed_count += 1
                    print(f"   ❌ Failed: '{doc_info['original']}'")
                    
            except Exception as e:
                failed_count += 1
                print(f"   ❌ Error updating '{doc_info['original']}': {e}")
        
        # 4. Sonuç raporu
        print(f"\n{'='*60}")
        print(f"📊 UNICODE NORMALIZATION MIGRATION ÖZETİ")
        print(f"{'='*60}")
        print(f"📋 Toplam Document: {len(document_list)}")
        print(f"🔄 Normalize Edildi: {updated_count}")
        print(f"❌ Başarısız: {failed_count}")
        print(f"✅ Zaten Normalize: {len(no_change_needed)}")
        
        if updated_count > 0:
            print(f"\n🎉 Migration başarıyla tamamlandı!")
            print(f"   🔍 Artık 'Ayça' aramaları çalışacak!")
        else:
            print(f"\n⚠️ Hiçbir document normalize edilmedi.")
            
        # 5. Test: Ayça document'larını kontrol et
        print(f"\n5. Test: Normalize edilmiş Ayça document'ları...")
        
        test_query = """
        MATCH (d:Document) 
        WHERE d.fileName CONTAINS "Ayça" 
        AND d.fileName CONTAINS "2020"
        RETURN d.fileName 
        ORDER BY d.fileName 
        LIMIT 10
        """
        
        test_results = session.run(test_query)
        ayca_docs = list(test_results)
        
        if ayca_docs:
            print(f"✅ {len(ayca_docs)} Ayça 2020 document'ı bulundu:")
            for i, doc in enumerate(ayca_docs[:5], 1):
                print(f"   {i}. {doc['d.fileName']}")
            
            # Hedef belge var mı kontrol et
            target_found = any('galata' in doc['d.fileName'].lower() and 'd4' in doc['d.fileName'].lower() 
                             for doc in ayca_docs)
            if target_found:
                print(f"   🎯 Hedef belge (Galata D4) normalize edildi!")
        else:
            print(f"❌ Ayça 2020 document'ları hala bulunamadı")
    
    driver.close()
    print(f"\n✅ Migration tamamlandı!")

if __name__ == "__main__":
    migrate_unicode_document_names()
