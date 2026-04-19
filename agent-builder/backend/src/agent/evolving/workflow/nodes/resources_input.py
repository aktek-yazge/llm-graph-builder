"""resources_input — Source files / S3 prefix for the pipeline."""
from __future__ import annotations
from typing import Any
from ..node_registry import NodeType, ExecutionContext, PortDef, register
from ..models import PortDirection


@register
class ResourcesInputNode(NodeType):
    type_id = "resources_input"
    label = "Kaynaklar"
    description = "Dosya yollarini veya S3 prefix'ini pipeline'a saglar."
    category = "input"
    icon = "folder"
    color = "#38A169"

    @classmethod
    def params_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Islenecek dosya yollari",
                },
                "s3_prefix": {
                    "type": "string",
                    "description": "Opsiyonel S3 prefix (s3://bucket/path)",
                    "default": "",
                },
            },
        }

    @classmethod
    def input_ports(cls) -> list[PortDef]:
        return []

    @classmethod
    def output_ports(cls) -> list[PortDef]:
        return [PortDef(name="files", direction=PortDirection.OUTPUT, data_type="file_list")]

    async def execute(self, ctx: ExecutionContext, params: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
        file_paths = params.get("file_paths", [])
        if not file_paths:
            from ...tools.self_tools import _get_pg
            pg = ctx.pg
            rows = await pg.fetch(
                "SELECT file_path FROM workspace_documents WHERE agent_id=$1 AND status != 'deleted' ORDER BY sequence",
                ctx.agent_id,
            )
            file_paths = [r["file_path"] for r in rows]

        return {"files": file_paths}
