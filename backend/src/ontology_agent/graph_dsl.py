"""
Graph DSL (Domain Specific Language) Spesifikasyonu

Bu modül, LLM'in Neo4j sorguları için üretebileceği basit bir DSL tanımlar.
DSL, Cypher'dan çok daha basit ve hata yapmaya daha az müsait bir yapıdır.

Örnek DSL:
{
    "intent": "find_policies_by_customer",
    "description": "Müşterinin poliçelerini bul",
    "traversal": [
        {"from": "Customer", "relation": "HAS_POLICY", "to": "Policy"}
    ],
    "filters": [
        {"node": "Customer", "property": "name", "operator": "contains", "value": "ahmet"}
    ],
    "return_spec": {
        "nodes": ["Policy"],
        "properties": {"Policy": ["name", "policyNumber", "status"]}
    },
    "aggregate": null,
    "limit": 10,
    "order_by": {"node": "Policy", "property": "createdAt", "direction": "DESC"}
}

Bu DSL'den üretilen Cypher:
MATCH (customer:Customer)-[:HAS_POLICY]->(policy:Policy)
WHERE toLower(customer.name) CONTAINS 'ahmet'
RETURN DISTINCT policy.name, policy.policyNumber, policy.status
ORDER BY policy.createdAt DESC
LIMIT 10
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Literal
from enum import Enum
import json


class FilterOperator(str, Enum):
    """Desteklenen filtre operatörleri"""
    EQUALS = "equals"           # Tam eşleşme
    NOT_EQUALS = "not_equals"   # Eşit değil
    CONTAINS = "contains"       # İçerir (case-insensitive)
    STARTS_WITH = "starts_with" # İle başlar
    ENDS_WITH = "ends_with"     # İle biter
    GREATER_THAN = "gt"         # Büyüktür
    LESS_THAN = "lt"            # Küçüktür
    GREATER_EQUAL = "gte"       # Büyük eşit
    LESS_EQUAL = "lte"          # Küçük eşit
    IN = "in"                   # Liste içinde
    IS_NULL = "is_null"         # Null mu
    IS_NOT_NULL = "is_not_null" # Null değil mi
    REGEX = "regex"             # Regex pattern


class AggregateFunction(str, Enum):
    """Desteklenen aggregate fonksiyonları"""
    COUNT = "count"
    SUM = "sum"
    AVG = "avg"
    MIN = "min"
    MAX = "max"
    COLLECT = "collect"


class QueryIntent(str, Enum):
    """Sorgu niyetleri - LLM bunlardan birini seçer"""
    # Keşif (Discovery)
    EXPLORE_SCHEMA = "explore_schema"          # Şema keşfi
    EXPLORE_NODE = "explore_node"              # Node örneklerini gör
    EXPLORE_RELATIONSHIP = "explore_relationship"  # İlişki örneklerini gör
    
    # Arama (Search)
    FIND_BY_PROPERTY = "find_by_property"      # Property ile ara
    FIND_BY_RELATIONSHIP = "find_by_relationship"  # İlişki ile ara
    FIND_PATH = "find_path"                    # İki node arası yol
    
    # İçerik (Content)
    SEARCH_CONTENT = "search_content"          # Chunk içerik araması (semantic)
    SEARCH_TEXT = "search_text"                # Text-based içerik araması
    
    # Aggregation
    COUNT_NODES = "count_nodes"                # Node say
    AGGREGATE_VALUES = "aggregate_values"      # Değerleri topla/ortala
    GROUP_BY = "group_by"                      # Gruplama


@dataclass
class TraversalStep:
    """
    Bir graph traversal adımı
    
    Örnek: Customer -> HAS_POLICY -> Policy
    {
        "from_node": "Customer",
        "relation": "HAS_POLICY", 
        "to_node": "Policy",
        "direction": "outgoing"  # veya "incoming", "both"
    }
    """
    from_node: str                              # Başlangıç node label
    relation: str                               # İlişki tipi
    to_node: str                                # Hedef node label
    direction: Literal["outgoing", "incoming", "both"] = "outgoing"
    relation_alias: Optional[str] = None        # İlişki için alias (r, r1, r2...)
    optional: bool = False                      # OPTIONAL MATCH mi?
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "from_node": self.from_node,
            "relation": self.relation,
            "to_node": self.to_node,
            "direction": self.direction,
            "relation_alias": self.relation_alias,
            "optional": self.optional
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TraversalStep":
        return cls(
            from_node=data["from_node"],
            relation=data["relation"],
            to_node=data["to_node"],
            direction=data.get("direction", "outgoing"),
            relation_alias=data.get("relation_alias"),
            optional=data.get("optional", False)
        )


@dataclass
class Filter:
    """
    Bir filtre koşulu
    
    Örnek: Customer.name contains "ahmet"
    {
        "node": "Customer",
        "property": "name",
        "operator": "contains",
        "value": "ahmet"
    }
    """
    node: str                                   # Node label veya alias
    property: str                               # Property adı
    operator: FilterOperator                    # Operatör
    value: Any                                  # Değer
    case_sensitive: bool = False                # Büyük/küçük harf duyarlı mı?
    logical_op: Literal["AND", "OR"] = "AND"    # Önceki filtre ile birleşim
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "node": self.node,
            "property": self.property,
            "operator": self.operator.value if isinstance(self.operator, FilterOperator) else self.operator,
            "value": self.value,
            "case_sensitive": self.case_sensitive,
            "logical_op": self.logical_op
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Filter":
        return cls(
            node=data["node"],
            property=data["property"],
            operator=FilterOperator(data["operator"]) if isinstance(data["operator"], str) else data["operator"],
            value=data["value"],
            case_sensitive=data.get("case_sensitive", False),
            logical_op=data.get("logical_op", "AND")
        )


@dataclass  
class ReturnSpec:
    """
    Return spesifikasyonu
    
    Örnek: Policy node'undan name, policyNumber, status döndür
    {
        "nodes": ["Policy"],
        "properties": {
            "Policy": ["name", "policyNumber", "status"]
        },
        "distinct": true
    }
    """
    nodes: List[str] = field(default_factory=list)           # Hangi node'lar
    properties: Dict[str, List[str]] = field(default_factory=dict)  # Node -> properties
    distinct: bool = True                                     # DISTINCT kullan mı?
    aliases: Dict[str, str] = field(default_factory=dict)     # Property alias'ları
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "nodes": self.nodes,
            "properties": self.properties,
            "distinct": self.distinct,
            "aliases": self.aliases
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReturnSpec":
        return cls(
            nodes=data.get("nodes", []),
            properties=data.get("properties", {}),
            distinct=data.get("distinct", True),
            aliases=data.get("aliases", {})
        )


@dataclass
class AggregateSpec:
    """
    Aggregation spesifikasyonu
    
    Örnek: Poliçelerin toplam tutarını hesapla
    {
        "function": "sum",
        "node": "Amount",
        "property": "value",
        "alias": "toplam_tutar",
        "group_by": [{"node": "Customer", "property": "name"}]
    }
    """
    function: AggregateFunction                 # Aggregate fonksiyonu
    node: str                                   # Hangi node
    property: Optional[str] = None              # Hangi property (COUNT için opsiyonel)
    alias: str = "result"                       # Sonuç alias'ı
    group_by: List[Dict[str, str]] = field(default_factory=list)  # Gruplama
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "function": self.function.value if isinstance(self.function, AggregateFunction) else self.function,
            "node": self.node,
            "property": self.property,
            "alias": self.alias,
            "group_by": self.group_by
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AggregateSpec":
        return cls(
            function=AggregateFunction(data["function"]) if isinstance(data["function"], str) else data["function"],
            node=data["node"],
            property=data.get("property"),
            alias=data.get("alias", "result"),
            group_by=data.get("group_by", [])
        )


@dataclass
class OrderBySpec:
    """Sıralama spesifikasyonu"""
    node: str
    property: str
    direction: Literal["ASC", "DESC"] = "DESC"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "node": self.node,
            "property": self.property,
            "direction": self.direction
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OrderBySpec":
        return cls(
            node=data["node"],
            property=data["property"],
            direction=data.get("direction", "DESC")
        )


@dataclass
class SemanticSearchSpec:
    """
    Semantic (embedding) arama spesifikasyonu
    Chunk içeriklerinde semantic arama için kullanılır
    """
    query_text: str                             # Aranacak kavram/metin
    target_node: str = "Chunk"                  # Hangi node'da ara (genelde Chunk)
    embedding_property: str = "embedding"       # Embedding property
    text_property: str = "text"                 # Text property
    similarity_threshold: float = 0.75          # Benzerlik eşiği
    limit: int = 10                             # Maksimum sonuç
    return_score: bool = True                   # Score döndür mü?
    context_filters: List[Filter] = field(default_factory=list)  # Ek filtreler
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "query_text": self.query_text,
            "target_node": self.target_node,
            "embedding_property": self.embedding_property,
            "text_property": self.text_property,
            "similarity_threshold": self.similarity_threshold,
            "limit": self.limit,
            "return_score": self.return_score,
            "context_filters": [f.to_dict() for f in self.context_filters]
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SemanticSearchSpec":
        return cls(
            query_text=data["query_text"],
            target_node=data.get("target_node", "Chunk"),
            embedding_property=data.get("embedding_property", "embedding"),
            text_property=data.get("text_property", "text"),
            similarity_threshold=data.get("similarity_threshold", 0.75),
            limit=data.get("limit", 10),
            return_score=data.get("return_score", True),
            context_filters=[Filter.from_dict(f) for f in data.get("context_filters", [])]
        )


@dataclass
class GraphDSL:
    """
    Ana Graph DSL sınıfı
    
    LLM bu yapıyı JSON olarak üretir, biz Cypher'a çeviririz.
    """
    # Meta bilgi
    intent: QueryIntent                         # Sorgu niyeti
    description: str = ""                       # Açıklama (opsiyonel)
    step_name: str = ""                         # İşlem adı (logging için)
    
    # Graph traversal
    start_node: str = ""                        # Başlangıç node label
    traversal: List[TraversalStep] = field(default_factory=list)
    
    # Filtreler
    filters: List[Filter] = field(default_factory=list)
    
    # Return
    return_spec: Optional[ReturnSpec] = None
    
    # Aggregation
    aggregate: Optional[AggregateSpec] = None
    
    # Semantic search (Chunk araması için)
    semantic_search: Optional[SemanticSearchSpec] = None
    
    # Sıralama ve limit
    order_by: Optional[OrderBySpec] = None
    limit: int = 50
    skip: int = 0
    
    # Özel durumlar
    use_optional_match: bool = False            # OPTIONAL MATCH kullan mı?
    include_source_info: bool = False           # Chunk için kaynak bilgisi ekle
    
    def to_dict(self) -> Dict[str, Any]:
        """DSL'i dictionary'e çevir (JSON serialization için)"""
        return {
            "intent": self.intent.value if isinstance(self.intent, QueryIntent) else self.intent,
            "description": self.description,
            "step_name": self.step_name,
            "start_node": self.start_node,
            "traversal": [t.to_dict() for t in self.traversal],
            "filters": [f.to_dict() for f in self.filters],
            "return_spec": self.return_spec.to_dict() if self.return_spec else None,
            "aggregate": self.aggregate.to_dict() if self.aggregate else None,
            "semantic_search": self.semantic_search.to_dict() if self.semantic_search else None,
            "order_by": self.order_by.to_dict() if self.order_by else None,
            "limit": self.limit,
            "skip": self.skip,
            "use_optional_match": self.use_optional_match,
            "include_source_info": self.include_source_info
        }
    
    def to_json(self, indent: int = 2) -> str:
        """DSL'i JSON string'e çevir"""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GraphDSL":
        """Dictionary'den DSL oluştur"""
        return cls(
            intent=QueryIntent(data["intent"]) if isinstance(data["intent"], str) else data["intent"],
            description=data.get("description", ""),
            step_name=data.get("step_name", ""),
            start_node=data.get("start_node", ""),
            traversal=[TraversalStep.from_dict(t) for t in data.get("traversal", [])],
            filters=[Filter.from_dict(f) for f in data.get("filters", [])],
            return_spec=ReturnSpec.from_dict(data["return_spec"]) if data.get("return_spec") else None,
            aggregate=AggregateSpec.from_dict(data["aggregate"]) if data.get("aggregate") else None,
            semantic_search=SemanticSearchSpec.from_dict(data["semantic_search"]) if data.get("semantic_search") else None,
            order_by=OrderBySpec.from_dict(data["order_by"]) if data.get("order_by") else None,
            limit=data.get("limit", 50),
            skip=data.get("skip", 0),
            use_optional_match=data.get("use_optional_match", False),
            include_source_info=data.get("include_source_info", False)
        )
    
    @classmethod
    def from_json(cls, json_str: str) -> "GraphDSL":
        """JSON string'den DSL oluştur"""
        data = json.loads(json_str)
        return cls.from_dict(data)
    
    def validate_basic(self) -> List[str]:
        """
        Basit validation - daha detaylı validation DSLValidator'da
        Returns: Hata mesajları listesi (boşsa geçerli)
        """
        errors = []
        
        # Intent kontrolü
        if not self.intent:
            errors.append("intent zorunludur")
        
        # Semantic search için query_text kontrolü
        if self.intent == QueryIntent.SEARCH_CONTENT:
            if not self.semantic_search or not self.semantic_search.query_text:
                errors.append("SEARCH_CONTENT için semantic_search.query_text zorunludur")
        
        # Traversal veya start_node kontrolü
        if not self.traversal and not self.start_node:
            if self.intent not in [QueryIntent.EXPLORE_SCHEMA]:
                errors.append("traversal veya start_node gerekli")
        
        # Return veya aggregate kontrolü
        if not self.return_spec and not self.aggregate:
            if self.intent not in [QueryIntent.EXPLORE_SCHEMA, QueryIntent.COUNT_NODES]:
                errors.append("return_spec veya aggregate gerekli")
        
        return errors


