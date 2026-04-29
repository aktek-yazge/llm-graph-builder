"""Relation schemas için Pydantic validasyon testleri."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from kb_overlay.relations.schemas import (
    ExtractedRelation,
    RelationExtractionResponse,
    response_json_schema,
)


def test_valid_relation_passes():
    r = ExtractedRelation(
        subject_id="PERS_001",
        predicate="CEO_OF",
        object_id="COMP_001",
        evidence_text="Mehmet Yılmaz, ABC Bilişim'in CEO'sudur.",
        confidence=0.92,
    )
    assert r.subject_id == "PERS_001"
    assert r.predicate == "CEO_OF"
    assert 0.0 <= r.confidence <= 1.0


def test_confidence_out_of_range_rejected():
    with pytest.raises(ValidationError):
        ExtractedRelation(
            subject_id="A",
            predicate="WORKS_AT",
            object_id="B",
            evidence_text="x",
            confidence=1.5,
        )


def test_evidence_max_length_enforced():
    long_text = "A" * 500
    with pytest.raises(ValidationError):
        ExtractedRelation(
            subject_id="A",
            predicate="WORKS_AT",
            object_id="B",
            evidence_text=long_text,
        )


def test_response_with_empty_relations_is_valid():
    resp = RelationExtractionResponse(relations=[])
    assert resp.relations == []
    assert resp.notes is None


def test_response_with_multiple_relations():
    resp = RelationExtractionResponse(
        relations=[
            ExtractedRelation(
                subject_id="P1",
                predicate="WORKS_AT",
                object_id="C1",
                evidence_text="X works at Y",
                confidence=0.7,
            ),
            ExtractedRelation(
                subject_id="P2",
                predicate="CEO_OF",
                object_id="C1",
                evidence_text="Z is CEO of Y",
                confidence=0.95,
            ),
        ],
        notes="extracted from doc-1",
    )
    assert len(resp.relations) == 2


def test_json_schema_export_contains_required_fields():
    schema = response_json_schema()
    assert schema["type"] == "object"
    assert "properties" in schema
    assert "relations" in schema["properties"]
