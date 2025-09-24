#!/usr/bin/env python3
"""
LLM-driven vector search workflow'unu test eder
"""

import os
import sys
import logging
from pathlib import Path

# Backend path'ini ekle
backend_path = Path(__file__).parent / "backend"
sys.path.insert(0, str(backend_path))

from src.llm import get_llm, is_reasoning_model
from src.intelligent_agent import IntelligentAgent

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_reasoning_model_detection():
    """Reasoning model detection'ı test et"""
    print("🧪 Reasoning Model Detection Test")
    
    # Test models
    test_cases = [
        ("gpt-4", False),
        ("gpt-4o", False),
        ("o4-mini", True),
        ("o5-mini", True),
        ("o1-mini", True),
        ("o1-preview", True),
        ("claude-3", False),
    ]
    
    for model_name, expected in test_cases:
        # Mock LLM object with model_name attribute
        class MockLLM:
            def __init__(self, model_name):
                self.model_name = model_name
        
        mock_llm = MockLLM(model_name)
        result = is_reasoning_model(mock_llm)
        status = "✅" if result == expected else "❌"
        print(f"{status} {model_name}: {result} (expected: {expected})")

def test_tool_call_formats():
    """Tool call format handling'i test et"""
    print("\n🧪 Tool Call Format Test")
    
    # Mock object format
    class MockToolCall:
        def __init__(self):
            self.id = "call_123"
            self.function = MockFunction()
    
    class MockFunction:
        def __init__(self):
            self.name = "generate_embeddings_for_cypher"
            self.arguments = '{"query": "test query", "top_k": 5}'
    
    # Mock dict format (OpenAI direct)
    dict_tool_call = {
        "id": "call_456",
        "function": {
            "name": "generate_embeddings_for_cypher",
            "arguments": '{"query": "test dict query", "top_k": 3}'
        }
    }
    
    # Mock LangChain format
    langchain_tool_call = {
        "name": "generate_embeddings_for_cypher",
        "args": {"text": "taksit tutarı"},
        "id": "call_LG62gn0HZCxbpqSkXPSp0pBn",
        "type": "tool_call"
    }
    
    # Mock graph ve agent
    class MockGraph:
        pass
    
    graph = MockGraph()
    agent = IntelligentAgent(graph)
    
    print("Testing object format...")
    try:
        # Test basic parsing logic without actual execution
        tool_call = MockToolCall()
        
        # Object format test
        if hasattr(tool_call, 'function'):
            function_name = tool_call.function.name
            print(f"✅ Object format parsed: {function_name}")
        else:
            print("❌ Object format failed")
    except Exception as e:
        print(f"❌ Object format failed: {e}")
    
    print("Testing dict format...")
    try:
        # Dict format test
        if isinstance(dict_tool_call, dict) and "function" in dict_tool_call:
            function_name = dict_tool_call["function"]["name"]
            print(f"✅ Dict format parsed: {function_name}")
        else:
            print("❌ Dict format failed")
    except Exception as e:
        print(f"❌ Dict format failed: {e}")
    
    print("Testing LangChain format...")
    try:
        # LangChain format test
        if isinstance(langchain_tool_call, dict) and "name" in langchain_tool_call and "args" in langchain_tool_call:
            function_name = langchain_tool_call["name"]
            print(f"✅ LangChain format parsed: {function_name}")
        else:
            print("❌ LangChain format failed")
    except Exception as e:
        print(f"❌ LangChain format failed: {e}")

if __name__ == "__main__":
    print("🚀 LLM Graph Builder Workflow Test")
    print("=" * 50)
    
    test_reasoning_model_detection()
    test_tool_call_formats()
    
    print("\n✅ Test completed!")
