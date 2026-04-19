"""human_review — Pause execution for human approval (HITL)."""
from __future__ import annotations
from typing import Any
from ..node_registry import NodeType, ExecutionContext, PortDef, register
from ..models import PortDirection


@register
class HumanReviewNode(NodeType):
    type_id = "human_review"
    label = "Kullanici Incelemesi"
    description = "Islemi durdurur ve kullanicidan onay bekler (Human-in-the-Loop)."
    category = "control"
    icon = "user"
    color = "#718096"

    @classmethod
    def params_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt_message": {
                    "type": "string",
                    "default": "Devam etmek icin onayla.",
                    "description": "Kullaniciya gosterilecek mesaj",
                },
                "auto_approve_after_sec": {
                    "type": "integer",
                    "default": 0,
                    "description": "Otomatik onay suresi (0=bekle)",
                },
            },
        }

    @classmethod
    def input_ports(cls) -> list[PortDef]:
        return [PortDef(name="in", direction=PortDirection.INPUT, data_type="any")]

    @classmethod
    def output_ports(cls) -> list[PortDef]:
        return [PortDef(name="out", direction=PortDirection.OUTPUT, data_type="any")]

    async def execute(self, ctx: ExecutionContext, params: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
        if ctx.notification_mgr:
            await ctx.notification_mgr.notify(
                agent_id=ctx.agent_id,
                event_type="human_review_requested",
                data={
                    "run_id": ctx.run_id,
                    "message": params.get("prompt_message", "Devam etmek icin onayla."),
                },
            )
        return {"out": inputs.get("in", {})}
