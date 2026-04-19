"""
Platform Router
===============

CRUD endpoints for platform-level entities:
- /api/v2/platform/models       — LLM model catalog
- /api/v2/platform/evaluations  — Agent evaluation definitions
- /api/v2/platform/guardrails   — Safety rules
- /api/v2/platform/tools        — Agent tool catalog (DB)
- /api/v2/platform/mcp-tools    — MCP Gateway tool discovery (proxy)
- /api/v2/platform/mcp-gateways — MCP Gateway server list (proxy)
"""

from __future__ import annotations

import datetime
import logging
import os
import uuid as _uuid
from typing import Any, Optional
from uuid import UUID

import httpx
import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from src.agent.auth.middleware import CurrentUser, get_optional_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["platform"])

MCP_GATEWAY_URL = os.getenv("MCP_GATEWAY_URL", "")
MCP_GATEWAY_JWT_SECRET = os.getenv("MCP_GATEWAY_JWT_SECRET", "")
MCP_GATEWAY_ADMIN_EMAIL = os.getenv("MCP_GATEWAY_ADMIN_EMAIL", "admin@example.com")


def _gateway_token() -> str:
    """Generate a short-lived JWT accepted by the MCP Gateway."""
    now = datetime.datetime.now(datetime.timezone.utc)
    payload = {
        "sub": MCP_GATEWAY_ADMIN_EMAIL,
        "email": MCP_GATEWAY_ADMIN_EMAIL,
        "iat": now,
        "iss": "mcpgateway",
        "aud": "mcpgateway-api",
        "jti": str(_uuid.uuid4()),
        "exp": now + datetime.timedelta(hours=1),
    }
    return pyjwt.encode(payload, MCP_GATEWAY_JWT_SECRET, algorithm="HS256")


def _pg(request: Request):
    pg = getattr(request.app.state, "pg", None)
    if pg is None:
        raise HTTPException(503, "Database not available")
    return pg


# =============================================================================
# MODELS CRUD
# =============================================================================

class ModelCreate(BaseModel):
    provider: str
    model_name: str
    display_name: Optional[str] = None
    description: str = ""
    input_cost_per_1k: Optional[float] = None
    output_cost_per_1k: Optional[float] = None
    context_window: Optional[int] = None
    max_output_tokens: Optional[int] = None
    supports_vision: bool = False
    supports_function_calling: bool = True
    finetune_base_model: Optional[str] = None
    finetune_status: Optional[str] = None
    finetune_config: dict[str, Any] = {}
    is_active: bool = True


class ModelUpdate(BaseModel):
    provider: Optional[str] = None
    model_name: Optional[str] = None
    display_name: Optional[str] = None
    description: Optional[str] = None
    input_cost_per_1k: Optional[float] = None
    output_cost_per_1k: Optional[float] = None
    context_window: Optional[int] = None
    max_output_tokens: Optional[int] = None
    supports_vision: Optional[bool] = None
    supports_function_calling: Optional[bool] = None
    finetune_base_model: Optional[str] = None
    finetune_status: Optional[str] = None
    finetune_config: Optional[dict[str, Any]] = None
    is_active: Optional[bool] = None


def _row_to_model(row) -> dict:
    return {
        "model_id": str(row["model_id"]),
        "tenant_id": str(row["tenant_id"]) if row["tenant_id"] else None,
        "provider": row["provider"],
        "model_name": row["model_name"],
        "display_name": row["display_name"],
        "description": row["description"],
        "input_cost_per_1k": float(row["input_cost_per_1k"]) if row["input_cost_per_1k"] is not None else None,
        "output_cost_per_1k": float(row["output_cost_per_1k"]) if row["output_cost_per_1k"] is not None else None,
        "context_window": row["context_window"],
        "max_output_tokens": row["max_output_tokens"],
        "supports_vision": row["supports_vision"],
        "supports_function_calling": row["supports_function_calling"],
        "finetune_base_model": row["finetune_base_model"],
        "finetune_status": row["finetune_status"],
        "finetune_config": row["finetune_config"] or {},
        "is_active": row["is_active"],
        "created_at": str(row["created_at"]) if row["created_at"] else None,
    }


