"""human_review — Pause execution for human approval (HITL).

The node raises a special ``HumanReviewPending`` exception that the
runtime catches.  The runtime persists the run with status
``awaiting_approval`` and stops graph execution.  A separate API
endpoint (``POST /workflow/runs/{run_id}/approve``) resumes the run.

If ``auto_approve_after_sec > 0`` the runtime will schedule an
automatic approval after the specified delay.
"""
from __future__ import annotations

from typing import Any

from ..node_registry import NodeType, ExecutionContext, PortDef, register
from ..models import PortDirection


class HumanReviewPending(Exception):
    """Raised inside execute() to signal the runtime to pause."""

    def __init__(self, run_id: str, message: str, auto_approve_sec: int = 0) -> None:
        self.run_id = run_id
        self.message = message
        self.auto_approve_sec = auto_approve_sec
        super().__init__(f"Awaiting human approval for run {run_id}")


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
        message = params.get("prompt_message", "Devam etmek icin onayla.")
        auto_sec = params.get("auto_approve_after_sec", 0)

        if ctx.notification_mgr:
            await ctx.notification_mgr.notify(
                agent_id=ctx.agent_id,
                event_type="human_review_requested",
                data={
                    "run_id": ctx.run_id,
                    "message": message,
                    "auto_approve_after_sec": auto_sec,
                },
            )

        raise HumanReviewPending(
            run_id=ctx.run_id,
            message=message,
            auto_approve_sec=auto_sec,
        )
