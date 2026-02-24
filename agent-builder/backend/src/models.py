"""
Agent Builder Models
====================

Pydantic modelleri - API request/response ve internal data transfer için.

Bu modül şu kategorileri içerir:
- Goal modelleri: Kullanıcı hedefleri
- Skill modelleri: Yetenekler ve prompt template'leri
- Schema modelleri: Entity ve Relationship tanımları
- Agent modelleri: Agent tanımları
- Session modelleri: Builder conversation state
- Request/Response modelleri: API iletişimi

Kullanım:
---------
    from backend.src.agent_builder.models import (
        Goal, GoalCreate,
        Skill, SkillCreate,
        AgentDefinition, AgentCreate
    )
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field, validator


# =============================================================================
# ENUMS
# =============================================================================

class GoalType(str, Enum):
    """Goal tipleri"""
    EXTRACTION = "extraction"      # Belgeden veri çıkarma
    ANALYSIS = "analysis"          # Veri analizi
    SEARCH = "search"              # Arama ve sorgulama
    TRANSFORMATION = "transformation"  # Veri dönüşümü
    SUB_GOAL = "sub_goal"          # Alt hedef


class GoalStatus(str, Enum):
    """Goal durumları"""
    ACTIVE = "active"
    ACHIEVED = "achieved"
    ABANDONED = "abandoned"


class SkillCategory(str, Enum):
    """Skill kategorileri"""
    OCR = "ocr"                          # Görüntüden metin çıkarma
    EXTRACTION = "extraction"            # Entity extraction
    RELATIONSHIP_MAPPING = "relationship_mapping"  # İlişki tespiti
    QUERY = "query"                      # Sorgulama
    WORKFLOW = "workflow"                # Multi-step workflow


class AgentStatus(str, Enum):
    """Agent durumları"""
    DRAFT = "draft"          # Oluşturuluyor
    ACTIVE = "active"        # Aktif ve kullanılabilir
    ARCHIVED = "archived"    # Arşivlenmiş


class SessionStatus(str, Enum):
    """Builder session durumları"""
    ACTIVE = "active"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class SessionState(str, Enum):
    """Builder conversation state'leri"""
    GOAL_ELICITATION = "goal_elicitation"
    GOAL_DECOMPOSITION = "goal_decomposition"
    ONTOLOGY_SEARCH = "ontology_search"
    SKILL_MATCH = "skill_match"
    SAMPLE_REQUEST = "sample_request"
    SAMPLE_ANALYSIS = "sample_analysis"
    SCHEMA_PROPOSAL = "schema_proposal"
    SCHEMA_REVIEW = "schema_review"
    SKILL_GENERATION = "skill_generation"
    SKILL_TEST = "skill_test"
    LEARNING_CAPTURE = "learning_capture"
    AGENT_ASSEMBLY = "agent_assembly"
    GATEWAY_DEPLOY = "gateway_deploy"


class LearningType(str, Enum):
    """Öğrenme tipleri"""
    SUCCESS_PATTERN = "success_pattern"
    FAILURE_PATTERN = "failure_pattern"
    CORRECTION = "correction"
    EDGE_CASE = "edge_case"


# =============================================================================
# BASE MODELS
# =============================================================================

class TimestampMixin(BaseModel):
    """Timestamp alanları için mixin"""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# =============================================================================
# GOAL MODELS
# =============================================================================

class GoalBase(BaseModel):
    """Goal temel alanları"""
    name: str = Field(..., min_length=2, max_length=200, description="Goal adı")
    description: str = Field(..., min_length=10, max_length=2000, description="Goal açıklaması")
    goal_type: GoalType = Field(default=GoalType.EXTRACTION, description="Goal tipi")
    natural_language_query: Optional[str] = Field(None, description="Kullanıcının orijinal sorgusu")
    success_criteria: Optional[Dict[str, Any]] = Field(None, description="Başarı kriterleri")


class GoalCreate(GoalBase):
    """Goal oluşturma request modeli"""
    tenant_id: str = Field(..., description="Tenant ID")
    parent_goal_id: Optional[str] = Field(None, description="Üst goal ID (sub-goal için)")
    context_id: Optional[str] = Field(None, description="Context ID")


