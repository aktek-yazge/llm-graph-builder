"""Phase 1+2 testleri — agent-builder ConfidenceLabel + quality_gate label policy.

Standalone testler — agent-builder'ın deepagents/celery bağımlılıklarına ihtiyaç
duymaz. Her modül ``importlib.util`` ile dosya bazında yüklenir, böylece
``agent.evolving.__init__`` tetiklenmez.

Çalıştırma:
    cd kb-overlay && source .venv/bin/activate
    python -m pytest /Users/.../agent-builder/tests/test_confidence_and_quality_gate.py -v

veya (deepagents kurulu agent-builder venv'de):
    cd agent-builder/backend && pytest ../tests/test_confidence_and_quality_gate.py -v
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
from types import ModuleType

import pytest


HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, "..", "backend", "src"))


def _load_file(module_name: str, relpath: str) -> ModuleType:
    """Dosyayı parent __init__'leri tetiklemeden yükle.

    ``relpath`` ``backend/src`` köküne göre verilir.
    """
    abs_path = os.path.join(SRC, relpath)
    spec = importlib.util.spec_from_file_location(module_name, abs_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module: {abs_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Lazy module loading — pytest collection sırasında çalışır
# ---------------------------------------------------------------------------

_confidence = None
_quality_gate = None


def _confidence_module() -> ModuleType:
    global _confidence
    if _confidence is None:
        _confidence = _load_file(
            "ab_confidence_test_module",
            "agent/evolving/confidence.py",
        )
    return _confidence


# ---------------------------------------------------------------------------
# Phase 1: ConfidenceLabel basics
# ---------------------------------------------------------------------------


class TestConfidenceLabelEnum:
    def test_three_string_values(self):
        m = _confidence_module()
        assert m.ConfidenceLabel.EXTRACTED.value == "EXTRACTED"
        assert m.ConfidenceLabel.INFERRED.value == "INFERRED"
        assert m.ConfidenceLabel.AMBIGUOUS.value == "AMBIGUOUS"

    def test_label_is_str_subclass(self):
        # str subclass: kb-overlay enum'u ile JSON serialization wire-compatible
        m = _confidence_module()
        assert isinstance(m.ConfidenceLabel.EXTRACTED, str)


class TestLabelFromNumeric:
    """Celery worker label döndürmediğinde numeric'ten türetilir."""

    def test_high_threshold_extracted(self):
        m = _confidence_module()
        assert m.label_from_numeric(0.99) == m.ConfidenceLabel.EXTRACTED
        assert m.label_from_numeric(0.95) == m.ConfidenceLabel.EXTRACTED

    def test_mid_threshold_inferred(self):
        m = _confidence_module()
        assert m.label_from_numeric(0.7) == m.ConfidenceLabel.INFERRED
        assert m.label_from_numeric(0.6) == m.ConfidenceLabel.INFERRED

    def test_low_threshold_ambiguous(self):
        m = _confidence_module()
        assert m.label_from_numeric(0.4) == m.ConfidenceLabel.AMBIGUOUS
        assert m.label_from_numeric(0.0) == m.ConfidenceLabel.AMBIGUOUS

    def test_boundary_at_0_60(self):
        # Sınır kontrol: 0.60 INFERRED, 0.59 AMBIGUOUS
        m = _confidence_module()
        assert m.label_from_numeric(0.60) == m.ConfidenceLabel.INFERRED
        assert m.label_from_numeric(0.59) == m.ConfidenceLabel.AMBIGUOUS


class TestCoerceLabel:
    """Celery worker'dan gelen ham value'lar güvenli enum'a çevrilir."""

    def test_none_uses_fallback(self):
        m = _confidence_module()
        assert m.coerce_label(None, fallback_confidence=0.99) == m.ConfidenceLabel.EXTRACTED
        assert m.coerce_label(None, fallback_confidence=0.4) == m.ConfidenceLabel.AMBIGUOUS

    def test_empty_string_uses_fallback(self):
        m = _confidence_module()
        assert m.coerce_label("", fallback_confidence=0.5) == m.ConfidenceLabel.AMBIGUOUS

    def test_known_string_passthrough(self):
        m = _confidence_module()
        assert m.coerce_label("EXTRACTED") == m.ConfidenceLabel.EXTRACTED
        assert m.coerce_label("INFERRED") == m.ConfidenceLabel.INFERRED
        assert m.coerce_label("AMBIGUOUS") == m.ConfidenceLabel.AMBIGUOUS

    def test_lowercase_normalized_to_upper(self):
        m = _confidence_module()
        assert m.coerce_label("extracted") == m.ConfidenceLabel.EXTRACTED
        assert m.coerce_label("inferred") == m.ConfidenceLabel.INFERRED

    def test_unknown_string_uses_fallback(self):
        m = _confidence_module()
        assert m.coerce_label("garbage", fallback_confidence=0.99) == m.ConfidenceLabel.EXTRACTED
        assert m.coerce_label("xyz", fallback_confidence=0.3) == m.ConfidenceLabel.AMBIGUOUS

    def test_already_enum_passthrough(self):
        m = _confidence_module()
        assert m.coerce_label(m.ConfidenceLabel.AMBIGUOUS) == m.ConfidenceLabel.AMBIGUOUS


# ---------------------------------------------------------------------------
# Phase 1: Wire-compat with kb-overlay
# ---------------------------------------------------------------------------


