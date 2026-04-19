"""ocr_step — OCR processing for each document."""
from __future__ import annotations
from typing import Any
from ..node_registry import NodeType, ExecutionContext, PortDef, register
from ..models import PortDirection


@register
class OcrStepNode(NodeType):
    type_id = "ocr_step"
    label = "OCR"
    description = "Belgeleri OCR ile isler (Gemini OCR, fallback image extraction)."
    category = "processing"
    icon = "eye"
    color = "#DD6B20"

    @classmethod
    def params_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "ocr_mode": {
                    "type": "string",
                    "enum": ["hybrid", "gemini_only", "fallback_only"],
                    "default": "hybrid",
                    "description": "OCR stratejisi",
                },
                "max_pages": {
                    "type": "integer",
                    "default": 0,
                    "description": "Maks sayfa (0=sinirsiz)",
                },
            },
        }

    @classmethod
    def input_ports(cls) -> list[PortDef]:
        return [PortDef(name="files", direction=PortDirection.INPUT, data_type="file_list")]

    @classmethod
    def output_ports(cls) -> list[PortDef]:
        return [PortDef(name="ocr_results", direction=PortDirection.OUTPUT, data_type="ocr_list")]

    async def execute(self, ctx: ExecutionContext, params: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
        file_paths = inputs.get("files", [])
        ocr_mode = params.get("ocr_mode", "hybrid")
        results = []

        for fp in file_paths:
            result = {"file_path": fp, "status": "pending", "text": ""}
            existing = await ctx.pg.fetchrow(
                "SELECT ocr_text FROM ocr_result WHERE file_path=$1 ORDER BY created_at DESC LIMIT 1", fp,
            ) if ctx.pg else None

            if existing and existing["ocr_text"]:
                result["status"] = "cached"
                result["text"] = existing["ocr_text"]
            else:
                result["status"] = "needs_ocr"
                result["ocr_mode"] = ocr_mode

            results.append(result)

        return {"ocr_results": results}
