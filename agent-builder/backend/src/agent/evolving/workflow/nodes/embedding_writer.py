"""embedding_writer — Thin orchestration node that delegates embedding to Celery.

All embedding generation and Neo4j writes are performed by the
``workspace.create_embeddings`` Celery task.  This node collects texts from
chunks and entities, then dispatches the Celery task.
"""
from __future__ import annotations

import logging
from typing import Any

from ..node_registry import NodeType, ExecutionContext, PortDef, register
from ..models import PortDirection

logger = logging.getLogger(__name__)


@register
class EmbeddingWriterNode(NodeType):
    type_id = "embedding_writer"
    label = "Embedding Yazici"
    description = "Celery worker uzerinden vector embedding olusturur ve Neo4j'ye yazar."
    category = "output"
    icon = "cpu"
    color = "#9F7AEA"

    @classmethod
    def params_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "enum": ["openai", "gemini", "sentence-transformers"],
                    "default": "openai",
                    "description": "Embedding model secimi",
                },
                "batch_size": {
                    "type": "integer",
                    "default": 50,
                    "description": "Batch basina islenecek metin sayisi",
                },
            },
        }

    @classmethod
    def input_ports(cls) -> list[PortDef]:
        return [
            PortDef(name="chunks", direction=PortDirection.INPUT, data_type="chunk_list"),
            PortDef(name="entities", direction=PortDirection.INPUT, data_type="entity_list"),
        ]

    @classmethod
    def output_ports(cls) -> list[PortDef]:
        return [PortDef(name="embedding_stats", direction=PortDirection.OUTPUT, data_type="dict")]

    async def execute(self, ctx: ExecutionContext, params: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
        chunks = inputs.get("chunks", [])
        entities = inputs.get("entities", [])

        if not chunks and not entities:
            return {"embedding_stats": {"status": "skipped", "total_embedded": 0}}

        if not ctx.celery_app:
            logger.error("embedding_writer: celery_app not available in ExecutionContext")
            return {"embedding_stats": {"status": "failed", "total_embedded": 0, "error": "celery_app not configured"}}

        model_name = params.get("model", "openai")
        batch_size = params.get("batch_size", 50)

        texts: list[str] = []
        entity_ids: list[str] = []

        for chunk in chunks:
            text = chunk.get("text", "")
            if text.strip():
                texts.append(text)
                entity_ids.append("")

        for entity in entities:
            name = entity.get("properties", {}).get("name", entity.get("id", ""))
            evidence = entity.get("evidence", "")
            text = f"{name}: {evidence}" if evidence else name
            if text.strip():
                texts.append(text)
                entity_ids.append(entity.get("id", ""))

        if not texts:
            return {"embedding_stats": {"status": "skipped", "total_embedded": 0}}

        embedded_count = 0
        errors: list[str] = []

        for i in range(0, len(texts), batch_size):
            batch_texts = texts[i:i + batch_size]
            batch_ids = entity_ids[i:i + batch_size]
            valid_ids = [eid for eid in batch_ids if eid]

            task = ctx.celery_app.send_task(
                "workspace.create_embeddings",
                kwargs={
                    "doc_id": f"{ctx.run_id}-batch-{i}",
                    "texts": batch_texts,
                    "entity_ids": valid_ids if valid_ids else None,
                    "model": model_name,
                    "agent_id": ctx.agent_id,
                },
                queue="default",
            )

            try:
                result = task.get(timeout=600)
                embedded_count += result.get("embedded_count", 0) if isinstance(result, dict) else 0
            except Exception as exc:
                logger.warning("Celery create_embeddings batch %d failed: %s", i, exc)
                errors.append(f"batch[{i}:{i+batch_size}]: {exc}")

            if ctx.notification_mgr:
                await ctx.notification_mgr.notify(
                    agent_id=ctx.agent_id,
                    event_type="embedding_progress",
                    data={
                        "run_id": ctx.run_id,
                        "embedded": embedded_count,
                        "total": len(texts),
                    },
                )

        status = "completed" if not errors else "partial"
        logger.info("embedding_writer: %d/%d embedded via Celery (%d errors)", embedded_count, len(texts), len(errors))

        return {
            "embedding_stats": {
                "status": status,
                "total_embedded": embedded_count,
                "total_texts": len(texts),
                "chunk_count": len(chunks),
                "entity_count": len(entities),
                "errors": errors[:5],
            },
        }
