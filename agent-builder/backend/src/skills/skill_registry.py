"""
Skill Registry
==============

Skills'lerin PostgreSQL'de yonetimi.
CRUD operasyonlari ve skill execution icin veri hazirlama.
AgentRepository uzerinden islem yapar.
"""

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..agent_repository import AgentRepository
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


@dataclass
class SkillExecution:
    """
    Agentic OCR'da kullanilacak skill bilgileri.
    Runtime execution icin optimize edilmis format.
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
        return json.dumps(self.entity_schemas, ensure_ascii=False, indent=2)

    @property
    def relationship_schemas_json(self) -> str:
        return json.dumps(self.relationship_schemas, ensure_ascii=False, indent=2)

    def format_prompt(self, **kwargs) -> str:
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


class SkillRegistry:
    """
    Skill yonetimi icin registry sinifi.
    PostgreSQL AgentRepository uzerinden CRUD islemleri.
    """

    def __init__(self, agent_repo: AgentRepository):
        self.repo = agent_repo

    # =========================================================================
    # CREATE OPERATIONS
    # =========================================================================

    async def create_skill(self, skill_data: SkillCreate) -> str:
        skill_id = await self.repo.create_skill({
            "name": skill_data.name,
            "description": skill_data.description,
            "skill_category": skill_data.skill_category.value,
            "prompt_template": skill_data.prompt_template,
            "input_schema": skill_data.input_schema or {},
            "output_schema": skill_data.output_schema or {},
            "is_global": skill_data.is_global,
            "tenant_id": skill_data.tenant_id,
        })

        if skill_data.entity_schema_ids:
            for es_id in skill_data.entity_schema_ids:
                await self.repo.link_skill_entity_schema(skill_id, es_id)

        logger.info(f"Created skill: {skill_id} ({skill_data.name})")
        return skill_id

    # =========================================================================
    # READ OPERATIONS
    # =========================================================================

    async def get_skill(self, skill_id: str) -> Optional[Skill]:
        s = await self.repo.get_skill(skill_id)
        if not s:
            return None

        return Skill(
            id=s["id"],
            name=s["name"],
            description=s.get("description", ""),
            skill_category=SkillCategory(s.get("skill_category", "extraction")),
            prompt_template=s.get("prompt_template", ""),
            input_schema=s.get("input_schema"),
            output_schema=s.get("output_schema"),
            version=s.get("version", 1),
            effectiveness_score=s.get("effectiveness_score", 0.5),
            usage_count=s.get("usage_count", 0),
            is_global=s.get("is_global", False),
            tenant_id=s.get("tenant_id"),
            created_at=s.get("created_at"),
            updated_at=s.get("updated_at"),
        )

    async def get_skill_for_execution(self, skill_id: str) -> Optional[SkillExecution]:
        data = await self.repo.get_skill_for_execution(skill_id)
        if not data:
            return None

        entity_schemas = [
            {
                "entity_type": e.get("entity_type", ""),
                "description": e.get("description", ""),
                "properties": e.get("properties", {}),
            }
            for e in data.get("entity_schemas", [])
            if e.get("entity_type")
        ]

        rel_schemas = [
            {
                "relationship_type": rs.get("relationship_type", ""),
                "source_entity": rs.get("source_entity", ""),
                "target_entity": rs.get("target_entity", ""),
                "description": rs.get("description", ""),
            }
            for rs in data.get("relationship_schemas", [])
            if rs.get("relationship_type")
        ]

        return SkillExecution(
            skill_id=data["id"],
            name=data["name"],
            category=data.get("skill_category", "extraction"),
            prompt_template=data.get("prompt_template", ""),
            input_schema=data.get("input_schema", {}),
            output_schema=data.get("output_schema", {}),
            entity_schemas=entity_schemas,
            relationship_schemas=rel_schemas,
        )

    async def list_skills_for_tenant(
        self,
        tenant_id: str,
        include_global: bool = True,
        category: Optional[SkillCategory] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[SkillSummary]:
        rows = await self.repo.list_skills(
            tenant_id=tenant_id,
            include_global=include_global,
            category=category.value if category else None,
            limit=limit,
            offset=offset,
        )
        return [
            SkillSummary(
                id=r["id"],
                name=r["name"],
                description=r.get("description", ""),
                category=SkillCategory(r.get("skill_category", "extraction")),
                effectiveness_score=r.get("effectiveness_score", 0.5),
                is_global=r.get("is_global", False),
            )
            for r in rows
        ]

    # =========================================================================
    # UPDATE OPERATIONS
    # =========================================================================

    async def update_skill(self, skill_id: str, updates: Dict[str, Any]) -> bool:
        return await self.repo.update_skill(skill_id, updates)

    async def increment_version(self, skill_id: str) -> int:
        return await self.repo.increment_skill_version(skill_id)

    # =========================================================================
    # DELETE OPERATIONS
    # =========================================================================

    async def delete_skill(self, skill_id: str, tenant_id: str) -> bool:
        deleted = await self.repo.delete_skill(skill_id, tenant_id)
        if deleted:
            logger.info(f"Deleted skill: {skill_id}")
        return deleted
