# -*- coding: utf-8 -*-
"""
Hybrid A2A OCR Pipeline

Agent-to-Agent (A2A) yapısında iki aşamalı OCR:
1. Gemini 2.0 Flash: Paralel full-page OCR (asyncio.gather)
2. Claude Opus 4.5: Sequential extraction with continuation context

Bu yapı, two_stage_ocr.py'nin optimize edilmiş versiyonudur:
- Gemini tüm sayfaları paralel işler (~3x hız)
- Claude sayfa sayfa işler ama önceki sayfanın context'ini alır
- Her sayfa için ayrı JSON çıktısı, sonra merge

Avantajlar:
- Paralel OCR ile ~50% hız kazancı
- Daha küçük Claude prompt'ları (daha hızlı yanıt)
- Memory efficient (sayfa sayfa işlem)
- Continuation context ile bağlam korunur

Usage:
    from src.hybrid_a2a_ocr import HybridA2AOCR

    ocr = HybridA2AOCR()
    await ocr.initialize()

    result = await ocr.process(
        image_list=["/path/to/page1.png", "/path/to/page2.png"],
        file_name="Aksa-09.03.2016-9028",
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
from pathlib import Path

from langchain_neo4j import Neo4jGraph
from pydantic import SecretStr

logger = logging.getLogger(__name__)

print(f"[HYBRID_A2A_OCR_MODULE] Loaded at {datetime.now()}", flush=True)

# ============================================================================
# IMPORTS
# ============================================================================

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

# Anthropic (Claude)
try:
    from langchain_anthropic import ChatAnthropic
    LANGCHAIN_ANTHROPIC_AVAILABLE = True
except ImportError as e:
    logger.warning("LangChain Anthropic not available: %s", e)
    LANGCHAIN_ANTHROPIC_AVAILABLE = False
    ChatAnthropic = None  # type: ignore

# Google Gemini (native client for OCR)
try:
    from google import genai
    from google.genai import types as genai_types
    GEMINI_AVAILABLE = True
except ImportError as e:
    logger.warning("Google GenAI not available: %s", e)
    GEMINI_AVAILABLE = False
    genai = None  # type: ignore
    genai_types = None  # type: ignore

# Langfuse
try:
    from src.shared.langfuse_client import (
        get_langfuse_callback_handler,
        log_llm_usage,
    )
    LANGFUSE_AVAILABLE = True
except ImportError:
    LANGFUSE_AVAILABLE = False
    get_langfuse_callback_handler = None  # type: ignore
    log_llm_usage = None  # type: ignore


# ============================================================================
# ENVIRONMENT VARIABLES
# ============================================================================
GEMINI_OCR_MODEL = os.getenv("GEMINI_OCR_MODEL", "gemini-2.0-flash")
CLAUDE_EXTRACT_MODEL = os.getenv("HYBRID_EXTRACT_MODEL", "claude-opus-4-5")

# Paralel OCR için max concurrent requests
GEMINI_MAX_CONCURRENT = int(os.getenv("GEMINI_MAX_CONCURRENT", "5"))


class HybridA2AOCR:
    """
    Hybrid Agent-to-Agent OCR Pipeline.

    Stage 1 (Parallel): Gemini 2.0 Flash ile tüm sayfaları paralel OCR
    Stage 2 (Sequential): Claude Opus ile sayfa sayfa extraction (context ile)

    A2A iletişimi dosya bazlı:
    - Gemini OCR sonuçlarını dosyaya yazar
    - Claude dosyaları okur ve işler
    """

    def __init__(self):
        self._gemini_client = None
        self._claude_model: Optional["BaseChatModel"] = None
        self._initialized = False
        self._graph: Optional[Neo4jGraph] = None
        self._output_dir: Optional[str] = None
        
        # Token sayaçları (tüm işlem için toplam)
        self._agg_gemini_input_tokens = 0
        self._agg_gemini_output_tokens = 0
        self._agg_claude_input_tokens = 0
        self._agg_claude_output_tokens = 0
        self._agg_claude_cache_read = 0
        self._agg_claude_cache_creation = 0

    async def initialize(self) -> None:
        """Client'ları başlat."""
        if self._initialized:
            return

        # Gemini Client
        if not GEMINI_AVAILABLE:
            raise ImportError("google-genai paketi kurulu değil")

        gemini_api_key = os.environ.get("GEMINI_API_KEY")
        if not gemini_api_key:
            raise ValueError("GEMINI_API_KEY not set")

        self._gemini_client = genai.Client(api_key=gemini_api_key)
        logger.info(f"✅ Gemini client initialized: {GEMINI_OCR_MODEL}")

        # Claude Client
        if not LANGCHAIN_ANTHROPIC_AVAILABLE or ChatAnthropic is None:
            raise ImportError("langchain-anthropic paketi kurulu değil")

        anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")

        model_mapping = {
            "claude-opus-4.5": "claude-opus-4-5-20251101",
            "claude-opus-4-5": "claude-opus-4-5-20251101",
            "claude-sonnet-4": "claude-sonnet-4-20250514",
        }
        actual_model = model_mapping.get(CLAUDE_EXTRACT_MODEL, CLAUDE_EXTRACT_MODEL)

        self._claude_model = ChatAnthropic(
            model=actual_model,
            api_key=SecretStr(anthropic_api_key),
            max_tokens=8192,
        )
        logger.info(f"✅ Claude client initialized: {actual_model}")

        self._initialized = True
        print(f"   ✅ HybridA2AOCR initialized", flush=True)

    def _reset_token_counters(self) -> None:
        """Token sayaçlarını sıfırla (yeni işlem başlangıcında)."""
        self._agg_gemini_input_tokens = 0
        self._agg_gemini_output_tokens = 0
        self._agg_claude_input_tokens = 0
        self._agg_claude_output_tokens = 0
        self._agg_claude_cache_read = 0
        self._agg_claude_cache_creation = 0

    @staticmethod
    def _get_model_pricing(model_id: str) -> Dict[str, float]:
        """
        Model bazlı fiyat tablosu ($/MTok). Şubat 2026.
        
        Anthropic Claude:
        Claude Opus 4.5: Input $5, Output $25, Cache Write $6.25, Cache Hit $0.50
        Claude Sonnet 4: Input $3, Output $15, Cache Write $3.75, Cache Hit $0.30
        
        Google Gemini:
        Gemini 2.0 Flash: Input $0.10, Output $0.40
        Gemini 2.5 Flash: Input $0.075, Output $0.30
        """
        m = (model_id or "").lower()
        
        # ── ANTHROPIC (Claude) ──
        if "opus-4-5" in m or "opus-4.5" in m or "opus-4-6" in m:
            return {"input": 5.0, "output": 25.0, "cache_write": 6.25, "cache_read": 0.50}
        if "opus-4-1" in m or "opus-4" in m:
            return {"input": 15.0, "output": 75.0, "cache_write": 18.75, "cache_read": 1.50}
        if "sonnet" in m:
            return {"input": 3.0, "output": 15.0, "cache_write": 3.75, "cache_read": 0.30}
        if "haiku" in m:
            return {"input": 1.0, "output": 5.0, "cache_write": 1.25, "cache_read": 0.10}
        
        # ── GOOGLE (Gemini) ──
        if "gemini-2.5-flash" in m:
            return {"input": 0.075, "output": 0.30, "cache_write": 0.0, "cache_read": 0.0}
        if "gemini-2.0-flash" in m or "gemini-flash" in m:
            return {"input": 0.10, "output": 0.40, "cache_write": 0.0, "cache_read": 0.0}
        if "gemini-2.5-pro" in m or "gemini-pro" in m:
            return {"input": 1.25, "output": 10.0, "cache_write": 0.0, "cache_read": 0.0}
        
        # Default: Claude Opus 4.5
        return {"input": 5.0, "output": 25.0, "cache_write": 6.25, "cache_read": 0.50}

    # ========================================================================
    # STAGE 1: PARALLEL GEMINI OCR
    # ========================================================================

    async def _stage1_parallel_ocr(
        self,
        image_list: List[str],
        file_name: str,
    ) -> Dict[str, Any]:
        """
        Stage 1: Tüm sayfaları paralel OCR yap.

        asyncio.gather ile tüm sayfalar aynı anda işlenir.
        Sonuçlar dosyaya kaydedilir.

        Returns:
            {
                "ocr_files": ["/path/to/page_001_ocr.md", ...],
                "page_count": int,
                "total_chars": int,
                "duration_ms": int,
            }
        """
        logger.info(f"📖 Stage 1: Parallel Gemini OCR for {len(image_list)} pages")
        print(f"\n📖 STAGE 1: Parallel Gemini OCR ({len(image_list)} pages)", flush=True)

        start_time = time.time()
        sorted_images = sorted(image_list)

        # Output dizini oluştur
        ocr_output_dir = os.path.join(self._output_dir, "ocr_pages")
        os.makedirs(ocr_output_dir, exist_ok=True)

        # Semaphore ile concurrent limit
        semaphore = asyncio.Semaphore(GEMINI_MAX_CONCURRENT)

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
        total_chars = 0
        total_gemini_input = 0
        total_gemini_output = 0
        errors = []

        for idx, result in enumerate(results):
            page_num = idx + 1
            if isinstance(result, Exception):
                logger.error(f"   ❌ Page {page_num} failed: {result}")
                errors.append({"page": page_num, "error": str(result)})
            else:
                ocr_files.append(result["ocr_file"])
                total_chars += result["char_count"]
                total_gemini_input += result.get("input_tokens", 0)
                total_gemini_output += result.get("output_tokens", 0)
                print(
                    f"   ✅ Page {page_num}: {result['char_count']} chars, "
                    f"tokens(in={result.get('input_tokens', 0)}, out={result.get('output_tokens', 0)}), "
                    f"{result['duration_ms']}ms",
                    flush=True,
                )

        duration_ms = int((time.time() - start_time) * 1000)
        gemini_total = total_gemini_input + total_gemini_output

        logger.info(
            f"✅ Stage 1 complete: {total_chars} chars, "
            f"tokens(in={total_gemini_input}, out={total_gemini_output}, total={gemini_total}), "
            f"{duration_ms}ms"
        )
        print(
            f"   ✅ Stage 1 complete: {total_chars} chars, "
            f"📊 tokens(in={total_gemini_input}, out={total_gemini_output}), {duration_ms}ms",
            flush=True,
        )

        return {
            "ocr_files": ocr_files,
            "page_count": len(sorted_images),
            "total_chars": total_chars,
            "duration_ms": duration_ms,
            "errors": errors,
            "gemini_input_tokens": total_gemini_input,
            "gemini_output_tokens": total_gemini_output,
        }

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
                "char_count": int,
                "duration_ms": int,
            }
        """
        start_time = time.time()

        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Image not found: {image_path}")

        # Görüntüyü oku
        with open(image_path, "rb") as f:
            image_data = f.read()

        mime_type = "image/png" if image_path.lower().endswith(".png") else "image/jpeg"

        # OCR prompt - yapısal tanım + kolon sınırı
        ocr_prompt = """Sen Türkiye Ticaret Sicil Gazetesi OCR uzmanısın.

