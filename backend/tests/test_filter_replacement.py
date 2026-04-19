#!/usr/bin/env python3
"""
Test filter replacement behavior: "peki almanca?" should replace English requirement, not add
"""

import sys
sys.path.append('/workspace/backend/src')

from intelligent_agent import generate_agent_response, setup_logging

def test_filter_replacement():
    """Test that filter queries replace requirements rather than add them"""
    
    # Simulate conversation history with initial job ad
    conversation_history = [
        {
            "role": "user", 
            "content": "Backend Developer arıyoruz. Python, Django bilgisi şart. İngilizce dil bilgisi gerekli. 3 yıl deneyim."
        },
        {
            "role": "assistant",
            "content": "İş ilanınızı analiz ediyorum ve uygun CV'leri buluyorum...\n\nAction: match_cvs\nContent: Backend Developer pozisyonu için Python, Django bilgisi olan, İngilizce dil bilgisi bulunan, minimum 3 yıl deneyimli adaylar"
        }
    ]
    
    print("=== İLK SORGU (İş İlanı) ===")
    print("User:", conversation_history[0]["content"])
    print("Assistant:", conversation_history[1]["content"])
    print()
    
    # Now test filter: "peki almanca?"
    print("=== FİLTRE SORGUSU ===")
    filter_query = "peki almanca?"
    print(f"User: {filter_query}")
    
    # Generate response for filter
    response = generate_agent_response(
        query=filter_query,
        session_id="test_filter",
        conversation_history=conversation_history
    )
    
    print(f"Assistant: {response}")
    print()
    
    # Check if response contains replacement logic
    print("=== ANALİZ ===")
    response_lower = response.lower()
    
    if "almanca" in response_lower and "ingilizce" not in response_lower:
        print("✅ BAŞARILI: Sadece Almanca, İngilizce yok (replacement)")
    elif "almanca" in response_lower and "ingilizce" in response_lower:
        print("❌ HATA: Hem Almanca hem İngilizce var (addition)")
    else:
        print("🤔 BELİRSİZ: Response'da dil kriteri net değil")
        
    # Check if match_cvs action is used
    if "match_cvs" in response:
        print("✅ DOĞRU ACTION: match_cvs kullanılmış")
    else:
        print("❌ YANLIŞ ACTION: match_cvs kullanılmamış")

if __name__ == "__main__":
    setup_logging()
    test_filter_replacement()