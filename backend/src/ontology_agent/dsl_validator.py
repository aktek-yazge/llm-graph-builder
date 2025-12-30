"""
DSL Validator - Schema Uyumu Kontrolü

DSL'in Neo4j schema'sı ile uyumlu olup olmadığını kontrol eder.
Hatalı node label'ları, ilişki yönleri ve property'leri tespit eder.
"""

import logging
from typing import Dict, List, Any, Optional, Set, Tuple
from dataclasses import dataclass, field

from .graph_dsl import (
    GraphDSL,
    QueryIntent,
    TraversalStep,
    Filter,
    ReturnSpec,
)

logger = logging.getLogger(__name__)


@dataclass
class ValidationError:
    """Validation hatası"""
    error_type: str          # node_not_found, relation_not_found, property_not_found, direction_wrong
    message: str             # Hata mesajı
    element: str             # Hatalı element (node, relation, property adı)
    suggestion: Optional[str] = None  # Düzeltme önerisi
    severity: str = "error"  # error, warning


@dataclass
class ValidationResult:
    """Validation sonucu"""
    is_valid: bool
    errors: List[ValidationError] = field(default_factory=list)
    warnings: List[ValidationError] = field(default_factory=list)
    validated_dsl: Optional[GraphDSL] = None  # Auto-corrected DSL
    
    def has_errors(self) -> bool:
        return len(self.errors) > 0
    
    def has_warnings(self) -> bool:
        return len(self.warnings) > 0
    
    def get_error_messages(self) -> List[str]:
        return [e.message for e in self.errors]
    
    def get_suggestions(self) -> List[str]:
        return [e.suggestion for e in self.errors + self.warnings if e.suggestion]


@dataclass
class SchemaInfo:
    """
    Neo4j schema bilgisi - validation için kullanılır
    
    Format:
    {
        "nodes": {
            "Customer": {
                "properties": ["name", "email", "phone"],
                "count": 1000
            },
            "Policy": {...}
        },
        "relationships": {
            "HAS_POLICY": {
                "from": ["Customer"],
                "to": ["Policy"],
                "properties": ["createdAt"]
            }
        }
    }
    """
    nodes: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    relationships: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    
    def get_node_labels(self) -> Set[str]:
        """Tüm node label'larını döndür"""
        return set(self.nodes.keys())
    
    def get_relationship_types(self) -> Set[str]:
        """Tüm ilişki tiplerini döndür"""
        return set(self.relationships.keys())
    
    def get_node_properties(self, label: str) -> Set[str]:
        """Bir node'un property'lerini döndür"""
        if label in self.nodes:
            return set(self.nodes[label].get("properties", []))
        return set()
    
    def get_relationship_info(self, rel_type: str) -> Optional[Dict[str, Any]]:
        """İlişki bilgisini döndür (from, to, properties)"""
        return self.relationships.get(rel_type)
    
    def is_valid_relationship(self, from_node: str, rel_type: str, to_node: str) -> bool:
        """
        İlişkinin geçerli olup olmadığını kontrol et
        
        Returns:
            True eğer (from_node)-[:rel_type]->(to_node) schema'da varsa
        """
        rel_info = self.get_relationship_info(rel_type)
        if not rel_info:
            return False
        
        valid_from = from_node in rel_info.get("from", [])
        valid_to = to_node in rel_info.get("to", [])
        
        return valid_from and valid_to
    
    def find_similar_label(self, label: str) -> Optional[str]:
        """Benzer bir node label bul (typo düzeltme)"""
        label_lower = label.lower()
        for existing_label in self.nodes.keys():
            if existing_label.lower() == label_lower:
                return existing_label
        # Levenshtein distance ile benzerlik aranabilir
        return None
    
    def find_similar_relationship(self, rel_type: str) -> Optional[str]:
        """Benzer bir ilişki tipi bul"""
        rel_lower = rel_type.lower()
        for existing_rel in self.relationships.keys():
            if existing_rel.lower() == rel_lower:
                return existing_rel
        return None
    
    @classmethod
    def from_neo4j_schema(cls, schema_dict: Dict[str, Any]) -> "SchemaInfo":
        """
        Neo4j schema'sından SchemaInfo oluştur
        
        schema_dict format: get_cached_schema() çıktısı
        """
        nodes = {}
        relationships = {}
        
        for label, node_info in schema_dict.items():
            if node_info.get("type") == "node":
                # Node bilgisi
                properties = list(node_info.get("properties", {}).keys())
                nodes[label] = {
                    "properties": properties,
                    "count": node_info.get("count", 0)
                }
                
                # İlişkileri çıkar
                for rel_name, rel_info in node_info.get("relationships", {}).items():
                    direction = rel_info.get("direction", "OUT")
                    target_labels = rel_info.get("labels", [])
                    rel_properties = list(rel_info.get("properties", {}).keys())
                    
                    if rel_name not in relationships:
                        relationships[rel_name] = {
                            "from": [],
                            "to": [],
                            "properties": rel_properties
                        }
                    
                    if direction == "OUT":
                        if label not in relationships[rel_name]["from"]:
                            relationships[rel_name]["from"].append(label)
                        for target in target_labels:
                            if target not in relationships[rel_name]["to"]:
                                relationships[rel_name]["to"].append(target)
                    else:  # IN
                        if label not in relationships[rel_name]["to"]:
                            relationships[rel_name]["to"].append(label)
                        for target in target_labels:
                            if target not in relationships[rel_name]["from"]:
                                relationships[rel_name]["from"].append(target)
        
        return cls(nodes=nodes, relationships=relationships)


