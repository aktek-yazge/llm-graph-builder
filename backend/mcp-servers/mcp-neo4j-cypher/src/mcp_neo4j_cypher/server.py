import json
import logging
import os
import re
import sys
from typing import Any, Literal, Optional

from fastmcp.exceptions import ToolError
from fastmcp.server import FastMCP
from fastmcp.tools.tool import TextContent, ToolResult
from mcp.types import ToolAnnotations
from neo4j import AsyncDriver, AsyncGraphDatabase, Query, RoutingControl
from neo4j.exceptions import ClientError, Neo4jError
from pydantic import Field
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .utils import _truncate_string_to_tokens, _value_sanitize

logger = logging.getLogger("mcp_neo4j_cypher")

# Backend modüllerini import etmek için path'i ayarla
_backend_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "src"
)
if _backend_path not in sys.path:
    sys.path.insert(0, _backend_path)

# Embedding ve normalization fonksiyonlarını import et
try:
    from shared.common_fn import load_embedding_model
    from utf8_utils import normalize_unicode_text
except ImportError:
    # Fallback: Eğer backend modülleri bulunamazsa, basit bir fallback kullan
    logger.warning("Backend modülleri bulunamadı, embedding fonksiyonları kullanılamayabilir")
    
    def load_embedding_model(model_name: str):
        """Fallback embedding model loader"""
        try:
            from langchain_openai import OpenAIEmbeddings
            return OpenAIEmbeddings(), 1536
        except ImportError:
            raise ImportError("OpenAI embeddings not available")
    
    def normalize_unicode_text(text: str) -> str:
        """Fallback normalize function"""
        return text.strip() if text else ""


def _create_direct_schema_format(nodes_result, rels_result):
    """
    Doğrudan Cypher sorgu sonuçlarından minimal şema formatı oluşturur
    """
    lines = []

    # Tip kısaltmaları (property tahmin için)
    type_mapping = {
        "createdAt": "dt",
        "updatedAt": "dt",
        "created_at": "dt",
        "updated_at": "dt",
        "amount": "float",
        "count": "int",
        "year": "int",
        "month": "int",
    }

    # Node'ları işle
    nodes_section = []
    if not nodes_result:
        logger.debug("_create_direct_schema_format: nodes_result empty or None")
        nodes_result = []

    for node_data in nodes_result:
        # Güvenlik: boş/None kayıtları atla
        if not node_data:
            logger.debug("_create_direct_schema_format: skipping empty node_data entry")
            continue

        node_name = node_data.get("nodeType") if isinstance(node_data, dict) else None
        node_count = node_data.get("nodeCount") if isinstance(node_data, dict) else None
        properties = (
            node_data.get("properties", []) if isinstance(node_data, dict) else []
        )

        # İlk 6 property'yi kısa tip bilgisiyle al
        props_with_types = []
        for prop_name in properties[:6]:
            # Basit tip tahmin
            if prop_name in type_mapping:
                prop_type = type_mapping[prop_name]
            elif "id" in prop_name.lower():
                prop_type = "str"
            elif "name" in prop_name.lower():
                prop_type = "str"
            elif "address" in prop_name.lower():
                prop_type = "str"
            elif "content" in prop_name.lower():
                prop_type = "str"
            else:
                prop_type = "str"  # default

            props_with_types.append(f"{prop_name}:{prop_type}")

        nodes_section.append(
            f"({node_name}:{node_count}){{{','.join(props_with_types)}}}"
        )

    # Relationship'leri işle
    relationships_section = []
    if not rels_result:
        logger.debug("_create_direct_schema_format: rels_result empty or None")
        rels_result = []

    for rel_data in rels_result:
        if not rel_data:
            logger.debug("_create_direct_schema_format: skipping empty rel_data entry")
            continue

        rel_name = (
            rel_data.get("relationshipType") if isinstance(rel_data, dict) else None
        )
        from_node = rel_data.get("from_node") if isinstance(rel_data, dict) else None
        to_node = rel_data.get("to_node") if isinstance(rel_data, dict) else None
        rel_props = rel_data.get("rel_props", []) if isinstance(rel_data, dict) else []

        # Relationship properties (varsa ilk 3'ü)
        rel_props_with_types = []
        for prop_name in rel_props[:3]:
            if prop_name in type_mapping:
                prop_type = type_mapping[prop_name]
            else:
                prop_type = "str"  # default
            rel_props_with_types.append(f"{prop_name}:{prop_type}")

        # Pattern oluştur
        if rel_props_with_types:
            pattern = f"({from_node})-[:{rel_name} {{{','.join(rel_props_with_types)}}}]->({to_node})"
        else:
            pattern = f"({from_node})-[:{rel_name}]->({to_node})"

        relationships_section.append(pattern)

    # Sonucu birleştir
    if nodes_section:
        lines.append("# NODES")
        lines.extend(nodes_section)

    if relationships_section:
        lines.append("")
        lines.append("# RELATIONSHIPS")
        lines.extend(relationships_section)

    return "\n".join(lines)