class Goal(GoalBase, TimestampMixin):
    """Goal response modeli"""
    id: str = Field(..., description="Goal ID")
    status: GoalStatus = Field(default=GoalStatus.ACTIVE)
    tenant_id: Optional[str] = None
    embedding: Optional[List[float]] = Field(None, exclude=True)  # API'de gösterme
    
    class Config:
        from_attributes = True


class GoalWithSkills(Goal):
    """Skill bilgileriyle birlikte Goal"""
    achievable_skills: List["SkillSummary"] = Field(default_factory=list)
    sub_goals: List["Goal"] = Field(default_factory=list)


# =============================================================================
# SKILL MODELS
# =============================================================================

class PropertySchema(BaseModel):
    """Entity property şema tanımı"""
    type: str = Field(default="string", description="Property tipi (string, int, float, date, etc.)")
    required: bool = Field(default=False, description="Zorunlu mu")
    description: Optional[str] = Field(None, description="Property açıklaması")
    validation: Optional[str] = Field(None, description="Validation regex/rule")
    examples: Optional[List[str]] = Field(None, description="Örnek değerler")


class SkillBase(BaseModel):
    """Skill temel alanları"""
    name: str = Field(..., min_length=2, max_length=200, description="Skill adı")
    description: str = Field(..., min_length=10, max_length=2000, description="Skill açıklaması")
    skill_category: SkillCategory = Field(..., description="Skill kategorisi")
    prompt_template: str = Field(..., min_length=10, description="LLM prompt şablonu (Jinja2)")
    
    # Input/Output schemas (JSON Schema format)
    input_schema: Optional[Dict[str, Any]] = Field(None, description="Input JSON Schema")
    output_schema: Optional[Dict[str, Any]] = Field(None, description="Output JSON Schema")


class SkillCreate(SkillBase):
    """Skill oluşturma request modeli"""
    tenant_id: Optional[str] = Field(None, description="Tenant ID (null ise global)")
    is_global: bool = Field(default=False, description="Global skill mi")
    context_ids: List[str] = Field(default_factory=list, description="Geçerli context ID'leri")
    depends_on: List[str] = Field(default_factory=list, description="Bağımlı skill ID'leri")
    entity_schema_ids: List[str] = Field(default_factory=list, description="Çıkardığı entity schema ID'leri")


class Skill(SkillBase, TimestampMixin):
    """Skill response modeli"""
    id: str = Field(..., description="Skill ID")
    version: int = Field(default=1, description="Skill versiyonu")
    effectiveness_score: float = Field(default=0.5, ge=0.0, le=1.0, description="Başarı oranı")
    usage_count: int = Field(default=0, ge=0, description="Kullanım sayısı")
    is_global: bool = Field(default=False)
    tenant_id: Optional[str] = None
    
    class Config:
        from_attributes = True


class SkillSummary(BaseModel):
    """Skill özet bilgileri (listeler için)"""
    id: str
    name: str
    description: str
    category: SkillCategory
    effectiveness_score: float
    is_global: bool


class SkillWithDependencies(Skill):
    """Dependency bilgileriyle birlikte Skill"""
    dependencies: List[SkillSummary] = Field(default_factory=list)
    extracts_entities: List["EntitySchemaSummary"] = Field(default_factory=list)
    creates_relationships: List["RelationshipSchemaSummary"] = Field(default_factory=list)


# =============================================================================
# ENTITY SCHEMA MODELS
# =============================================================================

class EntitySchemaBase(BaseModel):
    """EntitySchema temel alanları"""
    entity_type: str = Field(..., min_length=2, max_length=100, description="Entity tipi adı")
    description: str = Field(..., min_length=5, max_length=1000, description="Entity açıklaması")
    properties: Dict[str, PropertySchema] = Field(..., description="Property tanımları")
    validation_rules: Optional[Dict[str, str]] = Field(None, description="Validation kuralları")
    examples: Optional[List[Dict[str, Any]]] = Field(None, description="Örnek entity'ler")


class EntitySchemaCreate(EntitySchemaBase):
    """EntitySchema oluşturma request modeli"""
    context: str = Field(..., description="Context adı (insurance, maintenance, etc.)")


class EntitySchema(EntitySchemaBase, TimestampMixin):
    """EntitySchema response modeli"""
    id: str = Field(..., description="Schema ID")
    context: str
    
    class Config:
        from_attributes = True


