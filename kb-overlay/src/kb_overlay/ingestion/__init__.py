"""Document-level preprocessing for ingestion pipeline.

Şu anki tek modül:
- preprocess: OCR çıktılarındaki yapay satır kırılmalarını birleştirir
  (markdown başlıkları, listeler, paragraf sınırlarını korur).
"""
from .preprocess import normalize_ocr_text

__all__ = ["normalize_ocr_text"]
