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
- /agents/{id}/wiki: Wiki sayfalari (sahne)
- /agents/{id}/scene: Sahne yayinlama ve onizleme
- /callback: Celery task callback (bildirim yonlendirme)
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path as FsPath
from typing import Any, List, Optional

from fastapi import APIRouter, HTTPException, Query, Path, UploadFile, File, Request
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
    llm_provider: str | None = None
    llm_model: str | None = None


class AgentInfo(BaseModel):
    agent_id: str
    name: str
    purpose: str
    domain: str = ""
    goal: str = ""
    entity_count: int = 0
    relationship_count: int = 0
    is_empty: bool = True
    llm_provider: str | None = None
    llm_model: str | None = None


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
    paths: list[str] = []


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


class AddSourceRequest(BaseModel):
    urls: list[str]
    source_type: str = "url"


class CallbackPayload(BaseModel):
    agent_id: str = ""
    doc_id: str = ""
    batch_id: str = ""
    status: str = ""
    event_type: str = "document_complete"
    confidence_score: float = 0.0
    error_message: str = ""
    extraction_result: Optional[dict] = None


class BatchCompletePayload(BaseModel):
    batch_id: str
    agent_id: str = ""


UPLOAD_DIR = FsPath(os.getenv("UPLOAD_DIR", "/tmp/evolving-uploads"))


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
    """Get or create agent via the registry. Refuses soft-deleted agents."""
    registry = _get_registry(request)
    try:
        return await registry.get_or_create(agent_id)
    except PermissionError as exc:
        raise HTTPException(status_code=410, detail=str(exc))


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
    await store.save_identity(
        agent_id, name=body.name, purpose=body.purpose,
        llm_provider=body.llm_provider, llm_model=body.llm_model,
    )
    await store.ensure_lifecycle_row(agent_id)

    return AgentInfo(
        agent_id=agent_id, name=body.name, purpose=body.purpose,
        llm_provider=body.llm_provider, llm_model=body.llm_model,
    )


@router.get("/agents", response_model=List[AgentInfo], summary="List active evolving agents")
async def list_agents(
    request: Request,
    tenant_id: str = Query(default="default"),
    limit: int = Query(default=50, ge=1, le=200),
):
    registry = _get_registry(request)
    from .knowledge_store import KnowledgeStore
    store = KnowledgeStore(registry._pg)
    await store.ensure_table()

    active_ids = await store.list_active_agent_ids(limit)

    agents = []
    for aid in active_ids:
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
            llm_provider=identity.get("llm_provider"),
            llm_model=identity.get("llm_model"),
        ))
    return agents


@router.get("/agents/deleted", summary="List soft-deleted (trash) evolving agents")
async def list_deleted_agents(
    request: Request,
    limit: int = Query(default=200, ge=1, le=500),
):
    """Return agents currently in the trash, newest deletions first."""
    registry = _get_registry(request)
    deleted = await registry.list_deleted(limit)
    return {"deleted": deleted, "total": len(deleted)}


@router.get("/agents/{agent_id}", response_model=AgentInfo, summary="Get evolving agent")
async def get_agent(request: Request, agent_id: str = Path(...)):
    agent = await _get_agent(request, agent_id)
    summary = await agent.get_ontology_summary()
    return AgentInfo(**summary)


@router.delete("/agents/{agent_id}", summary="Soft-delete an evolving agent (recoverable)")
async def delete_agent(
    request: Request,
    agent_id: str = Path(...),
    purge: bool = Query(default=False, description="If true, hard delete (irrecoverable)"),
):
    """
    Default: soft delete - agent moves to trash; data preserved; restore possible.
    Pass ?purge=true to permanently destroy all data for this agent.
    """
    registry = _get_registry(request)
    if purge:
        await registry.purge(agent_id)
        return {"purged": True, "agent_id": agent_id}
    await registry.delete(agent_id)
    return {"deleted": True, "agent_id": agent_id, "soft": True}


class UpdateAgentModelRequest(BaseModel):
    llm_provider: str
    llm_model: str


@router.patch("/agents/{agent_id}/model", response_model=AgentInfo, summary="Update agent LLM model")
async def update_agent_model(request: Request, agent_id: str = Path(...), body: UpdateAgentModelRequest = ...):
    registry = _get_registry(request)

    from .knowledge_store import KnowledgeStore
    store = KnowledgeStore(registry._pg)
    await store.update_agent_model(agent_id, body.llm_provider, body.llm_model)

    # Evict cached agent so it restarts with the new model on next request
    if agent_id in registry._agents:
        old_agent = registry._agents.pop(agent_id)
        try:
            await old_agent.close() if hasattr(old_agent, "close") else None
        except Exception:
            pass

    agent = await _get_agent(request, agent_id)
    summary = await agent.get_ontology_summary()
    return AgentInfo(**summary)


@router.post("/agents/{agent_id}/restore", summary="Restore a soft-deleted evolving agent")
async def restore_agent(request: Request, agent_id: str = Path(...)):
    """Bring a soft-deleted agent back from the trash."""
    registry = _get_registry(request)
    await registry.restore(agent_id)
    return {"restored": True, "agent_id": agent_id}


# ------------------------------------------------------------------
# Chat
# ------------------------------------------------------------------

@router.post("/agents/{agent_id}/chat", response_model=ChatResponse, summary="Chat with agent")
async def agent_chat(request: Request, agent_id: str, body: ChatRequest):
    agent = await _get_agent(request, agent_id)

    full_response = ""
    session_id = body.session_id
    async for chunk in agent.chat(body.message, body.session_id):
        ctype = chunk.get("type")
        if ctype == "message_chunk":
            full_response += chunk.get("content") or ""
        elif ctype == "final_response":
            session_id = chunk.get("session_id", session_id)

    return ChatResponse(response=full_response, session_id=session_id, agent_id=agent_id)


@router.post("/agents/{agent_id}/chat/stream", summary="Stream chat with agent (SSE)")
async def agent_chat_stream(request: Request, agent_id: str, body: ChatRequest):
    from sse_starlette.sse import EventSourceResponse

    agent = await _get_agent(request, agent_id)

    async def event_generator():
        async for chunk in agent.chat(body.message, body.session_id):
            if await request.is_disconnected():
                break
            yield {"data": json.dumps(chunk, ensure_ascii=False)}

    return EventSourceResponse(event_generator(), ping=15)


@router.post("/agents/{agent_id}/chat/resume", summary="Resume after human-in-the-loop interrupt")
async def agent_chat_resume(request: Request, agent_id: str, body: ResumeRequest):
    from sse_starlette.sse import EventSourceResponse

    agent = await _get_agent(request, agent_id)

    async def event_generator():
        async for chunk in agent.resume_after_interrupt(body.session_id, body.decision):
            if await request.is_disconnected():
                break
            yield {"data": json.dumps(chunk, ensure_ascii=False)}

    return EventSourceResponse(event_generator(), ping=15)


# ------------------------------------------------------------------
# Chat History & Sessions
# ------------------------------------------------------------------

@router.get("/agents/{agent_id}/sessions", summary="List chat sessions for agent")
async def list_sessions(request: Request, agent_id: str):
    agent = await _get_agent(request, agent_id)
    sessions = await agent.list_sessions()
    return {"agent_id": agent_id, "sessions": sessions}


@router.get("/agents/{agent_id}/chat/history", summary="Get chat history for a session")
async def get_chat_history(request: Request, agent_id: str, session_id: str = Query(default="")):
    agent = await _get_agent(request, agent_id)
    if not session_id:
        sessions = await agent.list_sessions()
        if sessions:
            session_id = sessions[-1]["session_id"]
        else:
            return {"agent_id": agent_id, "session_id": "", "messages": []}
    history = await agent.get_chat_history(session_id)
    return {"agent_id": agent_id, "session_id": session_id, "messages": history}


