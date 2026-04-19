#!/usr/bin/env python3
"""
Real tool call format test
"""

import sys
from pathlib import Path

# Backend path'ini ekle
backend_path = Path(__file__).parent / "backend"
sys.path.insert(0, str(backend_path))

from src.intelligent_agent import IntelligentAgent

def test_real_tool_call():
    """Gerçek tool call formatını test et"""
    print("🧪 Real Tool Call Test")
    
    # Gerçek LangChain format (hata mesajından alınan)
    real_tool_call = {
        'name': 'generate_embeddings_for_cypher', 
        'args': {'text': 'taksit tutarı'}, 
        'id': 'call_LG62gn0HZCxbpqSkXPSp0pBn', 
        'type': 'tool_call'
    }
    
    # Mock graph
    class MockGraph:
        pass
    
    # Mock embedding model
    class MockEmbeddingModel:
        def embed_query(self, text):
            return [0.1, 0.2, 0.3, 0.4, 0.5]  # Mock 5-dimensional vector
    
    graph = MockGraph()
    agent = IntelligentAgent(graph)
    agent.embedding_model = MockEmbeddingModel()
    
    print("Testing real LangChain tool call...")
    try:
        result = agent.handle_tool_calls([real_tool_call])
        print(f"✅ Tool call successful: {len(result)} results")
        if result:
            print(f"✅ Result content: {result[0]['content'][:100]}...")
    except Exception as e:
        print(f"❌ Tool call failed: {e}")

if __name__ == "__main__":
    print("🚀 Real Tool Call Test")
    print("=" * 30)
    
    test_real_tool_call()
    
    print("\n✅ Test completed!")
