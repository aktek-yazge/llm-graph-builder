"""
Workspace Repository
=====================

PostgreSQL-backed repository for Workspace, WorkspaceDocument,
BatchJob and ElicitationRequest CRUD.
Replaces Neo4j Workspace / WorkspaceDocument / BatchJob / ElicitationRequest nodes.
"""

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from .event_store.postgres_client import PostgresClient

logger = logging.getLogger(__name__)


class WorkspaceRepository:
    """Async CRUD for workspaces, workspace_documents, batch_jobs, elicitation_requests."""

    def __init__(self, pg: PostgresClient):
        self.pg = pg

    # =========================================================================
    # WORKSPACE
    # =========================================================================

    async def create_workspace(self, data: Dict[str, Any]) -> Dict[str, Any]:
        row = await self.pg.fetchrow(
            """
            INSERT INTO workspaces (id, name, description, tenant_id, status,
                ocr_mode, batch_size, agent_id, extraction_config)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            RETURNING *
            """,
            data["id"],
            data.get("name", ""),
            data.get("description", ""),
            data.get("tenant_id", "default"),
            data.get("status", "created"),
            data.get("ocr_mode", "hybrid"),
            data.get("batch_size", 100),
            data.get("agent_id"),
            json.dumps(data.get("extraction_config", {}), ensure_ascii=False)
            if isinstance(data.get("extraction_config"), dict)
            else data.get("extraction_config", "{}"),
        )
        return dict(row) if row else data

    async def get_workspace(self, workspace_id: str) -> Optional[Dict[str, Any]]:
        row = await self.pg.fetchrow(
            "SELECT * FROM workspaces WHERE id = $1",
            workspace_id,
        )
        if not row:
            return None
        d = dict(row)
        if isinstance(d.get("extraction_config"), str):
            try:
                d["extraction_config"] = json.loads(d["extraction_config"])
            except (json.JSONDecodeError, TypeError):
                pass
        return d

    async def count_workspaces(self, tenant_id: str) -> int:
        row = await self.pg.fetchrow(
            "SELECT count(*)::int AS total FROM workspaces WHERE tenant_id = $1",
            tenant_id,
        )
        return row["total"] if row else 0

    async def list_workspaces(
        self,
        tenant_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        rows = await self.pg.fetch(
            """
            SELECT w.id, w.name, w.status, w.created_at,
                   COALESCE(d.doc_count, 0) AS document_count,
                   COALESCE(d.processed, 0) AS processed_count,
                   COALESCE(d.successful, 0) AS successful_count,
                   COALESCE(d.failed, 0) AS failed_count
            FROM workspaces w
            LEFT JOIN LATERAL (
                SELECT count(*) AS doc_count,
                       count(*) FILTER (WHERE wd.status IN ('completed','failed','low_confidence')) AS processed,
                       count(*) FILTER (WHERE wd.status = 'completed') AS successful,
                       count(*) FILTER (WHERE wd.status = 'failed') AS failed
                FROM workspace_documents wd WHERE wd.workspace_id = w.id
            ) d ON TRUE
            WHERE w.tenant_id = $1
            ORDER BY w.created_at DESC
            LIMIT $2 OFFSET $3
            """,
            tenant_id, limit, offset,
        )
        return [dict(r) for r in rows]

    async def update_workspace(
        self,
        workspace_id: str,
        updates: Dict[str, Any],
    ) -> None:
        sets = ["updated_at = NOW()"]
        params: list = []
        idx = 1

        allowed = {
            "name", "description", "status", "ocr_mode", "batch_size",
            "agent_id", "skill_id", "resource_id", "extraction_config",
            "schema_source", "minio_bucket", "minio_prefix",
            "document_count", "sample_count",
        }
        for k, v in updates.items():
            if k not in allowed:
                continue
            if k == "extraction_config" and isinstance(v, dict):
                v = json.dumps(v, ensure_ascii=False)
            sets.append(f"{k} = ${idx}")
            params.append(v)
            idx += 1

        if len(sets) == 1:
            return

        params.append(workspace_id)
        query = f"UPDATE workspaces SET {', '.join(sets)} WHERE id = ${idx}"
        await self.pg.execute(query, *params)

    async def delete_workspace(self, workspace_id: str) -> None:
        await self.pg.execute("DELETE FROM workspaces WHERE id = $1", workspace_id)

    # =========================================================================
    # WORKSPACE DOCUMENTS
    # =========================================================================

    async def create_document(self, data: Dict[str, Any]) -> str:
        doc_id = data.get("id", f"wdoc-{uuid.uuid4().hex[:12]}")
        await self.pg.execute(
            """
            INSERT INTO workspace_documents
                (id, workspace_id, batch_job_id, file_path, file_name, status,
                 is_sample, sequence, minio_key)
            VALUES ($1, $2, NULLIF($3,''), $4, $5, $6, $7, $8, NULLIF($9,''))
            """,
            doc_id,
            data["workspace_id"],
            data.get("batch_job_id", ""),
            data.get("file_path", ""),
            data.get("file_name", ""),
            data.get("status", "pending"),
            data.get("is_sample", False),
            data.get("sequence", 0),
            data.get("minio_key", ""),
        )
        return doc_id

    async def get_document(self, doc_id: str) -> Optional[Dict[str, Any]]:
        row = await self.pg.fetchrow(
            "SELECT * FROM workspace_documents WHERE id = $1", doc_id,
        )
        return dict(row) if row else None

    async def list_documents(
        self,
        workspace_id: str,
        status: Optional[str] = None,
        batch_job_id: Optional[str] = None,
        is_sample: Optional[bool] = None,
        limit: int = 200,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        conditions = ["workspace_id = $1"]
        params: list = [workspace_id]
        idx = 2

        if status:
            conditions.append(f"status = ${idx}")
            params.append(status)
            idx += 1
        if batch_job_id:
            conditions.append(f"batch_job_id = ${idx}")
            params.append(batch_job_id)
            idx += 1
        if is_sample is not None:
            conditions.append(f"is_sample = ${idx}")
            params.append(is_sample)
            idx += 1

        params.extend([limit, offset])
        query = f"""
            SELECT * FROM workspace_documents
            WHERE {' AND '.join(conditions)}
            ORDER BY sequence, created_at
            LIMIT ${idx} OFFSET ${idx + 1}
        """
        rows = await self.pg.fetch(query, *params)
        return [dict(r) for r in rows]

    async def update_document(
        self,
        doc_id: str,
        updates: Dict[str, Any],
    ) -> None:
        sets = ["updated_at = NOW()"]
        params: list = []
        idx = 1

        allowed = {
            "status", "ocr_text", "ocr_chars", "ocr_pages",
            "confidence_score", "extraction_result", "error_message",
            "minio_key", "batch_job_id",
        }
        for k, v in updates.items():
            if k not in allowed:
                continue
            if k == "extraction_result" and isinstance(v, dict):
                v = json.dumps(v, ensure_ascii=False)
            if k == "extraction_result" and v is not None:
                sets.append(f"{k} = ${idx}::jsonb")
            else:
                sets.append(f"{k} = ${idx}")
            params.append(v)
            idx += 1

        if len(sets) == 1:
            return

        params.append(doc_id)
        query = f"UPDATE workspace_documents SET {', '.join(sets)} WHERE id = ${idx}"
        await self.pg.execute(query, *params)

    async def get_document_stats(
        self,
        workspace_id: str,
    ) -> Dict[str, Any]:
        row = await self.pg.fetchrow(
            """
            SELECT
                count(*)::int AS total,
                count(*) FILTER (WHERE status = 'completed')::int AS successful,
                count(*) FILTER (WHERE status = 'failed')::int AS failed,
                count(*) FILTER (WHERE status = 'low_confidence')::int AS low_confidence,
                count(*) FILTER (WHERE status = 'processing')::int AS in_progress,
                count(*) FILTER (WHERE status IN ('completed','failed','low_confidence'))::int AS processed,
                avg(confidence_score) AS avg_confidence
            FROM workspace_documents WHERE workspace_id = $1
            """,
            workspace_id,
        )
        if not row:
            return {"total": 0}
        return dict(row)

    async def get_queued_documents(
        self,
        batch_job_id: str,
    ) -> List[Dict[str, Any]]:
        rows = await self.pg.fetch(
            """
            SELECT id, file_path, file_name, minio_key
            FROM workspace_documents
            WHERE batch_job_id = $1 AND status = 'queued'
            ORDER BY sequence
            """,
            batch_job_id,
        )
        return [dict(r) for r in rows]

    # =========================================================================
    # BATCH JOBS
    # =========================================================================

    async def create_batch_job(self, data: Dict[str, Any]) -> str:
        job_id = data.get("id", f"batch-{uuid.uuid4().hex[:12]}")
        await self.pg.execute(
            """
            INSERT INTO batch_jobs (id, workspace_id, status, total_documents)
            VALUES ($1, $2, $3, $4)
            """,
            job_id,
            data["workspace_id"],
            data.get("status", "created"),
            data.get("total_documents", 0),
        )
        return job_id

    async def get_batch_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        row = await self.pg.fetchrow(
            "SELECT * FROM batch_jobs WHERE id = $1", job_id,
        )
        return dict(row) if row else None

    async def update_batch_job(
        self,
        job_id: str,
        updates: Dict[str, Any],
    ) -> None:
        sets: list = []
        params: list = []
        idx = 1

        for k, v in updates.items():
            sets.append(f"{k} = ${idx}")
            params.append(v)
            idx += 1

        if not sets:
            return

        params.append(job_id)
        query = f"UPDATE batch_jobs SET {', '.join(sets)} WHERE id = ${idx}"
        await self.pg.execute(query, *params)

    async def get_batch_progress(self, job_id: str) -> Dict[str, Any]:
        row = await self.pg.fetchrow(
            """
            SELECT bj.*,
                   COALESCE(d.total, 0) AS doc_total,
                   COALESCE(d.processed, 0) AS doc_processed,
                   COALESCE(d.successful, 0) AS doc_successful,
                   COALESCE(d.failed, 0) AS doc_failed,
                   COALESCE(d.low_conf, 0) AS doc_low_conf
            FROM batch_jobs bj
            LEFT JOIN LATERAL (
                SELECT count(*)::int AS total,
                       count(*) FILTER (WHERE wd.status IN ('completed','failed','low_confidence'))::int AS processed,
                       count(*) FILTER (WHERE wd.status = 'completed')::int AS successful,
                       count(*) FILTER (WHERE wd.status = 'failed')::int AS failed,
                       count(*) FILTER (WHERE wd.status = 'low_confidence')::int AS low_conf
                FROM workspace_documents wd WHERE wd.batch_job_id = bj.id
            ) d ON TRUE
            WHERE bj.id = $1
            """,
            job_id,
        )
        return dict(row) if row else {"error": "BatchJob not found"}

    async def list_batch_jobs(
        self,
        workspace_id: str,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if status:
            rows = await self.pg.fetch(
                "SELECT * FROM batch_jobs WHERE workspace_id = $1 AND status = $2 ORDER BY created_at DESC",
                workspace_id, status,
            )
        else:
            rows = await self.pg.fetch(
                "SELECT * FROM batch_jobs WHERE workspace_id = $1 ORDER BY created_at DESC",
                workspace_id,
            )
        return [dict(r) for r in rows]

    # =========================================================================
    # ELICITATION REQUESTS
    # =========================================================================

    async def create_elicitation(self, data: Dict[str, Any]) -> str:
        req_id = data.get("id", f"elicit-{uuid.uuid4().hex[:12]}")
        result_json = data.get("extraction_result", "{}")
        if isinstance(result_json, dict):
            result_json = json.dumps(result_json, ensure_ascii=False)
        await self.pg.execute(
            """
            INSERT INTO elicitation_requests
                (id, doc_id, workspace_id, batch_job_id, extraction_result,
                 confidence_score, file_name, status)
            VALUES ($1, $2, $3, NULLIF($4,''), $5::jsonb, $6, $7, $8)
            """,
            req_id,
            data.get("doc_id", ""),
            data["workspace_id"],
            data.get("batch_job_id", ""),
            result_json,
            data.get("confidence_score", 0.0),
            data.get("file_name", ""),
            data.get("status", "pending"),
        )
        return req_id

    async def get_elicitation(self, req_id: str) -> Optional[Dict[str, Any]]:
        row = await self.pg.fetchrow(
            "SELECT * FROM elicitation_requests WHERE id = $1", req_id,
        )
        return dict(row) if row else None

    async def list_elicitations(
        self,
        workspace_id: str,
        status: str = "pending",
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        rows = await self.pg.fetch(
            """
            SELECT * FROM elicitation_requests
            WHERE workspace_id = $1 AND status = $2
            ORDER BY confidence_score ASC, created_at ASC
            LIMIT $3 OFFSET $4
            """,
            workspace_id, status, limit, offset,
        )

        counts = await self.pg.fetchrow(
            """
            SELECT count(*)::int AS total,
                   count(*) FILTER (WHERE status = 'pending')::int AS pending,
                   count(*) FILTER (WHERE status = 'accepted')::int AS accepted,
                   count(*) FILTER (WHERE status = 'rejected')::int AS rejected
            FROM elicitation_requests WHERE workspace_id = $1
            """,
            workspace_id,
        )

        items = []
        for r in rows:
            d = dict(r)
            extraction = {}
            er = d.get("extraction_result")
            if isinstance(er, str):
                try:
                    extraction = json.loads(er)
                except (json.JSONDecodeError, TypeError):
                    pass
            elif isinstance(er, dict):
                extraction = er

            items.append({
                "id": d.get("id", ""),
                "doc_id": d.get("doc_id", ""),
                "file_name": d.get("file_name", ""),
                "confidence_score": d.get("confidence_score", 0),
                "status": d.get("status", "pending"),
                "node_count": len(extraction.get("nodes", [])),
                "relationship_count": len(extraction.get("relationships", [])),
                "extraction_preview": extraction,
            })

        c = dict(counts) if counts else {"total": 0, "pending": 0, "accepted": 0, "rejected": 0}
        return {
            "workspace_id": workspace_id,
            "total": c.get("total", 0) or 0,
            "pending": c.get("pending", 0) or 0,
            "accepted": c.get("accepted", 0) or 0,
            "rejected": c.get("rejected", 0) or 0,
            "items": items,
        }

    async def resolve_elicitation(
        self,
        req_id: str,
        action: str,
        modified_result: Optional[str] = None,
    ) -> Dict[str, Any]:
        req = await self.get_elicitation(req_id)
        if not req:
            return {"error": "Elicitation request not found"}

        doc_id = req.get("doc_id", "")

        if action == "accept":
            await self.pg.execute(
                "UPDATE elicitation_requests SET status = 'accepted', resolved_at = NOW() WHERE id = $1",
                req_id,
            )
            await self.update_document(doc_id, {"status": "completed"})
        elif action == "reject":
            await self.pg.execute(
                "UPDATE elicitation_requests SET status = 'rejected', resolved_at = NOW() WHERE id = $1",
                req_id,
            )
            await self.update_document(doc_id, {"status": "skipped"})
        elif action == "modify" and modified_result:
            await self.pg.execute(
                """
                UPDATE elicitation_requests
                SET status = 'accepted', extraction_result = $1::jsonb, resolved_at = NOW()
                WHERE id = $2
                """,
                modified_result, req_id,
            )
            await self.update_document(doc_id, {
                "status": "completed",
                "extraction_result": modified_result,
            })
        else:
            return {"error": f"Invalid action: {action}"}

        return {"id": req_id, "action": action, "doc_id": doc_id, "status": "resolved"}

    # =========================================================================
    # WORKSPACE SCHEMA JUNCTION (entity_schemas / relationship_schemas links)
    # =========================================================================

    async def link_entity_schema(self, workspace_id: str, schema_id: str) -> None:
        await self.pg.execute(
            """
            INSERT INTO workspace_entity_schemas (workspace_id, schema_id)
            VALUES ($1, $2)
            ON CONFLICT DO NOTHING
            """,
            workspace_id, schema_id,
        )

    async def link_relationship_schema(self, workspace_id: str, schema_id: str) -> None:
        await self.pg.execute(
            """
            INSERT INTO workspace_relationship_schemas (workspace_id, schema_id)
            VALUES ($1, $2)
            ON CONFLICT DO NOTHING
            """,
            workspace_id, schema_id,
        )

    async def get_workspace_entity_schemas(self, workspace_id: str) -> List[Dict[str, Any]]:
        rows = await self.pg.fetch(
            """
            SELECT es.* FROM entity_schemas es
            JOIN workspace_entity_schemas wes ON wes.schema_id = es.id
            WHERE wes.workspace_id = $1
            """,
            workspace_id,
        )
        return [dict(r) for r in rows]

    async def get_workspace_relationship_schemas(self, workspace_id: str) -> List[Dict[str, Any]]:
        rows = await self.pg.fetch(
            """
            SELECT rs.* FROM relationship_schemas rs
            JOIN workspace_relationship_schemas wrs ON wrs.schema_id = rs.id
            WHERE wrs.workspace_id = $1
            """,
            workspace_id,
        )
        return [dict(r) for r in rows]
