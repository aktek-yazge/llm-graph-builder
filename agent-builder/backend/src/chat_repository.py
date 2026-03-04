"""
Chat Repository
================

PostgreSQL-backed repository for ChatSession and ChatMessage CRUD.
Replaces Neo4j RuntimeSession / AgentChatMessage / BuilderSession nodes.
"""

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from .event_store.postgres_client import PostgresClient

logger = logging.getLogger(__name__)


class ChatRepository:
    """Async CRUD for chat_sessions, chat_messages, builder_sessions tables."""

    def __init__(self, pg: PostgresClient):
        self.pg = pg

    # -------------------------------------------------------------------------
    # ChatSession
    # -------------------------------------------------------------------------

    async def create_session(
        self,
        session_id: str,
        agent_id: str,
        tenant_id: str = "default",
        workspace_id: str = "",
    ) -> Dict[str, Any]:
        row = await self.pg.fetchrow(
            """
            INSERT INTO chat_sessions (id, agent_id, tenant_id, workspace_id)
            VALUES ($1, $2, $3, NULLIF($4, ''))
            ON CONFLICT (id) DO UPDATE SET updated_at = NOW()
            RETURNING id, agent_id, tenant_id, workspace_id, created_at, updated_at
            """,
            session_id, agent_id, tenant_id, workspace_id,
        )
        return dict(row) if row else {"id": session_id}

    async def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        row = await self.pg.fetchrow(
            "SELECT * FROM chat_sessions WHERE id = $1",
            session_id,
        )
        return dict(row) if row else None

    async def update_session(self, session_id: str) -> None:
        await self.pg.execute(
            "UPDATE chat_sessions SET updated_at = NOW() WHERE id = $1",
            session_id,
        )

    async def get_latest_workspace_session(
        self, workspace_id: str,
    ) -> Optional[Dict[str, Any]]:
        row = await self.pg.fetchrow(
            """
            SELECT * FROM chat_sessions
            WHERE workspace_id = $1
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            workspace_id,
        )
        return dict(row) if row else None

    # -------------------------------------------------------------------------
    # ChatMessage
    # -------------------------------------------------------------------------

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
    ) -> str:
        msg_id = f"msg-{uuid.uuid4().hex[:12]}"
        await self.pg.execute(
            """
            INSERT INTO chat_messages (id, session_id, role, content)
            VALUES ($1, $2, $3, $4)
            """,
            msg_id, session_id, role, content[:50000],
        )
        await self.pg.execute(
            "UPDATE chat_sessions SET updated_at = NOW() WHERE id = $1",
            session_id,
        )
        return msg_id

    async def get_messages(
        self,
        session_id: str,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        rows = await self.pg.fetch(
            """
            SELECT id, role, content, created_at
            FROM chat_messages
            WHERE session_id = $1
            ORDER BY created_at ASC
            LIMIT $2
            """,
            session_id, limit,
        )
        return [dict(r) for r in rows]

    # -------------------------------------------------------------------------
    # BuilderSession
    # -------------------------------------------------------------------------

    async def create_builder_session(
        self,
        session_id: str,
        tenant_id: str = "default",
        user_id: str = "",
    ) -> Dict[str, Any]:
        row = await self.pg.fetchrow(
            """
            INSERT INTO builder_sessions (id, tenant_id, user_id, status, current_state, state_data, messages)
            VALUES ($1, $2, NULLIF($3, ''), 'active', 'goal_elicitation', '{}', '[]')
            RETURNING *
            """,
            session_id, tenant_id, user_id,
        )
        return dict(row) if row else {"id": session_id}

    async def get_builder_session(
        self,
        session_id: str,
        tenant_id: str = "",
    ) -> Optional[Dict[str, Any]]:
        if tenant_id:
            row = await self.pg.fetchrow(
                "SELECT * FROM builder_sessions WHERE id = $1 AND tenant_id = $2",
                session_id, tenant_id,
            )
        else:
            row = await self.pg.fetchrow(
                "SELECT * FROM builder_sessions WHERE id = $1",
                session_id,
            )
        if not row:
            return None
        d = dict(row)
        if isinstance(d.get("state_data"), str):
            try:
                d["state_data"] = json.loads(d["state_data"])
            except (json.JSONDecodeError, TypeError):
                d["state_data"] = {}
        if isinstance(d.get("messages"), str):
            try:
                d["messages"] = json.loads(d["messages"])
            except (json.JSONDecodeError, TypeError):
                d["messages"] = []
        return d

    async def update_builder_session(
        self,
        session_id: str,
        *,
        status: Optional[str] = None,
        current_state: Optional[str] = None,
        state_data: Optional[Dict] = None,
        messages: Optional[List] = None,
        created_agent_id: Optional[str] = None,
    ) -> None:
        sets = ["updated_at = NOW()"]
        params: list = []
        idx = 1

        if status is not None:
            sets.append(f"status = ${idx}")
            params.append(status)
            idx += 1
        if current_state is not None:
            sets.append(f"current_state = ${idx}")
            params.append(current_state)
            idx += 1
        if state_data is not None:
            sets.append(f"state_data = ${idx}::jsonb")
            params.append(json.dumps(state_data, ensure_ascii=False))
            idx += 1
        if messages is not None:
            sets.append(f"messages = ${idx}::jsonb")
            params.append(json.dumps(messages, ensure_ascii=False))
            idx += 1
        if created_agent_id is not None:
            sets.append(f"created_agent_id = ${idx}")
            params.append(created_agent_id)
            idx += 1
        if status == "completed":
            sets.append(f"completed_at = NOW()")

        params.append(session_id)
        query = f"UPDATE builder_sessions SET {', '.join(sets)} WHERE id = ${idx}"
        await self.pg.execute(query, *params)

    async def abandon_builder_session(
        self,
        session_id: str,
        tenant_id: str,
    ) -> bool:
        result = await self.pg.execute(
            """
            UPDATE builder_sessions
            SET status = 'abandoned', updated_at = NOW()
            WHERE id = $1 AND tenant_id = $2
            """,
            session_id, tenant_id,
        )
        return "UPDATE 1" in result

    async def list_builder_sessions(
        self,
        tenant_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        rows = await self.pg.fetch(
            """
            SELECT id, tenant_id, user_id, status, current_state,
                   created_agent_id, created_at, updated_at
            FROM builder_sessions
            WHERE tenant_id = $1
            ORDER BY created_at DESC
            LIMIT $2 OFFSET $3
            """,
            tenant_id, limit, offset,
        )
        return [dict(r) for r in rows]
