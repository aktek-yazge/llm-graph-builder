import json
import logging
import re
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


def _to_minimal_schema_format(schema_json):
    """
    Neo4j şemasını minimal formata çevirir - simple_test.py'dan alınmıştır
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
        "FLOAT": "float"
    }
    
    # Tüm node'ları işle - şemadan otomatik çıkar
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
            lines.append(f"({node_name}:{count}){{{','.join(props_with_types)}}}")
    
    return '\n'.join(lines)


def _handle_datetime_in_record(record):
    """
    Neo4j record'ındaki DateTime objelerini string'e çevirir
    """
    def _convert_datetime(obj):
        """Recursive olarak DateTime objelerini string'e çevirir"""
        if hasattr(obj, 'year') and hasattr(obj, 'month') and hasattr(obj, 'day'):
            # Neo4j DateTime objesi
            if hasattr(obj, 'hour'):  # DateTime
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
    
    return '\n'.join(minimal_records)


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
        This requires that the APOC plugin is installed and enabled.
        """

        get_schema_query = """
        CALL apoc.meta.schema();
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
            results_json_str = await neo4j_driver.execute_query(
                get_schema_query,
                routing_control=RoutingControl.READ,
                database_=database,
                result_transformer_=lambda r: r.data(),
            )

            logger.debug(f"Read query returned {len(results_json_str)} rows")

            schema_clean = clean_schema(results_json_str[0].get("value"))

            # Minimal format'a çevir
            minimal_schema = _to_minimal_schema_format(schema_clean)

            return ToolResult(content=[TextContent(type="text", text=minimal_schema)])

        except ClientError as e:
            if "Neo.ClientError.Procedure.ProcedureNotFound" in str(e):
                raise ToolError(
                    "Neo4j Client Error: This instance of Neo4j does not have the APOC plugin installed. Please install and enable the APOC plugin to use the `get_neo4j_schema` tool."
                )
            else:
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
            datetime_handled_results = [_handle_datetime_in_record(el) for el in results]
            sanitized_results = [_value_sanitize(el) for el in datetime_handled_results]
            
            # Minimal format'a çevir
            minimal_results = _to_minimal_data_format(sanitized_results)
            
            if token_limit:
                minimal_results = _truncate_string_to_tokens(
                    minimal_results, token_limit
                )

            logger.debug(f"Read query returned {len(results)} rows, minimal format: {len(minimal_results)} chars")

            return ToolResult(content=[TextContent(type="text", text=minimal_results)])

        except Neo4jError as e:
            logger.error(f"Neo4j Error executing read query: {e}\n{query}\n{params}")
            raise ToolError(f"Neo4j Error: {e}\n{query}\n{params}")

        except Exception as e:
            logger.error(f"Error executing read query: {e}\n{query}\n{params}")
            raise ToolError(f"Error: {e}\n{query}\n{params}")

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
