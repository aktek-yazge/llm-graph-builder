#!/usr/bin/env python3
"""
GERÇEK Neo4j veritabanı ile schema prompt testi
"""

import sys
sys.path.append('/workspace/backend/src')

from domain_agnostic_schema import DomainAgnosticSchemaDiscovery

# Gerçek Neo4j graph bağlantısı için mock 
class RealDatabaseMockGraph:
    def query(self, cypher):
        """MCP Neo4j aura tool'dan aldığımız GERÇEK veriler"""
        
        # Entity types - gerçek veriler
        if "MATCH (e:Entity)" in cypher and "DISTINCT e.type" in cypher:
            return [
                {"entity_type": "Organization"},
                {"entity_type": "University"}, 
                {"entity_type": "Skill"},
                {"entity_type": "Üniversite"},
                {"entity_type": "Position"},
                {"entity_type": "Degree"},
                {"entity_type": "Language"},
                {"entity_type": "Dernek"},
                {"entity_type": "Vakfı"},
                {"entity_type": "High School"}
            ]
        
        # Relation types - gerçek veriler  
        elif "MATCH (p:Person)-[r]-(e:Entity)" in cypher and "DISTINCT type(r)" in cypher:
            return [
                {"relation_type": "HAS_ATTRIBUTE"},
                {"relation_type": "CONNECTED_TO"}
            ]
        
        # Person properties - gerçek veriler (özet)
        elif "MATCH (p:Person)" in cypher and "keys(p)" in cypher:
            return [
                {"properties": ["name", "contact_email", "contact_phone", "profile_location", 
                               "career_current_company", "career_current_position", "profile_summary",
                               "career_experience_years", "social_linkedin", "social_github", 
                               "createdAt", "extraction_method", "source_file", "entity_type"]}
            ]
        
        # TÜM relation types - gerçek veriler
        elif "MATCH ()-[r]-()" in cypher and "DISTINCT type(r)" in cypher:
            return [
                {"relation_type": "HAS_ATTRIBUTE"},
                {"relation_type": "PART_OF"},
                {"relation_type": "NEXT_CHUNK"},
                {"relation_type": "CONNECTED_TO"},
                {"relation_type": "HAS_CV"},
                {"relation_type": "FIRST_CHUNK"},
                {"relation_type": "NEXT"},
                {"relation_type": "LAST_MESSAGE"}
            ]
        
        # Document properties - gerçek veriler
        elif "MATCH (d:Document)" in cypher and "keys(d)" in cypher:
            return [
                {"properties": ["fileName", "fileSize", "fileType", "status", "createdAt", 
                               "updatedAt", "docType", "model", "processingTime", "nodeCount",
                               "relationshipCount", "cvExtracted", "extractionApproach"]}
            ]
        
        # Count queries - tahmin edilen değerler
        elif "count(" in cypher:
            if "Person" in cypher:
                return [{"count": 50}]  # Yaklaşık person sayısı
            elif "Entity" in cypher:
                return [{"count": 443}]  # Toplam entity sayısı (188+103+75+...)
            elif "Document" in cypher:
                return [{"total_count": 45}]
        
        # Document patterns
        elif "MATCH (d:Document)" in cypher and "count(d)" in cypher:
            return [{"document_count": 45, "all_properties": [["fileName", "fileSize", "fileType", "status", "createdAt", "updatedAt"]]}]
        
        # Entity-Relation mapping - gerçek veriler
        elif "MATCH (p:Person)-[r]->(e:Entity)" in cypher and "e.type as entity_type" in cypher:
            return [
                {"entity_type": "Language", "relation_type": "HAS_ATTRIBUTE", "count": 148},
                {"entity_type": "Skill", "relation_type": "HAS_ATTRIBUTE", "count": 177},
                {"entity_type": "Degree", "relation_type": "HAS_ATTRIBUTE", "count": 3},
                {"entity_type": "Organization", "relation_type": "CONNECTED_TO", "count": 227},
                {"entity_type": "Üniversite", "relation_type": "CONNECTED_TO", "count": 3},
                {"entity_type": "Position", "relation_type": "CONNECTED_TO", "count": 1}
            ]
        
        return []

def test_real_schema_prompt():
    """Gerçek veritabanı şeması ile test"""
    
    print("🔍 GERÇEK VERİTABANI İLE SCHEMA PROMPT TESTİ")
    print("=" * 60)
    
    # Gerçek verilerle test
    real_graph = RealDatabaseMockGraph()
    discoverer = DomainAgnosticSchemaDiscovery(real_graph)
    
    # Prompt oluştur
    prompt = discoverer.generate_llm_schema_prompt()
    
    print("🎯 GENERATED PROMPT (REAL DATA):")
    print("-" * 60)
    print(prompt)
    print("-" * 60)
    
    # Token analysis
    words = prompt.split()
    chars = len(prompt)
    lines = prompt.count('\n') + 1
    estimated_tokens = chars // 4
    
    print(f"📊 TOKEN ANALYSIS:")
    print(f"   Words: {len(words)}")
    print(f"   Characters: {chars}")
    print(f"   Lines: {lines}")
    print(f"   Estimated Tokens: {estimated_tokens}")
    
    # Şema detayları
    schema_info = discoverer.discover_full_domain_schema()
    print(f"\n🔍 REAL SCHEMA DISCOVERY:")
    print(f"   Entity types: {schema_info['entity_types']}")
    print(f"   Relation types: {schema_info['relation_types']}")
    print(f"   Statistics: {schema_info['statistics']}")
    
    return prompt, estimated_tokens

if __name__ == "__main__":
    test_real_schema_prompt()