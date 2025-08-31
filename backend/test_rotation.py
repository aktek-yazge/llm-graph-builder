#!/usr/bin/env python3
"""
Log rotation test - büyük miktarda log üretip rotation'ı test eder
"""
import sys
import os

# Backend src klasörünü path'e ekle
sys.path.insert(0, 'src')

from otel_logging_setup import setup_simple_json_logging
import logging
import time

def test_log_rotation():
    """Log rotation'ı test et"""
    
    # JSON logging'i başlat
    setup_simple_json_logging()
    
    logger = logging.getLogger('rotation_test')
    
    print("🔄 Log rotation testi başlatılıyor...")
    print("📝 1000 adet test logu üretiliyor...")
    
    for i in range(1000):
        if i % 100 == 0:
            print(f"📊 {i}/1000 log üretildi...")
        
        # Farklı seviyede loglar üret
        if i % 10 == 0:
            logger.error(f"❌ Test error log #{i} - Bu uzun bir hata mesajıdır ve log dosyasının boyutunu artırmak için fazladan metin içerir. Lorem ipsum dolor sit amet, consectetur adipiscing elit.")
        elif i % 5 == 0:
            logger.warning(f"⚠️ Test warning log #{i} - Bu uyarı mesajı da oldukça uzundur ve log rotation'ın çalışıp çalışmadığını test etmek içindir.")
        else:
            logger.info(f"ℹ️ Test info log #{i} - Normal bilgi mesajı. Bu mesaj da rotation test için yeterince uzun olmalıdır.")
            
        # Her 50 logda bir kısa bekleme
        if i % 50 == 0:
            time.sleep(0.01)
    
    print("✅ Test tamamlandı!")
    print("📂 logs/ klasörünü kontrol edin:")
    print("   - simple-logs.jsonl (ana dosya)")
    print("   - simple-logs.jsonl.1, .2, .3... (rotated dosyalar)")
    
    return True

if __name__ == "__main__":
    test_log_rotation()
