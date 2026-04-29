"""entity_resolution — Thin orchestration node that delegates to Celery.

All duplicate-entity merging is performed by the ``tasks.entity_resolution_task``
Celery task.  This node simply dispatches the task and returns the result.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from ..node_registry import NodeType, ExecutionContext, PortDef, register
from ..models import PortDirection

logger = logging.getLogger(__name__)


@register
class EntityResolutionNode(NodeType):
    type_id = "entity_resolution"
    label = "Varlik Birlesturme"
    description = "Celery worker uzerinden ayni entity'leri tespit eder ve birlestir."
    category = "processing"
    icon = "git-merge"
    color = "#38B2AC"

    @classmethod
    def params_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "similarity_threshold": {
                    "type": "number",
                    "default": 0.85,
                    "description": "Fuzzy esik (0.0-1.0)",
                },
            },
        }

    @classmethod
    def input_ports(cls) -> list[PortDef]:
        return [PortDef(name="entities", direction=PortDirection.INPUT, data_type="entity_list")]

    @classmethod
    def output_ports(cls) -> list[PortDef]:
        return [PortDef(name="resolved_entities", direction=PortDirection.OUTPUT, data_type="entity_list")]

    async def execute(self, ctx: ExecutionContext, params: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
        entities = inputs.get("entities", [])
        if not entities:
            return {"resolved_entities": []}

        if not ctx.celery_app:
            logger.error("entity_resolution: celery_app not available in ExecutionContext")
            return {"resolved_entities": entities, "error": "celery_app not configured"}

        neo4j_uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")

        task = ctx.celery_app.send_task(
            "tasks.entity_resolution_task",
            kwargs={"neo4j_uri": neo4j_uri},
            queue="default",
        )

        try:
            result = task.get(timeout=600)
        except Exception as exc:
            logger.error("Celery entity_resolution_task failed: %s", exc)
            return {"resolved_entities": entities}

        summary = result.get("summary", {}) if isinstance(result, dict) else {}
        merge_count = summary.get("merged_count", 0)

        if ctx.notification_mgr:
            await ctx.notification_mgr.notify(
                agent_id=ctx.agent_id,
                event_type="entity_resolution_complete",
                data={
                    "run_id": ctx.run_id,
                    "original_count": len(entities),
                    "merged_count": merge_count,
                    "celery_result": summary,
                },
            )

        logger.info(
            "entity_resolution: dispatched to Celery, %d merges reported",
            merge_count,
        )

        return {"resolved_entities": entities}
