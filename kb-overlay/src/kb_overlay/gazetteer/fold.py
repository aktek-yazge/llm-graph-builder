"""Gazetteer-spesifik 1-1 fold transformasyonu.

`normalize.core.normalize_loose`'tan farklı: suffix temizlemez ve multi-space
collapse yapmaz. Bu sayede transform tamamen char-by-char (insertion/deletion
yok), offset map'lemesine gerek kalmadan span'ler 1-1 korunur.
"""
from __future__ import annotations

import string

from ..normalize.turkish import TR_TO_ASCII

# Noktalama karakterleri → boşluk (her biri tek karakter, offset korunur)
_PUNCT_CHARS = set(string.punctuation) - {"'", "\u2018", "\u2019"}  # apostrof'u koru
_PUNCT_TRANSLATION = str.maketrans({c: " " for c in _PUNCT_CHARS})


def gazetteer_fold(text: str) -> str:
    """1-karakter → 1-karakter fold. Offset bozulmaz.

    Adımlar:
      1. Türkçe karakterleri ASCII'ye fold (ı→i, ş→s, ...)
      2. Lowercase
      3. Noktalama → boşluk

    NOT:
      - Multi-space collapse YAPMAZ → "ABC  Bilişim" ve "ABC Bilişim" farklı
        görünür. Belge ile trie key'i aynı transformdan geçtiği için bu
        normalde sorun değil; ama bilinen bir limitasyondur.
      - Apostrof noktalama olarak değil, kelime parçası olarak kabul edilir
        (çünkü "ABC'nin" → "ABC nin" olursa "ABC" alias'ı match etmez).
    """
    if not text:
        return ""
    text = text.translate(TR_TO_ASCII)
    text = text.lower()
    text = text.translate(_PUNCT_TRANSLATION)
    return text


def gazetteer_fold_keep_offsets(text: str) -> tuple[str, list[int]]:
    """Fold + her index için orijinal offset'i döndürür (debug/back-mapping).

    Gerçi 1-1 mapping olduğundan ``offsets[i] == i`` olmalı; bu fonksiyon
    sadece invariant'i doğrulamak için var.
    """
    folded = gazetteer_fold(text)
    if len(folded) != len(text):
        raise RuntimeError(
            f"gazetteer_fold not 1-1: input len={len(text)}, output len={len(folded)}"
        )
    return folded, list(range(len(text)))
