"""
Knowledge Store
===============

PostgreSQL-backed bilgi deposu.
Agent'in ontolojisini, domain kurallarini ve ogrenilen pattern'leri
versiyonlanmis olarak saklar.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Optional

from .ontology_model import AgentOntology

logger = logging.getLogger(__name__)

MIGRATION_SQL = """
CREATE TABLE IF NOT EXISTS agent_knowledge (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id VARCHAR(128) NOT NULL,
    knowledge_type VARCHAR(64) NOT NULL,
    key VARCHAR(256) NOT NULL,
    value JSONB NOT NULL DEFAULT '{}',
    version INT NOT NULL DEFAULT 1,
    confidence FLOAT NOT NULL DEFAULT 0.5,
    source VARCHAR(64) NOT NULL DEFAULT 'conversation',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE(agent_id, knowledge_type, key, version)
);

CREATE INDEX IF NOT EXISTS idx_ak_agent ON agent_knowledge(agent_id);
CREATE INDEX IF NOT EXISTS idx_ak_type ON agent_knowledge(agent_id, knowledge_type);

CREATE TABLE IF NOT EXISTS ontology_discoveries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id VARCHAR(128) NOT NULL,
    discovery_type VARCHAR(32) NOT NULL,
    name VARCHAR(256) NOT NULL,
    sample_count INT NOT NULL DEFAULT 1,
    first_seen_doc VARCHAR(256),
    sample_properties JSONB NOT NULL DEFAULT '[]',
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE(agent_id, discovery_type, name)
);

CREATE INDEX IF NOT EXISTS idx_disc_agent ON ontology_discoveries(agent_id);
CREATE INDEX IF NOT EXISTS idx_disc_status ON ontology_discoveries(agent_id, status);

