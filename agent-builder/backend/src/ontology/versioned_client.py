"""
Versioned Ontology Client
=========================

Wraps OntologyDBClient so that:
- Read queries go directly to Neo4j (fast, unchanged)
- Write operations go through MutationGateway (event-sourced)

Drop-in replacement for OntologyDBClient in code that needs versioning.
"""

import logging
from typing import Any, Dict, List, Optional

from .neo4j_client import OntologyDBClient
from ..event_store.event_store import EventStore
from ..mutation_gateway.gateway import MutationGateway
from ..mutation_gateway.rollback import RollbackManager
from ..mutation_gateway.audit import AuditTrail
from ..event_store.postgres_client import PostgresClient

logger = logging.getLogger(__name__)


class VersionedOntologyClient:
    """
    Facade that bundles the Neo4j client with event-sourcing
    components (MutationGateway, RollbackManager, AuditTrail).

    Usage:
        vc = VersionedOntologyClient(db, pg, tenant_id="t1", user_id="u1")
        await vc.gateway.create_node("Goal", {...}, llm_prompt="...")
        history = await vc.audit.get_entity_history("t1", "goal-abc")
        await vc.rollback.rollback_steps(2)
    """

    def __init__(
        self,
        db: OntologyDBClient,
        pg: PostgresClient,
        tenant_id: str,
        user_id: Optional[str] = None,
        llm_model: Optional[str] = None,
        session_id: Optional[str] = None,
    ):
        self.db = db
        self.tenant_id = tenant_id
        self.user_id = user_id

        self._event_store = EventStore(pg)
        self._gateway = MutationGateway(
            db=db,
            event_store=self._event_store,
            tenant_id=tenant_id,
            user_id=user_id,
            llm_model=llm_model,
            session_id=session_id,
        )
        self._rollback = RollbackManager(
            db=db,
            event_store=self._event_store,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        self._audit = AuditTrail(
            event_store=self._event_store,
            pg=pg,
        )

    # ---- convenience accessors ----

    @property
    def event_store(self) -> EventStore:
        return self._event_store

    @property
    def gateway(self) -> MutationGateway:
        return self._gateway

    @property
    def rollback(self) -> RollbackManager:
        return self._rollback

    @property
    def audit(self) -> AuditTrail:
        return self._audit

    # ---- delegated reads (unchanged) ----

    async def execute_query(
        self,
        query: str,
        params: Optional[Dict[str, Any]] = None,
        write: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        For read queries, delegates directly to OntologyDBClient.
        For write queries, also delegates directly but logs a warning
        if the caller should be using the gateway instead.
        """
        if write:
            logger.debug(
                "Direct write through VersionedOntologyClient. "
                "Consider using gateway.create_node / update_node / etc. "
                "for full event sourcing."
            )
        return await self.db.execute_query(query, params, write=write)

    async def health_check(self) -> Dict[str, Any]:
        neo4j_health = await self.db.health_check()
        pg_health = await self._audit.get_event_stats(self.tenant_id)
        return {
            "neo4j": neo4j_health,
            "event_store": pg_health,
        }
