"""
Builder Agent
=============

LLM-driven agent olusturma motoru.
Statik state machine yerine LangGraph tool-calling agent kullanir.

react_agent.py pattern'ini temel alir:
- Configurable LLM (Gemini, OpenAI, Anthropic)
- Tool-calling ile Ontology DB ve MCP Gateway etkilesimi
- Session bazli conversation memory (Neo4j)
- SSE streaming response

Kullanim:
    agent = await create_builder_agent(db, tenant_id="tenant-A")
    async for chunk in agent.stream("Sigorta belgeleri islemek istiyorum"):
        print(chunk)
"""

import json
import logging
import os
import uuid
from datetime import datetime
from typing import Any, AsyncIterator, Dict, List, Optional

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.prebuilt import create_react_agent

from .prompts import get_builder_system_prompt
from .tools import create_builder_tools
from ..ontology.neo4j_client import OntologyDBClient
from ..ontology.reasoner import OntologyReasoner
from ..skills.skill_registry import SkillRegistry
from ..gateway.mcp_gateway_client import MCPGatewayClient, get_gateway_client
from ..gateway.virtual_server import VirtualServerManager
from ..models import BuilderChatResponse, SessionState
from ..mutation_gateway.gateway import MutationGateway

logger = logging.getLogger(__name__)

LLM_PROVIDER = os.getenv("BUILDER_LLM_PROVIDER", "google")
LLM_MODEL = os.getenv("BUILDER_LLM_MODEL", "gemini-2.5-flash")
LLM_TEMPERATURE = float(os.getenv("BUILDER_LLM_TEMPERATURE", "0.3"))


def _create_llm(provider: str = LLM_PROVIDER, model: str = LLM_MODEL):
    """Configurable LLM instance olustur."""
    provider = provider.lower()

    if provider in ("google", "gemini"):
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model=model,
            temperature=LLM_TEMPERATURE,
        )
    elif provider in ("openai", "gpt"):
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model,
            temperature=LLM_TEMPERATURE,
        )
    elif provider in ("anthropic", "claude"):
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=model,
            temperature=LLM_TEMPERATURE,
            max_tokens=8192,
        )
    else:
        raise ValueError(f"Unsupported LLM provider: {provider}")


