from fastapi import FastAPI, File, UploadFile, Form, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi_health import health
from fastapi.middleware.cors import CORSMiddleware
from src.main import *
from src.QA_integration import QA_RAG, QA_RAG_stream, clear_chat_history
from src.intelligent_agent import IntelligentAgent
from src.alternative_agent import AlternativeAgent
from src.qa_based_entity_extractor import QABasedEntityExtractor, create_domain_specific_questions
from src.llm import detect_document_domain
from src.shared.common_fn import *
from src.shared.llm_graph_builder_exception import LLMGraphBuilderException
import uvicorn
import asyncio
import base64
from langserve import add_routes
from langchain_google_vertexai import ChatVertexAI
from src.api_response import create_api_response
from src.graphDB_dataAccess import graphDBdataAccess
from src.graph_query import get_graph_results,get_chunktext_results,visualize_schema
from src.chunkid_entities import get_entities_from_chunkids
from src.post_processing import create_vector_fulltext_indexes, create_entity_embedding, graph_schema_consolidation
from src.document_analytics import get_person_policy_analytics, get_company_analytics, get_document_relationship_stats, search_person_documents
from sse_starlette.sse import EventSourceResponse
from src.communities import create_communities
from src.neighbours import get_neighbour_nodes
import json
from typing import List, Optional
from google.oauth2.credentials import Credentials
import os
import re
from urllib.parse import unquote
from src.utf8_utils import normalize_file_name
from src.logger import CustomLogger
from datetime import datetime, timezone
import time
import gc
from Secweb.XContentTypeOptions import XContentTypeOptions
from Secweb.XFrameOptions import XFrame
from fastapi.middleware.gzip import GZipMiddleware
from src.ragas_eval import *
from starlette.types import ASGIApp, Receive, Scope, Send
from langchain_neo4j import Neo4jGraph
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from dotenv import load_dotenv
import tempfile
from pathlib import Path
from src.intelligent_agent import IntelligentAgent
from src.alternative_agent import AlternativeAgent
import time
import json
import logging

# HTTP Request Logging Middleware for OpenTelemetry
class HTTPLoggingMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app
        self.logger = logging.getLogger('http_requests')

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        start_time = time.time()
        
        # Store response info
        response_status = 200
        
        async def send_wrapper(message):
            nonlocal response_status
            if message["type"] == "http.response.start":
                response_status = message.get("status", 200)
            await send(message)

        await self.app(scope, receive, send_wrapper)
        
        # Calculate duration
        duration = time.time() - start_time
        
        # Log HTTP request with structured data
        method = scope.get("method", "GET")
        path = scope.get("path", "/")
        client_ip = scope.get("client", ["unknown", 0])[0]
        
        # Create log message
        log_message = f"🌐 HTTP {method} {path} - {response_status} ({duration:.3f}s)"
        
        self.logger.info(log_message, extra={
            'component': 'http_server',
            'operation': 'http_request',
            'method': method,
            'path': path,
            'status_code': response_status,
            'duration_ms': round(duration * 1000, 2),
            'client_ip': client_ip,
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S.%3fZ', time.gmtime())
        })
try:
    from docling.document_converter import DocumentConverter
    DOCLING_AVAILABLE = True
except ImportError:
    DOCLING_AVAILABLE = False
    
from docling_core.types.doc import ImageRefMode, DocItemLabel
from docling_core.types.doc.document import  DEFAULT_EXPORT_LABELS
load_dotenv(override=True)

from pathlib import Path
from typing import Dict, List
import requests
import shutil
import subprocess
from PyPDF2 import PdfReader
from pdf2image import convert_from_path

logger = CustomLogger()
CHUNK_DIR = os.path.join(os.path.dirname(__file__), "chunks")
MERGED_DIR = os.path.join(os.path.dirname(__file__), "merged_files")
MARKDOWN_CACHE_DIR = os.path.join(os.path.dirname(__file__), "markdown_cache")

def sanitize_filename(filename):
   """
   Sanitize the user-provided filename to prevent directory traversal and remove unsafe characters.
   Also normalizes UTF-8 encoding for consistency.
   """
   # Remove path separators and collapse redundant separators
   filename = os.path.basename(filename)
   filename = os.path.normpath(filename)
   
   # Normalize UTF-8 encoding for consistency
   filename = normalize_file_name(filename)
   
   return filename

def create_markdown_cache_key(filename, file_size):
    """
    Dosya adı ve boyutundan markdown cache key oluştur
    """
    # Dosya adını güvenli hale getir (özel karakterleri _ ile değiştir)
    safe_name = re.sub(r'[^\w\.-]', '_', filename)
    # Dosya uzantısını .md yap
    name_without_ext = os.path.splitext(safe_name)[0]
    cache_key = f"{name_without_ext}_{file_size}.md"
    return cache_key

def get_cached_markdown(filename, file_size):
    """
    Cache'den markdown içeriği oku (varsa)
    """
    try:
        cache_key = create_markdown_cache_key(filename, file_size)
        cache_path = os.path.join(MARKDOWN_CACHE_DIR, cache_key)
        
        if os.path.exists(cache_path):
            with open(cache_path, 'r', encoding='utf-8') as f:
                return f.read()
    except Exception as e:
        logging.warning(f"Cache okuma hatası: {e}")
    
    return None

def save_markdown_to_cache(filename, file_size, markdown_content):
    """
    Markdown içeriğini cache'e kaydet
    """
    try:
        # Cache klasörünü oluştur
        os.makedirs(MARKDOWN_CACHE_DIR, exist_ok=True)
        
        cache_key = create_markdown_cache_key(filename, file_size)
        cache_path = os.path.join(MARKDOWN_CACHE_DIR, cache_key)
        
        with open(cache_path, 'w', encoding='utf-8') as f:
            f.write(markdown_content)
            
        logging.info(f"Markdown cache'e kaydedildi: {cache_key}")
        return cache_path
    except Exception as e:
        logging.warning(f"Cache kaydetme hatası: {e}")
        return None

def validate_file_path(directory, filename):
   """
   Construct the full file path and ensure it is within the specified directory.
   """
   file_path = os.path.join(directory, filename)
   abs_directory = os.path.abspath(directory)
   abs_file_path = os.path.abspath(file_path)
   # Ensure the file path starts with the intended directory path
   if not abs_file_path.startswith(abs_directory):
       raise ValueError("Invalid file path")
   return abs_file_path

def healthy_condition():
    output = {"healthy": True}
    return output

def healthy():
    return True

def sick():
    return False
class CustomGZipMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        paths: List[str],
        minimum_size: int = 1000,
        compresslevel: int = 5
    ):
        self.app = app
        self.paths = paths
        self.minimum_size = minimum_size
        self.compresslevel = compresslevel
    
    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
 
        path = scope["path"]
        should_compress = any(path.startswith(gzip_path) for gzip_path in self.paths)
        
        if not should_compress:
            return await self.app(scope, receive, send)
        
        gzip_middleware = GZipMiddleware(
            app=self.app,
            minimum_size=self.minimum_size,
            compresslevel=self.compresslevel
        )
        await gzip_middleware(scope, receive, send)
        

def convert_result_to_base64(
    result: Dict[str, List[Dict[str, str]]]
) -> Dict[str, List[Dict[str, str]]]:
    """
    TypeScript convertResultToBase64 fonksiyonunun Python versiyonu.
    result: {
        "attachmentName": [
            {"fileName": "example.png", "path": "/path/to/example.png"}
        ]
    }
    return: {
        "attachmentName": [
            {"fileName": "example.png", "base64": "iVBORw0KGgoAAAANSUhEUg..."}
        ]
    }
    """
    base64_result: Dict[str, List[Dict[str, str]]] = {}

    for attachment_name, images in result.items():
        base64_result[attachment_name] = []
        for image in images:
            file_name = image["fileName"]
            path = Path(image["path"])
            with open(path, "rb") as f:
                file_bytes = f.read()
            encoded = base64.b64encode(file_bytes).decode("utf-8")
            base64_result[attachment_name].append({"fileName": file_name, "base64": encoded})

    return base64_result

# Klasör ayarları
TEMP_FOLDER = os.getenv("FILE_TEMP_FOLDER", "./temp/temp/")
PDF_TEMP_FOLDER = os.getenv("PDF_CONVERT_TEMP_FOLDER", "./temp/pdf_temp/")
IMAGE_OUTPUT_FOLDER = os.getenv("IMAGE_OUTPUT_FOLDER", "./temp/images/")

def ensure_folders():
    for folder in [TEMP_FOLDER, PDF_TEMP_FOLDER, IMAGE_OUTPUT_FOLDER]:
        Path(folder).mkdir(parents=True, exist_ok=True)

def download_file(url: str, output_path: str) -> str:
    response = requests.get(url, stream=True)
    with open(output_path, "wb") as f:
        shutil.copyfileobj(response.raw, f)
    return output_path

def convert_to_pdf(file_path: str, filename: str) -> str:
    """ LibreOffice kullanarak dosyayı PDF'e çevirir """
    output_path = os.path.join(PDF_TEMP_FOLDER, filename + ".pdf")
    subprocess.run([
        "libreoffice", "--headless", "--convert-to", "pdf", 
        "--outdir", PDF_TEMP_FOLDER, file_path
    ], check=True)
    return output_path

def get_pdf_page_count(pdf_path: str) -> int:
    reader = PdfReader(pdf_path)
    return len(reader.pages)

def pdf_to_images(pdf_path: str, output_base_name: str) -> list[str]:
    """ PDF sayfalarını PNG'e çevirir """
    images = convert_from_path(
        pdf_path,
        dpi=200,
        output_folder=IMAGE_OUTPUT_FOLDER,
        output_file=output_base_name,
        fmt="png",
        size=(1200, 1600)
    )
    image_paths = []
    for i, img in enumerate(images, start=1):
        output_path = os.path.join(IMAGE_OUTPUT_FOLDER, f"{output_base_name}_sayfa{i}.png")
        img.save(output_path, "PNG")
        image_paths.append(output_path)
    return image_paths

# def handle_attachments(
#     attachments: Dict[str, List[Dict[str, str]]]
# ) -> Dict[str, List[Dict[str, str]]]:
#     """
#     attachments: {
#         "filename.docx": [
#             {"fileName": "filename.docx", "downloadUrl": "http://...", "fileType": "docx"}
#         ]
#     }

#     return: {
#         "filename.docx": [
#             {"fileName": "filename_1.png", "path": "./images/filename_1.png"}
#         ]
#     }
#     """

#     ensure_folders()
#     result: Dict[str, List[Dict[str, str]]] = {}

#     for attachment_name, items in attachments.items():
#         for attachment in items:
#             file_name = attachment["fileName"]
#             file_type = attachment["fileType"].lower()
#             download_url = attachment["downloadUrl"]

#             filename_without_ext = Path(file_name).stem
#             temp_file_path = os.path.join(TEMP_FOLDER, file_name)

#             # 1. Dosyayı indir
#             download_file(download_url, temp_file_path)

#             # 2. PDF değilse dönüştür
#             if file_type != "pdf":
#                 pdf_path = convert_to_pdf(temp_file_path, filename_without_ext)
#             else:
#                 pdf_path = temp_file_path

#             # 3. PDF sayfalarını resme çevir
#             images = pdf_to_images(pdf_path, filename_without_ext)

#             # 4. Çıktı hazırlama
#             images_info = [{"fileName": Path(p).name, "path": p} for p in images]

#             # 🔧 fix: aynı attachment için tek key altında topla
#             if attachment_name not in result:
#                 result[attachment_name] = []
#             result[attachment_name].extend(images_info)

#     return result

def handle_attachments(
    attachments: Dict[str, List[Dict[str, str]]]
) -> Dict[str, List[Dict[str, str]]]:
    """
    attachments: {
        "filename.docx": [
            {"fileName": "filename.docx", "downloadUrl": "http://...", "fileType": "docx"}
        ]
    }

    return: {
        "filename.docx": [
            {"fileName": "filename_1.png", "path": "./images/filename_1.png"}
        ]
    }
    """

    ensure_folders()
    result: Dict[str, List[Dict[str, str]]] = {}

    for attachment_name, items in attachments.items():
        for attachment in items:
            file_name = attachment["fileName"]
            file_type = attachment["fileType"].lower()
            download_url = attachment["downloadUrl"]

            filename_without_ext = Path(file_name).stem
            temp_file_path = os.path.join(TEMP_FOLDER, file_name)

            # 1. Dosyayı indir
            download_file(download_url, temp_file_path)

            # 2. PDF değilse dönüştür
            if file_type != "pdf":
                pdf_path = convert_to_pdf(temp_file_path, filename_without_ext)
            else:
                pdf_path = temp_file_path

            # 3. PDF sayfalarını resme çevir
            # images = pdf_to_images(pdf_path, filename_without_ext)

            # 4. Çıktı hazırlama
            images_info = [{"fileName": filename_without_ext, "path": pdf_path}]

            # 🔧 fix: aynı attachment için tek key altında topla
            if attachment_name not in result:
                result[attachment_name] = []
            result[attachment_name].extend(images_info)

    return result


class UTF8JSONResponse:
    """UTF-8 JSON Response Middleware"""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            async def send_wrapper(message):
                if message["type"] == "http.response.start":
                    headers = dict(message.get("headers", []))
                    # Content-Type header'ını UTF-8 ile güncelle
                    for key, value in headers.items():
                        if key == b"content-type" and value.startswith(b"application/json"):
                            headers[key] = b"application/json; charset=utf-8"
                    message["headers"] = list(headers.items())
                await send(message)
            await self.app(scope, receive, send_wrapper)
        else:
            await self.app(scope, receive, send)

