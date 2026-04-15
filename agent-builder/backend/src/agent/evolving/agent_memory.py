"""
Agent Memory
============

Agent'in ontoloji + domain bilgisinden dinamik system prompt ve
extraction prompt olusturur.

deepagents'taki AGENTS.md'nin PostgreSQL-backed dinamik versiyonu.
KG Prompt Generator'in constructPrompt() fonksiyonunun Python uyarlamasi.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .ontology_model import AgentOntology
from .knowledge_store import KnowledgeStore

logger = logging.getLogger(__name__)


class AgentMemory:
    """Ontoloji ve bilgi deposundan dinamik prompt builder."""

    def __init__(self, store: KnowledgeStore):
        self.store = store

    async def build_system_prompt(self, agent_id: str, mode: str = "plan") -> str:
        """Agent'in tum bilgisinden conversation system prompt olustur.

        Args:
            mode: 'plan' (readonly, planlama) veya 'agent' (uygulama)
        """
        identity = await self.store.load_identity(agent_id)
        ontology = await self.store.load_ontology(agent_id)
        feedbacks = await self.store.get_all(agent_id, "quality_feedback")
        patterns = await self.store.get_all(agent_id, "learned_pattern")

        sections: list[str] = []

        sections.append(f"# {identity.get('name', 'Agent')}")
        if identity.get("purpose"):
            sections.append(f"\n## Amac\n{identity['purpose']}")

        if not ontology.is_empty:
            sections.append(self._ontology_section(ontology))

        if feedbacks:
            lines = []
            for fb in feedbacks:
                val = fb["value"] if isinstance(fb["value"], str) else json.dumps(fb["value"], ensure_ascii=False)
                lines.append(f"- {val}")
            sections.append("\n## Ogrenilen Dersler\n" + "\n".join(lines))

        if patterns:
            lines = []
            for p in patterns:
                val = p["value"] if isinstance(p["value"], str) else json.dumps(p["value"], ensure_ascii=False)
                lines.append(f"- {val}")
            sections.append("\n## Kesfedilen Pattern'ler\n" + "\n".join(lines))

        plan = await self.store.load_plan(agent_id)
        if mode == "plan":
            sections.append(self._plan_mode_prompt(plan))
        else:
            sections.append(self._agent_mode_prompt(plan))

        return "\n".join(sections)

    def build_extraction_prompt(
        self,
        ontology: AgentOntology,
        wiki_context: str = "",
    ) -> str:
        """
        Ontolojiden extraction prompt uret.

        Args:
            ontology: Yapisal ontoloji modeli.
            wiki_context: Wiki sayfalarindan derlenmis zengin baglam.
                         Ornekler, edge case'ler, ogrenilen pattern'ler icerir.

        KG Prompt Generator'in constructPrompt() mantigi.
        AgenticOCR'in system prompt'u olarak kullanilir.
        """
        if ontology.is_empty:
            return ""

        parts: list[str] = []

        parts.append(f"""# SYSTEM PROMPT: KNOWLEDGE GRAPH EXTRACTION

You are an expert Ontology Engineer and Information Extraction specialist for the {ontology.domain} domain.

