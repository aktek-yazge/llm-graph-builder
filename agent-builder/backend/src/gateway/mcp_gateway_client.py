"""
MCP Gateway Client
==================

IBM ContextForge MCP Gateway ile programatik etkilesim.

Gateway API'leri (OpenAPI /openapi.json ile dogrulanmis):
- /gateways   : Upstream MCP server kaydi (auto-discovery)
- /servers    : Virtual server CRUD (tool/prompt/resource bundling)
- /tools      : Tool CRUD (REST, MCP, gRPC)
- /prompts    : Prompt CRUD (Jinja2 templates, versioning)
- /resources  : Resource CRUD (URI-based, caching)
- /tokens     : JWT token uretimi
- /rpc        : JSON-RPC 2.0 tool invocation

Kullanim:
    client = MCPGatewayClient("http://localhost:4444")
    await client.authenticate("admin@example.com", "password")

    vs = await client.create_virtual_server("tenant-A", tool_ids=["t1", "t2"])
    tools = await client.list_tools()
    prompts = await client.list_prompts()
    resources = await client.list_resources()
"""

import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

GATEWAY_URL = os.getenv("MCP_GATEWAY_URL", "http://localhost:4444")
GATEWAY_ADMIN_EMAIL = os.getenv("MCP_GATEWAY_ADMIN_EMAIL", "admin@example.com")
GATEWAY_ADMIN_PASSWORD = os.getenv("MCP_GATEWAY_ADMIN_PASSWORD", "changeme")

_gateway_instance: Optional["MCPGatewayClient"] = None


class MCPGatewayError(Exception):
    """Gateway API hatasi"""

    def __init__(self, status_code: int, message: str, endpoint: str = ""):
        self.status_code = status_code
        self.endpoint = endpoint
        super().__init__(f"[{status_code}] {endpoint}: {message}")


