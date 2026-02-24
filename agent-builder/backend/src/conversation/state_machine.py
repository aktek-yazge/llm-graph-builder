"""
Conversation State Machine
==========================

Agent Builder conversation akışını yöneten state machine.

Her state, kullanıcı mesajını işler ve bir sonraki state'e geçiş yapar.
Goal-driven workflow ile agent oluşturma sürecini yönetir.

State Akışı:
------------
1. goal_elicitation: Kullanıcı hedefini belirler
2. ontology_search: Mevcut skill'ler aranır
3. skill_match: Uygun skill'ler önerilir
4. sample_request: Örnek belgeler istenir
5. sample_analysis: Belgeler analiz edilir
6. schema_proposal: Entity schema önerilir
7. schema_review: Kullanıcı schema'yı onaylar
8. skill_generation: Yeni skill oluşturulur
9. skill_test: Skill test edilir
10. learning_capture: Feedback kaydedilir
11. agent_assembly: Agent oluşturulur
12. gateway_deploy: MCP Gateway'e deploy edilir

Kullanım:
---------
    from backend.src.agent_builder.conversation import ConversationStateMachine
    
    machine = ConversationStateMachine(db, reasoner)
    response = await machine.process_message(session_id, "Sigorta belgeleri işlemek istiyorum")
"""

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..models import (
    BuilderSession,
    BuilderChatResponse,
    SessionState,
    SessionStatus,
)
from ..ontology.neo4j_client import OntologyDBClient
from ..ontology.reasoner import OntologyReasoner

logger = logging.getLogger(__name__)


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class StateContext:
    """
    State handler'a geçirilen context.
    Session bilgileri ve yardımcı servisler içerir.
    """
    session_id: str
    tenant_id: str
    user_message: str
    current_state: SessionState
    state_data: Dict[str, Any]
    db: OntologyDBClient
    reasoner: OntologyReasoner
    
    # State handler tarafından güncellenir
    response_message: str = ""
    next_state: Optional[SessionState] = None
    next_state_data: Optional[Dict[str, Any]] = None
    action_required: Optional[str] = None
    options: Optional[List[Dict[str, str]]] = None


@dataclass
class StateTransition:
    """State geçiş tanımı"""
    from_state: SessionState
    to_state: SessionState
    condition: Optional[Callable[[StateContext], bool]] = None
    description: str = ""


# =============================================================================
# STATE HANDLER BASE
# =============================================================================

class StateHandler(ABC):
    """
    State handler base class.
    Her state için bir handler oluşturulur.
    """
    
    @property
    @abstractmethod
    def state(self) -> SessionState:
        """Bu handler'ın yönettiği state"""
        pass
    
    @abstractmethod
    async def handle(self, ctx: StateContext) -> None:
        """
        Kullanıcı mesajını işle ve context'i güncelle.
        
        Args:
            ctx: State context - response ve next_state güncellenir
        """
        pass
    
    @property
    def allowed_transitions(self) -> List[SessionState]:
        """Bu state'ten geçilebilecek state'ler"""
        return []


# =============================================================================
# CONCRETE STATE HANDLERS
# =============================================================================

