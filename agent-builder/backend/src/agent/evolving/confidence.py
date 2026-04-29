"""ConfidenceLabel enum — agent-builder tarafı.

graphify projesinden adopt edildi (EXTRACTED / INFERRED / AMBIGUOUS).
String-based enum kb-overlay'in ConfidenceLabel'ı ile interop sağlar — wire
formatı aynı, JSON serialization aynı.

Kullanım:
- entity_extractor: Celery task çıktısındaki nodes/edges'e confidence_label ekler
- quality_gate: label-based policy (AMBIGUOUS → ambiguous output port)
- agent_knowledge: extracted_entity / extracted_relation kayıtlarında saklanır
"""
from __future__ import annotations

from enum import Enum
from typing import Optional


class ConfidenceLabel(str, Enum):
    """Bir extraction kaydının emniyet kaynağı.

    - EXTRACTED  → kaynaktan birebir geldi (deterministik). Doğrulamaya gerek yok.
    - INFERRED   → algoritmik tahmin (LLM, fuzzy, vector). Numeric eşik uygulanır.
    - AMBIGUOUS  → çelişki var; quality gate ne olursa olsun human review'a yönlenir.
    """

    EXTRACTED = "EXTRACTED"
    INFERRED = "INFERRED"
    AMBIGUOUS = "AMBIGUOUS"


# Numeric confidence → label fallback mapping. Celery worker explicit
# confidence_label döndürmediğinde bu eşiklere düşeriz:
#   >= 0.95 → EXTRACTED  (neredeyse kesin, muhtemelen deterministik kaynak)
#   >= 0.60 → INFERRED   (LLM/fuzzy/algorithm tahmini)
#   < 0.60  → AMBIGUOUS  (düşük güven — manuel inceleme şart)
LABEL_FROM_NUMERIC_HIGH = 0.95
LABEL_FROM_NUMERIC_MID = 0.60


def label_from_numeric(confidence: float) -> ConfidenceLabel:
    """Numeric confidence'tan fallback ConfidenceLabel türet."""
    if confidence >= LABEL_FROM_NUMERIC_HIGH:
        return ConfidenceLabel.EXTRACTED
    if confidence >= LABEL_FROM_NUMERIC_MID:
        return ConfidenceLabel.INFERRED
    return ConfidenceLabel.AMBIGUOUS


def coerce_label(
    raw: Optional[str | ConfidenceLabel],
    *,
    fallback_confidence: float = 1.0,
) -> ConfidenceLabel:
    """Dış dünyadan gelen value'yu güvenli ConfidenceLabel'a çevir.

    - None ya da boş string → fallback_confidence'tan türet
    - Tanınmayan string → fallback_confidence'tan türet (uyarısız default)
    - Geçerli enum value → olduğu gibi
    """
    if raw is None or raw == "":
        return label_from_numeric(fallback_confidence)
    if isinstance(raw, ConfidenceLabel):
        return raw
    try:
        return ConfidenceLabel(str(raw).upper())
    except ValueError:
        return label_from_numeric(fallback_confidence)


__all__ = [
    "ConfidenceLabel",
    "LABEL_FROM_NUMERIC_HIGH",
    "LABEL_FROM_NUMERIC_MID",
    "coerce_label",
    "label_from_numeric",
]
