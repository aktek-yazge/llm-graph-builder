"""QueryResolver — agent Cypher üretmeden ÖNCE çalışan sorgu-zamanı resolver.

Kullanıcının sorduğu Türkçe/İngilizce soruda geçen tüm şirket/kişi mention'larını
canonical_id'ye çözer. Bu sayede agent LLM'ine asla "raw isim" gitmez; sadece
çözülmüş canonical_id'ler verilir ve Cypher şablonları parametre olarak bunları
kullanır.

Akış:
    "ABC Bilişim Anonim Şirketi'nin CEO'su kim?"
        → mention'lar: ["ABC Bilişim Anonim Şirketi"]   (NER veya gazetteer)
        → resolver:    [{surface, canonical_id: "COMP_..."}]
        → agent:       MATCH (p:Person)-[:CEO_OF]->(c:Company {canonical_id: $cid}) ...
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from ..dictionary import AliasStore
from ..discovery import get_ner_backend
from ..discovery.ner import Mention, NerBackend
from ..gazetteer import GazetteerBuilder, GazetteerSpotter
from ..resolver import CascadingResolver, ResolutionResult

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class QueryEntity:
    surface: str
    canonical_id: Optional[str]
    canonical_name: Optional[str]
    entity_type: Optional[str]
    confidence: float
    stage: str
    span_start: int
    span_end: int
    is_ambiguous: bool = False
    is_unknown: bool = False
    candidates: list[dict] = field(default_factory=list)


@dataclass(slots=True)
class ResolvedQuery:
    raw_query: str
    entities: list[QueryEntity] = field(default_factory=list)
    canonical_id_map: dict[str, str] = field(default_factory=dict)
    """surface_form → canonical_id (agent prompt'a verilecek)"""

    def has_unresolved(self) -> bool:
        return any(e.is_unknown or e.is_ambiguous or e.canonical_id is None for e in self.entities)

    def to_prompt_block(self) -> str:
        """Agent LLM'e verilecek 'çözülmüş entity tablosu' formatı."""
        if not self.entities:
            return "Sorguda canonical entity tespit edilmedi."
        lines = ["surface | canonical_id | canonical_name | type | confidence"]
        for e in self.entities:
            lines.append(
                f"{e.surface} | {e.canonical_id or '?'} | "
                f"{e.canonical_name or '?'} | {e.entity_type or '?'} | "
                f"{e.confidence:.2f}"
            )
        return "\n".join(lines)


class QueryResolver:
    """Sorgu-zamanı entity resolver.

    Document extractor ile aynı pipeline'ı kullanır ama:
      * ``auto_create_new_entities=False`` — sorguda yeni entity yaratmıyoruz
      * ``auto_persist_new_aliases=False`` — fuzzy hit'leri sözlüğe yazmıyoruz
        (aksi halde her sorgu sözlüğü kirletir; sadece doc ingest yazar)
    """

    def __init__(
        self,
        store: AliasStore,
        *,
        ner_backend: Optional[NerBackend] = None,
        use_gazetteer: bool = True,
        fuzzy_threshold: int = 88,
    ):
        self.store = store
        self.ner = ner_backend or get_ner_backend("naive")
        self.spotter: Optional[GazetteerSpotter] = None
        if use_gazetteer and GazetteerBuilder.is_available():
            try:
                automaton = GazetteerBuilder(store).build()
                self.spotter = GazetteerSpotter(automaton)
            except Exception as exc:
                logger.warning("Gazetteer build atlandı: %s", exc)

        self.resolver = CascadingResolver(
            store,
            fuzzy_threshold=fuzzy_threshold,
            auto_persist_new_aliases=False,  # sorgu yolunda yazmıyoruz
        )

    def rebuild_gazetteer(self) -> None:
        """Sözlük büyüdüğünde trie'yi yeniden inşa et."""
        if not GazetteerBuilder.is_available():
            return
        try:
            self.spotter = GazetteerSpotter(GazetteerBuilder(self.store).build())
        except Exception as exc:  # pragma: no cover
            logger.warning("Gazetteer rebuild atlandı: %s", exc)

    def resolve(self, query: str) -> ResolvedQuery:
        out = ResolvedQuery(raw_query=query)
        if not query or not query.strip():
            return out

        # 1. Gazetteer pass — sözlüğümüzdeki bilinen isimler
        gaz_spans: list[tuple[int, int]] = []
        if self.spotter is not None:
            try:
                hits = self.spotter.spot(query)
            except Exception as exc:  # pragma: no cover
                hits = []
                logger.warning("Gazetteer spotting failed: %s", exc)
            for h in hits:
                ent = self.store.get_entity(h.canonical_id)
                out.entities.append(
                    QueryEntity(
                        surface=h.surface,
                        canonical_id=h.canonical_id,
                        canonical_name=ent.canonical_name if ent else None,
                        entity_type=ent.entity_type if ent else h.entity_type,
                        confidence=h.confidence,
                        stage="gazetteer_hit",
                        span_start=h.span_start,
                        span_end=h.span_end,
                    )
                )
                gaz_spans.append((h.span_start, h.span_end))

        # 2. NER pass — gazetteer'ın atladığı yeni mention'lar
        try:
            ner_mentions: list[Mention] = self.ner.extract(query)
        except Exception as exc:  # pragma: no cover
            ner_mentions = []
            logger.warning("NER extract failed: %s", exc)

        for m in ner_mentions:
            if any(not (m.span_end <= gs or m.span_start >= ge) for gs, ge in gaz_spans):
                continue  # gazetteer ile örtüşüyor, atla

            kind = m.entity_type_guess if m.entity_type_guess in ("company", "person") else "any"
            etype_filter = (
                m.entity_type_guess
                if m.entity_type_guess in ("company", "person", "organization", "location", "product")
                else None
            )
            res: ResolutionResult = self.resolver.resolve(
                m.surface,
                kind=kind,
                entity_type=etype_filter,
                context_text=query,
            )
            ent_record = self.store.get_entity(res.canonical_id) if res.canonical_id else None
            out.entities.append(
                QueryEntity(
                    surface=m.surface,
                    canonical_id=res.canonical_id,
                    canonical_name=ent_record.canonical_name if ent_record else None,
                    entity_type=ent_record.entity_type if ent_record else m.entity_type_guess,
                    confidence=res.confidence,
                    stage=res.stage.value,
                    span_start=m.span_start,
                    span_end=m.span_end,
                    is_ambiguous=res.is_ambiguous,
                    is_unknown=res.is_new,
                    candidates=[
                        {
                            "canonical_id": c.canonical_id,
                            "score": c.score,
                            "via": c.via,
                            "matched_text": c.matched_text,
                        }
                        for c in res.candidates[:5]
                    ],
                )
            )

        # 3. Map oluştur (surface → canonical_id) — agent için
        out.canonical_id_map = {
            e.surface: e.canonical_id for e in out.entities if e.canonical_id is not None
        }
        # Span'e göre sırala — okuma sırası
        out.entities.sort(key=lambda e: (e.span_start, e.span_end))
        return out
