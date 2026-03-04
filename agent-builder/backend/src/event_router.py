"""
Event Store Router
==================

Event history, rollback, snapshot yonetimi API endpoint'leri.
"""

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from .dependencies import get_event_store, get_ontology_db, get_mutation_gateway_for_tenant
from .event_store import EventStore, EventFilter, EventType
from .mutation_gateway import RollbackManager
from .ontology.neo4j_client import OntologyDBClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/events", tags=["Events"])


# =============================================================================
# REQUEST/RESPONSE MODELS
# =============================================================================

class RollbackStepsRequest(BaseModel):
    tenant_id: str
    steps: int = 1


class RollbackToSnapshotRequest(BaseModel):
    tenant_id: str
    snapshot_name: str


class RollbackToTimestampRequest(BaseModel):
    tenant_id: str
    timestamp: datetime


class SnapshotCreateRequest(BaseModel):
    tenant_id: str
    name: str
    description: Optional[str] = None


class EventResponse(BaseModel):
    id: Optional[str] = None
    sequence_no: Optional[int] = None
    tenant_id: str
    user_id: Optional[str] = None
    event_type: str
    entity_type: str
    entity_id: str
    before_state: Optional[dict] = None
    after_state: Optional[dict] = None
    metadata: Optional[dict] = None
    is_compensation: bool = False
    created_at: Optional[datetime] = None


# =============================================================================
# EVENT QUERY ENDPOINTS
# =============================================================================

@router.get("", summary="Query events")
async def query_events(
    tenant_id: str = Query(...),
    entity_id: Optional[str] = Query(default=None),
    entity_type: Optional[str] = Query(default=None),
    event_type: Optional[str] = Query(default=None),
    user_id: Optional[str] = Query(default=None),
    session_id: Optional[str] = Query(default=None),
    since: Optional[datetime] = Query(default=None),
    until: Optional[datetime] = Query(default=None),
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0),
    es: EventStore = Depends(get_event_store),
):
    """Event'leri filtrele ve sorgula."""
    et = None
    if event_type:
        try:
            et = EventType(event_type)
        except ValueError:
            raise HTTPException(400, f"Invalid event_type: {event_type}")

    events = await es.get_events(EventFilter(
        tenant_id=tenant_id,
        entity_id=entity_id,
        entity_type=entity_type,
        event_type=et,
        user_id=user_id,
        session_id=session_id,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    ))

    return [_event_to_dict(e) for e in events]


@router.get("/entity/{entity_id}/history", summary="Entity history")
async def entity_history(
    entity_id: str,
    tenant_id: str = Query(...),
    limit: int = Query(default=100, le=500),
    es: EventStore = Depends(get_event_store),
):
    """Tek bir entity'nin tum degisiklik gecmisi."""
    events = await es.get_entity_history(tenant_id, entity_id, limit)
    return [_event_to_dict(e) for e in events]


@router.get("/stats", summary="Event store statistics")
async def event_stats(
    tenant_id: str = Query(...),
    es: EventStore = Depends(get_event_store),
):
    """Event store istatistikleri."""
    from .mutation_gateway import AuditTrail
    from .event_store import get_postgres_client

    pg = await get_postgres_client()
    audit = AuditTrail(es, pg)
    return await audit.get_event_stats(tenant_id)


# =============================================================================
# ROLLBACK ENDPOINTS
# =============================================================================

@router.post("/rollback/steps", summary="Rollback last N steps")
async def rollback_steps(
    request: RollbackStepsRequest,
    db: OntologyDBClient = Depends(get_ontology_db),
    es: EventStore = Depends(get_event_store),
):
    """Son N yazma islemini geri al (compensation event'ler olusturur)."""
    rollback_mgr = RollbackManager(
        db=db, event_store=es,
        tenant_id=request.tenant_id,
    )
    compensations = await rollback_mgr.rollback_steps(request.steps)
    return {
        "rolled_back": len(compensations),
        "compensations": [_event_to_dict(c) for c in compensations],
    }


