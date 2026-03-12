"""
Agent Skills
=============

Agent'in ontolojisini AgenticOCR'in SkillExecution formatina donusturur.
Celery worker'in bekledigi API response formatini saglar.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .ontology_model import AgentOntology
from .agent_memory import AgentMemory
from .knowledge_store import KnowledgeStore

logger = logging.getLogger(__name__)


class AgentSkillManager:
    """Ontoloji -> SkillExecution donusumu ve API endpoint desteği."""

    def __init__(self, store: KnowledgeStore, memory: AgentMemory):
        self.store = store
        self.memory = memory

    async def ontology_to_skill_execution(self, agent_id: str) -> dict[str, Any]:
        """
        Agent'in ontolojisini AgenticOCR SkillExecution formatina donustur.

        AgenticOCR'in bekledigi format (agentic_ocr.py L331-341):
            skill_id, name, category, prompt_template,
            input_schema, output_schema,
            entity_schemas, relationship_schemas,
            version, effectiveness_score
        """
        ontology = await self.store.load_ontology(agent_id)
        identity = await self.store.load_identity(agent_id)

        if ontology.is_empty:
            return {}

        prompt_template = self.memory.build_extraction_prompt(ontology)

        entity_schemas = []
        for ec in ontology.entity_classes:
            schema: dict[str, Any] = {
                "name": ec.name,
                "description": ec.description,
                "properties": {},
            }
            for prop in ec.properties:
                schema["properties"][prop.name] = {
                    "type": prop.type,
                    "constraint": prop.constraint,
                    "description": prop.description,
                }
            if ec.parent:
                schema["parent"] = ec.parent
            entity_schemas.append(schema)

        relationship_schemas = []
        for rp in ontology.relationship_predicates:
            relationship_schemas.append({
                "name": rp.name,
                "source": rp.source,
                "target": rp.target,
                "edge_properties": rp.edge_properties,
                "description": rp.description,
            })

        version_row = await self.store.get(agent_id, "ontology", "full")
        version = version_row["version"] if version_row else 1

        return {
            "skill_id": f"agent-{agent_id}-extraction",
            "name": f"{identity.get('name', 'Agent')} - {ontology.domain} Extraction",
            "category": "extraction",
            "prompt_template": prompt_template,
            "input_schema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "OCR ile cikarilmis belge metni"},
                },
                "required": ["text"],
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "nodes": {"type": "array"},
                    "edges": {"type": "array"},
                },
            },
            "entity_schemas": entity_schemas,
            "relationship_schemas": relationship_schemas,
            "version": version,
            "effectiveness_score": 0.5,
        }

    async def save_skill(self, agent_id: str) -> dict[str, Any]:
        """Ontolojiyi skill olarak kaydet ve geri don."""
        skill_data = await self.ontology_to_skill_execution(agent_id)
        if not skill_data:
            return {"error": "Ontoloji bos, once entity'ler ekleyin."}

        await self.store.upsert(
            agent_id,
            "skill_execution",
            "latest",
            skill_data,
            source="system",
            confidence=1.0,
        )
        logger.info("Skill saved for agent %s: %s", agent_id, skill_data["skill_id"])
        return skill_data

    async def get_skill_for_api(self, agent_id: str) -> dict[str, Any] | None:
        """
        API endpoint icin skill dondur.
        AgenticOCR /api/v2/agent-builder/skills/{skill_id}/execution endpoint'ini karsilar.
        """
        row = await self.store.get(agent_id, "skill_execution", "latest")
        if not row:
            return await self.ontology_to_skill_execution(agent_id)
        value = row["value"]
        if isinstance(value, str):
            value = json.loads(value)
        return value if value else None
