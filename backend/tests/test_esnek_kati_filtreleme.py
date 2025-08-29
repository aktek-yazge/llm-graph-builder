#!/usr/bin/env python3
"""
Test: Esnek node - Katı relationship filtreleme mantığı
Bu test, yeni filtreleme yaklaşımını doğrular:
- Node tipleri: Esnek (allowed listeden seçmeyi tercih et, ama yeni tipleri de kabul et)
- Relationship tipleri: Katı (sadece allowed liste)
"""

import os
import sys
import logging

# Add the src directory to Python path
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from langchain.schema import Document
from src.graph_transformer.transformer import LLMGraphTransformer
from langchain_community.graphs.graph_document import Node, Relationship

# Test için basit bir mock LLM
class MockLLM:
    def invoke(self, prompt_dict, config=None):
        # Test için önceden hazırlanmış JSON response
        test_response = """
        [
            {
                "head": "Ahmet Yılmaz",
                "head_type": "Customer",
                "relation": "HAS_POLICY", 
                "tail": "POL-12345",
                "tail_type": "Policy"
            },
            {
                "head": "POL-12345", 
                "head_type": "Policy",
                "relation": "DOCUMENTED_IN",
                "tail": "Document-567",
                "tail_type": "Document"
            },
            {
                "head": "Ahmet Yılmaz",
                "head_type": "Customer", 
                "relation": "OWNS_PROPERTY",
                "tail": "Ev-İstanbul",
                "tail_type": "RealEstate"
            },
            {
                "head": "POL-12345",
                "head_type": "Policy",
                "relation": "INVALID_RELATIONSHIP_TYPE",
                "tail": "Something",
                "tail_type": "SomeNode"
            }
        ]
        """
        
        class MockResponse:
            def __init__(self, content):
                self.content = content
        
        return MockResponse(test_response)
    
    def with_structured_output(self, schema, include_raw=False):
        """Mock with_structured_output method"""
        return self
    
    def bind_tools(self, tools):
        """Mock bind_tools method"""
        return self

