#!/usr/bin/env python3
"""
Runtime Schema Discovery Test
Tamamen runtime'da schema bilgilerini çıkarıp kontrol eder
"""

import sys
import os
sys.path.append('/workspace/backend')

try:
    from src.domain_agnostic_schema import DomainAgnosticSchemaDiscovery
    from backend.db_connection import get_neo4j_connection
    
    print("🚀 Runtime Schema Discovery Test başlıyor...")
    
    # Neo4j bağlantısı
    graph = get_neo4j_connection()
    print("✅ Neo4j bağlantısı başarılı")
    
    # Schema discovery
    discoverer = DomainAgnosticSchemaDiscovery(graph)
    
    print("\n📊 1. Entity Types Keşfi:")
    entity_types = discoverer.discover_entity_subtypes()
    print(f"Entity Types ({len(entity_types)}): {entity_types[:10]}")
    
    print("\n🔗 2. Relation Types Keşfi:")
    relation_types = discoverer.discover_relation_types()
    print(f"Relations ({len(relation_types)}): {relation_types}")
    
    print("\n👤 3. Person Properties Keşfi:")
    person_properties = discoverer._get_person_properties()
    print(f"Person Props ({len(person_properties)}): {person_properties}")
    
    print("\n📄 4. Document Patterns Keşfi:")
    document_info = discoverer.discover_document_patterns()
    print(f"Documents: {document_info}")
    
    print("\n🎯 5. Full Schema Discovery:")
    full_schema = discoverer.discover_full_domain_schema()
    print(f"Full Schema Stats: {full_schema['statistics']}")
    
    print("\n📝 6. LLM Schema Prompt (İlk 500 karakter):")
    llm_prompt = discoverer.generate_llm_schema_prompt()
    print(llm_prompt[:500] + "...")
    
    print("\n✅ Runtime Schema Discovery Test başarılı!")
    
except ImportError as e:
    print(f"❌ Import hatası: {e}")
    print("backend/db_connection import edilmiyor, alternatif deneyelim...")
    
    try:
        # MCP tool kullanarak test
        print("🔄 MCP Neo4j tool ile test...")
        # Bu kısmı MCP ile test etmek için placeholder
        print("MCP test gerekli...")
        
    except Exception as e:
        print(f"❌ MCP test hatası: {e}")

except Exception as e:
    print(f"❌ Test hatası: {e}")
    import traceback
    traceback.print_exc()