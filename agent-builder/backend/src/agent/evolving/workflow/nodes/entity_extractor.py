"""entity_extractor — Thin orchestration node that delegates entity extraction to Celery.

All LLM calls and heavy processing are performed by the
``workspace.extract_entities`` Celery task.  This node collects OCR / chunk
results, dispatches Celery tasks, and merges the returned entities/relations.

Confidence labelling
--------------------
Each node/edge returned from Celery is annotated with a ``confidence_label``
(EXTRACTED / INFERRED / AMBIGUOUS — graphify-adopted vocabulary). If the Celery
task already returned a label, it is used; otherwise it is derived from the
numeric ``confidence`` via thresholds (see ``..confidence``). The label travels
with the entity into ``agent_knowledge`` and is consumed by ``quality_gate``
for label-aware policies (e.g. AMBIGUOUS → human_review).
"""
from __future__ import annotations

import logging
from typing import Any

from ...confidence import ConfidenceLabel, coerce_label
from ..node_registry import NodeType, ExecutionContext, PortDef, register
from ..models import PortDirection

logger = logging.getLogger(__name__)


@register
class EntityExtractorNode(NodeType):
    type_id = "entity_extractor"
    label = "Entity Cikarici"
    description = "Celery worker uzerinden goal-driven entity/relation cikarir."
    category = "processing"
    icon = "search"
    color = "#D53F8C"

    @classmethod
    def params_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "batch_size": {
                    "type": "integer",
                    "default": 5,
                    "description": "Paralel islenecek belge sayisi",
                },
                "confidence_threshold": {
                    "type": "number",
                    "default": 0.7,
                    "description": "Minimum guven esigi",
                },
                "persist_to_kb": {
                    "type": "boolean",
                    "default": True,
                    "description": "Sonuclari agent_knowledge'a kaydet",
                },
            },
        }

    @classmethod
    def input_ports(cls) -> list[PortDef]:
        return [
            PortDef(name="ocr_results", direction=PortDirection.INPUT, data_type="ocr_list"),
            PortDef(name="ontology", direction=PortDirection.INPUT, data_type="ontology"),
        ]

    @classmethod
    def output_ports(cls) -> list[PortDef]:
        return [
            PortDef(name="entities", direction=PortDirection.OUTPUT, data_type="entity_list"),
            PortDef(name="relations", direction=PortDirection.OUTPUT, data_type="relation_list"),
        ]

    async def execute(self, ctx: ExecutionContext, params: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
        ocr_results = inputs.get("ocr_results", [])
        if not ocr_results:
            return {"entities": [], "relations": []}

        if not ctx.celery_app:
            logger.error("entity_extractor: celery_app not available in ExecutionContext")
            return {"entities": [], "relations": [], "error": "celery_app not configured"}

        confidence_threshold = params.get("confidence_threshold", 0.7)
        persist = params.get("persist_to_kb", True)

        all_entities: list[dict[str, Any]] = []
        all_relations: list[dict[str, Any]] = []
        entity_id_set: set[str] = set()
        filtered_count = 0
        label_counts: dict[str, int] = {
            ConfidenceLabel.EXTRACTED.value: 0,
            ConfidenceLabel.INFERRED.value: 0,
            ConfidenceLabel.AMBIGUOUS.value: 0,
        }

        for idx, item in enumerate(ocr_results):
            text = item.get("text", "")
            file_name = item.get("file_name", "") or item.get("file_path", f"doc-{idx}")

            if not text.strip():
                continue

            doc_id = item.get("resource_id", "") or f"{ctx.run_id}-{idx}"

            task = ctx.celery_app.send_task(
                "workspace.extract_entities",
                kwargs={
                    "workspace_doc_id": doc_id,
                    "ocr_text": text,
                    "file_name": file_name,
                    "agent_id": ctx.agent_id,
                    "batch_id": ctx.run_id,
                },
                queue="default",
            )

            try:
                result = task.get(timeout=600)
            except Exception as exc:
                logger.error("Celery extract_entities failed for %s: %s", file_name, exc)
                continue

            nodes = result.get("nodes", []) if isinstance(result, dict) else []
            edges = result.get("relationships", []) if isinstance(result, dict) else []

            for node in nodes:
                nid = node.get("id", "")
                conf = float(node.get("confidence", 1.0))

                if conf < confidence_threshold:
                    filtered_count += 1
                    continue

                # Celery worker label döndürdüyse onu kullan, aksi takdirde
                # numeric confidence'tan threshold'larla türet.
                label = coerce_label(node.get("confidence_label"), fallback_confidence=conf)

                if nid and nid not in entity_id_set:
                    entity_id_set.add(nid)
                    node["source_file"] = file_name
                    node["confidence"] = conf
                    node["confidence_label"] = label.value
                    label_counts[label.value] += 1
                    all_entities.append(node)

            for edge in edges:
                edge_conf = float(edge.get("confidence", 1.0))
                if edge_conf < confidence_threshold:
                    filtered_count += 1
                    continue

                edge_label = coerce_label(
                    edge.get("confidence_label"), fallback_confidence=edge_conf
                )
                edge["source_file"] = file_name
                edge["confidence"] = edge_conf
                edge["confidence_label"] = edge_label.value
                label_counts[edge_label.value] += 1
                all_relations.append(edge)

            if ctx.notification_mgr:
                await ctx.notification_mgr.notify(
                    agent_id=ctx.agent_id,
                    event_type="entity_extraction_progress",
                    data={
                        "run_id": ctx.run_id,
                        "file_name": file_name,
                        "doc_index": idx + 1,
                        "total_docs": len(ocr_results),
                        "entities_so_far": len(all_entities),
                        "relations_so_far": len(all_relations),
                    },
                )

        if persist and (all_entities or all_relations):
            await self._persist_to_knowledge(ctx, all_entities, all_relations)

        avg_conf = 0.0
        if all_entities:
            avg_conf = sum(e.get("confidence", 1.0) for e in all_entities) / len(all_entities)

        ambiguous_count = label_counts[ConfidenceLabel.AMBIGUOUS.value]

        if ctx.notification_mgr:
            await ctx.notification_mgr.notify(
                agent_id=ctx.agent_id,
                event_type="entity_extraction_complete",
                data={
                    "run_id": ctx.run_id,
                    "entity_count": len(all_entities),
                    "relation_count": len(all_relations),
                    "filtered_count": filtered_count,
                    "avg_confidence": round(avg_conf, 3),
                    "label_counts": label_counts,
                    "ambiguous_count": ambiguous_count,
                },
            )

        logger.info(
            "entity_extractor: %d entities, %d relations from %d docs via Celery "
            "(filtered %d below %.2f, ambiguous=%d, labels=%s)",
            len(all_entities), len(all_relations), len(ocr_results),
            filtered_count, confidence_threshold, ambiguous_count, label_counts,
        )

        return {
            "entities": all_entities,
            "relations": all_relations,
            "avg_confidence": round(avg_conf, 3),
            "label_counts": label_counts,
            "ambiguous_count": ambiguous_count,
        }

    @staticmethod
    async def _persist_to_knowledge(
        ctx: ExecutionContext,
        entities: list[dict[str, Any]],
        relations: list[dict[str, Any]],
    ) -> None:
        """Persist extracted entities and relations to agent_knowledge."""
        from ...knowledge_store import KnowledgeStore

        ks = KnowledgeStore(ctx.pg)

        for entity in entities:
            eid = entity.get("id", "")
            if not eid:
                continue
            await ks.upsert(
                ctx.agent_id, "extracted_entity", eid,
                {
                    "class": entity.get("class", "Entity"),
                    "properties": entity.get("properties", {}),
                    "confidence": entity.get("confidence", 1.0),
                    "confidence_label": entity.get("confidence_label", ""),
                    "source_file": entity.get("source_file", ""),
                    "evidence": entity.get("evidence", ""),
                },
                source="workflow_entity_extractor",
            )

        for i, rel in enumerate(relations):
            rel_key = f"{rel.get('source', '')}_{rel.get('predicate', '')}_{rel.get('target', '')}_{i}"
            await ks.upsert(
                ctx.agent_id, "extracted_relation", rel_key,
                {
                    "source": rel.get("source", ""),
                    "predicate": rel.get("predicate", ""),
                    "target": rel.get("target", ""),
                    "properties": rel.get("properties", {}),
                    "confidence": rel.get("confidence", 1.0),
                    "confidence_label": rel.get("confidence_label", ""),
                    "source_file": rel.get("source_file", ""),
                    "evidence": rel.get("evidence", ""),
                },
                source="workflow_entity_extractor",
            )
