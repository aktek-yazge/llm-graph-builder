# LLM Graph Builder - Grafana Dashboard Kurulum Rehberi

## Dashboard Özellikleri

Bu dashboard, LLM Graph Builder backend'inin loglarını izlemek için tasarlanmıştır. Aşağıdaki ana bileşenleri içerir:

### 1. Panel Açıklamaları

#### **Panel 1: Log Seviyelerine Göre Dağılım**

- **Tip**: Stat Panel
- **Açıklama**: INFO, WARNING, ERROR, DEBUG seviyelerindeki log sayıları
- **Loki Sorgusu**:

```
{service_name="llm-graph-builder"} | json | level != ""
```

#### **Panel 2: Hata Oranı**

- **Tip**: Gauge Panel
- **Açıklama**: Son 24 saatteki hata yüzdesi
- **Loki Sorgusu**:

```
{service_name="llm-graph-builder"} |~ "ERROR|❌"
```

#### **Panel 3: LLM Token Kullanımı**

- **Tip**: Time Series
- **Açıklama**: LLM token kullanım trendleri
- **Loki Sorgusu**:

```
{service_name="llm-graph-builder"} |~ "🔢 LLM Token Kullanımı"
```

#### **Panel 4: İşlem Kategorileri (Emoji Analizi)**

- **Tip**: Pie Chart
- **Açıklama**: Emoji'lere göre işlem dağılımı
- **Loki Sorguları**:

```
{service_name="llm-graph-builder"} |~ "🚀"  # LLM İşlemleri
{service_name="llm-graph-builder"} |~ "🔄"  # İşlem Durumları
{service_name="llm-graph-builder"} |~ "✅"  # Başarılı İşlemler
{service_name="llm-graph-builder"} |~ "❌"  # Hatalı İşlemler
{service_name="llm-graph-builder"} |~ "📝|📋"  # Veri İşleme
{service_name="llm-graph-builder"} |~ "🎯|🔗"  # Graf İşlemleri
```

#### **Panel 5: LLM Çağrı Süreleri**

- **Tip**: Time Series
- **Açıklama**: LLM API çağrı performansı
- **Loki Sorgusu**:

```
{service_name="llm-graph-builder"} |~ "⏱️ LLM Çağrı süresi"
```

#### **Panel 6: Son Hata Mesajları**

- **Tip**: Logs Panel
- **Açıklama**: Hata ve uyarı mesajlarının detaylı görünümü
- **Loki Sorgusu**:

```
{service_name="llm-graph-builder"} |~ "ERROR|❌|⚠️"
```

#### **Panel 7: Dosya İşleme İstatistikleri**

- **Tip**: Table Panel
- **Açıklama**: İşlenen dosyalar ve işlem sayıları
- **Loki Sorgusu**:

```
{service_name="llm-graph-builder"} |~ "Process file name:|🔄 Starting extract process"
```

#### **Panel 8: Schema ve Veritabanı İşlemleri**

- **Tip**: Time Series (Bar)
- **Açıklama**: Neo4j veritabanı bağlantı ve schema işlemleri
- **Loki Sorgusu**:

```
{service_name="llm-graph-builder"} |~ "🔄 Neo4j'den schema çekiliyor|DB'den çekilen|schema çekilemedi"
```

#### **Panel 9: Print vs Logger Mesajları**

- **Tip**: Stat Panel
- **Açıklama**: Print mesajları (emoji) vs Logger mesajları karşılaştırması
- **Loki Sorguları**:

```
# Print Mesajları (Emoji)
{service_name="llm-graph-builder"} |~ "🚀|🔄|✅|❌|📝|📋|🎯|🔗|⚙️|🖼️|⚠️"

# Logger Mesajları
{service_name="llm-graph-builder"} | json | level != ""
```

## Manuel Dashboard Oluşturma Adımları

### 1. Grafana'ya Erişim

```bash
# Grafana'ya tarayıcıdan erişin
open http://localhost:3000
# Varsayılan: admin/admin
```

### 2. Yeni Dashboard Oluşturma

