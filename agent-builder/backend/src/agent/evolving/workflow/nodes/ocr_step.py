"""ocr_step — OCR processing: cache existing results, run Gemini for new files."""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import uuid
from typing import Any

from ..node_registry import NodeType, ExecutionContext, PortDef, register
from ..models import PortDirection

logger = logging.getLogger(__name__)

CELERY_WORKER_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", "..", "celery_worker")
)


def _ensure_celery_worker_importable():
    worker_src = os.path.join(CELERY_WORKER_ROOT, "src")
    if worker_src not in sys.path:
        sys.path.insert(0, CELERY_WORKER_ROOT)
        sys.path.insert(0, worker_src)


@register
class OcrStepNode(NodeType):
    type_id = "ocr_step"
    label = "OCR"
    description = "Belgeleri OCR ile isler. Onceden islenmis belgeleri cache'ten okur, yeniler icin Gemini OCR calistirir."
    category = "processing"
    icon = "eye"
    color = "#DD6B20"

    @classmethod
    def params_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "ocr_mode": {
                    "type": "string",
                    "enum": ["hybrid", "gemini_only", "fallback_only"],
                    "default": "hybrid",
                    "description": "OCR stratejisi",
                },
                "max_pages": {
                    "type": "integer",
                    "default": 0,
                    "description": "Maks sayfa (0=sinirsiz)",
                },
            },
        }

    @classmethod
    def input_ports(cls) -> list[PortDef]:
        return [PortDef(name="files", direction=PortDirection.INPUT, data_type="file_list")]

    @classmethod
    def output_ports(cls) -> list[PortDef]:
        return [PortDef(name="ocr_results", direction=PortDirection.OUTPUT, data_type="ocr_list")]

    async def execute(self, ctx: ExecutionContext, params: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
        files = inputs.get("files", [])
        results: list[dict[str, Any]] = []

        ocr_cache = await self._load_ocr_cache(ctx)

        for file_info in files:
            if isinstance(file_info, str):
                file_info = {"path": file_info, "filename": file_info.rsplit("/", 1)[-1]}

            file_path = file_info.get("path", "")
            filename = file_info.get("filename", "")
            resource_id = file_info.get("resource_id", "")

            cached = (
                ocr_cache.get(file_path)
                or ocr_cache.get(filename)
                or ocr_cache.get(resource_id)
            )
            if cached and cached.get("ocr_text"):
                results.append({
                    "file_path": file_path,
                    "file_name": cached.get("file_name", filename),
                    "resource_id": resource_id,
                    "status": "cached",
                    "text": cached["ocr_text"],
                    "page_count": cached.get("page_count", 0),
                    "total_chars": cached.get("total_chars", 0),
                })
                continue

            if ctx.notification_mgr:
                await ctx.notification_mgr.notify(
                    agent_id=ctx.agent_id,
                    event_type="ocr_file_started",
                    data={"run_id": ctx.run_id, "file_name": filename},
                )

            try:
                ocr_result = await self._run_ocr(ctx, file_path, filename, resource_id)
                results.append(ocr_result)
            except Exception as exc:
                logger.error("OCR failed for %s: %s", filename, exc)
                results.append({
                    "file_path": file_path,
                    "file_name": filename,
                    "resource_id": resource_id,
                    "status": "failed",
                    "error": str(exc),
                    "text": "",
                })

            if ctx.notification_mgr:
                status = results[-1].get("status", "unknown")
                await ctx.notification_mgr.notify(
                    agent_id=ctx.agent_id,
                    event_type="ocr_file_completed",
                    data={
                        "run_id": ctx.run_id,
                        "file_name": filename,
                        "status": status,
                        "index": len(results),
                        "total": len(files),
                    },
                )

        cached_count = sum(1 for r in results if r.get("status") == "cached")
        new_count = sum(1 for r in results if r.get("status") == "completed")
        failed_count = sum(1 for r in results if r.get("status") == "failed")
        logger.info(
            "ocr_step: %d files — %d cached, %d new, %d failed",
            len(results), cached_count, new_count, failed_count,
        )

        return {"ocr_results": results}

    async def _load_ocr_cache(self, ctx: ExecutionContext) -> dict[str, dict]:
        """Load existing OCR results from agent_knowledge, indexed by path/name/rid."""
        from ...knowledge_store import KnowledgeStore
        ks = KnowledgeStore(ctx.pg)
        entries = await ks.get_all(ctx.agent_id, "ocr_result")

        cache: dict[str, dict] = {}
        for entry in entries:
            val = entry.get("value", {})
            if isinstance(val, str):
                try:
                    val = json.loads(val)
                except (json.JSONDecodeError, ValueError):
                    continue
            fp = val.get("file_path", "")
            fn = val.get("file_name", "")
            rid = val.get("resource_id", "")
            if fp:
                cache[fp] = val
            if fn:
                cache[fn] = val
            if rid:
                cache[rid] = val
        return cache

    async def _run_ocr(
        self,
        ctx: ExecutionContext,
        file_path: str,
        filename: str,
        resource_id: str,
    ) -> dict[str, Any]:
        """Run OCR on a single file: PDF -> images -> Gemini OCR -> persist."""
        if not file_path or not os.path.exists(file_path):
            if ctx.celery_app:
                return await self._run_ocr_celery(ctx, file_path, filename, resource_id)
            raise FileNotFoundError(f"File not found: {file_path}")

        import fitz  # PyMuPDF
        doc = fitz.open(file_path)
        output_dir = tempfile.mkdtemp(prefix="wf_ocr_")
        image_paths = []
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            pix = page.get_pixmap(dpi=200)
            img_path = os.path.join(output_dir, f"page_{page_num + 1:03d}.png")
            pix.save(img_path)
            image_paths.append(img_path)
        doc.close()

        if not image_paths:
            return {
                "file_path": file_path, "file_name": filename,
                "resource_id": resource_id,
                "status": "failed", "error": "No pages extracted", "text": "",
            }

        _ensure_celery_worker_importable()
        provider = os.getenv("OCR_PROVIDER", "gemini").lower()
        if provider == "chandra":
            from agents.chandra_ocr_agent import ChandraOCRAgent
            agent = ChandraOCRAgent()
        else:
            from agents.gemini_ocr_agent import GeminiOCRAgent
            agent = GeminiOCRAgent()

        await agent.initialize()
        ocr_out_dir = tempfile.mkdtemp(prefix="wf_ocr_out_")
        result = await agent.process(
            image_list=image_paths, output_dir=ocr_out_dir, file_name=None,
        )

        merged_text = result.get("merged_text", "")
        page_count = result.get("page_count", len(image_paths))
        total_chars = len(merged_text)
        token_usage = result.get("token_usage") or {}
        duration_ms = result.get("duration_ms") or 0

        doc_key = f"ocr_{uuid.uuid4().hex[:8]}"
        from ...knowledge_store import KnowledgeStore
        ks = KnowledgeStore(ctx.pg)
        await ks.upsert(
            ctx.agent_id, "ocr_result", doc_key,
            {
                "file_name": filename,
                "file_path": file_path,
                "resource_id": resource_id,
                "ocr_text": merged_text,
                "page_count": page_count,
                "total_chars": total_chars,
                "token_usage": token_usage,
                "duration_ms": duration_ms,
            },
            source="workflow_ocr_step",
        )

        return {
            "file_path": file_path,
            "file_name": filename,
            "resource_id": resource_id,
            "doc_key": doc_key,
            "status": "completed",
            "text": merged_text,
            "page_count": page_count,
            "total_chars": total_chars,
            "token_usage": token_usage,
            "duration_ms": duration_ms,
        }

    async def _run_ocr_celery(
        self,
        ctx: ExecutionContext,
        file_path: str,
        filename: str,
        resource_id: str,
    ) -> dict[str, Any]:
        """OCR via Celery for files not on local disk (e.g. MinIO)."""
        doc_id = f"wf-{uuid.uuid4().hex[:8]}"
        task = ctx.celery_app.send_task(
            "workspace.ocr_pages",
            args=[doc_id, [file_path], filename],
            queue="workspace",
        )
        result = task.get(timeout=300)

        if not isinstance(result, dict):
            raise RuntimeError(f"Unexpected Celery result: {result}")

        merged_text = result.get("merged_text", "")
        page_count = result.get("page_count", 0)
        total_chars = len(merged_text)

        doc_key = f"ocr_{uuid.uuid4().hex[:8]}"
        from ...knowledge_store import KnowledgeStore
        ks = KnowledgeStore(ctx.pg)
        await ks.upsert(
            ctx.agent_id, "ocr_result", doc_key,
            {
                "file_name": filename,
                "file_path": file_path,
                "resource_id": resource_id,
                "ocr_text": merged_text,
                "page_count": page_count,
                "total_chars": total_chars,
            },
            source="workflow_ocr_step_celery",
        )

        return {
            "file_path": file_path,
            "file_name": filename,
            "resource_id": resource_id,
            "doc_key": doc_key,
            "status": "completed",
            "text": merged_text,
            "page_count": page_count,
            "total_chars": total_chars,
        }
