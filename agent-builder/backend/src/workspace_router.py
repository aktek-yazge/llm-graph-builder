"""
Workspace Router
================

Document Processing Workspace API endpoint'leri.
All metadata now stored in PostgreSQL.
Neo4j only used for Knowledge Base extraction queries.
"""

import json
import logging
import os
import re
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from .models import (
    AgentChatRequest,
    AgentChatResponse,
    BatchJob,
    BatchJobStatus,
    BatchProgress,
    RichMessagePart,
    ReviewQueue,
    SchemaApproval,
    Workspace,
    WorkspaceCreate,
    WorkspaceStatus,
    WorkspaceSummary,
)
from .event_store.postgres_client import PostgresClient, get_postgres_client
from .workspace_repository import WorkspaceRepository
from .agent_repository import AgentRepository
from .comms_repository import CommsRepository
from .event_store.models import EventType
from .agent.event_bus import AgentEventBus, EventTypes
from .router import _parse_rich_parts

logger = logging.getLogger(__name__)


def _get_event_bus() -> AgentEventBus:
    return AgentEventBus.get_instance()

router = APIRouter(prefix="/api/v2/workspaces", tags=["Workspaces"])

UPLOAD_DIR = Path(os.getenv("WORKSPACE_UPLOAD_DIR", "/tmp/workspace_uploads"))


async def _get_ws_repo() -> WorkspaceRepository:
    pg = await get_postgres_client()
    return WorkspaceRepository(pg)


async def _get_agent_repo() -> AgentRepository:
    pg = await get_postgres_client()
    return AgentRepository(pg)


def _get_orchestrator(ws_repo: WorkspaceRepository):
    from .agent.batch_orchestrator import BatchOrchestrator
    return BatchOrchestrator(ws_repo)


# =============================================================================
# WORKSPACE CRUD
# =============================================================================

@router.post("", response_model=Workspace, summary="Create workspace")
async def create_workspace(request: WorkspaceCreate):
    """Yeni Document Processing Workspace olustur. Otomatik Workspace Agent olusturulur."""
    ws_id = f"ws-{uuid.uuid4().hex[:12]}"
    agent_id = f"ws-agent-{uuid.uuid4().hex[:12]}"

    agent_repo = await _get_agent_repo()
    ws_repo = await _get_ws_repo()

    await agent_repo.create_agent({
        "id": agent_id,
        "name": f"{request.name} Agent",
        "description": f"Workspace '{request.name}' icin rehber agent",
        "agent_type": "workspace_agent",
        "tenant_id": request.tenant_id,
        "status": "active",
        "workspace_id": ws_id,
    })

    await ws_repo.create_workspace({
        "id": ws_id,
        "name": request.name,
        "description": request.description,
        "tenant_id": request.tenant_id,
        "status": "created",
        "ocr_mode": request.ocr_mode,
        "batch_size": request.batch_size,
        "agent_id": agent_id,
    })

    logger.info("Created workspace %s with agent %s", ws_id, agent_id)

    return Workspace(
        id=ws_id,
        name=request.name,
        description=request.description,
        tenant_id=request.tenant_id,
        status=WorkspaceStatus.CREATED,
        ocr_mode=request.ocr_mode,
        batch_size=request.batch_size,
        agent_id=agent_id,
    )


@router.get("", response_model=List[WorkspaceSummary], summary="List workspaces")
async def list_workspaces(tenant_id: str = Query(...)):
    ws_repo = await _get_ws_repo()
    rows = await ws_repo.list_workspaces(tenant_id)
    return [
        WorkspaceSummary(
            id=r["id"],
            name=r["name"],
            status=r["status"],
            document_count=r.get("document_count", 0),
            processed_count=r.get("processed_count", 0),
            success_rate=round(
                (r.get("successful_count", 0) / max(r.get("document_count", 1), 1)) * 100, 1,
            ),
            created_at=r.get("created_at"),
        )
        for r in rows
    ]


