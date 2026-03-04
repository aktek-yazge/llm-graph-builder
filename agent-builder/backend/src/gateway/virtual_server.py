"""
Virtual Server Manager
======================

Per-tenant virtual server lifecycle yonetimi.

Her agent deploy edildiginde:
1. Tenant icin virtual server yoksa olusturulur
2. Agent'in skill'leri tool olarak kaydedilir
3. Tool'lar virtual server'a atanir
4. Virtual server aktive edilir

Kullanim:
    manager = VirtualServerManager(gateway_client, ontology_db)
    result = await manager.deploy_agent("agent-123", "tenant-A")
"""

import logging
from typing import Any, Dict, List, Optional

from .mcp_gateway_client import MCPGatewayClient
from ..ontology.neo4j_client import OntologyDBClient

logger = logging.getLogger(__name__)


class VirtualServerManager:
    """
    Agent'lari IBM ContextForge virtual server'lara deploy eder.

    Her tenant ayri bir virtual server alir.
    Agent skill'leri gateway tool'larina donusturulur.
    """

    def __init__(self, gateway: MCPGatewayClient, db: OntologyDBClient):
        self.gateway = gateway
        self.db = db

    # =========================================================================
    # TENANT VIRTUAL SERVER
    # =========================================================================

    async def get_or_create_tenant_server(
        self,
        tenant_id: str,
        description: str = "",
    ) -> Dict[str, Any]:
        """
        Tenant icin virtual server getir veya olustur.
        Convention: server name = "tenant-{tenant_id}"
        """
        server_name = f"tenant-{tenant_id}"

        servers = await self.gateway.list_virtual_servers()
        for s in servers:
            srv = s if isinstance(s, dict) else {}
            if srv.get("name") == server_name:
                logger.info("Found existing VS for tenant %s: %s", tenant_id, srv.get("id"))
                return srv

        result = await self.gateway.create_virtual_server(
            name=server_name,
            description=description or f"Virtual server for tenant {tenant_id}",
        )
        logger.info("Created new VS for tenant %s", tenant_id)
        return result

    # =========================================================================
    # AGENT DEPLOYMENT
    # =========================================================================

    async def deploy_agent(
        self,
        agent_id: str,
        tenant_id: str,
    ) -> Dict[str, Any]:
        """
        Agent'i MCP Gateway'e deploy et.

        1. Agent ve skill bilgilerini Ontology DB'den yukle
        2. Tenant virtual server'i olustur/getir
        3. Skill'leri gateway tool olarak kaydet
        4. Tool'lari virtual server'a ata
        5. AgentDefinition node'unu guncelle (mcp_virtual_server_id, mcp_endpoint)
        """
        agent = await self._load_agent(agent_id, tenant_id)
        if not agent:
            return {"success": False, "error": f"Agent not found: {agent_id}"}

        vs = await self.get_or_create_tenant_server(tenant_id)
        server_id = vs.get("id", vs.get("server", {}).get("id", ""))

        skills = await self._load_agent_skills(agent_id)
        tool_ids = []
        for skill in skills:
            tool = await self._register_skill_as_tool(skill, agent_id, tenant_id)
            if tool:
                tid = tool.get("id", tool.get("tool", {}).get("id", ""))
                if tid:
                    tool_ids.append(tid)

        nlm_tool_ids = await self._discover_notebooklm_tools()
        all_tool_ids = tool_ids + nlm_tool_ids

        if all_tool_ids:
            await self.gateway.update_virtual_server(
                server_id,
                associated_tools=all_tool_ids,
            )

        await self.gateway.set_server_state(server_id, active=True)

        mcp_endpoint = self.gateway.get_server_mcp_endpoint(server_id)
        await self._update_agent_deployment(
            agent_id, server_id, mcp_endpoint, all_tool_ids
        )

        return {
            "success": True,
            "agent_id": agent_id,
            "virtual_server_id": server_id,
            "mcp_endpoint": mcp_endpoint,
            "tool_count": len(all_tool_ids),
        }

    async def undeploy_agent(self, agent_id: str, tenant_id: str) -> bool:
        """Agent'i gateway'den kaldir (tool'lari sil, VS'yi pasife al)."""
        result = await self.db.execute_query("""
            MATCH (a:AgentDefinition {id: $agent_id, tenant_id: $tenant_id})
            RETURN a.mcp_virtual_server_id AS vs_id, a.gateway_tool_ids AS tool_ids
        """, {"agent_id": agent_id, "tenant_id": tenant_id})

        if not result:
            return False

        tool_ids = result[0].get("tool_ids") or []
        for tid in tool_ids:
            try:
                await self.gateway.delete_tool(tid)
            except Exception as e:
                logger.warning("Failed to delete tool %s: %s", tid, e)

        await self.db.execute_query("""
            MATCH (a:AgentDefinition {id: $agent_id})
            SET a.status = 'draft',
                a.mcp_virtual_server_id = null,
                a.mcp_endpoint = null,
                a.gateway_tool_ids = null,
                a.deployed_at = null
        """, {"agent_id": agent_id}, write=True)

        return True

    # =========================================================================
    # INTERNALS
    # =========================================================================

    async def _load_agent(self, agent_id: str, tenant_id: str) -> Optional[Dict]:
        result = await self.db.execute_query("""
            MATCH (a:AgentDefinition {id: $agent_id, tenant_id: $tenant_id})
            RETURN a {.*} AS agent
        """, {"agent_id": agent_id, "tenant_id": tenant_id})
        return result[0]["agent"] if result else None

    async def _load_agent_skills(self, agent_id: str) -> List[Dict]:
        result = await self.db.execute_query("""
            MATCH (a:AgentDefinition {id: $agent_id})-[:HAS_SKILL]->(s:Skill)
            OPTIONAL MATCH (s)-[:EXTRACTS]->(es:EntitySchema)
            OPTIONAL MATCH (s)-[:CREATES]->(rs:RelationshipSchema)
            RETURN s {.*} AS skill,
                   collect(DISTINCT es {.*}) AS entity_schemas,
                   collect(DISTINCT rs {.*}) AS relationship_schemas
        """, {"agent_id": agent_id})
        return [
            {**r["skill"], "entity_schemas": r["entity_schemas"], "relationship_schemas": r["relationship_schemas"]}
            for r in result
        ]

    async def _register_skill_as_tool(
        self,
        skill: Dict,
        agent_id: str,
        tenant_id: str,
    ) -> Optional[Dict]:
        """Skill'i gateway tool olarak kaydet."""
        skill_id = skill.get("id", "")
        skill_name = skill.get("name", "unknown")
        tool_name = f"{tenant_id}_{agent_id}_{skill_name}".replace("-", "_")

        agent_builder_url = self.gateway.base_url.replace(":4444", ":8001")
        tool_url = f"{agent_builder_url}/api/v2/agent-builder/skills/{skill_id}/execution"

        try:
            return await self.gateway.register_tool(
                name=tool_name,
                url=tool_url,
                request_type="GET",
                integration_type="REST",
                description=skill.get("description", f"Skill: {skill_name}"),
                input_schema={
                    "type": "object",
                    "properties": {
                        "skill_id": {"type": "string", "const": skill_id},
                        "text": {"type": "string", "description": "Input text to process"},
                    },
                    "required": ["text"],
                },
            )
        except Exception as e:
            logger.error("Failed to register skill %s as tool: %s", skill_id, e)
            return None

    async def _discover_notebooklm_tools(self) -> List[str]:
        """
        Gateway'de kayitli NotebookLM upstream tool'larini bul.
        notebook_query, notebook_list, research_start gibi tool'lar
        auto-discover ile import edilmis olabilir.
        """
        try:
            all_tools = await self.gateway.list_tools()
            nlm_prefixes = ("notebook_", "research_", "source_")
            nlm_ids = []
            for t in all_tools:
                tool_name = t.get("name", "")
                if any(tool_name.startswith(p) for p in nlm_prefixes):
                    tid = t.get("id", "")
                    if tid:
                        nlm_ids.append(tid)
            if nlm_ids:
                logger.info("Found %d NotebookLM tools in gateway", len(nlm_ids))
            return nlm_ids
        except Exception as e:
            logger.debug("No NotebookLM tools found in gateway: %s", e)
            return []

    async def _update_agent_deployment(
        self,
        agent_id: str,
        server_id: str,
        mcp_endpoint: str,
        tool_ids: List[str],
    ) -> None:
        await self.db.execute_query("""
            MATCH (a:AgentDefinition {id: $agent_id})
            SET a.status = 'active',
                a.mcp_virtual_server_id = $server_id,
                a.mcp_endpoint = $mcp_endpoint,
                a.gateway_tool_ids = $tool_ids,
                a.deployed_at = datetime()
        """, {
            "agent_id": agent_id,
            "server_id": server_id,
            "mcp_endpoint": mcp_endpoint,
            "tool_ids": tool_ids,
        }, write=True)

    # =========================================================================
    # WORKSPACE TOOLS REGISTRATION
    # =========================================================================

    async def register_workspace_tools(self, tenant_id: str) -> Dict[str, Any]:
        """
        Workspace document processing endpoint'lerini MCP tool olarak kaydet.

        Bu tool'lar dis sistemlerin ve agent'larin belge isleme
        yeteneklerine MCP uzerinden erismesini saglar.
        """
        vs = await self.get_or_create_tenant_server(tenant_id, "Workspace processing tools")
        server_id = vs.get("id", vs.get("server", {}).get("id", ""))

        agent_builder_url = self.gateway.base_url.replace(":4444", ":8001")
        registered = []

        workspace_tools = [
            {
                "name": f"{tenant_id}_workspace_process_batch",
                "url": f"{agent_builder_url}/api/v2/workspaces/{{workspace_id}}/start-processing",
                "method": "POST",
                "description": "Workspace'teki belgelerin batch islemesini baslat",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "workspace_id": {"type": "string", "description": "Workspace ID"},
                        "batch_job_id": {"type": "string", "description": "Batch job ID"},
                    },
                    "required": ["workspace_id", "batch_job_id"],
                },
            },
            {
                "name": f"{tenant_id}_workspace_get_status",
                "url": f"{agent_builder_url}/api/v2/workspaces/{{workspace_id}}/progress-snapshot",
                "method": "GET",
                "description": "Workspace isleme ilerleme durumunu getir",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "workspace_id": {"type": "string"},
                        "batch_job_id": {"type": "string"},
                    },
                    "required": ["workspace_id", "batch_job_id"],
                },
            },
            {
                "name": f"{tenant_id}_workspace_review_queue",
                "url": f"{agent_builder_url}/api/v2/workspaces/{{workspace_id}}/review-queue",
                "method": "GET",
                "description": "Dusuk guvenli ve hatali belgelerin review kuyrugunu getir",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "workspace_id": {"type": "string"},
                        "limit": {"type": "integer", "default": 50},
                    },
                    "required": ["workspace_id"],
                },
            },
            {
                "name": f"{tenant_id}_workspace_approve",
                "url": f"{agent_builder_url}/api/v2/workspaces/{{workspace_id}}/approve-batch",
                "method": "POST",
                "description": "Review kuyrugundaki belgeleri onayla veya reddet",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "workspace_id": {"type": "string"},
                        "document_ids": {"type": "array", "items": {"type": "string"}},
                        "action": {"type": "string", "enum": ["approve", "reject"]},
                    },
                    "required": ["workspace_id", "document_ids"],
                },
            },
        ]

        tool_ids = []
        for t in workspace_tools:
            try:
                result = await self.gateway.register_tool(
                    name=t["name"],
                    url=t["url"],
                    request_type=t["method"],
                    integration_type="REST",
                    description=t["description"],
                    input_schema=t["input_schema"],
                )
                tid = result.get("id", result.get("tool", {}).get("id", ""))
                if tid:
                    tool_ids.append(tid)
                    registered.append(t["name"])
            except Exception as e:
                logger.error("Failed to register workspace tool %s: %s", t["name"], e)

        if tool_ids:
            try:
                await self.gateway.update_virtual_server(
                    server_id,
                    associated_tools=tool_ids,
                )
            except Exception as e:
                logger.warning("Failed to associate workspace tools to VS: %s", e)

        logger.info("Registered %d workspace tools for tenant %s", len(registered), tenant_id)
        return {
            "tenant_id": tenant_id,
            "registered_tools": registered,
            "tool_count": len(registered),
        }

    # =========================================================================
    # KB RESOURCES — Register KB schema/stats as Gateway resources
    # =========================================================================

    async def register_kb_resources(
        self,
        server_id: str,
        workspace_name: str = "default",
    ) -> Dict[str, Any]:
        """
        Register KB schema and stats resources on the Gateway
        and associate them with a virtual server.
        """
        kb_resources = [
            {
                "name": f"kb-schema-{workspace_name}",
                "uri": "neo4j://kb/schema",
                "description": f"Knowledge Base schema for {workspace_name}: node labels, relationship types, property keys",
                "mime_type": "application/json",
            },
            {
                "name": f"kb-stats-{workspace_name}",
                "uri": "neo4j://kb/stats",
                "description": f"Knowledge Base statistics for {workspace_name}: node/relationship counts",
                "mime_type": "application/json",
            },
        ]

        resource_ids = []
        registered = []

        for res in kb_resources:
            try:
                existing = await self.gateway.list_resources()
                found = next(
                    (r for r in existing if r.get("name") == res["name"]),
                    None,
                )
                if found:
                    rid = found.get("id", "")
                    resource_ids.append(rid)
                    registered.append(res["name"])
                    continue

                result = await self.gateway.create_resource(
                    name=res["name"],
                    uri=res["uri"],
                    description=res["description"],
                    mime_type=res["mime_type"],
                )
                rid = result.get("id", result.get("resource", {}).get("id", ""))
                if rid:
                    resource_ids.append(rid)
                    registered.append(res["name"])
            except Exception as e:
                logger.error("Failed to register KB resource %s: %s", res["name"], e)

        if resource_ids:
            try:
                current = await self.gateway.get_virtual_server(server_id)
                existing_resources = []
                srv = current.get("server", current)
                if isinstance(srv, dict):
                    existing_resources = srv.get("associated_resources", [])
                merged = list(set(existing_resources + resource_ids))
                await self.gateway.update_virtual_server(
                    server_id, associated_resources=merged,
                )
            except Exception as e:
                logger.warning("Failed to associate KB resources to VS %s: %s", server_id, e)

        logger.info("Registered %d KB resources on server %s", len(registered), server_id)
        return {
            "server_id": server_id,
            "registered_resources": registered,
            "resource_ids": resource_ids,
        }

    # =========================================================================
    # RESOURCE ACCESS TOOLS — Agent'larin resource'lara erismesi icin
    # =========================================================================

    async def register_resource_tools(self, tenant_id: str) -> Dict[str, Any]:
        """
        Resource erisim endpoint'lerini MCP Gateway tool olarak kaydet.

        Agent'lar bu tool'lar araciligiyla resource'lari listeleyebilir,
        detaylarini gorebilir ve tip bazli filtreleme yapabilir.
        """
        vs = await self.get_or_create_tenant_server(tenant_id, "Resource access tools")
        server_id = vs.get("id", vs.get("server", {}).get("id", ""))

        agent_builder_url = self.gateway.base_url.replace(":4444", ":8001")
        registered = []

        resource_tools = [
            {
                "name": f"{tenant_id}_resource_list",
                "url": f"{agent_builder_url}/api/v2/resources",
                "method": "GET",
                "description": (
                    "Mevcut resource'lari listele. "
                    "Tip filtresi: minio (dosya), link (URL), youtube, image, notebooklm. "
                    "NotebookLM resource'lari notebook_id icerir, agent bunlara soru sorabilir."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "tenant_id": {"type": "string", "default": "default"},
                        "type": {
                            "type": "string",
                            "description": "Resource tipi filtresi",
                            "enum": ["minio", "link", "youtube", "image", "notebooklm"],
                        },
                        "limit": {"type": "integer", "default": 50},
                    },
                },
            },
            {
                "name": f"{tenant_id}_resource_detail",
                "url": f"{agent_builder_url}/api/v2/resources/{{resource_id}}",
                "method": "GET",
                "description": (
                    "Resource detayini getir: metadata, durum, belgeler. "
                    "Metadata icinde url, notebook_id gibi tip-spesifik bilgiler bulunur."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "resource_id": {"type": "string", "description": "Resource UUID"},
                        "include_docs": {"type": "boolean", "default": True},
                    },
                    "required": ["resource_id"],
                },
            },
        ]

        tool_ids = []
        for t in resource_tools:
            try:
                result = await self.gateway.register_tool(
                    name=t["name"],
                    url=t["url"],
                    request_type=t["method"],
                    integration_type="REST",
                    description=t["description"],
                    input_schema=t["input_schema"],
                )
                tid = result.get("id", result.get("tool", {}).get("id", ""))
                if tid:
                    tool_ids.append(tid)
                    registered.append(t["name"])
            except Exception as e:
                logger.error("Failed to register resource tool %s: %s", t["name"], e)

        if tool_ids:
            try:
                current = await self.gateway.get_virtual_server(server_id)
                existing_tools = []
                srv = current.get("server", current)
                if isinstance(srv, dict):
                    existing_tools = srv.get("associated_tools", [])
                merged = list(set(existing_tools + tool_ids))
                await self.gateway.update_virtual_server(
                    server_id, associated_tools=merged,
                )
            except Exception as e:
                logger.warning("Failed to associate resource tools to VS: %s", e)

        logger.info("Registered %d resource tools for tenant %s", len(registered), tenant_id)
        return {
            "tenant_id": tenant_id,
            "registered_tools": registered,
            "tool_count": len(registered),
        }
