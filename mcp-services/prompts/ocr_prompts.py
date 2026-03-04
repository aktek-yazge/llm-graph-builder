"""
OCR MCP Prompts - agentic_ocr and gemini_ocr prompt'larindan.

Registers OCR-related prompts: unified OCR system, goal-driven OCR,
entity extraction, gemini extraction, and document type detection.
"""

from pathlib import Path
from fastmcp.server import FastMCP

TEMPLATES_DIR = Path(__file__).parent / "templates"


def _read_template(name: str) -> str:
    path = TEMPLATES_DIR / "ocr" / name
    if path.exists():
        return path.read_text(encoding="utf-8")
    return f"[Template not found: ocr/{name}]"


def register_ocr_prompts(mcp: FastMCP):
    """Register all OCR-domain prompts on the FastMCP server."""

    @mcp.prompt("unified_ocr_system")
    def unified_ocr_system() -> str:
        """TSG (Ticaret Sicil Gazetesi) unified OCR system prompt.
        Defines expert identity, chunking rules, output format, and ID conventions."""
        return _read_template("unified_ocr_system.md")

    @mcp.prompt("goal_driven_ocr")
    def goal_driven_ocr(
        target_company: str,
        page_number: int = 1,
        total_pages: int = 1,
        continuation_context: str = "",
        graph_schema: str = "",
    ) -> str:
        """Page-level OCR extraction prompt for a target company.
        Fills in page info and graph schema for structured extraction."""
        template = _read_template("unified_ocr.md")
        return template.format(
            target_company=target_company,
            page_number=page_number,
            total_pages=total_pages,
            continuation_context=continuation_context,
            graph_schema=graph_schema,
        )

    @mcp.prompt("gemini_extraction")
    def gemini_extraction(file_name: str) -> str:
        """Target company extraction from filename and OCR text.
        Used to identify which company an OCR document belongs to."""
        template = _read_template("gemini_extraction.md")
        return template.replace("{{FILE_NAME}}", file_name)

    @mcp.prompt("entity_extraction")
    def entity_extraction(file_name: str, document_content: str = "") -> str:
        """Entity extraction prompt for structured knowledge graph population.
        Extracts companies, persons, meetings, and their relationships."""
        template = _read_template("entity_extraction.md")
        return template.format(file_name=file_name, document_content=document_content)

    @mcp.prompt("document_type_detection")
    def document_type_detection(page_text: str = "") -> str:
        """Detect document type: MAIN_POLICY, ENDORSEMENT, RENEWAL, or CANCELLATION."""
        template = _read_template("document_type_detection.md")
        return template.format(page_text=page_text)

    @mcp.prompt("agentic_ocr")
    def agentic_ocr(file_name: str) -> str:
        """Insurance policy OCR prompt. Basic page-level OCR with markdown output."""
        template = _read_template("agentic_ocr.md")
        return template.format(file_name=file_name)
