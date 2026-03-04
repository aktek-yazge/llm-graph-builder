"""
OCR/Extraction Bridge
=====================

Belge isleme pipeline'i:
- PDF -> PNG donusum (PyMuPDF)
- PNG -> Text (Gemini OCR via LangChain)
- celery_worker OCR/extraction bilesenlerine kopru

PyMuPDF hicbir zaman dogrudan metin cikarma icin kullanilmaz.
Metin cikarma her zaman Gemini OCR uzerinden yapilir.

Kullanim:
    bridge = OCRBridge(neo4j_driver)
    text = await bridge.process_document_for_text("/path/to/doc.pdf")
    result = await bridge.process_document(images, skill_id="skill-123")
    preview = bridge.preview_entities(result)
    await bridge.commit_to_knowledge_db(result)
"""

import base64
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    _default_celery_path = str(Path(__file__).resolve().parents[4] / "celery_worker")
except (IndexError, ValueError):
    _default_celery_path = "/app/celery_worker"

CELERY_WORKER_PATH = os.getenv("CELERY_WORKER_PATH", _default_celery_path)

_celery_worker_available = False

IMAGE_RESOLUTION_SCALE = 2.0


def _ensure_celery_worker_path():
    """celery_worker/src'yi sys.path'e ekle."""
    global _celery_worker_available
    if _celery_worker_available:
        return True

    worker_path = Path(CELERY_WORKER_PATH)
    if not worker_path.exists():
        logger.warning("celery_worker path not found: %s", worker_path)
        return False

    worker_str = str(worker_path)
    if worker_str not in sys.path:
        sys.path.insert(0, worker_str)

    _celery_worker_available = True
    return True


# =========================================================================
# PDF -> PNG (PyMuPDF) - Metin cikarma icin DEGIL, sadece goruntu donusumu
# =========================================================================

def extract_pdf_images(file_path: str, output_dir: Optional[str] = None) -> List[str]:
    """
    PyMuPDF ile PDF sayfalarini PNG goruntulere donusturur.

    PyMuPDF sadece PDF->PNG donusumu icin kullanilir.
    Metin cikarma her zaman Gemini OCR uzerinden yapilir.

    Args:
        file_path: PDF dosyasinin yolu
        output_dir: Cikti klasoru (None ise gecici dizin olusturulur)

    Returns:
        PNG dosya yollarinin listesi
    """
    try:
        import fitz
    except ImportError:
        logger.error("PyMuPDF (fitz) not installed - pip install PyMuPDF")
        return []

    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="ocr_bridge_")

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    try:
        start = time.time()
        doc = fitz.open(str(file_path))
        doc_stem = Path(file_path).stem
        saved = []

        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            mat = fitz.Matrix(IMAGE_RESOLUTION_SCALE, IMAGE_RESOLUTION_SCALE)
            pix = page.get_pixmap(matrix=mat)

            img_path = output_path / f"{doc_stem}_page_{page_num + 1:03d}.png"
            pix.save(str(img_path))
            pix = None
            saved.append(str(img_path))

        doc.close()
        logger.info(
            "PDF->PNG: %d pages in %.1fs (%s)",
            len(saved), time.time() - start, file_path,
        )
        return saved

    except Exception as e:
        logger.error("PDF->PNG failed for %s: %s", file_path, e)
        return []


# =========================================================================
# Gemini OCR via LangChain (celery_worker bagimliligi olmadan)
# =========================================================================

