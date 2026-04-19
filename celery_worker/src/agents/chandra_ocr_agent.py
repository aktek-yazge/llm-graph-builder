# -*- coding: utf-8 -*-
"""
Chandra OCR 2 Agent — Ollama Backend

Lokal Chandra OCR 2 modelini Ollama uzerinden calistiran agent.
GGUF quantized model (Q4_K_M ~3.1GB) ile Mac'te hizli calisir.
GeminiOCRAgent arayuzune tam uyumlu.

Gereksinimler:
    - Ollama kurulu ve calisiyor: ollama serve
    - Model cekilmis: ollama pull ahmgam/chandra-ocr-2:Q4_K_M

Env degiskenleri:
    CHANDRA_OLLAMA_MODEL   = ahmgam/chandra-ocr-2:Q4_K_M
    CHANDRA_OLLAMA_URL     = http://localhost:11434
    CHANDRA_MAX_CONCURRENT = 1

Usage:
    from src.agents.chandra_ocr_agent import ChandraOCRAgent

    agent = ChandraOCRAgent()
    await agent.initialize()

    result = await agent.process(
        image_list=["/path/to/page1.png", "/path/to/page2.png"],
        output_dir="/path/to/output",
    )
"""

import os
import time
import base64
import logging
import asyncio
from typing import Dict, Any, List, Optional

import httpx

logger = logging.getLogger(__name__)

OLLAMA_MODEL = os.getenv("CHANDRA_OLLAMA_MODEL", "fredrezones55/chandra-ocr-2:patch")
OLLAMA_URL = os.getenv("CHANDRA_OLLAMA_URL", "http://localhost:11434")
CHANDRA_MAX_CONCURRENT = int(os.getenv("CHANDRA_MAX_CONCURRENT", "1"))
CHANDRA_TIMEOUT = float(os.getenv("CHANDRA_TIMEOUT", "600"))


