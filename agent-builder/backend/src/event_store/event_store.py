"""
Event Store
===========

Immutable event log for all graph mutations.
Provides recording, querying, and snapshot management.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from .models import EventFilter, EventType, GraphEvent, NamedSnapshot
from .postgres_client import PostgresClient

logger = logging.getLogger(__name__)


class EventStore:
    """Immutable append-only event store for graph mutations."""

    def __init__(self, pg: PostgresClient):
        self.pg = pg

    # =========================================================================
    # RECORD
    # =========================================================================

    async def record_event(self, event: GraphEvent) -> GraphEvent:
        """
        Append a single event to the store. Returns the event with
        id, sequence_no, and created_at populated.
        """
        row = await self.pg.fetchrow(
            """
            INSERT INTO graph_events
                (tenant_id, user_id, event_type, entity_type, entity_id,
                 before_state, after_state, metadata,
                 llm_prompt, llm_model, session_id,
                 is_compensation, compensation_of)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
            RETURNING id, sequence_no, created_at
            """,
            event.tenant_id,
            event.user_id,
            event.event_type.value,
            event.entity_type,
            event.entity_id,
            json.dumps(event.before_state) if event.before_state else None,
            json.dumps(event.after_state) if event.after_state else None,
            json.dumps(event.metadata),
            event.llm_prompt,
            event.llm_model,
            event.session_id,
            event.is_compensation,
            event.compensation_of,
        )

        event.id = row["id"]
        event.sequence_no = row["sequence_no"]
        event.created_at = row["created_at"]

        logger.info(
            "Event #%d recorded: %s %s/%s",
            event.sequence_no, event.event_type.value,
            event.entity_type, event.entity_id,
        )
        return event

    # =========================================================================
    # QUERY
    # =========================================================================

    async def get_events(self, f: EventFilter) -> List[GraphEvent]:
        """Query events with flexible filtering."""
        conditions = ["tenant_id = $1"]
        params: list[Any] = [f.tenant_id]
        idx = 2

        if f.entity_id:
            conditions.append(f"entity_id = ${idx}")
            params.append(f.entity_id)
            idx += 1
        if f.entity_type:
            conditions.append(f"entity_type = ${idx}")
            params.append(f.entity_type)
            idx += 1
        if f.event_type:
            conditions.append(f"event_type = ${idx}")
            params.append(f.event_type.value)
            idx += 1
        if f.user_id:
            conditions.append(f"user_id = ${idx}")
            params.append(f.user_id)
            idx += 1
        if f.session_id:
            conditions.append(f"session_id = ${idx}")
            params.append(f.session_id)
            idx += 1
        if f.since:
            conditions.append(f"created_at >= ${idx}")
            params.append(f.since)
            idx += 1
        if f.until:
            conditions.append(f"created_at <= ${idx}")
            params.append(f.until)
            idx += 1

        where = " AND ".join(conditions)
        params.extend([f.limit, f.offset])

        rows = await self.pg.fetch(
            f"""
            SELECT * FROM graph_events
            WHERE {where}
            ORDER BY sequence_no DESC
            LIMIT ${idx} OFFSET ${idx + 1}
            """,
            *params,
        )
        return [self._row_to_event(r) for r in rows]

    async def get_entity_history(
        self, tenant_id: str, entity_id: str, limit: int = 100
    ) -> List[GraphEvent]:
        """Get full mutation history for a single entity."""
        return await self.get_events(
            EventFilter(tenant_id=tenant_id, entity_id=entity_id, limit=limit)
        )

    async def get_latest_sequence(self, tenant_id: str) -> int:
        """Return the highest sequence_no for this tenant, or 0."""
        val = await self.pg.fetchval(
            "SELECT COALESCE(MAX(sequence_no), 0) FROM graph_events WHERE tenant_id = $1",
            tenant_id,
        )
        return val

    async def get_events_since_sequence(
        self, tenant_id: str, sequence_no: int, limit: int = 500
    ) -> List[GraphEvent]:
        """Get all events after a given sequence number (for replay)."""
        rows = await self.pg.fetch(
            """
            SELECT * FROM graph_events
            WHERE tenant_id = $1 AND sequence_no > $2
            ORDER BY sequence_no ASC
            LIMIT $3
            """,
            tenant_id, sequence_no, limit,
        )
        return [self._row_to_event(r) for r in rows]

    async def get_events_in_range(
        self, tenant_id: str, from_seq: int, to_seq: int
    ) -> List[GraphEvent]:
        """Get events between two sequence numbers (inclusive)."""
        rows = await self.pg.fetch(
            """
            SELECT * FROM graph_events
            WHERE tenant_id = $1
              AND sequence_no >= $2
              AND sequence_no <= $3
            ORDER BY sequence_no ASC
            """,
            tenant_id, from_seq, to_seq,
        )
        return [self._row_to_event(r) for r in rows]

    async def get_last_n_events(
        self, tenant_id: str, n: int
    ) -> List[GraphEvent]:
        """Get the last N non-compensation events (for rollback)."""
        rows = await self.pg.fetch(
            """
            SELECT * FROM graph_events
            WHERE tenant_id = $1 AND is_compensation = FALSE
            ORDER BY sequence_no DESC
            LIMIT $2
            """,
            tenant_id, n,
        )
        return [self._row_to_event(r) for r in rows]

    # =========================================================================
    # SNAPSHOTS
    # =========================================================================

    async def create_snapshot(
        self, tenant_id: str, name: str,
        description: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> NamedSnapshot:
        """Create a named snapshot at the current sequence position."""
        seq = await self.get_latest_sequence(tenant_id)
        row = await self.pg.fetchrow(
            """
            INSERT INTO named_snapshots
                (tenant_id, name, description, sequence_no, created_by)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (tenant_id, name) DO UPDATE
                SET description = EXCLUDED.description,
                    sequence_no = EXCLUDED.sequence_no,
                    created_by = EXCLUDED.created_by,
                    created_at = NOW()
            RETURNING id, created_at
            """,
            tenant_id, name, description, seq, created_by,
        )
        logger.info("Snapshot '%s' created at seq #%d for tenant %s", name, seq, tenant_id)
        return NamedSnapshot(
            id=row["id"],
            tenant_id=tenant_id,
            name=name,
            description=description,
            sequence_no=seq,
            created_by=created_by,
            created_at=row["created_at"],
        )

    async def get_snapshot(self, tenant_id: str, name: str) -> Optional[NamedSnapshot]:
        row = await self.pg.fetchrow(
            "SELECT * FROM named_snapshots WHERE tenant_id = $1 AND name = $2",
            tenant_id, name,
        )
        if not row:
            return None
        return NamedSnapshot(**dict(row))

    async def list_snapshots(self, tenant_id: str) -> List[NamedSnapshot]:
        rows = await self.pg.fetch(
            """
            SELECT * FROM named_snapshots
            WHERE tenant_id = $1
            ORDER BY created_at DESC
            """,
            tenant_id,
        )
        return [NamedSnapshot(**dict(r)) for r in rows]

    # =========================================================================
    # HELPERS
    # =========================================================================

    @staticmethod
    def _row_to_event(row: Any) -> GraphEvent:
        d = dict(row)
        if isinstance(d.get("before_state"), str):
            d["before_state"] = json.loads(d["before_state"])
        if isinstance(d.get("after_state"), str):
            d["after_state"] = json.loads(d["after_state"])
        if isinstance(d.get("metadata"), str):
            d["metadata"] = json.loads(d["metadata"])
        d["event_type"] = EventType(d["event_type"])
        return GraphEvent(**d)