1. **"+" simgesine tıklayın → "Dashboard"**
2. **"Add new panel" seçin**
3. **Aşağıdaki panel'leri sırayla oluşturun:**

### 3. Panel Oluşturma Örnekleri

#### Örnek 1: Log Seviyesi Dağılımı

```
1. Panel Type: Stat seçin
2. Query kısmına: {service_name="llm-graph-builder"} | json | level != ""
3. Legend: {{level}}
4. Transform: "Group by" - level field'ına göre
5. Display: Table format
6. Thresholds: Green (0), Yellow (10), Red (50)
```

#### Örnek 2: Hata Oranı Gauge

```
1. Panel Type: Gauge seçin
2. Query: {service_name="llm-graph-builder"} |~ "ERROR|❌"
3. Min: 0, Max: 100
4. Unit: Percent
5. Thresholds: Green (0-2%), Yellow (2-5%), Red (5%+)
```

#### Örnek 3: LLM Token Time Series

```
1. Panel Type: Time series seçin
2. Query: {service_name="llm-graph-builder"} |~ "🔢 LLM Token Kullanımı"
3. Y-axis: Token/dakika
4. Line interpolation: Linear
5. Fill opacity: 10%
```

### 4. Dashboard Ayarları

```
- Title: "LLM Graph Builder - Log Observability"
- Tags: llm, graph, logs, observability
- Refresh interval: 30s
- Time range: Last 6 hours
```

### 5. Variables (Template Variables)

```
Name: service
Type: Query
Query: label_values(service_name)
Current value: llm-graph-builder
```

## Test Edilecek Log Kategorileri

### Sistem Başlatma

```bash
cd backend
python -c "
import sys
sys.path.append('src')
from otel_logging_setup import setup_logging
import logging
setup_logging()
logging.info('🔧 OpenTelemetry test log - sistem başlatıldı')
print('🚀 Backend test başladı')
print('✅ Sistem hazır')
"
```

### LLM İşlem Simülasyonu

```bash
python -c "
import sys
sys.path.append('src')
from otel_logging_setup import setup_logging
import logging
import time
setup_logging()
logging.info('🔢 LLM Token Kullanımı - Input: 150, Output: 75, Total: 225')
logging.info('⏱️ LLM Çağrı süresi: 2.34 saniye')
print('🎯 Final allowed nodes: [Document, Policy, Customer]')
print('🔗 Final allowed relationships: [HAS_POLICY, DOCUMENTED_IN]')
"
```

### Hata Simülasyonu

```bash
python -c "
import sys
sys.path.append('src')
from otel_logging_setup import setup_logging
import logging
setup_logging()
logging.warning('⚠️ Could not retrieve page_images from Document node: Connection timeout')
logging.error('❌ Policy extraction hatası: Invalid JSON format')
print('❌ Incremental logging error: File not found')
"
```

## Grafana Dashboard Import

Alternatif olarak, hazır dashboard'u import edebilirsiniz:

1. **Grafana → "+" → "Import"**
2. **"grafana-dashboard-config.json" dosyasını yükleyin**
3. **Data source: Loki seçin**
4. **"Import" butonuna basın**

## Troubleshooting

### Eğer loglar görünmüyorsa:

1. **Loki connection'ı kontrol edin**: `{service_name="llm-graph-builder"}`
2. **Time range'i genişletin**: Last 24 hours
3. **Promtail durumunu kontrol edin**: `podman logs llm-promtail`
4. **Log dosyasını kontrol edin**: `tail -f logs/otel-logs.json`

### Performance İyileştirmeleri:

1. **Query range'leri kısaltın** (1h yerine 30m)
2. **Refresh interval'ı artırın** (30s yerine 1m)
3. **Max lines'ı limitleyip** (logs panel için 50 line)

## Dashboard URL'leri

Oluşturulan dashboard'a hızlı erişim için:

- **Ana Dashboard**: http://localhost:3000/d/llm-graph-builder
- **Logs Explorer**: http://localhost:3000/explore (Loki datasource seçili)