app = FastAPI()

# Add HTTP logging middleware for OpenTelemetry integration
app.add_middleware(HTTPLoggingMiddleware)

app.add_middleware(UTF8JSONResponse)
app.add_middleware(XContentTypeOptions)
app.add_middleware(XFrame, Option={'X-Frame-Options': 'DENY'})
app.add_middleware(CustomGZipMiddleware, minimum_size=1000, compresslevel=5,paths=["/sources_list","/url/scan","/extract","/chat_bot","/chat_bot_stream","/chunk_entities","/get_neighbours","/graph_query","/schema","/populate_graph_schema","/get_unconnected_nodes_list","/get_duplicate_nodes","/fetch_chunktext","/schema_visualization"])
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SessionMiddleware, secret_key=os.urandom(24))

# S3 configuration for file serving
S3_BACKUP_BUCKET = os.environ.get("S3_BACKUP_BUCKET", "llm-graph-builder-backup")
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")

is_gemini_enabled = os.environ.get("GEMINI_ENABLED", "False").lower() in ("true", "1", "yes")
if is_gemini_enabled:
    add_routes(app,ChatVertexAI(), path="/vertexai")

app.add_api_route("/health", health([healthy_condition, healthy]))


@app.get("/files/{file_name}")
async def serve_document_file(file_name: str):
    """
    S3'ten document dosyalarını serve eder.
    """
    try:
        if not S3_BACKUP_BUCKET or not AWS_ACCESS_KEY_ID or not AWS_SECRET_ACCESS_KEY:
            raise HTTPException(status_code=503, detail="S3 configuration not available")
        
        # S3 key'ini tahmin et
        from pathlib import Path
        doc_name = Path(file_name).stem
        s3_key = f"documents/{doc_name}/{file_name}"
        
        # Presigned URL oluştur
        from src.document_sources.s3_upload_utils import generate_s3_presigned_url
        presigned_url = generate_s3_presigned_url(
            S3_BACKUP_BUCKET, s3_key, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, expiration=3600
        )
        
        if not presigned_url:
            raise HTTPException(status_code=404, detail=f"File not found: {file_name}")
        
        # Redirect to presigned URL
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=presigned_url, status_code=302)
        
    except Exception as e:
        logging.error(f"Error serving document file {file_name}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/images/{image_name}")
async def serve_page_image(image_name: str):
    """
    S3'ten page image dosyalarını serve eder.
    """
    try:
        if not S3_BACKUP_BUCKET or not AWS_ACCESS_KEY_ID or not AWS_SECRET_ACCESS_KEY:
            raise HTTPException(status_code=503, detail="S3 configuration not available")
        
        # S3 key'ini tahmin et (image name'den document adını çıkar)
        # Format: "doc_name_page_001.png"
        import re
        match = re.match(r'(.+)_page_\d+\.png$', image_name)
        if not match:
            raise HTTPException(status_code=400, detail="Invalid image name format")
        
        doc_name = match.group(1)
        s3_key = f"documents/{doc_name}/{image_name}"
        
        # Presigned URL oluştur
        from src.document_sources.s3_upload_utils import generate_s3_presigned_url
        presigned_url = generate_s3_presigned_url(
            S3_BACKUP_BUCKET, s3_key, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, expiration=3600
        )
        
        if not presigned_url:
            raise HTTPException(status_code=404, detail=f"Image not found: {image_name}")
        
        # Redirect to presigned URL
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=presigned_url, status_code=302)
        
    except Exception as e:
        logging.error(f"Error serving page image {image_name}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/url/scan")
async def create_source_knowledge_graph_url(
    uri=Form(None),
    userName=Form(None),
    password=Form(None),
    source_url=Form(None),
    database=Form(None),
    aws_access_key_id=Form(None),
    aws_secret_access_key=Form(None),
    wiki_query=Form(None),
    model=Form(),
    gcs_bucket_name=Form(None),
    gcs_bucket_folder=Form(None),
    source_type=Form(None),
    gcs_project_id=Form(None),
    access_token=Form(None),
    email=Form(None)
    ):
    
    try:
        start = time.time()
        if source_url is not None:
            source = source_url
        else:
            source = wiki_query
            
        graph = create_graph_database_connection(uri, userName, password, database)
        if source_type == 's3 bucket' and aws_access_key_id and aws_secret_access_key:
            lst_file_name,success_count,failed_count = await asyncio.to_thread(create_source_node_graph_url_s3,graph, model, source_url, aws_access_key_id, aws_secret_access_key, source_type
            )
        elif source_type == 'gcs bucket':
            lst_file_name,success_count,failed_count = await asyncio.to_thread(create_source_node_graph_url_gcs, graph, model, gcs_project_id, gcs_bucket_name, gcs_bucket_folder, source_type, Credentials(access_token)
            )
        elif source_type == 'web-url':
            lst_file_name,success_count,failed_count = await asyncio.to_thread(create_source_node_graph_web_url,graph, model, source_url, source_type
            )  
        elif source_type == 'youtube':
            lst_file_name,success_count,failed_count = await asyncio.to_thread(create_source_node_graph_url_youtube,graph, model, source_url, source_type
            )
        elif source_type == 'Wikipedia':
            lst_file_name,success_count,failed_count = await asyncio.to_thread(create_source_node_graph_url_wikipedia,graph, model, wiki_query, source_type
            )
        else:
            return create_api_response('Failed',message='source_type is other than accepted source')

        message = f"Source Node created successfully for source type: {source_type} and source: {source}"
        end = time.time()
        elapsed_time = end - start
        json_obj = {'api_name':'url_scan','db_url':uri,'url_scanned_file':lst_file_name, 'source_url':source_url, 'wiki_query':wiki_query, 'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{elapsed_time:.2f}','userName':userName, 'database':database, 'aws_access_key_id':aws_access_key_id,
                            'model':model, 'gcs_bucket_name':gcs_bucket_name, 'gcs_bucket_folder':gcs_bucket_folder, 'source_type':source_type,
                            'gcs_project_id':gcs_project_id, 'logging_time': formatted_time(datetime.now(timezone.utc)),'email':email}
        logger.log_struct(json_obj, "INFO")
        result ={'elapsed_api_time' : f'{elapsed_time:.2f}'}
        return create_api_response("Success",message=message,success_count=success_count,failed_count=failed_count,file_name=lst_file_name,data=result)
    except LLMGraphBuilderException as e:
        error_message = str(e)
        message = f" Unable to create source node for source type: {source_type} and source: {source}"
        # Set the status "Success" becuase we are treating these error already handled by application as like custom errors.
        json_obj = {'error_message':error_message, 'status':'Success','db_url':uri, 'userName':userName, 'database':database,'success_count':1, 'source_type': source_type, 'source_url':source_url, 'wiki_query':wiki_query, 'logging_time': formatted_time(datetime.now(timezone.utc)),'email':email}
        logger.log_struct(json_obj, "INFO")
        logging.exception(f'File Failed in upload: {e}')
        return create_api_response('Failed',message=message + error_message[:80],error=error_message,file_source=source_type)
    except Exception as e:
        error_message = str(e)
        message = f" Unable to create source node for source type: {source_type} and source: {source}"
        json_obj = {'error_message':error_message, 'status':'Failed','db_url':uri, 'userName':userName, 'database':database,'failed_count':1, 'source_type': source_type, 'source_url':source_url, 'wiki_query':wiki_query, 'logging_time': formatted_time(datetime.now(timezone.utc)),'email':email}
        logger.log_struct(json_obj, "ERROR")
        logging.exception(f'Exception Stack trace upload:{e}')
        return create_api_response('Failed',message=message + error_message[:80],error=error_message,file_source=source_type)
    finally:
        gc.collect()

