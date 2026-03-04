"""
Batch Orchestrator
==================

10K+ belge icin Celery-tabanli toplu isleme yoneticisi.

Workspace'e bagli BatchJob lifecycle:
  created -> uploading -> queued -> processing -> quality_check -> completed

Islevler:
- Dosyalari batch'lere bol ve Celery task group olustur
- PostgreSQL uzerinden ilerleme takibi
- SSE ile real-time progress stream
- Hata toleransi: hatali dosyalari atla, review queue'ya ekle
"""

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from ..workspace_repository import WorkspaceRepository
from ..event_store.models import EventType

logger = logging.getLogger(__name__)

UPLOAD_DIR = Path(os.getenv("WORKSPACE_UPLOAD_DIR", "/tmp/workspace_uploads"))
BATCH_SIZE = int(os.getenv("WORKSPACE_BATCH_SIZE", "100"))
CELERY_BROKER = os.getenv("CELERY_BROKER_URL", "amqp://rabbitmq:RabbitMQ!654*@localhost:5672//")


def _get_celery_app():
    try:
        from celery import Celery
        app = Celery("workspace_batch", broker=CELERY_BROKER)
        app.conf.task_serializer = "json"
        app.conf.result_serializer = "json"
        app.conf.accept_content = ["json"]
        return app
    except ImportError:
        logger.warning("Celery not available, batch processing disabled")
        return None


