# -*- coding: utf-8 -*-
"""
Gemini OCR Agent

Paralel Gemini OCR işlemi yapan bağımsız agent.
Tüm sayfaları asyncio.gather ile paralel işler.
Her sayfa için ayrı .md dosyası oluşturur.
Token kullanım ve maliyet loglar.

Usage:
    from src.agents.gemini_ocr_agent import GeminiOCRAgent

    agent = GeminiOCRAgent()
    await agent.initialize()

    result = await agent.process(
        image_list=["/path/to/page1.png", "/path/to/page2.png"],
        output_dir="/path/to/output",
    )
"""

import os
import time
import logging
import asyncio
from typing import Dict, Any, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

# Environment variables
GEMINI_OCR_MODEL = os.getenv("GEMINI_OCR_MODEL", "gemini-2.5-flash")
GEMINI_MAX_CONCURRENT = int(os.getenv("GEMINI_MAX_CONCURRENT", "5"))

# Google GenAI import
try:
    from google import genai
    from google.genai import types as genai_types

    GEMINI_AVAILABLE = True
except ImportError as e:
    logger.warning("Google GenAI not available: %s", e)
    GEMINI_AVAILABLE = False
    genai = None
    genai_types = None


class GeminiOCRAgent:
    """
    Paralel Gemini OCR Agent.

    Tüm sayfaları asyncio.gather ile paralel işler.
    Her sayfa için ayrı .md dosyası oluşturur.
    Token kullanım ve maliyet loglar.
    """

    def __init__(self):
        self._gemini_client = None
        self._initialized = False
        # Token sayaçları
        self._total_input_tokens = 0
        self._total_output_tokens = 0

    async def initialize(self) -> None:
        """Gemini client başlat."""
        if self._initialized:
            return

        if not GEMINI_AVAILABLE:
            raise ImportError("google-genai paketi kurulu değil")

        gemini_api_key = os.environ.get("GEMINI_API_KEY")
        if not gemini_api_key:
            raise ValueError("GEMINI_API_KEY not set")

        self._gemini_client = genai.Client(api_key=gemini_api_key)
        self._initialized = True
        logger.info(f"✅ GeminiOCRAgent initialized: {GEMINI_OCR_MODEL}")
        print(f"   ✅ GeminiOCRAgent initialized: {GEMINI_OCR_MODEL}", flush=True)

    def _reset_counters(self) -> None:
        """Token sayaçlarını sıfırla."""
        self._total_input_tokens = 0
        self._total_output_tokens = 0

    async def process(
        self,
        image_list: List[str],
        output_dir: str,
        file_name: Optional[str] = None,
        max_concurrent: int = GEMINI_MAX_CONCURRENT,
    ) -> Dict[str, Any]:
        """
        Tüm sayfaları paralel OCR yap ve opsiyonel olarak hedef şirketi ayıkla.

        Args:
            image_list: Görüntü dosya yolları listesi
            output_dir: OCR çıktı dizini
            file_name: Dosya adı (verilirse hedef şirket bu isimden tespit edilip extraction yapılır)
            max_concurrent: Maksimum paralel istek sayısı

        Returns:
            {
                "ocr_files": ["/path/page_001_ocr.md", ...],
                "ocr_texts": ["sayfa1 metni", "sayfa2 metni", ...],
                "merged_text": "tüm sayfaların birleşmiş metni",
                "total_chars": int,
                "page_count": int,
                "duration_ms": int,
                "errors": [...],
                "token_usage": {
                    "input_tokens": int,
                    "output_tokens": int,
                    "total_tokens": int,
                    "cost_usd": float,
                },
                # file_name verilmişse ek alanlar:
                "extraction": {
                    "extracted_text": str,
                    "extracted_file": str,
                    "found": bool,
                    ...
                }
            }
        """
        if not self._initialized:
            await self.initialize()

        # Token sayaçlarını sıfırla
        self._reset_counters()

        logger.info(
            f"📖 GeminiOCRAgent: Starting parallel OCR for {len(image_list)} pages"
        )
        print(
            f"\n{'='*60}\n"
            f"📖 GEMINI OCR AGENT START\n"
            f"   Model: {GEMINI_OCR_MODEL}\n"
            f"   Pages: {len(image_list)}\n"
            f"   Max Concurrent: {max_concurrent}\n"
            f"   Output: {output_dir}\n"
            f"{'='*60}",
            flush=True,
        )

        start_time = time.time()
        sorted_images = sorted(image_list)

        # Output dizini oluştur
        ocr_output_dir = os.path.join(output_dir, "ocr_pages")
        os.makedirs(ocr_output_dir, exist_ok=True)

        # Semaphore ile concurrent limit
        semaphore = asyncio.Semaphore(max_concurrent)

        async def ocr_with_semaphore(page_path: str, page_num: int) -> Dict[str, Any]:
            async with semaphore:
                return await self._ocr_single_page(page_path, page_num, ocr_output_dir)

        # Paralel OCR
        tasks = [
            ocr_with_semaphore(page_path, idx + 1)
            for idx, page_path in enumerate(sorted_images)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Sonuçları topla
        ocr_files = []
        ocr_texts = []
        total_chars = 0
        errors = []

        for idx, result in enumerate(results):
            page_num = idx + 1
            if isinstance(result, Exception):
                logger.error(f"   ❌ Page {page_num} failed: {result}")
                errors.append({"page": page_num, "error": str(result)})
                ocr_texts.append("")  # Boş metin ekle
            else:
                ocr_files.append(result["ocr_file"])
                ocr_texts.append(result["ocr_text"])
                total_chars += result["char_count"]
                print(
                    f"   ✅ Page {page_num}: {result['char_count']} chars, "
                    f"tokens(in={result.get('input_tokens', 0)}, out={result.get('output_tokens', 0)}), "
                    f"{result['duration_ms']}ms",
                    flush=True,
                )

        duration_ms = int((time.time() - start_time) * 1000)
        total_tokens = self._total_input_tokens + self._total_output_tokens

        # Maliyet hesapla
        pricing = self._get_model_pricing(GEMINI_OCR_MODEL)
        cost_input = self._total_input_tokens * pricing["input"] / 1_000_000
        cost_output = self._total_output_tokens * pricing["output"] / 1_000_000
        total_cost = cost_input + cost_output

        # Metinleri birleştir
        merged_text = self._merge_texts(ocr_texts)

        # Final log (OCR stage)
        logger.info(
            f"✅ GeminiOCRAgent OCR complete: {total_chars} chars, "
            f"tokens(in={self._total_input_tokens}, out={self._total_output_tokens}, total={total_tokens}), "
            f"cost=${total_cost:.4f}, {duration_ms}ms"
        )
        print(
            f"\n{'='*60}\n"
            f"✅ GEMINI OCR AGENT COMPLETE\n"
            f"   Pages: {len(sorted_images)}\n"
            f"   Total Chars: {total_chars:,}\n"
            f"   📊 Tokens: in={self._total_input_tokens:,}, out={self._total_output_tokens:,}, total={total_tokens:,}\n"
            f"   💰 Cost: ${total_cost:.4f}\n"
            f"   Duration: {duration_ms}ms\n"
            f"{'='*60}\n",
            flush=True,
        )

        result = {
            "ocr_files": ocr_files,
            "ocr_texts": ocr_texts,
            "merged_text": merged_text,
            "total_chars": total_chars,
            "page_count": len(sorted_images),
            "duration_ms": duration_ms,
            "errors": errors,
            "token_usage": {
                "input_tokens": self._total_input_tokens,
                "output_tokens": self._total_output_tokens,
                "total_tokens": total_tokens,
                "cost_usd": total_cost,
            },
        }
        
        # Hedef şirket extraction (file_name'den tespit edilir)
        if file_name and merged_text:
            extraction_result = await self.extract_target_company(
                merged_text=merged_text,
                output_dir=output_dir,
                file_name=file_name,
            )
            result["extraction"] = extraction_result
            
            # Token kullanımını güncelle
            result["token_usage"]["input_tokens"] += extraction_result["token_usage"]["input_tokens"]
            result["token_usage"]["output_tokens"] += extraction_result["token_usage"]["output_tokens"]
            result["token_usage"]["total_tokens"] += extraction_result["token_usage"]["total_tokens"]
            result["token_usage"]["cost_usd"] += extraction_result["token_usage"]["cost_usd"]
            result["duration_ms"] += extraction_result["duration_ms"]
        
        return result

    async def _ocr_single_page(
        self,
        image_path: str,
        page_num: int,
        output_dir: str,
    ) -> Dict[str, Any]:
        """
        Tek sayfa OCR yap ve dosyaya kaydet.

        Returns:
            {
                "ocr_file": "/path/to/page_001_ocr.md",
                "ocr_text": "sayfa metni",
                "char_count": int,
                "duration_ms": int,
                "input_tokens": int,
                "output_tokens": int,
            }
        """
        start_time = time.time()

        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Image not found: {image_path}")

        # Görüntüyü oku
        with open(image_path, "rb") as f:
            image_data = f.read()

        mime_type = "image/png" if image_path.lower().endswith(".png") else "image/jpeg"

        # OCR prompt - adım adım kolon okuma
        ocr_prompt = """Türkiye Ticaret Sicil Gazetesi - 5 kolonlu sayfa.

ADIM 1: Sayfadaki dikey kolon ayırıcı çizgileri bul (genelde 5 kolon var)
ADIM 2: Sol kolondan başla, TAMAMEN bitir, sonra sağa geç
ADIM 3: Her kolon için üstten alta oku

HER KOLONU AYRI PARAGRAF OLARAK YAZ.

KRİTİK HATA (YAPMA):
- Kolonlar arası metin karıştırma
- Bir kolondan diğerine atlama
- Aynı satırı soldan sağa okuma

ÇIKTI: Sadece OCR metni. Yorum, açıklama, analiz EKLEME."""

        # Gemini API çağrısı
        response = await asyncio.to_thread(
            self._gemini_client.models.generate_content,
            model=GEMINI_OCR_MODEL,
            contents=[
                genai_types.Content(
                    role="user",
                    parts=[
                        genai_types.Part.from_bytes(
                            data=image_data, mime_type=mime_type
                        ),
                        genai_types.Part.from_text(text=ocr_prompt),
                    ],
                ),
            ],
            config=genai_types.GenerateContentConfig(
                temperature=0.0,
            ),
        )

        ocr_text = response.text.strip() if response and response.text else ""
        duration_ms = int((time.time() - start_time) * 1000)

        # Token bilgisi
        input_tokens = 0
        output_tokens = 0
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            input_tokens = (
                getattr(response.usage_metadata, "prompt_token_count", 0) or 0
            )
            output_tokens = (
                getattr(response.usage_metadata, "candidates_token_count", 0) or 0
            )

            # Aggregate'e ekle
            self._total_input_tokens += input_tokens
            self._total_output_tokens += output_tokens

            logger.debug(
                f"   📊 Gemini Page {page_num}: in={input_tokens}, out={output_tokens}, {duration_ms}ms"
            )

        # Dosyaya kaydet
        ocr_file = os.path.join(output_dir, f"page_{page_num:03d}_ocr.md")
        with open(ocr_file, "w", encoding="utf-8") as f:
            f.write(f"[[PAGE:{page_num}]]\n\n")
            f.write(ocr_text)

        logger.debug(f"   💾 Page {page_num} saved: {ocr_file}")

        return {
            "ocr_file": ocr_file,
            "ocr_text": ocr_text,
            "char_count": len(ocr_text),
            "duration_ms": duration_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }

    def _merge_texts(self, ocr_texts: List[str], start_page: int = 1) -> str:
        """OCR metinlerini tek metin olarak birleştir."""
        parts = []
        for i, text in enumerate(ocr_texts):
            page_num = start_page + i
            if text:  # Boş metinleri atla
                parts.append(f"[[PAGE:{page_num}]]\n{text}\n")

        return "\n".join(parts)

    def _load_extraction_prompt(self, file_name: str) -> str:
        """Hedef şirket çıkarım prompt'unu yükle."""
        prompt_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "prompts",
            "akkok-sicil",
            "gemini_extraction_prompt.md",
        )
        
        if os.path.exists(prompt_path):
            with open(prompt_path, "r", encoding="utf-8") as f:
                prompt = f.read()
            return prompt.replace("{{FILE_NAME}}", file_name)
        else:
            return f"""Dosya adı: "{file_name}"

Bu dosya adından hedef şirketin hangisi olduğunu tespit et.
Verilen OCR metninden SADECE bu hedef şirketin ilanını ayıkla.
Diğer şirketlerin içeriklerini TAMAMEN atla.
Sayfa bilgilerini koru: [[PAGE:X]]
İlan metnini AYNEN kopyala, değiştirme."""

    async def extract_target_company(
        self,
        merged_text: str,
        output_dir: str,
        file_name: str = "extracted",
    ) -> Dict[str, Any]:
        """
        Birleştirilmiş OCR metninden hedef şirketin içeriğini çıkar.
        
        Args:
            merged_text: Tüm sayfaların birleşmiş OCR metni
            output_dir: Çıktı dizini
            file_name: Dosya adı (hedef şirket bu dosya adından tespit edilir)
        
        Returns:
            {
                "extracted_text": str,
                "extracted_file": str,
                "char_count": int,
                "found": bool,
                "token_usage": {...},
                "duration_ms": int,
            }
        """
        if not self._initialized:
            await self.initialize()
        
        start_time = time.time()
        
        logger.info(f"🎯 Extracting from file: {file_name}")
        print(
            f"\n{'='*60}\n"
            f"🎯 GEMINI EXTRACTION START\n"
            f"   File: {file_name}\n"
            f"   Input: {len(merged_text):,} chars\n"
            f"{'='*60}",
            flush=True,
        )
        
        # Prompt'u yükle (file_name'den hedef şirket tespit edilecek)
        extraction_prompt = self._load_extraction_prompt(file_name)
        
        # Gemini API çağrısı (metin modunda)
        user_prompt = f"""{extraction_prompt}

---

# OCR Metni

{merged_text}

---

**HATIRLATMA: Dosya adından ({file_name}) hangi şirket olduğunu tespit et ve sadece o şirketin içeriğini çıkar.**"""
        
        response = await asyncio.to_thread(
            self._gemini_client.models.generate_content,
            model=GEMINI_OCR_MODEL,
            contents=[
                genai_types.Content(
                    role="user",
                    parts=[
                        genai_types.Part.from_text(text=user_prompt),
                    ],
                ),
            ],
            config=genai_types.GenerateContentConfig(
                temperature=0.0,
                max_output_tokens=32000,
            ),
        )
        
        extracted_text = response.text.strip() if response and response.text else ""
        duration_ms = int((time.time() - start_time) * 1000)
        
        # Token bilgisi
        input_tokens = 0
        output_tokens = 0
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            input_tokens = (
                getattr(response.usage_metadata, "prompt_token_count", 0) or 0
            )
            output_tokens = (
                getattr(response.usage_metadata, "candidates_token_count", 0) or 0
            )
        
        # Maliyet hesapla
        pricing = self._get_model_pricing(GEMINI_OCR_MODEL)
        cost_input = input_tokens * pricing["input"] / 1_000_000
        cost_output = output_tokens * pricing["output"] / 1_000_000
        total_cost = cost_input + cost_output
        
        # Hedef şirket bulundu mu?
        found = "[[HEDEF ŞİRKET BULUNAMADI]]" not in extracted_text and len(extracted_text) > 100
        
        # Dosyaya kaydet
        os.makedirs(output_dir, exist_ok=True)
        extracted_file = os.path.join(output_dir, f"{file_name}_extracted.md")
        with open(extracted_file, "w", encoding="utf-8") as f:
            f.write(f"# {file_name} - Extracted Content\n\n")
            f.write(f"_Extraction Date: {datetime.now().isoformat()}_\n\n")
            f.write("---\n\n")
            f.write(extracted_text)
        
        logger.info(
            f"✅ Extraction complete: {len(extracted_text):,} chars, "
            f"found={found}, cost=${total_cost:.4f}, {duration_ms}ms"
        )
        print(
            f"\n{'='*60}\n"
            f"✅ GEMINI EXTRACTION COMPLETE\n"
            f"   Found: {found}\n"
            f"   Output: {len(extracted_text):,} chars\n"
            f"   📊 Tokens: in={input_tokens:,}, out={output_tokens:,}\n"
            f"   💰 Cost: ${total_cost:.4f}\n"
            f"   Duration: {duration_ms}ms\n"
            f"   File: {extracted_file}\n"
            f"{'='*60}\n",
            flush=True,
        )
        
        return {
            "extracted_text": extracted_text,
            "extracted_file": extracted_file,
            "char_count": len(extracted_text),
            "found": found,
            "token_usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "cost_usd": total_cost,
            },
            "duration_ms": duration_ms,
        }

    @staticmethod
    def _get_model_pricing(model_id: str) -> Dict[str, float]:
        """
        Gemini model fiyat tablosu ($/MTok). Şubat 2026.
        https://ai.google.dev/gemini-api/docs/pricing

        Gemini 2.0 Flash: Input $0.10, Output $0.40
        Gemini 2.0 Flash-Lite: Input $0.075, Output $0.30
        Gemini 2.5 Flash: Input $0.30 (metin/resim), Output $2.50
        Gemini 2.5 Flash-Lite: Input $0.10, Output $0.40
        Gemini 2.5 Pro: Input $1.25, Output $10.0
        """
        m = (model_id or "").lower()

        # Gemini 2.5 serisi
        if "2.5-flash-lite" in m:
            return {"input": 0.10, "output": 0.40}
        if "2.5-flash" in m:
            return {"input": 0.30, "output": 2.50}
        if "2.5-pro" in m:
            return {"input": 1.25, "output": 10.0}

        # Gemini 2.0 serisi
        if "2.0-flash-lite" in m:
            return {"input": 0.075, "output": 0.30}
        if "2.0-flash" in m:
            return {"input": 0.10, "output": 0.40}

        # Default: Gemini 2.5 Flash
        return {"input": 0.30, "output": 2.50}

    async def close(self) -> None:
        """Cleanup."""
        self._gemini_client = None
        self._initialized = False


