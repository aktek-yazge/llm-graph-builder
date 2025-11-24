from langchain_neo4j import Neo4jGraph
from src.shared.constants import (
    BUCKET_UPLOAD,
    BUCKET_FAILED_FILE,
    PROJECT_ID,
    QUERY_TO_GET_CHUNKS,
    QUERY_TO_DELETE_EXISTING_ENTITIES,
    QUERY_TO_GET_LAST_PROCESSED_CHUNK_POSITION,
    QUERY_TO_GET_LAST_PROCESSED_CHUNK_WITHOUT_ENTITY,
    START_FROM_BEGINNING,
    START_FROM_LAST_PROCESSED_POSITION,
    DELETE_ENTITIES_AND_START_FROM_BEGINNING,
    QUERY_TO_GET_NODES_AND_RELATIONS_OF_A_DOCUMENT,
)
from src.shared.schema_extraction import schema_extraction_from_text
from dotenv import load_dotenv
from datetime import datetime
import logging
import os
import time
import asyncio

# OpenTelemetry logging setup - mevcut kodda değişiklik yapmadan tüm logları Loki'ye gönder
# OpenTelemetry ve Loki entegrasyonu şimdilik comment yapıldı
# try:
#     from src.otel_logging_setup import initialize_otel_logging
#     # OpenTelemetry'i başlat (environment variable'lar ile yapılandırılır)
#     initialize_otel_logging()
#     logging.info("🔧 OpenTelemetry logging aktif - tüm loglar Loki'ye gönderiliyor")
# except Exception as otel_error:
#     logging.warning(f"⚠️ OpenTelemetry başlatılamadı: {otel_error} - Normal logging devam ediyor")
from src.create_chunks import CreateChunksofDocument
from src.graphDB_dataAccess import graphDBdataAccess
from src.document_sources.local_file import get_documents_from_file_by_path, generate_page_images_with_pymupdf
from src.entities.source_node import sourceNode
from src.llm import get_graph_from_llm
from src.document_sources.gcs_bucket import *
from src.document_sources.s3_bucket import *
from src.document_sources.s3_upload_utils import *
from src.document_sources.wikipedia import *
from src.document_sources.youtube import *
from src.shared.common_fn import *
from src.make_relationships import *
from src.document_sources.web_pages import *
from src.graph_query import get_graphDB_driver
from src.utf8_utils import normalize_unicode_text, normalize_file_name, ensure_utf8_encoding
import re
from langchain_community.document_loaders import WikipediaLoader, WebBaseLoader
import warnings
import sys
import shutil
import urllib.parse

# Logger helper fonksiyonlarını utils'den import et
from src.utils.log_helpers import (
    log_upload, 
    log_delete, 
    log_chunking, 
    log_extraction, 
    log_processing
)
import json
from src.shared.llm_graph_builder_exception import LLMGraphBuilderException
import markdown_to_json

import pandas as pd
import re
from io import StringIO
import time

warnings.filterwarnings("ignore")
load_dotenv()
logging.basicConfig(format="%(asctime)s - %(message)s", level="INFO")

# Neo4j notification ve deprecation warning'lerini kapat
logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)
logging.getLogger("neo4j").setLevel(logging.WARNING)

# Daha agresif filtreleme
neo4j_logger = logging.getLogger("neo4j")
def filter_neo4j_notifications(record):
    message = record.getMessage().lower()
    # Bu mesajları filtrele
    filtered_keywords = [
        "deprecation", "deprecated", "unknown label", "call subquery", 
        "variable scope clause", "notification", "severity", "category"
    ]
    return not any(keyword in message for keyword in filtered_keywords)

neo4j_logger.addFilter(filter_neo4j_notifications)

# Root logger'a da aynı filtreyi ekle
root_logger = logging.getLogger()
root_logger.addFilter(filter_neo4j_notifications)


def create_source_node_graph_url_s3(
    graph, model, source_url, aws_access_key_id, aws_secret_access_key, source_type
):

    lst_file_name = []
    files_info = get_s3_files_info(
        source_url,
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
    )
    if len(files_info) == 0:
        raise LLMGraphBuilderException("No pdf files found.")
    logging.info(f"files info : {files_info}")
    success_count = 0
    failed_count = 0

    for file_info in files_info:
        file_name = file_info["file_key"]
        obj_source_node = sourceNode()
        # Extract and normalize the filename
        raw_filename = (
            file_name.split("/")[-1].strip()
            if isinstance(file_name.split("/")[-1], str)
            else file_name.split("/")[-1]
        )
        obj_source_node.file_name = normalize_file_name(raw_filename)
        obj_source_node.file_type = "pdf"
        obj_source_node.file_size = file_info["file_size_bytes"]
        obj_source_node.file_source = source_type
        obj_source_node.model = model
        obj_source_node.url = str(source_url + file_name)
        obj_source_node.awsAccessKeyId = aws_access_key_id
        obj_source_node.created_at = datetime.now()
        obj_source_node.chunkNodeCount = 0
        obj_source_node.chunkRelCount = 0
        obj_source_node.entityNodeCount = 0
        obj_source_node.entityEntityRelCount = 0
        obj_source_node.communityNodeCount = 0
        obj_source_node.communityRelCount = 0
        try:
            graphDb_data_Access = graphDBdataAccess(graph)
            graphDb_data_Access.create_source_node(obj_source_node, model=model)
            success_count += 1
            lst_file_name.append(
                {
                    "fileName": obj_source_node.file_name,
                    "fileSize": obj_source_node.file_size,
                    "url": obj_source_node.url,
                    "status": "Success",
                }
            )

        except Exception as e:
            failed_count += 1
            lst_file_name.append(
                {
                    "fileName": obj_source_node.file_name,
                    "fileSize": obj_source_node.file_size,
                    "url": obj_source_node.url,
                    "status": "Failed",
                }
            )
    return lst_file_name, success_count, failed_count


def create_source_node_graph_url_gcs(
    graph,
    model,
    gcs_project_id,
    gcs_bucket_name,
    gcs_bucket_folder,
    source_type,
    credentials,
):

    success_count = 0
    failed_count = 0
    lst_file_name = []

    lst_file_metadata = get_gcs_bucket_files_info(
        gcs_project_id, gcs_bucket_name, gcs_bucket_folder, credentials
    )
    for file_metadata in lst_file_metadata:
        obj_source_node = sourceNode()
        # Normalize the filename from GCS metadata
        raw_filename = (
            file_metadata["fileName"].strip()
            if isinstance(file_metadata["fileName"], str)
            else file_metadata["fileName"]
        )
        obj_source_node.file_name = normalize_file_name(raw_filename)
        obj_source_node.file_size = file_metadata["fileSize"]
        obj_source_node.url = file_metadata["url"]
        obj_source_node.file_source = source_type
        obj_source_node.model = model
        obj_source_node.file_type = "pdf"
        obj_source_node.gcsBucket = gcs_bucket_name
        obj_source_node.gcsBucketFolder = file_metadata["gcsBucketFolder"]
        obj_source_node.gcsProjectId = file_metadata["gcsProjectId"]
        obj_source_node.created_at = datetime.now()
        obj_source_node.access_token = credentials.token
        obj_source_node.chunkNodeCount = 0
        obj_source_node.chunkRelCount = 0
        obj_source_node.entityNodeCount = 0
        obj_source_node.entityEntityRelCount = 0
        obj_source_node.communityNodeCount = 0
        obj_source_node.communityRelCount = 0

        try:
            graphDb_data_Access = graphDBdataAccess(graph)
            graphDb_data_Access.create_source_node(obj_source_node, model=model)
            success_count += 1
            lst_file_name.append(
                {
                    "fileName": obj_source_node.file_name,
                    "fileSize": obj_source_node.file_size,
                    "url": obj_source_node.url,
                    "status": "Success",
                    "gcsBucketName": gcs_bucket_name,
                    "gcsBucketFolder": obj_source_node.gcsBucketFolder,
                    "gcsProjectId": obj_source_node.gcsProjectId,
                }
            )
        except Exception as e:
            failed_count += 1
            lst_file_name.append(
                {
                    "fileName": obj_source_node.file_name,
                    "fileSize": obj_source_node.file_size,
                    "url": obj_source_node.url,
                    "status": "Failed",
                    "gcsBucketName": gcs_bucket_name,
                    "gcsBucketFolder": obj_source_node.gcsBucketFolder,
                    "gcsProjectId": obj_source_node.gcsProjectId,
                }
            )
    return lst_file_name, success_count, failed_count


def create_source_node_graph_web_url(graph, model, source_url, source_type):
    success_count = 0
    failed_count = 0
    lst_file_name = []
    pages = WebBaseLoader(source_url, verify_ssl=False).load()
    if pages == None or len(pages) == 0:
        failed_count += 1
        message = f"Unable to read data for given url : {source_url}"
        raise LLMGraphBuilderException(message)
    try:
        title = pages[0].metadata["title"].strip()
        if title:
            graphDb_data_Access = graphDBdataAccess(graph)
            existing_url = graphDb_data_Access.get_websource_url(title)
            if existing_url != source_url:
                title = str(title) + "-" + str(last_url_segment(source_url)).strip()
        else:
            title = last_url_segment(source_url)
        language = pages[0].metadata["language"]
    except:
        title = last_url_segment(source_url)
        language = "N/A"

    obj_source_node = sourceNode()
    obj_source_node.file_type = "text"
    obj_source_node.file_source = source_type
    obj_source_node.model = model
    obj_source_node.url = urllib.parse.unquote(source_url)
    obj_source_node.created_at = datetime.now()
    obj_source_node.file_name = normalize_file_name(title.strip() if isinstance(title, str) else title)
    obj_source_node.language = language
    obj_source_node.file_size = sys.getsizeof(pages[0].page_content)
    obj_source_node.chunkNodeCount = 0
    obj_source_node.chunkRelCount = 0
    obj_source_node.entityNodeCount = 0
    obj_source_node.entityEntityRelCount = 0
    obj_source_node.communityNodeCount = 0
    obj_source_node.communityRelCount = 0
    graphDb_data_Access = graphDBdataAccess(graph)
    graphDb_data_Access.create_source_node(obj_source_node, model=model)
    lst_file_name.append(
        {
            "fileName": obj_source_node.file_name,
            "fileSize": obj_source_node.file_size,
            "url": obj_source_node.url,
            "status": "Success",
        }
    )
    success_count += 1
    return lst_file_name, success_count, failed_count


def create_source_node_graph_url_youtube(graph, model, source_url, source_type):

    youtube_url, language = check_url_source(source_type=source_type, yt_url=source_url)
    success_count = 0
    failed_count = 0
    lst_file_name = []
    obj_source_node = sourceNode()
    obj_source_node.file_type = "text"
    obj_source_node.file_source = source_type
    obj_source_node.model = model
    obj_source_node.url = youtube_url
    obj_source_node.created_at = datetime.now()
    obj_source_node.chunkNodeCount = 0
    obj_source_node.chunkRelCount = 0
    obj_source_node.entityNodeCount = 0
    obj_source_node.entityEntityRelCount = 0
    obj_source_node.communityNodeCount = 0
    obj_source_node.communityRelCount = 0
    match = re.search(r"(?:v=)([0-9A-Za-z_-]{11})\s*", obj_source_node.url)
    logging.info(f"match value: {match}")
    obj_source_node.file_name = normalize_file_name(match.group(1))
    transcript = get_youtube_combined_transcript(match.group(1))
    logging.info(f"Youtube transcript : {transcript}")
    if transcript == None or len(transcript) == 0:
        message = (
            f"Youtube transcript is not available for : {obj_source_node.file_name}"
        )
        raise LLMGraphBuilderException(message)
    else:
        obj_source_node.file_size = sys.getsizeof(transcript)

    graphDb_data_Access = graphDBdataAccess(graph)
    graphDb_data_Access.create_source_node(obj_source_node, model=model)
    lst_file_name.append(
        {
            "fileName": obj_source_node.file_name,
            "fileSize": obj_source_node.file_size,
            "url": obj_source_node.url,
            "status": "Success",
        }
    )
    success_count += 1
    return lst_file_name, success_count, failed_count


