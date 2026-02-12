# -*- coding: utf-8 -*-
"""
Unified Vision OCR & Entity Extraction

Tüm sayfayı vision modele göndererek hedef şirketin içeriğini çıkaran
ve aynı anda entity/relationship extraction yapan modül.

Tek bir vision model çağrısı ile:
- Sayfa yapısını anlar
- Hedef şirketin içeriğini çıkarır
- Semantic chunk'lara böler
- Entity ve relationship'leri çıkarır
- JSON formatında yapılandırılmış veri döndürür

LangChain ChatAnthropic + Prompt Caching + Langfuse entegrasyonu.

Usage:
    from src.agentic_ocr import AgenticOCR

    ocr = AgenticOCR()
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
import base64
import logging
import asyncio
from typing import Dict, Any, List, Optional, TYPE_CHECKING
from datetime import datetime

from langchain_neo4j import Neo4jGraph
from pydantic import SecretStr

logger = logging.getLogger(__name__)

print(f"[AGENTIC_OCR_MODULE] Loaded at {datetime.now()}", flush=True)

# ============================================================================
# LANGCHAIN IMPORTS
# ============================================================================

if TYPE_CHECKING:
    from langchain_anthropic import ChatAnthropic

# LangChain Anthropic import
try:
    from langchain_anthropic import ChatAnthropic

    LANGCHAIN_ANTHROPIC_AVAILABLE = True
    logger.info("✅ LangChain ChatAnthropic imported")
except ImportError as e:
    logger.warning(f"⚠️ LangChain Anthropic not available: {e}")
    LANGCHAIN_ANTHROPIC_AVAILABLE = False
    ChatAnthropic = None  # type: ignore

# Langfuse LLM Observability
try:
    from src.shared.langfuse_client import (
        get_langfuse,
        get_langfuse_callback_handler,
        flush_langfuse,
        log_llm_usage,
    )
    LANGFUSE_AVAILABLE = True
    logger.info("✅ Langfuse client imported")
except ImportError as e:
    logger.warning(f"⚠️ Langfuse not available: {e}")
    LANGFUSE_AVAILABLE = False
    get_langfuse = None  # type: ignore
    get_langfuse_callback_handler = None  # type: ignore
    flush_langfuse = None  # type: ignore
    log_llm_usage = None  # type: ignore


class AgenticOCR:
    """
    Full-Page Vision OCR with LangChain ChatAnthropic.

    Her sayfayı doğrudan vision modeline gönderir.
    Model sayfanın multi-column yapısını anlar ve sadece hedef şirketin
    içeriğini markdown veya JSON olarak döndürür.
    
    LangChain ChatAnthropic + Prompt Caching + Langfuse entegrasyonu.
    """

    def __init__(self):
        self._gemini_client = None
        self._claude_model: Optional[ChatAnthropic] = None
        self._initialized = False
        self._model_provider = None  # "gemini" or "anthropic"
        self._model_name = None

    async def initialize(self) -> None:
        """Vision client'ı başlat (model tipine göre)."""
        if self._initialized:
            return

        model_name = os.environ.get("OCR_VISION_MODEL", "gemini-2.5-pro")
        self._model_name = model_name
        
        if model_name.startswith("claude"):
            # LangChain ChatAnthropic
            if not LANGCHAIN_ANTHROPIC_AVAILABLE or ChatAnthropic is None:
                raise ImportError(
                    "langchain-anthropic paketi kurulu değil. `uv add langchain-anthropic` ile kurun."
                )
            
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
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
            actual_model = model_mapping.get(model_name, model_name)
            
            # Extended thinking budget
            thinking_budget = int(os.environ.get("OCR_THINKING_BUDGET", "10000"))
            
            # Model parametreleri
            model_kwargs: Dict[str, Any] = {
                "model": actual_model,
                "api_key": SecretStr(api_key),
                "max_tokens": 16384,
            }
            
            # Extended thinking (budget > 0 ise)
            if thinking_budget > 0:
                model_kwargs["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": thinking_budget,
                }
                logger.info(f"🤖 Vision OCR: {actual_model} (extended_thinking={thinking_budget} tokens)")
            else:
                logger.info(f"🤖 Vision OCR: {actual_model} (thinking=disabled)")
            
            self._claude_model = ChatAnthropic(**model_kwargs)
            self._model_provider = "anthropic"
            
            print(f"   ✅ LangChain ChatAnthropic initialized: {actual_model}", flush=True)
        else:
            # Google (Gemini) client
            from google import genai
            api_key = os.environ.get("GEMINI_API_KEY")
            if not api_key:
                raise ValueError("GEMINI_API_KEY environment variable not set")
            self._gemini_client = genai.Client(api_key=api_key)
            self._model_provider = "gemini"
            logger.info(f"🤖 Vision OCR initialized: {model_name} (Gemini)")
        
        self._initialized = True

    async def process(
        self,
        image_list: List[str],
        file_name: str,
        file_id: Optional[int] = None,
        domain: Optional[str] = None,
        graph: Optional[Neo4jGraph] = None,
        output_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Sayfaları tek tek işle.

        Args:
            image_list: İşlenecek image path'leri
            file_name: Dosya adı (prompt'ta şirket adı çıkarılır)
            file_id: Tracking için
            domain: Domain (opsiyonel)
            graph: Neo4j graph bağlantısı (şema çekmek için)
            output_dir: JSON çıktısı için dizin (opsiyonel)

        Returns:
            {"markdown": "...", "metadata": {...}, "status": "success"|"error"}
        """
        self._graph = graph  # Şema çekmek için sakla
        if not self._initialized:
            await self.initialize()

        from prompts import get_domain
        from src.shared.langfuse_client import get_langfuse, flush_langfuse

        domain = domain or get_domain()
        target_company = self._extract_company_from_filename(file_name)
        model_name = os.environ.get("OCR_VISION_MODEL", "gemini-2.5-pro")

        logger.info(
            f"🚀 Starting Vision OCR: model={model_name}, file={file_name}, "
            f"target={target_company}, pages={len(image_list)}"
        )
        print(
            f"\n{'='*60}\n"
            f"🚀 VISION OCR START\n"
            f"   Model: {model_name}\n"
            f"   Target: {target_company}\n"
            f"   Pages: {len(image_list)}\n"
            f"{'='*60}",
            flush=True,
        )

        # Langfuse trace
        langfuse = get_langfuse()
        trace = None
        if langfuse:
            try:
                trace = langfuse.start_span(
                    name="vision_ocr",
                    input={
                        "file_name": file_name,
                        "page_count": len(image_list),
                        "target_company": target_company,
                    },
                    metadata={
                        "domain": domain,
                        "file_id": file_id,
                        "model": model_name,
                    },
                )
            except Exception as e:
                logger.warning(f"Langfuse trace failed: {e}")

        try:
            # Claude için JSON data, Gemini için markdown
            all_pages_data: List[Dict[str, Any]] = []
            all_markdown: List[str] = []
            continuation_note = ""
            pages_processed = 0
            target_found = False

            sorted_images = sorted(image_list)

            for page_idx, page_path in enumerate(sorted_images):
                page_path = os.path.abspath(page_path)
                page_num = page_idx + 1

                logger.info(f"📄 Page {page_num}/{len(sorted_images)}: {page_path}")
                print(
                    f"\n📄 Processing page {page_num}/{len(sorted_images)}...",
                    flush=True,
                )

                start_time = time.time()

                result = await self._process_page(
                    page_path=page_path,
                    target_company=target_company,
                    continuation_note=continuation_note,
                    page_number=page_num,
                    total_pages=len(sorted_images),
                    model_name=model_name,
                    file_name=file_name,
                )

                duration = time.time() - start_time
                pages_processed = page_num
                continuation_note = result.get("continuation_note", "")
                is_complete = result.get("is_complete", False)

                # Claude (JSON) vs Gemini (markdown) output handling
                if "data" in result:
                    # JSON response (Claude)
                    page_data = result.get("data", {})
                    if page_data.get("found", False):
                        target_found = True
                        all_pages_data.append(page_data)
                    
                    chunks_count = len(page_data.get("chunks", []))
                    nodes_count = len(page_data.get("nodes", []))
                    
                    print(
                        f"   ✅ Page {page_num}: {chunks_count} chunks, {nodes_count} nodes, {duration:.1f}s"
                        f"{f', devam: {continuation_note[:50]}...' if continuation_note else ''}"
                        f"{' [COMPLETE]' if is_complete else ''}",
                        flush=True,
                    )
                else:
                    # Markdown response (Gemini)
                    page_md = result.get("markdown", "")
                    if page_md:
                        all_markdown.append(page_md)
                    
                    print(
                        f"   ✅ Page {page_num}: {len(page_md)} chars, {duration:.1f}s"
                        f"{f', devam: {continuation_note[:50]}...' if continuation_note else ''}"
                        f"{' [COMPLETE]' if is_complete else ''}",
                        flush=True,
                    )

                if is_complete:
                    logger.info(f"✅ Content complete at page {page_num}")
                    break

            # Çıktıyı oluştur
            if all_pages_data:
                # Claude JSON output - sayfaları birleştir
                merged_data = self._merge_pages_data(all_pages_data, target_company)
                
                output = {
                    "data": merged_data,
                    "metadata": {
                        "target_company": target_company,
                        "pages_processed": pages_processed,
                        "total_pages": len(sorted_images),
                        "model": model_name,
                        "output_format": "json",
                    },
                    "status": "success",
                }
                
                # JSON çıktısını dosyaya kaydet
                if output_dir:
                    self._save_json_output(output, file_name, output_dir)
            else:
                # Gemini markdown output (legacy)
                final_markdown = "\n\n".join(all_markdown)
                
                output = {
                    "markdown": final_markdown,
                    "metadata": {
                        "target_company": target_company,
                        "pages_processed": pages_processed,
                        "total_pages": len(sorted_images),
                        "model": model_name,
                        "output_format": "markdown",
                    },
                    "status": "success",
                }

            if trace:
                try:
                    trace.update(
                        output=output,
                        metadata={
                            "status": "success",
                            "pages_processed": pages_processed,
                        },
                    )
                    trace.end()
                    flush_langfuse()
                except Exception:
                    pass

            # Output summary
            if all_pages_data:
                # JSON output summary
                total_chunks = sum(len(d.get("chunks", [])) for d in all_pages_data)
                total_nodes = sum(len(d.get("nodes", [])) for d in all_pages_data)
                total_rels = sum(len(d.get("relationships", [])) for d in all_pages_data)
                print(
                    f"\n{'='*60}\n"
                    f"✅ VISION OCR COMPLETE (JSON)\n"
                    f"   Chunks: {total_chunks}, Nodes: {total_nodes}, Relationships: {total_rels}\n"
                    f"   Pages: {pages_processed}\n"
                    f"{'='*60}\n",
                    flush=True,
                )
                logger.info(
                    f"✅ Vision OCR completed: {total_chunks} chunks, {total_nodes} nodes, "
                    f"{total_rels} relationships, {pages_processed} pages"
                )
            else:
                # Markdown output summary
                final_markdown = "\n\n".join(all_markdown) if all_markdown else ""
                print(
                    f"\n{'='*60}\n"
                    f"✅ VISION OCR COMPLETE (Markdown)\n"
                    f"   Total: {len(final_markdown)} chars, {pages_processed} pages\n"
                    f"{'='*60}\n",
                    flush=True,
                )
                logger.info(
                    f"✅ Vision OCR completed: {len(final_markdown)} chars, {pages_processed} pages"
                )
            
            return output

        except Exception as e:
            logger.error(f"❌ Vision OCR failed: {e}", exc_info=True)
            if trace:
                try:
                    trace.update(level="ERROR", status_message=str(e))
                    trace.end()
                    flush_langfuse()
                except Exception:
                    pass
            return {"markdown": "", "metadata": {}, "status": "error", "error": str(e)}

    async def _process_page(
        self,
        page_path: str,
        target_company: str,
        continuation_note: str,
        page_number: int,
        total_pages: int,
        model_name: str,
        file_name: str = "unknown",
    ) -> Dict[str, Any]:
        """
        Tek sayfayı vision model ile işle (Gemini veya Claude).

        Returns:
            {"markdown": "...", "continuation_note": "...", "is_complete": bool}
        """
        # Görüntüyü oku
        with open(page_path, "rb") as f:
            image_data = f.read()

        mime_type = "image/png" if page_path.lower().endswith(".png") else "image/jpeg"

        # Prompt oluştur
        prompt = self._build_prompt(
            target_company=target_company,
            continuation_note=continuation_note,
            page_number=page_number,
            total_pages=total_pages,
        )

        # Domain prompt ekle
        domain_prompt = self._load_domain_prompt()
        if domain_prompt:
            prompt += f"\n{domain_prompt}\n"

        print(f"   🤖 Calling {model_name}...", flush=True)

        # Model tipine göre API çağrısı
        if self._model_provider == "anthropic":
            response_text = await self._call_claude(
                image_data, mime_type, prompt, model_name,
                page_num=page_number,
                file_name=file_name,
            )
        else:
            response_text = await self._call_gemini(image_data, mime_type, prompt, model_name)

        # Yanıtı parse et
        if not response_text:
            logger.warning(f"Empty response from {model_name}")
            return {"data": {"found": False}, "continuation_note": "", "is_complete": True}

        # Claude (Anthropic) için JSON parse, Gemini için markdown
        if self._model_provider == "anthropic":
            return self._parse_json_response(response_text)
        else:
            return self._parse_markdown_response(response_text)

    async def _call_gemini(
        self,
        image_data: bytes,
        mime_type: str,
        prompt: str,
        model_name: str,
    ) -> str:
        """Gemini API çağrısı."""
        from google.genai import types

        response = await asyncio.to_thread(
            self._gemini_client.models.generate_content,
            model=model_name,
            contents=[
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_bytes(data=image_data, mime_type=mime_type),
                        types.Part.from_text(text=prompt),
                    ],
                ),
            ],
            config=types.GenerateContentConfig(
                temperature=0.2,
                topK=1,
                seed=42,
            ),
        )

        if not response or not response.text:
            return ""
        return response.text

    async def _call_claude(
        self,
        image_data: bytes,
        mime_type: str,
        prompt: str,
        model_name: str,
        page_num: int = 1,
        file_name: str = "unknown",
    ) -> str:
        """
        Claude API çağrısı via LangChain ChatAnthropic.
        
        LangChain entegrasyonu sayesinde:
        - Otomatik prompt caching
        - Langfuse observability
        - Extended thinking desteği
        
        Args:
            image_data: Görsel verisi (bytes)
            mime_type: Görsel MIME tipi
            prompt: Dinamik prompt (şema, hedef şirket, sayfa bilgisi)
            model_name: Model adı
            page_num: Sayfa numarası (Langfuse trace için)
            file_name: Dosya adı (Langfuse trace için)
        """
        if not self._claude_model:
            raise RuntimeError("Claude model not initialized. Call initialize() first.")
        
        # Base64 encode image
        image_base64 = base64.b64encode(image_data).decode("utf-8")
        
        # System prompt'u yükle (sabit - cache için)
        system_prompt = self._get_system_prompt()
        
        logger.info(f"📦 System prompt: {len(system_prompt)} chars (will be cached)")
        print(f"   📦 System prompt: {len(system_prompt)} chars", flush=True)
        
        # LangChain message format with cache_control
        # https://docs.langchain.com/oss/python/integrations/chat/anthropic#prompt-caching
        #
        # SystemMessage ve HumanMessage kullanılmalı - LangChain'in _format_messages
        # fonksiyonu BaseMessage nesneleri bekliyor
        from langchain_core.messages import SystemMessage, HumanMessage
        
        # System message - content list olarak verilmeli ve cache_control eklenmeli
        # ttl: "5m" (5 dakika default) - AnthropicPromptCachingMiddleware ile aynı
        system_message = SystemMessage(
            content=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral", "ttl": "5m"},
                },
            ]
        )
        
        # User message - vision + dynamic prompt (cache'lenmez)
        user_message = HumanMessage(
            content=[
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mime_type,
                        "data": image_base64,
                    },
                },
                {
                    "type": "text",
                    "text": prompt,  # Şema + sayfa bilgisi + hedef şirket
                },
            ]
        )
        
        messages = [system_message, user_message]
        
        # Langfuse trace context (opsiyonel)
        trace_metadata = {
            "file_name": file_name,
            "page_num": page_num,
            "model": self._model_name,
        }
        
        start_time = time.time()
        
        # LangChain invoke
        try:
            # Langfuse callback handler (varsa)
            callbacks = []
            if LANGFUSE_AVAILABLE and get_langfuse_callback_handler:
                langfuse_handler = get_langfuse_callback_handler(
                    session_id=f"ocr-{file_name}",
                    trace_name=f"ocr-{file_name}-p{page_num}",
                    tags=["ocr", "vision", "claude"],
                    metadata=trace_metadata,
                )
                if langfuse_handler:
                    callbacks.append(langfuse_handler)
                    logger.debug(f"📊 Langfuse callback attached")
            
            # LangChain ainvoke (async) with prompt caching
            # cache_control system message içinde zaten var
            
            # DEBUG: Gerçek API payload'ını yakala
            # _get_request_payload ile son payload'ı görelim
            try:
                _dbg_payload = self._claude_model._get_request_payload(messages)
                _dbg_sys = _dbg_payload.get("system")
                _dbg_thinking = _dbg_payload.get("thinking")
                
                has_cache = False
                if isinstance(_dbg_sys, list):
                    for blk in _dbg_sys:
                        if isinstance(blk, dict) and "cache_control" in blk:
                            has_cache = True
                            logger.info(f"🔍 [CACHE DEBUG] cache_control value: {blk['cache_control']}")
                            break
                
                logger.info(
                    f"🔍 [CACHE DEBUG] system type={type(_dbg_sys).__name__}, "
                    f"has_cache_control={has_cache}, "
                    f"thinking={_dbg_thinking is not None}, "
                    f"payload_keys={list(_dbg_payload.keys())}"
                )
                print(
                    f"   🔍 Cache debug: system={type(_dbg_sys).__name__}, "
                    f"cache_ctrl={has_cache}, thinking={_dbg_thinking is not None}",
                    flush=True,
                )
                
                # System bloklarını detaylı logla
                if isinstance(_dbg_sys, list):
                    for i, blk in enumerate(_dbg_sys):
                        keys = list(blk.keys()) if isinstance(blk, dict) else "str"
                        logger.info(f"🔍 [CACHE DEBUG] system[{i}] keys={keys}")
                elif isinstance(_dbg_sys, str):
                    logger.info(f"🔍 [CACHE DEBUG] system is plain str! ({len(_dbg_sys)} chars) - CACHE WILL NOT WORK")
                    print(f"   ❌ System is string, not list! Cache will fail.", flush=True)
                    
            except Exception as dbg_err:
                logger.warning(f"🔍 Cache debug failed: {dbg_err}")
            
            response = await self._claude_model.ainvoke(
                messages,
                config={"callbacks": callbacks} if callbacks else None,
            )
            
            duration_ms = int((time.time() - start_time) * 1000)
            
            # ================================================================
            # USAGE METADATA - Tüm token bilgileri
            # ================================================================
            # LangChain UsageMetadata yapısı:
            # {
            #   "input_tokens": total_input (cache dahil),
            #   "output_tokens": output,
            #   "total_tokens": input + output,
            #   "input_token_details": {
            #     "cache_read": cache_read_input_tokens,
            #     "cache_creation": cache_creation_input_tokens,
            #     "ephemeral_5m_input_tokens": 5 dakikalık cache,
            #     "ephemeral_1h_input_tokens": 1 saatlik cache,
            #   }
            # }
            usage_metadata = getattr(response, "usage_metadata", None) or {}
            
            # Response metadata: model, stop_reason, usage (raw)
            response_metadata = getattr(response, "response_metadata", None) or {}
            
            # Temel token bilgileri
            input_tokens = usage_metadata.get("input_tokens", 0) or 0
            output_tokens = usage_metadata.get("output_tokens", 0) or 0
            total_tokens = usage_metadata.get("total_tokens", 0) or 0
            
            # Input token details (cache bilgisi burada)
            input_token_details = usage_metadata.get("input_token_details", {}) or {}
            cache_read = input_token_details.get("cache_read", 0) or 0
            cache_creation = input_token_details.get("cache_creation", 0) or 0
            ephemeral_5m = input_token_details.get("ephemeral_5m_input_tokens", 0) or 0
            ephemeral_1h = input_token_details.get("ephemeral_1h_input_tokens", 0) or 0
            
            # Response metadata'dan ek bilgiler
            model_id = response_metadata.get("model", self._model_name)
            stop_reason = response_metadata.get("stop_reason", "unknown")
            message_id = response_metadata.get("id", "")
            raw_usage = response_metadata.get("usage", {}) or {}
            
            # Token loglama (detaylı)
            logger.info(
                f"📊 Tokens: input={input_tokens}, output={output_tokens}, total={total_tokens}, "
                f"cache_read={cache_read}, cache_creation={cache_creation}, "
                f"duration={duration_ms}ms, model={model_id}"
            )
            print(f"   📊 Tokens: in={input_tokens}, out={output_tokens}, total={total_tokens}, {duration_ms}ms", flush=True)
            
            # Cache status
            if cache_read > 0:
                cache_pct = (cache_read / input_tokens * 100) if input_tokens > 0 else 0
                logger.info(f"✅ Cache HIT: {cache_read} tokens ({cache_pct:.1f}% of input)")
                print(f"   ✅ Cache HIT: {cache_read} tokens ({cache_pct:.1f}%)", flush=True)
            elif cache_creation > 0:
                logger.info(f"📝 Cache CREATED: {cache_creation} tokens (5 min TTL)")
                print(f"   📝 Cache CREATED: {cache_creation} tokens", flush=True)
            else:
                logger.warning("⚠️ No cache activity - check prompt size or model support")
                print(f"   ⚠️ No cache activity", flush=True)
            
            # ================================================================
            # LANGFUSE - Tüm metadata'yı gönder
            # ================================================================
            if LANGFUSE_AVAILABLE and log_llm_usage:
                # Maliyet hesaplama - Model bazlı fiyatlar (Şubat 2026)
                # https://platform.claude.com/docs/en/about-claude/pricing
                #
                # Claude Opus 4.6/4.5: Input $5, Output $25, 5m Cache Write $6.25, Cache Hit $0.50
                # Claude Opus 4.1/4:   Input $15, Output $75, 5m Cache Write $18.75, Cache Hit $1.50
                # Claude Sonnet 4.5/4: Input $3, Output $15, 5m Cache Write $3.75, Cache Hit $0.30
                # Claude Haiku 4.5:    Input $1, Output $5, 5m Cache Write $1.25, Cache Hit $0.10
                
                # Model'e göre fiyat belirleme
                model_lower = (model_id or "").lower()
                if "opus-4-5" in model_lower or "opus-4.5" in model_lower or "opus-4-6" in model_lower:
                    # Opus 4.5/4.6
                    price_input = 5.0
                    price_output = 25.0
                    price_cache_write = 6.25
                    price_cache_read = 0.50
                elif "opus-4-1" in model_lower or "opus-4" in model_lower:
                    # Opus 4.1/4
                    price_input = 15.0
                    price_output = 75.0
                    price_cache_write = 18.75
                    price_cache_read = 1.50
                elif "sonnet" in model_lower:
                    # Sonnet 4.5/4
                    price_input = 3.0
                    price_output = 15.0
                    price_cache_write = 3.75
                    price_cache_read = 0.30
                elif "haiku" in model_lower:
                    # Haiku 4.5
                    price_input = 1.0
                    price_output = 5.0
                    price_cache_write = 1.25
                    price_cache_read = 0.10
                else:
                    # Default: Opus 4.5 fiyatları
                    price_input = 5.0
                    price_output = 25.0
                    price_cache_write = 6.25
                    price_cache_read = 0.50
                
                # Maliyet hesaplama
                # NOT: Anthropic'in input_tokens cache tokenları İÇERMEZ
                # input_tokens = base input (cache hariç)
                # LangChain toplam = input_tokens + cache_read + cache_creation
                uncached_input = max(0, input_tokens - cache_read - cache_creation)
                cost_input = uncached_input * price_input / 1_000_000
                cost_output = output_tokens * price_output / 1_000_000
                cost_cache_read = cache_read * price_cache_read / 1_000_000
                cost_cache_write = cache_creation * price_cache_write / 1_000_000
                total_cost = cost_input + cost_output + cost_cache_read + cost_cache_write
                
                log_llm_usage(
                    session_id=f"ocr-{file_name}",
                    model=model_id or "claude-opus-4-5",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cached_tokens=cache_read,
                    cost_usd=total_cost,
                    latency_ms=duration_ms,
                    step_name=f"ocr-page-{page_num}",
                    metadata={
                        # Dosya bilgileri
                        "file_name": file_name,
                        "page_num": page_num,
                        # Token detayları
                        "total_tokens": total_tokens,
                        "cache_creation": cache_creation,
                        "cache_read": cache_read,
                        "uncached_input": uncached_input,
                        "ephemeral_5m_tokens": ephemeral_5m,
                        "ephemeral_1h_tokens": ephemeral_1h,
                        # Response bilgileri
                        "model_id": model_id,
                        "stop_reason": stop_reason,
                        "message_id": message_id,
                        # Fiyatlandırma ($/MTok)
                        "pricing": {
                            "input": price_input,
                            "output": price_output,
                            "cache_write": price_cache_write,
                            "cache_read": price_cache_read,
                        },
                        # Maliyet detayları (USD)
                        "cost_breakdown": {
                            "input": cost_input,
                            "output": cost_output,
                            "cache_read": cost_cache_read,
                            "cache_write": cost_cache_write,
                        },
                    },
                )
            
            # Response içeriği
            # Extended thinking kullanıldığında content bir list olarak geliyor:
            # [{"type": "thinking", "thinking": "..."}, {"type": "text", "text": "..."}]
            if hasattr(response, "content"):
                content = response.content
                
                # String ise direkt döndür
                if isinstance(content, str):
                    return content
                
                # List ise text bloğunu bul
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict):
                            # Extended thinking response format
                            if block.get("type") == "text":
                                return block.get("text", "")
                            elif block.get("type") == "thinking":
                                thinking_text = block.get("thinking", "")
                                logger.debug(f"🧠 Thinking: {thinking_text[:200]}...")
                        elif isinstance(block, str):
                            # Bazen direkt string list olabilir
                            return block
                    
                    # Hiçbir text bloğu bulunamazsa, list'i string'e çevir
                    logger.warning(f"⚠️ No text block found in response, content type: {type(content)}")
                    return str(content)
                
                # Diğer türler için string'e çevir
                return str(content)
            
            return str(response)
            
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.error(f"❌ Claude API error after {duration_ms}ms: {e}")
            raise
        
        return ""
    
    def _get_system_prompt(self) -> str:
        """
        Sabit system prompt'u yükle.
        
        Bu prompt cache'lenir - her mesajda aynı olmalı.
        Değişen bilgiler (şema, sayfa) user mesajına eklenir.
        """
        from prompts import get_domain, load_prompt
        
        domain = get_domain()
        
        try:
            # unified_ocr_system.md - sabit system prompt
            system_prompt = load_prompt("unified_ocr_system", domain)
            return system_prompt
        except FileNotFoundError:
            logger.warning(f"unified_ocr_system.md not found for domain {domain}")
            return self._get_fallback_system_prompt()
    
    def _get_fallback_system_prompt(self) -> str:
        """Fallback system prompt."""
        return """Sen Türkiye Ticaret Sicil Gazetesi (TSG) uzmanısın.

Görevin: Gazete sayfalarından şirket ilanlarını okuyup bilgi grafiği için yapılandırılmış veri çıkarmak.

Çıktı formatı: JSON (chunks, nodes, relationships)

Önemli:
- Anlam bütünlüğünü koru (chunking)
- Mevcut şemadaki label ve ID pattern'lerini kullan
- Aynı entity için aynı ID kullan
"""

    def _build_prompt(
        self,
        target_company: str,
        continuation_note: str,
        page_number: int,
        total_pages: int,
    ) -> str:
        """
        unified_ocr.md template'inden prompt oluştur.
        
        Template içindeki placeholder'ları doldurur:
        - {target_company}
        - {page_number}
        - {total_pages}
        - {continuation_context}
        - {graph_schema}
        """
        from prompts import get_domain, load_prompt
        
        domain = get_domain()
        
        try:
            # unified_ocr.md template'ini yükle
            template = load_prompt("unified_ocr", domain)
        except FileNotFoundError:
            logger.warning(f"unified_ocr.md not found for domain {domain}, using fallback")
            template = self._get_fallback_prompt()
        
        # Continuation context
        if continuation_note and continuation_note.strip():
            continuation_context = f"**ÖNCEKİ SAYFADAN DEVAM**: {continuation_note}"
        else:
            continuation_context = ""
        
        # Graf şemasını çek (Neo4j bağlantısı varsa)
        graph_schema = self._get_graph_schema()
        
        # Template'i doldur
        prompt = template.format(
            target_company=target_company,
            page_number=page_number,
            total_pages=total_pages,
            continuation_context=continuation_context,
            graph_schema=graph_schema,
        )
        
        return prompt
    
    def _get_graph_schema(self) -> str:
        """
        Neo4j'den mevcut graf şemasını çeker.
        
        Şema bilgisi Opus'un mevcut yapıya uygun entity ve relationship
        üretmesini sağlar.
        """
        if not hasattr(self, "_graph") or self._graph is None:
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

    def _save_json_output(
        self,
        output: Dict[str, Any],
        file_name: str,
        output_dir: str,
    ) -> None:
        """
        JSON çıktısını dosyaya kaydeder.
        
        Args:
            output: Kaydedilecek JSON output
            file_name: Orijinal dosya adı
            output_dir: Çıktı dizini
        """
        import json
        from pathlib import Path
        
        try:
            output_path = Path(output_dir)
            output_path.mkdir(parents=True, exist_ok=True)
            
            # Dosya adından uzantıyı kaldır ve .json ekle
            base_name = Path(file_name).stem
            json_file = output_path / f"{base_name}_ocr_output.json"
            
            with open(json_file, "w", encoding="utf-8") as f:
                json.dump(output, f, ensure_ascii=False, indent=2)
            
            logger.info(f"📁 JSON output saved: {json_file}")
            print(f"📁 JSON output saved: {json_file}", flush=True)
            
        except Exception as e:
            logger.error(f"❌ Failed to save JSON output: {e}")
    
    def _merge_pages_data(
        self,
        pages_data: List[Dict[str, Any]],
        target_company: str,
    ) -> Dict[str, Any]:
        """
        Birden fazla sayfadan gelen JSON verilerini birleştir.
        
        - Chunk'ları sıralı birleştir (position güncelle)
        - Node'ları ID'ye göre merge et (aynı entity farklı sayfalarda geçebilir)
        - Relationship'leri birleştir (duplicate kaldır)
        """
        merged = {
            "found": True,
            "target_company": target_company,
            "document_type": "",
            "chunks": [],
            "nodes": [],
            "relationships": [],
        }
        
        # Document type: ilk bulunan değeri al
        for page_data in pages_data:
            if page_data.get("document_type"):
                merged["document_type"] = page_data["document_type"]
                break
        
        # Chunk position counter
        chunk_position = 0
        chunk_id_mapping: Dict[str, str] = {}  # old_id -> new_id (for cross-page references)
        
        for page_idx, page_data in enumerate(pages_data):
            page_chunks = page_data.get("chunks", [])
            
            for chunk in page_chunks:
                old_chunk_id = chunk.get("id", f"chunk_{chunk_position:03d}")
                chunk_position += 1
                new_chunk_id = f"chunk_{chunk_position:03d}"
                
                chunk_id_mapping[old_chunk_id] = new_chunk_id
                
                new_chunk = {
                    "id": new_chunk_id,
                    "text": chunk.get("text", ""),
                    "position": chunk_position,
                    "page": chunk.get("page", page_idx + 1),
                }
                merged["chunks"].append(new_chunk)
        
        # Node'ları birleştir (ID bazlı dedup)
        nodes_by_id: Dict[str, Dict[str, Any]] = {}
        
        for page_data in pages_data:
            for node in page_data.get("nodes", []):
                node_id = node.get("id", "")
                
                # Chunk ID'leri güncelle
                old_chunk_ids = node.get("chunk_ids", [])
                new_chunk_ids = [chunk_id_mapping.get(cid, cid) for cid in old_chunk_ids]
                
                if node_id in nodes_by_id:
                    # Mevcut node'a chunk_ids ekle
                    existing = nodes_by_id[node_id]
                    existing_chunk_ids = set(existing.get("chunk_ids", []))
                    existing_chunk_ids.update(new_chunk_ids)
                    existing["chunk_ids"] = list(existing_chunk_ids)
                else:
                    # Yeni node
                    new_node = {
                        "label": node.get("label", "Entity"),
                        "id": node_id,
                        "properties": node.get("properties", {}),
                        "chunk_ids": new_chunk_ids,
                    }
                    nodes_by_id[node_id] = new_node
        
        merged["nodes"] = list(nodes_by_id.values())
        
        # Relationship'leri birleştir (from_id, to_id, type bazlı dedup)
        rels_seen: set = set()
        
        for page_data in pages_data:
            for rel in page_data.get("relationships", []):
                rel_key = (rel.get("from_id"), rel.get("to_id"), rel.get("type"))
                
                if rel_key not in rels_seen:
                    rels_seen.add(rel_key)
                    merged["relationships"].append({
                        "from_id": rel.get("from_id", ""),
                        "to_id": rel.get("to_id", ""),
                        "type": rel.get("type", "RELATED_TO"),
                        "properties": rel.get("properties", {}),
                    })
        
        return merged
    
    def _get_fallback_prompt(self) -> str:
        """unified_ocr.md bulunamazsa kullanılacak fallback prompt."""
        return """
# OCR & Entity Extraction

Hedef şirket: {target_company}
Sayfa: {page_number}/{total_pages}
{continuation_context}

Bu gazete sayfasından hedef şirketin ilanını oku ve aşağıdaki JSON formatında çıkar:

```json
{{
  "found": true|false,
  "document_type": "string",
  "target_company": "{target_company}",
  "chunks": [{{"id": "chunk_001", "text": "...", "position": 1, "page": {page_number}}}],
  "nodes": [{{"label": "string", "id": "string", "properties": {{}}, "chunk_ids": []}}],
  "relationships": [{{"from_id": "string", "to_id": "string", "type": "string", "properties": {{}}}}]
}}
```

Hedef şirket sayfada yoksa: {{"target_company": "{target_company}", "found": false}}
"""

    def _parse_json_response(self, response_text: str) -> Dict[str, Any]:
        """
        Vision model JSON yanıtını parse et.
        
        Returns:
            {
                "data": {...},  # Parsed JSON (normalized)
                "continuation_note": "",
                "is_complete": bool
            }
        """
        text = response_text.strip()

        # Hedef şirket yoksa
        if text.upper() in ("YOK", "N/A", "NULL", ""):
            return {"data": {"found": False}, "continuation_note": "", "is_complete": True}

        # JSON block'u çıkar (```json ... ``` veya direkt JSON)
        json_text = text
        
        # Markdown code block içindeyse çıkar
        json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if json_match:
            json_text = json_match.group(1).strip()
        
        # JSON parse et
        try:
            raw_data = json.loads(json_text)
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error: {e}")
            logger.debug(f"Raw text: {text[:500]}...")
            # Fallback: markdown olarak döndür (legacy uyumluluk)
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
        
        # Opus şemadan öğrendiği için ek normalization'a gerek yok
        # Sadece temel temizlik yap
        data = self._basic_cleanup(raw_data)
        
        # Devam kontrolü: chunk'larda [PAGE_BREAK] veya devam ifadesi var mı
        continuation_note = ""
        is_complete = True
        
        chunks = data.get("chunks", [])
        if chunks:
            last_chunk_text = chunks[-1].get("text", "")
            
            # Devam ifadelerini kontrol et
            devam_patterns = [
                r"[Dd]evamı\s+(\d+)\.\s*[Ss]ayfada",
                r"[Dd]evamı\s+[Ss]ayfa\s+(\d+)",
                r"\(Devamı var\)",
                r"\.{3,}$",  # ... ile biten
            ]
            
            for pattern in devam_patterns:
                match = re.search(pattern, last_chunk_text)
                if match:
                    is_complete = False
                    if match.groups():
                        continuation_note = f"Sayfa {match.group(1)}'de devam ediyor"
                    else:
                        continuation_note = "İlan devam ediyor"
                    break
        
        return {
            "data": data,
            "continuation_note": continuation_note,
            "is_complete": is_complete,
        }
    
    def _basic_cleanup(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Temel veri temizliği.
        
        Şema-driven yaklaşımda Opus zaten doğru label ve ID kullanıyor.
        Burada sadece temel temizlik yapılıyor.
        """
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
    
    def _parse_markdown_response(self, response_text: str) -> Dict[str, Any]:
        """
        Legacy markdown parse (Gemini için veya JSON parse başarısız olursa).
        """
        text = response_text.strip()

        # Hedef şirket yoksa
        if text.upper() in ("YOK", "N/A", "NULL", ""):
            return {"markdown": "", "continuation_note": "", "is_complete": True}

        # "Devamı X. sayfada" veya benzeri ifade var mı kontrol et
        continuation_note = ""
        is_complete = True

        # Devam ifadelerini kontrol et
        devam_patterns = [
            r"[Dd]evamı\s+(\d+)\.\s*[Ss]ayfada",
            r"[Dd]evamı\s+[Ss]ayfa\s+(\d+)",
            r"\(Devamı var\)",
            r"\.{3,}$",  # ... ile biten
        ]

        for pattern in devam_patterns:
            match = re.search(pattern, text)
            if match:
                is_complete = False
                if match.groups():
                    continuation_note = f"Sayfa {match.group(1)}'de devam ediyor"
                else:
                    continuation_note = "İlan devam ediyor"
                break

        return {
            "markdown": text,
            "continuation_note": continuation_note,
            "is_complete": is_complete,
        }

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

    def _load_domain_prompt(self) -> str:
        """Domain-specific prompt'u yükle (varsa)."""
        try:
            from prompts import get_domain, load_prompt

            domain = get_domain()
            if domain:
                try:
                    domain_prompt = load_prompt("ocr_coordinator", domain)
                    if domain_prompt:
                        return f"\n## DOMAIN CONTEXT\n{domain_prompt}\n"
                except FileNotFoundError:
                    pass
        except Exception as e:
            logger.debug(f"Domain prompt yüklenemedi: {e}")
        return ""

    async def close(self) -> None:
        """Cleanup."""
        self._gemini_client = None
        self._initialized = False


# Global instance (singleton)
_agentic_ocr_instance: Optional[AgenticOCR] = None


async def get_agentic_ocr() -> AgenticOCR:
    """AgenticOCR singleton instance al."""
    global _agentic_ocr_instance
    if _agentic_ocr_instance is None:
        _agentic_ocr_instance = AgenticOCR()
        await _agentic_ocr_instance.initialize()
    return _agentic_ocr_instance


def process_agentic_ocr(
    image_list: List[str],
    file_name: str,
    file_id: Optional[int] = None,
    domain: Optional[str] = None,
    graph: Optional[Neo4jGraph] = None,
    output_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Sync wrapper for AgenticOCR.process().
    Celery task'larından çağrılmak üzere.
    
    Args:
        image_list: İşlenecek image path'leri
        file_name: Dosya adı
        file_id: Tracking için
        domain: Domain (opsiyonel)
        graph: Neo4j graph bağlantısı (şema çekmek için)
        output_dir: JSON çıktısı için dizin (opsiyonel)
    """
    print(
        f"[VISION_OCR] Called with {len(image_list)} images, file={file_name}",
        flush=True,
    )

    async def _run():
        ocr = await get_agentic_ocr()
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