@app.post("/extract")
async def extract_knowledge_graph_from_file(
    uri=Form(None),
    userName=Form(None),
    password=Form(None),
    model=Form(),
    database=Form(None),
    source_url=Form(None),
    aws_access_key_id=Form(None),
    aws_secret_access_key=Form(None),
    wiki_query=Form(None),
    gcs_project_id=Form(None),
    gcs_bucket_name=Form(None),
    gcs_bucket_folder=Form(None),
    gcs_blob_filename=Form(None),
    source_type=Form(None),
    file_name=Form(None),
    allowedNodes=Form(None),
    allowedRelationship=Form(None),
    token_chunk_size: Optional[int] = Form(None),
    chunk_overlap: Optional[int] = Form(None),
    chunks_to_combine: Optional[int] = Form(None),
    language=Form(None),
    access_token=Form(None),
    retry_condition=Form(None),
    additional_instructions=Form(None),
    # Sayfa sınırlandırma parametresi
    max_pages: str = Form(None),  # String olarak al, sonra validate et
    # Post-processing parametreleri
    enable_post_processing=Form(False),
    post_processing_rules=Form(None),  # JSON array: [{"sourceNodeType":"Year","targetNodeType":"Document","relationshipType":"HAS_YEAR","removeExistingRelationships":false}]
    # Entity Promotion parametreleri
    enable_entity_promotion=Form(True),  # Default olarak aktif
    entity_promotion_rules=Form(None),   # JSON array: ["Address", "Company", "Person", "Phone", "Email"]
    email=Form(None)
):
    """
    Calls 'extract_graph_from_file' in a new thread to create Neo4jGraph from a
    PDF file based on the model.

    Args:
          uri: URI of the graph to extract
          userName: Username to use for graph creation
          password: Password to use for graph creation
          file: File object containing the PDF file
          model: Type of model to use ('Diffbot'or'OpenAI GPT')

    Returns:
          Nodes and Relations created in Neo4j databse for the pdf file
    """
    try:
        start_time = time.time()
        
        # max_pages validation - undefined string'i None'a çevir
        validated_max_pages = None
        if max_pages is not None and max_pages.strip() not in ['', 'undefined', 'null']:
            try:
                validated_max_pages = int(max_pages)
                if validated_max_pages <= 0:
                    validated_max_pages = None
                    logging.info(f"ℹ️ max_pages değeri sıfır veya negatif, None olarak ayarlandı")
            except (ValueError, TypeError) as e:
                logging.warning(f"⚠️ max_pages değeri geçersiz '{max_pages}', None olarak ayarlandı: {e}")
                validated_max_pages = None
        
        logging.info(f"📊 max_pages validation: '{max_pages}' -> {validated_max_pages}")
        
        graph = create_graph_database_connection(uri, userName, password, database)   
        graphDb_data_Access = graphDBdataAccess(graph)
        if source_type == 'local file':
            file_name = sanitize_filename(file_name)
            merged_file_path = validate_file_path(MERGED_DIR, file_name)
            
            # Debug loglama: Dosya yolu ve varlık kontrolü
            logging.info(f"🔍 DEBUG - Original file_name: {file_name}")
            logging.info(f"🔍 DEBUG - Sanitized file_name: {file_name}")
            logging.info(f"🔍 DEBUG - MERGED_DIR: {MERGED_DIR}")
            logging.info(f"🔍 DEBUG - Constructed merged_file_path: {merged_file_path}")
            logging.info(f"🔍 DEBUG - File exists check: {os.path.exists(merged_file_path)}")
            
            # Merged files klasöründeki tüm dosyaları listele
            if os.path.exists(MERGED_DIR):
                files_in_dir = os.listdir(MERGED_DIR)
                logging.info(f"🔍 DEBUG - Files in {MERGED_DIR}: {files_in_dir}")
                
                # Dosya adı karşılaştırması
                for existing_file in files_in_dir:
                    if existing_file == file_name:
                        logging.info(f"✅ DEBUG - Exact match found: {existing_file}")
                    else:
                        logging.info(f"❌ DEBUG - No match: '{existing_file}' != '{file_name}'")
                        logging.info(f"🔍 DEBUG - Bytes comparison: {existing_file.encode('utf-8')} vs {file_name.encode('utf-8')}")
            
            # Dosya işleme başlamadan önce dosyanın varlığını kontrol et
            if not os.path.exists(merged_file_path):
                # Unicode normalizasyon farklılıkları için alternatif dosya adlarını dene
                logging.warning(f"File not found with NFC normalization, trying NFD normalization")
                
                import unicodedata
                # NFD normalizasyonu dene (Decomposed)
                file_name_nfd = unicodedata.normalize('NFD', file_name)
                merged_file_path_nfd = validate_file_path(MERGED_DIR, file_name_nfd)
                
                logging.info(f"🔍 DEBUG - Trying NFD normalized file_name: {file_name_nfd}")
                logging.info(f"🔍 DEBUG - NFD file path: {merged_file_path_nfd}")
                logging.info(f"🔍 DEBUG - NFD file exists: {os.path.exists(merged_file_path_nfd)}")
                
                if os.path.exists(merged_file_path_nfd):
                    logging.info(f"✅ Found file with NFD normalization: {merged_file_path_nfd}")
                    merged_file_path = merged_file_path_nfd
                    file_name = file_name_nfd
                else:
                    # Her iki normalizasyon da başarısız, dosya gerçekten yok
                    logging.warning(f"File {file_name} not found at {merged_file_path} - may have been deleted")
                    raise LLMGraphBuilderException(f"File {file_name} is no longer available for processing")
            
            uri_latency, result = await extract_graph_from_file_local_file(uri, userName, password, database, model, merged_file_path, file_name, allowedNodes, allowedRelationship, token_chunk_size, chunk_overlap, chunks_to_combine, retry_condition, additional_instructions, enable_post_processing, post_processing_rules, validated_max_pages)

        elif source_type == 's3 bucket' and source_url:
            uri_latency, result = await extract_graph_from_file_s3(uri, userName, password, database, model, source_url, aws_access_key_id, aws_secret_access_key, file_name, allowedNodes, allowedRelationship, token_chunk_size, chunk_overlap, chunks_to_combine, retry_condition, additional_instructions)
        
        elif source_type == 'web-url':
            uri_latency, result = await extract_graph_from_web_page(uri, userName, password, database, model, source_url, file_name, allowedNodes, allowedRelationship, token_chunk_size, chunk_overlap, chunks_to_combine, retry_condition, additional_instructions)

        elif source_type == 'youtube' and source_url:
            uri_latency, result = await extract_graph_from_file_youtube(uri, userName, password, database, model, source_url, file_name, allowedNodes, allowedRelationship, token_chunk_size, chunk_overlap, chunks_to_combine, retry_condition, additional_instructions)

        elif source_type == 'Wikipedia' and wiki_query:
            uri_latency, result = await extract_graph_from_file_Wikipedia(uri, userName, password, database, model, wiki_query, language, file_name, allowedNodes, allowedRelationship, token_chunk_size, chunk_overlap, chunks_to_combine, retry_condition, additional_instructions)

        elif source_type == 'gcs bucket' and gcs_bucket_name:
            uri_latency, result = await extract_graph_from_file_gcs(uri, userName, password, database, model, gcs_project_id, gcs_bucket_name, gcs_bucket_folder, gcs_blob_filename, access_token, file_name, allowedNodes, allowedRelationship, token_chunk_size, chunk_overlap, chunks_to_combine, retry_condition, additional_instructions)
        else:
            return create_api_response('Failed',message='source_type is other than accepted source')
        extract_api_time = time.time() - start_time
        if result is not None:
            logging.info("Going for counting nodes and relationships in extract")
            count_node_time = time.time()
            graph = create_graph_database_connection(uri, userName, password, database)   
            graphDb_data_Access = graphDBdataAccess(graph)
            # Thread'e taşı - blocking işlem
            count_response = await asyncio.to_thread(graphDb_data_Access.update_node_relationship_count, file_name)
            logging.info("Nodes and Relationship Counts updated")
            
            # Yeni yüklenen document için document-to-document ilişkilerini otomatik oluştur
            # try:
            #     from src.make_relationships import create_document_relationships
            #     doc_relationships_start = time.time()
            #     doc_connections = await asyncio.to_thread(create_document_relationships, graph, file_name)
            #     doc_relationships_end = time.time()
            #     logging.info(f"Document relationships created for {file_name}: {doc_connections} in {doc_relationships_end - doc_relationships_start:.2f} seconds")
            #     result['document_relationships'] = doc_connections
            # except Exception as doc_rel_error:
            #     logging.error(f"Error creating document relationships for {file_name}: {doc_rel_error}")
            #     result['document_relationships'] = {'error': str(doc_rel_error)}
            
            if count_response :
                result['chunkNodeCount'] = count_response[file_name].get('chunkNodeCount',"0")
                result['chunkRelCount'] =  count_response[file_name].get('chunkRelCount',"0")
                result['entityNodeCount']=  count_response[file_name].get('entityNodeCount',"0")
                result['entityEntityRelCount']=  count_response[file_name].get('entityEntityRelCount',"0")
                result['communityNodeCount']=  count_response[file_name].get('communityNodeCount',"0")
                result['communityRelCount']= count_response[file_name].get('communityRelCount',"0")
                result['nodeCount'] = count_response[file_name].get('nodeCount',"0")
                result['relationshipCount']  = count_response[file_name].get('relationshipCount',"0")
                logging.info(f"counting completed in {(time.time()-count_node_time):.2f}")
            
            # Otomatik post-processing (eğer istenirse)
            if enable_post_processing and post_processing_rules:
                try:
                    logging.info(f"Otomatik post-processing başlıyor: {file_name}")
                    
                    # JSON string'i parse et
                    if isinstance(post_processing_rules, str):
                        rules_list = json.loads(post_processing_rules)
                    else:
                        rules_list = post_processing_rules
                    
                    logging.info(f"Post-processing kuralları: {rules_list}")
                    
                    # Post-processing'i çalıştır - sadece bu dosya için
                    from src.llm import apply_dynamic_entity_post_processing
                    post_processing_start_time = time.time()
                    post_processing_result = await asyncio.to_thread(
                        apply_dynamic_entity_post_processing, 
                        graph, 
                        rules_list,
                        target_file_names=[file_name]
                    )
                    post_processing_end_time = time.time()
                    
                    logging.info(f"Otomatik post-processing tamamlandı: {post_processing_end_time - post_processing_start_time:.2f} saniye")
                    
                    # Post-processing sonuçlarını result'a ekle
                    result['post_processing'] = {
                        'enabled': True,
                        'rules_applied': len(rules_list),
                        'processed_entities': post_processing_result.get('total_processed_entities', 0),
                        'created_relationships': post_processing_result.get('total_created_relationships', 0),
                        'elapsed_time': f"{post_processing_end_time - post_processing_start_time:.2f}",
                        'rules': rules_list
                    }
                    
                    # Node count'ları güncelle - thread'e taşı
                    final_count_response = await asyncio.to_thread(graphDb_data_Access.update_node_relationship_count, file_name)
                    if final_count_response:
                        result['nodeCount'] = final_count_response[file_name].get('nodeCount',"0")
                        result['relationshipCount'] = final_count_response[file_name].get('relationshipCount',"0")
                        
                    logging.info(f"Post-processing ile {post_processing_result.get('total_created_relationships', 0)} yeni relationship oluşturuldu")
                    
                except Exception as post_processing_error:
                    logging.error(f"Otomatik post-processing hatası: {post_processing_error}")
                    result['post_processing'] = {
                        'enabled': True,
                        'error': str(post_processing_error),
                        'rules_applied': 0
                    }
            else:
                result['post_processing'] = {'enabled': False}
            
            # Policy Node Cleanup - DISABLED: Policy node'ları Document'e çevirmek yerine HAS_METADATA ile bağlıyoruz
            # try:
            #     logging.info(f"Policy node cleanup başlıyor: {file_name}")
            #     
            #     from src.policy_cleanup import cleanup_policy_nodes_to_document
            #     policy_cleanup_start_time = time.time()
            #     
            #     # Policy cleanup işlemi
            #     policy_cleanup_result = await asyncio.to_thread(
            #         cleanup_policy_nodes_to_document,
            #         graph,
            #         file_name
            #     )
            #     
            #     policy_cleanup_end_time = time.time()
            #     
            #     # Result'a Policy cleanup bilgilerini ekle
            #     result['policy_cleanup'] = {
            #         'status': policy_cleanup_result['status'],
            #         'policy_nodes_found': policy_cleanup_result['policy_nodes_found'],
            #         'relationships_moved': policy_cleanup_result['relationships_moved'],
            #         'policy_nodes_deleted': policy_cleanup_result['policy_nodes_deleted'],
            #         'elapsed_time': f"{policy_cleanup_end_time - policy_cleanup_start_time:.2f}",
            #         'processed_policies': policy_cleanup_result.get('processed_policies', [])
            #     }
            #     
            #     # Eğer Policy node'lar bulunup temizlendiyse, node count'ları güncelle
            #     if policy_cleanup_result['policy_nodes_deleted'] > 0:
            #         final_count_response = graphDb_data_Access.update_node_relationship_count(file_name)
            #         if final_count_response:
            #             result['nodeCount'] = final_count_response[file_name].get('nodeCount',"0")
            #             result['relationshipCount'] = final_count_response[file_name].get('relationshipCount',"0")
            #             
            #     logging.info(f"Policy cleanup tamamlandı: {policy_cleanup_result['policy_nodes_deleted']} Policy silindi, {policy_cleanup_result['relationships_moved']} relationship yönlendirildi")
                
            
            
            # Entity Promotion - Chunk entity'lerini Document'a terfi ettir
            try:
                if enable_entity_promotion:
                    logging.info(f"Entity promotion başlıyor: {file_name}")
                    
                    # Default entity promotion kuralları (eğer param gönderilmemişse)
                    default_promotion_rules = ["Address", "Company", "Person", "Phone", "Email", "Agent", "InsuranceCompany"]
                    
                    if entity_promotion_rules:
                        if isinstance(entity_promotion_rules, str):
                            promotion_rules = json.loads(entity_promotion_rules)
                        else:
                            promotion_rules = entity_promotion_rules
                    else:
                        promotion_rules = default_promotion_rules
                    
                    logging.info(f"Entity promotion kuralları: {promotion_rules}")
                    
                    from src.policy_cleanup import promote_chunk_entities_to_document
                    entity_promotion_start_time = time.time()
                    
                    # Entity promotion işlemi
                    entity_promotion_result = await asyncio.to_thread(
                        promote_chunk_entities_to_document,
                        graph,
                        file_name,
                        promotion_rules
                    )
                    
                    entity_promotion_end_time = time.time()
                    
                    # Result'a Entity promotion bilgilerini ekle
                    result['entity_promotion'] = {
                        'status': entity_promotion_result['status'],
                        'enabled': True,
                        'promoted_entities': entity_promotion_result['promoted_entities'],
                        'relationships_created': entity_promotion_result['relationships_created'],
                        'elapsed_time': f"{entity_promotion_end_time - entity_promotion_start_time:.2f}",
                        'promotion_rules': promotion_rules,
                        'promotion_details': entity_promotion_result.get('promotion_details', [])
                    }
                    
                    # Eğer entity'ler terfi ettirildiyse, node count'ları güncelle - thread'e taşı
                    if entity_promotion_result['relationships_created'] > 0:
                        final_count_response = await asyncio.to_thread(graphDb_data_Access.update_node_relationship_count, file_name)
                        if final_count_response:
                            result['nodeCount'] = final_count_response[file_name].get('nodeCount',"0")
                            result['relationshipCount'] = final_count_response[file_name].get('relationshipCount',"0")
                            
                    logging.info(f"Entity promotion tamamlandı: {entity_promotion_result['promoted_entities']} entity terfi edildi, {entity_promotion_result['relationships_created']} Document ilişkisi oluşturuldu")
                else:
                    result['entity_promotion'] = {'enabled': False}
                    logging.info("Entity promotion devre dışı")
                
            except Exception as entity_promotion_error:
                logging.error(f"Entity promotion hatası: {entity_promotion_error}")
                result['entity_promotion'] = {
                    'status': 'error',
                    'enabled': True,
                    'error': str(entity_promotion_error),
                    'elapsed_time': '0.00'
                }
            
            result['db_url'] = uri
            result['api_name'] = 'extract'
            result['source_url'] = source_url
            result['wiki_query'] = wiki_query
            result['source_type'] = source_type
            result['logging_time'] = formatted_time(datetime.now(timezone.utc))
            result['elapsed_api_time'] = f'{extract_api_time:.2f}'
            result['userName'] = userName
            result['database'] = database
            result['aws_access_key_id'] = aws_access_key_id
            result['gcs_bucket_name'] = gcs_bucket_name
            result['gcs_bucket_folder'] = gcs_bucket_folder
            result['gcs_blob_filename'] = gcs_blob_filename
            result['gcs_project_id'] = gcs_project_id
            result['language'] = language
            result['retry_condition'] = retry_condition
            result['email'] = email
        logger.log_struct(result, "INFO")
        result.update(uri_latency)
        logging.info(f"extraction completed in {extract_api_time:.2f} seconds for file name {file_name}")
        return create_api_response('Success', data=result, file_source= source_type)
    except LLMGraphBuilderException as e:
        error_message = str(e)
        graph = create_graph_database_connection(uri, userName, password, database)   
        graphDb_data_Access = graphDBdataAccess(graph)
        graphDb_data_Access.update_exception_db(file_name,error_message, retry_condition)
        if source_type == 'local file':
            failed_file_process(uri,file_name, merged_file_path)
        
        # Document node durumunu güvenli bir şekilde al
        try:
            node_detail = graphDb_data_Access.get_current_status_document_node(file_name)
        except Exception as node_error:
            logging.warning(f"Document node status alınamadı: {node_error}")
            node_detail = None
        
        # Set the status "Completed" in logging becuase we are treating these error already handled by application as like custom errors.
        file_created_at = None
        if node_detail and len(node_detail) > 0 and node_detail[0].get('created_time'):
            file_created_at = formatted_time(node_detail[0]['created_time'])
        else:
            file_created_at = formatted_time(datetime.now(timezone.utc))
        
        json_obj = {'api_name':'extract','message':error_message,'file_created_at':file_created_at,'error_message':error_message, 'file_name': file_name,'status':'Completed',
                    'db_url':uri, 'userName':userName, 'database':database,'success_count':1, 'source_type': source_type, 'source_url':source_url, 'wiki_query':wiki_query, 'logging_time': formatted_time(datetime.now(timezone.utc)),'email':email,
                    'allowedNodes': allowedNodes, 'allowedRelationship': allowedRelationship}
        logger.log_struct(json_obj, "INFO")
        logging.exception(f'File Failed in extraction: {e}')
        return create_api_response("Failed", message = error_message, error=error_message, file_name=file_name)
    except Exception as e:
        message=f"Failed To Process File:{file_name} or LLM Unable To Parse Content "
        error_message = str(e)
        graph = create_graph_database_connection(uri, userName, password, database)   
        graphDb_data_Access = graphDBdataAccess(graph)
        graphDb_data_Access.update_exception_db(file_name,error_message, retry_condition)
        if source_type == 'local file':
            failed_file_process(uri,file_name, merged_file_path)
        
        # Document node durumunu güvenli bir şekilde al
        try:
            node_detail = graphDb_data_Access.get_current_status_document_node(file_name)
        except Exception as node_error:
            logging.warning(f"Document node status alınamadı: {node_error}")
            node_detail = None
        
        file_created_at = None
        if node_detail and len(node_detail) > 0 and node_detail[0].get('created_time'):
            file_created_at = formatted_time(node_detail[0]['created_time'])
        else:
            file_created_at = formatted_time(datetime.now(timezone.utc))
        
        json_obj = {'api_name':'extract','message':message,'file_created_at':file_created_at,'error_message':error_message, 'file_name': file_name,'status':'Failed',
                    'db_url':uri, 'userName':userName, 'database':database,'failed_count':1, 'source_type': source_type, 'source_url':source_url, 'wiki_query':wiki_query, 'logging_time': formatted_time(datetime.now(timezone.utc)),'email':email,
                    'allowedNodes': allowedNodes, 'allowedRelationship': allowedRelationship}
        logger.log_struct(json_obj, "ERROR")
        logging.exception(f'File Failed in extraction: {e}')
        return create_api_response('Failed', message=message + error_message[:100], error=error_message, file_name = file_name)
    finally:
        gc.collect()