def create_source_node_graph_url_wikipedia(graph, model, wiki_query, source_type):

    success_count = 0
    failed_count = 0
    lst_file_name = []
    wiki_query_id, language = check_url_source(
        source_type=source_type, wiki_query=wiki_query
    )
    logging.info(f"Creating source node for {wiki_query_id.strip()}, {language}")
    pages = WikipediaLoader(
        query=wiki_query_id.strip(),
        lang=language,
        load_max_docs=1,
        load_all_available_meta=True,
    ).load()
    if pages == None or len(pages) == 0:
        failed_count += 1
        message = f"Unable to read data for given Wikipedia url : {wiki_query}"
        raise LLMGraphBuilderException(message)
    else:
        obj_source_node = sourceNode()
        obj_source_node.file_name = normalize_file_name(wiki_query_id.strip())
        obj_source_node.file_type = "text"
        obj_source_node.file_source = source_type
        obj_source_node.file_size = sys.getsizeof(pages[0].page_content)
        obj_source_node.model = model
        obj_source_node.url = urllib.parse.unquote(pages[0].metadata["source"])
        obj_source_node.created_at = datetime.now()
        obj_source_node.language = language
        obj_source_node.chunkNodeCount = 0
        obj_source_node.chunkRelCount = 0
        obj_source_node.entityNodeCount = 0
        obj_source_node.entityEntityRelCount = 0
        obj_source_node.communityNodeCount = 0
        obj_source_node.communityRelCount = 0
        graphDb_data_Access = graphDBdataAccess(graph)
        graphDb_data_Access.create_source_node(obj_source_node, model=model)
        success_count += 1
        lst_file_name.append(
            {
                "fileName": obj_source_node.file_name,
                "fileSize": obj_source_node.file_size,
                "url": obj_source_node.url,
                "language": obj_source_node.language,
                "status": "Success",
            }
        )
    return lst_file_name, success_count, failed_count


async def extract_graph_from_file_local_file(
    uri,
    userName,
    password,
    database,
    model,
    merged_file_path,
    fileName,
    allowedNodes,
    allowedRelationship,
    token_chunk_size,
    chunk_overlap,
    chunks_to_combine,
    retry_condition,
    additional_instructions,
    # Post-processing parametreleri
    enable_post_processing=False,
    post_processing_rules=None,
    max_pages=None,  # Sayfa sınırlandırma parametresi
):

    log_extraction(f"Process file name: {fileName}")
    if not retry_condition:
        # Extract işlemi artık sadece mevcut chunk'larla çalışır
        # Pages'leri yüklemek gereksiz - chunk'lar upload sırasında oluşturulmuş olmalı
        log_extraction(f"🔄 Graph extraction başlıyor for: {fileName} (chunks should exist from upload)")
        log_extraction(f"🎯 Extract mode: Local file processing")
        
        # Document node'undan page_images'ı al
        page_images = None
        try:
            graph = create_graph_database_connection(uri, userName, password, database)
            graphDb_data_Access = graphDBdataAccess(graph)
            result = graphDb_data_Access.execute_query(
                "MATCH (d:Document {fileName: $file_name}) RETURN d.page_images AS page_images",
                {"file_name": fileName}
            )
            if result and len(result) > 0 and result[0].get('page_images'):
                page_images = result[0]['page_images']
                logging.info(f"🖼️ Retrieved {len(page_images)} page images from Document node")
        except Exception as e:
            logging.warning(f"⚠️ Could not retrieve page_images from Document node: {e}")
        
        return await processing_source(
            uri,
            userName,
            password,
            database,
            model,
            fileName,  # file_name olarak kullan
            [],  # pages artık gereksiz - boş array gönder
            allowedNodes,
            allowedRelationship,
            token_chunk_size,
            chunk_overlap,
            chunks_to_combine,
            True,
            merged_file_path,
            additional_instructions=additional_instructions,
            # Post-processing parametreleri
            enable_post_processing=enable_post_processing,
            post_processing_rules=post_processing_rules,
            # Page images for chunk links
            page_images=page_images,
            max_pages=max_pages,
        )
    else:
        return await processing_source(
            uri,
            userName,
            password,
            database,
            model,
            fileName,
            [],  # pages artık gereksiz - boş array gönder
            allowedNodes,
            allowedRelationship,
            token_chunk_size,
            chunk_overlap,
            chunks_to_combine,
            True,
            merged_file_path,
            retry_condition,
            additional_instructions=additional_instructions,
            # Post-processing parametreleri
            enable_post_processing=enable_post_processing,
            post_processing_rules=post_processing_rules,
            max_pages=max_pages,
        )


async def extract_graph_from_file_s3(
    uri,
    userName,
    password,
    database,
    model,
    source_url,
    aws_access_key_id,
    aws_secret_access_key,
    file_name,
    allowedNodes,
    allowedRelationship,
    token_chunk_size,
    chunk_overlap,
    chunks_to_combine,
    retry_condition,
    additional_instructions,
):
    if not retry_condition:
        if aws_access_key_id == None or aws_secret_access_key == None:
            raise LLMGraphBuilderException("Please provide AWS access and secret keys")
        else:
            logging.info("Insert in S3 Block")
            file_name, pages = get_documents_from_s3(
                source_url, aws_access_key_id, aws_secret_access_key
            )

        if pages == None or len(pages) == 0:
            raise LLMGraphBuilderException(
                f"File content is not available for file : {file_name}"
            )
        return await processing_source(
            uri,
            userName,
            password,
            database,
            model,
            file_name,
            pages,
            allowedNodes,
            allowedRelationship,
            token_chunk_size,
            chunk_overlap,
            chunks_to_combine,
            additional_instructions=additional_instructions,
        )
    else:
        return await processing_source(
            uri,
            userName,
            password,
            database,
            model,
            file_name,
            [],
            allowedNodes,
            allowedRelationship,
            token_chunk_size,
            chunk_overlap,
            chunks_to_combine,
            retry_condition=retry_condition,
            additional_instructions=additional_instructions,
        )


async def extract_graph_from_web_page(
    uri,
    userName,
    password,
    database,
    model,
    source_url,
    file_name,
    allowedNodes,
    allowedRelationship,
    token_chunk_size,
    chunk_overlap,
    chunks_to_combine,
    retry_condition,
    additional_instructions,
):
    if not retry_condition:
        pages = get_documents_from_web_page(source_url)
        if pages == None or len(pages) == 0:
            raise LLMGraphBuilderException(
                f"Content is not available for given URL : {file_name}"
            )
        return await processing_source(
            uri,
            userName,
            password,
            database,
            model,
            file_name,
            pages,
            allowedNodes,
            allowedRelationship,
            token_chunk_size,
            chunk_overlap,
            chunks_to_combine,
            additional_instructions=additional_instructions,
        )
    else:
        return await processing_source(
            uri,
            userName,
            password,
            database,
            model,
            file_name,
            [],
            allowedNodes,
            allowedRelationship,
            token_chunk_size,
            chunk_overlap,
            chunks_to_combine,
            retry_condition=retry_condition,
            additional_instructions=additional_instructions,
        )


async def extract_graph_from_file_youtube(
    uri,
    userName,
    password,
    database,
    model,
    source_url,
    file_name,
    allowedNodes,
    allowedRelationship,
    token_chunk_size,
    chunk_overlap,
    chunks_to_combine,
    retry_condition,
    additional_instructions,
):
    if not retry_condition:
        file_name, pages = get_documents_from_youtube(source_url)

        if pages == None or len(pages) == 0:
            raise LLMGraphBuilderException(
                f"Youtube transcript is not available for file : {file_name}"
            )
        return await processing_source(
            uri,
            userName,
            password,
            database,
            model,
            file_name,
            pages,
            allowedNodes,
            allowedRelationship,
            token_chunk_size,
            chunk_overlap,
            chunks_to_combine,
            additional_instructions=additional_instructions,
        )
    else:
        return await processing_source(
            uri,
            userName,
            password,
            database,
            model,
            file_name,
            [],
            allowedNodes,
            allowedRelationship,
            token_chunk_size,
            chunk_overlap,
            chunks_to_combine,
            retry_condition=retry_condition,
            additional_instructions=additional_instructions,
        )


async def extract_graph_from_file_Wikipedia(
    uri,
    userName,
    password,
    database,
    model,
    wiki_query,
    language,
    file_name,
    allowedNodes,
    allowedRelationship,
    token_chunk_size,
    chunk_overlap,
    chunks_to_combine,
    retry_condition,
    additional_instructions,
):
    if not retry_condition:
        file_name, pages = get_documents_from_Wikipedia(wiki_query, language)
        if pages == None or len(pages) == 0:
            raise LLMGraphBuilderException(
                f"Wikipedia page is not available for file : {file_name}"
            )
        return await processing_source(
            uri,
            userName,
            password,
            database,
            model,
            file_name,
            pages,
            allowedNodes,
            allowedRelationship,
            token_chunk_size,
            chunk_overlap,
            chunks_to_combine,
            additional_instructions=additional_instructions,
        )
    else:
        return await processing_source(
            uri,
            userName,
            password,
            database,
            model,
            file_name,
            [],
            allowedNodes,
            allowedRelationship,
            token_chunk_size,
            chunk_overlap,
            chunks_to_combine,
            retry_condition=retry_condition,
            additional_instructions=additional_instructions,
        )


async def extract_graph_from_file_gcs(
    uri,
    userName,
    password,
    database,
    model,
    gcs_project_id,
    gcs_bucket_name,
    gcs_bucket_folder,
    gcs_blob_filename,
    access_token,
    file_name,
    allowedNodes,
    allowedRelationship,
    token_chunk_size,
    chunk_overlap,
    chunks_to_combine,
    retry_condition,
    additional_instructions,
):
    if not retry_condition:
        file_name, pages = get_documents_from_gcs(
            gcs_project_id,
            gcs_bucket_name,
            gcs_bucket_folder,
            gcs_blob_filename,
            access_token,
        )
        if pages == None or len(pages) == 0:
            raise LLMGraphBuilderException(
                f"File content is not available for file : {file_name}"
            )
        return await processing_source(
            uri,
            userName,
            password,
            database,
            model,
            file_name,
            pages,
            allowedNodes,
            allowedRelationship,
            token_chunk_size,
            chunk_overlap,
            chunks_to_combine,
            additional_instructions=additional_instructions,
        )
    else:
        return await processing_source(
            uri,
            userName,
            password,
            database,
            model,
            file_name,
            [],
            allowedNodes,
            allowedRelationship,
            token_chunk_size,
            chunk_overlap,
            chunks_to_combine,
            retry_condition=retry_condition,
            additional_instructions=additional_instructions,
        )


