# 🐳 Docker Environment Guide

Bu proje **3 farklı Docker ortamı** destekler: **Development**, **Staging**, ve **Production**. Her ortamın kendine özgü konfigürasyonu ve kullanım amacı vardır.

## 📋 Ortam Özeti

| Ortam           | Amaç                  | Backend/Frontend           | Neo4j/Qdrant           | Database         |
| --------------- | --------------------- | -------------------------- | ---------------------- | ---------------- |
| **Development** | Geliştirme, debugging | 8000, 5173                | 6333, 7474, 7687       | Development data |
| **Staging**     | Production test, QA   | 8001, 8081                | **Shared with Dev**    | **Shared with Dev** |
| **Production**  | Canlı sistem          | 8000, 8080                | 6333, 7474, 7687       | Production data  |

---

## 🔧 Development Environment (DevContainer)

### Amaç:

- **Active development** ve debugging
- **Hot reload** ile hızlı geliştirme
- **VS Code DevContainer** integration
- **Test yazma** ve debugging

### Özellikler:

- 🔥 **Hot reload** (backend + frontend)
- 🐍 **Python + Node.js** tek container'da
- 🔧 **Development tools** (zsh, oh-my-zsh, git)
- 📊 **Debug logging** aktif
- 🔍 **Source code mount** (real-time sync)

### 🚀 Çalıştırma:

#### VS Code DevContainer ile (Önerilen):

```bash
# 1. VS Code'da projeyi aç
# 2. Command Palette: "Dev Containers: Reopen in Container"
# 3. Container otomatik başlar
```

#### Manuel Docker Compose ile:

```bash
# DevContainer ortamını başlat
docker-compose -f .devcontainer/docker-compose.dev.yml up -d

# Backend'i manuel başlat (container içinde)
docker exec -it llm-graph-builder_devcontainer-devcontainer-1 zsh
cd /workspace && python -m uvicorn score:app --reload --host 0.0.0.0 --port 8000

# Frontend'i manuel başlat (container içinde - yeni terminal)
docker exec -it llm-graph-builder_devcontainer-devcontainer-1 zsh
cd /workspace/frontend && yarn dev --host 0.0.0.0 --port 5173
```

### 📍 Access Points:

- **Backend API**: http://localhost:8000
- **Frontend**: http://localhost:5173
- **Neo4j Browser**: http://localhost:7474
- **Qdrant Dashboard**: http://localhost:6333/dashboard

### 🔧 Development Workflow:

```bash
# Code değişiklikleri otomatik yansır
# Backend: uvicorn --reload ile hot reload
# Frontend: Vite dev server ile hot reload

# Test çalıştır
pytest backend/tests/

# Linting
black backend/
eslint frontend/src/
```

---

## 🧪 Staging Environment

### Amaç:

- **Production build'lerini test** etmek
- **Deployment pipeline** doğrulama
- **QA testing** ortamı
- **Performance testing**

### Özellikler:

- 🏭 **Production Dockerfile'ları** kullanır
- 🔒 **Production-like security** (nginx headers)
- 📊 **Production logging** konfigürasyonu
- � **DevContainer Neo4j/Qdrant** paylaşımı
- 💾 **Shared database** (development data ile test)
- 🌐 **DevContainer network** kullanır

### 🚀 Çalıştırma:

#### Otomatik Script ile (Önerilen):
```bash
# DevContainer dependency'leri kontrol eder ve staging'i başlatır
./start-staging.sh
```

#### Manuel Docker Compose ile:
```bash
# Önce DevContainer servislerinin çalıştığından emin ol
docker-compose -f .devcontainer/docker-compose.dev.yml up -d neo4j qdrant

# Staging ortamını başlat (sadece backend + frontend)
docker-compose -f docker-compose.staging.yml up --build -d

# Logları takip et
docker-compose -f docker-compose.staging.yml logs -f

# Durdur (Neo4j/Qdrant DevContainer'da kalır)
docker-compose -f docker-compose.staging.yml down
```

### 📍 Access Points:

- **Backend API**: http://localhost:8001
- **Frontend**: http://localhost:8081
- **Neo4j Browser**: http://localhost:7474 (shared with dev)
- **Qdrant Dashboard**: http://localhost:6333/dashboard (shared with dev)

### 🧪 Staging Test Workflow:

```bash
# 1. Development'ta feature tamamla (DevContainer çalışıyor)
# 2. Staging'e deploy et (aynı database ile production build test)
./start-staging.sh

# 3. API endpoint'lerini test et
curl http://localhost:8001/health
curl http://localhost:8001/api/status

# 4. Frontend functionality test et
# Browser: http://localhost:8081

# 5. Performance test (production build ile)
# Load testing tools ile 8001 portunu test et

# 6. Data consistency check
# Development (8000) ve Staging (8001) aynı Neo4j/Qdrant kullanıyor

# 7. Logs kontrol et
docker-compose -f docker-compose.staging.yml logs backend
docker-compose -f docker-compose.staging.yml logs frontend
```

### ⚡ **Staging'in Benzersiz Avantajı:**
- ✅ **Aynı veri seti** ile production build test
- ✅ **Zero data migration** - development datası ile test
- ✅ **Resource efficient** - shared services
- ✅ **Fast iteration** - build sadece app layer'ları

---

## 🚀 Production Environment

### Amaç:

- **Canlı sistem** deployment
- **Maximum performance** ve stability
- **Security optimized** konfigürasyon
- **Production monitoring**