class BatchOrchestrator:
    """
    Workspace batch processing yoneticisi.
    PostgreSQL uzerinden buyuk belge koleksiyonlarini isler.
    """

    def __init__(self, ws_repo: WorkspaceRepository):
        self.ws_repo = ws_repo
        self._celery = None

    @property
    def celery(self):
        if self._celery is None:
            self._celery = _get_celery_app()
        return self._celery

    # =========================================================================
    # BATCH JOB LIFECYCLE
    # =========================================================================

    async def create_batch_job(
        self,
        workspace_id: str,
        file_paths: List[str],
    ) -> Dict[str, Any]:
        job_id = f"batch-{uuid.uuid4().hex[:12]}"

        await self.ws_repo.create_batch_job({
            "id": job_id,
            "workspace_id": workspace_id,
            "status": "created",
            "total_documents": len(file_paths),
        })

        for i, fp in enumerate(file_paths):
            doc_id = f"wdoc-{uuid.uuid4().hex[:12]}"
            await self.ws_repo.create_document({
                "id": doc_id,
                "workspace_id": workspace_id,
                "batch_job_id": job_id,
                "file_path": fp,
                "file_name": Path(fp).name,
                "status": "queued",
                "sequence": i,
            })

        logger.info("Created batch job %s with %d documents", job_id, len(file_paths))
        return {"batch_job_id": job_id, "total_documents": len(file_paths)}

    async def start_processing(
        self,
        workspace_id: str,
        batch_job_id: str,
        pipeline: str = "",
    ) -> Dict[str, Any]:
        ws = await self.ws_repo.get_workspace(workspace_id)
        if not ws:
            return {"error": "Workspace not found"}

        skill_id = ws.get("skill_id", "")
        ocr_mode = ws.get("ocr_mode", "hybrid")
        batch_size = ws.get("batch_size", BATCH_SIZE)

        if not pipeline:
            schema_source = ws.get("schema_source", "")
            pipeline = "mcp" if schema_source == "mcp_sampling" else "hybrid"

        minio_bucket = ws.get("minio_bucket", "documents")
        minio_prefix = ws.get("minio_prefix", "")

        domain = ""
        extraction_config = ws.get("extraction_config", {})
        if isinstance(extraction_config, str):
            try:
                extraction_config = json.loads(extraction_config)
            except (json.JSONDecodeError, TypeError):
                extraction_config = {}
        domain = extraction_config.get("intent", "")

        schemas = await self._load_workspace_schemas(workspace_id)

        docs = await self.ws_repo.get_queued_documents(batch_job_id)
        if not docs:
            return {"error": "No queued documents found"}

        await self.ws_repo.update_batch_job(batch_job_id, {"status": "processing", "started_at": datetime.utcnow()})
        await self.ws_repo.update_workspace(workspace_id, {"status": "processing"})

        batches = [docs[i:i + batch_size] for i in range(0, len(docs), batch_size)]
        celery_task_ids = []

        for batch in batches:
            for doc in batch:
                minio_key = doc.get("minio_key", "")
                if not minio_key and minio_prefix and doc.get("file_name"):
                    minio_key = f"{minio_prefix.rstrip('/')}/{doc['file_name']}"

                task_id = self._enqueue_document_task(
                    doc["id"], doc.get("file_path", ""),
                    file_name=doc.get("file_name", ""),
                    skill_id=skill_id,
                    ocr_mode=ocr_mode,
                    workspace_id=workspace_id,
                    batch_job_id=batch_job_id,
                    schemas=schemas,
                    domain=domain,
                    pipeline=pipeline,
                    minio_bucket=minio_bucket,
                    minio_key=minio_key,
                )
                if task_id:
                    celery_task_ids.append(task_id)

        await self.ws_repo.update_batch_job(batch_job_id, {"celery_task_count": len(celery_task_ids)})

        est_seconds = len(docs) * 3
        logger.info(
            "Started batch %s: %d docs in %d batches, est %ds",
            batch_job_id, len(docs), len(batches), est_seconds,
        )

        try:
            from .event_bus import AgentEventBus, EventTypes
            bus = AgentEventBus.get_instance()
            await bus.publish(
                EventTypes.PROCESSING_STARTED,
                {"batch_job_id": batch_job_id, "total_documents": len(docs), "pipeline": pipeline},
                source_agent="batch_orchestrator",
                workspace_id=workspace_id,
            )
        except Exception:
            pass

        return {
            "batch_job_id": batch_job_id,
            "documents_queued": len(docs),
            "batches": len(batches),
            "estimated_seconds": est_seconds,
        }

    async def _load_workspace_schemas(self, workspace_id: str) -> Dict[str, Any]:
        entities = await self.ws_repo.get_workspace_entity_schemas(workspace_id)
        rels = await self.ws_repo.get_workspace_relationship_schemas(workspace_id)

        return {
            "entities": [
                {
                    "entity_type": e.get("entity_type", ""),
                    "description": e.get("description", ""),
                    "properties": e.get("properties", {}),
                }
                for e in entities
            ],
            "relationships": [
                {
                    "relationship_type": r.get("relationship_type", ""),
                    "source_entity": r.get("source_entity", ""),
                    "target_entity": r.get("target_entity", ""),
                    "description": r.get("description", ""),
                }
                for r in rels
            ],
        }

    def _enqueue_document_task(
        self,
        doc_id: str,
        file_path: str,
        file_name: str = "",
        skill_id: str = "",
        ocr_mode: str = "hybrid",
        workspace_id: str = "",
        batch_job_id: str = "",
        schemas: Optional[Dict[str, Any]] = None,
        domain: str = "",
        pipeline: str = "hybrid",
        minio_bucket: str = "",
        minio_key: str = "",
    ) -> Optional[str]:
        if not self.celery:
            return None

        if pipeline == "mcp" and minio_key:
            schema_json = ""
            if schemas:
                schema_json = json.dumps(schemas, ensure_ascii=False)

            result = self.celery.send_task(
                "workspace.process_document_mcp",
                kwargs={
                    "workspace_doc_id": doc_id,
                    "minio_bucket": minio_bucket or "documents",
                    "minio_key": minio_key,
                    "file_name": file_name,
                    "schema_json": schema_json,
                    "workspace_id": workspace_id,
                    "batch_job_id": batch_job_id,
                    "auto_accept": True,
                },
                queue="workspace",
            )
        else:
            result = self.celery.send_task(
                "workspace.process_document",
                kwargs={
                    "workspace_doc_id": doc_id,
                    "file_path": file_path,
                    "file_name": file_name,
                    "skill_id": skill_id,
                    "ocr_mode": ocr_mode,
                    "workspace_id": workspace_id,
                    "batch_job_id": batch_job_id,
                    "entity_schemas": (schemas or {}).get("entities", []),
                    "relationship_schemas": (schemas or {}).get("relationships", []),
                    "domain": domain,
                },
                queue="workspace",
            )
        return result.id

    # =========================================================================
    # PROGRESS TRACKING
    # =========================================================================

    async def get_progress(self, batch_job_id: str) -> Dict[str, Any]:
        r = await self.ws_repo.get_batch_progress(batch_job_id)
        if "error" in r:
            return r

        total = r.get("doc_total", 0) or 0
        processed = r.get("doc_processed", 0) or 0
        pct = (processed / total * 100) if total > 0 else 0

        elapsed = 0
        started = r.get("started_at")
        if started:
            try:
                if hasattr(started, "replace"):
                    elapsed = int((datetime.utcnow() - started.replace(tzinfo=None)).total_seconds())
            except Exception:
                pass

        remaining = None
        if processed > 0 and total > processed:
            rate = elapsed / processed
            remaining = int(rate * (total - processed))

        return {
            "batch_job_id": r.get("id", batch_job_id),
            "status": r.get("status", ""),
            "total": total,
            "processed": processed,
            "successful": r.get("doc_successful", 0) or 0,
            "failed": r.get("doc_failed", 0) or 0,
            "low_confidence": r.get("doc_low_conf", 0) or 0,
            "percent_complete": round(pct, 1),
            "elapsed_seconds": elapsed,
            "estimated_remaining_seconds": remaining,
        }

    async def stream_progress(
        self,
        batch_job_id: str,
        poll_interval: float = 2.0,
        workspace_id: str = "",
    ) -> AsyncIterator[Dict[str, Any]]:
        try:
            from .event_bus import AgentEventBus
            bus = AgentEventBus.get_instance()
        except Exception:
            bus = None

        while True:
            progress = await self.get_progress(batch_job_id)
            yield progress

            if bus and workspace_id:
                try:
                    await bus.publish(
                        "processing.progress",
                        {
                            "batch_job_id": batch_job_id,
                            "processed": progress.get("processed", 0),
                            "total": progress.get("total", 0),
                            "percent_complete": progress.get("percent_complete", 0),
                        },
                        source_agent="batch_orchestrator",
                        workspace_id=workspace_id,
                    )
                except Exception:
                    pass

            status = progress.get("status", "")
            if status in ("completed", "failed", "cancelled"):
                break

            total = progress.get("total", 0)
            processed = progress.get("processed", 0)
            if total > 0 and processed >= total:
                await self._finalize_batch(batch_job_id)
                progress["status"] = "completed"
                yield progress
                break

            await asyncio.sleep(poll_interval)

    # =========================================================================
    # REVIEW QUEUE
    # =========================================================================

    async def get_review_queue(
        self,
        workspace_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        docs = await self.ws_repo.list_documents(
            workspace_id, limit=limit,
        )
        review_docs = [d for d in docs if d.get("status") in ("low_confidence", "failed")]

        items = []
        for doc in review_docs[:limit]:
            extracted = {}
            er = doc.get("extraction_result")
            if isinstance(er, str):
                try:
                    extracted = json.loads(er)
                except (json.JSONDecodeError, TypeError):
                    pass
            elif isinstance(er, dict):
                extracted = er

            items.append({
                "document_id": doc.get("id", ""),
                "file_name": doc.get("file_name", ""),
                "status": doc.get("status", ""),
                "confidence_score": doc.get("confidence_score", 0),
                "extracted_entities": extracted.get("nodes", []),
                "extracted_relationships": extracted.get("relationships", []),
                "error_message": doc.get("error_message"),
            })

        return {
            "workspace_id": workspace_id,
            "total_items": len(review_docs),
            "items": items,
        }

    async def approve_review_items(
        self,
        workspace_id: str,
        document_ids: List[str],
        action: str = "approve",
    ) -> Dict[str, Any]:
        new_status = "completed" if action == "approve" else "skipped"
        for did in document_ids:
            await self.ws_repo.update_document(did, {"status": new_status})
        return {"action": action, "count": len(document_ids)}

    # =========================================================================
    # DOCUMENT STATUS CALLBACK
    # =========================================================================

    CONFIDENCE_THRESHOLD = float(os.getenv("SAMPLING_CONFIDENCE_THRESHOLD", "0.7"))

    async def update_document_status(
        self,
        doc_id: str,
        status: str,
        confidence_score: float = 0.0,
        extraction_result: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        updates: Dict[str, Any] = {
            "status": status,
            "confidence_score": confidence_score,
        }
        if extraction_result:
            updates["extraction_result"] = extraction_result
        if error_message:
            updates["error_message"] = error_message

        await self.ws_repo.update_document(doc_id, updates)

        try:
            from .event_bus import AgentEventBus, EventTypes
            bus = AgentEventBus.get_instance()
            event_type = EventTypes.PROCESSING_PROGRESS
            if status == "failed":
                event_type = EventTypes.PROCESSING_FAILED
            await bus.publish(
                event_type,
                {"doc_id": doc_id, "status": status, "confidence_score": confidence_score},
                source_agent="batch_orchestrator",
            )
        except Exception:
            pass

        if status in ("processed", "completed") and confidence_score < self.CONFIDENCE_THRESHOLD and confidence_score > 0:
            try:
                doc = await self.ws_repo.get_document(doc_id)
                if doc:
                    await self.create_elicitation_request(
                        doc_id=doc_id,
                        workspace_id=doc.get("workspace_id", ""),
                        batch_job_id=doc.get("batch_job_id", ""),
                        extraction_result=extraction_result or "{}",
                        confidence_score=confidence_score,
                        file_name=doc.get("file_name", ""),
                    )
                    logger.info(
                        "Auto-elicitation for doc %s (confidence=%.2f < threshold=%.2f)",
                        doc_id, confidence_score, self.CONFIDENCE_THRESHOLD,
                    )
            except Exception as e:
                logger.warning("Auto-elicitation failed for doc %s: %s", doc_id, e)

    # =========================================================================
    # INTERNALS
    # =========================================================================

    async def _finalize_batch(self, job_id: str) -> None:
        progress = await self.ws_repo.get_batch_progress(job_id)
        if "error" in progress:
            return

        successful = progress.get("doc_successful", 0) or 0
        failed = progress.get("doc_failed", 0) or 0
        low_conf = progress.get("doc_low_conf", 0) or 0
        total = progress.get("doc_total", 0) or 0
        has_issues = failed + low_conf

        final_status = "quality_check" if has_issues > 0 else "completed"

        await self.ws_repo.update_batch_job(job_id, {
            "status": final_status,
            "processed_documents": total,
            "successful_documents": successful,
            "failed_documents": failed,
            "low_confidence_documents": low_conf,
            "completed_at": datetime.utcnow(),
        })

        job = await self.ws_repo.get_batch_job(job_id)
        ws_id = job.get("workspace_id", "") if job else ""
        if ws_id:
            ws_status = "quality_check" if has_issues > 0 else "completed"
            await self.ws_repo.update_workspace(ws_id, {"status": ws_status})

        try:
            from .event_bus import AgentEventBus, EventTypes
            bus = AgentEventBus.get_instance()
            await bus.publish(
                EventTypes.PROCESSING_COMPLETED,
                {
                    "batch_job_id": job_id,
                    "total": total,
                    "successful": successful,
                    "failed": failed,
                    "low_confidence": low_conf,
                },
                source_agent="batch_orchestrator",
                workspace_id=ws_id,
            )
        except Exception:
            pass

    # =========================================================================
    # ELICITATION QUEUE
    # =========================================================================

    async def create_elicitation_request(
        self,
        doc_id: str,
        workspace_id: str,
        batch_job_id: str,
        extraction_result: str,
        confidence_score: float,
        file_name: str = "",
    ) -> Dict[str, Any]:
        req_id = await self.ws_repo.create_elicitation({
            "doc_id": doc_id,
            "workspace_id": workspace_id,
            "batch_job_id": batch_job_id,
            "extraction_result": extraction_result,
            "confidence_score": confidence_score,
            "file_name": file_name,
            "status": "pending",
        })

        logger.info("Created elicitation request %s for doc %s (conf=%.2f)", req_id, doc_id, confidence_score)

        try:
            from .event_bus import AgentEventBus, EventTypes
            bus = AgentEventBus.get_instance()
            await bus.publish(
                EventTypes.ELICITATION_REQUESTED,
                {"id": req_id, "doc_id": doc_id, "confidence_score": confidence_score},
                source_agent="batch_orchestrator",
                workspace_id=workspace_id,
            )
        except Exception:
            pass

        return {"id": req_id, "doc_id": doc_id, "status": "pending"}

    async def get_elicitation_queue(
        self,
        workspace_id: str,
        status: str = "pending",
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        return await self.ws_repo.list_elicitations(workspace_id, status, limit, offset)

    async def resolve_elicitation(
        self,
        request_id: str,
        action: str,
        modified_result: Optional[str] = None,
    ) -> Dict[str, Any]:
        result = await self.ws_repo.resolve_elicitation(request_id, action, modified_result)

        if "error" not in result:
            try:
                from .event_bus import AgentEventBus, EventTypes
                bus = AgentEventBus.get_instance()
                await bus.publish(
                    EventTypes.ELICITATION_RESOLVED,
                    {"id": request_id, "action": action, "doc_id": result.get("doc_id", "")},
                    source_agent="user",
                    workspace_id="",
                )
            except Exception:
                pass

        return result

    # =========================================================================
    # STATISTICS
    # =========================================================================

    async def get_workspace_stats(self, workspace_id: str) -> Dict[str, Any]:
        ws = await self.ws_repo.get_workspace(workspace_id)
        if not ws:
            return {"error": "Workspace not found"}

        stats = await self.ws_repo.get_document_stats(workspace_id)
        total = stats.get("total", 0) or 0

        return {
            "workspace_id": workspace_id,
            "name": ws.get("name", ""),
            "status": ws.get("status", ""),
            "total_documents": total,
            "successful": stats.get("successful", 0) or 0,
            "failed": stats.get("failed", 0) or 0,
            "low_confidence": stats.get("low_confidence", 0) or 0,
            "in_progress": stats.get("in_progress", 0) or 0,
            "average_confidence": round(float(stats.get("avg_confidence") or 0), 3),
            "success_rate": round((stats.get("successful", 0) or 0) / total * 100, 1) if total > 0 else 0,
        }
