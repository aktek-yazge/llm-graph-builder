# -*- coding: utf-8 -*-
"""
MCP Server Configuration for Celery Worker

Bu modül, Celery worker'daki AgenticOCR için MCP server konfigürasyonunu sağlar.

Transport Types:
    - stdio: Stateless tool'lar için (ImageSorcery, Time)
    - streamable_http: Stateful tool'lar için (Neo4j - connection pooling)
"""

import os
from typing import Dict, Any


def get_mcp_server_config() -> Dict[str, Any]:
    """
    Celery Worker için MCP server konfigürasyonu.

    Returns:
        MCP server config dictionary (MultiServerMCPClient için)

    Environment Variables:
        MCP_HTTP_HOST: Neo4j MCP HTTP host (default: 127.0.0.1)
        MCP_HTTP_PORT: Neo4j MCP HTTP port (default: 8002)
    """
    mcp_http_host = os.environ.get("MCP_HTTP_HOST", "127.0.0.1")
    mcp_http_port = int(os.environ.get("MCP_HTTP_PORT", "8002"))

    config = {
        # Neo4j - HTTP transport (connection pooling için stateful)
        "neo4j-database": {
            "url": f"http://{mcp_http_host}:{mcp_http_port}/mcp/",
            "transport": "streamable_http",
        },
        # ImageSorcery - stdio transport (stateless, on-demand)
        "imagesorcery": {
            "command": "uvx",
            "args": ["imagesorcery-mcp"],
            "transport": "stdio",
        },
    }

    return config


def get_mcp_server_config_stdio_only() -> Dict[str, Any]:
    """
    Sadece stdio transport kullanan MCP server konfigürasyonu.
    
    Neo4j'e ihtiyaç olmayan senaryolar için kullanılır.
    
    Returns:
        MCP server config dictionary
    """
    config = {
        # ImageSorcery - stdio transport
        "imagesorcery": {
            "command": "uvx",
            "args": ["imagesorcery-mcp"],
            "transport": "stdio",
        },
    }

    return config
