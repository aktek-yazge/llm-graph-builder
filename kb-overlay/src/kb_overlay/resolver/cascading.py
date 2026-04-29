"""Cascading entity resolver — 5 kademeli karar ağacı.

Sıralı denemeler:
    1. SQLite exact (norm_strict) lookup        → confidence 1.0
    2. SQLite loose (norm_loose) lookup         → confidence 0.95
    3. RapidFuzz token_set_ratio (top-K aday)   → confidence ~0.85, alias upsert
    4. Vector similarity (opsiyonel)            → confidence ~0.80, alias upsert
    5. Hiçbiri eşleşmedi                        → yeni aday → review queue

Bağlam (context_text) verilirse 3 ve 4. kademede ambiguity disambiguation için
kullanılır.

Her ResolutionResult'a graphify-style ``confidence_label`` (EXTRACTED /
INFERRED / AMBIGUOUS) derive edilir. Numeric ``confidence`` ile birlikte
kullanılır; downstream quality_gate'in label-based policy uygulayabilmesi için.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Protocol

from ..confidence import ConfidenceLabel
from ..dictionary import AliasRecord, AliasSource, AliasStore
from ..normalize import normalize

logger = logging.getLogger(__name__)


class ResolutionStage(str, Enum):
    EXACT_STRICT = "exact_strict"
    EXACT_LOOSE = "exact_loose"
    FUZZY = "fuzzy"
    VECTOR = "vector"
    NEW_CANDIDATE = "new_candidate"
    AMBIGUOUS = "ambiguous"


# Stage → ConfidenceLabel mapping.
# EXTRACTED:  kaynaktan birebir geldi (exact lookup).
# INFERRED:   algoritmik tahmin (fuzzy / vector / NER yeni gördü).
# AMBIGUOUS:  birden fazla aday eşit, çelişki → human review.
_STAGE_LABEL_MAP: dict[ResolutionStage, ConfidenceLabel] = {
    ResolutionStage.EXACT_STRICT: ConfidenceLabel.EXTRACTED,
    ResolutionStage.EXACT_LOOSE: ConfidenceLabel.EXTRACTED,
    ResolutionStage.FUZZY: ConfidenceLabel.INFERRED,
    ResolutionStage.VECTOR: ConfidenceLabel.INFERRED,
    ResolutionStage.NEW_CANDIDATE: ConfidenceLabel.INFERRED,
    ResolutionStage.AMBIGUOUS: ConfidenceLabel.AMBIGUOUS,
}


def label_from_stage(stage: ResolutionStage, *, is_ambiguous: bool = False) -> ConfidenceLabel:
    """Stage + ambiguity flag → ConfidenceLabel.

    is_ambiguous her zaman AMBIGUOUS'a düşer (mapping'i override eder).
    Mapping dışında bir stage gelirse INFERRED default'una çekilir.
    """
    if is_ambiguous:
        return ConfidenceLabel.AMBIGUOUS
    return _STAGE_LABEL_MAP.get(stage, ConfidenceLabel.INFERRED)


@dataclass(slots=True)
class CandidateMatch:
    canonical_id: str
    score: float
    via: str  # 'strict' | 'loose' | 'fuzzy' | 'vector'
    matched_text: Optional[str] = None


@dataclass(slots=True)
class ResolutionResult:
    """Bir mention için resolver çıktısı."""

    surface_form: str
    canonical_id: Optional[str]
    stage: ResolutionStage
    confidence: float
    candidates: list[CandidateMatch] = field(default_factory=list)
    is_ambiguous: bool = False
    is_new: bool = False
    notes: Optional[str] = None

    @property
    def confidence_label(self) -> ConfidenceLabel:
        """Stage + is_ambiguous'tan türetilen graphify-style etiket."""
        return label_from_stage(self.stage, is_ambiguous=self.is_ambiguous)


# Embedding/vector benzerlik için pluggable interface (opsiyonel)
class VectorProvider(Protocol):
    def embed(self, text: str) -> "list[float]":  # pragma: no cover - interface
        ...

    def search_similar(
        self,
        query_text: str,
        *,
        entity_type: Optional[str] = None,
        top_k: int = 5,
    ) -> list[CandidateMatch]:  # pragma: no cover - interface
        ...


