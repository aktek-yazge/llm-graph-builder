"""Çok-dilli şirket sonekleri ve unvan pattern'leri.

Bu listeler `turkish.py`'deki TR listelerine ek olarak normalizer'a verilir.
Çakışmalar yumuşaktır: tüm pattern'ler her metne uygulanır, eşleşmezse
sessizce atlanır.
"""
from __future__ import annotations

# İngilizce şirket sonekleri
EN_COMPANY_SUFFIXES: list[str] = [
    r"\binc\.?\b",
    r"\bcorp(?:oration)?\.?\b",
    r"\bllc\b",
    r"\bllp\b",
    r"\blp\b",
    r"\bltd\.?\b",
    r"\blimited\b",
    r"\bplc\b",
    r"\bco\.?\b",
    r"\bcompany\b",
    r"\bgroup\b",
    r"\bholdings?\b",
    r"\bpartners(?:hip)?\b",
    r"\benterprises?\b",
    r"\binternational\b",
    r"\bincorporated\b",
]

# Almanca / Avusturya / İsviçre
DE_COMPANY_SUFFIXES: list[str] = [
    r"\bgmbh\b",
    r"\bag\b",
    r"\bkg\b",
    r"\bohg\b",
    r"\bug\b",
    r"\bse\b",
    r"\bgesellschaft\b",
    r"\baktiengesellschaft\b",
]

# Fransızca / Belçika / Lüksemburg
FR_COMPANY_SUFFIXES: list[str] = [
    r"\bs\.?\s*a\.?\b",
    r"\bs\.?\s*a\.?\s*r\.?\s*l\.?\b",
    r"\bs\.?\s*a\.?\s*s\.?\b",
    r"\bs\.?\s*c\.?\s*p\.?\b",
    r"\beurl\b",
    r"\bsnc\b",
    r"\bsasu\b",
    r"\bs\.?\s*c\.?\s*s\.?\b",
]

# İtalyanca
IT_COMPANY_SUFFIXES: list[str] = [
    r"\bs\.?\s*p\.?\s*a\.?\b",
    r"\bs\.?\s*r\.?\s*l\.?\b",
    r"\bs\.?\s*n\.?\s*c\.?\b",
    r"\bs\.?\s*a\.?\s*s\.?\b",
]

# İspanyolca
ES_COMPANY_SUFFIXES: list[str] = [
    r"\bs\.?\s*a\.?\b",
    r"\bs\.?\s*l\.?\b",
    r"\bs\.?\s*l\.?\s*u\.?\b",
    r"\bs\.?\s*c\.?\b",
]

# Hollandaca
NL_COMPANY_SUFFIXES: list[str] = [
    r"\bb\.?\s*v\.?\b",
    r"\bn\.?\s*v\.?\b",
    r"\bv\.?\s*o\.?\s*f\.?\b",
]

# Tüm Avrupa sonekleri tek listede
EU_COMPANY_SUFFIXES: list[str] = (
    EN_COMPANY_SUFFIXES
    + DE_COMPANY_SUFFIXES
    + FR_COMPANY_SUFFIXES
    + IT_COMPANY_SUFFIXES
    + ES_COMPANY_SUFFIXES
    + NL_COMPANY_SUFFIXES
)

# İngilizce kişi unvanları
EN_PERSON_TITLES: list[str] = [
    r"\bdr\.?\b",
    r"\bprof(?:essor)?\.?\b",
    r"\bmr\.?\b",
    r"\bmrs\.?\b",
    r"\bms\.?\b",
    r"\bmiss\b",
    r"\bsir\b",
    r"\bdame\b",
    r"\blord\b",
    r"\blady\b",
    r"\bhon\.?\b",
]
