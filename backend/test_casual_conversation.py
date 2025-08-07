#!/usr/bin/env python3
"""
Günlük konuşma tespiti fonksiyonunu test etmek için basit bir script.
"""
import os
import sys
import logging
from dotenv import load_dotenv

# Add the src directory to Python path
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

# Load environment variables
load_dotenv()

from src.QA_integration import is_casual_conversation
from src.llm import get_llm

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_casual_conversation_detection():
    """
    Günlük konuşma tespiti fonksiyonunu test eder.
    """
    try:
        # Get LLM model
        model_name = os.getenv('DEFAULT_MODEL', 'openai_gpt_4o_mini')
        llm, _ = get_llm(model=model_name)
        logger.info(f"LLM model loaded: {model_name}")

        # Test cases
        test_cases = [
            # Günlük konuşmalar (CASUAL olması beklenen)
            ("Merhaba", True),
            ("Selam", True),
            ("İyi günler", True),
            ("Nasılsın?", True),
            ("Teşekkür ederim", True),
            ("Hoşça kal", True),
            ("Merhaba, nasıl gidiyor?", True),
            ("Selam, ne yapıyorsun?", True),
            
            # Bilgi gerektiren sorular (QUESTION olması beklenen)
            ("Bu belgelerde hangi sigorta poliçeleri var?", False),
            ("AKSA şirketinin faaliyet raporu hakkında bilgi verebilir misin?", False),
            ("2020 yılındaki dask poliçeleri nelerdir?", False),
            ("Ali Raif Dinçkök'ün kaç adet poliçesi var?", False),
            ("Langchain nedir?", False),
            ("PyCaret ile makine öğrenimi nasıl yapılır?", False),
            ("Belgelerden veri analizi yapabilir misin?", False),
        ]

        print("🧪 Günlük Konuşma Tespiti Test Sonuçları:")
        print("=" * 60)
        
        correct_predictions = 0
        total_tests = len(test_cases)
        
        for question, expected_casual in test_cases:
            try:
                is_casual = is_casual_conversation(question, llm)
                is_correct = is_casual == expected_casual
                
                status = "✅" if is_correct else "❌"
                expected_label = "CASUAL" if expected_casual else "QUESTION"
                actual_label = "CASUAL" if is_casual else "QUESTION"
                
                print(f"{status} '{question[:40]}{'...' if len(question) > 40 else ''}'")
                print(f"   Beklenen: {expected_label}, Sonuç: {actual_label}")
                
                if is_correct:
                    correct_predictions += 1
                    
            except Exception as e:
                print(f"❌ '{question[:40]}...' - Hata: {e}")
        
        print("=" * 60)
        accuracy = (correct_predictions / total_tests) * 100
        print(f"🎯 Doğruluk Oranı: {correct_predictions}/{total_tests} ({accuracy:.1f}%)")
        
        if accuracy >= 80:
            print("🎉 Test başarılı! Günlük konuşma tespiti düzgün çalışıyor.")
        else:
            print("⚠️  Test başarısız! Günlük konuşma tespiti iyileştirilmeli.")
            
    except Exception as e:
        logger.error(f"Test sırasında hata oluştu: {e}")
        print(f"❌ Test hatası: {e}")

if __name__ == "__main__":
    test_casual_conversation_detection()