@app.post("/extract_qa_based")
async def extract_qa_based_knowledge_graph(
    uri=Form(None),
    userName=Form(None),
    password=Form(None),
    database=Form(None),
    model=Form(),
    document_chunks=Form(),
    file_name=Form(),
    domain=Form(None),
    custom_questions=Form(None),
    email=Form(None)
):
    """
    QA tabanlı entity çıkarma endpoint'i
    """
    try:
        logging.info(f"QA tabanlı extraction başlıyor: {file_name}")
        logging.info(f"Gelen domain parametresi: {domain}")
        
        # Parameters validate
        if not document_chunks or not file_name or not model:
            raise HTTPException(status_code=400, detail="document_chunks, file_name ve model parametreleri gerekli")
        
        # Parse document chunks (JSON string olarak gönderilmiş olabilir)
        if isinstance(document_chunks, str):
            try:
                chunks_list = json.loads(document_chunks)
            except:
                chunks_list = [document_chunks]  # Single chunk
        else:
            chunks_list = document_chunks
        
        # Custom questions parse et (eğer varsa)
        questions_dict = None
        if custom_questions:
            try:
                questions_dict = json.loads(custom_questions)
            except:
                logging.warning("Custom questions parse edilemedi, default sorular kullanılacak")
        
        # Domain tespiti (eğer belirtilmemişse)
        if not domain and len(chunks_list) > 0:
            domain = detect_document_domain(file_name, chunks_list[0])
            logging.info(f"Otomatik domain tespiti: {domain}")
        elif domain:
            logging.info(f"Kullanıcı tarafından seçilen domain: {domain}")
        
        # QA tabanlı extractor oluştur
        extractor = QABasedEntityExtractor(model)
        
        # Domain'e özgü sorular al (eğer custom yoksa)
        if not questions_dict:
            if domain:
                questions_dict = create_domain_specific_questions(domain)
                logging.info(f"Domain '{domain}' için otomatik sorular oluşturuldu: {len(questions_dict)} kategori")
                # Domain sorularını da loglayalım
                for category, questions in questions_dict.items():
                    logging.info(f"  {category}: {len(questions)} soru")
            else:
                questions_dict = extractor.default_questions
                logging.info("Domain belirtilmediği için genel sorular kullanılıyor")
        else:
            logging.info("Kullanıcı tarafından özel sorular sağlandı")
        
        # Entity'leri çıkar
        logging.info(f"Extractor'a gönderilen sorular: {json.dumps(questions_dict, ensure_ascii=False, indent=2)}")
        graph_documents = await extractor.extract_entities_from_qa(
            document_chunks=chunks_list,
            file_name=file_name,
            custom_questions=questions_dict
        )
        
        # Çıkarılan entity ve relation sayılarını logla
        total_entities = sum(len(doc.nodes) for doc in graph_documents)
        total_relationships = sum(len(doc.relationships) for doc in graph_documents)
        logging.info(f"Toplam çıkarılan entity: {total_entities}, relationship: {total_relationships}")
        
        # Her GraphDocument için ayrıntılı loglama
        for i, doc in enumerate(graph_documents):
            logging.info(f"GraphDocument {i}: {len(doc.nodes)} entity, {len(doc.relationships)} relationship")
            # İlk birkaç entity'yi de logla
            for j, node in enumerate(doc.nodes[:5]):  # İlk 5 entity
                logging.info(f"  Entity {j}: {node.type} - {node.properties}")
        
        # Dosya adından temiz bir isim oluştur (uzantıları kaldır, özel karakterleri temizle)
        clean_file_name = file_name.replace('.pdf', '').replace('.docx', '').replace('.txt', '')
        clean_file_name = re.sub(r'[^\w\-_\.]', '_', clean_file_name)
        
        # Neo4j'ye kaydet (opsiyonel - mevcut extract endpoint mantığını kullanarak)
        if uri and userName and password:
            graph_db = graphDBdataAccess(uri, userName, password, database)
            # GraphDocument'ları Neo4j'ye kaydet
            # Bu kısmı mevcut save işlemiyle entegre edebiliriz
        
        # newSchema.json formatında triplet'ler oluştur
        triplets = []
        unique_triplets = set()
        
        for doc in graph_documents:
            for rel in doc.relationships:
                source_type = rel.source.type if hasattr(rel.source, 'type') else 'Unknown'
                target_type = rel.target.type if hasattr(rel.target, 'type') else 'Unknown'
                rel_type = rel.type
                
                # İlişki tipini büyük harfe çevir ve alt çizgi ile ayır
                formatted_rel_type = rel_type.upper().replace(' ', '_').replace('-', '_')
                
                # Triplet formatı: "SourceType-RELATION_TYPE->TargetType" (istenen format)
                triplet = f"{source_type}-{formatted_rel_type}->{target_type}"
                
                # Duplicate'ları önle
                if triplet not in unique_triplets:
                    triplets.append(triplet)
                    unique_triplets.add(triplet)
        
        # Node tiplerini ve relationship type'larını çıkar (schemas.json formatı için)
        unique_labels = set()
        unique_relationship_types = set()
        
        for doc in graph_documents:
            # Node tiplerini topla
            for node in doc.nodes:
                if hasattr(node, 'type') and node.type:
                    unique_labels.add(node.type)
            
            # Relationship tiplerini topla
            for rel in doc.relationships:
                if hasattr(rel, 'type') and rel.type:
                    # İlişki tipini büyük harfe çevir ve format düzelt
                    formatted_rel_type = rel.type.upper().replace(' ', '_').replace('-', '_')
                    unique_relationship_types.add(formatted_rel_type)
        
        # Frontend için schema formatında bilgiler oluştur
        schema = {
            "labels": sorted(list(unique_labels)),
            "relationshipTypes": sorted(list(unique_relationship_types)),
            "schema": clean_file_name
        }
        
        # Şemayı kaydet
        schema_data = {
            'file_name': file_name,
            'clean_file_name': clean_file_name,
            'domain': domain,
            'extraction_method': 'qa_based',
            'model': model,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'entities_count': sum(len(doc.nodes) for doc in graph_documents),
            'relationships_count': sum(len(doc.relationships) for doc in graph_documents),
            'unique_entity_types': len(unique_labels),
            'unique_relationship_types': len(unique_relationship_types),
            'triplets_count': len(triplets),
            'questions_used': questions_dict,
            'schema': schema,  # Frontend için schema format
            'triplets': triplets  # Triplet formatında ilişkiler
        }
        
        # Ana schema dosyasına kaydet (tüm detaylı bilgiler burada)
        schema_file = f"qa_schemas/{clean_file_name}.json"
        os.makedirs("qa_schemas", exist_ok=True)
        with open(schema_file, 'w', encoding='utf-8') as f:
            json.dump(schema_data, f, ensure_ascii=False, indent=2)
        
        logging.info(f"QA tabanlı extraction tamamlandı: {len(graph_documents)} GraphDocument oluşturuldu")
        
        response_data = {
            'graph_documents_count': len(graph_documents),
            'total_entities': sum(len(doc.nodes) for doc in graph_documents),
            'total_relationships': sum(len(doc.relationships) for doc in graph_documents),
            'unique_entity_types': len(unique_labels),
            'unique_relationship_types': len(unique_relationship_types),
            'triplets_count': len(triplets),
            'domain': domain,
            'schema_file': schema_file,  # Ana detaylı schema dosyası
            'extraction_method': 'qa_based',
            'schema': schema,  # Frontend için schema format
            'triplets': triplets  # Kolay kullanım için ayrıca triplet'leri de gönder
        }
        
        return create_api_response('Success', data=response_data, file_name=file_name)
        
    except Exception as e:
        logging.error(f"QA tabanlı extraction hatası: {e}")
        return create_api_response('Failed', message=str(e), file_name=file_name)

@app.post("/load_qa_schema")
async def load_qa_schema(
    schema_file=Form(),
    email=Form(None)
):
    """
    Kaydedilmiş QA tabanlı şemayı yükle
    """
    try:
        if not os.path.exists(schema_file):
            raise HTTPException(status_code=404, detail="Schema dosyası bulunamadı")
        
        with open(schema_file, 'r', encoding='utf-8') as f:
            schema_data = json.load(f)
        
        return create_api_response('Success', data=schema_data)
        
    except Exception as e:
        logging.error(f"Schema yükleme hatası: {e}")
        return create_api_response('Failed', message=str(e))

@app.get("/list_qa_schemas")
async def list_qa_schemas():
    """
    Mevcut QA tabanlı şemaları listele
    """
    try:
        schema_dir = "qa_schemas"
        if not os.path.exists(schema_dir):
            return create_api_response('Success', data=[])
        
        schemas = []
        for file in os.listdir(schema_dir):
            if file.endswith('.json'):
                file_path = os.path.join(schema_dir, file)
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        schema_info = json.load(f)
                    
                    # Unique relationship types sayısını hesapla
                    unique_rels_count = len(schema_info.get('schema', {}).get('relationshipTypes', []))
                    
                    schemas.append({
                        'filename': file,
                        'file_path': file_path,
                        'document_name': schema_info.get('file_name'),
                        'domain': schema_info.get('domain'),
                        'timestamp': schema_info.get('timestamp'),
                        'entities_count': schema_info.get('entities_count'),
                        'relationships_count': schema_info.get('relationships_count'),  # toplam triplet sayısı
                        'unique_relationship_types': unique_rels_count,  # unique relationship types sayısı  
                        'triplets_count': schema_info.get('relationships_count'),  # netlik için aynı değeri tekrar
                        'model': schema_info.get('model')
                    })
                except:
                    # Bozuk dosyaları atla
                    continue
        
        # Timestamp'e göre sırala (en yeni önce)
        schemas.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
        
        return create_api_response('Success', data=schemas)
        
    except Exception as e:
        logging.error(f"Schema listeleme hatası: {e}")
        return create_api_response('Failed', message=str(e))

@app.post("/convert-to-markdown")
async def convert_to_markdown(file: UploadFile = File(...)):
    """
    Dosyayı Docling kullanarak markdown'a çevirme endpoint'i (cache destekli)
    """
    if not DOCLING_AVAILABLE:
        raise HTTPException(status_code=500, detail="Docling kütüphanesi yüklü değil. pip install docling komutu ile yükleyin.")
    
    try:
        # Dosya içeriğini oku
        content = await file.read()
        file_size = len(content)
        
        # Önce cache'i kontrol et
        cached_markdown = get_cached_markdown(file.filename, file_size)
        if cached_markdown:
            logging.info(f"Markdown cache'den alındı: {file.filename}")
            return {
                "status": "success",
                "markdown": cached_markdown,
                "filename": file.filename,
                "original_size": file_size,
                "markdown_size": len(cached_markdown),
                "from_cache": True
            }
        
        # Cache'de yok, Docling ile dönüştür
        logging.info(f"Docling ile markdown'a çevriliyor: {file.filename}")
        
        # Geçici dosya oluştur
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(file.filename).suffix) as tmp_file:
            tmp_file.write(content)
            tmp_file_path = tmp_file.name
        
        try:
            labels = [
                label
                for label in DEFAULT_EXPORT_LABELS
                if label not in (DocItemLabel.PICTURE, DocItemLabel.PAGE_FOOTER)
            ]
            
            # Docling ile dosyayı işle
            converter = DocumentConverter()
            result = converter.convert(tmp_file_path)
            
            # Markdown çıktısını al
            markdown_content = result.document.export_to_markdown(labels=labels)
            
            # Cache'e kaydet
            save_markdown_to_cache(file.filename, file_size, markdown_content)
            
            return {
                "status": "success",
                "markdown": markdown_content,
                "filename": file.filename,
                "original_size": file_size,
                "markdown_size": len(markdown_content),
                "from_cache": False
            }
            
        finally:
            # Geçici dosyayı sil
            os.unlink(tmp_file_path)
            
    except Exception as e:
        logging.error(f"Dosya dönüştürme hatası: {e}")
        raise HTTPException(status_code=500, detail=f"Dosya dönüştürme hatası: {str(e)}")
            
