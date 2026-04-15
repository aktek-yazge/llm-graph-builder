"""
OCR Tools
=========

GeminiOCR bridge tool'lari + native extraction.

Iki mod destekler:
1. DIRECT MODE: Kullanici lokal dosya verdiyse, in-process islenir.
   PyMuPDF + GeminiOCRAgent dogrudan async cagrilir.
   Extraction, agent'in kendi ontolojisi + LLM ile yapilir (AgenticOCR KULLANILMAZ).
2. CELERY MODE: Dosya MinIO/storage'daysa, Celery worker'a gonderilir.
   Gercek task isimleri: workspace.extract_images, workspace.ocr_pages,
   workspace.extract_entities, workspace.process_document.

Mod secimi otomatik: dosya yolu lokal dosya sistemi mi, MinIO key mi?
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

CELERY_WORKER_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", "..", "celery_worker")
)


def _ensure_celery_worker_importable():
    """celery_worker/src modulleri import edilebilir olsun."""
    worker_src = os.path.join(CELERY_WORKER_ROOT, "src")
    if worker_src not in sys.path:
        sys.path.insert(0, CELERY_WORKER_ROOT)
        sys.path.insert(0, worker_src)


def _is_local_file(path: str) -> bool:
    return os.path.exists(path)


def _are_local_files(paths: list[str]) -> bool:
    return all(os.path.exists(p) for p in paths) if paths else False


def _get_llm():
    """OCR ve extraction icin LLM olustur (gorsel isleme optimize)."""
    provider = os.getenv("EVOLVING_OCR_PROVIDER", "google").lower()
    model_name = os.getenv("EVOLVING_OCR_MODEL", "gemini-2.5-flash")

    if provider in ("google", "gemini"):
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model=model_name, temperature=0.1)
    elif provider in ("openai", "gpt"):
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=model_name, temperature=0.1)
    else:
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=model_name, temperature=0.1)


def create_ocr_tools(agent_id: str, celery_app=None, store=None, memory=None) -> list:
    """OCR bridge tool'larini olustur.

    Args:
        agent_id: Agent ID
        celery_app: Celery app instance (MinIO dosyalari icin gerekli)
        store: KnowledgeStore instance (extraction icin ontoloji erisimi)
        memory: AgentMemory instance (extraction prompt uretimi)
    """

    @tool
    async def extract_images_from_pdf(file_path: str) -> str:
        """PDF dosyasini PNG sayfa goruntulerine donustur.
        Pipeline'in ilk adimi. OCR oncesi gerekli.
        Lokal dosyalar dogrudan islenir, MinIO dosyalari Celery uzerinden.

        Args:
            file_path: PDF dosya yolu (lokal path veya MinIO key)
        """
        if _is_local_file(file_path):
            return await _extract_images_direct(file_path)

        if not celery_app:
            return f"Dosya lokal bulunamadi ({file_path}) ve Celery yapilandirilmamis."
        return await _extract_images_celery(file_path)

    @tool
    async def run_ocr(image_paths: list[str], file_name: str = "") -> str:
        """Sayfa goruntuleri uzerinde Gemini OCR calistir ve markdown metin cikar.
        Lokal PNG dosyalari dogrudan islenir, uzak dosyalar Celery uzerinden.

        Args:
            image_paths: PNG sayfa goruntu dosya yollari listesi
            file_name: Dosya adi (opsiyonel, hedef sirket tespiti icin)
        """
        if _are_local_files(image_paths):
            return await _run_ocr_direct(image_paths, file_name)

        if not celery_app:
            return "Goruntu dosyalari lokal bulunamadi ve Celery yapilandirilmamis."
        return await _run_ocr_celery(image_paths, file_name)

    @tool
    async def ocr_and_analyze(file_path: str, file_name: str = "") -> str:
        """PDF dosyasini OCR ile oku ve icerigini analiz et. Extraction YAPMAZ.
        Belgenin ne icerdigi hakkinda bilgi verir. Kullanici ile tartismak,
        ontoloji tasarlamak icin ideal ilk adim.
        Tam OCR metni knowledge store'a kaydedilir, burada ozet doner.

        Args:
            file_path: PDF dosya yolu
            file_name: Dosya adi (opsiyonel)
        """
        try:
            actual_name = file_name or os.path.basename(file_path)

            img_result_str = await _extract_images_direct(file_path)
            try:
                img_result = json.loads(img_result_str)
            except (json.JSONDecodeError, ValueError):
                return f"Image extraction basarisiz: {img_result_str}"

            if "images" not in img_result:
                return f"Image extraction basarisiz: {img_result_str}"

            ocr_result_str = await _run_ocr_direct(img_result["images"], actual_name)
            try:
                ocr_result = json.loads(ocr_result_str)
            except (json.JSONDecodeError, ValueError):
                return f"OCR basarisiz: {ocr_result_str}"

            merged_text = ocr_result.get("merged_text", "")
            if not merged_text:
                return f"OCR basarisiz veya bos metin. {ocr_result.get('page_count', 0)} sayfa islendi."

            doc_key = f"ocr_{uuid.uuid4().hex[:8]}"
            if store:
                try:
                    await store.upsert(
                        agent_id, "ocr_result", doc_key,
                        {"file_name": actual_name, "file_path": file_path,
                         "ocr_text": merged_text, "page_count": img_result.get("page_count", 0),
                         "total_chars": len(merged_text)},
                        source="ocr_and_analyze",
                    )
                except Exception as exc:
                    logger.warning("OCR result store failed: %s", exc)

            preview_len = 3000
            preview = merged_text[:preview_len]
            if len(merged_text) > preview_len:
                preview += f"\n\n... (toplam {len(merged_text)} karakter, ilk {preview_len} gosterildi)"

            return json.dumps({
                "status": "success",
                "file_name": actual_name,
                "page_count": img_result.get("page_count", 0),
                "total_chars": len(merged_text),
                "ocr_text_preview": preview,
                "stored_as": doc_key,
                "next_step": "Yukaridaki metni analiz et. Ne turu entity ve relationship'ler goruyorsun? Kullaniciya oner ve tartis.",
            }, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("ocr_and_analyze error: %s", e, exc_info=True)
            return f"OCR analiz hatasi: {e}"

    @tool
    async def get_ocr_text(doc_key: str) -> str:
        """ocr_and_analyze ile kaydedilen tam OCR metnini getir.
        ocr_and_analyze sonucundaki 'stored_as' alanindaki key'i kullan.

        Args:
            doc_key: OCR sonuc anahtari (orn: 'ocr_abc12345')
        """
        if not store:
            return "Store yapilandirmasi eksik."
        try:
            entries = await store.get_all(agent_id, "ocr_result")
            for entry in entries:
                if entry.get("key") == doc_key:
                    val = entry.get("value", {})
                    if isinstance(val, str):
                        val = json.loads(val)
                    return val.get("ocr_text", "OCR metni bulunamadi.")
            return f"'{doc_key}' anahtarli OCR sonucu bulunamadi."
        except Exception as e:
            return f"OCR metni yuklenirken hata: {e}"

    @tool
    async def run_extraction(
        ocr_text: str,
        file_name: str = "",
    ) -> str:
        """OCR metni uzerinde mevcut ontoloji ile entity/relationship cikar.
        Agent'in kendi ontolojisini kullanarak LLM ile extraction yapar.
        ONEMLI: Ontoloji bos ise calismaz - once entity/relationship tanimlari ekleyin.

        Args:
            ocr_text: GeminiOCR'dan gelen OCR metni
            file_name: Dosya adi (opsiyonel)
        """
        return await _run_extraction_native(ocr_text, file_name)

    @tool
    async def run_full_pipeline(
        file_path: str,
        file_name: str = "",
    ) -> str:
        """Tam pipeline: PDF -> Sayfa Goruntuleri -> OCR -> Entity Extraction.
        ONEMLI: Bu tool ontoloji hazir olduguinda kullanilmali.
        Ontoloji bos ise once ocr_and_analyze ile belgeyi oku ve ontoloji olustur.
        Lokal dosya ise adim adim dogrudan islenir.
        MinIO dosyasi ise workspace.process_document Celery task'ina gonderilir.

        Args:
            file_path: PDF dosya yolu (lokal path veya MinIO key)
            file_name: Dosya adi (opsiyonel)
        """
        if _is_local_file(file_path):
            return await _run_pipeline_direct(file_path, file_name)

        if not celery_app:
            return f"Dosya lokal bulunamadi ({file_path}) ve Celery yapilandirilmamis."
        return await _run_pipeline_celery(file_path, file_name)

    @tool
    async def test_extraction_on_sample(sample_text: str) -> str:
        """Mevcut ontoloji ile verilen metin uzerinde extraction testi yap.
        Dogrudan LLM ile test eder. Ontolojinin ne kadar iyi calistigini gormek icin kullan.

        Args:
            sample_text: Test edilecek metin ornegi (OCR metni veya duz metin)
        """
        return await _run_extraction_native(sample_text, "")

    # ─── NATIVE EXTRACTION (AgenticOCR yerine) ──────────────────

    async def _run_extraction_native(ocr_text: str, file_name: str) -> str:
        """Agent'in kendi ontolojisi + LLM ile extraction yap."""
        try:
            if not store or not memory:
                return "Extraction icin store/memory yapilandirmasi eksik."

            ontology = await store.load_ontology(agent_id)
            if ontology.is_empty:
                return (
                    "Ontoloji bos, extraction yapilamaz. "
                    "Once add_entity_class ve add_relationship_predicate ile "
                    "entity ve relationship tanimlari ekleyin."
                )

            wiki_context = ""
            try:
                from ..wiki_store import WikiStore
                wiki = WikiStore(store)
                wiki_context = await wiki.build_extraction_context(agent_id)
            except Exception:
                pass

            extraction_prompt = memory.build_extraction_prompt(ontology, wiki_context=wiki_context)
            llm = _get_llm()

            from langchain_core.messages import HumanMessage, SystemMessage

            text_chunk = ocr_text[:12000]
            user_msg = f"Bu belge metninden bilgi cikart:\n\n{text_chunk}"
            if file_name:
                user_msg = f"Dosya: {file_name}\n\n{user_msg}"

            response = await llm.ainvoke([
                SystemMessage(content=extraction_prompt),
                HumanMessage(content=user_msg),
            ])

            raw = response.content
            if isinstance(raw, list):
                raw = "".join(
                    item.get("text", str(item)) if isinstance(item, dict) else str(item)
                    for item in raw
                )

            json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
            if json_match:
                raw = json_match.group(1)

            try:
                parsed = json.loads(raw)
                nodes = parsed.get("nodes", [])
                edges = parsed.get("edges", parsed.get("relationships", []))
                return json.dumps({
                    "mode": "native",
                    "node_count": len(nodes),
                    "relationship_count": len(edges),
                    "nodes": nodes[:30],
                    "relationships": edges[:30],
                    "status": "success",
                }, ensure_ascii=False, indent=2)
            except (json.JSONDecodeError, ValueError):
                return json.dumps({
                    "mode": "native",
                    "status": "partial",
                    "raw_response": raw[:3000],
                    "note": "LLM yapilandirilmis JSON donduremedi, ham yanit asagida.",
                }, ensure_ascii=False, indent=2)

        except Exception as e:
            logger.error("native extraction error: %s", e, exc_info=True)
            return f"Extraction hatasi: {e}"

    # ─── DIRECT MODE implementations ─────────────────────────────

    async def _extract_images_direct(file_path: str) -> str:
        try:
            import fitz  # PyMuPDF

            doc = fitz.open(file_path)
            output_dir = tempfile.mkdtemp(prefix="evolving_ocr_")
            image_paths = []

            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                pix = page.get_pixmap(dpi=200)
                img_path = os.path.join(output_dir, f"page_{page_num + 1:03d}.png")
                pix.save(img_path)
                image_paths.append(img_path)

            doc.close()
            return json.dumps({
                "mode": "direct",
                "page_count": len(image_paths),
                "images": image_paths,
                "output_dir": output_dir,
            }, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("extract_images_direct error: %s", e)
            return f"Image extraction hatasi: {e}"

    async def _run_ocr_direct(image_paths: list[str], file_name: str) -> str:
        try:
            _ensure_celery_worker_importable()
            from agents.gemini_ocr_agent import GeminiOCRAgent

            agent = GeminiOCRAgent()
            await agent.initialize()

            output_dir = tempfile.mkdtemp(prefix="evolving_ocr_out_")
            result = await agent.process(
                image_list=image_paths,
                output_dir=output_dir,
                file_name=file_name or None,
            )

            ocr_texts = result.get("ocr_texts", [])
            merged = result.get("merged_text", "")
            page_count = result.get("page_count", 0)

            return json.dumps({
                "mode": "direct",
                "page_count": page_count,
                "total_chars": result.get("total_chars", 0),
                "ocr_texts_count": len(ocr_texts),
                "merged_text_preview": merged[:1000],
                "merged_text": merged,
                "output_dir": output_dir,
                "token_usage": result.get("token_usage", {}),
            }, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("run_ocr_direct error: %s", e)
            return f"OCR hatasi: {e}"

    async def _run_pipeline_direct(file_path: str, file_name: str) -> str:
        try:
            img_result_str = await _extract_images_direct(file_path)
            try:
                img_result = json.loads(img_result_str)
            except (json.JSONDecodeError, ValueError):
                return f"Image extraction basarisiz: {img_result_str}"

            if "images" not in img_result:
                return f"Image extraction basarisiz: {img_result_str}"

            ocr_result_str = await _run_ocr_direct(img_result["images"], file_name)
            try:
                ocr_result = json.loads(ocr_result_str)
            except (json.JSONDecodeError, ValueError):
                return f"OCR basarisiz: {ocr_result_str}"

            merged_text = ocr_result.get("merged_text", "")
            if not merged_text:
                return f"OCR basarisiz veya bos metin: {ocr_result_str}"

            extraction_result_str = await _run_extraction_native(merged_text, file_name)
            try:
                extraction_data = json.loads(extraction_result_str)
            except (json.JSONDecodeError, ValueError):
                extraction_data = {"raw": extraction_result_str}

            return json.dumps({
                "mode": "direct",
                "pipeline": "PDF -> Images -> OCR -> Extraction",
                "pages": img_result.get("page_count", 0),
                "ocr_chars": ocr_result.get("total_chars", 0),
                "extraction": extraction_data,
            }, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("run_pipeline_direct error: %s", e)
            return f"Pipeline hatasi: {e}"

    # ─── CELERY MODE implementations ─────────────────────────────

    async def _extract_images_celery(file_path: str) -> str:
        try:
            doc_id = f"ev-{uuid.uuid4().hex[:8]}"
            task = celery_app.send_task(
                "workspace.extract_images",
                args=[doc_id, file_path],
                queue="workspace",
            )
            result = task.get(timeout=120)
            if isinstance(result, dict):
                result["mode"] = "celery"
            return json.dumps(result, ensure_ascii=False, indent=2) if isinstance(result, dict) else str(result)
        except Exception as e:
            logger.error("extract_images_celery error: %s", e)
            return f"Image extraction hatasi (Celery): {e}"

    async def _run_ocr_celery(image_paths: list[str], file_name: str) -> str:
        try:
            doc_id = f"ev-{uuid.uuid4().hex[:8]}"
            task = celery_app.send_task(
                "workspace.ocr_pages",
                args=[doc_id, image_paths, file_name],
                queue="workspace",
            )
            result = task.get(timeout=180)
            if isinstance(result, dict):
                merged = result.get("merged_text", "")
                return json.dumps({
                    "mode": "celery",
                    "page_count": result.get("page_count", 0),
                    "merged_text_preview": merged[:1000],
                    "merged_text": merged,
                }, ensure_ascii=False, indent=2)
            return str(result)
        except Exception as e:
            logger.error("run_ocr_celery error: %s", e)
            return f"OCR hatasi (Celery): {e}"

    async def _run_pipeline_celery(file_path: str, file_name: str) -> str:
        try:
            doc_id = f"ev-{uuid.uuid4().hex[:8]}"
            skill_id = f"agent-{agent_id}-extraction"
            task = celery_app.send_task(
                "workspace.process_document",
                args=[doc_id, file_path, file_name],
                kwargs={"skill_id": skill_id, "ocr_mode": "hybrid"},
                queue="workspace",
            )
            result = task.get(timeout=300)
            if isinstance(result, dict):
                result["mode"] = "celery"
            return json.dumps(result, ensure_ascii=False, indent=2) if isinstance(result, dict) else str(result)
        except Exception as e:
            logger.error("run_pipeline_celery error: %s", e)
            return f"Pipeline hatasi (Celery): {e}"

    tools = [
        extract_images_from_pdf,
        run_ocr,
        ocr_and_analyze,
        get_ocr_text,
        run_extraction,
        run_full_pipeline,
        test_extraction_on_sample,
    ]

    return tools