class GoalElicitationHandler(StateHandler):
    """
    İlk state - kullanıcı hedefini belirler.
    
    Kullanıcıdan ne yapmak istediğini sorar,
    hedefi anlar ve formalize eder.
    """
    
    @property
    def state(self) -> SessionState:
        return SessionState.GOAL_ELICITATION
    
    @property
    def allowed_transitions(self) -> List[SessionState]:
        return [SessionState.ONTOLOGY_SEARCH, SessionState.GOAL_DECOMPOSITION]
    
    async def handle(self, ctx: StateContext) -> None:
        user_input = ctx.user_message.strip()
        
        # İlk mesaj - karşılama
        if not ctx.state_data.get("greeted"):
            ctx.response_message = (
                "Merhaba! Ben Agent Builder asistanınızım. "
                "Size özel bir belge işleme agent'ı oluşturmamıza yardımcı olacağım.\n\n"
                "Lütfen ne tür belgelerle çalışmak istediğinizi ve "
                "bu belgelerden ne tür bilgiler çıkarmak istediğinizi anlatın.\n\n"
                "Örnek: 'Sigorta poliçelerinden müşteri ve teminat bilgilerini çıkarmak istiyorum.'"
            )
            ctx.next_state_data = {"greeted": True}
            ctx.action_required = "Hedefinizi açıklayın"
            return
        
        # Kullanıcı hedefini anlat
        if len(user_input) < 10:
            ctx.response_message = (
                "Hedefinizi biraz daha detaylı açıklar mısınız? "
                "Hangi tür belgeler? Ne tür bilgiler çıkarmak istiyorsunuz?"
            )
            ctx.action_required = "Daha detaylı açıklama"
            return
        
        # Hedef kaydedildi, ontology'de ara
        ctx.state_data["goal_description"] = user_input
        ctx.next_state = SessionState.ONTOLOGY_SEARCH
        ctx.next_state_data = {
            "goal_description": user_input,
            "search_pending": True
        }
        ctx.response_message = (
            f"Anladım: {user_input[:100]}...\n\n"
            "Mevcut skill ve şemaları kontrol ediyorum..."
        )


class OntologySearchHandler(StateHandler):
    """
    Ontology'de mevcut skill ve schema arar.
    """
    
    @property
    def state(self) -> SessionState:
        return SessionState.ONTOLOGY_SEARCH
    
    @property
    def allowed_transitions(self) -> List[SessionState]:
        return [SessionState.SKILL_MATCH, SessionState.SAMPLE_REQUEST]
    
    async def handle(self, ctx: StateContext) -> None:
        goal_desc = ctx.state_data.get("goal_description", "")
        
        # Context tahmin et
        context_name = self._detect_context(goal_desc)
        
        # Skill'leri ara
        skills = await ctx.reasoner.find_skills_by_context(
            context_name=context_name,
            tenant_id=ctx.tenant_id,
            limit=5
        )
        
        if skills:
            ctx.next_state = SessionState.SKILL_MATCH
            ctx.next_state_data = {
                **ctx.state_data,
                "found_skills": [s.to_dict() for s in skills],
                "detected_context": context_name
            }
            
            skill_list = "\n".join([f"- {s.name}: {s.description[:60]}..." for s in skills[:3]])
            ctx.response_message = (
                f"'{context_name}' bağlamında {len(skills)} mevcut skill buldum:\n\n"
                f"{skill_list}\n\n"
                "Bu skill'lerden birini kullanmak ister misiniz, "
                "yoksa yeni bir skill oluşturalım mı?"
            )
            ctx.options = [
                {"id": "use_existing", "label": "Mevcut skill kullan"},
                {"id": "create_new", "label": "Yeni skill oluştur"}
            ]
        else:
            # Skill bulunamadı, örnek belge iste
            ctx.next_state = SessionState.SAMPLE_REQUEST
            ctx.next_state_data = {
                **ctx.state_data,
                "detected_context": context_name,
                "no_existing_skills": True
            }
            ctx.response_message = (
                f"'{context_name}' bağlamında mevcut skill bulamadım. "
                "Yeni bir skill oluşturacağız.\n\n"
                "Lütfen 3-5 örnek belge yükleyin. "
                "Bunları analiz ederek size uygun bir schema önereceğim."
            )
            ctx.action_required = "Örnek belge yükleyin"
    
    def _detect_context(self, text: str) -> str:
        """Metinden context tahmin et"""
        text_lower = text.lower()
        
        context_keywords = {
            "insurance": ["sigorta", "poliçe", "teminat", "prim", "hasar"],
            "maintenance": ["bakım", "arıza", "ekipman", "servis", "teknik"],
            "legal": ["sözleşme", "hukuk", "madde", "taraf", "mahkeme"],
            "financial": ["fatura", "ödeme", "vergi", "banka", "finans"]
        }
        
        for context, keywords in context_keywords.items():
            if any(kw in text_lower for kw in keywords):
                return context
        
        return "document_processing"


