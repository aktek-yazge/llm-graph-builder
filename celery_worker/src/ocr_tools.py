# -*- coding: utf-8 -*-
"""
OCR Tools for Agentic OCR

Bu modül, AgenticOCR agent'ının kullanacağı custom tool'ları içerir.

Tool'lar:
    - ocr_cropped_image: Gemini Flash 2 ile kırpılmış görüntüden OCR
    - mark_cropped: Kırpılan koordinatları kaydet
    - mark_skipped: Atlanan koordinatları kaydet
    - get_page_log: Sayfa durumunu göster
    - finish_page: Sayfa işlemeyi tamamla
"""

import os
import json
import logging
from datetime import datetime
from typing import Dict, Any, List
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

# Gemini client cache
_gemini_client = None

# =============================================================================
# DETAILED LOGGING SUPPORT
# =============================================================================

# Tool call log for this page
_tool_call_log: List[Dict[str, Any]] = []


def _log_tool_call(
    tool_name: str,
    model: str,
    inputs: Dict[str, Any],
    output: Any,
    duration_ms: float = 0,
) -> None:
    """Tool çağrısını detaylı logla."""
    entry = {
        "timestamp": datetime.now().isoformat(),
        "tool": tool_name,
        "model": model,
        "inputs": inputs,
        "output_preview": str(output)[:200] if output else None,
        "output_length": len(str(output)) if output else 0,
        "duration_ms": duration_ms,
    }
    _tool_call_log.append(entry)
    
    # Console log
    log_msg = (
        f"🔧 TOOL CALL: {tool_name}\n"
        f"   Model: {model}\n"
        f"   Inputs: {json.dumps(inputs, ensure_ascii=False)}\n"
        f"   Output: {str(output)[:100]}...\n"
        f"   Duration: {duration_ms:.0f}ms"
    )
    logger.info(log_msg)
    print(log_msg, flush=True)


def get_tool_call_log() -> List[Dict[str, Any]]:
    """Bu sayfadaki tüm tool çağrılarını döndür."""
    return _tool_call_log.copy()


def reset_tool_call_log() -> None:
    """Tool çağrı logunu sıfırla."""
    global _tool_call_log
    _tool_call_log = []


# =============================================================================
# GEMINI OCR TOOL
# =============================================================================

def _get_gemini_client():
    """Gemini client'ı lazy initialize et."""
    global _gemini_client
    if _gemini_client is None:
        from google import genai
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable not set")
        _gemini_client = genai.Client(api_key=api_key)
    return _gemini_client


