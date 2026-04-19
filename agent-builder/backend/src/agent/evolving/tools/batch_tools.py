"""
Batch Tools
===========

Buyuk olcekli belge isleme icin batch yonetim tool'lari.

Mevcut altyapiyi kullanir:
- PostgreSQL ``batch_jobs`` ve ``workspace_documents`` tablolari (schema.sql)
- Celery ``workspace.process_document`` task'i
- HTTP callback ile durum takibi (``/callback/document-status``)

Iki batch tool'u vardir:

1. ``start_batch_processing`` (sync-ish):
   Kucuk/orta batch'ler icin. Agent islerin tamamlanmasini polling ile bekler.

2. ``start_long_batch`` (async-aware, durable):
   1000+ belge gibi UZUN suren islemler icin. Hemen geri doner;
   ``pending_resumes`` tablosuna kayit duser. Batch bittiginde
   ``/callback/batch-complete`` webhook'u tetiklenir ve agent yeni bir turn'de
   sonucla bilgilendirilir. Chat kapansa bile is devam eder.

Kolon isimleri veritabanindaki gercek yapiyla uyumludur:
- ``batch_jobs.id`` (PK), ``workspace_id``, ``status``, ``total_documents``,
  ``processed_documents``, ``successful_documents``, ``failed_documents``,
  ``low_confidence_documents``, ``celery_task_count``, ``description``,
  ``started_at``, ``completed_at``
- ``workspace_documents.id`` (PK), ``batch_job_id``, ``workspace_id``,
  ``file_path``, ``file_name``, ``sequence``, ``status``, ``confidence_score``,
  ``extraction_result``, ``error_message``
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any, Optional

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

logger = logging.getLogger(__name__)


def create_batch_tools(agent_id: str, pg=None, celery_app=None) -> list:
    """Batch islemleri icin tool'lari olustur."""

    async def _create_batch_row(
        batch_id: str,
        file_paths: list[str],
        skill_id: str,
        ocr_mode: str,
        description: str = "",
    ) -> None:
        await pg.execute(
            """
            INSERT INTO batch_jobs
                (id, workspace_id, status, total_documents, description)
            VALUES ($1, $2, 'created', $3, $4)
            """,
            batch_id,
            agent_id,
            len(file_paths),
            description or None,
        )

    async def _enqueue_documents(
        batch_id: str,
        file_paths: list[str],
        skill_id: str,
        ocr_mode: str,
    ) -> int:
        """Insert workspace_documents rows + send Celery tasks. Returns task count."""
        for i, fp in enumerate(file_paths):
            doc_id = f"doc-{uuid.uuid4().hex[:8]}"
            await pg.execute(
                """
                INSERT INTO workspace_documents
                    (id, batch_job_id, workspace_id, file_path, file_name, sequence, status)
                VALUES ($1, $2, $3, $4, $5, $6, 'queued')
                """,
                doc_id,
                batch_id,
                agent_id,
                fp,
                os.path.basename(fp),
                i + 1,
            )

        await pg.execute(
            "UPDATE batch_jobs SET status='processing', started_at=NOW() "
            "WHERE id=$1",
            batch_id,
        )

        queued_docs = await pg.fetch(
            "SELECT id, file_path, file_name FROM workspace_documents "
            "WHERE batch_job_id=$1 ORDER BY sequence",
            batch_id,
        )

        actual_skill_id = skill_id or f"agent-{agent_id}-extraction"
        task_count = 0
        for doc in queued_docs:
            celery_app.send_task(
                "workspace.process_document",
                args=[doc["id"], doc["file_path"], doc["file_name"]],
                kwargs={
                    "skill_id": actual_skill_id,
                    "agent_id": agent_id,
                    "batch_id": batch_id,
                    "ocr_mode": ocr_mode,
                },
                queue="workspace",
            )
            task_count += 1

        await pg.execute(
            "UPDATE batch_jobs SET celery_task_count=$1 WHERE id=$2",
            task_count,
            batch_id,
        )
        return task_count

    @tool
    async def start_batch_processing(
        file_paths: list[str],
        skill_id: str = "",
        batch_size: int = 100,
        ocr_mode: str = "hybrid",
    ) -> str:
        """MinIO/S3'teki belgeleri toplu islemeye basla (kucuk/orta olcek).
        Agent ilerlemeyi periyodik olarak polling ile sorgular.

        BUYUK BATCH'LER ICIN: ``start_long_batch`` tool'unu kullan; chat kapansa bile
        is devam eder ve bittiginde otomatik bilgilendirme gelir.

        Args:
            file_paths: MinIO/lokal dosya yollari listesi
            skill_id: Kullanilacak skill ID (bos ise agent'in kendi skill'i)
            batch_size: Bilgi amacli kullanilir; tum dosyalar tek seferde kuyruklanir
            ocr_mode: OCR modu: hybrid | sequential | unified
        """
        if not celery_app:
            return "Celery yapilandirilmamis. Batch isleme icin Celery gerekli."
        if not pg:
            return "PostgreSQL yapilandirilmamis."

        batch_id = f"batch-{uuid.uuid4().hex[:12]}"

        try:
            await _create_batch_row(batch_id, file_paths, skill_id, ocr_mode)
            task_count = await _enqueue_documents(batch_id, file_paths, skill_id, ocr_mode)

            return json.dumps({
                "batch_id": batch_id,
                "total_documents": len(file_paths),
                "tasks_queued": task_count,
                "skill_id": skill_id or f"agent-{agent_id}-extraction",
                "status": "processing",
                "message": (
                    f"{len(file_paths)} belge kuyruga alindi. "
                    "Ilerlemeyi `get_batch_progress` ile sorgula."
                ),
            }, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("start_batch error: %s", e)
            return f"Batch baslama hatasi: {e}"

    @tool
    async def start_long_batch(
        file_paths: list[str],
        description: str = "",
        skill_id: str = "",
        ocr_mode: str = "hybrid",
        notify_when_done: bool = True,
        config: RunnableConfig = None,  # type: ignore[assignment]
    ) -> str:
        """1000+ belge gibi UZUN suren batch islemleri icin durable tool.
        Hemen geri doner; is bitince agent yeni turn'de bilgilendirilir ve
        kullaniciya in-app notification gonderilir. **Chat kapansa, backend
        restart edilse bile is devam eder.**

        Tool calistiktan sonra agent kullaniciya kisa bir bilgilendirme uretip
        turn'u kapatmali ('Batch X baslatildi, bittiginde haber veririm').
        get_batch_progress ile araya giriliyorsa bile, bitince OTOMATIK ek bir
        sistem mesaji gelecek ve agent ona yanit verecek.

        Args:
            file_paths: Islenecek dosya yollari (MinIO/lokal)
            description: Insan-okunabilir aciklama (notification metninde gosterilir)
            skill_id: Kullanilacak skill ID (bos ise agent'in kendi skill'i)
            ocr_mode: OCR modu: hybrid | sequential | unified
            notify_when_done: True ise pending_resumes'e yazilir, callback gelince
                inject_system_event tetiklenir
        """
        if not celery_app:
            return "Celery yapilandirilmamis. Batch isleme icin Celery gerekli."
        if not pg:
            return "PostgreSQL yapilandirilmamis."

        batch_id = f"batch-{uuid.uuid4().hex[:12]}"

        thread_id = ""
        session_id = ""
        if config is not None:
            configurable = config.get("configurable") or {}
            thread_id = configurable.get("thread_id", "") or ""
            if ":" in thread_id:
                session_id = thread_id.split(":", 1)[1]

        try:
            await _create_batch_row(
                batch_id, file_paths, skill_id, ocr_mode, description=description,
            )
            task_count = await _enqueue_documents(batch_id, file_paths, skill_id, ocr_mode)

            registered_resume = False
            if notify_when_done and thread_id and session_id:
                try:
                    await pg.execute(
                        """
                        INSERT INTO pending_resumes
                            (batch_job_id, workspace_id, session_id, thread_id, status, event_text_template)
                        VALUES ($1, $2, $3, $4, 'waiting', $5)
                        ON CONFLICT (batch_job_id) DO UPDATE
                          SET thread_id = EXCLUDED.thread_id,
                              session_id = EXCLUDED.session_id,
                              status = 'waiting'
                        """,
                        batch_id, agent_id, session_id, thread_id,
                        description or "",
                    )
                    registered_resume = True
                except Exception as exc:
                    logger.warning(
                        "pending_resumes insert failed for batch=%s: %s", batch_id, exc,
                    )
            elif notify_when_done:
                logger.warning(
                    "start_long_batch: thread_id/session_id yok, "
                    "notify_when_done devre disi (batch=%s)", batch_id,
                )

            return json.dumps({
                "batch_id": batch_id,
                "total_documents": len(file_paths),
                "tasks_queued": task_count,
                "status": "started",
                "notify_when_done": registered_resume,
                "thread_id": thread_id,
                "message": (
                    f"Batch {batch_id} baslatildi ({len(file_paths)} belge kuyrukta). "
                    + (
                        "Bittiginde otomatik bilgilendirme gelecek. "
                        "Kullaniciya kisa bir not yaz ve turn'u kapat."
                        if registered_resume
                        else "Otomatik bilgilendirme aktif degil; "
                        "ilerlemeyi `get_batch_progress` ile sorgulayabilirsin."
                    )
                ),
            }, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("start_long_batch error: %s", e, exc_info=True)
            return f"Long batch baslatma hatasi: {e}"

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
                    agent_id,
                )
                if not row:
                    return "Henuz batch isleme baslatilmamis."
                batch_id = row["id"]

            job = await pg.fetchrow(
                "SELECT * FROM batch_jobs WHERE id = $1", batch_id,
            )
            if not job:
                return f"Batch bulunamadi: {batch_id}"

            stats = await pg.fetchrow(
                """
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE status IN ('completed', 'entities_extracted')) as successful,
                    COUNT(*) FILTER (WHERE status = 'failed') as failed,
                    COUNT(*) FILTER (WHERE status IN ('low_confidence', 'needs_review')) as needs_review,
                    COUNT(*) FILTER (WHERE status = 'queued') as queued,
                    COUNT(*) FILTER (WHERE status IN ('processing', 'extracting_images', 'images_extracted', 'ocr_processing', 'ocr_completed', 'extracting_entities')) as in_progress,
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
                "started_at": str(job["started_at"]) if job["started_at"] else None,
                "completed_at": str(job["completed_at"]) if job["completed_at"] else None,
            }, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("get_batch_progress error: %s", e)
            return f"Ilerleme sorgu hatasi: {e}"

    @tool
    async def list_problem_documents(batch_id: str = "", limit: int = 20) -> str:
        """Basarisiz veya dusuk guvenli belgeleri listele.

        Args:
            batch_id: Batch ID (bos ise agent'in son batch'i)
            limit: Maksimum sonuc sayisi
        """
        if not pg:
            return "PostgreSQL yapilandirilmamis."

        try:
            if not batch_id:
                row = await pg.fetchrow(
                    "SELECT id FROM batch_jobs WHERE workspace_id=$1 ORDER BY created_at DESC LIMIT 1",
                    agent_id,
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

        Args:
            batch_id: Batch ID (bos ise agent'in son batch'i)
            limit: Maksimum sonuc
        """
        if not pg:
            return "PostgreSQL yapilandirilmamis."

        try:
            if not batch_id:
                row = await pg.fetchrow(
                    "SELECT id FROM batch_jobs WHERE workspace_id=$1 ORDER BY created_at DESC LIMIT 1",
                    agent_id,
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
                "UPDATE workspace_documents SET status='completed', updated_at=NOW() WHERE id=$1",
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
        """Inceleme kuyrugundaki belgeyi reddet.

        Args:
            doc_id: Belge ID
            reason: Red nedeni
        """
        if not pg:
            return "PostgreSQL yapilandirilmamis."

        try:
            await pg.execute(
                "UPDATE workspace_documents SET status='failed', error_message=$2, updated_at=NOW() "
                "WHERE id=$1",
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
        start_long_batch,
        get_batch_progress,
        list_problem_documents,
        get_review_queue,
        approve_document,
        reject_document,
    ]


# ---------------------------------------------------------------------------
# Helper used by router /callback/batch-complete
# ---------------------------------------------------------------------------

async def compute_batch_summary(pg, batch_id: str) -> dict[str, Any]:
    """Compute completion summary for a batch (callable from anywhere)."""
    job = await pg.fetchrow(
        "SELECT * FROM batch_jobs WHERE id=$1", batch_id,
    )
    if not job:
        return {"batch_id": batch_id, "found": False}

    stats = await pg.fetchrow(
        """
        SELECT
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE status IN ('completed', 'entities_extracted')) as completed,
            COUNT(*) FILTER (WHERE status = 'failed') as failed,
            COUNT(*) FILTER (WHERE status IN ('low_confidence', 'needs_review')) as needs_review,
            AVG(confidence_score) FILTER (WHERE confidence_score > 0) as avg_confidence
        FROM workspace_documents
        WHERE batch_job_id=$1
        """,
        batch_id,
    )

    return {
        "batch_id": batch_id,
        "agent_id": job["workspace_id"],
        "status": job["status"],
        "description": job.get("description") or "",
        "total": int(stats["total"] or 0),
        "completed": int(stats["completed"] or 0),
        "failed": int(stats["failed"] or 0),
        "needs_review": int(stats["needs_review"] or 0),
        "avg_confidence": round(float(stats["avg_confidence"] or 0), 3),
        "started_at": str(job["started_at"]) if job["started_at"] else None,
        "completed_at": str(job["completed_at"]) if job["completed_at"] else None,
    }


async def maybe_finalize_batch(pg, batch_id: str) -> Optional[dict[str, Any]]:
    """Atomically check if all docs for batch_id reached terminal status.
    If yes, transition batch_jobs.status to 'completed' (only if not already)
    and return the summary. Otherwise return None.

    Returns the summary only on the FIRST transition so callers can fire the
    completion webhook exactly once.
    """
    pending = await pg.fetchrow(
        """
        SELECT COUNT(*) as remaining FROM workspace_documents
        WHERE batch_job_id=$1
          AND status NOT IN ('completed', 'entities_extracted', 'failed', 'low_confidence', 'needs_review')
        """,
        batch_id,
    )
    if pending and int(pending["remaining"]) > 0:
        return None

    updated = await pg.execute(
        """
        UPDATE batch_jobs
        SET status='completed', completed_at=NOW()
        WHERE id=$1 AND status<>'completed'
        """,
        batch_id,
    )
    try:
        affected = int(updated.split()[-1])
    except (ValueError, IndexError):
        affected = 0

    if affected == 0:
        return None

    summary = await compute_batch_summary(pg, batch_id)
    return summary
