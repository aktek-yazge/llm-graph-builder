"""
Pytest Configuration
====================

Shared fixtures and configuration for Agent Builder tests.
"""

import os
import pytest
import httpx


@pytest.fixture(scope="session")
def api_base_url():
    """Get API base URL from environment or use default."""
    return os.getenv("AGENT_BUILDER_URL", "http://localhost:8001")


@pytest.fixture(scope="session")
def tenant_id():
    """Test tenant ID."""
    return "test-tenant"


@pytest.fixture
def http_client(api_base_url):
    """Create HTTP client for API tests."""
    client = httpx.Client(
        base_url=f"{api_base_url}/api/v2/agent-builder",
        timeout=30.0,
    )
    yield client
    client.close()


@pytest.fixture
async def async_http_client(api_base_url):
    """Create async HTTP client for API tests."""
    async with httpx.AsyncClient(
        base_url=f"{api_base_url}/api/v2/agent-builder",
        timeout=30.0,
    ) as client:
        yield client
