"""
Ontology Reasoner
=================

Goal-driven ve Ontology-driven reasoning servisi.
Kullanıcı hedeflerinden uygun skill ve schema'ları bulur.

Bu modül Agent Builder'ın "beyni" olarak çalışır:
- Goal parsing ve decomposition
- Skill matching ve scoring
- Schema suggestion
- Learning feedback loop

Kullanım:
---------
    from backend.src.agent_builder.ontology import OntologyReasoner, get_ontology_client
    
    client = await get_ontology_client()
    reasoner = OntologyReasoner(client)
    
    # Goal için skill bul
    skills = await reasoner.find_skills_for_goal(goal_id, tenant_id)
    
    # Schema öner
    proposal = await reasoner.suggest_entity_schema(sample_analysis, context)
"""

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .neo4j_client import OntologyDBClient

logger = logging.getLogger(__name__)


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class ScoredSkill:
    """
    Skor ile birlikte Skill bilgisi.
    find_skills_for_goal sonucu olarak döner.
    """
    skill_id: str
    name: str
    description: str
    category: str
    score: float  # 0.0-1.0 arası relevance skoru
    effectiveness: float  # Skill'in geçmiş başarı oranı
    dependencies: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "score": self.score,
            "effectiveness": self.effectiveness,
            "dependencies": self.dependencies
        }


@dataclass
class SubGoal:
    """
    Decompose edilmiş alt hedef.
    """
    goal_id: str
    name: str
    description: str
    order: int
    optional: bool = False
    achievable_by: List[str] = field(default_factory=list)  # Skill IDs


@dataclass
class SchemaProposal:
    """
    Entity/Relationship schema önerisi.
    """
    entities: List[Dict[str, Any]]  # EntitySchema önerileri
    relationships: List[Dict[str, Any]]  # RelationshipSchema önerileri
    confidence: float
    based_on: List[str]  # Benzer mevcut schema ID'leri
    reasoning: str  # Neden bu schema'yı önerdik


@dataclass
class LearningRecord:
    """
    Öğrenme kaydı.
    """
    learning_id: str
    learning_type: str  # success_pattern, failure_pattern, correction, edge_case
    description: str
    skill_id: str
    tenant_id: str
    confidence: float = 0.5


# =============================================================================
# ONTOLOGY REASONER
# =============================================================================

