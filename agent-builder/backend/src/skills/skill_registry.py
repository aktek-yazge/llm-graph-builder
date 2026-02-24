"""
Skill Registry
==============

Skills'lerin Neo4j Ontology DB'de yönetimi.
CRUD operasyonları ve skill execution için veri hazırlama.

Kullanım:
---------
    from backend.src.agent_builder.skills import SkillRegistry
    from backend.src.agent_builder.ontology import get_ontology_client
    
    client = await get_ontology_client()
    registry = SkillRegistry(client)
    
    # Skill oluştur
    skill_id = await registry.create_skill(skill_create)
    
    # Execution için skill yükle
    skill_exec = await registry.get_skill_for_execution(skill_id)
"""

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..ontology.neo4j_client import OntologyDBClient
from ..models import (
    Skill,
    SkillCreate,
    SkillSummary,
    SkillWithDependencies,
    SkillCategory,
    EntitySchemaSummary,
    RelationshipSchemaSummary,
)

logger = logging.getLogger(__name__)


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class SkillExecution:
    """
    Agentic OCR'da kullanılacak skill bilgileri.
    Runtime execution için optimize edilmiş format.
    """
    skill_id: str
    name: str
    category: str
    prompt_template: str
    input_schema: Dict[str, Any]
    output_schema: Dict[str, Any]
    entity_schemas: List[Dict[str, Any]] = field(default_factory=list)
    relationship_schemas: List[Dict[str, Any]] = field(default_factory=list)
    
    @property
    def entity_schemas_json(self) -> str:
        """Entity schema'ları JSON string olarak"""
        return json.dumps(self.entity_schemas, ensure_ascii=False, indent=2)
    
    @property
    def relationship_schemas_json(self) -> str:
        """Relationship schema'ları JSON string olarak"""
        return json.dumps(self.relationship_schemas, ensure_ascii=False, indent=2)
    
    def format_prompt(self, **kwargs) -> str:
        """
        Prompt template'i verilen parametrelerle formatla.
        
        Otomatik olarak eklenen parametreler:
        - entity_schemas: Entity schema JSON
        - relationship_schemas: Relationship schema JSON
        """
        format_params = {
            "entity_schemas": self.entity_schemas_json,
            "relationship_schemas": self.relationship_schemas_json,
            **kwargs
        }
        
        try:
            return self.prompt_template.format(**format_params)
        except KeyError as e:
            logger.warning(f"Missing prompt parameter: {e}")
            return self.prompt_template


# =============================================================================
# SKILL REGISTRY
# =============================================================================