def _to_minimal_schema_format(schema_json):
    """
    Neo4j şemasını minimal formata çevirir - node'lar ve relationship pattern'lerini dahil eder
    """
    if isinstance(schema_json, str):
        schema = json.loads(schema_json)
    else:
        schema = schema_json

    lines = []

    # Tip kısaltmaları
    type_mapping = {
        "STRING": "str",
        "INTEGER": "int",
        "DATE_TIME": "dt",
        "LOCAL_DATE_TIME": "ldt",
        "BOOLEAN": "bool",
        "LIST": "list",
        "FLOAT": "float",
    }

    # Node'ları işle
    nodes_section = []
    unique_relationships = set()  # Duplicate'ları engellemek için
    relationship_stats = {}  # Relationship istatistikleri için

    for node_name, node_data in schema.items():
        if node_data.get("type") == "node":
            count = node_data.get("count", 0)
            # İlk 6 property'yi kısa tip bilgisiyle al
            properties = node_data.get("properties", {})
            props_with_types = []
            for prop_name, prop_info in list(properties.items())[:6]:
                prop_type = prop_info.get("type", "?")
                short_type = type_mapping.get(prop_type, prop_type.lower()[:3])
                props_with_types.append(f"{prop_name}:{short_type}")
            nodes_section.append(
                f"({node_name}:{count}){{{','.join(props_with_types)}}}"
            )

            # Relationship'leri işle - sadece OUT direction'ları al (duplicate'ları önler)
            relationships = node_data.get("relationships", {})
            for rel_name, rel_data in relationships.items():
                direction = rel_data.get("direction", "OUT")

                # Sadece OUT direction'ları işle, IN'leri atla (çünkü başka node'da OUT olarak zaten var)
                if direction != "OUT":
                    continue

                target_labels = rel_data.get("labels", [])

                # Relationship properties (varsa ilk 3'ü)
                rel_props = rel_data.get("properties", {})
                rel_props_with_types = []
                for prop_name, prop_info in list(rel_props.items())[:3]:
                    prop_type = prop_info.get("type", "?")
                    short_type = type_mapping.get(prop_type, prop_type.lower()[:3])
                    rel_props_with_types.append(f"{prop_name}:{short_type}")

                # Her target label için pattern oluştur
                for target_label in target_labels:
                    # APOC direction'ını doğrudan kullan (çevirme yok)
                    if direction == "OUT":  # Node'dan target'a giden relationship
                        if rel_props_with_types:
                            pattern = f"({node_name})-[:{rel_name} {{{','.join(rel_props_with_types)}}}]->({target_label})"
                        else:
                            pattern = f"({node_name})-[:{rel_name}]->({target_label})"
                    else:  # Target'dan node'a gelen relationship (IN)
                        if rel_props_with_types:
                            pattern = f"({target_label})-[:{rel_name} {{{','.join(rel_props_with_types)}}}]->({node_name})"
                        else:
                            pattern = f"({target_label})-[:{rel_name}]->({node_name})"

                    # Sadece OUT direction'lı pattern'leri ekle
                    unique_relationships.add(pattern)

                    # İstatistik topla
                    rel_key = f"{rel_name}"
                    if rel_key not in relationship_stats:
                        relationship_stats[rel_key] = set()
                    if direction == "OUT":  # Node'dan target'a
                        relationship_stats[rel_key].add(f"{node_name}->{target_label}")
                    else:  # Target'dan node'a
                        relationship_stats[rel_key].add(f"{target_label}->{node_name}")

    # Unique relationship'leri listeye çevir
    relationships_section = list(unique_relationships)

    # Önce node'lar
    if nodes_section:
        lines.append("# NODES")
        lines.extend(nodes_section)

    # Sonra relationship pattern'leri
    if relationships_section:
        lines.append("")
        lines.append("# RELATIONSHIPS")
        lines.extend(relationships_section)

    return "\n".join(lines)


