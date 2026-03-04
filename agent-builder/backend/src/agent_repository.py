"""
Agent Repository
=================

PostgreSQL-backed repository for AgentDefinition, Skill, Goal,
EntitySchema, RelationshipSchema, Context and junction tables.
Replaces Neo4j nodes of the same labels.
"""

import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from .event_store.postgres_client import PostgresClient

logger = logging.getLogger(__name__)


class AgentRepository:
    """Async CRUD for agent_definitions, skills, goals, schemas, contexts and junction tables."""

    def __init__(self, pg: PostgresClient):
        self.pg = pg

    # =========================================================================
    # AGENT DEFINITIONS
    # =========================================================================

    async def create_agent(self, data: Dict[str, Any]) -> str:
        agent_id = data.get("id", f"agent-{uuid.uuid4().hex[:12]}")
        config = data.get("config", {})
        if isinstance(config, dict):
            config = json.dumps(config, ensure_ascii=False)
        await self.pg.execute(
            """
            INSERT INTO agent_definitions
                (id, name, description, purpose, agent_type, status, tenant_id,
                 workspace_id, config, mcp_virtual_server_id)
            VALUES ($1,$2,$3,$4,$5,$6,$7,NULLIF($8,''),$9,NULLIF($10,''))
            """,
            agent_id,
            data.get("name", ""),
            data.get("description", ""),
            data.get("purpose", ""),
            data.get("agent_type", "workspace_agent"),
            data.get("status", "draft"),
            data.get("tenant_id", "default"),
            data.get("workspace_id", ""),
            config,
            data.get("mcp_virtual_server_id", ""),
        )
        return agent_id

    async def get_agent(
        self, agent_id: str, tenant_id: str = ""
    ) -> Optional[Dict[str, Any]]:
        if tenant_id:
            row = await self.pg.fetchrow(
                "SELECT * FROM agent_definitions WHERE id = $1 AND tenant_id = $2",
                agent_id, tenant_id,
            )
        else:
            row = await self.pg.fetchrow(
                "SELECT * FROM agent_definitions WHERE id = $1",
                agent_id,
            )
        if not row:
            return None
        d = dict(row)
        if isinstance(d.get("config"), str):
            try:
                d["config"] = json.loads(d["config"])
            except (json.JSONDecodeError, TypeError):
                d["config"] = {}
        return d

    async def update_agent(
        self,
        agent_id: str,
        updates: Dict[str, Any],
    ) -> None:
        sets = ["updated_at = NOW()"]
        params: list = []
        idx = 1

        for k, v in updates.items():
            if k in ("id",):
                continue
            if k == "config" and isinstance(v, dict):
                v = json.dumps(v, ensure_ascii=False)
            sets.append(f"{k} = ${idx}")
            params.append(v)
            idx += 1

        if len(sets) == 1:
            return

        params.append(agent_id)
        query = f"UPDATE agent_definitions SET {', '.join(sets)} WHERE id = ${idx}"
        await self.pg.execute(query, *params)

    async def list_agents(
        self,
        tenant_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        rows = await self.pg.fetch(
            """
            SELECT id, name, description, agent_type, status,
                   workspace_id, created_at, updated_at
            FROM agent_definitions
            WHERE tenant_id = $1
            ORDER BY created_at DESC
            LIMIT $2 OFFSET $3
            """,
            tenant_id, limit, offset,
        )
        return [dict(r) for r in rows]

    async def get_agent_tenant(self, agent_id: str) -> str:
        row = await self.pg.fetchrow(
            "SELECT tenant_id FROM agent_definitions WHERE id = $1", agent_id,
        )
        return row["tenant_id"] if row else "default"

    # =========================================================================
    # SKILLS
    # =========================================================================

    async def create_skill(self, data: Dict[str, Any]) -> str:
        skill_id = data.get("id", f"skill-{uuid.uuid4().hex[:12]}")
        input_schema = data.get("input_schema", {})
        output_schema = data.get("output_schema", {})
        if isinstance(input_schema, dict):
            input_schema = json.dumps(input_schema)
        if isinstance(output_schema, dict):
            output_schema = json.dumps(output_schema)

        await self.pg.execute(
            """
            INSERT INTO skills
                (id, name, description, skill_category, prompt_template,
                 input_schema, output_schema, version, effectiveness_score,
                 usage_count, is_global, tenant_id)
            VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7::jsonb,$8,$9,$10,$11,$12)
            """,
            skill_id,
            data.get("name", ""),
            data.get("description", ""),
            data.get("skill_category", "extraction"),
            data.get("prompt_template", ""),
            input_schema,
            output_schema,
            data.get("version", 1),
            data.get("effectiveness_score", 0.5),
            data.get("usage_count", 0),
            data.get("is_global", False),
            data.get("tenant_id", "default"),
        )
        return skill_id

    async def get_skill(self, skill_id: str) -> Optional[Dict[str, Any]]:
        row = await self.pg.fetchrow(
            "SELECT * FROM skills WHERE id = $1", skill_id,
        )
        if not row:
            return None
        d = dict(row)
        for k in ("input_schema", "output_schema"):
            if isinstance(d.get(k), str):
                try:
                    d[k] = json.loads(d[k])
                except (json.JSONDecodeError, TypeError):
                    d[k] = {}
        return d

    async def update_skill(
        self,
        skill_id: str,
        updates: Dict[str, Any],
    ) -> bool:
        allowed = {"name", "description", "prompt_template", "input_schema", "output_schema"}
        safe = {k: v for k, v in updates.items() if k in allowed}
        if not safe:
            return False

        for k in ("input_schema", "output_schema"):
            if k in safe and isinstance(safe[k], dict):
                safe[k] = json.dumps(safe[k])

        sets = ["updated_at = NOW()"]
        params: list = []
        idx = 1
        for k, v in safe.items():
            sets.append(f"{k} = ${idx}")
            params.append(v)
            idx += 1

        params.append(skill_id)
        query = f"UPDATE skills SET {', '.join(sets)} WHERE id = ${idx}"
        result = await self.pg.execute(query, *params)
        return "UPDATE" in result

    async def delete_skill(self, skill_id: str, tenant_id: str) -> bool:
        result = await self.pg.execute(
            "DELETE FROM skills WHERE id = $1 AND tenant_id = $2 AND is_global = false",
            skill_id, tenant_id,
        )
        return "DELETE 1" in result

    async def list_skills(
        self,
        tenant_id: str,
        include_global: bool = True,
        category: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        conditions = []
        params: list = []
        idx = 1

        conditions.append(f"(tenant_id = ${idx}")
        params.append(tenant_id)
        idx += 1

        if include_global:
            conditions[-1] += " OR is_global = true)"
        else:
            conditions[-1] += ")"

        if category:
            conditions.append(f"skill_category = ${idx}")
            params.append(category)
            idx += 1

        params.extend([limit, offset])
        query = f"""
            SELECT id, name, description, skill_category, effectiveness_score,
                   is_global, created_at
            FROM skills
            WHERE {' AND '.join(conditions)}
            ORDER BY effectiveness_score DESC, name
            LIMIT ${idx} OFFSET ${idx + 1}
        """
        rows = await self.pg.fetch(query, *params)
        return [dict(r) for r in rows]

    async def increment_skill_version(self, skill_id: str) -> int:
        row = await self.pg.fetchrow(
            """
            UPDATE skills SET version = version + 1, updated_at = NOW()
            WHERE id = $1 RETURNING version
            """,
            skill_id,
        )
        return row["version"] if row else 1

    # =========================================================================
    # SKILL-SCHEMA JUNCTIONS
    # =========================================================================

    async def link_skill_entity_schema(self, skill_id: str, schema_id: str) -> None:
        await self.pg.execute(
            "INSERT INTO skill_entity_schemas (skill_id, schema_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            skill_id, schema_id,
        )

    async def link_skill_relationship_schema(self, skill_id: str, schema_id: str) -> None:
        await self.pg.execute(
            "INSERT INTO skill_relationship_schemas (skill_id, schema_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            skill_id, schema_id,
        )

    async def get_skill_entity_schemas(self, skill_id: str) -> List[Dict[str, Any]]:
        rows = await self.pg.fetch(
            """
            SELECT es.* FROM entity_schemas es
            JOIN skill_entity_schemas ses ON ses.schema_id = es.id
            WHERE ses.skill_id = $1
            """,
            skill_id,
        )
        return [dict(r) for r in rows]

    async def get_skill_relationship_schemas(self, skill_id: str) -> List[Dict[str, Any]]:
        rows = await self.pg.fetch(
            """
            SELECT rs.* FROM relationship_schemas rs
            JOIN skill_relationship_schemas srs ON srs.schema_id = rs.id
            WHERE srs.skill_id = $1
            """,
            skill_id,
        )
        return [dict(r) for r in rows]

    async def get_skill_for_execution(self, skill_id: str) -> Optional[Dict[str, Any]]:
        skill = await self.get_skill(skill_id)
        if not skill:
            return None
        entity_schemas = await self.get_skill_entity_schemas(skill_id)
        rel_schemas = await self.get_skill_relationship_schemas(skill_id)

        for es in entity_schemas:
            if isinstance(es.get("properties"), str):
                try:
                    es["properties"] = json.loads(es["properties"])
                except (json.JSONDecodeError, TypeError):
                    es["properties"] = {}

        return {
            **skill,
            "entity_schemas": entity_schemas,
            "relationship_schemas": rel_schemas,
        }

    # =========================================================================
    # AGENT-SKILL / AGENT-GOAL JUNCTIONS
    # =========================================================================

    async def link_agent_skill(self, agent_id: str, skill_id: str, priority: int = 0) -> None:
        await self.pg.execute(
            "INSERT INTO agent_skills (agent_id, skill_id, priority) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
            agent_id, skill_id, priority,
        )

    async def link_agent_goal(self, agent_id: str, goal_id: str) -> None:
        await self.pg.execute(
            "INSERT INTO agent_goals (agent_id, goal_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            agent_id, goal_id,
        )

    async def get_agent_skills_full(self, agent_id: str) -> tuple:
        """Load agent skills with entity/relationship schemas (replaces Neo4j HAS_SKILL->EXTRACTS->CREATES)."""
        skill_rows = await self.pg.fetch(
            """
            SELECT s.* FROM skills s
            JOIN agent_skills ags ON ags.skill_id = s.id
            WHERE ags.agent_id = $1
            ORDER BY ags.priority DESC
            """,
            agent_id,
        )

        skills = []
        all_es: list = []
        all_rs: list = []
        seen_es: set = set()
        seen_rs: set = set()

        for row in skill_rows:
            s = dict(row)
            for k in ("input_schema", "output_schema"):
                if isinstance(s.get(k), str):
                    try:
                        s[k] = json.loads(s[k])
                    except (json.JSONDecodeError, TypeError):
                        s[k] = {}
            skills.append(s)

            es_list = await self.get_skill_entity_schemas(s["id"])
            for es in es_list:
                eid = es.get("id", "")
                if eid and eid not in seen_es:
                    if isinstance(es.get("properties"), str):
                        try:
                            es["properties"] = json.loads(es["properties"])
                        except (json.JSONDecodeError, TypeError):
                            es["properties"] = {}
                    all_es.append(es)
                    seen_es.add(eid)

            rs_list = await self.get_skill_relationship_schemas(s["id"])
            for rs in rs_list:
                rid = rs.get("id", "")
                if rid and rid not in seen_rs:
                    all_rs.append(rs)
                    seen_rs.add(rid)

        return skills, all_es, all_rs

    # =========================================================================
    # GOALS
    # =========================================================================

    async def create_goal(self, data: Dict[str, Any]) -> str:
        goal_id = data.get("id", f"goal-{uuid.uuid4().hex[:12]}")
        await self.pg.execute(
            """
            INSERT INTO goals (id, name, description, goal_type, natural_language_query,
                               success_criteria, status, tenant_id)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            """,
            goal_id,
            data.get("name", ""),
            data.get("description", ""),
            data.get("goal_type", "extraction"),
            data.get("natural_language_query", ""),
            data.get("success_criteria", ""),
            data.get("status", "active"),
            data.get("tenant_id", "default"),
        )
        return goal_id

    async def get_goal(self, goal_id: str) -> Optional[Dict[str, Any]]:
        row = await self.pg.fetchrow("SELECT * FROM goals WHERE id = $1", goal_id)
        return dict(row) if row else None

    # =========================================================================
    # ENTITY & RELATIONSHIP SCHEMAS
    # =========================================================================

    async def create_entity_schema(self, data: Dict[str, Any]) -> str:
        schema_id = data.get("id", f"es-{uuid.uuid4().hex[:12]}")
        props = data.get("properties", {})
        if isinstance(props, dict):
            props = json.dumps(props, ensure_ascii=False)
        validation = data.get("validation_rules", {})
        if isinstance(validation, dict):
            validation = json.dumps(validation, ensure_ascii=False)

        await self.pg.execute(
            """
            INSERT INTO entity_schemas (id, entity_type, description, properties,
                                        validation_rules, examples, context, tenant_id)
            VALUES ($1,$2,$3,$4::jsonb,$5::jsonb,$6,NULLIF($7,''),$8)
            ON CONFLICT (id) DO NOTHING
            """,
            schema_id,
            data.get("entity_type", ""),
            data.get("description", ""),
            props,
            validation,
            data.get("examples", ""),
            data.get("context", ""),
            data.get("tenant_id", "default"),
        )
        return schema_id

    async def create_relationship_schema(self, data: Dict[str, Any]) -> str:
        schema_id = data.get("id", f"rs-{uuid.uuid4().hex[:12]}")
        props = data.get("properties", {})
        if isinstance(props, dict):
            props = json.dumps(props, ensure_ascii=False)

        await self.pg.execute(
            """
            INSERT INTO relationship_schemas
                (id, relationship_type, description, source_entity, target_entity,
                 properties, cardinality, bidirectional, tenant_id)
            VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7,$8,$9)
            ON CONFLICT (id) DO NOTHING
            """,
            schema_id,
            data.get("relationship_type", ""),
            data.get("description", ""),
            data.get("source_entity", ""),
            data.get("target_entity", ""),
            props,
            data.get("cardinality", "many-to-many"),
            data.get("bidirectional", False),
            data.get("tenant_id", "default"),
        )
        return schema_id

    async def get_entity_schema(self, schema_id: str) -> Optional[Dict[str, Any]]:
        row = await self.pg.fetchrow("SELECT * FROM entity_schemas WHERE id = $1", schema_id)
        if not row:
            return None
        d = dict(row)
        if isinstance(d.get("properties"), str):
            try:
                d["properties"] = json.loads(d["properties"])
            except (json.JSONDecodeError, TypeError):
                d["properties"] = {}
        return d

    async def get_relationship_schema(self, schema_id: str) -> Optional[Dict[str, Any]]:
        row = await self.pg.fetchrow("SELECT * FROM relationship_schemas WHERE id = $1", schema_id)
        return dict(row) if row else None

    # =========================================================================
    # CONTEXTS
    # =========================================================================

    async def create_context(self, data: Dict[str, Any]) -> str:
        ctx_id = data.get("id", f"ctx-{uuid.uuid4().hex[:12]}")
        await self.pg.execute(
            """
            INSERT INTO contexts (id, name, description, domain_keywords, parent_context, tenant_id)
            VALUES ($1,$2,$3,$4,NULLIF($5,''),$6)
            ON CONFLICT (id) DO NOTHING
            """,
            ctx_id,
            data.get("name", ""),
            data.get("description", ""),
            data.get("domain_keywords", []),
            data.get("parent_context", ""),
            data.get("tenant_id", "default"),
        )
        return ctx_id