@router.post("/rollback/to-snapshot", summary="Rollback to snapshot")
async def rollback_to_snapshot(
    request: RollbackToSnapshotRequest,
    db: OntologyDBClient = Depends(get_ontology_db),
    es: EventStore = Depends(get_event_store),
):
    """Belirtilen snapshot'a kadar geri al."""
    rollback_mgr = RollbackManager(
        db=db, event_store=es,
        tenant_id=request.tenant_id,
    )
    try:
        compensations = await rollback_mgr.rollback_to_snapshot(request.snapshot_name)
    except ValueError as e:
        raise HTTPException(404, str(e))

    return {
        "rolled_back": len(compensations),
        "snapshot": request.snapshot_name,
        "compensations": [_event_to_dict(c) for c in compensations],
    }


@router.post("/rollback/to-timestamp", summary="Rollback to timestamp")
async def rollback_to_timestamp(
    request: RollbackToTimestampRequest,
    db: OntologyDBClient = Depends(get_ontology_db),
    es: EventStore = Depends(get_event_store),
):
    """Belirtilen zaman damgasina kadar geri al."""
    rollback_mgr = RollbackManager(
        db=db, event_store=es,
        tenant_id=request.tenant_id,
    )
    compensations = await rollback_mgr.rollback_to_timestamp(request.timestamp)
    return {
        "rolled_back": len(compensations),
        "target_time": request.timestamp.isoformat(),
        "compensations": [_event_to_dict(c) for c in compensations],
    }


# =============================================================================
# SNAPSHOT ENDPOINTS
# =============================================================================

@router.post("/snapshots", summary="Create snapshot")
async def create_snapshot(
    request: SnapshotCreateRequest,
    es: EventStore = Depends(get_event_store),
):
    """Mevcut durumun snapshot'ini olustur (rollback noktasi)."""
    snap = await es.create_snapshot(
        tenant_id=request.tenant_id,
        name=request.name,
        description=request.description,
    )
    return {
        "id": str(snap.id) if snap.id else None,
        "name": snap.name,
        "sequence_no": snap.sequence_no,
        "created_at": snap.created_at.isoformat() if snap.created_at else None,
    }


@router.get("/snapshots", summary="List snapshots")
async def list_snapshots(
    tenant_id: str = Query(...),
    es: EventStore = Depends(get_event_store),
):
    """Tenant'in tum snapshot'larini listele."""
    snapshots = await es.list_snapshots(tenant_id)
    return [
        {
            "id": str(s.id) if s.id else None,
            "name": s.name,
            "description": s.description,
            "sequence_no": s.sequence_no,
            "created_by": s.created_by,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        }
        for s in snapshots
    ]


# =============================================================================
# DIFF ENDPOINT
# =============================================================================

@router.get("/diff", summary="Compare graph state between two timestamps")
async def diff_events(
    tenant_id: str = Query(...),
    time_a: datetime = Query(...),
    time_b: datetime = Query(...),
    es: EventStore = Depends(get_event_store),
):
    """Iki zaman damgasi arasindaki degisiklikleri goster."""
    from .mutation_gateway import AuditTrail
    from .event_store import get_postgres_client

    pg = await get_postgres_client()
    audit = AuditTrail(es, pg)
    return await audit.diff(tenant_id, time_a, time_b)


# =============================================================================
# HELPERS
# =============================================================================

def _event_to_dict(event) -> dict:
    return {
        "id": str(event.id) if event.id else None,
        "sequence_no": event.sequence_no,
        "tenant_id": event.tenant_id,
        "user_id": event.user_id,
        "event_type": event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type),
        "entity_type": event.entity_type,
        "entity_id": event.entity_id,
        "before_state": event.before_state,
        "after_state": event.after_state,
        "metadata": event.metadata,
        "is_compensation": event.is_compensation,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }
