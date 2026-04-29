"""Gazetteer trie inşası — pyahocorasick wrapper.

Trie key'leri "fold" edilmiş halde saklanır:
  - Türkçe karakterler ASCII'ye fold (ı→i, ş→s, ...)
  - Lowercase
  - Noktalama → boşluk (1-char → 1-char, offset korunur)
  - YOK: multi-space collapse, suffix stripping (offset bozar)

Belge metni de aynı fold'dan geçirilir. Bu sayede pyahocorasick byte-bazlı
match yapsa da, dönüş offset'leri orijinal metinde geçerlidir (1-1 mapping).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, Optional

try:
    import ahocorasick
    _AHOCORASICK_AVAILABLE = True
except ImportError:  # pragma: no cover
    ahocorasick = None  # type: ignore
    _AHOCORASICK_AVAILABLE = False

from ..dictionary import AliasStore
from .fold import gazetteer_fold

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class GazetteerEntry:
    """Trie'ye eklenen tek bir alias."""

    fold_key: str
    canonical_id: str
    entity_type: str
    original_surface: str


class GazetteerBuilder:
    """SQLite alias_store'dan Aho-Corasick automaton inşa eder.

    Kullanım:
        builder = GazetteerBuilder(store)
        automaton = builder.build()  # pyahocorasick.Automaton
        # otomata gazetteer.spotter.GazetteerSpotter ile kullanılır
    """

    MIN_KEY_LEN = 3  # 2 karakterli "AB" gibi gürültüler trie'ye girmez

    def __init__(self, store: AliasStore, *, min_key_len: Optional[int] = None):
        if not _AHOCORASICK_AVAILABLE:
            raise ImportError(
                "pyahocorasick not installed. Install with: pip install 'kb-overlay[ner]' "
                "or `pip install pyahocorasick`"
            )
        self.store = store
        self.min_key_len = min_key_len if min_key_len is not None else self.MIN_KEY_LEN

    def build(self, *, entity_type: Optional[str] = None) -> "ahocorasick.Automaton":
        automaton = ahocorasick.Automaton()
        added = 0
        skipped_short = 0
        seen: set[tuple[str, str]] = set()  # dedup (fold_key, canonical_id)

        for surface, canonical_id, etype in self.store.iter_surface_forms(entity_type=entity_type):
            fold_key = gazetteer_fold(surface)
            if not fold_key:
                continue
            if len(fold_key) < self.min_key_len:
                skipped_short += 1
                continue
            dedup_key = (fold_key, canonical_id)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            entry = GazetteerEntry(
                fold_key=fold_key,
                canonical_id=canonical_id,
                entity_type=etype,
                original_surface=surface,
            )
            # pyahocorasick aynı key'e birden fazla value tolere etmez —
            # son `add_word` öncekini ezer. Çakışmalı (aynı fold farklı canonical)
            # durumda mevcut entry'i kontrol edip listeye dönüştürürüz.
            existing = automaton.get(fold_key, None)
            if existing is None:
                automaton.add_word(fold_key, [entry])
            else:
                existing.append(entry)
                # mutate in place; pyahocorasick reference'ı tutar
            added += 1

        if added > 0:
            automaton.make_automaton()
        logger.info(
            "Gazetteer built: %d aliases added (%d skipped as too short)",
            added,
            skipped_short,
        )
        return automaton

    @staticmethod
    def is_available() -> bool:
        return _AHOCORASICK_AVAILABLE
