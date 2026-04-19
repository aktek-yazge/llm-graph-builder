"""ontology_designer — HITL ontology design step."""
from __future__ import annotations
from typing import Any
from ..node_registry import NodeType, ExecutionContext, PortDef, register
from ..models import PortDirection


@register
class OntologyDesignerNode(NodeType):
    type_id = "ontology_designer"
    label = "Ontoloji Tasarimcisi"
    description = "Wiki sayfalarindan ontoloji onerisi yapar; kullanici onayi gerektirir (HITL)."
    category = "design"
    icon = "diagram"
    color = "#805AD5"

    @classmethod
    def params_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "auto_suggest": {
                    "type": "boolean",
                    "default": True,
                    "description": "LLM ile otomatik entity/relation onerisi",
                },
                "require_approval": {
                    "type": "boolean",
                    "default": True,
                    "description": "Kullanici onaylama adimi",
                },
            },
        }

    @classmethod
    def input_ports(cls) -> list[PortDef]:
        return [PortDef(name="wiki_pages", direction=PortDirection.INPUT, data_type="wiki_page_list")]

    @classmethod
    def output_ports(cls) -> list[PortDef]:
        return [PortDef(name="ontology", direction=PortDirection.OUTPUT, data_type="ontology")]

    async def execute(self, ctx: ExecutionContext, params: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
        from ...knowledge_store import KnowledgeStore
        ks = KnowledgeStore(ctx.pg)
        ontology = await ks.load_ontology(ctx.agent_id)
        return {"ontology": ontology.model_dump() if ontology else {}}
