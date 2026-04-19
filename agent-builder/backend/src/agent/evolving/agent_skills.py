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

from .agent_memory import AgentMemory
from .knowledge_store import KnowledgeStore
from .wiki_store import WikiStore

logger = logging.getLogger(__name__)


class AgentSkillManager:
    """Ontoloji -> SkillExecution donusumu ve API endpoint desteği."""

    def __init__(self, store: KnowledgeStore, memory: AgentMemory, wiki: WikiStore | None = None):
        self.store = store
        self.memory = memory
        self.wiki = wiki

    async def ontology_to_skill_execution(
        self,
        agent_id: str,
        categories: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Agent'in ontolojisini AgenticOCR SkillExecution formatina donustur.

        AgenticOCR'in bekledigi format (agentic_ocr.py L331-341):
            skill_id, name, category, prompt_template,
            input_schema, output_schema,
            entity_schemas, relationship_schemas,
            version, effectiveness_score

        Args:
            agent_id: Agent identifier.
            categories: Optional wiki kategorileri filtresi. None ise tum
                kategoriler prompt'a dahil edilir. 20K production'da token
                butcesini kontrol etmek icin kullanilir.
        """
        ontology = await self.store.load_ontology(agent_id)
        identity = await self.store.load_identity(agent_id)

        if ontology.is_empty:
            return {}

        wiki_context = ""
        if self.wiki:
            try:
                wiki_context = await self.wiki.build_extraction_context(
                    agent_id, categories=categories,
                )
            except Exception as exc:
                logger.warning("Wiki context unavailable: %s", exc)

        prompt_template = self.memory.build_extraction_prompt(ontology, wiki_context=wiki_context)

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

        page_count = 0
        token_estimate = len(prompt_template) // 4
        if self.wiki:
            try:
                all_pages = await self.wiki.list_pages(agent_id)
                if categories:
                    allowed = set(categories)
                    page_count = sum(
                        1 for p in all_pages
                        if not p["path"].startswith("_") and p.get("category") in allowed
                    )
                else:
                    page_count = sum(1 for p in all_pages if not p["path"].startswith("_"))
            except Exception:
                pass

        from datetime import datetime as _dt
        scene_metadata = {
            "categories": categories or [],
            "page_count": page_count,
            "token_estimate": token_estimate,
            "frozen_at": _dt.utcnow().isoformat(),
        }

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
            "scene_metadata": scene_metadata,
        }

    async def save_skill(
        self,
        agent_id: str,
        categories: list[str] | None = None,
    ) -> dict[str, Any]:
        """Ontolojiyi skill olarak kaydet ve geri don.

        Args:
            agent_id: Agent identifier.
            categories: Optional wiki kategori filtresi. Snapshot'a hangi
                wiki kategorilerinin dahil edilecegini belirler.
        """
        skill_data = await self.ontology_to_skill_execution(agent_id, categories=categories)
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
