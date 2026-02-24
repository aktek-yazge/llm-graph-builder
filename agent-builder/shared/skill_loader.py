"""
Dynamic Skill Loader
====================

Neo4j Ontology DB'den skill'leri yükleyen modül.
Agentic OCR tarafından runtime'da kullanılır.

Bu modül Agent Builder ile oluşturulan skill'lerin
Agentic OCR'da dinamik olarak kullanılmasını sağlar.

Kullanım:
---------
    from src.skill_loader import load_skill_from_ontology, SkillExecution
    
    # Ontology DB'den skill yükle
    skill = await load_skill_from_ontology(skill_id)
    
    # Prompt'u formatla
    prompt = skill.format_prompt(text=ocr_text)
    
    # LLM'e gönder
    result = await llm.invoke(prompt)

Notlar:
-------
- Skill'ler Neo4j Ontology DB'de saklanır (Documents DB değil!)
- Entity ve relationship schema'ları skill ile birlikte yüklenir
- Connection pooling ile performans optimize edilir
"""

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from neo4j import AsyncGraphDatabase, AsyncDriver

logger = logging.getLogger(__name__)


# =============================================================================
# CONFIGURATION
# =============================================================================

# Ontology DB bağlantı bilgileri (Documents DB'den FARKLI!)
ONTOLOGY_NEO4J_URI = os.getenv("ONTOLOGY_NEO4J_URI", "bolt://localhost:7688")
ONTOLOGY_NEO4J_USERNAME = os.getenv("ONTOLOGY_NEO4J_USERNAME", "neo4j")
ONTOLOGY_NEO4J_PASSWORD = os.getenv("ONTOLOGY_NEO4J_PASSWORD", "ontology_secret")
ONTOLOGY_NEO4J_DATABASE = os.getenv("ONTOLOGY_NEO4J_DATABASE", "ontology")


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class SkillExecution:
    """
    Runtime'da kullanılacak skill bilgileri.
    
    Agentic OCR bu class'ı kullanarak:
    1. Prompt template'i formatlar
    2. Entity schema'ları LLM'e bağlam olarak verir
    3. Output'u validate eder
    """
    skill_id: str
    name: str
    category: str
    prompt_template: str
    input_schema: Dict[str, Any]
    output_schema: Dict[str, Any]
    entity_schemas: List[Dict[str, Any]] = field(default_factory=list)
    relationship_schemas: List[Dict[str, Any]] = field(default_factory=list)
    version: int = 1
    effectiveness_score: float = 0.5
    
    @property
    def entity_schemas_json(self) -> str:
        """Entity schema'ları JSON string olarak"""
        return json.dumps(self.entity_schemas, ensure_ascii=False, indent=2)
    
    @property
    def relationship_schemas_json(self) -> str:
        """Relationship schema'ları JSON string olarak"""
        return json.dumps(self.relationship_schemas, ensure_ascii=False, indent=2)
    
    @property
    def entity_types_list(self) -> List[str]:
        """Entity type isimlerinin listesi"""
        return [e["entity_type"] for e in self.entity_schemas if e.get("entity_type")]
    
    @property
    def relationship_types_list(self) -> List[str]:
        """Relationship type isimlerinin listesi"""
        return [r["relationship_type"] for r in self.relationship_schemas if r.get("relationship_type")]
    
    def format_prompt(self, **kwargs) -> str:
        """
        Prompt template'i verilen parametrelerle formatla.
        
        Otomatik olarak eklenen parametreler:
        - entity_schemas: Entity schema JSON
        - relationship_schemas: Relationship schema JSON
        - entity_types: Entity type listesi (virgülle ayrılmış)
        - relationship_types: Relationship type listesi
        
        Args:
            **kwargs: Prompt'a geçirilecek ek parametreler (text, image_path, vb.)
            
        Returns:
            Formatlanmış prompt string
        """
        format_params = {
            "entity_schemas": self.entity_schemas_json,
            "relationship_schemas": self.relationship_schemas_json,
            "entity_types": ", ".join(self.entity_types_list),
            "relationship_types": ", ".join(self.relationship_types_list),
            **kwargs
        }
        
        try:
            # Python str.format() kullan
            return self.prompt_template.format(**format_params)
        except KeyError as e:
            logger.warning(f"Missing prompt parameter: {e}")
            # Placeholder'ları temizlemeden döndür
            return self.prompt_template
    
    def to_dict(self) -> Dict[str, Any]:
        """Dict representation"""
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "category": self.category,
            "prompt_template": self.prompt_template,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "entity_schemas": self.entity_schemas,
            "relationship_schemas": self.relationship_schemas,
            "version": self.version,
            "effectiveness_score": self.effectiveness_score
        }


# =============================================================================
# SKILL LOADER
# =============================================================================

