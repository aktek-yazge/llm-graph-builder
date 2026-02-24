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


async def get_skill_registry(db: OntologyDBClient = Depends(get_ontology_db)) -> SkillRegistry:
    """Skill Registry dependency"""
    return SkillRegistry(db)


# =============================================================================
# SESSION ENDPOINTS
# =============================================================================

@router.post("/sessions", response_model=BuilderSession, summary="Create builder session")
async def create_builder_session(
    request: BuilderSessionCreate,
    db: OntologyDBClient = Depends(get_ontology_db)
):
    """
    Yeni Agent Builder conversation session'ı oluştur.
    
    Session, kullanıcı ile builder agent arasındaki konuşmayı yönetir.
    Goal-driven workflow ile agent oluşturma sürecini takip eder.
    """
    import uuid
    
    session_id = f"session-{uuid.uuid4().hex[:12]}"
    
    query = """
    CREATE (bs:BuilderSession {
        id: $session_id,
        tenant_id: $tenant_id,
        status: 'active',
        current_state: 'goal_elicitation',
        state_data: '{}',
        messages: '[]',
        created_at: datetime(),
        updated_at: datetime()
    })
    RETURN bs
    """
    
    result = await db.execute_query(query, {
        "session_id": session_id,
        "tenant_id": request.tenant_id
    }, write=True)
    
    if not result:
        raise HTTPException(status_code=500, detail="Failed to create session")
    
    logger.info(f"Created builder session: {session_id} for tenant: {request.tenant_id}")
    
    return BuilderSession(
        id=session_id,
        tenant_id=request.tenant_id,
        status="active",
        current_state="goal_elicitation",
        state_data={}
    )


@router.post("/sessions/{session_id}/message", summary="Send message to builder")
async def builder_chat(
    session_id: str,
    request: BuilderChatRequest,
    db: OntologyDBClient = Depends(get_ontology_db),
    reasoner: OntologyReasoner = Depends(get_reasoner)
):
    """
    Builder agent'a mesaj gönder.
    
    Conversation state machine kullanarak mesajı işler.
    State-specific handler'lar çalışır ve yanıt döner.
    
    Returns:
        BuilderChatResponse
    """
    # Session'ı getir ve tenant kontrolü yap
    session_query = """
    MATCH (bs:BuilderSession {id: $session_id})
    RETURN bs.tenant_id as tenant_id
    """
    
    result = await db.execute_query(session_query, {"session_id": session_id})
    
    if not result:
        raise HTTPException(status_code=404, detail="Session not found")
    
    tenant_id = result[0]["tenant_id"]
    
    # Conversation state machine ile işle
    state_machine = ConversationStateMachine(db, reasoner)
    response = await state_machine.process_message(session_id, request.message)
    
    return response


@router.post("/sessions/{session_id}/upload-samples", response_model=SampleUploadResponse)
async def upload_sample_documents(
    session_id: str,
    files: List[UploadFile] = File(...),
    background_tasks: BackgroundTasks = None,
    db: OntologyDBClient = Depends(get_ontology_db)
):
    """
    Örnek belgeler yükle.
    
    Yüklenen belgeler Gemini OCR ile analiz edilir.
    Analiz sonuçları schema önerisi için kullanılır.
    """
    # Session kontrolü
    session_query = """
    MATCH (bs:BuilderSession {id: $session_id})
    RETURN bs.tenant_id as tenant_id
    """
    
    result = await db.execute_query(session_query, {"session_id": session_id})
    
    if not result:
        raise HTTPException(status_code=404, detail="Session not found")
    
    # TODO: Dosyaları kaydet ve analiz başlat
    sample_ids = [f"sample-{i}" for i in range(len(files))]
    
    return SampleUploadResponse(
        sample_ids=sample_ids,
        analysis_started=True,
        message=f"{len(files)} dosya yüklendi. Analiz başlatılıyor..."
    )


