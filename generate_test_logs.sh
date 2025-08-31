#!/bin/bash

# LLM Graph Builder - Live Log Test Script
# Bu script backend'den canlı log üretmek için kullanılır

echo "🚀 Backend test logları üretiliyor..."

cd /Users/mehmeterdogan/python-projects/llm-graph-builder/backend

# Test logs - OpenTelemetry aktif
PYTHONPATH="$(pwd)/src" python3 -c "
import logging
import sys
sys.path.append('src')

# OTEL setup
try:
    from otel_logging_setup import setup_logging
    setup_logging()
    print('✅ OpenTelemetry hazır')
except Exception as e:
    print(f'⚠️ OTEL setup hatası: {e}')
    logging.basicConfig(level=logging.INFO)

# Backend benzeri log mesajları
logging.info('🔧 Backend test başlatıldı - Dashboard için log akışı')
print('🚀 LLMGraphTransformer process_response başladı')
print('📝 Input text uzunluğu: 1520 karakter')
print('⚙️ Function call modu: True')
print('🎯 Final allowed nodes: [Document, Policy, Customer, PolicyYear, InsuredItem]')
print('🔗 Final allowed relationships: [HAS_POLICY, DOCUMENTED_IN, HAS_YEAR, HAS_TYPE]')

# LLM işlem logları
logging.info('🔢 LLM Token Kullanımı - Input: 250, Output: 120, Total: 370')
logging.info('⏱️ LLM Çağrı süresi: 3.24 saniye')
logging.info('📏 Prompt uzunluğu: 1250 karakter, Response uzunluğu: 890 karakter')

# İşlem durumu logları  
print('🔄 Neo4j'den schema çekiliyor...')
print('🗄️ DB'den çekilen node tipleri (5): [Document, Policy, Customer, PolicyYear, InsuredItem]')
print('🔗 DB'den çekilen relationship tipleri (4): [HAS_POLICY, DOCUMENTED_IN, HAS_YEAR, HAS_TYPE]')

# Başarılı işlemler
print('✅ Database bağlantısı başarılı')
print('✅ Policy extraction tamamlandı: 2.45 saniye')
print('✅ Final sonuç - Nodes: 15, Relationships: 12')

# Uyarılar ve hatalar
logging.warning('⚠️ Could not retrieve page_images from Document node: Connection timeout')
logging.warning('⚠️ Test uyarısı: Schema cache miss - rebuilding')
logging.error('❌ Test hatası: Invalid JSON format in LLM response')

# Dosya işleme
logging.info('🔍 Policy extraction başlıyor: test_document.pdf')
logging.info('🖼️ Retrieved 3 page images from Document node')
print('📋 Context bulundu: 2450 karakter')

print('🎯 Dashboard test logları tamamlandı - Grafana'da görünmelidir!')
"

echo "✅ Test logları üretildi! Grafana dashboard'da kontrol edin."
echo "📊 Grafana URL: http://localhost:3000"
