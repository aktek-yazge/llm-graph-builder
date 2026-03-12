"""
Batch Tools
===========

Buyuk olcekli belge isleme icin batch yonetim tool'lari.

Mevcut batch_orchestrator.py altyapisini kullanir:
- PostgreSQL batch_jobs + workspace_documents tablolari
- Celery workspace.process_document task'i
- HTTP callback ile durum takibi

Agent bu tool'larla:
1. MinIO/S3'teki belgeleri toplu isle (batch baslat)
2. Ilerlemeyi sorgula (kac belge islendi, kaci basarisiz)
3. Sorunlu belgeleri listele (dusuk guven, basarisiz)
4. Review queue'yu yonet (onayla/reddet)
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


def create_batch_tools(agent_id: str, pg=None, celery_app=None) -> list:
    """Batch islemleri icin tool'lari olustur."""

    @tool
    async def start_batch_processing(
        file_paths: list[str],
        skill_id: str = "",
        batch_size: int = 100,
        ocr_mode: str = "hybrid",
    ) -> str:
        """MinIO/S3'teki belgeleri toplu islemeye basla.
        20K belge icin bile kullanilabilir, Celery worker'larina dagitir.

        Args:
            file_paths: MinIO dosya yollari listesi
            skill_id: Kullanilacak skill ID (bos ise agent'in kendi skill'i)
            batch_size: Her batch'teki belge sayisi (varsayilan: 100)
            ocr_mode: OCR modu: hybrid | sequential | unified
        """
        if not celery_app:
            return "Celery yapilandirilmamis. Batch isleme icin Celery gerekli."
        if not pg:
            return "PostgreSQL yapilandirilmamis."

        actual_skill_id = skill_id or f"agent-{agent_id}-extraction"
        batch_id = f"batch-{uuid.uuid4().hex[:12]}"
        workspace_id = f"ws-{agent_id}"

        try:
            await pg.execute(
                """
                INSERT INTO batch_jobs (id, workspace_id, status, total_documents)
                VALUES ($1, $2, 'created', $3)
                """,
                batch_id, workspace_id, len(file_paths),
            )

            for i, fp in enumerate(file_paths):
                doc_id = f"doc-{uuid.uuid4().hex[:8]}"
                await pg.execute(
                    """
                    INSERT INTO workspace_documents
                        (id, workspace_id, batch_job_id, file_path, file_name, status, sequence)
                    VALUES ($1, $2, $3, $4, $5, 'queued', $6)
                    """,
                    doc_id, workspace_id, batch_id, fp,
                    os.path.basename(fp), i + 1,
                )

            await pg.execute(
                "UPDATE batch_jobs SET status = 'processing', started_at = NOW() WHERE id = $1",
                batch_id,
            )

            queued_docs = await pg.fetch(
                "SELECT id, file_path, file_name FROM workspace_documents WHERE batch_job_id = $1 ORDER BY sequence",
                batch_id,
            )

            task_count = 0
            for doc in queued_docs:
                celery_app.send_task(
                    "workspace.process_document",
                    args=[doc["id"], doc["file_path"], doc["file_name"]],
                    kwargs={
                        "skill_id": actual_skill_id,
                        "workspace_id": workspace_id,
                        "batch_job_id": batch_id,
                        "ocr_mode": ocr_mode,
                    },
                    queue="workspace",
                )
                task_count += 1

            await pg.execute(
                "UPDATE batch_jobs SET celery_task_count = $1 WHERE id = $2",
                task_count, batch_id,
            )

            return json.dumps({
                "batch_id": batch_id,
                "total_documents": len(file_paths),
                "tasks_queued": task_count,
                "skill_id": actual_skill_id,
                "status": "processing",
                "message": f"{len(file_paths)} belge kuyruga alindi.",
            }, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("start_batch error: %s", e)
            return f"Batch baslama hatasi: {e}"

    @tool
    async def get_batch_progress(batch_id: str = "") -> str:
        """Batch islemenin ilerlemesini sorgula.
        Kac belge islendi, kaci basarili, kaci basarisiz.

        Args:
            batch_id: Batch ID (bos ise agent'in son batch'i)
        """
        if not pg:
            return "PostgreSQL yapilandirilmamis."

        try:
            if not batch_id:
                row = await pg.fetchrow(
                    """
                    SELECT id FROM batch_jobs
                    WHERE workspace_id = $1
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    f"ws-{agent_id}",
                )
                if not row:
                    return "Henuz batch isleme baslatilmamis."
                batch_id = row["id"]

            job = await pg.fetchrow("SELECT * FROM batch_jobs WHERE id = $1", batch_id)
            if not job:
                return f"Batch bulunamadi: {batch_id}"

            stats = await pg.fetchrow(
                """
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE status = 'completed') as successful,
                    COUNT(*) FILTER (WHERE status = 'failed') as failed,
                    COUNT(*) FILTER (WHERE status IN ('low_confidence', 'needs_review')) as needs_review,
                    COUNT(*) FILTER (WHERE status = 'queued') as queued,
                    COUNT(*) FILTER (WHERE status = 'processing') as in_progress,
                    AVG(confidence_score) FILTER (WHERE confidence_score > 0) as avg_confidence
                FROM workspace_documents
                WHERE batch_job_id = $1
                """,
                batch_id,
            )

            total = stats["total"]
            processed = stats["successful"] + stats["failed"] + stats["needs_review"]
            percent = round((processed / total * 100), 1) if total > 0 else 0

            return json.dumps({
                "batch_id": batch_id,
                "status": job["status"],
                "total": total,
                "processed": processed,
                "successful": stats["successful"],
                "failed": stats["failed"],
                "needs_review": stats["needs_review"],
                "queued": stats["queued"],
                "in_progress": stats["in_progress"],
                "percent_complete": percent,
                "avg_confidence": round(float(stats["avg_confidence"] or 0), 3),
                "started_at": str(job.get("started_at", "")),
                "completed_at": str(job.get("completed_at", "")) if job.get("completed_at") else None,
            }, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("get_batch_progress error: %s", e)
            return f"Ilerleme sorgu hatasi: {e}"

    @tool
    async def list_problem_documents(batch_id: str = "", limit: int = 20) -> str:
        """Basarisiz veya dusuk guvenli belgeleri listele.
        Kullaniciya gosterilecek sorunlu belgeleri dondurur.

        Args:
            batch_id: Batch ID (bos ise agent'in son batch'i)
            limit: Maksimum sonuc sayisi
        """
        if not pg:
            return "PostgreSQL yapilandirilmamis."

        try:
            if not batch_id:
                row = await pg.fetchrow(
                    "SELECT id FROM batch_jobs WHERE workspace_id = $1 ORDER BY created_at DESC LIMIT 1",
                    f"ws-{agent_id}",
                )
                if not row:
                    return "Henuz batch isleme baslatilmamis."
                batch_id = row["id"]

            docs = await pg.fetch(
                """
                SELECT id, file_name, status, confidence_score, error_message,
                       extraction_result::text as result_preview
                FROM workspace_documents
                WHERE batch_job_id = $1 AND status IN ('failed', 'low_confidence', 'needs_review')
                ORDER BY confidence_score ASC NULLS FIRST
                LIMIT $2
                """,
                batch_id, limit,
            )

            problems = []
            for d in docs:
                entry: dict[str, Any] = {
                    "doc_id": d["id"],
                    "file_name": d["file_name"],
                    "status": d["status"],
                    "confidence": float(d["confidence_score"]) if d["confidence_score"] else 0,
                }
                if d["error_message"]:
                    entry["error"] = d["error_message"]
                if d["result_preview"]:
                    entry["result_preview"] = d["result_preview"][:200]
                problems.append(entry)

            return json.dumps({
                "batch_id": batch_id,
                "problem_count": len(problems),
                "documents": problems,
            }, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("list_problem_documents error: %s", e)
            return f"Sorunlu belge listesi hatasi: {e}"

    @tool
    async def get_review_queue(batch_id: str = "", limit: int = 10) -> str:
        """Inceleme kuyrugundaki belgeleri getir.
        Kullanicinin onaylamasi/reddetmesi gereken extraction sonuclari.

        Args:
            batch_id: Batch ID (bos ise agent'in son batch'i)
            limit: Maksimum sonuc
        """
        if not pg:
            return "PostgreSQL yapilandirilmamis."

        try:
            if not batch_id:
                row = await pg.fetchrow(
                    "SELECT id FROM batch_jobs WHERE workspace_id = $1 ORDER BY created_at DESC LIMIT 1",
                    f"ws-{agent_id}",
                )
                if not row:
                    return "Henuz batch isleme baslatilmamis."
                batch_id = row["id"]

            docs = await pg.fetch(
                """
                SELECT id, file_name, confidence_score, extraction_result
                FROM workspace_documents
                WHERE batch_job_id = $1 AND status IN ('low_confidence', 'needs_review')
                ORDER BY confidence_score ASC
                LIMIT $2
                """,
                batch_id, limit,
            )

            items = []
            for d in docs:
                result = d["extraction_result"]
                if isinstance(result, str):
                    try:
                        result = json.loads(result)
                    except json.JSONDecodeError:
                        result = {"raw": result[:300]}

                node_count = 0
                rel_count = 0
                if isinstance(result, dict):
                    data = result.get("data", result)
                    node_count = len(data.get("nodes", []))
                    rel_count = len(data.get("relationships", data.get("edges", [])))

                items.append({
                    "doc_id": d["id"],
                    "file_name": d["file_name"],
                    "confidence": float(d["confidence_score"]) if d["confidence_score"] else 0,
                    "node_count": node_count,
                    "relationship_count": rel_count,
                })

            return json.dumps({
                "batch_id": batch_id,
                "review_count": len(items),
                "items": items,
            }, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("get_review_queue error: %s", e)
            return f"Review queue hatasi: {e}"

    @tool
    async def approve_document(doc_id: str, notes: str = "") -> str:
        """Inceleme kuyrugundaki belgeyi onayla.

        Args:
            doc_id: Belge ID
            notes: Onay notu (opsiyonel)
        """
        if not pg:
            return "PostgreSQL yapilandirilmamis."

        try:
            await pg.execute(
                "UPDATE workspace_documents SET status = 'completed' WHERE id = $1",
                doc_id,
            )
            if notes:
                from ..knowledge_store import KnowledgeStore
                store = KnowledgeStore(pg)
                await store.upsert(agent_id, "review_decision", doc_id, {
                    "action": "approved", "notes": notes,
                }, source="user_review")

            return f"Belge {doc_id} onaylandi."
        except Exception as e:
            return f"Onay hatasi: {e}"

    @tool
    async def reject_document(doc_id: str, reason: str = "") -> str:
        """Inceleme kuyrugundaki belgeyi reddet ve yeniden islenmek uzere isaretle.

        Args:
            doc_id: Belge ID
            reason: Red nedeni
        """
        if not pg:
            return "PostgreSQL yapilandirilmamis."

        try:
            await pg.execute(
                "UPDATE workspace_documents SET status = 'failed', error_message = $2 WHERE id = $1",
                doc_id, reason or "Kullanici tarafindan reddedildi",
            )
            if reason:
                from ..knowledge_store import KnowledgeStore
                store = KnowledgeStore(pg)
                await store.upsert(agent_id, "review_decision", doc_id, {
                    "action": "rejected", "reason": reason,
                }, source="user_review")

            return f"Belge {doc_id} reddedildi. Neden: {reason or 'Belirtilmedi'}"
        except Exception as e:
            return f"Red hatasi: {e}"

    return [
        start_batch_processing,
        get_batch_progress,
        list_problem_documents,
        get_review_queue,
        approve_document,
        reject_document,
    ]
