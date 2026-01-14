# Traefik Multi-Tenant Setup

Bu klasör Traefik reverse proxy konfigürasyonunu içerir. Traefik sayesinde tüm servisler tek domain üzerinden, path bazlı routing ile erişilebilir.

## Mimari

### Production Yapısı (Karşı Sunucu + Bizim Sunucu)

```
                    İNTERNET
                       │
                       ▼
    ┌─────────────────────────────────────────────────────────┐
    │  KARŞI SUNUCU (128.127.169.30)                          │
    │  ┌───────────────────────────────────────────────────┐  │
    │  │  Traefik + Let's Encrypt SSL                      │  │
    │  │  demoserver.yazge.aktekbilisim.com (:443)         │  │
    │  └───────────────────────────────────────────────────┘  │
    └─────────────────────┬───────────────────────────────────┘
                          │ HTTP (:80)
                          ▼
    ┌─────────────────────────────────────────────────────────┐
    │  BİZİM SUNUCU (18.153.150.114)                          │
    │  ┌───────────────────────────────────────────────────┐  │
    │  │                    TRAEFIK                        │  │
    │  │   /wat          → frontend-wat:5173              │  │
    │  │   /wat/server   → backend-wat:8000               │  │
    │  │   /wat/mcp      → mcp-neo4j-cypher-wat:8002      │  │
    │  │   /aksa         → frontend-aksa:5173             │  │
    │  │   /aksa/server  → backend-aksa:8000              │  │
    │  └───────────────────────────────────────────────────┘  │
    └─────────────────────────────────────────────────────────┘
```

### Lokal Geliştirme Yapısı

```
    ┌─────────────────────────────────────────────────────────┐
    │  LOCALHOST (:80)                                        │
    │  ┌───────────────────────────────────────────────────┐  │
    │  │                    TRAEFIK                        │  │
    │  │   /wat          → frontend-wat:5173              │  │
    │  │   /wat/server   → backend-wat:8000               │  │
    │  │   /aksa         → frontend-aksa:5173             │  │
    │  │   /aksa/server  → backend-aksa:8000              │  │
    │  └───────────────────────────────────────────────────┘  │
    └─────────────────────────────────────────────────────────┘
```

## Production Erişim Adresleri

| Proje | Servis | URL |
|-------|--------|-----|
| **WAT** | Frontend | https://demoserver.yazge.aktekbilisim.com/wat |
| | Backend API | https://demoserver.yazge.aktekbilisim.com/wat/server |
| | MCP Server | https://demoserver.yazge.aktekbilisim.com/wat/mcp |
| **AKSA** | Frontend | https://demoserver.yazge.aktekbilisim.com/aksa |
| | Backend API | https://demoserver.yazge.aktekbilisim.com/aksa/server |

> ⚠️ **Neo4j** dışarıdan erişime kapalıdır. Sadece container'lar arası `bolt://neo4j:7687` ile erişilebilir.

## Kurulum

### 1. Traefik Network Oluştur

```bash
docker network create traefik-public
```

### 2. Observability Network (varsa atla)

```bash
docker network create observability
```

### 3. Traefik Başlat

```bash
cd /home/ubuntu/python-projects/wat-motor/traefik
docker compose up -d
```

### 4. Stack'leri Başlat

```bash
# WAT Stack
cd /home/ubuntu/python-projects/wat-motor/backend
docker compose -f docker-compose.preview.yml up -d

# AKSA Stack
cd /home/ubuntu/python-projects/llm-graph-builder-aksa
docker compose up -d
```

## Lokal Test (Opsiyonel)

Sunucu üzerinde lokal test için `/etc/hosts` dosyasına ekle:

```bash
echo "127.0.0.1 demoserver.yazge.aktekbilisim.com" | sudo tee -a /etc/hosts
```

## Karşı Sunucu Traefik Ayarları

Karşı sunucuda (128.127.169.30) Traefik config'i:

```yaml
http:
  routers:
    demo-server:
      rule: "Host(`demoserver.yazge.aktekbilisim.com`)"
      entrypoints:
        - websecure
      tls:
        certResolver: myresolver
      service: demo-server-service
  services:
    demo-server-service:
      loadBalancer:
        servers:
          - url: "http://18.153.150.114:80"
```

