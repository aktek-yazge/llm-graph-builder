"""resources_input — Collect all raw files uploaded to the agent."""
from __future__ import annotations

import json
import logging
from typing import Any

from ..node_registry import NodeType, ExecutionContext, PortDef, register
from ..models import PortDirection

logger = logging.getLogger(__name__)


@register
class ResourcesInputNode(NodeType):
    type_id = "resources_input"
    label = "Kaynaklar"
    description = "Agent'a yuklenmus tum belgeleri pipeline'a saglar."
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
                    "description": "Opsiyonel: sadece belirli dosyalari isle",
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
        from ...knowledge_store import KnowledgeStore
        ks = KnowledgeStore(ctx.pg)

        explicit = params.get("file_paths", [])
        if explicit:
            files = [{"path": fp, "filename": fp.rsplit("/", 1)[-1]} for fp in explicit]
            return {"files": files}

        files: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        sf_entries = await ks.get_all(ctx.agent_id, "sample_files")
        for entry in sf_entries:
            val = entry.get("value", {})
            if isinstance(val, str):
                try:
                    val = json.loads(val)
                except (json.JSONDecodeError, ValueError):
                    continue
            for f in val.get("files", []):
                rid = f.get("resource_id", "")
                if rid and rid in seen_ids:
                    continue
                if rid:
                    seen_ids.add(rid)
                files.append({
                    "resource_id": rid,
                    "filename": f.get("filename", ""),
                    "path": f.get("path", ""),
                    "content_type": f.get("content_type", ""),
                    "size": f.get("size", 0),
                })

        logger.info("resources_input: %d file(s) for agent %s", len(files), ctx.agent_id)
        return {"files": files}
