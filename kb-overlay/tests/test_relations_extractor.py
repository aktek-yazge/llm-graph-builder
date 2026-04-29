"""Relation extractor + StubProvider testleri.

Gerçek LLM gerektirmez; StubProvider deterministik çıktı verir.
Filtreleme (bilinmeyen canonical_id reddi) ve şema validasyonu test edilir.
"""
from __future__ import annotations

from kb_overlay.relations import (
    RelationExtractor,
    ResolvedEntityInput,
    StubProvider,
)


def _make_entities() -> list[ResolvedEntityInput]:
    return [
        ResolvedEntityInput(
            canonical_id="COMP_abc",
            canonical_name="ABC Bilişim A.Ş.",
            entity_type="company",
            surface="ABC Bilişim",
            span_start=0,
            span_end=11,
        ),
        ResolvedEntityInput(
            canonical_id="PERS_meh",
            canonical_name="Mehmet Yılmaz",
            entity_type="person",
            surface="Mehmet Yılmaz",
            span_start=20,
            span_end=33,
        ),
    ]


def test_no_entities_returns_empty():
    ex = RelationExtractor(provider=StubProvider())
    out = ex.extract("some text", entities=[])
    assert out.relations == []
    assert out.notes == "no_entities"


def test_stub_provider_passes_through_known_relations():
    fixed = [{
        "subject_id": "PERS_meh",
        "predicate": "CEO_OF",
        "object_id": "COMP_abc",
        "evidence_text": "Mehmet Yılmaz, ABC Bilişim'in CEO'sudur.",
        "confidence": 0.9,
    }]
    ex = RelationExtractor(provider=StubProvider(fixed_relations=fixed))
    out = ex.extract("ABC Bilişim ... Mehmet Yılmaz CEO ...", entities=_make_entities())
    assert len(out.relations) == 1
    r = out.relations[0]
    assert r.subject_id == "PERS_meh"
    assert r.predicate == "CEO_OF"
    assert r.object_id == "COMP_abc"


def test_unknown_canonical_ids_are_filtered():
    """LLM halüsinasyonu: bilinmeyen ID üretirse extractor atmali."""
    fixed = [
        {
            "subject_id": "PERS_meh",
            "predicate": "CEO_OF",
            "object_id": "COMP_abc",
            "evidence_text": "valid",
            "confidence": 0.8,
        },
        {
            "subject_id": "PERS_HALLUCINATED",
            "predicate": "WORKS_AT",
            "object_id": "COMP_abc",
            "evidence_text": "halüsinasyon",
            "confidence": 0.7,
        },
    ]
    ex = RelationExtractor(provider=StubProvider(fixed_relations=fixed))
    out = ex.extract("text", entities=_make_entities())
    assert len(out.relations) == 1
    assert out.relations[0].subject_id == "PERS_meh"


def test_invalid_response_returns_empty_with_notes():
    """Şemaya uymayan provider yanıtı validasyon hatası vermeli, çökmemeli."""

    class BadProvider(StubProvider):
        def complete_json(self, *a, **kw):
            return {"relations": [{"subject_id": "X", "predicate": "Y"}]}  # eksik alanlar

    ex = RelationExtractor(provider=BadProvider())
    out = ex.extract("text", entities=_make_entities())
    assert out.relations == []
    assert out.notes is not None
    assert "validation_error" in out.notes


def test_provider_exception_returns_empty_with_notes():
    class FailingProvider(StubProvider):
        def complete_json(self, *a, **kw):
            raise RuntimeError("network down")

    ex = RelationExtractor(provider=FailingProvider())
    out = ex.extract("text", entities=_make_entities())
    assert out.relations == []
    assert out.notes is not None
    assert "llm_error" in out.notes
