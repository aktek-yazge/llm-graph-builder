"""entity_extractor — Goal-driven entity extraction using ontology + wiki context."""
from __future__ import annotations
from typing import Any
from ..node_registry import NodeType, ExecutionContext, PortDef, register
from ..models import PortDirection


@register
class EntityExtractorNode(NodeType):
    type_id = "entity_extractor"
    label = "Entity Cikarici"
    description = "Ontoloji ve wiki baglamindan goal-driven entity/relation cikarir."
    category = "processing"
    icon = "search"
    color = "#D53F8C"

    @classmethod
    def params_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "batch_size": {
                    "type": "integer",
                    "default": 10,
                    "description": "Paralel islenecek belge sayisi",
                },
                "confidence_threshold": {
                    "type": "number",
                    "default": 0.7,
                    "description": "Minimum guven esigi",
                },
            },
        }

    @classmethod
    def input_ports(cls) -> list[PortDef]:
        return [
            PortDef(name="ocr_results", direction=PortDirection.INPUT, data_type="ocr_list"),
            PortDef(name="ontology", direction=PortDirection.INPUT, data_type="ontology"),
        ]

    @classmethod
    def output_ports(cls) -> list[PortDef]:
        return [
            PortDef(name="entities", direction=PortDirection.OUTPUT, data_type="entity_list"),
            PortDef(name="relations", direction=PortDirection.OUTPUT, data_type="relation_list"),
        ]

    async def execute(self, ctx: ExecutionContext, params: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
        return {
            "entities": [],
            "relations": [],
        }
