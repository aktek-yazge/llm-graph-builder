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
from .wiki_store import WikiStore
from .agent_memory import AgentMemory
from .agent_skills import AgentSkillManager
from .tools.self_tools import create_self_tools
from .tools.wiki_tools import create_wiki_tools
from .tools.ocr_tools import create_ocr_tools
from .tools.batch_tools import create_batch_tools
from .tools.quality_tools import create_quality_tools

logger = logging.getLogger(__name__)

CHAT_PROVIDER = os.getenv("EVOLVING_CHAT_PROVIDER", "openai")
CHAT_MODEL = os.getenv("EVOLVING_CHAT_MODEL", "gpt-5.4")
CHAT_TEMPERATURE = float(os.getenv("EVOLVING_CHAT_TEMPERATURE", "0.3"))

SUBAGENT_MODEL = os.getenv("EVOLVING_SUBAGENT_MODEL", "openai:gpt-5.4-mini")

_PROVIDER_MAP = {
    "google": "google_genai",
    "gemini": "google_genai",
    "openai": "openai",
    "gpt": "openai",
    "anthropic": "anthropic",
    "claude": "anthropic",
}


def _model_string(provider: str = CHAT_PROVIDER, model: str = CHAT_MODEL) -> str:
    """Build provider:model string for deepagents init_chat_model."""
    prefix = _PROVIDER_MAP.get(provider.lower(), provider.lower())
    return f"{prefix}:{model}"