-- Agent lifecycle: tracks soft-delete state per agent.
-- A row exists for every known agent. deleted_at IS NULL = active; NOT NULL = soft-deleted.
-- Hard delete (purge) removes the row from this table AND every related row in agent_knowledge.
CREATE TABLE IF NOT EXISTS agent_lifecycle (
    agent_id VARCHAR(128) PRIMARY KEY,
    deleted_at TIMESTAMP NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_lifecycle_deleted ON agent_lifecycle(deleted_at);
"""


class KnowledgeStore:
    """PostgreSQL CRUD for agent knowledge."""

    def __init__(self, pg):
        """pg: asyncpg pool or connection-like object with execute/fetch/fetchrow."""
        self.pg = pg

    async def ensure_table(self) -> None:
        await self.pg.execute(MIGRATION_SQL)

    # ─── LIFECYCLE (soft delete / restore / purge) ──────────────────

    async def ensure_lifecycle_row(self, agent_id: str) -> None:
        """Insert an active lifecycle row if one doesn't already exist."""
        await self.pg.execute(
            """
            INSERT INTO agent_lifecycle (agent_id, deleted_at)
            VALUES ($1, NULL)
            ON CONFLICT (agent_id) DO NOTHING
            """,
            agent_id,
        )

    async def is_deleted(self, agent_id: str) -> bool:
        """True if the agent is soft-deleted."""
        row = await self.pg.fetchrow(
            "SELECT deleted_at FROM agent_lifecycle WHERE agent_id = $1",
            agent_id,
        )
        if not row:
            return False
        return row["deleted_at"] is not None

    async def soft_delete_agent(self, agent_id: str) -> None:
        """Mark the agent as soft-deleted; data is preserved and can be restored."""
        await self.pg.execute(
            """
            INSERT INTO agent_lifecycle (agent_id, deleted_at)
            VALUES ($1, NOW())
            ON CONFLICT (agent_id) DO UPDATE SET deleted_at = NOW()
            """,
            agent_id,
        )

    async def restore_agent(self, agent_id: str) -> None:
        """Clear the soft-delete flag, bringing the agent back."""
        await self.pg.execute(
            """
            INSERT INTO agent_lifecycle (agent_id, deleted_at)
            VALUES ($1, NULL)
            ON CONFLICT (agent_id) DO UPDATE SET deleted_at = NULL
            """,
            agent_id,
        )

    async def purge_agent(self, agent_id: str) -> None:
        """Hard delete: remove every trace of the agent from all related tables."""
        await self.pg.execute(
            "DELETE FROM agent_knowledge WHERE agent_id = $1", agent_id
        )
        await self.pg.execute(
            "DELETE FROM ontology_discoveries WHERE agent_id = $1", agent_id
        )
        await self.pg.execute(
            "DELETE FROM agent_lifecycle WHERE agent_id = $1", agent_id
        )

    async def list_active_agent_ids(self, limit: int = 200) -> list[str]:
        """Return agent IDs that are not soft-deleted (joined with identity)."""
        rows = await self.pg.fetch(
            """
            SELECT DISTINCT ak.agent_id
            FROM agent_knowledge ak
            LEFT JOIN agent_lifecycle al ON al.agent_id = ak.agent_id
            WHERE ak.knowledge_type = 'identity'
              AND al.deleted_at IS NULL
            ORDER BY ak.agent_id
            LIMIT $1
            """,
            limit,
        )
        return [r["agent_id"] for r in rows]

    async def list_deleted_agents(self, limit: int = 200) -> list[dict[str, Any]]:
        """Return soft-deleted agents with their last-known identity & deletion time."""
        rows = await self.pg.fetch(
            """
            SELECT al.agent_id,
                   al.deleted_at,
                   ak.value AS identity_value
            FROM agent_lifecycle al
            LEFT JOIN LATERAL (
                SELECT value
                FROM agent_knowledge
                WHERE agent_id = al.agent_id
                  AND knowledge_type = 'identity'
                  AND key = 'main'
                ORDER BY version DESC
                LIMIT 1
            ) ak ON TRUE
            WHERE al.deleted_at IS NOT NULL
            ORDER BY al.deleted_at DESC
            LIMIT $1
            """,
            limit,
        )

        out: list[dict[str, Any]] = []
        for r in rows:
            value = r["identity_value"]
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except (json.JSONDecodeError, ValueError):
                    value = {}
            value = value or {}
            out.append({
                "agent_id": r["agent_id"],
                "deleted_at": r["deleted_at"].isoformat() if r["deleted_at"] else None,
                "name": value.get("name", "Agent"),
                "purpose": value.get("purpose", ""),
            })
        return out

    # ─── GENERIC OPS ────────────────────────────────────────────────

    async def get(
        self,
        agent_id: str,
        knowledge_type: str,
        key: str,
    ) -> Optional[dict[str, Any]]:
        """En son versiyonu getir."""
        row = await self.pg.fetchrow(
            """
            SELECT * FROM agent_knowledge
            WHERE agent_id = $1 AND knowledge_type = $2 AND key = $3
            ORDER BY version DESC LIMIT 1
            """,
            agent_id, knowledge_type, key,
        )
        return dict(row) if row else None

    async def get_all(
        self,
        agent_id: str,
        knowledge_type: str = "",
    ) -> list[dict[str, Any]]:
        """Bir agent'in tum bilgilerini (veya bir tipe gore) getir. Her key icin son versiyon."""
        if knowledge_type:
            rows = await self.pg.fetch(
                """
                SELECT DISTINCT ON (key) *
                FROM agent_knowledge
                WHERE agent_id = $1 AND knowledge_type = $2
                ORDER BY key, version DESC
                """,
                agent_id, knowledge_type,
            )
        else:
            rows = await self.pg.fetch(
                """
                SELECT DISTINCT ON (knowledge_type, key) *
                FROM agent_knowledge
                WHERE agent_id = $1
                ORDER BY knowledge_type, key, version DESC
                """,
                agent_id,
            )
        return [dict(r) for r in rows]

    async def upsert(
        self,
        agent_id: str,
        knowledge_type: str,
        key: str,
        value: Any,
        source: str = "conversation",
        confidence: float = 0.5,
    ) -> dict[str, Any]:
        """Yeni versiyon olarak kaydet (append-only).

        Paralel tool cagrilari ayni (agent_id, knowledge_type, key) uzerinde
        cakisirsa SELECT max+1 read-modify-write race olusur. Cozum: version'i
        SQL icinde COALESCE(MAX(version),0)+1 ile atomic hesapla ve unique
        constraint ihlalinde 5 kere kadar retry yap.
        """
        import asyncpg

        value_json = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
        last_exc: Exception | None = None

        for attempt in range(5):
            row_id = str(uuid.uuid4())
            try:
                row = await self.pg.fetchrow(
                    """
                    INSERT INTO agent_knowledge
                        (id, agent_id, knowledge_type, key, value, version, confidence, source)
                    SELECT
                        $1::uuid,
                        $2::varchar,
                        $3::varchar,
                        $4::varchar,
                        $5::jsonb,
                        COALESCE(
                            (SELECT MAX(version) FROM agent_knowledge
                             WHERE agent_id = $2::varchar
                               AND knowledge_type = $3::varchar
                               AND key = $4::varchar),
                            0
                        ) + 1,
                        $6::float8,
                        $7::varchar
                    RETURNING version
                    """,
                    row_id, agent_id, knowledge_type, key, value_json, confidence, source,
                )
                new_version = int(row["version"]) if row else 1
                logger.info(
                    "Knowledge upsert: agent=%s type=%s key=%s v%d",
                    agent_id, knowledge_type, key, new_version,
                )
                return {"id": row_id, "version": new_version}
            except asyncpg.exceptions.UniqueViolationError as exc:
                last_exc = exc
                logger.warning(
                    "Knowledge upsert race (attempt %d/5) agent=%s type=%s key=%s: %s",
                    attempt + 1, agent_id, knowledge_type, key, exc,
                )
                await asyncio.sleep(0.05 * (attempt + 1))
                continue

        assert last_exc is not None
        raise last_exc

    async def delete(self, agent_id: str, knowledge_type: str, key: str) -> int:
        """Tum versiyonlari sil."""
        result = await self.pg.execute(
            "DELETE FROM agent_knowledge WHERE agent_id = $1 AND knowledge_type = $2 AND key = $3",
            agent_id, knowledge_type, key,
        )
        return int(result.split()[-1]) if isinstance(result, str) else 0

    # ─── ONTOLOGY CONVENIENCE ───────────────────────────────────────

    async def save_ontology(self, agent_id: str, ontology: AgentOntology, source: str = "conversation") -> None:
        """Tum ontolojiyi tek seferde kaydet."""
        await self.upsert(agent_id, "ontology", "full", ontology.to_dict(), source=source, confidence=1.0)

    async def load_ontology(self, agent_id: str) -> AgentOntology:
        """Kayitli ontolojiyi yukle, yoksa bos dondur."""
        row = await self.get(agent_id, "ontology", "full")
        if not row:
            return AgentOntology()
        value = row["value"]
        if isinstance(value, str):
            value = json.loads(value)
        return AgentOntology.from_dict(value)

    # ─── IDENTITY ──────────────────────────────────────────────────

    async def save_identity(
        self, agent_id: str, name: str, purpose: str,
        llm_provider: str | None = None, llm_model: str | None = None,
    ) -> None:
        data: dict[str, str] = {"name": name, "purpose": purpose}
        if llm_provider:
            data["llm_provider"] = llm_provider
        if llm_model:
            data["llm_model"] = llm_model
        await self.upsert(agent_id, "identity", "main", data, confidence=1.0)

    async def load_identity(self, agent_id: str) -> dict[str, str]:
        row = await self.get(agent_id, "identity", "main")
        if not row:
            return {"name": "Agent", "purpose": ""}
        value = row["value"]
        if isinstance(value, str):
            value = json.loads(value)
        return value

    async def update_agent_model(
        self, agent_id: str, llm_provider: str, llm_model: str,
    ) -> dict[str, str]:
        """Update only model fields in identity, preserving name/purpose."""
        identity = await self.load_identity(agent_id)
        identity["llm_provider"] = llm_provider
        identity["llm_model"] = llm_model
        await self.upsert(agent_id, "identity", "main", identity, confidence=1.0)
        return identity

    # ─── GLOBAL RESOURCES ──────────────────────────────────────────

    async def list_all_resources(self) -> list[dict[str, Any]]:
        """Aggregate sample_files + source_urls across ALL agents with identity info."""
        rows = await self.pg.fetch(
            """
            SELECT ak.agent_id,
                   ak.knowledge_type,
                   ak.key,
                   ak.value,
                   ak.created_at,
                   COALESCE(id_row.value->>'name', 'Agent') AS agent_name
            FROM agent_knowledge ak
            LEFT JOIN LATERAL (
                SELECT value FROM agent_knowledge
                WHERE agent_id = ak.agent_id
                  AND knowledge_type = 'identity'
                  AND key = 'main'
                ORDER BY version DESC LIMIT 1
            ) id_row ON TRUE
            LEFT JOIN agent_lifecycle al ON al.agent_id = ak.agent_id
            WHERE ak.knowledge_type IN ('sample_files', 'source_urls')
              AND (al.deleted_at IS NULL OR al.agent_id IS NULL)
            ORDER BY ak.created_at DESC
            """
        )
        resources: list[dict[str, Any]] = []
        for r in rows:
            val = r["value"]
            if isinstance(val, str):
                try:
                    val = json.loads(val)
                except (json.JSONDecodeError, ValueError):
                    continue
            agent_id = r["agent_id"]
            agent_name = r["agent_name"] or "Agent"
            created_at = str(r["created_at"]) if r["created_at"] else None

            if r["knowledge_type"] == "sample_files":
                for f in val.get("files", []):
                    resources.append({
                        "resource_id": f.get("resource_id", ""),
                        "agent_id": agent_id,
                        "agent_name": agent_name,
                        "type": "file",
                        "filename": f.get("filename", ""),
                        "content_type": f.get("content_type", ""),
                        "size": f.get("size", 0),
                        "path": f.get("path", ""),
                        "created_at": created_at,
                    })
            elif r["knowledge_type"] == "source_urls":
                for s in val.get("sources", []):
                    resources.append({
                        "resource_id": f"url_{uuid.uuid4().hex[:8]}",
                        "agent_id": agent_id,
                        "agent_name": agent_name,
                        "type": s.get("type", "url"),
                        "filename": s.get("url", ""),
                        "content_type": "",
                        "size": 0,
                        "path": s.get("url", ""),
                        "created_at": created_at,
                    })
        return resources

    async def delete_resource_by_id(self, resource_id: str) -> bool:
        """Remove a specific file resource by its resource_id from agent_knowledge."""
        rows = await self.pg.fetch(
            """
            SELECT id, agent_id, key, value
            FROM agent_knowledge
            WHERE knowledge_type = 'sample_files'
            """
        )
        for r in rows:
            val = r["value"]
            if isinstance(val, str):
                try:
                    val = json.loads(val)
                except (json.JSONDecodeError, ValueError):
                    continue
            files = val.get("files", [])
            remaining = [f for f in files if f.get("resource_id") != resource_id]
            if len(remaining) < len(files):
                if remaining:
                    val["files"] = remaining
                    val["paths"] = [f.get("path", "") for f in remaining]
                    await self.pg.execute(
                        "UPDATE agent_knowledge SET value = $1::jsonb WHERE id = $2",
                        json.dumps(val, ensure_ascii=False),
                        r["id"],
                    )
                else:
                    await self.pg.execute(
                        "DELETE FROM agent_knowledge WHERE id = $1", r["id"]
                    )
                return True
        return False

    # ─── ACTIVE WORKFLOW ──────────────────────────────────────────

    async def save_active_workflow(self, agent_id: str, workflow_id: str) -> None:
        await self.upsert(
            agent_id, "workflow_state", "active",
            {"active_workflow_id": workflow_id}, confidence=1.0,
        )

    async def load_active_workflow(self, agent_id: str) -> str | None:
        row = await self.get(agent_id, "workflow_state", "active")
        if not row:
            return None
        value = row["value"]
        if isinstance(value, str):
            value = json.loads(value)
        return value.get("active_workflow_id")

    # ─── HISTORY ──────────────────────────────────────────────────

    async def get_version_history(self, agent_id: str, knowledge_type: str, key: str) -> list[dict[str, Any]]:
        """Bir key'in tum versiyonlarini getir."""
        rows = await self.pg.fetch(
            """
            SELECT version, value, confidence, source, created_at
            FROM agent_knowledge
            WHERE agent_id = $1 AND knowledge_type = $2 AND key = $3
            ORDER BY version ASC
            """,
            agent_id, knowledge_type, key,
        )
        return [dict(r) for r in rows]

    # ─── PLAN ─────────────────────────────────────────────────────

    async def save_plan(
        self,
        agent_id: str,
        steps: list[dict[str, str]],
        summary: str = "",
    ) -> dict[str, Any]:
        """Yeni plan olustur veya mevcut plani degistir.

        Args:
            steps: [{"id": 1, "content": "..."}, ...]
            summary: Plan ozeti
        """
        numbered = []
        for i, s in enumerate(steps):
            numbered.append({
                "id": s.get("id", i + 1),
                "content": s.get("content", s) if isinstance(s, dict) else str(s),
            })
        value = {"steps": numbered, "summary": summary, "status": "draft"}
        return await self.upsert(agent_id, "active_plan", "current", value, source="plan_mode")

    async def load_plan(self, agent_id: str) -> dict[str, Any] | None:
        """Aktif plani yukle."""
        row = await self.get(agent_id, "active_plan", "current")
        if not row:
            return None
        val = row["value"]
        if isinstance(val, str):
            val = json.loads(val)
        return val

    async def update_plan_status(self, agent_id: str, status: str) -> None:
        """Plan durumunu guncelle: draft | approved | executing | completed."""
        plan = await self.load_plan(agent_id)
        if not plan:
            return
        plan["status"] = status
        await self.upsert(agent_id, "active_plan", "current", plan, source="plan_mode")

    async def update_plan_step(
        self, agent_id: str, step_id: int, new_content: str
    ) -> dict[str, Any] | None:
        """Tek bir plan adimini guncelle."""
        plan = await self.load_plan(agent_id)
        if not plan:
            return None
        for step in plan.get("steps", []):
            if step.get("id") == step_id:
                step["content"] = new_content
                break
        else:
            return None
        await self.upsert(agent_id, "active_plan", "current", plan, source="plan_mode")
        return plan

    async def add_plan_step(
        self, agent_id: str, after_step_id: int, content: str
    ) -> dict[str, Any] | None:
        """Belirli bir adimdan sonra yeni adim ekle."""
        plan = await self.load_plan(agent_id)
        if not plan:
            return None
        steps = plan.get("steps", [])
        insert_idx = len(steps)
        for i, step in enumerate(steps):
            if step.get("id") == after_step_id:
                insert_idx = i + 1
                break
        max_id = max((s.get("id", 0) for s in steps), default=0)
        steps.insert(insert_idx, {"id": max_id + 1, "content": content})
        for i, step in enumerate(steps):
            step["id"] = i + 1
        plan["steps"] = steps
        await self.upsert(agent_id, "active_plan", "current", plan, source="plan_mode")
        return plan

    async def remove_plan_step(
        self, agent_id: str, step_id: int
    ) -> dict[str, Any] | None:
        """Plan adimini kaldir."""
        plan = await self.load_plan(agent_id)
        if not plan:
            return None
        steps = plan.get("steps", [])
        plan["steps"] = [s for s in steps if s.get("id") != step_id]
        for i, step in enumerate(plan["steps"]):
            step["id"] = i + 1
        await self.upsert(agent_id, "active_plan", "current", plan, source="plan_mode")
        return plan

    def plan_to_markdown(self, plan: dict[str, Any]) -> str:
        """Plan dict'inden markdown olustur."""
        lines = []
        if plan.get("summary"):
            lines.append(f"## Plan: {plan['summary']}")
        else:
            lines.append("## Plan")
        lines.append("")
        for step in plan.get("steps", []):
            lines.append(f"{step['id']}. {step['content']}")
        status = plan.get("status", "draft")
        lines.append(f"\n*Durum: {status}*")
        return "\n".join(lines)

    # ─── ONTOLOGY DISCOVERIES ─────────────────────────────────────

    async def upsert_discovery(
        self,
        agent_id: str,
        discovery_type: str,
        name: str,
        first_seen_doc: str = "",
        sample_properties: list[str] | None = None,
    ) -> dict[str, Any]:
        """Insert new discovery or increment sample_count if already exists."""
        existing = await self.pg.fetchrow(
            "SELECT id, sample_count FROM ontology_discoveries WHERE agent_id = $1 AND discovery_type = $2 AND name = $3",
            agent_id, discovery_type, name,
        )
        props_json = json.dumps(sample_properties or [], ensure_ascii=False)
        if existing:
            await self.pg.execute(
                """
                UPDATE ontology_discoveries
                SET sample_count = sample_count + 1,
                    sample_properties = (
                        SELECT jsonb_agg(DISTINCT elem)
                        FROM jsonb_array_elements(sample_properties || $1::jsonb) AS elem
                    ),
                    updated_at = NOW()
                WHERE id = $2
                """,
                props_json, str(existing["id"]),
            )
            return {"id": str(existing["id"]), "new": False, "sample_count": existing["sample_count"] + 1}

        row_id = str(uuid.uuid4())
        await self.pg.execute(
            """
            INSERT INTO ontology_discoveries (id, agent_id, discovery_type, name, first_seen_doc, sample_properties)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb)
            """,
            row_id, agent_id, discovery_type, name, first_seen_doc, props_json,
        )
        return {"id": row_id, "new": True, "sample_count": 1}

    async def list_discoveries(
        self,
        agent_id: str,
        status: str = "",
        discovery_type: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List discoveries filtered by status and/or type."""
        conditions = ["agent_id = $1"]
        params: list[Any] = [agent_id]
        idx = 2
        if status:
            conditions.append(f"status = ${idx}")
            params.append(status)
            idx += 1
        if discovery_type:
            conditions.append(f"discovery_type = ${idx}")
            params.append(discovery_type)
            idx += 1
        conditions.append(f"TRUE")
        params.append(limit)
        query = f"""
            SELECT * FROM ontology_discoveries
            WHERE {' AND '.join(conditions)}
            ORDER BY sample_count DESC, created_at DESC
            LIMIT ${idx}
        """
        rows = await self.pg.fetch(query, *params)
        result = []
        for r in rows:
            d = dict(r)
            for k in ("id",):
                if k in d and hasattr(d[k], "hex"):
                    d[k] = str(d[k])
            for k in ("created_at", "updated_at"):
                if k in d and d[k] is not None:
                    d[k] = str(d[k])
            result.append(d)
        return result

    async def update_discovery_status(
        self,
        agent_id: str,
        discovery_type: str,
        name: str,
        status: str,
    ) -> bool:
        """Set discovery status to approved/rejected."""
        result = await self.pg.execute(
            """
            UPDATE ontology_discoveries SET status = $1, updated_at = NOW()
            WHERE agent_id = $2 AND discovery_type = $3 AND name = $4
            """,
            status, agent_id, discovery_type, name,
        )
        count = int(result.split()[-1]) if isinstance(result, str) else 0
        return count > 0
