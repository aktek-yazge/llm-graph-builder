"""publish_graphrag_endpoint — Freeze KG + publish endpoint for react_agent."""
from __future__ import annotations
import os
from typing import Any
from ..node_registry import NodeType, ExecutionContext, PortDef, register
from ..models import PortDirection


@register
class PublishGraphRAGEndpointNode(NodeType):
    type_id = "publish_graphrag_endpoint"
    label = "GraphRAG Yayinla"
    description = "KG'yi dondurur ve end-user agent (react_agent) icin endpoint olusturur."
    category = "output"
    icon = "globe"
    color = "#319795"

    @classmethod
    def params_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "endpoint_name": {
                    "type": "string",
                    "default": "",
                    "description": "Endpoint adi (bos=agent adiyla olusturulur)",
                },
            },
        }

    @classmethod
    def input_ports(cls) -> list[PortDef]:
        return [
            PortDef(name="kg_stats", direction=PortDirection.INPUT, data_type="dict"),
            PortDef(name="files", direction=PortDirection.INPUT, data_type="file_list"),
        ]

    @classmethod
    def output_ports(cls) -> list[PortDef]:
        return [PortDef(name="endpoint", direction=PortDirection.OUTPUT, data_type="dict")]

    async def execute(self, ctx: ExecutionContext, params: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
        from ..store import WorkflowStore
        store = WorkflowStore(ctx.pg)

        wf = await store.get_by_agent(ctx.agent_id)
        workflow_id = wf.workflow_id if wf else ""
        version = wf.version if wf else 1

        from ...knowledge_store import KnowledgeStore
        ks = KnowledgeStore(ctx.pg)
        ontology = await ks.load_ontology(ctx.agent_id)

        source_documents = await self._collect_source_documents(
            ks, ctx.agent_id, inputs.get("files", []),
        )

        endpoint_name = params.get("endpoint_name", "")
        if not endpoint_name:
            identity = await ks.get(ctx.agent_id, "identity", "main")
            agent_name = ""
            if identity:
                val = identity.get("value", {})
                if isinstance(val, str):
                    import json
                    try:
                        val = json.loads(val)
                    except (json.JSONDecodeError, ValueError):
                        val = {}
                agent_name = val.get("name", "")
            endpoint_name = agent_name or ctx.agent_id

        neo4j_uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        neo4j_db = os.getenv("NEO4J_DATABASE", "neo4j")

        endpoint_id = await store.create_graphrag_endpoint(
            agent_id=ctx.agent_id,
            workflow_id=workflow_id,
            workflow_version=version,
            neo4j_uri=neo4j_uri,
            neo4j_database=neo4j_db,
            ontology_snapshot=ontology.to_dict() if ontology else {},
            schema_summary=str(ontology) if ontology else "",
            name=endpoint_name,
            source_documents=source_documents,
        )

        if wf:
            await store.publish(wf.workflow_id)

        return {
            "endpoint": {
                "endpoint_id": endpoint_id,
                "neo4j_uri": neo4j_uri,
                "neo4j_database": neo4j_db,
                "workflow_version": version + 1,
                "status": "active",
                "source_document_count": len(source_documents),
            },
        }

    @staticmethod
    async def _collect_source_documents(
        ks, agent_id: str, files: list,
    ) -> list[dict[str, Any]]:
        """Build source_documents list from agent's sample_files knowledge."""
        sf_entries = await ks.get_all(agent_id, "sample_files")
        all_files: list[dict[str, Any]] = []
        for entry in sf_entries:
            val = entry.get("value", {})
            if isinstance(val, str):
                import json
                try:
                    val = json.loads(val)
                except (json.JSONDecodeError, ValueError):
                    continue
            for f in val.get("files", []):
                all_files.append(f)

        if files:
            path_set: set[str] = set()
            for f in files:
                if isinstance(f, dict):
                    path_set.add(f.get("path", ""))
                    path_set.add(f.get("filename", ""))
                elif isinstance(f, str):
                    path_set.add(f)
            path_set.discard("")
            if path_set:
                matched = [
                    f for f in all_files
                    if f.get("path", "") in path_set or f.get("filename", "") in path_set
                ]
                if matched:
                    all_files = matched

        docs = []
        seen_ids: set[str] = set()
        for f in all_files:
            rid = f.get("resource_id", "")
            if rid and rid in seen_ids:
                continue
            if rid:
                seen_ids.add(rid)
            docs.append({
                "resource_id": rid,
                "filename": f.get("filename", ""),
                "size": f.get("size", 0),
                "content_type": f.get("content_type", ""),
            })
        return docs
