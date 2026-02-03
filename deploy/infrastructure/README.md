# Infrastructure Stack

Tüm tenant'ların paylaştığı ortak servisler.

## Servisler

| Servis   | Port              | Açıklama                                  |
| -------- | ----------------- | ----------------------------------------- |
| RabbitMQ | 5672, 15672       | Message broker (AMQP + Management UI)     |
| Alloy    | 4317, 4318, 12345 | Telemetry collector (OTLP gRPC/HTTP + UI) |

## Mimari

```
┌─────────────────────────────────────────────────────────────────┐
│                    INFRASTRUCTURE STACK                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌─────────────┐        ┌─────────────────────────────────────┐  │
│  │  RabbitMQ   │        │           Grafana Alloy             │  │
│  │  :5672      │        │  :4317 OTLP gRPC                    │  │
│  │  :15672 UI  │        │  :4318 OTLP HTTP                    │  │
│  └─────────────┘        │  :12345 UI                          │  │
│        ▲                └──────────────┬──────────────────────┘  │
│        │                               │                          │
└────────┼───────────────────────────────┼──────────────────────────┘
         │                               │
         │                               ▼
┌────────┴───────────────┐    ┌─────────────────────────────────┐
│  TENANT STACKS         │    │  HARİCİ SERVİSLER               │
│  - akkok-sicil         │    │  - Loki (logs)                  │
│  - wat-motor           │    │  - Tempo (traces)               │
│  - dinkal              │    │  - Grafana (visualization)      │
│                        │    │  - Langfuse (LLM observability) │
└────────────────────────┘    └─────────────────────────────────┘
```

## Kurulum

### 1. Environment Variables

```bash
cp .env.example .env
nano .env  # Değerleri doldurun
```

### 2. Ağ Oluşturma (İlk Kurulum)

```bash
# Traefik network (eğer yoksa)
docker network create traefik-public

# Infrastructure network otomatik oluşturulur
```

### 3. Çalıştırma

```bash
# Manuel
docker compose -p infrastructure up -d

# Infisical ile
infisical run --env=staging --path=/infrastructure -- \
  docker compose -p infrastructure up -d
```

## Tenant Stack'lerden Erişim

Tenant stack'leri infrastructure servislerine erişmek için:

1. `infrastructure_infrastructure` network'üne bağlanmalı
2. Container adıyla erişmeli (örn: `rabbitmq:5672`)

### Örnek Docker Compose (Tenant)

```yaml
services:
  backend:
    environment:
      - CELERY_BROKER_URL=amqp://rabbitmq:rabbitmqpass@rabbitmq:5672//
    networks:
      - internal
      - infrastructure

networks:
  infrastructure:
    external: true
    name: infrastructure_infrastructure
```

## Harici Servis Yapılandırması

### Loki & Tempo

Alloy, telemetriyi harici Loki ve Tempo'ya gönderir:

```bash
# .env
LOKI_URL=https://loki.example.com
TEMPO_URL=https://tempo.example.com:4317
```

### Prometheus Remote Write (Opsiyonel)

Metrics için Prometheus remote write endpoint:

```bash
PROMETHEUS_REMOTE_WRITE_URL=https://prometheus.example.com/api/v1/write
```

## Secrets Yönetimi

### Infisical'a Export

```bash
./export-secrets-to-infisical.sh
```

### Infisical'dan Çalıştırma

```bash
infisical run --env=staging --path=/infrastructure -- \
  docker compose -p infrastructure up -d
```

## Monitoring

### Alloy UI

```
http://localhost:12345
```

### RabbitMQ Management

```
http://localhost:15672
# veya Traefik üzerinden:
https://rabbitmq.yazge.aktekbilisim.com
```

## Troubleshooting

### Network Bağlantı Sorunları

```bash
# Network'leri kontrol et
docker network ls | grep infrastructure

# Container'ın network'lerini kontrol et
docker inspect <container> | jq '.[0].NetworkSettings.Networks'
```

### Alloy Log Kontrolü

```bash
docker logs alloy -f
```

### RabbitMQ Bağlantı Testi

```bash
# Container içinden
docker exec -it <backend-container> \
  python -c "import pika; pika.BlockingConnection(pika.URLParameters('amqp://rabbitmq:pass@rabbitmq:5672//'))"
```