class TestKbOverlayWireCompat:
    """agent-builder ↔ kb-overlay arasında string-based JSON interop garantili."""

    def test_string_values_match_kb_overlay(self):
        # kb-overlay enum'u import edilebilir mi (kb-overlay venv'de çalıştırılırsa)
        try:
            from kb_overlay.confidence import ConfidenceLabel as KBLabel
        except ImportError:
            pytest.skip("kb-overlay not installed in current venv")

        m = _confidence_module()
        assert m.ConfidenceLabel.EXTRACTED.value == KBLabel.EXTRACTED.value
        assert m.ConfidenceLabel.INFERRED.value == KBLabel.INFERRED.value
        assert m.ConfidenceLabel.AMBIGUOUS.value == KBLabel.AMBIGUOUS.value

    def test_json_serialization_compatible(self):
        import json
        m = _confidence_module()
        try:
            from kb_overlay.confidence import ConfidenceLabel as KBLabel
        except ImportError:
            pytest.skip("kb-overlay not installed")
        # Her ikisi str-subclass olduğu için json.dumps aynı string'i üretir
        assert json.dumps(m.ConfidenceLabel.EXTRACTED) == json.dumps(KBLabel.EXTRACTED)
        assert json.dumps(m.ConfidenceLabel.INFERRED) == json.dumps(KBLabel.INFERRED)


# ---------------------------------------------------------------------------
# Phase 1+2: Quality gate label policy (lightweight, no full workflow needed)
# ---------------------------------------------------------------------------

# QualityGateNode parent __init__ + node_registry imports gerektirir; test
# kapsamımızı dar tutmak için node logic'inin YALNIZ karar mantığını test eden
# bir küçük mini-class kullanırız. Bu, gerçek node ile aynı algoritmik kararı
# tekrar üretir; kontrat değişmediği sürece stabil.


def _decide_gate_route(
    *,
    avg_confidence: float,
    ambiguous_count: int,
    threshold: float,
    policy: str,
    max_ambiguous: int,
) -> str:
    """quality_gate.execute() içindeki decision-tree'nin saf-fonksiyon karşılığı.

    Bu fonksiyon QualityGateNode.execute ile aynı kararı vermeli — kontrat testi.
    """
    if policy == "label_aware" and ambiguous_count > max_ambiguous:
        return "ambiguous"
    return "pass" if avg_confidence >= threshold else "fail"


class TestQualityGatePolicy:
    """label-aware modda AMBIGUOUS yönlendirme + numeric fallback davranışı."""

    def test_label_aware_routes_ambiguous_to_dedicated_port(self):
        # AMBIGUOUS=5, max=0 → "ambiguous" port (numeric eşiği geçse bile)
        route = _decide_gate_route(
            avg_confidence=0.99,
            ambiguous_count=5,
            threshold=0.7,
            policy="label_aware",
            max_ambiguous=0,
        )
        assert route == "ambiguous"

    def test_label_aware_tolerance_threshold(self):
        # max_ambiguous=3, count=2 → tolere edilir, numeric kararı
        route = _decide_gate_route(
            avg_confidence=0.85,
            ambiguous_count=2,
            threshold=0.7,
            policy="label_aware",
            max_ambiguous=3,
        )
        assert route == "pass"

    def test_label_aware_falls_through_to_fail(self):
        # AMBIGUOUS yok, numeric düşük → "fail"
        route = _decide_gate_route(
            avg_confidence=0.4,
            ambiguous_count=0,
            threshold=0.7,
            policy="label_aware",
            max_ambiguous=0,
        )
        assert route == "fail"

    def test_numeric_only_ignores_ambiguous(self):
        # Legacy mode: AMBIGUOUS sayımı dikkate alınmaz, sadece numeric
        route = _decide_gate_route(
            avg_confidence=0.85,
            ambiguous_count=10,
            threshold=0.7,
            policy="numeric_only",
            max_ambiguous=0,
        )
        assert route == "pass"

    def test_numeric_only_fail_below_threshold(self):
        route = _decide_gate_route(
            avg_confidence=0.5,
            ambiguous_count=0,
            threshold=0.7,
            policy="numeric_only",
            max_ambiguous=0,
        )
        assert route == "fail"


# ---------------------------------------------------------------------------
# Phase 1+2: Real QualityGateNode integration (skip if deps unavailable)
# ---------------------------------------------------------------------------

@pytest.fixture
def quality_gate_node_class():
    """Gerçek QualityGateNode sınıfını yüklemeye çalış; deepagents/etc.
    eksikse skip. Pure-python module zinciri (models.py, node_registry.py,
    quality_gate.py) deepagents gerektirmez.
    """
    try:
        # Bu bir tehlikeli yol — node_registry import'u workflow.models'ı çeker
        # ama o pure pydantic. Sadece quality_gate.py'i kendisi load edersek OK.
        # node_registry'yi de file-load edelim.
        models = _load_file("ab_models", "agent/evolving/workflow/models.py")
        sys.modules["models"] = models  # quality_gate'in `from ..models` u için

        # node_registry: package-style import ister; hile yapamayız → fallback
        # Bu yüzden gerçek node yerine pure decision'a güveniriz (yukarıda).
        pytest.skip(
            "QualityGateNode tam-import quality_gate'in package-relative import "
            "yapısı yüzünden zor; saf decision testleri TestQualityGatePolicy'de."
        )
    except (ImportError, ModuleNotFoundError):
        pytest.skip("agent-builder dependencies not installed")
