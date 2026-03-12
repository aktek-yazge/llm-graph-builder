"""
Self-Evolving Agent
===================

Konusma yoluyla kendini gelistiren agent.
KG Prompt Generator yaklasimi + deepagents pattern'i birlesimi.

deepagents create_deep_agent kullanir:
- Dinamik system prompt (ontolojiden otomatik uretilir)
- Self-improvement tools (ontoloji CRUD)
- MCP tools (MultiServerMCPClient ile Context Forge'dan dinamik)
- OCR bridge tools (Celery pipeline)
- Batch & quality tools
- Subagents: quality-analyst, ocr-strategy-advisor
- Human-in-the-loop: start_batch_processing, save_as_skill icin onay

LangGraph StateGraph KULLANILMAZ.
State takibi PostgreSQL knowledge_store uzerinden yapilir.
Conversation persistence deepagents + AsyncPostgresSaver ile yapilir.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any, AsyncIterator, Optional

from deepagents import create_deep_agent
from langchain_core.messages import HumanMessage

from .knowledge_store import KnowledgeStore
from .agent_memory import AgentMemory
from .agent_skills import AgentSkillManager
from .tools.self_tools import create_self_tools
from .tools.ocr_tools import create_ocr_tools
from .tools.batch_tools import create_batch_tools
from .tools.quality_tools import create_quality_tools

logger = logging.getLogger(__name__)

LLM_PROVIDER = os.getenv("EVOLVING_LLM_PROVIDER", "google")
LLM_MODEL = os.getenv("EVOLVING_LLM_MODEL", "gemini-2.5-flash")
LLM_TEMPERATURE = float(os.getenv("EVOLVING_LLM_TEMPERATURE", "0.3"))


def _model_string(provider: str = LLM_PROVIDER, model: str = LLM_MODEL) -> str:
    """Build provider:model string for deepagents init_chat_model."""
    provider = provider.lower()
    provider_map = {
        "google": "google_genai",
        "gemini": "google_genai",
        "openai": "openai",
        "gpt": "openai",
        "anthropic": "anthropic",
        "claude": "anthropic",
    }
    prefix = provider_map.get(provider, provider)
    return f"{prefix}:{model}"


class SelfEvolvingAgent:
    """
    Konusma yoluyla kendini gelistiren agent.

    Lifecycle:
        1. Agent olusturulur (agent_id ile)
        2. Kullanici sohbet eder, agent tool'lari kullanarak ontolojisini olusturur
        3. Ontoloji yeterliyken save_as_skill ile Celery uyumlu SkillExecution uretir
        4. Pipeline calismaya baslar

    Agent'in butun bilgisi PostgreSQL agent_knowledge tablosunda saklanir.
    System prompt her mesajda ontolojiden dinamik olarak uretilir.
    """

    def __init__(
        self,
        agent_id: str,
        pg,
        celery_app=None,
        mcp_client=None,
        notification_mgr=None,
        llm_provider: str = LLM_PROVIDER,
        llm_model: str = LLM_MODEL,
    ):
        self.agent_id = agent_id
        self._pg = pg
        self.store = KnowledgeStore(pg)
        self.memory = AgentMemory(self.store)
        self.skill_manager = AgentSkillManager(self.store, self.memory)
        self._celery_app = celery_app
        self._mcp_client = mcp_client
        self._notification_mgr = notification_mgr
        self._model_str = _model_string(llm_provider, llm_model)
        self._checkpointer = None

    async def ensure_ready(self) -> None:
        """Tabloyu olustur, identity yoksa varsayilan kaydet."""
        await self.store.ensure_table()
        identity = await self.store.load_identity(self.agent_id)
        if not identity.get("purpose"):
            await self.store.save_identity(
                self.agent_id,
                name="Yeni Agent",
                purpose="Henuz belirlenmedi. Kullaniciyla konusarak amac belirlenecek.",
            )

        self._checkpointer = await self._create_checkpointer()

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    def _build_local_tools(self) -> list:
        """Assemble all locally-defined (non-MCP) tools."""
        tools: list = []
        tools.extend(create_self_tools(self.agent_id, self.store, self.memory, self.skill_manager))
        tools.extend(create_ocr_tools(self.agent_id, self._celery_app))
        tools.extend(create_batch_tools(self.agent_id, pg=self._pg, celery_app=self._celery_app))
        return tools

    def _build_subagent_tools(self) -> list:
        """Quality and OCR strategy tools delegated to subagents."""
        quality_tools = create_quality_tools(self.agent_id, pg=self._pg)
        return quality_tools

    def _build_subagents(self) -> list[dict[str, Any]]:
        quality_tools = create_quality_tools(self.agent_id, pg=self._pg)

        ocr_tools = create_ocr_tools(self.agent_id, self._celery_app)
        test_tool = [t for t in ocr_tools if t.name == "test_extraction_on_sample"]

        return [
            {
                "name": "quality-analyst",
                "description": (
                    "Extraction kalitesini analiz et. "
                    "Batch tamamlandiginda veya kullanici istediginde: "
                    "extraction sonuclarini ornekle, celiskileri bul, anomalileri tespit et, rapor uret."
                ),
                "tools": quality_tools,
                "model": "google_genai:gemini-2.5-flash",
            },
            {
                "name": "ocr-strategy-advisor",
                "description": (
                    "Yeni belge tipi geldiginde veya OCR kalitesi dusuk oldugunda: "
                    "ornek sayfalari analiz et, en uygun OCR modunu oner."
                ),
                "tools": test_tool,
                "model": "google_genai:gemini-2.5-flash",
            },
        ]

    # ------------------------------------------------------------------
    # Agent construction
    # ------------------------------------------------------------------

    async def _build_agent(self):
        """Create a compiled deep agent graph for this session."""
        system_prompt = await self.memory.build_system_prompt(self.agent_id)
        local_tools = self._build_local_tools()

        mcp_tools: list = []
        if self._mcp_client is not None:
            try:
                mcp_tools = self._mcp_client.get_tools()
            except Exception as exc:
                logger.warning("MCP tools unavailable: %s", exc)

        all_tools = [*local_tools, *mcp_tools]
        subagents = self._build_subagents()

        kwargs: dict[str, Any] = {
            "model": self._model_str,
            "tools": all_tools,
            "system_prompt": system_prompt,
            "subagents": subagents,
            "interrupt_on": {
                "start_batch_processing": True,
                "save_as_skill": True,
            },
        }
        if self._checkpointer:
            kwargs["checkpointer"] = self._checkpointer

        return create_deep_agent(**kwargs)

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------

    async def chat(
        self,
        message: str,
        session_id: str = "",
    ) -> AsyncIterator[dict[str, Any]]:
        """
        Kullanici mesajini isle, tool-calling loop ile yanit uret.

        Yields:
            {"type": "tool_call", "name": ..., "args": ...}
            {"type": "tool_result", "name": ..., "result": ...}
            {"type": "message_chunk", "content": ...}
            {"type": "final_response", "content": ...}
        """
        if not session_id:
            session_id = f"sess-{uuid.uuid4().hex[:12]}"

        agent = await self._build_agent()

        config: dict[str, Any] = {"recursion_limit": 50}
        if self._checkpointer:
            config["configurable"] = {"thread_id": f"{self.agent_id}:{session_id}"}

        full_response = ""
        try:
            async for event in agent.astream(
                {"messages": [HumanMessage(content=message)]},
                config=config,
            ):
                for node_name, node_output in event.items():
                    messages = node_output.get("messages", [])
                    for msg in messages:
                        from langchain_core.messages import AIMessage, ToolMessage

                        if isinstance(msg, AIMessage):
                            if msg.tool_calls:
                                for tc in msg.tool_calls:
                                    yield {
                                        "type": "tool_call",
                                        "name": tc["name"],
                                        "args": tc["args"],
                                    }
                            elif msg.content:
                                full_response += msg.content
                                yield {
                                    "type": "message_chunk",
                                    "content": msg.content,
                                }
                        elif isinstance(msg, ToolMessage):
                            result_preview = str(msg.content)[:500]
                            yield {
                                "type": "tool_result",
                                "name": msg.name,
                                "result": result_preview,
                            }
        except Exception as e:
            logger.error("Agent chat error: %s", e, exc_info=True)
            full_response = f"Bir hata olustu: {e}"
            yield {"type": "error", "content": str(e)}

        yield {
            "type": "final_response",
            "content": full_response,
            "session_id": session_id,
        }

    async def resume_after_interrupt(
        self,
        session_id: str,
        decision: str = "approve",
    ) -> AsyncIterator[dict[str, Any]]:
        """
        Resume agent after a human-in-the-loop interrupt.
        decision: 'approve' | 'reject'
        """
        if not self._checkpointer:
            yield {"type": "error", "content": "Checkpointer not configured"}
            return

        agent = await self._build_agent()
        thread_id = f"{self.agent_id}:{session_id}"

        resume_value = decision == "approve"

        full_response = ""
        try:
            from langgraph.types import Command
            async for event in agent.astream(
                Command(resume=resume_value),
                config={"configurable": {"thread_id": thread_id}},
            ):
                for node_name, node_output in event.items():
                    messages = node_output.get("messages", [])
                    for msg in messages:
                        from langchain_core.messages import AIMessage, ToolMessage

                        if isinstance(msg, AIMessage):
                            if msg.tool_calls:
                                for tc in msg.tool_calls:
                                    yield {"type": "tool_call", "name": tc["name"], "args": tc["args"]}
                            elif msg.content:
                                full_response += msg.content
                                yield {"type": "message_chunk", "content": msg.content}
                        elif isinstance(msg, ToolMessage):
                            yield {"type": "tool_result", "name": msg.name, "result": str(msg.content)[:500]}
        except Exception as e:
            logger.error("Agent resume error: %s", e, exc_info=True)
            yield {"type": "error", "content": str(e)}

        yield {"type": "final_response", "content": full_response, "session_id": session_id}

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    async def chat_sync(self, message: str, session_id: str = "") -> str:
        """Senkron convenience wrapper (test icin)."""
        result = ""
        async for chunk in self.chat(message, session_id):
            if chunk["type"] == "final_response":
                result = chunk["content"]
        return result

    async def get_ontology_summary(self) -> dict[str, Any]:
        """Mevcut ontoloji ozetini dondur."""
        ontology = await self.store.load_ontology(self.agent_id)
        identity = await self.store.load_identity(self.agent_id)
        return {
            "agent_id": self.agent_id,
            "name": identity.get("name", ""),
            "purpose": identity.get("purpose", ""),
            "domain": ontology.domain,
            "goal": ontology.goal,
            "entity_count": len(ontology.entity_classes),
            "relationship_count": len(ontology.relationship_predicates),
            "rule_count": len(ontology.inference_rules),
            "constraint_count": len(ontology.constraints),
            "is_empty": ontology.is_empty,
        }

    async def get_skill_execution(self) -> dict[str, Any] | None:
        """AgenticOCR uyumlu SkillExecution formatini dondur."""
        return await self.skill_manager.get_skill_for_api(self.agent_id)

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    async def _create_checkpointer(self):
        """Create an async PostgreSQL checkpointer for conversation persistence."""
        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

            dsn = os.getenv(
                "EVENT_STORE_DSN",
                "postgresql://event_user:event_secret@localhost:5433/event_store",
            )
            saver = AsyncPostgresSaver.from_conn_string(dsn)
            await saver.setup()
            return saver
        except Exception as exc:
            logger.warning("AsyncPostgresSaver not available: %s (conversations will not persist)", exc)
            return None