def _handle_datetime_in_record(record):
    """
    Neo4j record'ındaki DateTime objelerini string'e çevirir
    """

    def _convert_datetime(obj):
        """Recursive olarak DateTime objelerini string'e çevirir"""
        if hasattr(obj, "year") and hasattr(obj, "month") and hasattr(obj, "day"):
            # Neo4j DateTime objesi
            if hasattr(obj, "hour"):  # DateTime
                return f"{obj.year}-{obj.month:02d}-{obj.day:02d}T{obj.hour:02d}:{obj.minute:02d}:{obj.second:02d}"
            else:  # Date
                return f"{obj.year}-{obj.month:02d}-{obj.day:02d}"
        elif isinstance(obj, dict):
            return {k: _convert_datetime(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [_convert_datetime(item) for item in obj]
        else:
            return obj

    return _convert_datetime(record)


def _to_minimal_data_format(data):
    """
    Cypher sorgu sonuçlarını minimal formata çevirir - simple_test.py'dan alınmıştır
    """
    if isinstance(data, str):
        data = json.loads(data)

    if not isinstance(data, list):
        return "Error: Veri list formatında olmalı"

    if not data:
        return "[]"

    # Her kaydı minimal hale çevir
    minimal_records = []
    for i, record in enumerate(data):
        # Key-value çiftlerini oluştur
        props = []
        for key, value in record.items():
            props.append(f"{key}:{value}")

        # (Record:index){key1:value1,key2:value2} formatında
        minimal_records.append(f"(R:{i}){{{','.join(props)}}}")

    return "\n".join(minimal_records)


def _format_namespace(namespace: str) -> str:
    if namespace:
        if namespace.endswith("-"):
            return namespace
        else:
            return namespace + "-"
    else:
        return ""


def _is_write_query(query: str) -> bool:
    """Check if the query is a write query."""
    return (
        re.search(r"\b(MERGE|CREATE|SET|DELETE|REMOVE|ADD)\b", query, re.IGNORECASE)
        is not None
    )


def create_mcp_server(
    neo4j_driver: AsyncDriver,
    database: str = "neo4j",
    namespace: str = "",
    read_timeout: int = 30,
    token_limit: Optional[int] = None,
    read_only: bool = False,
) -> FastMCP:
    mcp: FastMCP = FastMCP(
        "mcp-neo4j-cypher", dependencies=["neo4j", "pydantic"], stateless_http=True
    )

    namespace_prefix = _format_namespace(namespace)
    allow_writes = not read_only

    @mcp.tool(
        name=namespace_prefix + "get_neo4j_schema",
        annotations=ToolAnnotations(
            title="Get Neo4j Schema",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def get_neo4j_schema() -> list[ToolResult]:
        """
        List all nodes, their attributes and their relationships to other nodes in the neo4j database.
        Uses native Neo4j metadata functions instead of APOC.
        """

        # Node bilgilerini al
        get_nodes_query = """
        CALL db.labels() YIELD label
        WITH collect(label) as labels
        UNWIND labels as lbl
        CALL {
          WITH lbl
          MATCH (n) WHERE lbl IN labels(n)
          WITH count(n) as cnt, collect(properties(n))[0] as sample_props
          RETURN cnt, keys(sample_props) as props
        }
        RETURN lbl as nodeType, cnt as nodeCount, props as properties
        ORDER BY lbl
        """

        # Relationship pattern'lerini al
        get_rels_query = """
        CALL db.relationshipTypes() YIELD relationshipType
        CALL {
          WITH relationshipType
          MATCH (a)-[r]->(b) WHERE type(r) = relationshipType
          WITH labels(a)[0] as from_node, labels(b)[0] as to_node, 
               collect(properties(r))[0] as sample_props, count(*) as cnt
          ORDER BY cnt DESC
          LIMIT 1
          RETURN from_node, to_node, keys(sample_props) as rel_props
        }
        RETURN relationshipType, from_node, to_node, rel_props
        ORDER BY relationshipType
        """

        def clean_schema(schema: dict) -> dict:
            cleaned = {}

            for key, entry in schema.items():
                new_entry = {"type": entry["type"]}
                if "count" in entry:
                    new_entry["count"] = entry["count"]

                labels = entry.get("labels", [])
                if labels:
                    new_entry["labels"] = labels

                props = entry.get("properties", {})
                clean_props = {}
                for pname, pinfo in props.items():
                    cp = {}
                    if "indexed" in pinfo:
                        cp["indexed"] = pinfo["indexed"]
                    if "type" in pinfo:
                        cp["type"] = pinfo["type"]
                    if cp:
                        clean_props[pname] = cp
                if clean_props:
                    new_entry["properties"] = clean_props

                if entry.get("relationships"):
                    rels_out = {}
                    for rel_name, rel in entry["relationships"].items():
                        cr = {}
                        if "direction" in rel:
                            cr["direction"] = rel["direction"]
                        # nested labels
                        rlabels = rel.get("labels", [])
                        if rlabels:
                            cr["labels"] = rlabels
                        # nested properties
                        rprops = rel.get("properties", {})
                        clean_rprops = {}
                        for rpname, rpinfo in rprops.items():
                            crp = {}
                            if "indexed" in rpinfo:
                                crp["indexed"] = rpinfo["indexed"]
                            if "type" in rpinfo:
                                crp["type"] = rpinfo["type"]
                            if crp:
                                clean_rprops[rpname] = crp
                        if clean_rprops:
                            cr["properties"] = clean_rprops

                        if cr:
                            rels_out[rel_name] = cr

                    if rels_out:
                        new_entry["relationships"] = rels_out

                cleaned[key] = new_entry

            return cleaned

        try:
            # Node bilgilerini al
            nodes_result = await neo4j_driver.execute_query(
                get_nodes_query,
                routing_control=RoutingControl.READ,
                database_=database,
                result_transformer_=lambda r: r.data(),
            )

            # Relationship bilgilerini al
            rels_result = await neo4j_driver.execute_query(
                get_rels_query,
                routing_control=RoutingControl.READ,
                database_=database,
                result_transformer_=lambda r: r.data(),
            )

            # Validate results
            if nodes_result is None:
                logger.error(
                    "get_neo4j_schema: neo4j_driver.execute_query returned None for nodes_result"
                )
                raise ToolError("Neo4j driver returned no nodes result (None)")

            if rels_result is None:
                logger.error(
                    "get_neo4j_schema: neo4j_driver.execute_query returned None for rels_result"
                )
                raise ToolError("Neo4j driver returned no relationships result (None)")

            logger.debug(
                f"Found {len(nodes_result)} nodes and {len(rels_result)} relationship types"
            )

            # Yeni format'a çevir
            minimal_schema = _create_direct_schema_format(nodes_result, rels_result)

            return ToolResult(content=[TextContent(type="text", text=minimal_schema)])

        except ClientError as e:
            raise ToolError(f"Neo4j Client Error: {e}")

        except Neo4jError as e:
            raise ToolError(f"Neo4j Error: {e}")

        except Exception as e:
            logger.error(f"Error retrieving Neo4j database schema: {e}")
            raise ToolError(f"Unexpected Error: {e}")

    @mcp.tool(
        name=namespace_prefix + "read_neo4j_cypher",
        annotations=ToolAnnotations(
            title="Read Neo4j Cypher",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def read_neo4j_cypher(
        query: str = Field(..., description="The Cypher query to execute."),
        params: dict[str, Any] = Field(
            dict(), description="The parameters to pass to the Cypher query."
        ),
    ) -> list[ToolResult]:
        """Execute a read Cypher query on the neo4j database."""

        if _is_write_query(query):
            raise ValueError("Only MATCH queries are allowed for read-query")

        try:
            query_obj = Query(query, timeout=float(read_timeout))
            results = await neo4j_driver.execute_query(
                query_obj,
                parameters_=params,
                routing_control=RoutingControl.READ,
                database_=database,
                result_transformer_=lambda r: r.data(),
            )
            # Önce DateTime objelerini handle et, sonra sanitize et
            datetime_handled_results = [
                _handle_datetime_in_record(el) for el in results
            ]
            sanitized_results = [_value_sanitize(el) for el in datetime_handled_results]

            # Minimal format'a çevir
            minimal_results = _to_minimal_data_format(sanitized_results)

            if token_limit:
                minimal_results = _truncate_string_to_tokens(
                    minimal_results, token_limit
                )

            logger.debug(
                f"Read query returned {len(results)} rows, minimal format: {len(minimal_results)} chars"
            )

            return ToolResult(content=[TextContent(type="text", text=minimal_results)])

        except Neo4jError as e:
            logger.error(f"Neo4j Error executing read query: {e}\n{query}\n{params}")
            raise ToolError(f"Neo4j Error: {e}\n{query}\n{params}")

        except Exception as e:
            logger.error(f"Error executing read query: {e}\n{query}\n{params}")
            raise ToolError(f"Error: {e}\n{query}\n{params}")

    @mcp.tool(
        name=namespace_prefix + "read_neo4j_cypher_with_embedding",
        annotations=ToolAnnotations(
            title="Read Neo4j Cypher with Embedding",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def read_neo4j_cypher_with_embedding(
        query_text: str = Field(
            ...,
            description=(
                "The text query for semantic search. This will be converted to an embedding vector. "
                "Use ONLY content keywords (e.g., 'payment plan', 'policy details', 'coverage information'), "
                "NOT metadata like customer names, dates, or specific codes."
            ),
        ),
        cypher_query: str = Field(
            ...,
            description=(
                "The Cypher query to execute. MUST include $embedding_vector parameter in the query. "
                "Example: 'MATCH (c:Chunk) WHERE gds.similarity.cosine(c.embedding, $embedding_vector) > 0.8 "
                "RETURN c.text, gds.similarity.cosine(c.embedding, $embedding_vector) as score ORDER BY score DESC'"
            ),
        ),
        params: dict[str, Any] = Field(
            dict(),
            description=(
                "Additional parameters to pass to the Cypher query (optional). "
                "Note: $embedding_vector will be automatically added to params."
            ),
        ),
    ) -> list[ToolResult]:
        """
        Execute a read Cypher query with automatic embedding generation for semantic search.
        
        This tool is designed for semantic search scenarios where you need to:
        1. Generate an embedding from a text query
        2. Use that embedding in a Cypher query for vector similarity search
        
        Workflow:
        - You provide a text query (e.g., "payment plan details")
        - You provide a Cypher query that uses $embedding_vector parameter
        - This tool automatically:
          * Generates embedding from the text query
          * Binds the embedding to $embedding_vector parameter
          * Executes the Cypher query
          * Returns the results
        
        Example usage:
        - query_text: "taksit ödeme planı"
        - cypher_query: "MATCH (c:Chunk) WHERE gds.similarity.cosine(c.embedding, $embedding_vector) > 0.8 RETURN c.text, gds.similarity.cosine(c.embedding, $embedding_vector) as score ORDER BY score DESC LIMIT 10"
        - params: {} (optional additional parameters)
        """

        # Validate that cypher_query contains $embedding_vector parameter
        if "$embedding_vector" not in cypher_query:
            raise ToolError(
                "Cypher query must include $embedding_vector parameter. "
                "Example: 'MATCH (c:Chunk) WHERE gds.similarity.cosine(c.embedding, $embedding_vector) > 0.8 RETURN c'"
            )

        # Validate that query is not a write query
        if _is_write_query(cypher_query):
            raise ToolError("Only MATCH queries are allowed for read-query with embedding")

        try:
            # Step 1: Load embedding model
            logger.info(f"🧠 Embedding model yükleniyor...")
            embedding_model_name = os.getenv("EMBEDDING_MODEL", "openai")
            embedding_model, embedding_dimension = load_embedding_model(embedding_model_name)
            logger.info(f"✅ Embedding model yüklendi: {embedding_model_name} (dimension: {embedding_dimension})")

            # Step 2: Normalize query text
            normalized_text = normalize_unicode_text(query_text)
            logger.info(f"🧹 Normalize edilmiş text: {normalized_text[:100]}...")

            # Step 3: Generate embedding
            logger.info(f"🔢 Embedding oluşturuluyor...")
            embedding_vector = embedding_model.embed_query(normalized_text)
            logger.info(f"✅ {len(embedding_vector)} boyutlu embedding oluşturuldu")

            # Step 4: Add embedding to params
            params_with_embedding = params.copy()
            params_with_embedding["embedding_vector"] = embedding_vector

            # Step 5: Execute Cypher query
            logger.info(f"🔍 Cypher sorgusu çalıştırılıyor...")
            query_obj = Query(cypher_query, timeout=float(read_timeout))
            results = await neo4j_driver.execute_query(
                query_obj,
                parameters_=params_with_embedding,
                routing_control=RoutingControl.READ,
                database_=database,
                result_transformer_=lambda r: r.data(),
            )

            # Step 6: Process results (same as read_neo4j_cypher)
            datetime_handled_results = [
                _handle_datetime_in_record(el) for el in results
            ]
            sanitized_results = [_value_sanitize(el) for el in datetime_handled_results]

            # Minimal format'a çevir
            minimal_results = _to_minimal_data_format(sanitized_results)

            if token_limit:
                minimal_results = _truncate_string_to_tokens(
                    minimal_results, token_limit
                )

            logger.info(
                f"✅ Semantic arama tamamlandı: {len(results)} sonuç bulundu, "
                f"minimal format: {len(minimal_results)} karakter"
            )

            return ToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=f"Semantic search completed. Found {len(results)} results.\n\n{minimal_results}",
                    )
                ]
            )

        except ImportError as e:
            error_msg = f"Embedding model import hatası: {e}. Lütfen backend modüllerinin doğru yüklendiğinden emin olun."
            logger.error(error_msg)
            raise ToolError(error_msg)

        except Neo4jError as e:
            logger.error(
                f"Neo4j Error executing semantic search query: {e}\n"
                f"Query text: {query_text}\nCypher query: {cypher_query}\nParams: {params}"
            )
            raise ToolError(
                f"Neo4j Error: {e}\nQuery text: {query_text}\nCypher query: {cypher_query}"
            )

        except Exception as e:
            error_msg = f"Embedding oluşturma veya sorgu çalıştırma hatası: {e}"
            logger.error(
                f"{error_msg}\nQuery text: {query_text}\nCypher query: {cypher_query}\nParams: {params}"
            )
            raise ToolError(f"{error_msg}\nQuery text: {query_text}\nCypher query: {cypher_query}")

    @mcp.tool(
        name=namespace_prefix + "write_neo4j_cypher",
        annotations=ToolAnnotations(
            title="Write Neo4j Cypher",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=True,
        ),
        enabled=allow_writes,
    )
    async def write_neo4j_cypher(
        query: str = Field(..., description="The Cypher query to execute."),
        params: dict[str, Any] = Field(
            dict(), description="The parameters to pass to the Cypher query."
        ),
    ) -> list[ToolResult]:
        """Execute a write Cypher query on the neo4j database."""

        if not _is_write_query(query):
            raise ValueError("Only write queries are allowed for write-query")

        try:
            _, summary, _ = await neo4j_driver.execute_query(
                query,
                parameters_=params,
                routing_control=RoutingControl.WRITE,
                database_=database,
            )

            counters_json_str = json.dumps(summary.counters.__dict__, default=str)

            logger.debug(f"Write query affected {counters_json_str}")

            return ToolResult(
                content=[TextContent(type="text", text=counters_json_str)]
            )

        except Neo4jError as e:
            logger.error(f"Neo4j Error executing write query: {e}\n{query}\n{params}")
            raise ToolError(f"Neo4j Error: {e}\n{query}\n{params}")

        except Exception as e:
            logger.error(f"Error executing write query: {e}\n{query}\n{params}")
            raise ToolError(f"Error: {e}\n{query}\n{params}")

    return mcp


async def main(
    db_url: str,
    username: str,
    password: str,
    database: str,
    transport: Literal["stdio", "sse", "http"] = "stdio",
    namespace: str = "",
    host: str = "127.0.0.1",
    port: int = 8000,
    path: str = "/mcp/",
    allow_origins: list[str] = [],
    allowed_hosts: list[str] = [],
    read_timeout: int = 30,
    token_limit: Optional[int] = None,
    read_only: bool = False,
) -> None:
    logger.info("Starting MCP neo4j Server")

    neo4j_driver = AsyncGraphDatabase.driver(
        db_url,
        auth=(
            username,
            password,
        ),
    )
    custom_middleware = [
        Middleware(
            CORSMiddleware,
            allow_origins=allow_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["*"],
        ),
        Middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts),
    ]

    mcp = create_mcp_server(
        neo4j_driver, database, namespace, read_timeout, token_limit, read_only
    )

    # Run the server with the specified transport
    match transport:
        case "http":
            logger.info(
                f"Running Neo4j Cypher MCP Server with HTTP transport on {host}:{port}..."
            )
            await mcp.run_http_async(
                host=host, port=port, path=path, middleware=custom_middleware
            )
        case "stdio":
            logger.info("Running Neo4j Cypher MCP Server with stdio transport...")
            await mcp.run_stdio_async()
        case "sse":
            logger.info(
                f"Running Neo4j Cypher MCP Server with SSE transport on {host}:{port}..."
            )
            await mcp.run_http_async(
                host=host,
                port=port,
                path=path,
                middleware=custom_middleware,
                transport="sse",
            )
        case _:
            logger.error(
                f"Invalid transport: {transport} | Must be either 'stdio', 'sse', or 'http'"
            )
            raise ValueError(
                f"Invalid transport: {transport} | Must be either 'stdio', 'sse', or 'http'"
            )


if __name__ == "__main__":
    main()