async def processing_source(
    uri,
    userName,
    password,
    database,
    model,
    file_name,
    pages,
    allowedNodes,
    allowedRelationship,
    token_chunk_size,
    chunk_overlap,
    chunks_to_combine,
    is_uploaded_from_local=None,
    merged_file_path=None,
    retry_condition=None,
    additional_instructions=None,
    # Post-processing parametreleri
    enable_post_processing=False,
    post_processing_rules=None,
    # Page images for chunk links
    page_images=None,
    max_pages=None,
):
    """
    Extracts a Neo4jGraph from a PDF file based on the model.

    Args:
          uri: URI of the graph to extract
      db_name : db_name is database name to connect graph db
          userName: Username to use for graph creation ( if None will use username from config file )
          password: Password to use for graph creation ( if None will use password from config file )
          file: File object containing the PDF file to be used
          model: Type of model to use ('Diffbot'or'OpenAI GPT')

    Returns:
          Json response to API with fileName, nodeCount, relationshipCount, processingTime,
      status and model as attributes.
    """
    uri_latency = {}
    response = {}
    start_time = datetime.now()
    processing_source_start_time = time.time()
    start_create_connection = time.time()
    graph = create_graph_database_connection(uri, userName, password, database)
    end_create_connection = time.time()
    elapsed_create_connection = end_create_connection - start_create_connection
    logging.info(
        f"Time taken database connection: {elapsed_create_connection:.2f} seconds"
    )
    uri_latency["create_connection"] = f"{elapsed_create_connection:.2f}"
    graphDb_data_Access = graphDBdataAccess(graph)
    
    # Document node'ın mutlaka oluşturulduğundan emin ol
    try:
        logging.info(f"Document node kontrolü ve oluşturması: {file_name}")
        graphDb_data_Access.create_source_node(file_name, model=model)
        logging.info(f"Document node garantilendi: {file_name}")
    except Exception as e:
        logging.error(f"Document node oluşturma hatası: {e}")
        # Document durumunu Failed yap
        graphDb_data_Access.update_exception_db(file_name, str(e))
        raise e
    
    create_chunk_vector_index(graph)
    start_get_chunkId_chunkDoc_list = time.time()
    total_chunks, chunkId_chunkDoc_list = get_chunkId_chunkDoc_list(
        graph, file_name, pages, token_chunk_size, chunk_overlap, retry_condition, page_images
    )
    end_get_chunkId_chunkDoc_list = time.time()
    elapsed_get_chunkId_chunkDoc_list = (
        end_get_chunkId_chunkDoc_list - start_get_chunkId_chunkDoc_list
    )
    logging.info(
        f"Time taken to create list chunkids with chunk document: {elapsed_get_chunkId_chunkDoc_list:.2f} seconds"
    )
    uri_latency["create_list_chunk_and_document"] = (
        f"{elapsed_get_chunkId_chunkDoc_list:.2f}"
    )
    uri_latency["total_chunks"] = total_chunks

    # POLICY EXTRACTION - Eksik policy bilgilerini chunk içeriklerinden çıkar
    if total_chunks > 0:  # Chunk'lar varsa policy extraction yap
        try:
            logging.info(f"🔍 Policy entity extraction başlıyor: {file_name}")
            from src.policy_extraction import extract_missing_policy_info
            
            start_policy_extraction = time.time()
            policy_extraction_result = await extract_missing_policy_info(graph, file_name, model)
            end_policy_extraction = time.time()
            elapsed_policy_extraction = end_policy_extraction - start_policy_extraction
            
            logging.info(f"✅ Policy entity extraction tamamlandı: {elapsed_policy_extraction:.2f} saniye")
            logging.info(f"📋 Entity extraction sonucu: {policy_extraction_result}")
            
            uri_latency["policy_extraction"] = f"{elapsed_policy_extraction:.2f}"
            
        except Exception as e:
            logging.error(f"❌ Policy entity extraction hatası: {e}")
            # Policy extraction başarısız olsa bile ana işleme devam et
            uri_latency["policy_extraction"] = "failed"

    start_status_document_node = time.time()
    result = graphDb_data_Access.get_current_status_document_node(file_name)
    end_status_document_node = time.time()
    elapsed_status_document_node = end_status_document_node - start_status_document_node
    logging.info(f'Time taken to get the current status of document node: {elapsed_status_document_node:.2f} seconds')
    uri_latency["get_status_document_node"] = f"{elapsed_status_document_node:.2f}"
    # default for retry processed chunk offset
    select_chunks_with_retry = 0
    # initialize counts
    node_count = 0
    rel_count = 0

    # Batch processing of chunks handles relationships and embeddings
    # Final counts updated in loop below

    if len(result) > 0:
        if result[0]["Status"] != "Processing":
            obj_source_node = sourceNode()
            status = "Processing"
            obj_source_node.file_name = normalize_file_name(
                file_name.strip() if isinstance(file_name, str) else file_name
            )
            obj_source_node.status = status
            obj_source_node.total_chunks = total_chunks
            obj_source_node.model = model
            if retry_condition == START_FROM_LAST_PROCESSED_POSITION:
                node_count = result[0]["nodeCount"]
                rel_count = result[0]["relationshipCount"]
                select_chunks_with_retry = result[0]["processed_chunk"]
            obj_source_node.processed_chunk = 0 + select_chunks_with_retry
            logging.info(file_name)
            logging.info(obj_source_node)

            start_update_source_node = time.time()
            graphDb_data_Access.update_source_node(obj_source_node)
            graphDb_data_Access.update_node_relationship_count(file_name)
            end_update_source_node = time.time()
            elapsed_update_source_node = (
                end_update_source_node - start_update_source_node
            )
            logging.info(
                f"Time taken to update the document source node: {elapsed_update_source_node:.2f} seconds"
            )
            uri_latency["update_source_node"] = f"{elapsed_update_source_node:.2f}"

            logging.info("Update the status as Processing")
            update_graph_chunk_processed = int(
                os.environ.get("UPDATE_GRAPH_CHUNKS_PROCESSED")
            )
            # selected_chunks = []
            is_cancelled_status = False
            job_status = "Completed"
            failed_chunks = []
            successful_chunks = 0
            
            for i in range(0, len(chunkId_chunkDoc_list), update_graph_chunk_processed):
                select_chunks_upto = i + update_graph_chunk_processed
                logging.info(f"Selected Chunks upto: {select_chunks_upto}")
                if len(chunkId_chunkDoc_list) <= select_chunks_upto:
                    select_chunks_upto = len(chunkId_chunkDoc_list)
                selected_chunks = chunkId_chunkDoc_list[i:select_chunks_upto]

                result = graphDb_data_Access.get_current_status_document_node(file_name)
                is_cancelled_status = result[0]["is_cancelled"]
                logging.info(f"Value of is_cancelled : {result[0]['is_cancelled']}")
                if bool(is_cancelled_status) == True:
                    job_status = "Cancelled"
                    logging.info("Exit from running loop of processing file")
                    break
                else:
                    try:
                        processing_chunks_start_time = time.time()
                        node_count, rel_count, latency_processed_chunk = (
                            await processing_chunks(
                                selected_chunks,
                                graph,
                                uri,
                                userName,
                                password,
                                database,
                                file_name,
                                model,
                                allowedNodes,
                                allowedRelationship,
                                chunks_to_combine,
                                node_count,
                                rel_count,
                                additional_instructions,
                                max_pages,
                            )
                        )
                        successful_chunks += len(selected_chunks)
                        logging.info(f"Chunk batch {i}-{select_chunks_upto} başarıyla işlendi")
                    except Exception as chunk_error:
                        # Chunk processing hatası - sonraki batch'e geç
                        failed_chunks.extend([f"batch_{i}-{select_chunks_upto}"])
                        logging.error(f"Chunk batch {i}-{select_chunks_upto} işlenirken hata: {chunk_error}")
                        logging.info(f"Sonraki chunk batch'ine geçiliyor...")
                        
                        # Graph bağlantısını yeniden kurmayı dene
                        try:
                            if graph is None or graph._driver._closed:
                                logging.info("Graph bağlantısı yeniden kuruluyor...")
                                graph = create_graph_database_connection(uri, userName, password, database)
                                graphDb_data_Access = graphDBdataAccess(graph)
                        except Exception as reconnect_error:
                            logging.error(f"Graph yeniden bağlantı hatası: {reconnect_error}")
                        
                        # Bu batch için continue - sonraki batch'e geç
                        continue
                        
                    # Bu kısım sadece başarılı chunk'lar için çalışır
                    processing_chunks_end_time = time.time()
                    processing_chunks_elapsed_end_time = (
                        processing_chunks_end_time - processing_chunks_start_time
                    )
                    logging.info(
                        f"Time taken {update_graph_chunk_processed} chunks processed upto {select_chunks_upto} completed in {processing_chunks_elapsed_end_time:.2f} seconds for file name {file_name}"
                    )
                    uri_latency[f"processed_combine_chunk_{i}-{select_chunks_upto}"] = (
                        f"{processing_chunks_elapsed_end_time:.2f}"
                    )
                    uri_latency[f"processed_chunk_detail_{i}-{select_chunks_upto}"] = (
                        latency_processed_chunk
                    )
                    end_time = datetime.now()
                    processed_time = end_time - start_time

                    obj_source_node = sourceNode()
                    obj_source_node.file_name = normalize_file_name(file_name)
                    obj_source_node.updated_at = end_time
                    obj_source_node.processing_time = processed_time
                    obj_source_node.processed_chunk = (
                        select_chunks_upto + select_chunks_with_retry
                    )
                    if retry_condition == START_FROM_BEGINNING:
                        result = execute_graph_query(
                            graph,
                            QUERY_TO_GET_NODES_AND_RELATIONS_OF_A_DOCUMENT,
                            params={"filename": file_name},
                        )
                        obj_source_node.node_count = result[0]["nodes"]
                        obj_source_node.relationship_count = result[0]["rels"]
                    else:
                        obj_source_node.node_count = node_count
                        obj_source_node.relationship_count = rel_count
                    graphDb_data_Access.update_source_node(obj_source_node)
                    graphDb_data_Access.update_node_relationship_count(file_name)

            # Extract işlemi tamamlandıktan sonra özet çıkar
            total_chunks_attempted = len(chunkId_chunkDoc_list)
            failed_chunks_count = len(failed_chunks)
            success_rate = (successful_chunks / total_chunks_attempted) * 100 if total_chunks_attempted > 0 else 0
            
            logging.info(f"Extract özeti - Dosya: {file_name}")
            logging.info(f"Toplam chunk: {total_chunks_attempted}, Başarılı: {successful_chunks}, Başarısız: {failed_chunks_count}")
            logging.info(f"Başarı oranı: {success_rate:.1f}%")
            
            if failed_chunks_count > 0:
                logging.warning(f"Başarısız chunk batch'ler: {failed_chunks}")
                # Eğer %50'den fazla chunk başarısızsa, job'ı partial olarak işaretle
                if success_rate < 50:
                    job_status = "Partially Failed"
                    logging.warning(f"Extract kısmen başarısız - başarı oranı %{success_rate:.1f}")
                else:
                    job_status = "Partially Completed"
                    logging.info(f"Extract kısmen tamamlandı - başarı oranı %{success_rate:.1f}")
            else:
                logging.info("Tüm chunk'lar başarıyla işlendi")

            result = graphDb_data_Access.get_current_status_document_node(file_name)
            is_cancelled_status = result[0]["is_cancelled"]
            if bool(is_cancelled_status) == True:
                logging.info(f"Is_cancelled True at the end extraction")
                job_status = "Cancelled"
            logging.info(f"Job Status at the end : {job_status}")
            end_time = datetime.now()
            processed_time = end_time - start_time
            obj_source_node = sourceNode()
            obj_source_node.file_name = normalize_file_name(
                file_name.strip() if isinstance(file_name, str) else file_name
            )
            obj_source_node.status = job_status
            obj_source_node.processing_time = processed_time

            graphDb_data_Access.update_source_node(obj_source_node)
            graphDb_data_Access.update_node_relationship_count(normalize_file_name(file_name))
            logging.info(
                "Updated the nodeCount and relCount properties in Document node"
            )
            logging.info(f"file:{file_name} extraction has been completed")

            # merged_file_path have value only when file uploaded from local

            if is_uploaded_from_local:
                gcs_file_cache = os.environ.get("GCS_FILE_CACHE")
                if gcs_file_cache == "True":
                    folder_name = create_gcs_bucket_folder_name_hashed(uri, file_name)
                    delete_file_from_gcs(BUCKET_UPLOAD, folder_name, file_name)
                else:
                    delete_uploaded_local_file(merged_file_path, file_name)
            processing_source_func = time.time() - processing_source_start_time
            logging.info(
                f"Time taken to processing source function completed in {processing_source_func:.2f} seconds for file name {file_name}"
            )
            uri_latency["Processed_source"] = f"{processing_source_func:.2f}"
            if node_count == 0:
                uri_latency["Per_entity_latency"] = "N/A"
            else:
                uri_latency["Per_entity_latency"] = (
                    f"{int(processing_source_func)/node_count}/s"
                )

            # Otomatik cleanup ve post-processing (eğer extract başarılı ise)
            # if job_status == "Completed":
            #     try:
            #         # 1. Policy Node Cleanup - Orphan Policy node'larını temizle
            #         logging.info(f"Policy node cleanup başlıyor: {file_name}")
            #         from src.policy_cleanup import cleanup_policy_nodes_to_document
                    
            #         cleanup_result = cleanup_policy_nodes_to_document(graph, file_name)
            #         if cleanup_result['policy_nodes_found'] > 0:
            #             logging.info(f"Policy cleanup tamamlandı: {cleanup_result['policy_nodes_deleted']} node silindi, {cleanup_result['relationships_moved']} ilişki taşındı")
            #         else:
            #             logging.info(f"Policy cleanup: Temizlenecek Policy node bulunamadı")
                        
            #     except Exception as cleanup_error:
            #         logging.error(f"Policy cleanup hatası: {cleanup_error}")
                
                # 2. Cross-chunk similarity relationships
                # try:
                #     logging.info(f"Cross-chunk similarity relationships başlıyor: {file_name}")
                #     cross_chunk_start_time = time.time()
                #     create_cross_chunk_relations(graph, file_name)
                #     cross_chunk_end_time = time.time()
                #     logging.info(f"Cross-chunk relationships tamamlandı: {cross_chunk_end_time - cross_chunk_start_time:.2f} saniye")
                #     uri_latency["cross_chunk_rel_post"] = f"{cross_chunk_end_time - cross_chunk_start_time:.2f}"
                # except Exception as cross_chunk_error:
                #     logging.error(f"Cross-chunk relationship hatası: {cross_chunk_error}")
                #     uri_latency["cross_chunk_rel_post"] = "FAILED"
                
                # 3. Post-processing (eğer kurallar verilmiş ise)
                if enable_post_processing and post_processing_rules:
                    try:
                        logging.info(f"Extract sonrası otomatik post-processing başlıyor: {file_name}")
                        
                        # Post-processing kurallarını parse et
                        if isinstance(post_processing_rules, str):
                            import json
                            rules_list = json.loads(post_processing_rules)
                        else:
                            rules_list = post_processing_rules
                        
                        logging.info(f"Post-processing kuralları: {rules_list}")
                        
                        # Post-processing'i çalıştır - sadece bu dosya için
                        from src.llm import apply_dynamic_entity_post_processing
                        
                        # Neo4j'deki gerçek dosya adını al (Unicode escape karakterleri ile)
                        real_file_name_query = """
                        MATCH (d:Document) 
                        WHERE d.fileName = $fileName OR d.fileName CONTAINS $fileNamePart
                        RETURN d.fileName as realFileName
                        LIMIT 1
                        """
                        # Dosya adının bir kısmını al (ilk 20 karakter gibi)
                        file_name_part = file_name[:20] if len(file_name) > 20 else file_name
                        real_file_result = execute_graph_query(graph, real_file_name_query, 
                                                             params={"fileName": file_name, "fileNamePart": file_name_part})
                        
                        if real_file_result and real_file_result[0].get('realFileName'):
                            real_file_name = real_file_result[0]['realFileName']
                            logging.info(f"Post-processing için gerçek dosya adı: {real_file_name}")
                            target_files = [real_file_name]
                        else:
                            logging.warning(f"Gerçek dosya adı bulunamadı, orijinal kullanılıyor: {file_name}")
                            target_files = [file_name]
                        
                        post_processing_start_time = time.time()
                        post_processing_result = apply_dynamic_entity_post_processing(
                            graph, 
                            rules_list, 
                            target_file_names=target_files
                        )
                        post_processing_end_time = time.time()
                        
                        logging.info(f"Extract sonrası post-processing tamamlandı: {post_processing_end_time - post_processing_start_time:.2f} saniye")
                        logging.info(f"Post-processing ile {post_processing_result.get('total_created_relationships', 0)} yeni relationship oluşturuldu")
                        
                        # Node count'ları tekrar say (post-processing sonrasında)
                        graphDb_data_Access = graphDBdataAccess(graph)
                        final_count_response = graphDb_data_Access.update_node_relationship_count(file_name)
                        if final_count_response:
                            final_node_count = int(final_count_response[file_name].get('nodeCount', node_count))
                            final_rel_count = int(final_count_response[file_name].get('relationshipCount', rel_count))
                            
                            # Response'u güncelle
                            response["nodeCount"] = final_node_count
                            response["relationshipCount"] = final_rel_count
                            
                            logging.info(f"Post-processing sonrası güncel sayılar - Nodes: {final_node_count}, Relationships: {final_rel_count}")
                        
                        # Post-processing bilgilerini uri_latency'ye ekle
                        uri_latency["post_processing_enabled"] = "true"
                        uri_latency["post_processing_time"] = f"{post_processing_end_time - post_processing_start_time:.2f}"
                        uri_latency["post_processing_created_rels"] = str(post_processing_result.get('total_created_relationships', 0))
                        uri_latency["post_processing_rules_count"] = str(len(rules_list))
                        
                    except Exception as post_processing_error:
                        logging.error(f"Extract sonrası post-processing hatası: {post_processing_error}")
                        uri_latency["post_processing_enabled"] = "true"
                        uri_latency["post_processing_error"] = str(post_processing_error)
                else:
                    uri_latency["post_processing_enabled"] = "false"
                
                # Cross-chunk relationships her zaman post-processing aşamasında yapıldı
                logging.info(f"Post-processing tamamlandı - Cross-chunk relationships dahil edildi")

            response["fileName"] = file_name
            response["nodeCount"] = node_count
            response["relationshipCount"] = rel_count
            response["total_processing_time"] = round(processed_time.total_seconds(), 2)
            response["status"] = job_status
            response["model"] = model
            response["success_count"] = 1

            return uri_latency, response
        else:
            logging.info(
                "File does not process because its already in Processing status"
            )
            return uri_latency, response
    else:
        error_message = "Unable to get the status of document node."
        logging.error(error_message)
        raise LLMGraphBuilderException(error_message)