class SkillLoader:
    """
    Ontology DB'den skill yükleyen sınıf.
    
    Singleton pattern - tüm worker'lar aynı connection pool'u kullanır.
    Lazy initialization - ilk kullanımda bağlanır.
    
    Attributes:
        _driver: Neo4j async driver instance
        _initialized: Bağlantı kuruldu mu
    """
    
    _instance: Optional["SkillLoader"] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._driver = None
            cls._instance._initialized = False
        return cls._instance
    
    @property
    def is_initialized(self) -> bool:
        return self._initialized
    
    async def initialize(self) -> None:
        """
        Ontology DB'ye bağlan.
        İlk skill yüklemeden önce çağrılmalı.
        """
        if self._initialized:
            return
        
        try:
            logger.info(f"Connecting to Ontology DB: {ONTOLOGY_NEO4J_URI[:30]}...")
            
            self._driver = AsyncGraphDatabase.driver(
                ONTOLOGY_NEO4J_URI,
                auth=(ONTOLOGY_NEO4J_USERNAME, ONTOLOGY_NEO4J_PASSWORD),
                max_connection_pool_size=20,
                connection_acquisition_timeout=30,
            )
            
            # Connection test
            async with self._driver.session(database=ONTOLOGY_NEO4J_DATABASE) as session:
                result = await session.run("RETURN 1 as test")
                await result.consume()
            
            self._initialized = True
            logger.info("✅ Connected to Ontology DB for skill loading")
            
        except Exception as e:
            logger.error(f"❌ Failed to connect to Ontology DB: {e}")
            self._initialized = False
            raise
    
    async def close(self) -> None:
        """Bağlantıyı kapat"""
        if self._driver:
            await self._driver.close()
            self._driver = None
            self._initialized = False
    
    async def load_skill(self, skill_id: str) -> Optional[SkillExecution]:
        """
        Skill'i Ontology DB'den yükle.
        
        Entity ve relationship schema'ları dahil eder.
        
        Args:
            skill_id: Skill ID
            
        Returns:
            SkillExecution veya None (bulunamazsa)
        """
        if not self._initialized:
            await self.initialize()
        
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
               s.version as version,
               s.effectiveness_score as effectiveness_score,
               entity_schemas,
               rel_schemas
        """
        
        try:
            async with self._driver.session(database=ONTOLOGY_NEO4J_DATABASE) as session:
                result = await session.run(query, {"skill_id": skill_id})
                record = await result.single()
                
                if not record:
                    logger.warning(f"Skill not found: {skill_id}")
                    return None
                
                # Entity schema'ları parse et (null olanları filtrele)
                entity_schemas = [
                    {
                        "entity_type": e["entity_type"],
                        "description": e["description"],
                        "properties": json.loads(e["properties"]) if e.get("properties") else {}
                    }
                    for e in record["entity_schemas"]
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
                    for rs in record["rel_schemas"]
                    if rs.get("relationship_type")
                ]
                
                skill = SkillExecution(
                    skill_id=record["id"],
                    name=record["name"],
                    category=record["category"],
                    prompt_template=record["prompt_template"],
                    input_schema=json.loads(record["input_schema"]) if record.get("input_schema") else {},
                    output_schema=json.loads(record["output_schema"]) if record.get("output_schema") else {},
                    entity_schemas=entity_schemas,
                    relationship_schemas=rel_schemas,
                    version=record.get("version") or 1,
                    effectiveness_score=record.get("effectiveness_score") or 0.5
                )
                
                logger.debug(f"Loaded skill: {skill_id} ({skill.name})")
                return skill
                
        except Exception as e:
            logger.error(f"Error loading skill {skill_id}: {e}")
            raise
    
    async def update_skill_usage(
        self,
        skill_id: str,
        success: bool = True
    ) -> None:
        """
        Skill kullanım istatistiklerini güncelle.
        
        Effectiveness score EMA ile güncellenir.
        
        Args:
            skill_id: Skill ID
            success: İşlem başarılı mıydı
        """
        if not self._initialized:
            return
        
        # EMA alpha - son kullanımlar daha etkili
        alpha = 0.1
        success_value = 1.0 if success else 0.0
        
        query = """
        MATCH (s:Skill {id: $skill_id})
        SET s.effectiveness_score = $alpha * $success_value + (1 - $alpha) * s.effectiveness_score,
            s.usage_count = s.usage_count + 1,
            s.updated_at = datetime()
        """
        
        try:
            async with self._driver.session(database=ONTOLOGY_NEO4J_DATABASE) as session:
                await session.run(query, {
                    "skill_id": skill_id,
                    "alpha": alpha,
                    "success_value": success_value
                })
                
        except Exception as e:
            # Log but don't fail the main process
            logger.warning(f"Failed to update skill usage for {skill_id}: {e}")


# =============================================================================
# MODULE-LEVEL FUNCTIONS
# =============================================================================

_loader: Optional[SkillLoader] = None


async def get_skill_loader() -> SkillLoader:
    """Global SkillLoader instance'ı al"""
    global _loader
    
    if _loader is None:
        _loader = SkillLoader()
    
    if not _loader.is_initialized:
        await _loader.initialize()
    
    return _loader


async def load_skill_from_ontology(skill_id: str) -> Optional[SkillExecution]:
    """
    Skill'i Ontology DB'den yükle.
    
    Convenience function - SkillLoader.load_skill() wrapper.
    
    Args:
        skill_id: Skill ID
        
    Returns:
        SkillExecution veya None
        
    Example:
        skill = await load_skill_from_ontology("skill-abc123")
        if skill:
            prompt = skill.format_prompt(text=ocr_text)
    """
    loader = await get_skill_loader()
    return await loader.load_skill(skill_id)


async def update_skill_effectiveness(skill_id: str, success: bool = True) -> None:
    """
    Skill effectiveness score'unu güncelle.
    
    Args:
        skill_id: Skill ID
        success: İşlem başarılı mıydı
    """
    loader = await get_skill_loader()
    await loader.update_skill_usage(skill_id, success)