class EntitySchemaSummary(BaseModel):
    """EntitySchema özet bilgileri"""
    id: str
    entity_type: str
    description: str
    property_count: int


# =============================================================================
# RELATIONSHIP SCHEMA MODELS
# =============================================================================

class Cardinality(str, Enum):
    """İlişki kardinalitesi"""
    ONE_TO_ONE = "1:1"
    ONE_TO_MANY = "1:N"
    MANY_TO_MANY = "N:M"


class RelationshipSchemaBase(BaseModel):
    """RelationshipSchema temel alanları"""
    relationship_type: str = Field(..., min_length=2, max_length=100, description="İlişki tipi adı")
    description: str = Field(..., min_length=5, max_length=1000, description="İlişki açıklaması")
    source_entity: str = Field(..., description="Kaynak entity tipi")
    target_entity: str = Field(..., description="Hedef entity tipi")
    properties: Optional[Dict[str, PropertySchema]] = Field(None, description="İlişki property'leri")
    cardinality: Cardinality = Field(default=Cardinality.ONE_TO_MANY)
    bidirectional: bool = Field(default=False, description="Çift yönlü mü")


class RelationshipSchemaCreate(RelationshipSchemaBase):
    """RelationshipSchema oluşturma request modeli"""


class RelationshipSchema(RelationshipSchemaBase, TimestampMixin):
    """RelationshipSchema response modeli"""
    id: str = Field(..., description="Schema ID")
    
    class Config:
        from_attributes = True


class RelationshipSchemaSummary(BaseModel):
    """RelationshipSchema özet bilgileri"""
    id: str
    relationship_type: str
    source_entity: str
    target_entity: str


# =============================================================================
# AGENT MODELS
# =============================================================================

class AgentBase(BaseModel):
    """AgentDefinition temel alanları"""
    name: str = Field(..., min_length=2, max_length=200, description="Agent adı")
    description: str = Field(..., min_length=10, max_length=2000, description="Agent açıklaması")
    purpose: str = Field(..., min_length=10, max_length=4000, description="Detaylı amaç açıklaması")


class AgentCreate(AgentBase):
    """Agent oluşturma request modeli"""
    tenant_id: str = Field(..., description="Tenant ID")
    goal_ids: List[str] = Field(..., min_items=1, description="Agent'ın hedefleri")
    skill_ids: List[str] = Field(..., min_items=1, description="Agent'a atanan skill'ler")
    entity_schema_ids: List[str] = Field(default_factory=list, description="Kullanılan entity schema'ları")
    context_id: Optional[str] = Field(None, description="Çalıştığı context")


class AgentDefinition(AgentBase, TimestampMixin):
    """AgentDefinition response modeli"""
    id: str = Field(..., description="Agent ID")
    status: AgentStatus = Field(default=AgentStatus.DRAFT)
    tenant_id: str
    mcp_virtual_server_id: Optional[str] = Field(None, description="MCP Gateway virtual server ID")
    deployed_at: Optional[datetime] = None
    config: Optional[Dict[str, Any]] = None
    
    class Config:
        from_attributes = True


class AgentSummary(BaseModel):
    """Agent özet bilgileri"""
    id: str
    name: str
    description: str
    status: AgentStatus
    skill_count: int
    deployed: bool


class GoalSummary(BaseModel):
    """Goal özet bilgileri"""
    id: str
    name: str
    description: str
    goal_type: GoalType


class AgentWithDetails(AgentDefinition):
    """Detaylı Agent bilgileri"""
    goals: List[GoalSummary] = Field(default_factory=list)
    skills: List[SkillSummary] = Field(default_factory=list)
    entity_schemas: List[EntitySchemaSummary] = Field(default_factory=list)


# =============================================================================
# CONTEXT MODELS
# =============================================================================

class ContextBase(BaseModel):
    """Context temel alanları"""
    name: str = Field(..., min_length=2, max_length=100)
    description: str = Field(..., min_length=5, max_length=500)
    domain_keywords: List[str] = Field(default_factory=list)


class Context(ContextBase):
    """Context response modeli"""
    id: str
    parent_context_id: Optional[str] = None
    
    class Config:
        from_attributes = True


# =============================================================================
# SESSION MODELS
# =============================================================================

class BuilderSessionCreate(BaseModel):
    """Builder session oluşturma request modeli"""
    tenant_id: str = Field(..., description="Tenant ID")


