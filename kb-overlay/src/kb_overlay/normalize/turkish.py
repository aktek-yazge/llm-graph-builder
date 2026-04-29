"""Türkçe-spesifik şirket sonekleri, kişi unvanları ve ek (suffix) pattern'leri.

Buradaki listeler regex pattern'lerdir; case-insensitive uygulanırlar (re.I).
Listeler genişletilmeye açıktır — yeni varyant her zaman eklenebilir.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Şirket sonekleri (suffix). \b sınırlarıyla sözcük olarak yakalanır.
# Örnekler:
#   "ABC Bilişim A.Ş."        → "ABC Bilişim"
#   "XYZ Holding Ltd. Şti."   → "XYZ Holding"
#   "TEB Sanayi Ticaret A.Ş." → "TEB"
# ---------------------------------------------------------------------------
TR_COMPANY_SUFFIXES: list[str] = [
    r"\banonim\s+şirket(?:i)?\b",
    r"\ba\.?\s*ş\.?\b",
    r"\blimited\s+şirket(?:i)?\b",
    r"\bltd\.?\s*şti\.?\b",
    r"\bltd\.?\b",
    r"\bsanayi(?:\s+ve)?\s+ticaret\b",
    r"\bticaret(?:\s+ve)?\s+sanayi\b",
    r"\bsan\.?\s*ve\s*tic\.?\b",
    r"\bsan\.?\s*tic\.?\b",
    r"\btic\.?\s*san\.?\b",
    r"\bsanayi\b",
    r"\bticaret\b",
    r"\bholding\b",
    r"\bgrup\b",
    r"\bgrubu\b",
    r"\bkurumsal\b",
    r"\bortaklığı\b",
    r"\bkollektif\s+şirket(?:i)?\b",
    r"\bkomandit\s+şirket(?:i)?\b",
    r"\bkooperatif(?:i)?\b",
    r"\bvakfı\b",
    r"\bvakfi\b",
    r"\bderneği\b",
    r"\bdernegi\b",
]

# Kurumsal prefix'ler
TR_COMPANY_PREFIXES: list[str] = [
    r"^t\.?\s*c\.?\s+",  # T.C.
    r"^türkiye\s+cumhuriyeti\s+",
]

# Kişi unvan ve sıfatları
TR_PERSON_TITLES: list[str] = [
    r"\bsayın\b",
    r"\bdr\.?\b",
    r"\bprof\.?\b",
    r"\bdoç\.?\b",
    r"\byrd\.?\s*doç\.?\b",
    r"\bav\.?\b",
    r"\bavukat\b",
    r"\bmüh\.?\b",
    r"\bmühendis\b",
    r"\bsmmm\b",
    r"\bymm\b",
    r"\bbay\b",
    r"\bbayan\b",
    r"\bsn\.?\b",
]

# ---------------------------------------------------------------------------
# Türkçe ekler (genitive, locative, ablative vb.) — tokenleştirme öncesi temizlenir.
# Apostrofla ayrılmış ekleri de yakalar: "ABC Bilişim'in" → "ABC Bilişim"
# ---------------------------------------------------------------------------
# Türkçe apostrof + ek temizleyici. Apostroftan sonra gelen çekim ekini siler.
# Örn: "ABC Bilişim'in" → "ABC Bilişim", "Ankara'da" → "Ankara"
# Sıralama önemli: en uzun pattern önce yazılır ki "nin" "in"den önce yakalansın.
# \u0131 = ı (dotless i), \u2019 = ', \u2018 = '
TR_APOSTROPHE_SUFFIX = re.compile(
    r"['\u2019\u2018]\s*(?:"
    # 4+ karakter ekler önce (en spesifik)
    r"larından|lerinden|larında|lerinde|lardan|lerden|larda|lerde|"
    r"nde[\u0131i]?n?|ndan|nden|"
    # 3 karakter ekler
    r"n[\u0131i]n|nun|nün|"
    r"dan|den|tan|ten|"
    r"yla|yle|"
    # 2 karakter ekler
    r"[\u0131i]n|un|ün|"
    r"da|de|ta|te|"
    r"ya|ye|"
    r"la|le|"
    r"y[\u0131iuü]|"
    r"s[\u0131i]|"
    r"l[\u0131i]k?|"
    r"d[\u0131iuü]r|t[\u0131iuü]r|"
    r"m[\u0131iuü]z|m[\u0131iuü]n|"
    # 1 karakter (vowel suffix - en kısa son)
    r"[\u0131iuüae])\b",
    re.IGNORECASE,
)

# Sözcük sonu ekler (apostrofsuz). NOT: bu pattern AGRESIF olabilir — varsayılan
# olarak kapalı kullanılır, sadece gazetteer hit sonrası "fine-tune" için.
# \u0131 = 'ı' (Türkçe dotless i)
TR_WORD_END_SUFFIXES = re.compile(
    r"(\w+?)(?:n\u0131n|nin|nun|nün|\u0131n|in|un|ün|"
    r"dan|den|tan|ten|"
    r"da|de|ta|te|"
    r"ya|ye|"
    r"la|le|"
    r"yla|yle|"
    r"lar\u0131ndan|lerinden|"
    r"lar\u0131nda|lerinde)\b",
    re.IGNORECASE,
)

# Türkçe karakter → ASCII fold (loose key için)
TR_TO_ASCII = str.maketrans("ıİşŞğĞüÜöÖçÇâÂîÎûÛ", "iIsSgGuUoOcCaAiIuU")