class SkillMatchHandler(StateHandler):
    """
    Mevcut skill'ler arasından seçim yapar.
    """
    
    @property
    def state(self) -> SessionState:
        return SessionState.SKILL_MATCH
    
    @property
    def allowed_transitions(self) -> List[SessionState]:
        return [SessionState.AGENT_ASSEMBLY, SessionState.SAMPLE_REQUEST, SessionState.SKILL_GENERATION]
    
    async def handle(self, ctx: StateContext) -> None:
        user_choice = ctx.user_message.strip().lower()
        
        if "mevcut" in user_choice or "kullan" in user_choice or "1" in user_choice:
            # Mevcut skill kullan
            skills = ctx.state_data.get("found_skills", [])
            if skills:
                ctx.next_state = SessionState.AGENT_ASSEMBLY
                ctx.next_state_data = {
                    **ctx.state_data,
                    "selected_skill_id": skills[0]["skill_id"],
                    "selected_skill_name": skills[0]["name"]
                }
                ctx.response_message = (
                    f"'{skills[0]['name']}' skill'ini kullanacağız.\n\n"
                    "Agent'a bir isim vermek ister misiniz?"
                )
                ctx.action_required = "Agent ismi girin"
        else:
            # Yeni skill oluştur
            ctx.next_state = SessionState.SAMPLE_REQUEST
            ctx.next_state_data = {
                **ctx.state_data,
                "create_new_skill": True
            }
            ctx.response_message = (
                "Yeni bir skill oluşturacağız.\n\n"
                "Lütfen 3-5 örnek belge yükleyin. "
                "Bunları analiz ederek size uygun bir schema önereceğim."
            )
            ctx.action_required = "Örnek belge yükleyin"


class SampleRequestHandler(StateHandler):
    """
    Örnek belge yüklenmesini bekler.
    """
    
    @property
    def state(self) -> SessionState:
        return SessionState.SAMPLE_REQUEST
    
    @property
    def allowed_transitions(self) -> List[SessionState]:
        return [SessionState.SAMPLE_ANALYSIS]
    
    async def handle(self, ctx: StateContext) -> None:
        # Dosya yükleme durumu kontrol et
        uploaded_samples = ctx.state_data.get("uploaded_sample_ids", [])
        
        if "yükledim" in ctx.user_message.lower() or "hazır" in ctx.user_message.lower():
            if uploaded_samples:
                ctx.next_state = SessionState.SAMPLE_ANALYSIS
                ctx.next_state_data = {
                    **ctx.state_data,
                    "analysis_pending": True
                }
                ctx.response_message = (
                    f"{len(uploaded_samples)} örnek belge analiz ediliyor...\n"
                    "Bu birkaç dakika sürebilir."
                )
            else:
                ctx.response_message = (
                    "Henüz yüklenmiş örnek belge göremiyorum. "
                    "Lütfen yukarıdaki yükleme alanını kullanarak dosya yükleyin."
                )
                ctx.action_required = "Örnek belge yükleyin"
        else:
            ctx.response_message = (
                "Örnek belgelerinizi yukarıdaki alanda yükleyebilirsiniz.\n"
                "Yükledikten sonra 'Yükledim' yazarak devam edebilirsiniz."
            )
            ctx.action_required = "Örnek belge yükleyin"


