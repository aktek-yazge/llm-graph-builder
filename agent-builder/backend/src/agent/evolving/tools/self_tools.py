"""
Self Tools
==========

Agent'in kendini gelistirmek icin kullandigi tool'lar.
Ontoloji yonetimi, feedback isleme, test calıstirma.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.tools import tool

from ..ontology_model import (
    EntityClass,
    Property,
    RelationshipPredicate,
    InferenceRule,
)
from ..knowledge_store import KnowledgeStore
from ..agent_memory import AgentMemory
from ..agent_skills import AgentSkillManager
from ..wiki_store import WikiStore

logger = logging.getLogger(__name__)


def create_self_tools(
    agent_id: str,
    store: KnowledgeStore,
    memory: AgentMemory,
    skill_manager: AgentSkillManager,
    wiki: WikiStore | None = None,
) -> list:
    """Agent'in kendini gelistirmek icin kullandigi tool'lari olustur."""

    async def _wiki_first_guard(kind: str, name: str) -> str | None:
        """
        Wiki-first ihlalini runtime'da yakala.

        DEPRECATED: Workflow engine aktif oldugunda bu guard devre disi kalir
        (workflow yapisi akisi zaten garantiler). Gecis sureci icin korunuyor;
        sadece warning loglar, bloklama yapmaz.
        """
        import logging as _lg
        _lg.getLogger(__name__).debug(
            "wiki_first_guard check (deprecated — workflow engine handles ordering): %s/%s",
            kind, name,
        )
        return None

    @tool
    async def add_entity_class(
        name: str,
        description: str,
        properties: list[dict[str, str]],
        parent: str = "",
    ) -> str:
        """Ontolojiye yeni entity sinifi ekle veya mevcut olani guncelle.

        ⚠️ WIKI-FIRST KURALI (RUNTIME ENFORCED): Bu tool, wiki-first akisinin
        6. ADIMIDIR. Cagirmadan once `entities/<name>` wiki sayfasi yazilmis
        OLMALI. Aksi halde tool reddeder ve sana wiki yazmani soyler.

        Args:
            name: Entity sinifi adi (orn: 'Policy', 'Customer'). Wiki sayfa
                  adiyla birebir ayni olmalidir (`entities/<name>`).
            description: Kisa aciklama
            properties: Property listesi [{"name": "...", "type": "string|number|date|boolean", "constraint": "required|optional"}]
            parent: Ust sinif adi (opsiyonel)
        """
        violation = await _wiki_first_guard("entities", name)
        if violation:
            return violation

        ontology = await store.load_ontology(agent_id)

        props = [
            Property(
                name=p["name"],
                type=p.get("type", "string"),
                constraint=p.get("constraint", "optional"),
                description=p.get("description", ""),
            )
            for p in properties
        ]
        entity = EntityClass(name=name, description=description, parent=parent, properties=props)
        ontology.upsert_entity(entity)

        await store.save_ontology(agent_id, ontology, source="conversation")

        if wiki:
            related_rels = [
                r.name for r in ontology.relationship_predicates
                if r.source == name or r.target == name
            ]
            await wiki.sync_entity_page(
                agent_id, name,
                description=description,
                properties=properties,
                parent=parent,
                related_relationships=related_rels,
            )

        return f"Entity class '{name}' eklendi/guncellendi. Toplam entity: {len(ontology.entity_classes)}"

    @tool
    async def add_relationship_predicate(
        name: str,
        source: str,
        target: str,
        edge_properties: list[str] | None = None,
        description: str = "",
    ) -> str:
        """Ontolojiye yeni iliski tipi ekle.

        ⚠️ WIKI-FIRST KURALI (RUNTIME ENFORCED): Bu tool, wiki-first akisinin
        6. ADIMIDIR. Cagirmadan once `relationships/<name>` wiki sayfasi yazilmis
        OLMALI. Aksi halde tool reddeder.

        Args:
            name: Iliski adi (orn: 'HAS_POLICY', 'COVERS'). Wiki sayfa adiyla
                  birebir ayni olmalidir (`relationships/<name>`).
            source: Kaynak entity sinifi
            target: Hedef entity sinifi
            edge_properties: Edge property isimleri (opsiyonel)
            description: Aciklama
        """
        violation = await _wiki_first_guard("relationships", name)
        if violation:
            return violation

        ontology = await store.load_ontology(agent_id)
        rel = RelationshipPredicate(
            name=name,
            source=source,
            target=target,
            edge_properties=edge_properties or [],
            description=description,
        )
        ontology.upsert_relationship(rel)

        await store.save_ontology(agent_id, ontology, source="conversation")

        if wiki:
            await wiki.sync_relationship_page(
                agent_id, name,
                source_entity=source,
                target_entity=target,
                edge_properties=edge_properties,
                description=description,
            )

        return f"Relationship '{name}' ({source} -> {target}) eklendi. Toplam iliski: {len(ontology.relationship_predicates)}"

    @tool
    async def add_inference_rule(
        condition: str,
        inference: str,
        rule_type: str = "implied",
    ) -> str:
        """Cikarim kurali ekle.

        Args:
            condition: Kosul ifadesi (orn: 'X HAS_POLICY Y')
            inference: Cikarim ifadesi (orn: 'X IS_CUSTOMER_OF Y.insurer')
            rule_type: Kural tipi: implied | transitive | inverse
        """
        ontology = await store.load_ontology(agent_id)
        rule = InferenceRule(condition=condition, inference=inference, rule_type=rule_type)
        ontology.inference_rules.append(rule)

        await store.save_ontology(agent_id, ontology, source="conversation")
        return f"Inference rule eklendi. Toplam kural: {len(ontology.inference_rules)}"

    @tool
    async def add_constraint(constraint: str) -> str:
        """Ontolojiye global kisitlama ekle.

        Args:
            constraint: Kisitlama metni (orn: 'Her Policy en az bir Coverage icermeli')
        """
        ontology = await store.load_ontology(agent_id)
        ontology.constraints.append(constraint)
        await store.save_ontology(agent_id, ontology, source="conversation")
        return f"Constraint eklendi. Toplam: {len(ontology.constraints)}"

    @tool
    async def set_domain_info(domain: str, goal: str) -> str:
        """Agent'in domain ve hedef bilgisini ayarla.

        ⚠️ WIKI-FIRST: Domain'i ayarlamadan once wiki'de en az bir analiz veya
        kaynak sayfa bulunmalidir (kullanici metni ve domain'i gorerek onaylamali).
        Aksi halde tool reddeder.

        Args:
            domain: Calisma alani (orn: 'Sigorta Polce Yonetimi')
            goal: Extraction hedefi (orn: 'Police belgelerinden musteri, teminat ve prim bilgilerini cikar')
        """
        if wiki is not None:
            try:
                index = await wiki.get_index(agent_id)
            except Exception:
                index = ""
            if not index or "Henuz wiki sayfasi yok" in str(index) or len(str(index).strip()) < 20:
                return (
                    "WIKI-FIRST IHLALI: Domain'i set etmeden once wiki'de en az "
                    "bir analiz/kaynak sayfa olmali. Once `create_wiki_page("
                    "'analysis/domain-overview', '...')` veya `create_wiki_page("
                    "'sources/<doc_slug>', '...')` cagir; kullanicinin gordugu "
                    "ve onayladigi domain anlayisini dokumante et. Sonra bu tool'u tekrar cagir."
                )

        ontology = await store.load_ontology(agent_id)
        ontology.domain = domain
        ontology.goal = goal
        await store.save_ontology(agent_id, ontology, source="conversation")
        return f"Domain: '{domain}', Goal: '{goal}' olarak ayarlandi."

    @tool
    async def get_current_ontology() -> str:
        """Mevcut ontoloji durumunu goster."""
        ontology = await store.load_ontology(agent_id)
        if ontology.is_empty:
            return "Ontoloji henuz bos. Once domain, entity ve relationship ekleyin."
        return json.dumps(ontology.to_dict(), ensure_ascii=False, indent=2)

    @tool
    async def delete_resource(filename: str = "", url: str = "") -> str:
        """Yuklenmis bir dosyayi veya URL kaynagini sil.

        Args:
            filename: Silinecek dosyanin adi (filename ile path ayni anda verilmemeli)
            url: Silinecek URL kaynagi

        Not: `filename` verildiyse ayni isimde olan TUM dosyalar silinir (mukerrerleri siler).
        """
        import os as _os

        if not filename and not url:
            return "Hata: Silmek icin filename veya url parametrelerinden birini belirtin."

        deleted_files: list[str] = []
        deleted_urls: list[str] = []

        if filename:
            sample_entries = await store.get_all(agent_id, "sample_files")
            for entry in sample_entries:
                key = entry.get("key", "")
                val = entry.get("value", {})
                if isinstance(val, str):
                    try:
                        val = json.loads(val)
                    except (json.JSONDecodeError, ValueError):
                        continue

                files = val.get("files", [])
                remaining: list[dict] = []
                for f in files:
                    if f.get("filename") == filename:
                        fpath = f.get("path", "")
                        if fpath and _os.path.exists(fpath):
                            try:
                                _os.remove(fpath)
                            except OSError:
                                pass
                        deleted_files.append(f.get("filename", fpath))
                    else:
                        remaining.append(f)

                if len(remaining) != len(files):
                    if remaining:
                        paths = [r.get("path", "") for r in remaining]
                        await store.upsert(
                            agent_id, "sample_files", key,
                            {"files": remaining, "paths": paths},
                            source="user_delete",
                        )
                    else:
                        await store.delete(agent_id, "sample_files", key)

        if url:
            source_entries = await store.get_all(agent_id, "source_urls")
            for entry in source_entries:
                key = entry.get("key", "")
                val = entry.get("value", {})
                if isinstance(val, str):
                    try:
                        val = json.loads(val)
                    except (json.JSONDecodeError, ValueError):
                        continue

                urls = val.get("urls", [])
                sources = val.get("sources", [])
                if url in urls:
                    new_urls = [u for u in urls if u != url]
                    new_sources = [s for s in sources if s.get("url") != url]
                    deleted_urls.append(url)
                    if new_urls:
                        await store.upsert(
                            agent_id, "source_urls", key,
                            {"sources": new_sources, "urls": new_urls},
                            source="user_delete",
                        )
                    else:
                        await store.delete(agent_id, "source_urls", key)

        if not deleted_files and not deleted_urls:
            return f"Eslesen kayit bulunamadi. (filename='{filename}', url='{url}')"

        parts: list[str] = []
        if deleted_files:
            parts.append(f"{len(deleted_files)} dosya silindi: {', '.join(deleted_files)}")
        if deleted_urls:
            parts.append(f"{len(deleted_urls)} URL silindi: {', '.join(deleted_urls)}")
        return ". ".join(parts)

    @tool
    async def delete_all_resources(confirm: bool = False) -> str:
        """TUM yuklenmis dosyalari ve URL'leri sil.

        Args:
            confirm: Onaylamak icin True gonderin. Geri alinamaz.
        """
        import os as _os

        if not confirm:
            return "Hata: Tum kaynaklari silmek icin `confirm=True` gonderin."

        sample_entries = await store.get_all(agent_id, "sample_files")
        source_entries = await store.get_all(agent_id, "source_urls")

        file_count = 0
        for entry in sample_entries:
            val = entry.get("value", {})
            if isinstance(val, str):
                try:
                    val = json.loads(val)
                except (json.JSONDecodeError, ValueError):
                    continue
            for f in val.get("files", []):
                fpath = f.get("path", "")
                if fpath and _os.path.exists(fpath):
                    try:
                        _os.remove(fpath)
                    except OSError:
                        pass
                file_count += 1
            await store.delete(agent_id, "sample_files", entry.get("key", ""))

        url_count = 0
        for entry in source_entries:
            val = entry.get("value", {})
            if isinstance(val, str):
                try:
                    val = json.loads(val)
                except (json.JSONDecodeError, ValueError):
                    continue
            url_count += len(val.get("urls", []))
            await store.delete(agent_id, "source_urls", entry.get("key", ""))

        return f"Temizlik tamamlandi: {file_count} dosya, {url_count} URL silindi."

    @tool
    async def list_resources() -> str:
        """Yuklenmis dosyalari ve URL kaynaklarini listele.

        JSON formatinda doner: {"files": [...], "urls": [...], "summary": "...",
        "pending_ocr_count": int, "warning": str|None}.

        Her dosya: name, filename, size, type, path, resource_id, **ocr_status**
        ('completed' veya 'pending'), **doc_key** (OCR'lanmissa), page_count, total_chars.

        ⚠️ KRITIK: ocr_status='pending' ise dosya icerigi HENUZ OKUNMADI. Asla
        dosya adindan icerik UYDURMA. Once `ocr_and_analyze(file_path)` cagir,
        sonra `read_ocr_pages(doc_key, ...)` ile gercek metni oku.

        Kullanici 'kac dosya var', 'hangi dosyalar yuklendi' veya bir belge
        hakkinda is istediginde once bunu cagir."""
        sample_files = await store.get_all(agent_id, "sample_files")
        source_urls = await store.get_all(agent_id, "source_urls")

        ocr_entries = await store.get_all(agent_id, "ocr_result")
        ocr_by_rid: dict[str, dict] = {}
        ocr_by_path: dict[str, dict] = {}
        ocr_by_name: dict[str, dict] = {}
        for entry in ocr_entries:
            val = entry.get("value", {})
            if isinstance(val, str):
                try:
                    val = json.loads(val)
                except (json.JSONDecodeError, ValueError):
                    continue
            info = {
                "doc_key": entry.get("key", ""),
                "page_count": val.get("page_count", 0),
                "total_chars": val.get("total_chars", 0),
            }
            rid = val.get("resource_id", "") or ""
            fpath = val.get("file_path", "") or ""
            fname = val.get("file_name", "") or ""
            if rid:
                ocr_by_rid[rid] = info
            if fpath:
                ocr_by_path[fpath] = info
            if fname:
                ocr_by_name[fname] = info

        files: list[dict] = []
        pending_count = 0
        for sf in sample_files:
            val = sf.get("value", {})
            if isinstance(val, str):
                try:
                    val = json.loads(val)
                except (json.JSONDecodeError, ValueError):
                    continue
            for f in val.get("files", []):
                fname = f.get("filename", "")
                fpath = f.get("path", "") or ""
                rid = f.get("resource_id", "") or ""

                ocr_info = None
                if rid and rid in ocr_by_rid:
                    ocr_info = ocr_by_rid[rid]
                elif fpath and fpath in ocr_by_path:
                    ocr_info = ocr_by_path[fpath]
                elif fname and fname in ocr_by_name:
                    ocr_info = ocr_by_name[fname]
                else:
                    for op, info in ocr_by_path.items():
                        if op.endswith(fname) or (fname and fname in op):
                            ocr_info = info
                            break

                ocr_status = "completed" if ocr_info else "pending"
                if ocr_status == "pending":
                    pending_count += 1

                files.append({
                    "name": fname,
                    "filename": fname,
                    "size": f.get("size", 0),
                    "type": f.get("content_type", ""),
                    "path": fpath,
                    "resource_id": rid,
                    "ocr_status": ocr_status,
                    "doc_key": ocr_info["doc_key"] if ocr_info else None,
                    "page_count": ocr_info["page_count"] if ocr_info else None,
                    "total_chars": ocr_info["total_chars"] if ocr_info else None,
                })

        urls: list[dict] = []
        for su in source_urls:
            val = su.get("value", {})
            if isinstance(val, str):
                try:
                    val = json.loads(val)
                except (json.JSONDecodeError, ValueError):
                    continue
            for u in val.get("urls", []):
                urls.append({"url": u, "name": u})

        summary = f"{len(files)} dosya ({pending_count} OCR bekliyor), {len(urls)} URL"
        if not files and not urls:
            summary = "Henuz dosya veya URL yuklenmemis."

        warning = None
        if pending_count > 0:
            warning = (
                f"⚠️ {pending_count} dosyada OCR yok. Bu dosyalar hakkinda is yapmadan ONCE "
                "her biri icin `ocr_and_analyze(file_path)` cagir. Dosya icerigini ASLA "
                "isim/path'ten tahmin etme — gercek metin OCR'dan gelmeli."
            )

        return json.dumps(
            {
                "files": files,
                "urls": urls,
                "summary": summary,
                "pending_ocr_count": pending_count,
                "warning": warning,
            },
            ensure_ascii=False,
        )

    @tool
    async def generate_extraction_prompt() -> str:
        """Mevcut ontolojiden extraction prompt uret ve goster.
        Wiki sayfalari varsa ornekler ve pattern'ler de dahil edilir."""
        ontology = await store.load_ontology(agent_id)
        if ontology.is_empty:
            return "Ontoloji bos, once entity/relationship tanimlari ekleyin."
        wiki_context = ""
        if wiki:
            try:
                wiki_context = await wiki.build_extraction_context(agent_id)
            except Exception:
                pass
        return memory.build_extraction_prompt(ontology, wiki_context=wiki_context)

    @tool
    async def update_from_feedback(
        feedback: str,
        affected_entities: list[str] | None = None,
    ) -> str:
        """Kullanici geri bildirimine gore bilgi deposunu guncelle.

        Args:
            feedback: Kullanici feedback metni
            affected_entities: Etkilenen entity isimleri (opsiyonel)
        """
        fb_count = len(await store.get_all(agent_id, 'quality_feedback'))
        await store.upsert(
            agent_id,
            "quality_feedback",
            f"fb_{fb_count}",
            {
                "feedback": feedback,
                "affected_entities": affected_entities or [],
            },
            source="user_feedback",
            confidence=0.8,
        )

        if wiki:
            slug = feedback[:40].lower().replace(" ", "-").replace("/", "-")
            await wiki.add_pattern_page(
                agent_id,
                pattern_name=f"feedback-{fb_count}-{slug}",
                description=feedback,
                related_entities=affected_entities or [],
                source="user_feedback",
            )

        return f"Feedback kaydedildi. Wiki ve ontoloji bir sonraki prompt'ta bu feedback'i dikkate alacak."

    @tool
    async def save_as_skill() -> str:
        """Mevcut ontolojiyi Celery worker AgenticOCR icin SkillExecution olarak kaydet."""
        result = await skill_manager.save_skill(agent_id)
        if "error" in result:
            return result["error"]
        return f"Skill kaydedildi: {result['skill_id']} (v{result['version']})"

    @tool
    async def remove_entity_class(name: str) -> str:
        """Ontolojiden entity sinifi kaldir.

        Args:
            name: Kaldirilacak entity sinifi adi
        """
        ontology = await store.load_ontology(agent_id)
        removed = ontology.remove_entity(name)
        if not removed:
            return f"Entity '{name}' bulunamadi."
        await store.save_ontology(agent_id, ontology, source="conversation")
        return f"Entity '{name}' kaldirildi."

    @tool
    async def remove_relationship(name: str) -> str:
        """Ontolojiden iliski tipi kaldir.

        Args:
            name: Kaldirilacak iliski adi
        """
        ontology = await store.load_ontology(agent_id)
        removed = ontology.remove_relationship(name)
        if not removed:
            return f"Relationship '{name}' bulunamadi."
        await store.save_ontology(agent_id, ontology, source="conversation")
        return f"Relationship '{name}' kaldirildi."

    # ─── AUTO-DISCOVERY TOOLS ────────────────────────────────────

    @tool
    async def analyze_extraction_results(batch_id: str = "", limit: int = 50) -> str:
        """Extraction sonuclarini analiz et, ontolojide olmayan yeni entity/relation tiplerini kesfet.

        Her extraction'dan donen node label ve relationship type'lari ontoloji ile
        karsilastirir. Yeni bulunanlar ontology_discoveries tablosuna kaydedilir.

        Args:
            batch_id: Belirli bir batch'i analiz et (bos birakılırsa tum belgeler)
            limit: Analiz edilecek maksimum belge sayisi
        """
        ontology = await store.load_ontology(agent_id)
        known_entities = {e.name for e in ontology.entity_classes}
        known_rels = {r.name for r in ontology.relationship_predicates}
        system_labels = {"Document", "Chunk", "__Entity__", "_Bloom_Perspective_", "_Bloom_Scene_"}

        conditions = ["agent_id = $1", "extraction_result IS NOT NULL"]
        params: list[Any] = [agent_id]
        idx = 2
        if batch_id:
            conditions.append(f"batch_id = ${idx}")
            params.append(batch_id)
            idx += 1
        params.append(limit)

        rows = await store.pg.fetch(
            f"""
            SELECT doc_id, file_path, extraction_result
            FROM workspace_documents
            WHERE {' AND '.join(conditions)}
            ORDER BY updated_at DESC
            LIMIT ${idx}
            """,
            *params,
        )

        new_entities: dict[str, dict[str, Any]] = {}
        new_rels: dict[str, dict[str, Any]] = {}

        for row in rows:
            result = row["extraction_result"]
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except (json.JSONDecodeError, TypeError):
                    continue

            doc_ref = row.get("file_path", row.get("doc_id", ""))

            for node in result.get("nodes", []):
                label = node.get("label", "")
                if not label or label in known_entities or label in system_labels:
                    continue
                props = list(node.get("properties", {}).keys())
                if label not in new_entities:
                    new_entities[label] = {"doc": doc_ref, "props": set(props), "count": 0}
                new_entities[label]["count"] += 1
                new_entities[label]["props"].update(props)

            for rel in result.get("relationships", []):
                rel_type = rel.get("type", "")
                if not rel_type or rel_type in known_rels:
                    continue
                if rel_type not in new_rels:
                    new_rels[rel_type] = {"doc": doc_ref, "count": 0}
                new_rels[rel_type]["count"] += 1

        saved_entities = 0
        for name, info in new_entities.items():
            r = await store.upsert_discovery(
                agent_id, "entity", name,
                first_seen_doc=info["doc"],
                sample_properties=sorted(info["props"]),
            )
            if r["new"]:
                saved_entities += 1

        saved_rels = 0
        for name, info in new_rels.items():
            r = await store.upsert_discovery(
                agent_id, "relationship", name,
                first_seen_doc=info["doc"],
            )
            if r["new"]:
                saved_rels += 1

        total_new = saved_entities + saved_rels
        if total_new == 0 and not new_entities and not new_rels:
            return f"{len(rows)} belge analiz edildi. Ontolojide tanimsiz yeni tip bulunamadi."

        lines = [f"{len(rows)} belge analiz edildi."]
        if new_entities:
            names = ", ".join(sorted(new_entities.keys()))
            lines.append(f"Yeni entity tipleri ({len(new_entities)}): {names}")
        if new_rels:
            names = ", ".join(sorted(new_rels.keys()))
            lines.append(f"Yeni relationship tipleri ({len(new_rels)}): {names}")
        lines.append("Onay icin approve_discovery veya reject_discovery tool'larini kullan.")
        return "\n".join(lines)

    @tool
    async def approve_discovery(name: str, discovery_type: str = "entity", description: str = "") -> str:
        """Kesfedilen entity veya relationship tipini onayla ve ontolojiye ekle.

        Args:
            name: Onaylanacak tipin adi
            discovery_type: 'entity' veya 'relationship'
            description: Tip aciklamasi (opsiyonel)
        """
        updated = await store.update_discovery_status(agent_id, discovery_type, name, "approved")
        if not updated:
            return f"Discovery bulunamadi: {discovery_type}/{name}"

        ontology = await store.load_ontology(agent_id)

        if discovery_type == "entity":
            discoveries = await store.list_discoveries(agent_id, discovery_type="entity")
            disc = next((d for d in discoveries if d["name"] == name), None)
            props = []
            if disc and disc.get("sample_properties"):
                sample_props = disc["sample_properties"]
                if isinstance(sample_props, str):
                    sample_props = json.loads(sample_props)
                props = [Property(name=p, type="string", constraint="optional") for p in sample_props if isinstance(p, str)]

            entity = EntityClass(name=name, description=description, properties=props)
            ontology.upsert_entity(entity)
        else:
            rel = RelationshipPredicate(name=name, source="", target="", description=description)
            ontology.upsert_relationship(rel)

        await store.save_ontology(agent_id, ontology, source="auto_discovery")
        return f"{discovery_type.capitalize()} '{name}' onaylandi ve ontolojiye eklendi."

    @tool
    async def reject_discovery(name: str, discovery_type: str = "entity") -> str:
        """Kesfedilen entity veya relationship tipini reddet.

        Args:
            name: Reddedilecek tipin adi
            discovery_type: 'entity' veya 'relationship'
        """
        updated = await store.update_discovery_status(agent_id, discovery_type, name, "rejected")
        if not updated:
            return f"Discovery bulunamadi: {discovery_type}/{name}"
        return f"{discovery_type.capitalize()} '{name}' reddedildi. Gelecek extraction'larda dikkate alinmayacak."

    @tool
    async def list_pending_discoveries() -> str:
        """Onay bekleyen kesfedilmis entity ve relationship tiplerini listele."""
        discoveries = await store.list_discoveries(agent_id, status="pending")
        if not discoveries:
            return "Onay bekleyen kesif yok."

        entities = [d for d in discoveries if d["discovery_type"] == "entity"]
        rels = [d for d in discoveries if d["discovery_type"] == "relationship"]

        lines = []
        if entities:
            lines.append(f"Entity tipleri ({len(entities)}):")
            for d in entities:
                props_info = ""
                if d.get("sample_properties"):
                    sp = d["sample_properties"]
                    if isinstance(sp, str):
                        sp = json.loads(sp)
                    if sp:
                        props_info = f" [props: {', '.join(sp[:5])}]"
                lines.append(f"  - {d['name']} (x{d['sample_count']}){props_info}")
        if rels:
            lines.append(f"Relationship tipleri ({len(rels)}):")
            for d in rels:
                lines.append(f"  - {d['name']} (x{d['sample_count']})")
        return "\n".join(lines)

    @tool
    async def create_dynamic_indexes(neo4j_uri: str = "", neo4j_user: str = "", neo4j_password: str = "") -> str:
        """Onaylanan entity tipleri icin Neo4j'de RANGE ve FULLTEXT indexler olustur.

        Ontolojideki entity class isimlerinden otomatik index olusturur.
        Mevcut hardcoded index listelerinin yerini alir.

        Args:
            neo4j_uri: Neo4j baglanti adresi (bos ise env'den alinir)
            neo4j_user: Neo4j kullanici adi
            neo4j_password: Neo4j sifre
        """
        import os
        uri = neo4j_uri or os.environ.get("NEO4J_URI", "bolt://localhost:7687")
        user = neo4j_user or os.environ.get("NEO4J_USERNAME", "neo4j")
        password = neo4j_password or os.environ.get("NEO4J_PASSWORD", "password")

        ontology = await store.load_ontology(agent_id)
        if ontology.is_empty:
            return "Ontoloji bos, index olusturulamaz."

        entity_names = [e.name for e in ontology.entity_classes]

        try:
            from neo4j import GraphDatabase
            driver = GraphDatabase.driver(uri, auth=(user, password))

            created = []
            with driver.session() as session:
                for label in entity_names:
                    idx_id = f"idx_{label.lower()}_id"
                    try:
                        session.run(f"CREATE INDEX {idx_id} IF NOT EXISTS FOR (n:{label}) ON (n.id)")
                        created.append(f"RANGE {label}.id")
                    except Exception as e:
                        if "EquivalentSchemaRuleAlreadyExists" not in str(e):
                            logger.warning("Index %s failed: %s", idx_id, e)

                    idx_name = f"idx_{label.lower()}_normalized_name"
                    try:
                        session.run(f"CREATE INDEX {idx_name} IF NOT EXISTS FOR (n:{label}) ON (n.normalized_name)")
                        created.append(f"RANGE {label}.normalized_name")
                    except Exception as e:
                        if "EquivalentSchemaRuleAlreadyExists" not in str(e):
                            pass

                ft_labels = entity_names + ["__Entity__"]
                ft_label_str = "|".join(f"`{l}`" for l in ft_labels)
                try:
                    session.run(
                        f"CREATE FULLTEXT INDEX entity_names_dynamic IF NOT EXISTS "
                        f"FOR (n:{ft_label_str}) ON EACH [n.name, n.normalized_name, n.id]"
                    )
                    created.append("FULLTEXT entity_names_dynamic")
                except Exception as e:
                    if "EquivalentSchemaRuleAlreadyExists" not in str(e):
                        logger.warning("Fulltext index failed: %s", e)

            driver.close()
            return f"{len(created)} index olusturuldu/guncellendi: {', '.join(created)}"
        except ImportError:
            return "neo4j Python driver yuklu degil. pip install neo4j gerekli."
        except Exception as exc:
            return f"Neo4j baglanti hatasi: {exc}"

    # ─── PLAN TOOLS ─────────────────────────────────────────────

    @tool
    async def create_plan(steps: list[str], summary: str = "") -> str:
        """Yapilandirilmis plan olustur. PLAN MODE'da kullanilir.
        Plani kullaniciya markdown olarak sun ve onay iste.

        Args:
            steps: Plan adimlari listesi (orn: ["Belgeyi OCR ile oku", "Icerigini ozetle", ...])
            summary: Plan ozeti/baslik (opsiyonel)
        """
        step_dicts = [{"id": i + 1, "content": s} for i, s in enumerate(steps)]
        await store.save_plan(agent_id, step_dicts, summary)
        plan = await store.load_plan(agent_id)
        md = store.plan_to_markdown(plan) if plan else ""
        return f"Plan olusturuldu ({len(steps)} adim).\n\n{md}"

    @tool
    async def update_plan_step(step_id: int, new_content: str) -> str:
        """Plan adimini guncelle. PLAN MODE'da kullanilir.

        Args:
            step_id: Guncellenecek adim numarasi (1'den baslar)
            new_content: Yeni adim icerigi
        """
        result = await store.update_plan_step(agent_id, step_id, new_content)
        if not result:
            return f"Adim {step_id} bulunamadi veya aktif plan yok."
        md = store.plan_to_markdown(result)
        return f"Adim {step_id} guncellendi.\n\n{md}"

    @tool
    async def add_plan_step(after_step_id: int, content: str) -> str:
        """Plana yeni adim ekle. PLAN MODE'da kullanilir.

        Args:
            after_step_id: Bu adimdan sonra ekle (0 = en basa ekle)
            content: Yeni adim icerigi
        """
        result = await store.add_plan_step(agent_id, after_step_id, content)
        if not result:
            return "Aktif plan bulunamadi."
        md = store.plan_to_markdown(result)
        return f"Yeni adim eklendi.\n\n{md}"

    @tool
    async def remove_plan_step(step_id: int) -> str:
        """Plandan adim kaldir. PLAN MODE'da kullanilir.

        Args:
            step_id: Kaldirilacak adim numarasi
        """
        result = await store.remove_plan_step(agent_id, step_id)
        if not result:
            return f"Adim {step_id} bulunamadi veya aktif plan yok."
        md = store.plan_to_markdown(result)
        return f"Adim {step_id} kaldirildi.\n\n{md}"

    @tool
    async def get_current_plan() -> str:
        """Aktif plani goster."""
        plan = await store.load_plan(agent_id)
        if not plan:
            return "Aktif plan yok."
        return store.plan_to_markdown(plan)

    @tool
    async def request_plan_mode(reason: str, topic: str = "") -> str:
        """Karmasik veya uzun bir gorev icin kullanicidan PLAN MODU izni iste.

        Bu tool aciklamasini iyi oku:
        - Sen su anda AGENT (uygulama) modundasin. Default mod budur.
        - Cogu istek dogrudan agent modunda yapilir; plan modu istemeden once dusun.
        - Sadece su durumlarda cagir:
            * Cok adimli, uzun surecek bir is (orn. 50+ dosyalik batch, yeni ontoloji
              tasarimi, sahne yayinlama)
            * Birden fazla yol var ve kullaniciyla once tartisilmasi gereken bir karar
            * Buyuk bir refactor / mimari karar
            * Kullanici acikca "plan yapalim" / "tartisalim" / "once konusalim" demis

        Bu tool cagrildiginda kullaniciya bir onay karti gosterilir. Sen tool
        cagrisindan SONRA tek bir kisa cumle ile niye plan modu istedigini soyle
        ("Bu is uzun surecek, plan modunda tartisalim mi?") ve DUR — daha tool
        cagirma. Kullanicinin onay vermesini bekle.

        Kullanici onaylarsa sistem otomatik olarak plan moduna gecer ve sen
        sonraki turunda create_plan / update_plan_step gibi plan tool'larina
        erisirsin. Reddederse mevcut bilgilerle gorevi agent modunda yap.

        Args:
            reason: Plan modunun NEDEN gerekli oldugu (1-2 cumle, sade Turkce).
            topic: Plan modunda tartisilacak konu basligi (opsiyonel).
        """
        payload = {
            "status": "awaiting_user_approval",
            "reason": reason,
            "topic": topic or reason[:60],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    return [
        add_entity_class,
        add_relationship_predicate,
        add_inference_rule,
        add_constraint,
        set_domain_info,
        get_current_ontology,
        list_resources,
        delete_resource,
        delete_all_resources,
        generate_extraction_prompt,
        update_from_feedback,
        save_as_skill,
        remove_entity_class,
        remove_relationship,
        analyze_extraction_results,
        approve_discovery,
        reject_discovery,
        list_pending_discoveries,
        create_dynamic_indexes,
        create_plan,
        update_plan_step,
        add_plan_step,
        remove_plan_step,
        get_current_plan,
        request_plan_mode,
    ]
