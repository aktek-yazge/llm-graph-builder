"""
Agent Builder Service
=====================

Agent Builder ana servis sınıfı.
Session yönetimi, conversation state machine ve orchestration.

Bu servis şu işlemleri koordine eder:
- Session lifecycle (create, process, complete)
- Conversation state machine
- Sample document analysis
- Skill generation ve test
- Agent creation ve deployment

Kullanım:
---------
    from backend.src.agent_builder.service import AgentBuilderService
    from backend.src.agent_builder.ontology import get_ontology_client
    
    client = await get_ontology_client()
    service = AgentBuilderService(client, tenant_id="tenant-001")
    
    # Session başlat
    session = await service.create_session(user_id="user-123")
    
    # Mesaj gönder
    response = await service.process_message(session.id, "Sigorta belgeleri işlemek istiyorum")
"""

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from .models import (
    BuilderSession,
    BuilderChatRequest,
    BuilderChatResponse,
    SessionState,
    SessionStatus,
    AgentCreate,
    AgentDefinition,
    SkillCreate,
    Skill,
)
from .ontology.neo4j_client import OntologyDBClient
from .ontology.reasoner import OntologyReasoner
from .conversation.state_machine import ConversationStateMachine
from .skills.skill_registry import SkillRegistry
from .agent.builder_agent import BuilderAgent, create_builder_agent
from .gateway.mcp_gateway_client import MCPGatewayClient, get_gateway_client
from .gateway.virtual_server import VirtualServerManager
from .chat_repository import ChatRepository
from .agent_repository import AgentRepository

logger = logging.getLogger(__name__)


