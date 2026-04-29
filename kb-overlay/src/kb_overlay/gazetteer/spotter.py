"""Belge metninde gazetteer otomata ile mention spotting.

Aho-Corasick fold edilmiş metin üzerinde çalışır; offset'ler 1-1 mapping
sayesinde orijinal text'e doğrudan uygulanır. Word-boundary check ile alt-string
hit'leri (örn. "ABC" → "ABC Bilişim" içinde) elenir.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from .builder import GazetteerEntry
from .fold import gazetteer_fold

try:
    import ahocorasick  # noqa: F401
    _AHOCORASICK_AVAILABLE = True
except ImportError:  # pragma: no cover
    _AHOCORASICK_AVAILABLE = False

logger = logging.getLogger(__name__)

# Word boundary\'ler: whitespace, noktalama, string sınırı
_WORD_BOUNDARY_CHARS = set(" \t\n\r.,;:!?()[]{}<>\"/\\|*+=`~^")


@dataclass(slots=True, frozen=True)
class SpottedMention:
    """Gazetteer\'ın yakaladığı bir mention."""

    surface: str          # belgedeki orijinal yazım (örn. "ABC Bilişim\'in")
    matched_alias: str    # eşleşen alias surface_form\'u (örn. "ABC Bilişim")
    span_start: int       # belge içindeki başlangıç offset (inclusive)
    span_end: int         # bitiş offset (exclusive)
    canonical_id: str
    entity_type: str
    confidence: float = 1.0


class GazetteerSpotter:
    """Hazır otomatayı belge metnine uygulayıp mention\'ları döndürür."""

    def __init__(self, automaton):
        if not _AHOCORASICK_AVAILABLE:
            raise ImportError("pyahocorasick not installed")
        self.automaton = automaton

    def spot(self, text: str, *, entity_type: Optional[str] = None) -> list[SpottedMention]:
        if not text or not self.automaton:
            return []

        folded = gazetteer_fold(text)
        if len(folded) != len(text):
            raise RuntimeError("gazetteer_fold not 1-1")

        raw_hits: list[SpottedMention] = []
        for end_idx, entries in self.automaton.iter(folded):
            # entries: list[GazetteerEntry] (aynı fold key birden fazla canonical olabilir)
            for entry in entries:
                key_len = len(entry.fold_key)
                start_idx = end_idx - key_len + 1
                end_excl = end_idx + 1

                if entity_type and entry.entity_type != entity_type:
                    continue

                if not _is_word_bounded(text, start_idx, end_excl):
                    continue

                raw_hits.append(
                    SpottedMention(
                        surface=text[start_idx:end_excl],
                        matched_alias=entry.original_surface,
                        span_start=start_idx,
                        span_end=end_excl,
                        canonical_id=entry.canonical_id,
                        entity_type=entry.entity_type,
                        confidence=1.0,
                    )
                )

        # Aynı span\'da farklı alias\'lar match etmişse uzun olanı tut
        # (longest-match, "ABC" yerine "ABC Bilişim" tercih edilsin)
        return _longest_match_dedup(raw_hits)


def _is_word_bounded(text: str, start: int, end: int) -> bool:
    """Span\'in başında ve sonunda word boundary var mı?"""
    n = len(text)
    if start < 0 or end > n:
        return False
    left_ok = start == 0 or text[start - 1] in _WORD_BOUNDARY_CHARS
    right_ok = end == n or text[end] in _WORD_BOUNDARY_CHARS
    # Sağda apostrof+ek olabilir: "ABC Bilişim\'in" → ABC Bilişim\'i değil ABC Bilişim match etsin.
    # Apostrof match\'in hemen sağındaysa sağ boundary kabul edilir.
    if not right_ok and end < n and text[end] in ("\'", "\u2018", "\u2019"):
        right_ok = True
    return left_ok and right_ok


def _longest_match_dedup(hits: list[SpottedMention]) -> list[SpottedMention]:
    """Üst üste binen span\'lerden en uzun olanı seç.

    İki span üst üste biniyorsa (overlap) ve hangisi daha uzunsa onu tut.
    Aynı uzunlukta ise her ikisi de tutulur (ambiguous, resolver\'a bırak).
    """
    if not hits:
        return []
    hits_sorted = sorted(hits, key=lambda h: (h.span_start, -(h.span_end - h.span_start)))
    kept: list[SpottedMention] = []
    for h in hits_sorted:
        # Var olan herhangi bir kept span tarafından "yutuluyor" mu?
        swallowed = False
        for k in kept:
            if k.span_start <= h.span_start and k.span_end >= h.span_end and k != h:
                if (k.span_end - k.span_start) > (h.span_end - h.span_start):
                    swallowed = True
                    break
        if not swallowed:
            kept.append(h)
    # Sondan başa bir tarama daha: kept içinde h tarafından yutulan eski daha kısa bir hit varsa kaldır
    final: list[SpottedMention] = []
    for h in kept:
        keep = True
        for other in kept:
            if other is h:
                continue
            if (
                other.span_start <= h.span_start
                and other.span_end >= h.span_end
                and (other.span_end - other.span_start) > (h.span_end - h.span_start)
            ):
                keep = False
                break
        if keep:
            final.append(h)
    return final
