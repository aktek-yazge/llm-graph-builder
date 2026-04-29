"""Phase 2 testleri — Strict Pydantic schema + per-relation salvage + retry.

LLM çıktısı schema'ya uymadığında doğru davranışı doğrular:
1. Tek relation extra field'a sahipse: salvage (geçerlileri tut)
2. Tüm response bozuksa: 1 retry, sıkılaştırılmış prompt
3. Retry de fail ederse: boş + validation_error notes
"""
from __future__ import annotations

from typing import Optional

import pytest
from pydantic import ValidationError

from kb_overlay.confidence import ConfidenceLabel
from kb_overlay.relations.extractor import (
    LLMProvider,
    RelationExtractor,
    ResolvedEntityInput,
    _combine_labels,
    _salvage_relations,
)
from kb_overlay.relations.schemas import (
    ExtractedRelation,
    RelationExtractionResponse,
)


# ---------------------------------------------------------------------------
# Mock providers
# ---------------------------------------------------------------------------


class _ScriptedProvider(LLMProvider):
    """Sırasıyla farklı response döndüren mock provider — retry path'lerini test
    eder. ``responses[i]`` i'inci çağrıda döner; bittikten sonra son cevap.
    """

    name = "scripted"

    def __init__(self, responses: list[dict]):
        self.responses = responses
        self.call_count = 0
        self.last_system_prompt: Optional[str] = None

    def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        json_schema: Optional[dict] = None,
        temperature: float = 0.0,
    ) -> dict:
        self.last_system_prompt = system_prompt
        idx = min(self.call_count, len(self.responses) - 1)
        self.call_count += 1
        return self.responses[idx]


def _ents() -> list[ResolvedEntityInput]:
    return [
        ResolvedEntityInput(
            canonical_id="A", canonical_name="ACo", entity_type="company",
            surface="A", span_start=0, span_end=1,
            confidence_label=ConfidenceLabel.EXTRACTED,
        ),
        ResolvedEntityInput(
            canonical_id="B", canonical_name="BCo", entity_type="company",
            surface="B", span_start=2, span_end=3,
            confidence_label=ConfidenceLabel.EXTRACTED,
        ),
    ]


# ---------------------------------------------------------------------------
# Schema strictness
# ---------------------------------------------------------------------------


class TestSchemaStrictness:
    def test_extra_field_at_top_level_rejected(self):
        with pytest.raises(ValidationError):
            RelationExtractionResponse.model_validate(
                {"relations": [], "evil_top": "no"}
            )

    def test_extra_field_in_relation_rejected(self):
        with pytest.raises(ValidationError):
            ExtractedRelation.model_validate(
                {
                    "subject_id": "A", "predicate": "X", "object_id": "B",
                    "evidence_text": "e", "halluc": "yes",
                }
            )

    def test_default_confidence_label_inferred(self):
        r = ExtractedRelation(
            subject_id="A", predicate="X", object_id="B", evidence_text="e",
        )
        assert r.confidence_label == ConfidenceLabel.INFERRED


# ---------------------------------------------------------------------------
# Per-relation salvage
# ---------------------------------------------------------------------------


class TestPerRelationSalvage:
    def test_salvage_keeps_valid_drops_invalid(self):
        raw = {
            "relations": [
                {"subject_id": "A", "predicate": "X", "object_id": "B", "evidence_text": "ok"},
                {"subject_id": "A", "predicate": "X", "object_id": "B", "evidence_text": "bad",
                 "halluc": "yes"},
                {"subject_id": "A", "predicate": "Y", "object_id": "B", "evidence_text": "ok2"},
            ],
        }
        salvaged, discarded = _salvage_relations(raw)
        assert len(salvaged) == 2
        assert discarded == 1
        assert salvaged[0].evidence_text == "ok"
        assert salvaged[1].evidence_text == "ok2"

    def test_salvage_handles_non_dict_raw(self):
        salvaged, discarded = _salvage_relations("not a dict")
        assert salvaged == [] and discarded == 0

    def test_salvage_handles_missing_relations_key(self):
        salvaged, discarded = _salvage_relations({"other": "stuff"})
        assert salvaged == [] and discarded == 0


# ---------------------------------------------------------------------------
# Full extractor flow with retry
# ---------------------------------------------------------------------------


