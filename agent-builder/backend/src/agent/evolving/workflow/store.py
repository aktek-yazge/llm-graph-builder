"""
Workflow Store — CRUD + queries for workflows and runs in PostgreSQL.
Supports multiple workflows per agent and cross-agent template sharing.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from .models import WorkflowDSL, WorkflowRow, WorkflowStatus

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _gen_id(prefix: str = "wf") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class WorkflowStore:
    """Postgres-backed storage for workflow DSL, runs, and steps."""

    def __init__(self, pg) -> None:
        self._pg = pg

    # ── Workflow CRUD ────────────────────────────────────────────────

    async def get_or_create(self, agent_id: str, active_workflow_id: str | None = None) -> WorkflowRow:
        """Return the active workflow or the latest one; create a Default if none exist."""
        if active_workflow_id:
            row = await self._pg.fetchrow(
                "SELECT * FROM workflows WHERE workflow_id=$1 AND agent_id=$2",
                active_workflow_id, agent_id,
            )
            if row:
                return self._row_to_model(row)

        row = await self._pg.fetchrow(
            "SELECT * FROM workflows WHERE agent_id=$1 ORDER BY updated_at DESC LIMIT 1",
            agent_id,
        )
        if row:
            return self._row_to_model(row)

        return await self.create_workflow(agent_id, "Default")

    async def get(self, workflow_id: str) -> Optional[WorkflowRow]:
        row = await self._pg.fetchrow(
            "SELECT * FROM workflows WHERE workflow_id=$1", workflow_id,
        )
        return self._row_to_model(row) if row else None

    async def get_by_agent(self, agent_id: str) -> Optional[WorkflowRow]:
        row = await self._pg.fetchrow(
            "SELECT * FROM workflows WHERE agent_id=$1 ORDER BY updated_at DESC LIMIT 1",
            agent_id,
        )
        return self._row_to_model(row) if row else None

    async def list_workflows(self, agent_id: str) -> list[WorkflowRow]:
        """Return all workflows for an agent, most recently updated first."""
        rows = await self._pg.fetch(
            "SELECT * FROM workflows WHERE agent_id=$1 ORDER BY updated_at DESC",
            agent_id,
        )
        return [self._row_to_model(r) for r in rows]

    async def create_workflow(
        self,
        agent_id: str | None,
        name: str,
        description: str = "",
        dsl: WorkflowDSL | None = None,
        source_template_id: str | None = None,
    ) -> WorkflowRow:
        """Insert a new workflow row and return it."""
        wid = _gen_id("wf")
        dsl = dsl or WorkflowDSL()
        await self._pg.execute(
            """
            INSERT INTO workflows
                (workflow_id, agent_id, name, description, version, status,
                 dsl_json, is_template, source_template_id)
            VALUES ($1, $2, $3, $4, 1, 'draft', $5::jsonb, FALSE, $6)
            """,
            wid, agent_id, name, description, dsl.model_dump_json(),
            source_template_id,
        )
        return WorkflowRow(
            workflow_id=wid,
            agent_id=agent_id,
            name=name,
            description=description,
            version=1,
            status=WorkflowStatus.DRAFT,
            dsl=dsl,
            source_template_id=source_template_id,
        )

    async def rename_workflow(self, workflow_id: str, name: str) -> None:
        await self._pg.execute(
            "UPDATE workflows SET name=$1, updated_at=NOW() WHERE workflow_id=$2",
            name, workflow_id,
        )

    async def update_description(self, workflow_id: str, description: str) -> None:
        await self._pg.execute(
            "UPDATE workflows SET description=$1, updated_at=NOW() WHERE workflow_id=$2",
            description, workflow_id,
        )

    async def delete_workflow(self, workflow_id: str) -> None:
        """Delete a workflow and its runs/steps (CASCADE via FK)."""
        await self._pg.execute(
            "DELETE FROM workflow_run_steps WHERE run_id IN (SELECT run_id FROM workflow_runs WHERE workflow_id=$1)",
            workflow_id,
        )
        await self._pg.execute(
            "DELETE FROM workflow_runs WHERE workflow_id=$1", workflow_id,
        )
        await self._pg.execute(
            "DELETE FROM graphrag_endpoints WHERE workflow_id=$1", workflow_id,
        )
        await self._pg.execute(
            "DELETE FROM workflows WHERE workflow_id=$1", workflow_id,
        )

    async def clone_workflow(
        self,
        workflow_id: str,
        target_agent_id: str,
        new_name: str | None = None,
    ) -> WorkflowRow:
        """Clone a workflow's DSL into a new workflow for the target agent."""
        source = await self.get(workflow_id)
        if not source:
            raise ValueError(f"Workflow {workflow_id} not found")
        name = new_name or f"{source.name} (kopya)"
        return await self.create_workflow(
            agent_id=target_agent_id,
            name=name,
            description=source.description,
            dsl=source.dsl,
            source_template_id=workflow_id,
        )

    # ── Template sharing ─────────────────────────────────────────────

    async def list_all_workflows(
        self,
        search: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """List all workflows across all agents with optional search/filter.
        Returns (items, total_count)."""
        where_clauses = []
        args: list[Any] = []
        idx = 1

        if search:
            where_clauses.append(f"(w.name ILIKE ${idx} OR w.description ILIKE ${idx})")
            args.append(f"%{search}%")
            idx += 1

        if status:
            where_clauses.append(f"w.status = ${idx}")
            args.append(status)
            idx += 1

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        count_row = await self._pg.fetchrow(
            f"SELECT COUNT(*) AS cnt FROM workflows w {where_sql}",
            *args,
        )
        total = int(count_row["cnt"]) if count_row else 0

        limit_arg_idx = idx
        offset_arg_idx = idx + 1
        args.extend([limit, offset])

        rows = await self._pg.fetch(
            f"""
            SELECT w.*, ak.value->>'name' AS agent_name
            FROM workflows w
            LEFT JOIN agent_knowledge ak
                ON ak.agent_id = w.agent_id
                AND ak.knowledge_type = 'identity'
                AND ak.key = 'main'
                AND ak.version = (
                    SELECT MAX(version) FROM agent_knowledge ak2
                    WHERE ak2.agent_id = w.agent_id
                    AND ak2.knowledge_type = 'identity' AND ak2.key = 'main'
                )
            {where_sql}
            ORDER BY w.updated_at DESC
            LIMIT ${limit_arg_idx} OFFSET ${offset_arg_idx}
            """,
            *args,
        )

        items = []
        for r in rows:
            wf = self._row_to_model(r)
            items.append({
                "workflow_id": wf.workflow_id,
                "agent_id": wf.agent_id,
                "agent_name": r.get("agent_name") or ("Standalone" if wf.agent_id is None else "Agent"),
                "name": wf.name,
                "description": wf.description,
                "version": wf.version,
                "status": wf.status.value if hasattr(wf.status, "value") else wf.status,
                "node_count": len(wf.dsl.nodes),
                "is_template": wf.is_template,
                "source_template_id": wf.source_template_id,
                "created_at": str(wf.created_at) if wf.created_at else None,
                "updated_at": str(wf.updated_at) if wf.updated_at else None,
            })
        return items, total

    async def set_template(self, workflow_id: str, is_template: bool, description: str | None = None) -> None:
        if description is not None:
            await self._pg.execute(
                "UPDATE workflows SET is_template=$1, description=$2, updated_at=NOW() WHERE workflow_id=$3",
                is_template, description, workflow_id,
            )
        else:
            await self._pg.execute(
                "UPDATE workflows SET is_template=$1, updated_at=NOW() WHERE workflow_id=$2",
                is_template, workflow_id,
            )

    async def list_templates(self, exclude_agent_id: str | None = None) -> list[dict[str, Any]]:
        """List all shared workflow templates, optionally excluding one agent's own."""
        if exclude_agent_id:
            rows = await self._pg.fetch(
                """
                SELECT w.*, ak.value->>'name' AS agent_name
                FROM workflows w
                LEFT JOIN agent_knowledge ak
                    ON ak.agent_id = w.agent_id
                    AND ak.knowledge_type = 'identity'
                    AND ak.key = 'main'
                    AND ak.version = (
                        SELECT MAX(version) FROM agent_knowledge ak2
                        WHERE ak2.agent_id = w.agent_id
                        AND ak2.knowledge_type = 'identity' AND ak2.key = 'main'
                    )
                WHERE w.is_template = TRUE AND w.agent_id != $1
                ORDER BY w.updated_at DESC
                """,
                exclude_agent_id,
            )
        else:
            rows = await self._pg.fetch(
                """
                SELECT w.*, ak.value->>'name' AS agent_name
                FROM workflows w
                LEFT JOIN agent_knowledge ak
                    ON ak.agent_id = w.agent_id
                    AND ak.knowledge_type = 'identity'
                    AND ak.key = 'main'
                    AND ak.version = (
                        SELECT MAX(version) FROM agent_knowledge ak2
                        WHERE ak2.agent_id = w.agent_id
                        AND ak2.knowledge_type = 'identity' AND ak2.key = 'main'
                    )
                WHERE w.is_template = TRUE
                ORDER BY w.updated_at DESC
                """,
            )
        result = []
        for r in rows:
            wf = self._row_to_model(r)
            result.append({
                "workflow_id": wf.workflow_id,
                "agent_id": wf.agent_id,
                "agent_name": r.get("agent_name") or "Agent",
                "name": wf.name,
                "description": wf.description,
                "node_count": len(wf.dsl.nodes),
                "version": wf.version,
                "status": wf.status.value if hasattr(wf.status, "value") else wf.status,
            })
        return result

    # ── DSL persistence ──────────────────────────────────────────────

    async def save_dsl(self, workflow_id: str, dsl: WorkflowDSL) -> None:
        await self._pg.execute(
            """
            UPDATE workflows SET dsl_json=$1::jsonb, updated_at=NOW()
            WHERE workflow_id=$2
            """,
            dsl.model_dump_json(), workflow_id,
        )

    async def publish(self, workflow_id: str) -> int:
        """Freeze current DSL: increment version, set status=published. Returns new version."""
        row = await self.get(workflow_id)
        if not row:
            raise ValueError(f"Workflow {workflow_id} not found")

        new_version = row.version + 1
        await self._pg.execute(
            """
            UPDATE workflows
            SET version=$1, status='published', published_at=NOW(), updated_at=NOW()
            WHERE workflow_id=$2
            """,
            new_version, workflow_id,
        )
        return new_version

    async def set_status(self, workflow_id: str, status: str) -> None:
        await self._pg.execute(
            "UPDATE workflows SET status=$1, updated_at=NOW() WHERE workflow_id=$2",
            status, workflow_id,
        )

    # ── Run management ───────────────────────────────────────────────

    async def create_run(
        self,
        workflow_id: str,
        agent_id: str,
        mode: str,
        inputs: dict[str, Any] | None = None,
    ) -> str:
        run_id = _gen_id("run")
        await self._pg.execute(
            """
            INSERT INTO workflow_runs (run_id, workflow_id, agent_id, mode, status, inputs_json)
            VALUES ($1, $2, $3, $4, 'running', $5::jsonb)
            """,
            run_id, workflow_id, agent_id, mode,
            json.dumps(inputs or {}, ensure_ascii=False, default=str),
        )
        return run_id

    async def finish_run(
        self, run_id: str, status: str, summary: dict[str, Any] | None = None,
    ) -> None:
        await self._pg.execute(
            """
            UPDATE workflow_runs
            SET status=$1, summary_json=$2::jsonb, completed_at=NOW()
            WHERE run_id=$3
            """,
            status,
            json.dumps(summary or {}, ensure_ascii=False, default=str),
            run_id,
        )

    async def get_run(self, run_id: str) -> Optional[dict[str, Any]]:
        row = await self._pg.fetchrow(
            "SELECT * FROM workflow_runs WHERE run_id=$1", run_id,
        )
        return dict(row) if row else None

    async def list_runs(
        self, agent_id: str, limit: int = 20, offset: int = 0,
    ) -> list[dict[str, Any]]:
        rows = await self._pg.fetch(
            """
            SELECT * FROM workflow_runs
            WHERE agent_id=$1
            ORDER BY started_at DESC
            LIMIT $2 OFFSET $3
            """,
            agent_id, limit, offset,
        )
        return [dict(r) for r in rows]

    # ── Step management ──────────────────────────────────────────────

    async def upsert_step(
        self,
        run_id: str,
        node_id: str,
        node_type: str,
        status: str,
        inputs: dict[str, Any] | None = None,
        outputs: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> int:
        """Insert or update a step row. Returns the step id."""
        existing = await self._pg.fetchrow(
            "SELECT id FROM workflow_run_steps WHERE run_id=$1 AND node_id=$2",
            run_id, node_id,
        )
        if existing:
            cols = ["status=$1"]
            args: list[Any] = [status]
            idx = 2
            if outputs is not None:
                cols.append(f"outputs_json=${idx}::jsonb")
                args.append(json.dumps(outputs, ensure_ascii=False, default=str))
                idx += 1
            if error is not None:
                cols.append(f"error=${idx}")
                args.append(error)
                idx += 1
            if status == "running":
                cols.append(f"started_at=NOW()")
            if status in ("completed", "failed", "skipped"):
                cols.append(f"completed_at=NOW()")
            cols.append(f"id=${idx}")
            args.append(existing["id"])
            await self._pg.execute(
                f"UPDATE workflow_run_steps SET {', '.join(cols)} WHERE id=${idx}",
                *args,
            )
            return int(existing["id"])

        row = await self._pg.fetchrow(
            """
            INSERT INTO workflow_run_steps
                (run_id, node_id, node_type, status, inputs_json, started_at)
            VALUES ($1, $2, $3, $4, $5::jsonb, CASE WHEN $4='running' THEN NOW() ELSE NULL END)
            RETURNING id
            """,
            run_id, node_id, node_type, status,
            json.dumps(inputs or {}, ensure_ascii=False, default=str),
        )
        return int(row["id"])

    async def get_steps(self, run_id: str) -> list[dict[str, Any]]:
        rows = await self._pg.fetch(
            "SELECT * FROM workflow_run_steps WHERE run_id=$1 ORDER BY started_at ASC NULLS LAST, id ASC",
            run_id,
        )
        return [dict(r) for r in rows]

    # ── GraphRAG endpoints ───────────────────────────────────────────

    async def create_graphrag_endpoint(
        self,
        agent_id: str,
        workflow_id: str,
        workflow_version: int,
        neo4j_uri: str,
        neo4j_database: str,
        ontology_snapshot: dict[str, Any],
        schema_summary: str,
        name: str = "",
        source_documents: list[dict[str, Any]] | None = None,
    ) -> str:
        eid = _gen_id("gre")
        await self._pg.execute(
            """
            INSERT INTO graphrag_endpoints
                (endpoint_id, agent_id, workflow_id, workflow_version,
                 neo4j_uri, neo4j_database, ontology_snapshot, schema_summary,
                 name, source_documents)
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10::jsonb)
            """,
            eid, agent_id, workflow_id, workflow_version,
            neo4j_uri, neo4j_database,
            json.dumps(ontology_snapshot, ensure_ascii=False, default=str),
            schema_summary,
            name,
            json.dumps(source_documents or [], ensure_ascii=False, default=str),
        )
        return eid

    async def get_graphrag_endpoint(self, endpoint_id: str) -> Optional[dict[str, Any]]:
        row = await self._pg.fetchrow(
            "SELECT * FROM graphrag_endpoints WHERE endpoint_id=$1", endpoint_id,
        )
        return dict(row) if row else None

    async def get_active_endpoint(self, agent_id: str) -> Optional[dict[str, Any]]:
        row = await self._pg.fetchrow(
            """
            SELECT * FROM graphrag_endpoints
            WHERE agent_id=$1 AND status='active'
            ORDER BY published_at DESC LIMIT 1
            """,
            agent_id,
        )
        return dict(row) if row else None

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _row_to_model(row) -> WorkflowRow:
        dsl_raw = row["dsl_json"]
        if isinstance(dsl_raw, str):
            dsl_raw = json.loads(dsl_raw)
        dsl = WorkflowDSL.model_validate(dsl_raw)
        return WorkflowRow(
            workflow_id=row["workflow_id"],
            agent_id=row["agent_id"],
            name=row["name"],
            description=row.get("description", ""),
            version=row["version"],
            status=row["status"],
            dsl=dsl,
            is_template=row.get("is_template", False),
            source_template_id=row.get("source_template_id"),
            published_at=row.get("published_at"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )
