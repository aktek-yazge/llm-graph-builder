# Langfuse Self-Hosted Setup

## 📌 Overview

**Langfuse v3.142.0** - LLM Observability, Tracing, and Prompt Management

This setup provides a complete self-hosted Langfuse instance for LLM observability in the LLM Graph Builder project.

## 🏗️ Architecture

| Service | Port | Description |
|---------|------|-------------|
| langfuse-web | 3101 | Langfuse Web UI & API |
| langfuse-worker | 3030 | Background worker |
| langfuse-postgres | 5433 | PostgreSQL database |
| langfuse-clickhouse | 8124/9001 | ClickHouse analytics |
| langfuse-redis | 6380 | Redis cache |
| langfuse-minio | 9190/9191 | S3-compatible storage |

> **Note:** Ports are configured to avoid conflicts with the main LLM Graph Builder services.

## 🚀 Quick Start

### 1. Start Langfuse

```bash
cd observability/langfuse
docker compose up -d
```

### 2. Wait for Services

Watch the logs until you see "Ready":

```bash
docker compose logs -f langfuse-web
```

### 3. Access Langfuse UI

Open http://localhost:3101 in your browser.

**Default Credentials (configured via headless init):**
- Email: `admin@llmgraphbuilder.local`
- Password: `admin_password_2024`

### 4. Get API Keys

After login, go to **Settings > API Keys** and copy your:
- **Public Key**: `pk-llmgb-main-2024`
- **Secret Key**: `sk-llmgb-main-2024-secret`

## ⚙️ Configuration for LLM Graph Builder

Add these to your `backend/.preview.env`:

```env
# Langfuse Configuration
LANGFUSE_ENABLED=true
LANGFUSE_HOST=http://localhost:3101
LANGFUSE_PUBLIC_KEY=pk-llmgb-main-2024
LANGFUSE_SECRET_KEY=sk-llmgb-main-2024-secret
```

## 🔒 Production Security

For production deployments, generate secure secrets:

```bash
# Generate encryption key
openssl rand -hex 32

# Generate salt
openssl rand -base64 32

# Generate NextAuth secret
openssl rand -base64 32
```

Create a `.env` file (copy from below) and update all `# CHANGEME` values:

```env
# ============================================
# Langfuse v3.142.0 - Environment Configuration
# ============================================

# Security Secrets - CHANGE THESE IN PRODUCTION!
LANGFUSE_ENCRYPTION_KEY=<your-64-char-hex-key>
LANGFUSE_SALT=<your-random-salt>
LANGFUSE_NEXTAUTH_SECRET=<your-random-secret>

# PostgreSQL
LANGFUSE_POSTGRES_USER=langfuse
LANGFUSE_POSTGRES_PASSWORD=<strong-password>
LANGFUSE_DATABASE_URL=postgresql://langfuse:<password>@langfuse-postgres:5432/langfuse

# ClickHouse
LANGFUSE_CLICKHOUSE_PASSWORD=<strong-password>

# Redis
LANGFUSE_REDIS_AUTH=<strong-password>

# MinIO
LANGFUSE_MINIO_ROOT_PASSWORD=<strong-password>
LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY=<strong-password>
LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY=<strong-password>

# Admin User
LANGFUSE_INIT_USER_EMAIL=your-email@domain.com
LANGFUSE_INIT_USER_PASSWORD=<strong-password>
```

## 📊 Features Available

### Included in Open Source:
- ✅ LLM Tracing & Spans
- ✅ Prompt Management
- ✅ Token Usage Tracking
- ✅ Cost Calculation
- ✅ User Feedback & Scores
- ✅ Sessions & Metadata
- ✅ API & SDKs (Python, JS/TS)
- ✅ OpenAI, LangChain, LlamaIndex integrations

### Enterprise Features (require license):
- 🔒 SSO (SAML, OIDC)
- 🔒 RBAC
- 🔒 Automated Access Provisioning
- 🔒 UI Customization

## 📁 Data Persistence

Data is stored in Docker volumes:
- `langfuse_postgres_data` - User data, traces
- `langfuse_clickhouse_data` - Analytics data
- `langfuse_minio_data` - Media & exports

To backup:
```bash
docker compose exec langfuse-postgres pg_dump -U langfuse langfuse > backup.sql
```

## 🛑 Shutdown

```bash
# Stop services (keep data)
docker compose down

# Stop and remove data (⚠️ destructive)
docker compose down -v
```

## 🔄 Upgrade

```bash
docker compose pull
docker compose up -d
```

## 📚 Resources

- [Langfuse Docs](https://langfuse.com/docs)
- [Self-Hosting Guide](https://langfuse.com/self-hosting)
- [GitHub](https://github.com/langfuse/langfuse)
- [Python SDK](https://github.com/langfuse/langfuse-python)

