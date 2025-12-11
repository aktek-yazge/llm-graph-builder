#!/usr/bin/env python3
"""
UTF-8 Test ve Debugging Script
"""

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from src.utf8_utils import *
from src.shared.common_fn import create_graph_database_connection
from src.graphDB_dataAccess import graphDBdataAccess
from dotenv import load_dotenv
import logging
import requests
import json

load_dotenv()

# Neo4j bağlantı bilgileri
NEO4J_URI = os.getenv('NEO4J_URI', 'neo4j://localhost:7687')
NEO4J_USER = os.getenv('NEO4J_USERNAME', 'neo4j')
NEO4J_PASSWORD = os.getenv('NEO4J_PASSWORD', 'qwerty5555')
NEO4J_DATABASE = os.getenv('NEO4J_DATABASE', 'neo4j')

# logging.basicConfig(level=logging.INFO)  # main.py'de yapılıyor

def test_utf8_functions():
    """UTF-8 utility fonksiyonlarını test et"""
    print("=== UTF-8 Utility Fonksiyonları Test ===")
    
    test_cases = [
        "Normal text",
        "Türkçe karakterler: ğüşıöç",
        "Ayça Dinçkök",
        "Çiftçi",
        "Büyükçekmece",
        "İstanbul",
        # Decomposed Unicode test
        "Ayç\u0327a",  # c + cedilla
        "e\u0301",     # e + acute accent
    ]
    
    for i, test_text in enumerate(test_cases, 1):
        normalized = normalize_unicode_text(test_text)
        is_valid = validate_utf8_text(normalized)
        
        print(f"{i}. Original: '{test_text}'")
        print(f"   Normalized: '{normalized}'")
        print(f"   Valid UTF-8: {is_valid}")
        print(f"   Changed: {test_text != normalized}")
        print()

def test_database_connection():
    """Neo4j bağlantısını ve UTF-8 desteğini test et"""
    print("=== Neo4j Bağlantı ve UTF-8 Test ===")
    
    try:
        graph = create_graph_database_connection(
            NEO4J_URI, 
            NEO4J_USER, 
            NEO4J_PASSWORD, 
            NEO4J_DATABASE
        )
        
        print("✅ Neo4j bağlantısı başarılı")
        
        # UTF-8 test verisi oluştur
        test_data = {
            "test_text": "Ayça Dinçkök Galata Residance",
            "normalized_text": normalize_unicode_text("Ayça Dinçkök Galata Residance")
        }
        
        # Test node oluştur
        test_query = """
        MERGE (test:UTF8Test {id: "test"})
        SET test.original_text = $original,
            test.normalized_text = $normalized,
            test.test_time = datetime()
        RETURN test
        """
        
        result = graph.query(test_query, {
            "original": test_data["test_text"],
            "normalized": test_data["normalized_text"]
        })
        
        print(f"✅ UTF-8 test node oluşturuldu")
        print(f"   Original: {test_data['test_text']}")
        print(f"   Normalized: {test_data['normalized_text']}")
        
        # Test node'u oku
        read_query = """
        MATCH (test:UTF8Test {id: "test"})
        RETURN test.original_text as original, test.normalized_text as normalized
        """
        
        read_result = graph.query(read_query)
        if read_result:
            read_data = read_result[0]
            print(f"✅ UTF-8 test node okundu")
            print(f"   Read Original: {read_data['original']}")
            print(f"   Read Normalized: {read_data['normalized']}")
        
        # Test node'u temizle
        cleanup_query = "MATCH (test:UTF8Test {id: 'test'}) DELETE test"
        graph.query(cleanup_query)
        print("✅ Test node temizlendi")
        
        if not graph._driver._closed:
            graph._driver.close()
            
    except Exception as e:
        print(f"❌ Neo4j test hatası: {e}")

def check_existing_documents():
    """Mevcut document'lardaki UTF-8 sorunlarını kontrol et"""
    print("=== Mevcut Document'lar UTF-8 Kontrolü ===")
    
    try:
        graph = create_graph_database_connection(
            NEO4J_URI, 
            NEO4J_USER, 
            NEO4J_PASSWORD, 
            NEO4J_DATABASE
        )
        
        # Tüm document'ları al
        query = """
        MATCH (d:Document)
        WHERE d.fileName IS NOT NULL
        RETURN d.fileName as fileName, elementId(d) as id
        ORDER BY d.fileName
        LIMIT 20
        """
        
        result = graph.query(query)
        
        print(f"Bulunan {len(result)} document:")
        
        problematic_docs = []
        
        for i, doc in enumerate(result, 1):
            original = doc['fileName']
            normalized = normalize_unicode_text(original)
            is_valid = validate_utf8_text(original)
            
            status = "✅" if is_valid and original == normalized else "⚠️"
            
            print(f"{i}. {status} {original}")
            
            if not is_valid or original != normalized:
                problematic_docs.append({
                    'id': doc['id'],
                    'original': original,
                    'normalized': normalized,
                    'valid': is_valid
                })
                print(f"   → Normalized: {normalized}")
        
        if problematic_docs:
            print(f"\n⚠️ {len(problematic_docs)} document'ta UTF-8 sorunu tespit edildi")
            
            # Düzeltme teklifi
            print("\nDüzeltme için şu komutu çalıştırabilirsiniz:")
            print("python unicode_migration.py")
        else:
            print("\n✅ Tüm document'lar UTF-8 uyumlu")
        
        if not graph._driver._closed:
            graph._driver.close()
            
    except Exception as e:
        print(f"❌ Document kontrolü hatası: {e}")

