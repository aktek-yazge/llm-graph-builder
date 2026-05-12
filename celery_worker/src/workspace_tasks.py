"""
Workspace Document Processing Tasks
====================================

Agent Builder Workspace icin Celery task'lari.

Iki pipeline desteklenir:
1. Hybrid Pipeline: PDF -> PyMuPDF -> PNG -> GeminiOCR -> text -> AgenticOCR text mode -> entities
2. MCP Pipeline: MinIO -> MCP Gateway -> extraction_server (Sampling) -> entities

Dusuk guvenli sonuclar ElicitationRequest olarak Neo4j'ye kaydedilir.
Kullanici dashboard'dan inceleyebilir.

Her task tamamlandiginda Agent Builder callback endpoint'ine durum bildirir.
"""

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.celery_app import app

logger = logging.getLogger(__name__)

CALLBACK_URL = os.getenv(
    "AGENT_BUILDER_CALLBACK_URL",
    "http://localhost:8001/api/v2/evolving/callback/document-status",
)
BATCH_COMPLETE_CALLBACK_URL = os.getenv(
    "AGENT_BUILDER_BATCH_COMPLETE_URL",
    "http://localhost:8001/api/v2/evolving/callback/batch-complete",
)
EVENT_STORE_DSN = os.getenv(
    "EVENT_STORE_DSN",
    "postgresql://event_user:event_secret@localhost:5433/event_store",
)

IMAGE_RESOLUTION_SCALE = 2.0

# Extraction cache (graphify-adopted content-hash skip).
# Aynı doc + aynı extractor konfigürasyonu daha önce işlendiyse LLM çağrısı atlanır.
# Cache key: (doc_id, sha256(text)[:16], extractor_version).
# Schema init worker startup'ta yapılır; çalışmazsa cache devre dışı kalır,
# task'lar normal akışta devam eder.
EXTRACTION_CACHE_ENABLED = os.getenv("EXTRACTION_CACHE_ENABLED", "1") not in ("0", "false", "False")
_extraction_cache_schema_initialized = False


def _ensure_extraction_cache_schema() -> bool:
    """Lazy idempotent schema init — ilk extract_entities task'inde tetiklenir."""
    global _extraction_cache_schema_initialized
    if _extraction_cache_schema_initialized:
        return True
    if not EXTRACTION_CACHE_ENABLED:
        return False
    try:
        from src.extraction_cache import init_extraction_cache_schema
    except ImportError:
        logger.warning("extraction_cache module not importable")
        return False
    ok = init_extraction_cache_schema(EVENT_STORE_DSN)
    if ok:
        _extraction_cache_schema_initialized = True
        logger.info("extraction_cache schema initialized")
    return ok


# ============================================================================
# HELPERS
# ============================================================================

