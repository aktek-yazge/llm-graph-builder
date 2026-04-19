#!/usr/bin/env python3
"""
Parse agent response test
"""

import sys
from pathlib import Path

# Backend path'ini ekle
backend_path = Path(__file__).parent / "backend"
sys.path.insert(0, str(backend_path))

from src.intelligent_agent import IntelligentAgent

def test_parse_agent_response():
    """Parse agent response testini çalıştır"""
    print("🧪 Parse Agent Response Test")
    
    # Mock graph
    class MockGraph:
        pass
    
    agent = IntelligentAgent(MockGraph())
    
    # Problematic response (pipe character ile)
    problematic_response = """Observation: Tool calls tamamlandı: 1 tool çağrısı, 1 embedding oluşturuldu. Cypher'da $embedding_vector kullanabilirsin.
Thought: Şimdi, bu embedding ile ilgili belgede semantic vector search yaparak taksit bilgilerini içeren chunk'ları bulacağım.
Action: cypher_query
Content: |
  CALL db.index.vector.queryNodes('vector', 8, $embedding_vector) YIELD node, score
  MATCH (node)-[:PART_OF]->(d:Document)
  WHERE d.fileName = "Ayça Dinçkök Galata Residance D4 Konut 2020.pdf"
  RETURN node.text, node.chunkId, node.page_number, node.position, score
  ORDER BY score DESC"""
    
    print("Testing problematic response...")
    observation, thought, (action, action_content) = agent.parse_agent_response(problematic_response)
    
    print(f"✅ Observation: {observation}")
    print(f"✅ Thought: {thought}")
    print(f"✅ Action: {action}")
    print(f"✅ Content (clean): {action_content}")
    
    # Check if pipe character is removed
    if action_content.startswith('CALL'):
        print("✅ Pipe character başarıyla temizlendi!")
    else:
        print("❌ Pipe character hala var!")
    
    print("\n" + "="*60)
    print("CLEANED CONTENT:")
    print(action_content)

if __name__ == "__main__":
    print("🚀 Parse Agent Response Test")
    print("=" * 50)
    
    test_parse_agent_response()
    
    print("\n✅ Test completed!")
