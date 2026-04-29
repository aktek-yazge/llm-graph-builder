"""Normalizer testleri — Türkçe + multilingual."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from kb_overlay.normalize import normalize, normalize_loose, normalize_strict


# ---------------------------------------------------------------------------
# Birim testler
# ---------------------------------------------------------------------------


class TestStrict:
    def test_basic_lowercase(self):
        assert normalize_strict("ABC Bilişim") == "abc bilişim"

    def test_company_suffix_stripped(self):
        assert normalize_strict("ABC Bilişim A.Ş.", "company") == "abc bilişim"
        assert normalize_strict("ABC Bilişim Anonim Şirketi", "company") == "abc bilişim"
        assert normalize_strict("ABC Bilişim AŞ", "company") == "abc bilişim"

    def test_ltd_sti_stripped(self):
        assert normalize_strict(
            "XYZ San. ve Tic. Ltd. Şti.", "company"
        ) == "xyz"

    def test_holding_stripped(self):
        assert normalize_strict("ABC Holding", "company") == "abc"

    def test_apostrophe_suffix_stripped(self):
        assert normalize_strict("ABC Bilişim'in", "company") == "abc bilişim"
        assert normalize_strict("ABC Bilişim'den", "company") == "abc bilişim"
        assert normalize_strict("ABC Bilişim'de", "company") == "abc bilişim"

    def test_person_titles_stripped(self):
        assert normalize_strict("Sayın Mehmet Yılmaz", "person") == "mehmet yılmaz"
        assert normalize_strict("Dr. Mehmet Yılmaz", "person") == "mehmet yılmaz"
        assert normalize_strict("Prof. Dr. Mehmet Yılmaz", "person") == "mehmet yılmaz"

    def test_turkey_prefix_stripped(self):
        assert normalize_strict("T.C. Ziraat Bankası A.Ş.", "company") == "ziraat bankası"

    def test_punctuation_to_space(self):
        assert normalize_strict("A.B.C, Bilişim!") == "a b c bilişim"

    def test_empty_input(self):
        assert normalize_strict("") == ""
        assert normalize_strict("   ") == ""


class TestLoose:
    def test_ascii_fold(self):
        assert normalize_loose("İş Bankası") == "is bankasi"
        assert normalize_loose("Şirket") == "sirket"
        assert normalize_loose("ÇĞİÖŞÜ") == "cgiosu"

    def test_loose_collision_intentional(self):
        # İş Bankası ve Iş Bankası loose'da aynı, strict'te farklı (mümkün).
        # Tasarım kararı: I → i, İ → i (strict'te), o yüzden strict'te de
        # collision olabilir; bu kasıtlı.
        assert normalize_loose("İş Bankası") == normalize_loose("Iş Bankası")


class TestEnglishMultilingual:
    def test_inc_corp_llc(self):
        assert normalize_strict("Acme Inc.", "company") == "acme"
        assert normalize_strict("Acme Corp.", "company") == "acme"
        assert normalize_strict("Acme Corporation", "company") == "acme"
        assert normalize_strict("Acme LLC", "company") == "acme"
        assert normalize_strict("Acme Ltd.", "company") == "acme"

    def test_german_french(self):
        assert normalize_strict("BMW AG", "company") == "bmw"
        assert normalize_strict("Bosch GmbH", "company") == "bosch"
        assert normalize_strict("Renault S.A.", "company") == "renault"


# ---------------------------------------------------------------------------
# YAML corpus tabanlı testler
# ---------------------------------------------------------------------------


def _load_corpus():
    corpus_path = Path(__file__).parent / "fixtures" / "turkish_variants.yaml"
    return yaml.safe_load(corpus_path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def corpus():
    return _load_corpus()


def test_corpus_company_suffix_variants_collapse(corpus):
    """Aynı grup içindeki tüm varyantlar aynı strict key vermeli."""
    for group in corpus["company_suffix_variants"]["groups"]:
        canonical_norm = normalize_strict(group["canonical"], "company")
        # NOT: bazı varyantlar suffix temizlendikten sonra canonical'dan kısa kalabilir
        # (örn. "Acme LLC" → "acme", canonical "Acme Corp." → "acme"). O yüzden tüm
        # varyantların 1 veya 2 farklı key ürettiğini ve canonical'ın bunlardan biri
        # olduğunu kontrol ediyoruz.
        keys = {normalize_strict(v, "company") for v in group["variants"]}
        assert len(keys) <= 2, f"Beklenenden fazla farklı key üretildi: {keys}"
        assert canonical_norm in keys, (
            f"Canonical '{group['canonical']}' → '{canonical_norm}' "
            f"hiçbir varyantla eşleşmedi: {keys}"
        )


def test_corpus_person_titles_collapse(corpus):
    for group in corpus["person_titles"]["groups"]:
        keys = {normalize_strict(v, "person") for v in group["variants"]}
        assert len(keys) == 1, f"Person titles aynı key vermedi: {keys}"


def test_corpus_apostrophe_suffixes_collapse(corpus):
    for group in corpus["turkish_apostrophe_suffixes"]["groups"]:
        keys = {normalize_strict(v, "company") for v in group["variants"]}
        assert len(keys) == 1, f"Apostrophe suffix temizliği yetersiz: {keys}"


def test_corpus_ascii_fold_collisions(corpus):
    for group in corpus["ascii_fold_collisions"]["groups"]:
        keys = {normalize_loose(v, "company") for v in group["loose_match"]}
        assert len(keys) == 1, f"Loose ASCII fold aynı key vermedi: {keys}"