class BuilderAgent:
    """
    LLM-driven Agent Builder.

    Kullanici ile sohbet ederek agent olusturma surecini yonetir.
    LangGraph create_react_agent ile tool-calling destekler.
    """

    def __init__(
        self,
        db: OntologyDBClient,
        tenant_id: str,
        gateway: MCPGatewayClient,
        llm_provider: str = LLM_PROVIDER,
        llm_model: str = LLM_MODEL,
        mutation_gateway: Optional[MutationGateway] = None,
    ):
        self.db = db
        self.tenant_id = tenant_id
        self.gateway = gateway
        self.llm_provider = llm_provider
        self.llm_model = llm_model
        self.mutation_gateway = mutation_gateway

        self._reasoner = OntologyReasoner(db)
        self._skill_registry = SkillRegistry(db)
        self._vs_manager = VirtualServerManager(gateway, db)

        self._llm = _create_llm(llm_provider, llm_model)
        self._tools = create_builder_tools(
            db=db,
            reasoner=self._reasoner,
            skill_registry=self._skill_registry,
            gateway=gateway,
            vs_manager=self._vs_manager,
            tenant_id=tenant_id,
            mutation_gateway=mutation_gateway,
        )
        self._agent = create_react_agent(
            model=self._llm,
            tools=self._tools,
        )

    # =========================================================================
    # SESSION MANAGEMENT
    # =========================================================================

    async def create_session(self, user_id: Optional[str] = None) -> str:
        """Yeni builder session olustur, session_id dondur."""
        session_id = f"session-{uuid.uuid4().hex[:12]}"

        await self.db.execute_query("""
            CREATE (bs:BuilderSession {
                id: $session_id,
                tenant_id: $tenant_id,
                user_id: $user_id,
                status: 'active',
                current_state: 'conversation',
                state_data: '{}',
                messages: '[]',
                created_at: datetime(),
                updated_at: datetime()
            })
            RETURN bs.id AS id
        """, {
            "session_id": session_id,
            "tenant_id": self.tenant_id,
            "user_id": user_id,
        }, write=True)

        logger.info("Created builder session: %s", session_id)
        return session_id

    async def _load_history(self, session_id: str) -> List[Any]:
        """Session'dan conversation history yukle."""
        result = await self.db.execute_query("""
            MATCH (bs:BuilderSession {id: $session_id, tenant_id: $tenant_id})
            RETURN bs.messages AS messages
        """, {"session_id": session_id, "tenant_id": self.tenant_id})

        if not result:
            return []

        raw = result[0].get("messages", "[]")
        try:
            messages_data = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            return []

        messages = []
        for m in messages_data:
            role = m.get("role", "")
            content = m.get("content", "")
            if role == "human":
                messages.append(HumanMessage(content=content))
            elif role == "ai":
                messages.append(AIMessage(content=content))

        return messages

    async def _save_message(
        self,
        session_id: str,
        role: str,
        content: str,
    ) -> None:
        """Mesaji session history'sine ekle."""
        result = await self.db.execute_query("""
            MATCH (bs:BuilderSession {id: $session_id})
            RETURN bs.messages AS messages
        """, {"session_id": session_id})

        existing = []
        if result:
            raw = result[0].get("messages", "[]")
            try:
                existing = json.loads(raw) if isinstance(raw, str) else raw
            except (json.JSONDecodeError, TypeError):
                existing = []

        existing.append({
            "role": role,
            "content": content,
            "timestamp": datetime.utcnow().isoformat(),
        })

        await self.db.execute_query("""
            MATCH (bs:BuilderSession {id: $session_id})
            SET bs.messages = $messages,
                bs.updated_at = datetime()
        """, {
            "session_id": session_id,
            "messages": json.dumps(existing, ensure_ascii=False),
        }, write=True)

    # =========================================================================
    # SYSTEM PROMPT
    # =========================================================================

    async def _build_system_prompt(self) -> str:
        """Dinamik system prompt olustur."""
        contexts_result = await self.db.execute_query("""
            MATCH (c:Context)
            RETURN c.name AS name, c.description AS description
            ORDER BY c.name
        """)
        contexts_str = "\n".join([
            f"- {r['name']}: {r['description']}" for r in contexts_result
        ]) if contexts_result else ""

        skills_result = await self.db.execute_query("""
            MATCH (s:Skill)
            WHERE s.is_global = true OR s.tenant_id = $tenant_id
            RETURN s.name AS name, s.skill_category AS category, s.description AS description
            LIMIT 20
        """, {"tenant_id": self.tenant_id})
        skills_str = "\n".join([
            f"- {r['name']} ({r['category']}): {r['description'][:80]}"
            for r in skills_result
        ]) if skills_result else ""

        return get_builder_system_prompt(
            existing_contexts=contexts_str,
            existing_skills_summary=skills_str,
        )

    # =========================================================================
    # PROCESS MESSAGE (non-streaming)
    # =========================================================================

    async def process_message(
        self,
        session_id: str,
        user_message: str,
    ) -> BuilderChatResponse:
        """
        Kullanici mesajini isle ve yanitla.
        Non-streaming versiyon -- service.py entegrasyonu icin.
        """
        history = await self._load_history(session_id)
        system_prompt = await self._build_system_prompt()

        await self._save_message(session_id, "human", user_message)

        input_messages = [SystemMessage(content=system_prompt)]
        input_messages.extend(history)
        input_messages.append(HumanMessage(content=user_message))

        result = await self._agent.ainvoke(
            {"messages": input_messages},
        )

        final_messages = result.get("messages", [])
        ai_response = ""
        for msg in reversed(final_messages):
            if isinstance(msg, AIMessage) and msg.content and not msg.tool_calls:
                ai_response = msg.content
                break

        if not ai_response:
            ai_response = "Islem tamamlandi."

        await self._save_message(session_id, "ai", ai_response)

        return BuilderChatResponse(
            message=ai_response,
            state=SessionState.GOAL_ELICITATION,
        )

    # =========================================================================
    # STREAM (SSE)
    # =========================================================================

    async def stream(
        self,
        session_id: str,
        user_message: str,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        Kullanici mesajini isle ve streaming yanit dondur.
        SSE endpoint icin kullanilir.

        Yields:
            {"type": "message_chunk", "content": "..."}
            {"type": "tool_call", "name": "...", "args": {...}}
            {"type": "tool_result", "name": "...", "result": "..."}
            {"type": "final_response", "content": "..."}
        """
        history = await self._load_history(session_id)
        system_prompt = await self._build_system_prompt()

        await self._save_message(session_id, "human", user_message)

        input_messages = [SystemMessage(content=system_prompt)]
        input_messages.extend(history)
        input_messages.append(HumanMessage(content=user_message))

        full_response = ""

        async for chunk in self._agent.astream(
            {"messages": input_messages},
            stream_mode="updates",
        ):
            for node_name, node_output in chunk.items():
                messages_out = node_output.get("messages", [])
                for message in messages_out:
                    if isinstance(message, AIMessage):
                        if message.tool_calls:
                            for tc in message.tool_calls:
                                yield {
                                    "type": "tool_call",
                                    "name": tc["name"],
                                    "args": tc["args"],
                                }
                        elif message.content:
                            full_response += message.content
                            yield {
                                "type": "message_chunk",
                                "content": message.content,
                            }
                    elif isinstance(message, ToolMessage):
                        tool_preview = message.content[:200] if message.content else ""
                        yield {
                            "type": "tool_result",
                            "name": message.name or "unknown",
                            "result": tool_preview,
                        }

        if full_response:
            await self._save_message(session_id, "ai", full_response)
            yield {
                "type": "final_response",
                "content": full_response,
            }


# =============================================================================
# FACTORY
# =============================================================================

_builder_agents: Dict[str, BuilderAgent] = {}


async def create_builder_agent(
    db: OntologyDBClient,
    tenant_id: str,
    gateway: Optional[MCPGatewayClient] = None,
    mutation_gateway: Optional[MutationGateway] = None,
) -> BuilderAgent:
    """BuilderAgent olustur veya cache'den getir."""
    cache_key = f"{tenant_id}:{LLM_PROVIDER}:{LLM_MODEL}"

    if cache_key in _builder_agents:
        return _builder_agents[cache_key]

    if gateway is None:
        gateway = await get_gateway_client()

    # Try to create mutation gateway if not provided
    if mutation_gateway is None:
        try:
            from ..event_store import get_postgres_client, EventStore
            pg = await get_postgres_client()
            event_store = EventStore(pg)
            mutation_gateway = MutationGateway(
                db=db,
                event_store=event_store,
                tenant_id=tenant_id,
                llm_model=LLM_MODEL,
            )
            logger.info("MutationGateway enabled for tenant %s", tenant_id)
        except Exception as e:
            logger.warning("MutationGateway unavailable: %s (writes won't be event-sourced)", e)

    agent = BuilderAgent(
        db=db,
        tenant_id=tenant_id,
        gateway=gateway,
        mutation_gateway=mutation_gateway,
    )
    _builder_agents[cache_key] = agent
    logger.info("Created BuilderAgent for tenant %s (%s/%s)", tenant_id, LLM_PROVIDER, LLM_MODEL)
    return agent