@router.get("/models")
async def list_models(
    request: Request,
    provider: Optional[str] = None,
    user: Optional[CurrentUser] = Depends(get_optional_user),
):
    pg = _pg(request)
    tenant_filter = ""
    params: list = []
    idx = 1

    if user:
        tenant_filter = f"WHERE tenant_id = ${idx} OR tenant_id IS NULL"
        params.append(str(user.tenant_id))
        idx += 1

    if provider:
        prefix = "AND" if tenant_filter else "WHERE"
        tenant_filter += f" {prefix} provider = ${idx}"
        params.append(provider)
        idx += 1

    rows = await pg.fetch(
        f"SELECT * FROM llm_models {tenant_filter} ORDER BY provider, model_name",
        *params,
    )
    return {"models": [_row_to_model(r) for r in rows], "total": len(rows)}


@router.post("/models", status_code=201)
async def create_model(
    body: ModelCreate,
    request: Request,
    user: Optional[CurrentUser] = Depends(get_optional_user),
):
    pg = _pg(request)
    import json
    tid = str(user.tenant_id) if user else None

    row = await pg.fetchrow(
        """
        INSERT INTO llm_models (
            tenant_id, provider, model_name, display_name, description,
            input_cost_per_1k, output_cost_per_1k, context_window, max_output_tokens,
            supports_vision, supports_function_calling,
            finetune_base_model, finetune_status, finetune_config, is_active
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14::jsonb, $15)
        RETURNING *
        """,
        tid, body.provider, body.model_name, body.display_name, body.description,
        body.input_cost_per_1k, body.output_cost_per_1k, body.context_window, body.max_output_tokens,
        body.supports_vision, body.supports_function_calling,
        body.finetune_base_model, body.finetune_status,
        json.dumps(body.finetune_config, ensure_ascii=False),
        body.is_active,
    )
    return _row_to_model(row)


@router.get("/models/{model_id}")
async def get_model(model_id: str, request: Request):
    pg = _pg(request)
    row = await pg.fetchrow("SELECT * FROM llm_models WHERE model_id = $1", model_id)
    if not row:
        raise HTTPException(404, "Model not found")
    return _row_to_model(row)


@router.put("/models/{model_id}")
async def update_model(model_id: str, body: ModelUpdate, request: Request):
    pg = _pg(request)
    import json

    existing = await pg.fetchrow("SELECT * FROM llm_models WHERE model_id = $1", model_id)
    if not existing:
        raise HTTPException(404, "Model not found")

    updates = body.model_dump(exclude_none=True)
    if not updates:
        return _row_to_model(existing)

    set_clauses = []
    params = []
    idx = 1
    for key, val in updates.items():
        if key == "finetune_config":
            set_clauses.append(f"{key} = ${idx}::jsonb")
            params.append(json.dumps(val, ensure_ascii=False))
        else:
            set_clauses.append(f"{key} = ${idx}")
            params.append(val)
        idx += 1

    set_clauses.append(f"updated_at = NOW()")
    params.append(model_id)

    row = await pg.fetchrow(
        f"UPDATE llm_models SET {', '.join(set_clauses)} WHERE model_id = ${idx} RETURNING *",
        *params,
    )
    return _row_to_model(row)


@router.delete("/models/{model_id}")
async def delete_model(model_id: str, request: Request):
    pg = _pg(request)
    deleted = await pg.fetchval(
        "DELETE FROM llm_models WHERE model_id = $1 RETURNING model_id", model_id
    )
    if not deleted:
        raise HTTPException(404, "Model not found")
    return {"status": "deleted", "model_id": model_id}


# =============================================================================
# EVALUATIONS CRUD
# =============================================================================

class EvaluationCreate(BaseModel):
    agent_id: Optional[str] = None
    name: str
    eval_type: str = "accuracy"
    config: dict[str, Any] = {}
    status: str = "draft"


class EvaluationUpdate(BaseModel):
    agent_id: Optional[str] = None
    name: Optional[str] = None
    eval_type: Optional[str] = None
    config: Optional[dict[str, Any]] = None
    status: Optional[str] = None
    score: Optional[float] = None


def _row_to_eval(row) -> dict:
    return {
        "evaluation_id": str(row["evaluation_id"]),
        "tenant_id": str(row["tenant_id"]) if row["tenant_id"] else None,
        "agent_id": row["agent_id"],
        "name": row["name"],
        "eval_type": row["eval_type"],
        "config": row["config"] or {},
        "last_run_at": str(row["last_run_at"]) if row["last_run_at"] else None,
        "score": float(row["score"]) if row["score"] is not None else None,
        "status": row["status"],
        "created_at": str(row["created_at"]) if row["created_at"] else None,
    }