async def processing_source_v2(
    uri,
    userName,
    password,
    database,
    model,
    file_name,
    pages,
    allowedNodes,
    allowedRelationship,
    additional_instructions=None,
    max_pages=None,
    page_images=None,
):
    """
    V2 Processing: Policy-specific entity extraction (simplified)
    
    Pages'ten Policy-specific entity'leri çıkarır:
    - ✅ Chunk oluşturma (create_chunks_for_upload ile)
    - ❌ Chunk embeddings yok (şimdilik)
    - ❌ Chunk-Entity linking yok (şimdilik)
    - ❌ Rastgele entity extraction yok (Person, Organization, etc.)
    - ✅ Policy-specific entities (Policy, Customer, InsuranceCompany, Agent, Coverage)
    - ✅ LLM extraction (_create_document_related_nodes)
    - ✅ Neo4j'ye kaydetme
    - ✅ Policy-Entity relationships (HAS_ENTITY)
    - ❌ Duplicate merge yok (manuel olarak /merge_duplicate_entities endpoint'i ile yapılır)
    
    Not: Sadece 1 LLM çağrısı yapılır (_create_document_related_nodes içinde)
    """
    uri_latency = {}
    response = {}
    start_time = datetime.now()
    
    try:
        # Graph connection
        start_create_connection = time.time()
        graph = create_graph_database_connection(uri, userName, password, database)
        elapsed_create_connection = time.time() - start_create_connection
        logging.info(f"⏱️ Database connection: {elapsed_create_connection:.2f}s")
        uri_latency["create_connection"] = f"{elapsed_create_connection:.2f}"
        
        graphDb_data_Access = graphDBdataAccess(graph)
        
        # Chunk'ları oluştur (V2 için)
        if pages:
            logging.info(f"🧩 Creating {len(pages)} chunks for V2 file: {file_name}")
            # create_chunks_for_upload imported via src.make_relationships import *
            logging.info(f"🔄 V2: Starting create_chunks_for_upload for: {file_name} (async, non-blocking)")
            await create_chunks_for_upload(graph, pages, file_name, page_images=page_images)
            logging.info(f"✅ Chunks created successfully for: {file_name}")
        
        # Document status kontrolü (node zaten chunking'de oluşturuldu) - async
        start_status_check = time.time()
        result = await asyncio.to_thread(graphDb_data_Access.get_current_status_document_node, file_name)
        elapsed_status_check = time.time() - start_status_check
        uri_latency["status_check"] = f"{elapsed_status_check:.2f}"
        
        if not result or len(result) == 0:
            raise LLMGraphBuilderException(f"Unable to get document status for: {file_name}")
        
        # Neo4j'deki Document node status'unu kontrol et
        current_status = result[0]["Status"]
        
        # Chunked status'u graph creation için uygun
        if current_status == "Chunked":
            logging.info(f"✅ File is Chunked and ready for graph creation: {file_name}")
        # Processing status'u restart sonrası olabilir, bu durumda işleme devam et
        # (SQLite'daki status "pending"e reset edilmiş olabilir ama Neo4j'deki status hala "Processing" olabilir)
        elif current_status == "Processing":
            logging.warning(
                f"⚠️ File is in Processing status in Neo4j, but continuing anyway (may be restart scenario): {file_name}"
            )
            # Continue processing - this handles restart scenarios where SQLite was reset but Neo4j wasn't
        else:
            logging.info(f"📋 Current file status: {current_status} for {file_name}")
        
        # Status'u Processing olarak güncelle - async
        obj_source_node = sourceNode()
        obj_source_node.file_name = normalize_file_name(file_name)
        obj_source_node.status = "Processing"
        obj_source_node.model = model
        obj_source_node.processed_chunk = 0
        obj_source_node.total_chunks = len(pages)  # Page sayısı
        
        start_update_status = time.time()
        await asyncio.to_thread(graphDb_data_Access.update_source_node, obj_source_node)
        elapsed_update_status = time.time() - start_update_status
        uri_latency["update_status_to_processing"] = f"{elapsed_update_status:.2f}"
        
        logging.info(f"🔄 V2 Processing started for: {file_name} ({len(pages)} pages)")
        
        # Policy-specific Entity Extraction (LLM çağrısı - tek LLM call)
        # Retry mekanizması ile LLM extraction hatalarını yönet
        start_extraction = time.time()
        logging.info(f"🚀 Policy-specific entity extraction başlıyor...")
        
        max_retries = int(os.environ.get("LLM_EXTRACTION_MAX_RETRIES", "3"))
        retry_delay = int(os.environ.get("LLM_EXTRACTION_RETRY_DELAY", "5"))  # seconds
        retries = 0
        current_delay = retry_delay
        extraction_successful = False
        last_error = None
        
        while retries < max_retries and not extraction_successful:
            try:
                # _create_document_related_nodes: Policy, Customer, InsuranceCompany vs. çıkarır
                # Bu fonksiyon içinde zaten LLM çağrısı yapılıyor (create_policy_node_from_document)
                # LLM çağrısı senkron olduğu için thread pool'da çalıştırıyoruz (sunucuyu bloklamamak için)
                await asyncio.to_thread(
                    graphDb_data_Access._create_document_related_nodes,
                    file_name,
                    "auto",
                    None,
                    model
                )
                
                extraction_successful = True
                elapsed_extraction = time.time() - start_extraction
                uri_latency["policy_entity_extraction"] = f"{elapsed_extraction:.2f}"
                if retries > 0:
                    logging.info(f"✅ Policy entity extraction başarılı (deneme {retries + 1}/{max_retries}) - {elapsed_extraction:.2f}s")
                else:
                    logging.info(f"✅ Policy entity extraction tamamlandı - {elapsed_extraction:.2f}s")
                
            except Exception as extraction_error:
                retries += 1
                last_error = extraction_error
                error_str = str(extraction_error)
                
                # LLM extraction hatalarını kontrol et
                is_llm_error = (
                    "varlık çıkarımı başarısız" in error_str.lower() or
                    "llm extraction hatası" in error_str.lower() or
                    "extraction" in error_str.lower() or
                    "entity" in error_str.lower() or
                    "policy" in error_str.lower()
                )
                
                if retries < max_retries and is_llm_error:
                    logging.warning(
                        f"⚠️ LLM extraction hatası (deneme {retries}/{max_retries}): {error_str[:200]}... "
                        f"{current_delay} saniye bekleyip tekrar denenecek..."
                    )
                    await asyncio.sleep(current_delay)
                    current_delay *= 2  # Exponential backoff
                else:
                    # Retry limit'e ulaşıldı veya LLM hatası değil
                    elapsed_extraction = time.time() - start_extraction
                    uri_latency["policy_entity_extraction"] = f"FAILED - {elapsed_extraction:.2f}"
                    if retries >= max_retries:
                        logging.error(
                            f"❌ Policy entity extraction {max_retries} deneme sonrası başarısız: {error_str[:500]}"
                        )
                    else:
                        logging.error(f"❌ Policy entity extraction hatası (retry yapılmayacak): {error_str[:500]}")
                    # Dosya durumunu Failed yap ve işlemi sonlandır - async
                    await asyncio.to_thread(graphDb_data_Access.update_exception_db, file_name, str(last_error))
                    raise last_error
        
        if not extraction_successful:
            elapsed_extraction = time.time() - start_extraction
            uri_latency["policy_entity_extraction"] = f"FAILED - {elapsed_extraction:.2f}"
            logging.error(f"❌ Policy entity extraction başarısız: {last_error}")
            await asyncio.to_thread(graphDb_data_Access.update_exception_db, file_name, str(last_error))
            raise last_error
        
        # Policy-Entity Relationships
        # start_policy_rel = time.time()
        # try:
        #     create_policy_entity_relationships(graph, file_name)
        #     elapsed_policy_rel = time.time() - start_policy_rel
        #     uri_latency["policy_entity_rel"] = f"{elapsed_policy_rel:.2f}"
        #     logging.info(f"✅ Policy-Entity relationships created - {elapsed_policy_rel:.2f}s")
        # except Exception as policy_error:
        #     elapsed_policy_rel = time.time() - start_policy_rel
        #     uri_latency["policy_entity_rel"] = f"FAILED - {elapsed_policy_rel:.2f}"
        #     logging.error(f"❌ Policy-Entity relationship hatası: {policy_error}")
        
        # Final counts update - async
        start_count_update = time.time()
        try:
            counts = await asyncio.to_thread(graphDb_data_Access.update_node_relationship_count, file_name)
            node_count = counts[file_name].get("nodeCount", 0)
            rel_count = counts[file_name].get("relationshipCount", 0)
            elapsed_count_update = time.time() - start_count_update
            uri_latency["count_update"] = f"{elapsed_count_update:.2f}"
            logging.info(f"✅ Final counts: {node_count} nodes, {rel_count} relationships")
        except Exception as count_error:
            elapsed_count_update = time.time() - start_count_update
            uri_latency["count_update"] = f"FAILED - {elapsed_count_update:.2f}"
            logging.error(f"❌ Count update hatası: {count_error}")
            node_count = 0
            rel_count = 0
        
        # Status'u Completed olarak güncelle
        end_time = datetime.now()
        processed_time = end_time - start_time
        
        obj_source_node = sourceNode()
        obj_source_node.file_name = normalize_file_name(file_name)
        obj_source_node.status = "Completed"
        obj_source_node.processing_time = processed_time
        obj_source_node.updated_at = end_time
        obj_source_node.node_count = node_count
        obj_source_node.relationship_count = rel_count
        obj_source_node.processed_chunk = len(pages)  # Tüm pages işlendi
        
        # Final status update - async
        await asyncio.to_thread(graphDb_data_Access.update_source_node, obj_source_node)
        
        total_processing_time = time.time() - start_time.timestamp()
        uri_latency["total_processing_time"] = f"{total_processing_time:.2f}"
        
        logging.info(f"✅ V2 Processing completed for: {file_name}")
        logging.info(f"📊 Results: {node_count} nodes, {rel_count} relationships in {total_processing_time:.2f}s")
        
        # Response
        response = {
            "fileName": file_name,
            "nodeCount": node_count,
            "relationshipCount": rel_count,
            "total_processing_time": round(processed_time.total_seconds(), 2),
            "status": "Completed",
            "model": model,
            "success_count": 1,
            "processing_version": "V2"
        }
        
        return uri_latency, response
        
    except Exception as e:
        logging.error(f"❌ processing_source_v2 failed for {file_name}: {e}")
        import traceback
        logging.error(f"Traceback: {traceback.format_exc()}")
        
        # Status'u Failed olarak güncelle - async
        try:
            obj_source_node = sourceNode()
            obj_source_node.file_name = normalize_file_name(file_name)
            obj_source_node.status = "Failed"
            obj_source_node.processing_error = str(e)[:500]
            await asyncio.to_thread(graphDb_data_Access.update_source_node, obj_source_node)
        except:
            pass
        
        response = {
            "fileName": file_name,
            "status": "Failed",
            "error": str(e),
            "processing_version": "V2"
        }
        
        return uri_latency, response