Gazete sayfası dikey kolonlardan oluşur. Kolonlar dikey çizgilerle ayrılır. Kolon 1 en solda, son kolon en sağda.

Bir kolonu okurken dikey sınırın ötesine geçilmez. Önce bir kolon tamamen bitirilir, sonra sağdaki kolona geçilir.

Metni bu sırayla aktarırsın."""

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
                temperature=0.0,
            ),
        )

        ocr_text = response.text.strip() if response and response.text else ""
        duration_ms = int((time.time() - start_time) * 1000)

        # ── Gemini Token Bilgisi ──
        gemini_input_tokens = 0
        gemini_output_tokens = 0
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            gemini_input_tokens = getattr(response.usage_metadata, "prompt_token_count", 0) or 0
            gemini_output_tokens = getattr(response.usage_metadata, "candidates_token_count", 0) or 0
            
            # Aggregate'e ekle
            self._agg_gemini_input_tokens += gemini_input_tokens
            self._agg_gemini_output_tokens += gemini_output_tokens
            
            logger.info(
                f"   📊 Gemini Page {page_num}: in={gemini_input_tokens}, out={gemini_output_tokens}, {duration_ms}ms"
            )

        # Dosyaya kaydet
        ocr_file = os.path.join(output_dir, f"page_{page_num:03d}_ocr.md")
        with open(ocr_file, "w", encoding="utf-8") as f:
            f.write(f"# Sayfa {page_num}\n\n")
            f.write(ocr_text)

        logger.info(f"   💾 Page {page_num} saved: {ocr_file}")

        return {
            "ocr_file": ocr_file,
            "char_count": len(ocr_text),
            "duration_ms": duration_ms,
            "input_tokens": gemini_input_tokens,
            "output_tokens": gemini_output_tokens,
        }

    # ========================================================================
    # STAGE 2: SEQUENTIAL CLAUDE EXTRACTION WITH CONTEXT
    # ========================================================================

    async def _stage2_sequential_extract(
        self,
        ocr_files: List[str],
        target_company: str,
        file_name: str,
    ) -> Dict[str, Any]:
        """
        Stage 2: Sayfa sayfa Claude extraction (context ile).

        Her sayfa için:
        1. OCR dosyasını oku
        2. Önceki sayfanın context'i ile birlikte Claude'a gönder
        3. JSON çıktısını al ve kaydet
        4. Sonraki sayfa için context oluştur

        Returns:
            {
                "pages_data": [page1_json, page2_json, ...],
                "duration_ms": int,
            }
        """
        logger.info(f"🔍 Stage 2: Sequential Claude extraction for '{target_company}'")
        print(f"\n🔍 STAGE 2: Sequential Claude Extraction ({len(ocr_files)} pages)", flush=True)

        start_time = time.time()
        pages_data: List[Dict[str, Any]] = []
        continuation_context = ""  # Önceki sayfadan devam eden bağlam
        target_found = False

        # Graph schema
        graph_schema = self._get_graph_schema()

        for page_idx, ocr_file in enumerate(ocr_files):
            page_num = page_idx + 1

            print(f"   🔍 Extracting page {page_num}/{len(ocr_files)}...", flush=True)

            # OCR metnini oku
            with open(ocr_file, "r", encoding="utf-8") as f:
                page_text = f.read()

            # Claude extraction
            page_start = time.time()
            page_result = await self._extract_single_page(
                page_text=page_text,
                target_company=target_company,
                page_num=page_num,
                total_pages=len(ocr_files),
                continuation_context=continuation_context,
                graph_schema=graph_schema,
                file_name=file_name,
            )
            page_duration = int((time.time() - page_start) * 1000)

            # Sonuçları kaydet
            page_data = page_result.get("data", {})
            if page_data.get("found", False):
                target_found = True
                pages_data.append(page_data)

            # Context'i güncelle
            continuation_context = page_result.get("continuation_context", "")

            # İstatistikler
            chunks_count = len(page_data.get("chunks", []))
            nodes_count = len(page_data.get("nodes", []))

            print(
                f"   ✅ Page {page_num}: {chunks_count} chunks, {nodes_count} nodes, {page_duration}ms"
                f"{' [FOUND]' if page_data.get('found') else ''}"
                f"{' [CONTINUES]' if continuation_context else ''}",
                flush=True,
            )

            # JSON kaydet
            json_file = os.path.join(self._output_dir, "extract_pages", f"page_{page_num:03d}_extract.json")
            os.makedirs(os.path.dirname(json_file), exist_ok=True)
            with open(json_file, "w", encoding="utf-8") as f:
                json.dump(page_data, f, ensure_ascii=False, indent=2)

        duration_ms = int((time.time() - start_time) * 1000)
        
        # Claude token özeti
        claude_total = self._agg_claude_input_tokens + self._agg_claude_output_tokens

        logger.info(
            f"✅ Stage 2 complete: {len(pages_data)} pages with data, "
            f"tokens(in={self._agg_claude_input_tokens}, out={self._agg_claude_output_tokens}, total={claude_total}), "
            f"cache_read={self._agg_claude_cache_read}, cache_create={self._agg_claude_cache_creation}, "
            f"{duration_ms}ms"
        )
        print(
            f"   ✅ Stage 2 complete: {len(pages_data)} pages extracted, "
            f"📊 tokens(in={self._agg_claude_input_tokens}, out={self._agg_claude_output_tokens}), "
            f"{duration_ms}ms",
            flush=True,
        )

        return {
            "pages_data": pages_data,
            "target_found": target_found,
            "duration_ms": duration_ms,
            "claude_input_tokens": self._agg_claude_input_tokens,
            "claude_output_tokens": self._agg_claude_output_tokens,
            "claude_cache_read": self._agg_claude_cache_read,
            "claude_cache_creation": self._agg_claude_cache_creation,
        }

    async def _extract_single_page(
        self,
        page_text: str,
        target_company: str,
        page_num: int,
        total_pages: int,
        continuation_context: str,
        graph_schema: str,
        file_name: str,
    ) -> Dict[str, Any]:
        """
        Tek sayfa için Claude extraction.

        Returns:
            {
                "data": {...},  # JSON output
                "continuation_context": "...",  # Sonraki sayfa için context
            }
        """
        from langchain_core.messages import SystemMessage, HumanMessage

        # System prompt
        system_prompt = self._get_system_prompt()

        # User prompt
        user_prompt = self._build_page_extraction_prompt(
            page_text=page_text,
            target_company=target_company,
            page_num=page_num,
            total_pages=total_pages,
            continuation_context=continuation_context,
            graph_schema=graph_schema,
        )

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]

        # Langfuse callbacks
        callbacks = []
        if LANGFUSE_AVAILABLE and get_langfuse_callback_handler:
            handler = get_langfuse_callback_handler(
                session_id=f"hybrid-a2a-{file_name}",
                trace_name=f"extract-page-{page_num}",
                tags=["hybrid", "extraction", "claude"],
                metadata={"page_num": page_num, "target_company": target_company},
            )
            if handler:
                callbacks.append(handler)

        try:
            response = await self._claude_model.ainvoke(
                messages,
                config={"callbacks": callbacks} if callbacks else None,
            )

            # ================================================================
            # TOKEN USAGE LOGGING
            # ================================================================
            usage_metadata = getattr(response, "usage_metadata", None) or {}
            input_tokens = usage_metadata.get("input_tokens", 0) or 0
            output_tokens = usage_metadata.get("output_tokens", 0) or 0
            total_tokens = usage_metadata.get("total_tokens", 0) or 0
            
            # Cache bilgileri
            input_token_details = usage_metadata.get("input_token_details", {}) or {}
            cache_read = input_token_details.get("cache_read", 0) or 0
            cache_creation = input_token_details.get("cache_creation", 0) or 0
            
            # Token log
            logger.info(
                f"📊 Page {page_num} Tokens: input={input_tokens}, output={output_tokens}, "
                f"cache_read={cache_read}, cache_creation={cache_creation}"
            )
            print(
                f"      📊 Tokens: in={input_tokens}, out={output_tokens}, total={total_tokens}"
                f"{f', cache_read={cache_read}' if cache_read > 0 else ''}"
                f"{f', cache_write={cache_creation}' if cache_creation > 0 else ''}",
                flush=True,
            )
            
            # Sayfa token bilgilerini aggregate'e ekle
            self._agg_claude_input_tokens += input_tokens
            self._agg_claude_output_tokens += output_tokens
            self._agg_claude_cache_read += cache_read
            self._agg_claude_cache_creation += cache_creation

            # Response parse
            response_text = ""
            if hasattr(response, "content"):
                if isinstance(response.content, str):
                    response_text = response.content
                elif isinstance(response.content, list):
                    for block in response.content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            response_text = block.get("text", "")
                            break

            # JSON parse
            parsed = self._parse_json_response(response_text)

            return {
                "data": parsed.get("data", {}),
                "continuation_context": parsed.get("continuation_context", ""),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read": cache_read,
                "cache_creation": cache_creation,
            }

        except Exception as e:
            logger.error(f"❌ Page {page_num} extraction failed: {e}")
            return {
                "data": {"found": False, "error": str(e)},
                "continuation_context": "",
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read": 0,
                "cache_creation": 0,
            }

    def _build_page_extraction_prompt(
        self,
        page_text: str,
        target_company: str,
        page_num: int,
        total_pages: int,
        continuation_context: str,
        graph_schema: str,
    ) -> str:
        """Sayfa bazlı extraction prompt'u oluştur."""

        context_section = ""
        if continuation_context:
            context_section = f"""
## ÖNCEKİ SAYFADAN DEVAM

{continuation_context}

---
"""

        return f"""## HEDEF ŞİRKET: {target_company}

## SAYFA: {page_num}/{total_pages}
{context_section}
## GRAF ŞEMASI

{graph_schema}

## OCR METNİ (Sayfa {page_num})

```
{page_text}
```

## GÖREV

1. Bu sayfada "{target_company}" şirketine ait içerik var mı kontrol et
2. Varsa, chunk'lara böl ve entity/relationship çıkar
3. İlan devam ediyorsa continuation_context oluştur

## JSON ÇIKTI FORMATI

```json
{{
  "found": true/false,
  "target_company": "{target_company}",
  "document_type": "string (varsa)",
  "chunks": [
    {{
      "id": "chunk_p{page_num:02d}_001",
      "text": "...",
      "position": 1,
      "page": {page_num}
    }}
  ],
  "nodes": [
    {{
      "label": "Company|Person|...",
      "id": "entity_id",
      "properties": {{}},
      "chunk_ids": ["chunk_p{page_num:02d}_001"]
    }}
  ],
  "relationships": [
    {{
      "from_id": "...",
      "to_id": "...",
      "type": "RELATIONSHIP_TYPE",
      "properties": {{}}
    }}
  ],
  "continuation_context": "İlan devam ediyorsa özet (sonraki sayfa için)"
}}
```

## KURALLAR

- Hedef şirket bu sayfada yoksa: `{{"found": false}}`
- Chunk ID'leri: `chunk_p{page_num:02d}_NNN` formatında
- İlan yarıda kaldıysa continuation_context doldur
- Entity ID'leri şemadaki pattern'e uygun olsun
"""

    # ========================================================================
    # MERGE LOGIC
    # ========================================================================

    def _merge_pages_data(
        self,
        pages_data: List[Dict[str, Any]],
        target_company: str,
    ) -> Dict[str, Any]:
        """
        Tüm sayfa verilerini birleştir.

        - Chunk'ları sıralı birleştir
        - Node'ları ID'ye göre merge (deduplicate)
        - Relationship'leri birleştir
        """
        merged = {
            "found": True,
            "target_company": target_company,
            "document_type": "",
            "chunks": [],
            "nodes": [],
            "relationships": [],
        }

        # Document type: ilk bulunan
        for page_data in pages_data:
            if page_data.get("document_type"):
                merged["document_type"] = page_data["document_type"]
                break

        # Chunk'ları birleştir (position güncelle)
        chunk_position = 0
        for page_data in pages_data:
            for chunk in page_data.get("chunks", []):
                chunk_position += 1
                new_chunk = {
                    "id": chunk.get("id", f"chunk_{chunk_position:03d}"),
                    "text": chunk.get("text", ""),
                    "position": chunk_position,
                    "page": chunk.get("page", 1),
                }
                merged["chunks"].append(new_chunk)

        # Node'ları birleştir (ID bazlı dedup)
        nodes_by_id: Dict[str, Dict[str, Any]] = {}
        for page_data in pages_data:
            for node in page_data.get("nodes", []):
                node_id = node.get("id", "")
                if node_id in nodes_by_id:
                    # Mevcut node'a chunk_ids ekle
                    existing = nodes_by_id[node_id]
                    existing_chunks = set(existing.get("chunk_ids", []))
                    existing_chunks.update(node.get("chunk_ids", []))
                    existing["chunk_ids"] = list(existing_chunks)
                else:
                    nodes_by_id[node_id] = node.copy()
        merged["nodes"] = list(nodes_by_id.values())

        # Relationship'leri birleştir (dedup)
        rels_seen: set = set()
        for page_data in pages_data:
            for rel in page_data.get("relationships", []):
                rel_key = (rel.get("from_id"), rel.get("to_id"), rel.get("type"))
                if rel_key not in rels_seen:
                    rels_seen.add(rel_key)
                    merged["relationships"].append(rel)

        return merged

    # ========================================================================
    # MAIN PROCESS
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
        Hybrid A2A OCR pipeline'ı çalıştır.

        1. Gemini: Paralel OCR → dosyalara kaydet
        2. Claude: Sequential extraction → sayfa sayfa JSON
        3. Merge: Tüm sayfaları birleştir
        """
        _ = file_id, domain  # API uyumluluğu
        self._graph = graph

        if not self._initialized:
            await self.initialize()

        # Token sayaçlarını sıfırla
        self._reset_token_counters()

        target_company = self._extract_company_from_filename(file_name)

        # Output dizini
        if output_dir:
            self._output_dir = output_dir
        else:
            self._output_dir = os.path.join(
                os.path.dirname(image_list[0]) if image_list else ".",
                "..",
                "hybrid_a2a_output",
            )
        os.makedirs(self._output_dir, exist_ok=True)

        logger.info(f"🚀 Starting Hybrid A2A OCR: {file_name}, target={target_company}")
        print(
            f"\n{'='*70}\n"
            f"🚀 HYBRID A2A OCR START\n"
            f"   Stage 1: Gemini Parallel OCR\n"
            f"   Stage 2: Claude Sequential Extraction\n"
            f"   Target: {target_company}\n"
            f"   Pages: {len(image_list)}\n"
            f"   Output: {self._output_dir}\n"
            f"{'='*70}",
            flush=True,
        )

        total_start = time.time()

        try:
            # ================================================================
            # STAGE 1: Parallel Gemini OCR
            # ================================================================
            stage1_result = await self._stage1_parallel_ocr(
                image_list=image_list,
                file_name=file_name,
            )

            ocr_files = stage1_result["ocr_files"]

            if not ocr_files:
                return {
                    "data": {"found": False, "reason": "OCR başarısız"},
                    "metadata": {"target_company": target_company},
                    "status": "error",
                }

            # ================================================================
            # STAGE 2: Sequential Claude Extraction
            # ================================================================
            stage2_result = await self._stage2_sequential_extract(
                ocr_files=ocr_files,
                target_company=target_company,
                file_name=file_name,
            )

            pages_data = stage2_result["pages_data"]

            # ================================================================
            # STAGE 3: Merge
            # ================================================================
            if pages_data:
                merged_data = self._merge_pages_data(pages_data, target_company)
            else:
                merged_data = {"found": False, "target_company": target_company}

            total_duration = int((time.time() - total_start) * 1000)

            # ================================================================
            # TOKEN & COST CALCULATION
            # ================================================================
            
            # Gemini (Stage 1) token toplamları
            gemini_input = stage1_result.get("gemini_input_tokens", 0)
            gemini_output = stage1_result.get("gemini_output_tokens", 0)
            gemini_total = gemini_input + gemini_output
            
            # Claude (Stage 2) token toplamları
            claude_input = self._agg_claude_input_tokens
            claude_output = self._agg_claude_output_tokens
            claude_cache_read = self._agg_claude_cache_read
            claude_cache_creation = self._agg_claude_cache_creation
            claude_total = claude_input + claude_output
            
            # Grand total
            grand_input = gemini_input + claude_input
            grand_output = gemini_output + claude_output
            grand_total = grand_input + grand_output
            
            # Gemini maliyet hesaplama
            gemini_pricing = self._get_model_pricing(GEMINI_OCR_MODEL)
            gemini_cost_input = gemini_input * gemini_pricing["input"] / 1_000_000
            gemini_cost_output = gemini_output * gemini_pricing["output"] / 1_000_000
            gemini_cost_total = gemini_cost_input + gemini_cost_output
            
            # Claude maliyet hesaplama
            claude_pricing = self._get_model_pricing(CLAUDE_EXTRACT_MODEL)
            uncached_claude_input = max(0, claude_input - claude_cache_read - claude_cache_creation)
            claude_cost_input = uncached_claude_input * claude_pricing["input"] / 1_000_000
            claude_cost_output = claude_output * claude_pricing["output"] / 1_000_000
            claude_cost_cache_read = claude_cache_read * claude_pricing["cache_read"] / 1_000_000
            claude_cost_cache_write = claude_cache_creation * claude_pricing["cache_write"] / 1_000_000
            claude_cost_total = claude_cost_input + claude_cost_output + claude_cost_cache_read + claude_cost_cache_write
            
            # Grand total cost
            grand_cost = gemini_cost_total + claude_cost_total

            # Output
            output = {
                "data": merged_data,
                "metadata": {
                    "target_company": target_company,
                    "pages_processed": len(image_list),
                    "stage1_duration_ms": stage1_result["duration_ms"],
                    "stage1_chars": stage1_result["total_chars"],
                    "stage2_duration_ms": stage2_result["duration_ms"],
                    "total_duration_ms": total_duration,
                    "model_ocr": GEMINI_OCR_MODEL,
                    "model_extract": CLAUDE_EXTRACT_MODEL,
                    "pipeline": "hybrid_a2a",
                    # Token usage
                    "token_usage": {
                        "gemini": {
                            "input_tokens": gemini_input,
                            "output_tokens": gemini_output,
                            "total_tokens": gemini_total,
                            "cost_usd": gemini_cost_total,
                        },
                        "claude": {
                            "input_tokens": claude_input,
                            "output_tokens": claude_output,
                            "total_tokens": claude_total,
                            "cache_read": claude_cache_read,
                            "cache_creation": claude_cache_creation,
                            "cost_usd": claude_cost_total,
                        },
                        "total": {
                            "input_tokens": grand_input,
                            "output_tokens": grand_output,
                            "total_tokens": grand_total,
                            "cost_usd": grand_cost,
                        },
                    },
                },
                "status": "success",
            }

            # Final JSON kaydet
            final_json = os.path.join(self._output_dir, f"{Path(file_name).stem}_final.json")
            with open(final_json, "w", encoding="utf-8") as f:
                json.dump(output, f, ensure_ascii=False, indent=2)

            # Summary
            chunks = len(merged_data.get("chunks", []))
            nodes = len(merged_data.get("nodes", []))
            rels = len(merged_data.get("relationships", []))

            # ================================================================
            # FINAL COST & TOKEN SUMMARY LOG
            # ================================================================
            print(
                f"\n{'='*70}\n"
                f"✅ HYBRID A2A OCR COMPLETE\n"
                f"   Stage 1 (Parallel OCR): {stage1_result['total_chars']} chars, {stage1_result['duration_ms']}ms\n"
                f"   Stage 2 (Sequential Extract): {len(pages_data)} pages, {stage2_result['duration_ms']}ms\n"
                f"   Output: {chunks} chunks, {nodes} nodes, {rels} relationships\n"
                f"   Total Duration: {total_duration}ms\n"
                f"   Saved: {final_json}\n"
                f"{'='*70}\n"
                f"\n📊 GEMINI TOKEN USAGE (Stage 1 - {GEMINI_OCR_MODEL})\n"
                f"   Input Tokens:  {gemini_input:,}\n"
                f"   Output Tokens: {gemini_output:,}\n"
                f"   Total Tokens:  {gemini_total:,}\n"
                f"   Cost:          ${gemini_cost_total:.4f}\n"
                f"\n📊 CLAUDE TOKEN USAGE (Stage 2 - {CLAUDE_EXTRACT_MODEL})\n"
                f"   Input Tokens:  {claude_input:,}\n"
                f"   Output Tokens: {claude_output:,}\n"
                f"   Total Tokens:  {claude_total:,}\n"
                f"   Cache Read:    {claude_cache_read:,}\n"
                f"   Cache Write:   {claude_cache_creation:,}\n"
                f"   Cost:          ${claude_cost_total:.4f}\n"
                f"\n💰 GRAND TOTAL\n"
                f"   Total Input:   {grand_input:,}\n"
                f"   Total Output:  {grand_output:,}\n"
                f"   Total Tokens:  {grand_total:,}\n"
                f"   TOTAL COST:    ${grand_cost:.4f}\n"
                f"{'='*70}\n",
                flush=True,
            )
            
            # Logger'a da yaz
            logger.info(
                f"💰 HYBRID A2A COST SUMMARY: "
                f"Gemini({gemini_total} tok, ${gemini_cost_total:.4f}) + "
                f"Claude({claude_total} tok, ${claude_cost_total:.4f}) = "
                f"TOTAL({grand_total} tok, ${grand_cost:.4f})"
            )

            return output

        except Exception as e:
            logger.error(f"❌ Hybrid A2A OCR failed: {e}", exc_info=True)
            return {
                "data": {},
                "metadata": {"target_company": target_company},
                "status": "error",
                "error": str(e),
            }

    # ========================================================================
    # HELPER METHODS
    # ========================================================================

    def _get_system_prompt(self) -> str:
        """System prompt yükle."""
        from prompts import get_domain, load_prompt

        domain = get_domain()
        try:
            return load_prompt("unified_ocr_system", domain)
        except FileNotFoundError:
            return """Sen Türkiye Ticaret Sicil Gazetesi (TSG) uzmanısın.