@router.post("/agents/{agent_id}/chat/reset", summary="Start a new chat session (clears conversation)")
async def reset_chat(request: Request, agent_id: str):
    """Mevcut konusmayi sifirla, yeni session baslat."""
    agent = await _get_agent(request, agent_id)
    if agent._checkpointer and hasattr(agent, "_checkpointer_conn"):
        try:
            import asyncpg
            dsn = os.getenv(
                "EVENT_STORE_DSN",
                "postgresql://event_user:event_secret@localhost:5433/event_store",
            )
            conn = await asyncpg.connect(dsn)
            try:
                prefix = f"{agent_id}:%"
                await conn.execute("DELETE FROM checkpoints WHERE thread_id LIKE $1", prefix)
                await conn.execute("DELETE FROM checkpoint_writes WHERE thread_id LIKE $1", prefix)
            finally:
                await conn.close()
        except Exception as exc:
            logger.warning("Chat reset DB cleanup failed: %s", exc)
    return {"agent_id": agent_id, "status": "reset", "message": "Konusma sifirlandi. Yeni session baslatilacak."}


class RewindRequest(BaseModel):
    session_id: str
    user_message_index: int


@router.post(
    "/agents/{agent_id}/chat/rewind",
    summary="Rewind chat to before a specific user message; undo side effects",
)
async def rewind_chat(request: Request, agent_id: str, body: RewindRequest):
    """N'inci kullanici mesajinin oncesine geri don, agent'in o noktadan sonra
    yaptigi yan etkileri (extracted_records .md dosyalari + DB kayitlari, ontoloji
    versiyonlari) sil. Konusma o noktada kesilir; frontend ayni mesaji yeniden
    gonderebilir."""
    agent = await _get_agent(request, agent_id)
    if not body.session_id:
        raise HTTPException(status_code=400, detail="session_id zorunlu")
    if body.user_message_index < 0:
        raise HTTPException(status_code=400, detail="user_message_index >= 0 olmali")

    result = await agent.rewind_to_user_message(
        session_id=body.session_id,
        user_message_index=body.user_message_index,
    )
    if result.get("status") == "no_checkpointer":
        raise HTTPException(status_code=400, detail="Konusma kalici degil (checkpointer yok)")
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail=result.get("message", "Geri alinacak nokta bulunamadi"))
    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("message", result.get("error", "Rewind hatasi")))
    return {"agent_id": agent_id, **result}


# ------------------------------------------------------------------
# Mode & Plan
# ------------------------------------------------------------------

class ModeSwitchRequest(BaseModel):
    mode: str  # "plan" | "agent"


class PlanStepUpdate(BaseModel):
    step_id: int
    new_content: str


class PlanStepAdd(BaseModel):
    after_step_id: int = 0
    content: str


@router.get("/agents/{agent_id}/mode", summary="Get current agent mode")
async def get_mode(request: Request, agent_id: str):
    agent = await _get_agent(request, agent_id)
    mode = await agent.get_mode()
    plan = await agent.store.load_plan(agent_id)
    return {
        "agent_id": agent_id,
        "mode": mode,
        "has_plan": plan is not None,
        "plan": plan,
    }


@router.post("/agents/{agent_id}/mode", summary="Switch agent mode")
async def switch_mode(request: Request, agent_id: str, body: ModeSwitchRequest):
    agent = await _get_agent(request, agent_id)
    if body.mode == "agent":
        result = await agent.approve_plan()
    else:
        result = await agent.switch_to_plan()
    return {"agent_id": agent_id, **result}


class PlanModeRequest(BaseModel):
    decision: str  # "approve" | "reject"
    reason: str = ""
    topic: str = ""


@router.post("/agents/{agent_id}/mode/plan-request", summary="Approve/reject agent's plan-mode request")
async def respond_plan_mode_request(request: Request, agent_id: str, body: PlanModeRequest):
    """User responds to the agent's `request_plan_mode` tool call.

    On approve: a fresh draft plan is created with the given reason as summary,
    flipping the agent into plan mode for the next chat turn.
    On reject: nothing changes; agent stays in agent mode.
    """
    agent = await _get_agent(request, agent_id)
    if body.decision == "approve":
        summary = (body.topic or body.reason or "Plan tartismasi").strip()
        result = await agent.start_plan_discussion(summary)
        return {"agent_id": agent_id, "decision": "approved", **result}
    return {"agent_id": agent_id, "decision": "rejected", "mode": "agent"}


@router.get("/agents/{agent_id}/plan", summary="Get current plan")
async def get_plan(request: Request, agent_id: str):
    agent = await _get_agent(request, agent_id)
    plan = await agent.store.load_plan(agent_id)
    if not plan:
        return {"agent_id": agent_id, "plan": None, "markdown": ""}
    md = agent.store.plan_to_markdown(plan)
    return {"agent_id": agent_id, "plan": plan, "markdown": md}


@router.put("/agents/{agent_id}/plan/step", summary="Update a plan step")
async def update_plan_step(request: Request, agent_id: str, body: PlanStepUpdate):
    agent = await _get_agent(request, agent_id)
    result = await agent.store.update_plan_step(agent_id, body.step_id, body.new_content)
    if not result:
        raise HTTPException(404, "Step not found or no active plan")
    md = agent.store.plan_to_markdown(result)
    return {"agent_id": agent_id, "plan": result, "markdown": md}


@router.post("/agents/{agent_id}/plan/step", summary="Add a new plan step")
async def add_plan_step(request: Request, agent_id: str, body: PlanStepAdd):
    agent = await _get_agent(request, agent_id)
    result = await agent.store.add_plan_step(agent_id, body.after_step_id, body.content)
    if not result:
        raise HTTPException(404, "No active plan")
    md = agent.store.plan_to_markdown(result)
    return {"agent_id": agent_id, "plan": result, "markdown": md}


@router.delete("/agents/{agent_id}/plan/step/{step_id}", summary="Remove a plan step")
async def remove_plan_step(request: Request, agent_id: str, step_id: int):
    agent = await _get_agent(request, agent_id)
    result = await agent.store.remove_plan_step(agent_id, step_id)
    if not result:
        raise HTTPException(404, "Step not found or no active plan")
    md = agent.store.plan_to_markdown(result)
    return {"agent_id": agent_id, "plan": result, "markdown": md}


@router.delete("/agents/{agent_id}/plan", summary="Clear active plan")
async def clear_plan(request: Request, agent_id: str):
    """Aktif plani tamamen sil. Tamamlanan/vazgecilen planlari panelden temizler."""
    agent = await _get_agent(request, agent_id)
    await agent.store.delete(agent_id, "active_plan", "current")
    return {"agent_id": agent_id, "plan": None, "mode": "agent", "status": "cleared"}


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

    agent_dir = UPLOAD_DIR / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)

    saved_paths: list[str] = []
    file_infos: list[dict] = []
    for f in files:
        safe_name = f"{uuid.uuid4().hex[:8]}_{f.filename}"
        dest = agent_dir / safe_name
        content = await f.read()
        dest.write_bytes(content)
        saved_paths.append(str(dest))
        file_infos.append({
            "resource_id": f"res_{uuid.uuid4().hex[:8]}",
            "filename": f.filename,
            "content_type": f.content_type,
            "size": len(content),
            "path": str(dest),
        })

    await agent.store.upsert(
        agent_id, "sample_files", f"batch_{uuid.uuid4().hex[:8]}",
        {"files": file_infos, "paths": saved_paths},
        source="user_upload",
    )

    logger.info("Agent %s: %d dosya yuklendi -> %s", agent_id, len(files), saved_paths)

    return UploadResponse(
        file_count=len(files),
        message=f"{len(files)} dosya yuklendi. Agent ile konusarak bu belgelerden entity/relationship kesfedebilirsiniz.",
        paths=saved_paths,
    )