async def processing_chunks(
  chunkId_chunkDoc_list,
  graph,
  uri,
  userName,
  password,
  database,
  file_name,
  model,
  allowedNodes,
  allowedRelationship,
  chunks_to_combine,
  node_count,
  rel_count,
  additional_instructions=None,
  max_pages=None,
):
  latency = {}
  successful_steps = 0
  total_steps = 7  # 6'dan 7'ye artırdık

  # (re)open driver if closed
  if graph is None or graph._driver._closed:
    graph = create_graph_database_connection(uri, userName, password, database)

  # 1. update embeddings on chunks
  try:
    t0 = time.time()
    create_chunk_embeddings(graph, chunkId_chunkDoc_list, file_name)
    latency["update_embedding"] = f"{time.time() - t0:.2f}"
    successful_steps += 1
    logging.info(f"Step 1/7 başarılı: Chunk embeddings oluşturuldu")
  except Exception as e:
    latency["update_embedding"] = "FAILED"
    logging.error(f"Step 1/7 başarısız: Chunk embeddings oluşturulamadı - {e}")

  # 2. ask LLM for sub-graph per chunk
  try:
    t1 = time.time()
    logging.info(f"🚀 LLM Graph Transformer başlıyor - Entity extraction için LLM çağrılıyor")
    graph_documents = await get_graph_from_llm(
      model,
      chunkId_chunkDoc_list,
      allowedNodes,
      allowedRelationship,
      chunks_to_combine,
      file_name,
      additional_instructions,
      graph,
      max_pages
    )
    latency["entity_extraction"] = f"{time.time() - t1:.2f}"
    successful_steps += 1
    logging.info(f"Step 2/7 başarılı: ✅ LLM entity extraction tamamlandı - {len(graph_documents)} graph document oluşturuldu")
  except Exception as e:
    latency["entity_extraction"] = "FAILED"
    logging.error(f"Step 2/7 başarısız: ❌ LLM entity extraction hatası - {e}")
    # LLM hatası kritik - boş graph_documents ile devam et
    graph_documents = []

  # 3. normalize IDs / backticks / types
  try:
    cleaned = handle_backticks_nodes_relationship_id_type(graph_documents)
    successful_steps += 1
    logging.info(f"Step 3/7 başarılı: ✅ Entity'ler normalize edildi - {len(cleaned)} temizlenmiş graph document")
  except Exception as e:
    logging.error(f"Step 3/7 başarısız: ❌ Entity normalization hatası - {e}")
    cleaned = []

  # 4. save nodes & rels into Neo4j
  try:
    t2 = time.time()
    logging.info(f"🗃️ Entity'ler Neo4j'ye kaydediliyor...")
    save_graphDocuments_in_neo4j(graph, cleaned)
    latency["save_graphDocuments"] = f"{time.time() - t2:.2f}"
    successful_steps += 1
    logging.info(f"Step 4/7 başarılı: ✅ Entity'ler Neo4j'ye kaydedildi")
  except Exception as e:
    latency["save_graphDocuments"] = "FAILED"
    logging.error(f"Step 4/7 başarısız: ❌ Neo4j'ye kaydetme hatası - {e}")

  # 5. relate each chunk to its extracted entities (technical tracking)
  try:
    pairs = get_chunk_and_graphDocument(cleaned, chunkId_chunkDoc_list)
    t3 = time.time()
    logging.info(f"🔗 Chunk-Entity EXTRACTED_FROM ilişkileri oluşturuluyor...")
    merge_relationship_between_chunk_and_entites(graph, pairs)
    latency["chunk_entity_rel"] = f"{time.time() - t3:.2f}"
    successful_steps += 1
    logging.info(f"Step 5/7 başarılı: ✅ Chunk-Entity EXTRACTED_FROM ilişkileri oluşturuldu (technical tracking)")
  except Exception as e:
    latency["chunk_entity_rel"] = "FAILED"
    logging.error(f"Step 5/7 başarısız: ❌ Chunk-Entity EXTRACTED_FROM ilişki hatası - {e}")

  # 6. Create Policy-Entity relationships (business logic)
  try:
    t4 = time.time()
    logging.info(f"🏢 Policy-Entity HAS_ENTITY ilişkileri oluşturuluyor...")
    create_policy_entity_relationships(graph, file_name)
    latency["policy_entity_rel"] = f"{time.time() - t4:.2f}"
    successful_steps += 1
    logging.info(f"Step 6/7 başarılı: ✅ Policy-Entity HAS_ENTITY ilişkileri oluşturuldu (business logic)")
  except Exception as e:
    latency["policy_entity_rel"] = "FAILED"
    logging.error(f"Step 6/7 başarısız: ❌ Policy-Entity HAS_ENTITY ilişki hatası - {e}")

  # 6.5. Merge duplicate nodes with upload-time nodes
  try:
    from src.llm import merge_duplicate_nodes_with_upload_nodes
    t5 = time.time()
    merge_result = merge_duplicate_nodes_with_upload_nodes(graph, file_name)
    latency["duplicate_node_merge"] = f"{time.time() - t5:.2f}"
    logging.info(f"Duplicate node merge: {merge_result['merged_count']} node merge edildi")
    if merge_result['merged_count'] > 0:
      logging.info(f"Merge detayları: {merge_result['details']}")
  except Exception as e:
    latency["duplicate_node_merge"] = "FAILED"
    logging.error(f"Duplicate node merge hatası - {e}")

  # 7. update overall node/relationship counts (her zaman çalıştır)
  try:
    graphDb = graphDBdataAccess(graph)
    counts = graphDb.update_node_relationship_count(file_name)
    node_count = counts[file_name].get("nodeCount", 0)
    rel_count = counts[file_name].get("relationshipCount", 0)
    logging.info(f"Node/Relationship sayıları güncellendi: {node_count} nodes, {rel_count} rels")
  except Exception as e:
    logging.error(f"Node/Relationship sayı güncelleme hatası - {e}")
    # Varsayılan değerlerle devam et
    node_count = node_count if node_count else 0
    rel_count = rel_count if rel_count else 0

  success_rate = (successful_steps / total_steps) * 100
  logging.info(f"Chunk processing özeti: {successful_steps}/{total_steps} step başarılı (%{success_rate:.1f})")
  
  return node_count, rel_count, latency


