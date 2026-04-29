"""kg_writer — Thin orchestration node that delegates Neo4j writes to Celery.

All Neo4j interactions are performed by the ``src.neo4j_writer.neo4j_batch_write_task``
Celery task.  This node groups entities by label and relations by predicate,
then dispatches batch write operations via Celery.
"""
from __future__ import annotations

import logging
from typing import Any

from ..node_registry import NodeType, ExecutionContext, PortDef, register
from ..models import PortDirection

logger = logging.getLogger(__name__)

BATCH_SIZE = 50


def _normalize_id(raw_id: str) -> str:
    return raw_id.strip().replace(" ", "_").replace("-", "_")


def _safe_label(label: str) -> str:
    result = "".join(c for c in label if c.isalnum() or c == "_")
    return result or "Entity"


@register
class KgWriterNode(NodeType):
    type_id = "kg_writer"
    label = "KG Yazici"
    description = "Celery worker uzerinden entity ve relation'lari Neo4j'e batch yazar."
    category = "output"
    icon = "database"
    color = "#2B6CB0"

    @classmethod
    def params_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "neo4j_database": {"type": "string", "default": "neo4j"},
                "merge_strategy": {
                    "type": "string",
                    "enum": ["merge", "create"],
                    "default": "merge",
                },
                "batch_size": {
                    "type": "integer",
                    "default": 50,
                    "description": "Tek seferde yazilacak node/edge sayisi",
                },
            },
        }

    @classmethod
    def input_ports(cls) -> list[PortDef]:
        return [
            PortDef(name="entities", direction=PortDirection.INPUT, data_type="entity_list"),
            PortDef(name="relations", direction=PortDirection.INPUT, data_type="relation_list"),
        ]

    @classmethod
    def output_ports(cls) -> list[PortDef]:
        return [PortDef(name="kg_stats", direction=PortDirection.OUTPUT, data_type="dict")]

    async def execute(self, ctx: ExecutionContext, params: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
        entities = inputs.get("entities", [])
        relations = inputs.get("relations", [])

        if not entities and not relations:
            return {"kg_stats": {"entities_written": 0, "relations_written": 0, "status": "skipped"}}

        if not ctx.celery_app:
            logger.error("kg_writer: celery_app not available in ExecutionContext")
            return {"kg_stats": {"entities_written": 0, "relations_written": 0, "status": "failed", "error": "celery_app not configured"}}

        database = params.get("neo4j_database", "neo4j")
        strategy = params.get("merge_strategy", "merge")
        batch_sz = params.get("batch_size", BATCH_SIZE)

        entities_written = 0
        relations_written = 0
        errors: list[str] = []

        entities_by_label: dict[str, list[dict]] = {}
        for entity in entities:
            label = _safe_label(entity.get("class", "Entity"))
            eid = _normalize_id(entity.get("id", ""))
            if not eid:
                continue
            props = dict(entity.get("properties", {}))
            props["id"] = eid
            if "name" not in props:
                props["name"] = eid.replace("_", " ").title()
            entities_by_label.setdefault(label, []).append(props)

        for label, items in entities_by_label.items():
            for i in range(0, len(items), batch_sz):
                batch = items[i:i + batch_sz]
                verb = "MERGE" if strategy == "merge" else "CREATE"
                set_clause = "SET n += row" if strategy == "merge" else "SET n = row"
                query = (
                    f"UNWIND $batch AS row "
                    f"{verb} (n:`{label}` {{id: row.id}}) "
                    f"{set_clause} "
                    f"RETURN count(n) AS cnt"
                )
                operations = [{
                    "operation": "custom_query",
                    "params": {"query": query, "params": {"batch": batch}},
                }]

                task = ctx.celery_app.send_task(
                    "src.neo4j_writer.neo4j_batch_write_task",
                    args=[operations, database],
                    queue="neo4j_write",
                )

                try:
                    result = task.get(timeout=300)
                    if isinstance(result, dict) and result.get("status") == "success":
                        entities_written += len(batch)
                except Exception as exc:
                    logger.warning("Celery batch entity write failed for label %s: %s", label, exc)
                    errors.append(f"entity batch {label}[{i}:{i+batch_sz}]: {exc}")

                if ctx.notification_mgr:
                    await ctx.notification_mgr.notify(
                        agent_id=ctx.agent_id,
                        event_type="kg_write_progress",
                        data={
                            "run_id": ctx.run_id,
                            "phase": "entities",
                            "written": entities_written,
                            "total": len(entities),
                        },
                    )

        rels_by_pred: dict[str, list[dict]] = {}
        for rel in relations:
            source_id = _normalize_id(rel.get("source", ""))
            target_id = _normalize_id(rel.get("target", ""))
            predicate = rel.get("predicate", "RELATED_TO")
            if not source_id or not target_id:
                continue
            safe_pred = "".join(c for c in predicate if c.isalnum() or c == "_") or "RELATED_TO"
            edge_props = dict(rel.get("properties", {}))
            rels_by_pred.setdefault(safe_pred, []).append({
                "src": source_id,
                "tgt": target_id,
                "props": edge_props,
            })

        for pred, items in rels_by_pred.items():
            for i in range(0, len(items), batch_sz):
                batch = items[i:i + batch_sz]
                verb = "MERGE" if strategy == "merge" else "CREATE"
                set_clause = "SET r += row.props" if strategy == "merge" else "SET r = row.props"
                query = (
                    f"UNWIND $batch AS row "
                    f"MATCH (a {{id: row.src}}), (b {{id: row.tgt}}) "
                    f"{verb} (a)-[r:`{pred}`]->(b) "
                    f"{set_clause} "
                    f"RETURN count(r) AS cnt"
                )
                operations = [{
                    "operation": "custom_query",
                    "params": {"query": query, "params": {"batch": batch}},
                }]

                task = ctx.celery_app.send_task(
                    "src.neo4j_writer.neo4j_batch_write_task",
                    args=[operations, database],
                    queue="neo4j_write",
                )

                try:
                    result = task.get(timeout=300)
                    if isinstance(result, dict) and result.get("status") == "success":
                        relations_written += len(batch)
                except Exception as exc:
                    logger.warning("Celery batch relation write failed for pred %s: %s", pred, exc)
                    errors.append(f"rel batch {pred}[{i}:{i+batch_sz}]: {exc}")

                if ctx.notification_mgr:
                    await ctx.notification_mgr.notify(
                        agent_id=ctx.agent_id,
                        event_type="kg_write_progress",
                        data={
                            "run_id": ctx.run_id,
                            "phase": "relations",
                            "written": relations_written,
                            "total": len(relations),
                        },
                    )

        status = "completed" if not errors else "partial"
        logger.info(
            "kg_writer: %d entities, %d relations written via Celery (%d errors)",
            entities_written, relations_written, len(errors),
        )

        if ctx.notification_mgr:
            await ctx.notification_mgr.notify(
                agent_id=ctx.agent_id,
                event_type="kg_write_complete",
                data={
                    "run_id": ctx.run_id,
                    "entities_written": entities_written,
                    "relations_written": relations_written,
                    "error_count": len(errors),
                    "status": status,
                },
            )

        return {
            "kg_stats": {
                "entities_written": entities_written,
                "relations_written": relations_written,
                "errors": errors[:10],
                "status": status,
            },
        }
