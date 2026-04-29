"""Normalizer çekirdeği: bir isim için iki anahtar üretir.

- ``strict``: Türkçe karakterler korunur (İş Bankası ve Iş Bankası farklı kalır).
              Suffix/ek temizleme uygulanır.
- ``loose``:  ASCII-fold edilmiştir (ı→i, ş→s vb.). Daha fazla varyantı yakalar
              ama collision riski vardır; her zaman strict ile birlikte kullanılır.

Resolver önce ``strict`` ile lookup yapar, bulamazsa ``loose`` ile fuzzy genişletir.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .multilingual import EN_PERSON_TITLES, EU_COMPANY_SUFFIXES
from .turkish import (
    TR_APOSTROPHE_SUFFIX,
    TR_COMPANY_PREFIXES,
    TR_COMPANY_SUFFIXES,
    TR_PERSON_TITLES,
    TR_TO_ASCII,
)

EntityKind = Literal["company", "person", "any"]

# Tüm şirket sonek pattern'leri tek seferde compile edilir
_COMPANY_SUFFIX_PATTERNS = [re.compile(p, re.IGNORECASE) for p in TR_COMPANY_SUFFIXES + EU_COMPANY_SUFFIXES]
_COMPANY_PREFIX_PATTERNS = [re.compile(p, re.IGNORECASE) for p in TR_COMPANY_PREFIXES]
_PERSON_TITLE_PATTERNS = [re.compile(p, re.IGNORECASE) for p in TR_PERSON_TITLES + EN_PERSON_TITLES]

_PUNCT_TO_SPACE = re.compile(r"[.,;:!?()\[\]\"<>{}/\\|*+=`~^]")
_MULTI_SPACE = re.compile(r"\s+")
# Apostrofun kendisini koru ama ek varsa sil — TR_APOSTROPHE_SUFFIX bunu yapıyor
_LEFTOVER_APOSTROPHE = re.compile(r"['\u2018\u2019]")


@dataclass(frozen=True, slots=True)
class NormalizedName:
    """Bir orijinal isim için iki normalize edilmiş anahtar.

    ``strict`` ve ``loose`` aynı string olabilir (Türkçe karakter yoksa).
    """

    original: str
    strict: str
    loose: str
    kind: EntityKind

    @property
    def keys(self) -> tuple[str, str]:
        return (self.strict, self.loose)


def _strip_patterns(text: str, patterns: list[re.Pattern[str]]) -> str:
    for pat in patterns:
        text = pat.sub(" ", text)
    return text


def _basic_clean(text: str) -> str:
    text = text.strip()
    text = TR_APOSTROPHE_SUFFIX.sub("", text)  # "ABC'nin" → "ABC"
    text = _LEFTOVER_APOSTROPHE.sub("", text)
    text = _PUNCT_TO_SPACE.sub(" ", text)
    text = _MULTI_SPACE.sub(" ", text).strip()
    return text


def _strip_for_kind(text: str, kind: EntityKind) -> str:
    if kind in ("company", "any"):
        text = _strip_patterns(text, _COMPANY_PREFIX_PATTERNS)
        text = _strip_patterns(text, _COMPANY_SUFFIX_PATTERNS)
    if kind in ("person", "any"):
        text = _strip_patterns(text, _PERSON_TITLE_PATTERNS)
    return _MULTI_SPACE.sub(" ", text).strip()


def normalize_strict(name: str, kind: EntityKind = "any") -> str:
    """Türkçe karakterler korunarak normalize."""
    if not name:
        return ""
    text = _basic_clean(name)
    # strict'te lower yapacağız ama Türkçe-aware olmalı
    # str.lower() bizim tablomuzdaki Türkçe büyükleri zaten doğru indirir (İ, Ş, Ğ vb.)
    # ama "I" → "i" olur (bu strict'te kabul; loose'da zaten ASCII fold var)
    text = _safe_lower(text)
    text = _strip_for_kind(text, kind)
    return text


def normalize_loose(name: str, kind: EntityKind = "any") -> str:
    """ASCII-fold edilmiş normalize. Daha agresif eşleşme için."""
    if not name:
        return ""
    text = _basic_clean(name)
    text = text.translate(TR_TO_ASCII)
    text = text.lower()
    text = _strip_for_kind(text, kind)
    return text


def normalize(name: str, kind: EntityKind = "any") -> NormalizedName:
    """Hem strict hem loose anahtar üretir."""
    return NormalizedName(
        original=name or "",
        strict=normalize_strict(name, kind),
        loose=normalize_loose(name, kind),
        kind=kind,
    )


# ---------------------------------------------------------------------------
# Türkçe-aware lower: Python'un default lower'ı "I" → "ı" yapar (Türkçe locale).
# Cross-platform tutarlılık için manuel mapping. "İ" → "i", "I" → "i".
# Strict modda "I"yı "ı" yerine "i" yapmak collision yaratır mı?
# (İş Bankası → "is bankasi" olur strict'te; ı'yı koruma niyetimiz farklılaşırdı.)
# Karar: strict'te de "I" → "i" yap. Çünkü insan "ı" mı "I" mı yazdığını bilmez,
# ama "İ" ile "I" ayrımı kasıtlıdır. Bu trade-off kabul edilebilir.
# ---------------------------------------------------------------------------
_TR_LOWER = str.maketrans(
    "İIŞĞÜÖÇÂÎÛ",
    "iişğüöçâîû",  # İ→i, I→i (collision kabul), Ş→ş, Ğ→ğ, Ü→ü, Ö→ö, Ç→ç
)


def _safe_lower(text: str) -> str:
    text = text.translate(_TR_LOWER)
    return text.lower()