@app.post("/sources_list")
async def get_source_list(
    uri=Form(None),
    userName=Form(None),
    password=Form(None),
    database=Form(None),
    email=Form(None)):
    """
    Calls 'get_source_list_from_graph' which returns list of sources which already exist in databse
    """
    try:
        start = time.time()
        result = await asyncio.to_thread(get_source_list_from_graph,uri,userName,password,database)
        end = time.time()
        elapsed_time = end - start
        json_obj = {'api_name':'sources_list','db_url':uri, 'userName':userName, 'database':database, 'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{elapsed_time:.2f}','email':email}
        logger.log_struct(json_obj, "INFO")
        return create_api_response("Success",data=result, message=f"Total elapsed API time {elapsed_time:.2f}")
    except Exception as e:
        job_status = "Failed"
        message="Unable to fetch source list"
        error_message = str(e)
        logging.exception(f'Exception:{error_message}')
        return create_api_response(job_status, message=message, error=error_message)

@app.post("/post_processing")
async def post_processing(uri=Form(None), userName=Form(None), password=Form(None), database=Form(None), tasks=Form(None), email=Form(None)):
    try:
        graph = create_graph_database_connection(uri, userName, password, database)
        tasks = set(map(str.strip, json.loads(tasks)))
        api_name = 'post_processing'
        count_response = []
        start = time.time()
        
        # Document relationships oluştur (yeni özellik)
        if "connect_documents_by_entities" in tasks:
            from src.make_relationships import create_document_relationships
            doc_connections = await asyncio.to_thread(create_document_relationships, graph)
            api_name = 'post_processing/connect_documents_by_entities'
            logging.info(f'Document connections created: {doc_connections}')
        
        if "materialize_text_chunk_similarities" in tasks:
            await asyncio.to_thread(update_graph, graph)
            api_name = 'post_processing/update_similarity_graph'
            logging.info(f'Updated KNN Graph')

        if "enable_hybrid_search_and_fulltext_search_in_bloom" in tasks:
            await asyncio.to_thread(create_vector_fulltext_indexes, uri=uri, username=userName, password=password, database=database)
            api_name = 'post_processing/enable_hybrid_search_and_fulltext_search_in_bloom'
            logging.info(f'Full Text index created')

        if os.environ.get('ENTITY_EMBEDDING','False').upper()=="TRUE" and "materialize_entity_similarities" in tasks:
            await asyncio.to_thread(create_entity_embedding, graph)
            api_name = 'post_processing/create_entity_embedding'
            logging.info(f'Entity Embeddings created')

        if "graph_schema_consolidation" in tasks :
            await asyncio.to_thread(graph_schema_consolidation, graph)
            api_name = 'post_processing/graph_schema_consolidation'
            logging.info(f'Updated nodes and relationship labels')
            
        if "enable_communities" in tasks:
            api_name = 'create_communities'
            await asyncio.to_thread(create_communities, uri, userName, password, database)  
            
            logging.info(f'created communities')
        graph = create_graph_database_connection(uri, userName, password, database)   
        graphDb_data_Access = graphDBdataAccess(graph)
        document_name = ""
        count_response = await asyncio.to_thread(graphDb_data_Access.update_node_relationship_count, document_name)
        if count_response:
            count_response = [{"filename": filename, **counts} for filename, counts in count_response.items()]
            logging.info(f'Updated source node with community related counts')
        
        end = time.time()
        elapsed_time = end - start
        json_obj = {'api_name': api_name, 'db_url': uri, 'userName':userName, 'database':database, 'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{elapsed_time:.2f}','email':email}
        logger.log_struct(json_obj)
        return create_api_response('Success', data=count_response, message='All tasks completed successfully')
    
    except Exception as e:
        job_status = "Failed"
        error_message = str(e)
        message = f"Unable to complete tasks"
        logging.exception(f'Exception in post_processing tasks: {error_message}')
        return create_api_response(job_status, message=message, error=error_message)
    
    finally:
        gc.collect()

@app.post("/entity_relationship_post_processing")
async def entity_relationship_post_processing(
    uri=Form(None), 
    userName=Form(None), 
    password=Form(None), 
    database=Form(None), 
    file_names=Form(None), 
    post_processing_rules=Form(None),
    email=Form(None)
):
    """
    Dinamik entity relationship post-processing endpoint'i.
    Kullanıcının belirlediği kurallara göre entity'leri target node'lara bağlar.
    
    Args:
        file_names: İşlenecek dosya isimlerinin JSON listesi
        post_processing_rules: Post-processing kurallarının JSON formatı:
        [
            {
                "source_node_type": "Year",
                "target_node_type": "Document", 
                "relationship_types": ["OCCURS_IN", "HAS_YEAR"],
                "target_selection": "document" // "document" veya "specific_target"
            },
            {
                "source_node_type": "Person",
                "target_node_type": "Company",
                "relationship_types": ["WORKS_FOR"],
                "target_selection": "specific_target"
            }
        ]
    """
    try:
        start = time.time()
        graph = create_graph_database_connection(uri, userName, password, database)
        
        # Parametreleri parse et
        if post_processing_rules:
            rules_list = json.loads(post_processing_rules) if isinstance(post_processing_rules, str) else post_processing_rules
        else:
            rules_list = []
        
        # file_names parametresini parse et
        target_files = None
        if file_names:
            try:
                target_files = json.loads(file_names) if isinstance(file_names, str) else file_names
                if target_files:
                    logging.info(f"Hedef dosyalar: {target_files}")
            except json.JSONDecodeError:
                logging.warning(f"file_names parse edilemedi: {file_names}")
        
        # Post-processing işlemini çalıştır
        from src.llm import apply_dynamic_entity_post_processing
        result = await asyncio.to_thread(apply_dynamic_entity_post_processing, graph, rules_list, target_files)
        
        end = time.time()
        elapsed_time = end - start
        
        json_obj = {
            'api_name': 'entity_relationship_post_processing', 
            'db_url': uri, 
            'userName': userName, 
            'database': database, 
            'post_processing_rules': post_processing_rules,
            'processed_files': result.get('processed_files', 0),
            'applied_rules': len(rules_list),
            'logging_time': formatted_time(datetime.now(timezone.utc)), 
            'elapsed_api_time': f'{elapsed_time:.2f}',
            'email': email
        }
        logger.log_struct(json_obj, "INFO")
        
        processed_files = result.get('processed_files', 0)
        return create_api_response('Success', data=result, message=f'Entity relationship post-processing completed across {processed_files} documents with {len(rules_list)} rules in {elapsed_time:.2f} seconds')
    
    except Exception as e:
        job_status = "Failed"
        error_message = str(e)
        message = f"Unable to complete entity relationship post-processing"
        logging.exception(f'Exception in entity_relationship_post_processing: {error_message}')
        return create_api_response(job_status, message=message, error=error_message)
    
    finally:
        gc.collect()
                
@app.post("/chat_bot")
async def chat_bot(uri=Form(None),model=Form(None),userName=Form(None), password=Form(None), database=Form(None),question=Form(None), document_names=Form(None),session_id=Form(None),mode=Form(None),email=Form(None)):
    logging.info(f"QA_RAG called at {datetime.now()}")
    qa_rag_start_time = time.time()
    try:
        if mode == "graph":
            graph = Neo4jGraph( url=uri,username=userName,password=password,database=database,sanitize = True, refresh_schema=True)
        else:
            graph = create_graph_database_connection(uri, userName, password, database)
        
        graph_DB_dataAccess = graphDBdataAccess(graph)
        write_access = graph_DB_dataAccess.check_account_access(database=database)
        # Try to instantiate IntelligentAgent and AlternativeAgent and pass them to QA_RAG (fallback to None on failure)
        intelligent_agent = None
        try:
            intelligent_agent = IntelligentAgent(graph)
        except Exception:
            intelligent_agent = None
        
        alternative_agent = None
        try:
            alternative_agent = AlternativeAgent(graph)
        except Exception:
            alternative_agent = None

        result = await asyncio.to_thread(
            QA_RAG,
            graph=graph,
            model=model,
            question=question,
            document_names=document_names,
            session_id=session_id,
            mode=mode,
            write_access=write_access,
            intelligent_agent=intelligent_agent,
            alternative_agent=alternative_agent,
        )

        total_call_time = time.time() - qa_rag_start_time
        logging.info(f"Total Response time is  {total_call_time:.2f} seconds")
        result["info"]["response_time"] = round(total_call_time, 2)
        
        json_obj = {'api_name':'chat_bot','db_url':uri, 'userName':userName, 'database':database, 'question':question,'document_names':document_names,
                             'session_id':session_id, 'mode':mode, 'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{total_call_time:.2f}','email':email}
        logger.log_struct(json_obj, "INFO")
        
        return create_api_response('Success',data=result)
    except Exception as e:
        job_status = "Failed"
        message="Unable to get chat response"
        error_message = str(e)
        logging.exception(f'Exception in chat bot:{error_message}')
        return create_api_response(job_status, message=message, error=error_message,data=mode)
    finally:
        gc.collect()

@app.post("/chat_bot_stream")
async def chat_bot_stream(
    request: Request,
    uri: str = Form(None),
    model: str = Form(None),
    userName: str = Form(None),
    password: str = Form(None),
    database: str = Form(None),
    question: str = Form(None),
    document_names: str = Form(None),
    session_id: str = Form(None),
    mode: str = Form(None),
    email: str = Form(None),
    files: Optional[str] = Form(None)
):
    """
    Gerçek LLM streaming kullanarak Server-Sent Events (SSE) ile 
    token-by-token chat cevapları gönderir.
    """
    
    # print("chat_bot_stream files: ", files)
    
    filesJson = None
    downloadedFiles = None
    if files:
        try:
            filesJson = json.loads(files)
        except json.JSONDecodeError:
            logging.info("files JSON parse error.")
            # return {"error": "Invalid JSON in 'files'"}
    print("chat_bot_stream filesJson: ", filesJson)
    if filesJson:
        try:
            # handle_attachments can be slow (downloads, conversions). Run in thread to avoid blocking event loop.
            downloadedFiles = await asyncio.to_thread(handle_attachments, filesJson)
        except json.JSONDecodeError:
            logging.info("files handle_attachments error.")
            # return {"error": "Invalid JSON in 'files'"}
        except Exception as e:
            logging.exception(f"handle_attachments hatası: {e}")
            downloadedFiles = None
    print("chat_bot_stream downloadedFiles: ", downloadedFiles)
    # files_data: Dict[str, List[Dict[str, str]]] = {}
    # if downloadedFiles:
    #     try:
    #         files_data = convert_result_to_base64(downloadedFiles)
    #     except json.JSONDecodeError:
    #         logging.info("files convert_result_to_base64 error.")
            # return {"error": "Invalid JSON in 'files'"}
    
    
    async def generate_real_streaming_response():
        try:
            logging.info(f"QA_RAG Real Stream called at {datetime.now()}")
            qa_rag_start_time = time.time()
            
            # İlk durum mesajı gönder
            yield f"data: {json.dumps({'type': 'status', 'message': 'Gerçek streaming başlatılıyor...', 'status': 'starting'}, ensure_ascii=False)}\n\n"
            
            # Graph bağlantısını kur
            if mode == "graph":
                graph = Neo4jGraph(url=uri, username=userName, password=password, database=database, sanitize=True, refresh_schema=True)
            else:
                graph = create_graph_database_connection(uri, userName, password, database)
            
            yield f"data: {json.dumps({'type': 'status', 'message': 'Veritabanı bağlantısı kuruldu', 'status': 'connected'}, ensure_ascii=False)}\n\n"
            
            graph_DB_dataAccess = graphDBdataAccess(graph)
            write_access = graph_DB_dataAccess.check_account_access(database=database)
            
            # Gerçek streaming başlat
            final_result = None
            total_tokens = 0
            
            # Instantiate IntelligentAgent for streaming path and pass it through (fallback to None)
            intelligent_agent = None
            try:
                intelligent_agent = IntelligentAgent(graph)
            except Exception:
                intelligent_agent = None
                
            # Instantiate AlternativeAgent for streaming path and pass it through (fallback to None)
            alternative_agent = None
            try:
                alternative_agent = AlternativeAgent(graph)
            except Exception:
                alternative_agent = None

            async for chunk in QA_RAG_stream(
                graph=graph,
                model=model,
                question=question,
                document_names=document_names,
                session_id=session_id,
                mode=mode,
                write_access=write_access,
                # intelligent_agent=intelligent_agent,
                alternative_agent=alternative_agent,
                files=downloadedFiles
            ):
                # Client disconnect kontrolü
                if await request.is_disconnected():
                    logging.info("SSE Client disconnected during real streaming")
                    break
                
                # Chunk'ı client'a gönder
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                
                # Final result'ı sakla
                if chunk.get("type") == "complete":
                    final_result = chunk
                    total_tokens = chunk.get("info", {}).get("total_tokens", 0)
            
            # Timing bilgilerini ekle
            total_call_time = time.time() - qa_rag_start_time
            logging.info(f"Real streaming total response time: {total_call_time:.2f} seconds")
            
            # Final timing chunk'ı gönder
            timing_chunk = {
                'type': 'timing',
                'status': 'finished',
                'elapsed_time': f"{total_call_time:.2f}",
                'total_tokens': total_tokens,
                'timestamp': formatted_time(datetime.now(timezone.utc))
            }
            yield f"data: {json.dumps(timing_chunk, ensure_ascii=False)}\n\n"
            
            # Loglama
            json_obj = {
                'api_name': 'chat_bot_stream_real',
                'db_url': uri,
                'userName': userName,
                'database': database,
                'question': question,
                'document_names': document_names,
                'session_id': session_id,
                'mode': mode,
                'logging_time': formatted_time(datetime.now(timezone.utc)),
                'elapsed_api_time': f'{total_call_time:.2f}',
                'total_tokens': total_tokens,
                'email': email,
                'streaming_type': 'real_llm_streaming'
            }
            logger.log_struct(json_obj, "INFO")
            
        except Exception as e:
            error_message = str(e)
            logging.exception(f'Exception in real streaming chat bot: {error_message}')
            
            error_chunk = {
                'type': 'error',
                'status': 'error', 
                'message': 'Streaming sırasında bir hata oluştu',
                'error': error_message,
                'timestamp': formatted_time(datetime.now(timezone.utc))
            }
            yield f"data: {json.dumps(error_chunk, ensure_ascii=False)}\n\n"
            
        finally:
            gc.collect()
    
    return EventSourceResponse(generate_real_streaming_response())