@router.get("/evaluations")
async def list_evaluations(
    request: Request,
    user: Optional[CurrentUser] = Depends(get_optional_user),
):
    pg = _pg(request)
    if user:
        rows = await pg.fetch(
            "SELECT * FROM evaluations WHERE tenant_id = $1 OR tenant_id IS NULL ORDER BY created_at DESC",
            str(user.tenant_id),
        )
    else:
        rows = await pg.fetch("SELECT * FROM evaluations ORDER BY created_at DESC")
    return {"evaluations": [_row_to_eval(r) for r in rows], "total": len(rows)}


@router.post("/evaluations", status_code=201)
async def create_evaluation(
    body: EvaluationCreate,
    request: Request,
    user: Optional[CurrentUser] = Depends(get_optional_user),
):
    pg = _pg(request)
    import json
    tid = str(user.tenant_id) if user else None

    row = await pg.fetchrow(
        """
        INSERT INTO evaluations (tenant_id, agent_id, name, eval_type, config, status)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6)
        RETURNING *
        """,
        tid, body.agent_id, body.name, body.eval_type,
        json.dumps(body.config, ensure_ascii=False), body.status,
    )
    return _row_to_eval(row)


@router.get("/evaluations/{evaluation_id}")
async def get_evaluation(evaluation_id: str, request: Request):
    pg = _pg(request)
    row = await pg.fetchrow("SELECT * FROM evaluations WHERE evaluation_id = $1", evaluation_id)
    if not row:
        raise HTTPException(404, "Evaluation not found")
    return _row_to_eval(row)


@router.put("/evaluations/{evaluation_id}")
async def update_evaluation(evaluation_id: str, body: EvaluationUpdate, request: Request):
    pg = _pg(request)
    import json

    existing = await pg.fetchrow("SELECT * FROM evaluations WHERE evaluation_id = $1", evaluation_id)
    if not existing:
        raise HTTPException(404, "Evaluation not found")

    updates = body.model_dump(exclude_none=True)
    if not updates:
        return _row_to_eval(existing)

    set_clauses = []
    params = []
    idx = 1
    for key, val in updates.items():
        if key == "config":
            set_clauses.append(f"{key} = ${idx}::jsonb")
            params.append(json.dumps(val, ensure_ascii=False))
        else:
            set_clauses.append(f"{key} = ${idx}")
            params.append(val)
        idx += 1

    set_clauses.append("updated_at = NOW()")
    params.append(evaluation_id)

    row = await pg.fetchrow(
        f"UPDATE evaluations SET {', '.join(set_clauses)} WHERE evaluation_id = ${idx} RETURNING *",
        *params,
    )
    return _row_to_eval(row)


@router.delete("/evaluations/{evaluation_id}")
async def delete_evaluation(evaluation_id: str, request: Request):
    pg = _pg(request)
    deleted = await pg.fetchval(
        "DELETE FROM evaluations WHERE evaluation_id = $1 RETURNING evaluation_id", evaluation_id
    )
    if not deleted:
        raise HTTPException(404, "Evaluation not found")
    return {"status": "deleted", "evaluation_id": evaluation_id}


# =============================================================================
# GUARDRAILS CRUD
# =============================================================================

class GuardrailCreate(BaseModel):
    name: str
    guardrail_type: str = "content_filter"
    config: dict[str, Any] = {}
    is_active: bool = True
    scope: str = "global"


class GuardrailUpdate(BaseModel):
    name: Optional[str] = None
    guardrail_type: Optional[str] = None
    config: Optional[dict[str, Any]] = None
    is_active: Optional[bool] = None
    scope: Optional[str] = None


def _row_to_guardrail(row) -> dict:
    return {
        "guardrail_id": str(row["guardrail_id"]),
        "tenant_id": str(row["tenant_id"]) if row["tenant_id"] else None,
        "name": row["name"],
        "guardrail_type": row["guardrail_type"],
        "config": row["config"] or {},
        "is_active": row["is_active"],
        "scope": row["scope"],
        "created_at": str(row["created_at"]) if row["created_at"] else None,
    }


@router.get("/guardrails")
async def list_guardrails(
    request: Request,
    user: Optional[CurrentUser] = Depends(get_optional_user),
):
    pg = _pg(request)
    if user:
        rows = await pg.fetch(
            "SELECT * FROM guardrails WHERE tenant_id = $1 OR tenant_id IS NULL ORDER BY created_at DESC",
            str(user.tenant_id),
        )
    else:
        rows = await pg.fetch("SELECT * FROM guardrails ORDER BY created_at DESC")
    return {"guardrails": [_row_to_guardrail(r) for r in rows], "total": len(rows)}


