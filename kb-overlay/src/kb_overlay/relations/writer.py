"""Relation writer — extractor çıktısını Neo4j'ye MERGE eder.

DocumentExtractor (mention'ları çözer) + RelationExtractor (ilişkileri çıkarır)
+ Neo4jStore (graph'ı yazar) hepsini birleştiren orchestrator.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from ..discovery import DocumentExtractor, ExtractionResult, ResolvedMention
from ..neo4j_store import GraphRelation, Neo4jStore
from .extractor import RelationExtractor, ResolvedEntityInput
from .schemas import RelationExtractionResponse

logger = logging.getLogger(__name__)

# RelationExtractor'ın notes alanına yazdığı telemetri formatı
_DISCARDED_RE = re.compile(r"discarded=(\d+)")


@dataclass(slots=True)
class IngestionResult:
    doc_id: str
    extraction: ExtractionResult
    relations: RelationExtractionResponse
    nodes_written: int = 0
    relations_written: int = 0
    mentions_linked: int = 0
    errors: list[str] = field(default_factory=list)

    # Phase 2: schema validation diagnostics
    discarded_relations: int = 0
    """Schema validation'dan geçemediği için drop edilen LLM relation sayısı."""

    validation_recovered: bool = False
    """True ise retry ile kurtarıldı; quality monitoring için."""

    @property
    def has_validation_warnings(self) -> bool:
        return self.discarded_relations > 0 or self.validation_recovered


class RelationPipeline:
    """Belge → mentions → ilişkiler → Neo4j graph."""

    def __init__(
        self,
        *,
        document_extractor: DocumentExtractor,
        relation_extractor: Optional[RelationExtractor] = None,
        neo4j_store: Optional[Neo4jStore] = None,
        write_mentions_to_neo4j: bool = True,
        write_entities_to_neo4j: bool = True,
        min_mention_confidence_for_relation: float = 0.5,
    ):
        self.doc_ex = document_extractor
        self.rel_ex = relation_extractor
        self.graph = neo4j_store
        self.write_mentions = write_mentions_to_neo4j
        self.write_entities = write_entities_to_neo4j
        self.min_conf = min_mention_confidence_for_relation

    def ingest(
        self,
        text: str,
        *,
        doc_id: str,
        title: Optional[str] = None,
    ) -> IngestionResult:
        # 1. Mention çıkarımı + resolution
        extraction = self.doc_ex.extract(text, doc_id=doc_id, title=title)
        result = IngestionResult(doc_id=doc_id, extraction=extraction, relations=RelationExtractionResponse())

        if self.graph is None:
            logger.info("Neo4j yok; sadece SQLite tarafına yazıldı (extraction).")
            return result

        # 2. Document node + entity node'ları MERGE
        try:
            self.graph.upsert_document(doc_id, title=title)
        except Exception as exc:  # pragma: no cover
            result.errors.append(f"upsert_document: {exc}")
            logger.error("Document upsert hatası: %s", exc)

        if self.write_entities:
            for cid in self._unique_canonical_ids(extraction.mentions):
                ent = self.doc_ex.store.get_entity(cid)
                if not ent:
                    continue
                aliases = [a.surface_form for a in self.doc_ex.store.get_aliases_for(cid)]
                try:
                    self.graph.upsert_entity(ent, aliases=aliases)
                    result.nodes_written += 1
                except Exception as exc:
                    result.errors.append(f"upsert_entity({cid}): {exc}")

        if self.write_mentions:
            for m in extraction.mentions:
                if not m.canonical_id:
                    continue
                try:
                    self.graph.link_mention(
                        canonical_id=m.canonical_id,
                        doc_id=doc_id,
                        span_start=m.span_start,
                        span_end=m.span_end,
                        surface=m.surface,
                        confidence=m.confidence,
                    )
                    result.mentions_linked += 1
                except Exception as exc:  # pragma: no cover
                    result.errors.append(f"link_mention({m.canonical_id}): {exc}")

        # 3. İlişki çıkarımı (LLM)
        if self.rel_ex is not None:
            entities_for_llm = self._build_llm_inputs(extraction.mentions)
            if entities_for_llm:
                rel_resp = self.rel_ex.extract(text, entities_for_llm)
                result.relations = rel_resp
                # Phase 2: extractor.notes alanından validation telemetri çıkar
                _populate_validation_metrics(result, rel_resp.notes)
                for r in rel_resp.relations:
                    try:
                        self.graph.upsert_relation(
                            GraphRelation(
                                subject_id=r.subject_id,
                                predicate=r.predicate,
                                object_id=r.object_id,
                                source_doc_id=doc_id,
                                evidence_text=r.evidence_text,
                                confidence=r.confidence,
                                confidence_label=r.confidence_label,
                                span_start=r.span_start,
                                span_end=r.span_end,
                            )
                        )
                        result.relations_written += 1
                    except Exception as exc:
                        result.errors.append(
                            f"upsert_relation({r.subject_id}->{r.object_id}): {exc}"
                        )

        return result

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _unique_canonical_ids(mentions: list[ResolvedMention]) -> set[str]:
        return {m.canonical_id for m in mentions if m.canonical_id}

    def _build_llm_inputs(
        self,
        mentions: list[ResolvedMention],
    ) -> list[ResolvedEntityInput]:
        """Resolved mention'lardan tek-canonical-bir-giriş listesi üret.

        Aynı canonical_id'ye düşen birden fazla mention varsa, ilk geçen
        (en küçük span_start) kullanılır — LLM'in entity listesinde tekrar
        olmasın.
        """
        out: dict[str, ResolvedEntityInput] = {}
        for m in mentions:
            if not m.canonical_id or m.confidence < self.min_conf:
                continue
            entity = self.doc_ex.store.get_entity(m.canonical_id)
            if not entity:
                continue
            existing = out.get(m.canonical_id)
            if existing is not None and existing.span_start <= m.span_start:
                continue
            out[m.canonical_id] = ResolvedEntityInput(
                canonical_id=m.canonical_id,
                canonical_name=entity.canonical_name,
                entity_type=entity.entity_type,
                surface=m.surface,
                span_start=m.span_start,
                span_end=m.span_end,
                confidence_label=m.confidence_label,
            )
        return sorted(out.values(), key=lambda e: e.span_start)


