"""community_detection — Thin orchestration node that delegates to Celery.

All GDS community detection is performed by the ``tasks.community_detection_task``
Celery task.  This node dispatches the task and returns the result.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from ..node_registry import NodeType, ExecutionContext, PortDef, register
from ..models import PortDirection

logger = logging.getLogger(__name__)


@register
class CommunityDetectionNode(NodeType):
    type_id = "community_detection"
    label = "Topluluk Tespiti"
    description = "Celery worker uzerinden knowledge graph topluluk tespiti yapar."
    category = "analysis"
    icon = "share-2"
    color = "#4FD1C5"

    @classmethod
    def params_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "algorithm": {
                    "type": "string",
                    "enum": ["louvain", "leiden", "label_propagation"],
                    "default": "leiden",
                    "description": "Topluluk tespit algoritmasi",
                },
            },
        }

    @classmethod
    def input_ports(cls) -> list[PortDef]:
        return [PortDef(name="kg_stats", direction=PortDirection.INPUT, data_type="dict")]

    @classmethod
    def output_ports(cls) -> list[PortDef]:
        return [PortDef(name="communities", direction=PortDirection.OUTPUT, data_type="dict")]

    async def execute(self, ctx: ExecutionContext, params: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
        algorithm = params.get("algorithm", "leiden")

        if not ctx.celery_app:
            logger.error("community_detection: celery_app not available in ExecutionContext")
            return {"communities": {"status": "failed", "error": "celery_app not configured"}}

        uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        user = os.getenv("NEO4J_USERNAME", "neo4j")
        password = os.getenv("NEO4J_PASSWORD", "password")
        database = os.getenv("NEO4J_DATABASE", "neo4j")

        task = ctx.celery_app.send_task(
            "tasks.community_detection_task",
            kwargs={
                "neo4j_uri": uri,
                "neo4j_username": user,
                "neo4j_password": password,
                "neo4j_database": database,
            },
            queue="default",
        )

        try:
            result = task.get(timeout=600)
        except Exception as exc:
            logger.error("Celery community_detection_task failed: %s", exc)
            return {"communities": {
                "status": "failed",
                "error": f"Celery task failed: {exc}",
                "algorithm": algorithm,
            }}

        community_count = result.get("community_count", 0) if isinstance(result, dict) else 0

        if ctx.notification_mgr:
            await ctx.notification_mgr.notify(
                agent_id=ctx.agent_id,
                event_type="community_detection_complete",
                data={
                    "run_id": ctx.run_id,
                    "community_count": community_count,
                    "algorithm": algorithm,
                },
            )

        logger.info(
            "community_detection: %d communities found via Celery (algorithm=%s)",
            community_count, algorithm,
        )

        return {"communities": {
            "status": "completed",
            "algorithm": algorithm,
            "community_count": community_count,
            "celery_result": result,
        }}