# ============================================================================
# SINGLETON & SYNC WRAPPER
# ============================================================================

_gemini_ocr_instance: Optional[GeminiOCRAgent] = None


async def get_gemini_ocr_agent() -> GeminiOCRAgent:
    """Singleton instance al."""
    global _gemini_ocr_instance
    if _gemini_ocr_instance is None:
        _gemini_ocr_instance = GeminiOCRAgent()
        await _gemini_ocr_instance.initialize()
    return _gemini_ocr_instance


def process_gemini_ocr(
    image_list: List[str],
    output_dir: str,
    file_name: Optional[str] = None,
    max_concurrent: int = GEMINI_MAX_CONCURRENT,
) -> Dict[str, Any]:
    """
    Sync wrapper for Celery tasks.
    
    Args:
        image_list: Görüntü dosya yolları listesi
        output_dir: OCR çıktı dizini
        file_name: Dosya adı (verilirse hedef şirket bu isimden tespit edilir)
        max_concurrent: Maksimum paralel istek sayısı
    """
    print(f"[GEMINI_OCR_AGENT] Called with {len(image_list)} images", flush=True)
    if file_name:
        print(f"[GEMINI_OCR_AGENT] File name: {file_name}", flush=True)

    async def _run():
        agent = await get_gemini_ocr_agent()
        return await agent.process(
            image_list=image_list,
            output_dir=output_dir,
            file_name=file_name,
            max_concurrent=max_concurrent,
        )

    try:
        loop = asyncio.get_running_loop()
        import nest_asyncio

        nest_asyncio.apply()
        return loop.run_until_complete(_run())
    except RuntimeError:
        return asyncio.run(_run())
