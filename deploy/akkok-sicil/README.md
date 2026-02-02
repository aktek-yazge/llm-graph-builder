# Akkok-Sicil Staging Deployment Guide

Bu doküman, Akkok-Sicil LLM Graph Builder sisteminin staging/preview ortamına deployment sürecini açıklar.

## Sistem Mimarisi

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              TRAEFIK (Reverse Proxy)                         │
│                        https://akkok-sicil.yazge.aktekbilisim.com            │
└─────────────┬───────────┬───────────┬───────────┬───────────┬───────────────┘
              │           │           │           │           │
         /server      /neo4j     /pgadmin    /flower    /rabbitmq
              │           │           │           │           │
              ▼           ▼           ▼           ▼           ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              DOCKER NETWORK (internal)                       │
├─────────────┬───────────┬───────────┬───────────┬───────────┬───────────────┤
│   Backend   │   Neo4j   │  Postgres │   Redis   │ RabbitMQ  │   Frontend    │
│   (FastAPI) │  (Graph)  │  (pgvector)│  (Cache)  │  (Broker) │   (React)     │
├─────────────┴───────────┴───────────┴───────────┴───────────┴───────────────┤
│                              CELERY WORKERS                                   │
│                    ┌─────────────────┬─────────────────┐                     │
│                    │   Main Worker   │   DB Writer     │                     │
│                    │ (Chunking/Graph)│ (PostgreSQL)    │                     │
│                    └─────────────────┴─────────────────┘                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                              OBSERVABILITY                                    │
│           ┌─────────────┬─────────────┬─────────────────┐                    │
│           │   Langfuse  │    Alloy    │  Flower (Celery)│                    │
│           │  (LLM Obs)  │  (OTEL)     │   Monitoring    │                    │
│           └─────────────┴─────────────┴─────────────────┘                    │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Önkoşullar

### 1. Sunucu Gereksinimleri

- **OS**: Ubuntu 22.04 LTS veya üzeri
- **CPU**: Minimum 8 core (16 core önerilen)
- **RAM**: Minimum 32GB (64GB önerilen)
- **Disk**: Minimum 200GB SSD

### 2. Yazılım Gereksinimleri

```bash
# Docker & Docker Compose
docker --version    # >= 24.0
docker compose version  # >= 2.20

# Infisical CLI
infisical --version  # >= 0.22
```

### 3. Network Gereksinimleri

Aşağıdaki external Docker network'ler mevcut olmalı:

```bash
# Traefik public network
docker network create traefik-public

# Observability network (Loki, Tempo, Alloy için)
docker network create observability
```

### 4. DNS Kayıtları

```
akkok-sicil.yazge.aktekbilisim.com -> Sunucu IP
```

---

## Kurulum Adımları

### Adım 1: Projeyi Klonla

```bash
cd /opt
git clone https://github.com/your-org/llm-graph-builder.git akkok-sicil
cd akkok-sicil
```

### Adım 2: Infisical Kurulumu

#### 2.1 Infisical CLI Kurulumu

```bash
curl -1sLf 'https://dl.cloudsmith.io/public/infisical/infisical-cli/setup.deb.sh' | sudo -E bash
sudo apt-get update && sudo apt-get install -y infisical
```

#### 2.2 Infisical Login

```bash
# Interactive login (ilk seferinde)
infisical login --domain=https://your-infisical-server.com

# VEYA Machine Identity Token ile (CI/CD için)
export INFISICAL_TOKEN=<MACHINE_IDENTITY_TOKEN>
```

#### 2.3 Infisical Proje Yapısı

Infisical'da aşağıdaki yapıyı oluşturun:

