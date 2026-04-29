"""AliasStore CRUD ve lookup testleri."""
from __future__ import annotations

import pytest

from kb_overlay.dictionary import AliasSource, AliasStore, EntityStatus, EntityType


def test_create_entity_idempotent(store: AliasStore):
    cid1 = store.create_entity("ABC Bilişim A.Ş.", EntityType.COMPANY)
    cid2 = store.create_entity("ABC Bilişim A.Ş.", EntityType.COMPANY)
    assert cid1 == cid2
    e = store.get_entity(cid1)
    assert e is not None
    assert e.canonical_name == "ABC Bilişim A.Ş."
    assert e.entity_type == EntityType.COMPANY.value


def test_seed_aliases_indexed(store: AliasStore):
    cid = store.create_entity(
        "ABC Bilişim Teknolojileri A.Ş.",
        EntityType.COMPANY,
        seed_aliases=["ABC Bilişim", "ABC Bilişim A.Ş.", "ABC BT"],
    )
    aliases = store.get_aliases_for(cid)
    surfaces = {a.surface_form for a in aliases}
    assert surfaces >= {
        "ABC Bilişim Teknolojileri A.Ş.",
        "ABC Bilişim",
        "ABC Bilişim A.Ş.",
        "ABC BT",
    }


class TestLookup:
    def test_strict_hit(self, seeded_store: AliasStore):
        hits = seeded_store.lookup("ABC Bilişim", entity_type="company")
        assert hits, "ABC Bilişim strict bulunmadı"
        rec, kind = hits[0]
        assert kind == "strict"
        assert rec.canonical_id == "COMP_abc001"

    def test_loose_hit(self, seeded_store: AliasStore):
        hits = seeded_store.lookup("abc bilisim", entity_type="company")
        assert hits
        assert hits[0][0].canonical_id == "COMP_abc001"

    def test_apostrophe_strip_then_lookup(self, seeded_store: AliasStore):
        hits = seeded_store.lookup("ABC Bilişim'in", entity_type="company")
        assert hits
        assert hits[0][0].canonical_id == "COMP_abc001"

    def test_anonim_sirketi_variant(self, seeded_store: AliasStore):
        hits = seeded_store.lookup("ABC Bilişim Anonim Şirketi", entity_type="company")
        assert hits
        assert hits[0][0].canonical_id == "COMP_abc001"

    def test_unknown_returns_empty(self, seeded_store: AliasStore):
        hits = seeded_store.lookup("XYZ Olmayan Şirket", entity_type="company")
        assert hits == []

    def test_entity_type_filter(self, seeded_store: AliasStore):
        hits_person = seeded_store.lookup("Mehmet Yılmaz", entity_type="person")
        assert hits_person
        hits_company = seeded_store.lookup("Mehmet Yılmaz", entity_type="company")
        assert hits_company == []


class TestAliasUpsert:
    def test_add_alias_dedup(self, seeded_store: AliasStore):
        before = len(seeded_store.get_aliases_for("COMP_abc001"))
        seeded_store.add_alias("ABC Bilişim", "COMP_abc001", source=AliasSource.NER_FIRST)
        seeded_store.add_alias("ABC Bilişim", "COMP_abc001", source=AliasSource.NER_FIRST)
        after = len(seeded_store.get_aliases_for("COMP_abc001"))
        assert after == before

    def test_human_source_priority(self, seeded_store: AliasStore):
        """Aynı alias farklı source'larla eklenirse 'human' baskın olmalı."""
        seeded_store.add_alias("ABC BT Yeni", "COMP_abc001", source=AliasSource.NER_FIRST)
        seeded_store.add_alias("ABC BT Yeni", "COMP_abc001", source=AliasSource.HUMAN)
        seeded_store.add_alias("ABC BT Yeni", "COMP_abc001", source=AliasSource.NER_FIRST)
        aliases = [a for a in seeded_store.get_aliases_for("COMP_abc001") if a.surface_form == "ABC BT Yeni"]
        assert len(aliases) == 1
        assert aliases[0].source == AliasSource.HUMAN.value


class TestStats:
    def test_stats_after_seed(self, seeded_store: AliasStore):
        s = seeded_store.stats()
        assert s["entities_total"] == 3
        assert s["aliases_total"] >= 9
        assert s["by_type"]["company"] == 2
        assert s["by_type"]["person"] == 1


class TestIterSurfaceForms:
    def test_yields_all_surfaces(self, seeded_store: AliasStore):
        items = list(seeded_store.iter_surface_forms(entity_type="company"))
        assert len(items) >= 8
        for surface, cid, etype in items:
            assert isinstance(surface, str) and surface.strip()
            assert cid.startswith("COMP_")
            assert etype == "company"
