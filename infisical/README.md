# Infisical - Secret Management

Bu klasör, projenin merkezi secret yönetimi için self-hosted Infisical kurulumunu içerir.

## Hızlı Başlangıç

```bash
# 1. Infisical'ı başlat
cd infisical
docker compose up -d

# 2. Dashboard'a eriş
open http://localhost:8085

# 3. Admin hesabı oluştur (ilk giriş)
```

## İlk Kurulum Adımları

### 1. Admin Hesabı

- http://localhost:8085 adresine git
- "Create Account" ile admin hesabı oluştur

### 2. Proje Oluşturma

- Dashboard'da "New Project" tıkla
- Proje adı: `llm-graph-builder`

### 3. Environment'lar

Aşağıdaki environment'ları oluştur:

- `development` - Local geliştirme
- `preview` - Tenant preview ortamları
- `production` - Production

### 4. Secret Folders

Her environment için şu folder yapısını oluştur:

```
/
├── database/       # POSTGRES_URL, POSTGRES_PASSWORD
├── neo4j/          # NEO4J_URI, NEO4J_PASSWORD
├── broker/         # RABBITMQ_PASSWORD, CELERY_BROKER_URL
├── redis/          # REDIS_URL
├── llm/            # OPENAI_API_KEY, ANTHROPIC_API_KEY
├── aws/            # AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
├── langfuse/       # LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY
└── app/            # FLOWER_AUTH, diğer uygulama ayarları
```

### 5. Machine Identity

Backend ve Celery worker'lar için Machine Identity oluştur:

1. Project Settings > Machine Identities
2. "Create Identity" tıkla
3. İsim: `llm-graph-builder-prod`
4. Authentication: Universal Auth
5. Token'ı kopyala ve güvenli sakla

## Kullanım

### CLI ile Docker Compose Çalıştırma

```bash
# Infisical CLI'ı kur
curl -1sLf 'https://dl.cloudsmith.io/public/infisical/infisical-cli/setup.deb.sh' | sudo -E bash
sudo apt-get update && sudo apt-get install -y infisical

# Login (ilk seferinde)
infisical login

# Docker Compose'u Infisical ile çalıştır
infisical run --env=production --projectId=<PROJECT_ID> -- \
  docker compose -f docker-compose.yml up -d
```

### Machine Token ile (CI/CD veya Production)

```bash
# Token'ı environment'a ekle
export INFISICAL_TOKEN=<MACHINE_TOKEN>

# Çalıştır
infisical run --env=production -- docker compose up -d
```

### Tenant Bazlı Kullanım

```bash
# Akkok-sicil tenant'ı
infisical run --env=preview --path=/tenants/akkok-sicil -- \
  docker compose -p akkok-sicil -f docker-compose.preview.yml up -d

# Bakim tenant'ı
infisical run --env=preview --path=/tenants/bakim -- \
  docker compose -p bakim -f docker-compose.preview.yml up -d
```

## Yapı

```
infisical/
├── docker-compose.yml   # Infisical server stack
├── .env                 # Infisical server secrets (gitignore'da)
├── .env.example         # Örnek .env dosyası
└── README.md            # Bu dosya
```

## Traefik Entegrasyonu

Eğer Traefik kullanıyorsanız, Infisical otomatik olarak şu adreste erişilebilir olur:

- https://secrets.yazge.aktekbilisim.com

## Yedekleme

Infisical verilerini yedeklemek için:

```bash
# PostgreSQL backup
docker exec infisical-postgres pg_dump -U infisical infisical > infisical_backup.sql

# Volume backup
docker run --rm -v infisical_infisical_postgres_data:/data -v $(pwd):/backup \
  alpine tar czf /backup/infisical_data.tar.gz /data
```

## Troubleshooting

### Container başlamıyor

```bash
docker compose logs infisical
```

### Database bağlantı hatası

```bash
# PostgreSQL'in hazır olduğundan emin ol
docker compose logs infisical-db
```

### Redis bağlantı hatası

```bash
docker compose logs infisical-redis
```

