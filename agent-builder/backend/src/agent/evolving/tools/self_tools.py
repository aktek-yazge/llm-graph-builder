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

logger = logging.getLogger(__name__)


def create_self_tools(
    agent_id: str,
    store: KnowledgeStore,
    memory: AgentMemory,
    skill_manager: AgentSkillManager,
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
        """Mevcut ontolojiden extraction prompt uret ve goster."""
        ontology = await store.load_ontology(agent_id)
        if ontology.is_empty:
            return "Ontoloji bos, once entity/relationship tanimlari ekleyin."
        return memory.build_extraction_prompt(ontology)

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
        await store.upsert(
            agent_id,
            "quality_feedback",
            f"fb_{len(await store.get_all(agent_id, 'quality_feedback'))}",
            {
                "feedback": feedback,
                "affected_entities": affected_entities or [],
            },
            source="user_feedback",
            confidence=0.8,
        )
        return f"Feedback kaydedildi. Ontoloji bir sonraki prompt'ta bu feedback'i dikkate alacak."

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
    ]