def _populate_validation_metrics(result: IngestionResult, notes: Optional[str]) -> None:
    """RelationExtractor'ın notes alanından discard/recovery telemetri çıkar
    ve IngestionResult sayaçlarına yaz.

    notes formatı (extractor._merge_notes ile noktalı virgül ayrılmış):
      - "salvaged_after_validation_error;discarded=N"
      - "recovered_after_retry"
      - "recovered_partial_after_retry;discarded=N"
      - "validation_error_after_retry: ..."
      - "unknown_canonical_dropped=N"  (validation değil, ek info)
    """
    if not notes:
        return

    # Toplam discarded sayacı (salvaged + recovered_partial + unknown_canonical)
    total_discarded = 0
    for match in _DISCARDED_RE.finditer(notes):
        try:
            total_discarded += int(match.group(1))
        except (TypeError, ValueError):
            continue
    # unknown_canonical_dropped da discard sayar
    udrop_match = re.search(r"unknown_canonical_dropped=(\d+)", notes)
    if udrop_match:
        try:
            total_discarded += int(udrop_match.group(1))
        except (TypeError, ValueError):
            pass

    result.discarded_relations = total_discarded
    result.validation_recovered = (
        "recovered_after_retry" in notes
        or "recovered_partial_after_retry" in notes
        or "salvaged_after_validation_error" in notes
    )

    if "validation_error_after_retry" in notes:
        result.errors.append(f"validation_failed: {notes}")
