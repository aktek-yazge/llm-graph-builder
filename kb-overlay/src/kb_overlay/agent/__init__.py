"""Agent katmanı — Cypher üretmeden ÖNCE entity'leri canonical_id'ye çözer.

Bileşenler:
  * QueryResolver        — sorgudaki mention'ları çözer
  * cypher_templates     — parametreli, read-only sorgu şablonları
  * system_prompt        — agent LLM'e verilecek guardrail prompt
"""
from .cypher_templates import (
    TEMPLATES,
    CypherTemplate,
    get_template,
    list_template_names,
    render_template,
)
from .query_resolver import QueryEntity, QueryResolver, ResolvedQuery
from .system_prompt import build_system_prompt

__all__ = [
    "CypherTemplate",
    "QueryEntity",
    "QueryResolver",
    "ResolvedQuery",
    "TEMPLATES",
    "build_system_prompt",
    "get_template",
    "list_template_names",
    "render_template",
]
