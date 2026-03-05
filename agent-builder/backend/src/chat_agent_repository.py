"""
Chat Agent Repository
=====================

PostgreSQL repository for Chat Agent records.
A Chat Agent maps to a Virtual Server on the MCP Context Forge Gateway.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from .event_store.postgres_client import PostgresClient

logger = logging.getLogger(__name__)


class ChatAgentRepository:
    def __init__(self, pg: PostgresClient):
        self._pg = pg

    async def create(
        self,
        name: str,
        tenant_id: str = "default",
        description: str = "",
        workspace_id: Optional[str] = None,
        workspace_ids: Optional[List[str]] = None,
        agent_type: str = "expert",
        system_prompt: Optional[str] = None,
        associated_tools: Optional[List[str]] = None,
        associated_prompts: Optional[List[str]] = None,
        associated_resources: Optional[List[str]] = None,
        kb_resource_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
        config: Optional[Dict[str, Any]] = None,
        delegation_config: Optional[Dict[str, Any]] = None,
        connected_agent_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        effective_ws_ids = workspace_ids or []
        if workspace_id and workspace_id not in effective_ws_ids:
            effective_ws_ids = [workspace_id] + effective_ws_ids

        row = await self._pg.fetchrow(
            """
            INSERT INTO chat_agents
                (name, description, tenant_id, workspace_id, workspace_ids,
                 agent_type, system_prompt,
                 associated_tools, associated_prompts, associated_resources,
                 kb_resource_id, tags, config,
                 delegation_config, connected_agent_ids)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7,
                    $8::jsonb, $9::jsonb, $10::jsonb,
                    $11::uuid, $12, $13::jsonb,
                    $14::jsonb, $15::jsonb)
            RETURNING *
            """,
            name,
            description,
            tenant_id,
            workspace_id,
            json.dumps(effective_ws_ids),
            agent_type,
            system_prompt,
            json.dumps(associated_tools or []),
            json.dumps(associated_prompts or []),
            json.dumps(associated_resources or []),
            kb_resource_id,
            tags or [],
            json.dumps(config or {}),
            json.dumps(delegation_config or {"auto_threshold": 0.8, "max_depth": 3, "enabled": True}),
            json.dumps(connected_agent_ids or []),
        )
        return dict(row)

    async def get(self, agent_id: str) -> Optional[Dict[str, Any]]:
        row = await self._pg.fetchrow(
            "SELECT * FROM chat_agents WHERE id = $1::uuid", agent_id
        )
        return dict(row) if row else None

    async def list_by_tenant(
        self,
        tenant_id: str,
        limit: int = 50,
        offset: int = 0,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if status:
            rows = await self._pg.fetch(
                """
                SELECT * FROM chat_agents
                WHERE tenant_id = $1 AND status = $2
                ORDER BY created_at DESC
                LIMIT $3 OFFSET $4
                """,
                tenant_id, status, limit, offset,
            )
        else:
            rows = await self._pg.fetch(
                """
                SELECT * FROM chat_agents
                WHERE tenant_id = $1
                ORDER BY created_at DESC
                LIMIT $2 OFFSET $3
                """,
                tenant_id, limit, offset,
            )
        return [dict(r) for r in rows]

    async def list_by_workspace(self, workspace_id: str) -> List[Dict[str, Any]]:
        """Find agents connected to a workspace (checks both legacy and new fields)."""
        rows = await self._pg.fetch(
            """
            SELECT * FROM chat_agents
            WHERE workspace_id = $1
               OR workspace_ids @> $2::jsonb
            ORDER BY created_at DESC
            """,
            workspace_id,
            json.dumps([workspace_id]),
        )
        return [dict(r) for r in rows]

    async def update(self, agent_id: str, **kwargs) -> Optional[Dict[str, Any]]:
        sets = []
        args = []
        idx = 1

        field_map = {
            "name": "name",
            "description": "description",
            "status": "status",
            "agent_type": "agent_type",
            "system_prompt": "system_prompt",
            "gateway_server_id": "gateway_server_id",
            "workspace_id": "workspace_id",
            "a2a_agent_id": "a2a_agent_id",
            "kb_resource_id": "kb_resource_id",
        }
        json_fields = {
            "associated_tools", "associated_prompts", "associated_resources",
            "config", "workspace_ids", "delegation_config", "connected_agent_ids",
        }
        array_fields = {"tags"}

        for key, val in kwargs.items():
            if val is None:
                continue
            if key in field_map:
                sets.append(f"{field_map[key]} = ${idx}")
                args.append(val)
                idx += 1
            elif key in json_fields:
                sets.append(f"{key} = ${idx}::jsonb")
                args.append(json.dumps(val))
                idx += 1
            elif key in array_fields:
                sets.append(f"{key} = ${idx}")
                args.append(val)
                idx += 1

        if not sets:
            return await self.get(agent_id)

        sets.append(f"updated_at = NOW()")
        args.append(agent_id)

        query = f"""
            UPDATE chat_agents
            SET {', '.join(sets)}
            WHERE id = ${idx}::uuid
            RETURNING *
        """
        row = await self._pg.fetchrow(query, *args)
        return dict(row) if row else None

    async def delete(self, agent_id: str) -> bool:
        result = await self._pg.execute(
            "DELETE FROM chat_agents WHERE id = $1::uuid", agent_id
        )
        return "DELETE 1" in result

    async def set_gateway_server_id(
        self, agent_id: str, gateway_server_id: str
    ) -> Optional[Dict[str, Any]]:
        return await self.update(
            agent_id, gateway_server_id=gateway_server_id, status="active"
        )

    async def count_by_tenant(self, tenant_id: str) -> int:
        return await self._pg.fetchval(
            "SELECT COUNT(*) FROM chat_agents WHERE tenant_id = $1", tenant_id
        )

    async def get_connected_agents(self, agent_id: str) -> List[Dict[str, Any]]:
        """Get all agents that this agent can delegate to."""
        agent = await self.get(agent_id)
        if not agent:
            return []
        connected_ids = agent.get("connected_agent_ids") or []
        if not connected_ids:
            return []
        placeholders = ", ".join(f"${i + 1}::uuid" for i in range(len(connected_ids)))
        rows = await self._pg.fetch(
            f"SELECT * FROM chat_agents WHERE id IN ({placeholders})",
            *connected_ids,
        )
        return [dict(r) for r in rows]

    async def bind_workspaces(
        self, agent_id: str, workspace_ids: List[str]
    ) -> Optional[Dict[str, Any]]:
        """Set the workspace_ids for an agent."""
        return await self.update(agent_id, workspace_ids=workspace_ids)

    async def set_a2a_agent_id(
        self, agent_id: str, a2a_agent_id: str
    ) -> Optional[Dict[str, Any]]:
        """Store the ContextForge A2A registry ID."""
        return await self.update(agent_id, a2a_agent_id=a2a_agent_id)

    async def list_active_agents(self, tenant_id: str) -> List[Dict[str, Any]]:
        """List all active (deployed) agents for delegation routing."""
        rows = await self._pg.fetch(
            """
            SELECT * FROM chat_agents
            WHERE tenant_id = $1 AND status = 'active'
            ORDER BY name ASC
            """,
            tenant_id,
        )
        return [dict(r) for r in rows]
