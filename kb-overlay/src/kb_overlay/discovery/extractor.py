"""DocumentExtractor — gazetteer + NER + resolver birleşimi.

Bir belge metnini alır, tüm mention'ları (bilinen + yeni aday) çıkarır,
canonical_id'ye bağlar, sözlüğe yeni varyasyonları yazar, çözülemeyen
adayları review kuyruğuna gönderir.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Optional

from ..confidence import ConfidenceLabel
from ..dictionary import AliasSource, AliasStore, EntityType
from ..gazetteer import GazetteerSpotter, SpottedMention
from ..ingestion import normalize_ocr_text
from ..normalize import normalize
from ..resolver import CascadingResolver, ResolutionResult, ResolutionStage
from ..resolver.cascading import label_from_stage
from .ner import Mention, NerBackend

logger = logging.getLogger(__name__)


# DocumentExtractor'a özgü stage stringleri (ResolutionStage enum dışında).
# Bunlar resolver'dan değil, gazetteer ve auto-create yollarından geliyor.
_EXTRACTOR_STAGE_LABEL_MAP: dict[str, ConfidenceLabel] = {
    "gazetteer_hit": ConfidenceLabel.EXTRACTED,  # bilinen yazım = deterministik
    "auto_created": ConfidenceLabel.INFERRED,    # NER yeni gördü, doğrulanmamış
}


def _label_from_extractor_stage(stage: str, *, is_ambiguous: bool = False) -> ConfidenceLabel:
    """ResolvedMention.stage stringinden ConfidenceLabel türet.

    Önce ResolutionStage enum'una çevirmeyi dener (resolver'dan gelen);
    eşleşmezse extractor-özel mapping'e bakar; o da yoksa INFERRED default.
    """
    if is_ambiguous:
        return ConfidenceLabel.AMBIGUOUS
    try:
        return label_from_stage(ResolutionStage(stage), is_ambiguous=False)
    except ValueError:
        return _EXTRACTOR_STAGE_LABEL_MAP.get(stage, ConfidenceLabel.INFERRED)


@dataclass(slots=True)
class ResolvedMention:
    surface: str
    span_start: int
    span_end: int
    canonical_id: Optional[str]
    entity_type_guess: str
    stage: str
    confidence: float
    source_backend: str  # 'gazetteer' | NER backend adı
    is_new: bool = False
    is_ambiguous: bool = False
    notes: Optional[str] = None

    @property
    def confidence_label(self) -> ConfidenceLabel:
        """Stage + is_ambiguous'tan türetilen graphify-style etiket.

        EXTRACTED: gazetteer_hit, exact_strict, exact_loose
        INFERRED:  fuzzy, vector, new_candidate, auto_created
        AMBIGUOUS: is_ambiguous=True (stage ne olursa olsun)
        """
        return _label_from_extractor_stage(self.stage, is_ambiguous=self.is_ambiguous)


@dataclass(slots=True)
class ExtractionResult:
    doc_id: str
    text_length: int
    mentions: list[ResolvedMention] = field(default_factory=list)
    new_candidates: list[ResolvedMention] = field(default_factory=list)
    ambiguous: list[ResolvedMention] = field(default_factory=list)
    counts_by_stage: dict[str, int] = field(default_factory=dict)
    counts_by_type: dict[str, int] = field(default_factory=dict)


class DocumentExtractor:
    """Belge → mention'lar pipeline'ı."""

    def __init__(
        self,
        store: AliasStore,
        resolver: CascadingResolver,
        *,
        ner_backend: NerBackend,
        gazetteer_spotter: Optional[GazetteerSpotter] = None,
        review_queue=None,  # ReviewQueue | None — circular import için lazy type
        auto_create_new_entities: bool = True,
        auto_create_min_confidence: float = 0.6,
        ocr_normalize: bool = True,
    ):
        self.store = store
        self.resolver = resolver
        self.ner = ner_backend
        self.gazetteer = gazetteer_spotter
        self.review_queue = review_queue
        self.auto_create_new_entities = auto_create_new_entities
        self.auto_create_min_confidence = auto_create_min_confidence
        # OCR çıktılarındaki yapay satır kırılmalarını birleştir
        # (örn. "Aksa Akrilik Kimya Sanayii\nAnonim Şirketi" → tek satır).
        # Tüm OCR belgeleri için sistemik düzeltme; opt-out için False ver.
        self.ocr_normalize = ocr_normalize

    # ------------------------------------------------------------------ public

    def extract(
        self,
        text: str,
        *,
        doc_id: str,
        title: Optional[str] = None,
    ) -> ExtractionResult:
        if not text:
            return ExtractionResult(doc_id=doc_id, text_length=0)

        # En başta normalize: span'lar tüm pipeline'da normalized text'e göre
        # tutarlı kalsın. SHA da normalized text üzerinden alınır → aynı
        # belgenin iki farklı OCR satır kırılması varyasyonu aynı hash'e düşer.
        if self.ocr_normalize:
            text = normalize_ocr_text(text)

        sha = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()
        self.store.register_document(doc_id, title=title, sha256=sha)

        # 1. Gazetteer pass — bilinen mention'lar (yüksek precision)
        gaz_hits: list[SpottedMention] = []
        if self.gazetteer is not None:
            try:
                gaz_hits = self.gazetteer.spot(text)
            except Exception as exc:  # pragma: no cover
                logger.warning("Gazetteer spotting failed: %s", exc)

        # 2. NER pass — tüm aday mention'lar
        ner_mentions: list[Mention] = []
        try:
            ner_mentions = self.ner.extract(text)
        except Exception as exc:  # pragma: no cover
            logger.warning("NER extraction failed: %s", exc)

        # 3. Span merge: gazetteer her zaman kazanır; NER örtüşmeyen ekler
        merged = self._merge_spans(gaz_hits, ner_mentions)

        # 4. Her mention'ı resolve et
        result = ExtractionResult(doc_id=doc_id, text_length=len(text))
        for span_kind, payload in merged:
            if span_kind == "gazetteer":
                hit: SpottedMention = payload
                resolved = ResolvedMention(
                    surface=hit.surface,
                    span_start=hit.span_start,
                    span_end=hit.span_end,
                    canonical_id=hit.canonical_id,
                    entity_type_guess=hit.entity_type,
                    stage="gazetteer_hit",
                    confidence=hit.confidence,
                    source_backend="gazetteer",
                    is_new=False,
                )
                # gazetteer_hit alias'ını da provenance ile güncelle
                self.store.add_alias(
                    hit.surface,
                    hit.canonical_id,
                    kind=hit.entity_type if hit.entity_type in ("company", "person") else "any",
                    confidence=hit.confidence,
                    source=AliasSource.GAZETTEER_HIT,
                    source_doc_id=doc_id,
                    source_span=(hit.span_start, hit.span_end),
                )
                self._record(result, resolved)
                continue

            mention: Mention = payload
            kind_for_lookup = (
                mention.entity_type_guess
                if mention.entity_type_guess in ("company", "person")
                else "any"
            )
            entity_type_filter = (
                mention.entity_type_guess
                if mention.entity_type_guess in ("company", "person", "organization", "location", "product")
                else None
            )
            context = text[max(0, mention.span_start - 200):mention.span_end + 200]

            res: ResolutionResult = self.resolver.resolve(
                mention.surface,
                kind=kind_for_lookup,
                entity_type=entity_type_filter,
                context_text=context,
                source_doc_id=doc_id,
                source_span=(mention.span_start, mention.span_end),
            )

            resolved = ResolvedMention(
                surface=mention.surface,
                span_start=mention.span_start,
                span_end=mention.span_end,
                canonical_id=res.canonical_id,
                entity_type_guess=mention.entity_type_guess,
                stage=res.stage.value,
                confidence=res.confidence,
                source_backend=mention.backend,
                is_new=res.is_new,
                is_ambiguous=res.is_ambiguous,
                notes=res.notes,
            )

            # Yeni aday: auto-create veya review kuyruğuna gönder
            if res.is_new and self.auto_create_new_entities:
                cid = self._auto_create_new_entity(
                    mention=mention,
                    doc_id=doc_id,
                )
                if cid:
                    resolved.canonical_id = cid
                    resolved.stage = "auto_created"
                    resolved.confidence = self.auto_create_min_confidence

            self._record(result, resolved)

        return result

    # ------------------------------------------------------------------ helpers

    def _record(self, result: ExtractionResult, m: ResolvedMention) -> None:
        result.mentions.append(m)
        result.counts_by_stage[m.stage] = result.counts_by_stage.get(m.stage, 0) + 1
        result.counts_by_type[m.entity_type_guess] = (
            result.counts_by_type.get(m.entity_type_guess, 0) + 1
        )
        if m.is_new and m.canonical_id is None:
            result.new_candidates.append(m)
            self._enqueue_review(m, kind="new_entity")
        if m.is_ambiguous:
            result.ambiguous.append(m)
            self._enqueue_review(m, kind="ambiguous")

    def _enqueue_review(self, m: ResolvedMention, *, kind: str) -> None:
        if self.review_queue is None:
            return
        try:
            self.review_queue.enqueue(
                kind=kind,
                surface=m.surface,
                entity_type_guess=m.entity_type_guess,
                source_doc_id=None,
                payload={
                    "span_start": m.span_start,
                    "span_end": m.span_end,
                    "stage": m.stage,
                    "confidence": m.confidence,
                    "notes": m.notes,
                },
            )
        except Exception as exc:  # pragma: no cover
            logger.warning("Review queue enqueue failed: %s", exc)

    def _auto_create_new_entity(
        self,
        *,
        mention: Mention,
        doc_id: str,
    ) -> Optional[str]:
        """Yeni canonical entity yarat, ilk alias olarak surface_form ekle."""
        kind = mention.entity_type_guess
        if kind in ("unknown",):
            return None

        try:
            etype_enum = EntityType(kind)
        except ValueError:
            etype_enum = EntityType.OTHER

        cid = self.store.create_entity(
            canonical_name=mention.surface,
            entity_type=etype_enum,
            status="auto_added",
            source="document_extraction",
            metadata={"first_doc_id": doc_id, "ner_backend": mention.backend},
        )
        # surface_form alias'ı zaten create_entity içinde seed olarak eklendi.
        # Ama provenance için tekrar ekleyelim (source='ner_first_observed')
        self.store.add_alias(
            mention.surface,
            cid,
            kind="company" if kind == "company" else "person" if kind == "person" else "any",
            confidence=mention.confidence,
            source=AliasSource.NER_FIRST,
            source_doc_id=doc_id,
            source_span=(mention.span_start, mention.span_end),
        )
        return cid

    @staticmethod
    def _merge_spans(
        gaz_hits: list[SpottedMention],
        ner_mentions: list[Mention],
    ) -> list[tuple[str, object]]:
        """Gazetteer her zaman kazanır; NER örtüşmeyen span'leri ekler.

        Dönüş: [("gazetteer", SpottedMention) | ("ner", Mention)]
        """
        out: list[tuple[str, object]] = []
        gaz_ranges: list[tuple[int, int]] = []
        for g in gaz_hits:
            out.append(("gazetteer", g))
            gaz_ranges.append((g.span_start, g.span_end))

        for n in ner_mentions:
            overlap = any(
                not (n.span_end <= gs or n.span_start >= ge) for gs, ge in gaz_ranges
            )
            if not overlap:
                out.append(("ner", n))

        # Ortak span sıralaması — okuma sırası (offset)
        out.sort(key=lambda x: (x[1].span_start, x[1].span_end))
        return out
