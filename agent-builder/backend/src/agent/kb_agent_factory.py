"""
KB Agent Factory
================

Workspace Agent tarafindan onaylanan schema'dan otomatik
Knowledge Base Agent olusturur.

Akis:
1. Workspace'ten onaylanmis schema yukle (entity/relationship)
2. Extraction skill olustur
3. AgentDefinition node'u olustur, skill'lere bagla
4. MCP tool konfigurasyonunu ayarla (MinIO, extraction, neo4j)
5. Opsiyonel: MCP Gateway'e deploy et
"""

import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from ..agent_repository import AgentRepository
from ..workspace_repository import WorkspaceRepository
from ..gateway.mcp_gateway_client import MCPGatewayClient, get_gateway_client
from ..gateway.virtual_server import VirtualServerManager

logger = logging.getLogger(__name__)


class KBAgentFactory:
    """
    Onaylanmis Workspace schema'sindan KB Agent olusturur.
    Workspace -> Schema -> Skill -> AgentDefinition -> Deploy
    """

    def __init__(
        self,
        agent_repo: AgentRepository,
        ws_repo: WorkspaceRepository,
        gateway: Optional[MCPGatewayClient] = None,
    ):
        self.agent_repo = agent_repo
        self.ws_repo = ws_repo
        self._gateway = gateway

    async def _gw(self):
        if self._gateway:
            return self._gateway
        return await get_gateway_client()

    # =========================================================================
    # MAIN ENTRY POINT
    # =========================================================================

    async def create_from_workspace(
        self,
        workspace_id: str,
        tenant_id: str,
        auto_deploy: bool = False,
        custom_name: str = "",
        custom_purpose: str = "",
        minio_bucket: str = "",
        minio_prefix: str = "",
    ) -> Dict[str, Any]:
        ws = await self.ws_repo.get_workspace(workspace_id)
        if not ws:
            return {"error": f"Workspace not found: {workspace_id}"}

        ws_status = ws.get("status", "")
        if ws_status not in ("ready", "schema_review", "schema_proposed", "schema_approved"):
            return {"error": f"Workspace not ready (status={ws_status}). Schema must be approved first."}

        entity_schemas = await self.ws_repo.get_workspace_entity_schemas(workspace_id)
        relationship_schemas = await self.ws_repo.get_workspace_relationship_schemas(workspace_id)

        if not entity_schemas:
            return {"error": "No entity schemas found. Approve schema first."}

        ws_name = ws.get("name", "Workspace")
        agent_name = custom_name or f"{ws_name} KB Agent"
        agent_purpose = custom_purpose or self._generate_purpose(ws, entity_schemas, relationship_schemas)

        # Step 1: Goal
        goal_id = f"goal-{uuid.uuid4().hex[:12]}"
        await self.agent_repo.create_goal({
            "id": goal_id,
            "name": f"{ws_name} Knowledge Extraction",
            "description": f"Extract knowledge from documents in {ws_name}",
            "goal_type": "extraction",
            "natural_language_query": ws.get("extraction_config", ""),
            "status": "active",
            "tenant_id": tenant_id,
        })

        # Step 2: Skill
        skill_id = f"skill-{uuid.uuid4().hex[:12]}"
        prompt_template = self._build_extraction_prompt(entity_schemas, relationship_schemas, ws)

        await self.agent_repo.create_skill({
            "id": skill_id,
            "name": f"{ws_name} Extraction",
            "description": f"Entity/relationship extraction for {ws_name}",
            "skill_category": "extraction",
            "prompt_template": prompt_template,
            "tenant_id": tenant_id,
            "is_global": False,
            "effectiveness_score": 0.5,
            "usage_count": 0,
            "version": 1,
        })

        es_ids = [e["id"] for e in entity_schemas if e.get("id")]
        rs_ids = [r["id"] for r in relationship_schemas if r.get("id")]

        for es_id in es_ids:
            await self.agent_repo.link_skill_entity_schema(skill_id, es_id)
        for rs_id in rs_ids:
            await self.agent_repo.link_skill_relationship_schema(skill_id, rs_id)

        # Step 3: AgentDefinition
        agent_id = f"kb-agent-{uuid.uuid4().hex[:12]}"
        source_bucket = minio_bucket or ws.get("minio_bucket", "documents")
        source_prefix = minio_prefix or ws.get("minio_prefix", "")

        config = {
            "workspace_id": workspace_id,
            "source_bucket": source_bucket,
            "source_prefix": source_prefix,
            "ocr_mode": ws.get("ocr_mode", "hybrid"),
            "batch_size": ws.get("batch_size", 100),
            "schema_source": ws.get("schema_source", "workspace_agent"),
            "entity_types": [e.get("entity_type", "") for e in entity_schemas],
            "relationship_types": [r.get("relationship_type", "") for r in relationship_schemas],
            "mcp_tools": [
                "storage_browse_files",
                "storage_read_file",
                "storage_upload_file",
                "extract_extract_entities",
                "extract_classify_document",
                "extract_summarize_document",
                "neo4j_read_cypher",
            ],
        }

        await self.agent_repo.create_agent({
            "id": agent_id,
            "name": agent_name,
            "description": f"Knowledge Base extraction agent for {ws_name}",
            "purpose": agent_purpose,
            "status": "draft",
            "tenant_id": tenant_id,
            "config": config,
            "agent_type": "kb_extraction",
            "workspace_id": workspace_id,
        })

        await self.agent_repo.link_agent_skill(agent_id, skill_id)
        await self.agent_repo.link_agent_goal(agent_id, goal_id)

        await self.ws_repo.update_workspace(workspace_id, {
            "agent_id": agent_id,
            "skill_id": skill_id,
            "status": "agent_ready",
        })

        result = {
            "agent_id": agent_id,
            "agent_name": agent_name,
            "workspace_id": workspace_id,
            "goal_id": goal_id,
            "skill_id": skill_id,
            "entity_schema_count": len(es_ids),
            "relationship_schema_count": len(rs_ids),
            "config": config,
            "status": "draft",
        }

        if auto_deploy:
            deploy_result = await self._deploy_agent(agent_id, tenant_id)
            result["deploy"] = deploy_result
            result["status"] = "active" if deploy_result.get("success") else "draft"

        logger.info(
            "Created KB Agent %s for workspace %s (%d entities, %d relationships)",
            agent_id, workspace_id, len(es_ids), len(rs_ids),
        )
        return result

    # =========================================================================
    # DEPLOYMENT
    # =========================================================================

    async def _deploy_agent(self, agent_id: str, tenant_id: str) -> Dict[str, Any]:
        try:
            gw_client = await self._gw()
            from ..ontology import get_ontology_client
            db = await get_ontology_client()
            vs_manager = VirtualServerManager(gw_client, db)
            return await vs_manager.deploy_agent(agent_id, tenant_id)
        except Exception as e:
            logger.error("KB Agent deploy failed: %s", e)
            return {"success": False, "error": str(e)}

    # =========================================================================
    # PROMPT GENERATION
    # =========================================================================

    def _generate_purpose(
        self,
        ws: Dict,
        entity_schemas: List[Dict],
        relationship_schemas: List[Dict],
    ) -> str:
        ws_name = ws.get("name", "Workspace")
        entity_types = [e.get("entity_type", "") for e in entity_schemas]
        rel_types = [r.get("relationship_type", "") for r in relationship_schemas]

        purpose = (
            f"{ws_name} workspace'indeki belgelerden knowledge graph olustur. "
            f"Hedef entity tipleri: {', '.join(entity_types)}. "
        )
        if rel_types:
            purpose += f"Iliski tipleri: {', '.join(rel_types)}. "

        extraction_config = ws.get("extraction_config", {})
        if isinstance(extraction_config, str):
            try:
                extraction_config = json.loads(extraction_config)
            except (json.JSONDecodeError, TypeError):
                extraction_config = {}
        intent = extraction_config.get("intent", "")
        if intent:
            purpose += f"Kullanici amaci: {intent}. "

        purpose += (
            "Belgeleri MinIO'dan oku, MCP extraction tool'lari ile entity/relationship cikar, "
            "Neo4j knowledge graph'a kaydet."
        )
        return purpose

    def _build_extraction_prompt(
        self,
        entity_schemas: List[Dict],
        relationship_schemas: List[Dict],
        ws: Dict,
    ) -> str:
        entity_section = ""
        for es in entity_schemas:
            et = es.get("entity_type", "Unknown")
            desc = es.get("description", "")
            props_raw = es.get("properties", {})
            if isinstance(props_raw, str):
                try:
                    props = json.loads(props_raw)
                except (json.JSONDecodeError, TypeError):
                    props = {}
            else:
                props = props_raw or {}

            prop_lines = []
            for pname, pdef in props.items():
                ptype = pdef.get("type", "string") if isinstance(pdef, dict) else "string"
                required = pdef.get("required", False) if isinstance(pdef, dict) else False
                prop_lines.append(f"    - {pname}: {ptype}" + (" (zorunlu)" if required else ""))

            entity_section += f"\n### {et}\n{desc}\nProperty'ler:\n" + "\n".join(prop_lines) + "\n"

        rel_section = ""
        for rs in relationship_schemas:
            rt = rs.get("relationship_type", "RELATED")
            src = rs.get("source_entity", "?")
            tgt = rs.get("target_entity", "?")
            desc = rs.get("description", "")
            rel_section += f"\n- {rt}: ({src}) -> ({tgt}) - {desc}"

        extraction_rules = ""
        ec = ws.get("extraction_config", {})
        if isinstance(ec, str):
            try:
                ec = json.loads(ec)
            except (json.JSONDecodeError, TypeError):
                ec = {}
        rules = ec.get("extraction_rules", []) if isinstance(ec, dict) else []
        if rules:
            extraction_rules = "\n\nCIKARIM KURALLARI:\n" + "\n".join(f"- {r}" for r in rules)

        return f"""Asagidaki belgeden entity ve relationship cikar.

## ENTITY TIPLERI
{entity_section}

## ILISKI TIPLERI
{rel_section}
{extraction_rules}

## CIKTI FORMATI
JSON olarak dondur:
{{
  "nodes": [
    {{"id": "unique_id", "label": "EntityType", "properties": {{"prop1": "value"}}}}
  ],
  "relationships": [
    {{"source": "source_id", "target": "target_id", "type": "REL_TYPE", "properties": {{}}}}
  ]
}}

BELGE:
{{document_text}}"""
