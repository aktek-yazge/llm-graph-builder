"""
Orchestrator Service
====================

Tek kullaniciya donuk orkestrator agent'i yonetir.
Kullanicinin sorusunu analiz eder, ilgili expert agent'lari belirler,
paralel olarak sorgular ve yanitlari sentezleyip attribution ile sunar.
"""

import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..chat_agent_repository import ChatAgentRepository
from ..event_store.postgres_client import PostgresClient, get_postgres_client

logger = logging.getLogger(__name__)

EXPERT_QUERY_TIMEOUT = 120
MIN_RELEVANCE_THRESHOLD = 0.1


def _detect_default_model() -> str:
    """Mevcut API anahtarina gore en uygun modeli sec."""
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return "gemini-2.5-flash"
    if os.environ.get("OPENAI_API_KEY"):
        return "gpt-4o"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "claude-sonnet-4-20250514"
    return "gemini-2.5-flash"


@dataclass
class ExpertResponse:
    agent_id: str
    agent_name: str
    workspace_names: List[str]
    response: str
    success: bool = True
    error: Optional[str] = None
    elapsed_ms: float = 0


class OrchestratorService:
    """Orchestrates parallel expert agent queries and synthesizes responses."""

    def __init__(self, pg: PostgresClient):
        self._repo = ChatAgentRepository(pg)

    @classmethod
    async def create(cls) -> "OrchestratorService":
        pg = await get_postgres_client()
        return cls(pg)

    async def get_all_experts(self, tenant_id: str = "default") -> List[Dict[str, Any]]:
        """Get all active expert agents."""
        agents = await self._repo.list_active_agents(tenant_id)
        return [
            a for a in agents
            if a.get("agent_type", "expert") == "expert" and a["status"] == "active"
        ]

    async def select_relevant_experts(
        self,
        question: str,
        tenant_id: str = "default",
        all_experts: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Soruya ilgili expert'leri sec.
        Simdilik keyword-based filtreleme; ileride LLM-based filtreleme eklenebilir.
        """
        if all_experts is None:
            all_experts = await self.get_all_experts(tenant_id)

        if not all_experts:
            return []

        q_lower = question.lower()
        scored = []

        for expert in all_experts:
            score = 0.0
            name_lower = expert["name"].lower()
            desc_lower = (expert.get("description") or "").lower()

            for word in name_lower.split():
                if len(word) > 2 and word in q_lower:
                    score += 0.3

            for word in desc_lower.split():
                if len(word) > 3 and word in q_lower:
                    score += 0.1

            score = min(score, 1.0)
            scored.append({**expert, "_relevance": score})

        scored.sort(key=lambda x: x["_relevance"], reverse=True)

        relevant = [e for e in scored if e["_relevance"] >= MIN_RELEVANCE_THRESHOLD]

        if not relevant:
            logger.info(
                "No experts above threshold (%.2f), including all %d experts",
                MIN_RELEVANCE_THRESHOLD,
                len(all_experts),
            )
            relevant = scored

        return relevant[:5]

    async def fan_out_query(
        self,
        question: str,
        experts: List[Dict[str, Any]],
        context: str = "",
    ) -> List[ExpertResponse]:
        """Paralel olarak tum expert'leri sorgula."""
        if not experts:
            return []

        tasks = [
            self._query_single_expert(expert, question, context)
            for expert in experts
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        responses = []
        for expert, result in zip(experts, results):
            if isinstance(result, Exception):
                responses.append(ExpertResponse(
                    agent_id=str(expert["id"]),
                    agent_name=expert["name"],
                    workspace_names=self._get_ws_names(expert),
                    response="",
                    success=False,
                    error=str(result),
                ))
            else:
                responses.append(result)

        return responses

    async def _query_single_expert(
        self,
        expert: Dict[str, Any],
        question: str,
        context: str,
    ) -> ExpertResponse:
        """Tek bir expert agent'i sorgula (timeout korumali)."""
        import time

        from .react_agent_v2 import stream_react_agent_v2_response

        agent_id = str(expert["id"])
        agent_name = expert["name"]
        gw_id = expert.get("gateway_server_id")

        if not gw_id:
            return ExpertResponse(
                agent_id=agent_id,
                agent_name=agent_name,
                workspace_names=self._get_ws_names(expert),
                response="",
                success=False,
                error="Agent deployed degil (gateway_server_id yok)",
            )

        config = expert.get("config") or {}
        if isinstance(config, str):
            import json as _json
            try:
                config = _json.loads(config)
            except Exception:
                config = {}
        default_model = _detect_default_model()
        model = config.get("model", default_model) if isinstance(config, dict) else default_model
        session_id = f"orch-{uuid.uuid4().hex[:8]}"
        question_id = f"orch-q-{uuid.uuid4().hex[:8]}"

        full_question = question
        if context:
            full_question = f"[Ek baglanm]: {context}\n\n[Soru]: {question}"

        start = time.monotonic()
        chunks = []

        try:
            async with asyncio.timeout(EXPERT_QUERY_TIMEOUT):
                async for chunk in stream_react_agent_v2_response(
                    question=full_question,
                    server_id=gw_id,
                    model=model,
                    session_id=session_id,
                    question_id=question_id,
                    graph=None,
                    user_id=f"orchestrator-{agent_id}",
                ):
                    if chunk.get("type") == "final_response":
                        elapsed = (time.monotonic() - start) * 1000
                        return ExpertResponse(
                            agent_id=agent_id,
                            agent_name=agent_name,
                            workspace_names=self._get_ws_names(expert),
                            response=chunk.get("content", ""),
                            success=True,
                            elapsed_ms=elapsed,
                        )
                    if chunk.get("type") == "message_chunk":
                        chunks.append(chunk.get("content", ""))

        except asyncio.TimeoutError:
            elapsed = (time.monotonic() - start) * 1000
            logger.warning("Expert %s timed out after %.0fms", agent_name, elapsed)
            partial = "".join(chunks)
            return ExpertResponse(
                agent_id=agent_id,
                agent_name=agent_name,
                workspace_names=self._get_ws_names(expert),
                response=partial if partial else "",
                success=bool(partial),
                error=f"Timeout ({EXPERT_QUERY_TIMEOUT}s)" if not partial else None,
                elapsed_ms=elapsed,
            )

        elapsed = (time.monotonic() - start) * 1000
        response_text = "".join(chunks)
        return ExpertResponse(
            agent_id=agent_id,
            agent_name=agent_name,
            workspace_names=self._get_ws_names(expert),
            response=response_text if response_text else "Yanit alinamadi.",
            success=bool(response_text),
            elapsed_ms=elapsed,
        )

    def format_expert_responses(self, responses: List[ExpertResponse]) -> str:
        """Expert yanitlarini attribution ile formatla."""
        successful = [r for r in responses if r.success and r.response.strip()]

        if not successful:
            failed = [r for r in responses if not r.success]
            if failed:
                errors = "; ".join(f"{r.agent_name}: {r.error}" for r in failed)
                return f"Uzman agentlardan yanit alinamadi. Hatalar: {errors}"
            return "Ilgili uzman agent bulunamadi veya yanit alinamadi."

        parts = []
        for r in successful:
            ws_label = f" (Kaynak: {', '.join(r.workspace_names)})" if r.workspace_names else ""
            header = f"=== {r.agent_name}{ws_label} ==="
            parts.append(f"{header}\n{r.response}")

        formatted = "\n\n".join(parts)

        if len(successful) > 1:
            expert_names = ", ".join(r.agent_name for r in successful)
            formatted += f"\n\n--- Sentez icin bilgi kaynaklari: {expert_names} ---"

        return formatted

    def _get_ws_names(self, expert: Dict[str, Any]) -> List[str]:
        """Expert'in workspace isimlerini dondur (simdilik ID'leri)."""
        ws_ids = expert.get("workspace_ids") or []
        return [str(ws_id) for ws_id in ws_ids[:3]]

    async def get_or_create_orchestrator(
        self, tenant_id: str = "default"
    ) -> Optional[Dict[str, Any]]:
        """Get the default orchestrator agent, auto-create if not exists."""
        from ..event_store.postgres_client import get_postgres_client

        pg = await get_postgres_client()

        row = await pg.fetchrow(
            "SELECT * FROM chat_agents WHERE agent_type = 'orchestrator' AND tenant_id = $1 LIMIT 1",
            tenant_id,
        )
        if row:
            return dict(row)

        logger.info("Orchestrator agent not found, auto-creating for tenant=%s", tenant_id)

        from .deep_research_prompts import get_orchestrator_system_prompt

        try:
            agent_row = await self._repo.create(
                name="Orkestrator Agent",
                description="Kullanicinin tek giris noktasi. Soruyu analiz eder, ilgili uzman agentlari paralel sorgular ve yanitlari sentezler.",
                tenant_id=tenant_id,
                agent_type="orchestrator",
                system_prompt=get_orchestrator_system_prompt(),
                config={"model": _detect_default_model()},
                delegation_config={"auto_threshold": 0.5, "max_depth": 1, "enabled": True},
            )
            agent_id = str(agent_row["id"])
            logger.info("Orchestrator agent created: %s", agent_id)

            try:
                from ..gateway import get_gateway_client
                gw = await get_gateway_client()
                vs_result = await gw.create_virtual_server(
                    name="Orkestrator Agent",
                    description="Default orchestrator - paralel expert sorgulama ve sentez",
                    tags=["orchestrator", "auto-created"],
                )
                gw_id = vs_result.get("id", vs_result.get("server", {}).get("id", ""))
                if gw_id:
                    await self._repo.set_gateway_server_id(agent_id, gw_id)
                    logger.info("Orchestrator deployed to Gateway: %s", gw_id)

                    updated = await self._repo.get(agent_id)
                    if updated:
                        return updated
            except Exception as e:
                logger.warning("Orchestrator Gateway deploy failed (will work without): %s", e)

            created = await self._repo.get(agent_id)
            return created

        except Exception as e:
            logger.error("Failed to auto-create orchestrator: %s", e)
            return None