# ============================================================================
# ÖRNEK DSL'LER (LLM'e öğretmek için)
# ============================================================================

EXAMPLE_DSLS = {
    "find_customer_policies": GraphDSL(
        intent=QueryIntent.FIND_BY_RELATIONSHIP,
        description="Müşterinin poliçelerini bul",
        step_name="customer_policies",
        start_node="Customer",
        traversal=[
            TraversalStep(from_node="Customer", relation="HAS_POLICY", to_node="Policy")
        ],
        filters=[
            Filter(node="Customer", property="name", operator=FilterOperator.CONTAINS, value="ahmet")
        ],
        return_spec=ReturnSpec(
            nodes=["Policy"],
            properties={"Policy": ["name", "policyNumber", "status"]}
        ),
        limit=10
    ),
    
    "count_policies_by_year": GraphDSL(
        intent=QueryIntent.AGGREGATE_VALUES,
        description="Yıla göre poliçe sayısı",
        step_name="policy_count_by_year",
        start_node="Policy",
        traversal=[
            TraversalStep(from_node="Policy", relation="HAS_START_DATE", to_node="Date")
        ],
        filters=[
            Filter(node="Date", property="year", operator=FilterOperator.EQUALS, value=2024)
        ],
        aggregate=AggregateSpec(
            function=AggregateFunction.COUNT,
            node="Policy",
            alias="policy_count"
        ),
        limit=1
    ),
    
    "search_policy_content": GraphDSL(
        intent=QueryIntent.SEARCH_CONTENT,
        description="Poliçe içeriğinde 'taksit' ara",
        step_name="search_taksit",
        semantic_search=SemanticSearchSpec(
            query_text="taksit ödeme planı",
            target_node="Chunk",
            similarity_threshold=0.75,
            limit=10
        ),
        include_source_info=True
    ),
    
    "explore_node_samples": GraphDSL(
        intent=QueryIntent.EXPLORE_NODE,
        description="Policy node örneklerini gör",
        step_name="explore_policy",
        start_node="Policy",
        return_spec=ReturnSpec(
            nodes=["Policy"],
            properties={"Policy": ["name", "policyNumber"]}
        ),
        limit=5
    )
}

