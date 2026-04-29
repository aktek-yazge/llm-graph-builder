"""Bağımsız ConfidenceLabel enum'u — circular import'tan kaçınmak için top-level modül.

graphify projesinden adopt edildi (EXTRACTED / INFERRED / AMBIGUOUS).
Bu modül başka bir kb_overlay modülünden import yapmaz, böylece her yerden
güvenle import edilebilir (resolver, relations, discovery, neo4j_store).

Numeric ``confidence`` ile birlikte kullanılır:

    confidence       → 0.0–1.0, "ne kadar eminsin"
    confidence_label → kategorik, "emniyetin tipi"

Quality gate / review queue / agent UI label'a göre farklı politika uygulayabilir;
örn. AMBIGUOUS olanlar numeric eşik geçse bile her zaman insan onayına gönderilir.
"""
from __future__ import annotations

from enum import Enum


class ConfidenceLabel(str, Enum):
    """Bir extraction kaydının emniyet kaynağı.

    - EXTRACTED  → kaynaktan birebir geldi (gazetteer hit, exact lookup,
                   JSON'dan direkt alıntı). Doğrulamaya gerek yok.
    - INFERRED   → algoritmik tahmin (fuzzy match, vector similarity, LLM'in
                   pattern'den çıkardığı, NER'in ilk gördüğü). Numeric
                   confidence eşiğine göre quality_gate'ten geçebilir.
    - AMBIGUOUS  → birden fazla aday eşit ya da çelişki. Quality gate ne olursa
                   olsun human review'a yönlendirir.
    """

    EXTRACTED = "EXTRACTED"
    INFERRED = "INFERRED"
    AMBIGUOUS = "AMBIGUOUS"


__all__ = ["ConfidenceLabel"]