@router.get("/sessions/{session_id}", response_model=BuilderSession)
async def get_session(
    session_id: str,
    db: OntologyDBClient = Depends(get_ontology_db)
):
    """Session bilgilerini getir"""
    query = """
    MATCH (bs:BuilderSession {id: $session_id})
    RETURN bs
    """
    
    result = await db.execute_query(query, {"session_id": session_id})
    
    if not result:
        raise HTTPException(status_code=404, detail="Session not found")
    
    bs = result[0]["bs"]
    
    return BuilderSession(
        id=bs["id"],
        tenant_id=bs["tenant_id"],
        status=bs.get("status", "active"),
        current_state=bs.get("current_state", "goal_elicitation"),
        state_data={}
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
    db: OntologyDBClient = Depends(get_ontology_db)
):
    """
    Tenant'ın goal'lerini listele.
    
    Global goal'ler ve tenant'a özel goal'ler döner.
    """
    query = """
    MATCH (g:Goal)
    WHERE g.tenant_id = $tenant_id OR g.tenant_id IS NULL
    
    WITH g
    WHERE ($goal_type IS NULL OR g.goal_type = $goal_type)
    
    OPTIONAL MATCH (g)-[:APPLIED_IN]->(c:Context)
    WHERE $context IS NULL OR c.name = $context
    
    RETURN g.id as id,
           g.name as name,
           g.description as description,
           g.goal_type as goal_type
    ORDER BY g.name
    SKIP $offset
    LIMIT $limit
    """
    
    results = await db.execute_query(query, {
        "tenant_id": tenant_id,
        "goal_type": goal_type.value if goal_type else None,
        "context": context,
        "limit": limit,
        "offset": offset
    })
    
    return [
        GoalSummary(
            id=r["id"],
            name=r["name"],
            description=r["description"],
            goal_type=GoalType(r["goal_type"]) if r["goal_type"] else GoalType.EXTRACTION
        )
        for r in results
    ]


@router.post("/goals", response_model=Goal, summary="Create goal")
async def create_goal(
    request: GoalCreate,
    db: OntologyDBClient = Depends(get_ontology_db)
):
    """
    Yeni goal oluştur.
    """
    import uuid
    
    goal_id = f"goal-{uuid.uuid4().hex[:12]}"
    
    query = """
    CREATE (g:Goal {
        id: $goal_id,
        name: $name,
        description: $description,
        goal_type: $goal_type,
        natural_language_query: $natural_language_query,
        success_criteria: $success_criteria,
        status: 'active',
        tenant_id: $tenant_id,
        created_at: datetime()
    })
    RETURN g
    """
    
    import json
    
    result = await db.execute_query(query, {
        "goal_id": goal_id,
        "name": request.name,
        "description": request.description,
        "goal_type": request.goal_type.value,
        "natural_language_query": request.natural_language_query,
        "success_criteria": json.dumps(request.success_criteria) if request.success_criteria else None,
        "tenant_id": request.tenant_id
    }, write=True)
    
    # Context'e bağla
    if request.context_id:
        await db.execute_query("""
            MATCH (g:Goal {id: $goal_id}), (c:Context {id: $context_id})
            MERGE (g)-[:APPLIED_IN]->(c)
        """, {"goal_id": goal_id, "context_id": request.context_id}, write=True)
    
    # Parent goal'e bağla (sub-goal ise)
    if request.parent_goal_id:
        await db.execute_query("""
            MATCH (g:Goal {id: $goal_id}), (pg:Goal {id: $parent_goal_id})
            MERGE (pg)-[:REQUIRES {order: 0}]->(g)
        """, {"goal_id": goal_id, "parent_goal_id": request.parent_goal_id}, write=True)
    
    logger.info(f"Created goal: {goal_id}")
    
    return Goal(
        id=goal_id,
        name=request.name,
        description=request.description,
        goal_type=request.goal_type,
        natural_language_query=request.natural_language_query,
        success_criteria=request.success_criteria,
        status="active",
        tenant_id=request.tenant_id
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
    description="Agentic OCR'dan çağrılan endpoint. Skill'i execution formatında döndürür."
)
async def get_skill_execution(
    skill_id: str = Path(..., description="Skill ID"),
    db: OntologyDBClient = Depends(get_ontology_db)
):
    """
    Celery worker'dan çağrılan endpoint.
    Skill'i Agentic OCR'un kullanabileceği formatta döndürür.
    
    Response format:
    {
        "skill_id": "...",
        "name": "...",
        "category": "...",
        "prompt_template": "...",
        "input_schema": {...},
        "output_schema": {...},
        "entity_schemas": [...],
        "relationship_schemas": [...],
        "version": 1,
        "effectiveness_score": 0.5
    }
    """
    registry = SkillRegistry(db)
    skill = await registry.get_skill_for_execution(skill_id)
    
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")
    
    # Skill bilgilerini al (version ve effectiveness_score için)
    skill_detail = await registry.get_skill_by_id(skill_id)
    
    return {
        "skill_id": skill.skill_id,
        "name": skill.name,
        "category": skill.category,
        "prompt_template": skill.prompt_template,
        "input_schema": skill.input_schema,
        "output_schema": skill.output_schema,
        "entity_schemas": skill.entity_schemas,
        "relationship_schemas": skill.relationship_schemas,
        "version": skill_detail.version if skill_detail else 1,
        "effectiveness_score": skill_detail.effectiveness_score if skill_detail else 0.5
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
    db: OntologyDBClient = Depends(get_ontology_db)
):
    """
    Tenant'ın agent'larını listele.
    """
    query = """
    MATCH (a:AgentDefinition)
    WHERE a.tenant_id = $tenant_id
      AND ($status IS NULL OR a.status = $status)
    
    OPTIONAL MATCH (a)-[:HAS_SKILL]->(s:Skill)
    
    WITH a, count(s) as skill_count
    
    RETURN a.id as id,
           a.name as name,
           a.description as description,
           a.status as status,
           skill_count,
           a.mcp_virtual_server_id IS NOT NULL as deployed
    ORDER BY a.created_at DESC
    SKIP $offset
    LIMIT $limit
    """
    
    results = await db.execute_query(query, {
        "tenant_id": tenant_id,
        "status": status.value if status else None,
        "limit": limit,
        "offset": offset
    })
    
    return [
        AgentSummary(
            id=r["id"],
            name=r["name"],
            description=r["description"],
            status=AgentStatus(r["status"]) if r["status"] else AgentStatus.DRAFT,
            skill_count=r["skill_count"] or 0,
            deployed=r["deployed"] or False
        )
        for r in results
    ]


