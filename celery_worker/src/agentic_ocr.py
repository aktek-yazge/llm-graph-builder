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
    from langchain_core.language_models import BaseChatModel

# ============================================================================
# LANGCHAIN PROVIDER IMPORTS (tak-çıkar yapı)
# ============================================================================

# Anthropic (Claude)
try:
    from langchain_anthropic import ChatAnthropic
    LANGCHAIN_ANTHROPIC_AVAILABLE = True
    logger.info("✅ LangChain ChatAnthropic imported")
except ImportError as e:
    logger.warning(f"⚠️ LangChain Anthropic not available: {e}")
    LANGCHAIN_ANTHROPIC_AVAILABLE = False
    ChatAnthropic = None  # type: ignore

# OpenAI (GPT)
try:
    from langchain_openai import ChatOpenAI
    LANGCHAIN_OPENAI_AVAILABLE = True
    logger.info("✅ LangChain ChatOpenAI imported")
except ImportError as e:
    logger.warning(f"⚠️ LangChain OpenAI not available: {e}")
    LANGCHAIN_OPENAI_AVAILABLE = False
    ChatOpenAI = None  # type: ignore

# Google (Gemini) - LangChain version
try:
    from langchain_google_genai import ChatGoogleGenerativeAI
    LANGCHAIN_GOOGLE_AVAILABLE = True
    logger.info("✅ LangChain ChatGoogleGenerativeAI imported")
except ImportError as e:
    logger.warning(f"⚠️ LangChain Google GenAI not available: {e}")
    LANGCHAIN_GOOGLE_AVAILABLE = False
    ChatGoogleGenerativeAI = None  # type: ignore

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


# ============================================================================
# PROMPT MODE
# ============================================================================
# "prescriptive" = mevcut detaylı prompt (unified_ocr_system.md)
# "goal_driven"  = minimal, hedef odaklı prompt (goal_driven_system.md) + Neo4j tool
OCR_PROMPT_MODE = os.getenv("OCR_PROMPT_MODE", "prescriptive")

# Goal-driven modda yazma sorguları engellenir
_CYPHER_WRITE_KEYWORDS = re.compile(
    r"\b(CREATE|MERGE|SET|DELETE|REMOVE|DROP|DETACH|CALL\s+\{)\b",
    re.IGNORECASE,
)
_CYPHER_MAX_RESULT_CHARS = 8000  # Tool sonuç boyut limiti


