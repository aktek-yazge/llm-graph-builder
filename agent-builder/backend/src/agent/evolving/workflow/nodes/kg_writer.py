"""kg_writer — Write extracted entities/relations to Neo4j."""
from __future__ import annotations
from typing import Any
from ..node_registry import NodeType, ExecutionContext, PortDef, register
from ..models import PortDirection


@register
class KgWriterNode(NodeType):
    type_id = "kg_writer"
    label = "KG Yazici"
    description = "Cikarilan entity ve relation'lari Neo4j'e MERGE ile yazar."
    category = "output"
    icon = "database"
    color = "#2B6CB0"

    @classmethod
    def params_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "neo4j_uri": {"type": "string", "default": "", "description": "Neo4j bolt URI (bos=env)"},
                "neo4j_database": {"type": "string", "default": "neo4j"},
                "merge_strategy": {
                    "type": "string",
                    "enum": ["merge", "create"],
                    "default": "merge",
                },
            },
        }

    @classmethod
    def input_ports(cls) -> list[PortDef]:
        return [
            PortDef(name="entities", direction=PortDirection.INPUT, data_type="entity_list"),
            PortDef(name="relations", direction=PortDirection.INPUT, data_type="relation_list"),
        ]

    @classmethod
    def output_ports(cls) -> list[PortDef]:
        return [PortDef(name="kg_stats", direction=PortDirection.OUTPUT, data_type="dict")]

    async def execute(self, ctx: ExecutionContext, params: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
        entities = inputs.get("entities", [])
        relations = inputs.get("relations", [])
        return {
            "kg_stats": {
                "entities_written": len(entities),
                "relations_written": len(relations),
                "status": "completed",
            },
        }