@router.post("/guardrails", status_code=201)
async def create_guardrail(
    body: GuardrailCreate,
    request: Request,
    user: Optional[CurrentUser] = Depends(get_optional_user),
):
    pg = _pg(request)
    import json
    tid = str(user.tenant_id) if user else None

    row = await pg.fetchrow(
        """
        INSERT INTO guardrails (tenant_id, name, guardrail_type, config, is_active, scope)
        VALUES ($1, $2, $3, $4::jsonb, $5, $6)
        RETURNING *
        """,
        tid, body.name, body.guardrail_type,
        json.dumps(body.config, ensure_ascii=False), body.is_active, body.scope,
    )
    return _row_to_guardrail(row)


@router.get("/guardrails/{guardrail_id}")
async def get_guardrail(guardrail_id: str, request: Request):
    pg = _pg(request)
    row = await pg.fetchrow("SELECT * FROM guardrails WHERE guardrail_id = $1", guardrail_id)
    if not row:
        raise HTTPException(404, "Guardrail not found")
    return _row_to_guardrail(row)


@router.put("/guardrails/{guardrail_id}")
async def update_guardrail(guardrail_id: str, body: GuardrailUpdate, request: Request):
    pg = _pg(request)
    import json

    existing = await pg.fetchrow("SELECT * FROM guardrails WHERE guardrail_id = $1", guardrail_id)
    if not existing:
        raise HTTPException(404, "Guardrail not found")

    updates = body.model_dump(exclude_none=True)
    if not updates:
        return _row_to_guardrail(existing)

    set_clauses = []
    params = []
    idx = 1
    for key, val in updates.items():
        if key == "config":
            set_clauses.append(f"{key} = ${idx}::jsonb")
            params.append(json.dumps(val, ensure_ascii=False))
        else:
            set_clauses.append(f"{key} = ${idx}")
            params.append(val)
        idx += 1

    set_clauses.append("updated_at = NOW()")
    params.append(guardrail_id)

    row = await pg.fetchrow(
        f"UPDATE guardrails SET {', '.join(set_clauses)} WHERE guardrail_id = ${idx} RETURNING *",
        *params,
    )
    return _row_to_guardrail(row)


@router.delete("/guardrails/{guardrail_id}")
async def delete_guardrail(guardrail_id: str, request: Request):
    pg = _pg(request)
    deleted = await pg.fetchval(
        "DELETE FROM guardrails WHERE guardrail_id = $1 RETURNING guardrail_id", guardrail_id
    )
    if not deleted:
        raise HTTPException(404, "Guardrail not found")
    return {"status": "deleted", "guardrail_id": guardrail_id}


# =============================================================================
# TOOLS CRUD
# =============================================================================

class ToolCreate(BaseModel):
    name: str
    description: str = ""
    tool_type: str = "function"
    config: dict[str, Any] = {}
    schema_json: dict[str, Any] = {}
    is_active: bool = True


class ToolUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    tool_type: Optional[str] = None
    config: Optional[dict[str, Any]] = None
    schema_json: Optional[dict[str, Any]] = None
    is_active: Optional[bool] = None


def _row_to_tool(row) -> dict:
    return {
        "tool_id": str(row["tool_id"]),
        "tenant_id": str(row["tenant_id"]) if row["tenant_id"] else None,
        "name": row["name"],
        "description": row["description"],
        "tool_type": row["tool_type"],
        "config": row["config"] or {},
        "schema_json": row["schema_json"] or {},
        "is_active": row["is_active"],
        "created_at": str(row["created_at"]) if row["created_at"] else None,
    }


@router.get("/tools")
async def list_tools(
    request: Request,
    user: Optional[CurrentUser] = Depends(get_optional_user),
):
    pg = _pg(request)
    if user:
        rows = await pg.fetch(
            "SELECT * FROM agent_tools WHERE tenant_id = $1 OR tenant_id IS NULL ORDER BY created_at DESC",
            str(user.tenant_id),
        )
    else:
        rows = await pg.fetch("SELECT * FROM agent_tools ORDER BY created_at DESC")
    return {"tools": [_row_to_tool(r) for r in rows], "total": len(rows)}


