"""
Neo4j Cypher MCP Server - Multi-tenant graph database tools and resources.

Provides:
- read_cypher and read_cypher_with_embedding tools for executing Cypher queries
- MCP resources for KB schema discovery (node labels, relationship types, stats)
"""

import json
import logging
import os
import re
import unicodedata
from typing import Any, LiteralString, Optional, cast
from urllib.parse import parse_qs, urlparse

from fastmcp.server import FastMCP
from mcp.types import ToolAnnotations
from neo4j import Query, RoutingControl
from neo4j.exceptions import Neo4jError
from pydantic import Field

from utils.connection_pool import pool_manager

logger = logging.getLogger("mcp_services.neo4j")

READ_TIMEOUT = int(os.getenv("NEO4J_READ_TIMEOUT", "30"))

neo4j_mcp = FastMCP("Neo4j Cypher")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_unicode(text: str) -> str:
    if not text or not isinstance(text, str):
        return text
    try:
        normalized = unicodedata.normalize("NFC", text)
        return normalized.encode("utf-8", errors="replace").decode("utf-8").strip()
    except Exception:
        return text


def _is_write_query(query: str) -> bool:
    return bool(re.search(r"\b(MERGE|CREATE|SET|DELETE|REMOVE|ADD)\b", query, re.IGNORECASE))


def _value_sanitize(d: Any, list_limit: int = 128) -> Any:
    if isinstance(d, dict):
        new = {}
        for k, v in d.items():
            if isinstance(v, dict):
                s = _value_sanitize(v)
                if s is not None:
                    new[k] = s
            elif isinstance(v, list):
                if len(v) < list_limit:
                    s = _value_sanitize(v)
                    if s is not None:
                        new[k] = s
            else:
                new[k] = v
        return new
    elif isinstance(d, list):
        return [_value_sanitize(i) for i in d if _value_sanitize(i) is not None] if len(d) < list_limit else None
    return d


