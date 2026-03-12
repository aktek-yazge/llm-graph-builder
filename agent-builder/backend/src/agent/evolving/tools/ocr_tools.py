"""
OCR Tools
=========

GeminiOCR + AgenticOCR bridge tool'lari.

Iki mod destekler:
1. DIRECT MODE: Kullanici lokal dosya verdiyse, in-process islenir.
   PyMuPDF + GeminiOCRAgent + AgenticOCR dogrudan async cagrilir.
2. CELERY MODE: Dosya MinIO/storage'daysa, Celery worker'a gonderilir.
   Gercek task isimleri: workspace.extract_images, workspace.ocr_pages,
   workspace.extract_entities, workspace.process_document.

Mod secimi otomatik: dosya yolu lokal dosya sistemi mi, MinIO key mi?
"""

from __future__ import annotations

import json
import logging
import os
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
    """Verilen yol lokal dosya sisteminde mi?"""
    return os.path.exists(path)


def _are_local_files(paths: list[str]) -> bool:
    """Verilen yollarin hepsi lokal dosya sisteminde mi?"""
    return all(os.path.exists(p) for p in paths) if paths else False


def create_ocr_tools(agent_id: str, celery_app=None) -> list:
    """OCR bridge tool'larini olustur.

    Args:
        agent_id: Agent ID
        celery_app: Celery app instance (MinIO dosyalari icin gerekli)
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
    async def run_extraction(
        ocr_text: str,
        file_name: str = "",
        skill_id: str = "",
    ) -> str:
        """OCR metni uzerinde AgenticOCR text_mode ile entity/relationship cikar.
        Metin zaten bellekte oldugu icin her zaman dogrudan islenir.

        Args:
            ocr_text: GeminiOCR'dan gelen OCR metni
            file_name: Dosya adi (opsiyonel)
            skill_id: Kullanilacak skill ID (bos ise agent'in kendi skill'i)
        """
        actual_skill_id = skill_id or f"agent-{agent_id}-extraction"
        return await _run_extraction_direct(ocr_text, file_name, actual_skill_id)

    @tool
    async def run_full_pipeline(
        file_path: str,
        file_name: str = "",
        skill_id: str = "",
    ) -> str:
        """Tam pipeline: PDF -> Sayfa Goruntuleri -> OCR -> Entity Extraction.
        Lokal dosya ise adim adim dogrudan islenir.
        MinIO dosyasi ise workspace.process_document Celery task'ina gonderilir.

        Args:
            file_path: PDF dosya yolu (lokal path veya MinIO key)
            file_name: Dosya adi (opsiyonel)
            skill_id: Kullanilacak skill ID (bos ise agent'in kendi skill'i)
        """
        actual_skill_id = skill_id or f"agent-{agent_id}-extraction"

        if _is_local_file(file_path):
            return await _run_pipeline_direct(file_path, file_name, actual_skill_id)

        if not celery_app:
            return f"Dosya lokal bulunamadi ({file_path}) ve Celery yapilandirilmamis."
        return await _run_pipeline_celery(file_path, file_name, actual_skill_id)

    @tool
    async def test_extraction_on_sample(sample_text: str) -> str:
        """Mevcut ontoloji ile verilen metin uzerinde extraction testi yap.
        AgenticOCR kullanmadan, dogrudan LLM ile test eder.
        Ontolojinin ne kadar iyi calistigini gormek icin kullan.

        Args:
            sample_text: Test edilecek metin ornegi (OCR metni veya duz metin)
        """
        try:
            from ..knowledge_store import KnowledgeStore
            from ..agent_memory import AgentMemory

            store = KnowledgeStore(_pg_ref[0])
            mem = AgentMemory(store)
            ontology = await store.load_ontology(agent_id)

            if ontology.is_empty:
                return "Ontoloji bos, once entity/relationship tanimlari ekleyin."

            extraction_prompt = mem.build_extraction_prompt(ontology)

            from langchain_core.messages import HumanMessage, SystemMessage

            provider = os.getenv("EVOLVING_LLM_PROVIDER", "google").lower()
            model_name = os.getenv("EVOLVING_LLM_MODEL", "gemini-2.5-flash")

            if provider in ("google", "gemini"):
                from langchain_google_genai import ChatGoogleGenerativeAI
                llm = ChatGoogleGenerativeAI(model=model_name, temperature=0.1)
            elif provider in ("openai", "gpt"):
                from langchain_openai import ChatOpenAI
                llm = ChatOpenAI(model=model_name, temperature=0.1)
            else:
                from langchain_anthropic import ChatAnthropic
                llm = ChatAnthropic(model=model_name, temperature=0.1)

            response = await llm.ainvoke([
                SystemMessage(content=extraction_prompt),
                HumanMessage(content=f"Bu belge metninden bilgi cikart:\n\n{sample_text[:8000]}"),
            ])

            return f"Extraction testi tamamlandi:\n\n{response.content}"
        except Exception as e:
            logger.error("test_extraction error: %s", e)
            return f"Test hatasi: {e}"

    # ─── PG reference (test_extraction icin) ──────────────────────

    _pg_ref = [None]

    def set_pg(pg):
        _pg_ref[0] = pg

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

    async def _run_extraction_direct(ocr_text: str, file_name: str, skill_id: str) -> str:
        try:
            _ensure_celery_worker_importable()
            from agentic_ocr import AgenticOCR

            ocr = AgenticOCR()
            await ocr.initialize()

            result = await ocr.process(
                text_mode=True,
                ocr_texts=[ocr_text],
                file_name=file_name or "test-document",
                skill_id=skill_id or None,
            )

            data = result.get("data", result)
            if isinstance(data, dict):
                nodes = data.get("nodes", [])
                rels = data.get("relationships", data.get("edges", []))
                return json.dumps({
                    "mode": "direct",
                    "node_count": len(nodes),
                    "relationship_count": len(rels),
                    "nodes": nodes[:20],
                    "relationships": rels[:20],
                    "status": result.get("status", "success"),
                }, ensure_ascii=False, indent=2)
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("run_extraction_direct error: %s", e)
            return f"Extraction hatasi: {e}"

    async def _run_pipeline_direct(file_path: str, file_name: str, skill_id: str) -> str:
        try:
            img_result_str = await _extract_images_direct(file_path)
            img_result = json.loads(img_result_str)

            if "images" not in img_result:
                return f"Image extraction basarisiz: {img_result_str}"

            ocr_result_str = await _run_ocr_direct(img_result["images"], file_name)
            ocr_result = json.loads(ocr_result_str)

            merged_text = ocr_result.get("merged_text", "")
            if not merged_text:
                return f"OCR basarisiz veya bos metin: {ocr_result_str}"

            extraction_result_str = await _run_extraction_direct(merged_text, file_name, skill_id)

            return json.dumps({
                "mode": "direct",
                "pipeline": "PDF -> Images -> OCR -> Extraction",
                "pages": img_result.get("page_count", 0),
                "ocr_chars": ocr_result.get("total_chars", 0),
                "extraction": json.loads(extraction_result_str),
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

    async def _run_pipeline_celery(file_path: str, file_name: str, skill_id: str) -> str:
        try:
            doc_id = f"ev-{uuid.uuid4().hex[:8]}"
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
        run_extraction,
        run_full_pipeline,
        test_extraction_on_sample,
    ]

    for t in tools:
        t._set_pg = set_pg

    return tools