class TestExtractorRetryPolicy:
    def test_clean_response_no_retry_no_salvage(self):
        provider = _ScriptedProvider([
            {"relations": [
                {"subject_id": "A", "predicate": "CEO_OF", "object_id": "B",
                 "evidence_text": "X is CEO"},
            ]},
        ])
        ex = RelationExtractor(provider=provider)
        resp = ex.extract("text", _ents())
        assert provider.call_count == 1, "clean response should not trigger retry"
        assert len(resp.relations) == 1
        assert resp.notes is None or "validation_error" not in (resp.notes or "")

    def test_top_level_extra_triggers_retry(self):
        # 1. cevap top-level extra ile FAIL → salvage da fail (relations boş) → retry
        provider = _ScriptedProvider([
            {"relations": [], "evil_top_field": "no"},
            {"relations": [
                {"subject_id": "A", "predicate": "X", "object_id": "B",
                 "evidence_text": "ok"},
            ]},
        ])
        ex = RelationExtractor(provider=provider)
        resp = ex.extract("text", _ents())
        assert provider.call_count == 2, "should retry once"
        assert len(resp.relations) == 1
        assert "recovered_after_retry" in (resp.notes or "")

    def test_per_relation_salvage_no_retry(self):
        # Top-level OK ama bir relation extra field'a sahip → salvage işler, retry GEREKMEZ
        provider = _ScriptedProvider([
            {"relations": [
                {"subject_id": "A", "predicate": "X", "object_id": "B", "evidence_text": "ok"},
                {"subject_id": "A", "predicate": "Y", "object_id": "B",
                 "evidence_text": "bad", "halluc": "yes"},
            ]},
        ])
        ex = RelationExtractor(provider=provider)
        resp = ex.extract("text", _ents())
        # Top-level extra=forbid sayesinde model_validate fail eder; salvage devreye girer
        assert provider.call_count == 1, "salvage should not trigger retry"
        assert len(resp.relations) == 1
        assert "salvaged_after_validation_error" in (resp.notes or "")
        assert "discarded=1" in (resp.notes or "")

    def test_retry_disabled(self):
        provider = _ScriptedProvider([
            {"relations": [], "evil": "no"},
        ])
        ex = RelationExtractor(provider=provider, enable_retry_on_validation_error=False)
        resp = ex.extract("text", _ents())
        assert provider.call_count == 1, "retry disabled — only 1 call"
        assert len(resp.relations) == 0
        assert "validation_error_no_retry" in (resp.notes or "")

    def test_both_calls_fail(self):
        provider = _ScriptedProvider([
            {"relations": [], "evil": "no"},
            {"relations": [], "still_bad": "yes"},
        ])
        ex = RelationExtractor(provider=provider)
        resp = ex.extract("text", _ents())
        assert provider.call_count == 2
        assert len(resp.relations) == 0
        assert "validation_error_after_retry" in (resp.notes or "")

    def test_retry_prompt_contains_feedback(self):
        provider = _ScriptedProvider([
            {"relations": [], "evil": "no"},
            {"relations": [
                {"subject_id": "A", "predicate": "X", "object_id": "B", "evidence_text": "ok"},
            ]},
        ])
        ex = RelationExtractor(provider=provider)
        ex.extract("text", _ents())
        # Retry prompt'unda "ONEMLI" + "EXTRA alan EKLEME" feedback'i olmalı
        assert "ONEMLI" in (provider.last_system_prompt or "")
        assert "EKLEME" in (provider.last_system_prompt or "")


# ---------------------------------------------------------------------------
# Label combination
# ---------------------------------------------------------------------------


class TestCombineLabels:
    def test_all_extracted_stays_extracted(self):
        assert _combine_labels(
            ConfidenceLabel.EXTRACTED, ConfidenceLabel.EXTRACTED, ConfidenceLabel.EXTRACTED,
        ) == ConfidenceLabel.EXTRACTED

    def test_any_inferred_demotes_to_inferred(self):
        assert _combine_labels(
            ConfidenceLabel.EXTRACTED, ConfidenceLabel.INFERRED,
        ) == ConfidenceLabel.INFERRED

    def test_any_ambiguous_demotes_to_ambiguous(self):
        assert _combine_labels(
            ConfidenceLabel.EXTRACTED, ConfidenceLabel.AMBIGUOUS, ConfidenceLabel.INFERRED,
        ) == ConfidenceLabel.AMBIGUOUS

    def test_relation_with_ambiguous_subject(self):
        # Subject AMBIGUOUS → relation AMBIGUOUS olmalı (LLM ne derse desin)
        ents = [
            ResolvedEntityInput(
                canonical_id="A", canonical_name="ACo", entity_type="company",
                surface="A", span_start=0, span_end=1,
                confidence_label=ConfidenceLabel.AMBIGUOUS,
            ),
            ResolvedEntityInput(
                canonical_id="B", canonical_name="BCo", entity_type="company",
                surface="B", span_start=2, span_end=3,
                confidence_label=ConfidenceLabel.EXTRACTED,
            ),
        ]
        provider = _ScriptedProvider([
            {"relations": [
                {"subject_id": "A", "predicate": "CEO_OF", "object_id": "B",
                 "evidence_text": "X", "confidence_label": "EXTRACTED"},
            ]},
        ])
        ex = RelationExtractor(provider=provider)
        resp = ex.extract("text", ents)
        assert resp.relations[0].confidence_label == ConfidenceLabel.AMBIGUOUS, (
            "subject AMBIGUOUS → relation AMBIGUOUS, even if LLM said EXTRACTED"
        )
