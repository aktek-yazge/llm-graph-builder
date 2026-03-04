"""
Persistent PostgreSQL Blackboard
=================================

Agent'lar arasi paylasilan bilgi deposu.
Her entry PostgreSQL'de saklanir, agent'lar
session'lar arasi okuyup yazabilir.

Blackboard pattern: Shared knowledge space where agents
publish findings, read each other's progress, and
coordinate work without direct coupling.

Kullanim:
    bb = Blackboard(comms_repo)
    await bb.write("schema_proposal", "Customer entity found", agent_id="ws-agent-1")
    entries = await bb.read("schema_proposal", workspace_id="ws-abc")
    topics = await bb.list_topics(workspace_id="ws-abc")
"""

import logging
from typing import Any, Dict, List, Optional

from ..comms_repository import CommsRepository

logger = logging.getLogger(__name__)


class Blackboard:
    """
    PostgreSQL-backed persistent blackboard for agent communication.
    Delegates all operations to CommsRepository.
    """

    def __init__(self, comms_repo: CommsRepository):
        self.repo = comms_repo

    async def write(
        self,
        topic: str,
        content: str,
        agent_id: str = "",
        workspace_id: str = "",
        entry_type: str = "info",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return await self.repo.write_entry(
            topic=topic, content=content,
            agent_id=agent_id, workspace_id=workspace_id,
            entry_type=entry_type, metadata=metadata,
        )

    async def read(
        self,
        topic: str = "",
        workspace_id: str = "",
        agent_id: str = "",
        limit: int = 50,
        entry_type: str = "",
    ) -> List[Dict[str, Any]]:
        return await self.repo.read_entries(
            topic=topic, workspace_id=workspace_id,
            agent_id=agent_id, entry_type=entry_type, limit=limit,
        )

    async def list_topics(
        self,
        workspace_id: str = "",
    ) -> List[Dict[str, Any]]:
        return await self.repo.list_topics(workspace_id=workspace_id)

    async def send_message(
        self,
        from_agent: str,
        to_agent: str,
        message: str,
        workspace_id: str = "",
        message_type: str = "direct",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return await self.repo.send_message(
            from_agent=from_agent, to_agent=to_agent,
            message=message, workspace_id=workspace_id,
            message_type=message_type, metadata=metadata,
        )

    async def get_messages(
        self,
        agent_id: str,
        status: str = "unread",
        workspace_id: str = "",
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        return await self.repo.get_messages(
            agent_id=agent_id, status=status,
            workspace_id=workspace_id, limit=limit,
        )

    async def mark_read(self, message_ids: List[str]) -> int:
        return await self.repo.mark_read(message_ids)
