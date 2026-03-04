"""
Builder Agent Tools
===================

Builder Agent'in kullandigi LangChain tool tanimlari.

Kategoriler:
- Ontology: skill/goal/schema arama ve olusturma
- Gateway: virtual server ve tool yonetimi
- Analysis: ornek belge analizi (OCR/LLM)
- Knowledge: Knowledge DB schema kontrolu
- Versioning: event history, rollback, snapshots
"""

import json
import logging
from typing import Any, Dict, List, Optional

from langchain_core.tools import tool

from ..ontology.neo4j_client import OntologyDBClient
from ..ontology.reasoner import OntologyReasoner
from ..skills.skill_registry import SkillRegistry
from ..gateway.mcp_gateway_client import MCPGatewayClient
from ..gateway.virtual_server import VirtualServerManager
from ..mutation_gateway.gateway import MutationGateway

logger = logging.getLogger(__name__)


# =============================================================================
# TOOL FACTORY
# =============================================================================

def create_builder_tools(
    db: OntologyDBClient,
    reasoner: OntologyReasoner,
    skill_registry: SkillRegistry,
    gateway: MCPGatewayClient,
    vs_manager: VirtualServerManager,
    tenant_id: str,
    mutation_gateway: Optional[MutationGateway] = None,
) -> list:
    """Builder Agent icin tum tool'lari olustur ve dondur."""

    # =========================================================================
    # ONTOLOGY TOOLS
    # =========================================================================

    @tool
    async def search_existing_skills(
        query: str,
        context: str = "",
        limit: int = 5,
    ) -> str:
        """Ontology DB'de mevcut skill'leri arar.
        Skill adina, aciklamasina veya context'e gore arama yapar.
        Sonuc: Bulunan skill'lerin listesi (id, name, description, category, effectiveness).

        Args:
            query: Aranacak metin (ornek: 'sigorta police OCR')
            context: Domain context filtresi (ornek: 'insurance', 'maintenance')
            limit: Maksimum sonuc sayisi
        """
        skills = []

        if context:
            found = await reasoner.find_skills_by_context(
                context_name=context, tenant_id=tenant_id, limit=limit
            )
            skills.extend(found)

        if not skills:
            result = await db.execute_query("""
                CALL db.index.fulltext.queryNodes('skill_fulltext', $query)
                YIELD node, score
                WHERE node.is_global = true OR node.tenant_id = $tenant_id
                RETURN node {.id, .name, .description, .skill_category, .effectiveness_score} AS skill,
                       score
                ORDER BY score DESC
                LIMIT $limit
            """, {"query": query, "tenant_id": tenant_id, "limit": limit})
            skills = [r["skill"] for r in result]

        if not skills:
            return json.dumps({"found": 0, "skills": [], "message": "Skill bulunamadi"}, ensure_ascii=False)

        return json.dumps({
            "found": len(skills),
            "skills": [s if isinstance(s, dict) else s.to_dict() for s in skills],
        }, ensure_ascii=False, default=str)

    @tool
    async def search_contexts(query: str = "") -> str:
        """Mevcut domain context'lerini listeler.
        Context'ler: insurance, maintenance, legal, financial, document_processing.

        Args:
            query: Filtrelemek icin keyword (bos birak hepsini gormek icin)
        """
        q = """
            MATCH (c:Context)
            RETURN c {.id, .name, .description, .domain_keywords} AS context
            ORDER BY c.name
        """
        result = await db.execute_query(q)
        contexts = [r["context"] for r in result]

        if query:
            query_lower = query.lower()
            contexts = [
                c for c in contexts
                if query_lower in (c.get("name", "") + " " + c.get("description", "")).lower()
            ]

        return json.dumps({"contexts": contexts}, ensure_ascii=False, default=str)

    @tool
    async def create_goal(
        name: str,
        description: str,
        goal_type: str = "extraction",
        natural_language_query: str = "",
    ) -> str:
        """Yeni bir hedef (Goal) olusturur.
        Goal, agent'in neyi basarmak istedigini tanimlar.

        Args:
            name: Goal adi (ornek: 'Sigorta Police Analizi')
            description: Detayli aciklama
            goal_type: extraction, analysis, search, workflow
            natural_language_query: Dogal dilde sorgu (ornek: 'Policeden musteri bilgilerini cikar')
        """
        import uuid
        goal_id = f"goal-{uuid.uuid4().hex[:12]}"

        props = {
            "id": goal_id, "name": name, "description": description,
            "goal_type": goal_type, "natural_language_query": natural_language_query,
            "status": "active", "tenant_id": tenant_id,
        }

        if mutation_gateway:
            await mutation_gateway.create_node(
                "Goal", props,
                llm_prompt=f"Create goal: {name}",
            )
        else:
            await db.execute_query("""
                CREATE (g:Goal {
                    id: $goal_id, name: $name, description: $description,
                    goal_type: $goal_type, natural_language_query: $nlq,
                    status: 'active', tenant_id: $tenant_id,
                    created_at: datetime()
                })
                RETURN g.id AS id
            """, {
                "goal_id": goal_id, "name": name, "description": description,
                "goal_type": goal_type, "nlq": natural_language_query, "tenant_id": tenant_id,
            }, write=True)

        return json.dumps({"goal_id": goal_id, "name": name, "status": "created"}, ensure_ascii=False)

    @tool
    async def create_skill(
        name: str,
        description: str,
        skill_category: str = "extraction",
        prompt_template: str = "",
        context_ids: Optional[List[str]] = None,
    ) -> str:
        """Yeni bir skill olusturur. Skill, agent'in bir yetenegi/kapasitesidir.
        OCR, entity extraction, relationship mapping gibi islemleri tanimlar.

        Args:
            name: Skill adi (ornek: 'Sigorta Police OCR')
            description: Skill ne yapar
            skill_category: ocr, extraction, relationship_mapping, query, workflow
            prompt_template: LLM prompt template (entity cikarma talimatlari)
            context_ids: Iliskili context ID'leri listesi
        """
        from ..models import SkillCreate

        skill_data = SkillCreate(
            name=name,
            description=description,
            skill_category=skill_category,
            prompt_template=prompt_template,
            tenant_id=tenant_id,
            is_global=False,
            context_ids=context_ids or [],
        )
        skill_id = await skill_registry.create_skill(skill_data)

        if mutation_gateway:
            from ..event_store.models import EventType, GraphEvent
            await mutation_gateway.event_store.record_event(GraphEvent(
                tenant_id=tenant_id,
                user_id=mutation_gateway.user_id,
                event_type=EventType.CREATE_NODE,
                entity_type="Skill",
                entity_id=skill_id,
                after_state={"id": skill_id, "name": name, "category": skill_category},
                llm_prompt=f"Create skill: {name}",
                llm_model=mutation_gateway.llm_model,
                session_id=mutation_gateway.session_id,
            ))

        return json.dumps({
            "skill_id": skill_id, "name": name, "category": skill_category,
            "status": "created",
        }, ensure_ascii=False)

    @tool
    async def create_entity_schema(
        entity_type: str,
        description: str,
        properties: Dict[str, Any],
        context: str = "document_processing",
    ) -> str:
        """Entity schema olusturur. Belgelerden cikarilacak varliklarin yapisini tanimlar.

        Args:
            entity_type: Entity tipi (ornek: 'Policy', 'Customer', 'Invoice')
            description: Entity aciklamasi
            properties: Property tanimlari dict (ornek: {"policy_no": {"type": "string", "required": true}})
            context: Domain context
        """
        import uuid
        schema_id = f"es-{uuid.uuid4().hex[:12]}"

        props = {
            "id": schema_id, "entity_type": entity_type,
            "description": description,
            "properties": json.dumps(properties, ensure_ascii=False),
            "context": context,
        }

        if mutation_gateway:
            await mutation_gateway.create_node(
                "EntitySchema", props,
                llm_prompt=f"Create entity schema: {entity_type}",
            )
        else:
            await db.execute_query("""
                CREATE (es:EntitySchema {
                    id: $schema_id, entity_type: $entity_type,
                    description: $description, properties: $properties,
                    context: $context, created_at: datetime()
                })
                RETURN es.id AS id
            """, {
                "schema_id": schema_id, "entity_type": entity_type,
                "description": description,
                "properties": json.dumps(properties, ensure_ascii=False),
                "context": context,
            }, write=True)

        return json.dumps({
            "schema_id": schema_id, "entity_type": entity_type, "status": "created",
        }, ensure_ascii=False)

    @tool
    async def create_relationship_schema(
        relationship_type: str,
        source_entity: str,
        target_entity: str,
        description: str = "",
        cardinality: str = "many_to_many",
        properties: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Relationship schema olusturur. Entity'ler arasi iliskilerin yapisini tanimlar.

        Args:
            relationship_type: Iliski tipi (ornek: 'HAS_POLICY', 'WORKS_AT')
            source_entity: Kaynak entity tipi
            target_entity: Hedef entity tipi
            description: Iliski aciklamasi
            cardinality: one_to_one, one_to_many, many_to_many
            properties: Iliski property'leri
        """
        import uuid
        schema_id = f"rs-{uuid.uuid4().hex[:12]}"

        props = {
            "id": schema_id, "relationship_type": relationship_type,
            "source_entity": source_entity, "target_entity": target_entity,
            "description": description, "cardinality": cardinality,
            "properties": json.dumps(properties or {}, ensure_ascii=False),
        }

        if mutation_gateway:
            await mutation_gateway.create_node(
                "RelationshipSchema", props,
                llm_prompt=f"Create relationship schema: {relationship_type}",
            )
        else:
            await db.execute_query("""
                CREATE (rs:RelationshipSchema {
                    id: $schema_id, relationship_type: $rel_type,
                    source_entity: $source, target_entity: $target,
                    description: $description, cardinality: $cardinality,
                    properties: $properties, created_at: datetime()
                })
                RETURN rs.id AS id
            """, {
                "schema_id": schema_id, "rel_type": relationship_type,
                "source": source_entity, "target": target_entity,
                "description": description, "cardinality": cardinality,
                "properties": json.dumps(properties or {}, ensure_ascii=False),
            }, write=True)

        return json.dumps({
            "schema_id": schema_id, "relationship_type": relationship_type,
            "source": source_entity, "target": target_entity, "status": "created",
        }, ensure_ascii=False)

    @tool
    async def link_skill_to_schemas(
        skill_id: str,
        entity_schema_ids: List[str],
        relationship_schema_ids: Optional[List[str]] = None,
        goal_id: Optional[str] = None,
    ) -> str:
        """Skill'i entity/relationship schemalarina ve goal'e baglar.

        Args:
            skill_id: Skill ID
            entity_schema_ids: Baglancak EntitySchema ID listesi
            relationship_schema_ids: Baglancak RelationshipSchema ID listesi
            goal_id: Baglanacak Goal ID (opsiyonel)
        """
        linked = {"entities": 0, "relationships": 0, "goal": False}

        for es_id in entity_schema_ids:
            if mutation_gateway:
                await mutation_gateway.create_relationship(
                    "Skill", skill_id, "EntitySchema", es_id, "EXTRACTS",
                    llm_prompt=f"Link skill {skill_id} to entity schema {es_id}",
                )
            else:
                await db.execute_query("""
                    MATCH (s:Skill {id: $skill_id}), (es:EntitySchema {id: $es_id})
                    MERGE (s)-[:EXTRACTS]->(es)
                """, {"skill_id": skill_id, "es_id": es_id}, write=True)
            linked["entities"] += 1

        for rs_id in (relationship_schema_ids or []):
            if mutation_gateway:
                await mutation_gateway.create_relationship(
                    "Skill", skill_id, "RelationshipSchema", rs_id, "CREATES",
                    llm_prompt=f"Link skill {skill_id} to rel schema {rs_id}",
                )
            else:
                await db.execute_query("""
                    MATCH (s:Skill {id: $skill_id}), (rs:RelationshipSchema {id: $rs_id})
                    MERGE (s)-[:CREATES]->(rs)
                """, {"skill_id": skill_id, "rs_id": rs_id}, write=True)
            linked["relationships"] += 1

        if goal_id:
            if mutation_gateway:
                await mutation_gateway.create_relationship(
                    "Goal", goal_id, "Skill", skill_id, "ACHIEVABLE_BY",
                    properties={"priority": 1},
                    llm_prompt=f"Link goal {goal_id} to skill {skill_id}",
                )
            else:
                await db.execute_query("""
                    MATCH (g:Goal {id: $goal_id}), (s:Skill {id: $skill_id})
                    MERGE (g)-[:ACHIEVABLE_BY {priority: 1}]->(s)
                """, {"goal_id": goal_id, "skill_id": skill_id}, write=True)
            linked["goal"] = True

        return json.dumps({"skill_id": skill_id, "linked": linked}, ensure_ascii=False)

    # =========================================================================
    # GATEWAY TOOLS
    # =========================================================================

    @tool
    async def list_gateway_tools() -> str:
        """MCP Gateway'de kayitli tum tool'lari listeler.
        Her tool'un id, name, description, type bilgilerini dondurur.
        """
        try:
            tools = await gateway.list_tools()
            summary = [
                {"id": t.get("id"), "name": t.get("name"), "type": t.get("integrationType", "MCP")}
                for t in tools
            ]
            return json.dumps({"tool_count": len(tools), "tools": summary}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    @tool
    async def list_virtual_servers() -> str:
        """MCP Gateway'deki virtual server'lari listeler.
        Her server'in id, name, tool sayisi bilgilerini dondurur.
        """
        try:
            servers = await gateway.list_virtual_servers()
            summary = [
                {"id": s.get("id"), "name": s.get("name"), "description": s.get("description", "")}
                for s in servers
            ]
            return json.dumps({"server_count": len(servers), "servers": summary}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    @tool
    async def deploy_agent_to_gateway(
        agent_id: str,
        agent_name: str = "",
    ) -> str:
        """Agent'i MCP Gateway'e deploy eder.
        Tenant icin virtual server olusturur, skill'leri tool olarak kaydeder.

        Args:
            agent_id: Deploy edilecek agent ID
            agent_name: Agent adi (log icin)
        """
        try:
            result = await vs_manager.deploy_agent(agent_id, tenant_id)
            return json.dumps(result, ensure_ascii=False, default=str)
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

    @tool
    async def create_agent_definition(
        name: str,
        description: str,
        purpose: str,
        skill_ids: List[str],
        goal_ids: Optional[List[str]] = None,
    ) -> str:
        """Agent tanimi olusturur. Skill ve goal'leri baglar.

        Args:
            name: Agent adi (ornek: 'Sigorta Police Asistani')
            description: Agent aciklamasi
            purpose: Agent'in amaci (kullanici hedefinden turetilir)
            skill_ids: Agent'a atanacak Skill ID'leri
            goal_ids: Agent'in takip edecegi Goal ID'leri
        """
        import uuid
        agent_id = f"agent-{uuid.uuid4().hex[:12]}"

        props = {
            "id": agent_id, "name": name, "description": description,
            "purpose": purpose, "status": "draft", "tenant_id": tenant_id,
        }

        if mutation_gateway:
            await mutation_gateway.create_node(
                "AgentDefinition", props,
                llm_prompt=f"Create agent: {name}",
            )
            for sid in skill_ids:
                await mutation_gateway.create_relationship(
                    "AgentDefinition", agent_id, "Skill", sid, "HAS_SKILL",
                    properties={"enabled": True},
                    llm_prompt=f"Assign skill {sid} to agent {name}",
                )
            for gid in (goal_ids or []):
                await mutation_gateway.create_relationship(
                    "AgentDefinition", agent_id, "Goal", gid, "PURSUES",
                    properties={"is_primary": True},
                    llm_prompt=f"Agent {name} pursues goal {gid}",
                )
        else:
            await db.execute_query("""
                CREATE (a:AgentDefinition {
                    id: $agent_id, name: $name, description: $description,
                    purpose: $purpose, status: 'draft', tenant_id: $tenant_id,
                    created_at: datetime()
                })
                RETURN a.id AS id
            """, {
                "agent_id": agent_id, "name": name, "description": description,
                "purpose": purpose, "tenant_id": tenant_id,
            }, write=True)

            for sid in skill_ids:
                await db.execute_query("""
                    MATCH (a:AgentDefinition {id: $agent_id}), (s:Skill {id: $sid})
                    MERGE (a)-[:HAS_SKILL {assigned_at: datetime(), enabled: true}]->(s)
                """, {"agent_id": agent_id, "sid": sid}, write=True)

            for gid in (goal_ids or []):
                await db.execute_query("""
                    MATCH (a:AgentDefinition {id: $agent_id}), (g:Goal {id: $gid})
                    MERGE (a)-[:PURSUES {is_primary: true}]->(g)
                """, {"agent_id": agent_id, "gid": gid}, write=True)

        return json.dumps({
            "agent_id": agent_id, "name": name, "status": "draft",
            "skills": skill_ids, "goals": goal_ids or [],
        }, ensure_ascii=False)

    # =========================================================================
    # KNOWLEDGE DB TOOLS
    # =========================================================================

    @tool
    async def check_knowledge_db_schema(
        neo4j_uri: str = "",
    ) -> str:
        """Knowledge DB'nin (Documents DB) mevcut graph semasini kontrol eder.
        Mevcut label'lar, relationship tipleri ve property'leri dondurur.
        Bu bilgi yeni entity/relationship schema tanimlarken tekrardan kacinmak icin kullanilir.
        """
        try:
            result = await db.execute_query("""
                CALL db.labels() YIELD label
                RETURN collect(label) AS labels
            """)
            labels = result[0]["labels"] if result else []

            result2 = await db.execute_query("""
                CALL db.relationshipTypes() YIELD relationshipType
                RETURN collect(relationshipType) AS types
            """)
            rel_types = result2[0]["types"] if result2 else []

            return json.dumps({
                "labels": labels,
                "relationship_types": rel_types,
                "label_count": len(labels),
                "relationship_type_count": len(rel_types),
            }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    # =========================================================================
    # COLLECT ALL TOOLS
    # =========================================================================

    return [
        search_existing_skills,
        search_contexts,
        create_goal,
        create_skill,
        create_entity_schema,
        create_relationship_schema,
        link_skill_to_schemas,
        list_gateway_tools,
        list_virtual_servers,
        deploy_agent_to_gateway,
        create_agent_definition,
        check_knowledge_db_schema,
    ]
