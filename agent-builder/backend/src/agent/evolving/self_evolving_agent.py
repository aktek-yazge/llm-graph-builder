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
from langchain_core.messages import HumanMessage, SystemMessage

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


_TEXT_ITEM_TYPES = {"text", "output_text", "input_text"}
_NON_TEXT_ITEM_TYPES = {
    "function_call",
    "tool_use",
    "tool_call",
    "tool_call_chunk",
    "function_call_output",
    "reasoning",
    "reasoning_delta",
    "image",
    "image_url",
    "file",
}


def _extract_text(content: Any) -> str:
    """Normalize msg.content which may be str, list[dict], or other.

    OpenAI Responses API yields AIMessageChunk.content as a list of dicts like:
      {"type": "text", "text": "..."}            -> textual content (include)
      {"type": "function_call", "arguments": "..."} -> tool call delta (skip)
      {"type": "reasoning", ...}                 -> internal chain-of-thought (skip)
    We only concatenate actual textual items and ignore tool/function-call deltas.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                item_type = item.get("type")
                if item_type in _NON_TEXT_ITEM_TYPES:
                    continue
                if item_type and item_type not in _TEXT_ITEM_TYPES:
                    # Unknown non-text structured item: skip to be safe
                    continue
                text_val = item.get("text")
                if isinstance(text_val, str):
                    parts.append(text_val)
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
        "get_current_ontology", "list_resources",
        "get_wiki_page", "search_wiki",
        "get_wiki_index", "get_ocr_text", "get_batch_progress",
        "list_pending_discoveries",
        "read_ocr_pages", "list_ocr_documents",
        "list_extracted_records",
    })

    async def get_mode(self) -> str:
        """Return current mode: 'plan' or 'agent'.

        Default is 'agent'. The system enters plan mode only when there is an
        active draft plan being discussed. Approved/executing/completed plans
        keep the agent in agent mode (since the discussion is over).
        """
        plan = await self.store.load_plan(self.agent_id)
        if plan and plan.get("status") == "draft":
            return "plan"
        return "agent"

    async def approve_plan(self) -> dict[str, Any]:
        """Approve current plan and switch to agent mode."""
        plan = await self.store.load_plan(self.agent_id)
        if not plan:
            return {"error": "No active plan"}
        await self.store.update_plan_status(self.agent_id, "approved")
        return {"mode": "agent", "status": "approved", "steps": len(plan.get("steps", []))}

    async def switch_to_plan(self) -> dict[str, Any]:
        """Switch back to plan mode (keeps existing plan as draft)."""
        plan = await self.store.load_plan(self.agent_id)
        if plan:
            await self.store.update_plan_status(self.agent_id, "draft")
        else:
            # No plan yet — create empty draft so mode flips to plan
            await self.store.save_plan(self.agent_id, [], summary="")
        return {"mode": "plan"}

    async def start_plan_discussion(self, reason: str) -> dict[str, Any]:
        """Open plan mode by creating a fresh draft plan with the given reason.

        Called by the router after the user approves a `request_plan_mode`
        tool call. Any prior plan is replaced with a new empty draft so the
        agent can build it out collaboratively in the next turn.
        """
        await self.store.save_plan(self.agent_id, [], summary=reason)
        return {"mode": "plan", "status": "draft", "summary": reason}

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

        config: dict[str, Any] = {"recursion_limit": 120}
        if self._checkpointer:
            config["configurable"] = {"thread_id": f"{self.agent_id}:{session_id}"}

        last_response = ""
        _last_todos: list | None = None
        _yielded_tool_call_ids: set[str] = set()
        _yielded_tool_result_ids: set[str] = set()

        # Yeni turn icin checkpoint'teki mevcut tool_call ID'lerini "zaten
        # yayinlanmis" olarak isaretle. Aksi halde langgraph "updates" stream
        # kanalinda gecmis turn'lerin tool_call'larini yeniden emit ediyor ve
        # frontend bunlari yeni mesaj gibi goruyor (UX bug).
        if self._checkpointer:
            try:
                snapshot = await self._checkpointer.aget(config)
                if snapshot and "channel_values" in snapshot:
                    for prev_msg in snapshot["channel_values"].get("messages", []) or []:
                        prev_tcs = getattr(prev_msg, "tool_calls", None)
                        if prev_tcs:
                            for tc in prev_tcs:
                                tc_id = tc.get("id") or f"{tc.get('name')}:{id(tc)}"
                                _yielded_tool_call_ids.add(tc_id)
                        prev_tc_id = getattr(prev_msg, "tool_call_id", None)
                        if prev_tc_id:
                            _yielded_tool_result_ids.add(prev_tc_id)
            except Exception as exc:
                logger.debug("Could not snapshot existing tool calls: %s", exc)

        PLAN_MUTATING_TOOLS = frozenset({
            "create_plan", "update_plan_step", "add_plan_step", "remove_plan_step",
        })

        try:
            from langgraph.types import Overwrite
            from langchain_core.messages import AIMessageChunk, ToolMessage

            async for mode_name, payload in agent.astream(
                {"messages": [HumanMessage(content=message)]},
                config=config,
                stream_mode=["updates", "messages"],
            ):
                if mode_name == "messages":
                    # payload = (message_chunk, metadata)
                    chunk, _meta = payload
                    if isinstance(chunk, AIMessageChunk):
                        if chunk.content:
                            text = _extract_text(chunk.content)
                            if text:
                                last_response += text
                                yield {"type": "message_chunk", "content": text}
                        # tool_call delta'lari "updates" kanalinda bitmis halde gelecek
                    elif isinstance(chunk, ToolMessage):
                        tc_id = getattr(chunk, "tool_call_id", None)
                        if tc_id and tc_id in _yielded_tool_result_ids:
                            continue
                        if tc_id:
                            _yielded_tool_result_ids.add(tc_id)
                        yield {
                            "type": "tool_result",
                            "name": chunk.name,
                            "result": _extract_text(chunk.content)[:500],
                        }
                    continue

                # mode_name == "updates"
                if not isinstance(payload, dict):
                    continue
                for node_output in payload.values():
                    if not isinstance(node_output, dict):
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
                        tool_calls = getattr(msg, "tool_calls", None)
                        if not tool_calls:
                            continue
                        for tc in tool_calls:
                            tc_id = tc.get("id") or f"{tc.get('name')}:{id(tc)}"
                            if tc_id in _yielded_tool_call_ids:
                                continue
                            _yielded_tool_call_ids.add(tc_id)
                            yield {
                                "type": "tool_call",
                                "name": tc["name"],
                                "args": tc.get("args", {}),
                            }
                            if tc["name"] in PLAN_MUTATING_TOOLS:
                                plan = await self.store.load_plan(self.agent_id)
                                if plan:
                                    yield {"type": "plan_update", "plan": plan}
        except Exception as e:
            logger.error("Agent chat error: %s", e, exc_info=True)
            err_text = str(e)
            if "GRAPH_RECURSION_LIMIT" in err_text or "Recursion limit" in err_text:
                friendly = (
                    "Tool dongusune girdim ve adim limitine takildim (recursion). "
                    "Genelde sebep: gereginden fazla ardisik tool cagrisi (orn. tum sayfalari "
                    "tek tek okumak veya ontoloji islemini hemen yapmaya calismak).\n\n"
                    "Oneriyorum: 'Tekrar Sor' butonuyla mesajini sifirla; ardindan ne istedigini "
                    "tek cumlede daha sinirli yaz (orn. 'sadece ilk 2 sayfayi ozetle' veya 'wiki'ye "
                    "aday entity tipleri yaz, ontoloji ekleme')."
                )
                yield {"type": "error", "content": friendly}
            else:
                yield {"type": "error", "content": err_text}

        yield {
            "type": "final_response",
            "content": "",
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
        resume_config = {"configurable": {"thread_id": thread_id}}

        resume_value = decision == "approve"

        _yielded_tool_call_ids: set[str] = set()
        _yielded_tool_result_ids: set[str] = set()

        # Resume oncesi mevcut tool_call'lari "yayinlanmis" olarak isaretle.
        try:
            snapshot = await self._checkpointer.aget(resume_config)
            if snapshot and "channel_values" in snapshot:
                for prev_msg in snapshot["channel_values"].get("messages", []) or []:
                    prev_tcs = getattr(prev_msg, "tool_calls", None)
                    if prev_tcs:
                        for tc in prev_tcs:
                            tc_id = tc.get("id") or f"{tc.get('name')}:{id(tc)}"
                            _yielded_tool_call_ids.add(tc_id)
                    prev_tc_id = getattr(prev_msg, "tool_call_id", None)
                    if prev_tc_id:
                        _yielded_tool_result_ids.add(prev_tc_id)
        except Exception as exc:
            logger.debug("Resume snapshot error: %s", exc)

        try:
            from langgraph.types import Command, Overwrite
            from langchain_core.messages import AIMessageChunk, ToolMessage

            async for mode_name, payload in agent.astream(
                Command(resume=resume_value),
                config=resume_config,
                stream_mode=["updates", "messages"],
            ):
                if mode_name == "messages":
                    chunk, _meta = payload
                    if isinstance(chunk, AIMessageChunk):
                        if chunk.content:
                            text = _extract_text(chunk.content)
                            if text:
                                yield {"type": "message_chunk", "content": text}
                    elif isinstance(chunk, ToolMessage):
                        tc_id = getattr(chunk, "tool_call_id", None)
                        if tc_id and tc_id in _yielded_tool_result_ids:
                            continue
                        if tc_id:
                            _yielded_tool_result_ids.add(tc_id)
                        yield {
                            "type": "tool_result",
                            "name": chunk.name,
                            "result": _extract_text(chunk.content)[:500],
                        }
                    continue

                if not isinstance(payload, dict):
                    continue
                for node_output in payload.values():
                    if not isinstance(node_output, dict):
                        continue
                    raw_messages = node_output.get("messages", [])
                    if isinstance(raw_messages, Overwrite):
                        raw_messages = raw_messages.value
                    if not isinstance(raw_messages, list):
                        raw_messages = [raw_messages] if raw_messages else []
                    for msg in raw_messages:
                        tool_calls = getattr(msg, "tool_calls", None)
                        if not tool_calls:
                            continue
                        for tc in tool_calls:
                            tc_id = tc.get("id") or f"{tc.get('name')}:{id(tc)}"
                            if tc_id in _yielded_tool_call_ids:
                                continue
                            _yielded_tool_call_ids.add(tc_id)
                            yield {
                                "type": "tool_call",
                                "name": tc["name"],
                                "args": tc.get("args", {}),
                            }
        except Exception as e:
            logger.error("Agent resume error: %s", e, exc_info=True)
            yield {"type": "error", "content": str(e)}

        yield {"type": "final_response", "content": "", "session_id": session_id}

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    async def chat_sync(self, message: str, session_id: str = "") -> str:
        """Senkron convenience wrapper (test icin)."""
        result = ""
        async for chunk in self.chat(message, session_id):
            if chunk.get("type") == "message_chunk":
                result += chunk.get("content") or ""
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

    async def inject_system_event(
        self,
        session_id: str,
        event_text: str,
        event_data: dict[str, Any] | None = None,
        notify: bool = True,
    ) -> dict[str, Any]:
        """Inject an external system event as a new turn in the agent's
        conversation thread and stream the assistant response over SSE.

        Use case: a Celery batch finishes hours later — the webhook calls this
        method so the agent generates a short user-facing notification, that
        message is persisted in the LangGraph checkpointer, and any open chat
        UI receives it live via the existing SSE notification channel.

        Args:
            session_id: target chat session id
            event_text: natural-language description of the system event
            event_data: optional structured payload included in SSE notifications
            notify: if True, NotificationManager fan-out events while streaming
                (``chat_message_chunk``, ``chat_message_injected``)

        Returns:
            ``{"status": "ok", "session_id": ..., "response": "...",
                "tool_calls": [...], "duration": float}`` on success,
            ``{"status": "no_checkpointer" | "error", ...}`` otherwise.
        """
        if not self._checkpointer:
            return {"status": "no_checkpointer"}

        import time
        start = time.time()

        thread_id = f"{self.agent_id}:{session_id}"
        config: dict[str, Any] = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": 30,
        }

        agent = await self._build_agent()

        system_msg = SystemMessage(
            content=(
                f"[SYSTEM EVENT] {event_text}\n\n"
                "Bu mesaj kullanicidan gelmedi; arka planda calisan bir "
                "background task (orn. uzun bir batch isleme) tamamlandiginda "
                "otomatik enjekte edildi.\n\n"
                "Gorevin: kullaniciya kisa, ozet bir bilgilendirme mesaji yaz "
                "(1-3 cumle). YENI tool zinciri BASLATMA. Ek inceleme "
                "gerekiyorsa kullanicinin onay vermesini bekle ('isterseniz "
                "ayrintilara bakabilirim' gibi). Sadece olayi anlat ve sonraki "
                "adimi sor."
            )
        )

        existing_tool_call_ids: set[str] = set()
        existing_tool_result_ids: set[str] = set()
        try:
            snapshot = await self._checkpointer.aget(config)
            if snapshot and "channel_values" in snapshot:
                for prev_msg in snapshot["channel_values"].get("messages", []) or []:
                    prev_tcs = getattr(prev_msg, "tool_calls", None)
                    if prev_tcs:
                        for tc in prev_tcs:
                            tc_id = tc.get("id") or f"{tc.get('name')}:{id(tc)}"
                            existing_tool_call_ids.add(tc_id)
                    prev_tc_id = getattr(prev_msg, "tool_call_id", None)
                    if prev_tc_id:
                        existing_tool_result_ids.add(prev_tc_id)
        except Exception as exc:
            logger.debug("inject_system_event snapshot error: %s", exc)

        async def _maybe_notify(event_type: str, data: dict[str, Any]) -> None:
            if not notify or not self._notification_mgr:
                return
            try:
                await self._notification_mgr.notify(
                    agent_id=self.agent_id, event_type=event_type, data=data,
                )
            except Exception as exc:
                logger.debug("notify %s skipped: %s", event_type, exc)

        await _maybe_notify(
            "chat_message_injected_start",
            {
                "session_id": session_id,
                "event_text": event_text,
                "event_data": event_data or {},
            },
        )

        last_response = ""
        tool_call_log: list[dict[str, Any]] = []
        try:
            from langchain_core.messages import AIMessageChunk, ToolMessage
            from langgraph.types import Overwrite

            async for mode_name, payload in agent.astream(
                {"messages": [system_msg]},
                config=config,
                stream_mode=["updates", "messages"],
            ):
                if mode_name == "messages":
                    chunk, _meta = payload
                    if isinstance(chunk, AIMessageChunk):
                        if chunk.content:
                            text = _extract_text(chunk.content)
                            if text:
                                last_response += text
                                await _maybe_notify(
                                    "chat_message_chunk",
                                    {
                                        "session_id": session_id,
                                        "content": text,
                                        "source": "system_event",
                                    },
                                )
                    elif isinstance(chunk, ToolMessage):
                        tc_id = getattr(chunk, "tool_call_id", None)
                        if tc_id and tc_id in existing_tool_result_ids:
                            continue
                        if tc_id:
                            existing_tool_result_ids.add(tc_id)
                        await _maybe_notify(
                            "tool_result",
                            {
                                "session_id": session_id,
                                "name": chunk.name,
                                "result": _extract_text(chunk.content)[:500],
                                "source": "system_event",
                            },
                        )
                    continue

                if not isinstance(payload, dict):
                    continue
                for node_output in payload.values():
                    if not isinstance(node_output, dict):
                        continue
                    raw_messages = node_output.get("messages", [])
                    if isinstance(raw_messages, Overwrite):
                        raw_messages = raw_messages.value
                    if not isinstance(raw_messages, list):
                        raw_messages = [raw_messages] if raw_messages else []
                    for msg in raw_messages:
                        tool_calls = getattr(msg, "tool_calls", None)
                        if not tool_calls:
                            continue
                        for tc in tool_calls:
                            tc_id = tc.get("id") or f"{tc.get('name')}:{id(tc)}"
                            if tc_id in existing_tool_call_ids:
                                continue
                            existing_tool_call_ids.add(tc_id)
                            tc_entry = {
                                "name": tc["name"],
                                "args": tc.get("args", {}),
                            }
                            tool_call_log.append(tc_entry)
                            await _maybe_notify(
                                "tool_call",
                                {
                                    "session_id": session_id,
                                    "name": tc["name"],
                                    "args": tc.get("args", {}),
                                    "source": "system_event",
                                },
                            )

            await _maybe_notify(
                "chat_message_injected",
                {
                    "session_id": session_id,
                    "content": last_response,
                    "event_text": event_text,
                    "event_data": event_data or {},
                    "tool_calls": tool_call_log,
                },
            )
            return {
                "status": "ok",
                "session_id": session_id,
                "response": last_response,
                "tool_calls": tool_call_log,
                "duration": round(time.time() - start, 2),
            }
        except Exception as exc:
            logger.error("inject_system_event failed: %s", exc, exc_info=True)
            await _maybe_notify(
                "chat_message_injected_error",
                {"session_id": session_id, "error": str(exc)},
            )
            return {"status": "error", "error": str(exc)}

    async def rewind_to_user_message(
        self,
        session_id: str,
        user_message_index: int,
    ) -> dict[str, Any]:
        """Konusmayi N'inci kullanici mesajinin ONCESINE geri al ve agent'in
        bu mesajdan SONRA yaptigi yan etkileri (extracted_records .md dosyalari +
        kayitlari, ontoloji versiyonlari) sil.

        Args:
            session_id: chat session id
            user_message_index: 0-based — silinecek (geri alinacak) ilk user mesajinin
                indeksi. Konusma bu indeksten oncesini koruyacak; bu indeks ve sonrasi silinir.

        Returns metadata: kept_user_messages, deleted_checkpoints, deleted_records,
            deleted_files, ontology_versions_deleted, cutoff_ts.
        """
        if not self._checkpointer:
            return {"status": "no_checkpointer"}

        thread_id = f"{self.agent_id}:{session_id}"
        config = {"configurable": {"thread_id": thread_id}}

        from langchain_core.messages import HumanMessage as HM

        history: list = []
        try:
            async for ct in self._checkpointer.alist(config):
                history.append(ct)
        except Exception as exc:
            logger.warning("checkpoint alist error: %s", exc)
            return {"status": "error", "error": str(exc)}

        history.reverse()  # oldest -> newest

        target_idx = -1
        for i, ct in enumerate(history):
            msgs = (
                getattr(ct, "checkpoint", {}).get("channel_values", {}).get("messages", [])
                if hasattr(ct, "checkpoint") and isinstance(ct.checkpoint, dict)
                else []
            )
            user_count = sum(1 for m in msgs if isinstance(m, HM))
            if user_count <= user_message_index:
                target_idx = i
            else:
                break

        if target_idx == -1 or target_idx >= len(history):
            return {
                "status": "not_found",
                "message": f"{user_message_index}. user mesajinin oncesine ait checkpoint bulunamadi",
            }

        target_ct = history[target_idx]
        target_checkpoint_id = (
            target_ct.config.get("configurable", {}).get("checkpoint_id")
            if hasattr(target_ct, "config") and isinstance(target_ct.config, dict)
            else None
        )
        if not target_checkpoint_id:
            return {"status": "error", "message": "target checkpoint_id okunamadi"}

        cutoff_ts = None
        if target_idx + 1 < len(history):
            next_ct = history[target_idx + 1]
            cutoff_ts_str = (
                getattr(next_ct, "checkpoint", {}).get("ts")
                if hasattr(next_ct, "checkpoint") and isinstance(next_ct.checkpoint, dict)
                else None
            )
            if cutoff_ts_str:
                try:
                    from datetime import datetime
                    cutoff_ts = datetime.fromisoformat(
                        cutoff_ts_str.replace("Z", "+00:00")
                    )
                except (ValueError, AttributeError):
                    cutoff_ts = None

        import asyncpg
        dsn = os.getenv(
            "EVENT_STORE_DSN",
            "postgresql://event_user:event_secret@localhost:5433/event_store",
        )

        deleted_records = 0
        deleted_files = 0
        deleted_md_paths: list[str] = []
        ontology_versions_deleted = 0

        try:
            conn = await asyncpg.connect(dsn)
        except Exception as exc:
            logger.warning("rewind asyncpg connect failed: %s", exc)
            return {"status": "error", "error": f"db connect: {exc}"}

        try:
            await conn.execute(
                "DELETE FROM checkpoints WHERE thread_id = $1 AND checkpoint_id > $2",
                thread_id, target_checkpoint_id,
            )
            await conn.execute(
                "DELETE FROM checkpoint_writes WHERE thread_id = $1 AND checkpoint_id > $2",
                thread_id, target_checkpoint_id,
            )

            if cutoff_ts is not None:
                cutoff_naive = cutoff_ts.replace(tzinfo=None) if cutoff_ts.tzinfo else cutoff_ts

                rows = await conn.fetch(
                    """SELECT id, value FROM agent_knowledge
                       WHERE agent_id = $1 AND knowledge_type = 'extracted_records'
                       AND created_at >= $2""",
                    self.agent_id, cutoff_naive,
                )
                for row in rows:
                    try:
                        val = row["value"]
                        if isinstance(val, str):
                            val = json.loads(val)
                        fp = val.get("file_path") if isinstance(val, dict) else None
                        if fp:
                            from pathlib import Path
                            try:
                                Path(fp).unlink(missing_ok=True)
                                deleted_md_paths.append(fp)
                                deleted_files += 1
                            except OSError:
                                pass
                    except (json.JSONDecodeError, ValueError, TypeError):
                        continue

                rec_res = await conn.execute(
                    """DELETE FROM agent_knowledge
                       WHERE agent_id = $1 AND knowledge_type = 'extracted_records'
                       AND created_at >= $2""",
                    self.agent_id, cutoff_naive,
                )
                try:
                    deleted_records = int(rec_res.split()[-1])
                except (ValueError, IndexError):
                    deleted_records = 0

                onto_res = await conn.execute(
                    """DELETE FROM agent_knowledge
                       WHERE agent_id = $1 AND knowledge_type = 'ontology'
                       AND created_at >= $2""",
                    self.agent_id, cutoff_naive,
                )
                try:
                    ontology_versions_deleted = int(onto_res.split()[-1])
                except (ValueError, IndexError):
                    ontology_versions_deleted = 0
        finally:
            await conn.close()

        return {
            "status": "ok",
            "session_id": session_id,
            "kept_user_messages": user_message_index,
            "target_checkpoint_id": target_checkpoint_id,
            "cutoff_ts": cutoff_ts.isoformat() if cutoff_ts else None,
            "deleted_records": deleted_records,
            "deleted_files": deleted_files,
            "deleted_md_paths": deleted_md_paths,
            "ontology_versions_deleted": ontology_versions_deleted,
        }

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
