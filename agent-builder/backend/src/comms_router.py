"""
Agent Communication Router
============================

Agent'lar arasi iletisim API endpoint'leri:
- Blackboard okuma/yazma
- Agent mesajlasma
- SSE event stream
- Event gecmisi sorgulama
"""

import asyncio
import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .event_store.postgres_client import get_postgres_client
from .comms_repository import CommsRepository
from .agent.blackboard import Blackboard
from .agent.event_bus import AgentEventBus, EventTypes

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/comms", tags=["Agent Communication"])


async def _get_bb() -> Blackboard:
    pg = await get_postgres_client()
    return Blackboard(CommsRepository(pg))


# =============================================================================
# REQUEST / RESPONSE MODELS
# =============================================================================

class BlackboardWriteRequest(BaseModel):
    topic: str
    content: str
    agent_id: str = ""
    workspace_id: str = ""
    entry_type: str = "info"
    metadata: Optional[Dict[str, Any]] = None


class AgentMessageRequest(BaseModel):
    from_agent: str
    to_agent: str
    message: str
    workspace_id: str = ""
    message_type: str = "direct"
    metadata: Optional[Dict[str, Any]] = None


class MarkReadRequest(BaseModel):
    message_ids: List[str]


class EventPublishRequest(BaseModel):
    event_type: str
    payload: Dict[str, Any] = {}
    source_agent: str = ""
    workspace_id: str = ""


# =============================================================================
# BLACKBOARD ENDPOINTS
# =============================================================================

@router.post("/blackboard", summary="Write to blackboard")
async def write_blackboard(request: BlackboardWriteRequest):
    bb = await _get_bb()
    result = await bb.write(
        topic=request.topic,
        content=request.content,
        agent_id=request.agent_id,
        workspace_id=request.workspace_id,
        entry_type=request.entry_type,
        metadata=request.metadata,
    )

    bus = AgentEventBus.get_instance()
    await bus.publish(
        "blackboard.updated",
        {"topic": request.topic, "agent_id": request.agent_id},
        source_agent=request.agent_id,
        workspace_id=request.workspace_id,
    )

    return result


@router.get("/blackboard", summary="Read blackboard entries")
async def read_blackboard(
    topic: str = Query(default=""),
    workspace_id: str = Query(default=""),
    agent_id: str = Query(default=""),
    entry_type: str = Query(default=""),
    limit: int = Query(default=50, le=200),
):
    bb = await _get_bb()
    entries = await bb.read(
        topic=topic,
        workspace_id=workspace_id,
        agent_id=agent_id,
        limit=limit,
        entry_type=entry_type,
    )
    return {"entries": entries, "count": len(entries)}


@router.get("/blackboard/topics", summary="List blackboard topics")
async def list_blackboard_topics(workspace_id: str = Query(default="")):
    bb = await _get_bb()
    topics = await bb.list_topics(workspace_id=workspace_id)
    return {"topics": topics}


# =============================================================================
# AGENT MESSAGING ENDPOINTS
# =============================================================================

@router.post("/messages", summary="Send agent message")
async def send_message(request: AgentMessageRequest):
    bb = await _get_bb()
    result = await bb.send_message(
        from_agent=request.from_agent,
        to_agent=request.to_agent,
        message=request.message,
        workspace_id=request.workspace_id,
        message_type=request.message_type,
        metadata=request.metadata,
    )

    bus = AgentEventBus.get_instance()
    await bus.publish(
        EventTypes.AGENT_MESSAGE,
        {
            "message_id": result.get("id"),
            "from_agent": request.from_agent,
            "to_agent": request.to_agent,
            "message_type": request.message_type,
        },
        source_agent=request.from_agent,
        workspace_id=request.workspace_id,
    )

    return result


@router.get("/messages/{agent_id}", summary="Get agent messages")
async def get_messages(
    agent_id: str,
    status: str = Query(default="unread"),
    workspace_id: str = Query(default=""),
    limit: int = Query(default=50, le=200),
):
    bb = await _get_bb()
    messages = await bb.get_messages(
        agent_id=agent_id,
        status=status,
        workspace_id=workspace_id,
        limit=limit,
    )
    return {"messages": messages, "count": len(messages)}


@router.post("/messages/mark-read", summary="Mark messages as read")
async def mark_messages_read(request: MarkReadRequest):
    bb = await _get_bb()
    updated = await bb.mark_read(request.message_ids)
    return {"updated": updated}


# =============================================================================
# EVENT BUS ENDPOINTS
# =============================================================================

@router.post("/events/publish", summary="Publish event")
async def publish_event(request: EventPublishRequest):
    bus = AgentEventBus.get_instance()
    event = await bus.publish(
        event_type=request.event_type,
        payload=request.payload,
        source_agent=request.source_agent,
        workspace_id=request.workspace_id,
    )
    return event.to_dict()


@router.get("/events/history", summary="Event history")
async def event_history(
    event_type: str = Query(default=""),
    workspace_id: str = Query(default=""),
    limit: int = Query(default=50, le=200),
):
    bus = AgentEventBus.get_instance()
    events = bus.get_history(
        event_type=event_type,
        workspace_id=workspace_id,
        limit=limit,
    )
    return {"events": events, "count": len(events)}


# =============================================================================
# SSE STREAM
# =============================================================================

@router.get("/events/stream", summary="SSE event stream")
async def event_stream(
    workspace_id: str = Query(default=""),
    request: Request = None,
):
    stream_id = f"sse-{uuid.uuid4().hex[:8]}"
    bus = AgentEventBus.get_instance()
    queue = bus.create_sse_stream(stream_id)

    async def _generate():
        try:
            yield "event: connected\ndata: {\"stream_id\": \"%s\"}\n\n" % stream_id
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    if workspace_id and event.workspace_id != workspace_id:
                        continue
                    yield event.to_sse()
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                if request and await request.is_disconnected():
                    break
        finally:
            bus.remove_sse_stream(stream_id)

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
