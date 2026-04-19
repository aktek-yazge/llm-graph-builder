#!/usr/bin/env python3
"""
Reasoning response parsing test
"""

import sys
from pathlib import Path

# Backend path'ini ekle
backend_path = Path(__file__).parent / "backend"
sys.path.insert(0, str(backend_path))

from src.llm import get_reasoning_response_text, get_full_reasoning_response

def test_reasoning_response_parsing():
    """Reasoning response parsing'i test et"""
    print("🧪 Reasoning Response Parsing Test")
    
    # Mock reasoning response
    mock_response = {
        "choices": [{
            "message": {
                "content": "Final answer here",
                "reasoning": "This is the reasoning block where the model explains its thought process step by step..."
            }
        }]
    }
    
    # Mock response without reasoning
    mock_normal_response = {
        "choices": [{
            "message": {
                "content": "Normal response content"
            }
        }]
    }
    
    print("Testing reasoning response...")
    try:
        reasoning_text = get_reasoning_response_text(mock_response)
        full_response = get_full_reasoning_response(mock_response)
        
        print(f"✅ Reasoning extracted: {str(reasoning_text)[:50]}...")
        print(f"✅ Full response: {str(full_response)[:50]}...")
        
    except Exception as e:
        print(f"❌ Reasoning parsing failed: {e}")
    
    print("\nTesting normal response...")
    try:
        reasoning_text = get_reasoning_response_text(mock_normal_response)
        full_response = get_full_reasoning_response(mock_normal_response)
        
        print(f"✅ Normal response handled: reasoning={reasoning_text}, content={str(full_response)[:50]}...")
        
    except Exception as e:
        print(f"❌ Normal response parsing failed: {e}")

if __name__ == "__main__":
    print("🚀 Reasoning Response Test")
    print("=" * 40)
    
    test_reasoning_response_parsing()
    
    print("\n✅ Test completed!")
