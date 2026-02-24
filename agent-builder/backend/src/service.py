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
        """
        Args:
            db: Bağlı OntologyDBClient instance
            tenant_id: Tenant ID (multi-tenancy için)
        """
        self.db = db
        self.tenant_id = tenant_id
        
        # Servis bileşenleri (lazy init)
        self._reasoner: Optional[OntologyReasoner] = None
        self._state_machine: Optional[ConversationStateMachine] = None
        self._skill_registry: Optional[SkillRegistry] = None
    
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
            self._skill_registry = SkillRegistry(self.db)
        return self._skill_registry
    
    # =========================================================================
    # SESSION MANAGEMENT
    # =========================================================================
    
    async def create_session(self, user_id: Optional[str] = None) -> BuilderSession:
        """
        Yeni builder session oluştur.
        
        Args:
            user_id: Kullanıcı ID (optional)
            
        Returns:
            Oluşturulan BuilderSession
        """
        session_id = f"session-{uuid.uuid4().hex[:12]}"
        
        query = """
        CREATE (bs:BuilderSession {
            id: $session_id,
            tenant_id: $tenant_id,
            user_id: $user_id,
            status: 'active',
            current_state: 'goal_elicitation',
            state_data: '{}',
            messages: '[]',
            created_at: datetime(),
            updated_at: datetime()
        })
        RETURN bs.id as id, bs.tenant_id as tenant_id, bs.status as status
        """
        
        result = await self.db.execute_query(query, {
            "session_id": session_id,
            "tenant_id": self.tenant_id,
            "user_id": user_id
        }, write=True)
        
        logger.info(f"Created builder session: {session_id} for tenant: {self.tenant_id}")
        
        return BuilderSession(
            id=session_id,
            tenant_id=self.tenant_id,
            user_id=user_id,
            status=SessionStatus.ACTIVE,
            current_state=SessionState.GOAL_ELICITATION,
            state_data={}
        )
    
    async def get_session(self, session_id: str) -> Optional[BuilderSession]:
        """
        Session bilgilerini getir.
        
        Args:
            session_id: Session ID
            
        Returns:
            BuilderSession veya None
        """
        query = """
        MATCH (bs:BuilderSession {id: $session_id, tenant_id: $tenant_id})
        RETURN bs
        """
        
        result = await self.db.execute_query(query, {
            "session_id": session_id,
            "tenant_id": self.tenant_id
        })
        
        if not result:
            return None
        
        bs = result[0]["bs"]
        
        return BuilderSession(
            id=bs["id"],
            tenant_id=bs["tenant_id"],
            user_id=bs.get("user_id"),
            status=SessionStatus(bs.get("status", "active")),
            current_state=SessionState(bs.get("current_state", "goal_elicitation")),
            state_data=json.loads(bs.get("state_data", "{}"))
        )
    
    async def process_message(
        self,
        session_id: str,
        message: str
    ) -> BuilderChatResponse:
        """
        Kullanıcı mesajını işle.
        
        Conversation state machine'i kullanarak mesajı işler
        ve uygun yanıtı döndürür.
        
        Args:
            session_id: Session ID
            message: Kullanıcı mesajı
            
        Returns:
            BuilderChatResponse
        """
        # Session kontrolü
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
        
        # State machine'e gönder
        return await self.state_machine.process_message(session_id, message)
    
    async def abandon_session(self, session_id: str) -> bool:
        """
        Session'ı iptal et.
        
        Args:
            session_id: Session ID
            
        Returns:
            Başarılı mı
        """
        query = """
        MATCH (bs:BuilderSession {id: $session_id, tenant_id: $tenant_id})
        SET bs.status = 'abandoned',
            bs.updated_at = datetime()
        RETURN bs.id
        """
        
        result = await self.db.execute_query(query, {
            "session_id": session_id,
            "tenant_id": self.tenant_id
        }, write=True)
        
        return bool(result)
    
    # =========================================================================
    # SAMPLE ANALYSIS
    # =========================================================================
    
    async def register_uploaded_samples(
        self,
        session_id: str,
        sample_ids: List[str]
    ) -> bool:
        """
        Yüklenen örnekleri session'a kaydet.
        
        Args:
            session_id: Session ID
            sample_ids: Yüklenen örnek ID'leri
            
        Returns:
            Başarılı mı
        """
        # Mevcut state_data'yı güncelle
        session = await self.get_session(session_id)
        
        if not session:
            return False
        
        state_data = session.state_data
        state_data["uploaded_sample_ids"] = sample_ids
        
        query = """
        MATCH (bs:BuilderSession {id: $session_id})
        SET bs.state_data = $state_data,
            bs.updated_at = datetime()
        """
        
        await self.db.execute_query(query, {
            "session_id": session_id,
            "state_data": json.dumps(state_data, ensure_ascii=False)
        }, write=True)
        
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
        
        # Session state'ine kaydet
        session = await self.get_session(session_id)
        if session:
            state_data = session.state_data
            state_data["sample_analysis"] = analysis
            
            query = """
            MATCH (bs:BuilderSession {id: $session_id})
            SET bs.state_data = $state_data,
                bs.current_state = 'schema_proposal',
                bs.updated_at = datetime()
            """
            
            await self.db.execute_query(query, {
                "session_id": session_id,
                "state_data": json.dumps(state_data, ensure_ascii=False)
            }, write=True)
        
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
        
        # Session'a kaydet
        session = await self.get_session(session_id)
        if session:
            state_data = session.state_data
            state_data["generated_skill_id"] = skill_id
            
            await self.db.execute_query("""
                MATCH (bs:BuilderSession {id: $session_id})
                SET bs.state_data = $state_data,
                    bs.updated_at = datetime()
            """, {
                "session_id": session_id,
                "state_data": json.dumps(state_data, ensure_ascii=False)
            }, write=True)
        
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
        
        # Agent oluştur
        agent_id = f"agent-{uuid.uuid4().hex[:12]}"
        
        query = """
        CREATE (a:AgentDefinition {
            id: $agent_id,
            name: $name,
            description: $description,
            purpose: $purpose,
            status: 'draft',
            tenant_id: $tenant_id,
            created_at: datetime()
        })
        
        WITH a
        
        // Session'a bağla
        MATCH (bs:BuilderSession {id: $session_id})
        CREATE (bs)-[:CREATED_AGENT]->(a)
        
        RETURN a
        """
        
        await self.db.execute_query(query, {
            "agent_id": agent_id,
            "name": agent_name,
            "description": agent_description,
            "purpose": state_data.get("goal_description", agent_description),
            "tenant_id": self.tenant_id,
            "session_id": session_id
        }, write=True)
        
        # Skill'lere bağla
        for skill_id in skill_ids:
            await self.db.execute_query("""
                MATCH (a:AgentDefinition {id: $agent_id}), (s:Skill {id: $skill_id})
                MERGE (a)-[:HAS_SKILL {assigned_at: datetime(), enabled: true}]->(s)
            """, {"agent_id": agent_id, "skill_id": skill_id}, write=True)
        
        logger.info(f"Created agent {agent_id} from session {session_id}")
        
        return AgentDefinition(
            id=agent_id,
            name=agent_name,
            description=agent_description,
            purpose=state_data.get("goal_description", agent_description),
            status="draft",
            tenant_id=self.tenant_id
        )
    
    # =========================================================================
    # DEPLOYMENT
    # =========================================================================
    
    async def deploy_agent(self, agent_id: str) -> Dict[str, Any]:
        """
        Agent'ı MCP Gateway'e deploy et.
        
        Args:
            agent_id: Agent ID
            
        Returns:
            Deployment sonucu
        """
        # Agent kontrolü
        query = """
        MATCH (a:AgentDefinition {id: $agent_id, tenant_id: $tenant_id})
        RETURN a.status as status, a.mcp_virtual_server_id as vs_id
        """
        
        result = await self.db.execute_query(query, {
            "agent_id": agent_id,
            "tenant_id": self.tenant_id
        })
        
        if not result:
            return {"success": False, "message": "Agent bulunamadı"}
        
        if result[0].get("vs_id"):
            return {
                "success": True,
                "message": "Agent zaten deploy edilmiş",
                "virtual_server_id": result[0]["vs_id"]
            }
        
        # TODO: MCP Gateway API entegrasyonu
        # Virtual server oluştur
        
        vs_id = f"vs-{agent_id[-12:]}"
        
        # Agent'ı güncelle
        await self.db.execute_query("""
            MATCH (a:AgentDefinition {id: $agent_id})
            SET a.status = 'active',
                a.mcp_virtual_server_id = $vs_id,
                a.deployed_at = datetime()
        """, {"agent_id": agent_id, "vs_id": vs_id}, write=True)
        
        logger.info(f"Deployed agent {agent_id} to virtual server {vs_id}")
        
        return {
            "success": True,
            "message": "Agent başarıyla deploy edildi",
            "virtual_server_id": vs_id,
            "endpoint": f"/api/v2/agents/{agent_id}/process"
        }
