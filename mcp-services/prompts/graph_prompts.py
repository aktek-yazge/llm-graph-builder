"""
Graph Agent MCP Prompts - react_agent.py prompt'larindan turkce.

Registers domain-specific prompts (sigorta, akkok_sicil) and
shared Cypher query guides as MCP prompts on the given server.
"""

import os
from pathlib import Path
from fastmcp.server import FastMCP

TEMPLATES_DIR = Path(__file__).parent / "templates"


def _read_template(domain: str, name: str) -> str:
    path = TEMPLATES_DIR / domain / name
    if path.exists():
        return path.read_text(encoding="utf-8")
    return f"[Template not found: {domain}/{name}]"


def register_graph_prompts(mcp: FastMCP):
    """Register all graph-domain prompts on the FastMCP server."""

    @mcp.prompt("sigorta_system_prompt")
    def sigorta_system_prompt(schema_info: str = "") -> str:
        """Full system prompt for the insurance (sigorta) domain agent."""
        base = _read_template("sigorta", "system_base.md")
        tools = _read_template("sigorta", "tool_usage.md")
        content = _read_template("sigorta", "content.md")
        return f"{base}\n\n{tools}\n\n{content}\n\n{schema_info}"

    @mcp.prompt("akkok_sicil_system_prompt")
    def akkok_sicil_system_prompt(schema_info: str = "") -> str:
        """Full system prompt for the Akkok trade registry domain agent."""
        base = _read_template("akkok_sicil", "system_base.md")
        tools = _read_template("akkok_sicil", "tool_usage.md")
        thinking = _read_template("akkok_sicil", "thinking_guide.md")
        return f"{base}\n\n{tools}\n\n{thinking}\n\n{schema_info}"

    @mcp.prompt("cypher_tool_usage")
    def cypher_tool_usage() -> str:
        """Cypher tool usage guide for graph agents."""
        return _read_template("sigorta", "tool_usage.md")

    @mcp.prompt("cypher_rules")
    def cypher_rules() -> str:
        """Shared Cypher query rules and best practices."""
        return _read_template("shared", "cypher_rules.md")

    @mcp.prompt("ontology_driven_agent")
    def ontology_driven_agent() -> str:
        """Full ontology-driven insurance graph agent prompt."""
        return _read_template("shared", "ontology_driven_agent.md")

    @mcp.prompt("thinking_guide")
    def thinking_guide(domain: str = "general") -> str:
        """Domain-specific reasoning guide for the agent."""
        if domain == "akkok_sicil":
            return _read_template("akkok_sicil", "thinking_guide.md")
        return (
            "## Reasoning Guide\n\n"
            "1. Understand the question\n"
            "2. Plan which nodes/relationships to query\n"
            "3. Execute queries (Cypher or semantic)\n"
            "4. Validate results\n"
            "5. Answer in Turkish, clearly\n"
        )
