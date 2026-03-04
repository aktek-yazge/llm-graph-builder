"""
Shared Dependencies
===================

FastAPI dependency injection fonksiyonlari.
OntologyDBClient, EventStore, MutationGateway ve
PostgreSQL repository'lerin request-scoped veya singleton olarak saglanmasi.
"""

import logging
from typing import Optional

from fastapi import Depends, Query

from .ontology.neo4j_client import OntologyDBClient
from .ontology import get_ontology_client
from .event_store import EventStore, get_postgres_client
from .mutation_gateway import MutationGateway
from .chat_repository import ChatRepository
from .agent_repository import AgentRepository
from .workspace_repository import WorkspaceRepository
from .comms_repository import CommsRepository
from .resource_repository import ResourceRepository
from .chat_agent_repository import ChatAgentRepository

logger = logging.getLogger(__name__)

_event_store: Optional[EventStore] = None

# Singleton repository instances
_chat_repo: Optional[ChatRepository] = None
_agent_repo: Optional[AgentRepository] = None
_workspace_repo: Optional[WorkspaceRepository] = None
_comms_repo: Optional[CommsRepository] = None
_resource_repo: Optional[ResourceRepository] = None
_chat_agent_repo: Optional[ChatAgentRepository] = None


async def get_ontology_db() -> OntologyDBClient:
    """Ontology DB client dependency (Neo4j - only for Knowledge Base queries)."""
    return await get_ontology_client()


async def get_event_store() -> EventStore:
    """Singleton EventStore backed by PostgresClient."""
    global _event_store
    if _event_store is None:
        pg = await get_postgres_client()
        _event_store = EventStore(pg)
    return _event_store


async def get_mutation_gateway(
    tenant_id: str = Query(default="default-tenant"),
    db: OntologyDBClient = Depends(get_ontology_db),
) -> MutationGateway:
    es = await get_event_store()
    return MutationGateway(db=db, event_store=es, tenant_id=tenant_id)


async def get_mutation_gateway_for_tenant(
    tenant_id: str,
    db: Optional[OntologyDBClient] = None,
) -> MutationGateway:
    if db is None:
        db = await get_ontology_client()
    es = await get_event_store()
    return MutationGateway(db=db, event_store=es, tenant_id=tenant_id)


# =========================================================================
# REPOSITORY FACTORIES
# =========================================================================

async def get_chat_repo() -> ChatRepository:
    global _chat_repo
    if _chat_repo is None:
        pg = await get_postgres_client()
        _chat_repo = ChatRepository(pg)
    return _chat_repo


async def get_agent_repo() -> AgentRepository:
    global _agent_repo
    if _agent_repo is None:
        pg = await get_postgres_client()
        _agent_repo = AgentRepository(pg)
    return _agent_repo


async def get_workspace_repo() -> WorkspaceRepository:
    global _workspace_repo
    if _workspace_repo is None:
        pg = await get_postgres_client()
        _workspace_repo = WorkspaceRepository(pg)
    return _workspace_repo


async def get_comms_repo() -> CommsRepository:
    global _comms_repo
    if _comms_repo is None:
        pg = await get_postgres_client()
        _comms_repo = CommsRepository(pg)
    return _comms_repo


async def get_resource_repo() -> ResourceRepository:
    global _resource_repo
    if _resource_repo is None:
        pg = await get_postgres_client()
        _resource_repo = ResourceRepository(pg)
    return _resource_repo


async def get_chat_agent_repo() -> ChatAgentRepository:
    global _chat_agent_repo
    if _chat_agent_repo is None:
        pg = await get_postgres_client()
        _chat_agent_repo = ChatAgentRepository(pg)
    return _chat_agent_repo