class MCPGatewayClient:
    """
    IBM ContextForge MCP Gateway REST client.

    JWT auth, virtual server yonetimi, tool kaydi ve
    JSON-RPC tool invocation destekler.
    """

    def __init__(
        self,
        base_url: str = GATEWAY_URL,
        timeout: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self._token: Optional[str] = None
        self._token_expires: Optional[datetime] = None
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            follow_redirects=True,
        )

    # =========================================================================
    # AUTH
    # =========================================================================

    async def authenticate(
        self,
        email: str = GATEWAY_ADMIN_EMAIL,
        password: str = GATEWAY_ADMIN_PASSWORD,
    ) -> str:
        """
        JWT token olustur.

        ContextForge, PyJWT ile imzalanmis token kabul eder.
        Token, environment'daki JWT_SECRET_KEY ile olusturulur.
        """
        import jwt as pyjwt
        from datetime import timezone
        import uuid as _uuid

        secret = os.getenv("MCP_GATEWAY_JWT_SECRET", os.getenv("JWT_SECRET_KEY", "dev-secret-key-32-chars-minimum!!"))

        now = datetime.now(timezone.utc)
        payload = {
            "sub": email,
            "email": email,
            "iat": now,
            "iss": "mcpgateway",
            "aud": "mcpgateway-api",
            "jti": str(_uuid.uuid4()),
            "exp": now + timedelta(days=7),
        }
        self._token = pyjwt.encode(payload, secret, algorithm="HS256")
        self._token_expires = now.replace(tzinfo=None) + timedelta(days=7)
        logger.info("MCP Gateway JWT token created for %s", email)
        return self._token

    async def ensure_auth(self) -> None:
        """Token yoksa veya expired ise yeniden olustur."""
        if self._token and self._token_expires and datetime.utcnow() < self._token_expires:
            return
        await self.authenticate()

    @property
    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    # =========================================================================
    # LOW-LEVEL HTTP
    # =========================================================================

    async def _request(
        self,
        method: str,
        path: str,
        json: Any = None,
        params: Dict[str, Any] | None = None,
    ) -> Any:
        """Auth-aware HTTP request. Returns parsed JSON."""
        await self.ensure_auth()
        resp = await self._client.request(
            method, path, json=json, params=params, headers=self._headers,
        )
        if resp.status_code >= 400:
            raise MCPGatewayError(resp.status_code, resp.text, path)
        if resp.status_code == 204:
            return None
        return resp.json()

    async def _get(self, path: str, **params) -> Any:
        return await self._request("GET", path, params=params or None)

    async def _post(self, path: str, json: Any = None) -> Any:
        return await self._request("POST", path, json=json)

    async def _put(self, path: str, json: Any = None) -> Any:
        return await self._request("PUT", path, json=json)

    async def _delete(self, path: str) -> Any:
        return await self._request("DELETE", path)

    # =========================================================================
    # HEALTH
    # =========================================================================

    async def health_check(self) -> Dict[str, Any]:
        """Gateway health (no auth)."""
        resp = await self._client.get("/health")
        return resp.json()

    # =========================================================================
    # GATEWAYS (Upstream MCP Server Registration)
    # =========================================================================

    async def register_gateway(
        self,
        name: str,
        url: str,
        transport: str = "STREAMABLEHTTP",
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Upstream MCP server'i gateway'e kaydet.
        Auto-discovery ile tool'lar otomatik import edilir.
        """
        payload = {"name": name, "url": url, "transport": transport, **kwargs}
        result = await self._post("/gateways", json=payload)
        logger.info("Registered gateway: %s -> %s", name, url)
        return result

    async def list_gateways(self) -> List[Dict[str, Any]]:
        return await self._get("/gateways")

    async def get_gateway(self, gateway_id: str) -> Dict[str, Any]:
        return await self._get(f"/gateways/{gateway_id}")

    async def delete_gateway(self, gateway_id: str) -> None:
        await self._delete(f"/gateways/{gateway_id}")

    # =========================================================================
    # TOOLS
    # =========================================================================

    async def list_tools(self) -> List[Dict[str, Any]]:
        """Tum kayitli tool'lari listele."""
        return await self._get("/tools")

    async def get_tool(self, tool_id: str) -> Dict[str, Any]:
        return await self._get(f"/tools/{tool_id}")

    async def register_tool(
        self,
        name: str,
        url: str,
        request_type: str = "POST",
        integration_type: str = "REST",
        description: str = "",
        headers: Dict[str, str] | None = None,
        input_schema: Dict[str, Any] | None = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Yeni tool kaydet (REST, MCP, gRPC).
        """
        payload = {
            "name": name,
            "url": url,
            "requestType": request_type,
            "integrationType": integration_type,
            "description": description,
            **kwargs,
        }
        if headers:
            payload["headers"] = headers
        if input_schema:
            payload["inputSchema"] = input_schema

        result = await self._post("/tools", json=payload)
        logger.info("Registered tool: %s", name)
        return result

    async def update_tool(self, tool_id: str, **kwargs) -> Dict[str, Any]:
        """Tool bilgilerini guncelle."""
        return await self._put(f"/tools/{tool_id}", json=kwargs)

    async def set_tool_state(self, tool_id: str, active: bool) -> Dict[str, Any]:
        return await self._post(f"/tools/{tool_id}/state", json={"activate": active})

    async def toggle_tool(self, tool_id: str) -> Dict[str, Any]:
        """Tool durumunu tersine cevir (aktif/pasif)."""
        return await self._post(f"/tools/{tool_id}/toggle")

    async def delete_tool(self, tool_id: str) -> None:
        await self._delete(f"/tools/{tool_id}")

    # =========================================================================
    # PROMPTS
    # =========================================================================

    async def list_prompts(self, include_inactive: bool = False) -> List[Dict[str, Any]]:
        """Gateway'deki tum prompt'lari listele."""
        return await self._get("/prompts", include_inactive=str(include_inactive).lower())

    async def get_prompt(self, prompt_id: str) -> Dict[str, Any]:
        """Prompt detayini getir (ID veya name ile)."""
        return await self._get(f"/prompts/{prompt_id}")

    async def create_prompt(
        self,
        name: str,
        description: str = "",
        messages: List[Dict[str, Any]] | None = None,
        arguments: List[Dict[str, Any]] | None = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Yeni prompt olustur.

        Args:
            name: Prompt adi (unique)
            description: Aciklama
            messages: MCP prompt messages listesi
                [{"role": "user", "content": {"type": "text", "text": "..."}}]
            arguments: Prompt argumanlari
                [{"name": "arg1", "description": "...", "required": true}]
        """
        payload: Dict[str, Any] = {"name": name, "description": description, **kwargs}
        if messages:
            payload["messages"] = messages
        if arguments:
            payload["arguments"] = arguments
        result = await self._post("/prompts", json=payload)
        logger.info("Created prompt: %s", name)
        return result

    async def update_prompt(self, prompt_id: str, **kwargs) -> Dict[str, Any]:
        """Prompt'u guncelle."""
        return await self._put(f"/prompts/{prompt_id}", json=kwargs)

    async def delete_prompt(self, prompt_id: str) -> None:
        """Prompt'u sil."""
        await self._delete(f"/prompts/{prompt_id}")

    async def set_prompt_state(self, prompt_id: str, active: bool) -> Dict[str, Any]:
        """Prompt'u aktif/pasif yap."""
        return await self._post(f"/prompts/{prompt_id}/state", json={"activate": active})

    async def toggle_prompt(self, prompt_id: str) -> Dict[str, Any]:
        """Prompt durumunu tersine cevir."""
        return await self._post(f"/prompts/{prompt_id}/toggle")

    async def render_prompt(self, prompt_id: str, arguments: Dict[str, str] | None = None) -> Dict[str, Any]:
        """Prompt'u argümanlarla render et (Jinja2 template)."""
        return await self._post(f"/prompts/{prompt_id}", json=arguments or {})

    # =========================================================================
    # RESOURCES
    # =========================================================================

    async def list_resources(self, include_inactive: bool = False) -> List[Dict[str, Any]]:
        """Gateway'deki tum resource'lari listele."""
        return await self._get("/resources", include_inactive=str(include_inactive).lower())

    async def get_resource(self, resource_id: str) -> Dict[str, Any]:
        """Resource icerigini oku (ID veya URL-encoded URI ile)."""
        return await self._get(f"/resources/{resource_id}")

    async def get_resource_info(self, resource_id: str) -> Dict[str, Any]:
        """Resource meta bilgisini getir (icerik olmadan)."""
        return await self._get(f"/resources/{resource_id}/info")

    async def create_resource(
        self,
        name: str,
        uri: str,
        description: str = "",
        mime_type: str = "text/plain",
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Yeni resource olustur.

        Args:
            name: Resource adi
            uri: Resource URI (orn: 'file:///data/schema.json', 'http://...')
            description: Aciklama
            mime_type: MIME tipi
        """
        payload: Dict[str, Any] = {
            "name": name,
            "uri": uri,
            "description": description,
            "mimeType": mime_type,
            **kwargs,
        }
        result = await self._post("/resources", json=payload)
        logger.info("Created resource: %s (%s)", name, uri)
        return result

    async def update_resource(self, resource_id: str, **kwargs) -> Dict[str, Any]:
        """Resource'u guncelle."""
        return await self._put(f"/resources/{resource_id}", json=kwargs)

    async def delete_resource(self, resource_id: str) -> None:
        """Resource'u sil."""
        await self._delete(f"/resources/{resource_id}")

    async def set_resource_state(self, resource_id: str, active: bool) -> Dict[str, Any]:
        """Resource'u aktif/pasif yap."""
        return await self._post(f"/resources/{resource_id}/state", json={"activate": active})

    async def toggle_resource(self, resource_id: str) -> Dict[str, Any]:
        """Resource durumunu tersine cevir."""
        return await self._post(f"/resources/{resource_id}/toggle")

    async def list_resource_templates(self) -> List[Dict[str, Any]]:
        """Mevcut resource template'lerini listele."""
        return await self._get("/resources/templates/list")

    # =========================================================================
    # VIRTUAL SERVERS
    # =========================================================================

    async def create_virtual_server(
        self,
        name: str,
        description: str = "",
        associated_tool_ids: List[str] | None = None,
        associated_prompt_ids: List[str] | None = None,
        associated_resource_ids: List[str] | None = None,
        tags: List[str] | None = None,
        visibility: str = "public",
    ) -> Dict[str, Any]:
        """
        Virtual server olustur. Tool/prompt/resource ID'leri ile bundle edilir.

        Args:
            name: Server adi
            description: Aciklama
            associated_tool_ids: Atanacak tool UUID listesi
            associated_prompt_ids: Atanacak prompt UUID listesi
            associated_resource_ids: Atanacak resource UUID listesi
            tags: Etiketler
            visibility: private | team | public
        """
        server_data: Dict[str, Any] = {
            "name": name,
            "description": description,
        }
        if associated_tool_ids:
            server_data["associated_tools"] = associated_tool_ids
        if associated_prompt_ids:
            server_data["associated_prompts"] = associated_prompt_ids
        if associated_resource_ids:
            server_data["associated_resources"] = associated_resource_ids
        if tags:
            server_data["tags"] = tags

        payload: Dict[str, Any] = {"server": server_data, "visibility": visibility}

        result = await self._post("/servers", json=payload)
        server_id = result.get("id", result.get("server", {}).get("id", ""))
        logger.info("Created virtual server: %s (id=%s)", name, server_id)
        return result

    async def list_virtual_servers(self) -> List[Dict[str, Any]]:
        return await self._get("/servers")

    async def get_virtual_server(self, server_id: str) -> Dict[str, Any]:
        return await self._get(f"/servers/{server_id}")

    async def update_virtual_server(
        self, server_id: str, **kwargs
    ) -> Dict[str, Any]:
        return await self._put(f"/servers/{server_id}", json=kwargs)

    async def delete_virtual_server(self, server_id: str) -> None:
        await self._delete(f"/servers/{server_id}")

    async def set_server_state(self, server_id: str, active: bool) -> Dict[str, Any]:
        return await self._post(
            f"/servers/{server_id}/state", json={"activate": active}
        )

    async def toggle_server(self, server_id: str) -> Dict[str, Any]:
        """Server durumunu tersine cevir (aktif/pasif)."""
        return await self._post(f"/servers/{server_id}/toggle")

    async def get_server_tools(self, server_id: str) -> List[Dict[str, Any]]:
        """Virtual server'a atanmis tool'lari listele."""
        return await self._get(f"/servers/{server_id}/tools")

    async def get_server_prompts(self, server_id: str) -> List[Dict[str, Any]]:
        """Virtual server'a atanmis prompt'lari listele."""
        return await self._get(f"/servers/{server_id}/prompts")

    async def get_server_resources(self, server_id: str) -> List[Dict[str, Any]]:
        """Virtual server'a atanmis resource'lari listele."""
        return await self._get(f"/servers/{server_id}/resources")

    def get_server_mcp_endpoint(self, server_id: str) -> str:
        """Virtual server'in MCP endpoint URL'i (streamable-http)."""
        return f"{self.base_url}/servers/{server_id}/mcp"

    def get_server_sse_endpoint(self, server_id: str) -> str:
        """Virtual server'in SSE endpoint URL'i."""
        return f"{self.base_url}/servers/{server_id}/sse"

    def get_server_ws_endpoint(self, server_id: str) -> str:
        """Virtual server'in WebSocket endpoint URL'i."""
        ws_base = self.base_url.replace("http://", "ws://").replace("https://", "wss://")
        return f"{ws_base}/servers/{server_id}/ws"

    def get_server_http_config(self, server_id: str) -> Dict[str, Any]:
        """Virtual server icin HTTP client konfigurasyonu (MCP istemcilere verilir)."""
        return {
            "servers": {
                server_id: {
                    "type": "streamable-http",
                    "url": self.get_server_mcp_endpoint(server_id),
                    "headers": {
                        "Authorization": f"Bearer {self._token or 'your-token-here'}"
                    },
                }
            }
        }

    # =========================================================================
    # JSON-RPC TOOL INVOCATION
    # =========================================================================

    async def invoke_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any] | None = None,
        request_id: int = 1,
    ) -> Any:
        """
        JSON-RPC 2.0 ile tool cagir.
        """
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments or {}},
        }
        result = await self._post("/rpc", json=payload)
        if "error" in result:
            raise MCPGatewayError(
                500, f"RPC error: {result['error']}", f"/rpc/{tool_name}"
            )
        return result.get("result")

    # =========================================================================
    # LLM CHAT (Gateway's built-in ReAct agent)
    # =========================================================================

    async def llmchat_connect(
        self,
        server_id: str,
        model: str = "gpt-4o",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        user_id: str = "",
        streaming: bool = True,
    ) -> Dict[str, Any]:
        """
        Start an LLM Chat session with a Virtual Server.
        Gateway creates a ReAct agent with the server's tools.
        """
        server_url = self.get_server_mcp_endpoint(server_id)
        payload = {
            "user_id": user_id,
            "server": {
                "url": server_url,
                "transport": "streamable_http",
                "auth_token": self._token or "",
            },
            "llm": {
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            "streaming": streaming,
        }
        return await self._post("/llmchat/connect", json=payload)

    async def llmchat_chat(
        self,
        user_id: str,
        message: str,
        streaming: bool = False,
    ) -> Dict[str, Any]:
        """
        Send a chat message (non-streaming).
        For streaming, use llmchat_chat_stream instead.
        """
        payload = {
            "user_id": user_id,
            "message": message,
            "streaming": streaming,
        }
        return await self._post("/llmchat/chat", json=payload)

    async def llmchat_chat_stream_raw(
        self,
        user_id: str,
        message: str,
    ) -> httpx.Response:
        """
        Send a chat message and return the raw SSE response for streaming.
        Caller is responsible for iterating over the response.
        """
        await self.ensure_auth()
        return await self._client.send(
            self._client.build_request(
                "POST",
                "/llmchat/chat",
                json={"user_id": user_id, "message": message, "streaming": True},
                headers=self._headers,
            ),
            stream=True,
        )

    async def llmchat_disconnect(self, user_id: str) -> Dict[str, Any]:
        """End the LLM Chat session."""
        return await self._post("/llmchat/disconnect", json={"user_id": user_id})

    async def llmchat_status(self, user_id: str) -> Dict[str, Any]:
        """Check if a chat session is active."""
        return await self._get(f"/llmchat/status/{user_id}")

    # =========================================================================
    # A2A AGENTS
    # =========================================================================

    async def list_a2a_agents(self) -> List[Dict[str, Any]]:
        """List all registered A2A agents."""
        return await self._get("/a2a/")

    async def get_a2a_agent(self, agent_id: str) -> Dict[str, Any]:
        """Get A2A agent details."""
        return await self._get(f"/a2a/{agent_id}")

    async def register_a2a_agent(
        self,
        name: str,
        endpoint_url: str,
        agent_type: str = "Generic",
        description: str = "",
        tags: List[str] | None = None,
        visibility: str = "public",
        auth_type: str | None = None,
        capabilities: Dict[str, Any] | None = None,
        config: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """
        Register a new A2A agent on the ContextForge Gateway.

        Args:
            name: Agent name (unique slug generated automatically)
            endpoint_url: HTTP endpoint for A2A invocations
            agent_type: Generic, openai, anthropic, etc.
            description: Agent description
            tags: Tag list
            visibility: public | team | private
            auth_type: None, Bearer, Basic, etc.
            capabilities: Agent capabilities dict
            config: Extra configuration
        """
        agent_data: Dict[str, Any] = {
            "name": name,
            "endpoint_url": endpoint_url,
            "agent_type": agent_type,
            "description": description,
        }
        if tags:
            agent_data["tags"] = tags
        if auth_type:
            agent_data["auth_type"] = auth_type
        if capabilities:
            agent_data["capabilities"] = capabilities
        if config:
            agent_data["config"] = config

        payload: Dict[str, Any] = {"agent": agent_data, "visibility": visibility}
        result = await self._post("/a2a/", json=payload)
        logger.info("Registered A2A agent: %s (id=%s)", name, result.get("id", ""))
        return result

    async def update_a2a_agent(self, agent_id: str, **kwargs) -> Dict[str, Any]:
        """Update an A2A agent."""
        result = await self._put(f"/a2a/{agent_id}", json=kwargs)
        logger.info("Updated A2A agent: %s", agent_id)
        return result

    async def delete_a2a_agent(self, agent_id: str) -> None:
        """Delete an A2A agent from the registry."""
        await self._delete(f"/a2a/{agent_id}")
        logger.info("Deleted A2A agent: %s", agent_id)

    async def set_a2a_agent_state(self, agent_id: str, active: bool) -> Dict[str, Any]:
        """Activate or deactivate an A2A agent."""
        return await self._post(
            f"/a2a/{agent_id}/state", json={"activate": active}
        )

    async def toggle_a2a_agent(self, agent_id: str) -> Dict[str, Any]:
        """Toggle A2A agent enabled status."""
        return await self._post(f"/a2a/{agent_id}/toggle")

    async def invoke_a2a_agent(
        self, agent_name: str, message: str, **kwargs
    ) -> Dict[str, Any]:
        """Invoke an A2A agent by name."""
        payload = {"message": message, **kwargs}
        return await self._post(f"/a2a/{agent_name}/invoke", json=payload)

    # =========================================================================
    # EXPORT / IMPORT
    # =========================================================================

    async def export_config(self) -> Dict[str, Any]:
        return await self._get("/export")

    async def import_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        return await self._post("/import", json=config)

    # =========================================================================
    # LIFECYCLE
    # =========================================================================

    async def close(self) -> None:
        await self._client.aclose()


# =============================================================================
# SINGLETON
# =============================================================================

async def get_gateway_client() -> MCPGatewayClient:
    """Singleton MCPGatewayClient instance."""
    global _gateway_instance
    if _gateway_instance is None:
        _gateway_instance = MCPGatewayClient()
        try:
            await _gateway_instance.authenticate()
        except Exception as e:
            logger.warning("MCP Gateway auth failed: %s", e)
    return _gateway_instance
