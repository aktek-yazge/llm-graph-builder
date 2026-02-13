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
_grid_info: Dict[str, Any] = {}  # Son çizilen grid bilgisi


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
    global _cropped_regions, _skipped_regions, _current_image_path, _grid_info
    _cropped_regions = []
    _skipped_regions = []
    _current_image_path = ""
    _grid_info = {}
    reset_tool_call_log()  # Tool log'unu da sıfırla
    reset_crop_plan()  # Crop planını da sıfırla
    logger.info("🔄 Page state reset (regions + tool log + crop plan + grid)")


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


# =============================================================================
# TSG-AWARE SMART GRID TOOLS
# =============================================================================


def _detect_tsg_columns(image_path: str) -> Dict[str, Any]:
    """
    TSG sayfa görüntüsündeki sütun ayırıcı çizgileri otomatik tespit et.
    
    Sütun sayısı SABİT DEĞİLDİR - tespit edilen çizgilere göre dinamik
    olarak S1, S2, ... SN şeklinde numaralandırılır.
    
    İçerik bulunan son sınıra kadar tüm sütunlar numaralandırılır.
    
    Args:
        image_path: Sayfa görüntüsünün yolu
    
    Returns:
        Dict: {
            "columns": {"S1": (x_start, x_end), "S2": ..., ...},
            "separators": [x1, x2, ...],
            "width": int, "height": int,
            "detection_method": "auto" | "fallback"
        }
    """
    from PIL import Image
    import numpy as np
    
    img = Image.open(image_path)
    w, h = img.size
    
    try:
        from scipy.signal import find_peaks
        
        arr = np.array(img.convert('L'))
        
        # Sayfa ortasından geniş bant al (header/footer etkisinden kaçın)
        mid = h // 2
        band_half = min(100, h // 6)
        band = arr[mid - band_half:mid + band_half, :]
        col_profile = band.mean(axis=0)
        inv_profile = 255 - col_profile
        
        # Koyu dikey çizgileri bul
        peaks, props = find_peaks(
            inv_profile, 
            height=80,
            distance=150,
            prominence=15,
        )
        
        if len(peaks) >= 3:
            boundaries = sorted(peaks.tolist())
            
            # Ardışık çizgiler arası mesafeleri hesapla
            gaps = [boundaries[i+1] - boundaries[i] for i in range(len(boundaries)-1)]
            
            # Median sütun genişliği (gürültüye dayanıklı)
            sorted_gaps = sorted(gaps)
            median_gap = sorted_gaps[len(sorted_gaps) // 2]
            
            # --- Sağ tarafta eksik sütun kontrolü ---
            # Son çizgiden sayfa kenarına kalan alan bir sütunluk mu?
            remaining_right = w - boundaries[-1]
            if remaining_right > median_gap * 0.5:
                # Sağda bir sütunluk daha alan var - sağ sınırı ekle
                right_bound = min(w - 10, boundaries[-1] + int(median_gap))
                boundaries.append(right_bound)
            
            # --- Sol tarafta eksik sütun kontrolü ---
            # İlk çizgi çok içerideyse (tam bir sütun genişliği kadar boşluk)
            # bu sol sınır çizgisi tespit edilememiş demektir
            remaining_left = boundaries[0]
            if remaining_left > median_gap * 0.8:
                # Solda tam bir sütunluk alan var - sol sınırı ekle
                left_bound = max(10, boundaries[0] - int(median_gap))
                boundaries.insert(0, left_bound)
            # Eğer kalan alan küçükse (< median*0.8) bu sadece sayfa marginıdır
            
            # --- Çok dar segmentleri filtrele (gürültü) ---
            # Dar segmentleri komşusuyla birleştir
            min_col_width = median_gap * 0.6
            filtered_boundaries = [boundaries[0]]
            for i in range(1, len(boundaries)):
                gap = boundaries[i] - filtered_boundaries[-1]
                if gap >= min_col_width:
                    filtered_boundaries.append(boundaries[i])
                # else: bu sınırı atla (çok dar segment, gürültü)
            boundaries = filtered_boundaries
            
            # --- Sütunları oluştur ---
            columns = {}
            for i in range(len(boundaries) - 1):
                col_name = f"S{i + 1}"
                columns[col_name] = (boundaries[i], boundaries[i + 1])
            
            logger.info(
                f"TSG sütun tespiti (auto): {len(columns)} sütun, "
                f"sınırlar={boundaries}, "
                f"sütunlar={{{', '.join(f'{k}: {v[0]}-{v[1]}' for k, v in columns.items())}}}"
            )
            
            return {
                "columns": columns,
                "separators": boundaries,
                "width": w,
                "height": h,
                "detection_method": "auto",
            }
    
    except Exception as e:
        logger.warning(f"TSG sütun tespiti başarısız: {e}, fallback kullanılacak")
    
    # ===== FALLBACK: Standart TSG ölçüleri (5 sütun) =====
    scale = w / 1684.0
    b = [
        int(150 * scale),    # S1 sol kenar
        int(416 * scale),    # S1/S2 ayırıcı
        int(699 * scale),    # S2/S3 ayırıcı
        int(982 * scale),    # S3/S4 ayırıcı
        int(1265 * scale),   # S4/S5 ayırıcı
        int(1547 * scale),   # S5 sağ kenar
    ]
    
    columns = {}
    for i in range(len(b) - 1):
        columns[f"S{i + 1}"] = (b[i], b[i + 1])
    
    logger.info(
        f"TSG sütun tespiti (fallback): {len(columns)} sütun, scale={scale:.2f}"
    )
    
    return {
        "columns": columns,
        "separators": b,
        "width": w,
        "height": h,
        "detection_method": "fallback",
    }


def _parse_tsg_cell(cell_ref: str, grid_info: Dict[str, Any]) -> tuple:
    """
    TSG hücre referansını piksel koordinatına çevir.
    
    Format: "S3:5" → Sütun S3, Satır 5
    
    Args:
        cell_ref: Hücre referansı (örn: "S3:5", "S1:1")
        grid_info: draw_grid tarafından saklanan grid bilgisi
    
    Returns:
        (x_start, y_start, x_end, y_end) - hücrenin piksel sınırları
    """
    parts = cell_ref.strip().split(":")
    if len(parts) != 2:
        raise ValueError(
            f"Geçersiz hücre formatı: '{cell_ref}'. "
            f"Doğru format: 'S3:5' (Sütun:Satır)"
        )
    
    col_name = parts[0].strip().upper()
    row_num = int(parts[1].strip())
    
    columns = grid_info.get("columns", {})
    rows = grid_info.get("rows", 20)
    h = grid_info.get("height", 0)
    
    if col_name not in columns:
        valid = ", ".join(columns.keys())
        raise ValueError(
            f"Geçersiz sütun: '{col_name}'. Geçerli sütunlar: {valid}"
        )
    
    if row_num < 1 or row_num > rows:
        raise ValueError(
            f"Geçersiz satır: {row_num}. Geçerli aralık: 1-{rows}"
        )
    
    col_bounds = columns[col_name]
    row_h = h // rows
    
    x_start = col_bounds[0]
    x_end = col_bounds[1]
    y_start = (row_num - 1) * row_h
    y_end = row_num * row_h
    
    return (x_start, y_start, x_end, y_end)


def get_grid_info() -> Dict[str, Any]:
    """Son çizilen grid bilgisini döndür."""
    return _grid_info.copy()


@tool
def draw_grid(rows: int = 20) -> str:
    """
    TSG sayfası üzerine akıllı ızgara çiz.
    
    Sütun ayırıcı çizgileri OTOMATIK tespit eder ve bunlara hizalı grid çizer.
    
    Grid yapısı:
    - Sütunlar: S1, S2, S3, S4 (TSG'nin gerçek 4 sütununa hizalı)
    - Satırlar: 1, 2, 3, ... 20 (yukarıdan aşağıya eşit aralıklı)
    
    Referans formatı: "S3:5" = Sütun 3, Satır 5
    
    Args:
        rows: Satır sayısı (varsayılan 20, daha fazla = daha hassas)
    
    Returns:
        Grid görüntüsünün yolu, sütun bilgileri ve referans açıklaması
    """
    global _grid_info
    import time
    from PIL import Image, ImageDraw, ImageFont
    
    start_time = time.time()
    
    image_path = _current_image_path
    if not image_path:
        return "Hata: Mevcut görüntü yolu ayarlanmamış! get_image_path() ile kontrol et."
    
    if not os.path.exists(image_path):
        return f"Hata: Dosya bulunamadı: {image_path}"
    
    try:
        # TSG sütunlarını tespit et
        tsg = _detect_tsg_columns(image_path)
        columns = tsg["columns"]
        separators = tsg["separators"]
        w = tsg["width"]
        h = tsg["height"]
        detection = tsg["detection_method"]
        
        # Görüntüyü aç
        img = Image.open(image_path)
        if img.mode != 'RGBA':
            img = img.convert('RGBA')
        
        overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        
        row_h = h // rows
        
        # ---- Sütun ayırıcı çizgileri (KALIN, MAVİ) ----
        col_line_color = (0, 80, 255, 200)
        col_line_width = 4
        
        for sep_x in separators:
            draw.line([(sep_x, 0), (sep_x, h)], fill=col_line_color, width=col_line_width)
        
        # ---- Satır çizgileri (belirgin kırmızı) ----
        row_line_color = (255, 30, 30, 170)
        row_line_width = 2
        
        for j in range(1, rows):
            y = j * row_h
            draw.line([(0, y), (w, y)], fill=row_line_color, width=row_line_width)
        
        # ---- Font ----
        font_size = max(16, min(row_h // 3, 26))
        try:
            font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size
            )
        except Exception:
            try:
                font = ImageFont.truetype(
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", font_size
                )
            except Exception:
                font = ImageFont.load_default()
        
        small_font_size = max(12, font_size - 2)
        try:
            small_font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", small_font_size
            )
        except Exception:
            small_font = font
        
        # ---- Sütun başlık etiketleri (üst kısım) ----
        header_bg_color = (0, 80, 255, 220)
        header_text_color = (255, 255, 255, 255)
        
        for col_name, (x_start, x_end) in columns.items():
            col_center_x = (x_start + x_end) // 2
            label = col_name
            bbox = draw.textbbox((0, 0), label, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            
            # Üst başlık kutusu
            lx = col_center_x - tw // 2
            ly = 4
            padding = 4
            draw.rectangle(
                [lx - padding, ly - 2, lx + tw + padding, ly + th + padding],
                fill=header_bg_color,
            )
            draw.text((lx, ly), label, fill=header_text_color, font=font)
        
        # ---- Satır numaraları (sol taraf, belirgin) ----
        row_label_color = (220, 20, 20, 255)
        row_label_bg = (255, 255, 255, 240)
        
        for j in range(rows):
            label = str(j + 1)
            y = j * row_h + 3
            bbox = draw.textbbox((3, y), label, font=small_font)
            padding = 3
            draw.rectangle(
                [bbox[0] - padding, bbox[1] - 2, bbox[2] + padding, bbox[3] + 2],
                fill=row_label_bg,
            )
            draw.text((3, y), label, fill=row_label_color, font=small_font)
        
        # ---- Her hücreye referans etiketi (LLM'in görebileceği netlikte) ----
        cell_label_color = (200, 20, 20, 230)
        cell_font_size = max(11, small_font_size - 2)
        try:
            cell_font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", cell_font_size
            )
        except Exception:
            cell_font = small_font
        
        for col_name, (x_start, x_end) in columns.items():
            for j in range(rows):
                ref_label = f"{col_name}:{j+1}"
                x = x_start + 4
                y = j * row_h + 3
                
                # Sol üst köşeye belirgin etiket
                bbox = draw.textbbox((x, y), ref_label, font=cell_font)
                padding = 2
                draw.rectangle(
                    [bbox[0] - padding, bbox[1] - 1, bbox[2] + padding, bbox[3] + 1],
                    fill=(255, 255, 255, 230),
                )
                draw.text((x, y), ref_label, fill=cell_label_color, font=cell_font)
        
        # Overlay birleştir
        img = Image.alpha_composite(img, overlay)
        img = img.convert('RGB')
        
        # Kaydet
        image_dir = os.path.dirname(image_path)
        crops_dir = os.path.join(image_dir, "..", "crops")
        os.makedirs(crops_dir, exist_ok=True)
        
        ts = datetime.now().strftime("%H%M%S")
        ext = os.path.splitext(image_path)[1]
        output_path = os.path.join(crops_dir, f"{ts}_00_tsg_grid{ext}")
        img.save(output_path)
        
        duration_ms = (time.time() - start_time) * 1000
        
        # Grid bilgisini kaydet
        _grid_info = {
            "image_path": image_path,
            "grid_path": output_path,
            "width": w,
            "height": h,
            "columns": columns,           # {"S1": (150, 416), "S2": (416, 699), ...}
            "separators": separators,
            "rows": rows,
            "row_height": row_h,
            "detection_method": detection,
        }
        
        _log_tool_call(
            "draw_grid",
            "local",
            {"image_path": image_path, "rows": rows, "detection": detection},
            output_path,
            duration_ms,
        )
        
        # Sütun bilgisi özeti
        col_summary = "\n".join(
            f"  {name}: x={bounds[0]}-{bounds[1]} ({bounds[1]-bounds[0]}px genişlik)"
            for name, bounds in columns.items()
        )
        
        result = (
            f"TSG Grid çizildi: {output_path}\n"
            f"Tespit: {detection} | Boyut: {w}x{h}\n\n"
            f"SÜTUNLAR (gerçek TSG sütun çizgilerine hizalı):\n{col_summary}\n\n"
            f"SATIRLAR: 1-{rows} (her satır ~{row_h}px)\n\n"
            f"REFERANS FORMATI: SütunAdı:SatırNo\n"
            f"Örnek: crop_by_cells('S3:5', 'S4:18') → S3 satır 5'ten S4 satır 18'e kadar kırp\n\n"
            f"ÖNEMLİ: Birden fazla kırpma yapabilirsin. Her sütunu AYRI kırp."
        )
        
        logger.info(f"TSG Grid: {output_path} ({detection}, {rows} satır)")
        return result
        
    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        error_msg = f"Grid çizme hatası: {e}"
        _log_tool_call("draw_grid", "local", {"image_path": image_path}, error_msg, duration_ms)
        logger.error(error_msg)
        return error_msg


@tool
def crop_by_cells(
    start_cell: str,
    end_cell: str,
) -> str:
    """
    TSG grid hücre referansları ile görüntü kırp.
    
    Önce draw_grid() ile ızgara çiz, sonra bu tool ile kırp.
    Kırpılmış görüntü orijinal (ızgarasız) görüntüden kesilir.
    
    BİRDEN FAZLA KEZ çağırabilirsin! Her sütun için ayrı kırpma yap.
    
    Args:
        start_cell: Sol üst köşe referansı. Format: "SütunAdı:SatırNo"
                    Örnek: "S3:5" (Sütun 3, Satır 5)
        end_cell: Sağ alt köşe referansı. Format: "SütunAdı:SatırNo"
                  Örnek: "S4:18" (Sütun 4, Satır 18)
    
    Returns:
        Kırpılmış görüntünün yolu ve koordinatları
    
    Örnekler:
        crop_by_cells("S3:8", "S3:20")  → Sütun 3'ün alt yarısını kırp
        crop_by_cells("S4:1", "S4:20")  → Sütun 4'ün tamamını kırp
        crop_by_cells("S2:5", "S3:15")  → S2-S3 arası bölgeyi kırp
    """
    import time
    from PIL import Image
    
    start_time = time.time()
    
    if not _grid_info:
        return "Hata: Önce draw_grid() çağırarak ızgara çiz!"
    
    image_path = _grid_info.get("image_path", _current_image_path)
    if not image_path or not os.path.exists(image_path):
        return f"Hata: Görüntü bulunamadı: {image_path}"
    
    try:
        columns = _grid_info.get("columns", {})
        rows = _grid_info.get("rows", 20)
        h = _grid_info.get("height", 0)
        w = _grid_info.get("width", 0)
        row_h = _grid_info.get("row_height", h // rows)
        
        # Başlangıç ve bitiş hücrelerini parse et
        start_bounds = _parse_tsg_cell(start_cell, _grid_info)
        end_bounds = _parse_tsg_cell(end_cell, _grid_info)
        
        # Kırpma koordinatları: start'ın sol-üst'ünden end'in sağ-alt'ına
        x1 = start_bounds[0]   # start sütununun sol kenarı
        y1 = start_bounds[1]   # start satırının üst kenarı
        x2 = end_bounds[2]     # end sütununun sağ kenarı
        y2 = end_bounds[3]     # end satırının alt kenarı
        
        # Sınır kontrolü
        x1 = max(0, x1)
        y1 = max(0, y1)
        x2 = min(w, x2)
        y2 = min(h, y2)
        
        # Görüntüyü kırp
        img = Image.open(image_path)
        cropped = img.crop((x1, y1, x2, y2))
        
        # Kaydet
        image_dir = os.path.dirname(image_path)
        crops_dir = os.path.join(image_dir, "..", "crops")
        os.makedirs(crops_dir, exist_ok=True)
        
        ts = datetime.now().strftime("%H%M%S")
        crop_index = len(_cropped_regions) + 1
        ext = os.path.splitext(image_path)[1]
        
        # Dosya adında okunabilir referans
        safe_start = start_cell.replace(":", "")
        safe_end = end_cell.replace(":", "")
        output_path = os.path.join(
            crops_dir,
            f"{ts}_{crop_index:02d}_crop_{safe_start}_{safe_end}{ext}",
        )
        cropped.save(output_path)
        
        duration_ms = (time.time() - start_time) * 1000
        
        # Kırpılan bölgeyi kaydet
        _cropped_regions.append({
            "coords": (x1, y1, x2, y2),
            "cells": f"{start_cell} → {end_cell}",
            "desc": f"TSG {start_cell} → {end_cell}",
        })
        
        _log_tool_call(
            "crop_by_cells",
            "local",
            {"start_cell": start_cell, "end_cell": end_cell},
            output_path,
            duration_ms,
        )
        
        crop_w = x2 - x1
        crop_h = y2 - y1
        
        result = (
            f"Kırpıldı: {output_path}\n"
            f"Koordinatlar: ({x1}, {y1}) - ({x2}, {y2})\n"
            f"Boyut: {crop_w}x{crop_h} piksel\n"
            f"Bölge: {start_cell} → {end_cell}"
        )
        
        logger.info(f"TSG Crop: {start_cell}-{end_cell} → ({x1},{y1})-({x2},{y2})")
        return result
        
    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        error_msg = f"Kırpma hatası: {e}"
        _log_tool_call(
            "crop_by_cells", "local",
            {"start_cell": start_cell, "end_cell": end_cell},
            error_msg, duration_ms,
        )
        logger.error(error_msg)
        return error_msg


def reset_grid_info() -> None:
    """Grid bilgisini sıfırla."""
    global _grid_info
    _grid_info = {}


def get_ocr_tools() -> list:
    """
    AgenticOCR için tüm custom tool'ları döndürür.
    
    Returns:
        List of LangChain tools
    """
    return [
        get_image_path,  # Dosya yolunu almak için
        draw_grid,       # TSG-aware koordinat ızgarası çiz
        crop_by_cells,   # TSG hücre referansı ile kırp
        ocr_cropped_image,
        mark_cropped,
        mark_skipped,
        get_page_log,
        finish_page,
    ]


def get_grid_tools() -> list:
    """
    Sadece grid ve crop tool'larını döndürür.
    
    Goal-driven mode için minimal tool seti.
    
    Returns:
        List of LangChain tools
    """
    return [
        get_image_path,
        draw_grid,
        crop_by_cells,
    ]