@router.post("/agents", response_model=AgentDefinition, summary="Create agent")
async def create_agent(
    request: AgentCreate,
    db: OntologyDBClient = Depends(get_ontology_db)
):
    """
    Yeni agent oluştur.
    """
    import uuid
    
    agent_id = f"agent-{uuid.uuid4().hex[:12]}"
    
    query = """
    CREATE (a:AgentDefinition {
        id: $agent_id,
        name: $name,
        description: $description,
        purpose: $purpose,
        status: 'draft',
        tenant_id: $tenant_id,
        created_at: datetime()
    })
    RETURN a
    """
    
    await db.execute_query(query, {
        "agent_id": agent_id,
        "name": request.name,
        "description": request.description,
        "purpose": request.purpose,
        "tenant_id": request.tenant_id
    }, write=True)
    
    # Goal'lere bağla
    for goal_id in request.goal_ids:
        await db.execute_query("""
            MATCH (a:AgentDefinition {id: $agent_id}), (g:Goal {id: $goal_id})
            MERGE (a)-[:PURSUES]->(g)
        """, {"agent_id": agent_id, "goal_id": goal_id}, write=True)
    
    # Skill'lere bağla
    for skill_id in request.skill_ids:
        await db.execute_query("""
            MATCH (a:AgentDefinition {id: $agent_id}), (s:Skill {id: $skill_id})
            MERGE (a)-[:HAS_SKILL {assigned_at: datetime(), enabled: true}]->(s)
        """, {"agent_id": agent_id, "skill_id": skill_id}, write=True)
    
    # Context'e bağla
    if request.context_id:
        await db.execute_query("""
            MATCH (a:AgentDefinition {id: $agent_id}), (c:Context {id: $context_id})
            MERGE (a)-[:OPERATES_IN]->(c)
        """, {"agent_id": agent_id, "context_id": request.context_id}, write=True)
    
    logger.info(f"Created agent: {agent_id}")
    
    return AgentDefinition(
        id=agent_id,
        name=request.name,
        description=request.description,
        purpose=request.purpose,
        status=AgentStatus.DRAFT,
        tenant_id=request.tenant_id
    )


@router.get("/agents/{agent_id}", response_model=AgentWithDetails, summary="Get agent details")
async def get_agent(
    agent_id: str,
    db: OntologyDBClient = Depends(get_ontology_db)
):
    """Agent detaylarını getir (goals, skills, schemas dahil)"""
    query = """
    MATCH (a:AgentDefinition {id: $agent_id})
    
    OPTIONAL MATCH (a)-[:PURSUES]->(g:Goal)
    WITH a, collect(DISTINCT {id: g.id, name: g.name, description: g.description, goal_type: g.goal_type}) as goals
    
    OPTIONAL MATCH (a)-[:HAS_SKILL]->(s:Skill)
    WITH a, goals, collect(DISTINCT {id: s.id, name: s.name, description: s.description, category: s.skill_category, effectiveness_score: s.effectiveness_score, is_global: s.is_global}) as skills
    
    OPTIONAL MATCH (a)-[:USES_SCHEMA]->(e:EntitySchema)
    WITH a, goals, skills, collect(DISTINCT {id: e.id, entity_type: e.entity_type, description: e.description}) as schemas
    
    RETURN a, goals, skills, schemas
    """
    
    results = await db.execute_query(query, {"agent_id": agent_id})
    
    if not results:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    r = results[0]
    a = r["a"]
    
    return AgentWithDetails(
        id=a["id"],
        name=a["name"],
        description=a["description"],
        purpose=a["purpose"],
        status=AgentStatus(a["status"]) if a.get("status") else AgentStatus.DRAFT,
        tenant_id=a["tenant_id"],
        mcp_virtual_server_id=a.get("mcp_virtual_server_id"),
        goals=[GoalSummary(**g) for g in r["goals"] if g.get("id")],
        skills=[SkillSummary(**s) for s in r["skills"] if s.get("id")],
        entity_schemas=[EntitySchemaSummary(id=e["id"], entity_type=e["entity_type"], description=e["description"], property_count=0) for e in r["schemas"] if e.get("id")]
    )