class SchemaProposalHandler(StateHandler):
    """
    Analiz sonrasında schema önerisi yapar.
    """
    
    @property
    def state(self) -> SessionState:
        return SessionState.SCHEMA_PROPOSAL
    
    @property
    def allowed_transitions(self) -> List[SessionState]:
        return [SessionState.SCHEMA_REVIEW, SessionState.SAMPLE_ANALYSIS]
    
    async def handle(self, ctx: StateContext) -> None:
        # Schema önerisini göster
        proposal = ctx.state_data.get("schema_proposal", {})
        entities = proposal.get("entities", [])
        relationships = proposal.get("relationships", [])
        
        entity_list = "\n".join([f"- {e['entity_type']}: {e['description'][:50]}..." for e in entities[:5]])
        rel_list = "\n".join([f"- {r['source_entity']} → {r['relationship_type']} → {r['target_entity']}" for r in relationships[:3]])
        
        ctx.response_message = (
            "Örnek belgelerinizi analiz ettim. İşte schema önerim:\n\n"
            f"**Entity'ler:**\n{entity_list}\n\n"
            f"**İlişkiler:**\n{rel_list}\n\n"
            "Bu schema uygun mu? Değişiklik yapmak ister misiniz?"
        )
        ctx.options = [
            {"id": "approve", "label": "Onayla"},
            {"id": "modify", "label": "Değiştir"},
            {"id": "regenerate", "label": "Yeniden oluştur"}
        ]
        ctx.next_state = SessionState.SCHEMA_REVIEW
        ctx.next_state_data = ctx.state_data


class AgentAssemblyHandler(StateHandler):
    """
    Agent'ı oluşturur.
    """
    
    @property
    def state(self) -> SessionState:
        return SessionState.AGENT_ASSEMBLY
    
    @property
    def allowed_transitions(self) -> List[SessionState]:
        return [SessionState.GATEWAY_DEPLOY]
    
    async def handle(self, ctx: StateContext) -> None:
        agent_name = ctx.user_message.strip()
        
        if len(agent_name) < 3:
            ctx.response_message = "Lütfen agent için en az 3 karakterlik bir isim girin."
            ctx.action_required = "Agent ismi girin"
            return
        
        # Agent oluştur (basit versiyon)
        import uuid
        agent_id = f"agent-{uuid.uuid4().hex[:12]}"
        
        ctx.state_data["agent_id"] = agent_id
        ctx.state_data["agent_name"] = agent_name
        
        ctx.next_state = SessionState.GATEWAY_DEPLOY
        ctx.next_state_data = ctx.state_data
        ctx.response_message = (
            f"✅ '{agent_name}' agent'ı oluşturuldu!\n\n"
            "Agent'ı MCP Gateway'e deploy etmek ister misiniz?"
        )
        ctx.options = [
            {"id": "deploy", "label": "Deploy et"},
            {"id": "later", "label": "Daha sonra"}
        ]


class GatewayDeployHandler(StateHandler):
    """
    Agent'ı MCP Gateway'e deploy eder.
    """
    
    @property
    def state(self) -> SessionState:
        return SessionState.GATEWAY_DEPLOY
    
    @property
    def allowed_transitions(self) -> List[SessionState]:
        return []  # Final state
    
    async def handle(self, ctx: StateContext) -> None:
        user_choice = ctx.user_message.strip().lower()
        
        if "deploy" in user_choice or "evet" in user_choice or "1" in user_choice:
            agent_name = ctx.state_data.get("agent_name", "Agent")
            agent_id = ctx.state_data.get("agent_id", "")
            
            ctx.response_message = (
                f"🚀 '{agent_name}' MCP Gateway'e deploy edildi!\n\n"
                f"**Agent ID:** {agent_id}\n"
                f"**Endpoint:** /api/v2/agents/{agent_id}/process\n\n"
                "Artık belgelerinizi bu agent ile işleyebilirsiniz. "
                "Yeni bir agent oluşturmak için yeni bir session başlatın."
            )
            
            # Session'ı tamamla
            await ctx.db.execute_query("""
                MATCH (bs:BuilderSession {id: $session_id})
                SET bs.status = 'completed',
                    bs.completed_at = datetime()
            """, {"session_id": ctx.session_id}, write=True)
        else:
            ctx.response_message = (
                "Agent draft olarak kaydedildi. "
                "İstediğiniz zaman /api/v2/agent-builder/agents/{id}/deploy "
                "endpoint'i ile deploy edebilirsiniz."
            )