# @app.post("/chat_bot_stream_legacy")
# async def chat_bot_stream_legacy(
#     request: Request,
#     uri: str = Form(None),
#     model: str = Form(None),
#     userName: str = Form(None),
#     password: str = Form(None),
#     database: str = Form(None),
#     question: str = Form(None),
#     document_names: str = Form(None),
#     session_id: str = Form(None),
#     mode: str = Form(None),
#     email: str = Form(None)
# ):
#     """
#     Eski simüle streaming versiyonu (backward compatibility için)
#     """
    
#     async def generate_simulated_streaming_response():
#         try:
#             logging.info(f"QA_RAG Simulated Stream called at {datetime.now()}")
#             qa_rag_start_time = time.time()
            
#             # İlk durum mesajı gönder
#             yield f"data: {json.dumps({'type': 'status', 'message': 'Simüle streaming başlatılıyor...', 'status': 'starting'})}\n\n"
            
#             # Graph bağlantısını kur
#             if mode == "graph":
#                 graph = Neo4jGraph(url=uri, username=userName, password=password, database=database, sanitize=True, refresh_schema=True)
#             else:
#                 graph = create_graph_database_connection(uri, userName, password, database)
            
#             yield f"data: {json.dumps({'type': 'status', 'message': 'Veritabanı bağlantısı kuruldu', 'status': 'connected'})}\n\n"
            
#             graph_DB_dataAccess = graphDBdataAccess(graph)
#             write_access = graph_DB_dataAccess.check_account_access(database=database)
            
#             yield f"data: {json.dumps({'type': 'status', 'message': 'Soru işleniyor...', 'status': 'processing'})}\n\n"
            
#             # QA_RAG işlemini çalıştır (eski batch yöntem)
#             result = await asyncio.to_thread(
#                 QA_RAG,
#                 graph=graph,
#                 model=model,
#                 question=question,
#                 document_names=document_names,
#                 session_id=session_id,
#                 mode=mode,
#                 write_access=write_access
#             )
            
#             total_call_time = time.time() - qa_rag_start_time
#             logging.info(f"Simulated streaming total response time: {total_call_time:.2f} seconds")
#             result["info"]["response_time"] = round(total_call_time, 2)
            
#             # Cevap parçalayarak gönder (simüle streaming effect)
#             message = result.get("message", "")
#             if message:
#                 # Mesajı kelime kelime stream et
#                 words = message.split()
#                 streamed_message = ""
                
#                 for i, word in enumerate(words):
#                     if await request.is_disconnected():
#                         logging.info("SSE Client disconnected during simulated streaming")
#                         break
                        
#                     streamed_message += word + " "
                    
#                     # Her birkaç kelimede bir chunk gönder
#                     if (i + 1) % 3 == 0 or i == len(words) - 1:
#                         chunk_data = {
#                             'type': 'message_chunk',
#                             'content': word + " ",  # Sadece bu kelime
#                             'full_message': streamed_message.strip(),  # Şimdiye kadarki tam mesaj
#                             'is_complete': i == len(words) - 1,
#                             'word_index': i + 1,
#                             'total_words': len(words),
#                             'session_id': result.get("session_id", session_id)
#                         }
#                         yield f"data: {json.dumps(chunk_data)}\n\n"
                        
#                         # Streaming efekti için kısa bekleme
#                         await asyncio.sleep(0.1)
            
#             # Son olarak tam sonucu gönder
#             final_data = {
#                 'type': 'complete',
#                 'status': 'finished',
#                 'data': result,
#                 'elapsed_time': f"{total_call_time:.2f}",
#                 'timestamp': formatted_time(datetime.now(timezone.utc))
#             }
#             yield f"data: {json.dumps(final_data)}\n\n"
            
#             # Loglama
#             json_obj = {
#                 'api_name': 'chat_bot_stream_legacy',
#                 'db_url': uri,
#                 'userName': userName,
#                 'database': database,
#                 'question': question,
#                 'document_names': document_names,
#                 'session_id': session_id,
#                 'mode': mode,
#                 'logging_time': formatted_time(datetime.now(timezone.utc)),
#                 'elapsed_api_time': f'{total_call_time:.2f}',
#                 'email': email,
#                 'streaming_type': 'simulated_streaming'
#             }
#             logger.log_struct(json_obj, "INFO")
            
#         except Exception as e:
#             error_message = str(e)
#             logging.exception(f'Exception in chat bot stream: {error_message}')
            
#             error_data = {
#                 'type': 'error',
#                 'status': 'failed',
#                 'message': "Chat cevabı alınamadı",
#                 'error': error_message,
#                 'timestamp': formatted_time(datetime.now(timezone.utc))
#             }
#             yield f"data: {json.dumps(error_data)}\n\n"
        
#         finally:
#             gc.collect()
    
#     return EventSourceResponse(generate_simulated_streaming_response())

@app.post("/chunk_entities")
async def chunk_entities(uri=Form(None),userName=Form(None), password=Form(None), database=Form(None), nodedetails=Form(None),entities=Form(),mode=Form(),email=Form(None)):
    try:
        start = time.time()
        result = await asyncio.to_thread(get_entities_from_chunkids,nodedetails=nodedetails,entities=entities,mode=mode,uri=uri, username=userName, password=password, database=database)
        end = time.time()
        elapsed_time = end - start
        json_obj = {'api_name':'chunk_entities','db_url':uri, 'userName':userName, 'database':database, 'nodedetails':nodedetails,'entities':entities,
                            'mode':mode, 'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{elapsed_time:.2f}','email':email}
        logger.log_struct(json_obj, "INFO")
        return create_api_response('Success',data=result,message=f"Total elapsed API time {elapsed_time:.2f}")
    except Exception as e:
        job_status = "Failed"
        message="Unable to extract entities from chunk ids"
        error_message = str(e)
        logging.exception(f'Exception in chat bot:{error_message}')
        return create_api_response(job_status, message=message, error=error_message)
    finally:
        gc.collect()

@app.post("/get_neighbours")
async def get_neighbours(uri=Form(None),userName=Form(None), password=Form(None), database=Form(None), elementId=Form(None),email=Form(None)):
    try:
        start = time.time()
        result = await asyncio.to_thread(get_neighbour_nodes,uri=uri, username=userName, password=password,database=database, element_id=elementId)
        end = time.time()
        elapsed_time = end - start
        json_obj = {'api_name':'get_neighbours', 'userName':userName, 'database':database,'db_url':uri, 'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{elapsed_time:.2f}','email':email}
        logger.log_struct(json_obj, "INFO")
        return create_api_response('Success',data=result,message=f"Total elapsed API time {elapsed_time:.2f}")
    except Exception as e:
        job_status = "Failed"
        message="Unable to extract neighbour nodes for given element ID"
        error_message = str(e)
        logging.exception(f'Exception in get neighbours :{error_message}')
        return create_api_response(job_status, message=message, error=error_message)
    finally:
        gc.collect()

@app.post("/graph_query")
async def graph_query(
    uri: str = Form(None),
    database: str = Form(None),
    userName: str = Form(None),
    password: str = Form(None),
    document_names: str = Form(None),
    email=Form(None)
):
    try:
        start = time.time()
        result = await asyncio.to_thread(
            get_graph_results,
            uri=uri,
            username=userName,
            password=password,
            database=database,
            document_names=document_names
        )
        end = time.time()
        elapsed_time = end - start
        json_obj = {'api_name':'graph_query','db_url':uri, 'userName':userName, 'database':database, 'document_names':document_names, 'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{elapsed_time:.2f}','email':email}
        logger.log_struct(json_obj, "INFO")
        return create_api_response('Success', data=result,message=f"Total elapsed API time {elapsed_time:.2f}")
    except Exception as e:
        job_status = "Failed"
        message = "Unable to get graph query response"
        error_message = str(e)
        logging.exception(f'Exception in graph query: {error_message}')
        return create_api_response(job_status, message=message, error=error_message)
    finally:
        gc.collect()
    

@app.post("/clear_chat_bot")
async def clear_chat_bot(uri=Form(None),userName=Form(None), password=Form(None), database=Form(None), session_id=Form(None),email=Form(None)):
    try:
        start = time.time()
        graph = create_graph_database_connection(uri, userName, password, database)
        result = await asyncio.to_thread(clear_chat_history,graph=graph,session_id=session_id)
        end = time.time()
        elapsed_time = end - start
        json_obj = {'api_name':'clear_chat_bot', 'db_url':uri, 'userName':userName, 'database':database, 'session_id':session_id, 'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{elapsed_time:.2f}','email':email}
        logger.log_struct(json_obj, "INFO")
        return create_api_response('Success',data=result)
    except Exception as e:
        job_status = "Failed"
        message="Unable to clear chat History"
        error_message = str(e)
        logging.exception(f'Exception in chat bot:{error_message}')
        return create_api_response(job_status, message=message, error=error_message)
    finally:
        gc.collect()
            
@app.post("/connect")
async def connect(uri=Form(None), userName=Form(None), password=Form(None), database=Form(None),email=Form(None)):
    try:
        start = time.time()
        graph = create_graph_database_connection(uri, userName, password, database)
        result = await asyncio.to_thread(connection_check_and_get_vector_dimensions, graph, database)
        gcs_file_cache = os.environ.get('GCS_FILE_CACHE')
        end = time.time()
        elapsed_time = end - start
        json_obj = {'api_name':'connect','db_url':uri, 'userName':userName, 'database':database, 'count':1, 'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{elapsed_time:.2f}','email':email}
        logger.log_struct(json_obj, "INFO")
        result['elapsed_api_time'] = f'{elapsed_time:.2f}'
        result['gcs_file_cache'] = gcs_file_cache
        return create_api_response('Success',data=result)
    except Exception as e:
        job_status = "Failed"
        message="Connection failed to connect Neo4j database"
        error_message = str(e)
        logging.exception(f'Connection failed to connect Neo4j database:{error_message}')
        return create_api_response(job_status, message=message, error=error_message)

@app.post("/upload")
async def upload_large_file_into_chunks(file:UploadFile = File(...), chunkNumber=Form(None), totalChunks=Form(None), 
                                        originalname=Form(None), model=Form(None), uri=Form(None), userName=Form(None), 
                                        password=Form(None), database=Form(None),email=Form(None), generateEmbedding=Form(None)):
    try:
        start = time.time()
        
        # Debug: FastAPI Form field'ından gelen dosya ismini kontrol et
        logging.info(f"🔍 RAW originalname from FastAPI Form: {repr(originalname)}")
        logging.info(f"🔍 originalname type: {type(originalname)}")
        
        # FastAPI Form field'ları bazen bytes olarak gelebilir, decode etmeye çalış
        if isinstance(originalname, bytes):
            try:
                originalname = originalname.decode('utf-8')
                logging.info(f"🔄 Decoded bytes to UTF-8: {originalname}")
            except UnicodeDecodeError as e:
                logging.warning(f"⚠️ UTF-8 decode failed, trying latin-1: {e}")
                originalname = originalname.decode('latin-1')
                logging.info(f"🔄 Decoded bytes to latin-1: {originalname}")
        
        # Eğer string ama yanlış encode edilmişse (URL-encoded UTF-8 bytes), düzelt
        if isinstance(originalname, str) and '\\x' in originalname:
            try:
                # '\\xc3\\xa7' gibi escaped bytes'ları gerçek bytes'a çevir
                import codecs
                originalname_bytes = codecs.decode(originalname, 'unicode_escape').encode('latin-1')
                originalname = originalname_bytes.decode('utf-8')
                logging.info(f"🔄 Fixed escaped UTF-8 bytes: {originalname}")
            except Exception as e:
                logging.warning(f"⚠️ Failed to fix escaped UTF-8: {e}")
        
        logging.info(f"📤 Upload API called - File: {originalname}, Chunk: {chunkNumber}/{totalChunks}")
        logging.info(f"🔧 Upload parameters - Model: {model}, GenerateEmbedding: {generateEmbedding}")
        
        graph = create_graph_database_connection(uri, userName, password, database)
        result = await asyncio.to_thread(upload_file, graph, model, file, chunkNumber, totalChunks, originalname, uri, CHUNK_DIR, MERGED_DIR, generateEmbedding)
        
        end = time.time()
        elapsed_time = end - start
        
        logging.info(f"✅ Upload processing completed in {elapsed_time:.2f}s - Chunk: {chunkNumber}/{totalChunks}")
        
        if int(chunkNumber) == int(totalChunks):
            logging.info(f"🎉 Final chunk processed for {originalname} - Upload complete!")
            json_obj = {'api_name':'upload','db_url':uri,'userName':userName, 'database':database, 'chunkNumber':chunkNumber,'totalChunks':totalChunks,
                                'original_file_name':originalname,'model':model, 'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{elapsed_time:.2f}','email':email}
            logger.log_struct(json_obj, "INFO")
        if int(chunkNumber) == int(totalChunks):
            return create_api_response('Success',data=result, message='Source Node Created Successfully')
        else:
            return create_api_response('Success', message=result)
    except Exception as e:
        message="Unable to upload file in chunks"
        error_message = str(e)
        logging.error(f"❌ Upload failed for {originalname}, chunk {chunkNumber}/{totalChunks}: {error_message}")
        
        graph = create_graph_database_connection(uri, userName, password, database)   
        graphDb_data_Access = graphDBdataAccess(graph)
        graphDb_data_Access.update_exception_db(originalname,error_message)
        logging.info(message)
        logging.exception(f'Exception:{error_message}')
        return create_api_response('Failed', message=message + error_message[:100], error=error_message, file_name = originalname)
    finally:
        gc.collect()
            
