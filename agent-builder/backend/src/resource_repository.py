"""
Resource Repository
===================

PostgreSQL-backed repository for Resource and ResourceDocument CRUD.
Uses the existing asyncpg pool from PostgresClient (Event Store DB).
"""

import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from .event_store.postgres_client import PostgresClient

logger = logging.getLogger(__name__)


class ResourceRepository:
    """Async CRUD operations for resources and resource_documents tables."""

    def __init__(self, pg: PostgresClient):
        self.pg = pg

    # -------------------------------------------------------------------------
    # Resource CRUD
    # -------------------------------------------------------------------------

    async def create(
        self,
        name: str,
        resource_type: str = "minio",
        tenant_id: str = "default",
        description: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        resource_id = str(uuid.uuid4())
        minio_prefix = f"resources/{resource_id}/"
        metadata_json = json.dumps(metadata or {})

        row = await self.pg.fetchrow(
            """
            INSERT INTO resources (id, name, type, description, tenant_id, minio_prefix, metadata)
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
            RETURNING id, name, type, description, tenant_id, minio_bucket, minio_prefix,
                      total_documents, extracted_docs, status, metadata, created_at, updated_at
            """,
            uuid.UUID(resource_id), name, resource_type, description,
            tenant_id, minio_prefix, metadata_json,
        )
        return dict(row) if row else {"id": resource_id}

    async def get(self, resource_id: str) -> Optional[Dict[str, Any]]:
        row = await self.pg.fetchrow(
            """
            SELECT id, name, type, description, workspace_id, tenant_id,
                   minio_bucket, minio_prefix, total_documents, extracted_docs,
                   status, metadata, created_at, updated_at
            FROM resources WHERE id = $1
            """,
            uuid.UUID(resource_id),
        )
        return dict(row) if row else None

    async def list_by_tenant(
        self,
        tenant_id: str = "default",
        limit: int = 50,
        offset: int = 0,
        resource_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if resource_type:
            rows = await self.pg.fetch(
                """
                SELECT id, name, type, description, status, total_documents, extracted_docs,
                       workspace_id, metadata, created_at
                FROM resources
                WHERE tenant_id = $1 AND type = $2
                ORDER BY created_at DESC
                LIMIT $3 OFFSET $4
                """,
                tenant_id, resource_type, limit, offset,
            )
        else:
            rows = await self.pg.fetch(
                """
                SELECT id, name, type, description, status, total_documents, extracted_docs,
                       workspace_id, metadata, created_at
                FROM resources
                WHERE tenant_id = $1
                ORDER BY created_at DESC
                LIMIT $2 OFFSET $3
                """,
                tenant_id, limit, offset,
            )
        return [dict(r) for r in rows]

    async def attach_to_workspace(
        self, resource_id: str, workspace_id: str
    ) -> None:
        await self.pg.execute(
            """
            UPDATE resources
            SET workspace_id = $1, updated_at = NOW()
            WHERE id = $2
            """,
            workspace_id, uuid.UUID(resource_id),
        )
        await self.pg.execute(
            """
            UPDATE workspaces
            SET resource_id = $1, updated_at = NOW()
            WHERE id = $2
            """,
            resource_id, workspace_id,
        )

    async def update_status(self, resource_id: str, status: str) -> None:
        await self.pg.execute(
            "UPDATE resources SET status = $1, updated_at = NOW() WHERE id = $2",
            status, uuid.UUID(resource_id),
        )

    async def delete(self, resource_id: str) -> None:
        await self.pg.execute(
            "DELETE FROM resources WHERE id = $1",
            uuid.UUID(resource_id),
        )

    # -------------------------------------------------------------------------
    # ResourceDocument CRUD
    # -------------------------------------------------------------------------

    async def add_document(
        self,
        resource_id: str,
        file_name: str,
        minio_key: str,
        file_size: int = 0,
        file_type: str = "",
    ) -> str:
        doc_id = str(uuid.uuid4())
        await self.pg.execute(
            """
            INSERT INTO resource_documents
                (id, resource_id, file_name, minio_key, file_size, file_type)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            uuid.UUID(doc_id), uuid.UUID(resource_id),
            file_name, minio_key, file_size, file_type,
        )
        await self.pg.execute(
            """
            UPDATE resources
            SET total_documents = total_documents + 1, updated_at = NOW()
            WHERE id = $1
            """,
            uuid.UUID(resource_id),
        )
        return doc_id

    async def get_documents(
        self,
        resource_id: str,
        extraction_status: Optional[str] = None,
        processing_status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        conditions = ["resource_id = $1"]
        params: list = [uuid.UUID(resource_id)]
        idx = 2

        if extraction_status:
            conditions.append(f"extraction_status = ${idx}")
            params.append(extraction_status)
            idx += 1

        if processing_status:
            conditions.append(f"processing_status = ${idx}")
            params.append(processing_status)
            idx += 1

        params.extend([limit, offset])

        query = f"""
            SELECT id, file_name, minio_key, file_size, file_type,
                   page_count, image_count, extraction_status, processing_status,
                   confidence_score, error_message, created_at
            FROM resource_documents
            WHERE {' AND '.join(conditions)}
            ORDER BY created_at
            LIMIT ${idx} OFFSET ${idx + 1}
        """
        rows = await self.pg.fetch(query, *params)
        return [dict(r) for r in rows]

    async def list_documents(
        self, resource_id: str, limit: int = 100, offset: int = 0,
    ) -> List[Dict[str, Any]]:
        return await self.get_documents(resource_id, limit=limit, offset=offset)

    async def get_document(self, doc_id: str) -> Optional[Dict[str, Any]]:
        row = await self.pg.fetchrow(
            """
            SELECT id, resource_id, file_name, minio_key, file_size, file_type,
                   page_count, image_count, extraction_status, processing_status,
                   confidence_score, extraction_result, error_message, metadata,
                   created_at, updated_at
            FROM resource_documents WHERE id = $1
            """,
            uuid.UUID(doc_id),
        )
        return dict(row) if row else None

    async def update_document_extraction(
        self,
        doc_id: str,
        status: str,
        page_count: int = 0,
        image_count: int = 0,
        error_message: str = "",
    ) -> None:
        await self.pg.execute(
            """
            UPDATE resource_documents
            SET extraction_status = $1, page_count = $2, image_count = $3,
                error_message = $4, updated_at = NOW()
            WHERE id = $5
            """,
            status, page_count, image_count, error_message, uuid.UUID(doc_id),
        )
        if status == "ready":
            row = await self.pg.fetchrow(
                "SELECT resource_id FROM resource_documents WHERE id = $1",
                uuid.UUID(doc_id),
            )
            if row:
                await self._increment_extracted(str(row["resource_id"]))

    async def update_document_processing(
        self,
        doc_id: str,
        status: str,
        confidence_score: float = 0.0,
        extraction_result: Optional[Dict] = None,
        error_message: str = "",
    ) -> None:
        import json
        result_json = json.dumps(extraction_result) if extraction_result else None
        await self.pg.execute(
            """
            UPDATE resource_documents
            SET processing_status = $1, confidence_score = $2,
                extraction_result = $3::jsonb, error_message = $4, updated_at = NOW()
            WHERE id = $5
            """,
            status, confidence_score, result_json, error_message, uuid.UUID(doc_id),
        )

    # -------------------------------------------------------------------------
    # Aggregation
    # -------------------------------------------------------------------------

    async def get_status_breakdown(
        self, resource_id: str
    ) -> Dict[str, Dict[str, int]]:
        ext_rows = await self.pg.fetch(
            """
            SELECT extraction_status AS status, COUNT(*)::int AS cnt
            FROM resource_documents WHERE resource_id = $1
            GROUP BY extraction_status
            """,
            uuid.UUID(resource_id),
        )
        proc_rows = await self.pg.fetch(
            """
            SELECT processing_status AS status, COUNT(*)::int AS cnt
            FROM resource_documents WHERE resource_id = $1
            GROUP BY processing_status
            """,
            uuid.UUID(resource_id),
        )
        return {
            "extraction": {r["status"]: r["cnt"] for r in ext_rows},
            "processing": {r["status"]: r["cnt"] for r in proc_rows},
        }

    async def update_resource_counts(self, resource_id: str) -> None:
        row = await self.pg.fetchrow(
            """
            SELECT
                COUNT(*)::int AS total,
                SUM(CASE WHEN extraction_status = 'ready' THEN 1 ELSE 0 END)::int AS extracted
            FROM resource_documents WHERE resource_id = $1
            """,
            uuid.UUID(resource_id),
        )
        if row:
            await self.pg.execute(
                """
                UPDATE resources
                SET total_documents = $1, extracted_docs = $2, updated_at = NOW()
                WHERE id = $3
                """,
                row["total"], row["extracted"], uuid.UUID(resource_id),
            )
            if row["total"] > 0 and row["total"] == row["extracted"]:
                await self.update_status(resource_id, "ready")

    async def _increment_extracted(self, resource_id: str) -> None:
        await self.pg.execute(
            """
            UPDATE resources
            SET extracted_docs = extracted_docs + 1, updated_at = NOW()
            WHERE id = $1
            """,
            uuid.UUID(resource_id),
        )
        row = await self.pg.fetchrow(
            "SELECT total_documents, extracted_docs FROM resources WHERE id = $1",
            uuid.UUID(resource_id),
        )
        if row and row["total_documents"] > 0 and row["total_documents"] == row["extracted_docs"]:
            await self.update_status(resource_id, "ready")
