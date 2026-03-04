"""
Resource Router
===============

REST API endpoints for Resource management:
- CRUD for resources
- File upload to MinIO
- Attach resource to workspace
- Document status queries
"""

import logging
import os
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from .event_store.postgres_client import PostgresClient, get_postgres_client
from .minio_client import upload_file as minio_upload, delete_prefix, list_prefix, get_presigned_url, MINIO_RESOURCES_BUCKET
from .models import ResourceCreate, ResourceDetail, ResourceSummary, ResourceStatusBreakdown
from .resource_repository import ResourceRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/resources", tags=["Resources"])

CONTENT_TYPE_MAP = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".txt": "text/plain",
    ".md": "text/markdown",
}


async def _get_repo() -> ResourceRepository:
    pg = await get_postgres_client()
    return ResourceRepository(pg)


# ---------------------------------------------------------------------------
# Resource CRUD
# ---------------------------------------------------------------------------

@router.post("", summary="Create a new resource")
async def create_resource(body: ResourceCreate, repo: ResourceRepository = Depends(_get_repo)):
    merged_metadata = dict(body.metadata) if body.metadata else {}
    if body.url:
        merged_metadata["url"] = body.url
    if body.notebook_id:
        merged_metadata["notebook_id"] = body.notebook_id

    result = await repo.create(
        name=body.name,
        resource_type=body.type.value,
        tenant_id=body.tenant_id,
        description=body.description,
        metadata=merged_metadata,
    )
    return {
        "id": str(result["id"]),
        "name": result["name"],
        "type": result["type"],
        "status": result["status"],
        "metadata": result.get("metadata", {}),
        "minio_prefix": result.get("minio_prefix", ""),
        "message": "Resource created",
    }


@router.get("", summary="List resources")
async def list_resources(
    tenant_id: str = Query(default="default"),
    type: Optional[str] = Query(default=None, description="Filter by resource type"),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    repo: ResourceRepository = Depends(_get_repo),
):
    resources = await repo.list_by_tenant(
        tenant_id, limit=limit, offset=offset, resource_type=type,
    )
    items = []
    for r in resources:
        raw_meta = r.get("metadata") or {}
        metadata = raw_meta if isinstance(raw_meta, dict) else {}
        items.append({
            "id": str(r["id"]),
            "name": r["name"],
            "description": r.get("description", ""),
            "type": r["type"],
            "status": r["status"],
            "total_documents": r["total_documents"],
            "extracted_documents": r["extracted_docs"],
            "workspace_id": r.get("workspace_id"),
            "metadata": metadata,
            "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
        })
    return {"resources": items, "count": len(items)}


