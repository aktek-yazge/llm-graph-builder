#!/usr/bin/env python3
"""
Token Debugging Test - OpenAI response format'ını analiz et
"""

from src.QA_integration import get_llm
from langchain_neo4j import Neo4jGraph
import os
import json
import logging

# Logging'i ayarla
# logging.basicConfig(level=logging.INFO)  # main.py'de yapılıyor

def test_openai_token_response():
    print("🔍 OpenAI Token Response Format Test")
    print("=" * 50)
    
        # LLM'i al
    llm, model_name = get_llm('openai_gpt_4o')
    print(f"✅ LLM Type: {type(llm)}")
    print(f"✅ Model Name: {model_name}")
    
    # Basit bir test mesajı gönder
    test_message = "Merhaba, bu bir token test mesajıdır."
    print(f"📝 Test Message: {test_message}")
    
    try:
        # LLM'den response al
        response = llm.invoke(test_message)
        print(f"💬 Response Content: {response.content[:100]}...")
        
        # Response objesini incelemek için içeriğini yazdır
        print("\n🔬 RESPONSE OBJECT ANALİZİ:")
        print(f"Response Type: {type(response)}")
        print(f"Response Dir: {[attr for attr in dir(response) if not attr.startswith('_')]}")
        
        # Response metadata'yı kontrol et
        if hasattr(response, 'response_metadata'):
            print(f"\n📊 RESPONSE METADATA:")
            metadata = response.response_metadata
            print(f"Metadata Keys: {list(metadata.keys())}")
            print(f"Full Metadata: {json.dumps(metadata, indent=2, default=str)}")
            
            # Token usage'ı kontrol et
            if 'token_usage' in metadata:
                token_usage = metadata['token_usage']
                print(f"\n💰 TOKEN USAGE:")
                print(f"Prompt Tokens: {token_usage.get('prompt_tokens', 'N/A')}")
                print(f"Completion Tokens: {token_usage.get('completion_tokens', 'N/A')}")
                print(f"Total Tokens: {token_usage.get('total_tokens', 'N/A')}")
            else:
                print("❌ Token usage metadata bulunamadı!")
        else:
            print("❌ Response metadata bulunamadı!")
            
        # Usage metadata'yı da kontrol et
        if hasattr(response, 'usage_metadata'):
            print(f"\n📈 USAGE METADATA:")
            usage_metadata = response.usage_metadata
            print(f"Usage Metadata: {usage_metadata}")
            print(f"Usage Metadata Type: {type(usage_metadata)}")
            print(f"Usage Metadata Dir: {[attr for attr in dir(usage_metadata) if not attr.startswith('_')]}")
        else:
            print("❌ Usage metadata bulunamadı!")
            
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()

def test_simple_invoke():
    """LangChain invoke() metodunu test et"""
    print("\n🚀 Simple Invoke Test")
    print("=" * 30)
    
    llm, _ = get_llm('openai_gpt_4o')
    response = llm.invoke("2+2 kaç eder?")
    
    print(f"Response: {response.content}")
    print(f"Response Metadata: {response.response_metadata}")
    
    # Token bilgilerini çıkar
    if 'token_usage' in response.response_metadata:
        usage = response.response_metadata['token_usage']
        print(f"🎯 TOKENS FOUND!")
        print(f"  Prompt: {usage.get('prompt_tokens')}")
        print(f"  Completion: {usage.get('completion_tokens')}")
        print(f"  Total: {usage.get('total_tokens')}")
    else:
        print("❌ Token usage yok!")

if __name__ == "__main__":
    test_openai_token_response()
    test_simple_invoke()
