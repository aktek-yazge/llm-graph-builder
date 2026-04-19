"""quality_gate — Conditional pass/fail based on confidence threshold."""
from __future__ import annotations
from typing import Any
from ..node_registry import NodeType, ExecutionContext, PortDef, register
from ..models import PortDirection


@register
class QualityGateNode(NodeType):
    type_id = "quality_gate"
    label = "Kalite Kapisi"
    description = "Guven esigine gore pass/fail karar verir (conditional edge)."
    category = "control"
    icon = "shield"
    color = "#E53E3E"

    @classmethod
    def params_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "threshold": {
                    "type": "number",
                    "default": 0.7,
                    "description": "Minimum ortalama guven esigi",
                },
            },
        }

    @classmethod
    def input_ports(cls) -> list[PortDef]:
        return [PortDef(name="in", direction=PortDirection.INPUT, data_type="any")]

    @classmethod
    def output_ports(cls) -> list[PortDef]:
        return [
            PortDef(name="pass", direction=PortDirection.OUTPUT, data_type="any"),
            PortDef(name="fail", direction=PortDirection.OUTPUT, data_type="any"),
        ]

    async def execute(self, ctx: ExecutionContext, params: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
        threshold = params.get("threshold", 0.7)
        data = inputs.get("in", {})

        avg_confidence = 0.0
        if isinstance(data, dict):
            avg_confidence = float(data.get("avg_confidence", 0.0))
        elif isinstance(data, list):
            scores = [float(item.get("confidence", 0)) for item in data if isinstance(item, dict)]
            avg_confidence = sum(scores) / len(scores) if scores else 0.0

        passed = avg_confidence >= threshold
        return {
            "pass": data if passed else None,
            "fail": data if not passed else None,
            "_gate_result": "pass" if passed else "fail",
        }
