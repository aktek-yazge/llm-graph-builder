import json
import logging
import os
import re
import unicodedata
from typing import Any, Literal, Optional

from dotenv import load_dotenv
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

# Load environment variables from backend/.env file
# server.py -> mcp_neo4j_cypher -> src -> mcp-neo4j-cypher -> mcp-servers -> backend
_backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
_env_path = os.path.join(_backend_dir, ".env")
# Önce backend/.env dosyasını yükle, sonra mevcut dizindeki .env dosyasını yükle
load_dotenv(_env_path)
load_dotenv()  # Mevcut dizindeki .env dosyasını da yükle (override etmez, sadece eksik değişkenleri ekler)

# Configure logging to stdout
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [MCP] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("mcp_neo4j_cypher")
logger.setLevel(logging.INFO)


def normalize_unicode_text(text: str) -> str:
    """
    Unicode text normalization for consistent UTF-8 storage
    
    Args:
        text (str): Input text to normalize
        
    Returns:
        str: Normalized UTF-8 text
    """
    if not text or not isinstance(text, str):
        return text
    
    try:
        # NFC normalization - Canonical Decomposition followed by Canonical Composition
        normalized = unicodedata.normalize('NFC', text)
        
        # UTF-8 encoding'i güvence altına al
        normalized = normalized.encode('utf-8', errors='replace').decode('utf-8')
        
        # Additional cleaning
        normalized = normalized.strip()
        
        return normalized
    except Exception as e:
        logger.warning(f"Text normalization hatası: {e}")
        return text


