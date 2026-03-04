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

import asyncio
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
    "http://localhost:8001/api/v2/workspaces/callback/document-status",
)

IMAGE_RESOLUTION_SCALE = 2.0


# ============================================================================
# HELPERS
# ============================================================================

def _report_status(
    doc_id: str,
    status: str,
    confidence_score: float = 0.0,
    extraction_result: Optional[str] = None,
    error_message: Optional[str] = None,
):
    """Agent Builder callback endpoint'ine durum bildir."""
    try:
        import httpx

        params: Dict[str, Any] = {"doc_id": doc_id, "status": status}
        if confidence_score > 0:
            params["confidence_score"] = confidence_score
        if error_message:
            params["error_message"] = error_message

        body = extraction_result if extraction_result else None

        with httpx.Client(timeout=10) as client:
            resp = client.post(
                CALLBACK_URL,
                params=params,
                content=body,
                headers={"Content-Type": "application/json"} if body else {},
            )
            resp.raise_for_status()

    except Exception as e:
        logger.warning("Callback failed for %s (status=%s): %s", doc_id, status, e)


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
def extract_images_task(self, workspace_doc_id: str, file_path: str, output_dir: str = ""):
    """
    PDF dosyasini PNG sayfa goruntulerine donusturur.

    Returns:
        {"doc_id": ..., "images": [...], "page_count": N}
    """
    logger.info("workspace.extract_images: %s (%s)", workspace_doc_id, file_path)
    _report_status(workspace_doc_id, "extracting_images")

    try:
        p = Path(file_path)
        suffix = p.suffix.lower()

        if suffix == ".pdf":
            target_dir = output_dir or tempfile.mkdtemp(prefix=f"ws_{workspace_doc_id}_")
            images = _extract_pdf_images(file_path, target_dir)
            if not images:
                _report_status(workspace_doc_id, "failed", error_message="PDF->PNG conversion failed")
                return {"doc_id": workspace_doc_id, "images": [], "error": "PDF->PNG failed"}

        elif suffix in (".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp"):
            images = [file_path]
        else:
            _report_status(workspace_doc_id, "failed", error_message=f"Unsupported file type: {suffix}")
            return {"doc_id": workspace_doc_id, "images": [], "error": f"Unsupported: {suffix}"}

        _report_status(workspace_doc_id, "images_extracted")
        return {"doc_id": workspace_doc_id, "images": images, "page_count": len(images)}

    except Exception as e:
        logger.error("extract_images failed for %s: %s", workspace_doc_id, e)
        _report_status(workspace_doc_id, "failed", error_message=str(e)[:500])
        raise self.retry(exc=e, countdown=30, max_retries=2)


# ============================================================================
# TASK 2: OCR (GeminiOCR)
# ============================================================================

