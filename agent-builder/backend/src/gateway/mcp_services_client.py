"""
MCP Services Client (Gateway-Centric)
======================================

FastMCP-based client for calling MCP tools through the IBM Context Forge Gateway.

Architecture:
    agent-builder  →  fastmcp.Client  →  Context Forge Gateway (virtual server MCP endpoint)
                                                    ↓
                                          mcp-services (upstream: neo4j_*, storage_*, extract_*)

The gateway is the central MCP hub. All tool calls go through a virtual server's
MCP endpoint on the gateway. Each tenant gets its own virtual server.

Usage:
    from ..gateway.mcp_services_client import call_mcp_tool, resolve_mcp_endpoint

    # With explicit virtual server
    url = resolve_mcp_endpoint(server_id="abc-123")
    result = await call_mcp_tool("storage_browse_files", {"bucket": "documents"}, mcp_url=url)

    # Auto-resolve from tenant (finds/creates tenant virtual server)
    result = await call_mcp_tool("extract_infer_schema", {...}, tenant_id="default-tenant")
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MCP_GATEWAY_URL = os.getenv("MCP_GATEWAY_URL", "http://localhost:4444")
MCP_DIRECT_URL = os.getenv("MCP_SERVICES_URL", "http://localhost:8010/mcp/")

_tenant_server_cache: Dict[str, str] = {}


def resolve_mcp_endpoint(
    server_id: str = "",
    gateway_url: str = "",
) -> str:
    """Build the MCP endpoint URL for a virtual server on the gateway.

    Args:
        server_id: Virtual server ID on Context Forge Gateway.
        gateway_url: Override gateway base URL.

    Returns:
        Full MCP endpoint URL, e.g. http://localhost:4444/servers/abc-123/mcp
    """
    base = gateway_url or MCP_GATEWAY_URL
    return f"{base.rstrip('/')}/servers/{server_id}/mcp"


async def _resolve_tenant_server_id(tenant_id: str) -> Optional[str]:
    """Look up or create the virtual server for a tenant on the gateway.

    Uses MCPGatewayClient to find server named 'tenant-{tenant_id}'.
    Caches results in-process.
    """
    if tenant_id in _tenant_server_cache:
        return _tenant_server_cache[tenant_id]

    try:
        from .mcp_gateway_client import get_gateway_client
        gw = await get_gateway_client()
        servers = await gw.list_virtual_servers()

        target_name = f"tenant-{tenant_id}"
        for s in servers:
            srv = s if isinstance(s, dict) else {}
            if srv.get("name") == target_name:
                sid = srv.get("id", "")
                if sid:
                    _tenant_server_cache[tenant_id] = sid
                    logger.info("Resolved tenant %s → virtual server %s", tenant_id, sid)
                    return sid

        result = await gw.create_virtual_server(
            name=target_name,
            description=f"Virtual server for tenant {tenant_id}",
        )
        sid = result.get("id", result.get("server", {}).get("id", ""))
        if sid:
            _tenant_server_cache[tenant_id] = sid
            logger.info("Created virtual server for tenant %s: %s", tenant_id, sid)
            return sid

    except Exception as e:
        logger.warning("Could not resolve tenant server for %s: %s", tenant_id, e)

    return None


async def _get_mcp_url(
    mcp_url: str = "",
    server_id: str = "",
    tenant_id: str = "",
) -> str:
    """Resolve the MCP endpoint URL with fallback chain.

    Priority:
      1. Explicit mcp_url
      2. Explicit server_id → gateway endpoint
      3. tenant_id → find/create virtual server → gateway endpoint
      4. Direct mcp-services URL (development fallback)
    """
    if mcp_url:
        return mcp_url

    if server_id:
        return resolve_mcp_endpoint(server_id)

    if tenant_id:
        sid = await _resolve_tenant_server_id(tenant_id)
        if sid:
            return resolve_mcp_endpoint(sid)

    logger.debug("No gateway server resolved, falling back to direct MCP URL: %s", MCP_DIRECT_URL)
    return MCP_DIRECT_URL


async def call_mcp_tool(
    tool_name: str,
    arguments: Optional[Dict[str, Any]] = None,
    *,
    mcp_url: str = "",
    server_id: str = "",
    tenant_id: str = "",
) -> Any:
    """
    Call an MCP tool through the Context Forge Gateway.

    Connects to the gateway's virtual server MCP endpoint via fastmcp.Client,
    invokes the tool, and returns the parsed result.

    Args:
        tool_name: Namespaced tool name (e.g. "storage_browse_files", "extract_infer_schema")
        arguments: Tool arguments dict
        mcp_url: Direct MCP endpoint URL (overrides everything)
        server_id: Virtual server ID on the gateway
        tenant_id: Tenant ID (resolves to virtual server automatically)

    Returns:
        Parsed JSON dict, or {"result": raw_text}, or {"error": msg} on failure.
    """
    from fastmcp import Client

    url = await _get_mcp_url(mcp_url=mcp_url, server_id=server_id, tenant_id=tenant_id)

    try:
        async with Client(url) as client:
            result = await client.call_tool(tool_name, arguments or {})

            if not result.content:
                return {}

            texts = []
            for block in result.content:
                if hasattr(block, "text"):
                    texts.append(block.text)

            combined = "\n".join(texts) if texts else ""

            if result.isError:
                logger.warning("MCP tool %s error: %s", tool_name, combined[:200])
                return {"error": combined}

            try:
                return json.loads(combined)
            except json.JSONDecodeError:
                return {"result": combined}

    except Exception as e:
        logger.error("MCP tool call failed (%s → %s): %s", tool_name, url, e)
        return {"error": f"MCP connection failed: {e}"}


async def list_mcp_tools(
    *,
    mcp_url: str = "",
    server_id: str = "",
    tenant_id: str = "",
) -> List[Dict[str, str]]:
    """List all available tools on the gateway virtual server."""
    from fastmcp import Client

    url = await _get_mcp_url(mcp_url=mcp_url, server_id=server_id, tenant_id=tenant_id)

    try:
        async with Client(url) as client:
            tools = await client.list_tools()
            return [
                {"name": t.name, "description": t.description or ""}
                for t in tools
            ]
    except Exception as e:
        logger.error("Failed to list MCP tools: %s", e)
        return []


async def read_mcp_resource(
    uri: str,
    *,
    mcp_url: str = "",
    server_id: str = "",
    tenant_id: str = "",
) -> Optional[str]:
    """Read a resource from the MCP server by URI."""
    from fastmcp import Client

    url = await _get_mcp_url(mcp_url=mcp_url, server_id=server_id, tenant_id=tenant_id)

    try:
        async with Client(url) as client:
            result = await client.read_resource(uri)
            if result and hasattr(result, "content"):
                for block in result.content:
                    if hasattr(block, "text"):
                        return block.text
            return None
    except Exception as e:
        logger.error("Failed to read MCP resource %s: %s", uri, e)
        return None


async def get_mcp_prompt(
    prompt_name: str,
    arguments: Optional[Dict[str, str]] = None,
    *,
    mcp_url: str = "",
    server_id: str = "",
    tenant_id: str = "",
) -> Optional[str]:
    """Get a prompt from the MCP server."""
    from fastmcp import Client

    url = await _get_mcp_url(mcp_url=mcp_url, server_id=server_id, tenant_id=tenant_id)

    try:
        async with Client(url) as client:
            result = await client.get_prompt(prompt_name, arguments or {})
            if result and result.messages:
                texts = []
                for msg in result.messages:
                    if hasattr(msg.content, "text"):
                        texts.append(msg.content.text)
                    elif isinstance(msg.content, str):
                        texts.append(msg.content)
                return "\n".join(texts) if texts else None
            return None
    except Exception as e:
        logger.error("Failed to get MCP prompt %s: %s", prompt_name, e)
        return None


def invalidate_tenant_cache(tenant_id: str = "") -> None:
    """Clear cached virtual server IDs. Call when servers are re-created."""
    if tenant_id:
        _tenant_server_cache.pop(tenant_id, None)
    else:
        _tenant_server_cache.clear()