class AgentBuilderService:
    """
    Agent Builder ana servis sınıfı.
    
    Session bazlı agent oluşturma workflow'unu yönetir.
    Conversation state machine'i kullanarak kullanıcıyla etkileşim sağlar.
    
    Attributes:
        db: OntologyDBClient instance
        tenant_id: Mevcut tenant ID
        reasoner: OntologyReasoner instance
        state_machine: ConversationStateMachine instance
        skill_registry: SkillRegistry instance
    """
    
    def __init__(self, db: OntologyDBClient, tenant_id: str):
        self.db = db
        self.tenant_id = tenant_id

        self._reasoner: Optional[OntologyReasoner] = None
        self._state_machine: Optional[ConversationStateMachine] = None
        self._skill_registry: Optional[SkillRegistry] = None
        self._builder_agent: Optional[BuilderAgent] = None
        self._gateway: Optional[MCPGatewayClient] = None
        self._vs_manager: Optional[VirtualServerManager] = None
        self._chat_repo: Optional[ChatRepository] = None
        self._agent_repo: Optional[AgentRepository] = None
    
    @property
    def reasoner(self) -> OntologyReasoner:
        """OntologyReasoner instance (lazy)"""
        if self._reasoner is None:
            self._reasoner = OntologyReasoner(self.db)
        return self._reasoner
    
    @property
    def state_machine(self) -> ConversationStateMachine:
        """ConversationStateMachine instance (lazy)"""
        if self._state_machine is None:
            self._state_machine = ConversationStateMachine(self.db, self.reasoner)
        return self._state_machine
    
    @property
    def skill_registry(self) -> SkillRegistry:
        """SkillRegistry instance (lazy)"""
        if self._skill_registry is None:
            raise RuntimeError("SkillRegistry not initialized. Call ensure_repos() first.")
        return self._skill_registry

    async def ensure_repos(self):
        """Lazy-init PostgreSQL repositories."""
        if self._chat_repo is None or self._agent_repo is None:
            from .event_store.postgres_client import get_postgres_client
            pg = await get_postgres_client()
            if self._chat_repo is None:
                self._chat_repo = ChatRepository(pg)
            if self._agent_repo is None:
                self._agent_repo = AgentRepository(pg)
            if self._skill_registry is None:
                self._skill_registry = SkillRegistry(self._agent_repo)
    
    # =========================================================================
    # SESSION MANAGEMENT
    # =========================================================================
    
    async def create_session(self, user_id: Optional[str] = None) -> BuilderSession:
        """
        Yeni builder session olustur.
        BuilderAgent uzerinden session olusturur, meta PostgreSQL'e kaydedilir.
        """
        await self.ensure_repos()
        agent = await self.get_builder_agent()
        session_id = await agent.create_session(user_id)

        await self._chat_repo.create_builder_session(
            session_id=session_id,
            tenant_id=self.tenant_id,
            user_id=user_id or "",
        )

        logger.info("Created builder session: %s for tenant: %s", session_id, self.tenant_id)

        return BuilderSession(
            id=session_id,
            tenant_id=self.tenant_id,
            user_id=user_id,
            status=SessionStatus.ACTIVE,
            current_state=SessionState.GOAL_ELICITATION,
            state_data={},
        )
    
    async def get_session(self, session_id: str) -> Optional[BuilderSession]:
        await self.ensure_repos()
        bs = await self._chat_repo.get_builder_session(session_id, self.tenant_id)
        if not bs:
            return None

        state_data = bs.get("state_data", {})
        if isinstance(state_data, str):
            try:
                state_data = json.loads(state_data)
            except (json.JSONDecodeError, TypeError):
                state_data = {}

        return BuilderSession(
            id=bs["id"],
            tenant_id=bs.get("tenant_id", self.tenant_id),
            user_id=bs.get("user_id"),
            status=SessionStatus(bs.get("status", "active")),
            current_state=SessionState(bs.get("current_state", "goal_elicitation")),
            state_data=state_data,
        )
    
    async def get_builder_agent(self) -> BuilderAgent:
        """BuilderAgent instance (lazy)."""
        if self._builder_agent is None:
            self._gateway = await get_gateway_client()
            self._builder_agent = await create_builder_agent(
                db=self.db, tenant_id=self.tenant_id, gateway=self._gateway,
            )
        return self._builder_agent

    async def get_vs_manager(self) -> VirtualServerManager:
        """VirtualServerManager instance (lazy)."""
        if self._vs_manager is None:
            if self._gateway is None:
                self._gateway = await get_gateway_client()
            self._vs_manager = VirtualServerManager(self._gateway, self.db)
        return self._vs_manager

    async def process_message(
        self,
        session_id: str,
        message: str,
    ) -> BuilderChatResponse:
        await self.ensure_repos()
        session = await self.get_session(session_id)

        if not session:
            return BuilderChatResponse(
                message="Session bulunamadı veya bu tenant'a ait değil.",
                state=SessionState.GOAL_ELICITATION,
                action_required="Yeni session oluşturun"
            )

        if session.status != SessionStatus.ACTIVE:
            return BuilderChatResponse(
                message=f"Bu session {session.status.value} durumunda. Yeni session oluşturun.",
                state=session.current_state
            )

        agent = await self.get_builder_agent()
        return await agent.process_message(session_id, message)

    async def stream_message(self, session_id: str, message: str):
        """
        Kullanıcı mesajını LLM-driven BuilderAgent ile isle (streaming).
        SSE endpoint icin kullanilir.

        Yields:
            Dict chunks: message_chunk, tool_call, tool_result, final_response
        """
        session = await self.get_session(session_id)
        if not session or session.status != SessionStatus.ACTIVE:
            yield {"type": "error", "content": "Session bulunamadi veya aktif degil."}
            return

        agent = await self.get_builder_agent()
        async for chunk in agent.stream(session_id, message):
            yield chunk
    
    async def abandon_session(self, session_id: str) -> bool:
        await self.ensure_repos()
        return await self._chat_repo.abandon_builder_session(session_id, self.tenant_id)
    
    # =========================================================================
    # SAMPLE ANALYSIS
    # =========================================================================
    
    async def register_uploaded_samples(
        self,
        session_id: str,
        sample_ids: List[str],
    ) -> bool:
        session = await self.get_session(session_id)
        if not session:
            return False

        state_data = session.state_data
        state_data["uploaded_sample_ids"] = sample_ids

        await self._chat_repo.update_builder_session(
            session_id, state_data=state_data,
        )
        return True
    
    async def analyze_samples(
        self,
        session_id: str,
        sample_ids: List[str]
    ) -> Dict[str, Any]:
        """
        Örnek belgeleri analiz et.
        
        Gemini OCR ile belgeleri analiz eder ve
        entity/relationship pattern'lerini tespit eder.
        
        Args:
            session_id: Session ID
            sample_ids: Analiz edilecek örnek ID'leri
            
        Returns:
            Analiz sonuçları
        """
        # TODO: Gemini OCR entegrasyonu
        # Şimdilik mock analiz sonucu
        
        analysis = {
            "detected_entities": ["Policy", "Customer", "Coverage"],
            "detected_fields": {
                "Policy": ["policy_no", "start_date", "end_date", "premium"],
                "Customer": ["name", "tc_no", "phone", "address"],
                "Coverage": ["type", "limit", "deductible"]
            },
            "detected_relationships": [
                ("Customer", "HAS_POLICY", "Policy"),
                ("Policy", "HAS_COVERAGE", "Coverage")
            ],
            "document_language": "tr",
            "document_type": "insurance_policy",
            "confidence": 0.85
        }
        
        session = await self.get_session(session_id)
        if session:
            state_data = session.state_data
            state_data["sample_analysis"] = analysis
            await self._chat_repo.update_builder_session(
                session_id,
                state_data=state_data,
                current_state="schema_proposal",
            )

        return analysis
    
    # =========================================================================
    # SKILL GENERATION
    # =========================================================================
    
    async def generate_skill_from_analysis(
        self,
        session_id: str,
        analysis: Dict[str, Any],
        skill_name: str,
        description: str
    ) -> str:
        """
        Analiz sonuçlarından skill oluştur.
        
        Args:
            session_id: Session ID
            analysis: Sample analiz sonuçları
            skill_name: Skill adı
            description: Skill açıklaması
            
        Returns:
            Oluşturulan skill ID
        """
        # Entity type'lardan prompt template oluştur
        entity_types = analysis.get("detected_entities", [])
        entity_types_str = ", ".join(entity_types)
        
        prompt_template = f"""Bu belgeden şu entity tiplerini çıkar: {{entity_types}}

Her entity için aşağıdaki property'leri tespit et:
{{entity_schemas}}

Entity'ler arasındaki ilişkileri de tespit et:
{{relationship_schemas}}

Belge içeriği:
{{text}}

Yanıtı JSON formatında ver:
{{{{
    "entities": [
        {{"type": "...", "properties": {{...}}}},
        ...
    ],
    "relationships": [
        {{"source": "...", "type": "...", "target": "..."}},
        ...
    ]
}}}}"""
        
        # Skill oluştur
        skill_data = SkillCreate(
            name=skill_name,
            description=description,
            skill_category="extraction",
            prompt_template=prompt_template,
            tenant_id=self.tenant_id,
            is_global=False,
            context_ids=[]
        )
        
        skill_id = await self.skill_registry.create_skill(skill_data)

        session = await self.get_session(session_id)
        if session:
            state_data = session.state_data
            state_data["generated_skill_id"] = skill_id
            await self._chat_repo.update_builder_session(
                session_id, state_data=state_data,
            )

        logger.info(f"Generated skill {skill_id} from analysis in session {session_id}")
        return skill_id
    
    # =========================================================================
    # AGENT CREATION
    # =========================================================================
    
    async def create_agent_from_session(
        self,
        session_id: str,
        agent_name: str,
        agent_description: str
    ) -> Optional[AgentDefinition]:
        """
        Session'dan agent oluştur.
        
        Session'daki goal, skill ve schema bilgilerini kullanarak
        agent tanımı oluşturur.
        
        Args:
            session_id: Session ID
            agent_name: Agent adı
            agent_description: Agent açıklaması
            
        Returns:
            Oluşturulan AgentDefinition veya None
        """
        session = await self.get_session(session_id)
        
        if not session:
            return None
        
        state_data = session.state_data
        
        # Session'dan skill ve goal ID'leri al
        skill_ids = []
        if state_data.get("generated_skill_id"):
            skill_ids.append(state_data["generated_skill_id"])
        if state_data.get("selected_skill_id"):
            skill_ids.append(state_data["selected_skill_id"])
        
        goal_ids = []
        if state_data.get("goal_id"):
            goal_ids.append(state_data["goal_id"])
        
        await self.ensure_repos()
        agent_id = f"agent-{uuid.uuid4().hex[:12]}"

        await self._agent_repo.create_agent({
            "id": agent_id,
            "name": agent_name,
            "description": agent_description,
            "purpose": state_data.get("goal_description", agent_description),
            "status": "draft",
            "tenant_id": self.tenant_id,
        })

        for skill_id in skill_ids:
            await self._agent_repo.link_agent_skill(agent_id, skill_id)

        await self._chat_repo.update_builder_session(
            session_id, created_agent_id=agent_id,
        )

        logger.info(f"Created agent {agent_id} from session {session_id}")

        return AgentDefinition(
            id=agent_id,
            name=agent_name,
            description=agent_description,
            purpose=state_data.get("goal_description", agent_description),
            status="draft",
            tenant_id=self.tenant_id,
        )
    
    # =========================================================================
    # DEPLOYMENT
    # =========================================================================
    
    async def deploy_agent(self, agent_id: str) -> Dict[str, Any]:
        await self.ensure_repos()
        agent = await self._agent_repo.get_agent(agent_id, self.tenant_id)
        if not agent:
            return {"success": False, "message": "Agent bulunamadi"}

        if agent.get("mcp_virtual_server_id"):
            return {
                "success": True,
                "message": "Agent zaten deploy edilmis",
                "virtual_server_id": agent["mcp_virtual_server_id"],
            }

        vs_manager = await self.get_vs_manager()
        deploy_result = await vs_manager.deploy_agent(agent_id, self.tenant_id)

        if deploy_result.get("success"):
            logger.info("Deployed agent %s to VS %s", agent_id, deploy_result.get("virtual_server_id"))

        return deploy_result
