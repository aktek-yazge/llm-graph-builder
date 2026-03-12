"""
Agent Registry
==============

Singleton that manages SelfEvolvingAgent instances and their
MultiServerMCPClient connections.

Responsibilities:
- Lazy-load agents on first access, persist identity in PostgreSQL
- Manage per-agent MCP client lifecycle (connect / disconnect)
- Provide shared pg pool and celery_app to all agents
- Graceful shutdown of all MCP connections on server stop
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)


class AgentRegistry:
    """Central registry for SelfEvolvingAgent instances."""

    def __init__(
        self,
        pg,
        celery_app=None,
        context_forge_base_url: str = "",
        context_forge_token: str = "",
    ) -> None:
        self._pg = pg
        self._celery_app = celery_app
        self._cf_base_url = context_forge_base_url or os.getenv(
            "CONTEXT_FORGE_URL", ""
        )
        self._cf_token = context_forge_token or os.getenv(
            "CONTEXT_FORGE_TOKEN", ""
        )

        self._agents: dict[str, Any] = {}
        self._mcp_clients: dict[str, Any] = {}
        self._lock = asyncio.Lock()

        from .notification_manager import NotificationManager
        self.notifications = NotificationManager()

    async def get_or_create(self, agent_id: str) -> Any:
        """Return a cached agent or create a new one."""
        if agent_id in self._agents:
            return self._agents[agent_id]

        async with self._lock:
            if agent_id in self._agents:
                return self._agents[agent_id]

            mcp_client = await self._create_mcp_client(agent_id)
            agent = await self._create_agent(agent_id, mcp_client)
            self._agents[agent_id] = agent
            logger.info("Agent loaded: %s", agent_id)
            return agent

    async def delete(self, agent_id: str) -> None:
        """Remove an agent and clean up its MCP client."""
        async with self._lock:
            agent = self._agents.pop(agent_id, None)
            mcp_client = self._mcp_clients.pop(agent_id, None)

        if mcp_client is not None:
            try:
                await mcp_client.__aexit__(None, None, None)
            except Exception as exc:
                logger.warning("MCP client cleanup failed for %s: %s", agent_id, exc)

        await self.notifications.close_agent(agent_id)

        await self._pg.execute(
            "DELETE FROM agent_knowledge WHERE agent_id = $1", agent_id
        )
        logger.info("Agent deleted: %s", agent_id)

    async def list_agent_ids(self, limit: int = 100) -> list[str]:
        """Return all known agent IDs from PostgreSQL."""
        rows = await self._pg.fetch(
            """
            SELECT DISTINCT agent_id
            FROM agent_knowledge
            WHERE knowledge_type = 'identity'
            ORDER BY agent_id
            LIMIT $1
            """,
            limit,
        )
        return [r["agent_id"] for r in rows]

    async def reload_all(self) -> None:
        """Pre-warm the registry from PostgreSQL (lazy: just records IDs)."""
        ids = await self.list_agent_ids()
        logger.info("Registry discovered %d agents in PostgreSQL", len(ids))

    async def shutdown(self) -> None:
        """Gracefully close all MCP clients."""
        async with self._lock:
            for aid, client in list(self._mcp_clients.items()):
                try:
                    await client.__aexit__(None, None, None)
                    logger.info("MCP client closed: %s", aid)
                except Exception as exc:
                    logger.warning("MCP client close error for %s: %s", aid, exc)
            self._mcp_clients.clear()
            self._agents.clear()
        logger.info("AgentRegistry shutdown complete")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _create_agent(self, agent_id: str, mcp_client) -> Any:
        from .self_evolving_agent import SelfEvolvingAgent

        agent = SelfEvolvingAgent(
            agent_id=agent_id,
            pg=self._pg,
            celery_app=self._celery_app,
            mcp_client=mcp_client,
            notification_mgr=self.notifications,
        )
        await agent.ensure_ready()
        return agent

    async def _create_mcp_client(self, agent_id: str) -> Optional[Any]:
        """Create a MultiServerMCPClient for this agent's Context Forge virtual server."""
        if not self._cf_base_url:
            logger.debug("No CONTEXT_FORGE_URL configured; agent %s will have no MCP tools", agent_id)
            return None

        try:
            from langchain_mcp_adapters.client import MultiServerMCPClient

            server_url = f"{self._cf_base_url.rstrip('/')}/v1/servers/{agent_id}/sse"
            client = MultiServerMCPClient(
                {
                    f"context-forge-{agent_id}": {
                        "url": server_url,
                        "transport": "sse",
                        "headers": {"Authorization": f"Bearer {self._cf_token}"},
                    }
                }
            )
            await client.__aenter__()
            self._mcp_clients[agent_id] = client
            logger.info("MCP client connected for agent %s", agent_id)
            return client
        except Exception as exc:
            logger.warning("MCP client creation failed for %s: %s (agent will work without MCP tools)", agent_id, exc)
            return None