## Yeni Proje Ekleme

### 1. Compose dosyasında Traefik label'ları ekle:

```yaml
services:
  frontend:
    container_name: frontend-yeniproje
    expose:
      - "5173"
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.frontend-yeniproje.rule=Host(`demoserver.yazge.aktekbilisim.com`) && PathPrefix(`/yeniproje`)"
      - "traefik.http.routers.frontend-yeniproje.entrypoints=web,websecure"
      - "traefik.http.routers.frontend-yeniproje.priority=10"
      - "traefik.http.services.frontend-yeniproje.loadbalancer.server.port=5173"
      - "traefik.http.middlewares.strip-yeniproje.stripprefix.prefixes=/yeniproje"
      - "traefik.http.routers.frontend-yeniproje.middlewares=strip-yeniproje"
    networks:
      - yeniproje
      - traefik-public

  backend:
    container_name: backend-yeniproje
    expose:
      - "8000"
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.backend-yeniproje.rule=Host(`demoserver.yazge.aktekbilisim.com`) && PathPrefix(`/yeniproje/server`)"
      - "traefik.http.routers.backend-yeniproje.entrypoints=web,websecure"
      - "traefik.http.routers.backend-yeniproje.priority=20"
      - "traefik.http.services.backend-yeniproje.loadbalancer.server.port=8000"
      - "traefik.http.middlewares.strip-yeniproje-server.stripprefix.prefixes=/yeniproje/server"
      - "traefik.http.routers.backend-yeniproje.middlewares=strip-yeniproje-server"
    networks:
      - yeniproje
      - traefik-public

networks:
  yeniproje:
    driver: bridge
  traefik-public:
    external: true
    name: traefik-public
```

### 2. Frontend vite.config.ts'e allowedHosts ekle:

```typescript
server: {
  allowedHosts: ['localhost', 'demoserver.yazge.aktekbilisim.com'],
}
```

### 3. Stack'i başlat:

```bash
docker compose up -d
```

## Troubleshooting

### Traefik routing çalışmıyor

```bash
# Traefik loglarını kontrol et
docker logs traefik

# Mevcut router'ları listele
curl -s http://localhost:8090/api/http/routers | python3 -m json.tool

# Container label'larını kontrol et
docker inspect frontend-wat | grep -A 30 Labels
```

### 502 Bad Gateway

```bash
# Backend container çalışıyor mu?
docker ps | grep backend

# Backend loglarını kontrol et
docker logs backend-wat --tail 20
```

### 403 Forbidden (Frontend)

Vite'ın `allowedHosts` ayarını kontrol et:
```typescript
// vite.config.ts
server: {
  allowedHosts: ['demoserver.yazge.aktekbilisim.com'],
}
```

Frontend'i rebuild et:
```bash
docker compose build frontend && docker compose up -d frontend
```

### SSL Sertifikası Sorunu

```bash
# Sertifika bilgilerini kontrol et
openssl s_client -connect demoserver.yazge.aktekbilisim.com:443 -servername demoserver.yazge.aktekbilisim.com 2>/dev/null | openssl x509 -noout -issuer -subject -dates
```

## Dosya Yapısı

```
wat-motor/
├── traefik/
│   ├── docker-compose.yml     # Traefik servisi
│   └── README.md              # Bu dosya
├── backend/
│   └── docker-compose.preview.yml  # WAT Backend + Frontend + Neo4j
├── celery_worker/
│   └── docker-compose.preview.yml  # Celery workers + Flower
└── observability/
    └── docker-compose.yml     # Grafana, Loki, Tempo, Alloy

llm-graph-builder-aksa/
└── docker-compose.yml         # AKSA Backend + Frontend + Neo4j
```

## Servis Durumu Kontrolü

```bash
# Tüm container'ları listele
docker ps --format "table {{.Names}}\t{{.Status}}" | grep -E "wat|aksa|traefik"

# Traefik dashboard
http://localhost:8090/dashboard/

# Health check
curl -s https://demoserver.yazge.aktekbilisim.com/wat/server/health
curl -s https://demoserver.yazge.aktekbilisim.com/aksa/server/health
```
