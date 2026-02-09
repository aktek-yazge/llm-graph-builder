# -*- coding: utf-8 -*-
"""
OCR Tools for Agentic OCR

Bu modül, AgenticOCR agent'ının kullanacağı custom tool'ları içerir.

Tool'lar:
    - mark_cropped: Kırpılan koordinatları kaydet
    - mark_skipped: Atlanan koordinatları kaydet
    - get_page_log: Sayfa durumunu göster
    - finish_page: Sayfa işlemeyi tamamla
"""

import logging
from typing import Dict, Any, List
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

# Sayfa içi state (her sayfa başında sıfırlanır)
_cropped_regions: List[Dict[str, Any]] = []  # [{"coords": (x1,y1,x2,y2), "desc": "..."}]
_skipped_regions: List[Dict[str, Any]] = []


def reset_page_state() -> None:
    """
    Sayfa içi state'i sıfırla.
    
    Her yeni sayfa başında Python controller tarafından çağrılır.
    """
    global _cropped_regions, _skipped_regions
    _cropped_regions = []
    _skipped_regions = []
    logger.info("🔄 Page state reset")


@tool
def mark_cropped(x1: int, y1: int, x2: int, y2: int, description: str) -> str:
    """
    Kırpılan bölgenin koordinatlarını kaydet.
    crop() çağırdıktan SONRA çağır.
    
    Args:
        x1: Sol üst köşe X koordinatı
        y1: Sol üst köşe Y koordinatı
        x2: Sağ alt köşe X koordinatı
        y2: Sağ alt köşe Y koordinatı
        description: Ne kırpıldı (örn: "Aksa Genel Kurul başlığı ve davet metni")
    
    Returns:
        Onay mesajı
    """
    _cropped_regions.append({
        "coords": (x1, y1, x2, y2),
        "desc": description
    })
    logger.info(f"✂️ CROPPED: ({x1},{y1})-({x2},{y2}) {description}")
    return f"✂️ Kaydedildi: ({x1},{y1})-({x2},{y2}) {description}"


@tool
def mark_skipped(x1: int, y1: int, x2: int, y2: int, description: str) -> str:
    """
    Atlanan bölgenin koordinatlarını kaydet.
    Bu koordinatlara bir daha GİTME.
    
    Args:
        x1: Sol üst köşe X koordinatı
        y1: Sol üst köşe Y koordinatı
        x2: Sağ alt köşe X koordinatı
        y2: Sağ alt köşe Y koordinatı
        description: Neden atlandı (örn: "Kooperatif ilanı - hedef şirket değil")
    
    Returns:
        Onay mesajı
    """
    _skipped_regions.append({
        "coords": (x1, y1, x2, y2),
        "desc": description
    })
    logger.info(f"⏭️ SKIPPED: ({x1},{y1})-({x2},{y2}) {description}")
    return f"⏭️ Atlandı: ({x1},{y1})-({x2},{y2}) {description}"


@tool
def get_page_log() -> str:
    """
    Bu sayfada hangi koordinatlar işlendi?
    
    Aynı bölgeyi tekrar işlememek için kontrol et.
    
    Returns:
        Kırpılan ve atlanan koordinatların listesi
    """
    if not _cropped_regions and not _skipped_regions:
        return "📋 Bu sayfada henüz işlem yapılmadı."
    
    lines = ["📋 SAYFA DURUMU:"]
    
    if _cropped_regions:
        lines.append("✂️ Kırpılan:")
        for r in _cropped_regions:
            lines.append(f"   {r['coords']} - {r['desc']}")
    
    if _skipped_regions:
        lines.append("⏭️ Atlanan (bu koordinatlara gitme):")
        for r in _skipped_regions:
            lines.append(f"   {r['coords']} - {r['desc']}")
    
    return "\n".join(lines)


@tool
def finish_page(
    markdown: str,
    continuation_note: str = "",
    is_complete: bool = False
) -> Dict[str, Any]:
    """
    Sayfa işlemeyi tamamla.
    
    Args:
        markdown: Bu sayfadan çıkarılan içerik (kırpılan bölgelerden birleştirilmiş)
        continuation_note: Sonraki sayfaya devredilecek not
                          (örn: "Madde 15 yarım: 'Şirket Esas Sözleşmesi'nin...'")
        is_complete: Hedef şirketin ilanı tamamen bitti mi?
                     True ise sonraki sayfalar işlenmez.
    
    Returns:
        Sayfa sonucu dict
    """
    result = {
        "markdown": markdown,
        "continuation_note": continuation_note,
        "is_complete": is_complete,
        "cropped_count": len(_cropped_regions),
        "skipped_count": len(_skipped_regions),
    }
    
    logger.info(
        f"✅ Sayfa tamamlandı. "
        f"{len(_cropped_regions)} kırpıldı, {len(_skipped_regions)} atlandı. "
        f"is_complete={is_complete}"
    )
    
    return result


def get_ocr_tools() -> list:
    """
    AgenticOCR için tüm custom tool'ları döndürür.
    
    Returns:
        List of LangChain tools
    """
    return [
        mark_cropped,
        mark_skipped,
        get_page_log,
        finish_page,
    ]
