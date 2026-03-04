"""
Agent Runtime Engine
====================

Agent Builder ile olusturulan agent'larin calisma zamani motoru.

Her agent:
- Ontology DB'den yuklenir (skill, schema, goal bilgisi)
- Dinamik system prompt ile baslatilir
- MCP Gateway virtual server uzerinden tool'lara erisir
- Blackboard pattern ile konu bazli bilgi paylasar
- Knowledge DB'ye yazmadan once onay ister

Kullanim:
    runtime = AgentRuntime(db, gateway)
    agent_session = await runtime.start_agent("agent-123", "tenant-A")
    async for chunk in runtime.chat(agent_session, "Policeleri analiz et"):
        print(chunk)
"""

import json
import logging
import os
import uuid
from datetime import datetime
from typing import Any, AsyncIterator, Dict, List, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from .prompts import get_runtime_system_prompt
from .ocr_bridge import OCRBridge
from .event_bus import AgentEventBus, EventTypes
from ..ontology.neo4j_client import OntologyDBClient
from ..gateway.mcp_gateway_client import MCPGatewayClient
from ..gateway.mcp_services_client import call_mcp_tool
from ..chat_repository import ChatRepository
from ..agent_repository import AgentRepository

logger = logging.getLogger(__name__)

LLM_PROVIDER = os.getenv("RUNTIME_LLM_PROVIDER", os.getenv("BUILDER_LLM_PROVIDER", "google"))
LLM_MODEL = os.getenv("RUNTIME_LLM_MODEL", os.getenv("BUILDER_LLM_MODEL", "gemini-2.5-flash"))


def _create_runtime_llm(provider: str = LLM_PROVIDER, model: str = LLM_MODEL):
    """Runtime agent icin LLM olustur."""
    provider = provider.lower()
    if provider in ("google", "gemini"):
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model=model, temperature=0.2)
    elif provider in ("openai", "gpt"):
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=model, temperature=0.2)
    elif provider in ("anthropic", "claude"):
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=model, temperature=0.2, max_tokens=8192)
    else:
        raise ValueError(f"Unsupported LLM provider: {provider}")


class AgentSession:
    """Calisma zamaninda bir agent oturumu."""

    def __init__(
        self,
        session_id: str,
        agent_id: str,
        tenant_id: str,
        agent_definition: Dict[str, Any],
        skills: List[Dict[str, Any]],
        entity_schemas: List[Dict[str, Any]],
        relationship_schemas: List[Dict[str, Any]],
        workspace_id: str = "",
        resource_id: str = "",
    ):
        self.session_id = session_id
        self.agent_id = agent_id
        self.tenant_id = tenant_id
        self.agent_definition = agent_definition
        self.skills = skills
        self.entity_schemas = entity_schemas
        self.relationship_schemas = relationship_schemas
        self.workspace_id = workspace_id
        self.resource_id = resource_id
        self.messages: List[Any] = []
        self.blackboard: Dict[str, Any] = {}
        self.pending_commits: List[Dict[str, Any]] = []
        self.created_at = datetime.utcnow()