def load_embedding_model(embedding_model_name: str):
    """
    Embedding model yükleme fonksiyonu
    OpenAI, VertexAI, Titan ve HuggingFace modellerini destekler
    """
    if embedding_model_name == "openai":
        try:
            from langchain_openai import OpenAIEmbeddings
            api_key = os.getenv("OPENAI_API_KEY")
            if api_key:
                embeddings = OpenAIEmbeddings(api_key=api_key)
            else:
                embeddings = OpenAIEmbeddings()
            dimension = 1536
            logger.info(f"Embedding: Using OpenAI Embeddings, Dimension:{dimension}")
            return embeddings, dimension
        except ImportError:
            raise ImportError("OpenAI embeddings not available. Install langchain-openai")
    
    elif embedding_model_name == "vertexai":
        try:
            from langchain_google_vertexai import VertexAIEmbeddings
            embeddings = VertexAIEmbeddings(model="textembedding-gecko@003")
            dimension = 768
            logger.info(f"Embedding: Using Vertex AI Embeddings, Dimension:{dimension}")
            return embeddings, dimension
        except ImportError:
            raise ImportError("VertexAI embeddings not available. Install langchain-google-vertexai")
    
    elif embedding_model_name == "titan":
        try:
            from langchain_community.embeddings import BedrockEmbeddings
            env_value = os.getenv("BEDROCK_EMBEDDING_MODEL")
            if not env_value:
                raise ValueError("Environment variable 'BEDROCK_EMBEDDING_MODEL' is not set.")
            try:
                model_name, aws_access_key, aws_secret_key, region_name = env_value.split(",")
            except ValueError:
                raise ValueError("BEDROCK_EMBEDDING_MODEL format: model_name,aws_access_key,aws_secret_key,region_name")
            
            embeddings = BedrockEmbeddings(
                model_id=model_name.strip(),
                credentials_profile_name=None,
                region_name=region_name.strip(),
                aws_access_key_id=aws_access_key.strip(),
                aws_secret_access_key=aws_secret_key.strip(),
            )
            dimension = 1536
            logger.info(f"Embedding: Using bedrock titan Embeddings, Dimension:{dimension}")
            return embeddings, dimension
        except ImportError:
            raise ImportError("Bedrock embeddings not available. Install langchain-community and boto3")
    
    else:
        # HuggingFace model için
        try:
            from langchain_huggingface import HuggingFaceEmbeddings
            from pathlib import Path
            
            cache_folder = os.getenv(
                "HUGGINGFACE_CACHE_FOLDER",
                os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "models")
            )
            Path(cache_folder).mkdir(parents=True, exist_ok=True)
            hf_model_name = os.getenv("HUGGINGFACE_MODEL_NAME", "BAAI/bge-m3")
            
            logger.info(f"📦 HuggingFace model cache klasörü: {cache_folder}")
            logger.info(f"🤖 HuggingFace model adı: {hf_model_name}")
            
            embeddings = HuggingFaceEmbeddings(
                model_name=hf_model_name,
                cache_folder=cache_folder,
                model_kwargs={"cache_dir": cache_folder},
                encode_kwargs={"normalize_embeddings": True},
            )
            dimension = 384
            logger.info(f"✅ Embedding: Using Langchain HuggingFaceEmbeddings (cached locally), Dimension:{dimension}")
            return embeddings, dimension
        except ImportError:
            raise ImportError("HuggingFace embeddings not available. Install langchain-huggingface")


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
        "mcp-neo4j-cypher", dependencies=["neo4j", "pydantic"]
    )

    namespace_prefix = _format_namespace(namespace)
    allow_writes = not read_only

    # =====================================================================
    # get_neo4j_schema TOOL DISABLED
    # Şema bilgisi artık prompt'a önceden ekleniyor (GlobalSchemaCache)
    # Bu tool gereksiz DB çağrısı yapıyor, performans için devre dışı bırakıldı
    # =====================================================================
    # @mcp.tool(
    #     name=namespace_prefix + "get_neo4j_schema",
    #     annotations=ToolAnnotations(
    #         title="Get Neo4j Schema",
    #         readOnlyHint=True,
    #         destructiveHint=False,
    #         idempotentHint=True,
    #         openWorldHint=True,
    #     ),
    # )
    # async def get_neo4j_schema() -> list[ToolResult]:
    #     """
    #     DISABLED: Schema is now pre-loaded into prompt via GlobalSchemaCache
    #     """
    #     pass

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
        """
        Execute a read Cypher query on the neo4j database.
        
        USE THIS TOOL FOR:
        - METADATA queries: names, numbers, dates, counts, IDs
        - Questions like: "Who?", "How many?", "Which date?", "What number?"
        - Finding entities and their properties from graph nodes
        
        DO NOT USE FOR:
        - Detail/content questions: "What does it say?", "What are the details?"
        - List/table requests: "List all X", "What are the Y?" (plural)
        - Document content search
        
        For content/detail queries, use read_neo4j_cypher_with_embedding instead.
        """
        
        # 📊 Tool Call Logging
        logger.info(f"")
        logger.info(f"{'🔷'*20}")
        logger.info(f"🔍 CYPHER READ QUERY")
        logger.info(f"{'🔷'*20}")
        logger.info(f"📝 {query}")
        logger.info(f"📦 Params: {params}")
        logger.info(f"{'🔷'*20}")

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

            # 📊 Result Logging
            logger.info(f"✅ RESULT: {len(results)} rows returned")
            logger.info(f"📊 Data: {minimal_results}")
            logger.info(f"{'🔷'*20}")

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
        Execute a semantic search in document content (Chunks) using embeddings.
        
        ⚠️ USE THIS TOOL WHEN:
        - Question asks for DETAILS, LISTS, TABLES, or EXPLANATIONS
        - Question contains: "neler?", "listele", "detayları", "ne diyor?", "var mı?"
        - Question asks about: installments, payments, coverage details, terms, conditions
        - Graph query returned FEW results (≤3) but more detail is expected
        - Information is NOT in graph nodes but IN DOCUMENT CONTENT
        
        WORKFLOW:
        1. First use read_neo4j_cypher to find the relevant entity (Policy, Document, etc.)
        2. Then use THIS tool to search within that entity's Chunks for detailed content
        
        PARAMETERS:
        - query_text: The CONCEPT you're searching for (e.g., "payment installment plan")
          DO NOT include metadata (names, dates, IDs) in query_text!
        - cypher_query: Must filter to specific entity's Chunks and use $embedding_vector
        
        Example usage:
        - query_text: "taksit ödeme planı"
        - cypher_query: "MATCH (c:Chunk) WHERE gds.similarity.cosine(c.embedding, $embedding_vector) > 0.8 RETURN c.text, gds.similarity.cosine(c.embedding, $embedding_vector) as score ORDER BY score DESC LIMIT 10"
        - params: {} (optional additional parameters)
        """
        
        # 📊 Tool Call Logging
        logger.info(f"")
        logger.info(f"{'🟣'*20}")
        logger.info(f"🧠 SEMANTIC SEARCH (Embedding)")
        logger.info(f"{'🟣'*20}")
        logger.info(f"🔤 Text: {query_text}")
        logger.info(f"📝 Cypher: {cypher_query}")
        logger.info(f"📦 Params: {params}")
        logger.info(f"{'🟣'*20}")

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

            # 📊 Result Logging
            logger.info(f"✅ RESULT: {len(results)} rows returned")
            logger.info(f"📊 Data: {minimal_results}")
            logger.info(f"{'🟣'*20}")

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
        
        # 📊 Tool Call Logging
        logger.info(f"")
        logger.info(f"{'🔴'*20}")
        logger.info(f"✏️ CYPHER WRITE QUERY")
        logger.info(f"{'🔴'*20}")
        logger.info(f"📝 {query}")
        logger.info(f"📦 Params: {params}")
        logger.info(f"{'🔴'*20}")

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

            # 📊 Result Logging
            logger.info(f"✅ RESULT: Write completed")
            logger.info(f"📊 Counters: {counters_json_str}")
            logger.info(f"{'🔴'*20}")

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
        # Connection pool ayarları - tek soru için bağlantıları yeniden kullan
        max_connection_pool_size=50,  # Maksimum bağlantı sayısı
        connection_acquisition_timeout=60,  # Bağlantı alma timeout (saniye)
        max_connection_lifetime=3600,  # Bağlantı ömrü (saniye)
    )
    logger.info("✅ Neo4j connection pool oluşturuldu (max: 50 bağlantı)")
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
                host=host, port=port, path=path, middleware=custom_middleware, stateless_http=True
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
                stateless_http=True,
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