### Özellikler:

- ⚡ **Gunicorn + Uvicorn** (8 worker, 8 thread)
- 🛡️ **Nginx security headers**
- 📊 **Production logging** (structured logs)
- 🔒 **Environment isolation**
- 💾 **Persistent volumes** (prod data)

### 🚀 Çalıştırma:

```bash
# Production ortamını başlat
docker-compose up --build -d

# Logları takip et
docker-compose logs -f

# Servis durumunu kontrol et
docker-compose ps

# Durdur
docker-compose down

# Maintenance mode (volume'ları koru)
docker-compose stop
```

### 📍 Access Points:

- **Backend API**: http://localhost:8000
- **Frontend**: http://localhost:8080
- **Neo4j Browser**: http://localhost:7474 (shared)
- **Qdrant Dashboard**: http://localhost:6333/dashboard (shared)

### 🚀 Production Deployment Workflow:

```bash
# 1. Code review ve merge
# 2. Staging'de final test
# 3. Production deployment

# Production deploy
docker-compose pull          # Update images
docker-compose up --build -d # Deploy

# Health check
curl http://localhost:8000/health
curl http://localhost:8080   # Frontend

# Monitor
docker-compose logs -f --tail=100

# Rollback (if needed)
docker-compose down
git checkout previous-commit
docker-compose up --build -d
```

---

## 🔄 Ortamlar Arası Geçiş

### Development → Staging Test:

```bash
# Development zaten çalışıyor (DevContainer)
# Staging otomatik script ile başlat (shared Neo4j/Qdrant kullanır)
./start-staging.sh

# Test et: http://localhost:8001 ve http://localhost:8081
# Aynı veri seti ile production build testi!
```

### Staging → Production:

```bash
# Staging'i durdur (Neo4j/Qdrant DevContainer'da kalır)
docker-compose -f docker-compose.staging.yml down

# Production başlat (kendi Neo4j/Qdrant'ı ile)
docker-compose up --build -d
# Test et: http://localhost:8000 ve http://localhost:8080
```

### Tüm Ortamları Aynı Anda:

```bash
# ✅ Development + Staging Birlikte:
# Development (DevContainer) - 8000, 5173 + shared Neo4j/Qdrant
# Staging - 8001, 8081 (shared Neo4j/Qdrant kullanır)

# ⚠️ Production Ayrı:
# Production - 8000, 8080 + kendi Neo4j/Qdrant'ı (port çakışması)
```

---

## 📊 Container ve Volume Yapısı

### Container İsimleri:

```
Development + Staging (Shared):
- llm-graph-builder_devcontainer-devcontainer-1  # Development app
- backend-staging                                 # Staging backend (production build)
- frontend-staging                                # Staging frontend (production build)
- qdrant-service                                  # SHARED Neo4j
- neo4j-service                                   # SHARED Qdrant

Production (Separate):
- backend-prod
- frontend-prod  
- qdrant-service-prod
- neo4j-service-prod
```

### Network'ler:

```
Development + Staging: workspace_llm-graph-builder_net  (SHARED)
Production:            llm-graph-builder_prod          (SEPARATE)
```

### Volume'lar:

```
Development + Staging: llm-graph-builder_*  (SHARED Neo4j/Qdrant data)
Production:            *_prod               (SEPARATE production data)
```

---

## 🐛 Troubleshooting

### Port Çakışmaları:

```bash
# Hangi servis hangi portu kullanıyor?
docker ps --format "table {{.Names}}\t{{.Ports}}"

# Port'u kim kullanıyor?
lsof -i :8000
netstat -tulpn | grep :8000
```

### Container Durumu:

```bash
# Tüm container'ları göster
docker ps -a

# Logs kontrol et
docker logs <container_name>

# Container içine gir
docker exec -it <container_name> bash
```

### Volume Sorunları:

```bash
# Volume'ları listele
docker volume ls

# Volume içeriğini kontrol et
docker volume inspect <volume_name>

# Volume'u temizle (DİKKAT: Veri kaybı!)
docker volume rm <volume_name>
```

### Environment Sorunları:

```bash
# Environment variable'ları kontrol et
docker exec <container_name> env

# Config dosyalarını kontrol et
docker exec <container_name> cat /code/.env
```

---

## 📝 Environment Files

```
backend/
├── .env              # Development & Production
├── .staging.env      # Staging specific

frontend/
├── .env              # Development & Production
├── .staging.env      # Staging specific

.devcontainer/
├── docker-compose.dev.yml    # Development
├── Dockerfile               # DevContainer

/
├── docker-compose.yml         # Production
├── docker-compose.staging.yml # Staging
```

---

## 🎯 Best Practices

### Development:

- ✅ Use DevContainer for consistent development environment
- ✅ Enable hot reload for faster iteration
- ✅ Use debug logging
- ✅ Mount source code for real-time sync

### Staging:

- ✅ Test production builds before deployment
- ✅ Use production-like configuration
- ✅ Validate environment variables
- ✅ Performance test under load

### Production:

- ✅ Use multi-worker setup (Gunicorn)
- ✅ Enable security headers (Nginx)
- ✅ Monitor logs and metrics
- ✅ Backup volumes regularly
- ✅ Use health checks

### General:

- ✅ Keep environment files secure (.env in .gitignore)
- ✅ Use specific tags for production images
- ✅ Document environment-specific configurations
- ✅ Regular cleanup of unused containers/volumes
