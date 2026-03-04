"""
LLM Graph Builder - Unified MCP Server

Orchestrates Neo4j, Embedding, and Storage sub-servers via FastMCP composition.
Registers graph and OCR prompts as MCP prompts.

Usage:
    python main.py                     # HTTP mode (default)
    python main.py --transport stdio   # STDIO mode
"""

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv
from fastmcp.server import FastMCP

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [MCP] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("mcp_services")

# ---------------------------------------------------------------------------
# Sampling fallback handler (shared by sub-servers that use sampling)
# ---------------------------------------------------------------------------

_sampling_handler = None
_sampling_model = os.getenv("SAMPLING_FALLBACK_MODEL", "gpt-4o-mini")
_sampling_behavior = os.getenv("SAMPLING_BEHAVIOR", "fallback")

try:
    from fastmcp.client.sampling.handlers.openai import OpenAISamplingHandler

    if os.getenv("OPENAI_API_KEY"):
        _sampling_handler = OpenAISamplingHandler(default_model=_sampling_model)
        logger.info("Sampling fallback enabled: OpenAI (%s)", _sampling_model)
    else:
        logger.warning("OPENAI_API_KEY not set - sampling fallback disabled, client must support sampling")
except ImportError:
    logger.warning("OpenAI sampling handler not available - install fastmcp[openai]")


# ---------------------------------------------------------------------------
# Create the root MCP server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "LLM Graph Builder MCP",
    instructions=(
        "Unified MCP server for LLM Graph Builder. "
        "Provides Neo4j graph queries (multi-tenant), MinIO file storage, "
        "LLM-powered document extraction (via MCP Sampling), "
        "and domain-specific prompts for insurance and trade registry analysis."
    ),
)

# ---------------------------------------------------------------------------
# Mount sub-servers with namespaces
# ---------------------------------------------------------------------------

from servers.neo4j_server import neo4j_mcp
from servers.storage_server import storage_mcp
from servers.extraction_server import extraction_mcp

if _sampling_handler:
    extraction_mcp.sampling_handler = _sampling_handler
    extraction_mcp.sampling_handler_behavior = _sampling_behavior
    logger.info("Configured sampling handler on extraction sub-server (behavior=%s)", _sampling_behavior)

mcp.mount(neo4j_mcp, namespace="neo4j")
mcp.mount(storage_mcp, namespace="storage")
mcp.mount(extraction_mcp, namespace="extract")

logger.info("Mounted sub-servers: neo4j, storage, extract")

# ---------------------------------------------------------------------------
# Register prompts on the root server
# ---------------------------------------------------------------------------

from prompts.graph_prompts import register_graph_prompts
from prompts.ocr_prompts import register_ocr_prompts

register_graph_prompts(mcp)
register_ocr_prompts(mcp)

logger.info("Registered graph and OCR prompts")


# ---------------------------------------------------------------------------
# Startup: sync prompt templates to MinIO (best-effort)
# ---------------------------------------------------------------------------

async def _sync_templates_to_minio():
    """Upload prompt templates to MinIO prompts bucket on startup."""
    try:
        from utils.minio_client import minio_client
        from pathlib import Path

        templates_dir = Path(__file__).parent / "prompts" / "templates"
        if not templates_dir.exists():
            logger.warning("Templates directory not found, skipping MinIO sync")
            return

        minio_client.ensure_bucket("prompts")

        count = 0
        for md_file in templates_dir.rglob("*.md"):
            relative = md_file.relative_to(templates_dir)
            key = str(relative)
            content = md_file.read_text(encoding="utf-8")
            minio_client.put_object_text("prompts", key, content)
            count += 1

        logger.info("Synced %d prompt templates to MinIO prompts bucket", count)
    except Exception as e:
        logger.warning("MinIO template sync failed (non-fatal): %s", e)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="LLM Graph Builder Unified MCP Server")
    parser.add_argument("--transport", choices=["stdio", "http"], default=os.getenv("MCP_TRANSPORT", "http"))
    parser.add_argument("--host", default=os.getenv("MCP_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("MCP_PORT", "8010")))
    parser.add_argument("--path", default=os.getenv("MCP_PATH", "/mcp/"))
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("LLM Graph Builder - Unified MCP Server")
    logger.info("=" * 60)
    logger.info("Transport: %s", args.transport)

    if args.transport == "http":
        logger.info("Endpoint: http://%s:%d%s", args.host, args.port, args.path)

        asyncio.get_event_loop().run_until_complete(_sync_templates_to_minio())

        mcp.run(
            transport="http",
            host=args.host,
            port=args.port,
            path=args.path,
        )
    else:
        logger.info("Running in STDIO mode")
        mcp.run()
