"""OCR text preprocessing.

OCR çıktılarında satır sonu (\n) ile bölünen kelimeler/cümleler/şirket
adları LLM'in entity tespitini bozar. Örnek:

    Aksa Akrilik Kimya Sanayii
    Anonim Şirketi

LLM "Aksa Akrilik Kimya Sanayii"yi yakalar ama "Anonim Şirketi" ayrı satırda
olduğu için tam kanonik adı (`Aksa Akrilik Kimya Sanayii Anonim Şirketi`)
oluşturamaz; resolver da kısa formu yanlış bir entity'ye fuzzy match yapar.

Bu modül, **belge-spesifik olmayan** sistemik bir düzeltme uygular:
yapay satır kırılmalarını birleştirir, gerçek paragraf/başlık/liste
sınırlarını korur. Tüm OCR belgeleri için aynı kuralları uygular.

Algoritma — satır sınıflandırma + komşuluk:

Her satır üç sınıftan birine düşer:
- ``blank``        : sadece whitespace → paragraf sınırı
- ``preserve``     : başlık / liste / sayfa marker / parantez annotation
- ``continuation`` : sıradan metin, birleştirme adayı

Birleştirme kuralı: yalnızca **iki ardışık ``continuation``** birleşir.
Aralarında ``blank`` veya ``preserve`` varsa hep ayrı kalırlar.

Continuation'lar arasında bile şu durumlarda birleştirme yapılmaz:
- Önceki satır cümle terminatörü (.!?:;) ile bitiyorsa.

Hyphenation: önceki satır "kelime-" şeklinde bitip sonraki lowercase ile
başlıyorsa tire kaldırılıp boşluksuz birleştirilir ("şir-\\nket" → "şirket").

Span offset'leri: bu fonksiyon ingestion'ın **en başında** çağrılır;
sonraki tüm pipeline (NER, gazetteer, resolver) normalized text üzerinde
çalışır → span'lar tutarlı kalır.
"""
from __future__ import annotations

import re
from typing import Final

# Cümleyi kapatan ASCII + Unicode karakterler
_SENTENCE_TERMINATORS: Final[frozenset[str]] = frozenset(
    ".!?:;\u2026\u3002\uff01\uff1f"  # ... 。 ! ?
)

# Madde işareti / liste başlangıcı (Türkçe karakterler dahil)
_LIST_MARKER_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:"
    r"[-*\u2022\u2023\u25E6\u2043]\s"            # bullet: - * • ‣ ◦ ⁃
    r"|\d+[.)]\s"                                  # 1. 2)
    r"|[a-zA-Z\u00E7\u00C7\u011F\u011E\u0131\u0130\u00F6\u00D6\u015F\u015E\u00FC\u00DC][.)]\s"  # a. A) (TR)
    r"|\([ivxlcdmIVXLCDM]+\)\s"                    # (i) (ii)
    r"|\([a-zA-Z\u00E7\u00C7\u011F\u011E\u0131\u0130\u00F6\u00D6\u015F\u015E\u00FC\u00DC]\)\s"  # (a) (b)
    r")"
)

# Korunan satır başlangıçları (markdown / sayfa marker / code / table / quote)
_PRESERVE_PREFIXES: Final[tuple[str, ...]] = (
    "#",          # markdown heading
    "[[PAGE:",    # OCR sayfa marker (kb-overlay konvansiyonu)
    "[PAGE:",
    "```",        # code fence
    ">",          # blockquote
    "|",          # markdown table
)


def normalize_ocr_text(text: str) -> str:
    """OCR satır kırılmalarını birleştirip temiz metin döndür.

    Parameters
    ----------
    text : str
        OCR çıktısı (markdown veya düz metin).

    Returns
    -------
    str
        Yapay satır kırılmaları temizlenmiş metin. Boş input → boş output.

    Notes
    -----
    Bu fonksiyon **idempotent**: ``f(f(x)) == f(x)``.

    Algoritma O(n): her satır tek seferde sınıflandırılır ve birleştirilir.
    """
    if not text:
        return text

    lines = text.splitlines(keepends=False)
    if not lines:
        return text

    out: list[str] = [lines[0]]
    out_kinds: list[str] = [_classify_line(lines[0])]

    for cur in lines[1:]:
        cur_kind = _classify_line(cur)
        prev_kind = out_kinds[-1]

        if prev_kind != "continuation" or cur_kind != "continuation":
            out.append(cur)
            out_kinds.append(cur_kind)
            continue

        # İki continuation arasında birleştirme adaylığı
        prev_stripped = out[-1].rstrip()
        cur_lstripped = cur.lstrip()

        if _is_hyphenation(prev_stripped, cur_lstripped):
            out[-1] = prev_stripped[:-1] + cur_lstripped
            # out_kinds[-1] zaten "continuation"; hyphenation sonrası da öyle
            continue

        if prev_stripped and prev_stripped[-1] in _SENTENCE_TERMINATORS:
            out.append(cur)
            out_kinds.append(cur_kind)
            continue

        out[-1] = prev_stripped + " " + cur_lstripped

    result = "\n".join(out)
    # splitlines() trailing newline'ı düşürür; orijinaldeki trailing newline'ı koru
    if text.endswith(("\n", "\r")):
        result += "\n"
    return result


def _classify_line(line: str) -> str:
    """Satırı 'blank' | 'preserve' | 'continuation' olarak sınıflandır."""
    stripped = line.strip()
    if not stripped:
        return "blank"
    if any(stripped.startswith(p) for p in _PRESERVE_PREFIXES):
        return "preserve"
    if _LIST_MARKER_RE.match(line.lstrip()):
        return "preserve"
    # Tek satırlık parantez annotation: "(Kaşe ve İmzalar)"
    if stripped.startswith("(") and stripped.endswith(")"):
        return "preserve"
    return "continuation"


def _is_hyphenation(prev_stripped: str, cur_lstripped: str) -> bool:
    """Önceki satır kelime ortasında tire ile mi bitti?

    "şir-" → sonraki satır lowercase ile başlamalı ki gerçek hyphenation olsun
    (yoksa "Bay-" "Bayan" gibi vakaları yanlış birleştirir).
    """
    if len(prev_stripped) < 2 or not prev_stripped.endswith("-"):
        return False
    if not prev_stripped[-2].isalpha():
        return False
    if not cur_lstripped or not cur_lstripped[0].isalpha():
        return False
    return cur_lstripped[0].islower()