async def _langchain_gemini_ocr(image_paths: List[str], file_name: str = "") -> Dict[str, Any]:
    """
    LangChain Google Genai ile goruntu OCR.

    celery_worker'a bagimli olmadan dogrudan Gemini Vision API kullanir.
    """
    model_name = os.getenv("OCR_VISION_MODEL", "gemini-2.5-flash")
    api_key = os.getenv("GEMINI_API_KEY", os.getenv("GOOGLE_API_KEY", ""))

    if not api_key:
        return {"error": "GEMINI_API_KEY or GOOGLE_API_KEY not set"}

    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
        from langchain_core.messages import HumanMessage

        llm = ChatGoogleGenerativeAI(model=model_name, google_api_key=api_key)
        ocr_texts = []

        for img_path in image_paths:
            p = Path(img_path)
            if not p.exists():
                logger.warning("Image not found: %s", img_path)
                ocr_texts.append("")
                continue

            img_data = base64.b64encode(p.read_bytes()).decode("utf-8")
            suffix = p.suffix.lower().lstrip(".")
            mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(suffix, "image/png")

            msg = HumanMessage(content=[
                {"type": "text", "text": "Bu belge goruntusundeki tum metni cikart. Sadece metni dondur, aciklama ekleme."},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{img_data}"}},
            ])

            response = await llm.ainvoke([msg])
            ocr_texts.append(response.content or "")

        merged = "\n\n".join(t for t in ocr_texts if t)
        return {"ocr_texts": ocr_texts, "merged_text": merged, "pages": len(image_paths)}

    except Exception as e:
        logger.error("LangChain Gemini OCR failed: %s", e)
        return {"error": str(e)}


