"""wiki_builder — Create wiki pages from OCR'd documents."""
from __future__ import annotations
from typing import Any
from ..node_registry import NodeType, ExecutionContext, PortDef, register
from ..models import PortDirection


@register
class WikiBuilderNode(NodeType):
    type_id = "wiki_builder"
    label = "Wiki Olusturucu"
    description = "OCR sonuclarindan wiki sayfalari olusturur (LLM ile)."
    category = "processing"
    icon = "book"
    color = "#3182CE"

    @classmethod
    def params_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "template": {
                    "type": "string",
                    "default": "",
                    "description": "Wiki sayfasi sablonu (bos = varsayilan)",
                },
                "auto_link": {
                    "type": "boolean",
                    "default": True,
                    "description": "Sayfalar arasi otomatik baglanti kur",
                },
            },
        }

    @classmethod
    def input_ports(cls) -> list[PortDef]:
        return [PortDef(name="ocr_results", direction=PortDirection.INPUT, data_type="ocr_list")]

    @classmethod
    def output_ports(cls) -> list[PortDef]:
        return [PortDef(name="wiki_pages", direction=PortDirection.OUTPUT, data_type="wiki_page_list")]

    async def execute(self, ctx: ExecutionContext, params: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
        ocr_results = inputs.get("ocr_results", [])
        pages = []
        for item in ocr_results:
            pages.append({
                "file_path": item.get("file_path", ""),
                "text": item.get("text", ""),
                "status": "draft",
            })
        return {"wiki_pages": pages}
