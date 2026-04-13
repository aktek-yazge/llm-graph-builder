"""
Evolving Agent Router
=====================

Self-Evolving Agent icin FastAPI endpoint'leri.
AgentRegistry uzerinden agent instance'lari yonetilir.

Endpoint gruplari:
- /agents: Agent CRUD, lifecycle
- /agents/{id}/chat: Sohbet (sync + SSE stream)
- /agents/{id}/chat/resume: Human-in-the-loop onay/red
- /agents/{id}/ontology: Ontoloji sorgulama
- /agents/{id}/skill: SkillExecution (AgenticOCR entegrasyonu)
- /agents/{id}/upload: Sample belge yukleme
- /agents/{id}/batch: Batch isleme yonetimi
- /agents/{id}/quality: Kalite kontrol
- /agents/{id}/notifications: Proaktif bildirimler (SSE + polling)
- /callback: Celery task callback (bildirim yonlendirme)
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, List, Optional

from fastapi import APIRouter, HTTPException, Query, Path, UploadFile, File, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Self-Evolving Agent"])


# ------------------------------------------------------------------
# Request / Response Models
# ------------------------------------------------------------------

class CreateAgentRequest(BaseModel):
    name: str = "Yeni Agent"
    purpose: str = ""
    tenant_id: str = "default"


class AgentInfo(BaseModel):
    agent_id: str
    name: str
    purpose: str
    domain: str = ""
    goal: str = ""
    entity_count: int = 0
    relationship_count: int = 0
    is_empty: bool = True


class ChatRequest(BaseModel):
    message: str
    session_id: str = ""


class ChatResponse(BaseModel):
    response: str
    session_id: str
    agent_id: str


class ResumeRequest(BaseModel):
    session_id: str
    decision: str = "approve"


class UploadResponse(BaseModel):
    file_count: int
    message: str


class BatchStartRequest(BaseModel):
    file_paths: list[str]
    skill_id: str = ""
    batch_size: int = 100
    ocr_mode: str = "hybrid"


class BatchProgressResponse(BaseModel):
    batch_id: str
    status: str
    total: int
    processed: int
    successful: int
    failed: int
    needs_review: int
    percent_complete: float


class CallbackPayload(BaseModel):
    agent_id: str = ""
    doc_id: str = ""
    batch_id: str = ""
    status: str = ""
    event_type: str = "document_complete"
    confidence_score: float = 0.0
    error_message: str = ""


# ------------------------------------------------------------------
# Registry accessor
# ------------------------------------------------------------------

def _get_registry(request: Request):
    """Retrieve AgentRegistry from app state (set during lifespan)."""
    registry = getattr(request.app.state, "agent_registry", None)
    if registry is None:
        raise HTTPException(503, "AgentRegistry not initialized. Server may still be starting.")
    return registry


async def _get_agent(request: Request, agent_id: str):
    """Get or create agent via the registry."""
    registry = _get_registry(request)
    return await registry.get_or_create(agent_id)


# ------------------------------------------------------------------
# Agent Lifecycle
# ------------------------------------------------------------------

@router.post("/agents", response_model=AgentInfo, summary="Create evolving agent")
async def create_agent(request: Request, body: CreateAgentRequest):
    registry = _get_registry(request)
    agent_id = f"ev-{uuid.uuid4().hex[:12]}"

    from .knowledge_store import KnowledgeStore
    store = KnowledgeStore(registry._pg)
    await store.ensure_table()
    await store.save_identity(agent_id, name=body.name, purpose=body.purpose)

    return AgentInfo(agent_id=agent_id, name=body.name, purpose=body.purpose)


@router.get("/agents", response_model=List[AgentInfo], summary="List evolving agents")
async def list_agents(
    request: Request,
    tenant_id: str = Query(default="default"),
    limit: int = Query(default=50, ge=1, le=200),
):
    registry = _get_registry(request)
    from .knowledge_store import KnowledgeStore
    store = KnowledgeStore(registry._pg)

    rows = await registry._pg.fetch(
        """
        SELECT DISTINCT agent_id
        FROM agent_knowledge
        WHERE knowledge_type = 'identity'
        ORDER BY agent_id
        LIMIT $1
        """,
        limit,
    )

    agents = []
    for row in rows:
        aid = row["agent_id"]
        identity = await store.load_identity(aid)
        ontology = await store.load_ontology(aid)
        agents.append(AgentInfo(
            agent_id=aid,
            name=identity.get("name", "Agent"),
            purpose=identity.get("purpose", ""),
            domain=ontology.domain,
            goal=ontology.goal,
            entity_count=len(ontology.entity_classes),
            relationship_count=len(ontology.relationship_predicates),
            is_empty=ontology.is_empty,
        ))
    return agents


@router.get("/agents/{agent_id}", response_model=AgentInfo, summary="Get evolving agent")
async def get_agent(request: Request, agent_id: str = Path(...)):
    agent = await _get_agent(request, agent_id)
    summary = await agent.get_ontology_summary()
    return AgentInfo(**summary)


@router.delete("/agents/{agent_id}", summary="Delete evolving agent")
async def delete_agent(request: Request, agent_id: str = Path(...)):
    registry = _get_registry(request)
    await registry.delete(agent_id)
    return {"deleted": True, "agent_id": agent_id}


# ------------------------------------------------------------------
# Chat
# ------------------------------------------------------------------

@router.post("/agents/{agent_id}/chat", response_model=ChatResponse, summary="Chat with agent")
async def agent_chat(request: Request, agent_id: str, body: ChatRequest):
    agent = await _get_agent(request, agent_id)

    full_response = ""
    session_id = body.session_id
    async for chunk in agent.chat(body.message, body.session_id):
        if chunk["type"] == "final_response":
            full_response = chunk["content"]
            session_id = chunk.get("session_id", session_id)

    return ChatResponse(response=full_response, session_id=session_id, agent_id=agent_id)


@router.post("/agents/{agent_id}/chat/stream", summary="Stream chat with agent (SSE)")
async def agent_chat_stream(request: Request, agent_id: str, body: ChatRequest):
    from sse_starlette.sse import EventSourceResponse

    agent = await _get_agent(request, agent_id)

    async def event_generator():
        async for chunk in agent.chat(body.message, body.session_id):
            yield {"data": json.dumps(chunk, ensure_ascii=False)}

    return EventSourceResponse(event_generator())


@router.post("/agents/{agent_id}/chat/resume", summary="Resume after human-in-the-loop interrupt")
async def agent_chat_resume(request: Request, agent_id: str, body: ResumeRequest):
    from sse_starlette.sse import EventSourceResponse

    agent = await _get_agent(request, agent_id)

    async def event_generator():
        async for chunk in agent.resume_after_interrupt(body.session_id, body.decision):
            yield {"data": json.dumps(chunk, ensure_ascii=False)}

    return EventSourceResponse(event_generator())


# ------------------------------------------------------------------
# Ontology
# ------------------------------------------------------------------

@router.get("/agents/{agent_id}/ontology", summary="Get agent ontology")
async def get_ontology(request: Request, agent_id: str):
    agent = await _get_agent(request, agent_id)
    ontology = await agent.store.load_ontology(agent_id)
    identity = await agent.store.load_identity(agent_id)
    return {"agent_id": agent_id, "name": identity.get("name", ""), "ontology": ontology.to_dict()}


@router.get("/agents/{agent_id}/ontology/prompt", summary="Get extraction prompt")
async def get_extraction_prompt(request: Request, agent_id: str):
    agent = await _get_agent(request, agent_id)
    ontology = await agent.store.load_ontology(agent_id)
    if ontology.is_empty:
        raise HTTPException(400, "Ontoloji bos. Once agent ile konusarak entity/relationship tanimlari ekleyin.")
    prompt = agent.memory.build_extraction_prompt(ontology)
    return {"agent_id": agent_id, "prompt": prompt, "char_count": len(prompt)}


@router.get("/agents/{agent_id}/ontology/history", summary="Ontology version history")
async def get_ontology_history(request: Request, agent_id: str):
    agent = await _get_agent(request, agent_id)
    history = await agent.store.get_version_history(agent_id, "ontology", "full")
    return {
        "agent_id": agent_id,
        "version_count": len(history),
        "versions": [
            {
                "version": h["version"],
                "source": h.get("source", ""),
                "created_at": str(h["created_at"]) if h.get("created_at") else None,
            }
            for h in history
        ],
    }


# ------------------------------------------------------------------
# Ontology Discoveries
# ------------------------------------------------------------------

@router.get("/agents/{agent_id}/ontology/discoveries", summary="List ontology discoveries")
async def list_discoveries(
    request: Request,
    agent_id: str,
    status: str = Query(default="", description="Filter: pending | approved | rejected"),
    discovery_type: str = Query(default="", description="Filter: entity | relationship"),
    limit: int = Query(default=100, ge=1, le=500),
):
    registry = _get_registry(request)
    from .knowledge_store import KnowledgeStore
    store = KnowledgeStore(registry._pg)
    items = await store.list_discoveries(agent_id, status=status, discovery_type=discovery_type, limit=limit)
    return {"agent_id": agent_id, "count": len(items), "discoveries": items}


@router.post("/agents/{agent_id}/ontology/discoveries/{name}/approve", summary="Approve a discovery")
async def approve_discovery_endpoint(request: Request, agent_id: str, name: str, discovery_type: str = Query(default="entity")):
    registry = _get_registry(request)
    from .knowledge_store import KnowledgeStore
    store = KnowledgeStore(registry._pg)

    updated = await store.update_discovery_status(agent_id, discovery_type, name, "approved")
    if not updated:
        raise HTTPException(404, f"Discovery not found: {discovery_type}/{name}")

    agent = await _get_agent(request, agent_id)
    ontology = await agent.store.load_ontology(agent_id)

    if discovery_type == "entity":
        from .ontology_model import EntityClass
        entity = EntityClass(name=name, description="")
        ontology.upsert_entity(entity)
    else:
        from .ontology_model import RelationshipPredicate
        rel = RelationshipPredicate(name=name, source="", target="", description="")
        ontology.upsert_relationship(rel)

    await agent.store.save_ontology(agent_id, ontology, source="auto_discovery")
    return {"approved": True, "name": name, "type": discovery_type}


@router.post("/agents/{agent_id}/ontology/discoveries/{name}/reject", summary="Reject a discovery")
async def reject_discovery_endpoint(request: Request, agent_id: str, name: str, discovery_type: str = Query(default="entity")):
    registry = _get_registry(request)
    from .knowledge_store import KnowledgeStore
    store = KnowledgeStore(registry._pg)

    updated = await store.update_discovery_status(agent_id, discovery_type, name, "rejected")
    if not updated:
        raise HTTPException(404, f"Discovery not found: {discovery_type}/{name}")
    return {"rejected": True, "name": name, "type": discovery_type}


# ------------------------------------------------------------------
# Skill Execution (AgenticOCR integration)
# ------------------------------------------------------------------

@router.get("/agents/{agent_id}/skill/execution", summary="Get skill for AgenticOCR")
async def get_skill_execution(request: Request, agent_id: str):
    agent = await _get_agent(request, agent_id)
    skill = await agent.get_skill_execution()
    if not skill:
        raise HTTPException(400, "Ontoloji bos, skill uretilemez.")
    return skill


# ------------------------------------------------------------------
# Sample Upload
# ------------------------------------------------------------------

@router.post("/agents/{agent_id}/upload-samples", response_model=UploadResponse)
async def upload_samples(request: Request, agent_id: str, files: List[UploadFile] = File(...)):
    agent = await _get_agent(request, agent_id)

    file_infos = [{"filename": f.filename, "content_type": f.content_type, "size": f.size} for f in files]

    await agent.store.upsert(
        agent_id, "sample_files", f"batch_{len(files)}", {"files": file_infos}, source="user_upload",
    )

    return UploadResponse(
        file_count=len(files),
        message=f"{len(files)} dosya yuklendi. Agent ile konusarak bu belgelerden entity/relationship kesfedebilirsiniz.",
    )


# ------------------------------------------------------------------
# Batch Processing
# ------------------------------------------------------------------

@router.post("/agents/{agent_id}/batch/start", summary="Start batch processing")
async def start_batch(request: Request, agent_id: str, body: BatchStartRequest):
    agent = await _get_agent(request, agent_id)
    from .tools.batch_tools import create_batch_tools

    registry = _get_registry(request)
    tools = create_batch_tools(agent_id, pg=registry._pg, celery_app=registry._celery_app)
    if not tools:
        raise HTTPException(500, "Batch tools olusturulamadi.")

    start_tool = tools[0]
    result = await start_tool.ainvoke({
        "file_paths": body.file_paths,
        "skill_id": body.skill_id,
        "batch_size": body.batch_size,
        "ocr_mode": body.ocr_mode,
    })
    return json.loads(result) if isinstance(result, str) else result


@router.get("/agents/{agent_id}/batch/progress", summary="Get batch progress")
async def batch_progress(request: Request, agent_id: str, batch_id: str = ""):
    agent = await _get_agent(request, agent_id)
    registry = _get_registry(request)
    from .tools.batch_tools import create_batch_tools
    tools = create_batch_tools(agent_id, pg=registry._pg)
    progress_tool = tools[1]
    result = await progress_tool.ainvoke({"batch_id": batch_id})
    return json.loads(result) if isinstance(result, str) else result


@router.get("/agents/{agent_id}/batch/problems", summary="List problem documents")
async def batch_problems(request: Request, agent_id: str, batch_id: str = "", limit: int = 20):
    agent = await _get_agent(request, agent_id)
    registry = _get_registry(request)
    from .tools.batch_tools import create_batch_tools
    tools = create_batch_tools(agent_id, pg=registry._pg)
    problem_tool = tools[2]
    result = await problem_tool.ainvoke({"batch_id": batch_id, "limit": limit})
    return json.loads(result) if isinstance(result, str) else result


@router.get("/agents/{agent_id}/batch/review-queue", summary="Get review queue")
async def review_queue(request: Request, agent_id: str, batch_id: str = "", limit: int = 10):
    agent = await _get_agent(request, agent_id)
    registry = _get_registry(request)
    from .tools.batch_tools import create_batch_tools
    tools = create_batch_tools(agent_id, pg=registry._pg)
    review_tool = tools[3]
    result = await review_tool.ainvoke({"batch_id": batch_id, "limit": limit})
    return json.loads(result) if isinstance(result, str) else result


# ------------------------------------------------------------------
# Quality Control
# ------------------------------------------------------------------

@router.get("/agents/{agent_id}/quality/report", summary="Generate quality report")
async def quality_report(request: Request, agent_id: str, batch_id: str = ""):
    agent = await _get_agent(request, agent_id)
    registry = _get_registry(request)
    from .tools.quality_tools import create_quality_tools
    tools = create_quality_tools(agent_id, pg=registry._pg)
    report_tool = tools[3]
    result = await report_tool.ainvoke({"batch_id": batch_id})
    return json.loads(result) if isinstance(result, str) else result


@router.get("/agents/{agent_id}/quality/conflicts", summary="Detect conflicts")
async def detect_conflicts_endpoint(request: Request, agent_id: str, batch_id: str = ""):
    agent = await _get_agent(request, agent_id)
    registry = _get_registry(request)
    from .tools.quality_tools import create_quality_tools
    tools = create_quality_tools(agent_id, pg=registry._pg)
    conflict_tool = tools[1]
    result = await conflict_tool.ainvoke({"batch_id": batch_id})
    return json.loads(result) if isinstance(result, str) else result


@router.get("/agents/{agent_id}/quality/anomalies", summary="Detect anomalies")
async def detect_anomalies_endpoint(request: Request, agent_id: str, batch_id: str = ""):
    agent = await _get_agent(request, agent_id)
    registry = _get_registry(request)
    from .tools.quality_tools import create_quality_tools
    tools = create_quality_tools(agent_id, pg=registry._pg)
    anomaly_tool = tools[2]
    result = await anomaly_tool.ainvoke({"batch_id": batch_id})
    return json.loads(result) if isinstance(result, str) else result


# ------------------------------------------------------------------
# Notifications (SSE push + polling)
# ------------------------------------------------------------------

@router.get("/agents/{agent_id}/notifications", summary="Get recent notifications (polling)")
async def get_notifications(request: Request, agent_id: str, limit: int = Query(default=50, ge=1, le=200)):
    registry = _get_registry(request)
    items = registry.notifications.get_recent(agent_id, limit=limit)
    return {"agent_id": agent_id, "count": len(items), "notifications": items}


@router.get("/agents/{agent_id}/notifications/stream", summary="Stream notifications (SSE)")
async def stream_notifications(request: Request, agent_id: str):
    from sse_starlette.sse import EventSourceResponse

    registry = _get_registry(request)

    async def event_generator():
        async for notif in registry.notifications.subscribe(agent_id):
            yield {"data": json.dumps(notif, ensure_ascii=False)}

    return EventSourceResponse(event_generator())


# ------------------------------------------------------------------
# Celery Callback (called by workspace worker)
# ------------------------------------------------------------------

@router.post("/callback/document-status", summary="Celery task callback")
async def celery_callback(request: Request, payload: CallbackPayload):
    """
    Celery worker'dan gelen durum bildirimlerini NotificationManager'a ilet.
    workspace_tasks.py _report_status() buraya POST yapar.
    """
    registry = _get_registry(request)

    agent_id = payload.agent_id
    if not agent_id and payload.batch_id:
        try:
            row = await registry._pg.fetchrow(
                "SELECT agent_id FROM batch_jobs WHERE batch_id = $1", payload.batch_id,
            )
            agent_id = row["agent_id"] if row else ""
        except Exception:
            pass

    if not agent_id:
        return {"received": True, "routed": False, "reason": "no agent_id"}

    await registry.notifications.notify(
        agent_id=agent_id,
        event_type=payload.event_type,
        data={
            "doc_id": payload.doc_id,
            "batch_id": payload.batch_id,
            "status": payload.status,
            "confidence_score": payload.confidence_score,
            "error_message": payload.error_message,
        },
    )

    return {"received": True, "routed": True, "agent_id": agent_id}
