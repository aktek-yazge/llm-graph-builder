# -*- coding: utf-8 -*-
"""
Two-Stage OCR Pipeline

İki aşamalı OCR pipeline:
1. Gemini 2.0 Flash: Tüm sayfaların full-page OCR'ı
2. Claude Opus 4.5: Hedef şirket filtreleme + entity extraction

Bu yapı, agentic_ocr.py'nin alternatifi olarak tasarlanmıştır.
Grid/crop tool'ları kullanılmaz, bunun yerine:
- Stage 1: Gemini basit OCR yapar (görüntü → metin)
- Stage 2: Claude metin üzerinde çalışır (filtreleme + extraction)

Usage:
    from src.two_stage_ocr import TwoStageOCR

    ocr = TwoStageOCR()
    await ocr.initialize()

    result = await ocr.process(
        image_list=["/path/to/page1.png", "/path/to/page2.png"],
        file_name="Aksa-09.03.2016-9028",
        file_id=123
    )
"""

import os
import re
import json
import time
import logging
import asyncio
from typing import Dict, Any, List, Optional, TYPE_CHECKING
from datetime import datetime

from langchain_neo4j import Neo4jGraph
from pydantic import SecretStr

logger = logging.getLogger(__name__)

print(f"[TWO_STAGE_OCR_MODULE] Loaded at {datetime.now()}", flush=True)

# ============================================================================
# LANGCHAIN IMPORTS
# ============================================================================

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

# Anthropic (Claude)
try:
    from langchain_anthropic import ChatAnthropic
    LANGCHAIN_ANTHROPIC_AVAILABLE = True
    logger.info("✅ LangChain ChatAnthropic imported")
except ImportError as e:
    logger.warning(f"⚠️ LangChain Anthropic not available: {e}")
    LANGCHAIN_ANTHROPIC_AVAILABLE = False
    ChatAnthropic = None  # type: ignore

# Google Gemini (native client for OCR)
try:
    from google import genai
    from google.genai import types as genai_types
    GEMINI_AVAILABLE = True
    logger.info("✅ Google GenAI imported")
except ImportError as e:
    logger.warning(f"⚠️ Google GenAI not available: {e}")
    GEMINI_AVAILABLE = False
    genai = None  # type: ignore
    genai_types = None  # type: ignore

# Langfuse LLM Observability
try:
    from src.shared.langfuse_client import (
        get_langfuse_callback_handler,
        log_llm_usage,
    )
    LANGFUSE_AVAILABLE = True
    logger.info("✅ Langfuse client imported")  # noqa: G004
except ImportError as e:
    logger.warning("Langfuse not available: %s", e)
    LANGFUSE_AVAILABLE = False
    get_langfuse_callback_handler = None  # type: ignore
    log_llm_usage = None  # type: ignore


# ============================================================================
# ENVIRONMENT VARIABLES
# ============================================================================
# Stage 1: Gemini OCR
GEMINI_OCR_MODEL = os.getenv("GEMINI_OCR_MODEL", "gemini-2.0-flash")

# Stage 2: Claude Extraction
TWO_STAGE_EXTRACT_MODEL = os.getenv("TWO_STAGE_EXTRACT_MODEL", "claude-opus-4-5")