@router.get("/{workspace_id}", response_model=Workspace, summary="Get workspace")
async def get_workspace(workspace_id: str):
    ws_repo = await _get_ws_repo()
    ws = await ws_repo.get_workspace(workspace_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return Workspace(**{k: v for k, v in ws.items() if k in Workspace.model_fields})


# =============================================================================
# PHASE 1: SAMPLE UPLOAD
# =============================================================================

@router.post("/{workspace_id}/upload-samples", summary="Upload sample documents")
async def upload_samples(
    workspace_id: str,
    files: List[UploadFile] = File(...),
):
    ws_repo = await _get_ws_repo()
    ws = await ws_repo.get_workspace(workspace_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")

    ws_upload_dir = UPLOAD_DIR / workspace_id / "samples"
    ws_upload_dir.mkdir(parents=True, exist_ok=True)

    sample_ids = []
    for f in files:
        file_id = f"sample-{uuid.uuid4().hex[:8]}"
        ext = Path(f.filename or "doc").suffix or ".pdf"
        save_path = ws_upload_dir / f"{file_id}{ext}"

        content = await f.read()
        save_path.write_bytes(content)

        await ws_repo.create_document({
            "id": file_id,
            "workspace_id": workspace_id,
            "file_path": str(save_path),
            "file_name": f.filename or file_id,
            "status": "queued",
            "is_sample": True,
        })
        sample_ids.append(file_id)

    await ws_repo.update_workspace(workspace_id, {
        "status": "sampling",
        "sample_count": len(files),
    })

    logger.info("Uploaded %d samples to workspace %s", len(files), workspace_id)
    return {
        "workspace_id": workspace_id,
        "sample_ids": sample_ids,
        "file_count": len(files),
        "message": f"{len(files)} ornek belge yuklendi. Schema analizi baslatiliyor...",
    }


# =============================================================================
# PHASE 1.5a: EXTRACT SAMPLE TEXT (OCR only)
# =============================================================================

@router.post("/{workspace_id}/extract-text", summary="OCR sample documents")
async def extract_sample_text(workspace_id: str):
    from .agent.workspace_agent import WorkspaceAgent
    from .ontology import get_ontology_client

    db = await get_ontology_client()
    agent = WorkspaceAgent(db)
    result = await agent.extract_sample_text(workspace_id)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


# =============================================================================
# PHASE 1.5b: SAMPLE SUMMARY
# =============================================================================

@router.get("/{workspace_id}/sample-summary", summary="Get OCR text summary")
async def get_sample_summary(workspace_id: str):
    from .agent.workspace_agent import WorkspaceAgent
    from .ontology import get_ontology_client

    db = await get_ontology_client()
    agent = WorkspaceAgent(db)
    result = await agent.get_sample_summary(workspace_id)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


# =============================================================================
# PHASE 1.5c: CREATE SKILL FROM USER INTENT
# =============================================================================

class CreateSkillRequest(BaseModel):
    user_intent: str
    extraction_rules: Optional[List[str]] = None


@router.post("/{workspace_id}/create-skill", summary="Create extraction skill from intent")
async def create_skill_from_intent(workspace_id: str, request: CreateSkillRequest):
    from .agent.workspace_agent import WorkspaceAgent
    from .ontology import get_ontology_client

    db = await get_ontology_client()
    agent = WorkspaceAgent(db)
    result = await agent.create_skill_from_intent(
        workspace_id,
        user_intent=request.user_intent,
        extraction_rules=request.extraction_rules,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


# =============================================================================
# PHASE 1.5d: MCP-BACKED SCHEMA ANALYSIS
# =============================================================================

class MCPAnalyzeRequest(BaseModel):
    minio_bucket: str = "documents"
    minio_prefix: str = ""
    domain_hint: str = ""
    max_samples: int = 5
    auto_approve: bool = False


@router.post("/{workspace_id}/analyze-mcp", summary="Analyze samples via MCP")
async def analyze_via_mcp(workspace_id: str, request: MCPAnalyzeRequest):
    from .agent.workspace_agent import WorkspaceAgent
    from .ontology import get_ontology_client

    db = await get_ontology_client()
    agent = WorkspaceAgent(db)
    result = await agent.analyze_via_mcp(
        workspace_id,
        minio_bucket=request.minio_bucket,
        minio_prefix=request.minio_prefix,
        domain_hint=request.domain_hint,
        max_samples=request.max_samples,
        auto_approve=request.auto_approve,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


# =============================================================================
# LEGACY: ANALYZE SAMPLES
# =============================================================================

@router.post("/{workspace_id}/analyze-samples", summary="Analyze uploaded samples (legacy)")
async def analyze_samples(workspace_id: str):
    from .agent.workspace_agent import WorkspaceAgent
    from .ontology import get_ontology_client

    db = await get_ontology_client()
    agent = WorkspaceAgent(db)
    result = await agent.analyze_samples(workspace_id)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


# =============================================================================
# PHASE 1.6: CREATE KB AGENT
# =============================================================================

class CreateKBAgentRequest(BaseModel):
    custom_name: str = ""
    custom_purpose: str = ""
    minio_bucket: str = ""
    minio_prefix: str = ""
    auto_deploy: bool = False


@router.post("/{workspace_id}/create-kb-agent", summary="Create KB Agent from approved schema")
async def create_kb_agent(workspace_id: str, request: CreateKBAgentRequest):
    ws_repo = await _get_ws_repo()
    ws = await ws_repo.get_workspace(workspace_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")

    from .agent.kb_agent_factory import KBAgentFactory

    tenant_id = ws.get("tenant_id", "default-tenant")
    agent_repo = await _get_agent_repo()
    factory = KBAgentFactory(agent_repo, ws_repo)
    result = await factory.create_from_workspace(
        workspace_id=workspace_id,
        tenant_id=tenant_id,
        auto_deploy=request.auto_deploy,
        custom_name=request.custom_name,
        custom_purpose=request.custom_purpose,
        minio_bucket=request.minio_bucket,
        minio_prefix=request.minio_prefix,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


# =============================================================================
# PHASE 2: SCHEMA APPROVAL
# =============================================================================

@router.get("/{workspace_id}/schema", summary="Get discovered schema")
async def get_workspace_schema(workspace_id: str):
    ws_repo = await _get_ws_repo()
    entities = await ws_repo.get_workspace_entity_schemas(workspace_id)
    relationships = await ws_repo.get_workspace_relationship_schemas(workspace_id)
    return {
        "workspace_id": workspace_id,
        "entity_schemas": entities,
        "relationship_schemas": relationships,
    }


@router.post("/{workspace_id}/approve-schema", summary="Approve schema")
async def approve_schema(workspace_id: str, approval: SchemaApproval):
    bus = _get_event_bus()
    if not approval.approved:
        await bus.publish(
            EventTypes.SCHEMA_REJECTED,
            {"workspace_id": workspace_id},
            workspace_id=workspace_id,
        )
        return {"workspace_id": workspace_id, "status": "schema_review", "message": "Schema onaylanmadi"}

    ws_repo = await _get_ws_repo()

    for es_id in approval.entity_schema_ids:
        await ws_repo.link_entity_schema(workspace_id, es_id)
    for rs_id in approval.relationship_schema_ids:
        await ws_repo.link_relationship_schema(workspace_id, rs_id)

    await ws_repo.update_workspace(workspace_id, {"status": "ready"})
    await bus.publish(
        EventTypes.SCHEMA_APPROVED,
        {"workspace_id": workspace_id, "entity_count": len(approval.entity_schema_ids), "relationship_count": len(approval.relationship_schema_ids)},
        workspace_id=workspace_id,
    )
    return {"workspace_id": workspace_id, "status": "ready", "message": "Schema onaylandi"}


# =============================================================================
# PHASE 3: BATCH UPLOAD & PROCESSING
# =============================================================================

@router.post("/{workspace_id}/upload-batch", summary="Upload batch documents")
async def upload_batch_documents(
    workspace_id: str,
    files: List[UploadFile] = File(...),
):
    ws_repo = await _get_ws_repo()
    ws = await ws_repo.get_workspace(workspace_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")

    ws_upload_dir = UPLOAD_DIR / workspace_id / "batch"
    ws_upload_dir.mkdir(parents=True, exist_ok=True)

    file_paths = []
    for f in files:
        file_id = f"doc-{uuid.uuid4().hex[:8]}"
        ext = Path(f.filename or "doc").suffix or ".pdf"
        save_path = ws_upload_dir / f"{file_id}{ext}"
        content = await f.read()
        save_path.write_bytes(content)
        file_paths.append(str(save_path))

    orchestrator = _get_orchestrator(ws_repo)
    job_result = await orchestrator.create_batch_job(workspace_id, file_paths)

    await ws_repo.update_workspace(workspace_id, {
        "document_count": (ws.get("document_count", 0) or 0) + len(files),
    })

    logger.info("Uploaded %d batch docs to workspace %s", len(files), workspace_id)
    return {
        "workspace_id": workspace_id,
        "batch_job_id": job_result["batch_job_id"],
        "files_uploaded": len(files),
        "message": f"{len(files)} belge yuklendi.",
    }


@router.post("/{workspace_id}/start-processing", summary="Start batch processing")
async def start_processing(
    workspace_id: str,
    batch_job_id: str = Query(...),
    pipeline: str = Query(default=""),
):
    ws_repo = await _get_ws_repo()
    orchestrator = _get_orchestrator(ws_repo)
    result = await orchestrator.start_processing(workspace_id, batch_job_id, pipeline=pipeline)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    bus = _get_event_bus()
    await bus.publish(
        EventTypes.PROCESSING_STARTED,
        {"batch_job_id": batch_job_id, "workspace_id": workspace_id, "pipeline": pipeline},
        workspace_id=workspace_id,
    )
    return result


# =============================================================================
# PHASE 4: PROGRESS & REVIEW
# =============================================================================

@router.get("/{workspace_id}/progress", summary="Stream batch progress (SSE)")
async def stream_progress(workspace_id: str, batch_job_id: str = Query(...)):
    ws_repo = await _get_ws_repo()
    orchestrator = _get_orchestrator(ws_repo)

    async def event_generator():
        async for progress in orchestrator.stream_progress(batch_job_id):
            progress["workspace_id"] = workspace_id
            yield {"data": json.dumps(progress, ensure_ascii=False, default=str)}

    return EventSourceResponse(event_generator())


@router.get("/{workspace_id}/progress-snapshot", summary="Get progress snapshot")
async def get_progress_snapshot(workspace_id: str, batch_job_id: str = Query(...)):
    ws_repo = await _get_ws_repo()
    orchestrator = _get_orchestrator(ws_repo)
    progress = await orchestrator.get_progress(batch_job_id)
    progress["workspace_id"] = workspace_id
    return progress


@router.get("/{workspace_id}/review-queue", summary="Get review queue")
async def get_review_queue(
    workspace_id: str,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0),
):
    ws_repo = await _get_ws_repo()
    orchestrator = _get_orchestrator(ws_repo)
    return await orchestrator.get_review_queue(workspace_id, limit, offset)


@router.post("/{workspace_id}/approve-batch", summary="Approve review items")
async def approve_review_items(
    workspace_id: str,
    document_ids: List[str],
    action: str = Query(default="approve"),
):
    ws_repo = await _get_ws_repo()
    orchestrator = _get_orchestrator(ws_repo)
    return await orchestrator.approve_review_items(workspace_id, document_ids, action)


@router.get("/{workspace_id}/stats", summary="Get workspace stats")
async def get_workspace_stats(workspace_id: str):
    ws_repo = await _get_ws_repo()
    orchestrator = _get_orchestrator(ws_repo)
    return await orchestrator.get_workspace_stats(workspace_id)


# =============================================================================
# ELICITATION QUEUE
# =============================================================================

@router.get("/{workspace_id}/elicitation-queue", summary="Get elicitation queue")
async def get_elicitation_queue(
    workspace_id: str,
    status: str = Query(default="pending"),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0),
):
    ws_repo = await _get_ws_repo()
    orchestrator = _get_orchestrator(ws_repo)
    return await orchestrator.get_elicitation_queue(workspace_id, status, limit, offset)


class ElicitationResolveRequest(BaseModel):
    action: str = "accept"
    modified_result: Optional[str] = None


@router.post("/{workspace_id}/elicitation/{request_id}/resolve", summary="Resolve elicitation")
async def resolve_elicitation(
    workspace_id: str,
    request_id: str,
    request: ElicitationResolveRequest,
):
    ws_repo = await _get_ws_repo()
    orchestrator = _get_orchestrator(ws_repo)
    result = await orchestrator.resolve_elicitation(
        request_id=request_id,
        action=request.action,
        modified_result=request.modified_result,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


class ElicitationCreateRequest(BaseModel):
    doc_id: str
    workspace_id: str
    batch_job_id: str = ""
    extraction_result: str = "{}"
    confidence_score: float = 0.0
    file_name: str = ""


@router.post("/elicitation-request", summary="Create elicitation request (Celery callback)")
async def create_elicitation_request(request: ElicitationCreateRequest):
    ws_repo = await _get_ws_repo()
    orchestrator = _get_orchestrator(ws_repo)
    return await orchestrator.create_elicitation_request(
        doc_id=request.doc_id,
        workspace_id=request.workspace_id,
        batch_job_id=request.batch_job_id,
        extraction_result=request.extraction_result,
        confidence_score=request.confidence_score,
        file_name=request.file_name,
    )


# =============================================================================
# DOCUMENT STATUS CALLBACK
# =============================================================================

@router.post("/callback/document-status", summary="Document status callback")
async def document_status_callback(
    doc_id: str = Query(...),
    status: str = Query(...),
    confidence_score: float = Query(default=0.0),
    extraction_result: Optional[str] = None,
    error_message: Optional[str] = None,
    workspace_id: str = Query(default=""),
):
    ws_repo = await _get_ws_repo()
    orchestrator = _get_orchestrator(ws_repo)
    await orchestrator.update_document_status(
        doc_id, status, confidence_score, extraction_result, error_message,
    )

    bus = _get_event_bus()
    event_type = EventTypes.PROCESSING_PROGRESS if status in ("processed", "completed") else EventTypes.PROCESSING_FAILED if status == "failed" else EventTypes.PROCESSING_PROGRESS
    await bus.publish(
        event_type,
        {"doc_id": doc_id, "status": status, "confidence_score": confidence_score, "error": error_message},
        workspace_id=workspace_id,
    )

    try:
        from .dependencies import get_event_store
        from .event_store.models import GraphEvent

        es = await get_event_store()
        await es.record_event(GraphEvent(
            tenant_id="default-tenant",
            event_type=EventType.UPDATE_NODE,
            entity_type="WorkspaceDocument",
            entity_id=doc_id,
            after_state={
                "status": status,
                "confidence_score": confidence_score,
                "has_extraction": bool(extraction_result),
                "error": error_message,
            },
            metadata={"source": "celery_callback", "step": status},
        ))
    except Exception as ev_err:
        logger.warning("Event recording for callback failed: %s", ev_err)

    return {"ok": True}


# =============================================================================
# MONITORING
# =============================================================================

@router.get("/{workspace_id}/monitoring", summary="Consolidated monitoring data")
async def get_workspace_monitoring(workspace_id: str):
    ws_repo = await _get_ws_repo()
    orchestrator = _get_orchestrator(ws_repo)

    stats = await orchestrator.get_workspace_stats(workspace_id)
    if "error" in stats:
        raise HTTPException(status_code=404, detail=stats["error"])

    batch_jobs = await ws_repo.list_batch_jobs(workspace_id)
    active_batch = None
    for bj in batch_jobs:
        if bj.get("status") in ("processing", "queued"):
            try:
                active_batch = await orchestrator.get_progress(bj["id"])
            except Exception:
                active_batch = {"batch_job_id": bj["id"], "status": bj["status"]}
            break

    elicitation = await orchestrator.get_elicitation_queue(workspace_id, limit=5)

    try:
        from .agent.event_bus import AgentEventBus
        bus = AgentEventBus.get_instance()
        recent_events = bus.get_history(workspace_id=workspace_id, limit=20)
    except Exception:
        recent_events = []

    try:
        pg = await get_postgres_client()
        comms = CommsRepository(pg)
        from .agent.blackboard import Blackboard
        bb = Blackboard(comms)
        topics = await bb.list_topics(workspace_id=workspace_id)
    except Exception:
        topics = []

    return {
        "workspace_id": workspace_id,
        "stats": stats,
        "active_batch": active_batch,
        "elicitation": {
            "total": elicitation.get("total", 0),
            "pending": elicitation.get("pending", 0),
            "accepted": elicitation.get("accepted", 0),
            "rejected": elicitation.get("rejected", 0),
            "recent_items": elicitation.get("items", [])[:5],
        },
        "recent_events": recent_events,
        "blackboard_topics": topics,
    }


# =============================================================================
# EVENT LOG
# =============================================================================

@router.get("/{workspace_id}/event-log", summary="Workspace event log")
async def get_workspace_event_log(
    workspace_id: str,
    last_n: int = Query(default=50, le=500),
):
    ws_repo = await _get_ws_repo()
    ws = await ws_repo.get_workspace(workspace_id)
    if not ws:
        return {"workspace_id": workspace_id, "events": [], "total": 0}

    tenant_id = ws.get("tenant_id", "default-tenant")
    all_docs = await ws_repo.list_documents(workspace_id, limit=10000)
    doc_ids = {d["id"] for d in all_docs}

    if not doc_ids:
        return {"workspace_id": workspace_id, "events": [], "total": 0}

    try:
        from .dependencies import get_event_store
        from .event_store.models import EventFilter

        es = await get_event_store()
        f = EventFilter(tenant_id=tenant_id, entity_type="WorkspaceDocument", limit=last_n)
        events = await es.get_events(f)
        ws_events = [e.model_dump(mode="json") for e in events if e.entity_id in doc_ids]
        return {"workspace_id": workspace_id, "events": ws_events[:last_n], "total": len(ws_events)}
    except Exception as e:
        logger.warning("Event log query failed: %s", e)
        return {"workspace_id": workspace_id, "events": [], "total": 0, "error": str(e)}


# =============================================================================
# WORKSPACE CHAT
# =============================================================================

@router.get("/{workspace_id}/chat/history", summary="Get workspace chat history")
async def get_workspace_chat_history(workspace_id: str):
    from .chat_repository import ChatRepository
    pg = await get_postgres_client()
    chat_repo = ChatRepository(pg)

    session = await chat_repo.get_latest_workspace_session(workspace_id)
    if not session:
        return {"session_id": None, "messages": []}

    session_id = session["id"]
    messages = await chat_repo.get_messages(session_id)

    return {
        "session_id": session_id,
        "messages": [
            {
                "role": m["role"],
                "content": m["content"],
                "created_at": m["created_at"].isoformat() if m.get("created_at") else None,
            }
            for m in messages
        ],
    }


@router.post("/{workspace_id}/chat/stream", summary="Chat with workspace agent (SSE streaming)")
async def workspace_chat_stream(workspace_id: str, request: AgentChatRequest):
    """SSE streaming chat endpoint. Yields events: message, tool_call, tool_result, done."""
    from starlette.responses import StreamingResponse
    from .agent.agent_runtime import AgentRuntime
    from .agent.ocr_bridge import OCRBridge
    from .gateway import get_gateway_client
    from .ontology import get_ontology_client

    ws_repo = await _get_ws_repo()
    ws = await ws_repo.get_workspace(workspace_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")

    agent_id = ws.get("agent_id")
    if not agent_id:
        raise HTTPException(status_code=400, detail="Workspace has no agent.")

    try:
        gateway = await get_gateway_client()
    except Exception:
        gateway = None

    db = await get_ontology_client()
    pg = await get_postgres_client()

    from .chat_repository import ChatRepository
    runtime = AgentRuntime(
        db, gateway, OCRBridge(),
        chat_repo=ChatRepository(pg),
        agent_repo=AgentRepository(pg),
    )
    session = await runtime.start_agent(
        agent_id=agent_id,
        tenant_id=ws.get("tenant_id", "default"),
        session_id=request.session_id or None,
        workspace_id=workspace_id,
        resource_id=ws.get("resource_id", ""),
    )

    async def _sse_generator():
        full_response = ""
        try:
            async for chunk in runtime.chat(session.session_id, request.message):
                ctype = chunk.get("type", "")
                if ctype == "message_chunk":
                    text = chunk.get("content", "")
                    full_response += text
                    yield f"event: message\ndata: {json.dumps({'text': text}, ensure_ascii=False)}\n\n"
                elif ctype == "tool_call":
                    yield f"event: tool_call\ndata: {json.dumps({'name': chunk.get('name', ''), 'args': chunk.get('args', {})}, ensure_ascii=False, default=str)}\n\n"
                elif ctype == "tool_result":
                    yield f"event: tool_result\ndata: {json.dumps({'name': chunk.get('name', ''), 'result': chunk.get('result', '')}, ensure_ascii=False, default=str)}\n\n"
                elif ctype == "final_response":
                    full_response = chunk.get("content", full_response)

            rich_parts_raw = _parse_rich_parts(full_response)
            clean_text = re.sub(r'\[RICH:\s*\w+(?:\([^)]*\))?\]', '', full_response).strip()
            yield f"event: done\ndata: {json.dumps({'full_response': clean_text, 'session_id': session.session_id, 'agent_id': agent_id, 'rich_parts': [rp.model_dump() if hasattr(rp, 'model_dump') else rp for rp in rich_parts_raw]}, ensure_ascii=False, default=str)}\n\n"
        except Exception as e:
            logger.error("SSE chat stream error: %s", e)
            yield f"event: error\ndata: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.post("/{workspace_id}/chat", response_model=AgentChatResponse, summary="Chat with workspace agent")
async def workspace_chat(workspace_id: str, request: AgentChatRequest):
    from .agent.agent_runtime import AgentRuntime
    from .agent.ocr_bridge import OCRBridge
    from .gateway import get_gateway_client
    from .ontology import get_ontology_client

    ws_repo = await _get_ws_repo()
    ws = await ws_repo.get_workspace(workspace_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")

    agent_id = ws.get("agent_id")
    if not agent_id:
        raise HTTPException(status_code=400, detail="Workspace has no agent. Please recreate the workspace.")

    try:
        gateway = await get_gateway_client()
    except Exception:
        gateway = None

    db = await get_ontology_client()
    pg = await get_postgres_client()

    from .chat_repository import ChatRepository
    runtime = AgentRuntime(
        db, gateway, OCRBridge(),
        chat_repo=ChatRepository(pg),
        agent_repo=AgentRepository(pg),
    )
    session = await runtime.start_agent(
        agent_id=agent_id,
        tenant_id=ws.get("tenant_id", "default"),
        session_id=request.session_id or None,
        workspace_id=workspace_id,
        resource_id=ws.get("resource_id", ""),
    )

    full_response = ""
    async for chunk in runtime.chat(session.session_id, request.message):
        if chunk.get("type") == "final_response":
            full_response = chunk.get("content", "")
        elif chunk.get("type") == "message_chunk":
            full_response += chunk.get("content", "")

    rich_parts = _parse_rich_parts(full_response)
    clean_text = re.sub(r'\[RICH:\s*\w+(?:\([^)]*\))?\]', '', full_response).strip()
    phase = ws.get("status", "created")

    return AgentChatResponse(
        response=clean_text,
        session_id=session.session_id,
        agent_id=agent_id,
        rich_parts=rich_parts,
        phase=phase,
    )