def _handle_datetime(obj: Any) -> Any:
    if hasattr(obj, "year") and hasattr(obj, "month") and hasattr(obj, "day"):
        if hasattr(obj, "hour"):
            return f"{obj.year}-{obj.month:02d}-{obj.day:02d}T{obj.hour:02d}:{obj.minute:02d}:{obj.second:02d}"
        return f"{obj.year}-{obj.month:02d}-{obj.day:02d}"
    elif isinstance(obj, dict):
        return {k: _handle_datetime(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_handle_datetime(i) for i in obj]
    return obj


def _to_minimal_format(data: Any) -> str:
    if isinstance(data, str):
        data = json.loads(data)
    if not isinstance(data, list):
        return "Error: data must be a list"
    if not data:
        return "[]"
    records = []
    for i, rec in enumerate(data):
        props = [f"{k}:{v}" for k, v in rec.items()]
        records.append(f"(R:{i}){{{','.join(props)}}}")
    return "\n".join(records)


def _validate_credentials(db_url, db_username, db_password) -> Optional[str]:
    missing = []
    if not db_url:
        missing.append("db_url")
    if not db_username:
        missing.append("db_username")
    if not db_password:
        missing.append("db_password")
    if missing:
        return (
            f"MULTI-TENANT ERROR: Missing credentials: {', '.join(missing)}. "
            "db_url, db_username, db_password are required for every tool call."
        )
    return None


def _load_embedding_model(model_name: str):
    if model_name == "openai":
        from langchain_openai import OpenAIEmbeddings
        return OpenAIEmbeddings(), 1536
    raise ImportError(f"Unsupported embedding model: {model_name}")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@neo4j_mcp.tool(
    name="read_cypher",
    annotations=ToolAnnotations(
        title="Read Neo4j Cypher",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def read_cypher(
    query: str = Field(..., description="The Cypher query to execute."),
    params: dict[str, Any] = Field(dict(), description="Parameters for the Cypher query."),
    db_url: Optional[str] = Field(None, description="REQUIRED: Neo4j connection URL (bolt://host:port)"),
    db_username: Optional[str] = Field(None, description="REQUIRED: Neo4j username"),
    db_password: Optional[str] = Field(None, description="REQUIRED: Neo4j password"),
    db_database: Optional[str] = Field("neo4j", description="Neo4j database name (default: neo4j)"),
) -> str:
    """
    Execute a read-only Cypher query on a Neo4j database.

    MULTI-TENANT MODE: db_url, db_username, db_password are REQUIRED.

    USE THIS TOOL FOR: metadata queries - names, numbers, dates, counts, IDs.
    For document content/detail queries, use read_cypher_with_embedding instead.
    """
    err = _validate_credentials(db_url, db_username, db_password)
    if err:
        return err

    if _is_write_query(query):
        return "Error: Only MATCH (read) queries are allowed."

    try:
        driver = await pool_manager.get_driver(db_url, db_username, db_password)
        query_obj = Query(cast(LiteralString, query), timeout=float(READ_TIMEOUT))
        results = await driver.execute_query(
            query_obj,
            parameters_=params,
            routing_control=RoutingControl.READ,
            database_=db_database or "neo4j",
            result_transformer_=lambda r: r.data(),
        )
        processed = [_value_sanitize(_handle_datetime(r)) for r in results]
        return _to_minimal_format(processed)

    except Neo4jError as e:
        msg = str(e)
        if "SyntaxError" in msg:
            return f"Cypher syntax error: {msg}. Clause order: MATCH - WHERE - WITH - RETURN - ORDER BY - LIMIT"
        return f"Neo4j error: {msg}"
    except Exception as e:
        return f"Error: {e}"


@neo4j_mcp.tool(
    name="read_cypher_with_embedding",
    annotations=ToolAnnotations(
        title="Read Neo4j Cypher with Embedding",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def read_cypher_with_embedding(
    query_text: str = Field(
        ...,
        description="Semantic search text. Use ONLY content keywords, not metadata like names/dates.",
    ),
    cypher_query: str = Field(
        ...,
        description=(
            "Cypher query with $embedding_vector parameter and 'c.embedding IS NOT NULL' check. "
            "Example: MATCH (c:Chunk) WHERE c.embedding IS NOT NULL "
            "AND gds.similarity.cosine(c.embedding, $embedding_vector) > 0.8 "
            "RETURN c.text, gds.similarity.cosine(c.embedding, $embedding_vector) as score "
            "ORDER BY score DESC"
        ),
    ),
    params: dict[str, Any] = Field(dict(), description="Additional Cypher parameters. $embedding_vector is auto-added."),
    db_url: Optional[str] = Field(None, description="REQUIRED: Neo4j connection URL (bolt://host:port)"),
    db_username: Optional[str] = Field(None, description="REQUIRED: Neo4j username"),
    db_password: Optional[str] = Field(None, description="REQUIRED: Neo4j password"),
    db_database: Optional[str] = Field("neo4j", description="Neo4j database name (default: neo4j)"),
) -> str:
    """
    Execute a semantic search in document content (Chunks) using embeddings.

    MULTI-TENANT MODE: db_url, db_username, db_password are REQUIRED.

    USE THIS TOOL WHEN: question asks for details, lists, tables, explanations,
    or document content. First use read_cypher to find the entity, then
    use this tool to search within its Chunks.

    query_text should contain only content keywords (not metadata like names/dates).
    cypher_query MUST include $embedding_vector and 'c.embedding IS NOT NULL' check.
    """
    err = _validate_credentials(db_url, db_username, db_password)
    if err:
        return err

    if "$embedding_vector" not in cypher_query:
        return (
            "Error: cypher_query must contain $embedding_vector parameter. "
            "Example: MATCH (c:Chunk) WHERE c.embedding IS NOT NULL "
            "AND gds.similarity.cosine(c.embedding, $embedding_vector) > 0.75 "
            "RETURN c.text ORDER BY score DESC"
        )

    if _is_write_query(cypher_query):
        return "Error: Only MATCH (read) queries are allowed."

    try:
        model_name = os.getenv("EMBEDDING_MODEL", "openai")
        embedding_model, _ = _load_embedding_model(model_name)

        normalized = _normalize_unicode(query_text)
        embedding_vector = embedding_model.embed_query(normalized)

        params_with_emb = params.copy()
        params_with_emb["embedding_vector"] = embedding_vector

        driver = await pool_manager.get_driver(db_url, db_username, db_password)
        query_obj = Query(cast(LiteralString, cypher_query), timeout=float(READ_TIMEOUT))
        results = await driver.execute_query(
            query_obj,
            parameters_=params_with_emb,
            routing_control=RoutingControl.READ,
            database_=db_database or "neo4j",
            result_transformer_=lambda r: r.data(),
        )

        processed = [_value_sanitize(_handle_datetime(r)) for r in results]
        minimal = _to_minimal_format(processed)
        return f"Semantic search completed. Found {len(results)} results.\n\n{minimal}"

    except Neo4jError as e:
        msg = str(e)
        if "SyntaxError" in msg:
            return f"Cypher syntax error: {msg}. Clause order: MATCH - WHERE - WITH - RETURN - ORDER BY - LIMIT"
        return f"Neo4j error: {msg}"
    except Exception as e:
        return f"Error: {e}"


# ---------------------------------------------------------------------------
# MCP Resources - KB schema discovery
# ---------------------------------------------------------------------------

@neo4j_mcp.resource("neo4j://kb/schema")
async def kb_schema() -> str:
    """
    Knowledge Base schema: node labels, relationship types, and property keys.
    Uses default Neo4j connection from environment.
    """
    db_url = os.getenv("NEO4J_URI", "")
    db_user = os.getenv("NEO4J_USERNAME", "")
    db_pass = os.getenv("NEO4J_PASSWORD", "")
    db_name = os.getenv("NEO4J_DATABASE", "neo4j")

    if not db_url or not db_user:
        return json.dumps({"error": "NEO4J_URI / NEO4J_USERNAME not configured"})

    try:
        driver = await pool_manager.get_driver(db_url, db_user, db_pass)

        labels_result = await driver.execute_query(
            "CALL db.labels() YIELD label RETURN collect(label) as labels",
            database_=db_name,
            result_transformer_=lambda r: r.data(),
        )
        labels = labels_result[0]["labels"] if labels_result else []

        rels_result = await driver.execute_query(
            "CALL db.relationshipTypes() YIELD relationshipType RETURN collect(relationshipType) as types",
            database_=db_name,
            result_transformer_=lambda r: r.data(),
        )
        rel_types = rels_result[0]["types"] if rels_result else []

        props_result = await driver.execute_query(
            "CALL db.propertyKeys() YIELD propertyKey RETURN collect(propertyKey) as keys",
            database_=db_name,
            result_transformer_=lambda r: r.data(),
        )
        prop_keys = props_result[0]["keys"] if props_result else []

        return json.dumps({
            "node_labels": labels,
            "relationship_types": rel_types,
            "property_keys": prop_keys,
            "database": db_name,
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"error": str(e)})


@neo4j_mcp.resource("neo4j://kb/stats")
async def kb_stats() -> str:
    """
    Knowledge Base statistics: node counts per label, relationship counts per type.
    Uses default Neo4j connection from environment.
    """
    db_url = os.getenv("NEO4J_URI", "")
    db_user = os.getenv("NEO4J_USERNAME", "")
    db_pass = os.getenv("NEO4J_PASSWORD", "")
    db_name = os.getenv("NEO4J_DATABASE", "neo4j")

    if not db_url or not db_user:
        return json.dumps({"error": "NEO4J_URI / NEO4J_USERNAME not configured"})

    try:
        driver = await pool_manager.get_driver(db_url, db_user, db_pass)

        node_counts = await driver.execute_query(
            """
            CALL db.labels() YIELD label
            CALL apoc.cypher.run('MATCH (n:`' + label + '`) RETURN count(n) as cnt', {}) YIELD value
            RETURN label, value.cnt as count
            """,
            database_=db_name,
            result_transformer_=lambda r: r.data(),
        )

        rel_counts = await driver.execute_query(
            """
            CALL db.relationshipTypes() YIELD relationshipType
            CALL apoc.cypher.run('MATCH ()-[r:`' + relationshipType + '`]->() RETURN count(r) as cnt', {}) YIELD value
            RETURN relationshipType as type, value.cnt as count
            """,
            database_=db_name,
            result_transformer_=lambda r: r.data(),
        )

        total_nodes = sum(r.get("count", 0) for r in node_counts)
        total_rels = sum(r.get("count", 0) for r in rel_counts)

        return json.dumps({
            "total_nodes": total_nodes,
            "total_relationships": total_rels,
            "nodes_by_label": {r["label"]: r["count"] for r in node_counts},
            "relationships_by_type": {r["type"]: r["count"] for r in rel_counts},
            "database": db_name,
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"error": str(e)})
