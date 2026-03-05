"""
A2A Router
==========

Agent-to-Agent delegation routing service.
Handles smart hybrid delegation: auto-delegate above threshold, ask user below.
Tracks all delegations via agent_messages for auditability.
"""

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, List, Optional

from ..chat_agent_repository import ChatAgentRepository
from ..comms_repository import CommsRepository
from ..event_store.postgres_client import PostgresClient, get_postgres_client

logger = logging.getLogger(__name__)

MAX_DELEGATION_DEPTH = 3


@dataclass
class DelegationResult:
    should_delegate: bool = False
    auto_delegate: bool = False
    target_agent_id: Optional[str] = None
    target_agent_name: Optional[str] = None
    confidence: float = 0.0
    reason: str = ""
    candidates: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class DelegationResponse:
    success: bool = True
    response: str = ""
    source_agent_id: str = ""
    source_agent_name: str = ""
    delegation_id: str = ""
    error: Optional[str] = None


class A2ARouter:
    """Routes delegation requests between agents with circular dependency protection."""

    def __init__(self, pg: PostgresClient):
        self._repo = ChatAgentRepository(pg)
        self._comms = CommsRepository(pg)

    @classmethod
    async def create(cls) -> "A2ARouter":
        pg = await get_postgres_client()
        return cls(pg)

    async def get_available_agents(
        self, agent_id: str, tenant_id: str = "default"
    ) -> List[Dict[str, Any]]:
        """Get agents available for delegation from this agent."""
        agent = await self._repo.get(agent_id)
        if not agent:
            return []

        connected_ids = agent.get("connected_agent_ids") or []
        if connected_ids:
            agents = await self._repo.get_connected_agents(agent_id)
        else:
            agents = await self._repo.list_active_agents(tenant_id)
            agents = [a for a in agents if str(a["id"]) != agent_id]

        return [
            {
                "id": str(a["id"]),
                "name": a["name"],
                "description": a.get("description", ""),
                "agent_type": a.get("agent_type", "expert"),
                "workspace_ids": a.get("workspace_ids") or [],
                "status": a["status"],
            }
            for a in agents
            if a["status"] == "active"
        ]

    async def route_delegation(
        self,
        from_agent_id: str,
        question: str,
        context_summary: str = "",
        tenant_id: str = "default",
    ) -> DelegationResult:
        """
        Determine which agent should handle a delegated question.
        Uses workspace descriptions and agent metadata for relevance scoring.
        """
        from_agent = await self._repo.get(from_agent_id)
        if not from_agent:
            return DelegationResult(reason="Source agent not found")

        delegation_config = from_agent.get("delegation_config") or {}
        if not delegation_config.get("enabled", True):
            return DelegationResult(reason="Delegation disabled for this agent")

        threshold = delegation_config.get("auto_threshold", 0.8)
        candidates = await self.get_available_agents(from_agent_id, tenant_id)

        if not candidates:
            return DelegationResult(reason="No agents available for delegation")

        scored = []
        q_lower = question.lower()
        for c in candidates:
            score = 0.0
            name_lower = c["name"].lower()
            desc_lower = c.get("description", "").lower()

            name_words = name_lower.split()
            for word in name_words:
                if len(word) > 2 and word in q_lower:
                    score += 0.3

            desc_words = desc_lower.split()
            for word in desc_words:
                if len(word) > 3 and word in q_lower:
                    score += 0.1

            score = min(score, 1.0)
            scored.append({**c, "relevance_score": score})

        scored.sort(key=lambda x: x["relevance_score"], reverse=True)
        best = scored[0] if scored else None

        if not best or best["relevance_score"] < 0.1:
            return DelegationResult(
                should_delegate=False,
                reason="No relevant agent found",
                candidates=scored[:5],
            )

        return DelegationResult(
            should_delegate=True,
            auto_delegate=best["relevance_score"] >= threshold,
            target_agent_id=best["id"],
            target_agent_name=best["name"],
            confidence=best["relevance_score"],
            reason=f"Best match: {best['name']} (score={best['relevance_score']:.2f})",
            candidates=scored[:5],
        )

    async def execute_delegation(
        self,
        from_agent_id: str,
        to_agent_id: str,
        question: str,
        context: str = "",
        delegation_chain: Optional[List[str]] = None,
    ) -> DelegationResponse:
        """Execute a delegation: send the question to the target agent and return its response."""
        chain = delegation_chain or []

        if not self._check_circular(chain, to_agent_id):
            return DelegationResponse(
                success=False,
                error=f"Circular delegation detected: {' -> '.join(chain)} -> {to_agent_id}",
            )

        if len(chain) >= MAX_DELEGATION_DEPTH:
            return DelegationResponse(
                success=False,
                error=f"Max delegation depth ({MAX_DELEGATION_DEPTH}) reached",
            )

        to_agent = await self._repo.get(to_agent_id)
        if not to_agent:
            return DelegationResponse(success=False, error="Target agent not found")

        if to_agent["status"] != "active":
            return DelegationResponse(
                success=False, error=f"Target agent status: {to_agent['status']}"
            )

        delegation_id = f"del-{uuid.uuid4().hex[:12]}"

        await self._comms.send_message(
            from_agent=from_agent_id,
            to_agent=to_agent_id,
            message=question,
            message_type="delegation_request",
            metadata={
                "delegation_id": delegation_id,
                "context": context[:500],
                "chain": chain + [from_agent_id],
            },
        )

        try:
            response_text = await self._invoke_agent(
                to_agent,
                question,
                context,
                chain + [from_agent_id],
            )

            await self._comms.send_message(
                from_agent=to_agent_id,
                to_agent=from_agent_id,
                message=response_text,
                message_type="delegation_response",
                metadata={
                    "delegation_id": delegation_id,
                    "success": True,
                },
            )

            workspace_ids = to_agent.get("workspace_ids") or []
            ws_label = f" [WS: {', '.join(workspace_ids[:3])}]" if workspace_ids else ""
            attribution = f"\n\n---\n[Kaynak: {to_agent['name']}{ws_label}]"
            attributed_response = response_text + attribution

            return DelegationResponse(
                success=True,
                response=attributed_response,
                source_agent_id=to_agent_id,
                source_agent_name=to_agent["name"],
                delegation_id=delegation_id,
            )

        except Exception as e:
            logger.error("Delegation to %s failed: %s", to_agent_id, e)
            await self._comms.send_message(
                from_agent=to_agent_id,
                to_agent=from_agent_id,
                message=str(e),
                message_type="delegation_error",
                metadata={"delegation_id": delegation_id, "error": str(e)},
            )
            return DelegationResponse(
                success=False,
                error=str(e),
                source_agent_id=to_agent_id,
                source_agent_name=to_agent["name"],
                delegation_id=delegation_id,
            )

    async def _invoke_agent(
        self,
        agent: Dict[str, Any],
        question: str,
        context: str,
        chain: List[str],
    ) -> str:
        """Invoke a target agent's ReactAgentV2 to get a response."""
        from .react_agent_v2 import stream_react_agent_v2_response

        gw_id = agent.get("gateway_server_id")
        if not gw_id:
            raise ValueError(f"Agent {agent['name']} is not deployed (no gateway_server_id)")

        model = (agent.get("config") or {}).get("model", "gpt-4o")
        agent_id = str(agent["id"])
        session_id = f"delegation-{uuid.uuid4().hex[:8]}"

        full_question = question
        if context:
            full_question = f"[Context from delegating agent]: {context}\n\n[Question]: {question}"

        chunks = []
        async for chunk in stream_react_agent_v2_response(
            question=full_question,
            server_id=gw_id,
            model=model,
            session_id=session_id,
            question_id=f"del-{uuid.uuid4().hex[:8]}",
            graph=None,
            user_id=f"delegation-{agent_id}",
        ):
            if chunk.get("type") == "final_response":
                return chunk.get("content", "")
            if chunk.get("type") == "message_chunk":
                chunks.append(chunk.get("content", ""))

        return "".join(chunks) if chunks else "No response received from delegated agent."

    def _check_circular(self, chain: List[str], target_id: str) -> bool:
        """Return False if adding target_id would create a circular delegation."""
        return target_id not in chain

    async def get_delegation_history(
        self,
        agent_id: str,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Get recent delegation activity for an agent (sent and received)."""
        pg = self._comms.pg
        rows = await pg.fetch(
            """
            SELECT id, from_agent, to_agent, message, message_type,
                   metadata, status, created_at
            FROM agent_messages
            WHERE (from_agent = $1 OR to_agent = $1)
              AND message_type IN ('delegation_request', 'delegation_response', 'delegation_error')
            ORDER BY created_at DESC
            LIMIT $2
            """,
            agent_id,
            limit,
        )
        return [
            {
                "id": r["id"],
                "from_agent": r["from_agent"],
                "to_agent": r["to_agent"],
                "message": r["message"][:200],
                "message_type": r["message_type"],
                "metadata": r.get("metadata") or {},
                "created_at": str(r.get("created_at", "")),
            }
            for r in rows
        ]
