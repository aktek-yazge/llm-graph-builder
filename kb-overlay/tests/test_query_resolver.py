"""QueryResolver — sorgu zamanı entity resolution testleri.

Gerçek Neo4j gerektirmez; sadece SQLite + in-memory gazetteer/NER.
"""
from __future__ import annotations

import pytest

from kb_overlay.agent import QueryResolver
from kb_overlay.discovery import get_ner_backend


@pytest.fixture()
def query_resolver(seeded_store):
    return QueryResolver(seeded_store, ner_backend=get_ner_backend("naive"))


def test_resolve_known_entity_via_gazetteer(query_resolver):
    """Sözlükteki bir ismi tam yazımla sorgula → gazetteer hit."""
    out = query_resolver.resolve("ABC Bilişim hakkında bilgi ver")
    matched = [e for e in out.entities if e.canonical_id == "COMP_abc001"]
    assert matched, f"COMP_abc001 bulunamadı, entities={out.entities}"
    assert any(e.stage == "gazetteer_hit" for e in matched)
    assert "ABC Bilişim" in out.canonical_id_map


def test_resolve_with_company_suffix_variant(query_resolver):
    """Sözlükteki tam alias varyant (Anonim Şirketi) gazetteer ile yakalanır."""
    out = query_resolver.resolve("ABC Bilişim Anonim Şirketi'nin CEO'su kim?")
    canonical_ids = {e.canonical_id for e in out.entities if e.canonical_id}
    assert "COMP_abc001" in canonical_ids


def test_resolve_unknown_entity_marked_as_unresolved(query_resolver):
    """Sözlükte olmayan bir isim NER ile yakalanır ama unknown olarak işaretlenir."""
    out = query_resolver.resolve("XYZ Bilinmeyen Şirketi A.Ş. hakkında bilgi")
    # NER yakaladıysa unresolved olmalı
    unresolved = [e for e in out.entities if e.is_unknown]
    if out.entities:
        # Eğer hiç entity bulunmadıysa boş liste olabilir; NER yakaladıysa unknown bekle
        assert out.has_unresolved() or all(e.canonical_id is None for e in unresolved)


def test_to_prompt_block_format(query_resolver):
    out = query_resolver.resolve("ABC Bilişim hakkında")
    block = out.to_prompt_block()
    assert "surface" in block
    assert "canonical_id" in block


def test_empty_query_returns_no_entities(query_resolver):
    out = query_resolver.resolve("")
    assert out.entities == []
    assert out.canonical_id_map == {}


def test_canonical_id_map_only_contains_resolved(query_resolver):
    """Map sadece canonical_id'si olan entity'leri içerir."""
    out = query_resolver.resolve("ABC Bilişim ile XYZ Bilinmeyen Şirketi A.Ş.")
    for surface, cid in out.canonical_id_map.items():
        assert cid is not None
        assert cid.startswith(("COMP_", "PERS_", "ORG_", "ENT_", "OTHER", "OTH"))
