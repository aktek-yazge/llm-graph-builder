"""
Comms Repository
=================

PostgreSQL-backed repository for Blackboard entries and Agent Messages.
Replaces Neo4j BlackboardEntry / AgentMessage nodes.
"""

import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from .event_store.postgres_client import PostgresClient

logger = logging.getLogger(__name__)


class CommsRepository:
    """Async CRUD for blackboard_entries and agent_messages tables."""

    def __init__(self, pg: PostgresClient):
        self.pg = pg

    # =========================================================================
    # BLACKBOARD
    # =========================================================================

    async def write_entry(
        self,
        topic: str,
        content: str,
        agent_id: str = "",
        workspace_id: str = "",
        entry_type: str = "info",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        entry_id = f"bb-{uuid.uuid4().hex[:12]}"
        meta_json = json.dumps(metadata or {}, ensure_ascii=False)
        await self.pg.execute(
            """
            INSERT INTO blackboard_entries (id, topic, content, agent_id, workspace_id, entry_type, metadata)
            VALUES ($1, $2, $3, NULLIF($4,''), NULLIF($5,''), $6, $7::jsonb)
            """,
            entry_id, topic, content, agent_id, workspace_id, entry_type, meta_json,
        )
        logger.debug("Blackboard write: topic=%s, agent=%s, entry=%s", topic, agent_id, entry_id)
        return {"id": entry_id, "topic": topic, "written": True}

    async def read_entries(
        self,
        topic: str = "",
        workspace_id: str = "",
        agent_id: str = "",
        entry_type: str = "",
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        conditions = []
        params: list = []
        idx = 1

        if topic:
            conditions.append(f"topic = ${idx}")
            params.append(topic)
            idx += 1
        if workspace_id:
            conditions.append(f"workspace_id = ${idx}")
            params.append(workspace_id)
            idx += 1
        if agent_id:
            conditions.append(f"agent_id = ${idx}")
            params.append(agent_id)
            idx += 1
        if entry_type:
            conditions.append(f"entry_type = ${idx}")
            params.append(entry_type)
            idx += 1

        where = " AND ".join(conditions) if conditions else "TRUE"
        params.append(limit)
        query = f"""
            SELECT id, topic, content, agent_id, workspace_id, entry_type,
                   metadata, created_at
            FROM blackboard_entries
            WHERE {where}
            ORDER BY created_at DESC
            LIMIT ${idx}
        """
        rows = await self.pg.fetch(query, *params)

        entries = []
        for r in rows:
            d = dict(r)
            meta = d.get("metadata")
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except (json.JSONDecodeError, TypeError):
                    meta = {}
            elif meta is None:
                meta = {}
            entries.append({
                "id": d.get("id", ""),
                "topic": d.get("topic", ""),
                "content": d.get("content", ""),
                "agent_id": d.get("agent_id", ""),
                "workspace_id": d.get("workspace_id", ""),
                "entry_type": d.get("entry_type", "info"),
                "metadata": meta,
                "created_at": str(d.get("created_at", "")) if d.get("created_at") else None,
            })
        return entries

    async def list_topics(
        self,
        workspace_id: str = "",
    ) -> List[Dict[str, Any]]:
        if workspace_id:
            rows = await self.pg.fetch(
                """
                SELECT topic,
                       count(*)::int AS entry_count,
                       max(created_at) AS last_updated,
                       array_agg(DISTINCT agent_id) FILTER (WHERE agent_id IS NOT NULL) AS agents
                FROM blackboard_entries
                WHERE workspace_id = $1
                GROUP BY topic
                ORDER BY last_updated DESC
                """,
                workspace_id,
            )
        else:
            rows = await self.pg.fetch(
                """
                SELECT topic,
                       count(*)::int AS entry_count,
                       max(created_at) AS last_updated,
                       array_agg(DISTINCT agent_id) FILTER (WHERE agent_id IS NOT NULL) AS agents
                FROM blackboard_entries
                GROUP BY topic
                ORDER BY last_updated DESC
                """,
            )
        return [
            {
                "topic": r["topic"],
                "entry_count": r["entry_count"],
                "last_updated": str(r.get("last_updated", "")),
                "agents": r.get("agents") or [],
            }
            for r in rows
        ]

    # =========================================================================
    # AGENT MESSAGES
    # =========================================================================

    async def send_message(
        self,
        from_agent: str,
        to_agent: str,
        message: str,
        workspace_id: str = "",
        message_type: str = "direct",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        msg_id = f"msg-{uuid.uuid4().hex[:12]}"
        meta_json = json.dumps(metadata or {}, ensure_ascii=False)
        await self.pg.execute(
            """
            INSERT INTO agent_messages (id, from_agent, to_agent, message,
                workspace_id, message_type, metadata, status)
            VALUES ($1, $2, $3, $4, NULLIF($5,''), $6, $7::jsonb, 'unread')
            """,
            msg_id, from_agent, to_agent, message, workspace_id, message_type, meta_json,
        )
        return {"id": msg_id, "from": from_agent, "to": to_agent, "sent": True}

    async def get_messages(
        self,
        agent_id: str,
        status: str = "unread",
        workspace_id: str = "",
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        conditions = ["to_agent = $1", "status = $2"]
        params: list = [agent_id, status]
        idx = 3
        if workspace_id:
            conditions.append(f"workspace_id = ${idx}")
            params.append(workspace_id)
            idx += 1

        params.append(limit)
        query = f"""
            SELECT id, from_agent, message, message_type, workspace_id,
                   status, created_at
            FROM agent_messages
            WHERE {' AND '.join(conditions)}
            ORDER BY created_at DESC
            LIMIT ${idx}
        """
        rows = await self.pg.fetch(query, *params)
        return [
            {
                "id": r["id"],
                "from_agent": r["from_agent"],
                "message": r["message"],
                "message_type": r["message_type"],
                "workspace_id": r.get("workspace_id", ""),
                "status": r["status"],
                "created_at": str(r.get("created_at", "")),
            }
            for r in rows
        ]

    async def mark_read(self, message_ids: List[str]) -> int:
        if not message_ids:
            return 0
        placeholders = ", ".join(f"${i+1}" for i in range(len(message_ids)))
        result = await self.pg.execute(
            f"""
            UPDATE agent_messages
            SET status = 'read', read_at = NOW()
            WHERE id IN ({placeholders})
            """,
            *message_ids,
        )
        try:
            return int(result.split()[-1]) if result else 0
        except (ValueError, IndexError):
            return 0