class CascadingResolver:
    def __init__(
        self,
        store: AliasStore,
        *,
        fuzzy_threshold: int = 90,
        vector_threshold: float = 0.85,
        vector_provider: Optional[VectorProvider] = None,
        max_fuzzy_candidates: int = 10,
        auto_persist_new_aliases: bool = True,
    ):
        self.store = store
        self.fuzzy_threshold = fuzzy_threshold
        self.vector_threshold = vector_threshold
        self.vector_provider = vector_provider
        self.max_fuzzy_candidates = max_fuzzy_candidates
        self.auto_persist_new_aliases = auto_persist_new_aliases

    # ------------------------------------------------------------------ public

    def resolve(
        self,
        mention: str,
        *,
        kind: str = "any",
        entity_type: Optional[str] = None,
        context_text: Optional[str] = None,
        source_doc_id: Optional[str] = None,
        source_span: Optional[tuple[int, int]] = None,
    ) -> ResolutionResult:
        if not mention or not mention.strip():
            return ResolutionResult(
                surface_form=mention,
                canonical_id=None,
                stage=ResolutionStage.NEW_CANDIDATE,
                confidence=0.0,
                is_new=True,
                notes="empty_mention",
            )

        # Kademe 1+2: SQLite lookup
        hits = self.store.lookup(mention, kind=kind, entity_type=entity_type)
        if hits:
            stage = ResolutionStage.EXACT_STRICT if hits[0][1] == "strict" else ResolutionStage.EXACT_LOOSE
            confidence = 1.0 if stage == ResolutionStage.EXACT_STRICT else 0.95

            unique_canonicals = {h[0].canonical_id for h in hits}
            if len(unique_canonicals) > 1:
                # Ambiguous lookup: aynı key birden fazla canonical
                ranked = self._disambiguate_with_context(
                    list(unique_canonicals), context_text, mention, kind
                )
                if ranked is None:
                    return ResolutionResult(
                        surface_form=mention,
                        canonical_id=None,
                        stage=ResolutionStage.AMBIGUOUS,
                        confidence=0.0,
                        candidates=[
                            CandidateMatch(canonical_id=cid, score=confidence, via=stage.value)
                            for cid in unique_canonicals
                        ],
                        is_ambiguous=True,
                        notes=f"{len(unique_canonicals)} canonical match aynı key'e bağlı",
                    )
                cid, score = ranked
                return ResolutionResult(
                    surface_form=mention,
                    canonical_id=cid,
                    stage=stage,
                    confidence=score,
                    candidates=[
                        CandidateMatch(canonical_id=cid2, score=confidence, via=stage.value)
                        for cid2 in unique_canonicals
                    ],
                    notes="disambiguated_by_context",
                )

            cid = next(iter(unique_canonicals))
            return ResolutionResult(
                surface_form=mention,
                canonical_id=cid,
                stage=stage,
                confidence=confidence,
                candidates=[CandidateMatch(canonical_id=cid, score=confidence, via=stage.value)],
            )

        # Kademe 3: Fuzzy
        fuzzy_match = self._fuzzy_search(mention, kind=kind, entity_type=entity_type)
        if fuzzy_match:
            best = fuzzy_match[0]
            score = best.score / 100.0
            if best.score >= self.fuzzy_threshold:
                # Disambiguation: birden fazla yüksek-skor varsa context ile
                top_close = [c for c in fuzzy_match if c.score >= self.fuzzy_threshold - 5]
                if len(top_close) > 1:
                    ranked = self._disambiguate_with_context(
                        [c.canonical_id for c in top_close], context_text, mention, kind
                    )
                    if ranked is None:
                        return ResolutionResult(
                            surface_form=mention,
                            canonical_id=None,
                            stage=ResolutionStage.AMBIGUOUS,
                            confidence=0.0,
                            candidates=top_close,
                            is_ambiguous=True,
                            notes="fuzzy_top_too_close",
                        )
                    best_cid = ranked[0]
                else:
                    best_cid = best.canonical_id

                if self.auto_persist_new_aliases:
                    self.store.add_alias(
                        mention,
                        best_cid,
                        kind=kind,
                        confidence=score,
                        source=AliasSource.FUZZY,
                        source_doc_id=source_doc_id,
                        source_span=source_span,
                    )
                return ResolutionResult(
                    surface_form=mention,
                    canonical_id=best_cid,
                    stage=ResolutionStage.FUZZY,
                    confidence=score,
                    candidates=fuzzy_match[:5],
                )

        # Kademe 4: Vector (opsiyonel)
        if self.vector_provider is not None:
            try:
                vec_matches = self.vector_provider.search_similar(
                    mention, entity_type=entity_type, top_k=5
                )
                if vec_matches and vec_matches[0].score >= self.vector_threshold:
                    best_v = vec_matches[0]
                    if self.auto_persist_new_aliases:
                        self.store.add_alias(
                            mention,
                            best_v.canonical_id,
                            kind=kind,
                            confidence=best_v.score,
                            source=AliasSource.EMBEDDING,
                            source_doc_id=source_doc_id,
                            source_span=source_span,
                        )
                    return ResolutionResult(
                        surface_form=mention,
                        canonical_id=best_v.canonical_id,
                        stage=ResolutionStage.VECTOR,
                        confidence=best_v.score,
                        candidates=vec_matches,
                    )
            except Exception as exc:  # pragma: no cover
                logger.warning("Vector search failed: %s", exc)

        # Kademe 5: Yeni aday
        return ResolutionResult(
            surface_form=mention,
            canonical_id=None,
            stage=ResolutionStage.NEW_CANDIDATE,
            confidence=0.0,
            candidates=fuzzy_match[:3] if fuzzy_match else [],
            is_new=True,
            notes="no_match_above_threshold",
        )

    # ------------------------------------------------------------------ kademe 3

    def _fuzzy_search(
        self,
        mention: str,
        *,
        kind: str,
        entity_type: Optional[str],
    ) -> list[CandidateMatch]:
        try:
            from rapidfuzz import fuzz, process
        except ImportError:  # pragma: no cover
            logger.warning("rapidfuzz not installed, skipping fuzzy stage")
            return []

        candidates = self.store.list_entities(entity_type=entity_type, limit=10000)
        if not candidates:
            return []

        target = normalize(mention, kind=kind).strict
        if not target:
            return []

        # process.extract: [(match_str, score, key), ...]
        choices = {e.canonical_id: e.norm_strict or e.canonical_name for e in candidates}
        scored = process.extract(
            target,
            choices,
            scorer=fuzz.token_set_ratio,
            limit=self.max_fuzzy_candidates,
        )
        out: list[CandidateMatch] = []
        for matched, score, cid in scored:
            out.append(
                CandidateMatch(
                    canonical_id=cid,
                    score=float(score),
                    via="fuzzy",
                    matched_text=matched,
                )
            )
        return out

    # ------------------------------------------------------------------ disambig

    def _disambiguate_with_context(
        self,
        canonical_ids: list[str],
        context_text: Optional[str],
        mention: str,
        kind: str,
    ) -> Optional[tuple[str, float]]:
        """Bağlam yoksa None döndürür → caller AMBIGUOUS işaretlemeli.

        Bağlam varsa basit bir overlap heuristic uygulanır: mention'ın etrafındaki
        sözcüklerin canonical entity'nin diğer alias'ları ile kesişimi sayılır.
        İleride: vector_provider varsa cosine ile context embedding karşılaştırılır.
        """
        if not context_text or not canonical_ids:
            return None

        ctx_tokens = set(normalize(context_text, kind=kind).loose.split())
        if not ctx_tokens:
            return None

        scores: list[tuple[str, float]] = []
        for cid in canonical_ids:
            entity = self.store.get_entity(cid)
            if not entity:
                continue
            entity_tokens = set(entity.norm_loose.split())
            for alias in self.store.get_aliases_for(cid):
                entity_tokens.update(alias.norm_loose.split())
            entity_tokens.discard("")
            if not entity_tokens:
                continue
            overlap = len(ctx_tokens & entity_tokens)
            score = overlap / max(1, len(entity_tokens))
            scores.append((cid, score))

        if not scores:
            return None
        scores.sort(key=lambda x: x[1], reverse=True)
        if len(scores) > 1 and abs(scores[0][1] - scores[1][1]) < 0.05:
            # Hâlâ çok yakın → ambiguous bırak
            return None
        return scores[0]