Görevin OCR metinden hedef şirketin bilgilerini çıkarmak ve JSON formatında döndürmek."""

    def _get_graph_schema(self) -> str:
        """Neo4j şemasını çek."""
        if self._graph is None:
            return "*Graf şeması mevcut değil*"
        try:
            from src.neo4j_schema_provider import get_schema_for_ocr
            return get_schema_for_ocr(self._graph, max_examples=5)
        except Exception as e:
            logger.warning(f"Schema failed: {e}")
            return "*Graf şeması alınamadı*"

    def _parse_json_response(self, response_text: str) -> Dict[str, Any]:
        """JSON parse et."""
        text = response_text.strip()
        if not text or text.upper() in ("YOK", "N/A"):
            return {"data": {"found": False}, "continuation_context": ""}

        # JSON block çıkar
        json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        json_text = json_match.group(1).strip() if json_match else text

        try:
            raw_data = json.loads(json_text)
            continuation = raw_data.pop("continuation_context", "")
            return {"data": raw_data, "continuation_context": continuation}
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error: {e}")
            return {"data": {"found": False, "_parse_error": str(e)}, "continuation_context": ""}

    def _extract_company_from_filename(self, file_name: str) -> str:
        """Dosya adından şirket adı çıkar."""
        match = re.match(r"^([A-Za-zÇçĞğİıÖöŞşÜü\s]+)", file_name)
        if match:
            return match.group(1).strip()
        parts = file_name.split("-")
        return parts[0].strip() if parts else file_name

    async def close(self) -> None:
        """Cleanup."""
        self._gemini_client = None
        self._claude_model = None
        self._initialized = False


# ============================================================================
# SINGLETON & SYNC WRAPPER
# ============================================================================

_hybrid_ocr_instance: Optional[HybridA2AOCR] = None


async def get_hybrid_a2a_ocr() -> HybridA2AOCR:
    """Singleton instance al."""
    global _hybrid_ocr_instance
    if _hybrid_ocr_instance is None:
        _hybrid_ocr_instance = HybridA2AOCR()
        await _hybrid_ocr_instance.initialize()
    return _hybrid_ocr_instance


def process_hybrid_a2a_ocr(
    image_list: List[str],
    file_name: str,
    file_id: Optional[int] = None,
    domain: Optional[str] = None,
    graph: Optional[Neo4jGraph] = None,
    output_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Sync wrapper for Celery tasks.
    """
    print(f"[HYBRID_A2A_OCR] Called with {len(image_list)} images", flush=True)

    async def _run():
        ocr = await get_hybrid_a2a_ocr()
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