## MISSION
{ontology.goal}""")

        # Class Hierarchy
        parts.append("\n## CONCEPTUAL HIERARCHY (CLASSES)")
        parts.append("The following entity classes define the nodes of our graph.")
        for entity in ontology.entity_classes:
            header = f"\n### Class: {entity.name}"
            if entity.parent:
                header += f" (Sub-class of: {entity.parent})"
            parts.append(header)
            parts.append(f"- **Definition:** {entity.description}")

            req = entity.required_properties
            opt = entity.optional_properties
            req_str = ", ".join(f"{p.name} [{p.type}]" for p in req) if req else "None"
            opt_str = ", ".join(f"{p.name} [{p.type}]" for p in opt) if opt else "None"
            parts.append(f"- **Required Properties:** {req_str}")
            parts.append(f"- **Optional Properties:** {opt_str}")

        # Relationship Schema
        parts.append("\n## RELATIONSHIP SCHEMA (PREDICATES)")
        parts.append("Define these edges connecting the instances of the classes above.")
        for rel in ontology.relationship_predicates:
            line = f"\n- **{rel.name}**: ({rel.source}) -> ({rel.target})"
            if rel.edge_properties:
                line += f" | Edge Properties: [{', '.join(rel.edge_properties)}]"
            if rel.description:
                line += f" -- {rel.description}"
            parts.append(line)

        # Inference Rules
        if ontology.inference_rules:
            parts.append("\n## LOGICAL ONTOLOGY & INFERENCE RULES")
            parts.append("Apply these logical rules during extraction:")
            for rule in ontology.inference_rules:
                parts.append(f"\n- If {rule.condition}, then {rule.inference} ({rule.rule_type})")

        # Constraints
        if ontology.constraints:
            parts.append("\n## STRUCTURAL CONSTRAINTS")
            parts.append("Strictly adhere to these constraints:")
            for c in ontology.constraints:
                parts.append(f"\n- {c}")

        # Output format
        parts.append("""

## OUTPUT REQUIREMENTS (JSON)
Return ONLY a valid JSON object with the following structure:
```json
{
  "nodes": [
    {"id": "unique_snake_case_id", "class": "ClassName", "properties": {"name": "val", ...}, "evidence": "verbatim source text"}
  ],
  "edges": [
    {"source": "node_id", "predicate": "REL_NAME", "target": "node_id", "properties": {...}, "evidence": "verbatim source text"}
  ]
}
```

IMPORTANT:
- Use snake_case for all node IDs: `{class_lowercase}_{normalized_name}`
- Include "evidence" field with the verbatim text that supports each extraction
- If the same entity appears multiple times, use the same ID
- Only extract entities and relationships defined in the schema above""")

        if wiki_context:
            parts.append(f"""

## DOMAIN KNOWLEDGE (from wiki)

The following section contains learned patterns, examples, edge cases and
domain-specific notes accumulated during previous extraction sessions.
Use this knowledge to improve extraction accuracy.

{wiki_context}""")

        return "\n".join(parts)

    # ─── PRIVATE HELPERS ────────────────────────────────────────────

    def _ontology_section(self, ontology: AgentOntology) -> str:
        lines = [f"\n## Domain: {ontology.domain}"]
        if ontology.goal:
            lines.append(f"Hedef: {ontology.goal}")

        if ontology.entity_classes:
            lines.append("\n### Entity Tipleri")
            for e in ontology.entity_classes:
                parent_note = f" (alt-sinif: {e.parent})" if e.parent else ""
                lines.append(f"- **{e.name}**{parent_note}: {e.description}")
                if e.properties:
                    for p in e.properties:
                        flag = " [zorunlu]" if p.constraint == "required" else ""
                        lines.append(f"  - {p.name} ({p.type}){flag}")

        if ontology.relationship_predicates:
            lines.append("\n### Iliski Tipleri")
            for r in ontology.relationship_predicates:
                lines.append(f"- {r.source} -[{r.name}]-> {r.target}")

        if ontology.constraints:
            lines.append("\n### Kurallar")
            for c in ontology.constraints:
                lines.append(f"- {c}")

        return "\n".join(lines)

    def _plan_mode_prompt(self, plan: dict[str, Any] | None) -> str:
        active_plan_text = ""
        if plan:
            active_plan_text = f"\n### Aktif Plan\n{self.store.plan_to_markdown(plan)}\n"

        return f"""
## MEVCUT MOD: PLAN MODE (Readonly)

Sen su anda PLAN modundasin. Bu modda:
- Kullaniciyla tartisarak plan olusturursun
- `create_plan` ile yapisal plan kaydedersin
- `update_plan_step`, `add_plan_step`, `remove_plan_step` ile plani duzenlersin
- Ontoloji veya dosya islemleri YAPMAZSIN (sadece mevcut durumu sorgulayabilirsin)

Kullanici plani onayladiginda ("uygula", "onayla", "basla") sistem Agent Mode'a gecer.
{active_plan_text}
## Sen Kimsin

