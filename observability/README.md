# 📊 Observability Stack (Modern - Grafana Alloy)

LLM Graph Builder için merkezi log, trace ve metric toplama sistemi.

**Grafana Alloy** kullanılarak modernize edilmiş - Promtail + OTel Collector + Grafana Agent tek araçta birleştirildi.

## 🏗️ Mimari

```
┌─────────────────────────────────────────────────────────────────────┐
│                         APPLICATIONS                                 │
│  ┌──────────┐  ┌──────────┐  ┌─────────────┐  ┌──────────────────┐  │
│  │ Backend  │  │ Frontend │  │ MCP Servers │  │ Celery Workers   │  │
│  └────┬─────┘  └────┬─────┘  └──────┬──────┘  └────────┬─────────┘  │
└───────┼─────────────┼───────────────┼──────────────────┼────────────┘
        │             │               │                  │
        │ OTLP        │ File logs     │ OTLP             │ Docker logs
        ▼             ▼               ▼                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      GRAFANA ALLOY                                   │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │  • OTLP Receiver (traces, metrics, logs)                       │ │
│  │  • File Log Collection (backend/*.jsonl)                       │ │
│  │  • Docker Container Discovery & Logs                           │ │
│  │  • Flow-based Pipeline Processing                              │ │
│  └──────────────────────────┬─────────────────────────────────────┘ │
└─────────────────────────────┼───────────────────────────────────────┘
                              │
          ┌───────────────────┼───────────────────┐
          │                   │                   │
          ▼                   ▼                   ▼
    ┌──────────┐        ┌──────────┐        ┌──────────┐
    │   LOKI   │        │  TEMPO   │        │ GRAFANA  │
    │  (logs)  │        │ (traces) │        │  (viz)   │
    └──────────┘        └──────────┘        └──────────┘
```

## 🚀 Hızlı Başlangıç

### 1. Stack'i Başlat

```bash
cd observability
docker-compose up -d
```

### 2. Servislere Eriş

| Servis | URL | Açıklama |
|--------|-----|----------|
| **Grafana** | http://localhost:3000 | Dashboard & Visualization |
| **Alloy UI** | http://localhost:12345 | Alloy Pipeline Monitoring |
| **Loki** | http://localhost:3100 | Log API |
| **Tempo** | http://localhost:3200 | Trace API |
| **OTLP gRPC** | grpc://localhost:4317 | Application telemetry |
| **OTLP HTTP** | http://localhost:4318 | Application telemetry |

### 3. Backend'i Bağla

Backend `.env` veya environment variables:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
OTEL_SERVICE_NAME=llm-graph-builder
OTEL_ENVIRONMENT=development
```

## 📁 Klasör Yapısı

```
observability/
├── docker-compose.yml           # Stack orchestration
├── alloy/
│   └── config.alloy             # Alloy pipeline config (River syntax)
├── loki/
│   └── config.yaml              # Log storage config
├── tempo/
│   └── config.yaml              # Trace storage config
├── grafana/
│   ├── provisioning/
│   │   ├── datasources/
│   │   │   └── datasources.yaml # Auto-configured datasources
│   │   └── dashboards/
│   │       └── dashboards.yaml  # Dashboard provider
│   └── dashboards/
│       └── llm-graph-builder.json
└── README.md
```

## 🔧 Alloy Konfigürasyonu

Alloy, **Flow-based (River)** syntax kullanır:

### Log Collection

```river
// Backend JSON log dosyalarını topla
local.file_match "backend_logs" {
  path_targets = [{
    __path__ = "/var/log/backend/*.jsonl",
    job      = "backend",
  }]
}

loki.source.file "backend_logs" {
  targets    = local.file_match.backend_logs.targets
  forward_to = [loki.write.default.receiver]
}
```

### OTLP Receiver

```river
// Application'lardan OTLP ile telemetry al
otelcol.receiver.otlp "default" {
  grpc { endpoint = "0.0.0.0:4317" }
  http { endpoint = "0.0.0.0:4318" }
  
  output {
    traces  = [otelcol.exporter.otlp.tempo.input]
    logs    = [otelcol.exporter.loki.default.input]
    metrics = [otelcol.exporter.prometheus.default.input]
  }
}
```

### Docker Container Logs

```river
// Docker container'ları otomatik keşfet
discovery.docker "containers" {
  host = "unix:///var/run/docker.sock"
  filter {
    name   = "label"
    values = ["logging=alloy"]  // label ile filtreleme
  }
}
```

## 🔌 Entegrasyon

### Ana docker-compose ile Birleştirme

```bash
# Preview ortamı + Observability
docker-compose -f docker-compose.preview.yml \
               -f observability/docker-compose.yml up -d
```

### Container'ları Alloy'a Bağlama

Docker container'ların loglarını toplamak için label ekleyin:

```yaml
services:
  backend:
    labels:
      - "logging=alloy"
```

### Network Bağlantısı

```bash
docker network connect observability backend-preview
```

Veya docker-compose'da:

```yaml
services:
  backend:
    networks:
      - preview
      - observability

networks:
  observability:
    external: true
    name: observability
```

## 📊 Grafana Dashboards

### Mevcut Dashboard: LLM Graph Builder - Observability

| Panel | Açıklama |
|-------|----------|
| INFO/WARNING/ERROR Stats | Son 1 saat log sayıları |
| Error Rate Gauge | 24 saat hata oranı (%) |
| Log Volume by Level | Zaman serisi log hacmi |
| Logs by Component | Pie chart - component dağılımı |
| Error & Warning Logs | Live log panel |
| All Logs | Tüm loglar (live) |

## 🔍 LogQL Sorgu Örnekleri

```logql
# Tüm ERROR logları
{service_name="llm-graph-builder"} |= "ERROR"