# =============================================================================
# STATE MACHINE
# =============================================================================

class ConversationStateMachine:
    """
    Agent Builder conversation state machine.
    
    Session bazlı conversation yönetimi.
    Her mesaj için uygun handler'ı çağırır ve state geçişlerini yönetir.
    """
    
    def __init__(self, db: OntologyDBClient, reasoner: OntologyReasoner):
        self.db = db
        self.reasoner = reasoner
        
        # Handler registry
        self._handlers: Dict[SessionState, StateHandler] = {}
        self._register_default_handlers()
    
    def _register_default_handlers(self) -> None:
        """Varsayılan handler'ları kaydet"""
        handlers = [
            GoalElicitationHandler(),
            OntologySearchHandler(),
            SkillMatchHandler(),
            SampleRequestHandler(),
            SchemaProposalHandler(),
            AgentAssemblyHandler(),
            GatewayDeployHandler(),
        ]
        
        for handler in handlers:
            self._handlers[handler.state] = handler
    
    def register_handler(self, handler: StateHandler) -> None:
        """Özel handler kaydet"""
        self._handlers[handler.state] = handler
    
    async def process_message(
        self,
        session_id: str,
        user_message: str
    ) -> BuilderChatResponse:
        """
        Kullanıcı mesajını işle ve yanıt döndür.
        
        Args:
            session_id: Session ID
            user_message: Kullanıcı mesajı
            
        Returns:
            BuilderChatResponse
        """
        # Session'ı getir
        session = await self._get_session(session_id)
        
        if not session:
            return BuilderChatResponse(
                message="Session bulunamadı.",
                state=SessionState.GOAL_ELICITATION,
                action_required="Yeni session oluşturun"
            )
        
        # Context oluştur
        ctx = StateContext(
            session_id=session_id,
            tenant_id=session["tenant_id"],
            user_message=user_message,
            current_state=SessionState(session["current_state"]),
            state_data=json.loads(session.get("state_data", "{}")),
            db=self.db,
            reasoner=self.reasoner
        )
        
        # Handler'ı bul ve çalıştır
        handler = self._handlers.get(ctx.current_state)
        
        if not handler:
            return BuilderChatResponse(
                message=f"Bu state için handler bulunamadı: {ctx.current_state}",
                state=ctx.current_state
            )
        
        try:
            await handler.handle(ctx)
        except Exception as e:
            logger.error(f"Handler error in {ctx.current_state}: {e}")
            return BuilderChatResponse(
                message=f"İşlem sırasında hata oluştu: {str(e)}",
                state=ctx.current_state
            )
        
        # State güncelle
        new_state = ctx.next_state or ctx.current_state
        new_state_data = ctx.next_state_data or ctx.state_data
        
        await self._update_session(
            session_id=session_id,
            state=new_state,
            state_data=new_state_data,
            user_message=user_message,
            assistant_message=ctx.response_message
        )
        
        return BuilderChatResponse(
            message=ctx.response_message,
            state=new_state,
            state_data=new_state_data,
            action_required=ctx.action_required,
            options=ctx.options
        )
    
    async def _get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Session bilgilerini getir"""
        result = await self.db.execute_query("""
            MATCH (bs:BuilderSession {id: $session_id})
            RETURN bs.tenant_id as tenant_id,
                   bs.current_state as current_state,
                   bs.state_data as state_data,
                   bs.status as status
        """, {"session_id": session_id})
        
        return result[0] if result else None
    
    async def _update_session(
        self,
        session_id: str,
        state: SessionState,
        state_data: Dict[str, Any],
        user_message: str,
        assistant_message: str
    ) -> None:
        """Session'ı güncelle"""
        await self.db.execute_query("""
            MATCH (bs:BuilderSession {id: $session_id})
            SET bs.current_state = $state,
                bs.state_data = $state_data,
                bs.updated_at = datetime()
        """, {
            "session_id": session_id,
            "state": state.value,
            "state_data": json.dumps(state_data, ensure_ascii=False)
        }, write=True)