@router.post("/tools", status_code=201)
async def create_tool(
    body: ToolCreate,
    request: Request,
    user: Optional[CurrentUser] = Depends(get_optional_user),
):
    pg = _pg(request)
    import json
    tid = str(user.tenant_id) if user else None

    row = await pg.fetchrow(
        """
        INSERT INTO agent_tools (tenant_id, name, description, tool_type, config, schema_json, is_active)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7)
        RETURNING *
        """,
        tid, body.name, body.description, body.tool_type,
        json.dumps(body.config, ensure_ascii=False),
        json.dumps(body.schema_json, ensure_ascii=False),
        body.is_active,
    )
    return _row_to_tool(row)


@router.get("/tools/{tool_id}")
async def get_tool(tool_id: str, request: Request):
    pg = _pg(request)
    row = await pg.fetchrow("SELECT * FROM agent_tools WHERE tool_id = $1", tool_id)
    if not row:
        raise HTTPException(404, "Tool not found")
    return _row_to_tool(row)


@router.put("/tools/{tool_id}")
async def update_tool(tool_id: str, body: ToolUpdate, request: Request):
    pg = _pg(request)
    import json

    existing = await pg.fetchrow("SELECT * FROM agent_tools WHERE tool_id = $1", tool_id)
    if not existing:
        raise HTTPException(404, "Tool not found")

    updates = body.model_dump(exclude_none=True)
    if not updates:
        return _row_to_tool(existing)

    set_clauses = []
    params = []
    idx = 1
    for key, val in updates.items():
        if key in ("config", "schema_json"):
            set_clauses.append(f"{key} = ${idx}::jsonb")
            params.append(json.dumps(val, ensure_ascii=False))
        else:
            set_clauses.append(f"{key} = ${idx}")
            params.append(val)
        idx += 1

    set_clauses.append("updated_at = NOW()")
    params.append(tool_id)

    row = await pg.fetchrow(
        f"UPDATE agent_tools SET {', '.join(set_clauses)} WHERE tool_id = ${idx} RETURNING *",
        *params,
    )
    return _row_to_tool(row)


@router.delete("/tools/{tool_id}")
async def delete_tool(tool_id: str, request: Request):
    pg = _pg(request)
    deleted = await pg.fetchval(
        "DELETE FROM agent_tools WHERE tool_id = $1 RETURNING tool_id", tool_id
    )
    if not deleted:
        raise HTTPException(404, "Tool not found")
    return {"status": "deleted", "tool_id": tool_id}


# =============================================================================
# MCP GATEWAY PROXY
# =============================================================================

@router.get("/mcp-tools")
async def list_mcp_tools():
    """Proxy: fetch tool list from MCP Gateway."""
    if not MCP_GATEWAY_URL:
        raise HTTPException(503, "MCP_GATEWAY_URL is not configured")
    try:
        token = _gateway_token()
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{MCP_GATEWAY_URL}/tools",
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            tools = resp.json()
        return {"tools": tools, "total": len(tools)}
    except httpx.HTTPStatusError as e:
        logger.error("MCP Gateway /tools returned %s: %s", e.response.status_code, e.response.text)
        raise HTTPException(502, f"MCP Gateway error: {e.response.status_code}")
    except Exception as e:
        logger.error("MCP Gateway /tools failed: %s", e)
        raise HTTPException(502, f"Cannot reach MCP Gateway: {e}")


@router.get("/mcp-gateways")
async def list_mcp_gateways():
    """Proxy: fetch registered gateways (upstream MCP servers) from MCP Gateway."""
    if not MCP_GATEWAY_URL:
        raise HTTPException(503, "MCP_GATEWAY_URL is not configured")
    try:
        token = _gateway_token()
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{MCP_GATEWAY_URL}/gateways",
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            gateways = resp.json()
        return {"gateways": gateways, "total": len(gateways)}
    except httpx.HTTPStatusError as e:
        logger.error("MCP Gateway /gateways returned %s: %s", e.response.status_code, e.response.text)
        raise HTTPException(502, f"MCP Gateway error: {e.response.status_code}")
    except Exception as e:
        logger.error("MCP Gateway /gateways failed: %s", e)
        raise HTTPException(502, f"Cannot reach MCP Gateway: {e}")