@router.post("/agents/{agent_id}/add-sources", response_model=UploadResponse)
async def add_sources(request: Request, agent_id: str, body: AddSourceRequest):
    """S3, MinIO veya HTTP URL'lerini kaynak olarak ekle."""
    agent = await _get_agent(request, agent_id)

    source_infos = [{"url": u, "type": body.source_type} for u in body.urls]

    await agent.store.upsert(
        agent_id, "source_urls", f"batch_{uuid.uuid4().hex[:8]}",
        {"sources": source_infos, "urls": body.urls},
        source="user_url",
    )

    logger.info("Agent %s: %d kaynak eklendi -> %s", agent_id, len(body.urls), body.urls)

    return UploadResponse(
        file_count=len(body.urls),
        message=f"{len(body.urls)} kaynak eklendi.",
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
    progress_tool = tools[2]
    result = await progress_tool.ainvoke({"batch_id": batch_id})
    if isinstance(result, str):
        try:
            return json.loads(result)
        except (json.JSONDecodeError, ValueError):
            return BatchProgressResponse(batch_id=batch_id or "", status="no_batch", total=0, processed=0, successful=0, failed=0, needs_review=0, percent_complete=0.0)
    return result or BatchProgressResponse(batch_id=batch_id or "", status="no_batch", total=0, processed=0, successful=0, failed=0, needs_review=0, percent_complete=0.0)


@router.get("/agents/{agent_id}/batch/problems", summary="List problem documents")
async def batch_problems(request: Request, agent_id: str, batch_id: str = "", limit: int = 20):
    agent = await _get_agent(request, agent_id)
    registry = _get_registry(request)
    from .tools.batch_tools import create_batch_tools
    tools = create_batch_tools(agent_id, pg=registry._pg)
    problem_tool = tools[3]
    result = await problem_tool.ainvoke({"batch_id": batch_id, "limit": limit})
    return json.loads(result) if isinstance(result, str) else result


@router.get("/agents/{agent_id}/batch/review-queue", summary="Get review queue")
async def review_queue(request: Request, agent_id: str, batch_id: str = "", limit: int = 10):
    agent = await _get_agent(request, agent_id)
    registry = _get_registry(request)
    from .tools.batch_tools import create_batch_tools
    tools = create_batch_tools(agent_id, pg=registry._pg)
    review_tool = tools[4]
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

@router.get("/agents/{agent_id}/notifications", summary="Get notifications (durable history)")
async def get_notifications(
    request: Request,
    agent_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    since_id: int | None = Query(default=None, ge=0),
    unread_only: bool = Query(default=False),
    mode: str = Query(default="history", regex="^(history|recent)$"),
):
    """Notifications endpoint.

    - ``mode=history`` (default): persistent PG-backed history with optional
      ``since_id`` cursor (>= ``since_id``) and ``unread_only`` filter.
    - ``mode=recent``: legacy in-memory deque (only the live process buffer).
    """
    registry = _get_registry(request)
    if mode == "recent":
        items = registry.notifications.get_recent(agent_id, limit=limit)
    else:
        items = await registry.notifications.get_history(
            agent_id, limit=limit, since_id=since_id, unread_only=unread_only,
        )
    return {"agent_id": agent_id, "count": len(items), "notifications": items}


class MarkReadPayload(BaseModel):
    notification_ids: list[int] | None = None


@router.post("/agents/{agent_id}/notifications/mark-read", summary="Mark notifications read")
async def mark_notifications_read(request: Request, agent_id: str, payload: MarkReadPayload | None = None):
    """Mark specific notification ids (or all unread) as read for this agent."""
    registry = _get_registry(request)
    ids = payload.notification_ids if payload else None
    updated = await registry.notifications.mark_read(agent_id, notification_ids=ids)
    return {"agent_id": agent_id, "marked": updated}


@router.get("/agents/{agent_id}/notifications/stream", summary="Stream notifications (SSE)")
async def stream_notifications(request: Request, agent_id: str):
    from sse_starlette.sse import EventSourceResponse

    registry = _get_registry(request)

    async def event_generator():
        async for notif in registry.notifications.subscribe(agent_id):
            if await request.is_disconnected():
                break
            yield {
                "event": notif.get("event_type", "message"),
                "data": json.dumps(notif, ensure_ascii=False),
            }

    return EventSourceResponse(
        event_generator(),
        ping=10,
        ping_message_factory=lambda: json.dumps({"event_type": "heartbeat"}),
    )


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
                "SELECT workspace_id FROM batch_jobs WHERE id = $1", payload.batch_id,
            )
            agent_id = row["workspace_id"] if row else ""
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


async def _process_batch_completion(
    registry,
    batch_id: str,
    fallback_agent_id: str = "",
) -> dict[str, Any]:
    """Resolve a finished batch: compute summary, notify, and inject a
    system event into the originating chat thread (idempotent).

    Returns a dict suitable for the webhook response. Used by both the
    HTTP webhook and the periodic safety-net task.
    """
    import asyncio
    from .tools.batch_tools import compute_batch_summary

    pg = registry._pg

    summary = await compute_batch_summary(pg, batch_id)
    if not summary.get("agent_id") and not fallback_agent_id:
        return {"received": True, "routed": False, "reason": "batch not found", "batch_id": batch_id}

    agent_id_for_batch = summary.get("agent_id") or fallback_agent_id

    pending = None
    try:
        pending = await pg.fetchrow(
            """
            SELECT workspace_id, session_id, thread_id, status, event_text_template
            FROM pending_resumes WHERE batch_job_id=$1
            """,
            batch_id,
        )
    except Exception as exc:
        logger.warning("pending_resumes lookup failed: %s", exc)

    await registry.notifications.notify(
        agent_id=agent_id_for_batch,
        event_type="batch_complete",
        data={"batch_id": batch_id, "summary": summary},
    )

    routed = False
    if pending and pending["status"] == "waiting":
        agent_id_target = pending["workspace_id"]
        session_id_target = pending["session_id"]
        template = pending["event_text_template"] or ""

        try:
            updated = await pg.execute(
                """
                UPDATE pending_resumes
                SET status='resumed', resumed_at=NOW()
                WHERE batch_job_id=$1 AND status='waiting'
                """,
                batch_id,
            )
            try:
                affected = int(updated.split()[-1])
            except (ValueError, IndexError):
                affected = 0
        except Exception as exc:
            logger.warning("pending_resumes update failed: %s", exc)
            affected = 0

        if affected:
            event_text = (
                f"Batch '{template}' tamamlandi. "
                if template else f"Batch {batch_id} tamamlandi. "
            )
            event_text += (
                f"Toplam {summary['total']} belge: "
                f"{summary['completed']} basarili, "
                f"{summary['failed']} basarisiz, "
                f"{summary['needs_review']} inceleme bekliyor "
                f"(ortalama guven {summary['avg_confidence']})."
            )

            try:
                agent = await registry.get_or_create(agent_id_target)
                asyncio.create_task(
                    agent.inject_system_event(
                        session_id=session_id_target,
                        event_text=event_text,
                        event_data={"batch_id": batch_id, "summary": summary},
                    )
                )
                routed = True
            except Exception as exc:
                logger.warning("agent inject_system_event scheduling failed: %s", exc)

    return {
        "received": True,
        "routed": routed,
        "batch_id": batch_id,
        "agent_id": agent_id_for_batch,
        "summary": summary,
    }


async def _resume_safety_sweep(registry) -> int:
    """Find batches whose Celery completion callback was lost and resume them.

    Strategy: every ``pending_resumes`` row with status='waiting' whose
    underlying ``batch_jobs`` row has ``status IN ('completed','failed')`` is
    re-driven through ``_process_batch_completion``. Idempotent — the inner
    UPDATE flips ``status='resumed'`` so subsequent sweeps no-op.

    Returns the number of pending rows recovered.
    """
    pg = registry._pg
    if pg is None:
        return 0

    try:
        rows = await pg.fetch(
            """
            SELECT pr.batch_job_id
            FROM pending_resumes pr
            JOIN batch_jobs bj ON bj.id = pr.batch_job_id
            WHERE pr.status='waiting' AND bj.status IN ('completed','failed')
            ORDER BY pr.created_at ASC
            LIMIT 50
            """,
        )
    except Exception as exc:
        logger.warning("safety sweep query failed: %s", exc)
        return 0

    if not rows:
        return 0

    recovered = 0
    for row in rows:
        try:
            await _process_batch_completion(registry, row["batch_job_id"])
            recovered += 1
        except Exception as exc:
            logger.warning("safety sweep resume failed for %s: %s", row["batch_job_id"], exc)

    if recovered:
        logger.info("Resume safety net recovered %d pending batch(es)", recovered)
    return recovered


@router.post("/callback/batch-complete", summary="Async batch completion webhook")
async def batch_complete_callback(request: Request, payload: BatchCompletePayload):
    """
    Celery worker batch tamamlandiginda buraya POST yapar. Webhook idempotenttir:
    ayni batch icin ``pending_resumes.status='resumed'`` isaretlenir, ikinci
    POST tetikleme yapmaz.

    Akis ``_process_batch_completion`` icinde toplanmistir; ayni helper safety
    task tarafindan da kullanilir.
    """
    registry = _get_registry(request)
    return await _process_batch_completion(
        registry, payload.batch_id, fallback_agent_id=payload.agent_id,
    )


