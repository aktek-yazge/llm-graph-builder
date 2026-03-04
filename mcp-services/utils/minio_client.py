import io
import logging
import os
from typing import Optional

from minio import Minio
from minio.error import S3Error

logger = logging.getLogger("mcp_services.minio")

DEFAULT_PAGE_SIZE = 50


class MinIOClient:
    """MinIO S3 client wrapper with pagination and partial read support."""

    def __init__(self):
        self._client: Optional[Minio] = None

    def _get_client(self) -> Minio:
        if self._client is None:
            endpoint = os.getenv("MINIO_ENDPOINT", "localhost:9000")
            access_key = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
            secret_key = os.getenv("MINIO_SECRET_KEY", "minioadmin")
            secure = os.getenv("MINIO_SECURE", "false").lower() == "true"
            self._client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
            logger.info("MinIO client connected to %s", endpoint)
        return self._client

    def get_object_text(self, bucket: str, key: str) -> str:
        client = self._get_client()
        response = client.get_object(bucket, key)
        try:
            return response.read().decode("utf-8")
        finally:
            response.close()
            response.release_conn()

    def get_object_partial(self, bucket: str, key: str, offset: int = 0, length: int = 0) -> bytes:
        """Read a byte range from an object. length=0 means read to end."""
        client = self._get_client()
        response = client.get_object(bucket, key, offset=offset, length=length or None)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def get_object_bytes(self, bucket: str, key: str) -> bytes:
        client = self._get_client()
        response = client.get_object(bucket, key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def stat_object(self, bucket: str, key: str) -> dict:
        """Get object metadata without downloading content."""
        client = self._get_client()
        stat = client.stat_object(bucket, key)
        return {
            "key": stat.object_name,
            "size": stat.size,
            "content_type": stat.content_type,
            "last_modified": stat.last_modified.isoformat() if stat.last_modified else None,
            "etag": stat.etag,
            "metadata": dict(stat.metadata) if stat.metadata else {},
        }

    def put_object(self, bucket: str, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        client = self._get_client()
        stream = io.BytesIO(data)
        client.put_object(bucket, key, stream, length=len(data), content_type=content_type)
        return f"{bucket}/{key}"

    def put_object_text(self, bucket: str, key: str, text: str) -> str:
        return self.put_object(bucket, key, text.encode("utf-8"), content_type="text/plain; charset=utf-8")

    def list_objects_paginated(
        self, bucket: str, prefix: str = "", offset: int = 0, limit: int = DEFAULT_PAGE_SIZE, recursive: bool = True
    ) -> dict:
        """List objects with pagination. Returns items + total count + has_more flag."""
        client = self._get_client()
        all_objects = client.list_objects(bucket, prefix=prefix, recursive=recursive)

        items = []
        total = 0
        for obj in all_objects:
            if total >= offset and len(items) < limit:
                items.append({
                    "key": obj.object_name,
                    "size": obj.size,
                    "last_modified": obj.last_modified.isoformat() if obj.last_modified else None,
                })
            total += 1

        return {
            "items": items,
            "total": total,
            "offset": offset,
            "limit": limit,
            "has_more": (offset + limit) < total,
        }

    def search_objects(
        self, bucket: str, query: str, prefix: str = "", limit: int = DEFAULT_PAGE_SIZE
    ) -> list[dict]:
        """Search objects by filename pattern (case-insensitive substring match)."""
        client = self._get_client()
        query_lower = query.lower()
        results = []
        for obj in client.list_objects(bucket, prefix=prefix, recursive=True):
            if query_lower in obj.object_name.lower():
                results.append({
                    "key": obj.object_name,
                    "size": obj.size,
                    "last_modified": obj.last_modified.isoformat() if obj.last_modified else None,
                })
                if len(results) >= limit:
                    break
        return results

    def list_objects(self, bucket: str, prefix: str = "", recursive: bool = True) -> list[dict]:
        client = self._get_client()
        result = []
        for obj in client.list_objects(bucket, prefix=prefix, recursive=recursive):
            result.append({
                "key": obj.object_name,
                "size": obj.size,
                "last_modified": obj.last_modified.isoformat() if obj.last_modified else None,
                "etag": obj.etag,
            })
        return result

    def bucket_exists(self, bucket: str) -> bool:
        return self._get_client().bucket_exists(bucket)

    def ensure_bucket(self, bucket: str):
        client = self._get_client()
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
            logger.info("Created bucket: %s", bucket)

    def count_objects(self, bucket: str, prefix: str = "") -> int:
        """Count total objects in a bucket (or prefix)."""
        client = self._get_client()
        return sum(1 for _ in client.list_objects(bucket, prefix=prefix, recursive=True))


minio_client = MinIOClient()