@router.patch("/mcp-tools/{tool_id}/toggle")
async def toggle_mcp_tool(tool_id: str, enabled: bool = True):
    """Proxy: enable/disable a tool on the MCP Gateway."""
    if not MCP_GATEWAY_URL:
        raise HTTPException(503, "MCP_GATEWAY_URL is not configured")
    try:
        token = _gateway_token()
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.patch(
                f"{MCP_GATEWAY_URL}/tools/{tool_id}",
                headers={"Authorization": f"Bearer {token}"},
                json={"enabled": enabled},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as e:
        logger.error("MCP Gateway PATCH tool %s: %s", e.response.status_code, e.response.text)
        raise HTTPException(502, f"MCP Gateway error: {e.response.status_code}")
    except Exception as e:
        logger.error("MCP Gateway toggle tool failed: %s", e)
        raise HTTPException(502, f"Cannot reach MCP Gateway: {e}")


# =============================================================================
# KNOWLEDGE BASES (graphrag_endpoints)
# =============================================================================

def _row_to_kb(row) -> dict:
    source_docs = row.get("source_documents") or []
    if isinstance(source_docs, str):
        import json as _json
        try:
            source_docs = _json.loads(source_docs)
        except (ValueError, TypeError):
            source_docs = []

    ontology = row.get("ontology_snapshot") or {}
    if isinstance(ontology, str):
        import json as _json
        try:
            ontology = _json.loads(ontology)
        except (ValueError, TypeError):
            ontology = {}

    entity_count = len(ontology.get("entity_types", []))
    relationship_count = len(ontology.get("relationship_types", []))

    return {
        "endpoint_id": row["endpoint_id"],
        "name": row.get("name") or "",
        "agent_id": row["agent_id"],
        "agent_name": row.get("agent_name") or row["agent_id"],
        "workflow_id": row.get("workflow_id") or "",
        "workflow_name": row.get("workflow_name") or "",
        "workflow_version": row.get("workflow_version", 1),
        "neo4j_uri": row.get("neo4j_uri", ""),
        "neo4j_database": row.get("neo4j_database", "neo4j"),
        "ontology_snapshot": ontology,
        "schema_summary": row.get("schema_summary", ""),
        "source_documents": source_docs,
        "source_document_count": len(source_docs),
        "entity_type_count": entity_count,
        "relationship_type_count": relationship_count,
        "status": row.get("status", "active"),
        "tenant_id": str(row["tenant_id"]) if row.get("tenant_id") else None,
        "published_at": str(row["published_at"]) if row.get("published_at") else None,
        "created_at": str(row["created_at"]) if row.get("created_at") else None,
    }


@router.get("/knowledge-bases")
async def list_knowledge_bases(
    request: Request,
    user: Optional[CurrentUser] = Depends(get_optional_user),
):
    """List all published Knowledge Bases (graphrag_endpoints) with agent/workflow names."""
    pg = _pg(request)
    tenant_filter = ""
    params: list = []
    idx = 1

    if user:
        tenant_filter = f"WHERE (g.tenant_id = ${idx} OR g.tenant_id IS NULL)"
        params.append(str(user.tenant_id))
        idx += 1

    rows = await pg.fetch(
        f"""
        SELECT g.*,
               COALESCE(
                   (SELECT value->>'name'
                    FROM agent_knowledge
                    WHERE agent_id = g.agent_id
                      AND knowledge_type = 'identity'
                      AND key = 'main'
                    ORDER BY version DESC LIMIT 1),
                   g.agent_id
               ) AS agent_name,
               COALESCE(w.name, '') AS workflow_name
        FROM graphrag_endpoints g
        LEFT JOIN workflows w ON w.workflow_id = g.workflow_id
        {tenant_filter}
        ORDER BY g.published_at DESC
        """,
        *params,
    )
    items = [_row_to_kb(r) for r in rows]
    return {"knowledge_bases": items, "total": len(items)}


@router.get("/knowledge-bases/{endpoint_id}")
async def get_knowledge_base(endpoint_id: str, request: Request):
    """Get a single Knowledge Base with full details including source documents."""
    pg = _pg(request)
    row = await pg.fetchrow(
        """
        SELECT g.*,
               COALESCE(
                   (SELECT value->>'name'
                    FROM agent_knowledge
                    WHERE agent_id = g.agent_id
                      AND knowledge_type = 'identity'
                      AND key = 'main'
                    ORDER BY version DESC LIMIT 1),
                   g.agent_id
               ) AS agent_name,
               COALESCE(w.name, '') AS workflow_name
        FROM graphrag_endpoints g
        LEFT JOIN workflows w ON w.workflow_id = g.workflow_id
        WHERE g.endpoint_id = $1
        """,
        endpoint_id,
    )
    if not row:
        raise HTTPException(404, "Knowledge base not found")
    return _row_to_kb(row)
