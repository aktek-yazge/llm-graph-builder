#!/usr/bin/env python3
"""
Schema prompt token efficiency testi
"""

import sys
sys.path.append('/workspace/backend/src')

try:
    from domain_agnostic_schema import DomainAgnosticSchemaDiscovery
except ImportError:
    # Fallback için dosyayı direkt import et
    sys.path.insert(0, '/workspace/backend/src')
    from domain_agnostic_schema import DomainAgnosticSchemaDiscovery

# Mock graph class for testing
class MockGraph:
    def query(self, cypher):
        # Mock data for testing
        if "MATCH (e:Entity)" in cypher and "DISTINCT e.type" in cypher:
            return [
                {"entity_type": "Skill"},
                {"entity_type": "Education"}, 
                {"entity_type": "Experience"},
                {"entity_type": "Certification"},
                {"entity_type": "Language"},
                {"entity_type": "Tool"},
                {"entity_type": "Project"}
            ]
        elif "MATCH (p:Person)-[r]-(e:Entity)" in cypher:
            return [
                {"relation_type": "HAS_SKILL"},
                {"relation_type": "HAS_EDUCATION"},
                {"relation_type": "HAS_EXPERIENCE"},
                {"relation_type": "HAS_CERTIFICATION"},
                {"relation_type": "SPEAKS"},
                {"relation_type": "USES_TOOL"}
            ]
        elif "MATCH (p:Person)" in cypher and "keys(p)" in cypher:
            return [
                {"properties": ["name", "email", "phone", "location", "title"]}
            ]
        elif "count(" in cypher:
            if "Person" in cypher:
                return [{"count": 150}]
            elif "Entity" in cypher:
                return [{"count": 1250}]
            elif "Document" in cypher:
                return [{"total_count": 45}]
        elif "MATCH (d:Document)" in cypher:
            return [{"document_count": 45, "all_properties": [["name", "path", "size", "type"]]}]
        return []

def test_token_efficiency():
    """Test token efficiency of schema prompt"""
    
    # Create mock discoverer
    mock_graph = MockGraph()
    discoverer = DomainAgnosticSchemaDiscovery(mock_graph)
    
    # Generate prompt
    prompt = discoverer.generate_llm_schema_prompt()
    
    print("🔍 GENERATED PROMPT:")
    print("=" * 60)
    print(prompt)
    print("=" * 60)
    
    # Token analysis (rough estimation)
    words = prompt.split()
    chars = len(prompt)
    lines = prompt.count('\n') + 1
    
    # Rough token estimation (1 token ≈ 4 chars in Turkish/English)
    estimated_tokens = chars // 4
    
    print(f"📊 TOKEN ANALYSIS:")
    print(f"   Words: {len(words)}")
    print(f"   Characters: {chars}")
    print(f"   Lines: {lines}")
    print(f"   Estimated Tokens: {estimated_tokens}")
    print(f"   Efficiency: {chars/len(words):.1f} chars/word")
    
    # Test schema discovery separately
    print(f"\n🔍 SCHEMA DISCOVERY TEST:")
    schema_info = discoverer.discover_full_domain_schema()
    
    print(f"   Entity types: {len(schema_info['entity_types'])}")
    print(f"   Relation types: {len(schema_info['relation_types'])}")
    print(f"   Statistics: {schema_info['statistics']}")
    
    return prompt, estimated_tokens

if __name__ == "__main__":
    test_token_efficiency()