# ------------------------------------------------------------------
# Ecosystem (aggregated view for graph visualization)
# ------------------------------------------------------------------

@router.get("/agents/{agent_id}/ecosystem", summary="Aggregated ecosystem data for graph visualization")
async def get_ecosystem(request: Request, agent_id: str):
    registry = _get_registry(request)
    agent = await _get_agent(request, agent_id)

    from .knowledge_store import KnowledgeStore
    store = KnowledgeStore(registry._pg)

    identity = await store.load_identity(agent_id)
    ontology = await store.load_ontology(agent_id)

    mode = "agent"
    try:
        mode = await agent.get_mode()
    except Exception:
        pass

    model = os.getenv("EVOLVING_CHAT_MODEL", "gpt-5.4")

    pending = approved = rejected = 0
    try:
        all_disc = await store.list_discoveries(agent_id, limit=1000)
        for d in all_disc:
            s = d.get("status", "pending")
            if s == "pending":
                pending += 1
            elif s == "approved":
                approved += 1
            elif s == "rejected":
                rejected += 1
    except Exception:
        pass

    mcp_info = {"connected": False, "tool_count": 0, "tool_names": []}
    mcp_client = registry._mcp_clients.get(agent_id)
    if mcp_client is not None:
        mcp_info["connected"] = True
        try:
            mcp_tools = mcp_client.get_tools()
            mcp_info["tool_count"] = len(mcp_tools)
            mcp_info["tool_names"] = [t.name for t in mcp_tools[:20]]
        except Exception:
            pass

    celery_available = registry._celery_app is not None

    neo4j_uri = os.getenv("NEO4J_URI", "")
    neo4j_configured = bool(neo4j_uri)
    if neo4j_uri:
        parts = neo4j_uri.split("@")
        neo4j_uri = f"***@{parts[-1]}" if len(parts) > 1 else neo4j_uri[:20] + "..."

    subagents = [
        {"name": "quality-analyst", "description": "Extraction kalitesini analiz eder, celiskileri ve anomalileri bulur", "tool_count": 4},
        {"name": "ocr-strategy-advisor", "description": "Belge orneklerini analiz eder, OCR stratejisi onerir", "tool_count": 1},
    ]

    batch_info: dict[str, Any] = {"active_count": 0, "latest": None}
    try:
        rows = await registry._pg.fetch(
            "SELECT id, status, total_documents, processed_documents FROM batch_jobs WHERE workspace_id = $1 ORDER BY created_at DESC LIMIT 5",
            agent_id,
        )
        active = [r for r in rows if r["status"] in ("pending", "processing", "running", "created")]
        batch_info["active_count"] = len(active)
        if rows:
            r = rows[0]
            total = r["total_documents"] or 0
            processed = r["processed_documents"] or 0
            batch_info["latest"] = {
                "batch_id": r["id"],
                "status": r["status"],
                "total": total,
                "processed": processed,
                "percent_complete": round((processed / total * 100) if total > 0 else 0, 1),
            }
    except Exception:
        pass

    sample_count = 0
    source_count = 0
    try:
        sf = await registry._pg.fetch(
            "SELECT value FROM agent_knowledge WHERE agent_id = $1 AND knowledge_type = 'sample_files'",
            agent_id,
        )
        for row in sf:
            val = row["value"] if isinstance(row["value"], dict) else json.loads(row["value"])
            sample_count += len(val.get("files", []))
    except Exception:
        pass
    try:
        su = await registry._pg.fetch(
            "SELECT value FROM agent_knowledge WHERE agent_id = $1 AND knowledge_type = 'source_urls'",
            agent_id,
        )
        for row in su:
            val = row["value"] if isinstance(row["value"], dict) else json.loads(row["value"])
            source_count += len(val.get("urls", []))
    except Exception:
        pass

    notif_count = len(registry.notifications.get_recent(agent_id, limit=50))

    return {
        "agent": {
            "agent_id": agent_id,
            "name": identity.get("name", "Agent"),
            "purpose": identity.get("purpose", ""),
            "domain": ontology.domain,
            "mode": mode,
            "model": model,
        },
        "ontology": {
            "entity_count": len(ontology.entity_classes),
            "relationship_count": len(ontology.relationship_predicates),
            "rule_count": len(ontology.inference_rules),
            "constraint_count": len(ontology.constraints),
            "domain": ontology.domain,
            "goal": ontology.goal,
        },
        "discoveries": {"pending": pending, "approved": approved, "rejected": rejected},
        "mcp": mcp_info,
        "celery": {"available": celery_available},
        "neo4j": {"configured": neo4j_configured, "uri": neo4j_uri},
        "subagents": subagents,
        "batch": batch_info,
        "resources": {"sample_files": sample_count, "source_urls": source_count},
        "notifications": {"recent_count": notif_count},
    }


# ------------------------------------------------------------------
# Wiki (sahne)
# ------------------------------------------------------------------

class WikiPageBody(BaseModel):
    content: str


class SceneFreezeRequest(BaseModel):
    categories: list[str] = []  # bos = tum kategoriler


def _page_summary(page: dict[str, Any]) -> str:
    """First non-heading, non-empty line (max 120 chars)."""
    for line in (page.get("content") or "").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped[:120]
    return ""


@router.get("/agents/{agent_id}/wiki/pages", summary="List wiki pages")
async def wiki_list_pages(
    request: Request,
    agent_id: str,
    category: str = Query(default=""),
    include_system: bool = Query(default=False),
):
    agent = await _get_agent(request, agent_id)
    pages = await agent.wiki.list_pages(agent_id, category=category)
    out: list[dict[str, Any]] = []
    for p in pages:
        if not include_system and p["path"].startswith("_"):
            continue
        out.append({
            "path": p["path"],
            "category": p.get("category", "general"),
            "summary": _page_summary(p),
            "version": p.get("version", 1),
            "links": p.get("links", []),
            "created_at": p.get("created_at"),
        })
    return {"agent_id": agent_id, "count": len(out), "pages": out}


@router.get("/agents/{agent_id}/wiki/search", summary="Search wiki pages")
async def wiki_search(request: Request, agent_id: str, q: str = Query(...)):
    agent = await _get_agent(request, agent_id)
    results = await agent.wiki.search(agent_id, q)
    return {
        "agent_id": agent_id,
        "query": q,
        "count": len(results),
        "results": [
            {
                "path": r["path"],
                "category": r.get("category", "general"),
                "summary": _page_summary(r),
                "version": r.get("version", 1),
            }
            for r in results
        ],
    }


@router.get("/agents/{agent_id}/wiki/index", summary="Get wiki index (markdown)")
async def wiki_index(request: Request, agent_id: str):
    agent = await _get_agent(request, agent_id)
    md = await agent.wiki.get_index(agent_id)
    return {"agent_id": agent_id, "markdown": md}


@router.post("/agents/{agent_id}/wiki/lint", summary="Run wiki health check")
async def wiki_lint(request: Request, agent_id: str):
    agent = await _get_agent(request, agent_id)
    report = await agent.wiki.lint(agent_id)
    return {"agent_id": agent_id, **report}


@router.get("/agents/{agent_id}/wiki/traverse", summary="Traverse wikilinks")
async def wiki_traverse(
    request: Request,
    agent_id: str,
    start: str = Query(...),
    depth: int = Query(default=2, ge=1, le=4),
):
    agent = await _get_agent(request, agent_id)
    reachable = await agent.wiki.traverse(agent_id, start, depth=depth)
    return {
        "agent_id": agent_id,
        "start": start,
        "depth": depth,
        "count": len(reachable),
        "pages": [
            {
                "path": path,
                "category": page.get("category", "general"),
                "summary": _page_summary(page),
                "links": page.get("links", []),
            }
            for path, page in reachable.items()
        ],
    }


@router.get("/agents/{agent_id}/wiki/log", summary="Get wiki mutation log")
async def wiki_log(request: Request, agent_id: str, limit: int = Query(default=50, ge=1, le=500)):
    agent = await _get_agent(request, agent_id)
    entries = await agent.wiki.get_log(agent_id, limit=limit)
    return {"agent_id": agent_id, "count": len(entries), "entries": entries}


