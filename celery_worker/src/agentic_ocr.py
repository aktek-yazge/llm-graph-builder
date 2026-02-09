# -*- coding: utf-8 -*-
"""
Generic Agentic OCR Framework

Bu modül, LangChain + MCP entegrasyonu ile prompt-driven agentic OCR sağlar.

Features:
    - Page-by-page: Her sayfa ayrı işlenir (context limiti aşılmaz)
    - Coordinate tracking: Kırpılan/atlanan bölgeler koordinat bazlı takip
    - Continuation: Sayfalar arası devam notu ile bağlam korunur
    - Observable: Langfuse ile full tracing

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
import time
import logging
import asyncio
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


class AgenticOCR:
    """
    Generic Agentic OCR Framework.
    
    Page-by-page processing: Her sayfa ayrı agent çağrısı ile işlenir.
    Coordinate tracking: Sayfa içi kırpma/atlama takibi.
    Continuation notes: Sayfalar arası bağlam korunur.
    """
    
    def __init__(self):
        self.mcp_client = None
        self.mcp_tools = []
        self.custom_tools = []
        self.agent = None
        self._initialized = False
    
    async def initialize(self) -> None:
        """
        MCP client başlat ve tool'ları al.
        
        Raises:
            ImportError: Gerekli paketler yüklü değilse
        """
        if self._initialized:
            return
        
        try:
            from langchain_mcp_adapters.client import MultiServerMCPClient
            from src.mcp_config import get_mcp_server_config_stdio_only
            from src.ocr_tools import get_ocr_tools
            
            # MCP client başlat (sadece stdio tools - ImageSorcery)
            config = get_mcp_server_config_stdio_only()
            logger.info(f"📡 Initializing MCP client with config: {list(config.keys())}")
            
            self.mcp_client = MultiServerMCPClient(config)
            self.mcp_tools = await self.mcp_client.get_tools()
            
            logger.info(f"🔧 MCP tools loaded: {[t.name for t in self.mcp_tools]}")
            
            # Custom tool'ları al
            self.custom_tools = get_ocr_tools()
            logger.info(f"🔧 Custom tools loaded: {[t.name for t in self.custom_tools]}")
            
            self._initialized = True
            
        except ImportError as e:
            logger.error(f"❌ Missing package: {e}")
            raise
        except Exception as e:
            logger.error(f"❌ MCP initialization failed: {e}")
            raise
    
    async def process(
        self,
        image_list: List[str],
        file_name: str,
        file_id: Optional[int] = None,
        domain: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Sayfaları tek tek işle.
        
        Args:
            image_list: İşlenecek image path'leri
            file_name: Dosya adı (prompt'ta kullanılır)
            file_id: Langfuse tracking için
            domain: Domain (None ise get_domain() kullanılır)
        
        Returns:
            {
                "markdown": "...",
                "metadata": {...},
                "status": "success" | "error"
            }
        """
        if not self._initialized:
            await self.initialize()
        
        from prompts import get_domain
        from src.ocr_tools import reset_page_state
        from src.shared.langfuse_client import get_langfuse, flush_langfuse
        
        # Domain belirleme
        domain = domain or get_domain()
        session_id = f"file_{file_id}" if file_id else f"ocr_{int(time.time())}"
        
        # Hedef şirketi dosya adından çıkar
        target_company = self._extract_company_from_filename(file_name)
        
        logger.info(
            f"🚀 Starting AgenticOCR: domain={domain}, file={file_name}, "
            f"target={target_company}, pages={len(image_list)}"
        )
        
        # 📊 Langfuse: Ana trace başlat
        langfuse = get_langfuse()
        trace = None
        langfuse_handler = None
        
        if langfuse:
            try:
                from langfuse import propagate_attributes
                
                propagate_attributes(
                    session_id=session_id,
                    user_id=f"file_{file_id}" if file_id else None,
                    metadata={"domain": domain, "file_name": file_name, "target_company": target_company},
                )
                
                trace = langfuse.start_span(
                    name="agentic_ocr",
                    input={"file_name": file_name, "page_count": len(image_list), "target_company": target_company},
                    metadata={
                        "session_id": session_id,
                        "domain": domain,
                        "file_id": file_id,
                    },
                )
                
                from langfuse.langchain import CallbackHandler as LangfuseCallbackHandler
                langfuse_handler = LangfuseCallbackHandler()
            except Exception as e:
                logger.warning("Langfuse trace creation failed: %s", e)
        
        try:
            # Agent oluştur (bir kez)
            agent = await self._create_agent(langfuse_handler)
            
            # Sayfaları tek tek işle
            all_markdown = []
            continuation_note = ""
            pages_processed = 0
            
            sorted_images = sorted(image_list)
            
            for page_idx, page_path in enumerate(sorted_images):
                logger.info(f"📄 Processing page {page_idx + 1}/{len(sorted_images)}: {page_path}")
                
                # Her sayfa başında sayfa içi state'i sıfırla
                reset_page_state()
                
                # Sayfa prompt'unu hazırla
                page_prompt = self._build_page_prompt(
                    target_company=target_company,
                    continuation_note=continuation_note,
                    page_number=page_idx + 1,
                    total_pages=len(sorted_images),
                    image_path=page_path,
                )
                
                # Sayfayı işle
                result = await self._process_single_page(
                    agent=agent,
                    page_path=page_path,
                    prompt=page_prompt,
                    langfuse_handler=langfuse_handler,
                )
                
                pages_processed = page_idx + 1
                
                # Sonuçları topla
                page_markdown = result.get("markdown", "")
                if page_markdown:
                    all_markdown.append(page_markdown)
                    logger.info(f"   📝 Page {page_idx + 1}: {len(page_markdown)} chars extracted")
                
                # Devam notunu güncelle (sayfa arası aktarım)
                continuation_note = result.get("continuation_note", "")
                if continuation_note:
                    logger.info(f"   📝 Continuation note: {continuation_note[:50]}...")
                
                # İlan bitti mi?
                if result.get("is_complete"):
                    logger.info(f"✅ Target company content complete at page {page_idx + 1}")
                    break
            
            # Tüm sayfaları birleştir
            final_markdown = "\n\n[PAGE BREAK]\n\n".join(all_markdown)
            
            output = {
                "markdown": final_markdown,
                "metadata": {
                    "target_company": target_company,
                    "pages_processed": pages_processed,
                    "total_pages": len(sorted_images),
                },
                "status": "success",
            }
            
            # 📊 Langfuse: Trace tamamla
            if trace:
                try:
                    trace.update(
                        output=output,
                        metadata={"status": "success", "pages_processed": pages_processed},
                    )
                    trace.end()
                    flush_langfuse()
                except Exception as e:
                    logger.warning(f"⚠️ Langfuse trace update failed: {e}")
            
            logger.info(f"✅ AgenticOCR completed: {len(final_markdown)} chars, {pages_processed} pages")
            return output
            
        except Exception as e:
            logger.error(f"❌ AgenticOCR failed: {e}", exc_info=True)
            
            # 📊 Langfuse: Hata kaydet
            if trace:
                try:
                    trace.update(
                        level="ERROR",
                        status_message=str(e),
                    )
                    trace.end()
                    flush_langfuse()
                except Exception as lf_error:
                    logger.warning(f"⚠️ Langfuse error logging failed: {lf_error}")
            
            return {
                "markdown": "",
                "metadata": {},
                "status": "error",
                "error": str(e),
            }
    
    async def _create_agent(self, langfuse_handler=None):  # noqa: ARG002
        """ReAct agent oluştur."""
        from langchain_google_genai import ChatGoogleGenerativeAI
        from langgraph.prebuilt import create_react_agent
        
        model = ChatGoogleGenerativeAI(
            model="gemini-2.0-flash",
            temperature=0.1,
            google_api_key=os.environ.get("GEMINI_API_KEY"),
        )
        
        # Tüm tool'ları birleştir (MCP + custom)
        all_tools = self.mcp_tools + self.custom_tools
        logger.info(f"🔧 Total tools available: {[t.name for t in all_tools]}")
        
        agent = create_react_agent(
            model=model,
            tools=all_tools,
        )
        
        return agent
    
    async def _process_single_page(
        self,
        agent,
        page_path: str,
        prompt: str,
        langfuse_handler=None,
    ) -> Dict[str, Any]:
        """Tek sayfa işle."""
        
        image_content = await self._prepare_image_content(page_path)
        
        callbacks = [langfuse_handler] if langfuse_handler else None
        
        result = await agent.ainvoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            image_content,
                        ],
                    }
                ]
            },
            config={"callbacks": callbacks} if callbacks else None,
        )
        
        # finish_page tool sonucunu çıkar
        return self._extract_page_result(result)
    
    def _extract_page_result(self, agent_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Agent sonucundan finish_page sonucunu çıkar.
        
        Args:
            agent_result: LangGraph agent result
        
        Returns:
            {markdown, continuation_note, is_complete}
        """
        messages = agent_result.get("messages", [])
        
        # finish_page tool sonucunu bul
        for msg in messages:
            if hasattr(msg, "name") and msg.name == "finish_page":
                if hasattr(msg, "content"):
                    try:
                        import json
                        content = msg.content
                        if isinstance(content, str):
                            result = json.loads(content)
                        elif isinstance(content, dict):
                            result = content
                        else:
                            continue
                        
                        return {
                            "markdown": result.get("markdown", ""),
                            "continuation_note": result.get("continuation_note", ""),
                            "is_complete": result.get("is_complete", False),
                        }
                    except json.JSONDecodeError:
                        logger.warning(f"Failed to parse finish_page result: {msg.content}")
        
        # finish_page bulunamadı, son AI mesajından çıkar
        logger.warning("finish_page tool result not found, extracting from last AI message")
        
        for msg in reversed(messages):
            if hasattr(msg, "content") and hasattr(msg, "type") and msg.type == "ai":
                content = msg.content
                if isinstance(content, str):
                    return {
                        "markdown": content,
                        "continuation_note": "",
                        "is_complete": False,
                    }
        
        return {
            "markdown": "",
            "continuation_note": "",
            "is_complete": False,
        }
    
    def _extract_company_from_filename(self, file_name: str) -> str:
        """
        Dosya adından şirket adını çıkar.
        
        Args:
            file_name: Dosya adı (örn: "Aksa-09.03.2016-9028-GENEL KURUL")
        
        Returns:
            Şirket adı (örn: "Aksa")
        """
        # İlk tire veya rakamdan önceki kısmı al
        match = re.match(r"^([A-Za-zÇçĞğİıÖöŞşÜü\s]+)", file_name)
        if match:
            company = match.group(1).strip()
            if company:
                return company
        
        # Fallback: ilk kelimeyi al
        parts = file_name.split("-")
        if parts:
            return parts[0].strip()
        
        return file_name
    
    def _build_page_prompt(
        self,
        target_company: str,
        continuation_note: str,
        page_number: int,
        total_pages: int,
        image_path: str,
    ) -> str:
        """Her sayfa için dinamik prompt oluştur."""
        
        prompt = f"""# Ticaret Sicil Gazetesi OCR

## HEDEF
Hedef şirket: {target_company}
Sayfa: {page_number}/{total_pages}
Image: {image_path}

## DEVAM NOTU
{continuation_note or "Yok (yeni başlangıç)"}

## ÇALIŞMA AKIŞI
1. Görüntüyü analiz et, hedef şirketin bölgelerini bul
2. Her bölge için:
   - Hedef şirkete ait DEĞİLSE → `mark_skipped(x1,y1,x2,y2,"açıklama")` 
   - Hedef şirkete aitse → `crop(input_path,x1,y1,x2,y2)` sonra `mark_cropped(x1,y1,x2,y2,"açıklama")`
3. `get_page_log()` ile durumu kontrol et (aynı koordinata gitme)
4. Tüm bölgeler işlenince → `finish_page(markdown, continuation_note, is_complete)`

## KURALLAR
- SADECE hedef şirketin içeriğini kırp
- Başka şirketleri `mark_skipped` ile işaretle ve o koordinatlara gitme
- "Devamı X. sayfada" görürsen continuation_note'a yaz
- Hedef şirketin ilanı tamamen bittiyse is_complete=True
- Kırpılan içerikleri birleştirip markdown olarak döndür

## TOOL'LAR
- `mark_skipped(x1,y1,x2,y2,desc)` - Atlanan koordinatı kaydet
- `crop(input_path,x1,y1,x2,y2)` - Bölüm kırp (ImageSorcery)
- `mark_cropped(x1,y1,x2,y2,desc)` - Kırpılan koordinatı kaydet
- `get_page_log()` - Hangi koordinatlar işlendi?
- `finish_page(markdown,continuation_note,is_complete)` - Sayfa tamamla
"""
        return prompt
    
    async def _prepare_image_content(self, image_path: str) -> Dict[str, Any]:
        """
        Image'ı LangChain message formatına hazırla.
        
        Args:
            image_path: Image dosya yolu
        
        Returns:
            LangChain image content dict
        """
        import base64
        
        with open(image_path, "rb") as f:
            image_data = f.read()
        
        if image_path.lower().endswith(".png"):
            mime_type = "image/png"
        elif image_path.lower().endswith((".jpg", ".jpeg")):
            mime_type = "image/jpeg"
        else:
            mime_type = "image/png"
        
        base64_image = base64.b64encode(image_data).decode("utf-8")
        
        return {
            "type": "image_url",
            "image_url": {
                "url": f"data:{mime_type};base64,{base64_image}"
            }
        }
    
    async def close(self) -> None:
        """MCP client'ı kapat."""
        if self.mcp_client:
            try:
                await self.mcp_client.__aexit__(None, None, None)
            except Exception:
                pass
            self.mcp_client = None
            self._initialized = False


# Global instance (singleton pattern)
_agentic_ocr_instance: Optional[AgenticOCR] = None


async def get_agentic_ocr() -> AgenticOCR:
    """
    AgenticOCR singleton instance al.
    
    Returns:
        Initialized AgenticOCR instance
    """
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
) -> Dict[str, Any]:
    """
    Sync wrapper for AgenticOCR.process().
    
    Celery task'larından çağrılmak üzere sync fonksiyon.
    
    Args:
        image_list: İşlenecek image path'leri
        file_name: Dosya adı
        file_id: Langfuse tracking için
        domain: Domain (opsiyonel)
    
    Returns:
        OCR sonucu
    """
    async def _run():
        ocr = await get_agentic_ocr()
        return await ocr.process(
            image_list=image_list,
            file_name=file_name,
            file_id=file_id,
            domain=domain,
        )
    
    # Event loop kontrol
    try:
        loop = asyncio.get_running_loop()
        import nest_asyncio
        nest_asyncio.apply()
        return loop.run_until_complete(_run())
    except RuntimeError:
        return asyncio.run(_run())
