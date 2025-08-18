"""
Docling ile dosya analizi test scripti
"""
import asyncio
import json
import base64
import os
import sys
from dotenv import load_dotenv

# .env dosyasını yükle
load_dotenv()

sys.path.append('/home/ubuntu/llm-graph-builder/backend')

from src.QA_integration import analyze_files_with_docling
from src.llm import get_llm
from langchain_community.chat_message_histories import ChatMessageHistory

async def test_docling_analysis():
    """Test fonksiyonu"""
    
    # Test LLM modelini ayarla
    model = "openai_gpt_4o_mini"
    
    # Test et ki LLM modeli çalışıyor
    try:
        llm, model_name = get_llm(model)
        print(f"LLM model başarıyla yüklendi: {model_name}")
    except Exception as e:
        print(f"LLM model yüklenemedi: {e}")
        return
    
    # Test URL'si (gerçek bir PDF URL'si olmalı)
    test_url = "https://arxiv.org/pdf/2406.07021"  # Buraya gerçek URL koyun
    
    # Test files formatını hazırla
    files_data = {
        "documents": [
            {
                "fileName": "sample.pdf",
                "url": test_url  # URL formatında
            }
        ]
    }
    
    # Mock history ve messages
    history = ChatMessageHistory()
    messages = []
    
    # Test question
    question = "Bu belge hakkında özet ver"
    model = "openai_gpt_4o_mini"  # .env dosyasında tanımlı model
    
    print("Docling URL analizi başlatılıyor...")
    
    # Analizi çalıştır
    async for chunk in analyze_files_with_docling(
        files_data, model, question, history, messages
    ):
        if chunk["type"] == "message_chunk":
            print(chunk["content"], end="", flush=True)
        elif chunk["type"] == "error":
            print(f"\nHata: {chunk['message']}")
            break
    
    print("\n\nTest tamamlandı!")

if __name__ == "__main__":
    # Environment değişkenini ayarla
    os.environ['USE_DOCLING'] = 'true'
    
    # Test'i çalıştır
    asyncio.run(test_docling_analysis())