@router.get("/agents/{agent_id}/wiki/graph", summary="Wiki graph: nodes + edges")
async def wiki_graph(request: Request, agent_id: str):
    agent = await _get_agent(request, agent_id)
    pages = await agent.wiki.list_pages(agent_id)
    visible = [p for p in pages if not p["path"].startswith("_")]

    name_index, path_index = agent.wiki._build_page_index(visible)

    inbound: dict[str, int] = {p["path"]: 0 for p in visible}
    edges: list[dict[str, str]] = []
    for p in visible:
        src = p["path"]
        for link in p.get("links", []):
            resolved = agent.wiki._resolve_path(link, name_index, path_index)
            if resolved and resolved in inbound:
                inbound[resolved] += 1
                edges.append({"source": src, "target": resolved})

    nodes = [
        {
            "id": p["path"],
            "path": p["path"],
            "category": p.get("category", "general"),
            "summary": _page_summary(p),
            "backlinks": inbound.get(p["path"], 0),
            "outbound": len(p.get("links", [])),
        }
        for p in visible
    ]
    return {"agent_id": agent_id, "node_count": len(nodes), "edge_count": len(edges), "nodes": nodes, "edges": edges}


@router.get("/agents/{agent_id}/wiki/pages/{page_path:path}", summary="Get wiki page")
async def wiki_get_page(request: Request, agent_id: str, page_path: str):
    agent = await _get_agent(request, agent_id)
    page = await agent.wiki.get_page(agent_id, page_path)
    if not page:
        raise HTTPException(404, f"Sayfa bulunamadi: {page_path}")
    backlinks = await agent.wiki.get_backlinks(agent_id, page_path)
    unresolved = await agent.wiki.resolve_links(agent_id, page["content"])
    broken = [target for target, resolved in unresolved.items() if resolved is None]
    return {
        "agent_id": agent_id,
        "path": page["path"],
        "category": page.get("category", "general"),
        "content": page.get("content", ""),
        "links": page.get("links", []),
        "version": page.get("version", 1),
        "created_at": page.get("created_at"),
        "backlinks": backlinks,
        "broken_links": broken,
    }


@router.put("/agents/{agent_id}/wiki/pages/{page_path:path}", summary="Create or update wiki page")
async def wiki_put_page(request: Request, agent_id: str, page_path: str, body: WikiPageBody):
    agent = await _get_agent(request, agent_id)
    existing = await agent.wiki.get_page(agent_id, page_path)
    if existing:
        result = await agent.wiki.update_page(agent_id, page_path, body.content, source="ui_edit")
    else:
        result = await agent.wiki.create_page(agent_id, page_path, body.content, source="ui_edit")
    return {"agent_id": agent_id, "path": page_path, "version": result.get("version", 1)}


@router.delete("/agents/{agent_id}/wiki/pages/{page_path:path}", summary="Delete wiki page")
async def wiki_delete_page(request: Request, agent_id: str, page_path: str):
    agent = await _get_agent(request, agent_id)
    deleted = await agent.wiki.delete_page(agent_id, page_path)
    if not deleted:
        raise HTTPException(404, f"Sayfa bulunamadi: {page_path}")
    return {"agent_id": agent_id, "path": page_path, "deleted": True}


@router.get("/agents/{agent_id}/wiki/history/{page_path:path}", summary="Page version history")
async def wiki_page_history(request: Request, agent_id: str, page_path: str):
    agent = await _get_agent(request, agent_id)
    versions = await agent.wiki.get_page_history(agent_id, page_path)
    return {
        "agent_id": agent_id,
        "path": page_path,
        "version_count": len(versions),
        "versions": [
            {
                "version": v.get("version", 1),
                "source": v.get("source", ""),
                "created_at": str(v["created_at"]) if v.get("created_at") else None,
            }
            for v in versions
        ],
    }


# ------------------------------------------------------------------
# Scene (publishing = save_skill + wiki snapshot)
# ------------------------------------------------------------------

async def _categories_from_request(categories: list[str]) -> list[str] | None:
    """Normalize categories arg: empty list -> None (all)."""
    return [c for c in categories if c] or None


@router.get("/agents/{agent_id}/scene/stats", summary="Scene stats (page count + token estimate)")
async def scene_stats(
    request: Request,
    agent_id: str,
    categories: str = Query(default="", description="Virgulle ayrilmis kategori filtresi"),
):
    agent = await _get_agent(request, agent_id)
    cat_list = [c.strip() for c in categories.split(",") if c.strip()] or None

    pages = await agent.wiki.list_pages(agent_id)
    visible = [p for p in pages if not p["path"].startswith("_")]

    by_cat: dict[str, int] = {}
    for p in visible:
        c = p.get("category", "general")
        by_cat[c] = by_cat.get(c, 0) + 1

    ontology = await agent.store.load_ontology(agent_id)
    context = await agent.wiki.build_extraction_context(agent_id, categories=cat_list)
    prompt = "" if ontology.is_empty else agent.memory.build_extraction_prompt(ontology, wiki_context=context)

    return {
        "agent_id": agent_id,
        "filter_categories": cat_list or [],
        "page_count_total": len(visible),
        "page_count_by_category": by_cat,
        "char_count": len(prompt),
        "token_estimate": len(prompt) // 4,
        "ontology_empty": ontology.is_empty,
    }


@router.get("/agents/{agent_id}/scene/preview", summary="Preview the extraction prompt Celery would see")
async def scene_preview(
    request: Request,
    agent_id: str,
    categories: str = Query(default=""),
):
    agent = await _get_agent(request, agent_id)
    cat_list = [c.strip() for c in categories.split(",") if c.strip()] or None

    ontology = await agent.store.load_ontology(agent_id)
    if ontology.is_empty:
        return {
            "agent_id": agent_id,
            "prompt": "",
            "char_count": 0,
            "token_estimate": 0,
            "ontology_empty": True,
            "filter_categories": cat_list or [],
        }
    context = await agent.wiki.build_extraction_context(agent_id, categories=cat_list)
    prompt = agent.memory.build_extraction_prompt(ontology, wiki_context=context)
    return {
        "agent_id": agent_id,
        "filter_categories": cat_list or [],
        "prompt": prompt,
        "char_count": len(prompt),
        "token_estimate": len(prompt) // 4,
        "ontology_empty": False,
    }


@router.get("/agents/{agent_id}/scene/published", summary="Get the currently published scene (frozen snapshot)")
async def scene_published(request: Request, agent_id: str):
    agent = await _get_agent(request, agent_id)
    row = await agent.store.get(agent_id, "skill_execution", "latest")
    if not row:
        return {"agent_id": agent_id, "published": False, "prompt": "", "metadata": None}

    value = row["value"]
    if isinstance(value, str):
        value = json.loads(value)

    prompt = value.get("prompt_template", "") if value else ""
    meta = value.get("scene_metadata") if value else None

    return {
        "agent_id": agent_id,
        "published": True,
        "prompt": prompt,
        "char_count": len(prompt),
        "token_estimate": len(prompt) // 4,
        "skill_id": value.get("skill_id") if value else None,
        "version": value.get("version") if value else None,
        "metadata": meta,
        "created_at": str(row["created_at"]) if row.get("created_at") else None,
    }


@router.post("/agents/{agent_id}/scene/publish", summary="Publish scene (freeze wiki + ontology into skill)")
async def scene_publish(request: Request, agent_id: str, body: SceneFreezeRequest):
    agent = await _get_agent(request, agent_id)
    cat_list = [c for c in body.categories if c] or None
    result = await agent.skill_manager.save_skill(agent_id, categories=cat_list)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return {
        "agent_id": agent_id,
        "published": True,
        "skill_id": result.get("skill_id"),
        "version": result.get("version"),
        "metadata": result.get("scene_metadata"),
    }


# ------------------------------------------------------------------
# OCR Document Preview (frontend)
# ------------------------------------------------------------------

