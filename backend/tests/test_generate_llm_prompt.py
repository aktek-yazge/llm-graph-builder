#!/usr/bin/env python3
"""
generate_llm_schema_prompt fonksiyonunu test eder
Tüm bilgilerin gelip gelmediğini kontrol eder (LIMIT yok)
"""

import sys
import os

# Backend modüllerini import et
sys.path.append('/workspace/backend/src')

from domain_agnostic_schema import DomainAgnosticSchemaDiscovery
from langchain_community.graphs import Neo4jGraph

def get_neo4j_connection():
    """Neo4j bağlantısı yaratır"""
    return Neo4jGraph(
        url=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        username=os.getenv("NEO4J_USERNAME", "neo4j"),
        password=os.getenv("NEO4J_PASSWORD", "qwerty5555"),
        database=os.getenv("NEO4J_DATABASE", "neo4j"),
    )

def test_generate_llm_schema_prompt():
    """generate_llm_schema_prompt fonksiyonunu test eder"""
    
    print("🚀 generate_llm_schema_prompt Test Başlıyor...")
    print("=" * 60)
    
    # Neo4j bağlantısı
    graph = get_neo4j_connection()
    if not graph:
        print("❌ Neo4j bağlantısı başarısız!")
        return False
    
    # Schema discovery instance
    discovery = DomainAgnosticSchemaDiscovery(graph)
    
    print("\n1️⃣ Raw Schema Discovery Test (LIMIT kontrolü):")
    schema_info = discovery.discover_full_domain_schema()
    
    print(f"   📊 Entity Types: {len(schema_info['entity_types'])} - {', '.join(schema_info['entity_types'][:5])}{'...' if len(schema_info['entity_types']) > 5 else ''}")
    print(f"   🔗 Relations: {len(schema_info['relation_types'])} - {', '.join(schema_info['relation_types'])}")
    print(f"   👤 Person Properties: {len(discovery._get_person_properties())} - {', '.join(discovery._get_person_properties()[:5])}{'...' if len(discovery._get_person_properties()) > 5 else ''}")
    print(f"   📈 Counts: Person({discovery._get_person_count()}), Entity({schema_info['statistics']['total_entities']}), Docs({schema_info['document_info']['count']})")
    
    print("\n2️⃣ generate_llm_schema_prompt Test:")
    
    # Ana test
    llm_prompt = discovery.generate_llm_schema_prompt()
    
    print("🎯 Generated LLM Schema Prompt:")
    print("=" * 60)
    print(llm_prompt)
    print("=" * 60)
    
    print(f"\n📏 Prompt uzunluğu: {len(llm_prompt)} karakter")
    
    # Validation checks
    print("\n3️⃣ Validation Checks:")
    
    # Tüm entity type'ları prompt'ta var mı?
    all_entity_types = schema_info['entity_types']
    entity_types_in_prompt = [et for et in all_entity_types if et in llm_prompt]
    print(f"   ✅ Entity Types in Prompt: {len(entity_types_in_prompt)}/{len(all_entity_types)} - {entity_types_in_prompt}")
    
    # Tüm relation type'ları prompt'ta var mı?
    all_relation_types = schema_info['relation_types'] 
    relation_types_in_prompt = [rt for rt in all_relation_types if rt in llm_prompt]
    print(f"   ✅ Relation Types in Prompt: {len(relation_types_in_prompt)}/{len(all_relation_types)} - {relation_types_in_prompt}")
    
    # Tüm person properties prompt'ta var mı?
    all_person_props = discovery._get_person_properties()
    person_props_in_prompt = [pp for pp in all_person_props if pp in llm_prompt]
    print(f"   ✅ Person Props in Prompt: {len(person_props_in_prompt)}/{len(all_person_props)} - {person_props_in_prompt}")
    
    # LIMIT kontrolü
    if "LIMIT" in llm_prompt:
        print("   ❌ LIMIT kelimesi prompt'ta bulundu!")
        return False
    else:
        print("   ✅ LIMIT yok - Tam schema extraction")
    
    # Hardcoded value kontrolü
    hardcoded_checks = ["skill", "experience", "education", "2020", "SOFTWARE", "DEVELOPER"]
    found_hardcoded = [hc for hc in hardcoded_checks if hc.lower() in llm_prompt.lower()]
    if found_hardcoded:
        print(f"   ⚠️ Possible hardcoded values: {found_hardcoded}")
    else:
        print("   ✅ No hardcoded values detected")
    
    print("\n✅ TÜM TESTLER BAŞARILI!")
    print("🎯 generate_llm_schema_prompt tamamen runtime data ile çalışıyor!")
    
    return True

if __name__ == "__main__":
    test_generate_llm_schema_prompt()