class OCRBridge:
    """
    celery_worker OCR bilesenleri ile agent runtime arasinda kopru.

    Sync ve async islemler icin wrapper saglar.
    Knowledge DB'ye yazmadan once preview/onay mekanizmasi sunar.
    """

    def __init__(self, neo4j_driver=None, neo4j_uri: str = "", neo4j_user: str = "", neo4j_password: str = ""):
        self.neo4j_driver = neo4j_driver
        self.neo4j_uri = neo4j_uri or os.getenv("NEO4J_URI", "")
        self.neo4j_user = neo4j_user or os.getenv("NEO4J_USERNAME", "neo4j")
        self.neo4j_password = neo4j_password or os.getenv("NEO4J_PASSWORD", "")

        self._schema_provider = None
        self._graph_executor = None

    @property
    def available(self) -> bool:
        return _ensure_celery_worker_path()

    # =========================================================================
    # SCHEMA PROVIDER
    # =========================================================================

    def get_schema_provider(self):
        """Neo4jSchemaProvider instance al."""
        if not self.available:
            return None

        if self._schema_provider is None:
            try:
                from src.neo4j_schema_provider import Neo4jSchemaProvider
                self._schema_provider = Neo4jSchemaProvider(self.neo4j_driver)
            except ImportError:
                logger.warning("Neo4jSchemaProvider import failed")
                return None

        return self._schema_provider

    async def get_existing_schema(self) -> Dict[str, Any]:
        """Mevcut Knowledge DB semasini al."""
        provider = self.get_schema_provider()
        if not provider:
            return {"labels": [], "relationship_types": [], "error": "Schema provider unavailable"}

        try:
            schema = await provider.get_schema()
            return schema
        except Exception as e:
            logger.error("Failed to get schema: %s", e)
            return {"labels": [], "relationship_types": [], "error": str(e)}

    # =========================================================================
    # DOCUMENT -> TEXT (ana giris noktasi)
    # =========================================================================

    async def process_document_for_text(
        self,
        file_path: str,
        file_name: str = "",
        max_pages: int = 10,
    ) -> Dict[str, Any]:
        """
        Belge dosyasindan metin cikarir.

        PDF: PyMuPDF ile PNG'ye donusturur -> Gemini OCR ile metin cikarir
        Image: Dogrudan Gemini OCR ile metin cikarir

        PyMuPDF hicbir zaman dogrudan metin cikarma icin kullanilmaz.

        Args:
            file_path: Belge dosya yolu (PDF veya goruntu)
            file_name: Dosya adi (loglama icin)
            max_pages: Analiz icin max sayfa (sample analiz icin sinir)

        Returns:
            {"merged_text": "...", "pages": N, "method": "..."}
        """
        p = Path(file_path)
        if not p.exists():
            return {"error": f"File not found: {file_path}", "merged_text": ""}

        fname = file_name or p.name
        suffix = p.suffix.lower()

        if suffix == ".pdf":
            images = extract_pdf_images(file_path)
            if not images:
                return {"error": "PDF->PNG conversion failed", "merged_text": ""}

            if len(images) > max_pages:
                logger.info("Limiting OCR to %d/%d pages for %s", max_pages, len(images), fname)
                images = images[:max_pages]

            ocr_result = await self.run_gemini_ocr(images, file_name=fname)
            ocr_result["method"] = "pdf_pymupdf_gemini_ocr"
            return ocr_result

        elif suffix in (".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp"):
            ocr_result = await self.run_gemini_ocr([file_path], file_name=fname)
            ocr_result["method"] = "image_gemini_ocr"
            return ocr_result

        else:
            return {"error": f"Unsupported file type: {suffix}", "merged_text": ""}

    # =========================================================================
    # GEMINI OCR
    # =========================================================================

    async def run_gemini_ocr(
        self,
        images: List[str],
        file_name: str = "",
    ) -> Dict[str, Any]:
        """
        Goruntu dosyalari uzerinde Gemini OCR calistir.

        Oncelik: celery_worker process_gemini_ocr -> fallback: LangChain Gemini Vision

        Args:
            images: Goruntu dosya yollari
            file_name: Kaynak dosya adi

        Returns:
            {"ocr_texts": [...], "merged_text": "...", ...}
        """
        if self.available:
            try:
                from src.agents import process_gemini_ocr
                result = process_gemini_ocr(images=images, file_name=file_name)
                if result and result.get("merged_text"):
                    return result
            except ImportError:
                logger.info("celery_worker GeminiOCR not available, using LangChain fallback")
            except Exception as e:
                logger.warning("celery_worker GeminiOCR failed (%s), using LangChain fallback", e)

        logger.info("Using LangChain Gemini OCR for %d images", len(images))
        return await _langchain_gemini_ocr(images, file_name=file_name)

    # =========================================================================
    # AGENTIC OCR
    # =========================================================================

    async def run_agentic_ocr(
        self,
        images: Optional[List[str]] = None,
        text: Optional[str] = None,
        skill_id: Optional[str] = None,
        domain: str = "",
        mode: str = "vision",
    ) -> Dict[str, Any]:
        """
        AgenticOCR ile OCR + entity extraction calistir.

        Args:
            images: Goruntu dosya yollari (vision mode)
            text: Onceden OCR'lenmis metin (text mode)
            skill_id: Kullanilacak skill ID (Agent Builder'dan)
            domain: Domain adi
            mode: 'vision' veya 'text'

        Returns:
            {"nodes": [...], "relationships": [...], "chunks": [...]}
        """
        if not self.available:
            return {"error": "celery_worker not available"}

        try:
            if mode == "text" and text:
                from src.agentic_ocr import process_agentic_ocr_text_mode
                result = process_agentic_ocr_text_mode(
                    text=text,
                    graph=self.neo4j_driver,
                    skill_id=skill_id,
                    domain=domain,
                )
            else:
                from src.agentic_ocr import process_agentic_ocr
                result = process_agentic_ocr(
                    images=images or [],
                    graph=self.neo4j_driver,
                    skill_id=skill_id,
                    domain=domain,
                )
            return result
        except ImportError as e:
            logger.error("AgenticOCR import failed: %s", e)
            return {"error": f"AgenticOCR not available: {e}"}
        except Exception as e:
            logger.error("AgenticOCR failed: %s", e)
            return {"error": str(e)}

    # =========================================================================
    # HYBRID PIPELINE
    # =========================================================================

    async def run_hybrid_pipeline(
        self,
        images: List[str],
        skill_id: Optional[str] = None,
        domain: str = "",
        file_name: str = "",
    ) -> Dict[str, Any]:
        """
        Hybrid pipeline: GeminiOCR (ucuz OCR) -> AgenticOCR text mode (extraction).
        """
        ocr_result = await self.run_gemini_ocr(images, file_name)
        if "error" in ocr_result:
            return ocr_result

        merged_text = ocr_result.get("merged_text", "")
        if not merged_text:
            return {"error": "OCR sonucu bos"}

        extraction_result = await self.run_agentic_ocr(
            text=merged_text,
            skill_id=skill_id,
            domain=domain,
            mode="text",
        )

        return {
            "ocr": ocr_result,
            "extraction": extraction_result,
            "pipeline": "hybrid",
        }

    # =========================================================================
    # PREVIEW & COMMIT
    # =========================================================================

    def preview_entities(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extraction sonuclarini kullaniciya gosterilecek formatta hazirla.
        Commit oncesi onay icin kullanilir.
        """
        extraction = result.get("extraction", result)
        nodes = extraction.get("nodes", [])
        relationships = extraction.get("relationships", [])

        node_summary = {}
        for node in nodes:
            label = node.get("label", node.get("type", "Unknown"))
            if label not in node_summary:
                node_summary[label] = []
            node_summary[label].append({
                "id": node.get("id", ""),
                "name": node.get("name", node.get("properties", {}).get("name", "")),
            })

        return {
            "entity_count": len(nodes),
            "relationship_count": len(relationships),
            "entity_types": node_summary,
            "relationships": [
                {
                    "type": r.get("type", ""),
                    "source": r.get("source", r.get("start", "")),
                    "target": r.get("target", r.get("end", "")),
                }
                for r in relationships[:20]
            ],
        }

    async def commit_to_knowledge_db(
        self,
        result: Dict[str, Any],
        document_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Onaylanan entity/relationship'leri Knowledge DB'ye yaz.
        GenericGraphExecutor kullanir (MERGE bazli, dedup).
        """
        if not self.available:
            return {"error": "celery_worker not available"}

        extraction = result.get("extraction", result)
        nodes = extraction.get("nodes", [])
        relationships = extraction.get("relationships", [])
        chunks = extraction.get("chunks", [])

        try:
            from src.generic_graph_executor import GenericGraphExecutor

            executor = GenericGraphExecutor(self.neo4j_driver)
            write_result = await executor.create_graph_from_llm_output(
                nodes=nodes,
                relationships=relationships,
                chunks=chunks,
                document_id=document_id,
            )

            try:
                from ..dependencies import get_event_store
                from ..event_store.models import EventType, GraphEvent

                es = await get_event_store()
                for node in nodes:
                    await es.record_event(GraphEvent(
                        tenant_id="default-tenant",
                        event_type=EventType.CREATE_NODE,
                        entity_type=node.get("label", "Unknown"),
                        entity_id=node.get("id", ""),
                        after_state=node,
                        metadata={"source": "ocr_bridge", "document_id": document_id or ""},
                    ))
                for rel in relationships:
                    await es.record_event(GraphEvent(
                        tenant_id="default-tenant",
                        event_type=EventType.CREATE_RELATIONSHIP,
                        entity_type=rel.get("type", "RELATED"),
                        entity_id=f"{rel.get('source', '')}->{rel.get('target', '')}",
                        after_state=rel,
                        metadata={"source": "ocr_bridge", "document_id": document_id or ""},
                    ))
            except Exception as ev_err:
                logger.warning("Event recording after commit failed: %s", ev_err)

            return {
                "committed": True,
                "nodes_written": len(nodes),
                "relationships_written": len(relationships),
                "details": write_result,
            }
        except ImportError:
            logger.error("GenericGraphExecutor import failed")
            return {"error": "GenericGraphExecutor not available"}
        except Exception as e:
            logger.error("Commit failed: %s", e)
            return {"error": str(e)}

    # =========================================================================
    # ENTITY RESOLUTION
    # =========================================================================

    async def resolve_entities(self, document_id: Optional[str] = None) -> Dict[str, Any]:
        """Post-processing entity dedup calistir."""
        if not self.available:
            return {"error": "celery_worker not available"}

        try:
            from src.entity_resolver import EntityResolver

            resolver = EntityResolver(self.neo4j_driver)
            result = await resolver.resolve(document_id=document_id)
            return {"resolved": True, "details": result}
        except ImportError:
            return {"error": "EntityResolver not available"}
        except Exception as e:
            return {"error": str(e)}