@app.post("/schema")
async def get_structured_schema(uri=Form(None), userName=Form(None), password=Form(None), database=Form(None),email=Form(None)):
    try:
        start = time.time()
        result = await asyncio.to_thread(get_labels_and_relationtypes, uri, userName, password, database)
        end = time.time()
        elapsed_time = end - start
        logging.info(f'Schema result from DB: {result}')
        json_obj = {'api_name':'schema','db_url':uri, 'userName':userName, 'database':database, 'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{elapsed_time:.2f}','email':email}
        logger.log_struct(json_obj, "INFO")
        return create_api_response('Success', data=result,message=f"Total elapsed API time {elapsed_time:.2f}")
    except Exception as e:
        message="Unable to get the labels and relationtypes from neo4j database"
        error_message = str(e)
        logging.info(message)
        logging.exception(f'Exception:{error_message}')
        return create_api_response("Failed", message=message, error=error_message)
    finally:
        gc.collect()
            
def decode_password(pwd):
    sample_string_bytes = base64.b64decode(pwd)
    decoded_password = sample_string_bytes.decode("utf-8")
    return decoded_password

def encode_password(pwd):
    data_bytes = pwd.encode('ascii')
    encoded_pwd_bytes = base64.b64encode(data_bytes)
    return encoded_pwd_bytes

@app.get("/update_extract_status/{file_name}")
async def update_extract_status(request: Request, file_name: str, uri:str=None, userName:str=None, password:str=None, database:str=None):
    # URL decode the file name and normalize Unicode characters
    try:
        file_name = unquote(file_name)
        file_name = normalize_file_name(file_name)
        logging.info(f"Decoded and normalized file name: {file_name}")
    except Exception as e:
        logging.error(f"Error decoding/normalizing file name: {e}")
    
    async def generate():
        status = ''
        
        if password is not None and password != "null":
            decoded_password = decode_password(password)
        else:
            decoded_password = None

        url = uri
        if url and " " in url:
            url= url.replace(" ","+")
            
        graph = create_graph_database_connection(url, userName, decoded_password, database)
        graphDb_data_Access = graphDBdataAccess(graph)
        while True:
            try:
                if await request.is_disconnected():
                    logging.info(" SSE Client disconnected")
                    break
                # get the current status of document node
                
                else:
                    result = graphDb_data_Access.get_current_status_document_node(file_name)
                    if len(result) > 0:
                        status = json.dumps({'fileName':file_name, 
                        'status':result[0]['Status'],
                        'processingTime':result[0]['processingTime'],
                        'nodeCount':result[0]['nodeCount'],
                        'relationshipCount':result[0]['relationshipCount'],
                        'model':result[0]['model'],
                        'total_chunks':result[0]['total_chunks'],
                        'fileSize':result[0]['fileSize'],
                        'processed_chunk':result[0]['processed_chunk'],
                        'fileSource':result[0]['fileSource'],
                        'chunkNodeCount' : result[0]['chunkNodeCount'],
                        'chunkRelCount' : result[0]['chunkRelCount'],
                        'entityNodeCount' : result[0]['entityNodeCount'],
                        'entityEntityRelCount' : result[0]['entityEntityRelCount'],
                        'communityNodeCount' : result[0]['communityNodeCount'],
                        'communityRelCount' : result[0]['communityRelCount']
                        })
                    yield status
            except asyncio.CancelledError:
                logging.info("SSE Connection cancelled")
    
    return EventSourceResponse(generate(),ping=60)

@app.post("/delete_document_and_entities")
async def delete_document_and_entities(uri=Form(None), 
                                       userName=Form(None), 
                                       password=Form(None), 
                                       database=Form(None), 
                                       filenames=Form(),
                                       source_types=Form(),
                                       deleteEntities=Form(),
                                       email=Form(None)):
    try:
        start = time.time()
        graph = create_graph_database_connection(uri, userName, password, database)
        graphDb_data_Access = graphDBdataAccess(graph)
        files_list_size = await asyncio.to_thread(graphDb_data_Access.delete_file_from_graph, filenames, source_types, deleteEntities, MERGED_DIR, uri)
        message = f"Deleted {files_list_size} documents with entities from database"
        end = time.time()
        elapsed_time = end - start
        json_obj = {'api_name':'delete_document_and_entities','db_url':uri, 'userName':userName, 'database':database, 'filenames':filenames,'deleteEntities':deleteEntities,
                            'source_types':source_types, 'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{elapsed_time:.2f}','email':email}
        logger.log_struct(json_obj, "INFO")
        return create_api_response('Success',message=message)
    except Exception as e:
        job_status = "Failed"
        message=f"Unable to delete document {filenames}"
        error_message = str(e)
        logging.exception(f'{message}:{error_message}')
        return create_api_response(job_status, message=message, error=error_message)
    finally:
        gc.collect()

@app.get('/document_status/{file_name}')
async def get_document_status(file_name, url, userName, password, database):
    # URL decode and normalize the file name
    try:
        file_name = unquote(file_name)
        file_name = normalize_file_name(file_name)
        logging.info(f"Getting status for normalized file name: {file_name}")
    except Exception as e:
        logging.error(f"Error decoding/normalizing file name: {e}")
    
    decoded_password = decode_password(password)
   
    try:
        if " " in url:
            uri= url.replace(" ","+")
        else:
            uri=url
        graph = create_graph_database_connection(uri, userName, decoded_password, database)
        graphDb_data_Access = graphDBdataAccess(graph)
        result = graphDb_data_Access.get_current_status_document_node(file_name)
        if len(result) > 0:
            status = {'fileName':file_name, 
                'status':result[0]['Status'],
                'processingTime':result[0]['processingTime'],
                'nodeCount':result[0]['nodeCount'],
                'relationshipCount':result[0]['relationshipCount'],
                'model':result[0]['model'],
                'total_chunks':result[0]['total_chunks'],
                'fileSize':result[0]['fileSize'],
                'processed_chunk':result[0]['processed_chunk'],
                'fileSource':result[0]['fileSource'],
                'chunkNodeCount' : result[0]['chunkNodeCount'],
                'chunkRelCount' : result[0]['chunkRelCount'],
                'entityNodeCount' : result[0]['entityNodeCount'],
                'entityEntityRelCount' : result[0]['entityEntityRelCount'],
                'communityNodeCount' : result[0]['communityNodeCount'],
                'communityRelCount' : result[0]['communityRelCount']
                }
        else:
            status = {'fileName':file_name, 'status':'Failed'}
        logging.info(f'Result of document status in refresh : {result}')
        return create_api_response('Success',message="",file_name=status)
    except Exception as e:
        message=f"Unable to get the document status"
        error_message = str(e)
        logging.exception(f'{message}:{error_message}')
        return create_api_response('Failed',message=message)
    
@app.post("/cancelled_job")
async def cancelled_job(uri=Form(None), userName=Form(None), password=Form(None), database=Form(None), filenames=Form(None), source_types=Form(None),email=Form(None)):
    try:
        start = time.time()
        graph = create_graph_database_connection(uri, userName, password, database)
        result = manually_cancelled_job(graph,filenames, source_types, MERGED_DIR, uri)
        end = time.time()
        elapsed_time = end - start
        json_obj = {'api_name':'cancelled_job','db_url':uri, 'userName':userName, 'database':database, 'filenames':filenames,
                            'source_types':source_types, 'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{elapsed_time:.2f}','email':email}
        logger.log_struct(json_obj, "INFO")
        return create_api_response('Success',message=result)
    except Exception as e:
        job_status = "Failed"
        message="Unable to cancelled the running job"
        error_message = str(e)
        logging.exception(f'Exception in cancelling the running job:{error_message}')
        return create_api_response(job_status, message=message, error=error_message)
    finally:
        gc.collect()

@app.post("/populate_graph_schema")
async def populate_graph_schema(input_text=Form(None), model=Form(None), is_schema_description_checked=Form(None),is_local_storage=Form(None),email=Form(None)):
    try:
        start = time.time()
        result = populate_graph_schema_from_text(input_text, model, is_schema_description_checked, is_local_storage)
        end = time.time()
        elapsed_time = end - start
        json_obj = {'api_name':'populate_graph_schema', 'model':model, 'is_schema_description_checked':is_schema_description_checked, 'input_text':input_text, 'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{elapsed_time:.2f}','email':email}
        logger.log_struct(json_obj, "INFO")
        return create_api_response('Success',data=result)
    except Exception as e:
        job_status = "Failed"
        message="Unable to get the schema from text"
        error_message = str(e)
        logging.exception(f'Exception in getting the schema from text:{error_message}')
        return create_api_response(job_status, message=message, error=error_message)
    finally:
        gc.collect()
        
@app.post("/get_unconnected_nodes_list")
async def get_unconnected_nodes_list(uri=Form(None), userName=Form(None), password=Form(None), database=Form(None),email=Form(None)):
    try:
        start = time.time()
        graph = create_graph_database_connection(uri, userName, password, database)
        graphDb_data_Access = graphDBdataAccess(graph)
        nodes_list, total_nodes = graphDb_data_Access.list_unconnected_nodes()
        end = time.time()
        elapsed_time = end - start
        json_obj = {'api_name':'get_unconnected_nodes_list','db_url':uri, 'userName':userName, 'database':database, 'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{elapsed_time:.2f}','email':email}
        logger.log_struct(json_obj, "INFO")
        return create_api_response('Success',data=nodes_list,message=total_nodes)
    except Exception as e:
        job_status = "Failed"
        message="Unable to get the list of unconnected nodes"
        error_message = str(e)
        logging.exception(f'Exception in getting list of unconnected nodes:{error_message}')
        return create_api_response(job_status, message=message, error=error_message)
    finally:
        gc.collect()
        
@app.post("/delete_unconnected_nodes")
async def delete_orphan_nodes(uri=Form(None), userName=Form(None), password=Form(None), database=Form(None),unconnected_entities_list=Form(),email=Form(None)):
    try:
        start = time.time()
        graph = create_graph_database_connection(uri, userName, password, database)
        graphDb_data_Access = graphDBdataAccess(graph)
        result = graphDb_data_Access.delete_unconnected_nodes(unconnected_entities_list)
        end = time.time()
        elapsed_time = end - start
        json_obj = {'api_name':'delete_unconnected_nodes','db_url':uri, 'userName':userName, 'database':database,'unconnected_entities_list':unconnected_entities_list, 'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{elapsed_time:.2f}','email':email}
        logger.log_struct(json_obj, "INFO")
        return create_api_response('Success',data=result,message="Unconnected entities delete successfully")
    except Exception as e:
        job_status = "Failed"
        message="Unable to delete the unconnected nodes"
        error_message = str(e)
        logging.exception(f'Exception in delete the unconnected nodes:{error_message}')
        return create_api_response(job_status, message=message, error=error_message)
    finally:
        gc.collect()
        
@app.post("/get_duplicate_nodes")
async def get_duplicate_nodes(uri=Form(None), userName=Form(None), password=Form(None), database=Form(None),email=Form(None)):
    try:
        start = time.time()
        graph = create_graph_database_connection(uri, userName, password, database)
        graphDb_data_Access = graphDBdataAccess(graph)
        nodes_list, total_nodes = graphDb_data_Access.get_duplicate_nodes_list()
        end = time.time()
        elapsed_time = end - start
        json_obj = {'api_name':'get_duplicate_nodes','db_url':uri,'userName':userName, 'database':database, 'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{elapsed_time:.2f}','email':email}
        logger.log_struct(json_obj, "INFO")
        return create_api_response('Success',data=nodes_list, message=total_nodes)
    except Exception as e:
        job_status = "Failed"
        message="Unable to get the list of duplicate nodes"
        error_message = str(e)
        logging.exception(f'Exception in getting list of duplicate nodes:{error_message}')
        return create_api_response(job_status, message=message, error=error_message)
    finally:
        gc.collect()
        
@app.post("/merge_duplicate_nodes")
async def merge_duplicate_nodes(uri=Form(None), userName=Form(None), password=Form(None), database=Form(None),duplicate_nodes_list=Form(),email=Form(None)):
    try:
        start = time.time()
        graph = create_graph_database_connection(uri, userName, password, database)
        graphDb_data_Access = graphDBdataAccess(graph)
        result = graphDb_data_Access.merge_duplicate_nodes(duplicate_nodes_list)
        end = time.time()
        elapsed_time = end - start
        json_obj = {'api_name':'merge_duplicate_nodes','db_url':uri, 'userName':userName, 'database':database,
                            'duplicate_nodes_list':duplicate_nodes_list, 'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{elapsed_time:.2f}','email':email}
        logger.log_struct(json_obj, "INFO")
        return create_api_response('Success',data=result,message="Duplicate entities merged successfully")
    except Exception as e:
        job_status = "Failed"
        message="Unable to merge the duplicate nodes"
        error_message = str(e)
        logging.exception(f'Exception in merge the duplicate nodes:{error_message}')
        return create_api_response(job_status, message=message, error=error_message)
    finally:
        gc.collect()
        
