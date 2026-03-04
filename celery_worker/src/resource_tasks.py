"""
Resource Image Extraction Tasks
================================

PDF -> PNG image extraction for Resource documents.
Downloads PDF from MinIO, extracts page images with PyMuPDF,
uploads PNGs back to MinIO, and updates PostgreSQL status.
"""

import logging
import os
import tempfile
from pathlib import Path
from typing import List

from src.celery_app import app

logger = logging.getLogger(__name__)

IMAGE_RESOLUTION_SCALE = float(os.getenv("IMAGE_RESOLUTION_SCALE", "2.0"))
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9010")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"

AGENT_BUILDER_URL = os.getenv("AGENT_BUILDER_URL", "http://localhost:8001")


def _get_minio():
    from minio import Minio
    return Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=MINIO_SECURE,
    )


def _extract_pdf_images(file_path: str, output_dir: str) -> List[str]:
    """PyMuPDF ile PDF -> PNG conversion."""
    try:
        import fitz
    except ImportError:
        logger.error("PyMuPDF (fitz) not installed")
        return []

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


def _report_extraction_status(
    doc_id: str,
    status: str,
    page_count: int = 0,
    image_count: int = 0,
    error_message: str = "",
):
    """Report extraction completion to agent-builder API."""
    try:
        import httpx
        url = f"{AGENT_BUILDER_URL}/api/v2/resources/documents/{doc_id}/extraction-complete"
        with httpx.Client(timeout=10) as client:
            resp = client.post(url, params={
                "status": status,
                "page_count": page_count,
                "image_count": image_count,
                "error_message": error_message,
            })
            resp.raise_for_status()
    except Exception as e:
        logger.warning("Extraction callback failed for %s: %s", doc_id, e)


@app.task(bind=True, name="resource.extract_images", max_retries=2, queue="resource")
def extract_images_task(
    self,
    resource_doc_id: str,
    resource_id: str,
    minio_bucket: str = "resources",
    minio_key: str = "",
):
    """
    1. Download PDF from MinIO
    2. Extract page images with PyMuPDF
    3. Upload PNGs to MinIO under resources/{resource_id}/images/{doc_id}/
    4. Report status via API callback
    """
    logger.info("Starting image extraction: doc=%s key=%s", resource_doc_id, minio_key)

    if not minio_key:
        _report_extraction_status(resource_doc_id, "failed", error_message="No minio_key provided")
        return {"status": "failed", "error": "No minio_key"}

    minio_client = _get_minio()

    with tempfile.TemporaryDirectory(prefix="res_extract_") as tmpdir:
        pdf_path = os.path.join(tmpdir, Path(minio_key).name)

        try:
            minio_client.fget_object(minio_bucket, minio_key, pdf_path)
        except Exception as e:
            error_msg = f"MinIO download failed: {e}"
            logger.error(error_msg)
            _report_extraction_status(resource_doc_id, "failed", error_message=error_msg)
            return {"status": "failed", "error": error_msg}

        images_dir = os.path.join(tmpdir, "images")
        images = _extract_pdf_images(pdf_path, images_dir)

        if not images:
            _report_extraction_status(
                resource_doc_id, "failed",
                error_message="No images extracted from PDF",
            )
            return {"status": "failed", "error": "No images extracted"}

        image_prefix = f"resources/{resource_id}/images/{resource_doc_id}/"
        uploaded = 0

        for img_path in images:
            img_name = Path(img_path).name
            obj_name = f"{image_prefix}{img_name}"
            try:
                minio_client.fput_object(
                    minio_bucket, obj_name, img_path,
                    content_type="image/png",
                )
                uploaded += 1
            except Exception as e:
                logger.warning("Failed to upload image %s: %s", img_name, e)

        _report_extraction_status(
            resource_doc_id,
            status="ready",
            page_count=len(images),
            image_count=uploaded,
        )

        logger.info(
            "Extraction complete: doc=%s pages=%d uploaded=%d",
            resource_doc_id, len(images), uploaded,
        )

        return {
            "status": "ready",
            "doc_id": resource_doc_id,
            "page_count": len(images),
            "image_count": uploaded,
        }
