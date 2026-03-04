"""
Dashboard Router
================

System-wide monitoring dashboard API endpoint'leri.
Tum workspace'ler, agent'lar ve sistem durumunu tek bakista gosterir.
All data from PostgreSQL.
"""

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Query

from .event_store.postgres_client import get_postgres_client
from .workspace_repository import WorkspaceRepository
from .agent_repository import AgentRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/dashboard", tags=["Dashboard"])


@router.get("/overview", summary="System-wide dashboard overview")
async def dashboard_overview(tenant_id: str = Query(default="default")):
    pg = await get_postgres_client()
    ws_repo = WorkspaceRepository(pg)
    agent_repo = AgentRepository(pg)

    ws_list = await ws_repo.list_workspaces(tenant_id)

    total_docs = 0
    total_processed = 0
    total_successful = 0
    total_failed = 0
    total_in_progress = 0
    active_count = 0

    workspaces: List[Dict[str, Any]] = []
    active_batches: List[Dict[str, Any]] = []
    for r in ws_list:
        dc = r.get("document_count", 0) or 0
        pr = r.get("processed_count", 0) or 0
        su = r.get("successful_count", 0) or 0
        fa = r.get("failed_count", 0) or 0
        sr = round(su / max(dc, 1) * 100, 1)

        total_docs += dc
        total_processed += pr
        total_successful += su
        total_failed += fa

        status = r.get("status", "created")
        if status in ("processing", "sampling", "schema_review"):
            active_count += 1
            ip = dc - pr
            if ip < 0:
                ip = 0
            total_in_progress += ip
            active_batches.append({
                "workspace_id": r["id"],
                "batch_job_id": r.get("active_batch_id", r["id"]),
                "total": dc,
                "processed": pr,
                "percent_complete": round(pr / max(dc, 1) * 100, 1),
            })

        workspaces.append({
            "id": r["id"],
            "name": r["name"],
            "status": status,
            "document_count": dc,
            "processed": pr,
            "successful": su,
            "success_rate": sr,
            "created_at": str(r.get("created_at", "")) if r.get("created_at") else None,
        })

    agents_list = await agent_repo.list_agents(tenant_id)
    agents = [
        {
            "id": a["id"],
            "name": a["name"],
            "status": a.get("status", "draft"),
            "agent_type": a.get("agent_type", ""),
        }
        for a in agents_list[:10]
    ]

    try:
        from .agent.event_bus import AgentEventBus
        bus = AgentEventBus.get_instance()
        recent_events = bus.get_history(limit=10)
    except Exception:
        recent_events = []

    health = await _get_system_health()

    return {
        "summary": {
            "workspace_count": len(workspaces),
            "active_workspaces": active_count,
            "agent_count": len(agents),
            "total_documents": total_docs,
            "total_processed": total_processed,
            "total_successful": total_successful,
            "total_failed": total_failed,
            "total_in_progress": total_in_progress,
            "overall_success_rate": round(total_successful / max(total_processed, 1) * 100, 1),
        },
        "workspaces": workspaces[:10],
        "active_batches": active_batches,
        "agents": agents,
        "recent_events": recent_events,
        "health": health,
    }


@router.get("/workspaces", summary="Paginated workspace list for dashboard")
async def dashboard_workspaces(
    tenant_id: str = Query(default="default"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
):
    pg = await get_postgres_client()
    ws_repo = WorkspaceRepository(pg)

    total = await ws_repo.count_workspaces(tenant_id)
    offset = (page - 1) * page_size
    ws_list = await ws_repo.list_workspaces(tenant_id, limit=page_size, offset=offset)

    items = []
    for r in ws_list:
        dc = r.get("document_count", 0) or 0
        su = r.get("successful_count", 0) or 0
        fa = r.get("failed_count", 0) or 0
        pr = r.get("processed_count", 0) or 0
        sr = round(su / max(dc, 1) * 100, 1)
        items.append({
            "id": r["id"],
            "name": r["name"],
            "status": r.get("status", "created"),
            "document_count": dc,
            "processed": pr,
            "successful": su,
            "failed": fa,
            "success_rate": sr,
            "created_at": str(r.get("created_at", "")) if r.get("created_at") else None,
        })

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, -(-total // page_size)),
    }


async def _get_system_health() -> Dict[str, Any]:
    health: Dict[str, Any] = {}

    try:
        from .ontology import get_ontology_client
        db = await get_ontology_client()
        h = await db.health_check()
        health["neo4j"] = {"status": "ok", **h}
    except Exception as e:
        health["neo4j"] = {"status": "error", "error": str(e)}

    try:
        pg = await get_postgres_client()
        pg_h = await pg.health_check()
        health["event_store"] = {"status": "ok", **pg_h}
    except Exception as e:
        health["event_store"] = {"status": "error", "error": str(e)}

    try:
        from .gateway import get_gateway_client
        gw = await get_gateway_client()
        gw_h = await gw.health_check()
        health["mcp_gateway"] = {"status": "ok", **gw_h}
    except Exception as e:
        health["mcp_gateway"] = {"status": "unavailable", "error": str(e)}

    return health
