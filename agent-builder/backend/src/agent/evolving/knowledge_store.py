"""
Knowledge Store
===============

PostgreSQL-backed bilgi deposu.
Agent'in ontolojisini, domain kurallarini ve ogrenilen pattern'leri
versiyonlanmis olarak saklar.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
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
"""


class KnowledgeStore:
    """PostgreSQL CRUD for agent knowledge."""

    def __init__(self, pg):
        """pg: asyncpg pool or connection-like object with execute/fetch/fetchrow."""
        self.pg = pg

    async def ensure_table(self) -> None:
        await self.pg.execute(MIGRATION_SQL)

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
        """Yeni versiyon olarak kaydet (append-only)."""
        existing = await self.get(agent_id, knowledge_type, key)
        new_version = (existing["version"] + 1) if existing else 1
        value_json = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value

        row_id = str(uuid.uuid4())
        await self.pg.execute(
            """
            INSERT INTO agent_knowledge (id, agent_id, knowledge_type, key, value, version, confidence, source)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8)
            """,
            row_id, agent_id, knowledge_type, key, value_json, new_version, confidence, source,
        )
        logger.info("Knowledge upsert: agent=%s type=%s key=%s v%d", agent_id, knowledge_type, key, new_version)
        return {"id": row_id, "version": new_version}

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

    async def save_identity(self, agent_id: str, name: str, purpose: str) -> None:
        await self.upsert(agent_id, "identity", "main", {"name": name, "purpose": purpose}, confidence=1.0)

    async def load_identity(self, agent_id: str) -> dict[str, str]:
        row = await self.get(agent_id, "identity", "main")
        if not row:
            return {"name": "Agent", "purpose": ""}
        value = row["value"]
        if isinstance(value, str):
            value = json.loads(value)
        return value

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