class BuilderSession(BaseModel):
    """Builder session response modeli"""
    id: str = Field(..., description="Session ID")
    tenant_id: str
    user_id: Optional[str] = None
    status: SessionStatus = Field(default=SessionStatus.ACTIVE)
    current_state: SessionState = Field(default=SessionState.GOAL_ELICITATION)
    state_data: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True


class BuilderMessage(BaseModel):
    """Builder conversation mesajı"""
    role: str = Field(..., description="Rol: user veya assistant")
    content: str = Field(..., description="Mesaj içeriği")
    timestamp: datetime = Field(default_factory=datetime.now)
    metadata: Optional[Dict[str, Any]] = None


class BuilderChatRequest(BaseModel):
    """Builder chat request modeli"""
    message: str = Field(..., min_length=1, max_length=10000, description="Kullanıcı mesajı")


class BuilderChatResponse(BaseModel):
    """Builder chat response modeli"""
    message: str = Field(..., description="Assistant yanıtı")
    state: SessionState = Field(..., description="Mevcut state")
    state_data: Optional[Dict[str, Any]] = Field(None, description="State-specific veriler")
    action_required: Optional[str] = Field(None, description="Kullanıcıdan beklenen aksiyon")
    options: Optional[List[Dict[str, str]]] = Field(None, description="Seçenekler (varsa)")


# =============================================================================
# LEARNING MODELS
# =============================================================================

class LearningCreate(BaseModel):
    """Learning oluşturma request modeli"""
    skill_id: str = Field(..., description="İlgili skill ID")
    learning_type: LearningType = Field(..., description="Öğrenme tipi")
    description: str = Field(..., min_length=10, max_length=2000)
    example_input: Optional[str] = None
    expected_output: Optional[str] = None
    actual_output: Optional[str] = None
    correction: Optional[str] = None


class Learning(LearningCreate, TimestampMixin):
    """Learning response modeli"""
    id: str
    tenant_id: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    
    class Config:
        from_attributes = True


# =============================================================================
# API REQUEST/RESPONSE MODELS
# =============================================================================

class SampleUploadResponse(BaseModel):
    """Örnek belge yükleme response"""
    sample_ids: List[str] = Field(..., description="Yüklenen örnek ID'leri")
    analysis_started: bool = Field(default=True)
    message: str


class SchemaProposalResponse(BaseModel):
    """Schema önerisi response"""
    entities: List[Dict[str, Any]] = Field(..., description="Entity schema önerileri")
    relationships: List[Dict[str, Any]] = Field(..., description="Relationship schema önerileri")
    confidence: float = Field(..., ge=0.0, le=1.0)
    based_on: List[str] = Field(default_factory=list, description="Baz alınan mevcut schema ID'leri")
    reasoning: str = Field(..., description="Öneri gerekçesi")


class SkillTestRequest(BaseModel):
    """Skill test request modeli"""
    document_ids: List[str] = Field(..., min_items=1, description="Test belgesi ID'leri")


class SkillTestResult(BaseModel):
    """Skill test sonucu"""
    success: bool
    extracted_entities: List[Dict[str, Any]] = Field(default_factory=list)
    extracted_relationships: List[Dict[str, Any]] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    execution_time_ms: int


class DeploymentRequest(BaseModel):
    """Agent deployment request modeli"""
    agent_id: str = Field(..., description="Deploy edilecek agent ID")


class DeploymentResult(BaseModel):
    """Agent deployment sonucu"""
    success: bool
    agent_id: str
    mcp_virtual_server_id: Optional[str] = None
    endpoint_url: Optional[str] = None
    message: str


class ProcessRequest(BaseModel):
    """Agent ile işleme request modeli"""
    file_ids: List[str] = Field(..., min_items=1, description="İşlenecek dosya ID'leri")


class ProcessResult(BaseModel):
    """Agent ile işleme sonucu"""
    task_id: str = Field(..., description="Celery task ID")
    status: str = Field(default="queued")
    message: str


# =============================================================================
# FORWARD REFERENCES
# =============================================================================

# Pydantic v2 için forward reference'ları güncelle
GoalWithSkills.model_rebuild()
SkillWithDependencies.model_rebuild()
AgentWithDetails.model_rebuild()