@router.get("/{resource_id}", summary="Get resource detail")
async def get_resource(
    resource_id: str,
    include_docs: bool = Query(default=True),
    doc_limit: int = Query(default=100, le=500),
    repo: ResourceRepository = Depends(_get_repo),
):
    resource = await repo.get(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    raw_meta = resource.get("metadata") or {}
    metadata = raw_meta if isinstance(raw_meta, dict) else {}

    result = {
        "id": str(resource["id"]),
        "name": resource["name"],
        "type": resource["type"],
        "description": resource.get("description", ""),
        "status": resource["status"],
        "workspace_id": resource.get("workspace_id"),
        "tenant_id": resource["tenant_id"],
        "minio_bucket": resource.get("minio_bucket", "resources"),
        "minio_prefix": resource.get("minio_prefix"),
        "total_documents": resource["total_documents"],
        "extracted_documents": resource["extracted_docs"],
        "metadata": metadata,
        "created_at": resource["created_at"].isoformat() if resource.get("created_at") else None,
        "updated_at": resource["updated_at"].isoformat() if resource.get("updated_at") else None,
    }

    if include_docs:
        docs = await repo.get_documents(resource_id, limit=doc_limit)
        result["documents"] = [
            {
                "id": str(d["id"]),
                "file_name": d["file_name"],
                "file_type": d.get("file_type"),
                "file_size": d.get("file_size", 0),
                "page_count": d.get("page_count", 0),
                "extraction_status": d["extraction_status"],
                "processing_status": d["processing_status"],
                "confidence_score": d.get("confidence_score", 0.0),
                "error_message": d.get("error_message"),
                "created_at": d["created_at"].isoformat() if d.get("created_at") else None,
            }
            for d in docs
        ]

    breakdown = await repo.get_status_breakdown(resource_id)
    result["status_breakdown"] = breakdown

    return result


@router.get("/{resource_id}/status", summary="Get status breakdown only")
async def get_resource_status(
    resource_id: str,
    repo: ResourceRepository = Depends(_get_repo),
):
    resource = await repo.get(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    breakdown = await repo.get_status_breakdown(resource_id)
    return {
        "resource_id": resource_id,
        "status": resource["status"],
        "total_documents": resource["total_documents"],
        "extracted_documents": resource["extracted_docs"],
        "breakdown": breakdown,
    }


@router.get("/{resource_id}/files", summary="List all MinIO files for resource as a tree")
async def list_resource_files(
    resource_id: str,
    repo: ResourceRepository = Depends(_get_repo),
):
    resource = await repo.get(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    prefix = resource.get("minio_prefix", f"resources/{resource_id}/")
    bucket = resource.get("minio_bucket", MINIO_RESOURCES_BUCKET)

    try:
        raw_files = list_prefix(prefix, bucket)
    except Exception as e:
        logger.warning("Failed to list MinIO files for resource %s: %s", resource_id, e)
        raw_files = []

    docs = await repo.get_documents(resource_id, limit=500)
    doc_map = {str(d["id"]): d for d in docs}

    tree: list[dict] = []
    image_files: dict[str, list[dict]] = {}

    for f in raw_files:
        rel = f["name"].removeprefix(prefix)
        parts = rel.split("/")

        if parts[0] == "images" and len(parts) >= 3:
            doc_id = parts[1]
            image_files.setdefault(doc_id, []).append({
                "name": parts[-1],
                "path": rel,
                "size": f["size"],
            })
        else:
            tree.append({
                "name": parts[-1],
                "path": rel,
                "size": f["size"],
                "type": "source",
            })

    doc_nodes = []
    for d in docs:
        did = str(d["id"])
        images = sorted(image_files.get(did, []), key=lambda x: x["name"])
        doc_nodes.append({
            "id": did,
            "file_name": d["file_name"],
            "file_type": d.get("file_type", ""),
            "file_size": d.get("file_size", 0),
            "page_count": d.get("page_count", 0),
            "image_count": d.get("image_count", 0),
            "extraction_status": d["extraction_status"],
            "processing_status": d["processing_status"],
            "confidence_score": d.get("confidence_score", 0.0),
            "error_message": d.get("error_message"),
            "extracted_images": images,
        })

    return {
        "resource_id": resource_id,
        "prefix": prefix,
        "documents": doc_nodes,
    }


@router.get("/{resource_id}/preview", summary="Get presigned URL for a file in the resource")
async def preview_file(
    resource_id: str,
    path: str = Query(..., description="Relative path within the resource prefix"),
    repo: ResourceRepository = Depends(_get_repo),
):
    resource = await repo.get(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    prefix = resource.get("minio_prefix", f"resources/{resource_id}/")
    bucket = resource.get("minio_bucket", MINIO_RESOURCES_BUCKET)
    object_name = f"{prefix}{path}"

    try:
        url = get_presigned_url(object_name, bucket=bucket, expires_hours=1)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"File not found: {e}")

    return {"url": url, "path": path, "object_name": object_name}


@router.delete("/{resource_id}", summary="Delete resource and all documents")
async def delete_resource(
    resource_id: str,
    repo: ResourceRepository = Depends(_get_repo),
):
    resource = await repo.get(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    prefix = resource.get("minio_prefix", f"resources/{resource_id}/")
    bucket = resource.get("minio_bucket", MINIO_RESOURCES_BUCKET)
    try:
        deleted_files = delete_prefix(prefix, bucket)
        logger.info("Deleted %d files from MinIO for resource %s", deleted_files, resource_id)
    except Exception as e:
        logger.warning("Failed to delete MinIO files for resource %s: %s", resource_id, e)

    await repo.delete(resource_id)
    return {"message": "Resource deleted", "resource_id": resource_id}


# ---------------------------------------------------------------------------
# File Upload
# ---------------------------------------------------------------------------

@router.post("/{resource_id}/upload", summary="Upload files to resource")
async def upload_files(
    resource_id: str,
    files: List[UploadFile] = File(...),
    repo: ResourceRepository = Depends(_get_repo),
):
    resource = await repo.get(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    await repo.update_status(resource_id, "uploading")

    minio_prefix = resource.get("minio_prefix", f"resources/{resource_id}/")
    bucket = resource.get("minio_bucket", MINIO_RESOURCES_BUCKET)

    uploaded = []
    celery_tasks = []

    for f in files:
        data = await f.read()
        file_name = f.filename or f"file_{len(uploaded)}"
        ext = Path(file_name).suffix.lower()
        file_type = ext.lstrip(".")
        content_type = CONTENT_TYPE_MAP.get(ext, "application/octet-stream")

        minio_key = f"{minio_prefix}{file_name}"

        try:
            minio_upload(data, minio_key, bucket=bucket, content_type=content_type)
        except Exception as e:
            logger.error("MinIO upload failed for %s: %s", file_name, e)
            continue

        doc_id = await repo.add_document(
            resource_id=resource_id,
            file_name=file_name,
            minio_key=minio_key,
            file_size=len(data),
            file_type=file_type,
        )

        uploaded.append({"id": doc_id, "file_name": file_name, "size": len(data)})

        if file_type == "pdf":
            try:
                from celery import Celery
                celery_broker = os.getenv("CELERY_BROKER_URL", "amqp://rabbitmq:RabbitMQ!654*@localhost:5672//")
                celery_app = Celery("resource_dispatch", broker=celery_broker)
                task = celery_app.send_task(
                    "resource.extract_images",
                    kwargs={
                        "resource_doc_id": doc_id,
                        "resource_id": resource_id,
                        "minio_bucket": bucket,
                        "minio_key": minio_key,
                    },
                    queue="resource",
                )
                celery_tasks.append(task.id)
            except Exception as e:
                logger.warning("Failed to dispatch extraction task for %s: %s", file_name, e)

    if uploaded:
        has_pdfs = any(u["file_name"].lower().endswith(".pdf") for u in uploaded)
        new_status = "extracting" if has_pdfs else "ready"
        await repo.update_status(resource_id, new_status)

        if not has_pdfs:
            for u in uploaded:
                await repo.update_document_extraction(u["id"], "ready")

    return {
        "resource_id": resource_id,
        "files_uploaded": len(uploaded),
        "uploaded": uploaded,
        "extraction_tasks": celery_tasks,
        "message": f"{len(uploaded)} file(s) uploaded",
    }


# ---------------------------------------------------------------------------
# Attach to Workspace
# ---------------------------------------------------------------------------

@router.post("/{resource_id}/attach/{workspace_id}", summary="Attach resource to workspace")
async def attach_to_workspace(
    resource_id: str,
    workspace_id: str,
    repo: ResourceRepository = Depends(_get_repo),
):
    resource = await repo.get(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    await repo.attach_to_workspace(resource_id, workspace_id)

    return {
        "resource_id": resource_id,
        "workspace_id": workspace_id,
        "message": "Resource attached to workspace",
    }


# ---------------------------------------------------------------------------
# Document-level endpoints (for Celery callbacks)
# ---------------------------------------------------------------------------

@router.post("/documents/{doc_id}/extraction-complete", summary="Callback: extraction done")
async def extraction_complete(
    doc_id: str,
    status: str = Query(default="ready"),
    page_count: int = Query(default=0),
    image_count: int = Query(default=0),
    error_message: str = Query(default=""),
    repo: ResourceRepository = Depends(_get_repo),
):
    await repo.update_document_extraction(
        doc_id, status, page_count=page_count,
        image_count=image_count, error_message=error_message,
    )
    return {"doc_id": doc_id, "extraction_status": status}


@router.post("/documents/{doc_id}/processing-update", summary="Update processing status")
async def processing_update(
    doc_id: str,
    status: str = Query(...),
    confidence_score: float = Query(default=0.0),
    error_message: str = Query(default=""),
    repo: ResourceRepository = Depends(_get_repo),
):
    await repo.update_document_processing(
        doc_id, status, confidence_score=confidence_score,
        error_message=error_message,
    )
    return {"doc_id": doc_id, "processing_status": status}