@router.get("/agents/{agent_id}/ocr/documents", summary="List OCR'd documents for this agent")
async def list_ocr_documents(request: Request, agent_id: str):
    registry = _get_registry(request)
    from .knowledge_store import KnowledgeStore
    ks = KnowledgeStore(registry._pg)
    entries = await ks.get_all(agent_id, "ocr_result")
    docs = []
    for entry in entries:
        val = entry.get("value", {})
        if isinstance(val, str):
            import json as _json
            try:
                val = _json.loads(val)
            except Exception:
                continue
        docs.append({
            "doc_key": entry.get("key", ""),
            "file_name": val.get("file_name", ""),
            "page_count": val.get("page_count", 0),
            "total_chars": val.get("total_chars", 0),
            "duration_ms": val.get("duration_ms", 0),
            "token_usage": val.get("token_usage"),
        })
    return {"agent_id": agent_id, "count": len(docs), "documents": docs}


@router.get("/agents/{agent_id}/ocr/{doc_key}/pages", summary="Paginated OCR text reader")
async def read_ocr_pages(
    request: Request,
    agent_id: str,
    doc_key: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=5, ge=1, le=50),
):
    import re as _re
    registry = _get_registry(request)
    from .knowledge_store import KnowledgeStore
    ks = KnowledgeStore(registry._pg)
    entries = await ks.get_all(agent_id, "ocr_result")
    val = None
    for entry in entries:
        if entry.get("key") == doc_key:
            val = entry.get("value", {})
            if isinstance(val, str):
                import json as _json
                val = _json.loads(val)
            break
    if val is None:
        raise HTTPException(404, f"OCR document '{doc_key}' not found")

    merged_text = val.get("ocr_text", "")
    parts = _re.split(r"\[\[PAGE:\d+\]\]", merged_text)
    pages = [p.strip() for p in parts if p.strip()]
    if not pages and merged_text:
        chunk_size = 4000
        pages = [merged_text[i:i + chunk_size] for i in range(0, len(merged_text), chunk_size)]

    total_pages = len(pages)
    selected = pages[offset:offset + limit]
    has_more = (offset + limit) < total_pages

    return {
        "doc_key": doc_key,
        "file_name": val.get("file_name", ""),
        "total_pages": total_pages,
        "offset": offset,
        "limit": limit,
        "returned": len(selected),
        "has_more": has_more,
        "pages": selected,
    }


# ------------------------------------------------------------------
# Unified Resources (sample_files + ocr_result joined by resource_id)
# ------------------------------------------------------------------

@router.get("/agents/{agent_id}/resources", summary="Merged resource list with OCR status")
async def list_agent_resources(request: Request, agent_id: str):
    agent = await _get_agent(request, agent_id)
    ks = agent.store

    sf_entries = await ks.get_all(agent_id, "sample_files")
    files: list[dict] = []
    for entry in sf_entries:
        val = entry.get("value", {})
        if isinstance(val, str):
            try:
                val = json.loads(val)
            except (json.JSONDecodeError, ValueError):
                continue
        for f in val.get("files", []):
            files.append(f)

    ocr_entries = await ks.get_all(agent_id, "ocr_result")
    # Birden fazla index: birinci tercih resource_id; yoksa (eski kayitlar icin)
    # file_path veya file_name uzerinden geri donusu fallback olarak kullan.
    ocr_by_rid: dict[str, dict] = {}
    ocr_by_path: dict[str, dict] = {}
    ocr_by_name: dict[str, dict] = {}
    for entry in ocr_entries:
        val = entry.get("value", {})
        if isinstance(val, str):
            try:
                val = json.loads(val)
            except (json.JSONDecodeError, ValueError):
                continue
        info = {
            "doc_key": entry.get("key", ""),
            "page_count": val.get("page_count", 0),
            "total_chars": val.get("total_chars", 0),
        }
        rid = val.get("resource_id", "") or ""
        fpath = val.get("file_path", "") or ""
        fname = val.get("file_name", "") or ""
        if rid:
            ocr_by_rid[rid] = info
        if fpath:
            ocr_by_path[fpath] = info
        if fname:
            ocr_by_name[fname] = info

    result = []
    for f in files:
        rid = f.get("resource_id", "") or ""
        fpath = f.get("path", "") or ""
        fname = f.get("filename", "") or ""

        ocr_info = None
        if rid and rid in ocr_by_rid:
            ocr_info = ocr_by_rid[rid]
        elif fpath and fpath in ocr_by_path:
            ocr_info = ocr_by_path[fpath]
        elif fname and fname in ocr_by_name:
            ocr_info = ocr_by_name[fname]
        else:
            # Son care: OCR file_path icindeki safe-name dosya adiyla eslesiyor mu?
            for op, info in ocr_by_path.items():
                if op.endswith(fname) or (fname and fname in op):
                    ocr_info = info
                    break

        result.append({
            "resource_id": rid,
            "filename": fname,
            "content_type": f.get("content_type", ""),
            "size": f.get("size", 0),
            "ocr_status": "completed" if ocr_info else "pending",
            "doc_key": ocr_info["doc_key"] if ocr_info else None,
            "page_count": ocr_info["page_count"] if ocr_info else None,
            "total_chars": ocr_info["total_chars"] if ocr_info else None,
        })

    return {"resources": result, "total": len(result)}


# ------------------------------------------------------------------
# Workflow — Multi-workflow CRUD, execution, and template sharing
# ------------------------------------------------------------------

def _wf_to_dict(wf) -> dict:
    """Serialize a WorkflowRow for JSON responses."""
    return {
        "workflow_id": wf.workflow_id,
        "agent_id": wf.agent_id,
        "name": wf.name,
        "description": wf.description,
        "version": wf.version,
        "status": wf.status.value if hasattr(wf.status, "value") else wf.status,
        "node_count": len(wf.dsl.nodes),
        "is_template": wf.is_template,
        "source_template_id": wf.source_template_id,
        "dsl": wf.dsl.model_dump(),
        "created_at": str(wf.created_at) if wf.created_at else None,
        "updated_at": str(wf.updated_at) if wf.updated_at else None,
    }


@router.get("/workflow/node-types", summary="List available node types for workflow canvas")
async def list_workflow_node_types():
    from .workflow import nodes as _  # noqa: F401 — ensure registration
    from .workflow.node_registry import get_catalog
    catalog = get_catalog()
    return {"node_types": [m.to_dict() for m in catalog]}


# ── Multi-workflow CRUD ───────────────────────────────────────────

@router.get("/agents/{agent_id}/workflows", summary="List all workflows for agent")
async def list_workflows(request: Request, agent_id: str):
    from .workflow.store import WorkflowStore
    registry = _get_registry(request)
    store = WorkflowStore(registry._pg)
    workflows = await store.list_workflows(agent_id)
    active_id = await registry.knowledge.load_active_workflow(agent_id)
    items = []
    for wf in workflows:
        d = _wf_to_dict(wf)
        del d["dsl"]
        items.append(d)
    return {"workflows": items, "active_workflow_id": active_id}


class CreateWorkflowPayload(BaseModel):
    name: str = "New Workflow"
    description: str = ""


@router.post("/agents/{agent_id}/workflows", summary="Create a new workflow")
async def create_workflow_endpoint(request: Request, agent_id: str, payload: CreateWorkflowPayload):
    from .workflow.store import WorkflowStore
    registry = _get_registry(request)
    store = WorkflowStore(registry._pg)
    wf = await store.create_workflow(agent_id, payload.name, payload.description)
    await registry.knowledge.save_active_workflow(agent_id, wf.workflow_id)
    await registry.notifications.notify(
        agent_id=agent_id,
        event_type="workflow_created",
        data={"workflow_id": wf.workflow_id, "name": wf.name},
    )
    return _wf_to_dict(wf)


@router.get("/agents/{agent_id}/workflows/{workflow_id}", summary="Get specific workflow")
async def get_workflow_by_id(request: Request, agent_id: str, workflow_id: str):
    from .workflow.store import WorkflowStore
    registry = _get_registry(request)
    store = WorkflowStore(registry._pg)
    wf = await store.get(workflow_id)
    if not wf or wf.agent_id != agent_id:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Workflow not found")
    return _wf_to_dict(wf)


class WorkflowDSLPayload(BaseModel):
    dsl: dict


