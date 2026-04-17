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
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from ..ontology_model import AgentOntology

logger = logging.getLogger(__name__)

CELERY_WORKER_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", "..", "celery_worker")
)

# Çıkarılan haber/yapılandırılmış metinlerin .md dosyalarını saklamak için.
# Kalıcı disk; agent başına alt klasör. Restart sonrasında kayboluyor olmasını
# önlemek için /tmp dışında bir yere environment ile yönlendirilebilir.
EXTRACTION_DIR = Path(os.getenv("EXTRACTION_DIR", "/tmp/evolving-extractions"))


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


def _split_into_pages(text: str) -> list[str]:
    """[[PAGE:N]] isaretcilerine gore metni sayfalara bol."""
    parts = re.split(r"\[\[PAGE:\d+\]\]", text)
    pages = [p.strip() for p in parts if p.strip()]
    if not pages:
        chunk_size = 4000
        pages = [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]
    return pages


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
    async def run_ocr(
        image_paths: list[str],
        file_name: str = "",
        file_path: str = "",
        resource_id: str = "",
    ) -> str:
        """Sayfa goruntuleri uzerinde Gemini OCR calistir ve markdown metin cikar.
        Lokal PNG dosyalari dogrudan islenir, uzak dosyalar Celery uzerinden.

        ⚠️ ONEMLI: Bu DUSUK SEVIYE bir tool'dur. Genelde `ocr_and_analyze` kullan.
        Bu tool'a sadece `extract_images_from_pdf` cikti'sini dogrudan OCR'lamak
        gerekiyorsa (orn. ocr_and_analyze fallback durumu) elle cagir.

        ✓ OTOMATIK PERSIST: Eger `file_path` parametresi verirsen, OCR sonucu
        PostgreSQL'e `ocr_result` olarak kaydedilir ve donus icinde `doc_key`
        bulunur. Bu sayede Resources panelinde "OCR Tamam" gorunur ve
        `read_ocr_pages(doc_key, ...)` ile sonra okuyabilirsin.
        `file_path` vermezsen sonuc SADECE tool yanitinda kalir, PG'ye
        kaydedilmez (eski davranis, geriye uyumluluk icin).

        Args:
            image_paths: PNG sayfa goruntu dosya yollari listesi
            file_name: Dosya adi (opsiyonel, hedef sirket tespiti icin)
            file_path: Orijinal PDF/dosya yolu. Verilirse OCR sonucu PG'ye kaydedilir.
            resource_id: Kaynak dosya ID'si (list_resources'dan). file_path
                ile birlikte verilirse Resources panelindeki kayitla eslesir.
        """
        if _are_local_files(image_paths):
            ocr_result_str = await _run_ocr_direct(image_paths, file_name)
        elif celery_app:
            ocr_result_str = await _run_ocr_celery(image_paths, file_name)
        else:
            return "Goruntu dosyalari lokal bulunamadi ve Celery yapilandirilmamis."

        if not file_path or not store:
            return ocr_result_str

        try:
            ocr_result = json.loads(ocr_result_str)
        except (json.JSONDecodeError, ValueError):
            return ocr_result_str

        merged_text = ocr_result.get("merged_text", "")
        if not merged_text:
            return ocr_result_str

        actual_name = file_name or os.path.basename(file_path)
        doc_key = f"ocr_{uuid.uuid4().hex[:8]}"
        try:
            await store.upsert(
                agent_id, "ocr_result", doc_key,
                {
                    "file_name": actual_name,
                    "file_path": file_path,
                    "resource_id": resource_id,
                    "ocr_text": merged_text,
                    "page_count": ocr_result.get("page_count", len(image_paths)),
                    "total_chars": len(merged_text),
                    "token_usage": ocr_result.get("token_usage") or {},
                    "duration_ms": ocr_result.get("duration_ms") or 0,
                },
                source="run_ocr",
            )
            ocr_result["doc_key"] = doc_key
            ocr_result["persisted"] = True
            ocr_result["file_name"] = actual_name
            ocr_result["resource_id"] = resource_id
            ocr_result["next_step"] = (
                f"OCR sonucu PG'ye kaydedildi (doc_key={doc_key}). Artik "
                "read_ocr_pages(doc_key, offset, limit) ile okuyabilirsin."
            )
        except Exception as exc:
            logger.warning("run_ocr persist failed: %s", exc)
            ocr_result["persisted"] = False
            ocr_result["persist_error"] = str(exc)

        return json.dumps(ocr_result, ensure_ascii=False, indent=2)

    @tool
    async def ocr_and_analyze(file_path: str, file_name: str = "", resource_id: str = "") -> str:
        """PDF dosyasini OCR ile oku ve kaydet. Metin DONMEZ, sadece metadata doner.
        Context sisirmeden cok sayfalik belgeleri islemeye uygun.
        OCR sonrasi kullaniciya belge hakkinda bilgi ver ve ne yapmak istedigini sor.

        Sonuc metadata icindeki doc_key ile belgeyi sayfa sayfa okuyabilirsin:
          - read_ocr_pages(doc_key, offset=0, limit=5)
          - list_ocr_documents()

        ONEMLI: Otomatik extraction YAPMA. Kullaniciya belgenin genel bilgisini
        sun ve nasil ilerlemek istedigini sor.

        Args:
            file_path: PDF dosya yolu
            file_name: Dosya adi (opsiyonel)
            resource_id: Kaynak dosya ID'si (list_resources'dan alinir, OCR sonucunu dosyaya baglar)
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

            ocr_result_str = await _run_ocr_direct(img_result["images"], "")
            try:
                ocr_result = json.loads(ocr_result_str)
            except (json.JSONDecodeError, ValueError):
                return f"OCR basarisiz: {ocr_result_str}"

            merged_text = ocr_result.get("merged_text", "")
            if not merged_text:
                images = img_result.get("images", [])
                images_hint = ", ".join(images[:3])
                if len(images) > 3:
                    images_hint += f", ... (+{len(images) - 3} daha)"
                return json.dumps({
                    "status": "empty_ocr",
                    "page_count": ocr_result.get("page_count", 0),
                    "image_paths": images,
                    "message": (
                        "OCR basarisiz veya bos metin. Sayfa goruntuleri elde edildi "
                        f"({len(images)} adet) ama Gemini bos metin dondu. "
                        "FALLBACK: `run_ocr(image_paths=<image_paths>, file_name=..., "
                        "file_path=<orijinal_pdf>, resource_id=...)` cagir — "
                        "file_path verirsen sonuc PG'ye kaydedilir."
                    ),
                    "fallback_hint": (
                        f"run_ocr(image_paths=[{images_hint}], file_name='{actual_name}', "
                        f"file_path='{file_path}', resource_id='{resource_id}')"
                    ),
                }, ensure_ascii=False, indent=2)

            page_count = img_result.get("page_count", 0)
            token_usage = ocr_result.get("token_usage") or {}
            duration_ms = ocr_result.get("duration_ms") or 0

            doc_key = f"ocr_{uuid.uuid4().hex[:8]}"
            if store:
                try:
                    await store.upsert(
                        agent_id, "ocr_result", doc_key,
                        {"file_name": actual_name, "file_path": file_path,
                         "resource_id": resource_id,
                         "ocr_text": merged_text, "page_count": page_count,
                         "total_chars": len(merged_text),
                         "token_usage": token_usage,
                         "duration_ms": duration_ms},
                        source="ocr_and_analyze",
                    )
                except Exception as exc:
                    logger.warning("OCR result store failed: %s", exc)

            first_line = merged_text.split("\n", 1)[0][:120] if merged_text else ""

            return json.dumps({
                "status": "success",
                "file_name": actual_name,
                "doc_key": doc_key,
                "resource_id": resource_id,
                "page_count": page_count,
                "total_chars": len(merged_text),
                "duration_ms": duration_ms,
                "token_usage": token_usage,
                "first_line": first_line,
                "next_step": (
                    "Belge kaydedildi. Kullaniciya belge bilgisini sun ve ne yapmak "
                    "istedigini sor. Detay icin read_ocr_pages(doc_key, offset, limit) kullan."
                ),
            }, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("ocr_and_analyze error: %s", e, exc_info=True)
            return f"OCR analiz hatasi: {e}"

    # ─── Paginated OCR reading ────────────────────────────────────

    @tool
    async def read_ocr_pages(doc_key: str, offset: int = 0, limit: int = 5) -> str:
        """Kaydedilmis OCR belgesini sayfa sayfa oku.
        Context'i sismeden buyuk belgeleri incelemeye uygun.

        Args:
            doc_key: OCR sonuc anahtari (orn: 'ocr_abc12345')
            offset: Baslangic sayfa numarasi (0-indexed)
            limit: Okunacak sayfa sayisi (varsayilan 5)
        """
        if not store:
            return "Store yapilandirmasi eksik."
        try:
            entries = await store.get_all(agent_id, "ocr_result")
            val = None
            for entry in entries:
                if entry.get("key") == doc_key:
                    val = entry.get("value", {})
                    if isinstance(val, str):
                        val = json.loads(val)
                    break
            if val is None:
                return f"'{doc_key}' anahtarli OCR sonucu bulunamadi."

            merged_text = val.get("ocr_text", "")
            if not merged_text:
                return "OCR metni bos."

            pages = _split_into_pages(merged_text)
            total_pages = len(pages)

            if offset >= total_pages:
                return json.dumps({
                    "doc_key": doc_key,
                    "total_pages": total_pages,
                    "offset": offset,
                    "returned": 0,
                    "text": "",
                    "has_more": False,
                }, ensure_ascii=False)

            selected = pages[offset:offset + limit]
            returned_text = "\n\n".join(selected)
            has_more = (offset + limit) < total_pages

            return json.dumps({
                "doc_key": doc_key,
                "file_name": val.get("file_name", ""),
                "total_pages": total_pages,
                "offset": offset,
                "limit": limit,
                "returned": len(selected),
                "has_more": has_more,
                "text": returned_text,
            }, ensure_ascii=False, indent=2)
        except Exception as e:
            return f"OCR sayfa okuma hatasi: {e}"

    @tool
    async def list_ocr_documents() -> str:
        """Bu agent'a ait tum OCR edilmis belgeleri listele.
        Her belge icin doc_key, dosya adi, sayfa sayisi ve karakter sayisi doner.
        Metin donmez — sadece metadata.
        """
        if not store:
            return "Store yapilandirmasi eksik."
        try:
            entries = await store.get_all(agent_id, "ocr_result")
            docs = []
            for entry in entries:
                val = entry.get("value", {})
                if isinstance(val, str):
                    try:
                        val = json.loads(val)
                    except Exception:
                        continue
                docs.append({
                    "doc_key": entry.get("key", ""),
                    "file_name": val.get("file_name", ""),
                    "page_count": val.get("page_count", 0),
                    "total_chars": val.get("total_chars", 0),
                })
            return json.dumps({
                "count": len(docs),
                "documents": docs,
            }, ensure_ascii=False, indent=2)
        except Exception as e:
            return f"OCR dokuman listesi hatasi: {e}"

    @tool
    async def get_ocr_text(doc_key: str) -> str:
        """Tam OCR metnini getir. DIKKAT: Buyuk belgelerde context'i sisirabilir.
        Mumkunse read_ocr_pages kullan.

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

    # ─── STRUCTURED RECORD EXTRACTION (domain-agnostic, saved as .md) ──

    @tool
    async def extract_records_from_ocr(
        doc_key: str,
        target_class: str = "",
        filter_query: str = "",
        custom_instructions: str = "",
    ) -> str:
        """OCR edilmis belgeden, AGENT'IN ONTOLOJISINE gore yapilandirilmis kayitlari .md
        dosyalari olarak cikar. Bu tool DOMAIN-AGNOSTIC'tir: "sirket", "police", "hasta
        kaydi", "sozlesme" — ne olursa olsun ontoloji ne tanimliyorsa onu cikartir.

        ⚠️ FAZ 3 TOOL'U — GOAL-DRIVEN: Bu tool sadece kullanici acikca "X kayitlarini
        cikart" dedikten sonra cagirilir. Faz 1 (WIKI) ve Faz 2 (ONTOLOJI) tamamlanmis
        olmalidir. Cagrida kullanicinin HEDEFINI yansit (hangi entity, hangi filtre).

        ONEMLI: Sohbete uzun extraction metni YAZMA. Tool'u cagir, kullaniciya kac kayit
        cikarildigini soyle, "Kaynaklar panelinden onizleyebilirsin" de.

        TEMEL KURALLAR:
        1. DOMAIN-AGNOSTIC: Hangi entity'lerin cikarilacagini ONTOLOJI belirler. Sen
           "sirket", "ilan", "sicil_no" gibi DOMAIN'E OZGU TERIMLER UYDURMA.
        2. ONTOLOJI BOSSA calismaz: Once kullaniciyla domain'i tartis, wiki yaz,
           ontolojiyi formalize et (Faz 1+2). Sonra bu tool'u cagir.
        3. GOAL-DRIVEN: target_class ve filter_query kullanicinin SOYLEDIGI hedefe gore
           doldurulmali. Kullanici "Toplantilari listele" demisse target_class='Toplanti'.
           "tum entity'leri cikart" gibi acik bir komut YOKSA target_class boş gecip
           tum sinif tarama YAPMA — pahali ve genelde kullanicinin niyeti degil.

        NE ZAMAN CAGIR:
        - Kullanici "X kayitlarini cikart", "belgedeki Y'leri ayikla", "sirketleri
          listele", "policeleri yapilandir" gibi STRUCTURED EXTRACTION isterse.
        - Onceden bir ontoloji kurulmus olmali; yoksa once onu kurmasi gerektigini
          kullaniciya soyle.

        NE ZAMAN CAGIRMA:
        - Kullanici sadece "ozet ver" / "bu belgede neler var" demisse → `read_ocr_pages`
          ile oku ve natural language cevapla.
        - Faz 1 (wiki) veya Faz 2 (ontoloji) henuz tamamlanmamissa.
        - Kullanicinin hedefi belirsizse → ONCE SOR ("hangi sinifi, hangi filtreyle?").

        Args:
            doc_key: OCR sonuc anahtari (orn: 'ocr_3de54...'). list_ocr_documents'tan ogren.
            target_class: Cikartilacak entity sinifi adi (ontolojiden). Bos -> tum siniflar.
            filter_query: Sonuclari filtrelemek icin serbest metin (orn. 'Zeytinliada',
                'police no 12345'). Esleme: case-insensitive substring, kayit adinda
                veya properties degerlerinde aranir. Bos -> tum kayitlar.
            custom_instructions: Kullanicinin verdigi ek talimatlar (orn. 'sadece
                tasfiye iliskili olanlari al'). Ontoloji'ye dokunmaz, sadece bu cagri
                icin LLM'e ekstra context.
        """
        if not store:
            return "Store yapilandirmasi eksik."
        try:
            ontology = await store.load_ontology(agent_id)
            if ontology.is_empty:
                return json.dumps({
                    "status": "ontology_empty",
                    "message": (
                        "Ontoloji bos — yapilandirilmis cikartim yapilamaz. Once "
                        "kullaniciyla domain'i (orn. 'sigorta poliçeleri', 'ticaret "
                        "sicili ilanlari', 'hastane kayitlari') tartisip add_entity_class "
                        "ile entity siniflari tanimlamalisin."
                    ),
                }, ensure_ascii=False)

            target_entities = ontology.entity_classes
            if target_class:
                target_entities = [e for e in target_entities if e.name == target_class]
                if not target_entities:
                    available = ", ".join(e.name for e in ontology.entity_classes) or "(bos)"
                    return json.dumps({
                        "status": "unknown_class",
                        "target_class": target_class,
                        "available_classes": available,
                        "message": f"'{target_class}' ontolojide tanimsiz. Mevcut: {available}",
                    }, ensure_ascii=False)

            entries = await store.get_all(agent_id, "ocr_result")
            ocr_val: dict[str, Any] | None = None
            for entry in entries:
                if entry.get("key") == doc_key:
                    val = entry.get("value", {})
                    if isinstance(val, str):
                        val = json.loads(val)
                    ocr_val = val
                    break
            if ocr_val is None:
                return f"'{doc_key}' anahtarli OCR sonucu bulunamadi."

            ocr_text = ocr_val.get("ocr_text", "")
            if not ocr_text:
                return "OCR metni bos."

            source_resource_id = ocr_val.get("resource_id", "") or ""
            source_file_name = ocr_val.get("file_name", "") or ""

            extraction_prompt = _build_record_extraction_prompt(
                ontology, target_entities, filter_query, custom_instructions,
            )
            llm = _get_llm()

            from langchain_core.messages import HumanMessage, SystemMessage

            text_chunk = ocr_text[:60000]
            user_msg = (
                "OCR metni asagida. Ontoloji semama gore kayitlari JSON liste olarak ver."
                f"\n\n--- OCR ---\n{text_chunk}\n--- END ---"
            )

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

            json_match = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", raw, re.DOTALL)
            if json_match:
                raw = json_match.group(1)

            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                return json.dumps({
                    "status": "partial",
                    "doc_key": doc_key,
                    "error": "LLM yapilandirilmis JSON donduremedi",
                    "raw_preview": (raw or "")[:1000],
                }, ensure_ascii=False)

            items = parsed.get("records") if isinstance(parsed, dict) else parsed
            if not isinstance(items, list):
                items = [items] if items else []

            if filter_query:
                fq = filter_query.lower().strip()

                def _matches(rec: dict[str, Any]) -> bool:
                    if fq in (rec.get("title") or "").lower():
                        return True
                    if fq in (rec.get("entity_class") or "").lower():
                        return True
                    props = rec.get("properties") or {}
                    if isinstance(props, dict):
                        for v in props.values():
                            if isinstance(v, str) and fq in v.lower():
                                return True
                    return False

                items = [it for it in items if isinstance(it, dict) and _matches(it)]

            if not items:
                return json.dumps({
                    "status": "empty",
                    "doc_key": doc_key,
                    "target_class": target_class,
                    "filter_query": filter_query,
                    "message": (
                        "Filtreyle eslesen kayit bulunamadi."
                        if filter_query else
                        "Belgede ontolojiye uygun kayit tespit edilemedi."
                    ),
                }, ensure_ascii=False)

            agent_dir = EXTRACTION_DIR / agent_id
            agent_dir.mkdir(parents=True, exist_ok=True)
            saved: list[dict[str, Any]] = []

            for raw_item in items:
                if not isinstance(raw_item, dict):
                    continue
                title = (raw_item.get("title") or "Kayit").strip()
                entity_class = (raw_item.get("entity_class") or "").strip()
                properties = raw_item.get("properties") if isinstance(raw_item.get("properties"), dict) else {}
                content_md = (raw_item.get("content_md") or raw_item.get("content") or "").strip()
                source_excerpt = (raw_item.get("source_excerpt") or "").strip()

                if not content_md and not properties:
                    continue

                extraction_id = f"ext_{uuid.uuid4().hex[:8]}"
                slug = _slugify(title)[:60] or "kayit"
                file_path = agent_dir / f"{doc_key}__{slug}__{extraction_id}.md"

                md_lines = [
                    f"# {title}",
                    "",
                    f"_Extraction: {extraction_id} · Source: {source_file_name or doc_key} · "
                    f"{datetime.utcnow().isoformat()}Z_",
                    "",
                ]
                if entity_class:
                    md_lines.append(f"**Tip:** {entity_class}")
                    md_lines.append("")
                if properties:
                    md_lines.append("## Özellikler")
                    md_lines.append("")
                    for k, v in properties.items():
                        v_str = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
                        md_lines.append(f"- **{k}:** {v_str}")
                    md_lines.append("")
                if content_md:
                    md_lines.append("---")
                    md_lines.append("")
                    md_lines.append(content_md)
                if source_excerpt:
                    md_lines.append("")
                    md_lines.append("---")
                    md_lines.append("")
                    md_lines.append("**Kaynak metin alıntısı:**")
                    md_lines.append("")
                    md_lines.append("> " + source_excerpt.replace("\n", "\n> "))

                full_md = "\n".join(md_lines)
                try:
                    file_path.write_text(full_md, encoding="utf-8")
                except OSError as exc:
                    logger.warning("extracted record write failed: %s", exc)
                    continue

                record = {
                    "extraction_id": extraction_id,
                    "doc_key": doc_key,
                    "source_resource_id": source_resource_id,
                    "source_file_name": source_file_name,
                    "title": title,
                    "entity_class": entity_class,
                    "properties": properties,
                    "file_path": str(file_path),
                    "char_count": len(full_md),
                    "created_at": datetime.utcnow().isoformat() + "Z",
                }
                try:
                    await store.upsert(
                        agent_id, "extracted_records", extraction_id, record,
                        source="extract_records_from_ocr",
                    )
                except Exception as exc:
                    logger.warning("extracted_records store failed: %s", exc)

                saved.append({
                    "extraction_id": extraction_id,
                    "title": title,
                    "entity_class": entity_class,
                    "char_count": len(full_md),
                })

            return json.dumps({
                "status": "success",
                "doc_key": doc_key,
                "source_file_name": source_file_name,
                "target_class": target_class,
                "filter_query": filter_query,
                "extracted_count": len(saved),
                "items": saved,
                "next_step": (
                    "Cikartma tamamlandi. Kullaniciya kac kayit cikarildigini ve hangi "
                    "siniflarda oldugunu kisaca soyle ve 'Kaynaklar panelinden "
                    "onizleyebilirsin' de. Detaylari chat'e yazma."
                ),
            }, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("extract_records_from_ocr error: %s", e, exc_info=True)
            return f"Yapilandirilmis cikartma hatasi: {e}"

    @tool
    async def list_extracted_records(doc_key: str = "", entity_class: str = "") -> str:
        """Bu agent icin daha once cikartilmis yapilandirilmis kayitlari listele.

        Args:
            doc_key: Bos -> tum cikartmalar. Dolu -> sadece bu OCR belgesinin cikartmalari.
            entity_class: Bos -> tum siniflar. Dolu -> sadece bu siniftan kayitlar.
        """
        if not store:
            return "Store yapilandirmasi eksik."
        try:
            entries = await store.get_all(agent_id, "extracted_records")
            items: list[dict[str, Any]] = []
            for entry in entries:
                val = entry.get("value", {})
                if isinstance(val, str):
                    try:
                        val = json.loads(val)
                    except (json.JSONDecodeError, ValueError):
                        continue
                if doc_key and val.get("doc_key") != doc_key:
                    continue
                if entity_class and val.get("entity_class") != entity_class:
                    continue
                items.append({
                    "extraction_id": val.get("extraction_id", entry.get("key", "")),
                    "doc_key": val.get("doc_key", ""),
                    "title": val.get("title", ""),
                    "entity_class": val.get("entity_class", ""),
                    "char_count": val.get("char_count", 0),
                    "created_at": val.get("created_at", ""),
                })
            return json.dumps({"count": len(items), "items": items}, ensure_ascii=False, indent=2)
        except Exception as e:
            return f"Cikartma listesi hatasi: {e}"

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
                file_name=None,
            )

            ocr_texts = result.get("ocr_texts", [])
            merged = result.get("merged_text", "")
            page_count = result.get("page_count", 0)

            return json.dumps({
                "mode": "direct",
                "page_count": page_count,
                "total_chars": result.get("total_chars", 0),
                "ocr_texts_count": len(ocr_texts),
                "merged_text": merged,
                "output_dir": output_dir,
                "token_usage": result.get("token_usage", {}),
                "duration_ms": result.get("duration_ms", 0),
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
        read_ocr_pages,
        list_ocr_documents,
        get_ocr_text,
        extract_records_from_ocr,
        list_extracted_records,
        run_extraction,
        run_full_pipeline,
        test_extraction_on_sample,
    ]

    return tools


# ─── MODULE-LEVEL HELPERS ────────────────────────────────────────────

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    """Türkçe karakterleri sade ASCII'ye çevirip dosya/URL adı için slugla."""
    if not text:
        return ""
    table = str.maketrans({
        "ç": "c", "ğ": "g", "ı": "i", "ö": "o", "ş": "s", "ü": "u",
        "Ç": "c", "Ğ": "g", "İ": "i", "Ö": "o", "Ş": "s", "Ü": "u",
    })
    s = text.translate(table).lower()
    s = _SLUG_RE.sub("-", s).strip("-")
    return s


def _build_record_extraction_prompt(
    ontology: AgentOntology,
    target_entities: list[Any],
    filter_query: str,
    custom_instructions: str,
) -> str:
    """Ontolojiden DOMAIN-AGNOSTIC structured extraction prompt uret.

    Hardcoded domain terimi (sirket, police, vs.) yoktur. Sema tamamen agentin
    ontology_model'inden cikar; agent farkli domain'lerde calisirken ayni tool
    farkli semalarla calisir.
    """
    filter_note = ""
    if filter_query:
        filter_note = (
            f"\n\nKULLANICI FILTRESI: SADECE basligi/property degerleri arasinda "
            f"'{filter_query}' GECEN kayitlari cikar. Diger kayitlari tamamen atla.\n"
        )

    custom_note = ""
    if custom_instructions:
        custom_note = f"\n\nEK TALIMAT (kullanicidan):\n{custom_instructions.strip()}\n"

    domain_line = ontology.domain or "Genel"
    goal_line = ontology.goal or "Belgedeki yapilandirilmamis bilgiyi sema'ya gore yapilandirmak"

    schema_lines = ["AGENT'IN ONTOLOJI SEMASI (cikartma SADECE bu siniflara gore yapilir):"]
    for entity in target_entities:
        header = f"\n### {entity.name}"
        if entity.parent:
            header += f" (alt-sinif: {entity.parent})"
        schema_lines.append(header)
        if entity.description:
            schema_lines.append(f"Tanim: {entity.description}")
        if entity.properties:
            schema_lines.append("Properties:")
            for p in entity.properties:
                req = " [zorunlu]" if p.constraint == "required" else ""
                desc = f" — {p.description}" if getattr(p, "description", "") else ""
                schema_lines.append(f"- {p.name} ({p.type}){req}{desc}")
        else:
            schema_lines.append("Properties: tanimsiz (LLM kendi anlamli alanlari sec).")

    schema_block = "\n".join(schema_lines)

    return (
        f"Sen bir Information Extraction uzmanisin. Calisma alani (domain): {domain_line}. "
        f"Hedef: {goal_line}. Sana ham OCR metni verilecek; ondan kullanicinin agent "
        f"icin tanimladigi ONTOLOJI'ye uyan kayitlari ayikla."
        f"{filter_note}{custom_note}"
        f"\n\n{schema_block}\n\n"
        "CIKTI SCHEMA (JSON):\n"
        "```json\n"
        "{\n"
        '  "records": [\n'
        "    {\n"
        '      "title": "Bu kaydin insan-okur basligi (orn. ana entity adi)",\n'
        '      "entity_class": "Yukaridaki ontoloji sinif adlarindan biri (orn. Policy / Company / Patient)",\n'
        '      "properties": { "<property_name>": <value>, ... },\n'
        '      "content_md": "Kaydin tum bilgilerini insan-okur Markdown formatinda sun. Onemli alanlar **kalin**, listeler madde isaretiyle.",\n'
        '      "source_excerpt": "Bu kayit hangi OCR metni parcasindan cikarildi? 200-400 karakter AYNEN alintilanmis kanit."\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "```\n\n"
        "KURALLAR:\n"
        "- SADECE yukaridaki JSON yapisini dondur, baska metin yazma.\n"
        "- entity_class MUTLAKA ontoloji semasindaki sinif adlarindan biri olmali. Eger metinde ontolojiye uymayan kayit varsa onu ATLA.\n"
        "- properties anahtarlari ontolojideki property name'leriyle ayni olsun. Ek bilgi varsa content_md'ye yaz.\n"
        "- title bos olmamali; tespit edemiyorsan o kaydi atla.\n"
        "- OCR'da tekrar/hata varsa duzelt; ayni satir 5 kez tekrar ediyorsa bir kez yaz.\n"
        "- Bir kaydin bilgisi farkli yerlerde dagilmissa birlestir.\n"
        "- Yorumlama/ozet ekleme; metinde olan bilgiyi yapilandir.\n"
    )