class DSLValidator:
    """
    DSL Validator
    
    DSL'in schema ile uyumunu kontrol eder:
    1. Node label'larının geçerliliği
    2. İlişki tiplerinin geçerliliği
    3. İlişki yönlerinin doğruluğu
    4. Property'lerin geçerliliği
    """
    
    def __init__(self, schema: SchemaInfo):
        self.schema = schema
    
    def validate(self, dsl: GraphDSL, auto_correct: bool = False) -> ValidationResult:
        """
        DSL'i validate et
        
        Args:
            dsl: Validate edilecek DSL
            auto_correct: Hataları otomatik düzelt (True ise)
            
        Returns:
            ValidationResult
        """
        errors = []
        warnings = []
        
        # 1. Start node validation
        if dsl.start_node:
            node_errors = self._validate_node_label(dsl.start_node)
            errors.extend(node_errors)
        
        # 2. Traversal validation
        for step in dsl.traversal:
            step_errors, step_warnings = self._validate_traversal_step(step)
            errors.extend(step_errors)
            warnings.extend(step_warnings)
        
        # 3. Filter validation
        for filter_obj in dsl.filters:
            filter_errors = self._validate_filter(filter_obj)
            errors.extend(filter_errors)
        
        # 4. Return spec validation
        if dsl.return_spec:
            return_errors = self._validate_return_spec(dsl.return_spec)
            errors.extend(return_errors)
        
        # 5. Aggregate validation
        if dsl.aggregate:
            agg_errors = self._validate_aggregate(dsl)
            errors.extend(agg_errors)
        
        # 6. Semantic search validation
        if dsl.semantic_search:
            ss_errors = self._validate_semantic_search(dsl)
            errors.extend(ss_errors)
        
        is_valid = len(errors) == 0
        
        # Auto-correct
        validated_dsl = None
        if auto_correct and not is_valid:
            validated_dsl = self._auto_correct(dsl, errors)
        
        return ValidationResult(
            is_valid=is_valid,
            errors=errors,
            warnings=warnings,
            validated_dsl=validated_dsl
        )
    
    def _validate_node_label(self, label: str) -> List[ValidationError]:
        """Node label'ını validate et"""
        errors = []
        
        if label not in self.schema.get_node_labels():
            similar = self.schema.find_similar_label(label)
            suggestion = f"'{similar}' mi demek istediniz?" if similar else None
            
            errors.append(ValidationError(
                error_type="node_not_found",
                message=f"Node label '{label}' schema'da bulunamadı",
                element=label,
                suggestion=suggestion
            ))
        
        return errors
    
    def _validate_traversal_step(self, step: TraversalStep) -> Tuple[List[ValidationError], List[ValidationError]]:
        """Traversal step'i validate et"""
        errors = []
        warnings = []
        
        # From node validation
        errors.extend(self._validate_node_label(step.from_node))
        
        # To node validation
        errors.extend(self._validate_node_label(step.to_node))
        
        # Relationship validation
        rel_info = self.schema.get_relationship_info(step.relation)
        
        if not rel_info:
            similar = self.schema.find_similar_relationship(step.relation)
            suggestion = f"'{similar}' mi demek istediniz?" if similar else None
            
            errors.append(ValidationError(
                error_type="relation_not_found",
                message=f"İlişki tipi '{step.relation}' schema'da bulunamadı",
                element=step.relation,
                suggestion=suggestion
            ))
        else:
            # Yön kontrolü
            if step.direction == "outgoing":
                # from_node -> to_node
                if not self.schema.is_valid_relationship(step.from_node, step.relation, step.to_node):
                    # Belki yön tersi?
                    if self.schema.is_valid_relationship(step.to_node, step.relation, step.from_node):
                        errors.append(ValidationError(
                            error_type="direction_wrong",
                            message=f"İlişki yönü yanlış: ({step.from_node})-[:{step.relation}]->({step.to_node})",
                            element=step.relation,
                            suggestion=f"Doğru yön: ({step.to_node})-[:{step.relation}]->({step.from_node})"
                        ))
                    else:
                        warnings.append(ValidationError(
                            error_type="relation_mismatch",
                            message=f"İlişki schema'da tanımlı node'lar ile eşleşmiyor",
                            element=step.relation,
                            severity="warning"
                        ))
            
            elif step.direction == "incoming":
                # from_node <- to_node (yani to_node -> from_node)
                if not self.schema.is_valid_relationship(step.to_node, step.relation, step.from_node):
                    warnings.append(ValidationError(
                        error_type="relation_mismatch",
                        message=f"Incoming ilişki schema ile eşleşmiyor",
                        element=step.relation,
                        severity="warning"
                    ))
        
        return errors, warnings
    
    def _validate_filter(self, filter_obj: Filter) -> List[ValidationError]:
        """Filter'ı validate et"""
        errors = []
        
        # Node label check
        node_props = self.schema.get_node_properties(filter_obj.node)
        
        if not node_props:
            # Node bulunamadı
            errors.extend(self._validate_node_label(filter_obj.node))
        else:
            # Property check
            if filter_obj.property not in node_props:
                # Property bulunamadı - warning olarak verelim, belki yeni property
                errors.append(ValidationError(
                    error_type="property_not_found",
                    message=f"Property '{filter_obj.property}' node '{filter_obj.node}' için bulunamadı",
                    element=f"{filter_obj.node}.{filter_obj.property}",
                    suggestion=f"Mevcut property'ler: {', '.join(list(node_props)[:5])}...",
                    severity="warning"
                ))
        
        return errors
    
    def _validate_return_spec(self, return_spec: ReturnSpec) -> List[ValidationError]:
        """Return spec'i validate et"""
        errors = []
        
        for node in return_spec.nodes:
            errors.extend(self._validate_node_label(node))
            
            # Property check
            props = return_spec.properties.get(node, [])
            node_props = self.schema.get_node_properties(node)
            
            for prop in props:
                if node_props and prop not in node_props:
                    errors.append(ValidationError(
                        error_type="property_not_found",
                        message=f"Return property '{prop}' node '{node}' için bulunamadı",
                        element=f"{node}.{prop}",
                        severity="warning"
                    ))
        
        return errors
    
    def _validate_aggregate(self, dsl: GraphDSL) -> List[ValidationError]:
        """Aggregate spec'i validate et"""
        errors = []
        
        if dsl.aggregate:
            agg = dsl.aggregate
            errors.extend(self._validate_node_label(agg.node))
            
            # Property check (COUNT için opsiyonel)
            if agg.property:
                node_props = self.schema.get_node_properties(agg.node)
                if node_props and agg.property not in node_props:
                    errors.append(ValidationError(
                        error_type="property_not_found",
                        message=f"Aggregate property '{agg.property}' bulunamadı",
                        element=f"{agg.node}.{agg.property}",
                        severity="warning"
                    ))
        
        return errors
    
    def _validate_semantic_search(self, dsl: GraphDSL) -> List[ValidationError]:
        """Semantic search spec'i validate et"""
        errors = []
        
        if dsl.semantic_search:
            ss = dsl.semantic_search
            
            # Target node check
            errors.extend(self._validate_node_label(ss.target_node))
            
            # Embedding property check
            node_props = self.schema.get_node_properties(ss.target_node)
            if node_props and ss.embedding_property not in node_props:
                errors.append(ValidationError(
                    error_type="property_not_found",
                    message=f"Embedding property '{ss.embedding_property}' bulunamadı",
                    element=f"{ss.target_node}.{ss.embedding_property}",
                    suggestion="Chunk node'unun 'embedding' property'si olmalı"
                ))
            
            # Query text check
            if not ss.query_text or len(ss.query_text.strip()) < 2:
                errors.append(ValidationError(
                    error_type="invalid_query_text",
                    message="Semantic search için query_text çok kısa",
                    element="query_text"
                ))
        
        return errors
    
    def _auto_correct(self, dsl: GraphDSL, errors: List[ValidationError]) -> GraphDSL:
        """
        Hataları otomatik düzelt
        
        Şu an sadece case-sensitivity düzeltmesi yapıyor
        """
        import copy
        corrected = copy.deepcopy(dsl)
        
        for error in errors:
            if error.error_type == "node_not_found":
                # Similar label varsa düzelt
                similar = self.schema.find_similar_label(error.element)
                if similar:
                    # Start node
                    if corrected.start_node == error.element:
                        corrected.start_node = similar
                    
                    # Traversal
                    for step in corrected.traversal:
                        if step.from_node == error.element:
                            step.from_node = similar
                        if step.to_node == error.element:
                            step.to_node = similar
                    
                    # Filters
                    for f in corrected.filters:
                        if f.node == error.element:
                            f.node = similar
                    
                    logger.info(f"Auto-corrected: '{error.element}' -> '{similar}'")
            
            elif error.error_type == "relation_not_found":
                similar = self.schema.find_similar_relationship(error.element)
                if similar:
                    for step in corrected.traversal:
                        if step.relation == error.element:
                            step.relation = similar
                    logger.info(f"Auto-corrected relation: '{error.element}' -> '{similar}'")
        
        return corrected


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