def get_chunkId_chunkDoc_list(
    graph, file_name, pages, token_chunk_size, chunk_overlap, retry_condition, page_images=None
):
    # File name'i normalize et
    file_name = normalize_file_name(file_name)
    
    if not retry_condition:
        logging.info("Looking for existing chunks (chunks must be created during upload)")
        
        # Chunk'ların upload sırasında oluşturulmuş olup olmadığını kontrol et
        # Yeni sistem: PART_OF ilişkisi ile
        existing_chunks = execute_graph_query(
            graph, QUERY_TO_GET_CHUNKS, params={"filename": file_name}
        )
        
        # Eğer yeni sistemde chunk bulunamazsa eski sistemi dene (fileName property'si ile)
        if not existing_chunks or not existing_chunks[0].get("text"):
            logging.info(f"🔄 Trying legacy chunk lookup for: {file_name}")
            legacy_query = """
                MATCH (c:Chunk) 
                WHERE c.fileName = $filename 
                RETURN c.id as id, c.text as text, c.position as position, c.page_number as page_number
                ORDER BY c.position
            """
            existing_chunks = execute_graph_query(graph, legacy_query, params={"filename": file_name})
        
        if existing_chunks and existing_chunks[0].get("text"):
            logging.info(f"✅ Found {len(existing_chunks)} existing chunks for {file_name}")
            
            # Mevcut chunk'ları kullan, sadece embedding'leri kontrol et ve ekle
            chunkId_chunkDoc_list = []
            for chunk in existing_chunks:
                # Metadata'ya page_number dahil et
                metadata = {"id": chunk["id"], "position": chunk["position"]}
                if chunk.get("page_number") is not None:
                    metadata["page_number"] = chunk["page_number"]
                
                chunk_doc = Document(
                    page_content=chunk["text"],
                    metadata=metadata,
                )
                chunkId_chunkDoc_list.append(
                    {"chunk_id": chunk["id"], "chunk_doc": chunk_doc}
                )
            
            # Embedding'leri kontrol et ve eksikleri ekle
            create_chunk_embeddings(graph, chunkId_chunkDoc_list, file_name)
            
            return len(existing_chunks), chunkId_chunkDoc_list
        else:
            # Chunk'lar upload sırasında oluşturulmamışsa (eski dosyalar için)
            logging.warning(f"No chunks found for {file_name}. Attempting to create chunks from existing document...")
            
            # Document node'dan dosya bilgilerini al
            try:
                document_query = """
                MATCH (d:Document {fileName: $file_name}) 
                RETURN d.file_type AS file_type, d.created_at AS created_at
                """
                doc_result = execute_graph_query(graph, document_query, params={"filename": file_name})
                
                if doc_result and len(doc_result) > 0:
                    logging.info(f"Found Document node for {file_name}, attempting chunk creation...")
                    
                    # Dosya path'ini bul ve chunk'ları oluştur
                    merged_file_path = None
                    possible_paths = [
                        f"/Users/mehmeterdogan/python-projects/llm-graph-builder/merged_files/{file_name}",
                        f"./merged_files/{file_name}",
                        f"merged_files/{file_name}"
                    ]
                    
                    for path in possible_paths:
                        if os.path.exists(path):
                            merged_file_path = path
                            break
                    
                    if merged_file_path and os.path.exists(merged_file_path):
                        logging.info(f"Found file at: {merged_file_path}, creating chunks...")
                        
                        # Dosyayı load et ve chunk'ları oluştur
                        from src.document_sources.local_file import load_document_content
                        loader, encoding_flag, _ = load_document_content(merged_file_path, generate_images=False)
                        pages = loader.load()
                        
                        if pages:
                            # Chunk'ları oluştur
                            from src.create_chunks import CreateChunksofDocument
                            create_chunks_obj = CreateChunksofDocument(pages, graph)
                            
                            chunks = create_chunks_obj.split_file_into_chunks_recursive(
                                chunk_size=int(token_chunk_size), 
                                chunk_overlap=int(chunk_overlap)
                            )
                            
                            if chunks:
                                # Chunk node'ları veritabanına kaydet
                                from src.make_relationships import create_chunks_for_upload
                                import asyncio
                                
                                # create_chunks_for_upload artık async, event loop içinde çalıştır
                                try:
                                    loop = asyncio.get_event_loop()
                                    if loop.is_running():
                                        # Eğer loop zaten çalışıyorsa, thread pool'da çalıştır
                                        import concurrent.futures
                                        async def run_create_chunks():
                                            return await create_chunks_for_upload(
                                                graph=graph,
                                                chunks=chunks, 
                                                file_name=file_name,
                                                page_images=page_images if page_images else [],
                                                generate_embedding=False  # Emergency durumda embedding oluşturma
                                            )
                                        with concurrent.futures.ThreadPoolExecutor() as executor:
                                            future = executor.submit(asyncio.run, run_create_chunks())
                                            created_chunks = future.result()
                                    else:
                                        created_chunks = loop.run_until_complete(create_chunks_for_upload(
                                            graph=graph,
                                            chunks=chunks, 
                                            file_name=file_name,
                                            page_images=page_images if page_images else [],
                                            generate_embedding=False  # Emergency durumda embedding oluşturma
                                        ))
                                except RuntimeError:
                                    # Event loop yoksa, yeni bir tane oluştur
                                    created_chunks = asyncio.run(create_chunks_for_upload(
                                        graph=graph,
                                        chunks=chunks, 
                                        file_name=file_name,
                                        page_images=page_images if page_images else [],
                                        generate_embedding=False  # Emergency durumda embedding oluşturma
                                    ))
                                
                                logging.info(f"✅ Emergency chunk creation completed - {len(created_chunks)} chunks created")
                                
                                # Şimdi chunk'ları kullan
                                chunkId_chunkDoc_list = []
                                for i, chunk_data in enumerate(created_chunks):
                                    chunk_doc = Document(
                                        page_content=chunk_data["text"],
                                        metadata={"id": chunk_data["id"], "position": i + 1},
                                    )
                                    chunkId_chunkDoc_list.append(
                                        {"chunk_id": chunk_data["id"], "chunk_doc": chunk_doc}
                                    )
                                
                                return len(created_chunks), chunkId_chunkDoc_list
                            else:
                                logging.error(f"Failed to create chunks for {file_name}")
                        else:
                            logging.error(f"No pages could be loaded from {merged_file_path}")
                    else:
                        logging.error(f"File not found in any expected location for {file_name}")
                else:
                    logging.error(f"No Document node found for {file_name}")
            except Exception as e:
                logging.error(f"Emergency chunk creation failed for {file_name}: {e}")
            
            # Son çare: hata ver
            raise LLMGraphBuilderException(
                f"No chunks found for {file_name}. File may need to be re-uploaded. "
                f"Please re-upload the file to create chunks during upload phase."
            )

    else:
        chunkId_chunkDoc_list = []
        chunks = execute_graph_query(
            graph, QUERY_TO_GET_CHUNKS, params={"filename": file_name}
        )

        if chunks[0]["text"] is None or chunks[0]["text"] == "" or not chunks:
            raise LLMGraphBuilderException(
                f"Chunks are not created for {file_name}. Please re-upload file and try again."
            )
        else:
            for chunk in chunks:
                # Metadata'ya page_number dahil et
                metadata = {"id": chunk["id"], "position": chunk["position"]}
                if chunk.get("page_number") is not None:
                    metadata["page_number"] = chunk["page_number"]
                
                chunk_doc = Document(
                    page_content=chunk["text"],
                    metadata=metadata,
                )
                chunkId_chunkDoc_list.append(
                    {"chunk_id": chunk["id"], "chunk_doc": chunk_doc}
                )

            if retry_condition == START_FROM_LAST_PROCESSED_POSITION:
                logging.info(f"Retry : start_from_last_processed_position")
                starting_chunk = execute_graph_query(
                    graph,
                    QUERY_TO_GET_LAST_PROCESSED_CHUNK_POSITION,
                    params={"filename": file_name},
                )

                if starting_chunk and starting_chunk[0]["position"] < len(
                    chunkId_chunkDoc_list
                ):
                    return (
                        len(chunks),
                        chunkId_chunkDoc_list[starting_chunk[0]["position"] - 1 :],
                    )

                elif starting_chunk and starting_chunk[0]["position"] == len(
                    chunkId_chunkDoc_list
                ):
                    starting_chunk = execute_graph_query(
                        graph,
                        QUERY_TO_GET_LAST_PROCESSED_CHUNK_WITHOUT_ENTITY,
                        params={"filename": file_name},
                    )
                    return (
                        len(chunks),
                        chunkId_chunkDoc_list[starting_chunk[0]["position"] - 1 :],
                    )

                else:
                    raise LLMGraphBuilderException(
                        f"All chunks of file {file_name} are already processed. If you want to re-process, Please start from begnning"
                    )

            else:
                logging.info(
                    f"Retry : start_from_beginning with chunks {len(chunkId_chunkDoc_list)}"
                )
                return len(chunks), chunkId_chunkDoc_list


def get_source_list_from_graph(uri, userName, password, db_name=None):
    """
    Args:
      uri: URI of the graph to extract
      db_name: db_name is database name to connect to graph db
      userName: Username to use for graph creation ( if None will use username from config file )
      password: Password to use for graph creation ( if None will use password from config file )
      file: File object containing the PDF file to be used
      model: Type of model to use ('Diffbot'or'OpenAI GPT')
    Returns:
     Returns a list of sources that are in the database by querying the graph and
     sorting the list by the last updated date.
    """
    logging.info("Get existing files list from graph")
    graph = Neo4jGraph(url=uri, database=db_name, username=userName, password=password)
    graph_DB_dataAccess = graphDBdataAccess(graph)
    if not graph._driver._closed:
        logging.info(f"closing connection for sources_list api")
        graph._driver.close()
    return graph_DB_dataAccess.get_source_list()


def update_graph(graph):
    """
    Update the graph node with SIMILAR relationship where embedding scrore match
    """
    graph_DB_dataAccess = graphDBdataAccess(graph)
    graph_DB_dataAccess.update_KNN_graph()


def connection_check_and_get_vector_dimensions(graph, database):
    """
    Args:
      uri: URI of the graph to extract
      userName: Username to use for graph creation ( if None will use username from config file )
      password: Password to use for graph creation ( if None will use password from config file )
      db_name: db_name is database name to connect to graph db
    Returns:
     Returns a status of connection from NEO4j is success or failure
    """
    graph_DB_dataAccess = graphDBdataAccess(graph)
    return graph_DB_dataAccess.connection_check_and_get_vector_dimensions(database)


def merge_chunks_local(file_name, total_chunks, chunk_dir, merged_dir):
    logging.info(f"🔗 Starting merge process for: {file_name}, Total chunks: {total_chunks}")

    if not os.path.exists(merged_dir):
        os.mkdir(merged_dir)
        logging.info(f"📂 Created merged directory: {merged_dir}")
    
    logging.info(f"📁 Merged File Directory: {merged_dir}")
    merged_file_path = os.path.join(merged_dir, file_name)
    logging.info(f"📄 Target merged file path: {merged_file_path}")
    
    try:
        with open(merged_file_path, "wb") as write_stream:
            total_bytes_written = 0
            for i in range(1, total_chunks + 1):
                chunk_file_path = os.path.join(chunk_dir, f"{file_name}_part_{i}")
                logging.info(f"🔍 Processing chunk {i}: {chunk_file_path}")
                
                if not os.path.exists(chunk_file_path):
                    logging.error(f"❌ Chunk file missing: {chunk_file_path}")
                    raise FileNotFoundError(f"Chunk file {chunk_file_path} not found")
                
                chunk_size = os.path.getsize(chunk_file_path)
                logging.info(f"📦 Chunk {i} size: {chunk_size} bytes")
                
                with open(chunk_file_path, "rb") as chunk_file:
                    bytes_copied = shutil.copyfileobj(chunk_file, write_stream)
                    total_bytes_written += chunk_size
                    logging.info(f"✅ Chunk {i} merged successfully")
                
                os.unlink(chunk_file_path)  # Delete the individual chunk file after merging
                logging.info(f"🗑️ Chunk file deleted: {chunk_file_path}")
        
        logging.info(f"✅ All chunks merged successfully. Total bytes written: {total_bytes_written}")
        
        if not os.path.exists(merged_file_path):
            logging.error(f"❌ Merged file was not created: {merged_file_path}")
            raise FileNotFoundError(f"Merged file was not created: {merged_file_path}")

        file_size = os.path.getsize(merged_file_path)
        logging.info(f"📏 Final merged file size: {file_size} bytes")
        
        if file_size != total_bytes_written:
            logging.warning(f"⚠️ Size mismatch - Expected: {total_bytes_written}, Actual: {file_size}")
        
        return file_size
        
    except Exception as e:
        logging.error(f"❌ Error during merge process: {str(e)}")
        # Cleanup any partial file
        if os.path.exists(merged_file_path):
            os.unlink(merged_file_path)
            logging.info(f"🗑️ Cleaned up partial merged file: {merged_file_path}")
        raise e