@router.post("/agents/{agent_id}/deploy", response_model=DeploymentResult, summary="Deploy agent")
async def deploy_agent(
    agent_id: str,
    db: OntologyDBClient = Depends(get_ontology_db)
):
    """
    Agent'ı MCP Gateway'e deploy et.
    
    Virtual server oluşturur ve skill'leri tool olarak kaydeder.
    """
    # Agent'ı getir
    query = """
    MATCH (a:AgentDefinition {id: $agent_id})
    RETURN a.status as status, a.mcp_virtual_server_id as vs_id
    """
    
    result = await db.execute_query(query, {"agent_id": agent_id})
    
    if not result:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    # Zaten deploy edilmişse
    if result[0].get("vs_id"):
        return DeploymentResult(
            success=True,
            agent_id=agent_id,
            mcp_virtual_server_id=result[0]["vs_id"],
            endpoint_url=f"/api/v2/agents/{agent_id}/process",
            message="Agent zaten deploy edilmiş."
        )
    
    # TODO: MCP Gateway API entegrasyonu
    # Gateway'e virtual server oluşturma isteği at
    
    # Şimdilik mock virtual server ID
    vs_id = f"vs-{agent_id[-12:]}"
    
    # Agent'ı güncelle
    await db.execute_query("""
        MATCH (a:AgentDefinition {id: $agent_id})
        SET a.status = 'active',
            a.mcp_virtual_server_id = $vs_id,
            a.deployed_at = datetime()
    """, {"agent_id": agent_id, "vs_id": vs_id}, write=True)
    
    logger.info(f"Deployed agent: {agent_id} -> {vs_id}")
    
    return DeploymentResult(
        success=True,
        agent_id=agent_id,
        mcp_virtual_server_id=vs_id,
        endpoint_url=f"/api/v2/agents/{agent_id}/process",
        message="Agent başarıyla deploy edildi."
    )


@router.post("/agents/{agent_id}/process", response_model=ProcessResult, summary="Process with agent")
async def process_with_agent(
    agent_id: str,
    request: ProcessRequest,
    background_tasks: BackgroundTasks,
    db: OntologyDBClient = Depends(get_ontology_db)
):
    """
    Agent ile belgeleri işle.
    
    Agent'ın skill'lerini alır ve her dosya için Celery task başlatır.
    Celery task agentic_ocr.process(skill_id=...) çağırır.
    """
    import os
    import uuid
    
    # Agent ve skill'lerini al
    query = """
    MATCH (a:AgentDefinition {id: $agent_id})
    WHERE a.status = 'active'
    
    OPTIONAL MATCH (a)-[:HAS_SKILL]->(s:Skill)
    WHERE s.skill_category IN ['ocr', 'extraction']
    
    WITH a, collect(s) as skills
    
    RETURN a.id as agent_id,
           a.tenant_id as tenant_id,
           [s IN skills | {id: s.id, name: s.name, category: s.skill_category}] as skills
    """
    
    result = await db.execute_query(query, {"agent_id": agent_id})
    
    if not result:
        raise HTTPException(
            status_code=400, 
            detail="Agent not found or not active. Deploy the agent first."
        )
    
    agent_data = result[0]
    skills = agent_data.get("skills", [])
    tenant_id = agent_data.get("tenant_id")
    
    if not skills:
        raise HTTPException(
            status_code=400,
            detail="Agent has no OCR/extraction skills. Add skills first."
        )
    
    # Primary skill (ilk OCR veya extraction skill)
    primary_skill = skills[0]
    skill_id = primary_skill["id"]
    
    # Celery task başlat
    task_ids = []
    
    try:
        from celery import Celery
        
        celery_broker = os.getenv("CELERY_BROKER_URL", "amqp://guest:guest@localhost:5672//")
        celery_app = Celery("agent_builder", broker=celery_broker)
        
        # Her dosya için task başlat
        for file_id in request.file_ids:
            result = celery_app.send_task(
                "celery_worker.src.tasks.skill_processing.process_file_with_skill",
                args=[file_id, skill_id, tenant_id]
            )
            task_ids.append(result.id)
            logger.info(f"Queued task {result.id} for file {file_id} with skill {skill_id}")
        
        # Grup task ID'si oluştur
        group_task_id = f"group-{uuid.uuid4().hex[:12]}"
        
        logger.info(f"Started processing with agent {agent_id}, group: {group_task_id}, tasks: {len(task_ids)}")
        
        return ProcessResult(
            task_id=group_task_id,
            status="queued",
            message=f"{len(request.file_ids)} dosya işlenmek üzere kuyruğa alındı. Skill: {primary_skill['name']}"
        )
        
    except Exception as e:
        logger.error(f"Failed to queue tasks: {e}")
        
        # Fallback: Task ID döndür ama warning log'la
        fallback_task_id = f"task-{uuid.uuid4().hex[:12]}"
        logger.warning(f"Celery connection failed, returning fallback task ID: {fallback_task_id}")
        
        return ProcessResult(
            task_id=fallback_task_id,
            status="pending",
            message=f"Task kuyruğa alınamadı (Celery bağlantı hatası). Lütfen Celery worker'ın çalıştığından emin olun."
        )