def test_chunk_processing():
    """Chunk processing UTF-8 test"""
    print("=== Chunk Processing UTF-8 Test ===")
    
    test_content = """
    Bu bir test içeriğidir. Türkçe karakterler: ğüşıöç
    Ayça Dinçkök isimli kişi Galata Residance'da yaşıyor.
    Çiftçi ailesi İstanbul'dan gelmiş.
    """
    
    # Content'i normalize et
    normalized_content = normalize_unicode_text(test_content)
    
    print(f"Original length: {len(test_content)}")
    print(f"Normalized length: {len(normalized_content)}")
    print(f"Valid UTF-8: {validate_utf8_text(normalized_content)}")
    
    # Hash test
    import hashlib
    hash1 = hashlib.sha1(test_content.encode('utf-8', errors='replace')).hexdigest()
    hash2 = hashlib.sha1(normalized_content.encode('utf-8')).hexdigest()
    
    print(f"Original hash: {hash1}")
    print(f"Normalized hash: {hash2}")
    print(f"Hashes match: {hash1 == hash2}")

def test_fastapi_utf8_middleware():
    """FastAPI UTF-8 middleware'ini test et"""
    print("=== FastAPI UTF-8 Middleware Test ===")
    
    # Test Turkish characters
    test_data = {
        'name': 'Ayça Dinçkök',
        'document': 'Galata Residance D4',
        'location': 'İstanbul Çiftçi Mahallesi',
        'nested': {
            'description': 'Büyükçekmece İlçesi',
            'details': 'ğüşıöç karakterleri'
        }
    }
    
    try:
        # Health endpoint test
        print("1. Health Endpoint Test:")
        response = requests.get('http://localhost:8000/health', timeout=3)
        print(f"   Status: {response.status_code}")
        
        content_type = response.headers.get('content-type', '')
        print(f"   Content-Type: {content_type}")
        print(f"   UTF-8 Charset: {'utf-8' in content_type.lower()}")
        print()
        
        if response.status_code == 200:
            print("✅ API çalışıyor")
            
            # Test JSON response encoding
            print("2. JSON Response Encoding Test:")
            json_str = json.dumps(test_data, ensure_ascii=False)
            print(f"   Test Data: {json_str}")
            print(f"   UTF-8 Length: {len(json_str.encode('utf-8'))} bytes")
            print(f"   Characters Length: {len(json_str)} characters")
            print()
            
            # Test actual API response headers
            print("3. API Response Headers Test:")
            headers = dict(response.headers)
            for key, value in headers.items():
                if 'content' in key.lower():
                    print(f"   {key}: {value}")
            print()
            
            # Test JSON decoding
            print("4. Response JSON Decoding Test:")
            try:
                response_data = response.json()
                print(f"   Response decoded successfully: {type(response_data)}")
                print(f"   Response keys: {list(response_data.keys()) if isinstance(response_data, dict) else 'Not a dict'}")
            except Exception as e:
                print(f"   JSON decode error: {e}")
            print()
            
            print("✅ UTF-8 Middleware test tamamlandı!")
        else:
            print(f"❌ API yanıt vermiyor: {response.status_code}")
            
    except requests.exceptions.ConnectionError:
        print("❌ API bağlantısı kurulamadı.")
        print("   Server başlatmak için: uvicorn score:app --reload")
    except Exception as e:
        print(f"❌ Test hatası: {e}")
    print()

if __name__ == "__main__":
    print("🔄 UTF-8 Test ve Debugging Başlatılıyor...\n")
    
    test_utf8_functions()
    print("-" * 60)
    
    test_database_connection()
    print("-" * 60)
    
    check_existing_documents()
    print("-" * 60)
    
    test_chunk_processing()
    print("-" * 60)
    
    test_fastapi_utf8_middleware()
    
    print("\n✅ Tüm testler tamamlandı!")
