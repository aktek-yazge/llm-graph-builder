"""wiki_builder — Thin orchestration node that delegates to Celery.

All LLM summarization is performed by the ``workspace.summarize_for_wiki``
Celery task.  This node dispatches one task per document, then writes the
results to the wiki store.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from ..node_registry import NodeType, ExecutionContext, PortDef, register
from ..models import PortDirection

logger = logging.getLogger(__name__)

_DEFAULT_TEMPLATE = """# {title}

## Belge Ozeti
{summary}

## Anahtar Bilgiler
{key_info}

## Ham Metin (ozet)
{text_excerpt}
"""

_TR_CHAR_MAP = str.maketrans({
    "ı": "i", "ğ": "g", "ü": "u", "ş": "s", "ö": "o", "ç": "c",
    "İ": "i", "Ğ": "g", "Ü": "u", "Ş": "s", "Ö": "o", "Ç": "c",
})


def _slugify(text: str) -> str:
    text = text.strip().lower()
    text = text.translate(_TR_CHAR_MAP)
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return text[:80] or "untitled"


@register
class WikiBuilderNode(NodeType):
    type_id = "wiki_builder"
    label = "Wiki Olusturucu"
    description = "Celery worker uzerinden OCR sonuclarindan wiki sayfalari olusturur."
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
                "max_chars": {
                    "type": "integer",
                    "default": 8000,
                    "description": "LLM'e gonderilecek maks karakter",
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
        if not ocr_results:
            return {"wiki_pages": []}

        if not ctx.celery_app:
            logger.error("wiki_builder: celery_app not available in ExecutionContext")
            return {"wiki_pages": [], "error": "celery_app not configured"}

        from ...wiki_store import WikiStore
        from ...knowledge_store import KnowledgeStore
        ks = KnowledgeStore(ctx.pg)
        wiki = WikiStore(ks)

        auto_link = params.get("auto_link", True)
        max_chars = params.get("max_chars", 8000)
        template = params.get("template", "") or _DEFAULT_TEMPLATE

        pages: list[dict[str, Any]] = []
        all_entities: list[str] = []

        for idx, item in enumerate(ocr_results):
            text = item.get("text", "")
            file_name = item.get("file_name", "") or item.get("file_path", f"doc-{idx}")

            if not text.strip():
                pages.append({
                    "file_path": item.get("file_path", ""),
                    "path": "",
                    "status": "skipped",
                    "reason": "empty_text",
                })
                continue

            if ctx.notification_mgr:
                await ctx.notification_mgr.notify(
                    agent_id=ctx.agent_id,
                    event_type="wiki_page_building",
                    data={
                        "run_id": ctx.run_id,
                        "file_name": file_name,
                        "index": idx + 1,
                        "total": len(ocr_results),
                    },
                )

            doc_id = item.get("resource_id", "") or f"{ctx.run_id}-{idx}"

            task = ctx.celery_app.send_task(
                "workspace.summarize_for_wiki",
                kwargs={
                    "doc_id": doc_id,
                    "text": text,
                    "file_name": file_name,
                    "max_chars": max_chars,
                    "agent_id": ctx.agent_id,
                },
                queue="default",
            )

            try:
                result = task.get(timeout=600)
            except Exception as exc:
                logger.error("Celery summarize_for_wiki failed for %s: %s", file_name, exc)
                fallback_title = file_name.rsplit(".", 1)[0] if "." in file_name else file_name
                fallback_path = f"sources/{_slugify(fallback_title)}"
                fallback_content = f"# {fallback_title}\n\n{text[:2000]}"

                try:
                    existing = await wiki.get_page(ctx.agent_id, fallback_path)
                    if existing:
                        await wiki.update_page(ctx.agent_id, fallback_path, fallback_content, source="workflow_wiki_builder_fallback")
                    else:
                        await wiki.create_page(ctx.agent_id, fallback_path, fallback_content, source="workflow_wiki_builder_fallback")
                except Exception:
                    logger.error("Fallback wiki page also failed for %s", file_name)

                pages.append({
                    "file_path": item.get("file_path", ""),
                    "path": fallback_path,
                    "title": fallback_title,
                    "status": "fallback",
                    "error": str(exc),
                })
                continue

            title = result.get("title", file_name)
            summary = result.get("summary", "")
            key_info = result.get("key_info", "")
            entities = result.get("entities_mentioned", [])
            category = result.get("category", "sources")

            all_entities.extend(entities)

            text_excerpt = text[:500] + ("..." if len(text) > 500 else "")

            page_content = template.format(
                title=title,
                summary=summary,
                key_info=key_info,
                text_excerpt=text_excerpt,
            )

            if auto_link and entities:
                link_section = "\n## Ilgili\n"
                for ent in entities:
                    link_section += f"- [[{ent}]]\n"
                page_content += link_section

            page_path = f"{category}/{_slugify(title)}"

            try:
                existing = await wiki.get_page(ctx.agent_id, page_path)
                if existing:
                    await wiki.update_page(ctx.agent_id, page_path, page_content, source="workflow_wiki_builder")
                    page_status = "updated"
                else:
                    await wiki.create_page(ctx.agent_id, page_path, page_content, source="workflow_wiki_builder")
                    page_status = "created"
            except Exception as exc:
                logger.error("Wiki page write failed for %s: %s", file_name, exc)
                page_status = "write_failed"

            pages.append({
                "file_path": item.get("file_path", ""),
                "path": page_path,
                "title": title,
                "summary": summary,
                "entities_mentioned": entities,
                "category": category,
                "status": page_status,
            })

        logger.info(
            "wiki_builder: %d pages created/updated via Celery (%d entities found)",
            sum(1 for p in pages if p.get("status") in ("created", "updated", "fallback")),
            len(all_entities),
        )
        return {"wiki_pages": pages}
