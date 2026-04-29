"""chunking — Thin orchestration node that delegates text splitting to Celery.

All heavy processing is performed by the ``workspace.chunk_text`` Celery task.
This node simply collects OCR results, dispatches one Celery task per document,
waits for the results and forwards them downstream.
"""
from __future__ import annotations

import logging
from typing import Any

from ..node_registry import NodeType, ExecutionContext, PortDef, register
from ..models import PortDirection

logger = logging.getLogger(__name__)


@register
class ChunkingNode(NodeType):
    type_id = "chunking"
    label = "Metin Parcalayici"
    description = "OCR sonrasi metinleri Celery worker uzerinden parcalara boler."
    category = "processing"
    icon = "scissors"
    color = "#ED8936"

    @classmethod
    def params_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "chunk_size": {
                    "type": "integer",
                    "default": 2000,
                    "description": "Hedef chunk boyutu (karakter)",
                },
                "overlap": {
                    "type": "integer",
                    "default": 200,
                    "description": "Chunk'lar arasi ust uste binen karakter",
                },
                "strategy": {
                    "type": "string",
                    "enum": ["fixed_size", "sentence_based"],
                    "default": "sentence_based",
                    "description": "Parcalama stratejisi",
                },
            },
        }

    @classmethod
    def input_ports(cls) -> list[PortDef]:
        return [PortDef(name="ocr_results", direction=PortDirection.INPUT, data_type="ocr_list")]

    @classmethod
    def output_ports(cls) -> list[PortDef]:
        return [PortDef(name="chunks", direction=PortDirection.OUTPUT, data_type="chunk_list")]

    async def execute(self, ctx: ExecutionContext, params: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
        ocr_results = inputs.get("ocr_results", [])
        if not ocr_results:
            return {"chunks": []}

        chunk_size = params.get("chunk_size", 2000)
        overlap = params.get("overlap", 200)
        strategy = params.get("strategy", "sentence_based")

        if not ctx.celery_app:
            logger.error("chunking: celery_app not available in ExecutionContext")
            return {"chunks": [], "error": "celery_app not configured"}

        all_chunks: list[dict[str, Any]] = []

        for idx, item in enumerate(ocr_results):
            text = item.get("text", "")
            file_name = item.get("file_name", "") or item.get("file_path", f"doc-{idx}")

            if not text.strip():
                continue

            doc_id = item.get("resource_id", "") or f"{ctx.run_id}-{idx}"

            task = ctx.celery_app.send_task(
                "workspace.chunk_text",
                kwargs={
                    "doc_id": doc_id,
                    "text": text,
                    "file_name": file_name,
                    "chunk_size": chunk_size,
                    "overlap": overlap,
                    "strategy": strategy,
                    "agent_id": ctx.agent_id,
                },
                queue="default",
            )

            try:
                result = task.get(timeout=300)
            except Exception as exc:
                logger.error("Celery chunk_text failed for %s: %s", file_name, exc)
                continue

            for chunk in result.get("chunks", []):
                all_chunks.append({
                    "text": chunk.get("text", ""),
                    "file_name": file_name,
                    "file_path": item.get("file_path", ""),
                    "resource_id": item.get("resource_id", ""),
                    "chunk_index": chunk.get("index", 0),
                    "total_chunks": result.get("total_chunks", 0),
                    "char_count": chunk.get("char_count", 0),
                    "source": "celery",
                })

            if ctx.notification_mgr:
                await ctx.notification_mgr.notify(
                    agent_id=ctx.agent_id,
                    event_type="chunking_progress",
                    data={
                        "run_id": ctx.run_id,
                        "file_name": file_name,
                        "chunks_created": result.get("total_chunks", 0),
                        "doc_index": idx + 1,
                        "total_docs": len(ocr_results),
                    },
                )

        logger.info(
            "chunking: %d chunks from %d docs via Celery (strategy=%s, size=%d, overlap=%d)",
            len(all_chunks), len(ocr_results), strategy, chunk_size, overlap,
        )

        return {"chunks": all_chunks}