```
Project: llm-graph-builder
└── Environment: staging
    └── Path: /tenants/akkok-sicil
        ├── TENANT_DOMAIN=akkok-sicil.yazge.aktekbilisim.com
        ├── # Database
        ├── POSTGRES_PASSWORD=<secure-password>
        ├── POSTGRES_USER=postgres
        ├── POSTGRES_DB=llm_graph_builder
        ├── # Neo4j
        ├── NEO4J_PASSWORD=<secure-password>
        ├── NEO4J_USERNAME=neo4j
        ├── NEO4J_DATABASE=neo4j
        ├── # RabbitMQ
        ├── RABBITMQ_USER=rabbitmq
        ├── RABBITMQ_PASSWORD=<secure-password>
        ├── # LLM API Keys
        ├── OPENAI_API_KEY=sk-xxx
        ├── GEMINI_API_KEY=AIza...
        ├── ANTHROPIC_API_KEY=sk-ant-xxx
        ├── # LLM Model Configs
        ├── LLM_MODEL_CONFIG_openai_gpt_4o_mini=gpt-4o-mini,sk-xxx
        ├── LLM_MODEL_CONFIG_openai_gpt_4o=gpt-4o,sk-xxx
        ├── LLM_MODEL_CONFIG_gemini_2.0_flash=gemini-2.0-flash-001,AIza...
        ├── # AWS S3
        ├── AWS_ACCESS_KEY_ID=AKIA...
        ├── AWS_SECRET_ACCESS_KEY=xxx
        ├── AWS_REGION=eu-central-1
        ├── S3_BUCKET_NAME=akkok-ticaret-sicil
        ├── # Langfuse
        ├── LANGFUSE_ENABLED=true
        ├── LANGFUSE_HOST=https://your-langfuse.com
        ├── LANGFUSE_PUBLIC_KEY=pk-xxx
        ├── LANGFUSE_SECRET_KEY=sk-xxx
        ├── # Authentication
        ├── JWT_SECRET_KEY=<random-32-char-string>
        ├── JWT_EXPIRATION_HOURS=168
        ├── ADMIN_EMAIL=admin@akkok.com
        ├── ADMIN_USERNAME=admin
        ├── ADMIN_PASSWORD=<secure-password>
        ├── # REACT Agent
        ├── USE_REACT_AGENT=true
        ├── REACT_MODEL=gpt-4o
        ├── REACT_TOOL_MODE=cypher
        └── # Flower
            └── FLOWER_AUTH=admin:admin
```

### Adım 3: Local Secrets Export (İlk Kurulum)

Development ortamından secrets'ları export edip Infisical'a import etmek için:

```bash
# Development .env dosyasından secrets oku
cd /workspace/backend

# Infisical'a import et
infisical secrets set \
  --env=staging \
  --path=/tenants/akkok-sicil \
  --domain=https://your-infisical-server.com \
  TENANT_DOMAIN="akkok-sicil.yazge.aktekbilisim.com" \
  POSTGRES_PASSWORD="AkkokSicil!654*" \
  NEO4J_PASSWORD="AkkokSicil!654*" \
  RABBITMQ_PASSWORD="RabbitMQ!654*" \
  OPENAI_API_KEY="sk-xxx" \
  # ... diğer secrets
```

Veya bulk import için JSON kullanın:

```bash
# .env dosyasından JSON oluştur
cat .env.akkok-sicil | grep -v '^#' | grep -v '^$' | \
  jq -R -s 'split("\n") | map(select(length > 0)) | map(split("=") | {key: .[0], value: .[1:]|join("=")}) | from_entries' \
  > secrets.json

# Infisical'a import et (manuel dashboard üzerinden)
```

### Adım 4: Sistemi Başlat

```bash
cd /opt/akkok-sicil/deploy/akkok-sicil

# Infisical ile başlat (önerilen)
infisical run \
  --env=staging \
  --path=/tenants/akkok-sicil \
  --domain=https://your-infisical-server.com \
  -- docker compose -p akkok-sicil -f docker-compose.staging.yml up -d

# Logları takip et
docker compose -p akkok-sicil logs -f
```

### Adım 5: Sağlık Kontrolü

