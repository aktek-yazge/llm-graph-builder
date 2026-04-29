"""Cascading resolver testleri — 5 kademe."""
from __future__ import annotations

import pytest

from kb_overlay.dictionary import AliasStore, EntityType
from kb_overlay.resolver import CascadingResolver, ResolutionStage


@pytest.fixture()
def resolver(seeded_store: AliasStore) -> CascadingResolver:
    return CascadingResolver(seeded_store, fuzzy_threshold=88)


class TestKademeExact:
    def test_strict_exact(self, resolver: CascadingResolver):
        res = resolver.resolve("ABC Bilişim", entity_type="company")
        assert res.canonical_id == "COMP_abc001"
        assert res.stage == ResolutionStage.EXACT_STRICT
        assert res.confidence >= 0.95

    def test_strict_with_suffix(self, resolver: CascadingResolver):
        res = resolver.resolve("ABC Bilişim A.Ş.", entity_type="company")
        assert res.canonical_id == "COMP_abc001"

    def test_strict_with_apostrophe(self, resolver: CascadingResolver):
        res = resolver.resolve("ABC Bilişim'in", entity_type="company")
        assert res.canonical_id == "COMP_abc001"


class TestKademeLoose:
    def test_loose_match_after_strict_miss(self, resolver: CascadingResolver):
        # "abc bilisim" (Türkçe karakter olmadan)
        res = resolver.resolve("abc bilisim", entity_type="company")
        assert res.canonical_id == "COMP_abc001"
        assert res.stage in (ResolutionStage.EXACT_STRICT, ResolutionStage.EXACT_LOOSE)


class TestKademeFuzzy:
    def test_typo_fuzzy_match(self, resolver: CascadingResolver):
        res = resolver.resolve("ABC Bilisim Teknolojileri", entity_type="company")
        assert res.canonical_id == "COMP_abc001"
        assert res.stage in (
            ResolutionStage.EXACT_LOOSE,
            ResolutionStage.EXACT_STRICT,
            ResolutionStage.FUZZY,
        )

    def test_fuzzy_persists_new_alias(self, seeded_store: AliasStore):
        resolver = CascadingResolver(
            seeded_store, fuzzy_threshold=85, auto_persist_new_aliases=True
        )
        before = len(seeded_store.get_aliases_for("COMP_abc001"))
        res = resolver.resolve("ABC Biliisim Teknolojileri", entity_type="company")
        if res.stage == ResolutionStage.FUZZY:
            after = len(seeded_store.get_aliases_for("COMP_abc001"))
            assert after > before, "Fuzzy match sonrası yeni alias kayıt edilmeli"


class TestKademeNewCandidate:
    def test_unknown_becomes_new_candidate(self, resolver: CascadingResolver):
        res = resolver.resolve("Çok Tuhaf Bir Şirket Adı", entity_type="company")
        assert res.is_new
        assert res.canonical_id is None
        assert res.stage == ResolutionStage.NEW_CANDIDATE


class TestPersonResolution:
    def test_person_with_title(self, resolver: CascadingResolver):
        res = resolver.resolve("Sayın Mehmet Yılmaz", entity_type="person")
        assert res.canonical_id == "PERS_mehyl001"

    def test_person_with_initials(self, resolver: CascadingResolver):
        res = resolver.resolve("M. Yılmaz", entity_type="person")
        assert res.canonical_id == "PERS_mehyl001"

    def test_person_strict_kind(self, resolver: CascadingResolver):
        res = resolver.resolve("Mehmet Yılmaz", kind="person", entity_type="person")
        assert res.canonical_id == "PERS_mehyl001"


class TestEmptyInput:
    def test_empty(self, resolver: CascadingResolver):
        res = resolver.resolve("")
        assert res.is_new
        assert res.canonical_id is None