Knowledge Graph Builder agentisin. Yapilandirilmamis belgelerden (PDF, gorsel, metin)
yapilandirilmis bilgi grafi olusturuyorsun. Kullaniciyla birlikte calisarak ontoloji
tasarliyorsun.

## Dosya Geldiginde

1. `create_plan` ile plan olustur:
   - Adim 1: Belgeyi oku (OCR)
   - Adim 2: Icerigi ozetle
   - Adim 3: Kullaniciya ne cikarilacagini sor
   - Adim 4: Kullanicinin cevabini bekle
   - Adim 5: Entity/relationship oner
   - Adim 6: Onay al
   - Adim 7: Ontolojiyi kaydet
2. Plani markdown olarak sun
3. Kullanici tartissin ve duzeltsin
4. Kullanici "uygula" dediginde plan onaylanir

## Kullanilabilir Tool'lar (Plan Mode)

- Plan: `create_plan`, `update_plan_step`, `add_plan_step`, `remove_plan_step`, `get_current_plan`
- Sorgulama: `get_current_ontology`, `get_wiki_page`, `search_wiki`, `get_ocr_text`, `list_pending_discoveries`

## Prensipler

1. ONCE PLAN: Oncelik plan olusturmak
2. TARTIS: Kullaniciyla plani tartis, gerekirse duzelt
3. AKSIYON ALMA: Plan onaylanmadan ontoloji/extraction islemleri YAPMA
4. SORU SOR: Belirsiz adimlar icin kullaniciya sor"""

    def _agent_mode_prompt(self, plan: dict[str, Any] | None) -> str:
        active_plan_text = ""
        if plan:
            active_plan_text = f"\n### Uygulanacak Plan\n{self.store.plan_to_markdown(plan)}\n"

        return f"""
## MEVCUT MOD: AGENT MODE (Uygulama)

Sen su anda AGENT modundasin. Onaylanan plani uyguluyorsun.
Her adimi tamamladiginda `write_todos` ile ilerlemeyi guncelle.
{active_plan_text}
## Sen Kimsin

Knowledge Graph Builder agentisin. Yapilandirilmamis belgelerden (PDF, gorsel, metin)
yapilandirilmis bilgi grafi olusturuyorsun.

## Calisma Akisi

1. Onaylanan planin adimlarini sirayla uygula
2. Her adimi tamamladiginda kullaniciya bilgi ver
3. Belirsizlik varsa sor, ama gereksiz yere durma
4. `ocr_and_analyze` ile belge oku, `get_ocr_text` ile tam metni al
5. Ontoloji islemleri: `add_entity_class`, `add_relationship_predicate`, vb.
6. Test: `test_extraction_on_sample` ile dogrula
7. Kayit: `save_as_skill` ile skill olarak kaydet

## Tool Ozeti

- Ontoloji: `add_entity_class`, `add_relationship_predicate`, `add_inference_rule`, `add_constraint`, `set_domain_info`, `get_current_ontology`, `remove_entity_class`, `remove_relationship`
- OCR: `ocr_and_analyze`, `get_ocr_text`, `extract_images_from_pdf`, `run_ocr`
- Extraction: `run_extraction`, `run_full_pipeline`, `test_extraction_on_sample`
- Skill/Batch: `save_as_skill`, `start_batch_processing`, `get_batch_progress`
- Wiki: `create_wiki_page`, `update_wiki_page`, `get_wiki_page`, `search_wiki`, `add_learned_pattern`
- Plan: `get_current_plan` (sadece goruntuleme)

## Prensipler

1. PLAN TAKIP: Onaylanan plani sirayla uygula
2. ONCE SOR: Kullanici ne istedigini soylemedikce ontoloji onerme
3. TEST ET: Ontoloji degisikliklerini test_extraction_on_sample ile dogrula
4. EVIDENCE: Cikarilan bilgiler icin kaynak metni belirt
5. BILDIR: Her adim tamamlandiginda kullaniciya bildir"""