@router.put("/agents/{agent_id}/workflows/{workflow_id}", summary="Update specific workflow DSL")
async def update_workflow_by_id(request: Request, agent_id: str, workflow_id: str, payload: WorkflowDSLPayload):
    from .workflow.models import WorkflowDSL
    from .workflow.store import WorkflowStore

    registry = _get_registry(request)
    store = WorkflowStore(registry._pg)
    wf = await store.get(workflow_id)
    if not wf or wf.agent_id != agent_id:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Workflow not found")

    dsl = WorkflowDSL.model_validate(payload.dsl)
    await store.save_dsl(workflow_id, dsl)

    await registry.notifications.notify(
        agent_id=agent_id,
        event_type="workflow_changed",
        data={"workflow_id": workflow_id, "source": "manual"},
    )
    return {"workflow_id": workflow_id, "status": "updated", "node_count": len(dsl.nodes)}


class RenameWorkflowPayload(BaseModel):
    name: str


@router.patch("/agents/{agent_id}/workflows/{workflow_id}", summary="Rename / update workflow metadata")
async def patch_workflow(request: Request, agent_id: str, workflow_id: str, payload: RenameWorkflowPayload):
    from .workflow.store import WorkflowStore
    registry = _get_registry(request)
    store = WorkflowStore(registry._pg)
    wf = await store.get(workflow_id)
    if not wf or wf.agent_id != agent_id:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Workflow not found")
    await store.rename_workflow(workflow_id, payload.name)
    await registry.notifications.notify(
        agent_id=agent_id,
        event_type="workflow_changed",
        data={"workflow_id": workflow_id, "source": "rename"},
    )
    return {"workflow_id": workflow_id, "name": payload.name}


@router.delete("/agents/{agent_id}/workflows/{workflow_id}", summary="Delete a workflow")
async def delete_workflow_endpoint(request: Request, agent_id: str, workflow_id: str):
    from .workflow.store import WorkflowStore
    registry = _get_registry(request)
    store = WorkflowStore(registry._pg)
    wf = await store.get(workflow_id)
    if not wf or wf.agent_id != agent_id:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Workflow not found")
    await store.delete_workflow(workflow_id)
    active_id = await registry.knowledge.load_active_workflow(agent_id)
    if active_id == workflow_id:
        remaining = await store.list_workflows(agent_id)
        new_active = remaining[0].workflow_id if remaining else None
        if new_active:
            await registry.knowledge.save_active_workflow(agent_id, new_active)
    await registry.notifications.notify(
        agent_id=agent_id,
        event_type="workflow_deleted",
        data={"workflow_id": workflow_id},
    )
    return {"status": "deleted", "workflow_id": workflow_id}


class SetActiveWorkflowPayload(BaseModel):
    workflow_id: str


@router.put("/agents/{agent_id}/active-workflow", summary="Set active workflow")
async def set_active_workflow(request: Request, agent_id: str, payload: SetActiveWorkflowPayload):
    from .workflow.store import WorkflowStore
    registry = _get_registry(request)
    store = WorkflowStore(registry._pg)
    wf = await store.get(payload.workflow_id)
    if not wf or wf.agent_id != agent_id:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Workflow not found")
    await registry.knowledge.save_active_workflow(agent_id, payload.workflow_id)
    await registry.notifications.notify(
        agent_id=agent_id,
        event_type="active_workflow_changed",
        data={"workflow_id": payload.workflow_id},
    )
    return {"active_workflow_id": payload.workflow_id}


# ── Workflow runs (addressed by workflow_id) ──────────────────────

class WorkflowRunPayload(BaseModel):
    mode: str = "test_one"
    inputs: dict | None = None


@router.post("/agents/{agent_id}/workflows/{workflow_id}/runs", summary="Start run on specific workflow")
async def start_workflow_run_by_id(request: Request, agent_id: str, workflow_id: str, payload: WorkflowRunPayload):
    from .workflow.store import WorkflowStore
    from .workflow.runtime import WorkflowRuntime

    registry = _get_registry(request)
    store = WorkflowStore(registry._pg)
    wf = await store.get(workflow_id)
    if not wf or wf.agent_id != agent_id:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Workflow not found")

    rt = WorkflowRuntime(
        pg=registry._pg,
        notification_mgr=registry.notifications,
        celery_app=getattr(registry, '_celery_app', None),
    )
    summary = await rt.start_run(
        agent_id=agent_id,
        workflow_id=workflow_id,
        mode=payload.mode,
        inputs=payload.inputs,
    )
    return summary


@router.post("/agents/{agent_id}/workflows/{workflow_id}/publish", summary="Publish specific workflow")
async def publish_workflow_by_id(request: Request, agent_id: str, workflow_id: str):
    from .workflow.store import WorkflowStore
    registry = _get_registry(request)
    store = WorkflowStore(registry._pg)
    wf = await store.get(workflow_id)
    if not wf or wf.agent_id != agent_id:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Workflow not found")

    new_version = await store.publish(workflow_id)
    await registry.notifications.notify(
        agent_id=agent_id,
        event_type="workflow_published",
        data={"workflow_id": workflow_id, "version": new_version},
    )
    return {"workflow_id": workflow_id, "version": new_version, "status": "published"}


# ── Global resources ──────────────────────────────────────────────

@router.get("/resources", summary="List all resources across all agents")
async def list_global_resources(request: Request):
    registry = _get_registry(request)
    ks = registry.knowledge
    resources = await ks.list_all_resources()
    return {"resources": resources, "total": len(resources)}


@router.post("/resources/upload", summary="Upload files to a specific agent (global entry point)")
async def upload_global_resource(
    request: Request,
    agent_id: str = Query(..., description="Target agent ID"),
    files: List[UploadFile] = File(...),
):
    agent = await _get_agent(request, agent_id)

    agent_dir = UPLOAD_DIR / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)

    saved_paths: list[str] = []
    file_infos: list[dict] = []
    for f in files:
        safe_name = f"{uuid.uuid4().hex[:8]}_{f.filename}"
        dest = agent_dir / safe_name
        content = await f.read()
        dest.write_bytes(content)
        saved_paths.append(str(dest))
        file_infos.append({
            "resource_id": f"res_{uuid.uuid4().hex[:8]}",
            "filename": f.filename,
            "content_type": f.content_type,
            "size": len(content),
            "path": str(dest),
        })

    await agent.store.upsert(
        agent_id, "sample_files", f"batch_{uuid.uuid4().hex[:8]}",
        {"files": file_infos, "paths": saved_paths},
        source="user_upload",
    )

    logger.info("Global upload -> Agent %s: %d files", agent_id, len(files))
    return {
        "file_count": len(files),
        "message": f"{len(files)} dosya yuklendi (agent: {agent_id}).",
        "paths": saved_paths,
    }


@router.delete("/resources/{resource_id}", summary="Delete a resource by ID")
async def delete_global_resource(request: Request, resource_id: str):
    registry = _get_registry(request)
    ks = registry.knowledge
    deleted = await ks.delete_resource_by_id(resource_id)
    if not deleted:
        raise HTTPException(404, "Resource not found")
    return {"status": "deleted", "resource_id": resource_id}


# ── Global workflows list ─────────────────────────────────────────


class CreateGlobalWorkflowPayload(BaseModel):
    name: str = "New Workflow"
    description: str = ""
    agent_id: str | None = None


@router.post("/workflows", summary="Create a standalone or agent-bound workflow")
async def create_global_workflow(request: Request, payload: CreateGlobalWorkflowPayload):
    from .workflow.store import WorkflowStore
    registry = _get_registry(request)
    store = WorkflowStore(registry._pg)
    wf = await store.create_workflow(payload.agent_id, payload.name, payload.description)
    if payload.agent_id:
        try:
            await registry.knowledge.save_active_workflow(payload.agent_id, wf.workflow_id)
            await registry.notifications.notify(
                agent_id=payload.agent_id,
                event_type="workflow_created",
                data={"workflow_id": wf.workflow_id, "name": wf.name},
            )
        except Exception:
            pass
    return _wf_to_dict(wf)


