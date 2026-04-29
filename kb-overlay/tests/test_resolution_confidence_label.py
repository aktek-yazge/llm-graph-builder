"""Phase 1 testleri — ResolutionStage → ConfidenceLabel mapping.

Cascading resolver'ın her stage'inden çıkan ConfidenceLabel'ın graphify'ın
3-değerli vocabulary'sine doğru çevrildiğini doğrular.
"""
from __future__ import annotations

import pytest

from kb_overlay.confidence import ConfidenceLabel
from kb_overlay.resolver.cascading import (
    ResolutionResult,
    ResolutionStage,
    label_from_stage,
)


class TestStageToLabelMapping:
    """label_from_stage() helper'ının her stage için doğru label döndürdüğünü doğrular."""

    def test_exact_strict_is_extracted(self):
        assert label_from_stage(ResolutionStage.EXACT_STRICT) == ConfidenceLabel.EXTRACTED

    def test_exact_loose_is_extracted(self):
        assert label_from_stage(ResolutionStage.EXACT_LOOSE) == ConfidenceLabel.EXTRACTED

    def test_fuzzy_is_inferred(self):
        assert label_from_stage(ResolutionStage.FUZZY) == ConfidenceLabel.INFERRED

    def test_vector_is_inferred(self):
        assert label_from_stage(ResolutionStage.VECTOR) == ConfidenceLabel.INFERRED

    def test_new_candidate_is_inferred(self):
        assert label_from_stage(ResolutionStage.NEW_CANDIDATE) == ConfidenceLabel.INFERRED

    def test_ambiguous_stage_is_ambiguous_label(self):
        assert label_from_stage(ResolutionStage.AMBIGUOUS) == ConfidenceLabel.AMBIGUOUS

    def test_is_ambiguous_flag_overrides_stage(self):
        # FUZZY normalde INFERRED'a düşer ama is_ambiguous=True ise AMBIGUOUS olur
        assert (
            label_from_stage(ResolutionStage.FUZZY, is_ambiguous=True)
            == ConfidenceLabel.AMBIGUOUS
        )
        # EXACT_STRICT bile is_ambiguous ile AMBIGUOUS olur
        assert (
            label_from_stage(ResolutionStage.EXACT_STRICT, is_ambiguous=True)
            == ConfidenceLabel.AMBIGUOUS
        )


class TestResolutionResultDerivedLabel:
    """ResolutionResult.confidence_label property'sinin stage'den türediğini doğrular."""

    def test_exact_strict_result_is_extracted(self):
        r = ResolutionResult(
            surface_form="ABC", canonical_id="C1",
            stage=ResolutionStage.EXACT_STRICT, confidence=1.0,
        )
        assert r.confidence_label == ConfidenceLabel.EXTRACTED

    def test_fuzzy_result_is_inferred(self):
        r = ResolutionResult(
            surface_form="ABC", canonical_id="C1",
            stage=ResolutionStage.FUZZY, confidence=0.85,
        )
        assert r.confidence_label == ConfidenceLabel.INFERRED

    def test_ambiguous_flag_overrides_strict_stage(self):
        r = ResolutionResult(
            surface_form="ABC", canonical_id=None,
            stage=ResolutionStage.EXACT_STRICT, confidence=0.0,
            is_ambiguous=True,
        )
        assert r.confidence_label == ConfidenceLabel.AMBIGUOUS

    def test_new_candidate_is_inferred(self):
        r = ResolutionResult(
            surface_form="NewCo", canonical_id=None,
            stage=ResolutionStage.NEW_CANDIDATE, confidence=0.0,
            is_new=True,
        )
        assert r.confidence_label == ConfidenceLabel.INFERRED


class TestLabelEnumStability:
    """ConfidenceLabel enum string-based, agent-builder ile interop için kritik."""

    def test_string_values_are_stable(self):
        assert ConfidenceLabel.EXTRACTED.value == "EXTRACTED"
        assert ConfidenceLabel.INFERRED.value == "INFERRED"
        assert ConfidenceLabel.AMBIGUOUS.value == "AMBIGUOUS"

    def test_label_is_str_subclass(self):
        # str subclass olmak json.dumps'da otomatik string'e dönmesini sağlar
        assert isinstance(ConfidenceLabel.EXTRACTED, str)
        assert ConfidenceLabel.EXTRACTED == "EXTRACTED"

    def test_construction_from_string(self):
        assert ConfidenceLabel("EXTRACTED") == ConfidenceLabel.EXTRACTED
        with pytest.raises(ValueError):
            ConfidenceLabel("UNKNOWN_LABEL")