```bash
# Tüm container'ların durumu
docker compose -p akkok-sicil ps

# Beklenen çıktı:
# NAME                        STATUS
# akkok-sicil-backend         Up (healthy)
# akkok-sicil-celery-db       Up
# akkok-sicil-celery-main     Up
# akkok-sicil-flower          Up
# akkok-sicil-frontend        Up
# akkok-sicil-mcp             Up (healthy)
# akkok-sicil-neo4j           Up
# akkok-sicil-pgadmin         Up
# akkok-sicil-postgres        Up (healthy)
# akkok-sicil-rabbitmq        Up (healthy)
# akkok-sicil-redis           Up (healthy)
```

### Adım 6: Erişim Testleri

```bash
# Frontend
curl -I https://akkok-sicil.yazge.aktekbilisim.com

# Backend Health
curl https://akkok-sicil.yazge.aktekbilisim.com/server/health

# Neo4j Browser
# https://akkok-sicil.yazge.aktekbilisim.com/neo4j

# Flower Dashboard
# https://akkok-sicil.yazge.aktekbilisim.com/flower
```

---

## Ortam Değişkenleri Referansı

### Zorunlu Değişkenler

| Değişken                | Açıklama           | Örnek                                |
| ----------------------- | ------------------ | ------------------------------------ |
| `TENANT_DOMAIN`         | Subdomain          | `akkok-sicil.yazge.aktekbilisim.com` |
| `POSTGRES_PASSWORD`     | PostgreSQL şifresi | `SecurePass!123`                     |
| `NEO4J_PASSWORD`        | Neo4j şifresi      | `SecurePass!123`                     |
| `RABBITMQ_PASSWORD`     | RabbitMQ şifresi   | `SecurePass!123`                     |
| `OPENAI_API_KEY`        | OpenAI API Key     | `sk-xxx`                             |
| `AWS_ACCESS_KEY_ID`     | AWS Access Key     | `AKIA...`                            |
| `AWS_SECRET_ACCESS_KEY` | AWS Secret Key     | `xxx`                                |
| `S3_BUCKET_NAME`        | S3 Bucket          | `akkok-ticaret-sicil`                |
| `JWT_SECRET_KEY`        | JWT imzalama key   | `random-32-chars`                    |
| `ADMIN_PASSWORD`        | Admin şifresi      | `SecurePass!123`                     |

### Opsiyonel Değişkenler

| Değişken           | Varsayılan          | Açıklama               |
| ------------------ | ------------------- | ---------------------- |
| `POSTGRES_USER`    | `postgres`          | PostgreSQL kullanıcısı |
| `POSTGRES_DB`      | `llm_graph_builder` | PostgreSQL veritabanı  |
| `NEO4J_USERNAME`   | `neo4j`             | Neo4j kullanıcısı      |
| `NEO4J_DATABASE`   | `neo4j`             | Neo4j veritabanı       |
| `RABBITMQ_USER`    | `rabbitmq`          | RabbitMQ kullanıcısı   |
| `AWS_REGION`       | `eu-central-1`      | AWS region             |
| `LOG_LEVEL`        | `info`              | Log seviyesi           |
| `LANGFUSE_ENABLED` | `true`              | Langfuse aktif mi      |
| `USE_REACT_AGENT`  | `true`              | REACT Agent aktif mi   |
| `REACT_MODEL`      | `gpt-4o`            | REACT Agent modeli     |
| `EMBEDDING_MODEL`  | `openai`            | Embedding modeli       |
| `V2_BATCH_SIZE`    | `10`                | İşleme batch boyutu    |
| `FLOWER_AUTH`      | `admin:admin`       | Flower kimlik bilgisi  |

---

## Operasyonel Komutlar

### Sistemi Yeniden Başlat

```bash
cd /opt/akkok-sicil/deploy/akkok-sicil

infisical run \
  --env=staging \
  --path=/tenants/akkok-sicil \
  -- docker compose -p akkok-sicil restart
```

### Belirli Servisi Yeniden Başlat

```bash
infisical run \
  --env=staging \
  --path=/tenants/akkok-sicil \
  -- docker compose -p akkok-sicil restart backend celery-main-worker
```