class AgentRuntime:
    """
    Olusturulan agent'larin calisma zamani motoru.

    Agent'i Ontology DB'den yukler, tool'larini olusturur,
    Context Forge Gateway uzerinden MCP tool'larina erisir.

    Runtime tool cagrilari fastmcp.Client ile gateway'in virtual server
    MCP endpoint'ine gider. MCPGatewayClient sadece virtual server yonetimi icin kullanilir.
    """

    def __init__(
        self,
        db: OntologyDBClient,
        gateway: Optional[MCPGatewayClient] = None,
        ocr_bridge: Optional[OCRBridge] = None,
        chat_repo: Optional[ChatRepository] = None,
        agent_repo: Optional[AgentRepository] = None,
        event_bus: Optional[AgentEventBus] = None,
    ):
        self.db = db
        self.gateway = gateway
        self.ocr_bridge = ocr_bridge or OCRBridge()
        self.chat_repo = chat_repo
        self.agent_repo = agent_repo
        self.event_bus = event_bus or AgentEventBus.get_instance()
        self._sessions: Dict[str, AgentSession] = {}

    # =========================================================================
    # AGENT LOADING
    # =========================================================================

    async def _ensure_repos(self):
        """Lazy-init repositories from global PostgresClient."""
        if self.chat_repo is None or self.agent_repo is None:
            from ..event_store.postgres_client import get_postgres_client
            pg = await get_postgres_client()
            if self.chat_repo is None:
                self.chat_repo = ChatRepository(pg)
            if self.agent_repo is None:
                self.agent_repo = AgentRepository(pg)

    async def _load_agent_definition(self, agent_id: str, tenant_id: str) -> Optional[Dict]:
        await self._ensure_repos()
        return await self.agent_repo.get_agent(agent_id, tenant_id)

    async def _load_agent_skills_full(self, agent_id: str) -> tuple:
        await self._ensure_repos()
        return await self.agent_repo.get_agent_skills_full(agent_id)

    # =========================================================================
    # SESSION MANAGEMENT
    # =========================================================================

    async def start_agent(
        self,
        agent_id: str,
        tenant_id: str = "",
        session_id: Optional[str] = None,
        workspace_id: str = "",
        resource_id: str = "",
    ) -> AgentSession:
        """Agent'i yukle ve oturum baslat.

        session_id verilirse mevcut oturumu Neo4j'den yukler (persistent chat).
        Verilmezse yeni oturum olusturur.
        """
        if session_id and session_id in self._sessions:
            return self._sessions[session_id]

        if not tenant_id:
            await self._ensure_repos()
            tenant_id = await self.agent_repo.get_agent_tenant(agent_id)

        agent_def = await self._load_agent_definition(agent_id, tenant_id)
        if not agent_def:
            raise ValueError(f"Agent not found: {agent_id}")

        skills, entity_schemas, rel_schemas = await self._load_agent_skills_full(agent_id)

        if session_id:
            session = await self._load_session(session_id, agent_id, tenant_id,
                                               agent_def, skills, entity_schemas, rel_schemas,
                                               workspace_id=workspace_id, resource_id=resource_id)
            if session:
                self._sessions[session_id] = session
                return session

        new_session_id = session_id or f"runtime-{uuid.uuid4().hex[:12]}"
        session = AgentSession(
            session_id=new_session_id,
            agent_id=agent_id,
            tenant_id=tenant_id,
            agent_definition=agent_def,
            skills=skills,
            entity_schemas=entity_schemas,
            relationship_schemas=rel_schemas,
            workspace_id=workspace_id,
            resource_id=resource_id,
        )
        self._sessions[new_session_id] = session

        await self._save_session_meta(session)
        logger.info("Started agent session %s for agent %s", new_session_id, agent_id)
        return session

    def get_session(self, session_id: str) -> Optional[AgentSession]:
        return self._sessions.get(session_id)

    # =========================================================================
    # SESSION PERSISTENCE (PostgreSQL)
    # =========================================================================

    async def _save_session_meta(self, session: AgentSession) -> None:
        """Persist session metadata to PostgreSQL."""
        try:
            await self._ensure_repos()
            await self.chat_repo.create_session(
                session_id=session.session_id,
                agent_id=session.agent_id,
                tenant_id=session.tenant_id,
                workspace_id=session.workspace_id,
            )
        except Exception as e:
            logger.warning("Failed to persist session meta: %s", e)

    async def _save_message(self, session_id: str, role: str, content: str) -> None:
        """Append a chat message to PostgreSQL for persistence."""
        try:
            await self._ensure_repos()
            await self.chat_repo.add_message(session_id, role, content)
        except Exception as e:
            logger.warning("Failed to persist message: %s", e)

    async def _load_session(
        self, session_id: str, agent_id: str, tenant_id: str,
        agent_def: Dict, skills: List, entity_schemas: List, rel_schemas: List,
        workspace_id: str = "", resource_id: str = "",
    ) -> Optional[AgentSession]:
        """Load session and message history from PostgreSQL."""
        try:
            await self._ensure_repos()
            rows = await self.chat_repo.get_messages(session_id)

            if not rows:
                existing = await self.chat_repo.get_session(session_id)
                if not existing:
                    return None

            session = AgentSession(
                session_id=session_id,
                agent_id=agent_id,
                tenant_id=tenant_id,
                agent_definition=agent_def,
                skills=skills,
                entity_schemas=entity_schemas,
                relationship_schemas=rel_schemas,
                workspace_id=workspace_id,
                resource_id=resource_id,
            )
            for r in rows:
                if r["role"] == "user":
                    session.messages.append(HumanMessage(content=r["content"]))
                else:
                    session.messages.append(AIMessage(content=r["content"]))

            logger.info("Loaded session %s with %d messages", session_id, len(rows))
            return session
        except Exception as e:
            logger.warning("Failed to load session %s: %s", session_id, e)
            return None

    # =========================================================================
    # RUNTIME TOOLS
    # =========================================================================

    def _create_runtime_tools(self, session: AgentSession, workspace_status: str = "") -> list:
        """Agent'in runtime tool'larini olustur. workspace_status'a gore filtreler."""
        ocr = self.ocr_bridge
        db_ref = self.db
        event_bus_ref = self.event_bus
        comms_repo_ref = self  # _ensure_repos lazily creates self.chat_repo / self.agent_repo

        async def _get_bb():
            from .blackboard import Blackboard
            from ..event_store.postgres_client import get_postgres_client
            from ..comms_repository import CommsRepository
            pg = await get_postgres_client()
            return Blackboard(CommsRepository(pg))

        @tool
        async def write_to_blackboard(
            topic: str,
            content: str,
            entry_type: str = "info",
            workspace_id: str = "",
        ) -> str:
            """Blackboard'a konu bazli bilgi yaz (PostgreSQL'de kalici).
            Diger agent'lar bu bilgiyi okuyabilir - session'lar arasi paylasim.

            Args:
                topic: Konu basligi (orn: 'schema_proposal', 'extraction_progress')
                content: Yazilacak icerik
                entry_type: info, proposal, decision, progress, alert
                workspace_id: Iliskili workspace ID
            """
            bb = await _get_bb()
            result = await bb.write(
                topic=topic, content=content,
                agent_id=session.agent_id,
                workspace_id=workspace_id,
                entry_type=entry_type,
            )
            session.blackboard.setdefault(topic, []).append({
                "content": content,
                "timestamp": datetime.utcnow().isoformat(),
                "agent_id": session.agent_id,
            })
            return json.dumps(result, ensure_ascii=False)

        @tool
        async def read_blackboard(
            topic: str = "",
            workspace_id: str = "",
            limit: int = 20,
        ) -> str:
            """Blackboard'dan bilgi oku (tum agent'larin yazdiklarini gor).

            Args:
                topic: Konu filtresi (bos birak tum konulari gormek icin)
                workspace_id: Workspace filtresi
                limit: Max entry sayisi
            """
            bb = await _get_bb()
            if topic:
                entries = await bb.read(topic=topic, workspace_id=workspace_id, limit=limit)
                return json.dumps({"topic": topic, "entries": entries}, ensure_ascii=False, default=str)
            else:
                topics = await bb.list_topics(workspace_id=workspace_id)
                return json.dumps({"topics": topics}, ensure_ascii=False, default=str)

        @tool
        async def send_agent_message(
            to_agent: str,
            message: str,
            workspace_id: str = "",
            message_type: str = "direct",
        ) -> str:
            """Baska bir agent'a mesaj gonder.
            Agent'lar arasi koordinasyon ve bilgi paylasimi icin.

            Args:
                to_agent: Hedef agent ID
                message: Mesaj icerigi
                workspace_id: Iliskili workspace
                message_type: direct, status_update, request, alert
            """
            bb = await _get_bb()
            result = await bb.send_message(
                from_agent=session.agent_id,
                to_agent=to_agent,
                message=message,
                workspace_id=workspace_id,
                message_type=message_type,
            )
            return json.dumps(result, ensure_ascii=False)

        @tool
        async def get_agent_messages(
            workspace_id: str = "",
            status: str = "unread",
        ) -> str:
            """Bana gelen mesajlari oku.

            Args:
                workspace_id: Workspace filtresi
                status: unread veya read
            """
            bb = await _get_bb()
            messages = await bb.get_messages(
                agent_id=session.agent_id,
                status=status,
                workspace_id=workspace_id,
            )
            if messages:
                msg_ids = [m["id"] for m in messages]
                await bb.mark_read(msg_ids)
            return json.dumps({"messages": messages, "count": len(messages)}, ensure_ascii=False, default=str)

        @tool
        async def run_ocr(
            images: List[str],
            pipeline: str = "hybrid",
            skill_id: str = "",
        ) -> str:
            """Belge goruntuleri uzerinde OCR + entity extraction calistir.

            Args:
                images: Goruntu dosya yollari
                pipeline: 'unified' (tek gecis), 'hybrid' (ucuz OCR + extraction), 'sequential'
                skill_id: Kullanilacak skill ID
            """
            sid = skill_id or (session.skills[0].get("id") if session.skills else "")

            if pipeline == "hybrid":
                result = await ocr.run_hybrid_pipeline(images, skill_id=sid)
            else:
                result = await ocr.run_agentic_ocr(images=images, skill_id=sid, mode="vision")

            if "error" not in result:
                preview = ocr.preview_entities(result)
                session.pending_commits.append(result)
                return json.dumps({
                    "status": "preview",
                    "preview": preview,
                    "message": f"{preview['entity_count']} entity ve {preview['relationship_count']} iliski bulundu. "
                               f"Kaydetmek icin 'commit_entities' tool'unu kullanin.",
                }, ensure_ascii=False, default=str)

            return json.dumps(result, ensure_ascii=False, default=str)

        @tool
        async def commit_entities(confirm: bool = True) -> str:
            """Bekleyen entity/relationship'leri Knowledge DB'ye kaydet.
            Oncesinde run_ocr ile preview alinmis olmali.

            Args:
                confirm: True ise kaydet, False ise iptal et
            """
            if not session.pending_commits:
                return json.dumps({"error": "Bekleyen commit yok"}, ensure_ascii=False)

            if not confirm:
                session.pending_commits.clear()
                return json.dumps({"cancelled": True}, ensure_ascii=False)

            results = []
            for commit_data in session.pending_commits:
                r = await ocr.commit_to_knowledge_db(commit_data)
                results.append(r)

            session.pending_commits.clear()
            return json.dumps({"committed": True, "results": results}, ensure_ascii=False, default=str)

        @tool
        async def check_existing_entities(entity_type: str, name_pattern: str = "") -> str:
            """Knowledge DB'de mevcut entity'leri kontrol et. Duplikasyondan kacinmak icin.

            Args:
                entity_type: Entity tipi (ornek: 'Policy', 'Customer')
                name_pattern: Aranacak isim pattern'i
            """
            try:
                query = f"""
                    MATCH (n:{entity_type})
                    WHERE $pattern = '' OR toLower(n.name) CONTAINS toLower($pattern)
                       OR toLower(n.id) CONTAINS toLower($pattern)
                    RETURN n.id AS id, n.name AS name
                    LIMIT 20
                """
                result = await self.db.execute_query(query, {"pattern": name_pattern})
                return json.dumps({
                    "entity_type": entity_type,
                    "found": len(result),
                    "entities": [{"id": r["id"], "name": r.get("name", "")} for r in result],
                }, ensure_ascii=False)
            except Exception as e:
                return json.dumps({"error": str(e)}, ensure_ascii=False)

        # =====================================================================
        # WORKSPACE WORKFLOW TOOLS
        # =====================================================================

        @tool
        async def create_processing_workflow(workspace_id: str, pipeline: str = "hybrid") -> str:
            """Toplu belge isleme workflow'u olustur. Non-blocking - hemen doner.
            ONEMLI: Bu tool'u SADECE KB Agent olusturulduktan ve kullanici toplu belge yukledikten SONRA cagir.
            Schema onayi asamasinda bu tool'u CAGIRMA - once create_kb_agent cagir.

            Args:
                workspace_id: Isleme baslatilacak workspace ID
                pipeline: Pipeline tipi ('hybrid' onerilen)
            """
            try:
                from .batch_orchestrator import BatchOrchestrator
                from ..event_store.postgres_client import get_postgres_client
                from ..workspace_repository import WorkspaceRepository

                pg = await get_postgres_client()
                ws_repo = WorkspaceRepository(pg)
                orch = BatchOrchestrator(ws_repo)

                jobs = await ws_repo.list_batch_jobs(workspace_id)
                jobs = [j for j in jobs if j.get("status") in ("created", "queued")]

                if not jobs:
                    return json.dumps({"error": "Islenecek batch job bulunamadi"}, ensure_ascii=False)

                batch_job_id = jobs[0].get("id", "")
                result = await orch.start_processing(workspace_id, batch_job_id)
                return json.dumps(result, ensure_ascii=False, default=str)

            except Exception as e:
                return json.dumps({"error": str(e)}, ensure_ascii=False)

        @tool
        async def check_workflow_status(workspace_id: str) -> str:
            """Workspace isleme durumunu kontrol et.
            Neo4j + Event Store sorgulayarak ozet istatistik dondurur.

            Args:
                workspace_id: Durumu sorgulanacak workspace ID
            """
            try:
                from .batch_orchestrator import BatchOrchestrator
                from ..event_store.postgres_client import get_postgres_client
                from ..workspace_repository import WorkspaceRepository

                pg = await get_postgres_client()
                ws_repo = WorkspaceRepository(pg)
                orch = BatchOrchestrator(ws_repo)
                stats = await orch.get_workspace_stats(workspace_id)

                try:
                    from ..dependencies import get_event_store
                    from ..event_store.models import EventFilter

                    es = await get_event_store()
                    f = EventFilter(
                        tenant_id="default-tenant",
                        entity_type="WorkspaceDocument",
                        limit=5,
                    )
                    recent_events = await es.get_events(f)
                    stats["recent_events"] = [
                        {"entity_id": e.entity_id, "step": e.metadata.get("step", ""), "timestamp": str(e.created_at)}
                        for e in recent_events
                    ]
                except Exception:
                    pass

                return json.dumps(stats, ensure_ascii=False, default=str)

            except Exception as e:
                return json.dumps({"error": str(e)}, ensure_ascii=False)

        @tool
        async def get_event_log(workspace_id: str, last_n: int = 20) -> str:
            """Son N olayi Event Store'dan getir.
            Belge isleme adimlari ve durum degisikliklerini gosterir.

            Args:
                workspace_id: Workspace ID
                last_n: Getirilecek olay sayisi (max 50)
            """
            try:
                from ..event_store.postgres_client import get_postgres_client
                from ..workspace_repository import WorkspaceRepository

                pg = await get_postgres_client()
                ws_repo = WorkspaceRepository(pg)
                all_docs = await ws_repo.list_documents(workspace_id, limit=10000)
                doc_ids = set(d["id"] for d in all_docs)

                from ..dependencies import get_event_store
                from ..event_store.models import EventFilter

                es = await get_event_store()
                f = EventFilter(
                    tenant_id="default-tenant",
                    entity_type="WorkspaceDocument",
                    limit=min(last_n, 50),
                )
                events = await es.get_events(f)

                ws_events = [
                    {
                        "entity_id": e.entity_id,
                        "event_type": e.event_type.value if hasattr(e.event_type, "value") else str(e.event_type),
                        "step": e.metadata.get("step", ""),
                        "timestamp": str(e.created_at),
                    }
                    for e in events if e.entity_id in doc_ids
                ]

                return json.dumps({
                    "workspace_id": workspace_id,
                    "events": ws_events[:last_n],
                    "total": len(ws_events),
                }, ensure_ascii=False, default=str)

            except Exception as e:
                return json.dumps({"error": str(e)}, ensure_ascii=False)

        @tool
        async def review_and_approve(workspace_id: str, document_ids: List[str], action: str = "approve") -> str:
            """Dusuk guvenli belgeleri onayla veya reddet.

            Args:
                workspace_id: Workspace ID
                document_ids: Islem yapilacak belge ID listesi
                action: 'approve' veya 'reject'
            """
            try:
                from .batch_orchestrator import BatchOrchestrator
                from ..event_store.postgres_client import get_postgres_client
                from ..workspace_repository import WorkspaceRepository

                pg = await get_postgres_client()
                ws_repo = WorkspaceRepository(pg)
                orch = BatchOrchestrator(ws_repo)
                result = await orch.approve_review_items(workspace_id, document_ids, action)
                return json.dumps(result, ensure_ascii=False, default=str)

            except Exception as e:
                return json.dumps({"error": str(e)}, ensure_ascii=False)

        # =====================================================================
        # WORKSPACE CONVERSATION TOOLS
        # =====================================================================

        @tool
        async def extract_sample_text(workspace_id: str) -> str:
            """Workspace'teki sample belgeleri OCR ile isle ve metni kaydet.
            Upload edildikten sonra ilk cagrilmasi gereken tool.

            Args:
                workspace_id: Workspace ID
            """
            try:
                from .workspace_agent import WorkspaceAgent
                agent = WorkspaceAgent(db_ref)
                result = await agent.extract_sample_text(workspace_id)
                return json.dumps(result, ensure_ascii=False, default=str)
            except Exception as e:
                return json.dumps({"error": str(e)}, ensure_ascii=False)

        @tool
        async def get_sample_summary(workspace_id: str) -> str:
            """OCR edilmis sample belgelerinin ozetini getir.
            Kullaniciya belgelerin icerigi hakkinda bilgi vermek icin kullan.

            Args:
                workspace_id: Workspace ID
            """
            try:
                from .workspace_agent import WorkspaceAgent
                agent = WorkspaceAgent(db_ref)
                result = await agent.get_sample_summary(workspace_id)
                return json.dumps(result, ensure_ascii=False, default=str)
            except Exception as e:
                return json.dumps({"error": str(e)}, ensure_ascii=False)

        @tool
        async def create_workspace_skill(
            workspace_id: str,
            user_intent: str,
            extraction_rules: List[str] = [],
        ) -> str:
            """Kullanicinin amacina gore extraction skill/schema olustur.
            Kullanici ne cikarilacagini belirttikten sonra cagir.

            Args:
                workspace_id: Workspace ID
                user_intent: Kullanicinin ne istedigini aciklayan metin
                extraction_rules: Ek kurallar listesi
            """
            try:
                from .workspace_agent import WorkspaceAgent
                agent = WorkspaceAgent(db_ref)
                result = await agent.create_skill_from_intent(
                    workspace_id,
                    user_intent=user_intent,
                    extraction_rules=extraction_rules or [],
                )
                return json.dumps(result, ensure_ascii=False, default=str)
            except Exception as e:
                return json.dumps({"error": str(e)}, ensure_ascii=False)

        @tool
        async def get_sample_ocr_text(workspace_id: str, doc_id: str = "", max_chars: int = 5000) -> str:
            """Belirli bir sample belgenin ham OCR metnini getir.
            Kullanici belgeyi detayli incelemek isterse kullan.

            Args:
                workspace_id: Workspace ID
                doc_id: Belge ID (bos ise ilk belge)
                max_chars: Maximum karakter sayisi
            """
            try:
                from ..event_store.postgres_client import get_postgres_client
                from ..workspace_repository import WorkspaceRepository

                pg = await get_postgres_client()
                ws_repo = WorkspaceRepository(pg)

                if doc_id:
                    d = await ws_repo.get_document(doc_id)
                    docs = [d] if d else []
                else:
                    docs = await ws_repo.list_documents(
                        workspace_id, is_sample=True, limit=1,
                    )
                    docs = [d for d in docs if d.get("ocr_text")]

                if not docs:
                    return json.dumps({"error": "OCR metni bulunamadi"}, ensure_ascii=False)

                d = docs[0]
                text = (d.get("ocr_text") or "")[:max_chars]
                return json.dumps({
                    "id": d["id"],
                    "file_name": d.get("file_name", ""),
                    "chars": d.get("chars", 0),
                    "text_preview": text,
                    "truncated": d.get("chars", 0) > max_chars,
                }, ensure_ascii=False)
            except Exception as e:
                return json.dumps({"error": str(e)}, ensure_ascii=False)

        @tool
        async def analyze_workspace_via_mcp(
            workspace_id: str,
            minio_bucket: str = "documents",
            minio_prefix: str = "",
            domain_hint: str = "",
            max_samples: int = 5,
        ) -> str:
            """Workspace icin MCP tabanli schema analizi baslat.
            MCP Sampling kullanarak ornek belgelerden schema onerisi cikarir.
            Kullaniciya Elicitation ile onay sorar.
            Legacy extract_sample_text + create_workspace_skill yerine bunu kullan.

            Args:
                workspace_id: Workspace ID
                minio_bucket: Orneklerin bulundugu MinIO bucket
                minio_prefix: Klasor filtresi
                domain_hint: Domain ipucu (orn: 'sigorta policeleri', 'hukuki belgeler')
                max_samples: Analiz edilecek ornek sayisi (1-10)
            """
            try:
                from .workspace_agent import WorkspaceAgent
                agent = WorkspaceAgent(db_ref)
                result = await agent.analyze_via_mcp(
                    workspace_id,
                    minio_bucket=minio_bucket,
                    minio_prefix=minio_prefix,
                    domain_hint=domain_hint,
                    max_samples=max_samples,
                    auto_approve=False,
                )
                return json.dumps(result, ensure_ascii=False, default=str)
            except Exception as e:
                return json.dumps({"error": str(e)}, ensure_ascii=False)

        @tool
        async def create_kb_agent(
            workspace_id: str,
            custom_name: str = "",
            custom_purpose: str = "",
            minio_bucket: str = "",
            minio_prefix: str = "",
            auto_deploy: bool = False,
        ) -> str:
            """Onaylanmis workspace schema'sindan Knowledge Base Agent olustur.
            Schema onaylandiktan sonra cagir. Otomatik olarak:
            - Extraction skill olusturur
            - Goal olusturur
            - AgentDefinition olusturur
            - Hepsini birbirine baglar

            Args:
                workspace_id: Workspace ID (schema onaylanmis olmali)
                custom_name: Ozel agent adi (bos ise otomatik)
                custom_purpose: Ozel amac aciklamasi
                minio_bucket: Belgelerin MinIO bucket'i (bos ise workspace'ten)
                minio_prefix: MinIO prefix
                auto_deploy: True ise Gateway'e otomatik deploy et
            """
            try:
                from .kb_agent_factory import KBAgentFactory
                from ..event_store.postgres_client import get_postgres_client
                from ..agent_repository import AgentRepository
                from ..workspace_repository import WorkspaceRepository
                pg = await get_postgres_client()
                agent_repo = AgentRepository(pg)
                ws_repo = WorkspaceRepository(pg)
                await ws_repo.update_workspace(workspace_id, {"status": "schema_approved"})
                factory = KBAgentFactory(agent_repo, ws_repo)
                result = await factory.create_from_workspace(
                    workspace_id=workspace_id,
                    tenant_id=session.tenant_id,
                    auto_deploy=auto_deploy,
                    custom_name=custom_name,
                    custom_purpose=custom_purpose,
                    minio_bucket=minio_bucket,
                    minio_prefix=minio_prefix,
                )
                await event_bus_ref.publish(
                    EventTypes.KB_AGENT_CREATED,
                    {"result": result, "workspace_id": workspace_id},
                    source_agent=session.agent_id,
                    workspace_id=workspace_id,
                )
                return json.dumps(result, ensure_ascii=False, default=str)
            except Exception as e:
                return json.dumps({"error": str(e)}, ensure_ascii=False)

        # =====================================================================
        # MCP-BACKED TOOLS (via FastMCP Client -> Context Forge Gateway)
        # =====================================================================

        tenant_id_ref = session.tenant_id

        @tool
        async def mcp_browse_documents(
            bucket: str = "documents",
            prefix: str = "",
            offset: int = 0,
            limit: int = 50,
        ) -> str:
            """MinIO'daki belgeleri sayfalayarak listele.
            10K+ belge icin guvenli - sayfalama ile calisir.

            Args:
                bucket: Bucket adi (documents, prompts, ocr-output)
                prefix: Klasor filtresi (orn: 'sigorta/', '2024/')
                offset: Kacinci belgeden baslansin
                limit: Kac belge gosterilsin (max 200)
            """
            try:
                result = await call_mcp_tool("storage_browse_files", {
                    "bucket": bucket, "prefix": prefix, "offset": offset, "limit": limit,
                }, tenant_id=tenant_id_ref)
                return json.dumps(result, ensure_ascii=False, default=str)
            except Exception as e:
                return json.dumps({"error": str(e)}, ensure_ascii=False)

        @tool
        async def mcp_search_documents(
            bucket: str = "documents",
            query: str = "",
            prefix: str = "",
            limit: int = 50,
        ) -> str:
            """MinIO'daki belgeleri isimlerine gore ara.
            Buyuk/kucuk harf duyarsiz arama yapar.

            Args:
                bucket: Bucket adi
                query: Arama terimi (dosya adina gore)
                prefix: Klasor filtresi
                limit: Max sonuc
            """
            try:
                result = await call_mcp_tool("storage_search_files", {
                    "bucket": bucket, "query": query, "prefix": prefix, "limit": limit,
                }, tenant_id=tenant_id_ref)
                return json.dumps(result, ensure_ascii=False, default=str)
            except Exception as e:
                return json.dumps({"error": str(e)}, ensure_ascii=False)

        @tool
        async def mcp_read_document(
            bucket: str = "documents",
            key: str = "",
            max_chars: int = 5000,
        ) -> str:
            """MinIO'daki bir belgenin icerigini oku.
            Buyuk dosyalar icin max_chars ile sinirla.

            Args:
                bucket: Bucket adi
                key: Dosya yolu
                max_chars: Maximum karakter (0 = tumu)
            """
            try:
                result = await call_mcp_tool("storage_read_file", {
                    "bucket": bucket, "key": key, "max_chars": max_chars,
                }, tenant_id=tenant_id_ref)
                return json.dumps(result, ensure_ascii=False, default=str)
            except Exception as e:
                return json.dumps({"error": str(e)}, ensure_ascii=False)

        @tool
        async def mcp_upload_document(
            bucket: str = "documents",
            key: str = "",
            content: str = "",
        ) -> str:
            """MinIO'ya belge yukle.

            Args:
                bucket: Hedef bucket (documents, prompts, ocr-output)
                key: Dosya yolu (orn: 'sigorta/police_001.txt')
                content: Dosya icerigi
            """
            try:
                result = await call_mcp_tool("storage_upload_file", {
                    "bucket": bucket, "key": key, "content": content,
                }, tenant_id=tenant_id_ref)
                return json.dumps(result, ensure_ascii=False, default=str)
            except Exception as e:
                return json.dumps({"error": str(e)}, ensure_ascii=False)

        @tool
        async def mcp_extract_entities(
            bucket: str = "documents",
            key: str = "",
            schema_json: str = "",
            auto_accept: bool = False,
        ) -> str:
            """Belgeden entity ve relationship cikar (MCP Sampling ile LLM kullanir).
            Dusuk guvenli sonuclar icin kullaniciya sorar (Elicitation).

            Args:
                bucket: Belgenin bulundugu bucket
                key: Belge dosya yolu
                schema_json: Extraction'i yonlendirecek schema (JSON)
                auto_accept: True ise dusuk guvenli sonuclari otomatik kabul et
            """
            try:
                result = await call_mcp_tool("extract_extract_entities", {
                    "bucket": bucket, "key": key,
                    "schema_json": schema_json, "auto_accept": auto_accept,
                }, tenant_id=tenant_id_ref)
                return json.dumps(result, ensure_ascii=False, default=str)
            except Exception as e:
                return json.dumps({"error": str(e)}, ensure_ascii=False)

        @tool
        async def mcp_classify_document(
            bucket: str = "documents",
            key: str = "",
            categories: str = "",
        ) -> str:
            """Belge turunu tespit et (MCP Sampling ile LLM kullanir).

            Args:
                bucket: Belgenin bulundugu bucket
                key: Belge dosya yolu
                categories: Gecerli kategoriler (virgul ile ayrilmis)
            """
            try:
                result = await call_mcp_tool("extract_classify_document", {
                    "bucket": bucket, "key": key,
                    "classification_categories": categories,
                }, tenant_id=tenant_id_ref)
                return json.dumps(result, ensure_ascii=False, default=str)
            except Exception as e:
                return json.dumps({"error": str(e)}, ensure_ascii=False)

        @tool
        async def mcp_summarize_document(
            bucket: str = "documents",
            key: str = "",
            focus: str = "",
        ) -> str:
            """Belgenin ozetini cikar (MCP Sampling ile LLM kullanir).

            Args:
                bucket: Belgenin bulundugu bucket
                key: Belge dosya yolu
                focus: Odaklanilacak alan (orn: 'mali detaylar', 'kisiler')
            """
            try:
                result = await call_mcp_tool("extract_summarize_document", {
                    "bucket": bucket, "key": key, "focus": focus,
                }, tenant_id=tenant_id_ref)
                return json.dumps(result, ensure_ascii=False, default=str)
            except Exception as e:
                return json.dumps({"error": str(e)}, ensure_ascii=False)

        @tool
        async def mcp_infer_schema(
            workspace_id: str = "",
            resource_id: str = "",
            max_samples: int = 5,
            domain_hint: str = "",
        ) -> str:
            """Ornek belgelerden knowledge graph semasi onerisi cikar.
            Resource'daki belgelerin metin icerigini analiz ederek entity/relationship schema onerisi yapar.

            Args:
                workspace_id: Workspace ID (resource_id yoksa workspace'in resource'unu kullanir)
                resource_id: Resource ID (dogrudan belirtilirse)
                max_samples: Analiz edilecek max ornek sayisi (1-10)
                domain_hint: Domain ipucu (orn: 'sigorta policeleri', 'ticaret sicil gazeteleri')
            """
            try:
                from ..event_store.postgres_client import get_postgres_client
                from ..resource_repository import ResourceRepository

                pg = await get_postgres_client()
                repo = ResourceRepository(pg)

                if not resource_id and workspace_id:
                    from ..workspace_repository import WorkspaceRepository
                    ws_repo = WorkspaceRepository(pg)
                    ws_data = await ws_repo.get_workspace(workspace_id)
                    if ws_data and ws_data.get("resource_id"):
                        resource_id = str(ws_data["resource_id"])

                if not resource_id and workspace_id:
                    res_row = await pg.fetchrow(
                        "SELECT id FROM resources WHERE workspace_id = $1 ORDER BY updated_at DESC LIMIT 1",
                        workspace_id,
                    )
                    if res_row:
                        resource_id = str(res_row["id"])

                if not resource_id:
                    return json.dumps({"error": "resource_id veya workspace_id gerekli. Lutfen once belge yukleyin."})

                docs = await repo.list_documents(resource_id, limit=max_samples)
                if not docs:
                    return json.dumps({"error": "Resource'ta belge bulunamadi. Lutfen once belge yukleyin."})

                pending_docs = [d for d in docs if d.get("extraction_status") == "pending"]
                ready_docs = [d for d in docs if d.get("extraction_status") != "pending"]

                if not ready_docs and pending_docs:
                    return json.dumps({
                        "error": "extraction_pending",
                        "message": f"{len(pending_docs)} belge hala isleniyor (image extraction). "
                                   "Lutfen extraction tamamlanana kadar bekleyin. "
                                   "Durumu kontrol etmek icin `query_resource_status` tool'unu kullanin.",
                        "pending_count": len(pending_docs),
                        "total_count": len(docs),
                    }, ensure_ascii=False)

                usable_docs = ready_docs if ready_docs else docs
                sample_names = [d.get("file_name", "") for d in usable_docs[:max_samples]]

                if pending_docs and ready_docs:
                    logger.info(
                        "Schema inference: %d ready, %d pending - using ready docs only",
                        len(ready_docs), len(pending_docs),
                    )

                sample_texts = []
                for doc in usable_docs[:max_samples]:
                    minio_path = doc.get("minio_key", "")
                    fname = doc.get("file_name", "unknown")
                    if minio_path:
                        try:
                            from ..minio_client import download_file
                            data = download_file(minio_path)
                            import fitz
                            pdf = fitz.open(stream=data, filetype="pdf")
                            text = ""
                            for page in pdf:
                                text += page.get_text()
                                if len(text) > 3000:
                                    break
                            pdf.close()
                            sample_texts.append(f"--- {fname} ---\n{text[:3000]}")
                        except Exception as ex:
                            sample_texts.append(f"--- {fname} ---\n[Okunamadi: {ex}]")
                    else:
                        sample_texts.append(f"--- {fname} ---\n[MinIO yolu yok]")

                combined = "\n\n".join(sample_texts)

                prompt = f"""Asagidaki {len(sample_texts)} ornek belgeyi analiz et.
Domain ipucu: {domain_hint or 'Belirtilmedi'}

Bu belgelerden cikarilabilecek bir Knowledge Graph schema'si oner.

JSON formatinda donus yap:
{{
  "entities": [
    {{"entity_type": "...", "description": "...", "properties": ["prop1", "prop2", ...]}}
  ],
  "relationships": [
    {{"type": "...", "source": "EntityA", "target": "EntityB", "description": "..."}}
  ],
  "domain": "...",
  "confidence": 0.0-1.0,
  "reasoning": "..."
}}

BELGELER:
{combined}"""

                from langchain_google_genai import ChatGoogleGenerativeAI
                llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.1)
                response = await llm.ainvoke(prompt)
                content = response.content
                if isinstance(content, list):
                    content = "".join(
                        p.get("text", "") if isinstance(p, dict) else str(p)
                        for p in content
                    )

                import re as _re
                parsed_proposal = {}
                json_match = _re.search(r'\{[\s\S]*\}', content)
                if json_match:
                    try:
                        parsed_proposal = json.loads(json_match.group())
                    except json.JSONDecodeError:
                        pass

                saved_entity_ids = []
                saved_rel_ids = []
                if workspace_id and parsed_proposal:
                    try:
                        from ..workspace_repository import WorkspaceRepository
                        ws_repo = WorkspaceRepository(pg)

                        await pg.execute(
                            "DELETE FROM workspace_entity_schemas WHERE workspace_id = $1",
                            workspace_id,
                        )
                        await pg.execute(
                            "DELETE FROM workspace_relationship_schemas WHERE workspace_id = $1",
                            workspace_id,
                        )

                        entities = parsed_proposal.get("entities") or parsed_proposal.get("entity_schemas") or []
                        for ent in entities:
                            etype = ent.get("entity_type", "Unknown")
                            eid = f"es-{workspace_id}-{etype.lower().replace(' ', '_')}"
                            props = ent.get("properties", {})
                            if isinstance(props, list):
                                props = {p: "string" for p in props}
                            await pg.execute(
                                """
                                INSERT INTO entity_schemas (id, entity_type, description, properties, tenant_id)
                                VALUES ($1, $2, $3, $4::jsonb, $5)
                                ON CONFLICT (id) DO UPDATE SET
                                    entity_type = EXCLUDED.entity_type,
                                    description = EXCLUDED.description,
                                    properties = EXCLUDED.properties
                                """,
                                eid, etype, ent.get("description", ""),
                                json.dumps(props, ensure_ascii=False),
                                session.tenant_id,
                            )
                            await pg.execute(
                                """
                                INSERT INTO workspace_entity_schemas (workspace_id, schema_id)
                                VALUES ($1, $2)
                                ON CONFLICT DO NOTHING
                                """,
                                workspace_id, eid,
                            )
                            saved_entity_ids.append(eid)

                        rels = parsed_proposal.get("relationships") or parsed_proposal.get("relationship_schemas") or []
                        for rel in rels:
                            rtype = rel.get("type") or rel.get("relationship_type", "RELATED")
                            rid = f"rs-{workspace_id}-{rtype.lower().replace(' ', '_')}"
                            await pg.execute(
                                """
                                INSERT INTO relationship_schemas (id, relationship_type, description, source_entity, target_entity, tenant_id)
                                VALUES ($1, $2, $3, $4, $5, $6)
                                ON CONFLICT (id) DO UPDATE SET
                                    relationship_type = EXCLUDED.relationship_type,
                                    description = EXCLUDED.description,
                                    source_entity = EXCLUDED.source_entity,
                                    target_entity = EXCLUDED.target_entity
                                """,
                                rid, rtype, rel.get("description", ""),
                                rel.get("source") or rel.get("source_entity", ""),
                                rel.get("target") or rel.get("target_entity", ""),
                                session.tenant_id,
                            )
                            await pg.execute(
                                """
                                INSERT INTO workspace_relationship_schemas (workspace_id, schema_id)
                                VALUES ($1, $2)
                                ON CONFLICT DO NOTHING
                                """,
                                workspace_id, rid,
                            )
                            saved_rel_ids.append(rid)

                        await ws_repo.update_workspace(workspace_id, {"status": "schema_proposed"})
                        logger.info(
                            "Saved schema proposal for workspace %s: %d entities, %d relationships",
                            workspace_id, len(saved_entity_ids), len(saved_rel_ids),
                        )
                        await event_bus_ref.publish(
                            EventTypes.SCHEMA_PROPOSED,
                            {
                                "entities": len(saved_entity_ids),
                                "relationships": len(saved_rel_ids),
                                "schema": parsed_proposal,
                            },
                            source_agent=session.agent_id,
                            workspace_id=workspace_id,
                        )
                    except Exception as save_err:
                        logger.warning("Failed to save schema proposal: %s", save_err)

                return json.dumps({
                    "status": "proposed",
                    "samples_analyzed": len(sample_texts),
                    "sample_files": sample_names,
                    "schema_proposal": parsed_proposal or content,
                    "saved_entity_schemas": len(saved_entity_ids),
                    "saved_relationship_schemas": len(saved_rel_ids),
                }, ensure_ascii=False, default=str)
            except Exception as e:
                return json.dumps({"error": str(e)}, ensure_ascii=False)

        @tool
        async def mcp_batch_review_status(
            bucket: str = "ocr-output",
            prefix: str = "",
        ) -> str:
            """Batch extraction sonuclarinin review durumunu goster.
            Kac belge otomatik onaylandi, kac belge inceleme bekliyor.

            Args:
                bucket: Sonuclarin bulundugu bucket
                prefix: Klasor filtresi
            """
            try:
                result = await call_mcp_tool("extract_batch_review_status", {
                    "bucket": bucket, "prefix": prefix,
                }, tenant_id=tenant_id_ref)
                return json.dumps(result, ensure_ascii=False, default=str)
            except Exception as e:
                return json.dumps({"error": str(e)}, ensure_ascii=False)

        # =====================================================================
        # NEO4J KNOWLEDGE BASE TOOLS (via Context Forge Gateway)
        # =====================================================================

        @tool
        async def neo4j_query(
            cypher: str,
            db_url: str = "",
            db_username: str = "",
            db_password: str = "",
        ) -> str:
            """Knowledge Base'deki verileri Cypher sorgusuyla sorgula.
            Kullanicinin sorularina cevap bulmak icin Neo4j'den veri cek.
            SADECE okuma sorgulari (MATCH, RETURN) kullan, yazma yapma.

            Args:
                cypher: Cypher sorgusu (MATCH ... RETURN ...)
                db_url: Neo4j baglanti URL'i (bos ise varsayilan)
                db_username: Kullanici adi (bos ise varsayilan)
                db_password: Sifre (bos ise varsayilan)
            """
            try:
                params: Dict[str, Any] = {"cypher": cypher}
                if db_url:
                    params["db_url"] = db_url
                    params["db_username"] = db_username
                    params["db_password"] = db_password
                else:
                    import os
                    params["db_url"] = os.getenv("NEO4J_URI", "bolt://localhost:7687")
                    params["db_username"] = os.getenv("NEO4J_USERNAME", "neo4j")
                    params["db_password"] = os.getenv("NEO4J_PASSWORD", "password")
                result = await call_mcp_tool("neo4j_read_cypher", params, tenant_id=tenant_id_ref)
                return json.dumps(result, ensure_ascii=False, default=str)
            except Exception as e:
                return json.dumps({"error": str(e)}, ensure_ascii=False)

        @tool
        async def neo4j_semantic_search(
            cypher: str,
            query_text: str,
            db_url: str = "",
            db_username: str = "",
            db_password: str = "",
        ) -> str:
            """Knowledge Base'de semantik arama yap.
            Embedding vektoru ile Chunk node'larinda benzerlik aramasi yapar.
            Cypher'da $embedding_vector parametresini kullan.

            Args:
                cypher: Cypher sorgusu ($embedding_vector icermeli)
                query_text: Aranacak metin (embedding'e donusturulur)
                db_url: Neo4j baglanti URL'i (bos ise varsayilan)
                db_username: Kullanici adi
                db_password: Sifre
            """
            try:
                import os
                params: Dict[str, Any] = {
                    "cypher": cypher,
                    "query_text": query_text,
                }
                if db_url:
                    params["db_url"] = db_url
                    params["db_username"] = db_username
                    params["db_password"] = db_password
                else:
                    params["db_url"] = os.getenv("NEO4J_URI", "bolt://localhost:7687")
                    params["db_username"] = os.getenv("NEO4J_USERNAME", "neo4j")
                    params["db_password"] = os.getenv("NEO4J_PASSWORD", "password")
                result = await call_mcp_tool("neo4j_read_cypher_with_embedding", params, tenant_id=tenant_id_ref)
                return json.dumps(result, ensure_ascii=False, default=str)
            except Exception as e:
                return json.dumps({"error": str(e)}, ensure_ascii=False)

        # =====================================================================
        # RESOURCE STATUS TOOLS (PostgreSQL)
        # =====================================================================

        @tool
        async def query_resource_status(
            resource_id: str = "",
            workspace_id: str = "",
        ) -> str:
            """Resource ve belge durumlarini sorgula (PostgreSQL).
            Dosya isimlerini, sayilarini ve extraction/processing durumlarini dondurur.

            Args:
                resource_id: Resource UUID (bos ise workspace'ten bul)
                workspace_id: Workspace ID (resource_id bos ise kullanilir)
            """
            try:
                from ..event_store.postgres_client import get_postgres_client
                from ..resource_repository import ResourceRepository
                import uuid as _uuid

                pg = await get_postgres_client()
                repo = ResourceRepository(pg)

                if not resource_id and workspace_id:
                    from ..workspace_repository import WorkspaceRepository
                    ws_repo = WorkspaceRepository(pg)
                    ws_data = await ws_repo.get_workspace(workspace_id)
                    if ws_data and ws_data.get("resource_id"):
                        resource_id = ws_data["resource_id"]

                if not resource_id:
                    return json.dumps({"error": "resource_id veya workspace_id gerekli"})

                resource = await repo.get(resource_id)
                if not resource:
                    return json.dumps({"error": "Resource bulunamadi"})

                breakdown = await repo.get_status_breakdown(resource_id)

                docs = await repo.get_documents(resource_id, limit=200)
                doc_list = [
                    {
                        "file_name": d.get("file_name", ""),
                        "file_type": d.get("file_type", ""),
                        "file_size": d.get("file_size", 0),
                        "page_count": d.get("page_count", 0),
                        "extraction_status": d.get("extraction_status", "pending"),
                        "processing_status": d.get("processing_status", "pending"),
                    }
                    for d in docs
                ]

                return json.dumps({
                    "resource_id": str(resource["id"]),
                    "name": resource["name"],
                    "status": resource["status"],
                    "total_documents": resource["total_documents"],
                    "extracted_documents": resource["extracted_docs"],
                    "breakdown": breakdown,
                    "documents": doc_list,
                }, ensure_ascii=False, default=str)
            except Exception as e:
                return json.dumps({"error": str(e)}, ensure_ascii=False)

        always_tools = [
            write_to_blackboard,
            read_blackboard,
            send_agent_message,
            get_agent_messages,
            query_resource_status,
            neo4j_query,
            neo4j_semantic_search,
        ]

        sampling_tools = [
            extract_sample_text,
            get_sample_summary,
            get_sample_ocr_text,
            run_ocr,
            analyze_workspace_via_mcp,
            mcp_browse_documents,
            mcp_search_documents,
            mcp_read_document,
            mcp_upload_document,
        ]

        schema_inference_tools = [
            mcp_infer_schema,
        ]

        schema_approval_tools = [
            create_kb_agent,
            mcp_infer_schema,
        ]

        processing_tools = [
            create_processing_workflow,
            check_workflow_status,
            get_event_log,
            create_workspace_skill,
            commit_entities,
            check_existing_entities,
            mcp_extract_entities,
            mcp_classify_document,
            mcp_summarize_document,
        ]

        monitoring_tools = [
            check_workflow_status,
            get_event_log,
            review_and_approve,
            mcp_batch_review_status,
        ]

        stage_map = {
            "created":          always_tools + sampling_tools + schema_inference_tools,
            "sampling":         always_tools + sampling_tools + schema_inference_tools,
            "schema_review":    always_tools + sampling_tools + schema_inference_tools,
            "schema_proposed":  always_tools + schema_approval_tools,
            "schema_approved":  always_tools + processing_tools,
            "agent_ready":      always_tools + processing_tools,
            "ready":            always_tools + processing_tools,
            "processing":       always_tools + monitoring_tools,
            "quality_check":    always_tools + monitoring_tools,
            "completed":        always_tools + monitoring_tools,
            "failed":           always_tools + sampling_tools + processing_tools,
        }

        if workspace_status and workspace_status in stage_map:
            selected = stage_map[workspace_status]
            seen = set()
            unique_tools = []
            for t in selected:
                if t.name not in seen:
                    seen.add(t.name)
                    unique_tools.append(t)
            logger.info(
                "Stage-filtered tools for status=%s: %s",
                workspace_status,
                [t.name for t in unique_tools],
            )
            return unique_tools

        all_tools = (
            always_tools + sampling_tools + schema_inference_tools
            + schema_approval_tools + processing_tools + monitoring_tools
        )
        seen = set()
        unique_tools = []
        for t in all_tools:
            if t.name not in seen:
                seen.add(t.name)
                unique_tools.append(t)
        return unique_tools

    # =========================================================================
    # CHAT
    # =========================================================================

    async def chat(
        self,
        session_id: str,
        user_message: str,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        Agent ile chat (streaming).

        Yields:
            {"type": "message_chunk"|"tool_call"|"tool_result"|"final_response", ...}
        """
        session = self.get_session(session_id)
        if not session:
            yield {"type": "error", "content": "Session not found"}
            return

        resource_context = ""
        workspace_status = ""
        if session.workspace_id:
            try:
                from ..event_store.postgres_client import get_postgres_client
                from ..resource_repository import ResourceRepository
                from ..workspace_repository import WorkspaceRepository
                pg = await get_postgres_client()

                ws_repo = WorkspaceRepository(pg)
                ws_data = await ws_repo.get_workspace(session.workspace_id)
                if ws_data:
                    workspace_status = ws_data.get("status", "")
                    if not session.resource_id and ws_data.get("resource_id"):
                        session.resource_id = str(ws_data["resource_id"])

                if not session.resource_id:
                    res_row = await pg.fetchrow(
                        "SELECT id FROM resources WHERE workspace_id = $1 ORDER BY updated_at DESC LIMIT 1",
                        session.workspace_id,
                    )
                    if res_row:
                        session.resource_id = str(res_row["id"])

                if session.resource_id:
                    repo = ResourceRepository(pg)
                    res = await repo.get(session.resource_id)
                    if res:
                        docs = await repo.list_documents(session.resource_id, limit=50)
                        doc_names = [d.get("file_name", "?") for d in docs]
                        resource_context = (
                            f"\n\n## Workspace Resource Durumu\n"
                            f"- Resource: `{res['name']}` (id: `{session.resource_id}`)\n"
                            f"- Toplam belge: {res.get('total_documents', len(docs))}\n"
                            f"- Belgeler: {', '.join(doc_names[:10])}"
                            f"{'...' if len(doc_names) > 10 else ''}\n"
                            f"\nBu belgeler MinIO'ya yuklenmis ve analiz icin hazir. "
                            f"Kullanici belge yukledigini soylerse, bu bilgiyi kullan."
                        )
            except Exception as e:
                logger.warning("Failed to load resource context: %s", e)

        system_prompt = get_runtime_system_prompt(
            agent_name=session.agent_definition.get("name", "Agent"),
            agent_purpose=session.agent_definition.get("purpose", ""),
            skills=session.skills,
            entity_schemas=session.entity_schemas,
            relationship_schemas=session.relationship_schemas,
            workspace_id=session.workspace_id,
            resource_id=session.resource_id,
            additional_instructions=resource_context,
        )

        tools = self._create_runtime_tools(session, workspace_status=workspace_status)
        llm = _create_runtime_llm()
        agent = create_react_agent(model=llm, tools=tools)

        input_messages = [SystemMessage(content=system_prompt)]
        input_messages.extend(session.messages)
        input_messages.append(HumanMessage(content=user_message))

        session.messages.append(HumanMessage(content=user_message))
        await self._save_message(session.session_id, "user", user_message)

        full_response = ""

        async for chunk in agent.astream(
            {"messages": input_messages},
            stream_mode="updates",
        ):
            for node_name, node_output in chunk.items():
                for message in node_output.get("messages", []):
                    if isinstance(message, AIMessage):
                        if message.tool_calls:
                            for tc in message.tool_calls:
                                chunk_data = {"type": "tool_call", "name": tc["name"], "args": tc["args"]}
                                yield chunk_data
                                await self.event_bus.publish(
                                    "agent.tool_call",
                                    {"tool": tc["name"], "args": tc["args"]},
                                    source_agent=session.agent_id,
                                    workspace_id=session.workspace_id,
                                )
                        elif message.content:
                            text = message.content
                            if isinstance(text, list):
                                text = "".join(
                                    part.get("text", "") if isinstance(part, dict) else str(part)
                                    for part in text
                                )
                            full_response += text
                            yield {"type": "message_chunk", "content": text}
                    elif isinstance(message, ToolMessage):
                        content = message.content
                        if isinstance(content, list):
                            content = "".join(
                                part.get("text", "") if isinstance(part, dict) else str(part)
                                for part in content
                            )
                        tool_name = message.name or "unknown"
                        chunk_data = {
                            "type": "tool_result",
                            "name": tool_name,
                            "result": (content or "")[:200],
                        }
                        yield chunk_data
                        await self.event_bus.publish(
                            "agent.tool_result",
                            {"tool": tool_name, "result": (content or "")[:200]},
                            source_agent=session.agent_id,
                            workspace_id=session.workspace_id,
                        )

        if full_response:
            session.messages.append(AIMessage(content=full_response))
            await self._save_message(session.session_id, "assistant", full_response)
            yield {"type": "final_response", "content": full_response}

    async def chat_sync(self, session_id: str, user_message: str) -> str:
        """Non-streaming chat -- son yaniti dondurur."""
        final = ""
        async for chunk in self.chat(session_id, user_message):
            if chunk.get("type") == "final_response":
                final = chunk.get("content", "")
        return final
