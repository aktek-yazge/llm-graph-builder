"""End-to-end DocumentExtractor testleri."""
from __future__ import annotations

import pytest

from kb_overlay.dictionary import AliasStore
from kb_overlay.discovery import DocumentExtractor, get_ner_backend
from kb_overlay.gazetteer import GazetteerBuilder, GazetteerSpotter
from kb_overlay.resolver import CascadingResolver
from kb_overlay.review import ReviewQueue


@pytest.fixture()
def extractor(seeded_store: AliasStore) -> DocumentExtractor:
    review_q = ReviewQueue(seeded_store)
    spotter = None
    try:
        automaton = GazetteerBuilder(seeded_store).build()
        spotter = GazetteerSpotter(automaton)
    except ImportError:
        pass

    resolver = CascadingResolver(seeded_store)
    ner = get_ner_backend("naive")
    return DocumentExtractor(
        seeded_store,
        resolver,
        ner_backend=ner,
        gazetteer_spotter=spotter,
        review_queue=review_q,
        auto_create_new_entities=True,
    )


SAMPLE_TEXT = (
    "Sayın Mehmet Yılmaz, "
    "ABC Bilişim'in genel kurul kararını ekte iletiyoruz. "
    "Türkiye İş Bankası A.Ş. ile yapılan anlaşma 2024 yılında imzalanmıştır. "
    "XYZ Holding ve Acme Inc. de bu sürece dahildir."
)


def test_extract_finds_seeded_entities(extractor: DocumentExtractor):
    result = extractor.extract(SAMPLE_TEXT, doc_id="DOC_TEST_001", title="Test Belgesi")
    canonical_ids = {m.canonical_id for m in result.mentions if m.canonical_id}
    assert "COMP_abc001" in canonical_ids, "ABC Bilişim yakalanmadı"
    assert "PERS_mehyl001" in canonical_ids, "Mehmet Yılmaz yakalanmadı"
    assert "COMP_isbank01" in canonical_ids, "İş Bankası yakalanmadı"


def test_extract_creates_new_candidates(extractor: DocumentExtractor):
    result = extractor.extract(SAMPLE_TEXT, doc_id="DOC_TEST_002")
    # XYZ Holding ve Acme Inc. seed'de yok → ya auto_create ya pending
    surfaces_resolved = {m.surface for m in result.mentions if m.canonical_id}
    surfaces_pending = {m.surface for m in result.new_candidates}
    new_or_resolved = surfaces_resolved | surfaces_pending
    assert any("XYZ" in s for s in new_or_resolved) or any("Holding" in s for s in new_or_resolved), (
        f"XYZ Holding hiç görülmedi: resolved={surfaces_resolved}, pending={surfaces_pending}"
    )


def test_extract_records_provenance(extractor: DocumentExtractor, seeded_store: AliasStore):
    extractor.extract(SAMPLE_TEXT, doc_id="DOC_PROV_001", title="Provenance test")
    aliases = seeded_store.get_aliases_for("COMP_abc001")
    assert any(a.source_doc_id == "DOC_PROV_001" for a in aliases), (
        "Provenance source_doc_id alias'a yazılmadı"
    )


def test_extract_offsets_are_correct(extractor: DocumentExtractor):
    result = extractor.extract(SAMPLE_TEXT, doc_id="DOC_OFFSET_001")
    for m in result.mentions:
        assert SAMPLE_TEXT[m.span_start:m.span_end] == m.surface