@tool
def ocr_cropped_image(image_path: str) -> str:
    """
    Kırpılmış görüntüden Gemini Flash 2 ile metin çıkar.
    
    crop() tool'u ile kırpılan görüntünün yolunu ver,
    bu tool Gemini Flash 2 kullanarak OCR yapar ve metni döndürür.
    
    Args:
        image_path: Kırpılmış görüntünün tam dosya yolu
                   (crop tool'unun döndürdüğü output_path)
    
    Returns:
        Görüntüden çıkarılan metin (markdown formatında)
    """
    import time
    from google.genai import types
    
    start_time = time.time()
    
    # Dosya kontrolü
    if not os.path.exists(image_path):
        error_msg = f"❌ Dosya bulunamadı: {image_path}"
        logger.error(error_msg)
        _log_tool_call("ocr_cropped_image", "gemini-2.0-flash", {"image_path": image_path}, error_msg)
        return error_msg
    
    try:
        # Görüntüyü oku
        with open(image_path, "rb") as f:
            image_data = f.read()
        
        # MIME type belirle
        if image_path.lower().endswith(".png"):
            mime_type = "image/png"
        elif image_path.lower().endswith((".jpg", ".jpeg")):
            mime_type = "image/jpeg"
        else:
            mime_type = "image/png"  # Default
        
        # Gemini client al
        client = _get_gemini_client()
        
        # OCR prompt
        ocr_prompt = """Bu görüntüdeki Türkçe metni aynen oku ve markdown formatında döndür.

KURALLAR:
- Metni olduğu gibi, düzeltme yapmadan yaz
- Başlıkları ## ile işaretle
- Maddeleri numaralı liste olarak yaz
- Paragrafları koru
- Tarih, sayı gibi bilgileri aynen aktar
- Sadece metni döndür, yorum ekleme"""
        
        # Gemini API çağrısı
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_bytes(data=image_data, mime_type=mime_type),
                        types.Part.from_text(text=ocr_prompt),
                    ],
                ),
            ],
            config=types.GenerateContentConfig(
                temperature=0.1,
            ),
        )
        
        duration_ms = (time.time() - start_time) * 1000
        
        # Sonucu al
        if response and response.text:
            extracted_text = response.text.strip()
            _log_tool_call(
                "ocr_cropped_image",
                "gemini-2.0-flash",
                {"image_path": image_path, "size_kb": len(image_data) // 1024},
                extracted_text,
                duration_ms,
            )
            logger.info(f"📝 OCR tamamlandı: {image_path} ({len(extracted_text)} karakter)")
            return extracted_text
        else:
            _log_tool_call("ocr_cropped_image", "gemini-2.0-flash", {"image_path": image_path}, "", duration_ms)
            logger.warning(f"⚠️ OCR boş sonuç: {image_path}")
            return ""
            
    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        error_msg = f"❌ OCR hatası ({image_path}): {e}"
        _log_tool_call("ocr_cropped_image", "gemini-2.0-flash", {"image_path": image_path}, error_msg, duration_ms)
        logger.error(error_msg)
        return error_msg


# =============================================================================
# PAGE STATE TRACKING
# =============================================================================

# Sayfa içi state (her sayfa başında sıfırlanır)
_cropped_regions: List[Dict[str, Any]] = []  # [{"coords": (x1,y1,x2,y2), "desc": "..."}]
_skipped_regions: List[Dict[str, Any]] = []
_current_image_path: str = ""  # Mevcut sayfa görüntüsünün yolu


def set_current_image_path(path: str) -> None:
    """Mevcut sayfa görüntüsünün yolunu ayarla."""
    global _current_image_path
    _current_image_path = path
    logger.info(f"📸 Current image path set: {path}")


def get_current_image_path() -> str:
    """Mevcut sayfa görüntüsünün yolunu döndür."""
    return _current_image_path


def reset_page_state() -> None:
    """
    Sayfa içi state'i sıfırla.
    
    Her yeni sayfa başında Python controller tarafından çağrılır.
    """
    global _cropped_regions, _skipped_regions, _current_image_path
    _cropped_regions = []
    _skipped_regions = []
    _current_image_path = ""
    reset_tool_call_log()  # Tool log'unu da sıfırla
    reset_crop_plan()  # Crop planını da sıfırla
    logger.info("🔄 Page state reset (regions + tool log + crop plan)")


@tool
def get_image_path() -> str:
    """
    Mevcut sayfa görüntüsünün TAM dosya yolunu döndürür.
    
    ⚠️ ÖNEMLİ: crop() çağırırken bu tool'un döndürdüğü yolu AYNEN kullan!
    Dosya yolunu ASLA elle yazma, bu tool'u kullan.
    
    Returns:
        Mevcut sayfanın tam dosya yolu (örn: /workspace/celery_worker/output_celery/...)
    """
    if not _current_image_path:
        return "❌ Hata: Mevcut görüntü yolu ayarlanmamış!"
    return _current_image_path


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
    
    _log_tool_call(
        "mark_cropped",
        "GPT-5.2 (coordinator)",
        {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "description": description},
        "OK",
    )
    
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
    
    _log_tool_call(
        "mark_skipped",
        "GPT-5.2 (coordinator)",
        {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "description": description},
        "OK",
    )
    
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


# =============================================================================
# BATCH CROP PLANNING TOOL
# =============================================================================

# Planned crops for batch processing
_planned_crops: List[Dict[str, Any]] = []
_planned_skips: List[Dict[str, Any]] = []


def get_planned_crops() -> List[Dict[str, Any]]:
    """Planlanan crop'ları döndür."""
    return _planned_crops.copy()


def get_planned_skips() -> List[Dict[str, Any]]:
    """Planlanan skip'leri döndür."""
    return _planned_skips.copy()


def reset_crop_plan() -> None:
    """Crop planını sıfırla."""
    global _planned_crops, _planned_skips
    _planned_crops = []
    _planned_skips = []


@tool
def plan_page_crops(
    crops: List[Dict[str, Any]],
    skips: List[Dict[str, Any]],
) -> str:
    """
    Sayfadaki TÜM kırpma kararlarını TEK SEFERDE kaydet.
    
    ⚠️ ÖNEMLİ: Bu tool'u kullanmadan önce TÜM SAYFAYI analiz et!
    Tüm sütunları gör, hangi bölümlerin hedef şirkete ait olduğunu belirle,
    sonra TÜM koordinatları bir seferde bu tool'a ver.
    
    Args:
        crops: Kırpılacak bölgeler listesi. Her bölge:
               {"x1": int, "y1": int, "x2": int, "y2": int, "desc": str}
               Örnek: [
                   {"x1": 60, "y1": 125, "x2": 400, "y2": 2320, "desc": "Aksa - sütun 1"},
                   {"x1": 400, "y1": 125, "x2": 800, "y2": 1500, "desc": "Aksa - sütun 2 üst"}
               ]
        
        skips: Atlanacak bölgeler listesi. Her bölge:
               {"x1": int, "y1": int, "x2": int, "y2": int, "desc": str}
               Örnek: [
                   {"x1": 800, "y1": 125, "x2": 1200, "y2": 2320, "desc": "Başka şirket - IZBAŞ"}
               ]
    
    Returns:
        Onay mesajı ve sonraki adımlar
    """
    global _planned_crops, _planned_skips
    
    # Kaydet
    _planned_crops = crops if crops else []
    _planned_skips = skips if skips else []
    
    # Detaylı log
    _log_tool_call(
        "plan_page_crops",
        "GPT-5.2 (coordinator)",
        {"crops_count": len(_planned_crops), "skips_count": len(_planned_skips)},
        f"Planned {len(_planned_crops)} crops, {len(_planned_skips)} skips",
    )
    
    log_msg = (
        f"\n{'='*50}\n"
        f"📋 SAYFA KIRPMA PLANI\n"
        f"{'='*50}\n"
    )
    
    if _planned_crops:
        log_msg += f"✂️ KIRPILACAK BÖLGELER ({len(_planned_crops)}):\n"
        for i, c in enumerate(_planned_crops, 1):
            log_msg += f"   {i}. ({c.get('x1')},{c.get('y1')})-({c.get('x2')},{c.get('y2')}) {c.get('desc', '')}\n"
    else:
        log_msg += "✂️ Kırpılacak bölge yok (hedef şirket bu sayfada yok)\n"
    
    if _planned_skips:
        log_msg += f"\n⏭️ ATLANACAK BÖLGELER ({len(_planned_skips)}):\n"
        for i, s in enumerate(_planned_skips, 1):
            log_msg += f"   {i}. ({s.get('x1')},{s.get('y1')})-({s.get('x2')},{s.get('y2')}) {s.get('desc', '')}\n"
    
    log_msg += f"{'='*50}\n"
    
    logger.info(log_msg)
    print(log_msg, flush=True)
    
    # Sonraki adımlar için talimat
    if _planned_crops:
        return (
            f"✅ Plan kaydedildi: {len(_planned_crops)} bölge kırpılacak, {len(_planned_skips)} bölge atlanacak.\n\n"
            f"ŞİMDİ SIRAYLA HER KIRPMA İÇİN:\n"
            f"1. crop(input_path, x1, y1, x2, y2) → Görüntüyü kırp\n"
            f"2. ocr_cropped_image(cropped_path) → OCR yap\n"
            f"3. mark_cropped(...) → Kaydı tut\n\n"
            f"TÜM KIRPMALARI TAMAMLADIKTAN SONRA:\n"
            f"- Tüm OCR sonuçlarını birleştir\n"
            f"- finish_page ile sayfayı tamamla"
        )
    else:
        return (
            f"✅ Plan kaydedildi: Hedef şirket bu sayfada yok.\n"
            f"Atlanacak {len(_planned_skips)} bölge var.\n\n"
            f"ŞİMDİ:\n"
            f"- Her atlanan bölge için mark_skipped çağır\n"
            f"- finish_page(markdown='', is_complete=False) ile sayfayı tamamla"
        )


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
    # Tool call özeti
    tool_calls = get_tool_call_log()
    
    result = {
        "markdown": markdown,
        "continuation_note": continuation_note,
        "is_complete": is_complete,
        "cropped_count": len(_cropped_regions),
        "skipped_count": len(_skipped_regions),
        "tool_calls": tool_calls,
    }
    
    # Detaylı log
    summary_log = (
        f"\n{'='*60}\n"
        f"📋 SAYFA TAMAMLANDI - ÖZET\n"
        f"{'='*60}\n"
        f"📝 Markdown: {len(markdown)} karakter\n"
        f"✂️ Kırpılan bölgeler: {len(_cropped_regions)}\n"
        f"⏭️ Atlanan bölgeler: {len(_skipped_regions)}\n"
        f"📌 Devam notu: {continuation_note[:100] if continuation_note else 'Yok'}\n"
        f"✅ Tamamlandı mı: {is_complete}\n"
        f"🔧 Tool çağrıları: {len(tool_calls)}\n"
    )
    
    # Kırpılan bölgeleri listele
    if _cropped_regions:
        summary_log += "\n✂️ KIRPILAN BÖLGELER:\n"
        for i, r in enumerate(_cropped_regions, 1):
            summary_log += f"   {i}. {r['coords']} - {r['desc']}\n"
    
    # Atlanan bölgeleri listele
    if _skipped_regions:
        summary_log += "\n⏭️ ATLANAN BÖLGELER:\n"
        for i, r in enumerate(_skipped_regions, 1):
            summary_log += f"   {i}. {r['coords']} - {r['desc']}\n"
    
    # Tool çağrılarını listele
    if tool_calls:
        summary_log += "\n🔧 TOOL ÇAĞRILARI:\n"
        for i, tc in enumerate(tool_calls, 1):
            summary_log += (
                f"   {i}. {tc['tool']} ({tc['model']})\n"
                f"      Inputs: {json.dumps(tc['inputs'], ensure_ascii=False)[:80]}\n"
                f"      Output: {tc['output_length']} chars, {tc['duration_ms']:.0f}ms\n"
            )
    
    summary_log += f"{'='*60}\n"
    
    logger.info(summary_log)
    print(summary_log, flush=True)
    
    return result


def get_ocr_tools() -> list:
    """
    AgenticOCR için tüm custom tool'ları döndürür.
    
    Returns:
        List of LangChain tools
    """
    return [
        get_image_path,  # Dosya yolunu almak için - agent'ın elle yazmasını önler
        plan_page_crops,  # Tüm kırpma kararlarını tek seferde al
        ocr_cropped_image,
        mark_cropped,
        mark_skipped,
        get_page_log,
        finish_page,
    ]