def upload_file(
    graph,
    model,
    chunk,
    chunk_number: int,
    total_chunks: int,
    originalname,
    uri,
    chunk_dir,
    merged_dir,
    generate_embedding: str = None,
):
    # Dosya adını normalize et (Unicode consistency için)
    import unicodedata
    from src.utf8_utils import normalize_file_name
    
    # Model parametresi kontrolü - varsayılan değer ataması
    if not model or model.strip() == "":
        model = "openai_gpt_4o_mini"
        log_upload(f"⚠️ Model parametresi boş veya gelmedi, varsayılan model kullanılıyor: {model}", "warning")
    else:
        log_upload(f"✅ Model parametresi alındı: {model}")
    
    originalname = normalize_file_name(originalname)
    log_upload(f"📤 Upload started - File: {originalname}, Chunk: {chunk_number}/{total_chunks}")
    log_upload(f"⚙️ Upload config - Model: {model}, Generate Embedding: {generate_embedding}")
    log_upload(f"🔤 Normalized filename: {originalname} (bytes: {originalname.encode('utf-8')})")
    
    # Chunk boyutu kontrol et
    if hasattr(chunk, 'size'):
        chunk_size = chunk.size
        log_upload(f"📏 Chunk size: {chunk_size} bytes")
        if chunk_size == 0:
            log_upload(f"⚠️ Received empty chunk for {originalname}, chunk {chunk_number}/{total_chunks}", "warning")
    else:
        log_upload(f"📏 Chunk size information not available for {originalname}")

    gcs_file_cache = os.environ.get("GCS_FILE_CACHE")
    log_upload(f"☁️ GCS file cache: {gcs_file_cache}")
    
    # Normalize filename early to ensure consistency throughout upload process
    normalized_filename = normalize_file_name(originalname.strip() if isinstance(originalname, str) else originalname)
    log_upload(f"Upload: Original filename: '{originalname}' -> Normalized: '{normalized_filename}'")

    if gcs_file_cache == "True":
        folder_name = create_gcs_bucket_folder_name_hashed(uri, normalized_filename)
        log_upload(f"📁 Uploading chunk to GCS: {folder_name}")
        upload_file_to_gcs(
            chunk, chunk_number, normalized_filename, BUCKET_UPLOAD, folder_name
        )
    else:
        if not os.path.exists(chunk_dir):
            os.mkdir(chunk_dir)
            log_upload(f"📂 Created chunk directory: {chunk_dir}")

        chunk_file_path = os.path.join(chunk_dir, f"{normalized_filename}_part_{chunk_number}")
        log_upload(f"💾 Saving chunk to: {chunk_file_path}")

        with open(chunk_file_path, "wb") as chunk_file:
            chunk_content = chunk.file.read()
            content_size = len(chunk_content)
            chunk_file.write(chunk_content)
            log_upload(f"✅ Chunk {chunk_number} saved successfully, bytes written: {content_size}")
            
            if content_size == 0:
                log_upload(f"⚠️ Written chunk is empty for {originalname}, chunk {chunk_number}/{total_chunks}", "warning")
            else:
                log_upload(f"📊 Chunk content summary - First 100 chars: {chunk_content[:100] if content_size > 0 else 'EMPTY'}")

    if int(chunk_number) == int(total_chunks):
        log_upload(f"🔗 Last chunk received, starting file merge process for: {originalname}")
        # If this is the last chunk, merge all chunks into a single file
        if gcs_file_cache == "True":
            file_size = merge_file_gcs(
                BUCKET_UPLOAD, normalized_filename, folder_name, int(total_chunks)
            )
        else:
            file_size = merge_chunks_local(
                normalized_filename, int(total_chunks), chunk_dir, merged_dir
            )

        logging.info(f"✅ File merged successfully - Final size: {file_size} bytes")
        
        # ✨ ÖNCE: Upload öncesi otomatik temizlik yap (dosya varsa temizle)
        log_upload(f"🧹 Starting pre-upload cleanup check for: {normalized_filename}")
        graphDb_data_Access = graphDBdataAccess(graph)
        cleanup_result = graphDb_data_Access.auto_clean_existing_file_data(normalized_filename)
        if cleanup_result:
            log_upload(f"✅ Pre-upload cleanup completed successfully")
        else:
            log_upload(f"ℹ️ No cleanup needed or cleanup skipped")
        
        # Desteklenen belge formatları için hem text hem image extraction (tek seferde)
        merged_file_path = os.path.join(merged_dir, normalized_filename)
        doc_link = None
        page_images = []
        pages = []
        
        # Docling desteklenen formatlar: PDF, DOCX, PPTX, HTML, CSV, Markdown
        file_extension = normalized_filename.split(".")[-1].lower()
        docling_supported_formats = ["pdf", "docx", "pptx", "html", "csv", "md"]
        
        if file_extension in docling_supported_formats and os.path.exists(merged_file_path):
                try:
                    logging.info(f"🖼️ Starting combined content & image extraction for {file_extension.upper()}: {normalized_filename}")
                    
                    # Output klasör yapısını oluştur
                    document_dir, pdf_dir, images_dir = create_document_output_structure(
                        normalized_filename, "output"
                    )
                    
                    # Belgeyi ilgili klasöre kopyala
                    doc_copy_path = os.path.join(pdf_dir, normalized_filename)
                    shutil.copy2(merged_file_path, doc_copy_path)
                    logging.info(f"📄 Document copied to: {doc_copy_path}")
                    
                    # Tek seferde hem text hem image extraction
                    from src.document_sources.local_file import load_document_content
                    
                    # S3'te page image'lar var mı kontrol et
                    s3_bucket = os.environ.get("S3_BACKUP_BUCKET", "llm-graph-builder-backup")
                    aws_access_key_id = os.environ.get("AWS_ACCESS_KEY_ID")
                    aws_secret_access_key = os.environ.get("AWS_SECRET_ACCESS_KEY")
                    doc_name = Path(normalized_filename).stem
                    
                    existing_images_in_s3 = False
                    existing_image_names = []
                    
                    if s3_bucket and aws_access_key_id and aws_secret_access_key:
                        from src.document_sources.s3_upload_utils import check_document_images_exist_in_s3
                        existing_images_in_s3, existing_image_names = check_document_images_exist_in_s3(
                            doc_name, s3_bucket, aws_access_key_id, aws_secret_access_key
                        )
                    
                    if existing_images_in_s3:
                        # S3'te image'lar zaten var, tekrar generate etme
                        logging.info(f"📸 Page images already exist in S3 for {doc_name}, skipping generation")
                        generated_images = []
                        page_images = existing_image_names
                        
                        # Sadece text extraction yap
                        loader, encoding_flag, _ = load_document_content(merged_file_path, generate_images=False)
                        pages = loader.load()
                        logging.info(f"📖 {file_extension.upper()} processed: Text only (images exist in S3)")
                        
                    else:
                        # S3'te image'lar yok, generate et
                        logging.info(f"📸 No page images found in S3 for {doc_name}, generating new images")
                        
                        if file_extension == "pdf":
                            # PDF için: PyMuPDF image + DoclingLoader text (optimize edilmiş)
                            generated_images = generate_page_images_with_pymupdf(merged_file_path, images_dir)
                            loader, encoding_flag, _ = load_document_content(merged_file_path, generate_images=False)
                            pages = loader.load()
                            logging.info(f"📖 PDF processed: PyMuPDF images + Docling text")
                        else:
                            # Diğer formatlar için: Docling hem text hem image (tek çağrı)
                            loader, encoding_flag, generated_images = load_document_content(
                                merged_file_path, generate_images=True, output_dir=images_dir
                            )
                            pages = loader.load()
                            logging.info(f"📖 {file_extension.upper()} processed: Docling combined text+images")
                    
                    if generated_images:
                        logging.info(f"✅ Generated {len(generated_images)} page images")
                        
                        if s3_bucket and aws_access_key_id and aws_secret_access_key:
                            # S3 yapısı: documents/{doc_name}/ (PDF) ve documents/{doc_name}/images/ (images)
                            base_s3_prefix = f"documents/{doc_name}"
                            
                            # PDF'i documents/{doc_name}/ altına upload et
                            pdf_urls, pdf_failed = upload_files_to_s3(
                                [doc_copy_path],
                                s3_bucket,
                                base_s3_prefix,
                                aws_access_key_id,
                                aws_secret_access_key,
                                delete_local_after_upload=True
                            )
                            
                            # Image'leri documents/{doc_name}/images/ altına upload et
                            img_urls, img_failed = upload_files_to_s3(
                                generated_images,
                                s3_bucket,
                                f"{base_s3_prefix}/images",
                                aws_access_key_id,
                                aws_secret_access_key,
                                delete_local_after_upload=True
                            )
                            
                            uploaded_urls = pdf_urls + img_urls
                            failed_files = pdf_failed + img_failed
                            
                            if uploaded_urls:
                                logging.info(f"✅ Uploaded {len(uploaded_urls)} files to S3 (1 PDF + {len(img_urls)} images)")
                                
                                # Document link'i bul (PDF dosyası) - sadece dosya adı
                                doc_link = None
                                for url in pdf_urls:
                                    if url.endswith(f"/{normalized_filename}"):
                                        doc_link = os.path.basename(url)  # Sadece dosya adı
                                        break
                                
                                # Page image link'lerini kaydet - sadece dosya adları
                                page_images = []
                                for url in img_urls:
                                    page_images.append(os.path.basename(url))  # Sadece dosya adı
                                
                                logging.info(f"📄 Document file name: {doc_link}")
                                logging.info(f"🖼️ Page image file names: {len(page_images)} images")
                                
                                # Local output klasörünü temizle
                                cleanup_local_files(document_dir)
                            
                            if failed_files:
                                logging.warning(f"⚠️ Failed to upload {len(failed_files)} files to S3")
                        else:
                            logging.warning("⚠️ S3 credentials not configured, skipping S3 upload")
                            page_images = []
                    elif existing_images_in_s3:
                        # S3'ten mevcut image isimlerini kullan
                        logging.info(f"🔄 Using existing {len(page_images)} page images from S3")
                    else:
                        logging.warning(f"⚠️ No page images generated for: {normalized_filename}")
                        page_images = []
                        
                except Exception as image_error:
                    logging.error(f"❌ Combined content & image extraction failed for {normalized_filename}: {image_error}")
                    # Continue with normal processing even if extraction fails
        
        # Source node oluştur
        log_upload(f"Creating source node for file: {originalname} (size: {file_size} bytes)")
        file_extension = normalized_filename.split(".")[-1]
        obj_source_node = sourceNode()
        obj_source_node.file_name = normalized_filename  # Already normalized
        obj_source_node.file_type = file_extension
        obj_source_node.file_size = file_size
        obj_source_node.file_source = "local file"
        obj_source_node.model = model
        obj_source_node.created_at = datetime.now()
        obj_source_node.chunkNodeCount = 0
        obj_source_node.chunkRelCount = 0
        obj_source_node.entityNodeCount = 0
        obj_source_node.entityEntityRelCount = 0
        obj_source_node.communityNodeCount = 0
        obj_source_node.communityRelCount = 0
        # Upload sırasında başlangıç değerleri
        obj_source_node.total_chunks = 0
        obj_source_node.processed_chunk = 0
        obj_source_node.node_count = 0
        obj_source_node.relationship_count = 0
        obj_source_node.processing_time = 0
        
        # S3 document link'i ve page images'ı ekle
        if doc_link:
            obj_source_node.doc_link = doc_link
            log_upload(f"Document link added to source node: {doc_link}")
            logging.info(f"📄 Added doc_link to source node: {doc_link}")
        
        if page_images:
            obj_source_node.page_images = page_images
            log_upload(f"Added {len(page_images)} page images to source node")
            logging.info(f"🖼️ Added {len(page_images)} page_images to source node")
        
        # Desteklenen belge formatları için chunk node'ları da oluştur (sadece pages varsa)
        if file_extension.lower() in docling_supported_formats and pages:
            try:
                log_upload(f"Starting chunk creation for supported format: {file_extension}")
                logging.info(f"🔄 Creating chunk nodes for: {originalname} (using already extracted pages)")
                
                # Zaten extract edilmiş pages'leri kullan (tekrar extract etme)
                if pages:
                    # Chunk'ları oluştur
                    from src.create_chunks import CreateChunksofDocument
                    create_chunks_obj = CreateChunksofDocument(pages, graph)
                    
                    # Varsayılan chunk parametreleri
                    token_chunk_size = int(os.environ.get("CHUNK_SIZE", "1000"))
                    chunk_overlap = int(os.environ.get("CHUNK_OVERLAP", "200"))
                    
                    chunks = create_chunks_obj.split_file_into_chunks_recursive(
                        chunk_size=token_chunk_size, 
                        chunk_overlap=chunk_overlap
                    )
                    
                    if chunks:
                        # Chunk node'ları veritabanına kaydet (extract-compatible format)
                        from src.make_relationships import create_chunks_for_upload
                        import asyncio
                        
                        # generate_embedding kontrolü - varsayılan false (manuel embedding)
                        should_generate_embedding = generate_embedding and generate_embedding.lower() in ['true', '1', 'yes']
                        
                        # create_chunks_for_upload artık async, event loop içinde çalıştır
                        try:
                            loop = asyncio.get_event_loop()
                            if loop.is_running():
                                # Eğer loop zaten çalışıyorsa, thread pool'da çalıştır
                                import concurrent.futures
                                async def run_create_chunks():
                                    return await create_chunks_for_upload(
                                        graph=graph,
                                        chunks=chunks, 
                                        file_name=originalname,
                                        page_images=page_images if page_images else [],
                                        generate_embedding=should_generate_embedding
                                    )
                                with concurrent.futures.ThreadPoolExecutor() as executor:
                                    future = executor.submit(asyncio.run, run_create_chunks())
                                    chunkId_chunkDoc_list = future.result()
                            else:
                                chunkId_chunkDoc_list = loop.run_until_complete(create_chunks_for_upload(
                                    graph=graph,
                                    chunks=chunks, 
                                    file_name=originalname,
                                    page_images=page_images if page_images else [],
                                    generate_embedding=should_generate_embedding
                                ))
                        except RuntimeError:
                            # Event loop yoksa, yeni bir tane oluştur
                            chunkId_chunkDoc_list = asyncio.run(create_chunks_for_upload(
                                graph=graph,
                                chunks=chunks, 
                                file_name=originalname,
                                page_images=page_images if page_images else [],
                                generate_embedding=should_generate_embedding
                            ))
                        
                        # Source node'daki chunk sayısını güncelle
                        obj_source_node.chunkNodeCount = len(chunkId_chunkDoc_list)
                        obj_source_node.total_chunks = len(chunks)  # Toplam chunk sayısı
                        obj_source_node.processed_chunk = len(chunkId_chunkDoc_list)  # İşlenen chunk sayısı
                        
                        # Vector index oluştur/kontrol et (embedding varsa)
                        if should_generate_embedding:
                            try:
                                from src.make_relationships import create_chunk_vector_index
                                create_chunk_vector_index(graph)
                                log_upload(f"Vector index checked/created after chunk creation")
                                logging.info(f"✅ Vector index checked/created for embeddings")
                            except Exception as vector_error:
                                log_upload(f"Vector index creation warning: {vector_error}", "warning")
                                logging.warning(f"⚠️ Vector index creation warning: {vector_error}")
                        
                        if should_generate_embedding:
                            log_upload(f"Successfully created {len(chunkId_chunkDoc_list)} chunk nodes with embeddings")
                            logging.info(f"✅ Created {len(chunkId_chunkDoc_list)} chunk nodes with embeddings for: {originalname}")
                        else:
                            log_upload(f"Successfully created {len(chunkId_chunkDoc_list)} chunk nodes (embeddings will be created manually)")
                            logging.info(f"✅ Created {len(chunkId_chunkDoc_list)} chunk nodes for: {originalname}")
                            logging.info(f"ℹ️ Embedding oluşturma atlandı - manuel olarak /create_embeddings endpoint'i ile oluşturulacak")
                            logging.info(f"📊 Embedding'leri manuel olarak oluşturmak için /create_embeddings endpoint'ini kullanın")
                        
                        logging.info(f"📊 Upload created chunks ready for extract processing")
                    else:
                        log_upload(f"No chunks created during upload", "warning")
                        logging.warning(f"⚠️ No chunks created for: {originalname}")
                else:
                    log_upload(f"No pages available for chunking", "warning")
                    logging.warning(f"⚠️ No pages available for chunking: {originalname}")
                    
            except Exception as chunk_error:
                log_upload(f"Failed to create chunk nodes: {chunk_error}", "error")
                logging.error(f"❌ Failed to create chunk nodes for {originalname}: {chunk_error}")
                # Continue without chunk creation
        
        # Source node'u veritabanına kaydet (temizlik zaten yapıldı)
        graphDb_data_Access.create_source_node(obj_source_node, model=model)
        log_upload(f"Source node successfully created in database for: {originalname}")
        logging.info(f"📋 Source node created in database for: {originalname}")
        
        # Chunk'ları Document'e bağla (eğer chunk'lar oluşturulmuşsa)
        if file_extension.lower() in docling_supported_formats and pages:
            try:
                from src.make_relationships import link_chunks_to_document
                linked_count = link_chunks_to_document(graph, originalname)
                if linked_count > 0:
                    logging.info(f"🔗 Successfully linked {linked_count} relationships between chunks and Document")
                else:
                    logging.info(f"ℹ️ Chunks were already linked to Document")
            except Exception as link_error:
                logging.error(f"❌ Failed to link chunks to Document: {link_error}")
        
        # Dosyanın gerçekten merged_dir'de oluşturulup oluşturulmadığını kontrol et
        if not gcs_file_cache or gcs_file_cache != "True":
            if os.path.exists(merged_file_path):
                actual_file_size = os.path.getsize(merged_file_path)
                logging.info(f"✅ File verification successful - File exists at: {merged_file_path}, Size: {actual_file_size} bytes")
            else:
                logging.error(f"❌ File verification failed - File NOT found at: {merged_file_path}")
        
        return {
            "file_size": file_size,
            "file_name": normalized_filename,  # Return normalized filename
            "file_extension": file_extension,
            "doc_link": doc_link,
            "page_images": page_images,
            "page_images_count": len(page_images),
            "message": f"Chunk {chunk_number}/{total_chunks} saved",
        }
    logging.info(f"✅ Chunk {chunk_number}/{total_chunks} processed successfully")
    return f"Chunk {chunk_number}/{total_chunks} saved"