# JSON parse ile level filtreleme
{service_name="llm-graph-builder"} | json | level="ERROR"

# Component bazlı filtreleme
{service_name="llm-graph-builder"} | json | component="upload"

# Son 5 dakikada hata sayısı
sum(count_over_time({service_name="llm-graph-builder"} |= "ERROR" [5m]))
```

## 🛠️ Troubleshooting

### Loglar Görünmüyorsa

1. Alloy UI'ı kontrol et: http://localhost:12345
2. Alloy container logları:
   ```bash
   docker logs alloy
   ```
3. Loki ready check:
   ```bash
   curl http://localhost:3100/ready
   ```

### Alloy Pipeline Debug

Alloy UI'da (http://localhost:12345):
- **Graph View**: Pipeline akışını görselleştir
- **Component Status**: Her component'in durumu
- **Metrics**: İç metrikler

### Container Logları Toplanmıyorsa

1. Container'a `logging=alloy` label'ı ekli mi?
2. Docker socket mount edilmiş mi?
3. Alloy'un docker'a erişimi var mı?

## 📈 Neden Alloy?

| Özellik | Eski Stack | Alloy |
|---------|------------|-------|
| Container sayısı | 3 (Promtail + OTel Collector + Agent) | 1 |
| Konfigürasyon | 3 farklı syntax | 1 (River) |
| OTel desteği | Ayrı collector gerekli | Native |
| Grafana entegrasyonu | Manuel | Native |
| Aktif geliştirme | Promtail: ⚠️ | ✅ |

## 📚 Referanslar

- [Grafana Alloy Documentation](https://grafana.com/docs/alloy/latest/)
- [Alloy Configuration Reference](https://grafana.com/docs/alloy/latest/reference/)
- [Grafana Loki](https://grafana.com/docs/loki/latest/)
- [Grafana Tempo](https://grafana.com/docs/tempo/latest/)
- [LogQL Cheat Sheet](https://grafana.com/docs/loki/latest/logql/)

## 🔄 Migration from Promtail

Eğer mevcut Promtail config'iniz varsa:

```bash
# Promtail config'i Alloy'a dönüştür
alloy convert --source-format=promtail --output=config.alloy promtail.yaml
```

---

## 🔧 Bilinen Sorunlar ve Çözümler

### Dashboard Query Hataları

#### 1. Loki JSON Parser Hatası
**Hata:** `pipeline error: 'JSONParserErr' for series`

**Sebep:** Dashboard query'leri `| json` kullanıyordu ama Alloy zaten JSON parse edip label ekliyor.

**Çözüm:**
```diff
# Önceki (hatalı)
- {job="backend"} | json | level != ""
# Sonraki (doğru)
+ {job="backend", level!=""}
```

#### 2. Loki OR Operatör Hatası
**Hata:** `parse error : unexpected type for left leg of binary operation (or)`

**Sebep:** LogQL'de `or` operatörü bu şekilde kullanılamaz.

**Çözüm:**
```diff
# Önceki (hatalı)
- {job="backend"} |= "ERROR" or {job="backend"} |= "WARNING"
# Sonraki (doğru)
+ {job="backend", level=~"ERROR|WARNING"}
```

#### 3. Grafana Datasource UID Uyumsuzluğu
**Hata:** Dashboard panelleri "No data" gösteriyor.

**Sebep:** Dashboard'daki datasource UID (`loki`) Grafana'daki gerçek UID ile uyuşmuyor.

**Çözüm:** `datasources.yaml`'da sabit UID tanımla:
```yaml
- name: Loki
  type: loki
  uid: loki  # Sabit UID
```

#### 4. TraceQL Metrics Desteklenmemesi
**Hata:** `queryType: "traceqlmetrics"` panelleri çalışmıyor.

**Sebep:** Tempo metrics-generator etkin değil veya TraceQL Metrics özelliği Grafana'da devre dışı.

**Çözüm:** 
- Grafana feature flags etkinleştir: `GF_FEATURE_TOGGLES_ENABLE=tempoSearch,tempoBackendSearch,tempoServiceGraph,tempoApmTable`
- Veya `queryType: "nativeSearch"` kullan

#### 5. Tempo Recent Traces Paneli Boş
**Hata:** "No data found in response"

**Sebep:** TraceQL query formatı yanlış.

**Çözüm:**
```diff
# Önceki (hatalı)
- "queryType": "traceql"
- "query": "{ resource.service.name =~ \".*\" }"
# Sonraki (doğru)
+ "queryType": "nativeSearch"
+ (query parametresi kaldırıldı)
```

---

## 📋 Çalışan Dashboard Panelleri

| Panel | Durum | Query Tipi |
|-------|-------|------------|
| INFO Logs (1h) | ✅ | Loki - label filter |
| WARNING Logs (1h) | ✅ | Loki - label filter |
| ERROR Logs (1h) | ✅ | Loki - label filter |
| Error Rate (24h) | ✅ | Loki - label filter |
| Log Volume by Level | ✅ | Loki - label filter |
| Logs by Component | ✅ | Loki - label filter |
| Logs by Operation | ✅ | Loki - label filter |
| Recent Traces | ⚠️ | Tempo - nativeSearch |
| Error Traces | ⚠️ | Tempo - nativeSearch |
| Error & Warning Logs | ✅ | Loki - regex filter |

---

**Modern. Simple. Unified.** 🚀
