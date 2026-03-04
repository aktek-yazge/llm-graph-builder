"""
Mutation Gateway
================

Intercepts all graph write operations. Every mutation is:
1. Recorded as an immutable event in PostgreSQL
2. Applied to the Neo4j graph

This guarantees a complete, auditable history of every change.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from ..event_store.event_store import EventStore
from ..event_store.models import EventType, GraphEvent
from ..ontology.neo4j_client import OntologyDBClient

logger = logging.getLogger(__name__)


class MutationGateway:
    """
    All graph writes MUST go through this gateway.
    It records the mutation as an event, then applies it to Neo4j.
    """

    def __init__(
        self,
        db: OntologyDBClient,
        event_store: EventStore,
        tenant_id: str,
        user_id: Optional[str] = None,
        llm_model: Optional[str] = None,
        session_id: Optional[str] = None,
    ):
        self.db = db
        self.event_store = event_store
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.llm_model = llm_model
        self.session_id = session_id

    # =========================================================================
    # NODE OPERATIONS
    # =========================================================================

    async def create_node(
        self,
        label: str,
        properties: Dict[str, Any],
        *,
        llm_prompt: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a node and record the event."""
        entity_id = properties.get("id", "")

        query = f"""
        CREATE (n:{label} $props)
        SET n.created_at = datetime(),
            n._version = 1
        RETURN n {{.*}} AS node
        """
        result = await self.db.execute_query(query, {"props": properties}, write=True)
        created = result[0]["node"] if result else properties

        await self.event_store.record_event(GraphEvent(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            event_type=EventType.CREATE_NODE,
            entity_type=label,
            entity_id=entity_id,
            before_state=None,
            after_state=self._sanitize(created),
            metadata=metadata or {},
            llm_prompt=llm_prompt,
            llm_model=self.llm_model,
            session_id=self.session_id,
        ))

        return created

    async def update_node(
        self,
        label: str,
        entity_id: str,
        updates: Dict[str, Any],
        *,
        llm_prompt: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Update a node's properties and record the event."""
        before_result = await self.db.execute_query(
            f"MATCH (n:{label} {{id: $id}}) RETURN n {{.*}} AS node",
            {"id": entity_id},
        )
        before = before_result[0]["node"] if before_result else {}

        set_clauses = ", ".join(f"n.{k} = $updates.{k}" for k in updates)
        query = f"""
        MATCH (n:{label} {{id: $id}})
        SET {set_clauses},
            n.updated_at = datetime(),
            n._version = COALESCE(n._version, 0) + 1
        RETURN n {{.*}} AS node
        """
        result = await self.db.execute_query(
            query, {"id": entity_id, "updates": updates}, write=True
        )
        after = result[0]["node"] if result else {**before, **updates}

        await self.event_store.record_event(GraphEvent(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            event_type=EventType.UPDATE_NODE,
            entity_type=label,
            entity_id=entity_id,
            before_state=self._sanitize(before),
            after_state=self._sanitize(after),
            metadata=metadata or {},
            llm_prompt=llm_prompt,
            llm_model=self.llm_model,
            session_id=self.session_id,
        ))

        return after

    async def delete_node(
        self,
        label: str,
        entity_id: str,
        *,
        llm_prompt: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        hard_delete: bool = False,
    ) -> bool:
        """
        Soft-delete a node (set _deleted=true) and record the event.
        Use hard_delete=True only for rollback compensation.
        """
        before_result = await self.db.execute_query(
            f"MATCH (n:{label} {{id: $id}}) RETURN n {{.*}} AS node",
            {"id": entity_id},
        )
        before = before_result[0]["node"] if before_result else {}

        if not before:
            logger.warning("Node %s/%s not found for deletion", label, entity_id)
            return False

        if hard_delete:
            await self.db.execute_query(
                f"MATCH (n:{label} {{id: $id}}) DETACH DELETE n",
                {"id": entity_id}, write=True,
            )
        else:
            await self.db.execute_query(
                f"""
                MATCH (n:{label} {{id: $id}})
                SET n._deleted = true,
                    n._deleted_at = datetime(),
                    n.updated_at = datetime()
                """,
                {"id": entity_id}, write=True,
            )

        await self.event_store.record_event(GraphEvent(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            event_type=EventType.DELETE_NODE,
            entity_type=label,
            entity_id=entity_id,
            before_state=self._sanitize(before),
            after_state=None,
            metadata=metadata or {},
            llm_prompt=llm_prompt,
            llm_model=self.llm_model,
            session_id=self.session_id,
        ))

        return True

    # =========================================================================
    # RELATIONSHIP OPERATIONS
    # =========================================================================

    async def create_relationship(
        self,
        source_label: str,
        source_id: str,
        target_label: str,
        target_id: str,
        rel_type: str,
        properties: Optional[Dict[str, Any]] = None,
        *,
        llm_prompt: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a relationship and record the event."""
        props = properties or {}
        rel_entity_id = f"{source_id}-[{rel_type}]->{target_id}"

        prop_string = ""
        if props:
            prop_string = " $props"

        query = f"""
        MATCH (a:{source_label} {{id: $source_id}}),
              (b:{target_label} {{id: $target_id}})
        MERGE (a)-[r:{rel_type}{prop_string}]->(b)
        ON CREATE SET r.created_at = datetime()
        RETURN type(r) AS type, properties(r) AS props
        """
        params: Dict[str, Any] = {
            "source_id": source_id,
            "target_id": target_id,
        }
        if props:
            params["props"] = props

        result = await self.db.execute_query(query, params, write=True)

        after_state = {
            "source_label": source_label,
            "source_id": source_id,
            "target_label": target_label,
            "target_id": target_id,
            "rel_type": rel_type,
            "properties": props,
        }

        await self.event_store.record_event(GraphEvent(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            event_type=EventType.CREATE_RELATIONSHIP,
            entity_type=rel_type,
            entity_id=rel_entity_id,
            before_state=None,
            after_state=after_state,
            metadata=metadata or {},
            llm_prompt=llm_prompt,
            llm_model=self.llm_model,
            session_id=self.session_id,
        ))

        return after_state

    async def delete_relationship(
        self,
        source_label: str,
        source_id: str,
        target_label: str,
        target_id: str,
        rel_type: str,
        *,
        llm_prompt: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Delete a relationship and record the event."""
        rel_entity_id = f"{source_id}-[{rel_type}]->{target_id}"

        before_result = await self.db.execute_query(
            f"""
            MATCH (a:{source_label} {{id: $source_id}})
                  -[r:{rel_type}]->
                  (b:{target_label} {{id: $target_id}})
            RETURN type(r) AS type, properties(r) AS props
            """,
            {"source_id": source_id, "target_id": target_id},
        )

        if not before_result:
            return False

        before_state = {
            "source_label": source_label,
            "source_id": source_id,
            "target_label": target_label,
            "target_id": target_id,
            "rel_type": rel_type,
            "properties": before_result[0].get("props", {}),
        }

        await self.db.execute_query(
            f"""
            MATCH (a:{source_label} {{id: $source_id}})
                  -[r:{rel_type}]->
                  (b:{target_label} {{id: $target_id}})
            DELETE r
            """,
            {"source_id": source_id, "target_id": target_id},
            write=True,
        )

        await self.event_store.record_event(GraphEvent(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            event_type=EventType.DELETE_RELATIONSHIP,
            entity_type=rel_type,
            entity_id=rel_entity_id,
            before_state=before_state,
            after_state=None,
            metadata=metadata or {},
            llm_prompt=llm_prompt,
            llm_model=self.llm_model,
            session_id=self.session_id,
        ))

        return True

    # =========================================================================
    # TRACKED RAW QUERIES
    # =========================================================================

    async def execute_tracked_query(
        self,
        query: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        event_type: EventType = EventType.UPDATE_NODE,
        entity_type: str = "Unknown",
        entity_id: str = "",
        llm_prompt: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Execute a raw Cypher write query and record it as an event.
        Use when the high-level create_node/update_node API is insufficient
        (e.g. complex MERGE, multi-node updates, SET with expressions).
        """
        result = await self.db.execute_query(query, params or {}, write=True)

        await self.event_store.record_event(GraphEvent(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            before_state=None,
            after_state={"query": query[:500], "params_keys": list((params or {}).keys())},
            metadata=metadata or {},
            llm_prompt=llm_prompt,
            llm_model=self.llm_model,
            session_id=self.session_id,
        ))

        return result

    # =========================================================================
    # HELPERS
    # =========================================================================

    @staticmethod
    def _sanitize(data: Any) -> Optional[Dict[str, Any]]:
        """Convert Neo4j node dict to JSON-safe dict."""
        if data is None:
            return None
        result = {}
        for k, v in (data if isinstance(data, dict) else {}).items():
            try:
                json.dumps(v)
                result[k] = v
            except (TypeError, ValueError):
                result[k] = str(v)
        return result