@app.post("/drop_create_vector_index")
async def drop_create_vector_index(uri=Form(None), userName=Form(None), password=Form(None), database=Form(None), isVectorIndexExist=Form(),email=Form(None)):
    try:
        start = time.time()
        graph = create_graph_database_connection(uri, userName, password, database)
        graphDb_data_Access = graphDBdataAccess(graph)
        result = graphDb_data_Access.drop_create_vector_index(isVectorIndexExist)
        end = time.time()
        elapsed_time = end - start
        json_obj = {'api_name':'drop_create_vector_index', 'db_url':uri, 'userName':userName, 'database':database,
                            'isVectorIndexExist':isVectorIndexExist, 'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{elapsed_time:.2f}','email':email}
        logger.log_struct(json_obj, "INFO")
        return create_api_response('Success',message=result)
    except Exception as e:
        job_status = "Failed"
        message="Unable to drop and re-create vector index with correct dimesion as per application configuration"
        error_message = str(e)
        logging.exception(f'Exception into drop and re-create vector index with correct dimesion as per application configuration:{error_message}')
        return create_api_response(job_status, message=message, error=error_message)
    finally:
        gc.collect()
        
@app.post("/retry_processing")
async def retry_processing(uri=Form(None), userName=Form(None), password=Form(None), database=Form(None), file_name=Form(), retry_condition=Form(), email=Form(None)):
    try:
        start = time.time()
        graph = create_graph_database_connection(uri, userName, password, database)
        chunks = execute_graph_query(graph,QUERY_TO_GET_CHUNKS,params={"filename":file_name})
        end = time.time()
        elapsed_time = end - start
        json_obj = {'api_name':'retry_processing', 'db_url':uri, 'userName':userName, 'database':database, 'file_name':file_name,'retry_condition':retry_condition,
                            'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{elapsed_time:.2f}','email':email}
        logger.log_struct(json_obj, "INFO")
        if chunks[0]['text'] is None or chunks[0]['text']=="" or not chunks :
            return create_api_response('Success',message=f"Chunks are not created for the file{file_name}. Please upload again the file to re-process.",data=chunks)
        else:
            await asyncio.to_thread(set_status_retry, graph,file_name,retry_condition)
            return create_api_response('Success',message=f"Status set to Ready to Reprocess for filename : {file_name}")
    except Exception as e:
        job_status = "Failed"
        message="Unable to set status to Retry"
        error_message = str(e)
        logging.exception(f'{error_message}')
        return create_api_response(job_status, message=message, error=error_message)
    finally:
        gc.collect()    

@app.post('/metric')
async def calculate_metric(question: str = Form(),
                           context: str = Form(),
                           answer: str = Form(),
                           model: str = Form(),
                           mode: str = Form()):
    try:
        start = time.time()
        context_list = [str(item).strip() for item in json.loads(context)] if context else []
        answer_list = [str(item).strip() for item in json.loads(answer)] if answer else []
        mode_list = [str(item).strip() for item in json.loads(mode)] if mode else []

        result = await asyncio.to_thread(
            get_ragas_metrics, question, context_list, answer_list, model
        )
        if result is None or "error" in result:
            return create_api_response(
                'Failed',
                message='Failed to calculate evaluation metrics.',
                error=result.get("error", "Ragas evaluation returned null")
            )
        data = {mode: {metric: result[metric][i] for metric in result} for i, mode in enumerate(mode_list)}
        end = time.time()
        elapsed_time = end - start
        json_obj = {'api_name':'metric', 'question':question, 'context':context, 'answer':answer, 'model':model,'mode':mode,
                            'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{elapsed_time:.2f}'}
        logger.log_struct(json_obj, "INFO")
        return create_api_response('Success', data=data)
    except Exception as e:
        logging.exception(f"Error while calculating evaluation metrics: {e}")
        return create_api_response(
            'Failed',
            message="Error while calculating evaluation metrics",
            error=str(e)
        )
    finally:
        gc.collect()
       

@app.post('/additional_metrics')
async def calculate_additional_metrics(question: str = Form(),
                                        context: str = Form(),
                                        answer: str = Form(),
                                        reference: str = Form(),
                                        model: str = Form(),
                                        mode: str = Form(),
):
   try:
       context_list = [str(item).strip() for item in json.loads(context)] if context else []
       answer_list = [str(item).strip() for item in json.loads(answer)] if answer else []
       mode_list = [str(item).strip() for item in json.loads(mode)] if mode else []
       result = await get_additional_metrics(question, context_list,answer_list, reference, model)
       if result is None or "error" in result:
           return create_api_response(
               'Failed',
               message='Failed to calculate evaluation metrics.',
               error=result.get("error", "Ragas evaluation returned null")
           )
       data = {mode: {metric: result[i][metric] for metric in result[i]} for i, mode in enumerate(mode_list)}
       return create_api_response('Success', data=data)
   except Exception as e:
       logging.exception(f"Error while calculating evaluation metrics: {e}")
       return create_api_response(
           'Failed',
           message="Error while calculating evaluation metrics",
           error=str(e)
       )
   finally:
       gc.collect()

@app.post("/fetch_chunktext")
async def fetch_chunktext(
   uri: str = Form(None),
   database: str = Form(None),
   userName: str = Form(None),
   password: str = Form(None),
   document_name: str = Form(),
   page_no: int = Form(1),
   email=Form(None)
):
   try:
       start = time.time()
       result = await asyncio.to_thread(
           get_chunktext_results,
           uri=uri,
           username=userName,
           password=password,
           database=database,
           document_name=document_name,
           page_no=page_no
       )
       end = time.time()
       elapsed_time = end - start
       json_obj = {
           'api_name': 'fetch_chunktext',
           'db_url': uri,
           'userName': userName,
           'database': database,
           'document_name': document_name,
           'page_no': page_no,
           'logging_time': formatted_time(datetime.now(timezone.utc)),
           'elapsed_api_time': f'{elapsed_time:.2f}',
           'email': email
       }
       logger.log_struct(json_obj, "INFO")
       return create_api_response('Success', data=result, message=f"Total elapsed API time {elapsed_time:.2f}")
   except Exception as e:
       job_status = "Failed"
       message = "Unable to get chunk text response"
       error_message = str(e)
       logging.exception(f'Exception in fetch_chunktext: {error_message}')
       return create_api_response(job_status, message=message, error=error_message)
   finally:
       gc.collect()


@app.post("/backend_connection_configuration")
async def backend_connection_configuration():
    try:
        start = time.time()
        uri = os.getenv('NEO4J_URI')
        username= os.getenv('NEO4J_USERNAME')
        database= os.getenv('NEO4J_DATABASE')
        password= os.getenv('NEO4J_PASSWORD')
        gcs_file_cache = os.environ.get('GCS_FILE_CACHE')
        if all([uri, username, database, password]):
            graph = Neo4jGraph()
            logging.info(f'login connection status of object: {graph}')
            if graph is not None:
                graph_connection = True        
                graphDb_data_Access = graphDBdataAccess(graph)
                result = graphDb_data_Access.connection_check_and_get_vector_dimensions(database)
                result['gcs_file_cache'] = gcs_file_cache
                result['uri'] = uri
                end = time.time()
                elapsed_time = end - start
                result['api_name'] = 'backend_connection_configuration'
                result['elapsed_api_time'] = f'{elapsed_time:.2f}'
                result['graph_connection'] = f'{graph_connection}',
                result['connection_from'] = 'backendAPI'
                logger.log_struct(result, "INFO")
                return create_api_response('Success',message=f"Backend connection successful",data=result)
        else:
            graph_connection = False
            return create_api_response('Success',message=f"Backend connection is not successful",data=graph_connection)
    except Exception as e:
        graph_connection = False
        job_status = "Failed"
        message="Unable to connect backend DB"
        error_message = str(e)
        logging.exception(f'{error_message}')
        return create_api_response(job_status, message=message, error=error_message.rstrip('.') + ', or fill from the login dialog.', data=graph_connection)
    finally:
        gc.collect()
    
@app.post("/schema_visualization")
async def get_schema_visualization(uri=Form(None), userName=Form(None), password=Form(None), database=Form(None)):
    try:
        start = time.time()
        result = await asyncio.to_thread(visualize_schema,
           uri=uri,
           userName=userName,
           password=password,
           database=database)
        if result:
            logging.info("Graph schema visualization query successful")
        end = time.time()
        elapsed_time = end - start
        logging.info(f'Schema result from DB: {result}')
        json_obj = {'api_name':'schema_visualization','db_url':uri, 'userName':userName, 'database':database, 'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{elapsed_time:.2f}'}
        logger.log_struct(json_obj, "INFO")
        return create_api_response('Success', data=result,message=f"Total elapsed API time {elapsed_time:.2f}")
    except Exception as e:
        message="Unable to get schema visualization from neo4j database"
        error_message = str(e)
        logging.exception(f'Exception:{error_message}')
        return create_api_response('Failed', message=message, error=error_message)

@app.get("/document_analytics")
async def document_analytics(uri=Form(None), userName=Form(None), password=Form(None), database=Form(None), analysis_type=Form("overview")):
    """
    Document relationship analytics endpoint
    
    analysis_type options:
    - overview: Genel document relationship istatistikleri
    - person_policies: Kişi bazlı poliçe analizi  
    - company_analysis: Şirket bazlı analiz
    - person_search: Belirli kişi arama (person_name parametresi gerekli)
    """
    try:
        start_time = time.time()
        graph = create_graph_database_connection(uri, userName, password, database)
        
        if analysis_type == "overview":
            result = get_document_relationship_stats(graph)
            api_name = 'document_analytics/overview'
            
        elif analysis_type == "person_policies":
            result = get_person_policy_analytics(graph)
            api_name = 'document_analytics/person_policies'
            
        elif analysis_type == "company_analysis":
            result = get_company_analytics(graph)
            api_name = 'document_analytics/company_analysis'
            
        else:
            result = {"error": f"Unknown analysis_type: {analysis_type}"}
            api_name = 'document_analytics/error'
        
        elapsed_time = time.time() - start_time
        json_obj = {
            'api_name': api_name, 
            'db_url': uri, 
            'userName': userName, 
            'database': database, 
            'logging_time': formatted_time(datetime.now(timezone.utc)), 
            'elapsed_api_time': f'{elapsed_time:.2f}',
            'analysis_type': analysis_type
        }
        logger.log_struct(json_obj, "INFO")
        
        return create_api_response('Success', data=result, message=f"Analysis completed in {elapsed_time:.2f} seconds")
        
    except Exception as e:
        message = f"Unable to complete document analytics: {analysis_type}"
        error_message = str(e)
        logging.exception(f'Exception in document_analytics: {error_message}')
        return create_api_response('Failed', message=message, error=error_message)

@app.post("/search_person_documents")
async def search_person_documents_endpoint(uri=Form(None), userName=Form(None), password=Form(None), database=Form(None), person_name=Form(None)):
    """
    Belirli bir kişinin tüm document'larını arar
    """
    try:
        if not person_name:
            return create_api_response('Failed', message="person_name parameter is required")
            
        start_time = time.time()
        graph = create_graph_database_connection(uri, userName, password, database)
        
        result = search_person_documents(graph, person_name)
        
        elapsed_time = time.time() - start_time
        json_obj = {
            'api_name': 'search_person_documents', 
            'db_url': uri, 
            'userName': userName, 
            'database': database, 
            'logging_time': formatted_time(datetime.now(timezone.utc)), 
            'elapsed_api_time': f'{elapsed_time:.2f}',
            'person_name': person_name
        }
        logger.log_struct(json_obj, "INFO")
        
        return create_api_response('Success', data=result, message=f"Search completed in {elapsed_time:.2f} seconds")
        
    except Exception as e:
        message = f"Unable to search documents for person: {person_name}"
        error_message = str(e)
        logging.exception(f'Exception in search_person_documents: {error_message}')
        return create_api_response('Failed', message=message, error=error_message)
        error_message = str(e)
        logging.info(message)
        logging.exception(f'Exception:{error_message}')
        return create_api_response("Failed", message=message, error=error_message)
    finally:
        gc.collect()

@app.post("/intelligent_search")
async def intelligent_search_endpoint(uri=Form(None), userName=Form(None), password=Form(None), database=Form(None), question=Form(None), model=Form("openai_gpt_4o")):
    """
    ReAct pattern kullanan intelligent agent ile akıllı arama
    """
    try:
        if not question:
            return create_api_response('Failed', message="question parameter is required")
            
        start_time = time.time()
        
        # Neo4j bağlantısı oluştur
        graph = create_graph_database_connection(uri, userName, password, database)
        
        # Intelligent agent'ı oluştur
        agent = IntelligentAgent(graph, model_name=model)
        
        # Soruyu çöz
        result = agent.solve_question(question)
        
        elapsed_time = time.time() - start_time
        
        # Logging
        json_obj = {
            'api_name': 'intelligent_search', 
            'db_url': uri, 
            'userName': userName, 
            'database': database, 
            'logging_time': formatted_time(datetime.now(timezone.utc)), 
            'elapsed_api_time': f'{elapsed_time:.2f}',
            'question': question,
            'model': model,
            'iterations': result.get('iterations', 0)
        }
        logger.log_struct(json_obj, "INFO")
        
        return create_api_response('Success', data=result, message=f"Intelligent search completed in {elapsed_time:.2f} seconds")
        
    except Exception as e:
        message = f"Unable to complete intelligent search for question: {question}"
        error_message = str(e)
        logging.exception(f'Exception in intelligent_search: {error_message}')
        return create_api_response('Failed', message=message, error=error_message)
    finally:
        gc.collect()


if __name__ == "__main__":
    uvicorn.run(app)