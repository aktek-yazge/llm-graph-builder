"""
Event Store Models
==================

Pydantic models for graph mutation events.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class EventType(str, Enum):
    CREATE_NODE = "CREATE_NODE"
    UPDATE_NODE = "UPDATE_NODE"
    DELETE_NODE = "DELETE_NODE"
    CREATE_RELATIONSHIP = "CREATE_RELATIONSHIP"
    UPDATE_RELATIONSHIP = "UPDATE_RELATIONSHIP"
    DELETE_RELATIONSHIP = "DELETE_RELATIONSHIP"


class GraphEvent(BaseModel):
    """A single immutable graph mutation event."""
    id: Optional[UUID] = None
    sequence_no: Optional[int] = None
    tenant_id: str
    user_id: Optional[str] = None
    event_type: EventType
    entity_type: str
    entity_id: str
    before_state: Optional[Dict[str, Any]] = None
    after_state: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    llm_prompt: Optional[str] = None
    llm_model: Optional[str] = None
    session_id: Optional[str] = None
    is_compensation: bool = False
    compensation_of: Optional[UUID] = None
    created_at: Optional[datetime] = None


class NamedSnapshot(BaseModel):
    """A named bookmark in the event timeline for easy rollback."""
    id: Optional[UUID] = None
    tenant_id: str
    name: str
    description: Optional[str] = None
    sequence_no: int
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None


class EventFilter(BaseModel):
    """Filter criteria for querying events."""
    tenant_id: str
    entity_id: Optional[str] = None
    entity_type: Optional[str] = None
    event_type: Optional[EventType] = None
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    since: Optional[datetime] = None
    until: Optional[datetime] = None
    limit: int = 50
    offset: int = 0
