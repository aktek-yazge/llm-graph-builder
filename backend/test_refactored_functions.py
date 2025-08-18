#!/usr/bin/env python3
"""
Test script for refactored analyze_files_with_docling functions
"""

import sys
import os
sys.path.append('/home/ubuntu/llm-graph-builder/backend')

from dotenv import load_dotenv
load_dotenv()

from src.QA_integration import convert_files_to_markdown, analyze_markdown_with_llm

def test_convert_files_to_markdown():
    """Test the convert_files_to_markdown function"""
    
    # Test dosya yapısı
    test_files = {
        "documents": [
            {
                "fileName": "test_document.pdf",
                "url": "https://arxiv.org/pdf/2406.07021"  # Bu URL çalışmayacak ama fonksiyon test edilecek
            }
        ]
    }
    
    print("=== CONVERT FILES TO MARKDOWN TEST ===")
    print(f"Test files: {test_files}")
    
    try:
        result = convert_files_to_markdown(test_files)
        print(f"Function executed successfully!")
        print(f"Result type: {type(result)}")
        print(f"Result preview (first 200 chars):")
        print(result[:200] + "..." if len(result) > 200 else result)
        return True
    except Exception as e:
        print(f"Error in convert_files_to_markdown: {e}")
        return False

async def test_analyze_markdown_with_llm():
    """Test the analyze_markdown_with_llm function"""
    
    test_markdown = """
## Belge: Test Document
**URL:** https://example.com/test.pdf

**[Sayfa 1]**

Bu bir test belgesi içeriğidir. Lorem ipsum dolor sit amet, consectetur adipiscing elit. 
Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.

**[Sayfa 2]**

Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat.
Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla pariatur.
"""
    
    print("\n=== ANALYZE MARKDOWN WITH LLM TEST ===")
    print(f"Test markdown length: {len(test_markdown)} characters")
    
    try:
        from src.llm import get_llm
        from langchain_core.messages import HumanMessage
        from langchain_neo4j import Neo4jChatMessageHistory
        
        # Mock history ve messages
        class MockHistory:
            def __init__(self):
                self.messages = []
        
        history = MockHistory()
        messages = []
        
        model = os.getenv('DEFAULT_OPENAI_CHAT_MODEL', 'openai_gpt_4.1')
        question = "Bu belge hakkında kısa bir özet ver."
        
        print(f"Testing with model: {model}")
        print(f"Question: {question}")
        
        chunk_count = 0
        async for chunk in analyze_markdown_with_llm(test_markdown, model, question, history, messages):
            chunk_count += 1
            if chunk_count <= 3:  # İlk 3 chunk'ı göster
                print(f"Chunk {chunk_count}: {chunk}")
            elif chunk_count == 4:
                print("... (more chunks) ...")
        
        print(f"Total chunks received: {chunk_count}")
        print(f"Messages in history: {len(messages)}")
        return True
        
    except Exception as e:
        print(f"Error in analyze_markdown_with_llm: {e}")
        import traceback
        traceback.print_exc()
        return False

async def main():
    """Main test function"""
    print("Testing refactored functions...")
    
    # Test 1: convert_files_to_markdown
    test1_result = test_convert_files_to_markdown()
    
    # Test 2: analyze_markdown_with_llm
    test2_result = await test_analyze_markdown_with_llm()
    
    print("\n=== TEST RESULTS ===")
    print(f"convert_files_to_markdown: {'PASS' if test1_result else 'FAIL'}")
    print(f"analyze_markdown_with_llm: {'PASS' if test2_result else 'FAIL'}")
    
    if test1_result and test2_result:
        print("All tests passed! Refactoring successful.")
    else:
        print("Some tests failed. Please check the implementation.")

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
