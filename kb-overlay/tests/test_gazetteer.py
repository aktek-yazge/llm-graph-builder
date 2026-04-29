"""Gazetteer (Aho-Corasick) testleri."""
from __future__ import annotations

import pytest

from kb_overlay.dictionary import AliasStore
from kb_overlay.gazetteer import GazetteerBuilder, GazetteerSpotter
from kb_overlay.gazetteer.fold import gazetteer_fold

# Skip tüm modül eğer pyahocorasick yoksa
pyahocorasick = pytest.importorskip("ahocorasick")


@pytest.fixture()
def spotter(seeded_store: AliasStore) -> GazetteerSpotter:
    automaton = GazetteerBuilder(seeded_store).build()
    return GazetteerSpotter(automaton)


class TestFold:
    def test_fold_is_one_to_one(self):
        text = "ABC Bilişim A.Ş. İş Bankası ŞİRKETİ"
        folded = gazetteer_fold(text)
        assert len(folded) == len(text)

    def test_fold_lowercase(self):
        assert gazetteer_fold("ABC") == "abc"

    def test_fold_turkish_chars(self):
        assert gazetteer_fold("Şirket") == "sirket"
        assert gazetteer_fold("ÇĞİÖŞÜ") == "cgiosu"

    def test_fold_punct_to_space(self):
        folded = gazetteer_fold("A.B.C")
        assert folded == "a b c"


class TestSpotter:
    def test_finds_known_alias(self, spotter: GazetteerSpotter):
        text = "Bu sözleşme ABC Bilişim ile imzalanmıştır."
        hits = spotter.spot(text)
        assert any(h.canonical_id == "COMP_abc001" for h in hits), "ABC Bilişim bulunmadı"

    def test_word_boundary_check(self, spotter: GazetteerSpotter):
        # "ABC" alias değil bu store'da; "ABC Bilişim" alias.
        # "ABCDEFG Holding" gibi metinde "ABC Bilişim" geçmediği için hit olmamalı.
        text = "ABCDEFG Holding ile sözleşme yapıldı."
        hits = spotter.spot(text)
        assert all(h.canonical_id != "COMP_abc001" for h in hits)

    def test_finds_aliases_with_apostrophe_suffix(self, spotter: GazetteerSpotter):
        text = "ABC Bilişim'in son raporu ekteki gibidir."
        hits = spotter.spot(text)
        abc_hits = [h for h in hits if h.canonical_id == "COMP_abc001"]
        assert abc_hits, "ABC Bilişim'in formunda apostrof sonrası alias bulunamadı"

    def test_longest_match_wins(self, spotter: GazetteerSpotter):
        text = "Türkiye İş Bankası A.Ş. açıkladı"
        hits = spotter.spot(text)
        is_hits = [h for h in hits if h.canonical_id == "COMP_isbank01"]
        assert is_hits
        longest = max(is_hits, key=lambda h: h.span_end - h.span_start)
        assert (longest.span_end - longest.span_start) >= len("İş Bankası")

    def test_offsets_are_correct(self, spotter: GazetteerSpotter):
        text = "Lütfen ABC Bilişim ile iletişime geçin."
        hits = spotter.spot(text)
        for h in hits:
            assert text[h.span_start:h.span_end] == h.surface

    def test_empty_text(self, spotter: GazetteerSpotter):
        assert spotter.spot("") == []