def test_esnek_kati_filtreleme():
    """Test esnek node / katı relationship filtreleme mantığı"""
    
    print("🧪 Test: Esnek Node - Katı Relationship Filtreleme")
    print("=" * 60)
    
    # Mock LLM ile transformer oluştur
    mock_llm = MockLLM()
    
    # Allowed types tanımla
    allowed_nodes = ["Customer", "Policy", "Document", "PolicyType", "InsuredItem", "PolicyYear"]
    allowed_relationships = ["HAS_POLICY", "DOCUMENTED_IN", "HAS_TYPE", "HAS_INSURED_ITEM", "HAS_YEAR"]
    
    transformer = LLMGraphTransformer(
        llm=mock_llm,
        allowed_nodes=allowed_nodes,
        allowed_relationships=allowed_relationships,
        strict_mode=True
    )
    
    # Test document
    test_doc = Document(
        page_content="Test içeriği - Ahmet Yılmaz'ın POL-12345 poliçesi",
        metadata={"chunk_id": "test-chunk-1"}
    )
    
    print("🎯 BEKLENEN SONUÇLAR:")
    print("📋 Node'lar:")
    print("  - Ahmet Yılmaz (Customer) ✅ allowed listede")
    print("  - POL-12345 (Policy) ✅ allowed listede") 
    print("  - Document-567 (Document) ✅ allowed listede")
    print("  - Ev-İstanbul (RealEstate) ✅ allowed listede DEĞİL ama ESNEK yaklaşım ile kabul edilmeli")
    print("  - Something (SomeNode) ✅ allowed listede DEĞİL ama ESNEK yaklaşım ile kabul edilmeli")
    print("")
    print("🔗 Relationship'lar:")
    print("  - HAS_POLICY ✅ allowed listede - kabul edilmeli")
    print("  - DOCUMENTED_IN ✅ allowed listede - kabul edilmeli") 
    print("  - OWNS_PROPERTY ❌ allowed listede DEĞİL - KATI yaklaşım ile reddedilmeli")
    print("  - INVALID_RELATIONSHIP_TYPE ❌ allowed listede DEĞİL - KATI yaklaşım ile reddedilmeli")
    print("")
    
    # Process document
    try:
        result = transformer.process_response(test_doc)
        
        print("✅ İŞLEM SONUÇLARI:")
        print(f"📊 Toplam node sayısı: {len(result.nodes)}")
        print(f"🔗 Toplam relationship sayısı: {len(result.relationships)}")
        print("")
        
        print("📋 BULUNAN NODE'LAR:")
        for i, node in enumerate(result.nodes):
            is_preferred = node.type in allowed_nodes
            status = "✅ Tercih edilen" if is_preferred else "🔄 Yeni tip (esnek kabul)"
            print(f"  {i+1}. {node.id} ({node.type}) - {status}")
        print("")
        
        print("🔗 BULUNAN RELATIONSHIP'LAR:")
        for i, rel in enumerate(result.relationships):
            is_allowed = rel.type in allowed_relationships
            status = "✅ İzinli" if is_allowed else "❌ İzinsiz (filtrelenmeli)"
            print(f"  {i+1}. {rel.source.id} --[{rel.type}]--> {rel.target.id} - {status}")
        print("")
        
        # Doğrulama testleri
        print("🔍 DOĞRULAMA TESTLERİ:")
        
        # Test 1: Tüm node'lar kabul edilmeli (esnek yaklaşım)
        expected_nodes = 5  # Ahmet, POL-12345, Document-567, Ev-İstanbul, Something
        actual_nodes = len(result.nodes)
        test1_pass = actual_nodes == expected_nodes
        print(f"  Test 1 - Node sayısı (esnek): Beklenen {expected_nodes}, Bulunan {actual_nodes} {'✅' if test1_pass else '❌'}")
        
        # Test 2: Sadece allowed relationship'lar kabul edilmeli (katı yaklaşım)
        allowed_rel_types = set(allowed_relationships)
        actual_rel_types = set(rel.type for rel in result.relationships)
        invalid_rels = actual_rel_types - allowed_rel_types
        test2_pass = len(invalid_rels) == 0
        print(f"  Test 2 - Relationship katılığı: İzinsiz relationship sayısı {len(invalid_rels)} {'✅' if test2_pass else '❌'}")
        
        if not test2_pass:
            print(f"    İzinsiz relationship'lar: {invalid_rels}")
        
        # Test 3: İzinli relationship'lar korunmalı
        expected_valid_rels = {"HAS_POLICY", "DOCUMENTED_IN"}
        actual_valid_rels = actual_rel_types & expected_valid_rels
        test3_pass = len(actual_valid_rels) == len(expected_valid_rels)
        print(f"  Test 3 - İzinli relationship'lar: Beklenen {len(expected_valid_rels)}, Bulunan {len(actual_valid_rels)} {'✅' if test3_pass else '❌'}")
        
        # Test 4: Node tip çeşitliliği (hem preferred hem yeni tipler)
        preferred_node_types = set(node.type for node in result.nodes if node.type in allowed_nodes)
        new_node_types = set(node.type for node in result.nodes if node.type not in allowed_nodes)
        test4_pass = len(preferred_node_types) > 0 and len(new_node_types) > 0
        print(f"  Test 4 - Node çeşitliliği: Tercih edilen {len(preferred_node_types)}, Yeni {len(new_node_types)} {'✅' if test4_pass else '❌'}")
        
        # Özet
        all_tests_pass = test1_pass and test2_pass and test3_pass and test4_pass
        print("")
        print("🎯 TEST ÖZET:")
        print(f"  {'✅ TÜM TESTLER BAŞARILI' if all_tests_pass else '❌ BAZI TESTLER BAŞARISIZ'}")
        print("")
        
        return all_tests_pass
        
    except Exception as e:
        print(f"❌ Test sırasında hata: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_esnek_kati_filtreleme()
    sys.exit(0 if success else 1)
