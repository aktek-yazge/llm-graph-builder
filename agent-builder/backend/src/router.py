"""
Agent Builder API Router
========================

FastAPI router - Agent Builder REST API endpoints.

Bu router şu endpoint gruplarını içerir:
- /sessions: Builder conversation session yönetimi
- /goals: Goal CRUD ve sorgulama
- /skills: Skill CRUD, test ve sorgulama  
- /agents: Agent CRUD ve deployment
- /ontology: Schema sorgulama ve öneri

Kullanım:
---------
    # score.py'de
    from backend.src.agent_builder.router import router as agent_builder_router
    app.include_router(agent_builder_router, prefix="/api/v2/agent-builder")
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Path, UploadFile, File, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .models import (
    # Session models
    BuilderSessionCreate,
    BuilderSession,
    BuilderChatRequest,
    BuilderChatResponse,
    AgentChatRequest,
    AgentChatResponse,
    RichMessagePart,
    # Goal models
    GoalCreate,
    Goal,
    GoalWithSkills,
    GoalSummary,
    GoalType,
    # Skill models
    SkillCreate,
    Skill,
    SkillSummary,
    SkillWithDependencies,
    SkillCategory,
    SkillTestRequest,
    SkillTestResult,
    # Schema models
    EntitySchemaCreate,
    EntitySchema,
    EntitySchemaSummary,
    RelationshipSchemaCreate,
    RelationshipSchema,
    RelationshipSchemaSummary,
    # Agent models
    AgentCreate,
    AgentDefinition,
    AgentSummary,
    AgentWithDetails,
    AgentStatus,
    DeploymentRequest,
    DeploymentResult,
    ProcessRequest,
    ProcessResult,
    # Other
    Context,
    SchemaProposalResponse,
    SampleUploadResponse,
)
from .ontology import get_ontology_client, OntologyDBClient, OntologyReasoner
from .skills import SkillRegistry
from .service import AgentBuilderService
from .conversation import ConversationStateMachine
from .event_store import get_postgres_client, EventStore, EventFilter
from .mutation_gateway import RollbackManager, AuditTrail

logger = logging.getLogger(__name__)

# =============================================================================
# ROUTER SETUP
# =============================================================================

router = APIRouter(tags=["Agent Builder"])


# =============================================================================
# DEPENDENCY INJECTION
# =============================================================================

async def get_ontology_db() -> OntologyDBClient:
    """Ontology DB client dependency"""
    return await get_ontology_client()


async def get_reasoner(db: OntologyDBClient = Depends(get_ontology_db)) -> OntologyReasoner:
    """Ontology Reasoner dependency"""
    return OntologyReasoner(db)


async def get_skill_registry() -> SkillRegistry:
    """Skill Registry dependency"""
    from .dependencies import get_agent_repo
    agent_repo = await get_agent_repo()
    return SkillRegistry(agent_repo)


# =============================================================================
# SESSION ENDPOINTS
# =============================================================================

@router.get("/sessions", summary="List builder sessions")
async def list_builder_sessions(
    tenant_id: str = Query(default="default"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    from .dependencies import get_chat_repo
    chat_repo = await get_chat_repo()
    rows = await chat_repo.list_builder_sessions(tenant_id, limit=limit, offset=offset)
    return [
        BuilderSession(
            id=r["id"],
            tenant_id=r["tenant_id"],
            status=r.get("status", "active"),
            current_state=r.get("current_state", ""),
            state_data={},
        )
        for r in rows
    ]


@router.post("/sessions", response_model=BuilderSession, summary="Create builder session")
async def create_builder_session(
    request: BuilderSessionCreate,
):
    import uuid
    from .dependencies import get_chat_repo
    chat_repo = await get_chat_repo()

    session_id = f"session-{uuid.uuid4().hex[:12]}"

    await chat_repo.create_builder_session(
        session_id=session_id,
        tenant_id=request.tenant_id,
    )

    logger.info(f"Created builder session: {session_id} for tenant: {request.tenant_id}")

    return BuilderSession(
        id=session_id,
        tenant_id=request.tenant_id,
        status="active",
        current_state="goal_elicitation",
        state_data={},
    )


@router.post("/sessions/{session_id}/message", summary="Send message to builder")
async def builder_chat(
    session_id: str,
    request: BuilderChatRequest,
):
    from .dependencies import get_chat_repo
    from .ontology import get_ontology_client

    chat_repo = await get_chat_repo()
    session = await chat_repo.get_builder_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    tenant_id = session["tenant_id"]
    db = await get_ontology_client()

    service = AgentBuilderService(db, tenant_id)
    response = await service.process_message(session_id, request.message)
    return response


@router.post("/sessions/{session_id}/stream", summary="Stream message to builder (SSE)")
async def builder_chat_stream(
    session_id: str,
    request: BuilderChatRequest,
):
    import json as _json
    from sse_starlette.sse import EventSourceResponse
    from .dependencies import get_chat_repo
    from .ontology import get_ontology_client

    chat_repo = await get_chat_repo()
    session = await chat_repo.get_builder_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    tenant_id = session["tenant_id"]
    db = await get_ontology_client()
    service = AgentBuilderService(db, tenant_id)

    async def event_generator():
        async for chunk in service.stream_message(session_id, request.message):
            yield {"data": _json.dumps(chunk, ensure_ascii=False)}

    return EventSourceResponse(event_generator())


@router.post("/sessions/{session_id}/upload-samples", response_model=SampleUploadResponse)
async def upload_sample_documents(
    session_id: str,
    files: List[UploadFile] = File(...),
    background_tasks: BackgroundTasks = None,
):
    from .dependencies import get_chat_repo
    chat_repo = await get_chat_repo()
    session = await chat_repo.get_builder_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    sample_ids = [f"sample-{i}" for i in range(len(files))]

    return SampleUploadResponse(
        sample_ids=sample_ids,
        analysis_started=True,
        message=f"{len(files)} dosya yüklendi. Analiz başlatılıyor...",
    )


@router.get("/sessions/{session_id}", response_model=BuilderSession)
async def get_session(
    session_id: str,
):
    from .dependencies import get_chat_repo
    chat_repo = await get_chat_repo()
    bs = await chat_repo.get_builder_session(session_id)
    if not bs:
        raise HTTPException(status_code=404, detail="Session not found")

    return BuilderSession(
        id=bs["id"],
        tenant_id=bs["tenant_id"],
        status=bs.get("status", "active"),
        current_state=bs.get("current_state", "goal_elicitation"),
        state_data=bs.get("state_data") or {},
    )


# =============================================================================
# GOAL ENDPOINTS
# =============================================================================

@router.get("/goals", response_model=List[GoalSummary], summary="List goals")
async def list_goals(
    tenant_id: str = Query(..., description="Tenant ID"),
    goal_type: Optional[GoalType] = Query(None, description="Goal tipi filtresi"),
    context: Optional[str] = Query(None, description="Context adı filtresi"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    from .dependencies import get_agent_repo
    from .event_store.postgres_client import get_postgres_client
    agent_repo = await get_agent_repo()
    pg = await get_postgres_client()

    sql = """
        SELECT id, name, description, goal_type
        FROM goals
        WHERE (tenant_id = $1 OR tenant_id IS NULL)
        ORDER BY name LIMIT $2 OFFSET $3
    """
    rows = await pg.fetch(sql, tenant_id, limit, offset)

    results = []
    for r in rows:
        gt = r.get("goal_type", "extraction")
        if goal_type and gt != goal_type.value:
            continue
        results.append(
            GoalSummary(
                id=r["id"], name=r["name"],
                description=r.get("description", ""),
                goal_type=GoalType(gt) if gt else GoalType.EXTRACTION,
            )
        )
    return results


@router.post("/goals", response_model=Goal, summary="Create goal")
async def create_goal(
    request: GoalCreate,
):
    from .dependencies import get_agent_repo
    agent_repo = await get_agent_repo()

    goal_id = await agent_repo.create_goal({
        "name": request.name,
        "description": request.description,
        "goal_type": request.goal_type.value,
        "natural_language_query": request.natural_language_query,
        "success_criteria": request.success_criteria,
        "tenant_id": request.tenant_id,
    })

    logger.info(f"Created goal: {goal_id}")

    return Goal(
        id=goal_id,
        name=request.name,
        description=request.description,
        goal_type=request.goal_type,
        natural_language_query=request.natural_language_query,
        success_criteria=request.success_criteria,
        status="active",
        tenant_id=request.tenant_id,
    )


@router.get("/goals/{goal_id}/skills", response_model=List[SkillSummary], summary="Get achievable skills")
async def get_achievable_skills(
    goal_id: str,
    tenant_id: str = Query(..., description="Tenant ID"),
    reasoner: OntologyReasoner = Depends(get_reasoner)
):
    """
    Goal için kullanılabilir skill'leri getir.
    
    Ontology reasoning ile en uygun skill'ler sıralanır.
    """
    scored_skills = await reasoner.find_skills_for_goal(
        goal_id=goal_id,
        tenant_id=tenant_id,
        include_global=True
    )
    
    return [
        SkillSummary(
            id=s.skill_id,
            name=s.name,
            description=s.description,
            category=SkillCategory(s.category),
            effectiveness_score=s.effectiveness,
            is_global=True  # TODO: Skill'den al
        )
        for s in scored_skills
    ]


# =============================================================================
# SKILL ENDPOINTS
# =============================================================================

@router.get("/skills", response_model=List[SkillSummary], summary="List skills")
async def list_skills(
    tenant_id: str = Query(..., description="Tenant ID"),
    category: Optional[SkillCategory] = Query(None, description="Kategori filtresi"),
    include_global: bool = Query(True, description="Global skill'leri dahil et"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    registry: SkillRegistry = Depends(get_skill_registry)
):
    """
    Tenant'ın kullanabildiği skill'leri listele.
    """
    return await registry.list_skills_for_tenant(
        tenant_id=tenant_id,
        include_global=include_global,
        category=category,
        limit=limit,
        offset=offset
    )


@router.post("/skills", response_model=Skill, summary="Create skill")
async def create_skill(
    request: SkillCreate,
    registry: SkillRegistry = Depends(get_skill_registry)
):
    """
    Yeni skill oluştur.
    """
    skill_id = await registry.create_skill(request)
    skill = await registry.get_skill(skill_id)
    
    if not skill:
        raise HTTPException(status_code=500, detail="Skill creation failed")
    
    return skill


@router.get("/skills/{skill_id}", response_model=Skill, summary="Get skill")
async def get_skill(
    skill_id: str,
    registry: SkillRegistry = Depends(get_skill_registry)
):
    """Skill bilgilerini getir"""
    skill = await registry.get_skill(skill_id)
    
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    
    return skill


@router.post("/skills/{skill_id}/test", response_model=SkillTestResult, summary="Test skill")
async def test_skill(
    skill_id: str,
    request: SkillTestRequest,
    registry: SkillRegistry = Depends(get_skill_registry)
):
    """
    Skill'i test belgelerinde çalıştır.
    
    Extraction sonuçlarını döndürür.
    """
    # Skill'i getir
    skill = await registry.get_skill_for_execution(skill_id)
    
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    
    # TODO: Agentic OCR entegrasyonu ile test et
    # Şimdilik mock sonuç
    
    return SkillTestResult(
        success=True,
        extracted_entities=[{"type": "Example", "value": "Test entity"}],
        extracted_relationships=[],
        errors=[],
        execution_time_ms=100
    )


@router.get(
    "/skills/{skill_id}/execution",
    summary="Get skill for execution",
    description="Agentic OCR'dan çağrılan endpoint. Skill'i execution formatında döndürür.",
)
async def get_skill_execution(
    skill_id: str = Path(..., description="Skill ID"),
):
    from .dependencies import get_agent_repo
    agent_repo = await get_agent_repo()
    registry = SkillRegistry(agent_repo)
    skill = await registry.get_skill_for_execution(skill_id)

    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")

    return {
        "skill_id": skill.skill_id,
        "name": skill.name,
        "category": skill.category,
        "prompt_template": skill.prompt_template,
        "input_schema": skill.input_schema,
        "output_schema": skill.output_schema,
        "entity_schemas": skill.entity_schemas,
        "relationship_schemas": skill.relationship_schemas,
        "version": getattr(skill, "version", 1),
        "effectiveness_score": getattr(skill, "effectiveness_score", 0.5),
    }


# =============================================================================
# AGENT ENDPOINTS
# =============================================================================

@router.get("/agents", response_model=List[AgentSummary], summary="List agents")
async def list_agents(
    tenant_id: str = Query(..., description="Tenant ID"),
    status: Optional[AgentStatus] = Query(None, description="Status filtresi"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    from .dependencies import get_agent_repo
    agent_repo = await get_agent_repo()
    results = await agent_repo.list_agents(tenant_id, limit=limit, offset=offset)

    return [
        AgentSummary(
            id=r["id"],
            name=r["name"],
            description=r.get("description", ""),
            status=AgentStatus(r.get("status", "draft")),
            skill_count=0,
            deployed=bool(r.get("mcp_virtual_server_id")),
        )
        for r in results
        if status is None or r.get("status") == status.value
    ]


@router.post("/agents", response_model=AgentDefinition, summary="Create agent")
async def create_agent(
    request: AgentCreate,
):
    import uuid
    from .dependencies import get_agent_repo
    agent_repo = await get_agent_repo()

    agent_id = f"agent-{uuid.uuid4().hex[:12]}"

    await agent_repo.create_agent({
        "id": agent_id,
        "name": request.name,
        "description": request.description,
        "purpose": request.purpose,
        "status": "draft",
        "tenant_id": request.tenant_id,
        "agent_type": "custom",
    })

    for goal_id in request.goal_ids:
        await agent_repo.link_agent_goal(agent_id, goal_id)

    for skill_id in request.skill_ids:
        await agent_repo.link_agent_skill(agent_id, skill_id)

    if request.context_id:
        await agent_repo.create_context({
            "name": "agent_context",
            "description": "",
            "agent_id": agent_id,
            "context_data": {},
        })

    logger.info(f"Created agent: {agent_id}")

    return AgentDefinition(
        id=agent_id,
        name=request.name,
        description=request.description,
        purpose=request.purpose,
        status=AgentStatus.DRAFT,
        tenant_id=request.tenant_id,
    )


@router.get("/agents/{agent_id}", response_model=AgentWithDetails, summary="Get agent details")
async def get_agent(
    agent_id: str,
):
    """Agent detaylarını getir (goals, skills, schemas dahil)"""
    from .dependencies import get_agent_repo
    agent_repo = await get_agent_repo()

    a = await agent_repo.get_agent(agent_id)
    if not a:
        raise HTTPException(status_code=404, detail="Agent not found")

    skills_list, entity_schemas, rel_schemas = await agent_repo.get_agent_skills_full(agent_id)

    return AgentWithDetails(
        id=a["id"],
        name=a["name"],
        description=a.get("description", ""),
        purpose=a.get("purpose", ""),
        status=AgentStatus(a.get("status", "draft")),
        tenant_id=a.get("tenant_id", "default"),
        mcp_virtual_server_id=a.get("mcp_virtual_server_id"),
        goals=[],
        skills=[
            SkillSummary(
                id=s["id"], name=s["name"],
                description=s.get("description", ""),
                category=s.get("skill_category", ""),
                effectiveness_score=s.get("effectiveness_score", 0),
                is_global=s.get("is_global", False),
            )
            for s in skills_list
        ],
        entity_schemas=[
            EntitySchemaSummary(
                id=e["id"], entity_type=e["entity_type"],
                description=e.get("description", ""), property_count=0,
            )
            for e in entity_schemas
        ],
    )


@router.post("/agents/{agent_id}/deploy", response_model=DeploymentResult, summary="Deploy agent")
async def deploy_agent(
    agent_id: str,
):
    from .dependencies import get_agent_repo
    agent_repo = await get_agent_repo()

    a = await agent_repo.get_agent(agent_id)
    if not a:
        raise HTTPException(status_code=404, detail="Agent not found")

    if a.get("mcp_virtual_server_id"):
        return DeploymentResult(
            success=True,
            agent_id=agent_id,
            mcp_virtual_server_id=a["mcp_virtual_server_id"],
            endpoint_url=f"/api/v2/agents/{agent_id}/process",
            message="Agent zaten deploy edilmiş.",
        )

    vs_id = f"vs-{agent_id[-12:]}"

    await agent_repo.update_agent(agent_id, {
        "status": "active",
        "mcp_virtual_server_id": vs_id,
    })

    logger.info(f"Deployed agent: {agent_id} -> {vs_id}")

    return DeploymentResult(
        success=True,
        agent_id=agent_id,
        mcp_virtual_server_id=vs_id,
        endpoint_url=f"/api/v2/agents/{agent_id}/process",
        message="Agent başarıyla deploy edildi.",
    )


@router.post("/agents/{agent_id}/process", response_model=ProcessResult, summary="Process with agent")
async def process_with_agent(
    agent_id: str,
    request: ProcessRequest,
    background_tasks: BackgroundTasks,
):
    import os
    import uuid
    from .dependencies import get_agent_repo
    agent_repo = await get_agent_repo()

    a = await agent_repo.get_agent(agent_id)
    if not a or a.get("status") != "active":
        raise HTTPException(
            status_code=400,
            detail="Agent not found or not active. Deploy the agent first.",
        )

    skills_list, _, _ = await agent_repo.get_agent_skills_full(agent_id)
    ocr_skills = [s for s in skills_list if s.get("skill_category") in ("ocr", "extraction")]

    if not ocr_skills:
        raise HTTPException(
            status_code=400,
            detail="Agent has no OCR/extraction skills. Add skills first.",
        )

    primary_skill = ocr_skills[0]
    skill_id = primary_skill["id"]
    tenant_id = a.get("tenant_id", "default")

    task_ids = []
    try:
        from celery import Celery

        celery_broker = os.getenv("CELERY_BROKER_URL", "amqp://rabbitmq:RabbitMQ!654*@localhost:5672//")
        celery_app = Celery("agent_builder", broker=celery_broker)

        for file_id in request.file_ids:
            result = celery_app.send_task(
                "celery_worker.src.tasks.skill_processing.process_file_with_skill",
                args=[file_id, skill_id, tenant_id],
            )
            task_ids.append(result.id)
            logger.info(f"Queued task {result.id} for file {file_id} with skill {skill_id}")

        group_task_id = f"group-{uuid.uuid4().hex[:12]}"
        logger.info(f"Started processing with agent {agent_id}, group: {group_task_id}, tasks: {len(task_ids)}")

        return ProcessResult(
            task_id=group_task_id,
            status="queued",
            message=f"{len(request.file_ids)} dosya kuyruğa alındı. Skill: {primary_skill['name']}",
        )

    except Exception as e:
        logger.error(f"Failed to queue tasks: {e}")
        fallback_task_id = f"task-{uuid.uuid4().hex[:12]}"
        return ProcessResult(
            task_id=fallback_task_id,
            status="pending",
            message="Task kuyruğa alınamadı (Celery bağlantı hatası).",
        )


def _parse_rich_parts(text: str) -> list[RichMessagePart]:
    """Agent yanitindaki [RICH: ...] isaretlerini parse eder."""
    import re
    parts: list[RichMessagePart] = []
    pattern = re.compile(r'\[RICH:\s*(\w+)(?:\(([^)]*)\))?\]')
    last_end = 0

    for m in pattern.finditer(text):
        if m.start() > last_end:
            txt = text[last_end:m.start()].strip()
            if txt:
                parts.append(RichMessagePart(type="text", content=txt))

        rich_type = m.group(1)
        params_raw = m.group(2) or ""

        meta: dict = {}
        if rich_type == "action_buttons" and params_raw:
            meta["buttons"] = [b.strip() for b in params_raw.split(",")]
        elif rich_type == "suggestion" and params_raw:
            meta["suggestions"] = [s.strip() for s in params_raw.split(",")]
        elif rich_type == "card" and params_raw:
            card_parts = params_raw.split("|", 1)
            meta["title"] = card_parts[0].strip()
            meta["body"] = card_parts[1].strip() if len(card_parts) > 1 else ""
        elif rich_type == "progress" and params_raw:
            p = params_raw.split("|", 1)
            meta["percent"] = int(p[0].strip()) if p[0].strip().isdigit() else 0
            meta["label"] = p[1].strip() if len(p) > 1 else ""
        elif rich_type == "status" and params_raw:
            p = params_raw.split("|", 1)
            meta["status"] = p[0].strip()
            meta["label"] = p[1].strip() if len(p) > 1 else ""

        parts.append(RichMessagePart(type=rich_type, content=params_raw, metadata=meta))
        last_end = m.end()

    if last_end < len(text):
        txt = text[last_end:].strip()
        if txt:
            parts.append(RichMessagePart(type="text", content=txt))

    return parts


# =============================================================================
# AGENT CHAT ENDPOINTS (Runtime)
# =============================================================================

@router.post("/agents/{agent_id}/chat", summary="Chat with a created agent")
async def agent_chat(
    agent_id: str,
    request: AgentChatRequest,
):
    from .agent.agent_runtime import AgentRuntime
    from .agent.ocr_bridge import OCRBridge
    from .gateway import get_gateway_client
    from .dependencies import get_chat_repo, get_agent_repo
    from .ontology import get_ontology_client

    try:
        gateway = await get_gateway_client()
    except Exception:
        gateway = None

    db = await get_ontology_client()
    chat_repo = await get_chat_repo()
    agent_repo = await get_agent_repo()

    runtime = AgentRuntime(
        db, gateway, OCRBridge(),
        chat_repo=chat_repo, agent_repo=agent_repo,
    )
    session = await runtime.start_agent(
        agent_id=agent_id,
        session_id=request.session_id or None,
    )

    full_response = ""
    async for chunk in runtime.chat(session.session_id, request.message):
        if chunk.get("type") == "final_response":
            full_response = chunk.get("content", "")
        elif chunk.get("type") == "message_chunk":
            full_response += chunk.get("content", "")

    rich_parts = _parse_rich_parts(full_response)
    import re
    clean_text = re.sub(r'\[RICH:\s*\w+(?:\([^)]*\))?\]', '', full_response).strip()

    return AgentChatResponse(
        response=clean_text,
        session_id=session.session_id,
        agent_id=agent_id,
        rich_parts=rich_parts,
    )


@router.get("/agents/{agent_id}/chat/stream", summary="Stream chat with agent (SSE)")
async def agent_chat_stream(
    agent_id: str,
    message: str = Query(..., description="User message"),
    session_id: str = Query(default="", description="Session ID for continuing chat"),
):
    import json as _json
    from sse_starlette.sse import EventSourceResponse
    from .agent.agent_runtime import AgentRuntime
    from .agent.ocr_bridge import OCRBridge
    from .gateway import get_gateway_client
    from .dependencies import get_chat_repo, get_agent_repo
    from .ontology import get_ontology_client

    try:
        gateway = await get_gateway_client()
    except Exception:
        gateway = None

    db = await get_ontology_client()
    chat_repo = await get_chat_repo()
    agent_repo = await get_agent_repo()

    runtime = AgentRuntime(
        db, gateway, OCRBridge(),
        chat_repo=chat_repo, agent_repo=agent_repo,
    )
    session = await runtime.start_agent(
        agent_id=agent_id,
        session_id=session_id or None,
    )

    async def event_generator():
        async for chunk in runtime.chat(session.session_id, message):
            yield {"data": _json.dumps(chunk, ensure_ascii=False)}

    return EventSourceResponse(event_generator())


@router.get("/agents/{agent_id}/chat/history", summary="Get chat history")
async def agent_chat_history(
    agent_id: str,
    session_id: str = Query(..., description="Session ID"),
    limit: int = Query(default=100, le=500),
):
    from .dependencies import get_chat_repo
    chat_repo = await get_chat_repo()
    messages = await chat_repo.get_messages(session_id, limit=limit)

    return {
        "session_id": session_id,
        "agent_id": agent_id,
        "messages": [
            {
                "role": m["role"],
                "content": m["content"],
                "created_at": str(m.get("created_at", "")) if m.get("created_at") else None,
            }
            for m in messages
        ],
        "count": len(messages),
    }


# =============================================================================
# ONTOLOGY ENDPOINTS
# =============================================================================

@router.get("/ontology/entity-schemas", response_model=List[EntitySchemaSummary], summary="List entity schemas")
async def list_entity_schemas(
    context: Optional[str] = Query(None, description="Context adı filtresi"),
    limit: int = Query(50, ge=1, le=200),
):
    from .event_store.postgres_client import get_postgres_client
    pg = await get_postgres_client()

    sql = "SELECT id, entity_type, description, properties FROM entity_schemas ORDER BY entity_type LIMIT $1"
    rows = await pg.fetch(sql, limit)

    return [
        EntitySchemaSummary(
            id=r["id"], entity_type=r["entity_type"],
            description=r.get("description", ""),
            property_count=len(r["properties"]) if isinstance(r.get("properties"), dict) else 0,
        )
        for r in rows
    ]


@router.get("/ontology/relationship-schemas", response_model=List[RelationshipSchemaSummary])
async def list_relationship_schemas(
    limit: int = Query(50, ge=1, le=200),
):
    from .event_store.postgres_client import get_postgres_client
    pg = await get_postgres_client()

    sql = "SELECT id, relationship_type, source_entity, target_entity FROM relationship_schemas ORDER BY relationship_type LIMIT $1"
    rows = await pg.fetch(sql, limit)

    return [
        RelationshipSchemaSummary(
            id=r["id"], relationship_type=r["relationship_type"],
            source_entity=r["source_entity"], target_entity=r["target_entity"],
        )
        for r in rows
    ]


@router.get("/ontology/contexts", response_model=List[Context], summary="List contexts")
async def list_contexts():
    from .event_store.postgres_client import get_postgres_client
    import json as _json

    pg = await get_postgres_client()
    sql = "SELECT id, name, description, context_data FROM contexts ORDER BY name"
    rows = await pg.fetch(sql)

    return [
        Context(
            id=r["id"], name=r["name"],
            description=r.get("description", ""),
            domain_keywords=r.get("context_data", {}).get("domain_keywords", []) if isinstance(r.get("context_data"), dict) else [],
            parent_context_id=None,
        )
        for r in rows
    ]


@router.post("/ontology/suggest-schema", response_model=SchemaProposalResponse, summary="Suggest schema")
async def suggest_schema_from_samples(
    sample_ids: List[str],
    context: str = Query(..., description="Context adı"),
    reasoner: OntologyReasoner = Depends(get_reasoner)
):
    """
    Örnek belgelerden schema öner.
    
    Yüklenen örnek belgelerin analizine dayanarak
    entity ve relationship schema önerisi yapar.
    """
    # TODO: Sample analiz sonuçlarını getir
    # Şimdilik mock analiz sonucu
    sample_analysis = {
        "detected_entities": ["Policy", "Customer"],
        "detected_fields": {
            "Policy": ["policy_no", "start_date", "end_date"],
            "Customer": ["name", "tc_no"]
        },
        "detected_relationships": [("Customer", "HAS_POLICY", "Policy")]
    }
    
    proposal = await reasoner.suggest_schema_from_analysis(sample_analysis, context)
    
    return SchemaProposalResponse(
        entities=proposal.entities,
        relationships=proposal.relationships,
        confidence=proposal.confidence,
        based_on=proposal.based_on,
        reasoning=proposal.reasoning
    )


# =============================================================================
# VERSIONING / EVENT STORE ENDPOINTS
# =============================================================================

@router.get("/events", summary="List graph mutation events")
async def list_events(
    tenant_id: str = Query(..., description="Tenant ID"),
    entity_id: Optional[str] = Query(None),
    entity_type: Optional[str] = Query(None),
    user_id: Optional[str] = Query(None),
    session_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """
    Query the immutable event log.
    Every graph mutation (create, update, delete) is recorded here.
    """
    pg = await get_postgres_client()
    store = EventStore(pg)

    events = await store.get_events(EventFilter(
        tenant_id=tenant_id,
        entity_id=entity_id,
        entity_type=entity_type,
        user_id=user_id,
        session_id=session_id,
        limit=limit,
        offset=offset,
    ))

    return {
        "count": len(events),
        "events": [e.model_dump(mode="json") for e in events],
    }


@router.get("/events/{entity_id}/history", summary="Entity mutation history")
async def entity_history(
    entity_id: str,
    tenant_id: str = Query(...),
    limit: int = Query(100, ge=1, le=500),
):
    """Full mutation history for a single entity."""
    pg = await get_postgres_client()
    store = EventStore(pg)
    events = await store.get_entity_history(tenant_id, entity_id, limit)
    return {
        "entity_id": entity_id,
        "count": len(events),
        "events": [e.model_dump(mode="json") for e in events],
    }


class RollbackRequest(BaseModel):
    tenant_id: str
    steps: Optional[int] = None
    snapshot_name: Optional[str] = None
    timestamp: Optional[str] = None
    user_id: Optional[str] = None


@router.post("/rollback", summary="Rollback graph changes")
async def rollback(
    request: RollbackRequest,
    db: OntologyDBClient = Depends(get_ontology_db),
):
    """
    Undo graph mutations by producing compensating events.
    Provide exactly one of: steps, snapshot_name, or timestamp.
    """
    pg = await get_postgres_client()
    store = EventStore(pg)
    mgr = RollbackManager(db, store, request.tenant_id, request.user_id)

    if request.steps:
        comps = await mgr.rollback_steps(request.steps)
    elif request.snapshot_name:
        comps = await mgr.rollback_to_snapshot(request.snapshot_name)
    elif request.timestamp:
        from datetime import datetime as dt
        ts = dt.fromisoformat(request.timestamp)
        comps = await mgr.rollback_to_timestamp(ts)
    else:
        raise HTTPException(400, "Provide steps, snapshot_name, or timestamp")

    return {
        "rolled_back": len(comps),
        "compensations": [c.model_dump(mode="json") for c in comps],
    }


class SnapshotCreate(BaseModel):
    tenant_id: str
    name: str
    description: Optional[str] = None
    created_by: Optional[str] = None


@router.post("/snapshots", summary="Create a named snapshot")
async def create_snapshot(request: SnapshotCreate):
    """
    Bookmark the current event position for easy rollback later.
    """
    pg = await get_postgres_client()
    store = EventStore(pg)
    snap = await store.create_snapshot(
        request.tenant_id, request.name,
        request.description, request.created_by,
    )
    return snap.model_dump(mode="json")


@router.get("/snapshots", summary="List named snapshots")
async def list_snapshots(
    tenant_id: str = Query(...),
):
    pg = await get_postgres_client()
    store = EventStore(pg)
    snaps = await store.list_snapshots(tenant_id)
    return [s.model_dump(mode="json") for s in snaps]


@router.post("/snapshots/{name}/restore", summary="Restore to snapshot")
async def restore_snapshot(
    name: str,
    tenant_id: str = Query(...),
    user_id: Optional[str] = Query(None),
    db: OntologyDBClient = Depends(get_ontology_db),
):
    """Rollback all changes made after the named snapshot."""
    pg = await get_postgres_client()
    store = EventStore(pg)
    mgr = RollbackManager(db, store, tenant_id, user_id)
    comps = await mgr.rollback_to_snapshot(name)
    return {
        "snapshot": name,
        "rolled_back": len(comps),
        "compensations": [c.model_dump(mode="json") for c in comps],
    }


@router.get("/diff", summary="Diff between two timestamps")
async def diff(
    tenant_id: str = Query(...),
    time_a: str = Query(..., description="ISO timestamp (start)"),
    time_b: str = Query(..., description="ISO timestamp (end)"),
):
    """Compare graph state between two points in time."""
    from datetime import datetime as dt
    pg = await get_postgres_client()
    audit = AuditTrail(EventStore(pg), pg)
    return await audit.diff(tenant_id, dt.fromisoformat(time_a), dt.fromisoformat(time_b))


@router.get("/events/stats", summary="Event store statistics")
async def event_stats(
    tenant_id: str = Query(...),
):
    pg = await get_postgres_client()
    audit = AuditTrail(EventStore(pg), pg)
    return await audit.get_event_stats(tenant_id)


# =============================================================================
# HEALTH CHECK
# =============================================================================

@router.get("/health", summary="Health check")
async def health_check(
    db: OntologyDBClient = Depends(get_ontology_db)
):
    """Agent Builder API sağlık kontrolü"""
    health = await db.health_check()

    pg_health = {}
    try:
        pg = await get_postgres_client()
        pg_health = await pg.health_check()
    except Exception as e:
        pg_health = {"status": "unavailable", "error": str(e)}

    return {
        "status": "healthy" if health["status"] == "healthy" else "unhealthy",
        "ontology_db": health,
        "event_store": pg_health,
        "version": "0.2.0"
    }