@app.task(bind=True, name="workspace.ocr_pages", max_retries=3)
def ocr_pages_task(self, workspace_doc_id: str, image_paths: List[str], file_name: str = ""):
    """
    Sayfa goruntuleri uzerinde GeminiOCR ile metin cikarir.

    Returns:
        {"doc_id": ..., "merged_text": "...", "page_count": N}
    """
    logger.info("workspace.ocr_pages: %s (%d images)", workspace_doc_id, len(image_paths))
    _report_status(workspace_doc_id, "ocr_processing")

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
            _report_status(workspace_doc_id, "failed", error_message="OCR returned empty text")
            return {"doc_id": workspace_doc_id, "merged_text": "", "error": "Empty OCR result"}

        _report_status(workspace_doc_id, "ocr_completed")
        return {
            "doc_id": workspace_doc_id,
            "merged_text": merged,
            "page_count": len(image_paths),
            "token_usage": result.get("token_usage", {}),
        }

    except Exception as e:
        logger.error("ocr_pages failed for %s: %s", workspace_doc_id, e)
        _report_status(workspace_doc_id, "failed", error_message=str(e)[:500])
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
):
    """
    OCR metinden schema-driven entity/relationship cikarir.

    Hybrid pipeline'in ikinci asamasi: AgenticOCR text mode.

    Returns:
        {"doc_id": ..., "nodes": [...], "relationships": [...], "confidence_score": float}
    """
    logger.info("workspace.extract_entities: %s (%d chars)", workspace_doc_id, len(text))
    _report_status(workspace_doc_id, "extracting_entities")

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

        _report_status(
            workspace_doc_id,
            "entities_extracted",
            confidence_score=confidence,
            extraction_result=json.dumps(
                {"nodes": nodes[:50], "relationships": relationships[:50]},
                ensure_ascii=False, default=str,
            ),
        )

        return {
            "doc_id": workspace_doc_id,
            "nodes": nodes,
            "relationships": relationships,
            "confidence_score": confidence,
        }

    except Exception as e:
        logger.error("extract_entities failed for %s: %s", workspace_doc_id, e)
        _report_status(workspace_doc_id, "failed", error_message=str(e)[:500])
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
    workspace_id: str = "",
    batch_job_id: str = "",
    ocr_mode: str = "hybrid",
):
    """
    Tek belge icin tam hybrid pipeline:
    PDF -> PyMuPDF -> PNG -> GeminiOCR -> text -> AgenticOCR text mode -> entities

    Her adimda callback gondererek Event Store'a kaydeder.
    """
    start = time.time()
    logger.info(
        "workspace.process_document: %s (%s) [ws=%s, batch=%s]",
        workspace_doc_id, file_name or file_path, workspace_id, batch_job_id,
    )
    _report_status(workspace_doc_id, "processing")

    try:
        # Step 1: Image extraction
        p = Path(file_path)
        suffix = p.suffix.lower()

        if suffix == ".pdf":
            output_dir = tempfile.mkdtemp(prefix=f"ws_{workspace_doc_id}_")
            images = _extract_pdf_images(file_path, output_dir)
            if not images:
                _report_status(workspace_doc_id, "failed", error_message="PDF->PNG conversion failed")
                return {"doc_id": workspace_doc_id, "status": "failed", "error": "PDF->PNG failed"}
        elif suffix in (".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp"):
            images = [file_path]
        else:
            _report_status(workspace_doc_id, "failed", error_message=f"Unsupported: {suffix}")
            return {"doc_id": workspace_doc_id, "status": "failed", "error": f"Unsupported: {suffix}"}

        _report_status(workspace_doc_id, "images_extracted")
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
            _report_status(workspace_doc_id, "failed", error_message="OCR returned empty text")
            return {"doc_id": workspace_doc_id, "status": "failed", "error": "Empty OCR"}

        _report_status(workspace_doc_id, "ocr_completed")
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

        _report_status(
            workspace_doc_id,
            final_status,
            confidence_score=confidence,
            extraction_result=json.dumps(
                {"nodes": nodes, "relationships": relationships},
                ensure_ascii=False, default=str,
            ),
        )

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
        _report_status(workspace_doc_id, "failed", error_message=str(e)[:500])
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
    workspace_id: str = "",
    batch_job_id: str = "",
    auto_accept: bool = True,
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
    start = time.time()
    logger.info(
        "workspace.process_document_mcp: %s (bucket=%s, key=%s) [ws=%s, batch=%s]",
        workspace_doc_id, minio_bucket, minio_key, workspace_id, batch_job_id,
    )
    _report_status(workspace_doc_id, "processing")

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
                workspace_id=workspace_id,
                batch_job_id=batch_job_id,
                extraction_result={"nodes": nodes, "relationships": relationships},
                confidence_score=confidence,
                file_name=file_name,
            )

        # Step 4: Report status
        extraction_json = json.dumps(
            {"nodes": nodes[:50], "relationships": relationships[:50]},
            ensure_ascii=False, default=str,
        )

        _report_status(
            workspace_doc_id,
            final_status,
            confidence_score=confidence,
            extraction_result=extraction_json,
        )

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
        _report_status(workspace_doc_id, "failed", error_message=str(e)[:500])
        raise self.retry(exc=e, countdown=60, max_retries=2)
