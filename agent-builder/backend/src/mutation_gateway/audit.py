"""
Audit Trail
============

Query helpers for inspecting the event history:
who changed what, when, and why.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..event_store.event_store import EventStore
from ..event_store.models import EventFilter, GraphEvent
from ..event_store.postgres_client import PostgresClient

logger = logging.getLogger(__name__)


class AuditTrail:
    """Read-only queries over the event store for auditing purposes."""

    def __init__(self, event_store: EventStore, pg: PostgresClient):
        self.event_store = event_store
        self.pg = pg

    async def get_entity_history(
        self, tenant_id: str, entity_id: str, limit: int = 100
    ) -> List[GraphEvent]:
        """Full mutation history for a single entity, newest first."""
        return await self.event_store.get_entity_history(tenant_id, entity_id, limit)

    async def get_changes_by_user(
        self, tenant_id: str, user_id: str,
        since: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[GraphEvent]:
        """All changes made by a specific user."""
        return await self.event_store.get_events(EventFilter(
            tenant_id=tenant_id,
            user_id=user_id,
            since=since,
            limit=limit,
        ))

    async def get_changes_by_llm_prompt(
        self, tenant_id: str, prompt_search: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Search events by LLM prompt text (partial match)."""
        rows = await self.pg.fetch(
            """
            SELECT id, sequence_no, event_type, entity_type, entity_id,
                   llm_prompt, llm_model, user_id, created_at
            FROM graph_events
            WHERE tenant_id = $1
              AND llm_prompt ILIKE '%' || $2 || '%'
            ORDER BY sequence_no DESC
            LIMIT $3
            """,
            tenant_id, prompt_search, limit,
        )
        return [dict(r) for r in rows]

    async def diff(
        self, tenant_id: str, time_a: datetime, time_b: datetime
    ) -> Dict[str, Any]:
        """
        Compare graph state between two timestamps.
        Returns counts and lists of created, updated, and deleted entities.
        """
        rows = await self.pg.fetch(
            """
            SELECT event_type, entity_type, entity_id,
                   before_state, after_state, created_at
            FROM graph_events
            WHERE tenant_id = $1
              AND created_at >= $2
              AND created_at <= $3
              AND is_compensation = FALSE
            ORDER BY sequence_no ASC
            """,
            tenant_id, time_a, time_b,
        )

        created: List[Dict[str, Any]] = []
        updated: List[Dict[str, Any]] = []
        deleted: List[Dict[str, Any]] = []

        for r in rows:
            entry = {
                "entity_type": r["entity_type"],
                "entity_id": r["entity_id"],
                "timestamp": r["created_at"].isoformat() if r["created_at"] else None,
            }
            et = r["event_type"]
            if et in ("CREATE_NODE", "CREATE_RELATIONSHIP"):
                created.append(entry)
            elif et in ("UPDATE_NODE", "UPDATE_RELATIONSHIP"):
                entry["before"] = r["before_state"]
                entry["after"] = r["after_state"]
                updated.append(entry)
            elif et in ("DELETE_NODE", "DELETE_RELATIONSHIP"):
                deleted.append(entry)

        return {
            "from": time_a.isoformat(),
            "to": time_b.isoformat(),
            "summary": {
                "created": len(created),
                "updated": len(updated),
                "deleted": len(deleted),
            },
            "created": created,
            "updated": updated,
            "deleted": deleted,
        }

    async def get_event_stats(self, tenant_id: str) -> Dict[str, Any]:
        """Summary statistics for the tenant's event store."""
        rows = await self.pg.fetch(
            """
            SELECT event_type, COUNT(*) as cnt
            FROM graph_events
            WHERE tenant_id = $1
            GROUP BY event_type
            ORDER BY cnt DESC
            """,
            tenant_id,
        )
        total = sum(r["cnt"] for r in rows)
        by_type = {r["event_type"]: r["cnt"] for r in rows}

        latest = await self.pg.fetchrow(
            """
            SELECT sequence_no, created_at
            FROM graph_events
            WHERE tenant_id = $1
            ORDER BY sequence_no DESC
            LIMIT 1
            """,
            tenant_id,
        )

        return {
            "total_events": total,
            "by_type": by_type,
            "latest_sequence": latest["sequence_no"] if latest else 0,
            "latest_timestamp": latest["created_at"].isoformat() if latest else None,
        }