class ChandraOCRAgent:
    """
    Lokal Chandra OCR 2 Agent via Ollama.

    GeminiOCRAgent ile ayni process() arayuzunu kullanir.
    Her sayfa Ollama /api/chat endpoint'ine gorsel olarak gonderilir.
    """

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None
        self._initialized = False
        self._total_input_tokens = 0
        self._total_output_tokens = 0

    async def initialize(self) -> None:
        if self._initialized:
            return

        self._client = httpx.AsyncClient(
            base_url=OLLAMA_URL,
            timeout=httpx.Timeout(CHANDRA_TIMEOUT, connect=10.0),
        )

        try:
            resp = await self._client.get("/api/tags")
            resp.raise_for_status()
            models = [m.get("name", "") for m in resp.json().get("models", [])]
            if not any(OLLAMA_MODEL in m for m in models):
                available = ", ".join(models) or "(none)"
                raise RuntimeError(
                    f"Model '{OLLAMA_MODEL}' Ollama'da bulunamadi. "
                    f"Mevcut: {available}. "
                    f"Cek: ollama pull {OLLAMA_MODEL}"
                )
        except httpx.ConnectError:
            raise RuntimeError(
                f"Ollama'ya baglanilamadi ({OLLAMA_URL}). "
                "Baslatmak icin: ollama serve"
            )

        self._initialized = True
        logger.info("ChandraOCRAgent initialized (ollama model=%s)", OLLAMA_MODEL)
        print(f"   ChandraOCRAgent initialized: {OLLAMA_MODEL} via Ollama", flush=True)

    def _reset_counters(self) -> None:
        self._total_input_tokens = 0
        self._total_output_tokens = 0

    async def process(
        self,
        image_list: List[str],
        output_dir: str,
        file_name: Optional[str] = None,
        max_concurrent: int = CHANDRA_MAX_CONCURRENT,
    ) -> Dict[str, Any]:
        """
        Sayfa gorsellerini Chandra OCR 2 (Ollama) ile isler.

        Donus yapisi GeminiOCRAgent ile ayni:
            ocr_files, ocr_texts, merged_text, total_chars,
            page_count, duration_ms, errors, token_usage.
        """
        if not self._initialized:
            await self.initialize()

        self._reset_counters()

        logger.info("ChandraOCRAgent: Starting OCR for %d pages", len(image_list))
        print(
            f"\n{'='*60}\n"
            f"CHANDRA OCR AGENT START (Ollama)\n"
            f"   Model: {OLLAMA_MODEL}\n"
            f"   Pages: {len(image_list)}\n"
            f"   Max Concurrent: {max_concurrent}\n"
            f"   Output: {output_dir}\n"
            f"{'='*60}",
            flush=True,
        )

        start_time = time.time()
        sorted_images = sorted(image_list)

        ocr_output_dir = os.path.join(output_dir, "ocr_pages")
        os.makedirs(ocr_output_dir, exist_ok=True)

        semaphore = asyncio.Semaphore(max_concurrent)

        async def ocr_with_semaphore(path: str, page_num: int) -> Dict[str, Any]:
            async with semaphore:
                return await self._ocr_single_page(path, page_num, ocr_output_dir)

        tasks = [
            ocr_with_semaphore(path, idx + 1)
            for idx, path in enumerate(sorted_images)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        ocr_files: List[str] = []
        ocr_texts: List[str] = []
        total_chars = 0
        errors: List[Dict[str, Any]] = []

        for idx, result in enumerate(results):
            page_num = idx + 1
            if isinstance(result, Exception):
                logger.error("Page %d failed: %s", page_num, result)
                errors.append({"page": page_num, "error": str(result)})
                ocr_texts.append("")
            else:
                ocr_files.append(result["ocr_file"])
                ocr_texts.append(result["ocr_text"])
                total_chars += result["char_count"]
                self._total_input_tokens += result.get("input_tokens", 0)
                self._total_output_tokens += result.get("output_tokens", 0)
                print(
                    f"   Page {page_num}: {result['char_count']} chars, "
                    f"{result['duration_ms']}ms",
                    flush=True,
                )

        duration_ms = int((time.time() - start_time) * 1000)
        merged_text = self._merge_texts(ocr_texts)

        logger.info(
            "ChandraOCRAgent complete: %d chars, %dms",
            total_chars, duration_ms,
        )
        print(
            f"\n{'='*60}\n"
            f"CHANDRA OCR AGENT COMPLETE\n"
            f"   Pages: {len(sorted_images)}\n"
            f"   Total Chars: {total_chars:,}\n"
            f"   Cost: $0.00 (local)\n"
            f"   Duration: {duration_ms}ms\n"
            f"{'='*60}\n",
            flush=True,
        )

        return {
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
                "total_tokens": self._total_input_tokens + self._total_output_tokens,
                "cost_usd": 0.0,
            },
        }

    async def _ocr_single_page(
        self,
        image_path: str,
        page_num: int,
        output_dir: str,
    ) -> Dict[str, Any]:
        """Tek sayfa OCR — Ollama /api/chat streaming ile."""
        start_time = time.time()

        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Image not found: {image_path}")

        with open(image_path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode("utf-8")

        payload = {
            "model": OLLAMA_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": "OCR this document page. Return the full text in markdown format preserving layout, tables, and structure. Do not add commentary.",
                    "images": [image_b64],
                }
            ],
            "stream": True,
            "options": {
                "temperature": 0.0,
                "num_predict": 16384,
            },
        }

        import json as _json

        chunks: List[str] = []
        input_tokens = 0
        output_tokens = 0

        async with self._client.stream("POST", "/api/chat", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                try:
                    data = _json.loads(line)
                except _json.JSONDecodeError:
                    continue

                msg = data.get("message", {})
                content = msg.get("content", "")
                if content:
                    chunks.append(content)

                if data.get("done"):
                    input_tokens = data.get("prompt_eval_count", 0) or 0
                    output_tokens = data.get("eval_count", 0) or 0
                    break

        ocr_text = "".join(chunks).strip()
        duration_ms = int((time.time() - start_time) * 1000)

        ocr_file = os.path.join(output_dir, f"page_{page_num:03d}_ocr.md")
        with open(ocr_file, "w", encoding="utf-8") as f:
            f.write(f"[[PAGE:{page_num}]]\n\n")
            f.write(ocr_text)

        return {
            "ocr_file": ocr_file,
            "ocr_text": ocr_text,
            "char_count": len(ocr_text),
            "duration_ms": duration_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }

    def _merge_texts(self, ocr_texts: List[str], start_page: int = 1) -> str:
        parts = []
        for i, text in enumerate(ocr_texts):
            page_num = start_page + i
            if text:
                parts.append(f"[[PAGE:{page_num}]]\n{text}\n")
        return "\n".join(parts)

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        self._initialized = False


# ============================================================================
# SINGLETON & SYNC WRAPPER
# ============================================================================

_chandra_ocr_instance: Optional[ChandraOCRAgent] = None


async def get_chandra_ocr_agent() -> ChandraOCRAgent:
    global _chandra_ocr_instance
    if _chandra_ocr_instance is None:
        _chandra_ocr_instance = ChandraOCRAgent()
        await _chandra_ocr_instance.initialize()
    return _chandra_ocr_instance


def process_chandra_ocr(
    image_list: List[str],
    output_dir: str,
    file_name: Optional[str] = None,
    max_concurrent: int = CHANDRA_MAX_CONCURRENT,
) -> Dict[str, Any]:
    """Sync wrapper for Celery tasks."""
    print(f"[CHANDRA_OCR_AGENT] Called with {len(image_list)} images", flush=True)

    async def _run():
        agent = await get_chandra_ocr_agent()
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
