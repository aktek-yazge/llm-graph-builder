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
    AgentOntology,
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

    @tool
    async def add_entity_class(
        name: str,
        description: str,
        properties: list[dict[str, str]],
        parent: str = "",
    ) -> str:
        """Ontolojiye yeni entity sinifi ekle veya mevcut olani guncelle.

        Args:
            name: Entity sinifi adi (orn: 'Policy', 'Customer')
            description: Kisa aciklama
            properties: Property listesi [{"name": "...", "type": "string|number|date|boolean", "constraint": "required|optional"}]
            parent: Ust sinif adi (opsiyonel)
        """
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

        Args:
            name: Iliski adi (orn: 'HAS_POLICY', 'COVERS')
            source: Kaynak entity sinifi
            target: Hedef entity sinifi
            edge_properties: Edge property isimleri (opsiyonel)
            description: Aciklama
        """
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

        Args:
            domain: Calisma alani (orn: 'Sigorta Polce Yonetimi')
            goal: Extraction hedefi (orn: 'Police belgelerinden musteri, teminat ve prim bilgilerini cikar')
        """
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

    return [
        add_entity_class,
        add_relationship_predicate,
        add_inference_rule,
        add_constraint,
        set_domain_info,
        get_current_ontology,
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
    ]