def create_validator_from_cache() -> Optional[DSLValidator]:
    """
    Global schema cache'den validator oluştur
    
    Startup'ta yüklenen raw APOC schema'yı kullanır.
    Schema yüklenmediyse None döner.
    """
    try:
        from src.shared.schema_cache import get_raw_schema, is_raw_schema_available
        
        if not is_raw_schema_available():
            logger.warning("⚠️ Raw schema cache'de yok - validation skip edilecek")
            return None
        
        raw_schema = get_raw_schema()
        if not raw_schema:
            logger.warning("⚠️ Raw schema None döndü - validation skip edilecek")
            return None
        
        schema_info = SchemaInfo.from_neo4j_schema(raw_schema)
        logger.info(f"✅ DSL Validator oluşturuldu: {len(schema_info.nodes)} node, {len(schema_info.relationships)} rel")
        return DSLValidator(schema_info)
        
    except ImportError as e:
        logger.error(f"❌ Schema cache import hatası: {e}")
        return None
    except Exception as e:
        logger.error(f"❌ Validator oluşturma hatası: {e}")
        return None


def validate_dsl(dsl: GraphDSL, schema_dict: Optional[Dict[str, Any]] = None) -> ValidationResult:
    """
    DSL'i validate et (convenience function)
    
    Args:
        dsl: Validate edilecek DSL
        schema_dict: Schema dictionary (None ise cache'den al)
    """
    if schema_dict:
        schema_info = SchemaInfo.from_neo4j_schema(schema_dict)
        validator = DSLValidator(schema_info)
    else:
        validator = create_validator_from_cache()
        if not validator:
            return ValidationResult(
                is_valid=False,
                errors=[ValidationError(
                    error_type="no_schema",
                    message="Schema bilgisi bulunamadı",
                    element="schema"
                )]
            )
    
    return validator.validate(dsl, auto_correct=True)

