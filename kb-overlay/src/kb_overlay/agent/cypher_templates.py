"""Parametreli Cypher şablonları — agent'ın 'serbest' Cypher yazmasını
azaltmak için sık kullanılan sorguları hazır şablon olarak verir.

Tüm şablonlar:
  * SADECE okur (MATCH/RETURN). MERGE/CREATE/SET içermez.
  * canonical_id parametre olarak verilir; raw isim kabul etmez.
  * LIMIT içerir (kontrol).

Agent prompt'ında bunlardan hangisinin uygun olduğu seçilir; uygun şablon
yoksa fallback olarak `Neo4jStore.execute_cypher` (read-only guard) kullanılır.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True, frozen=True)
class CypherTemplate:
    name: str
    description: str
    cypher: str
    required_params: tuple[str, ...]


TEMPLATES: dict[str, CypherTemplate] = {
    "entity_by_id": CypherTemplate(
        name="entity_by_id",
        description="Bir canonical entity'nin tüm alanlarını döndür.",
        cypher="""
        MATCH (e:Entity {canonical_id: $canonical_id})
        RETURN e.canonical_id   AS canonical_id,
               e.canonical_name AS canonical_name,
               e.entity_type    AS entity_type,
               e.aliases        AS aliases,
               e.metadata_json  AS metadata_json
        """,
        required_params=("canonical_id",),
    ),
    "entity_relations_outgoing": CypherTemplate(
        name="entity_relations_outgoing",
        description="Bir entity'nin tüm DIŞA giden ilişkilerini döndür.",
        cypher="""
        MATCH (e:Entity {canonical_id: $canonical_id})-[r]->(o:Entity)
        WHERE NOT type(r) IN ['MENTIONED_IN']
        RETURN type(r)               AS predicate,
               r.original_predicate  AS original_predicate,
               o.canonical_id        AS object_id,
               o.canonical_name      AS object_name,
               o.entity_type         AS object_type,
               r.confidence          AS confidence,
               r.evidence_text       AS evidence,
               r.source_doc_id       AS doc_id
        ORDER BY r.confidence DESC
        LIMIT $limit
        """,
        required_params=("canonical_id",),
    ),
    "entity_relations_incoming": CypherTemplate(
        name="entity_relations_incoming",
        description="Bir entity'ye GELEN tüm ilişkileri döndür.",
        cypher="""
        MATCH (s:Entity)-[r]->(e:Entity {canonical_id: $canonical_id})
        WHERE NOT type(r) IN ['MENTIONED_IN']
        RETURN type(r)               AS predicate,
               r.original_predicate  AS original_predicate,
               s.canonical_id        AS subject_id,
               s.canonical_name      AS subject_name,
               s.entity_type         AS subject_type,
               r.confidence          AS confidence,
               r.evidence_text       AS evidence,
               r.source_doc_id       AS doc_id
        ORDER BY r.confidence DESC
        LIMIT $limit
        """,
        required_params=("canonical_id",),
    ),
    "ceo_of_company": CypherTemplate(
        name="ceo_of_company",
        description="Bir şirketin CEO'sunu (kişi) döndür.",
        cypher="""
        MATCH (p:Person)-[r:CEO_OF]->(c:Company {canonical_id: $canonical_id})
        RETURN p.canonical_id   AS person_id,
               p.canonical_name AS person_name,
               r.evidence_text  AS evidence,
               r.source_doc_id  AS doc_id,
               r.confidence     AS confidence
        ORDER BY r.confidence DESC
        LIMIT $limit
        """,
        required_params=("canonical_id",),
    ),
    "company_employees": CypherTemplate(
        name="company_employees",
        description="Bir şirkette çalışanların (WORKS_AT) listesi.",
        cypher="""
        MATCH (p:Person)-[r:WORKS_AT]->(c:Company {canonical_id: $canonical_id})
        RETURN p.canonical_id   AS person_id,
               p.canonical_name AS person_name,
               r.evidence_text  AS evidence,
               r.source_doc_id  AS doc_id,
               r.confidence     AS confidence
        ORDER BY r.confidence DESC
        LIMIT $limit
        """,
        required_params=("canonical_id",),
    ),
    "person_employer": CypherTemplate(
        name="person_employer",
        description="Bir kişinin çalıştığı şirket(ler).",
        cypher="""
        MATCH (p:Person {canonical_id: $canonical_id})-[r:WORKS_AT|CEO_OF|CFO_OF|CTO_OF|CHAIRMAN_OF|BOARD_MEMBER_OF|FOUNDER_OF]->(c:Company)
        RETURN c.canonical_id   AS company_id,
               c.canonical_name AS company_name,
               type(r)          AS role,
               r.evidence_text  AS evidence,
               r.source_doc_id  AS doc_id,
               r.confidence     AS confidence
        ORDER BY r.confidence DESC
        LIMIT $limit
        """,
        required_params=("canonical_id",),
    ),
    "subsidiaries_of": CypherTemplate(
        name="subsidiaries_of",
        description="Bir şirketin yan kuruluşları.",
        cypher="""
        MATCH (sub:Company)-[r:SUBSIDIARY_OF]->(parent:Company {canonical_id: $canonical_id})
        RETURN sub.canonical_id   AS subsidiary_id,
               sub.canonical_name AS subsidiary_name,
               r.evidence_text    AS evidence,
               r.source_doc_id    AS doc_id,
               r.confidence       AS confidence
        ORDER BY r.confidence DESC
        LIMIT $limit
        """,
        required_params=("canonical_id",),
    ),
    "documents_mentioning": CypherTemplate(
        name="documents_mentioning",
        description="Bir entity'nin geçtiği belgeler.",
        cypher="""
        MATCH (e:Entity {canonical_id: $canonical_id})-[m:MENTIONED_IN]->(d:Document)
        RETURN d.doc_id    AS doc_id,
               d.title     AS title,
               m.surface   AS surface,
               m.span_start AS span_start,
               m.span_end   AS span_end,
               m.confidence AS confidence
        ORDER BY d.created_at DESC
        LIMIT $limit
        """,
        required_params=("canonical_id",),
    ),
    "co_mentioned_entities": CypherTemplate(
        name="co_mentioned_entities",
        description="Bir entity ile aynı belgelerde geçen diğer entity'ler.",
        cypher="""
        MATCH (e:Entity {canonical_id: $canonical_id})-[:MENTIONED_IN]->(d:Document)<-[:MENTIONED_IN]-(other:Entity)
        WHERE other.canonical_id <> $canonical_id
        RETURN other.canonical_id   AS canonical_id,
               other.canonical_name AS canonical_name,
               other.entity_type    AS entity_type,
               COUNT(DISTINCT d)    AS shared_doc_count
        ORDER BY shared_doc_count DESC
        LIMIT $limit
        """,
        required_params=("canonical_id",),
    ),
}


def list_template_names() -> list[str]:
    return sorted(TEMPLATES.keys())


def get_template(name: str) -> CypherTemplate:
    if name not in TEMPLATES:
        raise KeyError(f"Bilinmeyen şablon: {name}. Mevcutlar: {list_template_names()}")
    return TEMPLATES[name]


def render_template(name: str, params: dict[str, Any], *, default_limit: int = 25) -> tuple[str, dict]:
    """Şablonu döndürür ve LIMIT'in yer aldığı params'ı tamamlar."""
    tpl = get_template(name)
    missing = [p for p in tpl.required_params if p not in params]
    if missing:
        raise ValueError(f"{name}: eksik parametre(ler): {missing}")
    full = dict(params)
    full.setdefault("limit", default_limit)
    return tpl.cypher, full