# =============================================================================
# ONTOLOGY ENDPOINTS
# =============================================================================

@router.get("/ontology/entity-schemas", response_model=List[EntitySchemaSummary], summary="List entity schemas")
async def list_entity_schemas(
    context: Optional[str] = Query(None, description="Context adı filtresi"),
    limit: int = Query(50, ge=1, le=200),
    db: OntologyDBClient = Depends(get_ontology_db)
):
    """Mevcut entity schema'ları listele"""
    query = """
    MATCH (e:EntitySchema)
    WHERE $context IS NULL OR e.context = $context
    
    RETURN e.id as id,
           e.entity_type as entity_type,
           e.description as description,
           size(keys(e.properties)) as property_count
    ORDER BY e.entity_type
    LIMIT $limit
    """
    
    results = await db.execute_query(query, {
        "context": context,
        "limit": limit
    })
    
    return [
        EntitySchemaSummary(
            id=r["id"],
            entity_type=r["entity_type"],
            description=r["description"],
            property_count=r["property_count"] or 0
        )
        for r in results
    ]


@router.get("/ontology/relationship-schemas", response_model=List[RelationshipSchemaSummary])
async def list_relationship_schemas(
    limit: int = Query(50, ge=1, le=200),
    db: OntologyDBClient = Depends(get_ontology_db)
):
    """Mevcut relationship schema'ları listele"""
    query = """
    MATCH (r:RelationshipSchema)
    RETURN r.id as id,
           r.relationship_type as relationship_type,
           r.source_entity as source_entity,
           r.target_entity as target_entity
    ORDER BY r.relationship_type
    LIMIT $limit
    """
    
    results = await db.execute_query(query, {"limit": limit})
    
    return [
        RelationshipSchemaSummary(
            id=r["id"],
            relationship_type=r["relationship_type"],
            source_entity=r["source_entity"],
            target_entity=r["target_entity"]
        )
        for r in results
    ]


@router.get("/ontology/contexts", response_model=List[Context], summary="List contexts")
async def list_contexts(
    db: OntologyDBClient = Depends(get_ontology_db)
):
    """Mevcut context'leri listele"""
    query = """
    MATCH (c:Context)
    OPTIONAL MATCH (c)-[:CHILD_OF]->(p:Context)
    RETURN c.id as id,
           c.name as name,
           c.description as description,
           c.domain_keywords as domain_keywords,
           p.id as parent_context_id
    ORDER BY c.name
    """
    
    results = await db.execute_query(query, {})
    
    import json
    
    return [
        Context(
            id=r["id"],
            name=r["name"],
            description=r["description"],
            domain_keywords=json.loads(r["domain_keywords"]) if r.get("domain_keywords") else [],
            parent_context_id=r.get("parent_context_id")
        )
        for r in results
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
# HEALTH CHECK
# =============================================================================

@router.get("/health", summary="Health check")
async def health_check(
    db: OntologyDBClient = Depends(get_ontology_db)
):
    """Agent Builder API sağlık kontrolü"""
    health = await db.health_check()
    
    return {
        "status": "healthy" if health["status"] == "healthy" else "unhealthy",
        "ontology_db": health,
        "version": "0.1.0"
    }
