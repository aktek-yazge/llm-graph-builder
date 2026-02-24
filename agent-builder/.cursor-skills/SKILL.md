---
name: agent-builder
description: Goal-driven Agent Builder system with Neo4j Ontology DB, MCP Gateway integration, dynamic skill loading, and conversation state machine. Use when working with agent creation, skill management, goal-driven workflows, ontology operations, or MCP Gateway configuration.
---

# Agent Builder System

## Overview

Agent Builder, kullanıcıların goal-driven (hedef odaklı) ve ontology-driven (ontoloji odaklı) yaklaşımla özel agent'lar oluşturmasını sağlayan sistemdir.

## Mimari

```
┌─────────────────────────────────────────────────────────────────┐
│                        Frontend                                 │
│                   Agent Builder Panel                           │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     FastAPI Backend                             │
│              /api/v2/agent-builder/*                            │
│                                                                 │
│  ┌───────────────┐  ┌─────────────────┐  ┌──────────────────┐  │
│  │ router.py     │  │ service.py      │  │ ontology/        │  │
│  │ API Endpoints │  │ State Machine   │  │ Reasoner, Client │  │
│  └───────────────┘  └─────────────────┘  └──────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
          │                     │                      │
          ▼                     ▼                      ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────┐
│ Neo4j Ontology  │  │ MCP Gateway     │  │ Celery Worker       │
│ Goals, Skills   │  │ Virtual Servers │  │ Agentic OCR         │
│ Schemas         │  │ Tool Registry   │  │ Dynamic Skill Load  │
└─────────────────┘  └─────────────────┘  └─────────────────────┘
```

## Proje Yapısı

### Backend

| Dosya | Açıklama |
|-------|----------|
| `backend/src/agent_builder/__init__.py` | Agent Builder modül tanımı |
| `backend/src/agent_builder/router.py` | FastAPI REST API endpoints |
| `backend/src/agent_builder/service.py` | AgentBuilderService - ana orchestration |
| `backend/src/agent_builder/models.py` | Pydantic modelleri (Goal, Skill, Agent, etc.) |
| `backend/src/agent_builder/ontology/__init__.py` | Ontology module exports |
| `backend/src/agent_builder/ontology/neo4j_client.py` | Neo4j Ontology DB client |
| `backend/src/agent_builder/ontology/reasoner.py` | OntologyReasoner (goal/skill matching) |
| `backend/src/agent_builder/ontology/schema/*.cypher` | Neo4j schema tanımları |
| `backend/src/agent_builder/skills/__init__.py` | Skills module |
| `backend/src/agent_builder/skills/skill_registry.py` | Skill CRUD operations |
| `backend/src/agent_builder/conversation/__init__.py` | Conversation module |
| `backend/src/agent_builder/conversation/state_machine.py` | State machine + handlers |

### Celery Worker

| Dosya | Açıklama |
|-------|----------|
| `celery_worker/src/skill_loader.py` | Dynamic skill loading for Agentic OCR |
| `celery_worker/src/agentic_ocr.py` | skill_id parametresi eklendi |

### Frontend

| Dosya | Açıklama |
|-------|----------|
| `frontend/src/components/AgentBuilder/index.tsx` | Ana bileşen |
| `frontend/src/components/AgentBuilder/AgentBuilderChat.tsx` | Chat arayüzü |
| `frontend/src/components/AgentBuilder/AgentBuilderSidebar.tsx` | Session sidebar |
| `frontend/src/services/agentBuilderApi.ts` | API client |

### Deploy

| Dosya | Açıklama |
|-------|----------|
| `deploy/docker-compose.mcp-gateway.yml` | MCP Gateway + Ontology Neo4j deployment |
| `deploy/scripts/init-gateway.sh` | Gateway initialization script |

## Ontology Schema

### Neo4j Node Tipleri

```cypher
// Goal: Kullanıcı hedefleri
(:Goal {id, name, description, goal_type, tenant_id, embedding})

// Skill: Yetenekler (OCR, extraction, mapping)
(:Skill {id, name, prompt_template, effectiveness_score, is_global})

// EntitySchema: Entity tanımları
(:EntitySchema {id, entity_type, properties, context})

// RelationshipSchema: İlişki tanımları  
(:RelationshipSchema {id, relationship_type, source_entity, target_entity})

// AgentDefinition: Oluşturulan agent'lar
(:AgentDefinition {id, name, status, mcp_virtual_server_id})

// Learning: Öğrenme kayıtları (tenant-isolated)
(:Learning {id, learning_type, tenant_id, skill_id})
```

### Relationship Tipleri

```cypher
// Goal -> Skill
(goal)-[:ACHIEVABLE_BY {confidence}]->(skill)

// Skill -> EntitySchema
(skill)-[:EXTRACTS]->(entitySchema)

// Agent -> Skill
(agent)-[:HAS_SKILL]->(skill)

// Agent -> Goal
(agent)-[:PURSUES]->(goal)

// Skill -> Learning
(skill)-[:IMPROVED_BY]->(learning)
```

## API Endpoints