### Güncelleme (Yeni Kod Deploy)

```bash
cd /opt/akkok-sicil

# Kodu çek
git pull origin main

# Image'ları rebuild et
infisical run \
  --env=staging \
  --path=/tenants/akkok-sicil \
  -- docker compose -p akkok-sicil build --no-cache

# Servisleri güncelle
infisical run \
  --env=staging \
  --path=/tenants/akkok-sicil \
  -- docker compose -p akkok-sicil up -d
```

### Logları İzle

```bash
# Tüm loglar
docker compose -p akkok-sicil logs -f

# Belirli servis
docker compose -p akkok-sicil logs -f backend celery-main-worker

# Son 100 satır
docker compose -p akkok-sicil logs --tail=100 backend
```

### Sistemi Durdur

```bash
# Durdur (verileri koru)
docker compose -p akkok-sicil down

# Durdur ve verileri sil (DİKKAT!)
docker compose -p akkok-sicil down -v
```

---

## Yedekleme

### PostgreSQL Backup

```bash
# Backup al
docker exec akkok-sicil-postgres pg_dump -U postgres llm_graph_builder > backup_$(date +%Y%m%d).sql

# S3'e yükle
aws s3 cp backup_$(date +%Y%m%d).sql s3://akkok-ticaret-sicil/backups/
```

### Neo4j Backup

```bash
# Neo4j dump
docker exec akkok-sicil-neo4j neo4j-admin database dump neo4j --to-path=/data/backup

# Backup'ı host'a kopyala
docker cp akkok-sicil-neo4j:/data/backup ./neo4j_backup_$(date +%Y%m%d)
```

### Volume Backup

```bash
# Tüm volume'ları backup'la
for vol in $(docker volume ls -q | grep akkok-sicil); do
  docker run --rm -v $vol:/data -v $(pwd)/backups:/backup alpine \
    tar czf /backup/${vol}_$(date +%Y%m%d).tar.gz /data
done
```

---

## Troubleshooting

### Container Başlamıyor

```bash
# Logları kontrol et
docker compose -p akkok-sicil logs backend

# Container inspect
docker inspect akkok-sicil-backend

# Shell'e bağlan
docker exec -it akkok-sicil-backend bash
```

### Database Bağlantı Hatası

```bash
# PostgreSQL erişimi test et
docker exec akkok-sicil-postgres psql -U postgres -c "SELECT 1"

# Neo4j erişimi test et
docker exec akkok-sicil-neo4j cypher-shell -u neo4j -p $NEO4J_PASSWORD "RETURN 1"
```

### Celery Worker Sorunları

```bash
# Worker durumu
docker exec akkok-sicil-celery-main celery -A src.celery_app inspect active

# Queue durumu
docker exec akkok-sicil-celery-main celery -A src.celery_app inspect reserved

# Task'ları temizle
docker exec akkok-sicil-celery-main celery -A src.celery_app purge
```

### Disk Dolu

```bash
# Docker prune
docker system prune -af --volumes

# Log dosyalarını temizle
docker compose -p akkok-sicil logs --no-log-prefix | head -n 0
```

---

## Güvenlik Kontrol Listesi

- [ ] Tüm şifreler güçlü ve benzersiz
- [ ] Infisical Machine Identity token'ı güvenli
- [ ] SSL/TLS sertifikaları güncel (Traefik Let's Encrypt)
- [ ] AWS IAM minimum yetki prensibi
- [ ] Neo4j external erişimi kapalı
- [ ] PostgreSQL external erişimi kapalı
- [ ] RabbitMQ management UI güvenli
- [ ] Flower dashboard şifre korumalı
- [ ] Loglar hassas veri içermiyor

---

## Destek

Sorun durumunda:

1. Bu README'yi kontrol edin
2. Docker loglarını inceleyin
3. Infisical secrets'ları doğrulayın
4. Network bağlantılarını test edin
5. Gerekirse sistem yöneticisine başvurun

---

_Son güncelleme: 2026-02-02_