class OntologyReasoner:
    """
    Goal-driven ve Ontology-driven reasoning servisi.
    
    Bu class şu işlemleri yapar:
    1. Goal'den uygun Skill'leri bulma (semantic + structural matching)
    2. Goal decomposition (karmaşık hedefleri alt hedeflere ayırma)
    3. Schema suggestion (örnek belgelerden entity/relationship önerme)
    4. Learning recording (başarılı/başarısız pattern'leri kaydetme)
    5. Skill effectiveness güncelleme
    
    Attributes:
        db: OntologyDBClient instance
    """
    
    def __init__(self, db: OntologyDBClient):
        """
        Args:
            db: Bağlı OntologyDBClient instance
        """
        self.db = db
    
    # =========================================================================
    # SKILL FINDING
    # =========================================================================
    
    async def find_skills_for_goal(
        self,
        goal_id: str,
        tenant_id: str,
        include_global: bool = True,
        min_effectiveness: float = 0.3,
        limit: int = 10
    ) -> List[ScoredSkill]:
        """
        Verilen Goal için en uygun Skill'leri bul.
        
        Algoritma:
        1. Goal'e ACHIEVABLE_BY ile bağlı skill'leri bul
        2. Tenant'ın kendi skill'lerini ve global skill'leri dahil et
        3. Effectiveness score'a göre sırala
        4. Dependency'leri çöz
        
        Args:
            goal_id: Hedef Goal ID
            tenant_id: Tenant ID (isolation için)
            include_global: Global skill'leri dahil et
            min_effectiveness: Minimum effectiveness threshold
            limit: Maksimum sonuç sayısı
            
        Returns:
            Skorlu skill listesi (yüksekten düşüğe)
        """
        query = """
        // Goal'e bağlı skill'leri bul
        MATCH (g:Goal {id: $goal_id})-[r:ACHIEVABLE_BY]->(s:Skill)
        WHERE s.effectiveness_score >= $min_effectiveness
          AND (s.tenant_id = $tenant_id OR (s.is_global = true AND $include_global))
        
        // Dependency'leri bul
        OPTIONAL MATCH (s)-[:DEPENDS_ON]->(dep:Skill)
        
        WITH s, r, collect(DISTINCT dep.id) as dependencies
        
        // Skor hesapla: confidence * effectiveness
        WITH s, dependencies,
             (coalesce(r.confidence, 0.5) * s.effectiveness_score) as combined_score
        
        RETURN s.id as skill_id,
               s.name as name,
               s.description as description,
               s.skill_category as category,
               combined_score as score,
               s.effectiveness_score as effectiveness,
               dependencies
        ORDER BY combined_score DESC
        LIMIT $limit
        """
        
        results = await self.db.execute_query(query, {
            "goal_id": goal_id,
            "tenant_id": tenant_id,
            "include_global": include_global,
            "min_effectiveness": min_effectiveness,
            "limit": limit
        })
        
        return [
            ScoredSkill(
                skill_id=r["skill_id"],
                name=r["name"],
                description=r["description"],
                category=r["category"],
                score=r["score"],
                effectiveness=r["effectiveness"],
                dependencies=r["dependencies"] or []
            )
            for r in results
        ]
    
    async def find_skills_by_context(
        self,
        context_name: str,
        tenant_id: str,
        category: Optional[str] = None,
        limit: int = 20
    ) -> List[ScoredSkill]:
        """
        Context'e göre skill'leri bul.
        
        Goal tanımlı değilse, context üzerinden skill keşfi yapılır.
        
        Args:
            context_name: Context adı (insurance, maintenance vb.)
            tenant_id: Tenant ID
            category: Skill kategorisi filtresi (optional)
            limit: Maksimum sonuç
        """
        query = """
        // Context'e bağlı skill'leri bul
        MATCH (c:Context {name: $context_name})<-[:APPLICABLE_IN]-(s:Skill)
        WHERE (s.tenant_id = $tenant_id OR s.is_global = true)
          AND ($category IS NULL OR s.skill_category = $category)
        
        OPTIONAL MATCH (s)-[:DEPENDS_ON]->(dep:Skill)
        
        WITH s, collect(DISTINCT dep.id) as dependencies
        
        RETURN s.id as skill_id,
               s.name as name,
               s.description as description,
               s.skill_category as category,
               s.effectiveness_score as score,
               s.effectiveness_score as effectiveness,
               dependencies
        ORDER BY s.effectiveness_score DESC
        LIMIT $limit
        """
        
        results = await self.db.execute_query(query, {
            "context_name": context_name,
            "tenant_id": tenant_id,
            "category": category,
            "limit": limit
        })
        
        return [
            ScoredSkill(
                skill_id=r["skill_id"],
                name=r["name"],
                description=r["description"],
                category=r["category"],
                score=r["score"],
                effectiveness=r["effectiveness"],
                dependencies=r["dependencies"] or []
            )
            for r in results
        ]
    
    async def find_skills_semantic(
        self,
        query_text: str,
        query_embedding: List[float],
        tenant_id: str,
        limit: int = 10
    ) -> List[ScoredSkill]:
        """
        Semantic search ile skill bul.
        
        Kullanıcının natural language açıklamasına benzer skill'leri bulur.
        Vector similarity kullanır.
        
        Args:
            query_text: Arama metni (logging için)
            query_embedding: Arama embedding'i (1536 dim)
            tenant_id: Tenant ID
            limit: Maksimum sonuç
        """
        query = """
        // Vector similarity ile skill ara
        CALL db.index.vector.queryNodes('skill_embedding_idx', $limit, $embedding)
        YIELD node, score
        
        WHERE (node.tenant_id = $tenant_id OR node.is_global = true)
        
        OPTIONAL MATCH (node)-[:DEPENDS_ON]->(dep:Skill)
        
        WITH node, score, collect(DISTINCT dep.id) as dependencies
        
        RETURN node.id as skill_id,
               node.name as name,
               node.description as description,
               node.skill_category as category,
               score,
               node.effectiveness_score as effectiveness,
               dependencies
        ORDER BY score DESC
        """
        
        results = await self.db.execute_query(query, {
            "embedding": query_embedding,
            "tenant_id": tenant_id,
            "limit": limit
        })
        
        logger.info(f"Semantic skill search for '{query_text[:50]}...': {len(results)} results")
        
        return [
            ScoredSkill(
                skill_id=r["skill_id"],
                name=r["name"],
                description=r["description"],
                category=r["category"],
                score=r["score"],
                effectiveness=r["effectiveness"],
                dependencies=r["dependencies"] or []
            )
            for r in results
        ]
    
    # =========================================================================
    # GOAL DECOMPOSITION
    # =========================================================================
    
    async def decompose_goal(
        self,
        goal_id: str
    ) -> List[SubGoal]:
        """
        Kompleks Goal'ü alt hedeflere ayır.
        
        Ontology'deki mevcut Goal hiyerarşisini kullanır.
        Eğer alt hedefler tanımlıysa onları döndürür.
        
        Args:
            goal_id: Ana Goal ID
            
        Returns:
            Alt hedefler listesi (sıralı)
        """
        query = """
        // Alt hedefleri bul
        MATCH (g:Goal {id: $goal_id})-[r:REQUIRES]->(sg:Goal)
        
        // Her alt hedef için achievable skill'leri bul
        OPTIONAL MATCH (sg)-[:ACHIEVABLE_BY]->(s:Skill)
        
        WITH sg, r, collect(DISTINCT s.id) as skill_ids
        
        RETURN sg.id as goal_id,
               sg.name as name,
               sg.description as description,
               r.order as order,
               coalesce(r.optional, false) as optional,
               skill_ids
        ORDER BY r.order
        """
        
        results = await self.db.execute_query(query, {"goal_id": goal_id})
        
        return [
            SubGoal(
                goal_id=r["goal_id"],
                name=r["name"],
                description=r["description"],
                order=r["order"] or 0,
                optional=r["optional"],
                achievable_by=r["skill_ids"] or []
            )
            for r in results
        ]
    
    async def create_goal_decomposition(
        self,
        parent_goal_id: str,
        sub_goals: List[Dict[str, Any]],
        tenant_id: str
    ) -> List[str]:
        """
        Yeni alt hedefler oluştur ve parent'a bağla.
        
        Args:
            parent_goal_id: Ana Goal ID
            sub_goals: Alt hedef tanımları [{name, description, order, optional}]
            tenant_id: Tenant ID
            
        Returns:
            Oluşturulan alt hedef ID'leri
        """
        created_ids = []
        
        for sg in sub_goals:
            sg_id = f"goal-{uuid.uuid4().hex[:12]}"
            
            query = """
            // Alt hedef oluştur
            CREATE (sg:Goal {
                id: $sg_id,
                name: $name,
                description: $description,
                goal_type: 'sub_goal',
                status: 'active',
                tenant_id: $tenant_id,
                created_at: datetime()
            })
            
            // Parent'a bağla
            WITH sg
            MATCH (pg:Goal {id: $parent_goal_id})
            CREATE (pg)-[:REQUIRES {order: $order, optional: $optional}]->(sg)
            
            RETURN sg.id as id
            """
            
            result = await self.db.execute_query(query, {
                "sg_id": sg_id,
                "name": sg["name"],
                "description": sg["description"],
                "order": sg.get("order", 0),
                "optional": sg.get("optional", False),
                "parent_goal_id": parent_goal_id,
                "tenant_id": tenant_id
            }, write=True)
            
            created_ids.append(sg_id)
        
        logger.info(f"Created {len(created_ids)} sub-goals for {parent_goal_id}")
        return created_ids
    
    # =========================================================================
    # SCHEMA SUGGESTION
    # =========================================================================
    
    async def find_similar_schemas(
        self,
        context: str,
        entity_hints: List[str],
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Benzer context ve entity hint'lerine göre mevcut schema'ları bul.
        
        Args:
            context: Context adı
            entity_hints: Tespit edilen entity ipuçları (örn: ["policy", "customer"])
            limit: Maksimum sonuç
            
        Returns:
            Benzer EntitySchema listesi
        """
        # Fulltext search ile benzer schema'ları bul
        search_term = " ".join(entity_hints)
        
        query = """
        // Context'e göre filtrele ve fulltext ara
        CALL db.index.fulltext.queryNodes('entity_schema_fulltext', $search_term)
        YIELD node, score
        
        WHERE node.context = $context OR node.context IS NULL
        
        RETURN node.id as id,
               node.entity_type as entity_type,
               node.description as description,
               node.properties as properties,
               node.context as context,
               score
        ORDER BY score DESC
        LIMIT $limit
        """
        
        results = await self.db.execute_query(query, {
            "search_term": search_term,
            "context": context,
            "limit": limit
        })
        
        return [
            {
                "id": r["id"],
                "entity_type": r["entity_type"],
                "description": r["description"],
                "properties": json.loads(r["properties"]) if r["properties"] else {},
                "context": r["context"],
                "similarity_score": r["score"]
            }
            for r in results
        ]
    
    async def suggest_schema_from_analysis(
        self,
        sample_analysis: Dict[str, Any],
        context: str
    ) -> SchemaProposal:
        """
        Örnek belge analizinden entity/relationship schema öner.
        
        Bu fonksiyon:
        1. Benzer mevcut schema'ları bulur
        2. Sample'dan çıkarılan pattern'leri analiz eder
        3. Yeni veya genişletilmiş schema önerir
        
        Args:
            sample_analysis: Örnek belge analiz sonuçları
                {
                    "detected_entities": ["Policy", "Customer", ...],
                    "detected_fields": {"Policy": ["policy_no", "date", ...]},
                    "detected_relationships": [("Customer", "HAS", "Policy"), ...]
                }
            context: Context adı
            
        Returns:
            SchemaProposal
        """
        # 1. Benzer schema'ları bul
        entity_hints = sample_analysis.get("detected_entities", [])
        similar_schemas = await self.find_similar_schemas(context, entity_hints)
        
        # 2. Entity schema önerileri oluştur
        entities = []
        for entity_type in entity_hints:
            # Mevcut benzer schema var mı?
            matching = [s for s in similar_schemas if s["entity_type"].lower() == entity_type.lower()]
            
            if matching:
                # Mevcut schema'yı baz al, yeni field'ları ekle
                base_schema = matching[0]
                new_properties = sample_analysis.get("detected_fields", {}).get(entity_type, [])
                
                merged_properties = base_schema["properties"].copy()
                for prop in new_properties:
                    if prop not in merged_properties:
                        merged_properties[prop] = {"type": "string", "required": False}
                
                entities.append({
                    "entity_type": entity_type,
                    "description": base_schema.get("description", f"{entity_type} entity"),
                    "properties": merged_properties,
                    "based_on": base_schema["id"],
                    "is_new": False
                })
            else:
                # Yeni schema öner
                properties = {}
                for prop in sample_analysis.get("detected_fields", {}).get(entity_type, []):
                    properties[prop] = {"type": "string", "required": False}
                
                entities.append({
                    "entity_type": entity_type,
                    "description": f"{entity_type} entity (auto-detected)",
                    "properties": properties,
                    "based_on": None,
                    "is_new": True
                })
        
        # 3. Relationship schema önerileri
        relationships = []
        for rel in sample_analysis.get("detected_relationships", []):
            source, rel_type, target = rel if len(rel) == 3 else (rel[0], "RELATED_TO", rel[1])
            
            relationships.append({
                "relationship_type": rel_type,
                "source_entity": source,
                "target_entity": target,
                "description": f"{source} {rel_type.lower().replace('_', ' ')} {target}",
                "cardinality": "1:N",  # Default
                "is_new": True
            })
        
        # 4. Confidence hesapla
        existing_count = len([e for e in entities if not e["is_new"]])
        confidence = 0.5 + (0.3 * existing_count / max(len(entities), 1))
        
        return SchemaProposal(
            entities=entities,
            relationships=relationships,
            confidence=confidence,
            based_on=[s["id"] for s in similar_schemas],
            reasoning=f"Found {len(similar_schemas)} similar schemas in context '{context}'. "
                      f"{existing_count}/{len(entities)} entities match existing patterns."
        )
    
    # =========================================================================
    # LEARNING & FEEDBACK
    # =========================================================================
    
    async def record_learning(
        self,
        skill_id: str,
        tenant_id: str,
        learning_type: str,
        description: str,
        context_id: Optional[str] = None,
        example_input: Optional[str] = None,
        expected_output: Optional[str] = None,
        actual_output: Optional[str] = None,
        correction: Optional[str] = None
    ) -> str:
        """
        Öğrenme kaydı oluştur.
        
        Skill kullanımı sonrasında başarılı/başarısız pattern'leri kaydeder.
        Bu kayıtlar skill refinement için kullanılır.
        
        IMPORTANT: Learning'ler TENANT-ISOLATED tutulur!
        
        Args:
            skill_id: İlgili skill ID
            tenant_id: Tenant ID (REQUIRED - isolation için)
            learning_type: Öğrenme tipi (success_pattern, failure_pattern, correction, edge_case)
            description: Öğrenilen şeyin açıklaması
            context_id: Context ID (optional)
            example_input: Örnek input
            expected_output: Beklenen output
            actual_output: Gerçek output
            correction: Düzeltme açıklaması
            
        Returns:
            Oluşturulan Learning ID
        """
        learning_id = f"learn-{uuid.uuid4().hex[:12]}"
        
        query = """
        // Learning node oluştur
        CREATE (l:Learning {
            id: $learning_id,
            learning_type: $learning_type,
            description: $description,
            example_input: $example_input,
            expected_output: $expected_output,
            actual_output: $actual_output,
            correction: $correction,
            confidence: 0.5,
            tenant_id: $tenant_id,
            skill_id: $skill_id,
            learned_at: datetime()
        })
        
        // Skill'e bağla
        WITH l
        MATCH (s:Skill {id: $skill_id})
        CREATE (s)-[:IMPROVED_BY]->(l)
        
        // Context'e bağla (varsa)
        WITH l
        OPTIONAL MATCH (c:Context {id: $context_id})
        FOREACH (_ IN CASE WHEN c IS NOT NULL THEN [1] ELSE [] END |
            CREATE (l)-[:OBSERVED_IN]->(c)
        )
        
        RETURN l.id as id
        """
        
        await self.db.execute_query(query, {
            "learning_id": learning_id,
            "learning_type": learning_type,
            "description": description,
            "example_input": example_input,
            "expected_output": expected_output,
            "actual_output": actual_output,
            "correction": correction,
            "tenant_id": tenant_id,
            "skill_id": skill_id,
            "context_id": context_id
        }, write=True)
        
        logger.info(f"Recorded learning {learning_id} for skill {skill_id} (tenant: {tenant_id})")
        return learning_id
    
    async def update_skill_effectiveness(
        self,
        skill_id: str,
        success: bool,
        weight: float = 1.0
    ) -> float:
        """
        Skill effectiveness score'unu güncelle.
        
        Başarılı kullanımlar score'u artırır, başarısız kullanımlar düşürür.
        Exponential moving average kullanır.
        
        Args:
            skill_id: Skill ID
            success: Başarılı mı
            weight: Güncelleme ağırlığı (default: 1.0)
            
        Returns:
            Yeni effectiveness score
        """
        # EMA alpha - son kullanımlar daha etkili
        alpha = 0.1 * weight
        success_value = 1.0 if success else 0.0
        
        query = """
        MATCH (s:Skill {id: $skill_id})
        
        // EMA güncelleme: new_score = alpha * new_value + (1 - alpha) * old_score
        SET s.effectiveness_score = $alpha * $success_value + (1 - $alpha) * s.effectiveness_score,
            s.usage_count = s.usage_count + 1,
            s.updated_at = datetime()
        
        RETURN s.effectiveness_score as new_score
        """
        
        result = await self.db.execute_query(query, {
            "skill_id": skill_id,
            "alpha": alpha,
            "success_value": success_value
        }, write=True)
        
        new_score = result[0]["new_score"] if result else 0.5
        logger.debug(f"Updated skill {skill_id} effectiveness: {new_score:.3f}")
        
        return new_score
    
    async def get_learnings_for_skill(
        self,
        skill_id: str,
        tenant_id: str,
        learning_type: Optional[str] = None,
        limit: int = 20
    ) -> List[LearningRecord]:
        """
        Skill için tenant-isolated öğrenmeleri getir.
        
        Args:
            skill_id: Skill ID
            tenant_id: Tenant ID (REQUIRED - isolation)
            learning_type: Filtre (optional)
            limit: Maksimum sonuç
        """
        query = """
        MATCH (s:Skill {id: $skill_id})-[:IMPROVED_BY]->(l:Learning)
        WHERE l.tenant_id = $tenant_id
          AND ($learning_type IS NULL OR l.learning_type = $learning_type)
        
        RETURN l.id as learning_id,
               l.learning_type as learning_type,
               l.description as description,
               l.skill_id as skill_id,
               l.tenant_id as tenant_id,
               l.confidence as confidence
        ORDER BY l.learned_at DESC
        LIMIT $limit
        """
        
        results = await self.db.execute_query(query, {
            "skill_id": skill_id,
            "tenant_id": tenant_id,
            "learning_type": learning_type,
            "limit": limit
        })
        
        return [
            LearningRecord(
                learning_id=r["learning_id"],
                learning_type=r["learning_type"],
                description=r["description"],
                skill_id=r["skill_id"],
                tenant_id=r["tenant_id"],
                confidence=r["confidence"] or 0.5
            )
            for r in results
        ]