class SkillRegistry:
    """
    Skill yönetimi için registry sınıfı.
    
    Neo4j Ontology DB'de skill CRUD operasyonları ve
    Agentic OCR için skill execution hazırlama işlemleri.
    
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
    # CREATE OPERATIONS
    # =========================================================================
    
    async def create_skill(self, skill_data: SkillCreate) -> str:
        """
        Yeni skill oluştur.
        
        Args:
            skill_data: Skill oluşturma verileri
            
        Returns:
            Oluşturulan skill ID
        """
        skill_id = f"skill-{uuid.uuid4().hex[:12]}"
        
        # Ana skill node oluştur
        query = """
        CREATE (s:Skill {
            id: $id,
            name: $name,
            description: $description,
            skill_category: $category,
            prompt_template: $prompt_template,
            input_schema: $input_schema,
            output_schema: $output_schema,
            version: 1,
            effectiveness_score: 0.5,
            usage_count: 0,
            is_global: $is_global,
            tenant_id: $tenant_id,
            created_at: datetime(),
            updated_at: datetime()
        })
        RETURN s.id as id
        """
        
        await self.db.execute_query(query, {
            "id": skill_id,
            "name": skill_data.name,
            "description": skill_data.description,
            "category": skill_data.skill_category.value,
            "prompt_template": skill_data.prompt_template,
            "input_schema": json.dumps(skill_data.input_schema) if skill_data.input_schema else "{}",
            "output_schema": json.dumps(skill_data.output_schema) if skill_data.output_schema else "{}",
            "is_global": skill_data.is_global,
            "tenant_id": skill_data.tenant_id
        }, write=True)
        
        # Context ilişkilerini oluştur
        if skill_data.context_ids:
            await self._link_skill_to_contexts(skill_id, skill_data.context_ids)
        
        # Dependency ilişkilerini oluştur
        if skill_data.depends_on:
            await self._link_skill_dependencies(skill_id, skill_data.depends_on)
        
        # Entity schema ilişkilerini oluştur
        if skill_data.entity_schema_ids:
            await self._link_skill_to_entity_schemas(skill_id, skill_data.entity_schema_ids)
        
        logger.info(f"Created skill: {skill_id} ({skill_data.name})")
        return skill_id
    
    async def _link_skill_to_contexts(self, skill_id: str, context_ids: List[str]) -> None:
        """Skill'i context'lere bağla"""
        query = """
        MATCH (s:Skill {id: $skill_id})
        UNWIND $context_ids as context_id
        MATCH (c:Context {id: context_id})
        MERGE (s)-[:APPLICABLE_IN]->(c)
        """
        await self.db.execute_query(query, {
            "skill_id": skill_id,
            "context_ids": context_ids
        }, write=True)
    
    async def _link_skill_dependencies(self, skill_id: str, depends_on: List[str]) -> None:
        """Skill dependency'lerini oluştur"""
        query = """
        MATCH (s:Skill {id: $skill_id})
        UNWIND $depends_on as dep_id
        MATCH (dep:Skill {id: dep_id})
        MERGE (s)-[:DEPENDS_ON {dependency_type: 'required'}]->(dep)
        """
        await self.db.execute_query(query, {
            "skill_id": skill_id,
            "depends_on": depends_on
        }, write=True)
    
    async def _link_skill_to_entity_schemas(self, skill_id: str, schema_ids: List[str]) -> None:
        """Skill'in extract ettiği entity schema'ları bağla"""
        query = """
        MATCH (s:Skill {id: $skill_id})
        UNWIND $schema_ids as schema_id
        MATCH (e:EntitySchema {id: schema_id})
        MERGE (s)-[:EXTRACTS]->(e)
        """
        await self.db.execute_query(query, {
            "skill_id": skill_id,
            "schema_ids": schema_ids
        }, write=True)
    
    # =========================================================================
    # READ OPERATIONS
    # =========================================================================
    
    async def get_skill(self, skill_id: str) -> Optional[Skill]:
        """
        Skill bilgilerini getir.
        
        Args:
            skill_id: Skill ID
            
        Returns:
            Skill veya None
        """
        query = """
        MATCH (s:Skill {id: $skill_id})
        RETURN s
        """
        
        results = await self.db.execute_query(query, {"skill_id": skill_id})
        
        if not results:
            return None
        
        s = results[0]["s"]
        return Skill(
            id=s["id"],
            name=s["name"],
            description=s["description"],
            skill_category=SkillCategory(s["skill_category"]),
            prompt_template=s["prompt_template"],
            input_schema=json.loads(s.get("input_schema", "{}")) if s.get("input_schema") else None,
            output_schema=json.loads(s.get("output_schema", "{}")) if s.get("output_schema") else None,
            version=s.get("version", 1),
            effectiveness_score=s.get("effectiveness_score", 0.5),
            usage_count=s.get("usage_count", 0),
            is_global=s.get("is_global", False),
            tenant_id=s.get("tenant_id"),
            created_at=s.get("created_at"),
            updated_at=s.get("updated_at")
        )
    
    async def get_skill_for_execution(self, skill_id: str) -> Optional[SkillExecution]:
        """
        Agentic OCR'da kullanılacak skill bilgilerini getir.
        
        Entity ve relationship schema'ları dahil eder.
        
        Args:
            skill_id: Skill ID
            
        Returns:
            SkillExecution veya None
        """
        query = """
        MATCH (s:Skill {id: $skill_id})
        
        // Entity schema'ları
        OPTIONAL MATCH (s)-[:EXTRACTS]->(e:EntitySchema)
        WITH s, collect(DISTINCT {
            entity_type: e.entity_type,
            description: e.description,
            properties: e.properties
        }) as entity_schemas
        
        // Relationship schema'ları
        OPTIONAL MATCH (s)-[:CREATES]->(r:RelationshipSchema)
        WITH s, entity_schemas, collect(DISTINCT {
            relationship_type: r.relationship_type,
            source_entity: r.source_entity,
            target_entity: r.target_entity,
            description: r.description
        }) as rel_schemas
        
        RETURN s.id as id,
               s.name as name,
               s.skill_category as category,
               s.prompt_template as prompt_template,
               s.input_schema as input_schema,
               s.output_schema as output_schema,
               entity_schemas,
               rel_schemas
        """
        
        results = await self.db.execute_query(query, {"skill_id": skill_id})
        
        if not results:
            return None
        
        r = results[0]
        
        # Entity schema'ları parse et (null olanları filtrele)
        entity_schemas = [
            {
                "entity_type": e["entity_type"],
                "description": e["description"],
                "properties": json.loads(e["properties"]) if e.get("properties") else {}
            }
            for e in r["entity_schemas"]
            if e.get("entity_type")
        ]
        
        # Relationship schema'ları parse et
        rel_schemas = [
            {
                "relationship_type": rs["relationship_type"],
                "source_entity": rs["source_entity"],
                "target_entity": rs["target_entity"],
                "description": rs["description"]
            }
            for rs in r["rel_schemas"]
            if rs.get("relationship_type")
        ]
        
        return SkillExecution(
            skill_id=r["id"],
            name=r["name"],
            category=r["category"],
            prompt_template=r["prompt_template"],
            input_schema=json.loads(r["input_schema"]) if r.get("input_schema") else {},
            output_schema=json.loads(r["output_schema"]) if r.get("output_schema") else {},
            entity_schemas=entity_schemas,
            relationship_schemas=rel_schemas
        )
    
    async def list_skills_for_tenant(
        self,
        tenant_id: str,
        include_global: bool = True,
        category: Optional[SkillCategory] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[SkillSummary]:
        """
        Tenant'ın kullanabildiği skill'leri listele.
        
        Args:
            tenant_id: Tenant ID
            include_global: Global skill'leri dahil et
            category: Kategori filtresi
            limit: Maksimum sonuç
            offset: Sayfalama offset
            
        Returns:
            Skill özet listesi
        """
        query = """
        MATCH (s:Skill)
        WHERE s.tenant_id = $tenant_id 
           OR (s.is_global = true AND $include_global)
        
        WITH s
        WHERE $category IS NULL OR s.skill_category = $category
        
        RETURN s.id as id,
               s.name as name,
               s.description as description,
               s.skill_category as category,
               s.effectiveness_score as effectiveness_score,
               s.is_global as is_global
        ORDER BY s.effectiveness_score DESC, s.name
        SKIP $offset
        LIMIT $limit
        """
        
        results = await self.db.execute_query(query, {
            "tenant_id": tenant_id,
            "include_global": include_global,
            "category": category.value if category else None,
            "limit": limit,
            "offset": offset
        })
        
        return [
            SkillSummary(
                id=r["id"],
                name=r["name"],
                description=r["description"],
                category=SkillCategory(r["category"]),
                effectiveness_score=r["effectiveness_score"] or 0.5,
                is_global=r["is_global"] or False
            )
            for r in results
        ]
    
    # =========================================================================
    # UPDATE OPERATIONS
    # =========================================================================
    
    async def update_skill(
        self,
        skill_id: str,
        updates: Dict[str, Any]
    ) -> bool:
        """
        Skill güncelle.
        
        Args:
            skill_id: Skill ID
            updates: Güncellenecek alanlar
            
        Returns:
            Başarılı mı
        """
        # Güvenli alanlar - bunlar güncellenebilir
        allowed_fields = {
            "name", "description", "prompt_template",
            "input_schema", "output_schema"
        }
        
        # Sadece izin verilen alanları al
        safe_updates = {k: v for k, v in updates.items() if k in allowed_fields}
        
        if not safe_updates:
            return False
        
        # JSON alanlarını string'e çevir
        if "input_schema" in safe_updates and isinstance(safe_updates["input_schema"], dict):
            safe_updates["input_schema"] = json.dumps(safe_updates["input_schema"])
        if "output_schema" in safe_updates and isinstance(safe_updates["output_schema"], dict):
            safe_updates["output_schema"] = json.dumps(safe_updates["output_schema"])
        
        # Dinamik SET clause oluştur
        set_clauses = ", ".join([f"s.{k} = ${k}" for k in safe_updates.keys()])
        
        query = f"""
        MATCH (s:Skill {{id: $skill_id}})
        SET {set_clauses}, s.updated_at = datetime()
        RETURN s.id
        """
        
        params = {"skill_id": skill_id, **safe_updates}
        result = await self.db.execute_query(query, params, write=True)
        
        return bool(result)
    
    async def increment_version(self, skill_id: str) -> int:
        """
        Skill versiyonunu artır.
        
        Args:
            skill_id: Skill ID
            
        Returns:
            Yeni versiyon numarası
        """
        query = """
        MATCH (s:Skill {id: $skill_id})
        SET s.version = s.version + 1,
            s.updated_at = datetime()
        RETURN s.version as version
        """
        
        result = await self.db.execute_query(query, {"skill_id": skill_id}, write=True)
        return result[0]["version"] if result else 1
    
    # =========================================================================
    # DELETE OPERATIONS
    # =========================================================================
    
    async def delete_skill(self, skill_id: str, tenant_id: str) -> bool:
        """
        Skill sil (sadece tenant'ın kendi skill'leri).
        
        Global skill'ler silinemez.
        
        Args:
            skill_id: Skill ID
            tenant_id: Tenant ID (yetkilendirme için)
            
        Returns:
            Başarılı mı
        """
        query = """
        MATCH (s:Skill {id: $skill_id})
        WHERE s.tenant_id = $tenant_id AND s.is_global = false
        DETACH DELETE s
        RETURN count(s) as deleted
        """
        
        result = await self.db.execute_query(query, {
            "skill_id": skill_id,
            "tenant_id": tenant_id
        }, write=True)
        
        deleted = result[0]["deleted"] if result else 0
        
        if deleted > 0:
            logger.info(f"Deleted skill: {skill_id}")
        
        return deleted > 0
