# Agent Builder

Goal-driven ve Ontology-driven agent oluşturma sistemi.

## Proje Yapısı

```
agent-builder/
├── backend/                    # FastAPI Backend
│   ├── main.py                # Standalone servis entry point
│   ├── requirements.txt       # Python dependencies
│   └── src/
│       ├── __init__.py
│       ├── router.py          # API endpoints
│       ├── service.py         # AgentBuilderService
│       ├── models.py          # Pydantic models
│       ├── ontology/          # Neo4j Ontology DB
│       │   ├── neo4j_client.py
│       │   ├── reasoner.py
│       │   └── schema/        # Cypher schema files
│       ├── skills/            # Skill management
│       │   └── skill_registry.py
│       └── conversation/      # State machine
│           └── state_machine.py
│
├── frontend/                   # React Frontend
│   ├── package.json
│   └── src/
│       ├── components/        # UI components
│       └── services/          # API client
│
├── shared/                     # Shared modules
│   └── skill_loader.py        # Dynamic skill loading
│
├── deploy/                     # Deployment files
│   ├── docker-compose.mcp-gateway.yml
│   └── scripts/
│       └── init-gateway.sh
│
└── .cursor-skills/            # Cursor AI skills
    └── SKILL.md
```

## Kurulum

### Backend

```bash
cd backend
uv sync
uv run uvicorn main:app --host 0.0.0.0 --port 8001 --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

### Neo4j Ontology DB + MCP Gateway

```bash
cd deploy
docker-compose -f docker-compose.mcp-gateway.yml up -d
```

## Environment Variables

```bash
# Ontology DB
ONTOLOGY_NEO4J_URI=bolt://localhost:7688
ONTOLOGY_NEO4J_USERNAME=neo4j
ONTOLOGY_NEO4J_PASSWORD=ontology_secret
ONTOLOGY_NEO4J_DATABASE=ontology

# MCP Gateway
MCP_GATEWAY_JWT_SECRET=your-secret-key
MCP_GATEWAY_ADMIN_EMAIL=admin@example.com
MCP_GATEWAY_ADMIN_PASSWORD=changeme
```

## API Endpoints

| Method | Endpoint | Açıklama |
|--------|----------|----------|
| POST | `/api/v2/agent-builder/sessions` | Yeni session oluştur |
| POST | `/api/v2/agent-builder/sessions/{id}/message` | Mesaj gönder |
| GET | `/api/v2/agent-builder/goals` | Goal listesi |
| POST | `/api/v2/agent-builder/goals` | Goal oluştur |
| GET | `/api/v2/agent-builder/skills` | Skill listesi |
| POST | `/api/v2/agent-builder/skills` | Skill oluştur |
| GET | `/api/v2/agent-builder/skills/{id}/execution` | Skill execution bilgileri (Celery için) |
| GET | `/api/v2/agent-builder/agents` | Agent listesi |
| POST | `/api/v2/agent-builder/agents/{id}/deploy` | Agent deploy |
| POST | `/api/v2/agent-builder/agents/{id}/process` | Agent ile belge işle |

## Celery Worker Entegrasyonu

Agentic OCR'da dynamic skill loading için:

```python
# celery_worker/src/agentic_ocr.py
# skill_id parametresi ile çağır
result = await ocr.process(
    image_list=images,
    file_name="document.pdf",
    skill_id="skill-abc123"  # Ontology DB'den yüklenir
)
```

## Test

E2E testleri çalıştırmak için:

```bash
# Backend çalışıyor olmalı
cd agent-builder/backend
uv run pytest ../tests/test_e2e_agent_flow.py -v
```

Test akışı:
1. Health check
2. Session oluştur
3. Goal tanımla
4. Skill oluştur
5. Agent oluştur
6. Agent deploy et
7. Belge işle (Celery gerektirir)

## Mimari

```
┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐
│   Frontend      │      │   Agent Builder │      │   Neo4j         │
│   (React)       │─────▶│   API (8001)    │─────▶│   Ontology DB   │
│   Port: 3001    │      │                 │      │   Port: 7688    │
└─────────────────┘      └────────┬────────┘      └─────────────────┘
                                  │
                                  ▼
                         ┌─────────────────┐      ┌─────────────────┐
                         │   Celery        │─────▶│   Agentic OCR   │
                         │   Worker        │      │   (skill_id)    │
                         └─────────────────┘      └─────────────────┘
```

## Lisans

MIT
