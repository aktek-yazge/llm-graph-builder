# -*- coding: utf-8 -*-
"""
OCR Normalizer (Simplified)

Temel metin normalization fonksiyonları.
Şema-driven yaklaşımda Opus 4.5 Neo4j'den gelen şemaya göre
doğru label ve ID kullanıyor. Bu modül sadece yardımcı fonksiyonlar içerir.

Not: Label mapping artık kullanılmıyor - Opus şemadan öğreniyor.
"""

import re
import unicodedata


def normalize_turkish(text: str) -> str:
    """
    Türkçe karakterleri ASCII'ye dönüştürür.
    
    ş→s, ğ→g, ü→u, ö→o, ç→c, ı→i, İ→i
    """
    if not text:
        return ""
    
    # Türkçe karakter mapping
    tr_map = {
        'ş': 's', 'Ş': 's',
        'ğ': 'g', 'Ğ': 'g',
        'ü': 'u', 'Ü': 'u',
        'ö': 'o', 'Ö': 'o',
        'ç': 'c', 'Ç': 'c',
        'ı': 'i', 'İ': 'i',
        'ə': 'a', 'Ə': 'a',  # Azerbaycan Türkçesi
    }
    
    result = text.lower()
    for tr_char, ascii_char in tr_map.items():
        result = result.replace(tr_char, ascii_char)
    
    return result


def normalize_for_id(name: str) -> str:
    """
    Metni ID formatına normalize eder.
    
    - Küçük harfe çevir
    - Türkçe karakterleri dönüştür
    - Boşlukları underscore yap
    - Özel karakterleri kaldır
    
    Örnek: "AKSA AKRİLİK KİMYA SANAYİİ A.Ş." → "aksa_akrilik_kimya_sanayii_as"
    """
    if not name:
        return ""
    
    # Türkçe karakterleri normalize et
    normalized = normalize_turkish(name)
    
    # Unicode normalization (NFD -> NFC)
    normalized = unicodedata.normalize('NFD', normalized)
    normalized = ''.join(c for c in normalized if unicodedata.category(c) != 'Mn')
    
    # Sadece alfanumerik ve boşluk bırak
    normalized = re.sub(r'[^a-z0-9\s]', '', normalized)
    
    # Birden fazla boşluğu tek boşluğa indir
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    
    # Boşlukları underscore yap
    normalized = normalized.replace(' ', '_')
    
    return normalized


def clean_text(text: str) -> str:
    """
    Metin temizliği.
    
    - Baş ve sondaki boşlukları kaldır
    - Birden fazla boşluğu tek boşluğa indir
    - Satır sonlarını normalize et
    """
    if not text:
        return ""
    
    # Satır sonlarını normalize et
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    
    # Her satırı temizle
    lines = [line.strip() for line in text.split('\n')]
    
    # Boş satırları tek satıra indir
    result_lines = []
    prev_empty = False
    for line in lines:
        if not line:
            if not prev_empty:
                result_lines.append('')
                prev_empty = True
        else:
            result_lines.append(line)
            prev_empty = False
    
    return '\n'.join(result_lines).strip()


def validate_id_format(entity_id: str) -> bool:
    """
    ID formatının geçerli olup olmadığını kontrol eder.
    
    Geçerli format: lowercase alfanumerik ve underscore
    Örnek: company_aksa_akrilik, person_ahmet_yilmaz
    """
    if not entity_id:
        return False
    
    # Sadece lowercase alfanumerik ve underscore
    return bool(re.match(r'^[a-z0-9_]+$', entity_id))