def _extract_text(content: Any) -> str:
    """Normalize msg.content which may be str, list[dict], or other."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(item.get("text", str(item)))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(content) if content else ""


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
        llm_provider: str = CHAT_PROVIDER,
        llm_model: str = CHAT_MODEL,
    ):
        self.agent_id = agent_id
        self._pg = pg
        self.store = KnowledgeStore(pg)
        self.wiki = WikiStore(self.store)
        self.memory = AgentMemory(self.store)
        self.skill_manager = AgentSkillManager(self.store, self.memory, wiki=self.wiki)
        self._celery_app = celery_app
        self._mcp_client = mcp_client
        self._notification_mgr = notification_mgr
        self._model_str = _model_string(llm_provider, llm_model)
        self._checkpointer = None

    async def ensure_ready(self) -> None:
        """Tabloyu olustur, identity yoksa varsayilan kaydet."""
        await self.store.ensure_table()
        await self.wiki.ensure_indexes()
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
        tools.extend(create_self_tools(
            self.agent_id, self.store, self.memory, self.skill_manager,
            wiki=self.wiki,
        ))
        tools.extend(create_wiki_tools(self.agent_id, self.wiki))
        tools.extend(create_ocr_tools(self.agent_id, self._celery_app, store=self.store, memory=self.memory))
        tools.extend(create_batch_tools(self.agent_id, pg=self._pg, celery_app=self._celery_app))
        return tools

    def _build_subagent_tools(self) -> list:
        """Quality and OCR strategy tools delegated to subagents."""
        quality_tools = create_quality_tools(self.agent_id, pg=self._pg)
        return quality_tools

    def _build_subagents(self) -> list[dict[str, Any]]:
        quality_tools = create_quality_tools(self.agent_id, pg=self._pg)

        ocr_tools = create_ocr_tools(self.agent_id, self._celery_app, store=self.store, memory=self.memory)
        test_tool = [t for t in ocr_tools if t.name == "test_extraction_on_sample"]

        return [
            {
                "name": "quality-analyst",
                "description": (
                    "Extraction kalitesini analiz et. "
                    "Batch tamamlandiginda veya kullanici istediginde: "
                    "extraction sonuclarini ornekle, celiskileri bul, anomalileri tespit et, rapor uret."
                ),
                "system_prompt": (
                    "Sen bir kalite analisti subagent'isin. "
                    "Extraction sonuclarini analiz et, celiskileri bul, anomalileri tespit et ve rapor uret. "
                    "Sonuclari yapilandirilmis ve okunakli sekilde sun."
                ),
                "tools": quality_tools,
                "model": SUBAGENT_MODEL,
            },
            {
                "name": "ocr-strategy-advisor",
                "description": (
                    "Yeni belge tipi geldiginde veya OCR kalitesi dusuk oldugunda: "
                    "ornek sayfalari analiz et, en uygun OCR modunu oner."
                ),
                "system_prompt": (
                    "Sen bir OCR strateji danismani subagent'isin. "
                    "Belge tiplerini analiz et ve en uygun OCR modunu (native, gemini, hybrid) oner. "
                    "Ornek sayfalari inceleyerek kalite degerlendirmesi yap."
                ),
                "tools": test_tool,
                "model": SUBAGENT_MODEL,
            },
        ]

    # ------------------------------------------------------------------
    # Mode management
    # ------------------------------------------------------------------

    PLAN_ONLY_TOOLS = frozenset({
        "create_plan", "update_plan_step", "add_plan_step",
        "remove_plan_step", "get_current_plan",
        "get_current_ontology", "get_wiki_page", "search_wiki",
        "get_wiki_index", "get_ocr_text", "get_batch_progress",
        "list_pending_discoveries",
    })

    async def get_mode(self) -> str:
        """Return current mode: 'plan' or 'agent'."""
        plan = await self.store.load_plan(self.agent_id)
        if plan and plan.get("status") in ("approved", "executing"):
            return "agent"
        return "plan"

    async def approve_plan(self) -> dict[str, Any]:
        """Approve current plan and switch to agent mode."""
        plan = await self.store.load_plan(self.agent_id)
        if not plan:
            return {"error": "No active plan"}
        await self.store.update_plan_status(self.agent_id, "approved")
        return {"mode": "agent", "status": "approved", "steps": len(plan.get("steps", []))}

    async def switch_to_plan(self) -> dict[str, Any]:
        """Switch back to plan mode."""
        plan = await self.store.load_plan(self.agent_id)
        if plan:
            await self.store.update_plan_status(self.agent_id, "draft")
        return {"mode": "plan"}

    # ------------------------------------------------------------------
    # Agent construction
    # ------------------------------------------------------------------

    async def _build_agent(self, mode: str | None = None):
        """Create a compiled deep agent graph for this session."""
        if mode is None:
            mode = await self.get_mode()

        system_prompt = await self.memory.build_system_prompt(self.agent_id, mode=mode)
        local_tools = self._build_local_tools()

        mcp_tools: list = []
        if self._mcp_client is not None:
            try:
                mcp_tools = self._mcp_client.get_tools()
            except Exception as exc:
                logger.warning("MCP tools unavailable: %s", exc)

        all_tools = [*local_tools, *mcp_tools]

        if mode == "plan":
            all_tools = [t for t in all_tools if t.name in self.PLAN_ONLY_TOOLS]
            subagents = []
        else:
            all_tools = [t for t in all_tools if t.name not in {
                "create_plan", "update_plan_step", "add_plan_step", "remove_plan_step",
            }]
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

        mode = await self.get_mode()
        agent = await self._build_agent(mode=mode)

        yield {"type": "mode", "mode": mode}

        config: dict[str, Any] = {"recursion_limit": 50}
        if self._checkpointer:
            config["configurable"] = {"thread_id": f"{self.agent_id}:{session_id}"}

        full_response = ""
        _last_todos: list | None = None
        try:
            from langgraph.types import Overwrite
            from langchain_core.messages import AIMessage, ToolMessage

            async for event in agent.astream(
                {"messages": [HumanMessage(content=message)]},
                config=config,
            ):
                for node_name, node_output in event.items():
                    if node_output is None:
                        continue

                    raw_todos = node_output.get("todos")
                    if raw_todos is not None:
                        if isinstance(raw_todos, Overwrite):
                            raw_todos = raw_todos.value
                        if isinstance(raw_todos, list) and raw_todos != _last_todos:
                            _last_todos = raw_todos
                            yield {
                                "type": "todo_update",
                                "todos": [
                                    {"content": t.get("content", ""), "status": t.get("status", "pending")}
                                    for t in raw_todos if isinstance(t, dict)
                                ],
                            }

                    raw_messages = node_output.get("messages", [])

                    if isinstance(raw_messages, Overwrite):
                        raw_messages = raw_messages.value
                    if not isinstance(raw_messages, list):
                        raw_messages = [raw_messages] if raw_messages else []

                    for msg in raw_messages:
                        if isinstance(msg, AIMessage):
                            if msg.tool_calls:
                                for tc in msg.tool_calls:
                                    yield {
                                        "type": "tool_call",
                                        "name": tc["name"],
                                        "args": tc["args"],
                                    }
                                    if tc["name"] in ("create_plan", "update_plan_step", "add_plan_step", "remove_plan_step"):
                                        plan = await self.store.load_plan(self.agent_id)
                                        if plan:
                                            yield {"type": "plan_update", "plan": plan}
                            elif msg.content:
                                text = _extract_text(msg.content)
                                if text:
                                    full_response += text
                                    yield {
                                        "type": "message_chunk",
                                        "content": text,
                                    }
                        elif isinstance(msg, ToolMessage):
                            result_preview = _extract_text(msg.content)[:500]
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
            from langgraph.types import Command, Overwrite
            from langchain_core.messages import AIMessage, ToolMessage

            async for event in agent.astream(
                Command(resume=resume_value),
                config={"configurable": {"thread_id": thread_id}},
            ):
                for node_name, node_output in event.items():
                    raw_messages = node_output.get("messages", [])
                    if isinstance(raw_messages, Overwrite):
                        raw_messages = raw_messages.value
                    if not isinstance(raw_messages, list):
                        raw_messages = [raw_messages] if raw_messages else []

                    for msg in raw_messages:
                        if isinstance(msg, AIMessage):
                            if msg.tool_calls:
                                for tc in msg.tool_calls:
                                    yield {"type": "tool_call", "name": tc["name"], "args": tc["args"]}
                            elif msg.content:
                                text = _extract_text(msg.content)
                                if text:
                                    full_response += text
                                    yield {"type": "message_chunk", "content": text}
                        elif isinstance(msg, ToolMessage):
                            yield {"type": "tool_result", "name": msg.name, "result": _extract_text(msg.content)[:500]}
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

    async def get_chat_history(self, session_id: str) -> list[dict[str, Any]]:
        """Checkpointer'dan konusma gecmisini yukle."""
        if not self._checkpointer:
            return []
        try:
            from langchain_core.messages import HumanMessage as HM, AIMessage, ToolMessage

            thread_id = f"{self.agent_id}:{session_id}"
            config = {"configurable": {"thread_id": thread_id}}
            checkpoint = await self._checkpointer.aget(config)
            if not checkpoint or "channel_values" not in checkpoint:
                return []
            raw_messages = checkpoint["channel_values"].get("messages", [])
            result: list[dict[str, Any]] = []
            for msg in raw_messages:
                if isinstance(msg, HM):
                    result.append({"role": "user", "content": _extract_text(msg.content)})
                elif isinstance(msg, AIMessage):
                    if msg.tool_calls:
                        for tc in msg.tool_calls:
                            result.append({"role": "tool_call", "tool_name": tc["name"], "content": str(tc.get("args", {}))})
                    elif msg.content:
                        result.append({"role": "assistant", "content": _extract_text(msg.content)})
                elif isinstance(msg, ToolMessage):
                    result.append({"role": "tool", "tool_name": msg.name, "content": _extract_text(msg.content)[:500]})
            return result
        except Exception as exc:
            logger.warning("Chat history load error: %s", exc)
            return []

    async def list_sessions(self) -> list[dict[str, Any]]:
        """Checkpointer'daki tum session'lari listele."""
        if not self._checkpointer:
            return []
        try:
            prefix = f"{self.agent_id}:"
            sessions: list[dict[str, Any]] = []
            dsn = os.getenv(
                "EVENT_STORE_DSN",
                "postgresql://event_user:event_secret@localhost:5433/event_store",
            )
            import asyncpg
            conn = await asyncpg.connect(dsn)
            try:
                rows = await conn.fetch(
                    """
                    SELECT DISTINCT thread_id
                    FROM checkpoints
                    WHERE thread_id LIKE $1
                    ORDER BY thread_id
                    """,
                    f"{prefix}%",
                )
                for row in rows:
                    tid = row["thread_id"]
                    sid = tid[len(prefix):]
                    sessions.append({"session_id": sid, "thread_id": tid})
            finally:
                await conn.close()
            return sessions
        except Exception as exc:
            logger.warning("Session list error: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    async def _create_checkpointer(self):
        """Create an async PostgreSQL checkpointer for conversation persistence."""
        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
            import psycopg

            dsn = os.getenv(
                "EVENT_STORE_DSN",
                "postgresql://event_user:event_secret@localhost:5433/event_store",
            )
            conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True)
            saver = AsyncPostgresSaver(conn)
            await saver.setup()
            self._checkpointer_conn = conn
            return saver
        except Exception as exc:
            logger.warning("AsyncPostgresSaver not available: %s (conversations will not persist)", exc)
            return None