def get_labels_and_relationtypes(uri, userName, password, database):
    excluded_labels = {
        "Document",
        "Chunk",
        "_Bloom_Perspective_",
        "__Community__",
        "__Entity__",
        "Session",
        "Message",
    }
    excluded_relationships = {
        "NEXT_CHUNK",
        "_Bloom_Perspective_",
        "FIRST_CHUNK",
        "SIMILAR",
        "IN_COMMUNITY",
        "PARENT_COMMUNITY",
        "NEXT",
        "LAST_MESSAGE",
    }
    driver = get_graphDB_driver(uri, userName, password, database)
    triples = set()
    with driver.session(database=database) as session:
        result = session.run(
            """
           MATCH (n)-[r]->(m)
           RETURN DISTINCT labels(n) AS fromLabels, type(r) AS relType, labels(m) AS toLabels
       """
        )
        for record in result:
            from_labels = record["fromLabels"]
            to_labels = record["toLabels"]
            rel_type = record["relType"]
            from_label = next(
                (lbl for lbl in from_labels if lbl not in excluded_labels), None
            )
            to_label = next(
                (lbl for lbl in to_labels if lbl not in excluded_labels), None
            )
            if not from_label or not to_label:
                continue
            if rel_type == "PART_OF":
                if from_label == "Chunk" and to_label == "Document":
                    continue
            elif rel_type == "HAS_ENTITY":
                if from_label == "Chunk":
                    continue
            elif (
                from_label in excluded_labels
                or to_label in excluded_labels
                or rel_type in excluded_relationships
            ):
                continue
            triples.add(f"{from_label}-{rel_type}->{to_label}")
    return {"triplets": list(triples)}


def manually_cancelled_job(graph, filenames, source_types, merged_dir, uri):
    from src.utf8_utils import normalize_file_name

    filename_list = list(map(str.strip, json.loads(filenames)))
    source_types_list = list(map(str.strip, json.loads(source_types)))
    gcs_file_cache = os.environ.get("GCS_FILE_CACHE")

    for file_name, source_type in zip(filename_list, source_types_list):
        obj_source_node = sourceNode()
        # Normalize the filename before using it
        normalized_file_name = normalize_file_name(
            file_name.strip() if isinstance(file_name, str) else file_name
        )
        obj_source_node.file_name = normalized_file_name
        obj_source_node.is_cancelled = True
        obj_source_node.status = "Cancelled"
        obj_source_node.updated_at = datetime.now()
        graphDb_data_Access = graphDBdataAccess(graph)
        graphDb_data_Access.update_source_node(obj_source_node)
        count_response = graphDb_data_Access.update_node_relationship_count(normalized_file_name)
        obj_source_node = None
        merged_file_path = os.path.join(merged_dir, normalized_file_name)
        if source_type == "local file" and gcs_file_cache == "True":
            folder_name = create_gcs_bucket_folder_name_hashed(uri, normalized_file_name)
            delete_file_from_gcs(BUCKET_UPLOAD, folder_name, normalized_file_name)
        else:
            logging.info(
                f"Deleted File Path: {merged_file_path} and Deleted File Name : {normalized_file_name}"
            )
            delete_uploaded_local_file(merged_file_path, normalized_file_name)
    return "Cancelled the processing job successfully"


def populate_graph_schema_from_text(
    text, model, is_schema_description_checked, is_local_storage
):
    """_summary_

    Args:
        graph (Neo4Graph): Neo4jGraph connection object
        input_text (str): rendom text from PDF or user input.
        model (str): AI model to use extraction from text

    Returns:
        data (list): list of lebels and relationTypes
    """
    result = schema_extraction_from_text(
        text, model, is_schema_description_checked, is_local_storage
    )
    return result


def set_status_retry(graph, file_name, retry_condition):
    graphDb_data_Access = graphDBdataAccess(graph)
    obj_source_node = sourceNode()
    status = "Chunked"
    obj_source_node.file_name = normalize_file_name(
        file_name.strip() if isinstance(file_name, str) else file_name
    )
    obj_source_node.status = status
    obj_source_node.retry_condition = retry_condition
    obj_source_node.is_cancelled = False
    if (
        retry_condition == DELETE_ENTITIES_AND_START_FROM_BEGINNING
        or retry_condition == START_FROM_BEGINNING
    ):
        obj_source_node.processed_chunk = 0
    if retry_condition == DELETE_ENTITIES_AND_START_FROM_BEGINNING:
        execute_graph_query(
            graph, QUERY_TO_DELETE_EXISTING_ENTITIES, params={"filename": file_name}
        )
        obj_source_node.node_count = 0
        obj_source_node.relationship_count = 0
    logging.info(obj_source_node)
    graphDb_data_Access.update_source_node(obj_source_node)


def failed_file_process(uri, file_name, merged_file_path):
    gcs_file_cache = os.environ.get("GCS_FILE_CACHE")
    if gcs_file_cache == "True":
        folder_name = create_gcs_bucket_folder_name_hashed(uri, file_name)
        copy_failed_file(BUCKET_UPLOAD, BUCKET_FAILED_FILE, folder_name, file_name)
        time.sleep(5)
        delete_file_from_gcs(BUCKET_UPLOAD, folder_name, file_name)
    else:
        # Dosya zaten silinmişse tekrar silme işlemi yapmaya gerek yok
        if os.path.exists(merged_file_path):
            logging.info(
                f"Deleted File Path: {merged_file_path} and Deleted File Name : {file_name}"
            )
            delete_uploaded_local_file(merged_file_path, file_name)
        else:
            logging.info(f"File {file_name} already deleted, skipping cleanup")
