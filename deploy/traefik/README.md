# Traefik - Reverse Proxy

Edge router ve load balancer. Tüm HTTP trafiğini yönetir.

## Özellikler

- **Otomatik SSL**: Let's Encrypt ile ücretsiz sertifika
- **Docker Discovery**: Container label'larından otomatik routing
- **Dashboard**: Web UI ile monitoring

## Kurulum

### 1. Network Oluştur

```bash
docker network create traefik-public
```

### 2. Environment Ayarları

```bash
cp .env.example .env
nano .env
```

Dashboard şifresi oluşturma:

```bash
# htpasswd kurulu değilse
apt install apache2-utils

# Şifre oluştur
htpasswd -nb admin GucluSifre123

# Çıktıyı .env'e koy ($ -> $$ escape)
# admin:$apr1$xxx -> admin:$$apr1$$xxx
```

### 3. Başlat

```bash
./start.sh
```

## Dosya Yapısı

```
traefik/
├── docker-compose.yml    # Ana compose dosyası
├── traefik.yml          # Static configuration
├── dynamic/             # Dynamic configuration
│   ├── middlewares.yml  # Rate limit, security headers
│   └── tls.yml         # TLS ayarları
├── .env.example
└── start.sh / stop.sh
```

## Servis Ekleme

Diğer compose dosyalarında Traefik label'ları ekleyin:

```yaml
services:
  my-app:
    labels:
      - 'traefik.enable=true'
      - 'traefik.http.routers.my-app.rule=Host(`app.example.com`)'
      - 'traefik.http.routers.my-app.entrypoints=websecure'
      - 'traefik.http.routers.my-app.tls.certresolver=letsencrypt'
      - 'traefik.http.services.my-app.loadbalancer.server.port=8000'
    networks:
      - traefik-public

networks:
  traefik-public:
    external: true
```

## Dashboard

```
https://traefik.yazge.aktekbilisim.com
```

Basic auth ile korunur (.env'deki credentials).

## Troubleshooting

```bash
# Logları kontrol et
docker logs traefik -f

# Config doğrulama
docker exec traefik traefik healthcheck

# SSL sertifika durumu
docker exec traefik cat /letsencrypt/acme.json | jq '.letsencrypt.Certificates'
```