class AgenticOCR:
    """
    Full-Page Vision OCR with Multi-Provider LangChain Support.

    Her sayfayı doğrudan vision modeline gönderir.
    Model sayfanın multi-column yapısını anlar ve sadece hedef şirketin
    içeriğini markdown veya JSON olarak döndürür.
    
    Desteklenen provider'lar (tak-çıkar):
    - Anthropic: claude-opus-4.5, claude-sonnet-4, ...
    - OpenAI: gpt-4o, gpt-4.1, o3-mini, ...
    - Google: gemini-2.5-pro, gemini-2.0-flash, ...
    
    LangChain + Tool-Use + Langfuse entegrasyonu.
    """

    def __init__(self):
        self._llm_model: Optional["BaseChatModel"] = None  # LangChain ortak interface
        self._gemini_client = None  # Legacy: native Gemini client (fallback)
        self._initialized = False
        self._model_provider = None  # "anthropic" | "openai" | "google"
        self._model_name = None
        self._prompt_mode = OCR_PROMPT_MODE  # "prescriptive" or "goal_driven"

    async def initialize(self) -> None:
        """Vision client'ı başlat (model tipine göre)."""
        if self._initialized:
            return

        model_name = os.environ.get("OCR_VISION_MODEL", "gemini-2.5-pro")
        self._model_name = model_name
        
        # ================================================================
        # ANTHROPIC (Claude)
        # ================================================================
        if model_name.startswith("claude"):
            if not LANGCHAIN_ANTHROPIC_AVAILABLE or ChatAnthropic is None:
                raise ImportError(
                    "langchain-anthropic paketi kurulu değil. `uv add langchain-anthropic` ile kurun."
                )
            
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                raise ValueError("ANTHROPIC_API_KEY environment variable not set")
            
            # Model name mapping
            model_mapping = {
                "claude-opus-4.6": "claude-opus-4-6",
                "claude-opus-4-6": "claude-opus-4-6",
                "claude-opus-4.5": "claude-opus-4-5-20251101",
                "claude-opus-4-5": "claude-opus-4-5-20251101",
                "claude-opus-4": "claude-opus-4-20250514",
                "claude-sonnet-4.6": "claude-sonnet-4-6",
                "claude-sonnet-4-6": "claude-sonnet-4-6",
                "claude-sonnet-4.5": "claude-sonnet-4-5-20250929",
                "claude-sonnet-4-5": "claude-sonnet-4-5-20250929",
                "claude-sonnet-4": "claude-sonnet-4-20250514",
                "claude-sonnet": "claude-sonnet-4-20250514",
                "claude-haiku-4.5": "claude-haiku-4-5-20251001",
                "claude-haiku-4-5": "claude-haiku-4-5-20251001",
                "claude-haiku": "claude-haiku-4-5-20251001",
            }
            actual_model = model_mapping.get(model_name, model_name)
            
            # Extended thinking budget
            thinking_budget = int(os.environ.get("OCR_THINKING_BUDGET", "10000"))
            
            model_kwargs: Dict[str, Any] = {
                "model": actual_model,
                "api_key": SecretStr(api_key),
                "max_tokens": 16384,
            }
            
            if thinking_budget > 0:
                model_kwargs["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": thinking_budget,
                }
                logger.info(f"🤖 Vision OCR: {actual_model} (extended_thinking={thinking_budget} tokens)")
            else:
                logger.info(f"🤖 Vision OCR: {actual_model} (thinking=disabled)")
            
            self._llm_model = ChatAnthropic(**model_kwargs)
            self._model_provider = "anthropic"
            print(f"   ✅ LangChain ChatAnthropic initialized: {actual_model}", flush=True)
        
        # ================================================================
        # OPENAI (GPT)
        # ================================================================
        elif model_name.startswith("gpt") or model_name.startswith("o3") or model_name.startswith("o1"):
            if not LANGCHAIN_OPENAI_AVAILABLE or ChatOpenAI is None:
                raise ImportError(
                    "langchain-openai paketi kurulu değil. `uv add langchain-openai` ile kurun."
                )
            
            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                raise ValueError("OPENAI_API_KEY environment variable not set")
            
            # Model name mapping (kısa isimler)
            model_mapping = {
                "gpt-5.2": "gpt-5.2",
                "gpt-5.1": "gpt-5.1",
                "gpt-5": "gpt-5",
                "gpt-5-mini": "gpt-5-mini",
                "gpt-4o": "gpt-4o-2024-11-20",
                "gpt-4.1": "gpt-4.1",
                "gpt-4.1-mini": "gpt-4.1-mini",
                "o3": "o3",
                "o3-mini": "o3-mini-2025-01-31",
                "o4-mini": "o4-mini",
                "o1": "o1",
                "o1-mini": "o1-mini",
            }
            actual_model = model_mapping.get(model_name, model_name)
            
            model_kwargs: Dict[str, Any] = {
                "model": actual_model,
                "api_key": SecretStr(api_key),
                "max_tokens": 16384,
                "temperature": 0,
            }
            
            logger.info(f"🤖 Vision OCR: {actual_model} (OpenAI)")
            
            self._llm_model = ChatOpenAI(**model_kwargs)
            self._model_provider = "openai"
            print(f"   ✅ LangChain ChatOpenAI initialized: {actual_model}", flush=True)
        
        # ================================================================
        # GOOGLE (Gemini) - LangChain version
        # ================================================================
        elif model_name.startswith("gemini"):
            if not LANGCHAIN_GOOGLE_AVAILABLE or ChatGoogleGenerativeAI is None:
                # Fallback to native Gemini client
                logger.warning("⚠️ langchain-google-genai not available, using native client")
                from google import genai
                api_key = os.environ.get("GEMINI_API_KEY")
                if not api_key:
                    raise ValueError("GEMINI_API_KEY environment variable not set")
                self._gemini_client = genai.Client(api_key=api_key)
                self._model_provider = "gemini_native"
                logger.info(f"🤖 Vision OCR initialized: {model_name} (Gemini Native)")
            else:
                api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
                if not api_key:
                    raise ValueError("GEMINI_API_KEY or GOOGLE_API_KEY environment variable not set")
                
                # Model name mapping - LangChain Google GenAI doğrudan model adlarını kullanır
                model_mapping = {
                    "gemini-3-pro-preview": "gemini-3-pro-preview",
                    "gemini-2.5-pro": "gemini-2.5-pro",
                    "gemini-2.5-flash": "gemini-2.5-flash",
                    "gemini-2.0-flash": "gemini-2.0-flash",
                    "gemini-1.5-pro": "gemini-1.5-pro",
                    "gemini-1.5-flash": "gemini-1.5-flash",
                }
                actual_model = model_mapping.get(model_name, model_name)
                
                model_kwargs: Dict[str, Any] = {
                    "model": actual_model,
                    "google_api_key": api_key,
                    "max_output_tokens": 65536,  # Gemini 2.5 supports up to 65k
                    "temperature": 0,
                }
                
                logger.info(f"🤖 Vision OCR: {actual_model} (Google LangChain)")
                
                self._llm_model = ChatGoogleGenerativeAI(**model_kwargs)
                self._model_provider = "google"
                print(f"   ✅ LangChain ChatGoogleGenerativeAI initialized: {actual_model}", flush=True)
        
        else:
            raise ValueError(f"Desteklenmeyen model: {model_name}. claude/gpt/gemini prefix'i kullanın.")
        
        self._initialized = True

    async def _load_skill_with_fallback(self, skill_id: str):
        """
        Skill'i Agent Builder API'den yüklemeyi dene.
        
        Öncelik sırası:
        1. Agent Builder API (http://localhost:8001)
        2. Fallback: None döndür → mevcut domain/prompt klasör yapısı kullanılır
        
        Returns:
            SkillExecution veya None (None ise domain mode devam eder)
        """
        import os
        import httpx
        
        # Agent Builder API URL
        agent_builder_url = os.getenv("AGENT_BUILDER_URL", "http://localhost:8001")
        api_endpoint = f"{agent_builder_url}/api/v2/agent-builder/skills/{skill_id}/execution"
        
        # API'den yüklemeyi dene
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(api_endpoint)
                
                if response.status_code == 200:
                    data = response.json()
                    
                    # SkillExecution benzeri dataclass oluştur
                    from dataclasses import dataclass, field
                    from typing import Dict, Any, List
                    import json
                    
                    @dataclass
                    class SkillExecution:
                        skill_id: str
                        name: str
                        category: str
                        prompt_template: str
                        input_schema: Dict[str, Any]
                        output_schema: Dict[str, Any]
                        entity_schemas: List[Dict[str, Any]] = field(default_factory=list)
                        relationship_schemas: List[Dict[str, Any]] = field(default_factory=list)
                        version: int = 1
                        effectiveness_score: float = 0.5
                        
                        @property
                        def entity_schemas_json(self) -> str:
                            return json.dumps(self.entity_schemas, ensure_ascii=False, indent=2)
                        
                        @property
                        def entity_types_list(self) -> List[str]:
                            return [e["entity_type"] for e in self.entity_schemas if e.get("entity_type")]
                        
                        def format_prompt(self, **kwargs) -> str:
                            format_params = {
                                "entity_schemas": self.entity_schemas_json,
                                "entity_types": ", ".join(self.entity_types_list),
                                **kwargs
                            }
                            try:
                                return self.prompt_template.format(**format_params)
                            except KeyError:
                                return self.prompt_template
                    
                    skill = SkillExecution(
                        skill_id=data.get("skill_id", skill_id),
                        name=data.get("name", ""),
                        category=data.get("category", "extraction"),
                        prompt_template=data.get("prompt_template", ""),
                        input_schema=data.get("input_schema", {}),
                        output_schema=data.get("output_schema", {}),
                        entity_schemas=data.get("entity_schemas", []),
                        relationship_schemas=data.get("relationship_schemas", []),
                        version=data.get("version", 1),
                        effectiveness_score=data.get("effectiveness_score", 0.5)
                    )
                    
                    logger.info(f"✅ Loaded skill via API: {skill.name} (v{skill.version})")
                    return skill
                else:
                    logger.warning(f"⚠️ API returned {response.status_code} for skill {skill_id}")
                    
        except Exception as api_error:
            logger.warning(f"⚠️ Agent Builder API not available: {api_error}")
        
        # Fallback: None döndür → mevcut domain/prompt klasör yapısı (load_prompt) kullanılır
        logger.info(f"📁 Fallback to domain mode: skill_id={skill_id} ignored, using prompts/{self._domain}/ folder")
        return None

    async def process(
        self,
        image_list: Optional[List[str]] = None,
        file_name: str = "",
        file_id: Optional[int] = None,
        domain: Optional[str] = None,
        graph: Optional[Neo4jGraph] = None,
        output_dir: Optional[str] = None,
        # ──────────────────────────────────────────
        # TEXT MODE: Görsel yerine OCR metni işler
        # ──────────────────────────────────────────
        text_mode: bool = False,
        ocr_texts: Optional[List[str]] = None,
        batch_size: int = 50,
        # ──────────────────────────────────────────
        # DYNAMIC SKILL MODE: Ontology DB'den skill yükle
        # Agent Builder ile oluşturulan skill'leri kullanır
        # ──────────────────────────────────────────
        skill_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Sayfaları tek tek işle.

        Args:
            image_list: İşlenecek image path'leri (text_mode=False ise zorunlu)
            file_name: Dosya adı (prompt'ta şirket adı çıkarılır)
            file_id: Tracking için
            domain: Domain (opsiyonel)
            graph: Neo4j graph bağlantısı (şema çekmek için)
            output_dir: JSON çıktısı için dizin (opsiyonel)
            text_mode: True ise görsel yerine OCR metni işler
            ocr_texts: OCR metinleri (text_mode=True ise zorunlu)
            batch_size: Text mode'da batch boyutu (default: 50 sayfa)
            skill_id: Ontology DB'den yüklenecek skill ID (Agent Builder)
                      Verilirse, domain parametresi yerine skill'den prompt ve
                      entity schema'lar alınır.

        Returns:
            {"markdown": "...", "metadata": {...}, "status": "success"|"error"}
        """
        self._graph = graph  # Şema çekmek için sakla
        
        # ══════════════════════════════════════════════════════════════════
        # DYNAMIC SKILL MODE: Ontology DB'den skill yükle
        # Agent Builder ile oluşturulan skill'leri kullanır
        # Önce API'den çekmeyi dene, API yoksa fallback olarak klasör yapısını kullan
        # ══════════════════════════════════════════════════════════════════
        self._dynamic_skill = None
        if skill_id:
            self._dynamic_skill = await self._load_skill_with_fallback(skill_id)
        if not self._initialized:
            await self.initialize()

        # ══════════════════════════════════════════════════════════════════
        # TEXT MODE: Görsel yerine OCR metni işle
        # ══════════════════════════════════════════════════════════════════
        if text_mode:
            if not ocr_texts:
                raise ValueError("text_mode=True requires ocr_texts parameter")
            return await self._process_text_mode(
                ocr_texts=ocr_texts,
                file_name=file_name,
                file_id=file_id,
                domain=domain,
                output_dir=output_dir,
                batch_size=batch_size,
            )

        # ══════════════════════════════════════════════════════════════════
        # IMAGE MODE: Mevcut görsel işleme mantığı
        # ══════════════════════════════════════════════════════════════════
        if not image_list:
            raise ValueError("image_list required when text_mode=False")

        from prompts import get_domain
        from src.shared.langfuse_client import get_langfuse, flush_langfuse

        domain = domain or get_domain()
        target_company = self._extract_company_from_filename(file_name)
        model_name = os.environ.get("OCR_VISION_MODEL", "gemini-2.5-pro")

        logger.info(
            f"🚀 Starting Vision OCR: model={model_name}, file={file_name}, "
            f"target={target_company}, pages={len(image_list)}, mode={self._prompt_mode}"
        )
        print(
            f"\n{'='*60}\n"
            f"🚀 VISION OCR START\n"
            f"   Model: {model_name}\n"
            f"   Mode: {self._prompt_mode}\n"
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
        # Goal-driven mode: tüm LangChain provider'lar için tool-use destekli agent loop
        if self._prompt_mode == "goal_driven" and self._model_provider in ("anthropic", "openai", "google"):
            from src.ocr_tools import set_current_image_path, reset_page_state
            reset_page_state()  # Önceki sayfa state'ini temizle
            set_current_image_path(page_path)
            
            response_text = await self._call_agent_mode(
                image_data, mime_type, prompt, model_name,
                page_num=page_number,
                file_name=file_name,
            )
        elif self._model_provider == "anthropic":
            # Prescriptive mode: tek seferlik çağrı (Anthropic)
            response_text = await self._call_llm_vision(
                image_data, mime_type, prompt, model_name,
                page_num=page_number,
                file_name=file_name,
            )
        elif self._model_provider == "gemini_native":
            # Native Gemini client (LangChain olmadan fallback)
            response_text = await self._call_gemini(image_data, mime_type, prompt, model_name)
        else:
            # LangChain LLM ile basit çağrı (prescriptive mode - OpenAI/Google)
            response_text = await self._call_langchain_simple(
                image_data, mime_type, prompt, model_name
            )

        # Yanıtı parse et
        if not response_text:
            logger.warning(f"Empty response from {model_name}")
            return {"data": {"found": False}, "continuation_note": "", "is_complete": True}

        # Goal-driven mode: tüm provider'lar için JSON parse
        # Prescriptive/native Gemini mode: markdown parse
        if self._prompt_mode == "goal_driven":
            return self._parse_json_response(response_text)
        elif self._model_provider == "gemini_native":
            return self._parse_markdown_response(response_text)
        else:
            return self._parse_json_response(response_text)

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

    async def _call_langchain_simple(
        self,
        image_data: bytes,
        mime_type: str,
        prompt: str,
        model_name: str,
    ) -> str:
        """
        Prescriptive mode için LangChain basit çağrı (tool olmadan).
        
        OpenAI ve Google LangChain modelleri için kullanılır.
        """
        if not self._llm_model:
            raise RuntimeError("LLM model not initialized. Call initialize() first.")
        
        from langchain_core.messages import SystemMessage, HumanMessage
        
        image_base64 = base64.b64encode(image_data).decode("utf-8")
        system_prompt = self._get_system_prompt()
        
        # Provider'a göre image format
        if self._model_provider == "openai":
            image_content = {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime_type};base64,{image_base64}",
                    "detail": "high",
                },
            }
        else:  # google
            image_content = {
                "type": "image_url",
                "image_url": f"data:{mime_type};base64,{image_base64}",
            }
        
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(
                content=[
                    image_content,
                    {"type": "text", "text": prompt},
                ]
            ),
        ]
        
        try:
            response = await self._llm_model.ainvoke(messages)
            
            if hasattr(response, "content"):
                if isinstance(response.content, str):
                    return response.content
                elif isinstance(response.content, list):
                    text_parts = []
                    for block in response.content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif isinstance(block, str):
                            text_parts.append(block)
                    return "\n".join(text_parts)
            return ""
        except Exception as e:
            logger.error(f"❌ LangChain simple call failed: {e}")
            raise

    async def _call_llm_vision(
        self,
        image_data: bytes,
        mime_type: str,
        prompt: str,
        model_name: str,
        page_num: int = 1,
        file_name: str = "unknown",
    ) -> str:
        """
        LLM Vision API çağrısı via LangChain.
        
        Model-agnostic: Anthropic, OpenAI, Google destekler.
        
        LangChain entegrasyonu sayesinde:
        - Otomatik prompt caching
        - Langfuse observability
        - Extended thinking desteği (Anthropic)
        
        Args:
            image_data: Görsel verisi (bytes)
            mime_type: Görsel MIME tipi
            prompt: Dinamik prompt (şema, hedef şirket, sayfa bilgisi)
            model_name: Model adı
            page_num: Sayfa numarası (Langfuse trace için)
            file_name: Dosya adı (Langfuse trace için)
        """
        if not self._llm_model:
            raise RuntimeError("LLM model not initialized. Call initialize() first.")
        
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
                _dbg_payload = self._llm_model._get_request_payload(messages)
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
            
            response = await self._llm_model.ainvoke(
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
                # Maliyet hesaplama
                pricing = self._get_model_pricing(model_id)
                price_input = pricing["input"]
                price_output = pricing["output"]
                price_cache_write = pricing["cache_write"]
                price_cache_read = pricing["cache_read"]
                
                # NOT: Anthropic'in input_tokens cache tokenları İÇERMEZ
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
    
    def _get_system_prompt(self, text_mode: bool = False) -> str:
        """
        Sabit system prompt'u yükle ve skill'leri inject et.
        
        Bu prompt cache'lenir - her mesajda aynı olmalı.
        Değişen bilgiler (şema, sayfa) user mesajına eklenir.
        
        Args:
            text_mode: True ise text mode prompt (goal_driven_text_system.md) yüklenir
        
        Returns:
            System prompt string
        """
        from prompts import get_domain, load_prompt, load_skill
        
        domain = get_domain()
        system_prompt = None
        
        # Text mode: görsel tool'lar olmadan sadece metin işleme
        if text_mode:
            try:
                system_prompt = load_prompt("goal_driven_text_system", domain)
                logger.info(f"📦 Text mode system prompt loaded ({len(system_prompt)} chars)")
            except FileNotFoundError:
                logger.warning(f"goal_driven_text_system.md not found for domain {domain}, falling back to goal_driven")
        
        # Goal-driven mode: görsel işleme için tool'lu prompt
        if system_prompt is None and self._prompt_mode == "goal_driven":
            try:
                system_prompt = load_prompt("goal_driven_system", domain)
                logger.info(f"📦 Goal-driven system prompt loaded ({len(system_prompt)} chars)")
            except FileNotFoundError:
                logger.warning(f"goal_driven_system.md not found for domain {domain}, falling back")
        
        # Fallback to unified_ocr_system
        if system_prompt is None:
            try:
                system_prompt = load_prompt("unified_ocr_system", domain)
            except FileNotFoundError:
                logger.warning(f"unified_ocr_system.md not found for domain {domain}")
                return self._get_fallback_system_prompt()
        
        # Skill injection: {{ENTITY_EXTRACTION_SKILL}} placeholder'ı varsa inject et
        if "{{ENTITY_EXTRACTION_SKILL}}" in system_prompt:
            try:
                skill = load_skill("entity_extraction_skill", domain)
                system_prompt = system_prompt.replace(
                    "{{ENTITY_EXTRACTION_SKILL}}", 
                    skill["content"]
                )
                logger.info(f"📦 Entity extraction skill injected ({len(skill['content'])} chars)")
            except FileNotFoundError:
                logger.warning(f"entity_extraction_skill.md not found for domain {domain}, removing placeholder")
                system_prompt = system_prompt.replace("{{ENTITY_EXTRACTION_SKILL}}", "")
        
        return system_prompt
    
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
        User prompt template'inden prompt oluştur.
        
        Goal-driven mode'da goal_driven_user.md, prescriptive mode'da
        unified_ocr.md template'i yüklenir.
        
        Template içindeki placeholder'ları doldurur:
        - {target_company}
        - {page_number}
        - {total_pages}
        - {continuation_context}
        - {graph_schema}
        """
        from prompts import get_domain, load_prompt
        
        domain = get_domain()
        
        # Goal-driven mode: minimal user prompt
        if self._prompt_mode == "goal_driven":
            try:
                template = load_prompt("goal_driven_user", domain)
                logger.info(f"📝 Goal-driven user prompt loaded")
            except FileNotFoundError:
                logger.warning(f"goal_driven_user.md not found, falling back to unified_ocr.md")
                template = self._get_fallback_prompt()
        else:
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

    # ================================================================
    # SHARED: Pricing
    # ================================================================

    @staticmethod
    def _get_model_pricing(model_id: str) -> Dict[str, float]:
        """
        Model bazlı fiyat tablosu ($/MTok). Şubat 2026.
        
        Anthropic Claude:
        https://platform.claude.com/docs/en/about-claude/pricing
        Claude Opus 4.6/4.5: Input $5, Output $25, 5m Cache Write $6.25, Cache Hit $0.50
        Claude Opus 4.1/4:   Input $15, Output $75, 5m Cache Write $18.75, Cache Hit $1.50
        Claude Sonnet 4.5/4: Input $3, Output $15, 5m Cache Write $3.75, Cache Hit $0.30
        Claude Haiku 4.5:    Input $1, Output $5, 5m Cache Write $1.25, Cache Hit $0.10
        
        OpenAI:
        https://platform.openai.com/docs/models
        GPT-4o:       Input $2.50, Output $10
        GPT-4.1:      Input $2.00, Output $8.00
        GPT-4.1-mini: Input $0.40, Output $1.60
        o3-mini:      Input $1.10, Output $4.40
        o1:           Input $15, Output $60
        
        Google Gemini:
        https://ai.google.dev/pricing
        Gemini 2.5 Pro:   Input $1.25, Output $10 (pay-as-you-go)
        Gemini 2.5 Flash: Input $0.075, Output $0.30
        Gemini 2.0 Flash: Input $0.10, Output $0.40
        Gemini 1.5 Pro:   Input $1.25, Output $5.00
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
        
        # ── OPENAI (GPT) ──
        if "gpt-4o" in m:
            return {"input": 2.50, "output": 10.0, "cache_write": 0.0, "cache_read": 0.0}
        if "gpt-4.1-mini" in m or "gpt-4-1-mini" in m:
            return {"input": 0.40, "output": 1.60, "cache_write": 0.0, "cache_read": 0.0}
        if "gpt-4.1" in m or "gpt-4-1" in m:
            return {"input": 2.0, "output": 8.0, "cache_write": 0.0, "cache_read": 0.0}
        if "gpt-5-mini" in m:
            return {"input": 1.0, "output": 4.0, "cache_write": 0.0, "cache_read": 0.0}
        if "o3-mini" in m:
            return {"input": 1.10, "output": 4.40, "cache_write": 0.0, "cache_read": 0.0}
        if "o1-mini" in m:
            return {"input": 3.0, "output": 12.0, "cache_write": 0.0, "cache_read": 0.0}
        if "o1" in m:
            return {"input": 15.0, "output": 60.0, "cache_write": 0.0, "cache_read": 0.0}
        
        # ── GOOGLE (Gemini) ── Şubat 2026
        # https://ai.google.dev/gemini-api/docs/pricing
        if "gemini-2.5-pro" in m or "gemini-2-5-pro" in m:
            return {"input": 1.25, "output": 10.0, "cache_write": 0.0, "cache_read": 0.0}
        if "gemini-2.5-flash-lite" in m or "gemini-2-5-flash-lite" in m:
            return {"input": 0.10, "output": 0.40, "cache_write": 0.0, "cache_read": 0.0}
        if "gemini-2.5-flash" in m or "gemini-2-5-flash" in m:
            return {"input": 0.30, "output": 2.50, "cache_write": 0.0, "cache_read": 0.0}
        if "gemini-2.0-flash-lite" in m or "gemini-2-0-flash-lite" in m:
            return {"input": 0.075, "output": 0.30, "cache_write": 0.0, "cache_read": 0.0}
        if "gemini-2.0-flash" in m or "gemini-2-0-flash" in m:
            return {"input": 0.10, "output": 0.40, "cache_write": 0.0, "cache_read": 0.0}
        if "gemini-1.5-pro" in m or "gemini-1-5-pro" in m:
            return {"input": 1.25, "output": 5.0, "cache_write": 0.0, "cache_read": 0.0}
        if "gemini-1.5-flash" in m or "gemini-1-5-flash" in m:
            return {"input": 0.075, "output": 0.30, "cache_write": 0.0, "cache_read": 0.0}
        if "gemini" in m:
            # Default Gemini (2.5 Pro)
            return {"input": 1.25, "output": 10.0, "cache_write": 0.0, "cache_read": 0.0}
        
        # Default: Opus 4.5
        return {"input": 5.0, "output": 25.0, "cache_write": 6.25, "cache_read": 0.50}

    # ================================================================
    # GOAL-DRIVEN MODE: Neo4j Tool & Agent Loop
    # ================================================================

    def _create_neo4j_read_tool(self):
        """
        Goal-driven mode için Neo4j read-only query tool oluştur.
        
        LangChain @tool decorator ile oluşturulur ve bind_tools() ile
        model'e bağlanır. Yazma sorguları (CREATE, MERGE, DELETE vb.) engellenir.
        
        Returns:
            LangChain tool fonksiyonu veya None (graph bağlantısı yoksa)
        """
        if not hasattr(self, "_graph") or self._graph is None:
            logger.warning("⚠️ Neo4j graph bağlantısı yok, tool oluşturulamadı")
            return None
        
        graph = self._graph
        
        from langchain_core.tools import tool as langchain_tool
        
        @langchain_tool
        def neo4j_query(cypher: str) -> str:
            """Mevcut Neo4j bilgi grafiğini sorgula. Sadece okuma (MATCH/RETURN) sorguları çalıştırılabilir.
            
            Kullanım alanları:
            - Mevcut entity'lerin ID ve property'lerini kontrol et
            - Aynı şirket/kişi zaten grafta var mı bak
            - İlişki pattern'lerini ve mevcut yapıyı incele
            
            Args:
                cypher: Çalıştırılacak Cypher sorgusu (sadece read-only)
            """
            # Yazma sorgusu kontrolü
            if _CYPHER_WRITE_KEYWORDS.search(cypher):
                return "HATA: Sadece okuma sorguları çalıştırılabilir. CREATE/MERGE/DELETE/SET kullanılamaz."
            
            try:
                result = graph.query(cypher)
                result_str = json.dumps(result, default=str, ensure_ascii=False)
                
                # Sonuç boyutunu sınırla
                if len(result_str) > _CYPHER_MAX_RESULT_CHARS:
                    result_str = result_str[:_CYPHER_MAX_RESULT_CHARS] + "\n... (sonuç kısaltıldı)"
                
                return result_str
            except Exception as e:
                return f"Cypher hatası: {str(e)}"
        
        return neo4j_query

    async def _call_agent_mode(
        self,
        image_data: bytes,
        mime_type: str,
        prompt: str,
        model_name: str,
        page_num: int = 1,
        file_name: str = "unknown",
    ) -> str:
        """
        Goal-driven mode: Tool-use destekli agent loop.
        
        LLM Neo4j query tool'u kullanarak mevcut grafı sorgulayabilir,
        entity resolution yapabilir ve sonra nihai JSON çıktısını döndürür.
        
        Flow:
        1. LLM görüntüyü ve prompt'u alır
        2. neo4j_query tool'u ile mevcut entity'leri kontrol eder (0-N kez)
        3. Tool call bitince nihai JSON yanıtı döndürür
        """
        if not self._llm_model:
            raise RuntimeError("LLM model not initialized. Call initialize() first.")
        
        from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage, AIMessage
        from src.ocr_tools import get_grid_tools, set_current_image_path
        
        # Mevcut sayfa görüntüsünü ayarla (grid tool'ları için)
        # page_path parametresi _process_page'den geliyor, burada image_data var
        # image_path'i ayrı parametre olarak almamız gerekiyor
        
        # Neo4j tool oluştur
        neo4j_tool = self._create_neo4j_read_tool()
        tools = [neo4j_tool] if neo4j_tool else []
        
        # Grid tool'larını ekle (draw_grid, crop_by_cells, get_image_path)
        grid_tools = get_grid_tools()
        tools.extend(grid_tools)
        logger.info(f"📐 Grid tools eklendi: {[t.name for t in grid_tools]}")
        
        # Tool'ları model'e bağla
        if tools:
            model_with_tools = self._llm_model.bind_tools(tools)
            logger.info(f"🔧 Goal-driven mode: {len(tools)} tool bağlandı")
        else:
            model_with_tools = self._llm_model
            logger.warning("⚠️ Goal-driven mode: Tool yok, tool-less çalışılıyor")
        
        # Base64 encode image
        image_base64 = base64.b64encode(image_data).decode("utf-8")
        
        # System prompt (goal-driven)
        system_prompt = self._get_system_prompt()
        
        logger.info(f"📦 Goal-driven system prompt: {len(system_prompt)} chars")
        print(f"   📦 Goal-driven system prompt: {len(system_prompt)} chars", flush=True)
        
        # Messages oluştur (provider'a göre)
        if self._model_provider == "anthropic":
            # Anthropic: cache_control destekli
            system_message = SystemMessage(
                content=[
                    {
                        "type": "text",
                        "text": system_prompt,
                        "cache_control": {"type": "ephemeral", "ttl": "5m"},
                    },
                ]
            )
        else:
            # OpenAI/Google: basit string format
            system_message = SystemMessage(content=system_prompt)
        
        # Provider'a göre image format (LangChain multi-modal)
        if self._model_provider == "anthropic":
            # Anthropic Claude format
            image_content = {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": mime_type,
                    "data": image_base64,
                },
            }
        elif self._model_provider == "openai":
            # OpenAI GPT-4o format
            image_content = {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime_type};base64,{image_base64}",
                    "detail": "high",  # high resolution for OCR
                },
            }
        elif self._model_provider == "google":
            # Google Gemini LangChain format
            image_content = {
                "type": "image_url",
                "image_url": f"data:{mime_type};base64,{image_base64}",
            }
        else:
            raise ValueError(f"Unsupported provider for image: {self._model_provider}")
        
        user_message = HumanMessage(
            content=[
                image_content,
                {
                    "type": "text",
                    "text": prompt,
                },
            ]
        )
        
        messages = [system_message, user_message]
        
        # Langfuse callbacks
        callbacks = []
        if LANGFUSE_AVAILABLE and get_langfuse_callback_handler:
            langfuse_handler = get_langfuse_callback_handler(
                session_id=f"ocr-goal-driven-{file_name}",
                trace_name=f"ocr-gd-{file_name}-p{page_num}",
                tags=["ocr", "vision", self._model_provider or "unknown", "goal_driven"],
                metadata={
                    "file_name": file_name,
                    "page_num": page_num,
                    "model": model_name,
                    "prompt_mode": "goal_driven",
                },
            )
            if langfuse_handler:
                callbacks.append(langfuse_handler)
        
        # Agent loop: tool call → execute → continue → ... → final answer
        max_iterations = 10  # Sonsuz döngü koruması
        iteration = 0
        total_tool_calls = 0
        
        # Token sayaçları (tüm iterasyonlar toplam)
        agg_input_tokens = 0
        agg_output_tokens = 0
        agg_cache_read = 0
        agg_cache_creation = 0
        
        start_time = time.time()
        
        while iteration < max_iterations:
            iteration += 1
            
            logger.info(f"🔄 Agent iteration {iteration}/{max_iterations}")
            print(f"   🔄 Agent iteration {iteration}...", flush=True)
            
            try:
                response = await model_with_tools.ainvoke(
                    messages,
                    config={"callbacks": callbacks} if callbacks else {},
                )
            except Exception as e:
                logger.error(f"❌ Agent iteration {iteration} failed: {e}")
                raise
            
            # ── Token bilgilerini topla ──
            usage_metadata = getattr(response, "usage_metadata", None) or {}
            iter_input = usage_metadata.get("input_tokens", 0) or 0
            iter_output = usage_metadata.get("output_tokens", 0) or 0
            input_details = usage_metadata.get("input_token_details", {}) or {}
            iter_cache_read = input_details.get("cache_read", 0) or 0
            iter_cache_creation = input_details.get("cache_creation", 0) or 0
            
            agg_input_tokens += iter_input
            agg_output_tokens += iter_output
            agg_cache_read += iter_cache_read
            agg_cache_creation += iter_cache_creation
            
            logger.info(
                f"   📊 Iter {iteration}: in={iter_input}, out={iter_output}, "
                f"cache_read={iter_cache_read}, cache_create={iter_cache_creation}"
            )
            
            # Tool call var mı kontrol et
            if not hasattr(response, "tool_calls") or not response.tool_calls:
                # Final answer - token özetini logla ve döndür
                duration_ms = int((time.time() - start_time) * 1000)
                agg_total = agg_input_tokens + agg_output_tokens
                
                logger.info(
                    f"✅ Agent completed in {iteration} iteration(s), "
                    f"{total_tool_calls} tool call(s), {duration_ms}ms"
                )
                print(
                    f"   ✅ Agent done: {iteration} iter, {total_tool_calls} tool calls, {duration_ms}ms",
                    flush=True,
                )
                
                # ── Token / Maliyet Özeti ──
                self._log_agent_usage(
                    agg_input_tokens=agg_input_tokens,
                    agg_output_tokens=agg_output_tokens,
                    agg_cache_read=agg_cache_read,
                    agg_cache_creation=agg_cache_creation,
                    duration_ms=duration_ms,
                    iterations=iteration,
                    tool_calls=total_tool_calls,
                    file_name=file_name,
                    page_num=page_num,
                    response_metadata=getattr(response, "response_metadata", None) or {},
                )
                
                # Response content'i çıkar
                if hasattr(response, "content"):
                    if isinstance(response.content, str):
                        return response.content
                    elif isinstance(response.content, list):
                        # Content blocks listesi - text olanları birleştir
                        text_parts = []
                        for block in response.content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                text_parts.append(block.get("text", ""))
                            elif isinstance(block, str):
                                text_parts.append(block)
                        return "\n".join(text_parts)
                
                return ""
            
            # Tool call'ları işle
            messages.append(response)  # AI message with tool calls
            
            # Tool'ları isimle eşleştir (hızlı lookup için)
            tools_by_name = {t.name: t for t in tools}
            
            for tc in response.tool_calls:
                tool_name = tc.get("name", "unknown")
                tool_args = tc.get("args", {})
                tool_id = tc.get("id", "")
                
                total_tool_calls += 1
                logger.info(f"🔧 Tool call #{total_tool_calls}: {tool_name}({tool_args})")
                print(f"   🔧 Tool: {tool_name} → {str(tool_args)[:100]}", flush=True)
                
                # Tool'u bul ve çalıştır
                tool = tools_by_name.get(tool_name)
                if tool:
                    try:
                        tool_result = tool.invoke(tool_args)
                    except Exception as e:
                        tool_result = f"Tool hatası ({tool_name}): {str(e)}"
                        logger.error(f"❌ Tool error: {tool_name} - {e}")
                else:
                    tool_result = f"Bilinmeyen tool: {tool_name}"
                    logger.warning(f"⚠️ Unknown tool: {tool_name}")
                
                # Tool sonucunu mesajlara ekle
                # Görüntü üreten tool'lar için image content block ekle
                tool_content = self._build_tool_content(
                    tool_name, str(tool_result)
                )
                messages.append(
                    ToolMessage(
                        content=tool_content,
                        tool_call_id=tool_id,
                    )
                )
                
                logger.debug(f"   Tool result: {str(tool_result)[:200]}")
        
        # Max iteration'a ulaşıldı
        logger.warning(f"⚠️ Agent max iteration ({max_iterations}) aşıldı!")
        duration_ms = int((time.time() - start_time) * 1000)
        print(f"   ⚠️ Agent max iteration aşıldı: {duration_ms}ms", flush=True)
        
        # Son response'u text olarak döndür
        if messages and hasattr(messages[-1], "content"):
            return str(messages[-1].content)
        return ""

    # ================================================================
    # TOOL CONTENT BUILDER: Görüntü döndüren tool'lar için
    # ================================================================

    # Görüntü döndüren tool isimleri
    _IMAGE_TOOLS = {"draw_grid", "crop_by_cells"}

    def _build_tool_content(self, tool_name: str, tool_result: str):
        """
        Tool sonucunu content block'larına dönüştür.
        
        draw_grid ve crop_by_cells gibi görüntü üreten tool'lar için
        sonuç metninden dosya yolunu çıkarıp, görüntüyü base64 olarak
        tool mesajına ekler. Böylece LLM grid'i veya kırpılmış görüntüyü
        görebilir.
        
        NOT: OpenAI ToolMessage'larda görüntü desteklemiyor. Bu nedenle
        OpenAI için görüntü yerine sadece dosya yolu döndürülür.
        
        Args:
            tool_name: Tool adı
            tool_result: Tool'un döndürdüğü metin sonucu
        
        Returns:
            str (sadece metin) veya list (metin + görüntü content blocks)
        """
        # OpenAI ve Google: ToolMessage'larda image desteklenmiyor
        # Sadece Anthropic (Claude) tool response'larında görsel kabul ediyor
        if self._model_provider in ("openai", "google"):
            return tool_result
        
        if tool_name not in self._IMAGE_TOOLS:
            return tool_result
        
        # Tool sonucundan dosya yolunu çıkar
        # draw_grid: "TSG Grid çizildi: /path..." veya "✅ Izgara çizildi: /path..."
        # crop_by_cells: "Kırpıldı: /path..." veya "✂️ Kırpıldı: /path..."
        image_path = None
        for line in tool_result.split("\n"):
            line = line.strip()
            for prefix in (
                "TSG Grid çizildi:",
                "Kırpıldı:",
                "✅ Izgara çizildi:",
                "✂️ Kırpıldı:",
            ):
                if line.startswith(prefix):
                    image_path = line[len(prefix):].strip()
                    break
            if image_path:
                break
        
        if not image_path or not os.path.exists(image_path):
            logger.warning(f"⚠️ Tool {tool_name}: görüntü bulunamadı, sadece metin dönüyor")
            return tool_result
        
        try:
            with open(image_path, "rb") as f:
                img_data = f.read()
            
            img_base64 = base64.b64encode(img_data).decode("utf-8")
            mime_type = "image/png" if image_path.endswith(".png") else "image/jpeg"
            
            # Provider'a göre image content format
            if self._model_provider == "anthropic":
                image_content = {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mime_type,
                        "data": img_base64,
                    },
                }
            elif self._model_provider == "openai":
                image_content = {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{img_base64}",
                        "detail": "high",
                    },
                }
            elif self._model_provider == "google":
                image_content = {
                    "type": "image_url",
                    "image_url": f"data:{mime_type};base64,{img_base64}",
                }
            else:
                # Fallback: Anthropic format
                image_content = {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mime_type,
                        "data": img_base64,
                    },
                }
            
            # Content blocks: görüntü + metin
            content = [
                image_content,
                {
                    "type": "text",
                    "text": tool_result,
                },
            ]
            
            img_kb = len(img_data) // 1024
            logger.info(f"📸 Tool {tool_name}: görüntü eklendi ({img_kb}KB)")
            print(f"   📸 Tool görüntü: {image_path} ({img_kb}KB)", flush=True)
            
            return content
            
        except Exception as e:
            logger.warning(f"⚠️ Tool {tool_name}: görüntü eklenemedi: {e}")
            return tool_result

    def _log_agent_usage(
        self,
        agg_input_tokens: int,
        agg_output_tokens: int,
        agg_cache_read: int,
        agg_cache_creation: int,
        duration_ms: int,
        iterations: int,
        tool_calls: int,
        file_name: str,
        page_num: int,
        response_metadata: Dict[str, Any],
    ) -> None:
        """
        Goal-driven agent modunun toplam token kullanımını ve maliyetini loglar.
        
        Tüm iterasyonlardaki token'lar toplanır ve tek bir özet basılır.
        Langfuse'a da gönderilir (aktifse).
        """
        agg_total = agg_input_tokens + agg_output_tokens
        model_id = response_metadata.get("model", self._model_name or "claude-opus-4-5")
        
        # ── Console / Logger özeti ──
        logger.info(
            f"📊 Agent Tokens (total): input={agg_input_tokens}, output={agg_output_tokens}, "
            f"total={agg_total}, cache_read={agg_cache_read}, cache_creation={agg_cache_creation}, "
            f"duration={duration_ms}ms, model={model_id}"
        )
        print(
            f"   📊 Tokens: in={agg_input_tokens}, out={agg_output_tokens}, "
            f"total={agg_total}, {duration_ms}ms",
            flush=True,
        )
        
        # Cache status
        if agg_cache_read > 0:
            cache_pct = (agg_cache_read / agg_input_tokens * 100) if agg_input_tokens > 0 else 0
            logger.info(f"✅ Cache HIT: {agg_cache_read} tokens ({cache_pct:.1f}% of input)")
            print(f"   ✅ Cache HIT: {agg_cache_read} tokens ({cache_pct:.1f}%)", flush=True)
        elif agg_cache_creation > 0:
            logger.info(f"📝 Cache CREATED: {agg_cache_creation} tokens (5 min TTL)")
            print(f"   📝 Cache CREATED: {agg_cache_creation} tokens", flush=True)
        
        # ── Maliyet hesaplama ──
        pricing = self._get_model_pricing(model_id)
        price_input = pricing["input"]
        price_output = pricing["output"]
        price_cache_write = pricing["cache_write"]
        price_cache_read = pricing["cache_read"]
        
        uncached_input = max(0, agg_input_tokens - agg_cache_read - agg_cache_creation)
        cost_input = uncached_input * price_input / 1_000_000
        cost_output = agg_output_tokens * price_output / 1_000_000
        cost_cache_read = agg_cache_read * price_cache_read / 1_000_000
        cost_cache_write = agg_cache_creation * price_cache_write / 1_000_000
        total_cost = cost_input + cost_output + cost_cache_read + cost_cache_write
        
        logger.info(
            f"💰 Agent Cost: ${total_cost:.4f} "
            f"(input=${cost_input:.4f}, output=${cost_output:.4f}, "
            f"cache_read=${cost_cache_read:.4f}, cache_write=${cost_cache_write:.4f})"
        )
        print(
            f"   💰 Cost: ${total_cost:.4f} "
            f"(in=${cost_input:.4f}, out=${cost_output:.4f}, "
            f"cache_r=${cost_cache_read:.4f}, cache_w=${cost_cache_write:.4f})",
            flush=True,
        )
        
        # ── Langfuse ──
        if LANGFUSE_AVAILABLE and log_llm_usage:
            log_llm_usage(
                session_id=f"ocr-goal-driven-{file_name}",
                model=model_id or "claude-opus-4-5",
                input_tokens=agg_input_tokens,
                output_tokens=agg_output_tokens,
                cached_tokens=agg_cache_read,
                cost_usd=total_cost,
                latency_ms=duration_ms,
                step_name=f"ocr-gd-page-{page_num}",
                metadata={
                    "file_name": file_name,
                    "page_num": page_num,
                    "prompt_mode": "goal_driven",
                    "iterations": iterations,
                    "tool_calls": tool_calls,
                    "total_tokens": agg_total,
                    "cache_creation": agg_cache_creation,
                    "cache_read": agg_cache_read,
                    "uncached_input": uncached_input,
                    "model_id": model_id,
                    "cost_breakdown": {
                        "input": cost_input,
                        "output": cost_output,
                        "cache_read": cost_cache_read,
                        "cache_write": cost_cache_write,
                    },
                },
            )

    # ========================================================================
    # TEXT MODE: Görsel yerine OCR metni işle
    # ========================================================================

    async def _process_text_mode(
        self,
        ocr_texts: List[str],
        file_name: str,
        file_id: Optional[int] = None,
        domain: Optional[str] = None,
        output_dir: Optional[str] = None,
        batch_size: int = 50,  # Artık kullanılmıyor, geriye uyumluluk için
    ) -> Dict[str, Any]:
        """
        OCR metinlerini tek seferde işle.

        GeminiOCRAgent'tan gelen OCR metinlerini alır ve
        tümünü tek bir LLM çağrısında işler.

        Args:
            ocr_texts: Sayfa bazlı OCR metinleri
            file_name: Dosya adı
            file_id: Tracking için
            domain: Domain (opsiyonel)
            output_dir: JSON çıktısı için dizin
            batch_size: Kullanılmıyor (geriye uyumluluk)

        Returns:
            JSON output
        """
        from prompts import get_domain
        from src.shared.langfuse_client import get_langfuse, flush_langfuse

        domain = domain or get_domain()
        target_company = self._extract_company_from_filename(file_name)
        model_name = os.environ.get("OCR_VISION_MODEL", "claude-opus-4.5")
        total_pages = len(ocr_texts)

        logger.info(
            f"🚀 Starting TEXT MODE: model={model_name}, file={file_name}, "
            f"target={target_company}, pages={total_pages}"
        )
        print(
            f"\n{'='*60}\n"
            f"🚀 TEXT MODE START\n"
            f"   Model: {model_name}\n"
            f"   Target: {target_company}\n"
            f"   Pages: {total_pages}\n"
            f"{'='*60}",
            flush=True,
        )

        # Langfuse trace
        langfuse = get_langfuse()
        trace = None
        if langfuse:
            try:
                trace = langfuse.start_span(
                    name="text_mode_ocr",
                    input={
                        "file_name": file_name,
                        "page_count": total_pages,
                        "target_company": target_company,
                    },
                    metadata={
                        "domain": domain,
                        "file_id": file_id,
                        "model": model_name,
                        "mode": "text",
                    },
                )
            except Exception as e:
                logger.warning(f"Langfuse trace failed: {e}")

        try:
            start_time = time.time()

            # Tüm OCR metinlerini tek seferde işle
            result = await self._process_text_batch(
                ocr_texts=ocr_texts,
                target_company=target_company,
                model_name=model_name,
                file_name=file_name,
            )

            duration = time.time() - start_time

            # Sonucu işle
            page_data = result.get("data", {})
            found = page_data.get("found", False)

            if found:
                # Chunk'lardan markdown oluştur (legacy uyumluluk için)
                markdown_parts = []
                for chunk in page_data.get("chunks", []):
                    markdown_parts.append(chunk.get("text", ""))
                markdown_text = "\n\n".join(markdown_parts)

                output = {
                    "data": page_data,
                    "markdown": markdown_text,
                    "metadata": {
                        "target_company": target_company,
                        "pages_processed": total_pages,
                        "total_pages": total_pages,
                        "model": model_name,
                        "output_format": "json",
                        "mode": "text",
                    },
                    "status": "success",
                }

                # JSON çıktısını dosyaya kaydet
                if output_dir:
                    self._save_json_output(output, file_name, output_dir)
            else:
                output = {
                    "data": {"found": False, "target_company": target_company},
                    "markdown": "",
                    "metadata": {
                        "target_company": target_company,
                        "pages_processed": total_pages,
                        "total_pages": total_pages,
                        "model": model_name,
                        "output_format": "json",
                        "mode": "text",
                    },
                    "status": "success",
                }

            if trace:
                try:
                    trace.update(output=output, metadata={"status": "success"})
                    trace.end()
                    flush_langfuse()
                except Exception:
                    pass

            # Output summary
            chunks_count = len(page_data.get("chunks", []))
            nodes_count = len(page_data.get("nodes", []))
            rels_count = len(page_data.get("relationships", []))

            print(
                f"\n{'='*60}\n"
                f"✅ TEXT MODE COMPLETE ({duration:.1f}s)\n"
                f"   Found: {found}\n"
                f"   Chunks: {chunks_count}, Nodes: {nodes_count}, Relationships: {rels_count}\n"
                f"   Pages: {total_pages}\n"
                f"{'='*60}\n",
                flush=True,
            )
            logger.info(
                f"✅ Text mode completed: {chunks_count} chunks, {nodes_count} nodes, "
                f"{rels_count} relationships, {total_pages} pages, {duration:.1f}s"
            )

            return output

        except Exception as e:
            logger.error(f"❌ Text mode failed: {e}", exc_info=True)
            if trace:
                try:
                    trace.update(level="ERROR", status_message=str(e))
                    trace.end()
                    flush_langfuse()
                except Exception:
                    pass
            return {"data": {}, "metadata": {}, "status": "error", "error": str(e)}

    async def _process_text_batch(
        self,
        ocr_texts: List[str],
        target_company: str,
        model_name: str,
        file_name: str = "unknown",
    ) -> Dict[str, Any]:
        """
        OCR metinlerini tek seferde işle.

        Args:
            ocr_texts: Tüm OCR metinleri (sayfa bazlı)
            target_company: Hedef şirket
            model_name: Model adı
            file_name: Dosya adı

        Returns:
            {"data": {...}}
        """
        # OCR metinlerini birleştir (sayfa 1'den başla)
        merged_text = self._merge_ocr_texts(ocr_texts, start_page=1)
        total_pages = len(ocr_texts)

        logger.info(
            f"📄 Processing text: {total_pages} pages, {len(merged_text)} chars"
        )

        # Text mode için prompt oluştur
        prompt = self._build_text_mode_prompt(
            target_company=target_company,
            ocr_text=merged_text,
        )

        print(f"   🤖 Calling {model_name} (text mode)...", flush=True)

        # LLM'e gönder (görsel olmadan, sadece metin)
        response_text = await self._call_llm_text(
            prompt=prompt,
            model_name=model_name,
            page_range=f"1-{total_pages}",
            file_name=file_name,
        )

        # Yanıtı parse et
        if not response_text:
            logger.warning(f"Empty response from {model_name}")
            return {"data": {"found": False}}

        return self._parse_json_response(response_text)

    def _merge_ocr_texts(self, ocr_texts: List[str], start_page: int = 1) -> str:
        """
        OCR metinlerini tek metin olarak birleştir.

        Args:
            ocr_texts: Sayfa bazlı OCR metinleri
            start_page: Başlangıç sayfa numarası

        Returns:
            Birleştirilmiş metin
        """
        parts = []
        for i, text in enumerate(ocr_texts):
            page_num = start_page + i
            if text and text.strip():
                parts.append(f"[[PAGE:{page_num}]]\n{text.strip()}\n")

        return "\n".join(parts)

    def _build_text_mode_prompt(
        self,
        target_company: str,
        ocr_text: str,
    ) -> str:
        """
        Text mode için user prompt oluştur.
        
        System prompt'ta chunking, entity ve relationship kuralları tanımlı.
        User prompt sadece hedef şirket ve OCR metnini içerir.

        Args:
            target_company: Hedef şirket
            ocr_text: Birleştirilmiş OCR metni ([[PAGE:X]] marker'lı)

        Returns:
            Oluşturulan prompt
        """
        return f"""# Hedef Şirket: {target_company}

## OCR Metni
{ocr_text}
"""

    async def _call_llm_text(
        self,
        prompt: str,
        model_name: str,
        page_range: str = "1",
        file_name: str = "unknown",
    ) -> str:
        """
        LLM'e görsel olmadan sadece metin gönder.

        Text mode için - görsel işleme yapmadan LLM çağrısı.
        Model-agnostic: Anthropic, OpenAI, Google destekler.

        Args:
            prompt: Tam prompt (OCR metni dahil)
            model_name: Model adı
            page_range: Sayfa aralığı (loglama için)
            file_name: Dosya adı (Langfuse için)

        Returns:
            LLM yanıtı
        """
        if not self._llm_model:
            raise RuntimeError("LLM model not initialized. Call initialize() first.")

        from langchain_core.messages import SystemMessage, HumanMessage

        # Text mode için özel system prompt yükle (görsel tool'lar olmadan)
        system_prompt = self._get_system_prompt(text_mode=True)

        logger.info(f"📦 Text mode system prompt: {len(system_prompt)} chars")
        print(f"   📦 Text mode system prompt: {len(system_prompt)} chars", flush=True)

        # System message with cache_control
        system_message = SystemMessage(
            content=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral", "ttl": "5m"},
                },
            ]
        )

        # User message - sadece metin (görsel yok)
        user_message = HumanMessage(
            content=[
                {"type": "text", "text": prompt},
            ]
        )

        messages = [system_message, user_message]

        start_time = time.time()

        try:
            # Langfuse callback handler
            callbacks = []
            if LANGFUSE_AVAILABLE and get_langfuse_callback_handler:
                langfuse_handler = get_langfuse_callback_handler(
                    session_id=f"ocr-text-{file_name}",
                    trace_name=f"ocr-text-{file_name}-p{page_range}",
                    tags=["ocr", "text-mode", "claude"],
                    metadata={"file_name": file_name, "page_range": page_range},
                )
                if langfuse_handler:
                    callbacks.append(langfuse_handler)

            response = await self._llm_model.ainvoke(
                messages,
                config={"callbacks": callbacks} if callbacks else None,
            )

            duration_ms = int((time.time() - start_time) * 1000)

            # Usage metadata
            usage_metadata = getattr(response, "usage_metadata", None) or {}
            input_tokens = usage_metadata.get("input_tokens", 0) or 0
            output_tokens = usage_metadata.get("output_tokens", 0) or 0
            total_tokens = usage_metadata.get("total_tokens", 0) or 0

            input_token_details = usage_metadata.get("input_token_details", {}) or {}
            cache_read = input_token_details.get("cache_read", 0) or 0
            cache_creation = input_token_details.get("cache_creation", 0) or 0

            logger.info(
                f"📊 Tokens: input={input_tokens}, output={output_tokens}, total={total_tokens}, "
                f"cache_read={cache_read}, cache_creation={cache_creation}, {duration_ms}ms"
            )
            print(
                f"   📊 Tokens: in={input_tokens}, out={output_tokens}, "
                f"cache_read={cache_read}, {duration_ms}ms",
                flush=True,
            )

            # Langfuse logging
            if LANGFUSE_AVAILABLE and log_llm_usage:
                response_metadata = getattr(response, "response_metadata", None) or {}
                model_id = response_metadata.get("model", self._model_name)

                pricing = self._get_model_pricing(model_id)
                uncached_input = max(0, input_tokens - cache_read - cache_creation)
                cost_input = uncached_input * pricing["input"] / 1_000_000
                cost_output = output_tokens * pricing["output"] / 1_000_000
                cost_cache_read = cache_read * pricing["cache_read"] / 1_000_000
                cost_cache_write = cache_creation * pricing["cache_write"] / 1_000_000
                total_cost = cost_input + cost_output + cost_cache_read + cost_cache_write

                log_llm_usage(
                    session_id=f"ocr-text-{file_name}",
                    model=model_id or "claude-opus-4-5",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cached_tokens=cache_read,
                    cost_usd=total_cost,
                    latency_ms=duration_ms,
                    step_name=f"ocr-text-pages-{page_range}",
                    metadata={
                        "file_name": file_name,
                        "page_range": page_range,
                        "mode": "text",
                        "cache_creation": cache_creation,
                    },
                )

            # Response içeriği
            if hasattr(response, "content"):
                content = response.content

                if isinstance(content, str):
                    return content

                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            return block.get("text", "")
                        elif isinstance(block, str):
                            return block

            return ""

        except Exception as e:
            logger.error(f"❌ Claude text-only call failed: {e}")
            raise

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
            
            # Markdown dosyası oluştur (chunk text'lerini birleştir)
            md_file = output_path / f"{base_name}.md"
            chunks = output.get("data", {}).get("chunks", [])
            
            if chunks:
                # Chunk'ları position'a göre sırala ve text'leri birleştir
                sorted_chunks = sorted(chunks, key=lambda c: c.get("position", 0))
                markdown_parts = [chunk.get("text", "") for chunk in sorted_chunks]
                markdown_content = "\n\n---\n\n".join(markdown_parts)
                
                with open(md_file, "w", encoding="utf-8") as f:
                    f.write(markdown_content)
                
                logger.info(f"📄 Markdown saved: {md_file} ({len(chunks)} chunks)")
                print(f"📄 Markdown saved: {md_file} ({len(chunks)} chunks)", flush=True)
            
        except Exception as e:
            logger.error(f"❌ Failed to save output: {e}")
    
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
        # Önce kapalı code block dene
        json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if json_match:
            json_text = json_match.group(1).strip()
        else:
            # Kapanış ``` yoksa, açılıştan sonrasını al
            if text.startswith("```json"):
                json_text = text[7:].strip()  # "```json" = 7 karakter
            elif text.startswith("```"):
                json_text = text[3:].strip()  # "```" = 3 karakter
            
            # Sonda kalan ``` varsa temizle
            if json_text.endswith("```"):
                json_text = json_text[:-3].strip()
        
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
        
        # ── Devam kontrolü ──
        # 1. LLM'in JSON'da döndürdüğü is_complete alanını öncelikle kullan
        # 2. Ek güvenlik: TÜM chunk'ları devam ifadesi için tara (son chunk değil, hepsi)
        continuation_note = ""
        is_complete = data.get("is_complete", True)  # LLM'den gelen değer
        
        # LLM is_complete=false dediyse, zaten devam ediyor
        if not is_complete:
            continuation_note = "LLM: İlan devam ediyor"
        
        # Ek güvenlik: Tüm chunk metinlerinde devam ifadesi ara
        chunks = data.get("chunks", [])
        devam_patterns = [
            r"[Dd]evamı\s+(\d+)\.\s*[Ss]ayfada",
            r"[Dd]evamı\s+[Ss]ayfa\s+(\d+)",
            r"\([Dd]evamı\s+var\)",
        ]
        
        for chunk in chunks:
            chunk_text = chunk.get("text", "")
            for pattern in devam_patterns:
                match = re.search(pattern, chunk_text)
                if match:
                    is_complete = False
                    if match.groups():
                        continuation_note = f"Sayfa {match.group(1)}'de devam ediyor"
                    else:
                        continuation_note = "İlan devam ediyor"
                    break
            if not is_complete and continuation_note:
                break
        
        # is_complete alanını JSON'dan kaldır (pipeline'a geçmemeli)
        data.pop("is_complete", None)
        
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
    skill_id: Optional[str] = None,
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
        skill_id: Ontology DB'den yüklenecek skill ID (Agent Builder)
    """
    print(
        f"[VISION_OCR] Called with {len(image_list)} images, file={file_name}, skill={skill_id}",
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
            skill_id=skill_id,
        )

    try:
        loop = asyncio.get_running_loop()
        import nest_asyncio

        nest_asyncio.apply()
        return loop.run_until_complete(_run())
    except RuntimeError:
        return asyncio.run(_run())


def process_agentic_ocr_text_mode(
    ocr_texts: List[str],
    file_name: str,
    file_id: Optional[int] = None,
    domain: Optional[str] = None,
    output_dir: Optional[str] = None,
    batch_size: int = 50,
    skill_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Sync wrapper for AgenticOCR.process() in TEXT MODE.
    Sequential pipeline için: GeminiOCRAgent sonrası çağrılır.
    
    Args:
        ocr_texts: Sayfa bazlı OCR metinleri (GeminiOCRAgent çıktısı)
        file_name: Dosya adı
        file_id: Tracking için
        domain: Domain (opsiyonel)
        output_dir: JSON çıktısı için dizin (opsiyonel)
        batch_size: Batch boyutu (default: 50 sayfa)
        skill_id: Workspace skill ID (Ontology DB'den skill yukler)
    
    Returns:
        process_agentic_ocr ile aynı formatta sonuç
    """
    print(
        f"[TEXT_MODE_OCR] Called with {len(ocr_texts)} pages, file={file_name}, skill={skill_id}",
        flush=True,
    )

    async def _run():
        ocr = await get_agentic_ocr()
        return await ocr.process(
            text_mode=True,
            ocr_texts=ocr_texts,
            file_name=file_name,
            file_id=file_id,
            domain=domain,
            output_dir=output_dir,
            batch_size=batch_size,
            skill_id=skill_id,
        )

    try:
        loop = asyncio.get_running_loop()
        import nest_asyncio

        nest_asyncio.apply()
        return loop.run_until_complete(_run())
    except RuntimeError:
        return asyncio.run(_run())
