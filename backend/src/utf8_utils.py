#!/usr/bin/env python3
"""
UTF-8 ve Unicode Normalization Utilities
"""

import unicodedata
import logging

def normalize_unicode_text(text: str) -> str:
    """
    Unicode text normalization for consistent UTF-8 storage
    
    Args:
        text (str): Input text to normalize
        
    Returns:
        str: Normalized UTF-8 text
    """
    if not text or not isinstance(text, str):
        return text
    
    try:
        # NFC normalization - Canonical Decomposition followed by Canonical Composition
        normalized = unicodedata.normalize('NFC', text)
        
        # UTF-8 encoding'i güvence altına al
        normalized = normalized.encode('utf-8', errors='replace').decode('utf-8')
        
        # Additional cleaning
        normalized = normalized.strip()
        
        return normalized
    except Exception as e:
        logging.warning(f"Text normalization hatası: {e}")
        return text

def normalize_file_name(filename: str) -> str:
    """
    File name normalization for Neo4j consistency
    Dosya sistem uyumluluğu için NFC normalizasyonu kullanır
    
    Args:
        filename (str): Input filename
        
    Returns:
        str: Normalized filename
    """
    if not filename or not isinstance(filename, str):
        return filename
    
    try:
        # Unicode normalization - NFC kullan (Composed)
        # Bu, çoğu dosya sistemi ile uyumlu olan format
        normalized = unicodedata.normalize('NFC', filename)
        
        # UTF-8 encoding'i güvence altına al
        normalized = normalized.encode('utf-8', errors='replace').decode('utf-8')
        
        # Additional cleaning
        normalized = normalized.strip()
        
        logging.debug(f"Filename normalized: '{filename}' -> '{normalized}'")
        
        return normalized
    except Exception as e:
        logging.warning(f"Filename normalization hatası: {e}")
        return filename

def try_both_normalizations(filename: str):
    """
    Hem NFC hem NFD normalizasyonlarını döndürür
    Dosya arama işlemleri için kullanılır
    """
    if not filename or not isinstance(filename, str):
        return filename, filename
    
    try:
        nfc = unicodedata.normalize('NFC', filename)
        nfd = unicodedata.normalize('NFD', filename)
        return nfc, nfd
    except Exception:
        return filename, filename

def ensure_utf8_encoding(data: any) -> any:
    """
    Herhangi bir veri tipini UTF-8 uyumlu hale getirir
    
    Args:
        data: Normalize edilecek veri (string, dict, list vs.)
        
    Returns:
        UTF-8 uyumlu veri
    """
    if isinstance(data, str):
        return normalize_unicode_text(data)
    elif isinstance(data, dict):
        return {key: ensure_utf8_encoding(value) for key, value in data.items()}
    elif isinstance(data, list):
        return [ensure_utf8_encoding(item) for item in data]
    else:
        return data

def validate_utf8_text(text: str) -> bool:
    """
    Text'in geçerli UTF-8 olup olmadığını kontrol eder
    
    Args:
        text (str): Kontrol edilecek text
        
    Returns:
        bool: UTF-8 geçerli ise True
    """
    if not isinstance(text, str):
        return False
    
    try:
        # UTF-8 encode/decode testi
        text.encode('utf-8').decode('utf-8')
        return True
    except UnicodeError:
        return False

def fix_encoding_issues(text: str) -> str:
    """
    Encoding sorunlarını düzeltmeye çalışır
    
    Args:
        text (str): Düzeltilecek text
        
    Returns:
        str: Düzeltilmiş text
    """
    if not isinstance(text, str):
        return text
    
    try:
        # Farklı encoding'leri dene
        encodings = ['utf-8', 'latin-1', 'cp1252', 'iso-8859-1']
        
        for encoding in encodings:
            try:
                # Byte'a çevir ve UTF-8 olarak decode et
                if isinstance(text, str):
                    fixed = text.encode(encoding, errors='ignore').decode('utf-8', errors='replace')
                    return normalize_unicode_text(fixed)
            except (UnicodeError, UnicodeDecodeError, UnicodeEncodeError):
                continue
        
        # Son çare: sadece ASCII karakterleri koru
        return ''.join(char for char in text if ord(char) < 128)
        
    except Exception as e:
        logging.warning(f"Encoding fix hatası: {e}")
        return text

# Test fonksiyonu
def test_utf8_utils():
    """UTF-8 utilities'leri test eder"""
    test_cases = [
        "Normal text",
        "Türkçe karakterler: ğüşıöç",
        "Ayça Dinçkök",
        "Çiftçi",
        "Büyükçekmece",
        "İstanbul",
        # Decomposed Unicode test
        "Ayç\u0327a",  # c + cedilla
        "e\u0301",     # e + acute accent
    ]
    
    print("UTF-8 Utilities Test:")
    print("=" * 50)
    
    for i, test_text in enumerate(test_cases, 1):
        normalized = normalize_unicode_text(test_text)
        is_valid = validate_utf8_text(normalized)
        
        print(f"{i}. Original: '{test_text}'")
        print(f"   Normalized: '{normalized}'")
        print(f"   Valid UTF-8: {is_valid}")
        print(f"   Equal: {test_text == normalized}")
        print()

if __name__ == "__main__":
    test_utf8_utils()
