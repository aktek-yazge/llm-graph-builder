"""
MinIO Client
============

Thin wrapper around the minio SDK for resource file operations.
Reuses env vars: MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY.
"""

import io
import logging
import os
from typing import Optional

from minio import Minio
from minio.error import S3Error

logger = logging.getLogger(__name__)

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"
MINIO_RESOURCES_BUCKET = os.getenv("MINIO_RESOURCES_BUCKET", "resources")

_client: Optional[Minio] = None


def get_minio_client() -> Minio:
    global _client
    if _client is None:
        _client = Minio(
            MINIO_ENDPOINT,
            access_key=MINIO_ACCESS_KEY,
            secret_key=MINIO_SECRET_KEY,
            secure=MINIO_SECURE,
        )
    return _client


def ensure_bucket(bucket: str = MINIO_RESOURCES_BUCKET) -> None:
    client = get_minio_client()
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
        logger.info("Created MinIO bucket: %s", bucket)


def upload_file(
    data: bytes,
    object_name: str,
    bucket: str = MINIO_RESOURCES_BUCKET,
    content_type: str = "application/octet-stream",
) -> str:
    """Upload bytes to MinIO. Returns the object name."""
    client = get_minio_client()
    ensure_bucket(bucket)
    client.put_object(
        bucket,
        object_name,
        io.BytesIO(data),
        length=len(data),
        content_type=content_type,
    )
    return object_name


def download_file(
    object_name: str,
    bucket: str = MINIO_RESOURCES_BUCKET,
) -> bytes:
    """Download object from MinIO and return bytes."""
    client = get_minio_client()
    response = client.get_object(bucket, object_name)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def get_presigned_url(
    object_name: str,
    bucket: str = MINIO_RESOURCES_BUCKET,
    expires_hours: int = 1,
) -> str:
    from datetime import timedelta
    client = get_minio_client()
    return client.presigned_get_object(
        bucket, object_name, expires=timedelta(hours=expires_hours)
    )


def list_prefix(
    prefix: str,
    bucket: str = MINIO_RESOURCES_BUCKET,
) -> list[dict]:
    """List all objects under a prefix. Returns list of {name, size, last_modified}."""
    client = get_minio_client()
    objects = client.list_objects(bucket, prefix=prefix, recursive=True)
    result = []
    for obj in objects:
        result.append({
            "name": obj.object_name,
            "size": obj.size or 0,
            "last_modified": obj.last_modified.isoformat() if obj.last_modified else None,
        })
    return result


def delete_prefix(
    prefix: str,
    bucket: str = MINIO_RESOURCES_BUCKET,
) -> int:
    """Delete all objects under a prefix. Returns count deleted."""
    client = get_minio_client()
    objects = client.list_objects(bucket, prefix=prefix, recursive=True)
    count = 0
    for obj in objects:
        client.remove_object(bucket, obj.object_name)
        count += 1
    return count