def _report_status(
    doc_id: str,
    status: str,
    confidence_score: float = 0.0,
    extraction_result: Optional[str] = None,
    error_message: Optional[str] = None,
    agent_id: str = "",
    batch_id: str = "",
    event_type: str = "document_status",
):
    """Agent Builder callback endpoint'ine durum bildir.

    Tum alanlari unified JSON body olarak gonderir; backend ``CallbackPayload``
    ile birebir eslesir. ``extraction_result`` zaten JSON string olabilir;
    parse etmeyi deneriz, basarisiz olursak raw text alan olarak yollarız.
    """
    payload: Dict[str, Any] = {
        "doc_id": doc_id,
        "status": status,
        "agent_id": agent_id,
        "batch_id": batch_id,
        "event_type": event_type,
        "confidence_score": float(confidence_score or 0.0),
        "error_message": error_message or "",
    }
    if extraction_result:
        try:
            payload["extraction_result"] = json.loads(extraction_result)
        except (json.JSONDecodeError, TypeError):
            payload["extraction_result"] = {"raw": str(extraction_result)[:5000]}

    try:
        import httpx

        with httpx.Client(timeout=10) as client:
            resp = client.post(
                CALLBACK_URL,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
    except Exception as e:
        logger.warning("Callback failed for %s (status=%s): %s", doc_id, status, e)


def _terminal_status(status: str) -> bool:
    """True if a workspace_documents.status row should be considered done."""
    return status in {
        "completed",
        "entities_extracted",
        "failed",
        "low_confidence",
        "needs_review",
    }


def _update_workspace_document(
    doc_id: str,
    status: str,
    confidence_score: float = 0.0,
    extraction_result: Optional[Dict[str, Any]] = None,
    error_message: Optional[str] = None,
    celery_task_id: Optional[str] = None,
) -> None:
    """Synchronously update workspace_documents row in PostgreSQL.

    Worker'in PG'ye dogrudan yazmasi sayesinde ``get_batch_progress`` HTTP
    callback geç gelse bile dogru veriyi gosterir.
    """
    try:
        import psycopg
    except ImportError:
        logger.debug("psycopg not installed in worker; skip PG update for %s", doc_id)
        return

    extraction_json = None
    if extraction_result is not None:
        try:
            extraction_json = json.dumps(extraction_result, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            extraction_json = None

    sql = """
        UPDATE workspace_documents
        SET status=%s,
            confidence_score=%s,
            extraction_result=COALESCE(%s::jsonb, extraction_result),
            error_message=COALESCE(NULLIF(%s, ''), error_message),
            celery_task_id=COALESCE(NULLIF(%s, ''), celery_task_id),
            updated_at=NOW()
        WHERE doc_id=%s
    """
    try:
        with psycopg.connect(EVENT_STORE_DSN, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    (
                        status,
                        float(confidence_score or 0.0),
                        extraction_json,
                        error_message or "",
                        celery_task_id or "",
                        doc_id,
                    ),
                )
    except Exception as exc:
        logger.warning("workspace_documents update failed for %s: %s", doc_id, exc)


def _maybe_finalize_batch(batch_id: str) -> None:
    """If all docs in batch reached terminal status, transition batch_jobs.status='completed'
    exactly once and POST to /callback/batch-complete."""
    if not batch_id:
        return
    try:
        import psycopg
    except ImportError:
        return

    try:
        with psycopg.connect(EVENT_STORE_DSN, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*) FROM workspace_documents
                    WHERE batch_id=%s
                      AND status NOT IN ('completed', 'entities_extracted',
                                          'failed', 'low_confidence', 'needs_review')
                    """,
                    (batch_id,),
                )
                row = cur.fetchone()
                remaining = int(row[0]) if row else 0
                if remaining > 0:
                    return

                cur.execute(
                    """
                    UPDATE batch_jobs
                    SET status='completed', completed_at=NOW(), updated_at=NOW()
                    WHERE batch_id=%s AND status<>'completed'
                    """,
                    (batch_id,),
                )
                if cur.rowcount == 0:
                    return

                cur.execute(
                    "SELECT agent_id FROM batch_jobs WHERE batch_id=%s", (batch_id,),
                )
                ag = cur.fetchone()
                agent_id_for_batch = ag[0] if ag else ""
    except Exception as exc:
        logger.warning("_maybe_finalize_batch %s failed: %s", batch_id, exc)
        return

    try:
        import httpx

        with httpx.Client(timeout=10) as client:
            resp = client.post(
                BATCH_COMPLETE_CALLBACK_URL,
                json={"batch_id": batch_id, "agent_id": agent_id_for_batch},
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
    except Exception as exc:
        logger.warning("/callback/batch-complete POST failed for %s: %s", batch_id, exc)


def _extract_pdf_images(file_path: str, output_dir: Optional[str] = None) -> List[str]:
    """PyMuPDF ile PDF -> PNG. Metin cikarma icin DEGIL, sadece goruntu donusumu."""
    try:
        import fitz
    except ImportError:
        logger.error("PyMuPDF (fitz) not installed")
        return []

    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="ws_ocr_")

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    try:
        doc = fitz.open(str(file_path))
        stem = Path(file_path).stem
        images = []

        for i in range(len(doc)):
            page = doc.load_page(i)
            mat = fitz.Matrix(IMAGE_RESOLUTION_SCALE, IMAGE_RESOLUTION_SCALE)
            pix = page.get_pixmap(matrix=mat)
            img_path = out / f"{stem}_page_{i + 1:03d}.png"
            pix.save(str(img_path))
            pix = None
            images.append(str(img_path))

        doc.close()
        logger.info("PDF->PNG: %d pages from %s", len(images), file_path)
        return images

    except Exception as e:
        logger.error("PDF->PNG failed for %s: %s", file_path, e)
        return []


# ============================================================================
# TASK 1: IMAGE EXTRACTION
# ============================================================================

@app.task(bind=True, name="workspace.extract_images", max_retries=2)
def extract_images_task(
    self,
    workspace_doc_id: str,
    file_path: str,
    output_dir: str = "",
    agent_id: str = "",
    batch_id: str = "",
):
    """
    PDF dosyasini PNG sayfa goruntulerine donusturur.

    Returns:
        {"doc_id": ..., "images": [...], "page_count": N}
    """
    logger.info("workspace.extract_images: %s (%s)", workspace_doc_id, file_path)
    _update_workspace_document(workspace_doc_id, "extracting_images", celery_task_id=self.request.id)
    _report_status(workspace_doc_id, "extracting_images", agent_id=agent_id, batch_id=batch_id)

    try:
        p = Path(file_path)
        suffix = p.suffix.lower()

        if suffix == ".pdf":
            target_dir = output_dir or tempfile.mkdtemp(prefix=f"ws_{workspace_doc_id}_")
            images = _extract_pdf_images(file_path, target_dir)
            if not images:
                _update_workspace_document(workspace_doc_id, "failed", error_message="PDF->PNG conversion failed")
                _report_status(workspace_doc_id, "failed", error_message="PDF->PNG conversion failed", agent_id=agent_id, batch_id=batch_id)
                _maybe_finalize_batch(batch_id)
                return {"doc_id": workspace_doc_id, "images": [], "error": "PDF->PNG failed"}

        elif suffix in (".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp"):
            images = [file_path]
        else:
            _update_workspace_document(workspace_doc_id, "failed", error_message=f"Unsupported file type: {suffix}")
            _report_status(workspace_doc_id, "failed", error_message=f"Unsupported file type: {suffix}", agent_id=agent_id, batch_id=batch_id)
            _maybe_finalize_batch(batch_id)
            return {"doc_id": workspace_doc_id, "images": [], "error": f"Unsupported: {suffix}"}

        _update_workspace_document(workspace_doc_id, "images_extracted")
        _report_status(workspace_doc_id, "images_extracted", agent_id=agent_id, batch_id=batch_id)
        return {"doc_id": workspace_doc_id, "images": images, "page_count": len(images)}

    except Exception as e:
        logger.error("extract_images failed for %s: %s", workspace_doc_id, e)
        _update_workspace_document(workspace_doc_id, "failed", error_message=str(e)[:500])
        _report_status(workspace_doc_id, "failed", error_message=str(e)[:500], agent_id=agent_id, batch_id=batch_id)
        _maybe_finalize_batch(batch_id)
        raise self.retry(exc=e, countdown=30, max_retries=2)


# ============================================================================
# TASK 2: OCR (GeminiOCR)
# ============================================================================

@app.task(bind=True, name="workspace.ocr_pages", max_retries=3)
def ocr_pages_task(
    self,
    workspace_doc_id: str,
    image_paths: List[str],
    file_name: str = "",
    agent_id: str = "",
    batch_id: str = "",
):
    """
    Sayfa goruntuleri uzerinde GeminiOCR ile metin cikarir.

    Returns:
        {"doc_id": ..., "merged_text": "...", "page_count": N}
    """
    logger.info("workspace.ocr_pages: %s (%d images)", workspace_doc_id, len(image_paths))
    _update_workspace_document(workspace_doc_id, "ocr_processing", celery_task_id=self.request.id)
    _report_status(workspace_doc_id, "ocr_processing", agent_id=agent_id, batch_id=batch_id)

    try:
        from src.agents import process_gemini_ocr

        output_dir = os.getenv("OUTPUT_DIR", "output_celery")
        result = process_gemini_ocr(
            image_list=image_paths,
            output_dir=output_dir,
            file_name=file_name,
        )

        merged = result.get("merged_text", "")
        if not merged:
            ocr_texts = result.get("ocr_texts", [])
            merged = "\n\n".join(t for t in ocr_texts if t)

        if not merged:
            _update_workspace_document(workspace_doc_id, "failed", error_message="OCR returned empty text")
            _report_status(workspace_doc_id, "failed", error_message="OCR returned empty text", agent_id=agent_id, batch_id=batch_id)
            _maybe_finalize_batch(batch_id)
            return {"doc_id": workspace_doc_id, "merged_text": "", "error": "Empty OCR result"}

        _update_workspace_document(workspace_doc_id, "ocr_completed")
        _report_status(workspace_doc_id, "ocr_completed", agent_id=agent_id, batch_id=batch_id)
        return {
            "doc_id": workspace_doc_id,
            "merged_text": merged,
            "page_count": len(image_paths),
            "token_usage": result.get("token_usage", {}),
        }

    except Exception as e:
        logger.error("ocr_pages failed for %s: %s", workspace_doc_id, e)
        _update_workspace_document(workspace_doc_id, "failed", error_message=str(e)[:500])
        _report_status(workspace_doc_id, "failed", error_message=str(e)[:500], agent_id=agent_id, batch_id=batch_id)
        _maybe_finalize_batch(batch_id)
        raise self.retry(exc=e, countdown=60, max_retries=3)


# ============================================================================
# TASK 3: ENTITY EXTRACTION (AgenticOCR text mode)
# ============================================================================

@app.task(bind=True, name="workspace.extract_entities", max_retries=3)
def extract_entities_task(
    self,
    workspace_doc_id: str,
    text: str,
    file_name: str = "",
    entity_schemas: Optional[List[Dict]] = None,
    relationship_schemas: Optional[List[Dict]] = None,
    skill_id: str = "",
    domain: str = "",
    agent_id: str = "",
    batch_id: str = "",
    force_refresh: bool = False,
):
    """
    OCR metinden schema-driven entity/relationship cikarir.

    Hybrid pipeline'in ikinci asamasi: AgenticOCR text mode.

    Content-hash cache (graphify-adopted): ayni text + ayni extractor
    konfigurasyonu daha once islendiyse LLM cagrisi ATLANIR. Cache key
    `(doc_id, sha256(text)[:16], extractor_version)`. extractor_version =
    `pipeline=agentic_ocr;schema=...;domain=...;skill=...`. domain veya
    skill degisimi otomatik invalidation tetikler. ``force_refresh=True``
    cache'i bypass eder.

    Returns:
        {"doc_id": ..., "nodes": [...], "relationships": [...],
         "confidence_score": float, "cached": bool}
    """
    logger.info("workspace.extract_entities: %s (%d chars)", workspace_doc_id, len(text))

    # ── Cache lookup (LLM cagrisindan ONCE) ────────────────────────────
    cache_hit = None
    content_hash = None
    extractor_version = None
    if EXTRACTION_CACHE_ENABLED and not force_refresh and _ensure_extraction_cache_schema():
        try:
            from src.extraction_cache import (
                build_extractor_version,
                check_extraction_cache,
                compute_content_hash,
            )
            content_hash = compute_content_hash(text)
            extractor_version = build_extractor_version(domain=domain, skill_id=skill_id)
            cache_hit = check_extraction_cache(
                EVENT_STORE_DSN,
                doc_id=workspace_doc_id,
                content_hash=content_hash,
                extractor_version=extractor_version,
            )
        except Exception as exc:  # pragma: no cover
            logger.warning("extraction_cache lookup failed for %s: %s", workspace_doc_id, exc)

    if cache_hit is not None and cache_hit.get("extraction_result"):
        cached_payload = cache_hit["extraction_result"]
        if isinstance(cached_payload, str):
            try:
                cached_payload = json.loads(cached_payload)
            except json.JSONDecodeError:
                cached_payload = None

        if cached_payload:
            logger.info(
                "extraction_cache HIT %s (nodes=%d rels=%d age=%s)",
                workspace_doc_id,
                cache_hit["nodes_count"],
                cache_hit["relationships_count"],
                cache_hit.get("created_at"),
            )
            nodes = cached_payload.get("nodes", [])
            rels = cached_payload.get("relationships", [])
            cached_confidence = float(cached_payload.get("confidence_score") or 0.8)

            _update_workspace_document(
                workspace_doc_id, "entities_extracted",
                confidence_score=cached_confidence, extraction_result=cached_payload,
            )
            _report_status(
                workspace_doc_id,
                "entities_extracted",
                confidence_score=cached_confidence,
                extraction_result=json.dumps(cached_payload, ensure_ascii=False, default=str),
                agent_id=agent_id, batch_id=batch_id,
            )
            _maybe_finalize_batch(batch_id)
            return {
                "doc_id": workspace_doc_id,
                "nodes": nodes,
                "relationships": rels,
                "confidence_score": cached_confidence,
                "cached": True,
            }

    # ── Cache miss: gercek extraction ─────────────────────────────────
    _update_workspace_document(workspace_doc_id, "extracting_entities", celery_task_id=self.request.id)
    _report_status(workspace_doc_id, "extracting_entities", agent_id=agent_id, batch_id=batch_id)

    try:
        from src.agentic_ocr import process_agentic_ocr_text_mode

        result = process_agentic_ocr_text_mode(
            ocr_texts=[text],
            file_name=file_name,
            domain=domain,
            skill_id=skill_id if skill_id else None,
        )

        nodes = result.get("nodes", result.get("entities", []))
        relationships = result.get("relationships", [])

        confidence = 0.8
        if not nodes:
            confidence = 0.3
        elif len(nodes) < 3:
            confidence = 0.5

        result_payload = {
            "nodes": nodes[:50],
            "relationships": relationships[:50],
            "confidence_score": confidence,
        }
        _update_workspace_document(
            workspace_doc_id, "entities_extracted",
            confidence_score=confidence, extraction_result=result_payload,
        )
        _report_status(
            workspace_doc_id,
            "entities_extracted",
            confidence_score=confidence,
            extraction_result=json.dumps(result_payload, ensure_ascii=False, default=str),
            agent_id=agent_id, batch_id=batch_id,
        )
        _maybe_finalize_batch(batch_id)

        # Cache write (basarili extraction sonrasi)
        if EXTRACTION_CACHE_ENABLED and content_hash and extractor_version:
            try:
                from src.extraction_cache import save_extraction_cache
                save_extraction_cache(
                    EVENT_STORE_DSN,
                    doc_id=workspace_doc_id,
                    content_hash=content_hash,
                    extractor_version=extractor_version,
                    extraction_result=result_payload,
                    agent_id=agent_id,
                    batch_id=batch_id,
                )
            except Exception as exc:  # pragma: no cover
                logger.warning("extraction_cache save failed for %s: %s", workspace_doc_id, exc)

        return {
            "doc_id": workspace_doc_id,
            "nodes": nodes,
            "relationships": relationships,
            "confidence_score": confidence,
            "cached": False,
        }

    except Exception as e:
        logger.error("extract_entities failed for %s: %s", workspace_doc_id, e)
        _update_workspace_document(workspace_doc_id, "failed", error_message=str(e)[:500])
        _report_status(workspace_doc_id, "failed", error_message=str(e)[:500], agent_id=agent_id, batch_id=batch_id)
        _maybe_finalize_batch(batch_id)
        raise self.retry(exc=e, countdown=60, max_retries=3)


# ============================================================================
# TASK 4: FULL PIPELINE (Orchestrator)
# ============================================================================

@app.task(bind=True, name="workspace.process_document", max_retries=2)
def process_document_task(
    self,
    workspace_doc_id: str,
    file_path: str,
    file_name: str = "",
    entity_schemas: Optional[List[Dict]] = None,
    relationship_schemas: Optional[List[Dict]] = None,
    skill_id: str = "",
    domain: str = "",
    agent_id: str = "",
    batch_id: str = "",
    ocr_mode: str = "hybrid",
    # Legacy kwargs accepted for backwards compatibility:
    workspace_id: str = "",
    batch_job_id: str = "",
):
    """
    Tek belge icin tam hybrid pipeline:
    PDF -> PyMuPDF -> PNG -> GeminiOCR -> text -> AgenticOCR text mode -> entities

    Her adimda hem PostgreSQL ``workspace_documents`` rownu UPDATE eder
    hem de HTTP callback gondererek NotificationManager'a iletir.
    Tum belgeler bittiginde ``_maybe_finalize_batch`` ile batch_jobs.status
    'completed'a geciser ve ``/callback/batch-complete`` tetiklenir (sadece
    ilk transition).
    """
    if not agent_id and workspace_id and workspace_id.startswith("ws-"):
        agent_id = workspace_id[len("ws-"):]
    if not batch_id and batch_job_id:
        batch_id = batch_job_id

    start = time.time()
    logger.info(
        "workspace.process_document: %s (%s) [agent=%s, batch=%s]",
        workspace_doc_id, file_name or file_path, agent_id, batch_id,
    )
    _update_workspace_document(workspace_doc_id, "processing", celery_task_id=self.request.id)
    _report_status(workspace_doc_id, "processing", agent_id=agent_id, batch_id=batch_id)

    try:
        # Step 1: Image extraction
        p = Path(file_path)
        suffix = p.suffix.lower()

        if suffix == ".pdf":
            output_dir = tempfile.mkdtemp(prefix=f"ws_{workspace_doc_id}_")
            images = _extract_pdf_images(file_path, output_dir)
            if not images:
                _update_workspace_document(workspace_doc_id, "failed", error_message="PDF->PNG conversion failed")
                _report_status(workspace_doc_id, "failed", error_message="PDF->PNG conversion failed", agent_id=agent_id, batch_id=batch_id)
                _maybe_finalize_batch(batch_id)
                return {"doc_id": workspace_doc_id, "status": "failed", "error": "PDF->PNG failed"}
        elif suffix in (".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp"):
            images = [file_path]
        else:
            _update_workspace_document(workspace_doc_id, "failed", error_message=f"Unsupported: {suffix}")
            _report_status(workspace_doc_id, "failed", error_message=f"Unsupported: {suffix}", agent_id=agent_id, batch_id=batch_id)
            _maybe_finalize_batch(batch_id)
            return {"doc_id": workspace_doc_id, "status": "failed", "error": f"Unsupported: {suffix}"}

        _update_workspace_document(workspace_doc_id, "images_extracted")
        _report_status(workspace_doc_id, "images_extracted", agent_id=agent_id, batch_id=batch_id)
        logger.info("Step 1 done: %d images for %s", len(images), workspace_doc_id)

        # Step 2: GeminiOCR
        from src.agents import process_gemini_ocr

        output_dir_ocr = os.getenv("OUTPUT_DIR", "output_celery")
        ocr_result = process_gemini_ocr(
            image_list=images,
            output_dir=output_dir_ocr,
            file_name=file_name or p.name,
        )

        merged_text = ocr_result.get("merged_text", "")
        if not merged_text:
            ocr_texts = ocr_result.get("ocr_texts", [])
            merged_text = "\n\n".join(t for t in ocr_texts if t)

        if not merged_text:
            _update_workspace_document(workspace_doc_id, "failed", error_message="OCR returned empty text")
            _report_status(workspace_doc_id, "failed", error_message="OCR returned empty text", agent_id=agent_id, batch_id=batch_id)
            _maybe_finalize_batch(batch_id)
            return {"doc_id": workspace_doc_id, "status": "failed", "error": "Empty OCR"}

        _update_workspace_document(workspace_doc_id, "ocr_completed")
        _report_status(workspace_doc_id, "ocr_completed", agent_id=agent_id, batch_id=batch_id)
        logger.info("Step 2 done: %d chars OCR text for %s", len(merged_text), workspace_doc_id)

        # Step 3: AgenticOCR text mode (entity extraction)
        from src.agentic_ocr import process_agentic_ocr_text_mode

        extraction = process_agentic_ocr_text_mode(
            ocr_texts=[merged_text],
            file_name=file_name or p.name,
            domain=domain,
            skill_id=skill_id if skill_id else None,
        )

        nodes = extraction.get("nodes", extraction.get("entities", []))
        relationships = extraction.get("relationships", [])

        confidence = 0.8
        if not nodes:
            confidence = 0.3
        elif len(nodes) < 3:
            confidence = 0.5

        final_status = "completed" if confidence >= 0.5 else "low_confidence"
        result_payload = {"nodes": nodes, "relationships": relationships}

        _update_workspace_document(
            workspace_doc_id, final_status,
            confidence_score=confidence, extraction_result=result_payload,
        )
        _report_status(
            workspace_doc_id,
            final_status,
            confidence_score=confidence,
            extraction_result=json.dumps(result_payload, ensure_ascii=False, default=str),
            agent_id=agent_id, batch_id=batch_id,
        )
        _maybe_finalize_batch(batch_id)

        elapsed = time.time() - start
        logger.info(
            "workspace.process_document done: %s -> %s (%.1fs, %d nodes, %d rels, conf=%.2f)",
            workspace_doc_id, final_status, elapsed, len(nodes), len(relationships), confidence,
        )

        return {
            "doc_id": workspace_doc_id,
            "status": final_status,
            "nodes_count": len(nodes),
            "relationships_count": len(relationships),
            "confidence_score": confidence,
            "elapsed_seconds": round(elapsed, 1),
            "ocr_chars": len(merged_text),
            "pages": len(images),
        }

    except Exception as e:
        logger.error("process_document failed for %s: %s", workspace_doc_id, e)
        import traceback
        logger.error("Traceback: %s", traceback.format_exc())
        _update_workspace_document(workspace_doc_id, "failed", error_message=str(e)[:500])
        _report_status(workspace_doc_id, "failed", error_message=str(e)[:500], agent_id=agent_id, batch_id=batch_id)
        _maybe_finalize_batch(batch_id)
        raise self.retry(exc=e, countdown=60, max_retries=2)


# ============================================================================
# MCP GATEWAY HELPERS
# ============================================================================

MCP_GATEWAY_URL = os.getenv("MCP_GATEWAY_URL", "http://localhost:4444")
MCP_GATEWAY_JWT_SECRET = os.getenv(
    "MCP_GATEWAY_JWT_SECRET",
    os.getenv("JWT_SECRET_KEY", "dev-secret-key-32-chars-minimum!!"),
)

CONFIDENCE_AUTO_ACCEPT = float(os.getenv("CONFIDENCE_AUTO_ACCEPT", "0.70"))
CONFIDENCE_REVIEW = float(os.getenv("CONFIDENCE_REVIEW", "0.40"))


def _get_gateway_token() -> str:
    """MCP Gateway icin JWT token olustur."""
    import jwt as pyjwt
    from datetime import datetime, timedelta, timezone
    import uuid as _uuid

    now = datetime.now(timezone.utc)
    payload = {
        "sub": "celery-worker@system",
        "email": "celery-worker@system",
        "iat": now,
        "iss": "mcpgateway",
        "aud": "mcpgateway-api",
        "jti": str(_uuid.uuid4()),
        "exp": now + timedelta(hours=1),
    }
    return pyjwt.encode(payload, MCP_GATEWAY_JWT_SECRET, algorithm="HS256")


def _invoke_mcp_tool(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """MCP Gateway uzerinden tool cagir (sync, Celery worker icin)."""
    import httpx

    token = _get_gateway_token()
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }

    with httpx.Client(timeout=120) as client:
        resp = client.post(
            f"{MCP_GATEWAY_URL}/rpc",
            json=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        resp.raise_for_status()
        result = resp.json()

    if "error" in result:
        raise RuntimeError(f"MCP RPC error: {result['error']}")

    return result.get("result", {})


def _create_elicitation_request(
    doc_id: str,
    workspace_id: str,
    batch_job_id: str,
    extraction_result: Dict[str, Any],
    confidence_score: float,
    file_name: str = "",
):
    """Dusuk guvenli sonuc icin ElicitationRequest olustur (callback uzerinden)."""
    try:
        import httpx

        elicitation_url = os.getenv(
            "AGENT_BUILDER_ELICITATION_URL",
            "http://localhost:8001/api/v2/workspaces/elicitation-request",
        )

        payload = {
            "doc_id": doc_id,
            "workspace_id": workspace_id,
            "batch_job_id": batch_job_id,
            "extraction_result": json.dumps(extraction_result, ensure_ascii=False, default=str),
            "confidence_score": confidence_score,
            "file_name": file_name,
        }

        with httpx.Client(timeout=10) as client:
            resp = client.post(elicitation_url, json=payload)
            resp.raise_for_status()

    except Exception as e:
        logger.warning("Elicitation request creation failed for %s: %s", doc_id, e)


# ============================================================================
# TASK 5: MCP-BASED DOCUMENT PROCESSING
# ============================================================================

@app.task(bind=True, name="workspace.process_document_mcp", max_retries=2)
def process_document_mcp_task(
    self,
    workspace_doc_id: str,
    minio_bucket: str = "documents",
    minio_key: str = "",
    file_name: str = "",
    schema_json: str = "",
    agent_id: str = "",
    batch_id: str = "",
    auto_accept: bool = True,
    # Legacy kwargs for backwards compatibility
    workspace_id: str = "",
    batch_job_id: str = "",
):
    """
    MCP pipeline ile belge isleme:
    1. MinIO'dan belgeyi oku (MCP storage tool)
    2. MCP extraction tool ile entity/relationship cikar (Sampling)
    3. Guven puanina gore otomatik kabul veya review kuyruklama

    Bu task dogrudan LLM cagirmaz - MCP Gateway uzerinden
    extraction_server'in Sampling mekanizmasini kullanir.

    Args:
        workspace_doc_id: Belge ID
        minio_bucket: MinIO bucket adi
        minio_key: MinIO object key
        file_name: Dosya adi
        schema_json: Extraction schema (JSON string)
        workspace_id: Workspace ID
        batch_job_id: Batch job ID
        auto_accept: True ise yuksek guvenli sonuclari otomatik kabul et
    """
    if not agent_id and workspace_id and workspace_id.startswith("ws-"):
        agent_id = workspace_id[len("ws-"):]
    if not batch_id and batch_job_id:
        batch_id = batch_job_id

    start = time.time()
    logger.info(
        "workspace.process_document_mcp: %s (bucket=%s, key=%s) [agent=%s, batch=%s]",
        workspace_doc_id, minio_bucket, minio_key, agent_id, batch_id,
    )
    _update_workspace_document(workspace_doc_id, "processing", celery_task_id=self.request.id)
    _report_status(workspace_doc_id, "processing", agent_id=agent_id, batch_id=batch_id)

    try:
        # Step 1: Extract entities via MCP (uses Sampling internally)
        extract_args: Dict[str, Any] = {
            "bucket": minio_bucket,
            "key": minio_key,
            "auto_accept": auto_accept,
        }
        if schema_json:
            extract_args["schema_json"] = schema_json

        extraction_result = _invoke_mcp_tool("extract_extract_entities", extract_args)

        # Parse result
        content = extraction_result if isinstance(extraction_result, dict) else {}
        if isinstance(extraction_result, list):
            for item in extraction_result:
                if isinstance(item, dict) and item.get("type") == "text":
                    try:
                        content = json.loads(item.get("text", "{}"))
                    except (json.JSONDecodeError, TypeError):
                        content = {"raw": item.get("text", "")}
                    break

        nodes = content.get("nodes", content.get("entities", []))
        relationships = content.get("relationships", [])
        confidence = content.get("confidence", content.get("heuristic_confidence", 0.5))
        status_from_server = content.get("status", "")

        # Step 2: Determine action based on confidence
        if status_from_server == "rejected":
            final_status = "failed"
            confidence = 0.0
        elif confidence >= CONFIDENCE_AUTO_ACCEPT:
            final_status = "completed"
        elif confidence >= CONFIDENCE_REVIEW:
            final_status = "needs_review"
        else:
            final_status = "low_confidence"

        # Step 3: Queue for elicitation if needed
        if final_status in ("needs_review", "low_confidence"):
            _create_elicitation_request(
                doc_id=workspace_doc_id,
                workspace_id=workspace_id or f"ws-{agent_id}",
                batch_job_id=batch_id or batch_job_id,
                extraction_result={"nodes": nodes, "relationships": relationships},
                confidence_score=confidence,
                file_name=file_name,
            )

        # Step 4: Report status
        result_payload = {"nodes": nodes[:50], "relationships": relationships[:50]}
        extraction_json = json.dumps(result_payload, ensure_ascii=False, default=str)

        _update_workspace_document(
            workspace_doc_id, final_status,
            confidence_score=confidence, extraction_result=result_payload,
        )
        _report_status(
            workspace_doc_id,
            final_status,
            confidence_score=confidence,
            extraction_result=extraction_json,
            agent_id=agent_id, batch_id=batch_id,
        )
        _maybe_finalize_batch(batch_id)

        elapsed = time.time() - start
        logger.info(
            "workspace.process_document_mcp done: %s -> %s (%.1fs, %d nodes, %d rels, conf=%.2f)",
            workspace_doc_id, final_status, elapsed, len(nodes), len(relationships), confidence,
        )

        return {
            "doc_id": workspace_doc_id,
            "status": final_status,
            "pipeline": "mcp",
            "nodes_count": len(nodes),
            "relationships_count": len(relationships),
            "confidence_score": confidence,
            "elapsed_seconds": round(elapsed, 1),
        }

    except Exception as e:
        logger.error("process_document_mcp failed for %s: %s", workspace_doc_id, e)
        import traceback
        logger.error("Traceback: %s", traceback.format_exc())
        _update_workspace_document(workspace_doc_id, "failed", error_message=str(e)[:500])
        _report_status(workspace_doc_id, "failed", error_message=str(e)[:500], agent_id=agent_id, batch_id=batch_id)
        _maybe_finalize_batch(batch_id)
        raise self.retry(exc=e, countdown=60, max_retries=2)


# ============================================================================
# WORKFLOW TASKS -- Text-based tasks for the workflow pipeline
# ============================================================================

import re as _re

_SENTENCE_SPLIT_RE = _re.compile(r"(?<=[.!?])\s+")


def _fixed_size_chunks(text: str, size: int, overlap: int) -> list:
    chunks = []
    start = 0
    while start < len(text):
        end = start + size
        chunks.append(text[start:end])
        start = end - overlap if overlap < size else end
    return chunks


def _sentence_chunks(text: str, target_size: int, overlap: int) -> list:
    sentences = _SENTENCE_SPLIT_RE.split(text)
    chunks, current, current_len = [], [], 0
    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        if current_len + len(sent) > target_size and current:
            chunks.append(" ".join(current))
            if overlap > 0:
                tail = " ".join(current)[-overlap:]
                current, current_len = [tail], len(tail)
            else:
                current, current_len = [], 0
        current.append(sent)
        current_len += len(sent) + 1
    if current:
        chunks.append(" ".join(current))
    return chunks


@app.task(bind=True, name="workspace.chunk_text", max_retries=3)
def chunk_text_task(
    self,
    doc_id: str,
    text: str,
    file_name: str = "",
    chunk_size: int = 2000,
    overlap: int = 200,
    strategy: str = "sentence_based",
    agent_id: str = "",
    batch_id: str = "",
):
    """
    Text-bazli chunking. Workflow pipeline icin.

    Returns:
        {"doc_id": ..., "file_name": ..., "chunks": [{"text": ..., "index": ..., "char_count": ...}]}
    """
    logger.info("workspace.chunk_text: %s (%d chars, strategy=%s)", doc_id, len(text), strategy)

    try:
        if strategy == "sentence_based":
            parts = _sentence_chunks(text, chunk_size, overlap)
        else:
            parts = _fixed_size_chunks(text, chunk_size, overlap)

        chunks = [
            {"text": p, "index": i, "char_count": len(p)}
            for i, p in enumerate(parts)
        ]

        logger.info("workspace.chunk_text done: %s -> %d chunks", doc_id, len(chunks))
        return {
            "doc_id": doc_id,
            "file_name": file_name,
            "chunks": chunks,
            "total_chunks": len(chunks),
        }

    except Exception as e:
        logger.error("chunk_text failed for %s: %s", doc_id, e)
        raise self.retry(exc=e, countdown=30, max_retries=3)


@app.task(bind=True, name="workspace.create_embeddings", max_retries=3)
def create_embeddings_task_workflow(
    self,
    doc_id: str,
    texts: List[str],
    entity_ids: Optional[List[str]] = None,
    model: str = "openai",
    neo4j_database: str = "",
    agent_id: str = "",
):
    """
    Text listesinden embedding olusturur ve opsiyonel olarak Neo4j'ye yazar.

    Returns:
        {"doc_id": ..., "embedded_count": ..., "status": ...}
    """
    logger.info("workspace.create_embeddings: %s (%d texts, model=%s)", doc_id, len(texts), model)

    try:
        embedder = None
        if model == "openai":
            from langchain_openai import OpenAIEmbeddings
            embedder = OpenAIEmbeddings()
        elif model == "gemini":
            from langchain_google_genai import GoogleGenerativeAIEmbeddings
            embedder = GoogleGenerativeAIEmbeddings(model="models/embedding-001")
        else:
            from langchain_openai import OpenAIEmbeddings
            embedder = OpenAIEmbeddings()

        vectors = embedder.embed_documents(texts)
        embedded_count = len(vectors)

        if entity_ids and len(entity_ids) == len(vectors):
            neo4j_uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
            neo4j_user = os.getenv("NEO4J_USERNAME", "neo4j")
            neo4j_pass = os.getenv("NEO4J_PASSWORD", "password")
            database = neo4j_database or os.getenv("NEO4J_DATABASE", "neo4j")

            try:
                from neo4j import GraphDatabase
                driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_pass))
                with driver.session(database=database) as session:
                    for eid, vec in zip(entity_ids, vectors):
                        session.run("MATCH (n {id: $id}) SET n.embedding = $vec", id=eid, vec=vec)
                driver.close()
                logger.info("workspace.create_embeddings: wrote %d embeddings to Neo4j", len(entity_ids))
            except Exception as neo4j_err:
                logger.warning("Neo4j embedding write failed: %s", neo4j_err)

        logger.info("workspace.create_embeddings done: %s -> %d embedded", doc_id, embedded_count)
        return {
            "doc_id": doc_id,
            "embedded_count": embedded_count,
            "status": "completed",
        }

    except Exception as e:
        logger.error("create_embeddings failed for %s: %s", doc_id, e)
        raise self.retry(exc=e, countdown=60, max_retries=3)


@app.task(bind=True, name="workspace.summarize_for_wiki", max_retries=3)
def summarize_for_wiki_task(
    self,
    doc_id: str,
    text: str,
    file_name: str = "",
    max_chars: int = 8000,
    agent_id: str = "",
):
    """
    OCR text'i LLM ile ozetler ve wiki sayfasi icin yapilandirilmis JSON doner.

    Returns:
        {"doc_id": ..., "title": ..., "summary": ..., "key_info": ...,
         "entities_mentioned": [...], "category": ...}
    """
    logger.info("workspace.summarize_for_wiki: %s (%d chars)", doc_id, len(text))

    try:
        provider = os.getenv("EVOLVING_OCR_PROVIDER", "google").lower()
        model_name = os.getenv("EVOLVING_OCR_MODEL", "gemini-2.5-flash")

        if provider in ("google", "gemini"):
            from langchain_google_genai import ChatGoogleGenerativeAI
            llm = ChatGoogleGenerativeAI(model=model_name, temperature=0.1)
        elif provider in ("openai", "gpt"):
            from langchain_openai import ChatOpenAI
            llm = ChatOpenAI(model=model_name, temperature=0.1)
        else:
            from langchain_anthropic import ChatAnthropic
            llm = ChatAnthropic(model=model_name, temperature=0.1)

        prompt = (
            "Asagidaki OCR metni bir belgeden cikarilmistir. Bu metinden yapilandirilmis\n"
            "bir wiki sayfasi olustur. Ciktini asagidaki JSON formatinda ver:\n\n"
            '{\n'
            '  "title": "Belgenin kisa basligini yaz",\n'
            '  "summary": "2-3 cumlelik belge ozeti",\n'
            '  "key_info": "- Madde 1\\n- Madde 2\\n- Madde 3 seklinde onemli bilgileri listele",\n'
            '  "entities_mentioned": ["entity1", "entity2"],\n'
            '  "category": "sources"\n'
            '}\n\n'
            "Kurallar:\n"
            "- Turkce yaz.\n"
            "- Gercek metinden cikar, UYDURMA.\n"
            "- entities_mentioned: metinde gecen onemli kisileri, sirketleri, yerleri listele.\n"
            "- category: sources, entities, analysis veya general olabilir.\n\n"
            f"OCR Metni (ilk {max_chars} karakter):\n{text[:max_chars]}"
        )

        import asyncio
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        response = loop.run_until_complete(llm.ainvoke(prompt))
        content = response.content if hasattr(response, "content") else str(response)

        json_match = _re.search(r"\{[\s\S]*\}", content)
        if json_match:
            parsed = json.loads(json_match.group())
        else:
            parsed = {
                "title": file_name.rsplit(".", 1)[0] if "." in file_name else file_name,
                "summary": text[:200],
                "key_info": "",
                "entities_mentioned": [],
                "category": "sources",
            }

        result = {
            "doc_id": doc_id,
            "file_name": file_name,
            "title": parsed.get("title", file_name),
            "summary": parsed.get("summary", ""),
            "key_info": parsed.get("key_info", ""),
            "entities_mentioned": parsed.get("entities_mentioned", []),
            "category": parsed.get("category", "sources"),
        }
        logger.info("workspace.summarize_for_wiki done: %s -> %s", doc_id, result["title"])
        return result

    except Exception as e:
        logger.error("summarize_for_wiki failed for %s: %s", doc_id, e)
        raise self.retry(exc=e, countdown=60, max_retries=3)


@app.task(bind=True, name="workspace.design_ontology", max_retries=3)
def design_ontology_task(
    self,
    doc_id: str,
    wiki_content: str,
    existing_ontology_json: str = "",
    max_wiki_chars: int = 15000,
    agent_id: str = "",
):
    """
    Wiki content'ten LLM ile ontoloji tasarimi yapar.

    Returns:
        {"doc_id": ..., "proposed_ontology": {...}, "status": ...}
    """
    logger.info("workspace.design_ontology: %s (%d chars wiki)", doc_id, len(wiki_content))

    try:
        provider = os.getenv("EVOLVING_OCR_PROVIDER", "google").lower()
        model_name = os.getenv("EVOLVING_OCR_MODEL", "gemini-2.5-flash")

        if provider in ("google", "gemini"):
            from langchain_google_genai import ChatGoogleGenerativeAI
            llm = ChatGoogleGenerativeAI(model=model_name, temperature=0.2)
        elif provider in ("openai", "gpt"):
            from langchain_openai import ChatOpenAI
            llm = ChatOpenAI(model=model_name, temperature=0.2)
        else:
            from langchain_anthropic import ChatAnthropic
            llm = ChatAnthropic(model=model_name, temperature=0.2)

        existing_str = existing_ontology_json if existing_ontology_json else "Henuz ontoloji yok."

        prompt = (
            "Asagidaki wiki sayfalari belge ozetlerini icermektedir. Bu sayfalardan\n"
            "bir bilgi grafi ontolojisi tasarla.\n\n"
            f"Mevcut ontoloji (varsa):\n{existing_str}\n\n"
            f"Wiki sayfalari:\n{wiki_content[:max_wiki_chars]}\n\n"
            "Ciktini su JSON formatinda ver:\n"
            '{\n'
            '  "domain": "Bu belgelerin ait oldugu alan",\n'
            '  "goal": "Bu ontolojinin amaci (1 cumle)",\n'
            '  "entity_classes": [\n'
            '    {"name": "EntityAdi", "description": "tanim", "parent": "", "properties": [{"name": "prop", "type": "string", "constraint": "required", "description": "aciklama"}]}\n'
            '  ],\n'
            '  "relationship_predicates": [\n'
            '    {"name": "ILISKI_ADI", "source": "KaynakEntity", "target": "HedefEntity", "edge_properties": [], "description": "aciklama"}\n'
            '  ],\n'
            '  "constraints": ["Kural 1", "Kural 2"]\n'
            '}\n\n'
            "Kurallar:\n"
            "- Mevcut ontoloji varsa onunla BIRLESIK calis\n"
            "- Entity isimleri PascalCase, relationship isimleri UPPER_SNAKE_CASE\n"
            "- Turkce aciklamalar yaz"
        )

        import asyncio
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        response = loop.run_until_complete(llm.ainvoke(prompt))
        content = response.content if hasattr(response, "content") else str(response)

        json_match = _re.search(r"\{[\s\S]*\}", content)
        if json_match:
            proposed = json.loads(json_match.group())
        else:
            logger.warning("No JSON in ontology design response for %s", doc_id)
            return {"doc_id": doc_id, "proposed_ontology": {}, "status": "no_json"}

        logger.info(
            "workspace.design_ontology done: %s -> %d entities, %d rels",
            doc_id, len(proposed.get("entity_classes", [])),
            len(proposed.get("relationship_predicates", [])),
        )
        return {
            "doc_id": doc_id,
            "proposed_ontology": proposed,
            "status": "completed",
        }

    except Exception as e:
        logger.error("design_ontology failed for %s: %s", doc_id, e)
        raise self.retry(exc=e, countdown=60, max_retries=3)
