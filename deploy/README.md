# Staging Deployment

LLM Graph Builder staging ortamı deployment yapısı.

## Hızlı Başlangıç

```bash
# 1. İlk kurulum (network'ler ve .env dosyaları)
./setup.sh

# 2. .env dosyalarını düzenle
nano traefik/.env
nano infrastructure/.env
nano akkok-sicil/.env

# 3. Tümünü başlat
./deploy.sh
```

## Yapı

```
deploy/
├── deploy.sh              # Master deployment script
├── setup.sh               # İlk kurulum script'i
├── README.md
│
├── traefik/               # Reverse Proxy (1. sıra)
│   ├── docker-compose.yml
│   ├── traefik.yml
│   ├── dynamic/
│   └── .env.example
│
├── infrastructure/        # Shared Services (2. sıra)
│   ├── docker-compose.yml
│   ├── alloy/
│   └── .env.example
│
└── akkok-sicil/          # Tenant Stack (3. sıra)
    ├── docker-compose.staging.yml
    └── .env.example
```

## Komutlar

```bash
# Tümünü başlat
./deploy.sh

# Tek stack başlat
./deploy.sh traefik
./deploy.sh infrastructure
./deploy.sh akkok-sicil

# Durumu kontrol et
./deploy.sh status

# Tümünü durdur
./deploy.sh stop
```

## Stack Bağımlılıkları

```
traefik (SSL, routing)
    │
    └── infrastructure (RabbitMQ, Alloy)
            │
            └── akkok-sicil (Tenant uygulaması)
```

## Network'ler

| Network                       | Amaç                            |
| ----------------------------- | ------------------------------- |
| traefik-public                | Traefik'e expose olan servisler |
| observability                 | Alloy log collection            |
| infrastructure_infrastructure | RabbitMQ erişimi                |

## Ortam Değişkenleri

Her stack kendi `.env` dosyasını kullanır:

| Stack          | Kritik Değişkenler                           |
| -------------- | -------------------------------------------- |
| traefik        | `BASE_DOMAIN`, `TRAEFIK_DASHBOARD_AUTH`      |
| infrastructure | `RABBITMQ_PASSWORD`, `LOKI_URL`, `TEMPO_URL` |
| akkok-sicil    | Database passwords, API keys, JWT secrets    |

## Infisical Entegrasyonu

Manuel `.env` yerine Infisical kullanabilirsiniz:

```bash
# Her stack için
cd akkok-sicil
infisical run --env=staging --path=/tenants/akkok-sicil -- \
  docker compose -p akkok-sicil -f docker-compose.staging.yml up -d
```

## Troubleshooting

### Container Logları

```bash
docker logs <container-name> -f --tail 100
```

### Network Kontrolü

```bash
# Container hangi network'lerde?
docker inspect <container> | jq '.[0].NetworkSettings.Networks | keys'

# Network'teki container'lar
docker network inspect traefik-public | jq '.[0].Containers'
```

### Rebuild

```bash
# Tek servis
docker compose -p akkok-sicil -f docker-compose.staging.yml build --no-cache backend
docker compose -p akkok-sicil -f docker-compose.staging.yml up -d backend

# Tüm stack
./deploy.sh akkok-sicil
```

### Sertifika Sorunları

```bash
# Let's Encrypt sertifika durumu
docker exec traefik cat /letsencrypt/acme.json | jq '.letsencrypt.Certificates[].domain'
```

