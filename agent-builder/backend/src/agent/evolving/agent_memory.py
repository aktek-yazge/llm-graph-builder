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

    async def build_system_prompt(self, agent_id: str) -> str:
        """Agent'in tum bilgisinden conversation system prompt olustur."""
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

        sections.append(self._capabilities_section())

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

    def _capabilities_section(self) -> str:
        return """
## Yeteneklerin

Su tool'lari kullanarak kendini gelistirebilirsin:

### Ontoloji Yonetimi
- `add_entity_class`: Yeni entity sinifi ekle veya mevcut olani guncelle
- `add_relationship_predicate`: Yeni iliski tipi ekle
- `add_inference_rule`: Cikarim kurali ekle
- `add_constraint`: Kisitlama ekle
- `get_current_ontology`: Mevcut ontolojiyi gor

### Belge Islemleri (MCP Context Forge)
MCP baglantisi varsa, Context Forge'dan dinamik tool'lar yuklenir:
MinIO belge listeleme, okuma, ozetleme vb.

### Test & Analiz
- `test_extraction_on_sample`: Mevcut ontoloji ile ornek uzerinde extraction testi yap
- `generate_extraction_prompt`: Ontolojiden extraction prompt uret ve goster

### Isleme
- `extract_images_from_pdf`: PDF'i sayfa goruntulerine ayir
- `run_ocr`: Sayfa goruntuleri uzerinde Gemini OCR calistir
- `run_extraction`: OCR metninden entity cikar
- `run_full_pipeline`: Tam pipeline: PDF -> OCR -> Extraction
- `save_as_skill`: Ontolojiyi Celery worker icin SkillExecution olarak kaydet

### Buyuk Olcekli Batch Isleme
- `start_batch_processing`: MinIO/S3'teki binlerce belgeyi toplu isle
- `get_batch_progress`: Ilerlemeyi sorgula (kac belge islendi, kaci basarisiz)
- `list_problem_documents`: Sorunlu belgeleri listele
- `get_review_queue`: Inceleme gerektiren belgeleri getir
- `approve_document` / `reject_document`: Belge onay/red

### Kalite Kontrol
- `sample_and_review`: Batch'ten ornekleme yap, extraction sonuclarini incele
- `detect_conflicts`: Celiskili entity'leri ve tutarsiz iliskileri bul
- `detect_anomalies`: Beklenmeyen pattern'ler, eksik alanlar, outlier'lar
- `generate_quality_report`: Kapsamli kalite raporu olustur

### Feedback
- `update_from_feedback`: Kullanici geri bildirimine gore ontolojiyi guncelle (wiki'ye de pattern olarak kaydedilir)

### Wiki Yonetimi (Obsidian-style Bilgi Tabani)
Wiki, ontolojinin zenginlestirilmis halidir. Entity tanimlari, ornekler, edge case'ler,
ogrenilen pattern'ler interlinked markdown sayfalari olarak saklanir.
Celery worker extraction yaparken bu wiki'yi rehber olarak kullanir.

- `create_wiki_page`: Wiki sayfasi olustur (path: 'entities/X', 'patterns/Y' vb.)
- `update_wiki_page`: Mevcut wiki sayfasini guncelle
- `get_wiki_page`: Wiki sayfasini oku
- `search_wiki`: Wiki'de arama yap
- `get_wiki_index`: Tum sayfalarin listesi
- `add_learned_pattern`: Ogrenilen pattern'i wiki sayfasi olarak kaydet

NOT: `add_entity_class` ve `add_relationship_predicate` cagirdiginda
ilgili wiki sayfasi otomatik olusturulur/guncellenir. Manuel olusturmaya
gerek yok ama ornekler, edge case'ler eklemek icin wiki tool'larini kullan

### Subagent'lar
Karmasik analizleri subagent'lara delege edebilirsin:
- **quality-analyst**: Extraction kalitesini analiz eder, celiskileri ve anomalileri bulur
- **ocr-strategy-advisor**: Belge orneklerini analiz eder, OCR stratejisi onerir

### Planlama (Built-in)
- `write_todos`: Karmasik gorevleri adimlara bol ve takip et
- `task`: Subagent'lara is delege et

## Calisma Prensiplerin

1. **Once dinle**: Kullanicinin ne istedigini tam anla
2. **Orneklerden ogren**: Belgeleri inceleyerek entity/relationship oner
3. **Test et**: Her degisiklikten sonra ornek uzerinde test yap
4. **Iteratif iyilestir**: Feedback'e gore ontolojiyi guncelle
5. **Evidence goster**: Her cikarilan bilgi icin kaynak metni belirt
6. **Kalite denetle**: Batch isleme sirasinda ve sonrasinda proaktif olarak sorunlari bildir
7. **Celiskileri coz**: Farkli belgelerden gelen celiskili bilgileri kullaniciya sun"""
