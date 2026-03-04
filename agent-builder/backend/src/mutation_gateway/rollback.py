"""
Rollback Manager
================

Reverts graph changes by producing compensating events.
The original events are NEVER deleted - compensation events
are appended, maintaining a full audit trail.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..event_store.event_store import EventStore
from ..event_store.models import EventType, GraphEvent
from ..ontology.neo4j_client import OntologyDBClient

logger = logging.getLogger(__name__)

INVERSE_EVENT = {
    EventType.CREATE_NODE: EventType.DELETE_NODE,
    EventType.DELETE_NODE: EventType.CREATE_NODE,
    EventType.UPDATE_NODE: EventType.UPDATE_NODE,
    EventType.CREATE_RELATIONSHIP: EventType.DELETE_RELATIONSHIP,
    EventType.DELETE_RELATIONSHIP: EventType.CREATE_RELATIONSHIP,
    EventType.UPDATE_RELATIONSHIP: EventType.UPDATE_RELATIONSHIP,
}


class RollbackManager:
    """Produces compensating events to undo graph mutations."""

    def __init__(
        self,
        db: OntologyDBClient,
        event_store: EventStore,
        tenant_id: str,
        user_id: Optional[str] = None,
    ):
        self.db = db
        self.event_store = event_store
        self.tenant_id = tenant_id
        self.user_id = user_id

    async def rollback_steps(self, steps: int) -> List[GraphEvent]:
        """
        Undo the last N non-compensation events by applying inverse
        operations and recording compensation events.
        """
        events = await self.event_store.get_last_n_events(self.tenant_id, steps)
        if not events:
            logger.warning("No events to rollback for tenant %s", self.tenant_id)
            return []

        compensations: List[GraphEvent] = []
        for event in events:
            comp = await self._compensate(event)
            if comp:
                compensations.append(comp)

        logger.info(
            "Rolled back %d steps (%d compensations) for tenant %s",
            steps, len(compensations), self.tenant_id,
        )
        return compensations

    async def rollback_to_snapshot(self, snapshot_name: str) -> List[GraphEvent]:
        """Undo all events after the named snapshot."""
        snap = await self.event_store.get_snapshot(self.tenant_id, snapshot_name)
        if not snap:
            raise ValueError(f"Snapshot '{snapshot_name}' not found")

        latest_seq = await self.event_store.get_latest_sequence(self.tenant_id)
        if snap.sequence_no >= latest_seq:
            return []

        events = await self.event_store.get_events_in_range(
            self.tenant_id, snap.sequence_no + 1, latest_seq
        )

        # Only compensate non-compensation events, in reverse order
        targets = [e for e in reversed(events) if not e.is_compensation]
        compensations: List[GraphEvent] = []
        for event in targets:
            comp = await self._compensate(event)
            if comp:
                compensations.append(comp)

        logger.info(
            "Rolled back to snapshot '%s' (seq #%d), %d compensations for tenant %s",
            snapshot_name, snap.sequence_no, len(compensations), self.tenant_id,
        )
        return compensations

    async def rollback_to_timestamp(self, target_time: datetime) -> List[GraphEvent]:
        """Undo all events after the given timestamp."""
        from ..event_store.models import EventFilter

        events = await self.event_store.get_events(EventFilter(
            tenant_id=self.tenant_id,
            since=target_time,
            limit=10000,
        ))

        targets = [e for e in events if not e.is_compensation]
        compensations: List[GraphEvent] = []
        for event in targets:
            comp = await self._compensate(event)
            if comp:
                compensations.append(comp)

        logger.info(
            "Rolled back to %s, %d compensations for tenant %s",
            target_time.isoformat(), len(compensations), self.tenant_id,
        )
        return compensations

    # =========================================================================
    # INTERNAL
    # =========================================================================

    async def _compensate(self, event: GraphEvent) -> Optional[GraphEvent]:
        """Apply the inverse of a single event and record the compensation."""
        inverse_type = INVERSE_EVENT.get(event.event_type)
        if not inverse_type:
            logger.warning("Cannot compensate event type %s", event.event_type)
            return None

        try:
            if event.event_type == EventType.CREATE_NODE:
                await self._undo_create_node(event)
            elif event.event_type == EventType.DELETE_NODE:
                await self._undo_delete_node(event)
            elif event.event_type == EventType.UPDATE_NODE:
                await self._undo_update_node(event)
            elif event.event_type == EventType.CREATE_RELATIONSHIP:
                await self._undo_create_relationship(event)
            elif event.event_type == EventType.DELETE_RELATIONSHIP:
                await self._undo_delete_relationship(event)
            elif event.event_type == EventType.UPDATE_RELATIONSHIP:
                await self._undo_update_relationship(event)
        except Exception as e:
            logger.error("Compensation failed for event %s: %s", event.id, e)
            return None

        comp = await self.event_store.record_event(GraphEvent(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            event_type=inverse_type,
            entity_type=event.entity_type,
            entity_id=event.entity_id,
            before_state=event.after_state,
            after_state=event.before_state,
            metadata={"reason": "rollback", "original_event_seq": event.sequence_no},
            is_compensation=True,
            compensation_of=event.id,
        ))
        return comp

    async def _undo_create_node(self, event: GraphEvent) -> None:
        """Undo a CREATE_NODE by hard-deleting the node."""
        label = event.entity_type
        entity_id = event.entity_id
        await self.db.execute_query(
            f"MATCH (n:{label} {{id: $id}}) DETACH DELETE n",
            {"id": entity_id}, write=True,
        )

    async def _undo_delete_node(self, event: GraphEvent) -> None:
        """Undo a DELETE_NODE by re-creating the node from before_state."""
        if not event.before_state:
            return
        label = event.entity_type
        props = {k: v for k, v in event.before_state.items()
                 if not k.startswith("_")}
        await self.db.execute_query(
            f"CREATE (n:{label} $props) SET n._restored = true, n._version = COALESCE($ver, 1) + 1",
            {"props": props, "ver": event.before_state.get("_version", 0)},
            write=True,
        )

    async def _undo_update_node(self, event: GraphEvent) -> None:
        """Undo an UPDATE_NODE by restoring before_state properties."""
        if not event.before_state:
            return
        label = event.entity_type
        entity_id = event.entity_id
        restore_props = {k: v for k, v in event.before_state.items()
                         if not k.startswith("_") and k not in ("id",)}

        set_clauses = ", ".join(f"n.{k} = $props.{k}" for k in restore_props)
        if not set_clauses:
            return
        await self.db.execute_query(
            f"""
            MATCH (n:{label} {{id: $id}})
            SET {set_clauses},
                n.updated_at = datetime(),
                n._version = COALESCE(n._version, 0) + 1
            """,
            {"id": entity_id, "props": restore_props},
            write=True,
        )

    async def _undo_create_relationship(self, event: GraphEvent) -> None:
        """Undo a CREATE_RELATIONSHIP by deleting it."""
        if not event.after_state:
            return
        s = event.after_state
        await self.db.execute_query(
            f"""
            MATCH (a:{s['source_label']} {{id: $sid}})
                  -[r:{s['rel_type']}]->
                  (b:{s['target_label']} {{id: $tid}})
            DELETE r
            """,
            {"sid": s["source_id"], "tid": s["target_id"]},
            write=True,
        )

    async def _undo_delete_relationship(self, event: GraphEvent) -> None:
        """Undo a DELETE_RELATIONSHIP by re-creating it."""
        if not event.before_state:
            return
        s = event.before_state
        props = s.get("properties", {})
        prop_string = " $props" if props else ""
        await self.db.execute_query(
            f"""
            MATCH (a:{s['source_label']} {{id: $sid}}),
                  (b:{s['target_label']} {{id: $tid}})
            MERGE (a)-[r:{s['rel_type']}{prop_string}]->(b)
            """,
            {"sid": s["source_id"], "tid": s["target_id"], "props": props},
            write=True,
        )

    async def _undo_update_relationship(self, event: GraphEvent) -> None:
        """Undo an UPDATE_RELATIONSHIP by restoring before_state properties."""
        if not event.before_state:
            return
        s = event.before_state
        props = s.get("properties", {})
        if not props:
            return
        set_clauses = ", ".join(f"r.{k} = $props.{k}" for k in props)
        await self.db.execute_query(
            f"""
            MATCH (a:{s['source_label']} {{id: $sid}})
                  -[r:{s['rel_type']}]->
                  (b:{s['target_label']} {{id: $tid}})
            SET {set_clauses}
            """,
            {"sid": s["source_id"], "tid": s["target_id"], "props": props},
            write=True,
        )