class TwoStageOCR:
    """
    Two-Stage OCR Pipeline.

    İki aşamalı OCR:
    1. Gemini 2.0 Flash: Tüm sayfaların full-page OCR'ı
    2. Claude Opus 4.5: Hedef şirket filtreleme + entity extraction

    Tool-use agent loop kullanılmaz. Direkt 2 API çağrısı yapılır:
    - Stage 1: Her sayfa için Gemini OCR (paralel veya sıralı)
    - Stage 2: Tüm metin Claude'a gönderilir, filtreleme + extraction yapılır
    """

    def __init__(self):
        self._gemini_client = None
        self._claude_model: Optional["BaseChatModel"] = None
        self._initialized = False
        self._graph: Optional[Neo4jGraph] = None

    async def initialize(self) -> None:
        """Gemini ve Claude client'larını başlat."""
        if self._initialized:
            return

        # ================================================================
        # STAGE 1: Gemini Client
        # ================================================================
        if not GEMINI_AVAILABLE:
            raise ImportError(
                "google-genai paketi kurulu değil. `uv add google-genai` ile kurun."
            )

        gemini_api_key = os.environ.get("GEMINI_API_KEY")
        if not gemini_api_key:
            raise ValueError("GEMINI_API_KEY environment variable not set")

        self._gemini_client = genai.Client(api_key=gemini_api_key)
        logger.info(f"✅ Gemini client initialized for OCR: {GEMINI_OCR_MODEL}")
        print(f"   ✅ Gemini client initialized: {GEMINI_OCR_MODEL}", flush=True)

        # ================================================================
        # STAGE 2: Claude Client
        # ================================================================
        if not LANGCHAIN_ANTHROPIC_AVAILABLE or ChatAnthropic is None:
            raise ImportError(
                "langchain-anthropic paketi kurulu değil. `uv add langchain-anthropic` ile kurun."
            )

        anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable not set")

        # Model name mapping
        model_mapping = {
            "claude-opus-4.5": "claude-opus-4-5-20251101",
            "claude-opus-4-5": "claude-opus-4-5-20251101",
            "claude-opus-4": "claude-opus-4-20250514",
            "claude-sonnet-4": "claude-sonnet-4-20250514",
            "claude-sonnet-4-5": "claude-sonnet-4-5-20250929",
            "claude-sonnet": "claude-sonnet-4-20250514",
        }
        actual_model = model_mapping.get(TWO_STAGE_EXTRACT_MODEL, TWO_STAGE_EXTRACT_MODEL)

        model_kwargs: Dict[str, Any] = {
            "model": actual_model,
            "api_key": SecretStr(anthropic_api_key),
            "max_tokens": 16384,
        }

        self._claude_model = ChatAnthropic(**model_kwargs)
        logger.info(f"✅ Claude client initialized for extraction: {actual_model}")
        print(f"   ✅ Claude client initialized: {actual_model}", flush=True)

        self._initialized = True

    # ========================================================================
    # STAGE 1: GEMINI FULL-PAGE OCR
    # ========================================================================

    async def _stage1_gemini_ocr(
        self,
        image_list: List[str],
        file_name: str = "unknown",
    ) -> Dict[str, Any]:
        """
        Stage 1: Tüm sayfaları Gemini 2.0 Flash ile OCR yap.

        Her sayfa için ayrı OCR çağrısı yapılır ve sonuçlar birleştirilir.

        Args:
            image_list: Sayfa görüntü yolları listesi
            file_name: Dosya adı (loglama için)

        Returns:
            {
                "full_text": "Tüm sayfaların birleştirilmiş metni",
                "page_texts": ["Sayfa 1 metni", "Sayfa 2 metni", ...],
                "total_chars": int,
                "duration_ms": int,
            }
        """
        if not self._gemini_client:
            raise RuntimeError("Gemini client not initialized. Call initialize() first.")

        logger.info(f"📖 Stage 1: Starting Gemini OCR for {len(image_list)} pages")
        print(f"\n📖 STAGE 1: Gemini OCR ({len(image_list)} pages)", flush=True)

        start_time = time.time()
        page_texts: List[str] = []
        sorted_images = sorted(image_list)

        for page_idx, page_path in enumerate(sorted_images):
            page_num = page_idx + 1
            page_path = os.path.abspath(page_path)

            logger.info(f"   📄 OCR Page {page_num}/{len(sorted_images)}: {page_path}")
            print(f"   📄 OCR Page {page_num}/{len(sorted_images)}...", flush=True)

            page_start = time.time()

            try:
                page_text = await self._ocr_single_page(page_path, page_num)
                page_duration = int((time.time() - page_start) * 1000)

                if page_text:
                    page_texts.append(f"--- SAYFA {page_num} ---\n\n{page_text}")
                    logger.info(
                        f"   ✅ Page {page_num}: {len(page_text)} chars, {page_duration}ms"
                    )
                    print(
                        f"   ✅ Page {page_num}: {len(page_text)} chars, {page_duration}ms",
                        flush=True,
                    )
                else:
                    logger.warning(f"   ⚠️ Page {page_num}: Empty OCR result")
                    print(f"   ⚠️ Page {page_num}: Empty result", flush=True)

            except Exception as e:
                logger.error(f"   ❌ Page {page_num} OCR failed: {e}")
                print(f"   ❌ Page {page_num} OCR failed: {e}", flush=True)
                page_texts.append(f"--- SAYFA {page_num} ---\n\n[OCR HATASI: {e}]")

        # Tüm sayfa metinlerini birleştir
        full_text = "\n\n".join(page_texts)
        total_duration = int((time.time() - start_time) * 1000)

        logger.info(
            f"✅ Stage 1 complete: {len(full_text)} chars, {total_duration}ms"
        )
        print(
            f"   ✅ Stage 1 complete: {len(full_text)} chars, {total_duration}ms",
            flush=True,
        )

        # OCR çıktılarını dosyaya kaydet (debug için)
        if sorted_images:
            ocr_output_dir = os.path.join(
                os.path.dirname(sorted_images[0]), "..", "ocr_output"
            )
            os.makedirs(ocr_output_dir, exist_ok=True)

            # Her sayfa ayrı dosya
            for idx, page_text in enumerate(page_texts):
                page_file = os.path.join(ocr_output_dir, f"page_{idx + 1:03d}_ocr.md")
                with open(page_file, "w", encoding="utf-8") as f:
                    f.write(page_text)
                logger.info(f"   💾 Saved: {page_file}")

            # Birleştirilmiş tam metin
            full_file = os.path.join(ocr_output_dir, "full_ocr_text.md")
            with open(full_file, "w", encoding="utf-8") as f:
                f.write(full_text)
            logger.info(f"   💾 Full OCR saved: {full_file}")
            print(f"   💾 OCR saved to: {ocr_output_dir}", flush=True)

        # Langfuse logging
        if LANGFUSE_AVAILABLE and log_llm_usage:
            # Gemini 2.0 Flash pricing: $0.10/MTok input, $0.40/MTok output
            # Tahmini token sayısı (karakter/4)
            estimated_output_tokens = len(full_text) // 4
            estimated_cost = estimated_output_tokens * 0.40 / 1_000_000

            log_llm_usage(
                session_id=f"two-stage-ocr-{file_name}",
                model=GEMINI_OCR_MODEL,
                input_tokens=0,  # Görüntü token'ları farklı hesaplanıyor
                output_tokens=estimated_output_tokens,
                cached_tokens=0,
                cost_usd=estimated_cost,
                latency_ms=total_duration,
                step_name="stage1-gemini-ocr",
                metadata={
                    "file_name": file_name,
                    "pages": len(sorted_images),
                    "total_chars": len(full_text),
                },
            )

        return {
            "full_text": full_text,
            "page_texts": page_texts,
            "total_chars": len(full_text),
            "duration_ms": total_duration,
        }

    async def _ocr_single_page(self, image_path: str, _page_num: int) -> str:
        """
        Tek bir sayfa için Gemini OCR yap.

        Args:
            image_path: Sayfa görüntüsünün tam yolu
            _page_num: Sayfa numarası (unused, for future use)

        Returns:
            OCR sonucu (markdown formatında metin)
        """
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Image not found: {image_path}")

        # Görüntüyü oku
        with open(image_path, "rb") as f:
            image_data = f.read()

        # MIME type belirle
        if image_path.lower().endswith(".png"):
            mime_type = "image/png"
        elif image_path.lower().endswith((".jpg", ".jpeg")):
            mime_type = "image/jpeg"
        else:
            mime_type = "image/png"

        # OCR prompt - basit ve direkt
        ocr_prompt = """Bu görüntüdeki Türkçe metni tam ve doğru bir şekilde oku.

KURALLAR:
- Metni olduğu gibi, düzeltme yapmadan yaz
- Başlıkları ## ile işaretle
- Maddeleri numaralı veya madde işaretli liste olarak yaz
- Paragrafları koru
- Tarih, sayı, isim gibi bilgileri aynen aktar
- Çok kolonlu sayfalarda soldan sağa, yukarıdan aşağıya oku
- Sadece metni döndür, yorum veya açıklama ekleme

NOT: Bu bir Türkiye Ticaret Sicil Gazetesi sayfası olabilir. Birden fazla şirket ilanı içerebilir."""

        # Gemini API çağrısı
        response = await asyncio.to_thread(
            self._gemini_client.models.generate_content,
            model=GEMINI_OCR_MODEL,
            contents=[
                genai_types.Content(
                    role="user",
                    parts=[
                        genai_types.Part.from_bytes(data=image_data, mime_type=mime_type),
                        genai_types.Part.from_text(text=ocr_prompt),
                    ],
                ),
            ],
            config=genai_types.GenerateContentConfig(
                temperature=0.1,
            ),
        )

        if response and response.text:
            return response.text.strip()
        return ""

    # ========================================================================
    # STAGE 2: CLAUDE EXTRACTION
    # ========================================================================

    async def _stage2_claude_extract(
        self,
        full_text: str,
        target_company: str,
        file_name: str = "unknown",
        page_count: int = 1,
    ) -> Dict[str, Any]:
        """
        Stage 2: Claude Opus ile hedef şirketi filtrele ve entity extract et.

        OCR sonucu metin üzerinde çalışır (görüntü yok).
        Hedef şirketin bilgilerini filtreler ve yapılandırılmış JSON döndürür.

        Args:
            full_text: Stage 1'den gelen birleştirilmiş OCR metni
            target_company: Hedef şirket adı
            file_name: Dosya adı (loglama için)
            page_count: Toplam sayfa sayısı

        Returns:
            {
                "data": {...},  # JSON output (chunks, nodes, relationships)
                "duration_ms": int,
            }
        """
        if not self._claude_model:
            raise RuntimeError("Claude client not initialized. Call initialize() first.")

        logger.info(f"🔍 Stage 2: Starting Claude extraction for '{target_company}'")
        print(
            f"\n🔍 STAGE 2: Claude Extraction (target: {target_company})",
            flush=True,
        )

        start_time = time.time()

        # System prompt yükle
        system_prompt = self._get_system_prompt()

        # Graph schema çek
        graph_schema = self._get_graph_schema()

        # User prompt oluştur
        user_prompt = self._build_extraction_prompt(
            full_text=full_text,
            target_company=target_company,
            page_count=page_count,
            graph_schema=graph_schema,
        )

        logger.info(f"📦 System prompt: {len(system_prompt)} chars")
        logger.info(f"📝 User prompt: {len(user_prompt)} chars")
        print(f"   📦 System prompt: {len(system_prompt)} chars", flush=True)
        print(f"   📝 User prompt: {len(user_prompt)} chars", flush=True)

        # LangChain messages
        from langchain_core.messages import SystemMessage, HumanMessage

        # System message with cache control
        system_message = SystemMessage(
            content=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral", "ttl": "5m"},
                },
            ]
        )

        # User message (metin-bazlı, görüntü yok)
        user_message = HumanMessage(
            content=[
                {
                    "type": "text",
                    "text": user_prompt,
                },
            ]
        )

        messages = [system_message, user_message]

        # Langfuse callbacks
        callbacks = []
        if LANGFUSE_AVAILABLE and get_langfuse_callback_handler:
            langfuse_handler = get_langfuse_callback_handler(
                session_id=f"two-stage-ocr-{file_name}",
                trace_name=f"stage2-extract-{file_name}",
                tags=["ocr", "extraction", "claude", "two_stage"],
                metadata={
                    "file_name": file_name,
                    "target_company": target_company,
                    "model": TWO_STAGE_EXTRACT_MODEL,
                },
            )
            if langfuse_handler:
                callbacks.append(langfuse_handler)

        # Claude API çağrısı
        try:
            response = await self._claude_model.ainvoke(
                messages,
                config={"callbacks": callbacks} if callbacks else None,
            )

            duration_ms = int((time.time() - start_time) * 1000)

            # Token bilgileri
            usage_metadata = getattr(response, "usage_metadata", None) or {}
            input_tokens = usage_metadata.get("input_tokens", 0) or 0
            output_tokens = usage_metadata.get("output_tokens", 0) or 0
            total_tokens = usage_metadata.get("total_tokens", 0) or 0

            input_details = usage_metadata.get("input_token_details", {}) or {}
            cache_read = input_details.get("cache_read", 0) or 0
            cache_creation = input_details.get("cache_creation", 0) or 0

            logger.info(
                f"📊 Tokens: input={input_tokens}, output={output_tokens}, "
                f"total={total_tokens}, cache_read={cache_read}, {duration_ms}ms"
            )
            print(
                f"   📊 Tokens: in={input_tokens}, out={output_tokens}, "
                f"total={total_tokens}, {duration_ms}ms",
                flush=True,
            )

            # Cache status
            if cache_read > 0:
                cache_pct = (cache_read / input_tokens * 100) if input_tokens > 0 else 0
                logger.info(f"✅ Cache HIT: {cache_read} tokens ({cache_pct:.1f}%)")
                print(f"   ✅ Cache HIT: {cache_read} tokens ({cache_pct:.1f}%)", flush=True)

            # Response içeriğini çıkar
            response_text = ""
            if hasattr(response, "content"):
                content = response.content
                if isinstance(content, str):
                    response_text = content
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            response_text = block.get("text", "")
                            break
                        elif isinstance(block, str):
                            response_text = block
                            break

            # JSON parse
            parsed = self._parse_json_response(response_text)

            # Langfuse maliyet loglama
            if LANGFUSE_AVAILABLE and log_llm_usage:
                pricing = self._get_model_pricing(TWO_STAGE_EXTRACT_MODEL)
                uncached_input = max(0, input_tokens - cache_read - cache_creation)
                cost = (
                    uncached_input * pricing["input"] / 1_000_000
                    + output_tokens * pricing["output"] / 1_000_000
                    + cache_read * pricing["cache_read"] / 1_000_000
                    + cache_creation * pricing["cache_write"] / 1_000_000
                )

                log_llm_usage(
                    session_id=f"two-stage-ocr-{file_name}",
                    model=TWO_STAGE_EXTRACT_MODEL,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cached_tokens=cache_read,
                    cost_usd=cost,
                    latency_ms=duration_ms,
                    step_name="stage2-claude-extract",
                    metadata={
                        "file_name": file_name,
                        "target_company": target_company,
                        "cache_creation": cache_creation,
                    },
                )

            return {
                "data": parsed.get("data", {}),
                "duration_ms": duration_ms,
            }

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.error(f"❌ Claude extraction failed: {e}")
            print(f"   ❌ Claude extraction failed: {e}", flush=True)
            return {
                "data": {"found": False, "error": str(e)},
                "duration_ms": duration_ms,
            }

    def _get_system_prompt(self) -> str:
        """
        Stage 2 için system prompt yükle.

        unified_ocr_system.md kullanılır, ancak metin-bazlı çalışma için
        uyarlanmış notlar eklenir.
        """
        from prompts import get_domain, load_prompt

        domain = get_domain()

        try:
            system_prompt = load_prompt("unified_ocr_system", domain)
            # Metin-bazlı çalışma notu ekle
            text_mode_note = """

## NOT: METİN-BAZLI ÇALIŞMA

Bu işlemde görüntü yerine OCR sonucu metin verilmektedir.
Gemini 2.0 Flash tarafından OCR yapılmış ham metin üzerinde çalışıyorsun.
Görevin, bu metinden hedef şirketin bilgilerini filtrelemek ve yapılandırılmış JSON çıktısı üretmek.
"""
            return system_prompt + text_mode_note
        except FileNotFoundError:
            logger.warning(f"unified_ocr_system.md not found for domain {domain}")
            return self._get_fallback_system_prompt()

    def _get_fallback_system_prompt(self) -> str:
        """Fallback system prompt."""
        return """Sen Türkiye Ticaret Sicil Gazetesi (TSG) uzmanısın.

Görevin: OCR sonucu metinden hedef şirketin bilgilerini filtrelemek ve bilgi grafiği için yapılandırılmış veri çıkarmak.

## NOT: METİN-BAZLI ÇALIŞMA

Bu işlemde görüntü yerine OCR sonucu metin verilmektedir.
Gemini 2.0 Flash tarafından OCR yapılmış ham metin üzerinde çalışıyorsun.

Çıktı formatı: JSON (chunks, nodes, relationships)

Önemli:
- Anlam bütünlüğünü koru (chunking)
- Mevcut şemadaki label ve ID pattern'lerini kullan
- Aynı entity için aynı ID kullan
"""

    def _build_extraction_prompt(
        self,
        full_text: str,
        target_company: str,
        page_count: int,
        graph_schema: str,
    ) -> str:
        """
        Stage 2 için user prompt oluştur.

        OCR sonucu metin ve hedef şirket bilgisi ile birlikte
        extraction talimatları verilir.
        """
        prompt = f"""## HEDEF ŞİRKET

**{target_company}**

## GRAF ŞEMASI

{graph_schema}

## OCR SONUCU METİN

Aşağıdaki metin, {page_count} sayfalık bir Türkiye Ticaret Sicil Gazetesi belgesinin Gemini 2.0 Flash ile OCR sonucudur.
Bu metinden SADECE "{target_company}" şirketine ait bilgileri çıkar.

```
{full_text}
```

## GÖREV

1. Yukarıdaki metinde "{target_company}" şirketine ait ilanı/ilanları bul
2. Bu şirkete ait TÜM bilgileri çıkar (diğer şirketleri ATLA)
3. Aşağıdaki JSON formatında yapılandırılmış çıktı üret

## JSON ÇIKTI FORMATI

```json
{{
  "found": true,
  "target_company": "{target_company}",
  "document_type": "string (örn: GENEL_KURUL_KARARI, SERMAYE_ARTIRIMI, vb.)",
  "chunks": [
    {{
      "id": "chunk_001",
      "text": "Chunk metni (anlam bütünlüğünü koru)",
      "position": 1,
      "page": 1
    }}
  ],
  "nodes": [
    {{
      "label": "Company|Person|Capital|Address|...",
      "id": "entity_id (şemadaki pattern'e uygun)",
      "properties": {{}},
      "chunk_ids": ["chunk_001"]
    }}
  ],
  "relationships": [
    {{
      "from_id": "entity_id_1",
      "to_id": "entity_id_2",
      "type": "RELATIONSHIP_TYPE",
      "properties": {{}}
    }}
  ],
  "is_complete": true
}}
```

## ÖNEMLİ KURALLAR

- Hedef şirket metinde YOKSA: `{{"found": false, "target_company": "{target_company}"}}`
- Entity ID'leri şemadaki pattern'lere uygun oluştur
- Aynı entity farklı chunk'larda geçiyorsa AYNI ID kullan
- Chunk'ları anlam bütünlüğüne göre böl (paragraf/madde bazlı)
- İlan devam ediyorsa is_complete: false yap

Şimdi analiz yap ve JSON çıktısını üret:"""

        return prompt

    def _get_graph_schema(self) -> str:
        """
        Neo4j'den mevcut graf şemasını çeker.
        """
        if self._graph is None:
            logger.info("📊 No Neo4j connection, using fallback schema guidance")
            return self._get_fallback_schema_text()

        try:
            from src.neo4j_schema_provider import get_schema_for_ocr

            schema_text = get_schema_for_ocr(self._graph, max_examples=5)
            logger.info(f"📊 Graph schema loaded ({len(schema_text)} chars)")
            return schema_text
        except Exception as e:
            logger.warning(f"⚠️ Could not load graph schema: {e}")
            return self._get_fallback_schema_text()

    def _get_fallback_schema_text(self) -> str:
        """Şema alınamadığında kullanılacak fallback metin."""
        return """*Graf şeması henüz mevcut değil veya alınamadı.*

Yeni entity'ler için aşağıdaki ID pattern'lerini kullan:
- Şirket: `company_[normalized_name]`
- Kişi: `person_[normalized_name]`
- Diğer: `[label_lowercase]_[normalized_identifier]`
"""

    def _parse_json_response(self, response_text: str) -> Dict[str, Any]:
        """
        Claude JSON yanıtını parse et.
        """
        text = response_text.strip()

        # Hedef şirket yoksa
        if text.upper() in ("YOK", "N/A", "NULL", ""):
            return {"data": {"found": False}, "continuation_note": "", "is_complete": True}

        # JSON block'u çıkar
        json_text = text
        json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if json_match:
            json_text = json_match.group(1).strip()

        # JSON parse et
        try:
            raw_data = json.loads(json_text)
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error: {e}")
            logger.debug(f"Raw text: {text[:500]}...")
            return {
                "data": {
                    "found": True,
                    "chunks": [{"id": "chunk_001", "text": text, "position": 1, "page": 1}],
                    "nodes": [],
                    "relationships": [],
                    "_parse_error": str(e),
                },
                "continuation_note": "",
                "is_complete": True,
            }

        # Şirket bulunamadıysa
        if not raw_data.get("found", True):
            return {"data": raw_data, "continuation_note": "", "is_complete": True}

        # Temel temizlik
        data = self._basic_cleanup(raw_data)

        return {
            "data": data,
            "continuation_note": "",
            "is_complete": data.get("is_complete", True),
        }

    def _basic_cleanup(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Temel veri temizliği."""
        # Chunk'lardaki boşlukları temizle
        for chunk in data.get("chunks", []):
            if "text" in chunk:
                chunk["text"] = chunk["text"].strip()

        # Node property'lerindeki boşlukları temizle
        for node in data.get("nodes", []):
            props = node.get("properties", {})
            for key, value in props.items():
                if isinstance(value, str):
                    props[key] = value.strip()

        return data

    @staticmethod
    def _get_model_pricing(model_id: str) -> Dict[str, float]:
        """Model bazlı fiyat tablosu ($/MTok)."""
        m = (model_id or "").lower()

        if "opus-4-5" in m or "opus-4.5" in m:
            return {"input": 5.0, "output": 25.0, "cache_write": 6.25, "cache_read": 0.50}
        if "opus-4" in m:
            return {"input": 15.0, "output": 75.0, "cache_write": 18.75, "cache_read": 1.50}
        if "sonnet" in m:
            return {"input": 3.0, "output": 15.0, "cache_write": 3.75, "cache_read": 0.30}
        if "haiku" in m:
            return {"input": 1.0, "output": 5.0, "cache_write": 1.25, "cache_read": 0.10}

        # Default: Opus 4.5
        return {"input": 5.0, "output": 25.0, "cache_write": 6.25, "cache_read": 0.50}

    # ========================================================================
    # MAIN PROCESS METHOD
    # ========================================================================

    async def process(
        self,
        image_list: List[str],
        file_name: str,
        file_id: Optional[int] = None,  # noqa: ARG002
        domain: Optional[str] = None,  # noqa: ARG002
        graph: Optional[Neo4jGraph] = None,
        output_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        İki aşamalı OCR pipeline'ı çalıştır.

        Args:
            image_list: İşlenecek sayfa görüntü yolları
            file_name: Dosya adı (şirket adı çıkarılır)
            file_id: Tracking için (API uyumluluğu için)
            domain: Domain (API uyumluluğu için)
            graph: Neo4j graph bağlantısı (şema çekmek için)
            output_dir: JSON çıktısı için dizin (opsiyonel)

        Returns:
            {
                "data": {...},  # JSON output
                "metadata": {...},
                "status": "success" | "error"
            }
        """
        # file_id ve domain API uyumluluğu için var, kullanılmıyor
        _ = file_id, domain
        self._graph = graph
        if not self._initialized:
            await self.initialize()

        target_company = self._extract_company_from_filename(file_name)

        logger.info(
            f"🚀 Starting Two-Stage OCR: file={file_name}, "
            f"target={target_company}, pages={len(image_list)}"
        )
        print(
            f"\n{'='*60}\n"
            f"🚀 TWO-STAGE OCR START\n"
            f"   Stage 1: Gemini 2.0 Flash (OCR)\n"
            f"   Stage 2: Claude Opus 4.5 (Extraction)\n"
            f"   Target: {target_company}\n"
            f"   Pages: {len(image_list)}\n"
            f"{'='*60}",
            flush=True,
        )

        total_start = time.time()

        try:
            # ================================================================
            # STAGE 1: Gemini OCR
            # ================================================================
            stage1_result = await self._stage1_gemini_ocr(
                image_list=image_list,
                file_name=file_name,
            )

            full_text = stage1_result["full_text"]

            if not full_text or len(full_text) < 100:
                logger.warning("⚠️ Stage 1 returned very little text, skipping Stage 2")
                return {
                    "data": {"found": False, "reason": "OCR sonucu yetersiz"},
                    "metadata": {
                        "target_company": target_company,
                        "pages_processed": len(image_list),
                        "stage1_chars": len(full_text),
                        "model_ocr": GEMINI_OCR_MODEL,
                        "model_extract": TWO_STAGE_EXTRACT_MODEL,
                    },
                    "status": "error",
                }

            # ================================================================
            # STAGE 2: Claude Extraction
            # ================================================================
            stage2_result = await self._stage2_claude_extract(
                full_text=full_text,
                target_company=target_company,
                file_name=file_name,
                page_count=len(image_list),
            )

            total_duration = int((time.time() - total_start) * 1000)

            # Output oluştur
            output = {
                "data": stage2_result["data"],
                "metadata": {
                    "target_company": target_company,
                    "pages_processed": len(image_list),
                    "stage1_chars": len(full_text),
                    "stage1_duration_ms": stage1_result["duration_ms"],
                    "stage2_duration_ms": stage2_result["duration_ms"],
                    "total_duration_ms": total_duration,
                    "model_ocr": GEMINI_OCR_MODEL,
                    "model_extract": TWO_STAGE_EXTRACT_MODEL,
                    "output_format": "json",
                },
                "status": "success",
            }

            # JSON çıktısını dosyaya kaydet
            if output_dir:
                self._save_json_output(output, file_name, output_dir)

            # Output summary
            data = stage2_result["data"]
            chunks_count = len(data.get("chunks", []))
            nodes_count = len(data.get("nodes", []))
            rels_count = len(data.get("relationships", []))

            print(
                f"\n{'='*60}\n"
                f"✅ TWO-STAGE OCR COMPLETE\n"
                f"   Stage 1 (OCR): {len(full_text)} chars, {stage1_result['duration_ms']}ms\n"
                f"   Stage 2 (Extract): {chunks_count} chunks, {nodes_count} nodes, "
                f"{rels_count} rels, {stage2_result['duration_ms']}ms\n"
                f"   Total: {total_duration}ms\n"
                f"{'='*60}\n",
                flush=True,
            )
            logger.info(
                f"✅ Two-Stage OCR completed: {chunks_count} chunks, {nodes_count} nodes, "
                f"{rels_count} relationships, {total_duration}ms"
            )

            return output

        except Exception as e:
            logger.error(f"❌ Two-Stage OCR failed: {e}", exc_info=True)
            return {
                "data": {},
                "metadata": {"target_company": target_company},
                "status": "error",
                "error": str(e),
            }

    def _save_json_output(
        self,
        output: Dict[str, Any],
        file_name: str,
        output_dir: str,
    ) -> None:
        """JSON çıktısını dosyaya kaydeder."""
        from pathlib import Path

        try:
            output_path = Path(output_dir)
            output_path.mkdir(parents=True, exist_ok=True)

            base_name = Path(file_name).stem
            json_file = output_path / f"{base_name}_two_stage_output.json"

            with open(json_file, "w", encoding="utf-8") as f:
                json.dump(output, f, ensure_ascii=False, indent=2)

            logger.info(f"📁 JSON output saved: {json_file}")
            print(f"📁 JSON output saved: {json_file}", flush=True)

        except Exception as e:
            logger.error(f"❌ Failed to save JSON output: {e}")

    def _extract_company_from_filename(self, file_name: str) -> str:
        """Dosya adından şirket adını çıkar."""
        match = re.match(r"^([A-Za-zÇçĞğİıÖöŞşÜü\s]+)", file_name)
        if match:
            company = match.group(1).strip()
            if company:
                return company
        parts = file_name.split("-")
        if parts:
            return parts[0].strip()
        return file_name

    async def close(self) -> None:
        """Cleanup."""
        self._gemini_client = None
        self._claude_model = None
        self._initialized = False


# ============================================================================
# SINGLETON & SYNC WRAPPER
# ============================================================================

_two_stage_ocr_instance: Optional[TwoStageOCR] = None


async def get_two_stage_ocr() -> TwoStageOCR:
    """TwoStageOCR singleton instance al."""
    global _two_stage_ocr_instance
    if _two_stage_ocr_instance is None:
        _two_stage_ocr_instance = TwoStageOCR()
        await _two_stage_ocr_instance.initialize()
    return _two_stage_ocr_instance


def process_two_stage_ocr(
    image_list: List[str],
    file_name: str,
    file_id: Optional[int] = None,
    domain: Optional[str] = None,
    graph: Optional[Neo4jGraph] = None,
    output_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Sync wrapper for TwoStageOCR.process().
    Celery task'larından çağrılmak üzere.

    Args:
        image_list: İşlenecek sayfa görüntü yolları
        file_name: Dosya adı
        file_id: Tracking için
        domain: Domain (opsiyonel)
        graph: Neo4j graph bağlantısı (şema çekmek için)
        output_dir: JSON çıktısı için dizin (opsiyonel)
    """
    print(
        f"[TWO_STAGE_OCR] Called with {len(image_list)} images, file={file_name}",
        flush=True,
    )

    async def _run():
        ocr = await get_two_stage_ocr()
        return await ocr.process(
            image_list=image_list,
            file_name=file_name,
            file_id=file_id,
            domain=domain,
            graph=graph,
            output_dir=output_dir,
        )

    try:
        loop = asyncio.get_running_loop()
        import nest_asyncio

        nest_asyncio.apply()
        return loop.run_until_complete(_run())
    except RuntimeError:
        return asyncio.run(_run())
