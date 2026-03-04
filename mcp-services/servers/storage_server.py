"""
Storage MCP Server - MinIO S3 tools for document browsing.

Designed for large-scale document stores (10K+ files).
LLM can browse, search, inspect, and read documents via tools.
"""

import logging

from fastmcp.server import FastMCP
from pydantic import Field

from utils.minio_client import minio_client

logger = logging.getLogger("mcp_services.storage")

storage_mcp = FastMCP("Storage")


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------

@storage_mcp.resource("minio://buckets/info")
def bucket_info() -> dict:
    """List available MinIO buckets and their status."""
    buckets = ["documents", "prompts", "ocr-output"]
    result = {}
    for b in buckets:
        try:
            exists = minio_client.bucket_exists(b)
            if exists:
                count = minio_client.count_objects(b)
                result[b] = {"exists": True, "file_count": count}
            else:
                result[b] = {"exists": False}
        except Exception as e:
            result[b] = {"exists": False, "error": str(e)}
    return result


# ---------------------------------------------------------------------------
# Browse & Search Tools
# ---------------------------------------------------------------------------

@storage_mcp.tool(
    name="browse_files",
    description=(
        "Browse files in a MinIO bucket with pagination. "
        "Returns up to `limit` files starting from `offset`. "
        "Use prefix to filter by folder path. "
        "Response includes total count and has_more flag for navigation."
    ),
)
def browse_files(
    bucket: str = Field(..., description="Bucket name: documents, prompts, or ocr-output"),
    prefix: str = Field("", description="Filter by path prefix (e.g. 'invoices/', '2024/')"),
    offset: int = Field(0, description="Skip first N files (for pagination)"),
    limit: int = Field(50, description="Max files to return (default 50, max 200)"),
) -> dict:
    """Browse files with pagination - safe for buckets with thousands of files."""
    limit = min(limit, 200)
    try:
        return minio_client.list_objects_paginated(bucket, prefix=prefix, offset=offset, limit=limit)
    except Exception as e:
        return {"error": str(e), "items": [], "total": 0}


@storage_mcp.tool(
    name="search_files",
    description=(
        "Search for files by name pattern (case-insensitive substring match). "
        "Example: search_files(bucket='documents', query='invoice') "
        "finds 'invoices/2024/invoice_001.pdf', 'old_invoice.txt', etc."
    ),
)
def search_files(
    bucket: str = Field(..., description="Bucket name"),
    query: str = Field(..., description="Search term (matched against filenames, case-insensitive)"),
    prefix: str = Field("", description="Narrow search to a specific folder prefix"),
    limit: int = Field(50, description="Max results to return (default 50)"),
) -> list[dict]:
    """Search files by name pattern across the bucket."""
    limit = min(limit, 200)
    try:
        return minio_client.search_objects(bucket, query=query, prefix=prefix, limit=limit)
    except Exception as e:
        return [{"error": str(e)}]


@storage_mcp.tool(
    name="file_info",
    description=(
        "Get metadata about a specific file WITHOUT downloading it. "
        "Returns size, content type, last modified date, and custom metadata."
    ),
)
def file_info(
    bucket: str = Field(..., description="Bucket name"),
    key: str = Field(..., description="Full file path (object key)"),
) -> dict:
    """Inspect a file's metadata without downloading content."""
    try:
        stat = minio_client.stat_object(bucket, key)
        size_mb = round(stat["size"] / (1024 * 1024), 2) if stat.get("size") else 0
        stat["size_mb"] = size_mb
        return stat
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Read Tools
# ---------------------------------------------------------------------------

@storage_mcp.tool(
    name="read_file",
    description=(
        "Read a text file from MinIO. For large files, use max_chars to limit "
        "the response size (e.g. first 5000 characters). "
        "Returns the text content and total file size."
    ),
)
def read_file(
    bucket: str = Field(..., description="Bucket name"),
    key: str = Field(..., description="Full file path (object key)"),
    max_chars: int = Field(0, description="Max characters to return (0 = entire file, recommended: 5000-20000)"),
) -> dict:
    """Read a text file, optionally truncated to max_chars."""
    try:
        content = minio_client.get_object_text(bucket, key)
        total_len = len(content)
        truncated = False
        if max_chars > 0 and total_len > max_chars:
            content = content[:max_chars]
            truncated = True
        return {
            "content": content,
            "total_chars": total_len,
            "returned_chars": len(content),
            "truncated": truncated,
        }
    except Exception as e:
        return {"error": str(e)}


@storage_mcp.tool(
    name="read_file_range",
    description=(
        "Read a specific character range from a text file. "
        "Useful for reading the middle or end of large files. "
        "Example: read_file_range(bucket, key, start=5000, length=3000) "
        "reads characters 5000-8000."
    ),
)
def read_file_range(
    bucket: str = Field(..., description="Bucket name"),
    key: str = Field(..., description="Full file path (object key)"),
    start: int = Field(0, description="Start position (character offset)"),
    length: int = Field(5000, description="Number of characters to read"),
) -> dict:
    """Read a character range from a text file."""
    try:
        content = minio_client.get_object_text(bucket, key)
        total_len = len(content)
        chunk = content[start:start + length]
        return {
            "content": chunk,
            "total_chars": total_len,
            "start": start,
            "end": start + len(chunk),
            "has_more": (start + length) < total_len,
        }
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Write Tools
# ---------------------------------------------------------------------------

@storage_mcp.tool(name="upload_file", description="Upload text content to a MinIO bucket.")
def upload_file(
    bucket: str = Field(..., description="Target bucket: documents, prompts, or ocr-output"),
    key: str = Field(..., description="Object key / file path within bucket"),
    content: str = Field(..., description="File content as text"),
    content_type: str = Field("text/plain; charset=utf-8", description="MIME content type"),
) -> str:
    """Upload text content to a MinIO bucket."""
    try:
        path = minio_client.put_object(bucket, key, content.encode("utf-8"), content_type)
        return f"Uploaded to {path} ({len(content)} chars)"
    except Exception as e:
        return f"Upload error: {e}"