### Session Management
```
POST   /api/v2/agent-builder/sessions          # Create session
GET    /api/v2/agent-builder/sessions/{id}     # Get session
POST   /api/v2/agent-builder/sessions/{id}/message  # Chat
POST   /api/v2/agent-builder/sessions/{id}/upload-samples  # Upload samples
```

### Goals
```
GET    /api/v2/agent-builder/goals             # List goals
POST   /api/v2/agent-builder/goals             # Create goal
GET    /api/v2/agent-builder/goals/{id}/skills # Get achievable skills
```

### Skills
```
GET    /api/v2/agent-builder/skills            # List skills
POST   /api/v2/agent-builder/skills            # Create skill
GET    /api/v2/agent-builder/skills/{id}       # Get skill
POST   /api/v2/agent-builder/skills/{id}/test  # Test skill
```

### Agents
```
GET    /api/v2/agent-builder/agents            # List agents
POST   /api/v2/agent-builder/agents            # Create agent
GET    /api/v2/agent-builder/agents/{id}       # Get agent details
POST   /api/v2/agent-builder/agents/{id}/deploy   # Deploy to Gateway
POST   /api/v2/agent-builder/agents/{id}/process  # Process documents
```

### Ontology
```
GET    /api/v2/agent-builder/ontology/entity-schemas
GET    /api/v2/agent-builder/ontology/relationship-schemas
GET    /api/v2/agent-builder/ontology/contexts
POST   /api/v2/agent-builder/ontology/suggest-schema
```

## Conversation State Machine

Builder agent şu state'ler arasında geçiş yapar:

```
goal_elicitation    ──▶ ontology_search
        │                     │
        ▼                     ▼
goal_decomposition  ◀── skill_match
        │                     │
        ▼                     ▼
sample_request      ──▶ sample_analysis
        │                     │
        ▼                     ▼
schema_proposal     ──▶ schema_review
        │                     │
        ▼                     ▼
skill_generation    ──▶ skill_test
        │                     │
        ▼                     ▼
learning_capture    ──▶ agent_assembly
        │                     │
        ▼                     ▼
gateway_deploy      ──▶ COMPLETED
```

## Dynamic Skill Loading

Agentic OCR'da ontology'den skill yüklemek için:

```python
from src.skill_loader import load_skill_from_ontology

# Skill'i Ontology DB'den yükle
skill = await load_skill_from_ontology(skill_id)

# Prompt'u formatla
prompt = skill.format_prompt(
    text=ocr_text,
    entity_types=skill.entity_types_list
)

# LLM'e gönder
result = await llm.invoke(prompt)

# Effectiveness güncelle
await update_skill_effectiveness(skill_id, success=True)
```

## MCP Gateway Entegrasyonu

### Docker Deployment

```bash
cd /workspace/deploy
docker-compose -f docker-compose.mcp-gateway.yml up -d
```

### Environment Variables

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

### Gateway Admin UI

- URL: http://localhost:4444/admin
- Credentials: MCP_GATEWAY_ADMIN_EMAIL / MCP_GATEWAY_ADMIN_PASSWORD

## OntologyReasoner Kullanımı

```python
from backend.src.agent_builder.ontology import get_ontology_client, OntologyReasoner

# Client al
client = await get_ontology_client()
reasoner = OntologyReasoner(client)

# Goal için skill bul
skills = await reasoner.find_skills_for_goal(
    goal_id="goal-abc123",
    tenant_id="tenant-001",
    include_global=True
)

# Schema öner
proposal = await reasoner.suggest_schema_from_analysis(
    sample_analysis={"detected_entities": ["Policy", "Customer"]},
    context="insurance"
)

# Learning kaydet
await reasoner.record_learning(
    skill_id="skill-xyz",
    tenant_id="tenant-001",
    learning_type="success_pattern",
    description="Tablo yapısı başarıyla çıkarıldı"
)
```

## Skill Oluşturma

```python
from backend.src.agent_builder.models import SkillCreate, SkillCategory
from backend.src.agent_builder.skills import SkillRegistry

registry = SkillRegistry(client)

skill_data = SkillCreate(
    name="Insurance Policy Extraction",
    description="Sigorta poliçelerinden veri çıkarır",
    skill_category=SkillCategory.EXTRACTION,
    prompt_template="""
    Bu belgeden şu entity'leri çıkar: {entity_types}
    
    Entity Schemas:
    {entity_schemas}
    
    Belge:
    {text}
    """,
    tenant_id="tenant-001",
    is_global=False,
    context_ids=["ctx-insurance"]
)

skill_id = await registry.create_skill(skill_data)
```

## Önemli Notlar

1. **Multi-Tenancy**: Documents DB ve Ontology DB ayrı tutulur
2. **Learning Isolation**: Learning'ler tenant-specific, diğer tenant'lar göremez
3. **Skill Versioning**: Skill güncellendiğinde version artar
4. **Effectiveness Score**: Başarılı kullanımlarla EMA ile güncellenir
5. **Gateway Virtual Servers**: Her agent için otomatik oluşturulur