@router.delete("/workflows/{workflow_id}", summary="Delete a workflow by ID (standalone or any)")
async def delete_global_workflow(request: Request, workflow_id: str):
    from .workflow.store import WorkflowStore
    registry = _get_registry(request)
    store = WorkflowStore(registry._pg)
    wf = await store.get(workflow_id)
    if not wf:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Workflow not found")
    await store.delete_workflow(workflow_id)
    if wf.agent_id:
        active_id = await registry.knowledge.load_active_workflow(wf.agent_id)
        if active_id == workflow_id:
            remaining = await store.list_workflows(wf.agent_id)
            new_active = remaining[0].workflow_id if remaining else None
            if new_active:
                await registry.knowledge.save_active_workflow(wf.agent_id, new_active)
    return {"status": "deleted", "workflow_id": workflow_id}


@router.get("/workflows", summary="List all workflows across all agents")
async def list_all_workflows(
    request: Request,
    search: str | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    from .workflow.store import WorkflowStore
    registry = _get_registry(request)
    store = WorkflowStore(registry._pg)
    items, total = await store.list_all_workflows(
        search=search, status=status, limit=limit, offset=offset,
    )
    return {"workflows": items, "total": total}


# ── Template sharing ─────────────────────────────────────────────

@router.get("/workflow-templates", summary="List shared workflow templates")
async def list_workflow_templates(request: Request, exclude_agent_id: str | None = None):
    from .workflow.store import WorkflowStore
    registry = _get_registry(request)
    store = WorkflowStore(registry._pg)
    templates = await store.list_templates(exclude_agent_id=exclude_agent_id)
    return {"templates": templates}


class ShareTemplatePayload(BaseModel):
    description: str = ""


@router.post("/agents/{agent_id}/workflows/{workflow_id}/share", summary="Share workflow as template")
async def share_workflow_as_template(request: Request, agent_id: str, workflow_id: str, payload: ShareTemplatePayload):
    from .workflow.store import WorkflowStore
    registry = _get_registry(request)
    store = WorkflowStore(registry._pg)
    wf = await store.get(workflow_id)
    if not wf or wf.agent_id != agent_id:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Workflow not found")
    await store.set_template(workflow_id, True, payload.description or wf.description)
    return {"workflow_id": workflow_id, "is_template": True}


class ImportTemplatePayload(BaseModel):
    template_workflow_id: str
    name: str | None = None


@router.post("/agents/{agent_id}/import-template", summary="Import a shared template into agent")
async def import_template(request: Request, agent_id: str, payload: ImportTemplatePayload):
    from .workflow.store import WorkflowStore
    registry = _get_registry(request)
    store = WorkflowStore(registry._pg)
    new_wf = await store.clone_workflow(
        workflow_id=payload.template_workflow_id,
        target_agent_id=agent_id,
        new_name=payload.name,
    )
    await registry.knowledge.save_active_workflow(agent_id, new_wf.workflow_id)
    await registry.notifications.notify(
        agent_id=agent_id,
        event_type="workflow_created",
        data={"workflow_id": new_wf.workflow_id, "name": new_wf.name, "source": "template"},
    )
    return _wf_to_dict(new_wf)


# ── Compatibility aliases (old singular /workflow endpoints) ──────

@router.get("/agents/{agent_id}/workflow", summary="Get active workflow DSL (compat)")
async def get_workflow_compat(request: Request, agent_id: str):
    from .workflow.store import WorkflowStore
    registry = _get_registry(request)
    store = WorkflowStore(registry._pg)
    active_id = await registry.knowledge.load_active_workflow(agent_id)
    wf = await store.get_or_create(agent_id, active_workflow_id=active_id)
    return _wf_to_dict(wf)


@router.put("/agents/{agent_id}/workflow", summary="Update active workflow DSL (compat)")
async def update_workflow_compat(request: Request, agent_id: str, payload: WorkflowDSLPayload):
    from .workflow.models import WorkflowDSL
    from .workflow.store import WorkflowStore

    registry = _get_registry(request)
    store = WorkflowStore(registry._pg)
    active_id = await registry.knowledge.load_active_workflow(agent_id)
    wf = await store.get_or_create(agent_id, active_workflow_id=active_id)

    dsl = WorkflowDSL.model_validate(payload.dsl)
    await store.save_dsl(wf.workflow_id, dsl)

    await registry.notifications.notify(
        agent_id=agent_id,
        event_type="workflow_changed",
        data={"workflow_id": wf.workflow_id, "source": "manual"},
    )
    return {"workflow_id": wf.workflow_id, "status": "updated", "node_count": len(dsl.nodes)}


@router.post("/agents/{agent_id}/workflow/runs", summary="Start run on active workflow (compat)")
async def start_workflow_run_compat(request: Request, agent_id: str, payload: WorkflowRunPayload):
    from .workflow.store import WorkflowStore
    from .workflow.runtime import WorkflowRuntime

    registry = _get_registry(request)
    store = WorkflowStore(registry._pg)
    active_id = await registry.knowledge.load_active_workflow(agent_id)
    wf = await store.get_or_create(agent_id, active_workflow_id=active_id)

    rt = WorkflowRuntime(
        pg=registry._pg,
        notification_mgr=registry.notifications,
        celery_app=getattr(registry, '_celery_app', None),
    )
    summary = await rt.start_run(
        agent_id=agent_id,
        workflow_id=wf.workflow_id,
        mode=payload.mode,
        inputs=payload.inputs,
    )
    return summary


@router.get("/agents/{agent_id}/workflow/runs", summary="List workflow runs")
async def list_workflow_runs(
    request: Request,
    agent_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    from .workflow.store import WorkflowStore
    registry = _get_registry(request)
    store = WorkflowStore(registry._pg)
    runs = await store.list_runs(agent_id, limit=limit, offset=offset)
    return {"runs": runs, "total": len(runs)}


@router.get("/workflow/runs/{run_id}", summary="Get workflow run detail + steps")
async def get_workflow_run(request: Request, run_id: str):
    from .workflow.store import WorkflowStore
    registry = _get_registry(request)
    store = WorkflowStore(registry._pg)
    run = await store.get_run(run_id)
    if not run:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Run not found")
    steps = await store.get_steps(run_id)
    return {"run": run, "steps": steps}


class ApproveRunPayload(BaseModel):
    decision: str = "approve"


@router.post("/workflow/runs/{run_id}/approve", summary="Approve or reject a paused HITL run")
async def approve_workflow_run(request: Request, run_id: str, payload: ApproveRunPayload):
    """Resume a workflow run that is awaiting human approval.

    ``decision`` can be ``approve`` (continue execution) or ``reject`` (fail the run).
    """
    from .workflow.runtime import WorkflowRuntime

    registry = _get_registry(request)
    rt = WorkflowRuntime(
        pg=registry._pg,
        notification_mgr=registry.notifications,
        celery_app=getattr(registry, '_celery_app', None),
    )
    try:
        summary = await rt.resume_run(run_id, payload.decision)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return summary


@router.post("/agents/{agent_id}/workflow/publish", summary="Publish active workflow (compat)")
async def publish_workflow_compat(request: Request, agent_id: str):
    from .workflow.store import WorkflowStore
    registry = _get_registry(request)
    store = WorkflowStore(registry._pg)
    active_id = await registry.knowledge.load_active_workflow(agent_id)
    wf = await store.get_or_create(agent_id, active_workflow_id=active_id)

    new_version = await store.publish(wf.workflow_id)

    await registry.notifications.notify(
        agent_id=agent_id,
        event_type="workflow_published",
        data={"workflow_id": wf.workflow_id, "version": new_version},
    )
    return {"workflow_id": wf.workflow_id, "version": new_version, "status": "published"}


@router.get("/graphrag-endpoints/{endpoint_id}", summary="Get GraphRAG endpoint details")
async def get_graphrag_endpoint(request: Request, endpoint_id: str):
    from .workflow.store import WorkflowStore
    registry = _get_registry(request)
    store = WorkflowStore(registry._pg)
    ep = await store.get_graphrag_endpoint(endpoint_id)
    if not ep:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Endpoint not found")
    return ep


@router.get("/agents/{agent_id}/graphrag-endpoint", summary="Get active GraphRAG endpoint for agent")
async def get_active_graphrag_endpoint(request: Request, agent_id: str):
    from .workflow.store import WorkflowStore
    registry = _get_registry(request)
    store = WorkflowStore(registry._pg)
    ep = await store.get_active_endpoint(agent_id)
    return ep or {"status": "none", "message": "No active endpoint. Publish a workflow first."}
