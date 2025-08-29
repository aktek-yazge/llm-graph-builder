"""
Entity Post-Processor Test
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.entity_post_processor import EntityPostProcessor, apply_entity_post_processing
from langchain_experimental.graph_transformers.llm import GraphDocument, Node, Relationship
from langchain.docstore.document import Document

def test_basic_filtering():
    """Temel filtreleme testı"""
    print("=== Test: Temel Filtreleme ===")
    
    # Ham GraphDocument oluştur
    nodes = [
        Node(id="Ayça Dinçkök", type="Person"),
        Node(id="AKSA Sigorta", type="Company"), 
        Node(id="Document", type="Document"),  # Yasaklı
        Node(id="Existing Insurance Policy", type="Policy"),  # Yasaklı
        Node(id="2024-06-01", type="Date"),
        Node(id="2024", type="Year")
    ]
    
    relationships = [
        Relationship(
            source=Node(id="Document", type="Document"),
            target=Node(id="Ayça Dinçkök", type="Person"),
            type="CONTAINS"
        ),
        Relationship(
            source=Node(id="2024-06-01", type="Date"),
            target=Node(id="Document", type="Document"),  # Yanlış bağlantı
            type="START_DATE"
        )
    ]
    
    source_doc = Document(page_content="Test content")
    graph_doc = GraphDocument(
        nodes=nodes,
        relationships=relationships,
        source=source_doc
    )
    
    # Post-processing uygula
    processor = EntityPostProcessor(None)
    result = processor.process_entities([graph_doc], "test_policy.pdf")
    
    # Sonuçları kontrol et
    if result:
        cleaned_doc = result[0]
        print(f"Ham node sayısı: {len(nodes)}")
        print(f"Temizlenmiş node sayısı: {len(cleaned_doc.nodes)}")
        
        # Yasaklı entity'lerin filtrelendiğini kontrol et
        node_types = [node.type for node in cleaned_doc.nodes]
        if "Document" not in node_types and "Policy" not in node_types:
            print("✓ Yasaklı entity'ler başarıyla filtrelendi")
        else:
            print("✗ Yasaklı entity'ler filtrelemedi")
            
        # Kalan entity'leri göster
        print("Kalan entity'ler:")
        for node in cleaned_doc.nodes:
            print(f"  - {node.type}: {node.id}")
    else:
        print("✗ Post-processing sonuç döndürmedi")

def test_date_normalization():
    """Tarih normalizasyonu testi"""
    print("\n=== Test: Tarih Normalizasyonu ===")
    
    processor = EntityPostProcessor(None)
    
    test_dates = [
        ("01.06.2024", "2024-06-01"),
        ("15.12.2023", "2023-12-15"),
        ("2024-06-01", "2024-06-01"),  # Zaten normalize
        ("invalid_date", "invalid_date")  # Değişmemeli
    ]
    
    for input_date, expected in test_dates:
        result = processor._normalize_date(input_date)
        if result == expected:
            print(f"✓ {input_date} -> {result}")
        else:
            print(f"✗ {input_date} -> {result} (beklenen: {expected})")

def test_year_extraction():
    """Year entity üretme testi"""
    print("\n=== Test: Year Entity Üretme ===")
    
    nodes = [
        Node(id="2024-06-01", type="Date"),
        Node(id="2023-12-15", type="Date"),
        Node(id="Ayça Dinçkök", type="Person")
    ]
    
    relationships = []
    
    source_doc = Document(page_content="Policy content")
    graph_doc = GraphDocument(
        nodes=nodes,
        relationships=relationships,
        source=source_doc
    )
    
    processor = EntityPostProcessor(None)
    result = processor.process_entities([graph_doc], "test_policy.pdf")
    
    if result:
        cleaned_doc = result[0]
        year_nodes = [node for node in cleaned_doc.nodes if node.type == "Year"]
        
        print(f"Date node sayısı: {len([n for n in nodes if n.type == 'Date'])}")
        print(f"Üretilen Year node sayısı: {len(year_nodes)}")
        
        expected_years = {"2024", "2023"}
        actual_years = {node.id for node in year_nodes}
        
        if expected_years.issubset(actual_years):
            print("✓ Year entity'ler başarıyla üretildi")
            for year_node in year_nodes:
                print(f"  - Year: {year_node.id}")
        else:
            print(f"✗ Beklenen yıllar: {expected_years}, Bulunan: {actual_years}")

def test_validation():
    """Doğrulama testi"""
    print("\n=== Test: Doğrulama ===")
    
    # Hatalı GraphDocument oluştur
    nodes = [
        Node(id="Document Node", type="Document"),  # Yasaklı
        Node(id="2024-06-01", type="Date"),
        Node(id="chunk_123", type="Chunk")
    ]
    
    relationships = [
        Relationship(
            source=Node(id="chunk_123", type="Chunk"),
            target=Node(id="2024-06-01", type="Date"),
            type="HAS_DATE"
        )
    ]
    
    source_doc = Document(page_content="Test")
    graph_doc = GraphDocument(
        nodes=nodes,
        relationships=relationships,
        source=source_doc
    )
    
    processor = EntityPostProcessor(None)
    validation_errors = processor.validate_against_instructions([graph_doc])
    
    print(f"Doğrulama hataları ({len(validation_errors)}):")
    for error in validation_errors:
        print(f"  - {error}")
        
    if len(validation_errors) > 0:
        print("✓ Doğrulama hataları başarıyla tespit edildi")
    else:
        print("✗ Doğrulama hataları tespit edilemedi")

if __name__ == "__main__":
    print("Entity Post-Processor Test Başlıyor...\n")
    
    try:
        test_basic_filtering()
        test_date_normalization()
        test_year_extraction() 
        test_validation()
        
        print("\n=== Test Tamamlandı ===")
        
    except Exception as e:
        print(f"Test hatası: {e}")
        import traceback
        traceback.print_exc()
