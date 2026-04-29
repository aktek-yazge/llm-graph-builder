"""ontology_designer — Thin orchestration node that delegates to Celery.

All LLM-based ontology design is performed by the ``workspace.design_ontology``
Celery task.  This node assembles wiki content, dispatches the task, and
handles ontology merging and HITL approval.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from ..node_registry import NodeType, ExecutionContext, PortDef, register
from ..models import PortDirection

logger = logging.getLogger(__name__)


@register
class OntologyDesignerNode(NodeType):
    type_id = "ontology_designer"
    label = "Ontoloji Tasarimcisi"
    description = "Celery worker uzerinden wiki sayfalarindan ontoloji onerisi yapar; kullanici onayi gerektirir."
    category = "design"
    icon = "diagram"
    color = "#805AD5"

    @classmethod
    def params_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "auto_suggest": {
                    "type": "boolean",
                    "default": True,
                    "description": "LLM ile otomatik entity/relation onerisi",
                },
                "require_approval": {
                    "type": "boolean",
                    "default": True,
                    "description": "Kullanici onaylama adimi (pipeline durur)",
                },
                "max_wiki_chars": {
                    "type": "integer",
                    "default": 15000,
                    "description": "LLM'e gonderilecek maks wiki icerik karakteri",
                },
            },
        }

    @classmethod
    def input_ports(cls) -> list[PortDef]:
        return [PortDef(name="wiki_pages", direction=PortDirection.INPUT, data_type="wiki_page_list")]

    @classmethod
    def output_ports(cls) -> list[PortDef]:
        return [PortDef(name="ontology", direction=PortDirection.OUTPUT, data_type="ontology")]

    async def execute(self, ctx: ExecutionContext, params: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
        from ...knowledge_store import KnowledgeStore
        from ...ontology_model import AgentOntology

        ks = KnowledgeStore(ctx.pg)
        existing_ontology = await ks.load_ontology(ctx.agent_id)

        auto_suggest = params.get("auto_suggest", True)
        max_wiki_chars = params.get("max_wiki_chars", 15000)

        if not auto_suggest:
            return {"ontology": existing_ontology.to_dict() if existing_ontology else {}}

        if not ctx.celery_app:
            logger.error("ontology_designer: celery_app not available in ExecutionContext")
            return {"ontology": existing_ontology.to_dict() if existing_ontology else {}, "error": "celery_app not configured"}

        wiki_pages = inputs.get("wiki_pages", [])
        wiki_content = self._build_wiki_content(wiki_pages, max_wiki_chars)

        if not wiki_content.strip():
            from ...wiki_store import WikiStore
            wiki = WikiStore(ks)
            wiki_content = await wiki.build_extraction_context(ctx.agent_id)
            wiki_content = wiki_content[:max_wiki_chars]

        if not wiki_content.strip():
            return {"ontology": existing_ontology.to_dict() if existing_ontology else {}}

        existing_json = ""
        if existing_ontology and not existing_ontology.is_empty:
            existing_json = json.dumps(existing_ontology.to_dict(), ensure_ascii=False, indent=2)

        if ctx.notification_mgr:
            await ctx.notification_mgr.notify(
                agent_id=ctx.agent_id,
                event_type="ontology_designing",
                data={"run_id": ctx.run_id, "status": "celery_processing"},
            )

        task = ctx.celery_app.send_task(
            "workspace.design_ontology",
            kwargs={
                "doc_id": ctx.run_id,
                "wiki_content": wiki_content,
                "existing_ontology_json": existing_json,
                "max_wiki_chars": max_wiki_chars,
                "agent_id": ctx.agent_id,
            },
            queue="default",
        )

        try:
            result = task.get(timeout=600)
        except Exception as exc:
            logger.error("Celery design_ontology failed: %s", exc)
            return {"ontology": existing_ontology.to_dict() if existing_ontology else {}}

        proposed = result.get("proposed_ontology", {}) if isinstance(result, dict) else {}
        if not proposed:
            logger.warning("Celery design_ontology returned empty ontology")
            return {"ontology": existing_ontology.to_dict() if existing_ontology else {}}

        try:
            proposed_ontology = AgentOntology.from_dict(proposed)
            merged = self._merge_ontologies(existing_ontology, proposed_ontology)
            await ks.save_ontology(ctx.agent_id, merged)
        except Exception as exc:
            logger.error("Ontology merge/save failed: %s", exc)
            return {"ontology": existing_ontology.to_dict() if existing_ontology else {}}

        if params.get("require_approval", True):
            if ctx.notification_mgr:
                await ctx.notification_mgr.notify(
                    agent_id=ctx.agent_id,
                    event_type="human_review_requested",
                    data={
                        "run_id": ctx.run_id,
                        "review_type": "ontology_approval",
                        "entity_count": len(merged.entity_classes),
                        "relationship_count": len(merged.relationship_predicates),
                    },
                )

            from .human_review import HumanReviewPending
            raise HumanReviewPending(
                run_id=ctx.run_id,
                message=(
                    f"Ontoloji tasarimi tamamlandi: {len(merged.entity_classes)} entity, "
                    f"{len(merged.relationship_predicates)} relationship. "
                    f"Devam etmek icin onaylayin."
                ),
            )

        return {"ontology": merged.to_dict()}

    def _build_wiki_content(self, wiki_pages: list[dict], max_chars: int) -> str:
        parts: list[str] = []
        total = 0
        for page in wiki_pages:
            title = page.get("title", page.get("path", ""))
            summary = page.get("summary", "")
            text = page.get("text", "")
            content = summary or text

            if not content:
                continue

            section = f"### {title}\n{content}\n"
            if total + len(section) > max_chars:
                remaining = max_chars - total
                if remaining > 100:
                    parts.append(section[:remaining])
                break
            parts.append(section)
            total += len(section)

        return "\n".join(parts)

    def _merge_ontologies(self, existing, proposed):
        from ...ontology_model import AgentOntology

        if not existing or existing.is_empty:
            return proposed

        merged = AgentOntology(
            domain=proposed.domain or existing.domain,
            goal=proposed.goal or existing.goal,
            entity_classes=list(existing.entity_classes),
            relationship_predicates=list(existing.relationship_predicates),
            inference_rules=list(existing.inference_rules),
            constraints=list(existing.constraints),
        )

        existing_entity_names = {e.name for e in existing.entity_classes}
        for entity in proposed.entity_classes:
            if entity.name not in existing_entity_names:
                merged.entity_classes.append(entity)

        existing_rel_names = {r.name for r in existing.relationship_predicates}
        for rel in proposed.relationship_predicates:
            if rel.name not in existing_rel_names:
                merged.relationship_predicates.append(rel)

        existing_constraints = set(existing.constraints)
        for c in proposed.constraints:
            if c not in existing_constraints:
                merged.constraints.append(c)

        return merged
