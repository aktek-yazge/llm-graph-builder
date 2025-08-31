#!/usr/bin/env python3
"""
Basit log rotation test - sadece RotatingFileHandler'ı test eder
"""
import logging
import json
import os
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone

def test_simple_rotation():
    """Basit log rotation testi"""
    
    # Log klasörünü oluştur
    os.makedirs('logs', exist_ok=True)
    
    # RotatingFileHandler oluştur (100KB, 3 backup)
    handler = RotatingFileHandler(
        'logs/test-rotation.jsonl',
        maxBytes=100*1024,  # 100KB
        backupCount=3,
        encoding='utf-8'
    )
    
    # Logger'ı ayarla
    logger = logging.getLogger('rotation_test')
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    
    print("🔄 Basit log rotation testi başlatılıyor...")
    print("📝 2000 adet test logu üretiliyor...")
    
    for i in range(2000):
        if i % 200 == 0:
            print(f"📊 {i}/2000 log üretildi...")
        
        # JSON formatında uzun log mesajları
        log_data = {
            "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
            "level": "INFO",
            "message": f"Bu test log #{i} - Çok uzun bir mesaj rotation'ı test etmek için. Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.",
            "component": "test",
            "operation": "rotation_test",
            "extra_data": "Bu fazladan veri log boyutunu artırmak için eklenmiştir" * 5
        }
        
        # JSON string olarak logla
        logger.info(json.dumps(log_data, ensure_ascii=False))
    
    print("✅ Test tamamlandı!")
    print("📂 logs/ klasörünü kontrol edin:")
    
    # Oluşan dosyaları listele
    log_files = [f for f in os.listdir('logs') if f.startswith('test-rotation')]
    log_files.sort()
    
    for log_file in log_files:
        file_path = os.path.join('logs', log_file)
        file_size = os.path.getsize(file_path)
        print(f"   - {log_file}: {file_size:,} bytes")
    
    return len(log_files)

if __name__ == "__main__":
    file_count = test_simple_rotation()
    print(f"\n🎯 Toplam {file_count} adet log dosyası oluşturuldu!")
    if file_count > 1:
        print("✅ Log rotation başarıyla çalıştı!")
    else:
        print("❌ Log rotation çalışmadı - dosya boyutu limit'e ulaşmadı.")
