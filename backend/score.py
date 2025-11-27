# -*- coding: utf-8 -*-
import os
import sys
import logging
import importlib.util

# OpenTelemetry configuration - Jaeger collector için
# Jaeger OTLP endpoint: localhost:4318 (HTTP) veya localhost:4317 (gRPC)
os.environ.setdefault("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")  # Jaeger OTLP HTTP endpoint
os.environ.setdefault("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")  # HTTP protobuf protocol
os.environ.setdefault("OTEL_SERVICE_NAME", "llm-graph-builder")  # Service name for traces

# Ensure UTF-8 encoding for Turkish characters
if sys.stdout.encoding != "utf-8":
    import codecs

    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer)
    sys.stderr = codecs.getwriter("utf-8")(sys.stderr.buffer)

from fastapi import FastAPI, File, UploadFile, Form, Request, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi_health import health
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
# Import only necessary functions from src.main (not * to avoid loading processing dependencies)
from src.main import (
    create_source_node_graph_url_s3,
    create_source_node_graph_url_gcs,
    create_source_node_graph_url_youtube,
    create_source_node_graph_url_wikipedia,
    create_source_node_graph_web_url,
    get_source_list_from_graph,
    update_graph,
    connection_check_and_get_vector_dimensions,
    get_labels_and_relationtypes,
    manually_cancelled_job,
    populate_graph_schema_from_text,
    set_status_retry,
)
from src.QA_integration import QA_RAG, QA_RAG_stream, clear_chat_history
from src.intelligent_agent import IntelligentAgent
from src.workflow.fast_agent_integration_simple import stream_fast_agent_response
from src.qa_based_entity_extractor import (
    QABasedEntityExtractor,
    create_domain_specific_questions,
)
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
from src.graph_query import get_graph_results, get_chunktext_results, visualize_schema
from src.chunkid_entities import get_entities_from_chunkids
from src.post_processing import (
    create_vector_fulltext_indexes,
    create_entity_embedding,
    graph_schema_consolidation,
)
from src.document_analytics import (
    get_person_policy_analytics,
    get_company_analytics,
    get_document_relationship_stats,
    search_person_documents,
)
from sse_starlette.sse import EventSourceResponse
from src.communities import create_communities
from src.neighbours import get_neighbour_nodes
import json
from typing import List, Optional, Dict
from google.oauth2.credentials import Credentials
import os
import re
from urllib.parse import unquote
from src.utf8_utils import normalize_file_name
from src.logger import CustomLogger
from src.celery_client import celery_app
from src.models.file_queue_models import get_file_queue_db

# Gemini API for markdown extraction (New SDK: google-genai 1.48.0+)
try:
    from google import genai as genai_sdk

    GEMINI_AVAILABLE = True
except ImportError:
    genai = None
from src.device_utils import (
    get_optimal_device,
    print_device_info,
    optimize_for_apple_silicon,
)
from datetime import datetime, timezone
import time
import gc
# Secweb is optional - only needed for security headers
try:
    from Secweb.XContentTypeOptions import XContentTypeOptions
    from Secweb.XFrameOptions import XFrame
except (ImportError, ModuleNotFoundError):
    # Security headers are optional
    XContentTypeOptions = None
    XFrame = None
from fastapi.middleware.gzip import GZipMiddleware
from src.ragas_eval import *
from starlette.types import ASGIApp, Receive, Scope, Send
from langchain_neo4j import Neo4jGraph
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from dotenv import load_dotenv
import tempfile
from pathlib import Path
import time
import json
import logging
import shutil


# HTTP Request Logging Middleware for OpenTelemetry
class HTTPLoggingMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app
        self.logger = logging.getLogger("http_requests")

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

        # Skip logging for V2 list and status endpoints (too frequent)
        skip_paths = [
            "/api/v2/files/list",
            "/api/v2/files/status",
            "/api/v2/processing/status",
        ]
        
        if path in skip_paths:
            return

        # Create log message
        log_message = f"🌐 HTTP {method} {path} - {response_status} ({duration:.3f}s)"

        self.logger.info(
            log_message,
            extra={
                "component": "http_server",
                "operation": "http_request",
                "method": method,
                "path": path,
                "status_code": response_status,
                "duration_ms": round(duration * 1000, 2),
                "client_ip": client_ip,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.%3fZ", time.gmtime()),
            },
        )


try:
    from docling.document_converter import DocumentConverter

    DOCLING_AVAILABLE = True
except ImportError:
    DOCLING_AVAILABLE = False

# docling_core is only needed for document processing, which is done in celery_worker
try:
    from docling_core.types.doc import ImageRefMode, DocItemLabel
except (ImportError, ModuleNotFoundError):
    ImageRefMode = None
    DocItemLabel = None  # Document processing is in celery_worker
# DEFAULT_EXPORT_LABELS is only needed for document processing, which is done in celery_worker
try:
    from docling_core.types.doc.document import DEFAULT_EXPORT_LABELS
except (ImportError, ModuleNotFoundError):
    DEFAULT_EXPORT_LABELS = None  # Document processing is in celery_worker

load_dotenv(override=False)  # Don't override environment variables set by Docker Compose

from pathlib import Path
from typing import Dict, List
import requests
import shutil
import subprocess
# PyPDF2 is only needed for PDF processing, which is done in celery_worker
try:
    from PyPDF2 import PdfReader
except (ImportError, ModuleNotFoundError):
    PdfReader = None  # PDF processing is in celery_worker
# pdf2image is only needed for PDF processing, which is done in celery_worker
try:
    from pdf2image import convert_from_path
except (ImportError, ModuleNotFoundError):
    convert_from_path = None  # PDF processing is in celery_worker

logger = CustomLogger()

# 🚀 SESSION-BASED INTELLIGENT AGENT CACHE
# Agent'ları session ID'ye göre memory'de tut - performance optimization
_agent_cache: Dict[str, IntelligentAgent] = {}
_cache_access_times: Dict[str, float] = {}  # LRU tracking için access time'ları

# 📊 CACHE CONFIGURATION PARAMETERS
AGENT_CACHE_MAX_SIZE = int(
    os.environ.get("AGENT_CACHE_MAX_SIZE", "500")
)  # Max session sayısı
AGENT_CACHE_CLEANUP_COUNT = int(
    os.environ.get("AGENT_CACHE_CLEANUP_COUNT", "50")
)  # Temizleme sırasında silinecek session sayısı


def get_cached_agent(
    session_id: str, graph: Neo4jGraph, model_name: str
) -> IntelligentAgent:
    """
    Session ID'ye göre cache'lenmiş agent'ı döndür veya yeni oluştur
    LRU (Least Recently Used) cache mantığı ile memory management
    """
    import time

    try:
        current_time = time.time()

        # Önce cache'te var mı diye kontrol et
        if session_id in _agent_cache:
            cached_agent = _agent_cache[session_id]

            # LRU için access time'ını güncelle
            _cache_access_times[session_id] = current_time

            print(f"✅ Cached agent bulundu - Session: {session_id}")

            # Graph instance'ını güncelle (bağlantı değişmiş olabilir)
            cached_agent.graph = graph
            cached_agent.current_session_id = session_id

            return cached_agent

        # Cache'te yok, yeni agent oluştur
        print(f"🆕 Yeni agent oluşturuluyor - Session: {session_id}")
        new_agent = IntelligentAgent(graph, model_name=model_name)
        new_agent.current_session_id = session_id

        # Cache'e ekle
        _agent_cache[session_id] = new_agent
        _cache_access_times[session_id] = current_time

        # LRU Cache boyutu kontrolü (global parametrelerle)
        if len(_agent_cache) > AGENT_CACHE_MAX_SIZE:
            # En az kullanılan session'ları bul (LRU mantığı)
            sorted_sessions = sorted(
                _cache_access_times.items(),
                key=lambda x: x[1],  # access time'a göre sırala
            )

            # Konfigüre edilebilir sayıda session'ı sil
            sessions_to_remove = sorted_sessions[:AGENT_CACHE_CLEANUP_COUNT]
            removed_sessions = []

            for session_to_remove, last_access in sessions_to_remove:
                if session_to_remove in _agent_cache:
                    del _agent_cache[session_to_remove]
                    del _cache_access_times[session_to_remove]
                    removed_sessions.append(session_to_remove)

            print(
                f"🧹 LRU Cache temizlendi: {len(removed_sessions)} eski session silindi"
            )
            print(
                f"   Cache limit: {AGENT_CACHE_MAX_SIZE}, cleanup size: {AGENT_CACHE_CLEANUP_COUNT}"
            )
            print(
                f"   Silinen sessions: {removed_sessions[:5]}{'...' if len(removed_sessions) > 5 else ''}"
            )

        print(
            f"✅ Agent cache'lendi - Session: {session_id} | Toplam cache: {len(_agent_cache)}"
        )
        return new_agent

    except Exception as e:
        print(f"❌ Agent cache hatası - Session: {session_id} | Hata: {e}")
        # Fallback: cache'siz yeni agent
        return IntelligentAgent(graph, model_name=model_name)


def get_cache_stats() -> Dict:
    """
    Agent cache istatistiklerini döndür
    """
    import time

    current_time = time.time()

    stats = {
        "total_sessions": len(_agent_cache),
        "max_capacity": AGENT_CACHE_MAX_SIZE,
        "cleanup_count": AGENT_CACHE_CLEANUP_COUNT,
        "usage_percentage": round((len(_agent_cache) / AGENT_CACHE_MAX_SIZE) * 100, 1),
        "sessions": [],
    }

    # Session'ları son erişim zamanına göre sırala
    if _cache_access_times:
        sorted_sessions = sorted(
            _cache_access_times.items(),
            key=lambda x: x[1],
            reverse=True,  # En yeni erişim en üstte
        )

        for session_id, last_access in sorted_sessions:
            minutes_ago = round((current_time - last_access) / 60, 1)
            stats["sessions"].append(
                {
                    "session_id": session_id,
                    "last_access_minutes_ago": minutes_ago,
                    "has_agent": session_id in _agent_cache,
                }
            )

    return stats


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
    safe_name = re.sub(r"[^\w\.-]", "_", filename)
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
            with open(cache_path, "r", encoding="utf-8") as f:
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

        with open(cache_path, "w", encoding="utf-8") as f:
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
        compresslevel: int = 5,
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
            compresslevel=self.compresslevel,
        )
        await gzip_middleware(scope, receive, send)


def convert_result_to_base64(
    result: Dict[str, List[Dict[str, str]]],
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
            base64_result[attachment_name].append(
                {"fileName": file_name, "base64": encoded}
            )

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
    """LibreOffice kullanarak dosyayı PDF'e çevirir"""
    output_path = os.path.join(PDF_TEMP_FOLDER, filename + ".pdf")
    subprocess.run(
        [
            "libreoffice",
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            PDF_TEMP_FOLDER,
            file_path,
        ],
        check=True,
    )
    return output_path


def get_pdf_page_count(pdf_path: str) -> int:
    reader = PdfReader(pdf_path)
    return len(reader.pages)


def pdf_to_images(pdf_path: str, output_base_name: str) -> list[str]:
    """PDF sayfalarını PNG'e çevirir"""
    images = convert_from_path(
        pdf_path,
        dpi=200,
        output_folder=IMAGE_OUTPUT_FOLDER,
        output_file=output_base_name,
        fmt="png",
        size=(1200, 1600),
    )
    image_paths = []
    for i, img in enumerate(images, start=1):
        output_path = os.path.join(
            IMAGE_OUTPUT_FOLDER, f"{output_base_name}_sayfa{i}.png"
        )
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
    attachments: Dict[str, List[Dict[str, str]]],
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
                        if key == b"content-type" and value.startswith(
                            b"application/json"
                        ):
                            headers[key] = b"application/json; charset=utf-8"
                    message["headers"] = list(headers.items())
                await send(message)

            await self.app(scope, receive, send_wrapper)
        else:
            await self.app(scope, receive, send)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    optimize_for_apple_silicon()
    print_device_info()

    # V2 background processor manuel başlatmaya ayarlı (otomatik başlatma devre dışı)
    # Background processor'ı başlatmak için /api/v2/processing/start endpoint'ini kullanın
    logging.info(
        "ℹ️ V2 Background processor manuel başlatmaya ayarlı. Başlatmak için /api/v2/processing/start endpoint'ini kullanın."
    )

    yield
    # Shutdown - here you can add cleanup code if needed
    try:
        # Celery worker runs independently - no need to stop
        logging.info("⏹️ Server shutdown - celery worker continues running independently")
    except asyncio.CancelledError:
        # Cancellation durumunda sessizce geç (normal shutdown)
        logging.info(
            "⏹️ Server shutdown cancelled, background processor cleanup skipped"
        )
        raise  # CancelledError'ı yeniden fırlat (lifespan context manager için gerekli)
    except Exception as bg_error:
        logging.warning(f"⚠️ Failed to stop background processor: {bg_error}")


app = FastAPI(lifespan=lifespan)

# Uvicorn access logger'ını kapat (HTTP request logları - çok gürültülü)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

# Add HTTP logging middleware for OpenTelemetry integration
app.add_middleware(HTTPLoggingMiddleware)

app.add_middleware(UTF8JSONResponse)
# Security headers are optional
if XContentTypeOptions is not None:
    app.add_middleware(XContentTypeOptions)
if XFrame is not None:
    app.add_middleware(XFrame, Option={"X-Frame-Options": "DENY"})
app.add_middleware(
    CustomGZipMiddleware,
    minimum_size=1000,
    compresslevel=5,
    paths=[
        "/sources_list",
        "/url/scan",
        "/extract",
        "/chat_bot",
        "/chat_bot_stream",
        "/chunk_entities",
        "/get_neighbours",
        "/graph_query",
        "/schema",
        "/populate_graph_schema",
        "/get_unconnected_nodes_list",
        "/get_duplicate_nodes",
        "/fetch_chunktext",
        "/schema_visualization",
    ],
)
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

is_gemini_enabled = os.environ.get("GEMINI_ENABLED", "False").lower() in (
    "true",
    "1",
    "yes",
)
if is_gemini_enabled:
    add_routes(app, ChatVertexAI(), path="/vertexai")

app.add_api_route("/health", health([healthy_condition, healthy]))


@app.get("/files/{file_name:path}")
async def serve_document_file(file_name: str):
    """
    S3'ten document dosyalarını serve eder.
    URL encoding sorununu çözmek için file_name:path kullanıyoruz.
    """
    try:
        # URL decode işlemi
        import urllib.parse

        decoded_file_name = urllib.parse.unquote(file_name, encoding="utf-8")

        if not S3_BACKUP_BUCKET or not AWS_ACCESS_KEY_ID or not AWS_SECRET_ACCESS_KEY:
            raise HTTPException(
                status_code=503, detail="S3 configuration not available"
            )

        # S3 key'ini tahmin et
        from pathlib import Path

        doc_name = Path(decoded_file_name).stem
        s3_key = f"documents/{doc_name}/{decoded_file_name}"

        # Presigned URL oluştur
        from src.document_sources.s3_upload_utils import generate_s3_presigned_url

        presigned_url = generate_s3_presigned_url(
            S3_BACKUP_BUCKET,
            s3_key,
            AWS_ACCESS_KEY_ID,
            AWS_SECRET_ACCESS_KEY,
            expiration=3600,
        )

        if not presigned_url:
            raise HTTPException(status_code=404, detail=f"File not found: {file_name}")

        # Redirect to presigned URL
        from fastapi.responses import RedirectResponse

        return RedirectResponse(url=presigned_url, status_code=302)

    except Exception as e:
        logging.error(f"Error serving document file {file_name}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/images/{image_name:path}")
async def serve_page_image(image_name: str):
    """
    S3'ten page image dosyalarını serve eder.
    URL encoding sorununu çözmek için image_name:path kullanıyoruz.
    """
    try:
        # URL decode işlemi
        import urllib.parse

        decoded_image_name = urllib.parse.unquote(image_name, encoding="utf-8")

        if not S3_BACKUP_BUCKET or not AWS_ACCESS_KEY_ID or not AWS_SECRET_ACCESS_KEY:
            raise HTTPException(
                status_code=503, detail="S3 configuration not available"
            )

        # S3 key'ini tahmin et (image name'den document adını çıkar)
        # Format: "doc_name_page_001.png"
        import re

        match = re.match(r"(.+)_page_\d+\.png$", decoded_image_name)
        if not match:
            raise HTTPException(status_code=400, detail="Invalid image name format")

        doc_name = match.group(1)
        s3_key = f"documents/{doc_name}/{decoded_image_name}"

        # Presigned URL oluştur
        from src.document_sources.s3_upload_utils import generate_s3_presigned_url

        presigned_url = generate_s3_presigned_url(
            S3_BACKUP_BUCKET,
            s3_key,
            AWS_ACCESS_KEY_ID,
            AWS_SECRET_ACCESS_KEY,
            expiration=3600,
        )

        if not presigned_url:
            raise HTTPException(
                status_code=404, detail=f"Image not found: {image_name}"
            )

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
    email=Form(None),
):

    try:
        start = time.time()
        if source_url is not None:
            source = source_url
        else:
            source = wiki_query

        graph = create_graph_database_connection(uri, userName, password, database)
        if source_type == "s3 bucket" and aws_access_key_id and aws_secret_access_key:
            lst_file_name, success_count, failed_count = await asyncio.to_thread(
                create_source_node_graph_url_s3,
                graph,
                model,
                source_url,
                aws_access_key_id,
                aws_secret_access_key,
                source_type,
            )
        elif source_type == "gcs bucket":
            lst_file_name, success_count, failed_count = await asyncio.to_thread(
                create_source_node_graph_url_gcs,
                graph,
                model,
                gcs_project_id,
                gcs_bucket_name,
                gcs_bucket_folder,
                source_type,
                Credentials(access_token),
            )
        elif source_type == "web-url":
            lst_file_name, success_count, failed_count = await asyncio.to_thread(
                create_source_node_graph_web_url, graph, model, source_url, source_type
            )
        elif source_type == "youtube":
            lst_file_name, success_count, failed_count = await asyncio.to_thread(
                create_source_node_graph_url_youtube,
                graph,
                model,
                source_url,
                source_type,
            )
        elif source_type == "Wikipedia":
            lst_file_name, success_count, failed_count = await asyncio.to_thread(
                create_source_node_graph_url_wikipedia,
                graph,
                model,
                wiki_query,
                source_type,
            )
        else:
            return create_api_response(
                "Failed", message="source_type is other than accepted source"
            )

        message = f"Source Node created successfully for source type: {source_type} and source: {source}"
        end = time.time()
        elapsed_time = end - start
        json_obj = {
            "api_name": "url_scan",
            "db_url": uri,
            "url_scanned_file": lst_file_name,
            "source_url": source_url,
            "wiki_query": wiki_query,
            "logging_time": formatted_time(datetime.now(timezone.utc)),
            "elapsed_api_time": f"{elapsed_time:.2f}",
            "userName": userName,
            "database": database,
            "aws_access_key_id": aws_access_key_id,
            "model": model,
            "gcs_bucket_name": gcs_bucket_name,
            "gcs_bucket_folder": gcs_bucket_folder,
            "source_type": source_type,
            "gcs_project_id": gcs_project_id,
            "logging_time": formatted_time(datetime.now(timezone.utc)),
            "email": email,
        }
        logger.log_struct(json_obj, "INFO")
        result = {"elapsed_api_time": f"{elapsed_time:.2f}"}
        return create_api_response(
            "Success",
            message=message,
            success_count=success_count,
            failed_count=failed_count,
            file_name=lst_file_name,
            data=result,
        )
    except LLMGraphBuilderException as e:
        error_message = str(e)
        message = f" Unable to create source node for source type: {source_type} and source: {source}"
        # Set the status "Success" becuase we are treating these error already handled by application as like custom errors.
        json_obj = {
            "error_message": error_message,
            "status": "Success",
            "db_url": uri,
            "userName": userName,
            "database": database,
            "success_count": 1,
            "source_type": source_type,
            "source_url": source_url,
            "wiki_query": wiki_query,
            "logging_time": formatted_time(datetime.now(timezone.utc)),
            "email": email,
        }
        logger.log_struct(json_obj, "INFO")
        logging.exception(f"File Failed in upload: {e}")
        return create_api_response(
            "Failed",
            message=message + error_message[:80],
            error=error_message,
            file_source=source_type,
        )
    except Exception as e:
        error_message = str(e)
        message = f" Unable to create source node for source type: {source_type} and source: {source}"
        json_obj = {
            "error_message": error_message,
            "status": "Failed",
            "db_url": uri,
            "userName": userName,
            "database": database,
            "failed_count": 1,
            "source_type": source_type,
            "source_url": source_url,
            "wiki_query": wiki_query,
            "logging_time": formatted_time(datetime.now(timezone.utc)),
            "email": email,
        }
        logger.log_struct(json_obj, "ERROR")
        logging.exception(f"Exception Stack trace upload:{e}")
        return create_api_response(
            "Failed",
            message=message + error_message[:80],
            error=error_message,
            file_source=source_type,
        )
    finally:
        gc.collect()


# @app.post("/extract")
# async def extract_knowledge_graph_from_file(
#     uri=Form(None),
#     userName=Form(None),
#     password=Form(None),
#     model=Form(),
#     database=Form(None),
#     source_url=Form(None),
#     aws_access_key_id=Form(None),
#     aws_secret_access_key=Form(None),
#     wiki_query=Form(None),
#     gcs_project_id=Form(None),
#     gcs_bucket_name=Form(None),
#     gcs_bucket_folder=Form(None),
#     gcs_blob_filename=Form(None),
#     source_type=Form(None),
#     file_name=Form(None),
#     allowedNodes=Form(None),
#     allowedRelationship=Form(None),
#     token_chunk_size: Optional[int] = Form(None),
#     chunk_overlap: Optional[int] = Form(None),
#     chunks_to_combine: Optional[int] = Form(None),
#     language=Form(None),
#     access_token=Form(None),
#     retry_condition=Form(None),
#     additional_instructions=Form(None),
#     # Sayfa sınırlandırma parametresi
#     max_pages: str = Form(None),  # String olarak al, sonra validate et
#     # Post-processing parametreleri
#     enable_post_processing=Form(False),
#     post_processing_rules=Form(None),  # JSON array: [{"sourceNodeType":"Year","targetNodeType":"Document","relationshipType":"HAS_YEAR","removeExistingRelationships":false}]
#     # Entity Promotion parametreleri
#     enable_entity_promotion=Form(True),  # Default olarak aktif
#     entity_promotion_rules=Form(None),   # JSON array: ["Address", "Company", "Person", "Phone", "Email"]
#     email=Form(None)
# ):
#     """
#     Calls 'extract_graph_from_file' in a new thread to create Neo4jGraph from a
#     PDF file based on the model.

#     Args:
#           uri: URI of the graph to extract
#           userName: Username to use for graph creation
#           password: Password to use for graph creation
#           file: File object containing the PDF file
#           model: Type of model to use ('Diffbot'or'OpenAI GPT')

#     Returns:
#           Nodes and Relations created in Neo4j databse for the pdf file
#     """
#     try:
#         start_time = time.time()

#         # max_pages validation - undefined string'i None'a çevir
#         validated_max_pages = None
#         if max_pages is not None and max_pages.strip() not in ['', 'undefined', 'null']:
#             try:
#                 validated_max_pages = int(max_pages)
#                 if validated_max_pages <= 0:
#                     validated_max_pages = None
#                     logging.info(f"ℹ️ max_pages değeri sıfır veya negatif, None olarak ayarlandı")
#             except (ValueError, TypeError) as e:
#                 logging.warning(f"⚠️ max_pages değeri geçersiz '{max_pages}', None olarak ayarlandı: {e}")
#                 validated_max_pages = None

#         logging.info(f"📊 max_pages validation: '{max_pages}' -> {validated_max_pages}")

#         graph = create_graph_database_connection(uri, userName, password, database)
#         graphDb_data_Access = graphDBdataAccess(graph)
#         if source_type == 'local file':
#             file_name = sanitize_filename(file_name)
#             merged_file_path = validate_file_path(MERGED_DIR, file_name)

#             # Debug loglama: Dosya yolu ve varlık kontrolü
#             logging.info(f"🔍 DEBUG - Original file_name: {file_name}")
#             logging.info(f"🔍 DEBUG - Sanitized file_name: {file_name}")
#             logging.info(f"🔍 DEBUG - MERGED_DIR: {MERGED_DIR}")
#             logging.info(f"🔍 DEBUG - Constructed merged_file_path: {merged_file_path}")
#             logging.info(f"🔍 DEBUG - File exists check: {os.path.exists(merged_file_path)}")

#             # Merged files klasöründeki tüm dosyaları listele
#             if os.path.exists(MERGED_DIR):
#                 files_in_dir = os.listdir(MERGED_DIR)
#                 logging.info(f"🔍 DEBUG - Files in {MERGED_DIR}: {files_in_dir}")

#                 # Dosya adı karşılaştırması
#                 for existing_file in files_in_dir:
#                     if existing_file == file_name:
#                         logging.info(f"✅ DEBUG - Exact match found: {existing_file}")
#                     else:
#                         logging.info(f"❌ DEBUG - No match: '{existing_file}' != '{file_name}'")
#                         logging.info(f"🔍 DEBUG - Bytes comparison: {existing_file.encode('utf-8')} vs {file_name.encode('utf-8')}")

#             # Dosya işleme başlamadan önce dosyanın varlığını kontrol et
#             if not os.path.exists(merged_file_path):
#                 # Unicode normalizasyon farklılıkları için alternatif dosya adlarını dene
#                 logging.warning(f"File not found with NFC normalization, trying NFD normalization")

#                 import unicodedata
#                 # NFD normalizasyonu dene (Decomposed)
#                 file_name_nfd = unicodedata.normalize('NFD', file_name)
#                 merged_file_path_nfd = validate_file_path(MERGED_DIR, file_name_nfd)

#                 logging.info(f"🔍 DEBUG - Trying NFD normalized file_name: {file_name_nfd}")
#                 logging.info(f"🔍 DEBUG - NFD file path: {merged_file_path_nfd}")
#                 logging.info(f"🔍 DEBUG - NFD file exists: {os.path.exists(merged_file_path_nfd)}")

#                 if os.path.exists(merged_file_path_nfd):
#                     logging.info(f"✅ Found file with NFD normalization: {merged_file_path_nfd}")
#                     merged_file_path = merged_file_path_nfd
#                     file_name = file_name_nfd
#                 else:
#                     # Her iki normalizasyon da başarısız, dosya gerçekten yok
#                     logging.warning(f"File {file_name} not found at {merged_file_path} - may have been deleted")
#                     raise LLMGraphBuilderException(f"File {file_name} is no longer available for processing")

#             uri_latency, result = await extract_graph_from_file_local_file(uri, userName, password, database, model, merged_file_path, file_name, allowedNodes, allowedRelationship, token_chunk_size, chunk_overlap, chunks_to_combine, retry_condition, additional_instructions, enable_post_processing, post_processing_rules, validated_max_pages)

#         elif source_type == 's3 bucket' and source_url:
#             uri_latency, result = await extract_graph_from_file_s3(uri, userName, password, database, model, source_url, aws_access_key_id, aws_secret_access_key, file_name, allowedNodes, allowedRelationship, token_chunk_size, chunk_overlap, chunks_to_combine, retry_condition, additional_instructions)

#         elif source_type == 'web-url':
#             uri_latency, result = await extract_graph_from_web_page(uri, userName, password, database, model, source_url, file_name, allowedNodes, allowedRelationship, token_chunk_size, chunk_overlap, chunks_to_combine, retry_condition, additional_instructions)

#         elif source_type == 'youtube' and source_url:
#             uri_latency, result = await extract_graph_from_file_youtube(uri, userName, password, database, model, source_url, file_name, allowedNodes, allowedRelationship, token_chunk_size, chunk_overlap, chunks_to_combine, retry_condition, additional_instructions)

#         elif source_type == 'Wikipedia' and wiki_query:
#             uri_latency, result = await extract_graph_from_file_Wikipedia(uri, userName, password, database, model, wiki_query, language, file_name, allowedNodes, allowedRelationship, token_chunk_size, chunk_overlap, chunks_to_combine, retry_condition, additional_instructions)

#         elif source_type == 'gcs bucket' and gcs_bucket_name:
#             uri_latency, result = await extract_graph_from_file_gcs(uri, userName, password, database, model, gcs_project_id, gcs_bucket_name, gcs_bucket_folder, gcs_blob_filename, access_token, file_name, allowedNodes, allowedRelationship, token_chunk_size, chunk_overlap, chunks_to_combine, retry_condition, additional_instructions)
#         else:
#             return create_api_response('Failed',message='source_type is other than accepted source')
#         extract_api_time = time.time() - start_time
#         if result is not None:
#             logging.info("Going for counting nodes and relationships in extract")
#             count_node_time = time.time()
#             graph = create_graph_database_connection(uri, userName, password, database)
#             graphDb_data_Access = graphDBdataAccess(graph)
#             # Thread'e taşı - blocking işlem
#             count_response = await asyncio.to_thread(graphDb_data_Access.update_node_relationship_count, file_name)
#             logging.info("Nodes and Relationship Counts updated")

#             # Yeni yüklenen document için document-to-document ilişkilerini otomatik oluştur
#             # try:
#             #     from src.make_relationships import create_document_relationships
#             #     doc_relationships_start = time.time()
#             #     doc_connections = await asyncio.to_thread(create_document_relationships, graph, file_name)
#             #     doc_relationships_end = time.time()
#             #     logging.info(f"Document relationships created for {file_name}: {doc_connections} in {doc_relationships_end - doc_relationships_start:.2f} seconds")
#             #     result['document_relationships'] = doc_connections
#             # except Exception as doc_rel_error:
#             #     logging.error(f"Error creating document relationships for {file_name}: {doc_rel_error}")
#             #     result['document_relationships'] = {'error': str(doc_rel_error)}

#             if count_response :
#                 result['chunkNodeCount'] = count_response[file_name].get('chunkNodeCount',"0")
#                 result['chunkRelCount'] =  count_response[file_name].get('chunkRelCount',"0")
#                 result['entityNodeCount']=  count_response[file_name].get('entityNodeCount',"0")
#                 result['entityEntityRelCount']=  count_response[file_name].get('entityEntityRelCount',"0")
#                 result['communityNodeCount']=  count_response[file_name].get('communityNodeCount',"0")
#                 result['communityRelCount']= count_response[file_name].get('communityRelCount',"0")
#                 result['nodeCount'] = count_response[file_name].get('nodeCount',"0")
#                 result['relationshipCount']  = count_response[file_name].get('relationshipCount',"0")
#                 logging.info(f"counting completed in {(time.time()-count_node_time):.2f}")

#             # Otomatik post-processing (eğer istenirse)
#             if enable_post_processing and post_processing_rules:
#                 try:
#                     logging.info(f"Otomatik post-processing başlıyor: {file_name}")

#                     # JSON string'i parse et
#                     if isinstance(post_processing_rules, str):
#                         rules_list = json.loads(post_processing_rules)
#                     else:
#                         rules_list = post_processing_rules

#                     logging.info(f"Post-processing kuralları: {rules_list}")

#                     # Post-processing'i çalıştır - sadece bu dosya için
#                     from src.llm import apply_dynamic_entity_post_processing
#                     post_processing_start_time = time.time()
#                     post_processing_result = await asyncio.to_thread(
#                         apply_dynamic_entity_post_processing,
#                         graph,
#                         rules_list,
#                         target_file_names=[file_name]
#                     )
#                     post_processing_end_time = time.time()

#                     logging.info(f"Otomatik post-processing tamamlandı: {post_processing_end_time - post_processing_start_time:.2f} saniye")

#                     # Post-processing sonuçlarını result'a ekle
#                     result['post_processing'] = {
#                         'enabled': True,
#                         'rules_applied': len(rules_list),
#                         'processed_entities': post_processing_result.get('total_processed_entities', 0),
#                         'created_relationships': post_processing_result.get('total_created_relationships', 0),
#                         'elapsed_time': f"{post_processing_end_time - post_processing_start_time:.2f}",
#                         'rules': rules_list
#                     }

#                     # Node count'ları güncelle - thread'e taşı
#                     final_count_response = await asyncio.to_thread(graphDb_data_Access.update_node_relationship_count, file_name)
#                     if final_count_response:
#                         result['nodeCount'] = final_count_response[file_name].get('nodeCount',"0")
#                         result['relationshipCount'] = final_count_response[file_name].get('relationshipCount',"0")

#                     logging.info(f"Post-processing ile {post_processing_result.get('total_created_relationships', 0)} yeni relationship oluşturuldu")

#                 except Exception as post_processing_error:
#                     logging.error(f"Otomatik post-processing hatası: {post_processing_error}")
#                     result['post_processing'] = {
#                         'enabled': True,
#                         'error': str(post_processing_error),
#                         'rules_applied': 0
#                     }
#             else:
#                 result['post_processing'] = {'enabled': False}

#             # Policy Node Cleanup - DISABLED: Policy node'ları Document'e çevirmek yerine HAS_METADATA ile bağlıyoruz
#             # try:
#             #     logging.info(f"Policy node cleanup başlıyor: {file_name}")
#             #
#             #     from src.policy_cleanup import cleanup_policy_nodes_to_document
#             #     policy_cleanup_start_time = time.time()
#             #
#             #     # Policy cleanup işlemi
#             #     policy_cleanup_result = await asyncio.to_thread(
#             #         cleanup_policy_nodes_to_document,
#             #         graph,
#             #         file_name
#             #     )
#             #
#             #     policy_cleanup_end_time = time.time()
#             #
#             #     # Result'a Policy cleanup bilgilerini ekle
#             #     result['policy_cleanup'] = {
#             #         'status': policy_cleanup_result['status'],
#             #         'policy_nodes_found': policy_cleanup_result['policy_nodes_found'],
#             #         'relationships_moved': policy_cleanup_result['relationships_moved'],
#             #         'policy_nodes_deleted': policy_cleanup_result['policy_nodes_deleted'],
#             #         'elapsed_time': f"{policy_cleanup_end_time - policy_cleanup_start_time:.2f}",
#             #         'processed_policies': policy_cleanup_result.get('processed_policies', [])
#             #     }
#             #
#             #     # Eğer Policy node'lar bulunup temizlendiyse, node count'ları güncelle
#             #     if policy_cleanup_result['policy_nodes_deleted'] > 0:
#             #         final_count_response = graphDb_data_Access.update_node_relationship_count(file_name)
#             #         if final_count_response:
#             #             result['nodeCount'] = final_count_response[file_name].get('nodeCount',"0")
#             #             result['relationshipCount'] = final_count_response[file_name].get('relationshipCount',"0")
#             #
#             #     logging.info(f"Policy cleanup tamamlandı: {policy_cleanup_result['policy_nodes_deleted']} Policy silindi, {policy_cleanup_result['relationships_moved']} relationship yönlendirildi")


#             # Entity Promotion - Chunk entity'lerini Document'a terfi ettir
#             try:
#                 if enable_entity_promotion:
#                     logging.info(f"Entity promotion başlıyor: {file_name}")

#                     # Default entity promotion kuralları (eğer param gönderilmemişse)
#                     default_promotion_rules = ["Address", "Company", "Person", "Phone", "Email", "Agent", "InsuranceCompany"]

#                     if entity_promotion_rules:
#                         if isinstance(entity_promotion_rules, str):
#                             promotion_rules = json.loads(entity_promotion_rules)
#                         else:
#                             promotion_rules = entity_promotion_rules
#                     else:
#                         promotion_rules = default_promotion_rules

#                     logging.info(f"Entity promotion kuralları: {promotion_rules}")

#                     from src.policy_cleanup import promote_chunk_entities_to_document
#                     entity_promotion_start_time = time.time()

#                     # Entity promotion işlemi
#                     entity_promotion_result = await asyncio.to_thread(
#                         promote_chunk_entities_to_document,
#                         graph,
#                         file_name,
#                         promotion_rules
#                     )

#                     entity_promotion_end_time = time.time()

#                     # Result'a Entity promotion bilgilerini ekle
#                     result['entity_promotion'] = {
#                         'status': entity_promotion_result['status'],
#                         'enabled': True,
#                         'promoted_entities': entity_promotion_result['promoted_entities'],
#                         'relationships_created': entity_promotion_result['relationships_created'],
#                         'elapsed_time': f"{entity_promotion_end_time - entity_promotion_start_time:.2f}",
#                         'promotion_rules': promotion_rules,
#                         'promotion_details': entity_promotion_result.get('promotion_details', [])
#                     }

#                     # Eğer entity'ler terfi ettirildiyse, node count'ları güncelle - thread'e taşı
#                     if entity_promotion_result['relationships_created'] > 0:
#                         final_count_response = await asyncio.to_thread(graphDb_data_Access.update_node_relationship_count, file_name)
#                         if final_count_response:
#                             result['nodeCount'] = final_count_response[file_name].get('nodeCount',"0")
#                             result['relationshipCount'] = final_count_response[file_name].get('relationshipCount',"0")

#                     logging.info(f"Entity promotion tamamlandı: {entity_promotion_result['promoted_entities']} entity terfi edildi, {entity_promotion_result['relationships_created']} Document ilişkisi oluşturuldu")
#                 else:
#                     result['entity_promotion'] = {'enabled': False}
#                     logging.info("Entity promotion devre dışı")

#             except Exception as entity_promotion_error:
#                 logging.error(f"Entity promotion hatası: {entity_promotion_error}")
#                 result['entity_promotion'] = {
#                     'status': 'error',
#                     'enabled': True,
#                     'error': str(entity_promotion_error),
#                     'elapsed_time': '0.00'
#                 }

#             result['db_url'] = uri
#             result['api_name'] = 'extract'
#             result['source_url'] = source_url
#             result['wiki_query'] = wiki_query
#             result['source_type'] = source_type
#             result['logging_time'] = formatted_time(datetime.now(timezone.utc))
#             result['elapsed_api_time'] = f'{extract_api_time:.2f}'
#             result['userName'] = userName
#             result['database'] = database
#             result['aws_access_key_id'] = aws_access_key_id
#             result['gcs_bucket_name'] = gcs_bucket_name
#             result['gcs_bucket_folder'] = gcs_bucket_folder
#             result['gcs_blob_filename'] = gcs_blob_filename
#             result['gcs_project_id'] = gcs_project_id
#             result['language'] = language
#             result['retry_condition'] = retry_condition
#             result['email'] = email
#         logger.log_struct(result, "INFO")
#         result.update(uri_latency)
#         logging.info(f"extraction completed in {extract_api_time:.2f} seconds for file name {file_name}")
#         return create_api_response('Success', data=result, file_source= source_type)
#     except LLMGraphBuilderException as e:
#         error_message = str(e)
#         graph = create_graph_database_connection(uri, userName, password, database)
#         graphDb_data_Access = graphDBdataAccess(graph)
#         graphDb_data_Access.update_exception_db(file_name,error_message, retry_condition)
#         if source_type == 'local file':
#             failed_file_process(uri,file_name, merged_file_path)

#         # Document node durumunu güvenli bir şekilde al
#         try:
#             node_detail = graphDb_data_Access.get_current_status_document_node(file_name)
#         except Exception as node_error:
#             logging.warning(f"Document node status alınamadı: {node_error}")
#             node_detail = None

#         # Set the status "Completed" in logging becuase we are treating these error already handled by application as like custom errors.
#         file_created_at = None
#         if node_detail and len(node_detail) > 0 and node_detail[0].get('created_time'):
#             file_created_at = formatted_time(node_detail[0]['created_time'])
#         else:
#             file_created_at = formatted_time(datetime.now(timezone.utc))

#         json_obj = {'api_name':'extract','message':error_message,'file_created_at':file_created_at,'error_message':error_message, 'file_name': file_name,'status':'Completed',
#                     'db_url':uri, 'userName':userName, 'database':database,'success_count':1, 'source_type': source_type, 'source_url':source_url, 'wiki_query':wiki_query, 'logging_time': formatted_time(datetime.now(timezone.utc)),'email':email,
#                     'allowedNodes': allowedNodes, 'allowedRelationship': allowedRelationship}
#         logger.log_struct(json_obj, "INFO")
#         logging.exception(f'File Failed in extraction: {e}')
#         return create_api_response("Failed", message = error_message, error=error_message, file_name=file_name)
#     except Exception as e:
#         message=f"Failed To Process File:{file_name} or LLM Unable To Parse Content "
#         error_message = str(e)
#         graph = create_graph_database_connection(uri, userName, password, database)
#         graphDb_data_Access = graphDBdataAccess(graph)
#         graphDb_data_Access.update_exception_db(file_name,error_message, retry_condition)
#         if source_type == 'local file':
#             failed_file_process(uri,file_name, merged_file_path)

#         # Document node durumunu güvenli bir şekilde al
#         try:
#             node_detail = graphDb_data_Access.get_current_status_document_node(file_name)
#         except Exception as node_error:
#             logging.warning(f"Document node status alınamadı: {node_error}")
#             node_detail = None

#         file_created_at = None
#         if node_detail and len(node_detail) > 0 and node_detail[0].get('created_time'):
#             file_created_at = formatted_time(node_detail[0]['created_time'])
#         else:
#             file_created_at = formatted_time(datetime.now(timezone.utc))

#         json_obj = {'api_name':'extract','message':message,'file_created_at':file_created_at,'error_message':error_message, 'file_name': file_name,'status':'Failed',
#                     'db_url':uri, 'userName':userName, 'database':database,'failed_count':1, 'source_type': source_type, 'source_url':source_url, 'wiki_query':wiki_query, 'logging_time': formatted_time(datetime.now(timezone.utc)),'email':email,
#                     'allowedNodes': allowedNodes, 'allowedRelationship': allowedRelationship}
#         logger.log_struct(json_obj, "ERROR")
#         logging.exception(f'File Failed in extraction: {e}')
#         return create_api_response('Failed', message=message + error_message[:100], error=error_message, file_name = file_name)
#     finally:
#         gc.collect()

# @app.post("/extract_qa_based")
# async def extract_qa_based_knowledge_graph(
#     uri=Form(None),
#     userName=Form(None),
#     password=Form(None),
#     database=Form(None),
#     model=Form(),
#     document_chunks=Form(),
#     file_name=Form(),
#     domain=Form(None),
#     custom_questions=Form(None),
#     email=Form(None)
# ):
#     """
#     QA tabanlı entity çıkarma endpoint'i
#     """
#     try:
#         logging.info(f"QA tabanlı extraction başlıyor: {file_name}")
#         logging.info(f"Gelen domain parametresi: {domain}")

#         # Parameters validate
#         if not document_chunks or not file_name or not model:
#             raise HTTPException(status_code=400, detail="document_chunks, file_name ve model parametreleri gerekli")

#         # Parse document chunks (JSON string olarak gönderilmiş olabilir)
#         if isinstance(document_chunks, str):
#             try:
#                 chunks_list = json.loads(document_chunks)
#             except:
#                 chunks_list = [document_chunks]  # Single chunk
#         else:
#             chunks_list = document_chunks

#         # Custom questions parse et (eğer varsa)
#         questions_dict = None
#         if custom_questions:
#             try:
#                 questions_dict = json.loads(custom_questions)
#             except:
#                 logging.warning("Custom questions parse edilemedi, default sorular kullanılacak")

#         # Domain tespiti (eğer belirtilmemişse)
#         if not domain and len(chunks_list) > 0:
#             domain = detect_document_domain(file_name, chunks_list[0])
#             logging.info(f"Otomatik domain tespiti: {domain}")
#         elif domain:
#             logging.info(f"Kullanıcı tarafından seçilen domain: {domain}")

#         # QA tabanlı extractor oluştur
#         extractor = QABasedEntityExtractor(model)

#         # Domain'e özgü sorular al (eğer custom yoksa)
#         if not questions_dict:
#             if domain:
#                 questions_dict = create_domain_specific_questions(domain)
#                 logging.info(f"Domain '{domain}' için otomatik sorular oluşturuldu: {len(questions_dict)} kategori")
#                 # Domain sorularını da loglayalım
#                 for category, questions in questions_dict.items():
#                     logging.info(f"  {category}: {len(questions)} soru")
#             else:
#                 questions_dict = extractor.default_questions
#                 logging.info("Domain belirtilmediği için genel sorular kullanılıyor")
#         else:
#             logging.info("Kullanıcı tarafından özel sorular sağlandı")

#         # Entity'leri çıkar
#         logging.info(f"Extractor'a gönderilen sorular: {json.dumps(questions_dict, ensure_ascii=False, indent=2)}")
#         graph_documents = await extractor.extract_entities_from_qa(
#             document_chunks=chunks_list,
#             file_name=file_name,
#             custom_questions=questions_dict
#         )

#         # Çıkarılan entity ve relation sayılarını logla
#         total_entities = sum(len(doc.nodes) for doc in graph_documents)
#         total_relationships = sum(len(doc.relationships) for doc in graph_documents)
#         logging.info(f"Toplam çıkarılan entity: {total_entities}, relationship: {total_relationships}")

#         # Her GraphDocument için ayrıntılı loglama
#         for i, doc in enumerate(graph_documents):
#             logging.info(f"GraphDocument {i}: {len(doc.nodes)} entity, {len(doc.relationships)} relationship")
#             # İlk birkaç entity'yi de logla
#             for j, node in enumerate(doc.nodes[:5]):  # İlk 5 entity
#                 logging.info(f"  Entity {j}: {node.type} - {node.properties}")

#         # Dosya adından temiz bir isim oluştur (uzantıları kaldır, özel karakterleri temizle)
#         clean_file_name = file_name.replace('.pdf', '').replace('.docx', '').replace('.txt', '')
#         clean_file_name = re.sub(r'[^\w\-_\.]', '_', clean_file_name)

#         # Neo4j'ye kaydet (opsiyonel - mevcut extract endpoint mantığını kullanarak)
#         if uri and userName and password:
#             graph = create_graph_database_connection(uri, userName, password, database)
#             graph_db = graphDBdataAccess(graph)
#             # GraphDocument'ları Neo4j'ye kaydet
#             # Bu kısmı mevcut save işlemiyle entegre edebiliriz

#         # newSchema.json formatında triplet'ler oluştur
#         triplets = []
#         unique_triplets = set()

#         for doc in graph_documents:
#             for rel in doc.relationships:
#                 source_type = rel.source.type if hasattr(rel.source, 'type') else 'Unknown'
#                 target_type = rel.target.type if hasattr(rel.target, 'type') else 'Unknown'
#                 rel_type = rel.type

#                 # İlişki tipini büyük harfe çevir ve alt çizgi ile ayır
#                 formatted_rel_type = rel_type.upper().replace(' ', '_').replace('-', '_')

#                 # Triplet formatı: "SourceType-RELATION_TYPE->TargetType" (istenen format)
#                 triplet = f"{source_type}-{formatted_rel_type}->{target_type}"

#                 # Duplicate'ları önle
#                 if triplet not in unique_triplets:
#                     triplets.append(triplet)
#                     unique_triplets.add(triplet)

#         # Node tiplerini ve relationship type'larını çıkar (schemas.json formatı için)
#         unique_labels = set()
#         unique_relationship_types = set()

#         for doc in graph_documents:
#             # Node tiplerini topla
#             for node in doc.nodes:
#                 if hasattr(node, 'type') and node.type:
#                     unique_labels.add(node.type)

#             # Relationship tiplerini topla
#             for rel in doc.relationships:
#                 if hasattr(rel, 'type') and rel.type:
#                     # İlişki tipini büyük harfe çevir ve format düzelt
#                     formatted_rel_type = rel.type.upper().replace(' ', '_').replace('-', '_')
#                     unique_relationship_types.add(formatted_rel_type)

#         # Frontend için schema formatında bilgiler oluştur
#         schema = {
#             "labels": sorted(list(unique_labels)),
#             "relationshipTypes": sorted(list(unique_relationship_types)),
#             "schema": clean_file_name
#         }

#         # Şemayı kaydet
#         schema_data = {
#             'file_name': file_name,
#             'clean_file_name': clean_file_name,
#             'domain': domain,
#             'extraction_method': 'qa_based',
#             'model': model,
#             'timestamp': datetime.now(timezone.utc).isoformat(),
#             'entities_count': sum(len(doc.nodes) for doc in graph_documents),
#             'relationships_count': sum(len(doc.relationships) for doc in graph_documents),
#             'unique_entity_types': len(unique_labels),
#             'unique_relationship_types': len(unique_relationship_types),
#             'triplets_count': len(triplets),
#             'questions_used': questions_dict,
#             'schema': schema,  # Frontend için schema format
#             'triplets': triplets  # Triplet formatında ilişkiler
#         }

#         # Ana schema dosyasına kaydet (tüm detaylı bilgiler burada)
#         schema_file = f"qa_schemas/{clean_file_name}.json"
#         os.makedirs("qa_schemas", exist_ok=True)
#         with open(schema_file, 'w', encoding='utf-8') as f:
#             json.dump(schema_data, f, ensure_ascii=False, indent=2)

#         logging.info(f"QA tabanlı extraction tamamlandı: {len(graph_documents)} GraphDocument oluşturuldu")

#         response_data = {
#             'graph_documents_count': len(graph_documents),
#             'total_entities': sum(len(doc.nodes) for doc in graph_documents),
#             'total_relationships': sum(len(doc.relationships) for doc in graph_documents),
#             'unique_entity_types': len(unique_labels),
#             'unique_relationship_types': len(unique_relationship_types),
#             'triplets_count': len(triplets),
#             'domain': domain,
#             'schema_file': schema_file,  # Ana detaylı schema dosyası
#             'extraction_method': 'qa_based',
#             'schema': schema,  # Frontend için schema format
#             'triplets': triplets  # Kolay kullanım için ayrıca triplet'leri de gönder
#         }

#         return create_api_response('Success', data=response_data, file_name=file_name)

#     except Exception as e:
#         logging.error(f"QA tabanlı extraction hatası: {e}")
#         return create_api_response('Failed', message=str(e), file_name=file_name)

# @app.post("/load_qa_schema")
# async def load_qa_schema(
#     schema_file=Form(),
#     email=Form(None)
# ):
#     """
#     Kaydedilmiş QA tabanlı şemayı yükle
#     """
#     try:
#         if not os.path.exists(schema_file):
#             raise HTTPException(status_code=404, detail="Schema dosyası bulunamadı")

#         with open(schema_file, 'r', encoding='utf-8') as f:
#             schema_data = json.load(f)

#         return create_api_response('Success', data=schema_data)

#     except Exception as e:
#         logging.error(f"Schema yükleme hatası: {e}")
#         return create_api_response('Failed', message=str(e))


@app.get("/list_qa_schemas")
async def list_qa_schemas():
    """
    Mevcut QA tabanlı şemaları listele
    """
    try:
        schema_dir = "qa_schemas"
        if not os.path.exists(schema_dir):
            return create_api_response("Success", data=[])

        schemas = []
        for file in os.listdir(schema_dir):
            if file.endswith(".json"):
                file_path = os.path.join(schema_dir, file)
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        schema_info = json.load(f)

                    # Unique relationship types sayısını hesapla
                    unique_rels_count = len(
                        schema_info.get("schema", {}).get("relationshipTypes", [])
                    )

                    schemas.append(
                        {
                            "filename": file,
                            "file_path": file_path,
                            "document_name": schema_info.get("file_name"),
                            "domain": schema_info.get("domain"),
                            "timestamp": schema_info.get("timestamp"),
                            "entities_count": schema_info.get("entities_count"),
                            "relationships_count": schema_info.get(
                                "relationships_count"
                            ),  # toplam triplet sayısı
                            "unique_relationship_types": unique_rels_count,  # unique relationship types sayısı
                            "triplets_count": schema_info.get(
                                "relationships_count"
                            ),  # netlik için aynı değeri tekrar
                            "model": schema_info.get("model"),
                        }
                    )
                except:
                    # Bozuk dosyaları atla
                    continue

        # Timestamp'e göre sırala (en yeni önce)
        schemas.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

        return create_api_response("Success", data=schemas)

    except Exception as e:
        logging.error(f"Schema listeleme hatası: {e}")
        return create_api_response("Failed", message=str(e))


@app.post("/convert-to-markdown")
async def convert_to_markdown(file: UploadFile = File(...)):
    """
    Dosyayı Docling kullanarak markdown'a çevirme endpoint'i (cache destekli)
    """
    if not DOCLING_AVAILABLE:
        raise HTTPException(
            status_code=500,
            detail="Docling kütüphanesi yüklü değil. pip install docling komutu ile yükleyin.",
        )

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
                "from_cache": True,
            }

        # Cache'de yok, Docling ile dönüştür
        logging.info(f"Docling ile markdown'a çevriliyor: {file.filename}")

        # Geçici dosya oluştur
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=Path(file.filename).suffix
        ) as tmp_file:
            tmp_file.write(content)
            tmp_file_path = tmp_file.name

        try:
            # docling is only available in celery_worker
            if DEFAULT_EXPORT_LABELS is None or DocItemLabel is None:
                raise HTTPException(
                    status_code=501,
                    detail="Docling processing is only available in celery_worker. Use V2 endpoints for document processing."
                )
            
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
                "from_cache": False,
            }

        finally:
            # Geçici dosyayı sil
            os.unlink(tmp_file_path)

    except Exception as e:
        logging.error(f"Dosya dönüştürme hatası: {e}")
        raise HTTPException(
            status_code=500, detail=f"Dosya dönüştürme hatası: {str(e)}"
        )


@app.post("/sources_list")
async def get_source_list(
    uri=Form(None),
    userName=Form(None),
    password=Form(None),
    database=Form(None),
    email=Form(None),
):
    """
    Calls 'get_source_list_from_graph' which returns list of sources which already exist in databse
    """
    try:
        start = time.time()
        result = await asyncio.to_thread(
            get_source_list_from_graph, uri, userName, password, database
        )
        end = time.time()
        elapsed_time = end - start
        json_obj = {
            "api_name": "sources_list",
            "db_url": uri,
            "userName": userName,
            "database": database,
            "logging_time": formatted_time(datetime.now(timezone.utc)),
            "elapsed_api_time": f"{elapsed_time:.2f}",
            "email": email,
        }
        logger.log_struct(json_obj, "INFO")
        return create_api_response(
            "Success", data=result, message=f"Total elapsed API time {elapsed_time:.2f}"
        )
    except Exception as e:
        job_status = "Failed"
        message = "Unable to fetch source list"
        error_message = str(e)
        logging.exception(f"Exception:{error_message}")
        return create_api_response(job_status, message=message, error=error_message)


@app.post("/post_processing")
async def post_processing(
    uri=Form(None),
    userName=Form(None),
    password=Form(None),
    database=Form(None),
    tasks=Form(None),
    email=Form(None),
):
    try:
        graph = create_graph_database_connection(uri, userName, password, database)
        tasks = set(map(str.strip, json.loads(tasks)))
        api_name = "post_processing"
        count_response = []
        start = time.time()

        # Document relationships oluştur (yeni özellik)
        if "connect_documents_by_entities" in tasks:
            from src.make_relationships import create_document_relationships

            doc_connections = await asyncio.to_thread(
                create_document_relationships, graph
            )
            api_name = "post_processing/connect_documents_by_entities"
            logging.info(f"Document connections created: {doc_connections}")

        if "materialize_text_chunk_similarities" in tasks:
            await asyncio.to_thread(update_graph, graph)
            api_name = "post_processing/update_similarity_graph"
            logging.info(f"Updated KNN Graph")

        if "enable_hybrid_search_and_fulltext_search_in_bloom" in tasks:
            await asyncio.to_thread(
                create_vector_fulltext_indexes,
                uri=uri,
                username=userName,
                password=password,
                database=database,
            )
            api_name = (
                "post_processing/enable_hybrid_search_and_fulltext_search_in_bloom"
            )
            logging.info(f"Full Text index created")

        if (
            os.environ.get("ENTITY_EMBEDDING", "False").upper() == "TRUE"
            and "materialize_entity_similarities" in tasks
        ):
            await asyncio.to_thread(create_entity_embedding, graph)
            api_name = "post_processing/create_entity_embedding"
            logging.info(f"Entity Embeddings created")

        if "graph_schema_consolidation" in tasks:
            await asyncio.to_thread(graph_schema_consolidation, graph)
            api_name = "post_processing/graph_schema_consolidation"
            logging.info(f"Updated nodes and relationship labels")

        if "enable_communities" in tasks:
            api_name = "create_communities"
            await asyncio.to_thread(
                create_communities, uri, userName, password, database
            )

            logging.info(f"created communities")
        graph = create_graph_database_connection(uri, userName, password, database)
        graphDb_data_Access = graphDBdataAccess(graph)
        document_name = ""
        count_response = await asyncio.to_thread(
            graphDb_data_Access.update_node_relationship_count, document_name
        )
        if count_response:
            count_response = [
                {"filename": filename, **counts}
                for filename, counts in count_response.items()
            ]
            logging.info(f"Updated source node with community related counts")

        end = time.time()
        elapsed_time = end - start
        json_obj = {
            "api_name": api_name,
            "db_url": uri,
            "userName": userName,
            "database": database,
            "logging_time": formatted_time(datetime.now(timezone.utc)),
            "elapsed_api_time": f"{elapsed_time:.2f}",
            "email": email,
        }
        logger.log_struct(json_obj)
        return create_api_response(
            "Success", data=count_response, message="All tasks completed successfully"
        )

    except Exception as e:
        job_status = "Failed"
        error_message = str(e)
        message = f"Unable to complete tasks"
        logging.exception(f"Exception in post_processing tasks: {error_message}")
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
    email=Form(None),
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
            rules_list = (
                json.loads(post_processing_rules)
                if isinstance(post_processing_rules, str)
                else post_processing_rules
            )
        else:
            rules_list = []

        # file_names parametresini parse et
        target_files = None
        if file_names:
            try:
                target_files = (
                    json.loads(file_names)
                    if isinstance(file_names, str)
                    else file_names
                )
                if target_files:
                    logging.info(f"Hedef dosyalar: {target_files}")
            except json.JSONDecodeError:
                logging.warning(f"file_names parse edilemedi: {file_names}")

        # Post-processing işlemini çalıştır
        from src.llm import apply_dynamic_entity_post_processing

        result = await asyncio.to_thread(
            apply_dynamic_entity_post_processing, graph, rules_list, target_files
        )

        end = time.time()
        elapsed_time = end - start

        json_obj = {
            "api_name": "entity_relationship_post_processing",
            "db_url": uri,
            "userName": userName,
            "database": database,
            "post_processing_rules": post_processing_rules,
            "processed_files": result.get("processed_files", 0),
            "applied_rules": len(rules_list),
            "logging_time": formatted_time(datetime.now(timezone.utc)),
            "elapsed_api_time": f"{elapsed_time:.2f}",
            "email": email,
        }
        logger.log_struct(json_obj, "INFO")

        processed_files = result.get("processed_files", 0)
        return create_api_response(
            "Success",
            data=result,
            message=f"Entity relationship post-processing completed across {processed_files} documents with {len(rules_list)} rules in {elapsed_time:.2f} seconds",
        )

    except Exception as e:
        job_status = "Failed"
        error_message = str(e)
        message = f"Unable to complete entity relationship post-processing"
        logging.exception(
            f"Exception in entity_relationship_post_processing: {error_message}"
        )
        return create_api_response(job_status, message=message, error=error_message)

    finally:
        gc.collect()


@app.post("/chat_bot")
async def chat_bot(
    uri=Form(None),
    model=Form(None),
    userName=Form(None),
    password=Form(None),
    database=Form(None),
    question=Form(None),
    document_names=Form(None),
    session_id=Form(None),
    mode=Form(None),
    email=Form(None),
):
    logging.info(f"QA_RAG called at {datetime.now()}")
    qa_rag_start_time = time.time()
    try:
        if mode == "graph":
            graph = Neo4jGraph(
                url=uri,
                username=userName,
                password=password,
                database=database,
                sanitize=True,
                refresh_schema=True,
            )
        else:
            graph = create_graph_database_connection(uri, userName, password, database)

        graph_DB_dataAccess = graphDBdataAccess(graph)
        write_access = graph_DB_dataAccess.check_account_access(database=database)
        # Try to instantiate IntelligentAgent and pass them to QA_RAG (fallback to None on failure)
        intelligent_agent = None
        try:
            # 🚀 SESSION-BASED CACHED AGENT - Performance optimization
            intelligent_agent = get_cached_agent(session_id, graph, model)
            print(f"🎯 Cached agent alındı - Session: {session_id}")
        except Exception as e:
            print(f"❌ Cached agent hatası - Session: {session_id} | Hata: {e}")
            intelligent_agent = None

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
        )

        total_call_time = time.time() - qa_rag_start_time
        logging.info(f"Total Response time is  {total_call_time:.2f} seconds")
        result["info"]["response_time"] = round(total_call_time, 2)

        json_obj = {
            "api_name": "chat_bot",
            "db_url": uri,
            "userName": userName,
            "database": database,
            "question": question,
            "document_names": document_names,
            "session_id": session_id,
            "mode": mode,
            "logging_time": formatted_time(datetime.now(timezone.utc)),
            "elapsed_api_time": f"{total_call_time:.2f}",
            "email": email,
        }
        logger.log_struct(json_obj, "INFO")

        return create_api_response("Success", data=result)
    except Exception as e:
        job_status = "Failed"
        message = "Unable to get chat response"
        error_message = str(e)
        logging.exception(f"Exception in chat bot:{error_message}")
        return create_api_response(
            job_status, message=message, error=error_message, data=mode
        )
    finally:
        gc.collect()


@app.post("/setup_chatbot")
async def setup_chatbot(
    uri: str = Form(None),
    userName: str = Form(None),
    password: str = Form(None),
    database: str = Form(None),
    session_id: str = Form(None),
    model: str = Form(None),
    email: str = Form(None),
):
    """
    Belirli bir session ID için IntelligentAgent'ı önceden oluştur/cache'le
    Bu endpoint chat başlamadan önce agent'ı hazır hale getirir
    """
    try:
        start_time = time.time()

        # Parametreleri kontrol et
        if not session_id:
            return create_api_response(
                "Failed", message="session_id parametresi gerekli"
            )

        if not model:
            return create_api_response("Failed", message="model parametresi gerekli")

        # Graph bağlantısını kur
        graph = create_graph_database_connection(uri, userName, password, database)

        # Agent'ı oluştur veya mevcut cache'den al
        agent = get_cached_agent(session_id, graph, model)

        # Agent bilgilerini al
        agent_info = {
            "session_id": session_id,
            "model": model,
            "agent_created": True,
            "cache_hit": session_id in _agent_cache
            and session_id in _cache_access_times,
            "schema_initialized": hasattr(agent, "_schema_cache")
            and agent._schema_cache is not None,
            "total_cached_sessions": len(_agent_cache),
            "initialization_time": f"{time.time() - start_time:.3f}s",
        }

        # Loglama
        elapsed_time = time.time() - start_time
        json_obj = {
            "api_name": "setup_chatbot",
            "db_url": uri,
            "userName": userName,
            "database": database,
            "session_id": session_id,
            "model": model,
            "cache_hit": agent_info["cache_hit"],
            "total_cached_sessions": agent_info["total_cached_sessions"],
            "logging_time": formatted_time(datetime.now(timezone.utc)),
            "elapsed_api_time": f"{elapsed_time:.3f}",
            "email": email,
        }
        logger.log_struct(json_obj, "INFO")

        return create_api_response(
            "Success",
            data=agent_info,
            message=f"Chatbot setup completed for session {session_id}",
        )

    except Exception as e:
        error_message = str(e)
        logging.exception(f"Exception in setup_chatbot: {error_message}")
        return create_api_response(
            "Failed", message=f"Chatbot setup failed: {error_message}"
        )


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
    files: Optional[str] = Form(None),
    agent_type: str = Form("standard"),  # "standard" veya "fast_agent"
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
                graph = Neo4jGraph(
                    url=uri,
                    username=userName,
                    password=password,
                    database=database,
                    sanitize=True,
                    refresh_schema=True,
                )
            else:
                graph = create_graph_database_connection(
                    uri, userName, password, database
                )

            yield f"data: {json.dumps({'type': 'status', 'message': 'Veritabanı bağlantısı kuruldu', 'status': 'connected'}, ensure_ascii=False)}\n\n"

            graph_DB_dataAccess = graphDBdataAccess(graph)
            write_access = graph_DB_dataAccess.check_account_access(database=database)

            # Agent tipine göre streaming yaklaşımı seç
            final_result = None
            total_tokens = 0

            if agent_type != "fast_agent":
                # FastAgent kullanarak streaming
                yield f"data: {json.dumps({'type': 'status', 'message': 'FastAgent ile işleniyor...', 'status': 'fast_agent_processing'}, ensure_ascii=False)}\n\n"

                async for chunk in stream_fast_agent_response(
                    question=question,
                    graph=graph,
                    # model=model,
                    session_id=session_id,
                ):
                    # Client disconnect kontrolü
                    if await request.is_disconnected():
                        logging.info(
                            "SSE Client disconnected during FastAgent streaming"
                        )
                        break

                    # Chunk'ı client'a gönder
                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

                    # Final result'ı sakla
                    if chunk.get("type") == "complete":
                        final_result = chunk
                        total_tokens = chunk.get("info", {}).get("total_tokens", 0)

            else:
                # Standart QA_RAG streaming
                # Instantiate IntelligentAgent for streaming path and pass it through (fallback to None)
                intelligent_agent = None
                try:
                    # 🚀 SESSION-BASED CACHED AGENT - Performance optimization
                    intelligent_agent = get_cached_agent(session_id, graph, model)
                    print(f"🎯 Cached agent alındı - Session: {session_id}")
                except Exception as e:
                    print(f"❌ Cached agent hatası - Session: {session_id} | Hata: {e}")
                    intelligent_agent = None

                async for chunk in QA_RAG_stream(
                    graph=graph,
                    model=model,
                    question=question,
                    document_names=document_names,
                    session_id=session_id,
                    mode=mode,
                    write_access=write_access,
                    intelligent_agent=intelligent_agent,
                    files=downloadedFiles,
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
            logging.info(
                f"Real streaming total response time: {total_call_time:.2f} seconds"
            )

            # Final timing chunk'ı gönder
            timing_chunk = {
                "type": "timing",
                "status": "finished",
                "elapsed_time": f"{total_call_time:.2f}",
                "total_tokens": total_tokens,
                "timestamp": formatted_time(datetime.now(timezone.utc)),
            }
            yield f"data: {json.dumps(timing_chunk, ensure_ascii=False)}\n\n"

            # Loglama
            json_obj = {
                "api_name": "chat_bot_stream_real",
                "db_url": uri,
                "userName": userName,
                "database": database,
                "question": question,
                "document_names": document_names,
                "session_id": session_id,
                "mode": mode,
                "logging_time": formatted_time(datetime.now(timezone.utc)),
                "elapsed_api_time": f"{total_call_time:.2f}",
                "total_tokens": total_tokens,
                "email": email,
                "streaming_type": "real_llm_streaming",
            }
            logger.log_struct(json_obj, "INFO")

        except Exception as e:
            error_message = str(e)
            logging.exception(f"Exception in real streaming chat bot: {error_message}")

            error_chunk = {
                "type": "error",
                "status": "error",
                "message": "Streaming sırasında bir hata oluştu",
                "error": error_message,
                "timestamp": formatted_time(datetime.now(timezone.utc)),
            }
            yield f"data: {json.dumps(error_chunk, ensure_ascii=False)}\n\n"

        finally:
            gc.collect()

    return EventSourceResponse(generate_real_streaming_response())


@app.post("/test_fast_agent")
async def test_fast_agent(
    question: str = Form("Amasyalı soy adı olan sigortalımız var mı?"),
    model: str = Form("openai_gpt_4o_mini"),
    session_id: str = Form("test_session"),
):
    """FastAgent'i test etmek için basit endpoint"""
    try:
        from src.workflow.fast_agent_integration_simple import (
            stream_fast_agent_response,
        )

        # Test response'u topla
        response_parts = []
        async for chunk in stream_fast_agent_response(
            question=question,
            # model=model,
            session_id=session_id,
        ):
            response_parts.append(chunk)

        return create_api_response(
            "Success",
            data={
                "chunks": response_parts,
                "total_chunks": len(response_parts),
                "question": question,
                "model": model,
                "session_id": session_id,
            },
            message="FastAgent test completed successfully",
        )

    except Exception as e:
        logging.error(f"FastAgent test error: {e}")
        return create_api_response("Failed", message=f"FastAgent test failed: {str(e)}")


@app.get("/agent_cache_sessions")
async def get_agent_cache_sessions():
    """
    Cache'deki tüm session'ları listele
    """
    try:
        import time

        current_time = time.time()

        sessions_info = []
        for session_id, agent in _agent_cache.items():
            last_access = _cache_access_times.get(session_id, 0)
            sessions_info.append(
                {
                    "session_id": session_id,
                    "model": getattr(agent, "model_name", "unknown"),
                    "last_access": last_access,
                    "last_access_formatted": formatted_time(
                        datetime.fromtimestamp(last_access, tz=timezone.utc)
                    ),
                    "seconds_since_access": int(current_time - last_access),
                    "schema_cached": hasattr(agent, "_schema_cache")
                    and agent._schema_cache is not None,
                }
            )

        # Son erişim zamanına göre sırala (en son kullanılan önce)
        sessions_info.sort(key=lambda x: x["last_access"], reverse=True)

        summary = {
            "total_sessions": len(sessions_info),
            "cache_limit": AGENT_CACHE_MAX_SIZE,
            "cleanup_count": AGENT_CACHE_CLEANUP_COUNT,
            "sessions": sessions_info,
        }

        return create_api_response(
            "Success",
            data=summary,
            message=f"Found {len(sessions_info)} cached sessions",
        )

    except Exception as e:
        logging.error(f"Agent cache sessions hatası: {e}")
        return create_api_response(
            "Failed", message=f"Error retrieving sessions: {str(e)}"
        )


@app.post("/clear_agent_cache")
async def clear_agent_cache(
    session_id: str = Form(None),  # Specific session to clear, or None for all
    email: str = Form(None),
):
    """
    Agent cache'i temizle - belirli session veya tüm cache
    """
    try:
        if session_id:
            # Belirli session'ı temizle
            if session_id in _agent_cache:
                del _agent_cache[session_id]
                del _cache_access_times[session_id]
                message = f"Session {session_id} cache'den temizlendi"
                action = "single_session_cleared"
            else:
                message = f"Session {session_id} cache'de bulunamadı"
                action = "session_not_found"
        else:
            # Tüm cache'i temizle
            cleared_count = len(_agent_cache)
            _agent_cache.clear()
            _cache_access_times.clear()
            message = f"Tüm agent cache temizlendi ({cleared_count} session)"
            action = "all_cache_cleared"

        # Loglama
        json_obj = {
            "api_name": "clear_agent_cache",
            "action": action,
            "session_id": session_id,
            "remaining_sessions": len(_agent_cache),
            "logging_time": formatted_time(datetime.now(timezone.utc)),
            "email": email,
        }
        logger.log_struct(json_obj, "INFO")

        return create_api_response(
            "Success",
            message=message,
            data={
                "action": action,
                "session_id": session_id,
                "remaining_sessions": len(_agent_cache),
            },
        )

    except Exception as e:
        logging.error(f"Agent cache clear hatası: {e}")
        return create_api_response("Failed", message=f"Error clearing cache: {str(e)}")


@app.get("/agent_cache_stats")
async def get_agent_cache_stats():
    """
    Session-based agent cache istatistiklerini döndür
    """
    try:
        stats = get_cache_stats()
        return create_api_response(
            "Success",
            data=stats,
            message="Agent cache statistics retrieved successfully",
        )
    except Exception as e:
        logging.error(f"Agent cache stats hatası: {e}")
        return create_api_response(
            "Failed", message=f"Error retrieving cache stats: {str(e)}"
        )


@app.post("/agent_cache_config")
async def update_agent_cache_config(
    max_size: int = Form(None), cleanup_count: int = Form(None)
):
    """
    Agent cache konfigürasyonunu güncelle
    """
    global AGENT_CACHE_MAX_SIZE, AGENT_CACHE_CLEANUP_COUNT

    try:
        updated_params = {}

        if max_size is not None:
            if max_size > 0 and max_size <= 10000:  # Reasonable limits
                AGENT_CACHE_MAX_SIZE = max_size
                updated_params["max_size"] = max_size
            else:
                return create_api_response(
                    "Failed", message="max_size must be between 1 and 10000"
                )

        if cleanup_count is not None:
            if cleanup_count > 0 and cleanup_count <= 1000:  # Reasonable limits
                AGENT_CACHE_CLEANUP_COUNT = cleanup_count
                updated_params["cleanup_count"] = cleanup_count
            else:
                return create_api_response(
                    "Failed", message="cleanup_count must be between 1 and 1000"
                )

        # Current config döndür
        current_config = {
            "max_size": AGENT_CACHE_MAX_SIZE,
            "cleanup_count": AGENT_CACHE_CLEANUP_COUNT,
            "current_sessions": len(_agent_cache),
            "updated_params": updated_params,
        }

        return create_api_response(
            "Success",
            data=current_config,
            message="Cache configuration updated successfully",
        )

    except Exception as e:
        logging.error(f"Cache config update hatası: {e}")
        return create_api_response(
            "Failed", message=f"Error updating cache config: {str(e)}"
        )


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
async def chunk_entities(
    uri=Form(None),
    userName=Form(None),
    password=Form(None),
    database=Form(None),
    nodedetails=Form(None),
    entities=Form(),
    mode=Form(),
    email=Form(None),
):
    try:
        start = time.time()
        result = await asyncio.to_thread(
            get_entities_from_chunkids,
            nodedetails=nodedetails,
            entities=entities,
            mode=mode,
            uri=uri,
            username=userName,
            password=password,
            database=database,
        )
        end = time.time()
        elapsed_time = end - start
        json_obj = {
            "api_name": "chunk_entities",
            "db_url": uri,
            "userName": userName,
            "database": database,
            "nodedetails": nodedetails,
            "entities": entities,
            "mode": mode,
            "logging_time": formatted_time(datetime.now(timezone.utc)),
            "elapsed_api_time": f"{elapsed_time:.2f}",
            "email": email,
        }
        logger.log_struct(json_obj, "INFO")
        return create_api_response(
            "Success", data=result, message=f"Total elapsed API time {elapsed_time:.2f}"
        )
    except Exception as e:
        job_status = "Failed"
        message = "Unable to extract entities from chunk ids"
        error_message = str(e)
        logging.exception(f"Exception in chat bot:{error_message}")
        return create_api_response(job_status, message=message, error=error_message)
    finally:
        gc.collect()


@app.post("/get_neighbours")
async def get_neighbours(
    uri=Form(None),
    userName=Form(None),
    password=Form(None),
    database=Form(None),
    elementId=Form(None),
    email=Form(None),
):
    try:
        start = time.time()
        result = await asyncio.to_thread(
            get_neighbour_nodes,
            uri=uri,
            username=userName,
            password=password,
            database=database,
            element_id=elementId,
        )
        end = time.time()
        elapsed_time = end - start
        json_obj = {
            "api_name": "get_neighbours",
            "userName": userName,
            "database": database,
            "db_url": uri,
            "logging_time": formatted_time(datetime.now(timezone.utc)),
            "elapsed_api_time": f"{elapsed_time:.2f}",
            "email": email,
        }
        logger.log_struct(json_obj, "INFO")
        return create_api_response(
            "Success", data=result, message=f"Total elapsed API time {elapsed_time:.2f}"
        )
    except Exception as e:
        job_status = "Failed"
        message = "Unable to extract neighbour nodes for given element ID"
        error_message = str(e)
        logging.exception(f"Exception in get neighbours :{error_message}")
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
    email=Form(None),
):
    try:
        start = time.time()
        result = await asyncio.to_thread(
            get_graph_results,
            uri=uri,
            username=userName,
            password=password,
            database=database,
            document_names=document_names,
        )
        end = time.time()
        elapsed_time = end - start
        json_obj = {
            "api_name": "graph_query",
            "db_url": uri,
            "userName": userName,
            "database": database,
            "document_names": document_names,
            "logging_time": formatted_time(datetime.now(timezone.utc)),
            "elapsed_api_time": f"{elapsed_time:.2f}",
            "email": email,
        }
        logger.log_struct(json_obj, "INFO")
        return create_api_response(
            "Success", data=result, message=f"Total elapsed API time {elapsed_time:.2f}"
        )
    except Exception as e:
        job_status = "Failed"
        message = "Unable to get graph query response"
        error_message = str(e)
        logging.exception(f"Exception in graph query: {error_message}")
        return create_api_response(job_status, message=message, error=error_message)
    finally:
        gc.collect()


@app.post("/clear_chat_bot")
async def clear_chat_bot(
    uri=Form(None),
    userName=Form(None),
    password=Form(None),
    database=Form(None),
    session_id=Form(None),
    model=Form(None),
    new_session_id=Form(None),
    email=Form(None),
):
    try:
        start = time.time()

        # 🧹 SESSION-BASED AGENT CACHE TEMİZLİK ÖNCE YAP
        # Chat history temizlendiğinde ilgili agent'ı da cache'den kaldır
        agent_cache_result = "no_cache_entry"
        if session_id and session_id in _agent_cache:
            del _agent_cache[session_id]
            if session_id in _cache_access_times:
                del _cache_access_times[session_id]
            agent_cache_result = "cache_cleared"
            print(f"🧹 Agent cache temizlendi - Session: {session_id}")
        elif session_id:
            agent_cache_result = "cache_not_found"
            print(f"🔍 Agent cache'de bulunamadı - Session: {session_id}")

        # ⚠️ NEO4J BAĞLANTI KONTROLÜ
        result = None
        db_clear_result = "db_connection_failed"
        new_agent_result = "no_model_provided"

        try:
            # Neo4j bağlantısını dene
            graph = create_graph_database_connection(uri, userName, password, database)
            result = await asyncio.to_thread(
                clear_chat_history, graph=graph, session_id=session_id
            )
            db_clear_result = "db_cleared"
            print(f"✅ Neo4j'den chat history temizlendi - Session: {session_id}")

            # 🆕 CLEAR CHAT'TEN SONRA YENİ SESSION ID İLE AGENT OLUŞTUR (eğer new_session_id gönderildiyse)
            if model and new_session_id and db_clear_result == "db_cleared":
                try:
                    # Yeni session ID ile agent oluştur
                    new_session_agent = get_cached_agent(new_session_id, graph, model)
                    new_agent_result = (
                        f"agent_created_for_new_session: {new_session_id}"
                    )
                    print(
                        f"🆕 Clear chat sonrası yeni session için agent oluşturuldu - New Session: {new_session_id}, Model: {model}"
                    )

                    # 🆕 FastAgent için şema cache'ini doldur (yeni session için)
                    try:
                        from src.workflow.fast_agent_integration_simple import (
                            get_or_create_fast_agent,
                        )

                        fast_agent = await get_or_create_fast_agent(model, graph)
                        # Session bazlı cache durumunu kontrol et
                        cache_before = new_session_id in fast_agent.schema_cache
                        logging.info(
                            f"📋 FastAgent CLEAR_CHAT: Session {new_session_id} için cache durumu (önce): {cache_before}"
                        )

                        # Yeni session için şema bilgisini önceden al ve cache'le
                        schema_info = fast_agent._get_schema_for_session(new_session_id)

                        # Cache durumunu tekrar kontrol et
                        cache_after = new_session_id in fast_agent.schema_cache
                        logging.info(
                            f"📋 FastAgent CLEAR_CHAT: Session {new_session_id} için cache durumu (sonra): {cache_after}, Schema length: {len(schema_info) if schema_info else 0}"
                        )

                        if schema_info:
                            print(
                                f"✅ FastAgent: Session {new_session_id} için şema cache'lendi - Schema length: {len(schema_info)}"
                            )
                        else:
                            print(
                                f"⚠️ FastAgent: Session {new_session_id} için şema alınamadı"
                            )
                    except Exception as fast_agent_error:
                        logging.error(
                            f"⚠️ FastAgent şema cache hatası - Session {new_session_id}: {fast_agent_error}",
                            exc_info=True,
                        )
                        print(
                            f"⚠️ FastAgent şema cache hatası - Session {new_session_id}: {fast_agent_error}"
                        )
                except Exception as agent_error:
                    new_agent_result = (
                        f"new_session_agent_creation_failed: {str(agent_error)}"
                    )
                    print(
                        f"❌ Yeni session için agent oluşturma hatası - New Session: {new_session_id}: {agent_error}"
                    )

        except Exception as db_error:
            # Neo4j bağlantı hatası durumunda sadece cache temizle
            db_clear_result = f"db_connection_failed: {str(db_error)[:100]}"
            print(
                f"⚠️ Neo4j bağlantı hatası, sadece cache temizlendi - Session: {session_id}: {db_error}"
            )
            # Fallback result oluştur
            result = {
                "session_id": session_id,
                "message": "Chat cache cleared (database connection failed)",
                "user": "chatbot",
            }

        # Sonuca cache temizleme ve yeni agent bilgilerini ekle
        if isinstance(result, dict):
            result["agent_cache_status"] = agent_cache_result
            result["db_clear_status"] = db_clear_result
            result["new_agent_status"] = new_agent_result
            result["remaining_cached_sessions"] = len(_agent_cache)

        end = time.time()
        elapsed_time = end - start
        json_obj = {
            "api_name": "clear_chat_bot",
            "db_url": uri,
            "userName": userName,
            "database": database,
            "session_id": session_id,
            "model": model,
            "agent_cache_status": agent_cache_result,
            "db_clear_status": db_clear_result,
            "new_agent_status": new_agent_result,
            "remaining_cached_sessions": len(_agent_cache),
            "logging_time": formatted_time(datetime.now(timezone.utc)),
            "elapsed_api_time": f"{elapsed_time:.2f}",
            "email": email,
        }
        logger.log_struct(json_obj, "INFO")
        return create_api_response("Success", data=result)
    except Exception as e:
        job_status = "Failed"
        message = "Unable to clear chat History"
        error_message = str(e)
        logging.exception(f"Exception in chat bot:{error_message}")
        return create_api_response(job_status, message=message, error=error_message)
    finally:
        gc.collect()


@app.post("/connect")
async def connect(
    uri=Form(None),
    userName=Form(None),
    password=Form(None),
    database=Form(None),
    email=Form(None),
):
    try:
        start = time.time()
        graph = create_graph_database_connection(uri, userName, password, database)
        result = await asyncio.to_thread(
            connection_check_and_get_vector_dimensions, graph, database
        )
        gcs_file_cache = os.environ.get("GCS_FILE_CACHE")
        end = time.time()
        elapsed_time = end - start
        json_obj = {
            "api_name": "connect",
            "db_url": uri,
            "userName": userName,
            "database": database,
            "count": 1,
            "logging_time": formatted_time(datetime.now(timezone.utc)),
            "elapsed_api_time": f"{elapsed_time:.2f}",
            "email": email,
        }
        logger.log_struct(json_obj, "INFO")
        result["elapsed_api_time"] = f"{elapsed_time:.2f}"
        result["gcs_file_cache"] = gcs_file_cache
        return create_api_response("Success", data=result)
    except Exception as e:
        job_status = "Failed"
        message = "Connection failed to connect Neo4j database"
        error_message = str(e)
        logging.exception(
            f"Connection failed to connect Neo4j database:{error_message}"
        )
        return create_api_response(job_status, message=message, error=error_message)


@app.post("/upload")
async def upload_large_file_into_chunks(
    file: UploadFile = File(...),
    chunkNumber=Form(None),
    totalChunks=Form(None),
    originalname=Form(None),
    model=Form(None),
    uri=Form(None),
    userName=Form(None),
    password=Form(None),
    database=Form(None),
    email=Form(None),
    generateEmbedding=Form(None),
):
    try:
        start = time.time()

        # Debug: FastAPI Form field'ından gelen dosya ismini kontrol et
        logging.info(f"🔍 RAW originalname from FastAPI Form: {repr(originalname)}")
        logging.info(f"🔍 originalname type: {type(originalname)}")

        # FastAPI Form field'ları bazen bytes olarak gelebilir, decode etmeye çalış
        if isinstance(originalname, bytes):
            try:
                originalname = originalname.decode("utf-8")
                logging.info(f"🔄 Decoded bytes to UTF-8: {originalname}")
            except UnicodeDecodeError as e:
                logging.warning(f"⚠️ UTF-8 decode failed, trying latin-1: {e}")
                originalname = originalname.decode("latin-1")
                logging.info(f"🔄 Decoded bytes to latin-1: {originalname}")

        # Eğer string ama yanlış encode edilmişse (URL-encoded UTF-8 bytes), düzelt
        if isinstance(originalname, str) and "\\x" in originalname:
            try:
                # '\\xc3\\xa7' gibi escaped bytes'ları gerçek bytes'a çevir
                import codecs

                originalname_bytes = codecs.decode(
                    originalname, "unicode_escape"
                ).encode("latin-1")
                originalname = originalname_bytes.decode("utf-8")
                logging.info(f"🔄 Fixed escaped UTF-8 bytes: {originalname}")
            except Exception as e:
                logging.warning(f"⚠️ Failed to fix escaped UTF-8: {e}")

        logging.info(
            f"📤 Upload API called - File: {originalname}, Chunk: {chunkNumber}/{totalChunks}"
        )
        logging.info(
            f"🔧 Upload parameters - Model: {model}, GenerateEmbedding: {generateEmbedding}"
        )

        # Model parametresi kontrolü
        if not model or model.strip() == "":
            logging.warning(
                f"⚠️ Model parametresi boş veya gelmedi - upload_file fonksiyonunda varsayılan değer atanacak"
            )
        else:
            logging.info(f"✅ Model parametresi upload endpoint'inde alındı: {model}")

        graph = create_graph_database_connection(uri, userName, password, database)
        result = await asyncio.to_thread(
            upload_file,
            graph,
            model,
            file,
            chunkNumber,
            totalChunks,
            originalname,
            uri,
            CHUNK_DIR,
            MERGED_DIR,
            generateEmbedding,
        )

        end = time.time()
        elapsed_time = end - start

        logging.info(
            f"✅ Upload processing completed in {elapsed_time:.2f}s - Chunk: {chunkNumber}/{totalChunks}"
        )

        if int(chunkNumber) == int(totalChunks):
            logging.info(
                f"🎉 Final chunk processed for {originalname} - Upload complete!"
            )
            
            # Create record in Queue DB and trigger Celery Task
            try:
                queue_db = get_file_queue_db()
                file_record = queue_db.add_file(
                    filename=result["file_name"],
                    original_name=originalname,
                    file_path=os.path.join(MERGED_DIR, result["file_name"]),
                    file_size=result["file_size"],
                    neo4j_uri=uri,
                    neo4j_database=database,
                    model=model,
                    generate_embedding=generateEmbedding,
                    auto_process=True
                )
                logging.info(f"✅ File added to Queue DB: ID={file_record.id}")
                
                # Trigger Celery Task
                task_result = celery_app.send_task("src.tasks.process_file_pipeline", args=[file_record.id])
                logging.info(f"🚀 Celery task triggered: {task_result.id}")
                
            except Exception as queue_error:
                logging.error(f"❌ Failed to queue file for processing: {queue_error}")
                # Don't fail the upload response, but log the error

            json_obj = {
                "api_name": "upload",
                "db_url": uri,
                "userName": userName,
                "database": database,
                "chunkNumber": chunkNumber,
                "totalChunks": totalChunks,
                "original_file_name": originalname,
                "model": model,
                "logging_time": formatted_time(datetime.now(timezone.utc)),
                "elapsed_api_time": f"{elapsed_time:.2f}",
                "email": email,
            }
            logger.log_struct(json_obj, "INFO")
        if int(chunkNumber) == int(totalChunks):
            return create_api_response(
                "Success", data=result, message="Source Node Created Successfully"
            )
        else:
            return create_api_response("Success", message=result)
    except Exception as e:
        message = "Unable to upload file in chunks"
        error_message = str(e)
        logging.error(
            f"❌ Upload failed for {originalname}, chunk {chunkNumber}/{totalChunks}: {error_message}"
        )

        graph = create_graph_database_connection(uri, userName, password, database)
        graphDb_data_Access = graphDBdataAccess(graph)
        graphDb_data_Access.update_exception_db(originalname, error_message)
        logging.info(message)
        logging.exception(f"Exception:{error_message}")
        return create_api_response(
            "Failed",
            message=message + error_message[:100],
            error=error_message,
            file_name=originalname,
        )
    finally:
        gc.collect()


@app.post("/schema")
async def get_structured_schema(
    uri=Form(None),
    userName=Form(None),
    password=Form(None),
    database=Form(None),
    email=Form(None),
):
    try:
        start = time.time()
        result = await asyncio.to_thread(
            get_labels_and_relationtypes, uri, userName, password, database
        )
        end = time.time()
        elapsed_time = end - start
        logging.info(f"Schema result from DB: {result}")
        json_obj = {
            "api_name": "schema",
            "db_url": uri,
            "userName": userName,
            "database": database,
            "logging_time": formatted_time(datetime.now(timezone.utc)),
            "elapsed_api_time": f"{elapsed_time:.2f}",
            "email": email,
        }
        logger.log_struct(json_obj, "INFO")
        return create_api_response(
            "Success", data=result, message=f"Total elapsed API time {elapsed_time:.2f}"
        )
    except Exception as e:
        message = "Unable to get the labels and relationtypes from neo4j database"
        error_message = str(e)
        logging.info(message)
        logging.exception(f"Exception:{error_message}")
        return create_api_response("Failed", message=message, error=error_message)
    finally:
        gc.collect()


def decode_password(pwd):
    sample_string_bytes = base64.b64decode(pwd)
    decoded_password = sample_string_bytes.decode("utf-8")
    return decoded_password


def encode_password(pwd):
    data_bytes = pwd.encode("ascii")
    encoded_pwd_bytes = base64.b64encode(data_bytes)
    return encoded_pwd_bytes


@app.get("/update_extract_status/{file_name}")
async def update_extract_status(
    request: Request,
    file_name: str,
    uri: str = None,
    userName: str = None,
    password: str = None,
    database: str = None,
):
    # URL decode the file name and normalize Unicode characters
    try:
        file_name = unquote(file_name)
        file_name = normalize_file_name(file_name)
        logging.info(f"Decoded and normalized file name: {file_name}")
    except Exception as e:
        logging.error(f"Error decoding/normalizing file name: {e}")

    async def generate():
        status = ""

        if password is not None and password != "null":
            decoded_password = decode_password(password)
        else:
            decoded_password = None

        url = uri
        if url and " " in url:
            url = url.replace(" ", "+")

        graph = create_graph_database_connection(
            url, userName, decoded_password, database
        )
        graphDb_data_Access = graphDBdataAccess(graph)
        while True:
            try:
                if await request.is_disconnected():
                    logging.info(" SSE Client disconnected")
                    break
                # get the current status of document node

                else:
                    result = graphDb_data_Access.get_current_status_document_node(
                        file_name
                    )
                    if len(result) > 0:
                        status = json.dumps(
                            {
                                "fileName": file_name,
                                "status": result[0]["Status"],
                                "processingTime": result[0]["processingTime"],
                                "nodeCount": result[0]["nodeCount"],
                                "relationshipCount": result[0]["relationshipCount"],
                                "model": result[0]["model"],
                                "total_chunks": result[0]["total_chunks"],
                                "fileSize": result[0]["fileSize"],
                                "processed_chunk": result[0]["processed_chunk"],
                                "fileSource": result[0]["fileSource"],
                                "chunkNodeCount": result[0]["chunkNodeCount"],
                                "chunkRelCount": result[0]["chunkRelCount"],
                                "entityNodeCount": result[0]["entityNodeCount"],
                                "entityEntityRelCount": result[0][
                                    "entityEntityRelCount"
                                ],
                                "communityNodeCount": result[0]["communityNodeCount"],
                                "communityRelCount": result[0]["communityRelCount"],
                            }
                        )
                    yield status
            except asyncio.CancelledError:
                logging.info("SSE Connection cancelled")

    return EventSourceResponse(generate(), ping=60)


@app.post("/delete_document_and_entities")
async def delete_document_and_entities(
    uri=Form(None),
    userName=Form(None),
    password=Form(None),
    database=Form(None),
    filenames=Form(),
    source_types=Form(),
    deleteEntities=Form(),
    email=Form(None),
):
    try:
        start = time.time()
        graph = create_graph_database_connection(uri, userName, password, database)
        graphDb_data_Access = graphDBdataAccess(graph)
        files_list_size = await asyncio.to_thread(
            graphDb_data_Access.delete_file_from_graph,
            filenames,
            source_types,
            deleteEntities,
            MERGED_DIR,
            uri,
        )
        message = f"Deleted {files_list_size} documents with entities from database"
        end = time.time()
        elapsed_time = end - start
        json_obj = {
            "api_name": "delete_document_and_entities",
            "db_url": uri,
            "userName": userName,
            "database": database,
            "filenames": filenames,
            "deleteEntities": deleteEntities,
            "source_types": source_types,
            "logging_time": formatted_time(datetime.now(timezone.utc)),
            "elapsed_api_time": f"{elapsed_time:.2f}",
            "email": email,
        }
        logger.log_struct(json_obj, "INFO")
        return create_api_response("Success", message=message)
    except Exception as e:
        job_status = "Failed"
        message = f"Unable to delete document {filenames}"
        error_message = str(e)
        logging.exception(f"{message}:{error_message}")
        return create_api_response(job_status, message=message, error=error_message)
    finally:
        gc.collect()


@app.get("/document_status/{file_name}")
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
            uri = url.replace(" ", "+")
        else:
            uri = url
        graph = create_graph_database_connection(
            uri, userName, decoded_password, database
        )
        graphDb_data_Access = graphDBdataAccess(graph)
        result = graphDb_data_Access.get_current_status_document_node(file_name)
        if len(result) > 0:
            status = {
                "fileName": file_name,
                "status": result[0]["Status"],
                "processingTime": result[0]["processingTime"],
                "nodeCount": result[0]["nodeCount"],
                "relationshipCount": result[0]["relationshipCount"],
                "model": result[0]["model"],
                "total_chunks": result[0]["total_chunks"],
                "fileSize": result[0]["fileSize"],
                "processed_chunk": result[0]["processed_chunk"],
                "fileSource": result[0]["fileSource"],
                "chunkNodeCount": result[0]["chunkNodeCount"],
                "chunkRelCount": result[0]["chunkRelCount"],
                "entityNodeCount": result[0]["entityNodeCount"],
                "entityEntityRelCount": result[0]["entityEntityRelCount"],
                "communityNodeCount": result[0]["communityNodeCount"],
                "communityRelCount": result[0]["communityRelCount"],
            }
        else:
            status = {"fileName": file_name, "status": "Failed"}
        logging.info(f"Result of document status in refresh : {result}")
        return create_api_response("Success", message="", file_name=status)
    except Exception as e:
        message = f"Unable to get the document status"
        error_message = str(e)
        logging.exception(f"{message}:{error_message}")
        return create_api_response("Failed", message=message)


@app.post("/cancelled_job")
async def cancelled_job(
    uri=Form(None),
    userName=Form(None),
    password=Form(None),
    database=Form(None),
    filenames=Form(None),
    source_types=Form(None),
    email=Form(None),
):
    try:
        start = time.time()
        graph = create_graph_database_connection(uri, userName, password, database)
        result = manually_cancelled_job(graph, filenames, source_types, MERGED_DIR, uri)
        end = time.time()
        elapsed_time = end - start
        json_obj = {
            "api_name": "cancelled_job",
            "db_url": uri,
            "userName": userName,
            "database": database,
            "filenames": filenames,
            "source_types": source_types,
            "logging_time": formatted_time(datetime.now(timezone.utc)),
            "elapsed_api_time": f"{elapsed_time:.2f}",
            "email": email,
        }
        logger.log_struct(json_obj, "INFO")
        return create_api_response("Success", message=result)
    except Exception as e:
        job_status = "Failed"
        message = "Unable to cancelled the running job"
        error_message = str(e)
        logging.exception(f"Exception in cancelling the running job:{error_message}")
        return create_api_response(job_status, message=message, error=error_message)
    finally:
        gc.collect()


@app.post("/populate_graph_schema")
async def populate_graph_schema(
    input_text=Form(None),
    model=Form(None),
    is_schema_description_checked=Form(None),
    is_local_storage=Form(None),
    email=Form(None),
):
    try:
        start = time.time()
        result = populate_graph_schema_from_text(
            input_text, model, is_schema_description_checked, is_local_storage
        )
        end = time.time()
        elapsed_time = end - start
        json_obj = {
            "api_name": "populate_graph_schema",
            "model": model,
            "is_schema_description_checked": is_schema_description_checked,
            "input_text": input_text,
            "logging_time": formatted_time(datetime.now(timezone.utc)),
            "elapsed_api_time": f"{elapsed_time:.2f}",
            "email": email,
        }
        logger.log_struct(json_obj, "INFO")
        return create_api_response("Success", data=result)
    except Exception as e:
        job_status = "Failed"
        message = "Unable to get the schema from text"
        error_message = str(e)
        logging.exception(f"Exception in getting the schema from text:{error_message}")
        return create_api_response(job_status, message=message, error=error_message)
    finally:
        gc.collect()


@app.post("/get_unconnected_nodes_list")
async def get_unconnected_nodes_list(
    uri=Form(None),
    userName=Form(None),
    password=Form(None),
    database=Form(None),
    email=Form(None),
):
    try:
        start = time.time()
        graph = create_graph_database_connection(uri, userName, password, database)
        graphDb_data_Access = graphDBdataAccess(graph)
        nodes_list, total_nodes = graphDb_data_Access.list_unconnected_nodes()
        end = time.time()
        elapsed_time = end - start
        json_obj = {
            "api_name": "get_unconnected_nodes_list",
            "db_url": uri,
            "userName": userName,
            "database": database,
            "logging_time": formatted_time(datetime.now(timezone.utc)),
            "elapsed_api_time": f"{elapsed_time:.2f}",
            "email": email,
        }
        logger.log_struct(json_obj, "INFO")
        return create_api_response("Success", data=nodes_list, message=total_nodes)
    except Exception as e:
        job_status = "Failed"
        message = "Unable to get the list of unconnected nodes"
        error_message = str(e)
        logging.exception(
            f"Exception in getting list of unconnected nodes:{error_message}"
        )
        return create_api_response(job_status, message=message, error=error_message)
    finally:
        gc.collect()


@app.post("/delete_unconnected_nodes")
async def delete_orphan_nodes(
    uri=Form(None),
    userName=Form(None),
    password=Form(None),
    database=Form(None),
    unconnected_entities_list=Form(),
    email=Form(None),
):
    try:
        start = time.time()
        graph = create_graph_database_connection(uri, userName, password, database)
        graphDb_data_Access = graphDBdataAccess(graph)
        result = graphDb_data_Access.delete_unconnected_nodes(unconnected_entities_list)
        end = time.time()
        elapsed_time = end - start
        json_obj = {
            "api_name": "delete_unconnected_nodes",
            "db_url": uri,
            "userName": userName,
            "database": database,
            "unconnected_entities_list": unconnected_entities_list,
            "logging_time": formatted_time(datetime.now(timezone.utc)),
            "elapsed_api_time": f"{elapsed_time:.2f}",
            "email": email,
        }
        logger.log_struct(json_obj, "INFO")
        return create_api_response(
            "Success", data=result, message="Unconnected entities delete successfully"
        )
    except Exception as e:
        job_status = "Failed"
        message = "Unable to delete the unconnected nodes"
        error_message = str(e)
        logging.exception(f"Exception in delete the unconnected nodes:{error_message}")
        return create_api_response(job_status, message=message, error=error_message)
    finally:
        gc.collect()


@app.post("/get_duplicate_nodes")
async def get_duplicate_nodes(
    uri=Form(None),
    userName=Form(None),
    password=Form(None),
    database=Form(None),
    email=Form(None),
):
    try:
        start = time.time()
        graph = create_graph_database_connection(uri, userName, password, database)
        graphDb_data_Access = graphDBdataAccess(graph)
        nodes_list, total_nodes = graphDb_data_Access.get_duplicate_nodes_list()
        end = time.time()
        elapsed_time = end - start
        json_obj = {
            "api_name": "get_duplicate_nodes",
            "db_url": uri,
            "userName": userName,
            "database": database,
            "logging_time": formatted_time(datetime.now(timezone.utc)),
            "elapsed_api_time": f"{elapsed_time:.2f}",
            "email": email,
        }
        logger.log_struct(json_obj, "INFO")
        return create_api_response("Success", data=nodes_list, message=total_nodes)
    except Exception as e:
        job_status = "Failed"
        message = "Unable to get the list of duplicate nodes"
        error_message = str(e)
        logging.exception(
            f"Exception in getting list of duplicate nodes:{error_message}"
        )
        return create_api_response(job_status, message=message, error=error_message)
    finally:
        gc.collect()


@app.post("/merge_duplicate_nodes")
async def merge_duplicate_nodes(
    uri=Form(None),
    userName=Form(None),
    password=Form(None),
    database=Form(None),
    duplicate_nodes_list=Form(),
    email=Form(None),
):
    try:
        start = time.time()
        graph = create_graph_database_connection(uri, userName, password, database)
        graphDb_data_Access = graphDBdataAccess(graph)
        result = graphDb_data_Access.merge_duplicate_nodes(duplicate_nodes_list)
        end = time.time()
        elapsed_time = end - start
        json_obj = {
            "api_name": "merge_duplicate_nodes",
            "db_url": uri,
            "userName": userName,
            "database": database,
            "duplicate_nodes_list": duplicate_nodes_list,
            "logging_time": formatted_time(datetime.now(timezone.utc)),
            "elapsed_api_time": f"{elapsed_time:.2f}",
            "email": email,
        }
        logger.log_struct(json_obj, "INFO")
        return create_api_response(
            "Success", data=result, message="Duplicate entities merged successfully"
        )
    except Exception as e:
        job_status = "Failed"
        message = "Unable to merge the duplicate nodes"
        error_message = str(e)
        logging.exception(f"Exception in merge the duplicate nodes:{error_message}")
        return create_api_response(job_status, message=message, error=error_message)
    finally:
        gc.collect()


@app.post("/merge_duplicate_entities")
async def merge_duplicate_entities(
    uri=Form(None),
    userName=Form(None),
    password=Form(None),
    database=Form(None),
    node_types=Form(default=["all"]),
    email=Form(None),
):
    """
    Seçilen node türlerine göre duplicate merge işlemi yapar.

    Args:
        node_types: Merge yapılacak node türleri.
                   Seçenekler: ["customers"], ["insurance_companies"], ["coverage_types"], ["all"]
                   Birden fazla da seçilebilir: ["customers", "insurance_companies"]
    """
    try:
        start = time.time()

        # Eğer userName, password, database boşsa environment'tan al
        if not userName:
            userName = os.environ.get("NEO4J_USERNAME", "neo4j")
        if not password:
            password = os.environ.get("NEO4J_PASSWORD", "password")
        if not database:
            database = os.environ.get("NEO4J_DATABASE", "neo4j")

        # node_types parametresini işle
        if isinstance(node_types, str):
            if node_types.startswith("[") and node_types.endswith("]"):
                # JSON string formatında geldiyse parse et
                import json

                node_types = json.loads(node_types)
            else:
                # Tek string geldiyse liste yap
                node_types = [node_types]

        # Geçerli node türlerini kontrol et
        valid_node_types = ["customers", "insurance_companies", "coverage_types", "all"]
        if not all(nt in valid_node_types for nt in node_types):
            invalid_types = [nt for nt in node_types if nt not in valid_node_types]
            return create_api_response(
                "Failed",
                message=f"Geçersiz node türleri: {invalid_types}. Geçerli türler: {valid_node_types}",
                error=f"Invalid node types: {invalid_types}",
            )

        graph = create_graph_database_connection(uri, userName, password, database)
        graphDb_data_Access = graphDBdataAccess(graph)

        # Selective merge işlemini çalıştır
        result = graphDb_data_Access.merge_duplicate_entities_selective(node_types)

        end = time.time()
        elapsed_time = end - start

        # Logging
        json_obj = {
            "api_name": "merge_duplicate_entities",
            "db_url": uri,
            "userName": userName,
            "database": database,
            "node_types": node_types,
            "merge_results": result,
            "logging_time": formatted_time(datetime.now(timezone.utc)),
            "elapsed_api_time": f"{elapsed_time:.2f}",
            "email": email,
        }
        logger.log_struct(json_obj, "INFO")

        # Response mesajını oluştur
        if result.get("error"):
            return create_api_response(
                "Failed",
                message="Duplicate merge işlemi sırasında hata oluştu",
                error=result["error"],
            )

        total_merged = result.get("total_merged", 0)
        details = []
        for node_type, count in result.items():
            if node_type != "total_merged" and count > 0:
                details.append(f"{node_type}: {count}")

        details_str = ", ".join(details) if details else "hiçbir duplicate bulunamadı"
        message = f"Duplicate merge tamamlandı. Toplam {total_merged} node birleştirildi ({details_str})"

        return create_api_response("Success", data=result, message=message)

    except Exception as e:
        job_status = "Failed"
        message = "Duplicate entities merge işlemi başarısız"
        error_message = str(e)
        logging.exception(f"Exception in merge duplicate entities: {error_message}")
        return create_api_response(job_status, message=message, error=error_message)
    finally:
        gc.collect()


@app.post("/create_embeddings")
async def create_embeddings(
    uri=Form(None),
    userName=Form(None),
    password=Form(None),
    database=Form(None),
    file_names=Form(...),
    email=Form(None),
):
    """
    Belirtilen dosyalar için chunk embedding'leri oluşturur.

    Args:
        file_names: Embedding oluşturulacak dosya adları (JSON string formatında liste)
    """
    try:
        start = time.time()

        # Eğer userName, password, database boşsa environment'tan al
        if not userName:
            userName = os.environ.get("NEO4J_USERNAME", "neo4j")
        if not password:
            password = os.environ.get("NEO4J_PASSWORD", "password")
        if not database:
            database = os.environ.get("NEO4J_DATABASE", "neo4j")

        # file_names parametresini işle
        if isinstance(file_names, str):
            if file_names.startswith("[") and file_names.endswith("]"):
                # JSON string formatında geldiyse parse et
                import json

                file_names = json.loads(file_names)
            else:
                # Tek string geldiyse liste yap
                file_names = [file_names]

        if not file_names or len(file_names) == 0:
            return create_api_response(
                "Failed",
                message="En az bir dosya adı belirtilmelidir",
                error="No file names provided",
            )

        logging.info(
            f"🔄 {len(file_names)} dosya için embedding oluşturma başlatılıyor: {file_names}"
        )

        graph = create_graph_database_connection(uri, userName, password, database)
        graphDb_data_Access = graphDBdataAccess(graph)

        # Embedding oluşturma işlemini çalıştır
        result = graphDb_data_Access.create_embeddings_for_documents(file_names)

        end = time.time()
        elapsed_time = end - start

        # Logging
        json_obj = {
            "api_name": "create_embeddings",
            "db_url": uri,
            "userName": userName,
            "database": database,
            "file_names": file_names,
            "embedding_results": {
                "total_files": result.get("total_files", 0),
                "total_chunks_processed": result.get("total_chunks_processed", 0),
                "total_chunks_updated": result.get("total_chunks_updated", 0),
                "embedding_model": result.get("embedding_model", ""),
                "embedding_dimension": result.get("embedding_dimension", 0),
            },
            "logging_time": formatted_time(datetime.now(timezone.utc)),
            "elapsed_api_time": f"{elapsed_time:.2f}",
            "email": email,
        }
        logger.log_struct(json_obj, "INFO")

        # Response mesajını oluştur
        if result.get("error"):
            return create_api_response(
                "Failed",
                message="Embedding oluşturma işlemi sırasında hata oluştu",
                error=result["error"],
            )

        total_files = result.get("total_files", 0)
        total_chunks_updated = result.get("total_chunks_updated", 0)
        embedding_model = result.get("embedding_model", "Unknown")

        # Dosya bazında sonuçları özetle
        success_files = []
        failed_files = []
        skipped_files = []

        for file_name, file_result in result.get("files", {}).items():
            status = file_result.get("status", "unknown")
            if status == "success":
                success_files.append(
                    f"{file_name} ({file_result.get('chunks_updated', 0)} chunk)"
                )
            elif status == "error":
                failed_files.append(
                    f"{file_name} ({file_result.get('message', 'Bilinmeyen hata')})"
                )
            elif status == "skipped":
                skipped_files.append(f"{file_name} (zaten embedding'e sahip)")

        # Sonuç mesajını oluştur
        message_parts = []
        if success_files:
            message_parts.append(f"✅ Başarılı: {len(success_files)} dosya")
        if skipped_files:
            message_parts.append(f"⏭️ Atlandı: {len(skipped_files)} dosya")
        if failed_files:
            message_parts.append(f"❌ Başarısız: {len(failed_files)} dosya")

        if total_chunks_updated > 0:
            main_message = f"Embedding oluşturma tamamlandı. Toplam {total_chunks_updated} chunk için embedding oluşturuldu ({embedding_model})"
        else:
            main_message = "Hiçbir chunk için yeni embedding oluşturulmadı"

        if message_parts:
            main_message += f" - {', '.join(message_parts)}"

        return create_api_response("Success", data=result, message=main_message)

    except Exception as e:
        job_status = "Failed"
        message = "Embedding oluşturma işlemi başarısız"
        error_message = str(e)
        logging.exception(f"Exception in create embeddings: {error_message}")
        return create_api_response(job_status, message=message, error=error_message)
    finally:
        gc.collect()


@app.post("/create_entity_embeddings")
async def create_entity_embeddings(
    uri=Form(None),
    userName=Form(None),
    password=Form(None),
    database=Form(None),
    node_types=Form(...),
    email=Form(None),
):
    """
    Belirtilen entity node türleri için embedding'ler oluşturur.

    Args:
        node_types: Embedding oluşturulacak node türleri (JSON string formatında liste)
                   Örn: ["Customer", "Policy"] veya ["all"]
    """
    try:
        start = time.time()

        # Eğer userName, password, database boşsa environment'tan al
        if not userName:
            userName = os.environ.get("NEO4J_USERNAME", "neo4j")
        if not password:
            password = os.environ.get("NEO4J_PASSWORD", "password")
        if not database:
            database = os.environ.get("NEO4J_DATABASE", "neo4j")

        # node_types parametresini işle
        if isinstance(node_types, str):
            if node_types.startswith("[") and node_types.endswith("]"):
                # JSON string formatında geldiyse parse et
                import json

                node_types = json.loads(node_types)
            else:
                # Tek string geldiyse liste yap
                node_types = [node_types]

        if not node_types or len(node_types) == 0:
            return create_api_response(
                "Failed",
                message="En az bir node türü belirtilmelidir",
                error="No node types provided",
            )

        logging.info(
            f"🔄 {len(node_types)} node türü için entity embedding oluşturma başlatılıyor: {node_types}"
        )

        graph = create_graph_database_connection(uri, userName, password, database)
        graphDb_data_Access = graphDBdataAccess(graph)

        # Entity embedding oluşturma işlemini çalıştır
        result = graphDb_data_Access.create_entity_embeddings(node_types)

        end = time.time()
        elapsed_time = end - start

        # Logging
        json_obj = {
            "api_name": "create_entity_embeddings",
            "db_url": uri,
            "userName": userName,
            "database": database,
            "node_types": node_types,
            "embedding_results": {
                "total_node_types": result.get("total_node_types", 0),
                "total_entities_processed": result.get("total_entities_processed", 0),
                "total_embeddings_created": result.get("total_embeddings_created", 0),
                "embedding_model": result.get("embedding_model", ""),
                "embedding_dimension": result.get("embedding_dimension", 0),
            },
            "logging_time": formatted_time(datetime.now(timezone.utc)),
            "elapsed_api_time": f"{elapsed_time:.2f}",
            "email": email,
        }
        logger.log_struct(json_obj, "INFO")

        # Response mesajını oluştur
        if result.get("error"):
            return create_api_response(
                "Failed",
                message="Entity embedding oluşturma işlemi sırasında hata oluştu",
                error=result["error"],
            )

        total_types = result.get("total_node_types", 0)
        total_embeddings = result.get("total_embeddings_created", 0)
        embedding_model = result.get("embedding_model", "Unknown")
        available_types = result.get("available_types", [])

        # Node türü bazında sonuçları özetle
        success_types = []
        failed_types = []
        skipped_types = []

        for node_type, type_result in result.get("node_types", {}).items():
            status = type_result.get("status", "unknown")
            if status == "success":
                success_types.append(
                    f"{node_type} ({type_result.get('entities_updated', 0)} entity)"
                )
            elif status == "error":
                failed_types.append(
                    f"{node_type} ({type_result.get('message', 'Bilinmeyen hata')})"
                )
            elif status == "skipped":
                skipped_types.append(f"{node_type} (zaten embedding'e sahip)")

        # Sonuç mesajını oluştur
        message_parts = []
        if success_types:
            message_parts.append(f"✅ Başarılı: {', '.join(success_types)}")
        if skipped_types:
            message_parts.append(f"⏭️ Atlandı: {', '.join(skipped_types)}")
        if failed_types:
            message_parts.append(f"❌ Başarısız: {', '.join(failed_types)}")

        if total_embeddings > 0:
            main_message = f"Entity embedding oluşturma tamamlandı. Toplam {total_embeddings} entity için embedding oluşturuldu ({embedding_model})"
        else:
            main_message = "Hiçbir entity için yeni embedding oluşturulmadı"

        if message_parts:
            main_message += f" - {'. '.join(message_parts)}"

        if available_types:
            main_message += f". Mevcut node türleri: {', '.join(available_types)}"

        return create_api_response("Success", data=result, message=main_message)

    except Exception as e:
        job_status = "Failed"
        message = "Entity embedding oluşturma işlemi başarısız"
        error_message = str(e)
        logging.exception(f"Exception in create entity embeddings: {error_message}")
        return create_api_response(job_status, message=message, error=error_message)
    finally:
        gc.collect()


@app.post("/drop_create_vector_index")
async def drop_create_vector_index(
    uri=Form(None),
    userName=Form(None),
    password=Form(None),
    database=Form(None),
    isVectorIndexExist=Form(),
    email=Form(None),
):
    try:
        start = time.time()
        graph = create_graph_database_connection(uri, userName, password, database)
        graphDb_data_Access = graphDBdataAccess(graph)
        result = graphDb_data_Access.drop_create_vector_index(isVectorIndexExist)
        end = time.time()
        elapsed_time = end - start
        json_obj = {
            "api_name": "drop_create_vector_index",
            "db_url": uri,
            "userName": userName,
            "database": database,
            "isVectorIndexExist": isVectorIndexExist,
            "logging_time": formatted_time(datetime.now(timezone.utc)),
            "elapsed_api_time": f"{elapsed_time:.2f}",
            "email": email,
        }
        logger.log_struct(json_obj, "INFO")
        return create_api_response("Success", message=result)
    except Exception as e:
        job_status = "Failed"
        message = "Unable to drop and re-create vector index with correct dimesion as per application configuration"
        error_message = str(e)
        logging.exception(
            f"Exception into drop and re-create vector index with correct dimesion as per application configuration:{error_message}"
        )
        return create_api_response(job_status, message=message, error=error_message)
    finally:
        gc.collect()


@app.post("/retry_processing")
async def retry_processing(
    uri=Form(None),
    userName=Form(None),
    password=Form(None),
    database=Form(None),
    file_name=Form(),
    retry_condition=Form(),
    email=Form(None),
):
    try:
        start = time.time()
        graph = create_graph_database_connection(uri, userName, password, database)
        chunks = execute_graph_query(
            graph, QUERY_TO_GET_CHUNKS, params={"filename": file_name}
        )
        end = time.time()
        elapsed_time = end - start
        json_obj = {
            "api_name": "retry_processing",
            "db_url": uri,
            "userName": userName,
            "database": database,
            "file_name": file_name,
            "retry_condition": retry_condition,
            "logging_time": formatted_time(datetime.now(timezone.utc)),
            "elapsed_api_time": f"{elapsed_time:.2f}",
            "email": email,
        }
        logger.log_struct(json_obj, "INFO")
        if chunks[0]["text"] is None or chunks[0]["text"] == "" or not chunks:
            return create_api_response(
                "Success",
                message=f"Chunks are not created for the file{file_name}. Please upload again the file to re-process.",
                data=chunks,
            )
        else:
            await asyncio.to_thread(set_status_retry, graph, file_name, retry_condition)
            return create_api_response(
                "Success", message=f"Status set to Chunked for filename : {file_name}"
            )
    except Exception as e:
        job_status = "Failed"
        message = "Unable to set status to Retry"
        error_message = str(e)
        logging.exception(f"{error_message}")
        return create_api_response(job_status, message=message, error=error_message)
    finally:
        gc.collect()


@app.post("/metric")
async def calculate_metric(
    question: str = Form(),
    context: str = Form(),
    answer: str = Form(),
    model: str = Form(),
    mode: str = Form(),
):
    try:
        start = time.time()
        context_list = (
            [str(item).strip() for item in json.loads(context)] if context else []
        )
        answer_list = (
            [str(item).strip() for item in json.loads(answer)] if answer else []
        )
        mode_list = [str(item).strip() for item in json.loads(mode)] if mode else []

        result = await asyncio.to_thread(
            get_ragas_metrics, question, context_list, answer_list, model
        )
        if result is None or "error" in result:
            return create_api_response(
                "Failed",
                message="Failed to calculate evaluation metrics.",
                error=result.get("error", "Ragas evaluation returned null"),
            )
        data = {
            mode: {metric: result[metric][i] for metric in result}
            for i, mode in enumerate(mode_list)
        }
        end = time.time()
        elapsed_time = end - start
        json_obj = {
            "api_name": "metric",
            "question": question,
            "context": context,
            "answer": answer,
            "model": model,
            "mode": mode,
            "logging_time": formatted_time(datetime.now(timezone.utc)),
            "elapsed_api_time": f"{elapsed_time:.2f}",
        }
        logger.log_struct(json_obj, "INFO")
        return create_api_response("Success", data=data)
    except Exception as e:
        logging.exception(f"Error while calculating evaluation metrics: {e}")
        return create_api_response(
            "Failed", message="Error while calculating evaluation metrics", error=str(e)
        )
    finally:
        gc.collect()


@app.post("/additional_metrics")
async def calculate_additional_metrics(
    question: str = Form(),
    context: str = Form(),
    answer: str = Form(),
    reference: str = Form(),
    model: str = Form(),
    mode: str = Form(),
):
    try:
        context_list = (
            [str(item).strip() for item in json.loads(context)] if context else []
        )
        answer_list = (
            [str(item).strip() for item in json.loads(answer)] if answer else []
        )
        mode_list = [str(item).strip() for item in json.loads(mode)] if mode else []
        result = await get_additional_metrics(
            question, context_list, answer_list, reference, model
        )
        if result is None or "error" in result:
            return create_api_response(
                "Failed",
                message="Failed to calculate evaluation metrics.",
                error=result.get("error", "Ragas evaluation returned null"),
            )
        data = {
            mode: {metric: result[i][metric] for metric in result[i]}
            for i, mode in enumerate(mode_list)
        }
        return create_api_response("Success", data=data)
    except Exception as e:
        logging.exception(f"Error while calculating evaluation metrics: {e}")
        return create_api_response(
            "Failed", message="Error while calculating evaluation metrics", error=str(e)
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
    email=Form(None),
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
            page_no=page_no,
        )
        end = time.time()
        elapsed_time = end - start
        json_obj = {
            "api_name": "fetch_chunktext",
            "db_url": uri,
            "userName": userName,
            "database": database,
            "document_name": document_name,
            "page_no": page_no,
            "logging_time": formatted_time(datetime.now(timezone.utc)),
            "elapsed_api_time": f"{elapsed_time:.2f}",
            "email": email,
        }
        logger.log_struct(json_obj, "INFO")
        return create_api_response(
            "Success", data=result, message=f"Total elapsed API time {elapsed_time:.2f}"
        )
    except Exception as e:
        job_status = "Failed"
        message = "Unable to get chunk text response"
        error_message = str(e)
        logging.exception(f"Exception in fetch_chunktext: {error_message}")
        return create_api_response(job_status, message=message, error=error_message)
    finally:
        gc.collect()


@app.post("/backend_connection_configuration")
async def backend_connection_configuration():
    try:
        start = time.time()
        uri = os.getenv("NEO4J_URI")
        username = os.getenv("NEO4J_USERNAME")
        database = os.getenv("NEO4J_DATABASE")
        password = os.getenv("NEO4J_PASSWORD")
        gcs_file_cache = os.environ.get("GCS_FILE_CACHE")
        logging.info(f"🔍 Backend connection config - NEO4J_URI from env: {uri}")
        if all([uri, username, database, password]):
            graph = Neo4jGraph()
            logging.info(f"login connection status of object: {graph}")
            if graph is not None:
                graph_connection = True
                graphDb_data_Access = graphDBdataAccess(graph)
                result = graphDb_data_Access.connection_check_and_get_vector_dimensions(
                    database
                )
                result["gcs_file_cache"] = gcs_file_cache
                result["uri"] = uri
                logging.info(f"🔍 Backend connection config - Returning URI: {result.get('uri')}")
                end = time.time()
                elapsed_time = end - start
                result["api_name"] = "backend_connection_configuration"
                result["elapsed_api_time"] = f"{elapsed_time:.2f}"
                result["graph_connection"] = (f"{graph_connection}",)
                result["connection_from"] = "backendAPI"
                logger.log_struct(result, "INFO")
                return create_api_response(
                    "Success", message=f"Backend connection successful", data=result
                )
        else:
            graph_connection = False
            return create_api_response(
                "Success",
                message=f"Backend connection is not successful",
                data=graph_connection,
            )
    except Exception as e:
        graph_connection = False
        job_status = "Failed"
        message = "Unable to connect backend DB"
        error_message = str(e)
        logging.exception(f"{error_message}")
        return create_api_response(
            job_status,
            message=message,
            error=error_message.rstrip(".") + ", or fill from the login dialog.",
            data=graph_connection,
        )
    finally:
        gc.collect()


@app.post("/schema_visualization")
async def get_schema_visualization(
    uri=Form(None), userName=Form(None), password=Form(None), database=Form(None)
):
    try:
        start = time.time()
        result = await asyncio.to_thread(
            visualize_schema,
            uri=uri,
            userName=userName,
            password=password,
            database=database,
        )
        if result:
            logging.info("Graph schema visualization query successful")
        end = time.time()
        elapsed_time = end - start
        logging.info(f"Schema result from DB: {result}")
        json_obj = {
            "api_name": "schema_visualization",
            "db_url": uri,
            "userName": userName,
            "database": database,
            "logging_time": formatted_time(datetime.now(timezone.utc)),
            "elapsed_api_time": f"{elapsed_time:.2f}",
        }
        logger.log_struct(json_obj, "INFO")
        return create_api_response(
            "Success", data=result, message=f"Total elapsed API time {elapsed_time:.2f}"
        )
    except Exception as e:
        message = "Unable to get schema visualization from neo4j database"
        error_message = str(e)
        logging.exception(f"Exception:{error_message}")
        return create_api_response("Failed", message=message, error=error_message)


@app.get("/document_analytics")
async def document_analytics(
    uri=Form(None),
    userName=Form(None),
    password=Form(None),
    database=Form(None),
    analysis_type=Form("overview"),
):
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
            api_name = "document_analytics/overview"

        elif analysis_type == "person_policies":
            result = get_person_policy_analytics(graph)
            api_name = "document_analytics/person_policies"

        elif analysis_type == "company_analysis":
            result = get_company_analytics(graph)
            api_name = "document_analytics/company_analysis"

        else:
            result = {"error": f"Unknown analysis_type: {analysis_type}"}
            api_name = "document_analytics/error"

        elapsed_time = time.time() - start_time
        json_obj = {
            "api_name": api_name,
            "db_url": uri,
            "userName": userName,
            "database": database,
            "logging_time": formatted_time(datetime.now(timezone.utc)),
            "elapsed_api_time": f"{elapsed_time:.2f}",
            "analysis_type": analysis_type,
        }
        logger.log_struct(json_obj, "INFO")

        return create_api_response(
            "Success",
            data=result,
            message=f"Analysis completed in {elapsed_time:.2f} seconds",
        )

    except Exception as e:
        message = f"Unable to complete document analytics: {analysis_type}"
        error_message = str(e)
        logging.exception(f"Exception in document_analytics: {error_message}")
        return create_api_response("Failed", message=message, error=error_message)


@app.post("/search_person_documents")
async def search_person_documents_endpoint(
    uri=Form(None),
    userName=Form(None),
    password=Form(None),
    database=Form(None),
    person_name=Form(None),
):
    """
    Belirli bir kişinin tüm document'larını arar
    """
    try:
        if not person_name:
            return create_api_response(
                "Failed", message="person_name parameter is required"
            )

        start_time = time.time()
        graph = create_graph_database_connection(uri, userName, password, database)

        result = search_person_documents(graph, person_name)

        elapsed_time = time.time() - start_time
        json_obj = {
            "api_name": "search_person_documents",
            "db_url": uri,
            "userName": userName,
            "database": database,
            "logging_time": formatted_time(datetime.now(timezone.utc)),
            "elapsed_api_time": f"{elapsed_time:.2f}",
            "person_name": person_name,
        }
        logger.log_struct(json_obj, "INFO")

        return create_api_response(
            "Success",
            data=result,
            message=f"Search completed in {elapsed_time:.2f} seconds",
        )

    except Exception as e:
        message = f"Unable to search documents for person: {person_name}"
        error_message = str(e)
        logging.exception(f"Exception in search_person_documents: {error_message}")
        return create_api_response("Failed", message=message, error=error_message)
        error_message = str(e)
        logging.info(message)
        logging.exception(f"Exception:{error_message}")
        return create_api_response("Failed", message=message, error=error_message)
    finally:
        gc.collect()


@app.delete("/delete_similar_relationships")
async def delete_similar_relationships(
    uri=Form(None), userName=Form(None), password=Form(None), database=Form(None)
):
    """
    Tüm SIMILAR ilişkilerini veritabanından siler
    """
    try:
        logging.info("🗑️ SIMILAR ilişkileri silme işlemi başlatıldı")

        # Neo4j bağlantısı oluştur
        graph = create_graph_database_connection(uri, userName, password, database)

        # SIMILAR ilişkilerini say
        count_query = "MATCH ()-[r:SIMILAR]-() RETURN count(r) as similar_count"
        count_result = graph.query(count_query, session_params={"database": database})
        similar_count = count_result[0]["similar_count"] if count_result else 0

        logging.info(f"📊 Silinecek SIMILAR ilişki sayısı: {similar_count}")

        if similar_count == 0:
            return create_api_response(
                "Success",
                message="Silinecek SIMILAR ilişkisi bulunamadı",
                data={"deleted_relationships": 0},
            )

        # SIMILAR ilişkilerini sil
        delete_query = """
            MATCH ()-[r:SIMILAR]-()
            DELETE r
            RETURN count(r) as deleted_count
        """

        delete_result = graph.query(delete_query, session_params={"database": database})
        deleted_count = (
            similar_count  # Neo4j DELETE count döndürmez, önceki sayımı kullan
        )

        logging.info(f"✅ {deleted_count} SIMILAR ilişkisi silindi")

        return create_api_response(
            "Success",
            message=f"{deleted_count} SIMILAR ilişkisi başarıyla silindi",
            data={"deleted_relationships": deleted_count},
        )

    except Exception as e:
        error_message = str(e)
        logging.error(f"❌ SIMILAR ilişkileri silme hatası: {error_message}")
        return create_api_response(
            "Failed",
            message="SIMILAR ilişkileri silme işlemi başarısız",
            error=error_message,
        )


# ==========================================
# V2 FILE UPLOAD QUEUE API ENDPOINTS
# ==========================================

from src.models.file_queue_models import get_file_queue_db, FileStatus, UploadedFile
from src.utf8_utils import normalize_file_name
from typing import List, Dict, Any
from pydantic import BaseModel
import shutil
from pathlib import Path


# Pydantic models for API responses
class FileResponse(BaseModel):
    id: int
    filename: str
    original_name: str
    file_path: str
    upload_date: str
    file_size: int
    file_hash: str
    status: str
    created_at: str
    updated_at: str


class QueueStatsResponse(BaseModel):
    uploaded: int
    queued: int
    processing: int
    completed: int
    error: int
    total: int


class ProcessFileRequest(BaseModel):
    model: str = "openai_gpt_4o_mini"
    uri: str
    userName: str
    password: str
    database: str
    generateEmbedding: str = "false"


# Upload directory
UPLOAD_DIR = Path(__file__).parent / "upload"
UPLOAD_DIR.mkdir(exist_ok=True)


@app.post("/api/v2/files/upload")
async def upload_file_to_queue(
    file: UploadFile = File(...),
    originalname: str = Form(None),
    auto_process: str = Form("false"),
):
    """
    V2 Upload: Saves file to output structure + Image Extraction + S3 Upload
    Creates output/document_name/ structure with PDF, images, and uploads to S3
    """
    from concurrent.futures import ThreadPoolExecutor
    import json
    import shutil

    try:
        start = time.time()

        # Normalize filename
        normalized_filename = (
            normalize_file_name(originalname) if originalname else file.filename
        )
        logging.info(
            f"📤 V2 Upload API - File: {originalname} -> {normalized_filename}"
        )

        # Create output directory structure
        from src.document_sources.s3_upload_utils import (
            create_document_output_structure,
        )

        document_dir, pdf_dir, images_dir = create_document_output_structure(
            normalized_filename, "output"
        )

        # Save file to document directory structure
        file_path = os.path.join(pdf_dir, normalized_filename)
        content = await file.read()

        with open(file_path, "wb") as f:
            f.write(content)

        file_size = len(content)
        logging.info(
            f"✅ File saved to output structure: {file_path} ({file_size} bytes)"
        )

        # Image extraction will be done in background processor (20-file batches)
        # Upload endpoint only saves the file and returns immediately
        logging.info(
            f"ℹ️ Image extraction will be done in background processor for: {normalized_filename}"
        )

        # Add to database
        db = get_file_queue_db()

        # Check for existing file by hash to prevent duplicates
        file_hash = UploadedFile.calculate_file_hash(str(file_path))
        existing_file = db.get_file_by_hash(file_hash) if file_hash else None

        if existing_file:
            logging.info(
                f"📋 File already exists in queue: {existing_file.filename} (ID: {existing_file.id})"
            )
            return create_api_response(
                "Success",
                message="File already exists in queue",
                data={
                    "file_id": existing_file.id,
                    "filename": existing_file.filename,
                    "original_name": existing_file.original_name,
                    "upload_status": existing_file.upload_status,
                    "chunking_status": existing_file.chunking_status,
                    "graph_status": existing_file.graph_status,
                    "file_size": existing_file.file_size,
                    "duplicate": True,
                },
            )

        # Convert auto_process string to boolean
        auto_process_bool = (
            auto_process.lower() in ("true", "1", "yes", "on")
            if auto_process
            else False
        )

        # Add new file to queue
        uploaded_file = db.add_file(
            filename=normalized_filename,
            original_name=originalname or file.filename,
            file_path=str(file_path),
            file_size=file_size,
            auto_process=auto_process_bool,
        )

        # Image extraction and S3 upload will be done in background processor
        # No metadata update needed here

        elapsed_time = time.time() - start
        logging.info(
            f"✅ V2 Upload completed: ID={uploaded_file.id}, Status={uploaded_file.upload_status} ({elapsed_time:.2f}s)"
        )

        # Neo4j'ye initial sync et (upload başarılı)
        try:
            from src.models.status_sync import sync_queue_db_status_to_neo4j

            graph_connection = create_graph_database_connection(
                os.environ.get("NEO4J_URI"),
                os.environ.get("NEO4J_USERNAME"),
                os.environ.get("NEO4J_PASSWORD"),
                os.environ.get("NEO4J_DATABASE", "neo4j"),
            )
            sync_queue_db_status_to_neo4j(
                graph=graph_connection,
                file_name=uploaded_file.filename,
                upload_status=uploaded_file.upload_status,
                chunking_status=uploaded_file.chunking_status,
                graph_status=uploaded_file.graph_status,
                embedding_status=uploaded_file.embedding_status,
                database=os.environ.get("NEO4J_DATABASE", "neo4j"),
            )
            logging.info(
                f"✅ Initial Neo4j sync for uploaded file: {uploaded_file.filename}"
            )
        except Exception as sync_error:
            logging.warning(
                f"⚠️ Could not sync upload status to Neo4j: {str(sync_error)}"
            )

        # Trigger celery task for image extraction and processing
        try:
            celery_app.send_task("src.tasks.process_file_pipeline", args=[uploaded_file.id])
            logging.info(f"✅ Celery task triggered for file ID: {uploaded_file.id}")
        except Exception as celery_error:
            logging.warning(f"⚠️ Failed to trigger celery task: {celery_error}")
            # Continue anyway - celery worker will pick it up from queue

        return create_api_response(
            "Success",
            message="File uploaded successfully. Image extraction will be done in background.",
            data={
                "file_id": uploaded_file.id,
                "filename": uploaded_file.filename,
                "original_name": uploaded_file.original_name,
                "upload_status": uploaded_file.upload_status,
                "chunking_status": uploaded_file.chunking_status,
                "graph_status": uploaded_file.graph_status,
                "file_size": uploaded_file.file_size,
                "duplicate": False,
            },
        )

    except Exception as e:
        error_message = str(e)
        logging.error(f"❌ V2 Upload failed for {originalname}: {error_message}")
        return create_api_response(
            "Failed", message="File upload failed", error=error_message
        )


@app.get("/api/v2/files/list")
async def list_queued_files():
    """Get list of all files in queue with their status (no pagination - returns all files)"""
    try:
        db = get_file_queue_db()
        # Tüm kayıtları çek (limit ve offset yok)
        files = db.get_all_files(limit=None, offset=0)

        files_data = []
        for f in files:
            files_data.append(
                {
                    "id": f.id,
                    "filename": f.filename,
                    "original_name": f.original_name,
                    "file_path": f.file_path,
                    "upload_date": f.upload_date.isoformat(),
                    "file_size": f.file_size,
                    "file_hash": f.file_hash,
                    "status": f.status,
                    "upload_status": f.upload_status,
                    "chunking_status": f.chunking_status,
                    "graph_status": f.graph_status,
                    "embedding_status": f.embedding_status,
                    "created_at": f.created_at.isoformat(),
                    "updated_at": f.updated_at.isoformat(),
                    "chunking_started_at": (
                        f.chunking_started_at.isoformat()
                        if f.chunking_started_at
                        else None
                    ),
                    "chunking_completed_at": (
                        f.chunking_completed_at.isoformat()
                        if f.chunking_completed_at
                        else None
                    ),
                    "graph_started_at": (
                        f.graph_started_at.isoformat() if f.graph_started_at else None
                    ),
                    "graph_completed_at": (
                        f.graph_completed_at.isoformat()
                        if f.graph_completed_at
                        else None
                    ),
                    "embedding_started_at": (
                        f.embedding_started_at.isoformat()
                        if f.embedding_started_at
                        else None
                    ),
                    "embedding_completed_at": (
                        f.embedding_completed_at.isoformat()
                        if f.embedding_completed_at
                        else None
                    ),
                    "processing_started_at": (
                        f.processing_started_at.isoformat()
                        if f.processing_started_at
                        else None
                    ),
                    "processing_completed_at": (
                        f.processing_completed_at.isoformat()
                        if f.processing_completed_at
                        else None
                    ),
                    "processing_error": f.processing_error,
                }
            )

        return create_api_response(
            "Success", data={"files": files_data, "count": len(files_data)}
        )

    except Exception as e:
        error_message = str(e)
        logging.error(f"❌ Failed to list files: {error_message}")
        return create_api_response(
            "Failed", message="Failed to retrieve file list", error=error_message
        )


@app.post("/api/v2/files/{file_id}/chunk")
async def start_chunking(file_id: str):
    """Start chunking process for a file, multiple files, or all files (OCR + Image Extraction + Markdown)

    - If file_id is "all", processes all files with chunking_status="ready"
    - If file_id is comma-separated IDs (e.g., "1,2,3"), processes those specific files in batches
    - Otherwise, processes single file
    """
    try:
        db = get_file_queue_db()
        db_session = db.get_db_session()

        # Handle "all" parameter
        if file_id.lower() == "all":
            # Get all files ready for chunking (image extraction completed)
            files_ready_for_chunking = (
                db_session.query(UploadedFile)
                .filter(UploadedFile.upload_status == "uploaded")
                .filter(UploadedFile.chunking_status == "ready")
                .order_by(UploadedFile.created_at.asc())
                .all()
            )

            if not files_ready_for_chunking:
                db_session.close()
                return create_api_response(
                    "Success",
                    message="No files ready for chunking",
                    data={"processed_count": 0},
                )

            # Process files in batches using celery tasks
            batch_size = int(os.environ.get("V2_BATCH_SIZE", "20"))
            processed_count = 0

            # Önce tüm "ready" dosyalarını queue'ya al (status = "queued")
            # Bu sayede frontend'te "Queued for Chunking" gösterilebilir ve dosyalar kilitlenir
            all_ready_files = (
                db_session.query(UploadedFile)
                .filter(UploadedFile.upload_status == "uploaded")
                .filter(UploadedFile.chunking_status == "ready")
                .all()
            )

            if all_ready_files:
                all_ready_file_ids = [f.id for f in all_ready_files]
                db_session.query(UploadedFile).filter(
                    UploadedFile.id.in_(all_ready_file_ids)
                ).update(
                    {UploadedFile.status: "queued"},  # status = "queued" ile kuyruğa al
                    synchronize_session=False,
                )
                db_session.commit()
                logging.info(
                    f"📋 {len(all_ready_files)} dosya chunking kuyruğuna alındı (status=queued, chunking_status=ready)"
                )

            # While loop ile batch'ler halinde işle (Image extraction'daki gibi)
            while True:
                # Her seferinde fresh batch al (status = "queued" ve chunking_status = "ready")
                batch_files = (
                    db_session.query(UploadedFile)
                    .filter(UploadedFile.upload_status == "uploaded")
                    .filter(UploadedFile.status == "queued")  # Queue'daki dosyalar
                    .filter(UploadedFile.chunking_status == "ready")
                    .order_by(UploadedFile.created_at.asc())
                    .limit(batch_size)
                    .all()
                )

                if not batch_files:
                    # No more files in queue
                    break

                # Batch'teki dosyaların ID'lerini al
                batch_file_ids = [f.id for f in batch_files]

                # Sadece bu batch'teki dosyaların status'unu "processing" ve chunking_status'unu "chunking" olarak güncelle
                # (Image extraction'daki mantık gibi)
                db_session.query(UploadedFile).filter(
                    UploadedFile.id.in_(batch_file_ids)
                ).update(
                    {
                        UploadedFile.status: "processing",  # status = "processing" ile işleme al
                        UploadedFile.chunking_status: "chunking",
                        UploadedFile.chunking_started_at: datetime.now(timezone.utc),
                    },
                    synchronize_session=False,
                )
                db_session.commit()

                logging.info(
                    f"📦 Chunking batch seçildi: {len(batch_files)} dosya işlenmeye başlanıyor (ID'ler: {batch_file_ids})"
                )

                # Queue celery tasks for chunking
                for file_record in batch_files:
                    celery_app.send_task("src.tasks.chunk_file_task", args=[file_record.id])
                processed_count += len(batch_files)

                # Wait before processing next batch (eğer daha fazla dosya varsa)
                remaining_files = (
                    db_session.query(UploadedFile)
                    .filter(UploadedFile.upload_status == "uploaded")
                    .filter(UploadedFile.status == "queued")  # Queue'daki dosyalar
                    .filter(UploadedFile.chunking_status == "ready")
                    .count()
                )

                if remaining_files > 0:
                    logging.info(
                        f"⏳ Waiting before starting next batch ({remaining_files} files remaining)..."
                    )
                    await asyncio.sleep(2)  # 2 saniye bekle, sonraki batch'i al
                else:
                    # No more files, break
                    break

            db_session.close()
            return create_api_response(
                "Success",
                message=f"Chunking started for {processed_count} file(s)",
                data={"processed_count": processed_count},
            )

        # Single file processing
        try:
            file_id_int = int(file_id)
        except ValueError:
            db_session.close()
            return create_api_response("Failed", message="Invalid file_id parameter")

        file_record = db_session.query(UploadedFile).filter_by(id=file_id_int).first()
        if not file_record:
            db_session.close()
            return create_api_response("Failed", message="File not found")

        # Check if already chunked
        if file_record.chunking_status == "chunked":
            db_session.close()
            return create_api_response(
                "Success",
                message="File already chunked",
                data={"file_id": file_id_int, "chunking_status": "chunked"},
            )

        # Check if file is ready for chunking
        if file_record.chunking_status != "ready":
            db_session.close()
            return create_api_response(
                "Failed",
                message=f"File is not ready for chunking. Current status: {file_record.chunking_status}. Expected: ready",
                data={"file_id": file_id_int, "chunking_status": file_record.chunking_status},
            )

        # Check if upload_status is uploaded
        if file_record.upload_status != "uploaded":
            db_session.close()
            return create_api_response(
                "Failed",
                message=f"File upload is not completed. Current status: {file_record.upload_status}. Expected: uploaded",
                data={"file_id": file_id_int, "upload_status": file_record.upload_status},
            )

        # Update status to chunking
        file_record.chunking_status = "chunking"
        file_record.status = "processing"  # Ana status'ü de güncelle
        file_record.chunking_started_at = datetime.now(timezone.utc)
        db_session.commit()

        logging.info(
            f"🔄 Started chunking for file {file_id_int}: {file_record.original_name}"
        )

        # Database'den dosya yolunu al
        file_path = file_record.file_path

        if not os.path.exists(file_path):
            # Dosya local'de yok, S3'ten indirmeyi dene
            logging.warning(
                f"⚠️ File not found locally: {file_path}. Attempting to download from S3 and extract images..."
            )

            s3_bucket = os.environ.get("S3_BACKUP_BUCKET", "llm-graph-builder-backup")
            aws_access_key_id = os.environ.get("AWS_ACCESS_KEY_ID")
            aws_secret_access_key = os.environ.get("AWS_SECRET_ACCESS_KEY")

            if s3_bucket and aws_access_key_id and aws_secret_access_key:
                try:
                    import boto3
                    from botocore.exceptions import ClientError
                    from pathlib import Path

                    # S3 key'ini oluştur (documents/{doc_name}/{filename})
                    normalized_filename = file_record.filename
                    doc_name = Path(normalized_filename).stem
                    s3_key = f"documents/{doc_name}/{normalized_filename}"

                    # S3 client oluştur
                    s3_client = boto3.client(
                        "s3",
                        aws_access_key_id=aws_access_key_id,
                        aws_secret_access_key=aws_secret_access_key,
                    )

                    # S3'te dosya var mı kontrol et
                    try:
                        s3_client.head_object(Bucket=s3_bucket, Key=s3_key)
                        logging.info(f"✅ File found in S3: s3://{s3_bucket}/{s3_key}")

                        # Local dizini oluştur
                        os.makedirs(os.path.dirname(file_path), exist_ok=True)

                        # PDF'i S3'ten indir
                        logging.info(f"📥 Downloading PDF from S3: {s3_key}")
                        s3_client.download_file(s3_bucket, s3_key, file_path)
                        logging.info(f"✅ PDF downloaded successfully to: {file_path}")

                        # Dosya indirildi, şimdi image extraction yapılması için status'ü güncelle
                        # Image extraction yapılması için chunking_status'ü "ready" yap ve upload_status'ü kontrol et
                        file_record.chunking_status = "ready"  # Image extraction yapılacak
                        file_record.upload_status = "uploaded"  # PDF indirildi
                        file_record.status = "uploaded"  # Ana status'ü güncelle
                        db_session.commit()
                        db_session.close()

                        # Image extraction için celery task'i tetikle
                        celery_app.send_task("src.tasks.extract_images_task", args=[file_id_int])
                        # Image extraction işlemi celery worker'da başlatıldı
                        # File record'ı database'den yeniden al (session kapandı)
                        db_session_refresh = db.get_db_session()
                        try:
                            file_record_refresh = (
                                db_session_refresh.query(UploadedFile)
                                .filter_by(id=file_id_int)
                                .first()
                            )
                            if file_record_refresh:
                                asyncio.create_task(
                                    processor._process_single_file_extraction(
                                        file_record_refresh
                                    )
                                )
                        finally:
                            db_session_refresh.close()

                        logging.info(
                            f"🔄 PDF downloaded from S3, image extraction started for file {file_id_int}"
                        )

                        return create_api_response(
                            "Success",
                            message=f"File downloaded from S3. Image extraction started. Chunking will begin after image extraction completes.",
                            data={
                                "file_id": file_id_int,
                                "status": "downloaded_from_s3",
                                "chunking_status": "ready",
                            },
                        )

                    except ClientError as e:
                        if e.response["Error"]["Code"] == "404" or e.response["Error"]["Code"] == "NoSuchKey":
                            # S3'te de yok, dosyanın tekrar upload edilmesi gerekiyor
                            error_message = f"File not found locally and not found in S3. Please re-upload the file: {file_record.original_name}"
                            file_record.chunking_status = "failed"
                            file_record.processing_error = error_message[:500]
                            file_record.reason = f"Chunking failed: {error_message}"
                            db_session.commit()
                            db_session.close()
                            logging.error(f"❌ {error_message}")
                            return create_api_response(
                                "Failed",
                                message=error_message,
                                data={
                                    "file_id": file_id_int,
                                    "requires_reupload": True,
                                },
                            )
                        else:
                            raise

                except Exception as s3_error:
                    error_message = f"Failed to download file from S3: {str(s3_error)}. Please re-upload the file: {file_record.original_name}"
                    file_record.chunking_status = "failed"
                    file_record.processing_error = error_message[:500]
                    file_record.reason = f"Chunking failed: {error_message}"
                    db_session.commit()
                    db_session.close()
                    logging.error(f"❌ {error_message}")
                    return create_api_response(
                        "Failed",
                        message=error_message,
                        data={
                            "file_id": file_id_int,
                            "requires_reupload": True,
                        },
                    )
            else:
                # S3 credentials yok
                error_message = f"File not found locally and S3 credentials not configured. Please re-upload the file: {file_record.original_name}"
                file_record.chunking_status = "failed"
                file_record.processing_error = error_message[:500]
                file_record.reason = f"Chunking failed: {error_message}"
                db_session.commit()
                db_session.close()
                logging.error(f"❌ {error_message}")
                return create_api_response(
                    "Failed",
                    message=error_message,
                    data={
                        "file_id": file_id_int,
                        "requires_reupload": True,
                    },
                )

        # Chunking işlemini celery task'e yönlendir
        celery_app.send_task("src.tasks.chunk_file_task", args=[file_id_int])

        db_session.close()
        return create_api_response(
            "Success",
            message="Chunking started",
            data={"file_id": file_id_int, "chunking_status": "chunking"},
        )
    except Exception as e:
        error_message = str(e)
        logging.error(
            f"❌ Failed to start chunking for file {file_id}: {error_message}"
        )
        return create_api_response(
            "Failed", message="Failed to start chunking", error=error_message
        )


@app.get("/api/v2/files/{file_id}/status")
async def get_file_status(file_id: int):
    """Get status of a single V2 file (for polling)"""
    try:
        db = get_file_queue_db()
        db_session = db.get_db_session()

        file_record = db_session.query(UploadedFile).filter_by(id=file_id).first()
        if not file_record:
            return create_api_response("Failed", message="File not found")

        return create_api_response(
            "Success",
            data={
                "id": file_record.id,
                "original_name": file_record.original_name,
                "upload_status": file_record.upload_status,
                "chunking_status": file_record.chunking_status,
                "graph_status": file_record.graph_status,
                "embedding_status": file_record.embedding_status,
                "file_size": file_record.file_size,
                "markdown_path": file_record.markdown_path,
            },
        )
    except Exception as e:
        error_message = str(e)
        logging.error(f"❌ Failed to get file status: {error_message}")
        return create_api_response(
            "Failed", message="Failed to get file status", error=error_message
        )
    finally:
        db_session.close()


@app.post("/api/v2/files/endorsements/graph-create")
async def start_endorsement_graph_creation(
    file_id: str = Form("all"),
    model: str = Form("openai_gpt_4o_mini"),
    generate_embedding: bool = Form(False),
):
    """Start graph creation process for endorsement files (ENDORSEMENT, RENEWAL, CANCELLATION)

    If file_id is "all", processes all files with graph_status="pending_endorsement"
    If file_id is provided, processes that specific file
    """
    db_session = None
    try:
        # Debug: Log received parameters
        logging.info(
            f"📥 Endorsement graph creation request received - file_id: {repr(file_id)}, type: {type(file_id)}, model: {model}"
        )

        # Get Neo4j credentials from environment
        uri = os.environ.get("NEO4J_URI")
        userName = os.environ.get("NEO4J_USERNAME")
        password = os.environ.get("NEO4J_PASSWORD")
        database = os.environ.get("NEO4J_DATABASE", "neo4j")

        if not all([uri, userName, password]):
            return create_api_response(
                "Failed", message="Neo4j credentials not configured in backend .env"
            )

        db = get_file_queue_db()
        db_session = db.get_db_session()

        # Normalize file_id: None, boş string veya "all" ise "all" olarak kabul et
        original_file_id = file_id
        if file_id is None:
            file_id = "all"
            logging.info(f"🔄 file_id was None, normalized to 'all'")
        elif isinstance(file_id, str):
            file_id = file_id.strip().lower()
            if file_id == "":
                file_id = "all"
                logging.info(f"🔄 file_id was empty string, normalized to 'all'")
            else:
                logging.info(
                    f"🔄 file_id normalized: '{original_file_id}' -> '{file_id}'"
                )
        else:
            # file_id is not None and not a string, convert to string
            file_id = str(file_id).strip().lower()
            logging.info(f"🔄 file_id converted to string: '{file_id}'")

        logging.info(f"✅ Final file_id value: '{file_id}' (type: {type(file_id)})")

        # Handle "all" parameter - explicit check with multiple variations
        # file_id zaten normalize edilmiş, direkt kontrol et
        if file_id == "all":
            logging.info(
                f"✅ Processing 'all' endorsement files (normalized from: {repr(original_file_id)})"
            )
            # Get all files with pending_endorsement status
            files_ready_for_graph = (
                db_session.query(UploadedFile)
                .filter(UploadedFile.upload_status == "uploaded")
                .filter(UploadedFile.chunking_status == "chunked")
                .filter(UploadedFile.graph_status == "pending_endorsement")
                .order_by(UploadedFile.created_at.asc())
                .all()
            )

            if not files_ready_for_graph:
                db_session.close()
                return create_api_response(
                    "Success",
                    message="No endorsement files ready for graph creation",
                    data={"processed_count": 0},
                )

            # Process all endorsement files
            processed_count = 0
            for file_record in files_ready_for_graph:
                try:
                    # Update status to processing
                    file_record.graph_status = "processing"
                    file_record.graph_started_at = datetime.now(timezone.utc)
                    file_record.model_used = model
                    file_record.generate_embedding = (
                        "true" if generate_embedding else "false"
                    )
                    db_session.commit()

                    # Start graph creation via celery task
                    celery_app.send_task("src.tasks.create_graph_task", args=[file_record.id])
                    processed_count += 1
                    logging.info(
                        f"✅ Endorsement graph creation started for: {file_record.original_name} (ID: {file_record.id})"
                    )
                except Exception as file_error:
                    logging.error(
                        f"❌ Failed to start graph creation for endorsement file {file_record.id}: {str(file_error)}"
                    )
                    # Mark as failed
                    file_record.graph_status = "failed"
                    file_record.processing_error = str(file_error)[:500]
                    db_session.commit()

            db_session.close()
            return create_api_response(
                "Success",
                message=f"Graph creation started for {processed_count} endorsement file(s)",
                data={"processed_count": processed_count},
            )

        # Handle single file
        # file_id "all" değilse, sayısal bir ID olmalı
        # Eğer buraya geldiysek, file_id "all" değil demektir
        logging.warning(
            f"⚠️ file_id is not 'all', attempting to parse as integer: {repr(file_id)}"
        )
        try:
            file_id_int = int(file_id)
        except (ValueError, TypeError) as e:
            db_session.close()
            error_msg = f"Invalid file_id parameter: '{file_id}' is not a valid file ID (expected integer or 'all'). Error: {str(e)}"
            logging.error(f"❌ {error_msg}")
            return create_api_response(
                "Failed",
                message=error_msg,
            )

        file_record = (
            db_session.query(UploadedFile)
            .filter(UploadedFile.id == file_id_int)
            .first()
        )

        if not file_record:
            db_session.close()
            return create_api_response(
                "Failed", message=f"File not found: {file_id_int}"
            )

        # Check if file is an endorsement
        if file_record.graph_status != "pending_endorsement":
            db_session.close()
            return create_api_response(
                "Failed",
                message=f"File {file_id_int} is not an endorsement file (status: {file_record.graph_status})",
            )

        # Check if chunking is completed
        if file_record.chunking_status != "chunked":
            db_session.close()
            return create_api_response(
                "Failed",
                message=f"File {file_id_int} chunking not completed (status: {file_record.chunking_status})",
            )

        # Update status to processing
        file_record.graph_status = "processing"
        file_record.graph_started_at = datetime.now(timezone.utc)
        file_record.model_used = model
        file_record.generate_embedding = "true" if generate_embedding else "false"
        db_session.commit()

        logging.info(
            f"🎨 Starting endorsement graph creation for file {file_id_int}: {file_record.original_name}, Model: {model}"
        )

        # Graph creation işlemini celery task'e yönlendir
        celery_app.send_task("src.tasks.create_graph_task", args=[file_id_int])

        db_session.close()
        return create_api_response(
            "Success",
            message="Endorsement graph creation started",
            data={"file_id": file_id_int, "graph_status": "processing"},
        )
    except Exception as e:
        import traceback

        error_message = str(e)
        error_traceback = traceback.format_exc()
        logging.error(f"❌ Failed to start endorsement graph creation: {error_message}")
        logging.error(f"❌ Traceback: {error_traceback}")
        return create_api_response(
            "Failed",
            message="Failed to start endorsement graph creation",
            error=error_message,
        )
    finally:
        if db_session:
            db_session.close()


@app.post("/api/v2/files/{file_id}/graph-create")
async def start_graph_creation(
    file_id: str,
    model: str = Form("openai_gpt_4o_mini"),
    generate_embedding: bool = Form(False),
):
    """Start graph creation process for a file or all files

    If file_id is "all", processes all files with chunking_status="chunked" and graph_status="pending"
    """
    db_session = None  # Initialize outside try block
    try:
        # Get Neo4j credentials from environment
        uri = os.environ.get("NEO4J_URI")
        userName = os.environ.get("NEO4J_USERNAME")
        password = os.environ.get("NEO4J_PASSWORD")
        database = os.environ.get("NEO4J_DATABASE", "neo4j")

        if not all([uri, userName, password]):
            return create_api_response(
                "Failed", message="Neo4j credentials not configured in backend .env"
            )

        db = get_file_queue_db()
        db_session = db.get_db_session()

        # Handle "all" parameter
        if file_id.lower() == "all":
            # Get batch size from environment variable (default: 20)
            batch_size = int(os.environ.get("V2_BATCH_SIZE", "20"))

            # Get all files ready for graph creation (chunking completed, graph pending or processing)
            # Include "processing" status files to handle restart scenarios
            from sqlalchemy import or_

            files_ready_for_graph = (
                db_session.query(UploadedFile)
                .filter(UploadedFile.upload_status == "uploaded")
                .filter(UploadedFile.chunking_status == "chunked")
                .filter(
                    or_(
                        UploadedFile.graph_status == "pending",
                        UploadedFile.graph_status == "processing",
                    )
                )
                .order_by(UploadedFile.created_at.asc())
                .all()
            )

            if not files_ready_for_graph:
                db_session.close()
                return create_api_response(
                    "Success",
                    message="No files ready for graph creation",
                    data={"processed_count": 0},
                )

            # Process files in batches
            processed_count = 0

            # Önce tüm "pending" dosyalarını queue'ya al (status = "queued")
            # Bu sayede frontend'te "Queued for Graph" gösterilebilir ve dosyalar kilitlenir
            all_pending_files = (
                db_session.query(UploadedFile)
                .filter(UploadedFile.upload_status == "uploaded")
                .filter(UploadedFile.chunking_status == "chunked")
                .filter(UploadedFile.graph_status == "pending")
                .all()
            )

            if all_pending_files:
                all_pending_file_ids = [f.id for f in all_pending_files]
                db_session.query(UploadedFile).filter(
                    UploadedFile.id.in_(all_pending_file_ids)
                ).update(
                    {UploadedFile.status: "queued"},  # status = "queued" ile kuyruğa al
                    synchronize_session=False,
                )
                db_session.commit()
                logging.info(
                    f"📋 {len(all_pending_files)} dosya graph creation kuyruğuna alındı (status=queued, graph_status=pending)"
                )

            batch_number = 0
            while True:
                batch_number += 1
                # Get fresh batch from database for each iteration
                # Only get files that are still queued (status = "queued" and graph_status = "pending")
                batch_files = (
                    db_session.query(UploadedFile)
                    .filter(UploadedFile.upload_status == "uploaded")
                    .filter(UploadedFile.status == "queued")  # Queue'daki dosyalar
                    .filter(UploadedFile.chunking_status == "chunked")
                    .filter(UploadedFile.graph_status == "pending")
                    .order_by(UploadedFile.created_at.asc())
                    .limit(batch_size)
                    .all()
                )

                if not batch_files:
                    # No more files in queue
                    break

                # Batch'teki dosyaların ID'lerini al
                batch_file_ids = [f.id for f in batch_files]

                # Sadece bu batch'teki dosyaların status'unu "processing" ve graph_status'unu "processing" olarak güncelle
                db_session.query(UploadedFile).filter(
                    UploadedFile.id.in_(batch_file_ids)
                ).update(
                    {
                        UploadedFile.status: "processing",  # status = "processing" ile işleme al
                        UploadedFile.graph_status: "processing",
                    },
                    synchronize_session=False,
                )
                db_session.commit()

                logging.info(
                    f"📦 Graph creation batch seçildi: {len(batch_files)} dosya işlenmeye başlanıyor (ID'ler: {batch_file_ids})"
                )

                # Batch'teki dosyalar için model, uri gibi bilgileri güncelle
                db_session.query(UploadedFile).filter(
                    UploadedFile.id.in_(batch_file_ids)
                ).update(
                    {
                        UploadedFile.graph_started_at: datetime.now(timezone.utc),
                        UploadedFile.model_used: model,
                        UploadedFile.generate_embedding: str(generate_embedding),
                        UploadedFile.neo4j_uri: uri,
                        UploadedFile.neo4j_database: database,
                    },
                    synchronize_session=False,
                )
                db_session.commit()

                # Queue celery tasks for graph creation
                for file_record in batch_files:
                    celery_app.send_task("src.tasks.create_graph_task", args=[file_record.id])
                processed_count += len(batch_files)

                # Wait before starting next batch (eğer daha fazla dosya varsa)
                remaining_files = (
                    db_session.query(UploadedFile)
                    .filter(UploadedFile.upload_status == "uploaded")
                    .filter(UploadedFile.status == "queued")  # Queue'daki dosyalar
                    .filter(UploadedFile.chunking_status == "chunked")
                    .filter(UploadedFile.graph_status == "pending")
                    .count()
                )

                if remaining_files > 0:
                    logging.info(
                        f"⏳ Waiting before starting next batch ({remaining_files} files remaining)..."
                    )
                    await asyncio.sleep(2)  # 2 saniye bekle, sonraki batch'i al
                else:
                    # No more files, break
                    break

            # Close DB session
            db_session.close()

            return create_api_response(
                "Success",
                message=f"Graph creation started for {processed_count} file(s)",
                data={"processed_count": processed_count},
            )

        # Single file processing
        try:
            file_id_int = int(file_id)
        except ValueError:
            if db_session:
                db_session.close()
            return create_api_response("Failed", message="Invalid file_id parameter")

        logging.info(
            f"🚀 Graph creation request for file {file_id_int}: model={model}, database={database}"
        )

        file_record = db_session.query(UploadedFile).filter_by(id=file_id_int).first()
        if not file_record:
            db_session.close()
            return create_api_response("Failed", message="File not found")

        # Check if chunking is completed
        if file_record.chunking_status != "chunked":
            db_session.close()
            return create_api_response(
                "Failed",
                message=f"File must be chunked first (current status: {file_record.chunking_status})",
            )

        # Check if markdown file exists
        if not file_record.markdown_path or not os.path.exists(
            file_record.markdown_path
        ):
            db_session.close()
            return create_api_response(
                "Failed", message="Markdown file not found. Please run chunking first."
            )

        # If file is already in "processing" status, reset it to "pending" to allow restart
        # This handles the case where graph creation was started but server restarted
        if file_record.graph_status == "processing":
            logging.info(
                f"🔄 File {file_id_int} is already in processing status, resetting to pending to allow restart"
            )
            file_record.graph_status = "pending"
            file_record.graph_started_at = None
            db_session.commit()

        # Update status to processing
        file_record.graph_status = "processing"
        file_record.graph_started_at = datetime.now(timezone.utc)
        file_record.model_used = model
        file_record.generate_embedding = str(generate_embedding)
        file_record.neo4j_uri = uri
        file_record.neo4j_database = database
        db_session.commit()

        logging.info(
            f"✨ Started graph creation for file {file_id_int}: {file_record.original_name}, Model: {model}"
        )

        # Graph creation işlemini celery task'e yönlendir
        celery_app.send_task("src.tasks.create_graph_task", args=[file_id_int])

        db_session.close()
        return create_api_response(
            "Success",
            message="Graph creation started",
            data={"file_id": file_id_int, "graph_status": "processing"},
        )
    except Exception as e:
        error_message = str(e)
        logging.error(
            f"❌ Failed to start graph creation for file {file_id}: {error_message}"
        )
        return create_api_response(
            "Failed", message="Failed to start graph creation", error=error_message
        )
    finally:
        if db_session:
            db_session.close()


@app.post("/api/v2/files/{file_id}/create-embeddings")
async def create_embeddings_for_file(file_id: int):
    """Create embeddings for chunks of a completed file"""
    db_session = None
    try:
        # Get Neo4j credentials from environment
        uri = os.environ.get("NEO4J_URI")
        userName = os.environ.get("NEO4J_USERNAME")
        password = os.environ.get("NEO4J_PASSWORD")
        database = os.environ.get("NEO4J_DATABASE", "neo4j")

        if not all([uri, userName, password]):
            return create_api_response(
                "Failed", message="Neo4j credentials not configured in backend .env"
            )

        logging.info(
            f"📊 Embedding creation request for file {file_id}, database={database}"
        )

        db = get_file_queue_db()
        db_session = db.get_db_session()

        file_record = db_session.query(UploadedFile).filter_by(id=file_id).first()
        if not file_record:
            return create_api_response("Failed", message="File not found")

        # Check if chunking is completed
        if file_record.chunking_status != "chunked":
            return create_api_response(
                "Failed",
                message=f"Chunking must be completed first (current status: {file_record.chunking_status})",
            )

        # Update status to processing
        file_record.embedding_status = "processing"
        file_record.embedding_started_at = datetime.now(timezone.utc)
        file_record.status = "processing"  # Set general status to processing
        db_session.commit()

        logging.info(
            f"🔄 Started embedding creation for file {file_id}: {file_record.original_name}"
        )

        # Embedding işlemini Celery task'e yönlendir
        celery_app.send_task("src.tasks.create_embeddings_task", args=[file_id])

        return create_api_response(
            "Success",
            message="Embedding creation started",
            data={"file_id": file_id, "embedding_status": "processing"},
        )
    except Exception as e:
        error_message = str(e)
        logging.error(
            f"❌ Failed to start embedding creation for file {file_id}: {error_message}"
        )
        return create_api_response(
            "Failed", message="Failed to start embedding creation", error=error_message
        )
    finally:
        if db_session:
            db_session.close()


@app.post("/api/v2/files/{file_id}/reset")
async def reset_file_stage(file_id: str, stage: str = "invalidate"):
    """Reset file to a specific stage (cascading: upload→chunking→graph)

    If file_id is "all", resets all files based on the stage parameter.
    If stage is "invalidate", intelligently resets a SINGLE file based on its stuck/failed status.
    """
    try:
        if stage not in ["upload", "chunking", "graph", "invalidate"]:
            return create_api_response("Failed", message="Invalid stage parameter")

        db = get_file_queue_db()
        db_session = db.get_db_session()

        # Handle "all" parameter
        if file_id.lower() == "all":
            reset_count = 0  # Initialize reset_count for all stages
            if stage == "invalidate":
                 # Invalidate logic for ALL files
                files_to_check = (
                    db_session.query(UploadedFile)
                    .filter(UploadedFile.upload_status == "uploaded")
                    .filter(
                        (UploadedFile.chunking_status.in_(["chunking", "failed"])) |
                        (UploadedFile.graph_status.in_(["processing", "failed"])) |
                        (UploadedFile.embedding_status.in_(["processing", "failed"]))
                    )
                    .all()
                )
                
                for file_record in files_to_check:
                    reset_performed = False
                    
                    # Check Chunking Status
                    if file_record.chunking_status in ["chunking", "failed"]:
                        file_record.chunking_status = "ready"
                        file_record.chunking_started_at = None
                        file_record.chunking_completed_at = None
                        file_record.status = "uploaded"  # Reset status when chunking is reset
                        reset_performed = True
                        logging.info(f"🔄 Invalidate: File {file_record.id} chunking reset to ready, status reset to uploaded")
                        
                    # Check Graph Status (only if chunking is okay or already reset)
                    if file_record.graph_status in ["processing", "failed"]:
                        file_record.graph_status = "pending"
                        file_record.graph_started_at = None
                        file_record.graph_completed_at = None
                        # Ensure chunking is marked as chunked if we are resetting graph
                        if file_record.chunking_status != "ready" and file_record.chunking_status != "chunked":
                             file_record.chunking_status = "chunked"
                        file_record.status = "uploaded"  # Reset status when graph is reset
                        reset_performed = True
                        logging.info(f"🔄 Invalidate: File {file_record.id} graph reset to pending, status reset to uploaded")

                    # Check Embedding Status
                    if file_record.embedding_status in ["processing", "failed"]:
                        file_record.embedding_status = "pending"
                        file_record.embedding_started_at = None
                        file_record.embedding_completed_at = None
                        file_record.status = "uploaded"  # Reset status when embedding is reset
                        reset_performed = True
                        logging.info(f"🔄 Invalidate: File {file_record.id} embedding reset to pending, status reset to uploaded")
                    
                    if reset_performed:
                        file_record.status = "uploaded"
                        reset_count += 1
            
            if stage == "upload":
                # Reset all uploaded files
                files_to_reset = (
                    db_session.query(UploadedFile)
                    .filter(UploadedFile.upload_status == "uploaded")
                    .all()
                )
            elif stage == "chunking":
                # Reset files that need chunking reset (exclude graph_status="completed")
                # Only reset files where graph creation is NOT completed
                files_to_reset = (
                    db_session.query(UploadedFile)
                    .filter(UploadedFile.upload_status == "uploaded")
                    .filter(
                        UploadedFile.chunking_status.in_(
                            ["chunked", "chunking", "ready", "failed"]
                        )
                    )
                    .filter(
                        UploadedFile.graph_status != "completed"  # Don't touch completed files
                    )
                    .all()
                )
            elif stage == "graph":
                # Reset all files that have graph_status in ["processing", "completed", "failed"]
                files_to_reset = (
                    db_session.query(UploadedFile)
                    .filter(UploadedFile.upload_status == "uploaded")
                    .filter(UploadedFile.graph_status.in_(["processing", "completed", "failed"]))
                    .all()
                )
            else:
                files_to_reset = []

            for file_record in files_to_reset:
                # Reset logic with cascading
                if stage == "upload":
                    # Reset everything
                    file_record.upload_status = "uploading"
                    file_record.chunking_status = "pending"
                    file_record.graph_status = "pending"
                    file_record.embedding_status = "pending"  # Reset embedding too
                    file_record.status = "uploaded"  # Reset status
                    file_record.chunking_started_at = None
                    file_record.chunking_completed_at = None
                    file_record.graph_started_at = None
                    file_record.graph_completed_at = None
                    file_record.embedding_started_at = None
                    file_record.embedding_completed_at = None
                    reset_count += 1

                elif stage == "chunking":
                    # Reset chunking and graph (cascade)
                    # If chunking failed, reset to ready state (previous stage)
                    if file_record.chunking_status == "failed":
                        # Chunking failed → go back to ready state
                        file_record.chunking_status = "ready"
                        logging.info(
                            f"🔄 Reset CHUNKING stage for file {file_record.id} ({file_record.original_name}) - failed → ready (ready to retry)"
                        )
                    elif file_record.chunking_status in ["chunked", "chunking", "ready"]:
                        # Image extraction was already completed, set to "ready" for chunking
                        file_record.chunking_status = "ready"
                    else:
                        # Image extraction not completed yet, set to "pending"
                        file_record.chunking_status = "pending"
                    file_record.graph_status = "pending"
                    file_record.embedding_status = "pending"  # Reset embedding too
                    file_record.status = "uploaded"  # Reset status
                    file_record.chunking_started_at = None
                    file_record.chunking_completed_at = None
                    file_record.graph_started_at = None
                    file_record.graph_completed_at = None
                    file_record.embedding_started_at = None
                    file_record.embedding_completed_at = None
                    
                    # Delete markdown file to force re-chunking
                    logging.info(f"🔍 Markdown path for {file_record.original_name}: {file_record.markdown_path}")
                    if file_record.markdown_path:
                        # Convert relative path to absolute (try both backend and celery_worker directories)
                        md_path = file_record.markdown_path
                        if not os.path.isabs(md_path):
                            # Try celery_worker directory first
                            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                            celery_worker_path = os.path.join(project_root, "celery_worker", md_path)
                            backend_path = os.path.join(project_root, "backend", md_path)
                            
                            if os.path.exists(celery_worker_path):
                                md_path = celery_worker_path
                                logging.info(f"🔍 Found markdown in celery_worker: {md_path}")
                            elif os.path.exists(backend_path):
                                md_path = backend_path
                                logging.info(f"🔍 Found markdown in backend: {md_path}")
                            else:
                                logging.warning(f"⚠️ Markdown not found in celery_worker or backend: {file_record.markdown_path}")
                        
                        logging.info(f"🔍 Checking if markdown exists: {os.path.exists(md_path)}")
                        if os.path.exists(md_path):
                            try:
                                os.remove(md_path)
                                logging.info(f"🗑️ Deleted markdown file: {md_path}")
                                file_record.markdown_path = None
                            except Exception as md_error:
                                logging.warning(f"⚠️ Could not delete markdown file for {file_record.original_name}: {str(md_error)}")
                        else:
                            logging.info(f"ℹ️ Markdown file does not exist at: {md_path}")
                            file_record.markdown_path = None  # Clear invalid path
                    else:
                        logging.info(f"ℹ️ No markdown_path set for {file_record.original_name}")
                    
                    # Delete existing chunks and related entities from Neo4j (preserve Document)
                    try:
                        from src.shared.common_fn import create_graph_database_connection
                        neo4j_uri = file_record.neo4j_uri or os.environ.get("NEO4J_URI")
                        neo4j_database = file_record.neo4j_database or os.environ.get("NEO4J_DATABASE", "neo4j")
                        
                        if neo4j_uri:
                            graph_connection = create_graph_database_connection(
                                neo4j_uri,
                                os.environ.get("NEO4J_USERNAME"),
                                os.environ.get("NEO4J_PASSWORD"),
                                neo4j_database,
                            )
                            
                            # Step 1: Delete chunks and their direct relationships
                            delete_chunks_query = """
                            MATCH (d:Document {fileName: $fileName})<-[:PART_OF]-(c:Chunk)
                            DETACH DELETE c
                            RETURN count(c) as deletedChunks
                            """
                            chunk_result = graph_connection.query(delete_chunks_query, {"fileName": file_record.filename})
                            deleted_chunks = chunk_result[0]["deletedChunks"] if chunk_result else 0
                            
                            # Step 2: Delete document-related entities (Policy and connected nodes)
                            # Find all nodes connected to this document through any path (up to 3 hops)
                            delete_entities_query = """
                            MATCH (d:Document {fileName: $fileName})
                            
                            // Find Policy nodes connected via DOCUMENTED_IN
                            OPTIONAL MATCH (p:Policy)-[:DOCUMENTED_IN]->(d)
                            
                            // Find all nodes connected to Policy (1-2 hops)
                            OPTIONAL MATCH (p)-[*1..2]-(relatedNode)
                            WHERE relatedNode IS NOT NULL
                              AND NOT relatedNode:Document 
                              AND NOT relatedNode:Chunk
                              AND NOT relatedNode:`__Community__`
                            
                            // Safety check: only delete if not connected to other documents
                            WITH d, p, collect(DISTINCT relatedNode) as relatedNodes
                            WITH d, p, [node IN relatedNodes WHERE node IS NOT NULL 
                                AND NOT EXISTS {
                                    MATCH (node)-[*1..3]-(otherDoc:Document)
                                    WHERE otherDoc.fileName <> $fileName
                                }] AS safeNodes
                            
                            // Delete safe nodes and policy
                            FOREACH (node IN safeNodes | DETACH DELETE node)
                            WITH d, p, size(safeNodes) as deletedRelated
                            
                            // Delete policy if exists
                            DETACH DELETE p
                            
                            RETURN deletedRelated
                            """
                            entity_result = graph_connection.query(delete_entities_query, {"fileName": file_record.filename})
                            deleted_entities = entity_result[0]["deletedRelated"] if entity_result and entity_result[0]["deletedRelated"] else 0
                            
                            # Step 3: Clean up orphan nodes (nodes with no relationships)
                            cleanup_orphans_query = """
                            MATCH (n)
                            WHERE NOT n:Document 
                              AND NOT n:Chunk 
                              AND NOT n:`__Community__`
                              AND NOT EXISTS { (n)--() }
                            DETACH DELETE n
                            RETURN count(n) as deletedOrphans
                            """
                            orphan_result = graph_connection.query(cleanup_orphans_query)
                            deleted_orphans = orphan_result[0]["deletedOrphans"] if orphan_result else 0
                            
                            total_deleted = deleted_chunks + deleted_entities + deleted_orphans
                            if total_deleted > 0:
                                logging.info(f"🗑️ Neo4j cleanup for {file_record.original_name}: {deleted_chunks} chunks, {deleted_entities} entities, {deleted_orphans} orphans")
                        else:
                            logging.warning("⚠️ Neo4j URI not configured, skipping chunk deletion")
                    except Exception as neo4j_error:
                        logging.warning(f"⚠️ Could not delete chunks from Neo4j for {file_record.original_name}: {str(neo4j_error)}")
                    
                    reset_count += 1

                elif stage == "graph":
                    # Reset only graph
                    # If graph failed, reset to chunked state (previous stage)
                    if file_record.graph_status == "failed":
                        # Graph failed → go back to chunked state
                        file_record.graph_status = "pending"
                        # Ensure chunking_status is "chunked" (previous successful stage)
                        if file_record.chunking_status != "chunked":
                            file_record.chunking_status = "chunked"
                        logging.info(
                            f"🔄 Reset GRAPH stage for file {file_record.id} ({file_record.original_name}) - failed → chunked (ready to retry)"
                        )
                    else:
                        # Normal reset (processing or completed)
                        file_record.graph_status = "pending"
                    file_record.status = "uploaded"  # Reset status
                    file_record.graph_started_at = None
                    file_record.graph_completed_at = None
                    reset_count += 1

            db_session.commit()
            logging.info(f"🔄 Reset {stage.upper()} stage for {reset_count} file(s)")
            return create_api_response(
                "Success",
                message=f"Reset {stage} stage for {reset_count} file(s)",
                data={"reset_count": reset_count, "stage": stage},
            )

        # Single file reset (original logic)
        try:
            file_id_int = int(file_id)
        except ValueError:
            return create_api_response("Failed", message="Invalid file_id parameter")

        file_record = db_session.query(UploadedFile).filter_by(id=file_id_int).first()
        if not file_record:
            return create_api_response("Failed", message="File not found")

        # Reset logic with cascading
        if stage == "invalidate":
             # Invalidate logic for SINGLE file
            reset_performed = False
            
            # Check Chunking Status
            if file_record.chunking_status in ["chunking", "failed"]:
                file_record.chunking_status = "ready"
                file_record.chunking_started_at = None
                file_record.chunking_completed_at = None
                file_record.status = "uploaded"  # Reset status when chunking is reset
                reset_performed = True
                logging.info(f"🔄 Invalidate: File {file_record.id} chunking reset to ready, status reset to uploaded")
                
            # Check Graph Status (only if chunking is okay or already reset)
            if file_record.graph_status in ["processing", "failed"]:
                file_record.graph_status = "pending"
                file_record.graph_started_at = None
                file_record.graph_completed_at = None
                # Ensure chunking is marked as chunked if we are resetting graph
                if file_record.chunking_status != "ready" and file_record.chunking_status != "chunked":
                        file_record.chunking_status = "chunked"
                file_record.status = "uploaded"  # Reset status when graph is reset
                reset_performed = True
                logging.info(f"🔄 Invalidate: File {file_record.id} graph reset to pending, status reset to uploaded")

            # Check Embedding Status
            if file_record.embedding_status in ["processing", "failed"]:
                file_record.embedding_status = "pending"
                file_record.embedding_started_at = None
                file_record.embedding_completed_at = None
                file_record.status = "uploaded"  # Reset status when embedding is reset
                reset_performed = True
                logging.info(f"🔄 Invalidate: File {file_record.id} embedding reset to pending, status reset to uploaded")
            
            if reset_performed:
                file_record.status = "uploaded"
                logging.info(f"🔄 Invalidate: File {file_id_int} reset successfully")
            else:
                logging.info(f"ℹ️ Invalidate: File {file_id_int} did not need resetting")

        elif stage == "upload":
            # Reset everything
            file_record.upload_status = "uploading"
            file_record.chunking_status = "pending"
            file_record.graph_status = "pending"
            file_record.embedding_status = "pending"  # Reset embedding too
            file_record.status = "uploaded"  # Reset status
            file_record.chunking_started_at = None
            file_record.chunking_completed_at = None
            file_record.graph_started_at = None
            file_record.graph_completed_at = None
            file_record.embedding_started_at = None
            file_record.embedding_completed_at = None
            logging.info(
                f"🔄 Reset UPLOAD stage for file {file_id_int} (cascaded to all stages)"
            )

        elif stage == "chunking":
            # Reset chunking and graph (cascade)
            # If chunking failed, reset to ready state (previous stage)
            if file_record.chunking_status == "failed":
                # Chunking failed → go back to ready state
                file_record.chunking_status = "ready"
                logging.info(
                    f"🔄 Reset CHUNKING stage for file {file_id_int} (failed → ready, ready to retry)"
                )
            elif file_record.chunking_status in ["chunked", "chunking", "ready"]:
                # Image extraction was already completed, set to "ready" for chunking
                file_record.chunking_status = "ready"
            else:
                # Image extraction not completed yet, set to "pending"
                file_record.chunking_status = "pending"
            file_record.graph_status = "pending"
            file_record.embedding_status = "pending"  # Reset embedding too
            file_record.status = "uploaded"  # Reset status
            file_record.chunking_started_at = None
            file_record.chunking_completed_at = None
            file_record.graph_started_at = None
            file_record.graph_completed_at = None
            file_record.embedding_started_at = None
            file_record.embedding_completed_at = None
            
            # Delete markdown file to force re-chunking
            logging.info(f"🔍 Markdown path for {file_record.original_name}: {file_record.markdown_path}")
            if file_record.markdown_path:
                # Convert relative path to absolute (try both backend and celery_worker directories)
                md_path = file_record.markdown_path
                if not os.path.isabs(md_path):
                    # Try celery_worker directory first
                    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                    celery_worker_path = os.path.join(project_root, "celery_worker", md_path)
                    backend_path = os.path.join(project_root, "backend", md_path)
                    
                    if os.path.exists(celery_worker_path):
                        md_path = celery_worker_path
                        logging.info(f"🔍 Found markdown in celery_worker: {md_path}")
                    elif os.path.exists(backend_path):
                        md_path = backend_path
                        logging.info(f"🔍 Found markdown in backend: {md_path}")
                    else:
                        logging.warning(f"⚠️ Markdown not found in celery_worker or backend: {file_record.markdown_path}")
                
                logging.info(f"🔍 Checking if markdown exists: {os.path.exists(md_path)}")
                if os.path.exists(md_path):
                    try:
                        os.remove(md_path)
                        logging.info(f"🗑️ Deleted markdown file: {md_path}")
                        file_record.markdown_path = None
                    except Exception as md_error:
                        logging.warning(f"⚠️ Could not delete markdown file: {str(md_error)}")
                else:
                    logging.info(f"ℹ️ Markdown file does not exist at: {md_path}")
                    file_record.markdown_path = None  # Clear invalid path
            else:
                logging.info(f"ℹ️ No markdown_path set for {file_record.original_name}")
            
            
            # Delete existing chunks and related entities from Neo4j (preserve Document)
            try:
                from src.shared.common_fn import create_graph_database_connection
                neo4j_uri = file_record.neo4j_uri or os.environ.get("NEO4J_URI")
                neo4j_database = file_record.neo4j_database or os.environ.get("NEO4J_DATABASE", "neo4j")
                
                if neo4j_uri:
                    graph_connection = create_graph_database_connection(
                        neo4j_uri,
                        os.environ.get("NEO4J_USERNAME"),
                        os.environ.get("NEO4J_PASSWORD"),
                        neo4j_database,
                    )
                    
                    # Step 1: Delete chunks and their direct relationships
                    delete_chunks_query = """
                    MATCH (d:Document {fileName: $fileName})<-[:PART_OF]-(c:Chunk)
                    DETACH DELETE c
                    RETURN count(c) as deletedChunks
                    """
                    chunk_result = graph_connection.query(delete_chunks_query, {"fileName": file_record.filename})
                    deleted_chunks = chunk_result[0]["deletedChunks"] if chunk_result else 0
                    
                    # Step 2: Delete document-related entities (Policy and connected nodes)
                    delete_entities_query = """
                    MATCH (d:Document {fileName: $fileName})
                    
                    // Find Policy nodes connected via DOCUMENTED_IN
                    OPTIONAL MATCH (p:Policy)-[:DOCUMENTED_IN]->(d)
                    
                    // Find all nodes connected to Policy (1-2 hops)
                    OPTIONAL MATCH (p)-[*1..2]-(relatedNode)
                    WHERE relatedNode IS NOT NULL
                      AND NOT relatedNode:Document 
                      AND NOT relatedNode:Chunk
                      AND NOT relatedNode:`__Community__`
                    
                    // Safety check: only delete if not connected to other documents
                    WITH d, p, collect(DISTINCT relatedNode) as relatedNodes
                    WITH d, p, [node IN relatedNodes WHERE node IS NOT NULL 
                        AND NOT EXISTS {
                            MATCH (node)-[*1..3]-(otherDoc:Document)
                            WHERE otherDoc.fileName <> $fileName
                        }] AS safeNodes
                    
                    // Delete safe nodes and policy
                    FOREACH (node IN safeNodes | DETACH DELETE node)
                    WITH d, p, size(safeNodes) as deletedRelated
                    
                    // Delete policy if exists
                    DETACH DELETE p
                    
                    RETURN deletedRelated
                    """
                    entity_result = graph_connection.query(delete_entities_query, {"fileName": file_record.filename})
                    deleted_entities = entity_result[0]["deletedRelated"] if entity_result and entity_result[0]["deletedRelated"] else 0
                    
                    # Step 3: Clean up orphan nodes (nodes with no relationships)
                    cleanup_orphans_query = """
                    MATCH (n)
                    WHERE NOT n:Document 
                      AND NOT n:Chunk 
                      AND NOT n:`__Community__`
                      AND NOT EXISTS { (n)--() }
                    DETACH DELETE n
                    RETURN count(n) as deletedOrphans
                    """
                    orphan_result = graph_connection.query(cleanup_orphans_query)
                    deleted_orphans = orphan_result[0]["deletedOrphans"] if orphan_result else 0
                    
                    total_deleted = deleted_chunks + deleted_entities + deleted_orphans
                    if total_deleted > 0:
                        logging.info(f"🗑️ Neo4j cleanup for {file_record.original_name}: {deleted_chunks} chunks, {deleted_entities} entities, {deleted_orphans} orphans")
                else:
                    logging.warning("⚠️ Neo4j URI not configured, skipping chunk deletion")
            except Exception as neo4j_error:
                logging.warning(f"⚠️ Could not delete chunks from Neo4j: {str(neo4j_error)}")
            
            logging.info(
                f"🔄 Reset CHUNKING stage for file {file_id_int} (cascaded to graph, chunking_status={file_record.chunking_status})"
            )



        elif stage == "graph":
            # Reset only graph
            # If graph failed, reset to chunked state (previous stage)
            if file_record.graph_status == "failed":
                # Graph failed → go back to chunked state
                file_record.graph_status = "pending"
                # Ensure chunking_status is "chunked" (previous successful stage)
                if file_record.chunking_status != "chunked":
                    file_record.chunking_status = "chunked"
                logging.info(
                    f"🔄 Reset GRAPH stage for file {file_id_int} (failed → chunked, ready to retry)"
                )
            else:
                # Normal reset (processing or completed)
                file_record.graph_status = "pending"
            file_record.status = "uploaded"  # Reset status
            file_record.graph_started_at = None
            file_record.graph_completed_at = None
            logging.info(f"🔄 Reset GRAPH stage for file {file_id_int}")

        # Sync status to Neo4j
        try:
            # Get graph credentials
            from src.models.status_sync import sync_queue_db_status_to_neo4j
            from src.shared.common_fn import create_graph_database_connection

            # Neo4j connection details
            neo4j_uri = file_record.neo4j_uri or os.environ.get("NEO4J_URI")
            neo4j_database = file_record.neo4j_database or os.environ.get("NEO4J_DATABASE", "neo4j")

            if neo4j_uri:
                graph_connection = create_graph_database_connection(
                    neo4j_uri,
                    os.environ.get("NEO4J_USERNAME"),
                    os.environ.get("NEO4J_PASSWORD"),
                    neo4j_database,
                )
                sync_queue_db_status_to_neo4j(
                    graph=graph_connection,
                    file_name=file_record.filename,
                    upload_status=file_record.upload_status,
                    chunking_status=file_record.chunking_status,
                    graph_status=file_record.graph_status,
                    embedding_status=file_record.embedding_status,
                    database=neo4j_database,
                )
                logging.info(
                    f"✅ Successfully synced reset status to Neo4j for: {file_record.filename}"
                )
            else:
                logging.warning("⚠️ Neo4j URI not configured, skipping status sync")

        except Exception as sync_error:
            logging.warning(
                f"⚠️ Could not sync reset status to Neo4j: {str(sync_error)}"
            )

        db_session.commit()
        return create_api_response(
            "Success",
            message=f"{stage.capitalize()} stage reset",
            data={
                "file_id": file_id,
                "upload_status": file_record.upload_status,
                "chunking_status": file_record.chunking_status,
                "graph_status": file_record.graph_status,
            },
        )
    except Exception as e:
        error_message = str(e)
        logging.error(f"❌ Failed to reset file {file_id}: {error_message}")
        return create_api_response(
            "Failed", message="Failed to reset file", error=error_message
        )
    finally:
        db_session.close()


@app.post("/api/v2/files/{file_id}/process")
async def queue_file_for_processing(file_id: int, request: ProcessFileRequest):
    """Queue a file for background processing"""
    try:
        db = get_file_queue_db()

        # Get file from database
        file_record = db.get_file_by_id(file_id)
        if not file_record:
            return create_api_response(
                "Failed", message="File not found", error="File ID not in database"
            )

        # Check if file is in correct status
        if file_record.status not in [FileStatus.UPLOADED, FileStatus.ERROR]:
            return create_api_response(
                "Failed",
                message=f"File cannot be processed in current status: {file_record.status}",
                error="Invalid file status",
            )

        # Update file metadata with processing parameters
        db_session = db.get_db_session()
        try:
            file_record.neo4j_uri = request.uri
            file_record.neo4j_database = request.database
            file_record.model_used = request.model
            file_record.generate_embedding = request.generateEmbedding
            file_record.update_status(FileStatus.QUEUED)

            db_session.commit()
            db_session.refresh(file_record)

            logging.info(
                f"✅ File queued for processing: ID={file_id}, Model={request.model}"
            )

            return create_api_response(
                "Success",
                message="File queued for processing successfully",
                data={
                    "file_id": file_id,
                    "status": file_record.status,
                    "model": request.model,
                },
            )

        except Exception as e:
            db_session.rollback()
            raise e
        finally:
            db_session.close()

    except Exception as e:
        error_message = str(e)
        logging.error(f"❌ Failed to queue file {file_id}: {error_message}")
        return create_api_response(
            "Failed", message="Failed to queue file for processing", error=error_message
        )


@app.get("/api/v2/files/status")
async def get_queue_status():
    """Get current queue statistics"""
    try:
        db = get_file_queue_db()
        stats = db.get_queue_stats()

        return create_api_response("Success", data={"queue_stats": stats})

    except Exception as e:
        error_message = str(e)
        logging.error(f"❌ Failed to get queue status: {error_message}")
        return create_api_response(
            "Failed", message="Failed to retrieve queue status", error=error_message
        )


async def delete_file_background_task(
    file_ids: list,
    batch_size: int = 10,
):
    """
    Background task to delete files in batches
    Prevents blocking the API endpoint
    """
    db = get_file_queue_db()
    deleted_count = 0
    failed_count = 0
    
    try:
        from src.shared.common_fn import create_graph_database_connection
        
        # Process files in batches
        for i in range(0, len(file_ids), batch_size):
            batch = file_ids[i:i + batch_size]
            logging.info(f"🗑️ Processing deletion batch {i//batch_size + 1}: {len(batch)} files")
            
            # Neo4j bağlantısını batch seviyesinde kur (tüm batch için tek bağlantı)
            graph_connection = None
            neo4j_uri = os.environ.get("NEO4J_URI")
            neo4j_username = os.environ.get("NEO4J_USERNAME")
            neo4j_password = os.environ.get("NEO4J_PASSWORD")
            neo4j_database = os.environ.get("NEO4J_DATABASE", "neo4j")
            
            # Batch başında Neo4j bağlantısını kur (eğer URI varsa)
            if neo4j_uri:
                try:
                    graph_connection = create_graph_database_connection(
                        uri=neo4j_uri,
                        userName=neo4j_username,
                        password=neo4j_password,
                        database=neo4j_database,
                    )
                    logging.info(f"🔗 Neo4j connection established for batch {i//batch_size + 1}")
                except Exception as conn_error:
                    logging.error(f"❌ Failed to establish Neo4j connection for batch: {str(conn_error)}")
                    graph_connection = None
            
            # Process each file in the batch
            try:
                for file_id_int in batch:
                    try:
                        db_session = db.get_db_session()
                        file_record = db.get_file_by_id(file_id_int)
                        
                        if not file_record:
                            logging.warning(f"⚠️ File not found: ID={file_id_int}")
                            failed_count += 1
                            db_session.close()
                            continue
                        
                        file_path = Path(file_record.file_path)
                        original_name = file_record.original_name
                        filename = file_record.filename

                        # Neo4j'den Document ve ilişkili node'ları sil
                        neo4j_deleted = False
                        try:
                            # Dosya kaydında özel Neo4j URI varsa kullan, yoksa batch bağlantısını kullan
                            file_neo4j_uri = file_record.neo4j_uri or neo4j_uri
                            file_neo4j_database = file_record.neo4j_database or neo4j_database
                            
                            # Eğer dosya farklı URI/database kullanıyorsa, özel bağlantı kur
                            if file_neo4j_uri and (file_neo4j_uri != neo4j_uri or file_neo4j_database != neo4j_database):
                                # Dosya özel Neo4j kullanıyor, özel bağlantı kur
                                file_graph_connection = create_graph_database_connection(
                                    uri=file_neo4j_uri,
                                    userName=neo4j_username,
                                    password=neo4j_password,
                                    database=file_neo4j_database,
                                )
                                use_connection = file_graph_connection
                                use_database = file_neo4j_database
                            elif graph_connection:
                                # Batch bağlantısını kullan
                                use_connection = graph_connection
                                use_database = neo4j_database
                            else:
                                # Neo4j yapılandırılmamış
                                use_connection = None
                                use_database = None

                            if use_connection:

                                # V2 Document deletion query
                                delete_query = """
                                    MATCH (d:Document {fileName: $filename})
                                    
                                    // 1. Document'a bağlı Chunk node'ları topla
                                    OPTIONAL MATCH (d)<-[:PART_OF]-(c:Chunk)
                                    
                                    // 2. Document'a DOCUMENTED_IN ile bağlı Policy node'ları topla (-> yönünde)
                                    OPTIONAL MATCH (d)<-[:DOCUMENTED_IN]-(p:Policy)
                                    
                                    // 3. Document'a DOCUMENTED_IN ile bağlı Endorsement node'ları topla
                                    OPTIONAL MATCH (d)<-[:DOCUMENTED_IN]-(e:Endorsement)
                                    
                                    // 4. Policy'den -> yönünde bağlı tüm node'ları topla
                                    OPTIONAL MATCH (p)-[*1..2]->(relatedNodes)
                                    WHERE relatedNodes:PolicyYear OR relatedNodes:InsuredItem OR 
                                          relatedNodes:PolicyType OR relatedNodes:Customer OR
                                          relatedNodes:Agent OR relatedNodes:InsuranceCompany OR
                                          relatedNodes:Address OR relatedNodes:Phone OR relatedNodes:Email
                                    
                                    // 5. Policy'den bağlı Endorsement node'ları topla (FIRST_ENDORSEMENT, NEXT_ENDORSEMENT)
                                    OPTIONAL MATCH (p)-[:FIRST_ENDORSEMENT|NEXT_ENDORSEMENT*]->(policyEndorsements:Endorsement)
                                    
                                    // 6. Endorsement'lardan bağlı node'ları topla
                                    OPTIONAL MATCH (e)-[*1..2]->(endorsementRelatedNodes)
                                    WHERE endorsementRelatedNodes:Premium OR endorsementRelatedNodes:Coverage OR
                                          endorsementRelatedNodes:Clause OR endorsementRelatedNodes:Payment OR
                                          endorsementRelatedNodes:Address OR endorsementRelatedNodes:Phone OR
                                          endorsementRelatedNodes:Email
                                    
                                    OPTIONAL MATCH (policyEndorsements)-[*1..2]->(policyEndorsementRelatedNodes)
                                    WHERE policyEndorsementRelatedNodes:Premium OR policyEndorsementRelatedNodes:Coverage OR
                                          policyEndorsementRelatedNodes:Clause OR policyEndorsementRelatedNodes:Payment OR
                                          policyEndorsementRelatedNodes:Address OR policyEndorsementRelatedNodes:Phone OR
                                          policyEndorsementRelatedNodes:Email
                                    
                                    // 7. Sadece başka Document'larda kullanılmayan node'ları sil
                                    WITH d, 
                                         COLLECT(DISTINCT c) AS chunks,
                                         COLLECT(DISTINCT p) AS policies,
                                         COLLECT(DISTINCT e) + COLLECT(DISTINCT policyEndorsements) AS allEndorsements,
                                         COLLECT(DISTINCT relatedNodes) AS relatedNodesList,
                                         COLLECT(DISTINCT endorsementRelatedNodes) + COLLECT(DISTINCT policyEndorsementRelatedNodes) AS endorsementRelatedNodesList
                                    
                                    // Güvenli silme: Başka document'larda kullanılmayan Policy'leri kontrol et
                                    WITH d, chunks,
                                         [policy IN policies WHERE policy IS NOT NULL AND NOT EXISTS {
                                             MATCH (d2:Document)
                                             WHERE d2 <> d AND (d2)<-[:DOCUMENTED_IN]-(policy)
                                         }] AS safePolicies,
                                         [endorsement IN allEndorsements WHERE endorsement IS NOT NULL AND NOT EXISTS {
                                             MATCH (d2:Document)
                                             WHERE d2 <> d AND (
                                                 (d2)<-[:DOCUMENTED_IN]-(endorsement) OR
                                                 (d2)<-[:DOCUMENTED_IN]-(:Policy)-[:FIRST_ENDORSEMENT|NEXT_ENDORSEMENT*]->(endorsement)
                                             )
                                         }] AS safeEndorsements,
                                         [node IN relatedNodesList WHERE node IS NOT NULL AND NOT EXISTS {
                                             MATCH (d2:Document)<-[:DOCUMENTED_IN]-(p2:Policy)
                                             WHERE d2 <> d AND (
                                                 (p2)-[*1..2]->(node) OR
                                                 (p2)<-[:HAS_DOC]-(node) OR
                                                 (p2)<-[:DOCUMENTED_IN]-(node)
                                             )
                                         }] AS safeRelatedNodes,
                                         [node IN endorsementRelatedNodesList WHERE node IS NOT NULL AND NOT EXISTS {
                                             MATCH (d2:Document)
                                             WHERE d2 <> d AND (
                                                 (d2)<-[:DOCUMENTED_IN]-(:Endorsement)-[*1..2]->(node) OR
                                                 (d2)<-[:DOCUMENTED_IN]-(:Policy)-[:FIRST_ENDORSEMENT|NEXT_ENDORSEMENT*]->(:Endorsement)-[*1..2]->(node)
                                             )
                                         }] AS safeEndorsementRelatedNodes
                                    
                                    // 8. Silme işlemi
                                    FOREACH (chunk IN chunks | DETACH DELETE chunk)
                                    FOREACH (endorsement IN safeEndorsements | 
                                        FOREACH (relNode IN safeEndorsementRelatedNodes | DETACH DELETE relNode)
                                    )
                                    FOREACH (endorsement IN safeEndorsements | DETACH DELETE endorsement)
                                    FOREACH (policy IN safePolicies | 
                                        FOREACH (relNode IN safeRelatedNodes | DETACH DELETE relNode)
                                    )
                                    FOREACH (policy IN safePolicies | DETACH DELETE policy)
                                    DETACH DELETE d
                                    
                                    RETURN count(d) AS deletedDocuments
                                """

                                session_params = {}
                                if use_database:
                                    session_params["database"] = use_database

                                result = use_connection.query(
                                    delete_query,
                                    {"filename": filename},
                                    session_params=session_params,
                                )
                                
                                # Özel bağlantı kullanıldıysa kapat
                                if use_connection != graph_connection and hasattr(use_connection, 'close'):
                                    try:
                                        use_connection.close()
                                    except:
                                        pass

                                if result and len(result) > 0:
                                    deleted_count_neo4j = result[0]["deletedDocuments"]
                                    logging.info(
                                        f"✅ Deleted {deleted_count_neo4j} Document nodes from Neo4j for: {original_name}"
                                    )
                                    neo4j_deleted = True
                                else:
                                    neo4j_deleted = True  # Not an error if document doesn't exist
                            else:
                                neo4j_deleted = True  # Not an error if Neo4j is not configured

                        except Exception as neo4j_error:
                            logging.error(
                                f"❌ Failed to delete from Neo4j for {original_name}: {str(neo4j_error)}"
                            )
                            neo4j_deleted = False
                            failed_count += 1
                            db_session.close()
                            continue

                        if not neo4j_deleted:
                            failed_count += 1
                            db_session.close()
                            continue

                        # Delete file from filesystem if exists
                        if file_path.exists():
                            file_path.unlink()
                            logging.info(f"🗑️ Deleted file from filesystem: {file_path}")

                        # Delete from database (PostgreSQL or SQLite)
                        success = db.delete_file(file_id_int)
                        if success:
                            logging.info(
                                f"✅ File deleted from queue: ID={file_id_int}, Name={original_name}"
                            )
                            deleted_count += 1
                        else:
                            logging.warning(
                                f"⚠️ Failed to delete file from database: ID={file_id_int}, Name={original_name}"
                            )
                            failed_count += 1
                        
                        db_session.close()
                        
                        # Small delay between files to prevent overwhelming the system
                        await asyncio.sleep(0.1)
                        
                    except Exception as file_error:
                        logging.error(
                            f"❌ Failed to delete file {file_id_int}: {str(file_error)}"
                        )
                        failed_count += 1
                        continue
                
            finally:
                # Batch sonunda Neo4j bağlantısını kapat
                if graph_connection:
                    try:
                        if hasattr(graph_connection, 'close'):
                            graph_connection.close()
                        logging.info(f"🔌 Neo4j connection closed for batch {i//batch_size + 1}")
                    except Exception as close_error:
                        logging.warning(f"⚠️ Error closing Neo4j connection: {str(close_error)}")
            
            # Delay between batches
            if i + batch_size < len(file_ids):
                await asyncio.sleep(0.5)
        
        logging.info(
            f"✅ Background deletion completed: {deleted_count} deleted, {failed_count} failed"
        )
        
    except Exception as e:
        logging.error(f"❌ Background deletion task error: {str(e)}")


@app.delete("/api/v2/files/{file_id}")
async def delete_queued_file(file_id: str, background_tasks: BackgroundTasks):
    """Delete a file or all files from queue, filesystem, and Neo4j database

    If file_id is "all", deletes all files from the database asynchronously in batches
    """
    try:
        db = get_file_queue_db()
        db_session = db.get_db_session()

        # Handle "all" parameter - run as background task
        if file_id.lower() == "all":
            # Get all files
            all_files = db_session.query(UploadedFile).all()

            if not all_files:
                db_session.close()
                return create_api_response(
                    "Success",
                    message="No files to delete",
                    data={"deleted_count": 0},
                )

            # Get file IDs for background task
            file_ids = [f.id for f in all_files]
            total_files = len(file_ids)
            
            db_session.close()
            
            # Start deletion via Celery task
            batch_size = int(os.environ.get("DELETE_BATCH_SIZE", "10"))
            # Process files in batches via Celery
            for i in range(0, len(file_ids), batch_size):
                batch = file_ids[i:i + batch_size]
                celery_app.send_task("src.tasks.delete_files_task", args=[batch])
            
            logging.info(
                f"🗑️ Started background deletion for {total_files} files (batch size: {batch_size})"
            )
            
            return create_api_response(
                "Success",
                message=f"Deletion started in background for {total_files} file(s). Processing in batches of {batch_size}.",
                data={
                    "total_files": total_files,
                    "batch_size": batch_size,
                    "status": "processing",
                },
            )

        # Single file processing - also run as background task for consistency
        try:
            file_id_int = int(file_id)
        except ValueError:
            db_session.close()
            return create_api_response("Failed", message="Invalid file_id parameter")

        # Get file info before deletion
        file_record = db.get_file_by_id(file_id_int)
        if not file_record:
            db_session.close()
            return create_api_response(
                "Failed", message="File not found", error="File ID not in database"
            )

        db_session.close()
        
        # Start deletion via Celery task for single file
        celery_app.send_task("src.tasks.delete_files_task", args=[[file_id_int]])
        
        logging.info(f"🗑️ Started background deletion for file ID: {file_id_int}")
        
        return create_api_response(
            "Success",
            message="File deletion started in background",
            data={"file_id": file_id_int, "status": "processing"},
        )

    except Exception as e:
        error_message = str(e)
        logging.error(f"❌ Failed to delete file {file_id}: {error_message}")
        return create_api_response(
            "Failed", message="Failed to delete file", error=error_message
        )


# ==========================================
# BACKGROUND PROCESSING ENDPOINTS
# ==========================================

# Background processor removed - using celery tasks instead
# from src.background_processor import (
#     get_background_processor,
#     start_processing_loop,
#     process_file_immediately,
# )

# Celery worker runs independently - no background task needed


@app.post("/api/v2/processing/start")
async def start_background_processing():
    """Reset stuck processing files (celery worker runs independently)"""
    try:
        from src.models.file_queue_models import get_file_queue_db, UploadedFile
        from sqlalchemy import or_, and_

        # Önce yarıda kalan işlemleri resetle
        db = get_file_queue_db()
        db_session = db.get_db_session()
        reset_count = 0

        try:
            # Reset kriterleri: Sadece gerçekten takılmış kayıtları resetle
            files_to_reset = (
                db_session.query(UploadedFile)
                .filter(
                    or_(
                        UploadedFile.status == "uploaded",
                        UploadedFile.status == "processing",
                    )
                )
                .filter(
                    or_(
                        or_(
                            UploadedFile.chunking_status == "processing",
                            UploadedFile.chunking_status == "chunking",
                            UploadedFile.chunking_status == "failed",
                        ),
                        and_(
                            UploadedFile.chunking_status == "chunked",
                            or_(
                                UploadedFile.embedding_status == "processing",
                                UploadedFile.embedding_status == "failed",
                            ),
                        ),
                        and_(
                            UploadedFile.chunking_status == "chunked",
                            or_(
                                UploadedFile.graph_status == "processing",
                                UploadedFile.graph_status == "failed",
                            ),
                        ),
                    )
                )
                .all()
            )

            for file in files_to_reset:
                original_chunking = file.chunking_status
                original_graph = file.graph_status
                original_embedding = file.embedding_status
                
                if original_chunking in ["processing", "chunking", "failed"]:
                    file.chunking_status = "ready"
                    file.status = "uploaded"
                elif original_chunking == "chunked" and original_embedding in ["processing", "failed"]:
                    file.embedding_status = "pending"
                elif original_chunking == "chunked" and original_graph in ["processing", "failed"]:
                    file.graph_status = "pending"
                
                reset_count += 1

            if reset_count > 0:
                db_session.commit()
                logging.info(f"🔄 {reset_count} adet yarıda kalan işlem resetlendi")
            else:
                logging.info("ℹ️ Resetlenecek yarıda kalan işlem bulunamadı")

        except Exception as reset_error:
            db_session.rollback()
            logging.error(f"⚠️ Reset işlemi sırasında hata: {reset_error}")
        finally:
            db_session.close()

        return create_api_response(
            "Success",
            message="Processing files reset successfully. Celery worker will process them automatically.",
            data={
                "status": "reset_completed",
                "reset_count": reset_count,
            },
        )

    except Exception as e:
        error_message = str(e)
        logging.error(f"❌ Failed to reset processing: {error_message}")
        return create_api_response(
            "Failed",
            message="Failed to reset processing",
            error=error_message,
        )


@app.post("/api/v2/processing/stop")
async def stop_background_processing():
    """Stop background file processing (deprecated - celery worker runs independently)"""
    # Celery worker runs independently - this endpoint is kept for backward compatibility
    return create_api_response(
        "Success",
        message="Celery worker runs independently and cannot be stopped from API",
        data={"status": "celery_worker_independent"},
    )


@app.post("/api/v2/files/{file_id}/cancel")
async def cancel_file_processing(file_id: int):
    """Cancel processing for a specific V2 file"""
    db_session = None
    try:
        db = get_file_queue_db()
        db_session = db.get_db_session()

        file_record = db_session.query(UploadedFile).filter_by(id=file_id).first()
        if not file_record:
            return create_api_response("Failed", message="File not found")

        # Check if file is actually processing
        if file_record.status != "processing":
            return create_api_response(
                "Failed",
                message=f"File is not in processing state (current status: {file_record.status})",
            )

        logging.info(
            f"🛑 Cancelling processing for file {file_id}: {file_record.original_name}"
        )

        # Reset status based on which stage was being processed
        if file_record.embedding_status == "processing":
            file_record.embedding_status = "pending"
            file_record.reason = "Embedding processing cancelled by user"
        elif file_record.graph_status == "processing":
            file_record.graph_status = "pending"
            file_record.reason = "Graph processing cancelled by user"
        elif file_record.chunking_status == "chunking":
            file_record.chunking_status = "ready"
            file_record.reason = "Chunking cancelled by user"

        # Reset general status
        file_record.status = "uploaded"
        db_session.commit()

        logging.info(f"✅ Cancelled processing for: {file_record.original_name}")

        return create_api_response(
            "Success",
            message=f"Processing cancelled for {file_record.original_name}",
            data={"file_id": file_id, "status": "cancelled"},
        )

    except Exception as e:
        error_message = str(e)
        logging.error(f"❌ Failed to cancel file processing: {error_message}")
        return create_api_response(
            "Failed",
            message="Failed to cancel file processing",
            error=error_message,
        )
    finally:
        if db_session:
            db_session.close()


@app.get("/api/v2/processing/status")
async def get_processing_status():
    """Get processing status (celery worker runs independently)"""
    # Celery worker status can be checked via flower or celery inspect
    return create_api_response(
        "Success",
        message="Celery worker runs independently - check flower dashboard for status",
        data={"status": "celery_worker_independent", "flower_url": "http://localhost:5555"},
    )


# process_gemini_ocr, process_chunking_v2, and process_graph_creation_v2 
# are now in celery_worker/src/processing_utils.py
# These functions are removed from backend to avoid celery_worker dependencies
def process_gemini_ocr(image_list: list, image_source: str = "generated"):
    """
    Gemini 2.0 Flash ile image'ları markdown'a çevirme ve belge tipini tespit etme (sync function for executor)

    Args:
        image_list: Image path'leri veya filename'leri
        image_source: "local" (filename) veya "generated" (full path)

    Returns:
        dict: {
            "metadata": {
                "docType": "MAIN_POLICY" | "ENDORSEMENT" | "RENEWAL" | "CANCELLATION",
                ...
            },
            "markdown": "Markdown content with [PAGE BREAK] separators"
        }
    """
    import json
    
    result = {
        "metadata": {
            "docType": "MAIN_POLICY"  # Default value
        },
        "markdown": ""
    }
    
    if not GEMINI_AVAILABLE:
        logging.warning("❌ google.genai not available")
        return result

    try:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            logging.warning("❌ GEMINI_API_KEY not found")
            return result

        # Create client with new google-genai SDK
        client = genai_sdk.Client(api_key=api_key)
        logging.info("✅ Gemini client initialized successfully")

        from google.genai import types

        sorted_images = sorted(image_list)
        
        # İlk 1-2 resme bakarak belge tipini tespit et (1 sayfa varsa 1'e, 2+ sayfa varsa 2'ye bak)
        doc_type_detected = False
        if len(sorted_images) >= 1:
            try:
                # Kaç sayfaya bakacağımızı belirle (1 sayfa varsa 1'e, 2+ sayfa varsa 2'ye bak)
                pages_to_analyze = min(2, len(sorted_images))
                logging.info(f"🔍 Analyzing first {pages_to_analyze} page(s) to detect document type...")
                
                # İlk 1-2 resmi oku
                images_to_analyze = []
                for i in range(pages_to_analyze):
                    img_ref = sorted_images[i]
                    if image_source == "local":
                        img_path = os.path.join(
                            os.environ.get("OUTPUT_IMAGES_DIR", "output/images"), img_ref
                        )
                    else:
                        img_path = img_ref
                    
                    with open(img_path, "rb") as img_file:
                        images_to_analyze.append(img_file.read())
                    logging.info(f"🔍 Loaded image {i+1}/{pages_to_analyze}: {os.path.basename(img_path)}")
                
                # Belge tipi tespit prompt'u (dinamik - 1 veya 2 sayfa için)
                page_text = "page" if pages_to_analyze == 1 else "first 2 pages"
                doc_type_prompt = f"""Analyze this {page_text} of an insurance document and determine the document type.

IMPORTANT: Return ONLY a valid JSON object with this exact structure (no markdown, no code blocks, no explanations):
{{
    "docType": "MAIN_POLICY" | "ENDORSEMENT" | "RENEWAL" | "CANCELLATION"
}}

Document type definitions:
- MAIN_POLICY: Main insurance policy document (ana poliçe) - Original policy document
- ENDORSEMENT: Endorsement/amendment document (zeyilname) - Document that modifies or adds to an existing policy
- RENEWAL: Policy renewal document (yenileme) - Document for renewing an existing policy
- CANCELLATION: Policy cancellation document (iptal) - Document for canceling a policy

Look for these keywords in Turkish or English:
- "ZEYİLNAME", "ZEYİL", "ENDORSEMENT", "AMENDMENT" → ENDORSEMENT
- "YENİLEME", "RENEWAL", "RENEW" → RENEWAL
- "İPTAL", "CANCELLATION", "CANCEL" → CANCELLATION
- "POLİÇE", "POLICY" (without zeyilname/renewal/cancellation) → MAIN_POLICY

If the document title or header contains "ZEYİLNAME" or "ENDORSEMENT", it is ENDORSEMENT.
If uncertain or cannot determine, default to "MAIN_POLICY".

CRITICAL: Return ONLY the JSON object, no markdown code blocks (```), no explanations, no other text. Just the JSON."""

                # Resimleri Gemini'ye gönder
                parts = [types.Part.from_text(text=doc_type_prompt)]
                for img_bytes in images_to_analyze:
                    parts.append(types.Part.from_bytes(data=img_bytes, mime_type="image/png"))
                
                logging.info(f"🔍 Sending {len(images_to_analyze)} image(s) to Gemini for document type detection...")
                
                doc_type_response = client.models.generate_content(
                    model="models/gemini-2.5-flash-lite",
                    contents=parts,
                )
                
                # 🔍 Gemini'nin raw response'unu logla
                raw_response = doc_type_response.text if doc_type_response.text else ""
                logging.info(f"🔍 Gemini document type detection - Raw response: {raw_response}")
                logging.info(f"🔍 Gemini document type detection - Response length: {len(raw_response)} chars")
                
                if doc_type_response.text:
                    # JSON'u parse et
                    try:
                        # JSON'u temizle (eğer markdown code block içindeyse)
                        response_text = doc_type_response.text.strip()
                        logging.info(f"🔍 Gemini document type detection - After strip: {response_text[:500]}")
                        
                        if response_text.startswith("```"):
                            # Markdown code block'u kaldır
                            lines = response_text.split("\n")
                            response_text = "\n".join(lines[1:-1]) if len(lines) > 2 else response_text
                            logging.info(f"🔍 Gemini document type detection - After removing ```: {response_text[:500]}")
                        elif response_text.startswith("```json"):
                            lines = response_text.split("\n")
                            response_text = "\n".join(lines[1:-1]) if len(lines) > 2 else response_text
                            logging.info(f"🔍 Gemini document type detection - After removing ```json: {response_text[:500]}")
                        
                        doc_type_data = json.loads(response_text)
                        logging.info(f"🔍 Gemini document type detection - Parsed JSON: {doc_type_data}")
                        
                        detected_doc_type = doc_type_data.get("docType", "MAIN_POLICY")
                        logging.info(f"🔍 Gemini document type detection - Extracted docType: {detected_doc_type}")
                        
                        # Geçerli docType kontrolü
                        valid_types = ["MAIN_POLICY", "ENDORSEMENT", "RENEWAL", "CANCELLATION"]
                        if detected_doc_type in valid_types:
                            result["metadata"]["docType"] = detected_doc_type
                            doc_type_detected = True
                            logging.info(f"✅ Document type detected: {detected_doc_type}")
                        else:
                            logging.warning(f"⚠️ Invalid docType detected: {detected_doc_type}, using default MAIN_POLICY")
                            logging.warning(f"⚠️ Valid types are: {valid_types}")
                    except json.JSONDecodeError as e:
                        logging.error(f"❌ Failed to parse document type JSON: {e}")
                        logging.error(f"❌ Raw response (first 500 chars): {doc_type_response.text[:500]}")
                        logging.error(f"❌ Raw response (full): {doc_type_response.text}")
                    except Exception as e:
                        logging.error(f"❌ Error processing document type detection: {e}")
                        logging.error(f"❌ Exception type: {type(e).__name__}")
                        import traceback
                        logging.error(f"❌ Traceback: {traceback.format_exc()}")
                
                if not doc_type_detected:
                    logging.info("ℹ️ Document type detection failed or returned invalid result, using default MAIN_POLICY")
                    
            except Exception as e:
                logging.warning(f"⚠️ Document type detection failed: {e}, using default MAIN_POLICY")
        
        # Tüm sayfaları markdown'a çevir
        markdown_text = ""
        previous_page_context = ""
        
        for idx, img_ref in enumerate(sorted_images, start=1):
            try:
                # Determine if img_ref is path or filename
                if image_source == "local":
                    # img_ref is filename, construct path
                    img_path = os.path.join(
                        os.environ.get("OUTPUT_IMAGES_DIR", "output/images"), img_ref
                    )
                else:
                    # img_ref is full path
                    img_path = img_ref

                # Read image as bytes
                with open(img_path, "rb") as img_file:
                    image_bytes = img_file.read()

                # Prepare context string
                context_str = ""
                if previous_page_context:
                    context_str = f"\n\nCONTEXT FROM PREVIOUS PAGE (Use this to handle split sentences/paragraphs):\n{previous_page_context}\n"

                # Send to Gemini with new SDK
                prompt_text = f"""You are an AI expert in OCR and Semantic Chunking.
This is page {idx} of {len(sorted_images)} of a document.

TASK:
1. Convert this document page to clean markdown.
2. Group semantically related text into chunks wrapped in <CHUNK>...</CHUNK> tags.
3. Do NOT use ```markdown tags```.
4. Extract all text, tables, and structure exactly as shown.

CRITICAL CHUNKING RULES:
1. **HEADERS & CONTENT:** ALWAYS group a header with the content that follows it. NEVER create a chunk containing *only* a header.
   - BAD: <CHUNK># Header</CHUNK> <CHUNK>Content...</CHUNK>
   - GOOD: <CHUNK># Header\nContent...</CHUNK>

2. **TABLES:** ALWAYS group the table title/header with the table itself.
   - BAD: <CHUNK>Table Title</CHUNK> <CHUNK>| Col1 | Col2 |...</CHUNK>
   - GOOD: <CHUNK>Table Title\n| Col1 | Col2 |...</CHUNK>

3. **KEY-VALUE PAIRS:** Group section headers with their key-value pairs.
   - Example: "RİSK BİLGİLERİ" and the details below it (Kullanım Tarzı, Marka, etc.) MUST be in ONE chunk.

4. **SIGNATURES & FOOTERS:** Group all signature blocks, timestamps, and footer information into a SINGLE chunk at the end. Do not split names, dates, or "Asıldır" text into separate chunks.

5. **GENERAL:** Avoid creating very small chunks (1-2 lines) unless they are completely independent. Prefer merging with the preceding or following context.

REPETITIVE CONTENT HANDLING:
{f"- IGNORE headers and footers that are repeated from the first page (e.g., document titles, logos, standard footers).\n- Extract ONLY the unique content of this page.\n- Do NOT extract the main document title if it appears again." if idx > 1 else ""}

CONTEXT HANDLING:
{context_str}
- If the page starts with a continuation of a sentence/paragraph from the previous context, include it in the first <CHUNK> of this page.

Return ONLY the markdown content with <CHUNK> tags, nothing else."""

                response = client.models.generate_content(
                    model="models/gemini-2.5-flash-lite",
                    contents=[
                        types.Part.from_text(text=prompt_text),
                        types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
                    ],
                )

                if response.text:
                    current_page_text = response.text
                    
                    # Son sayfa değilse PAGE BREAK ekle
                    if idx < len(sorted_images):
                        markdown_text += current_page_text + "\n\n[PAGE BREAK]\n\n"
                    else:
                        # Son sayfa - PAGE BREAK ekleme
                        markdown_text += current_page_text

                    # Update context for next page (last 1000 chars)
                    previous_page_context = current_page_text[-1000:] if len(current_page_text) > 1000 else current_page_text

                    source_name = os.path.basename(img_path)
                    logging.info(
                        f"✅ Gemini 2.0 Flash processed page {idx}/{len(sorted_images)}: {source_name} ({len(current_page_text)} chars)"
                    )
                else:
                    logging.warning(
                        f"Gemini returned empty response for {os.path.basename(img_path)}"
                    )
            except Exception as e:
                logging.warning(f"Gemini processing failed for {img_ref}: {e}")

        result["markdown"] = markdown_text

        if markdown_text:
            logging.info(
                f"✅ Gemini 2.0 Flash generated {len(markdown_text)} characters of markdown from {len(image_list)} images"
            )
        else:
            logging.warning("Gemini generated empty markdown")

    except Exception as e:
        logging.error(f"Gemini processing error: {e}")

    return result


# process_chunking_v2 is now in celery_worker/src/processing_utils.py
# This function is removed from backend to avoid celery_worker dependencies
# V2 endpoints now use celery task: chunk_file_task
# async def process_chunking_v2(file_id: int, original_name: str, merged_file_path: str):
#     """
#     V2 Chunking Process: Uses pre-extracted images to create markdown with Gemini OCR + Creates Chunk Nodes
#     Image extraction and S3 upload are already done in upload endpoint
#     """
#     import json

    #db = None
    #db_session = None
    #loop = asyncio.get_event_loop()
    #executor = None

    #try:
        # Thread pool executor'ı oluştur (Gemini OCR için)
        #from concurrent.futures import ThreadPoolExecutor

        #executor = ThreadPoolExecutor(max_workers=1)

        #db = get_file_queue_db()
        #db_session = db.get_db_session()

        #file_record = db_session.query(UploadedFile).filter_by(id=file_id).first()
        #if not file_record:
            #logging.error(f"❌ File record not found for ID: {file_id}")
            #return

        #logging.info(
            #f"📖 Starting V2 chunking (Gemini OCR + Chunking) for: {original_name}"
        #)

        # Normalize filename
        #from src.utf8_utils import normalize_file_name

        #normalized_filename = normalize_file_name(original_name)

        # Output klasör yapısını oluştur (local dosya yolları için)
        #from src.document_sources.s3_upload_utils import (
            #create_document_output_structure,
        #)

        #document_dir, pdf_dir, images_dir = create_document_output_structure(
            #normalized_filename, "output"
        #)

        # Database'den page images ve doc link bilgisini al (upload sırasında kaydedildi)
        #page_images = []
        #doc_link = (
            #file_record.doc_link
            #if hasattr(file_record, "doc_link") and file_record.doc_link
            #else None
        #)

        #if file_record.page_images:
            #try:
                #page_images = json.loads(file_record.page_images)
                #logging.info(
                    #f"📸 Using {len(page_images)} page images from upload step"
                #)
            #except:
                #logging.warning("⚠️ Failed to parse page_images from database")

        #pages = []

        # V2 Chunking: Sadece pre-extracted images ile Gemini OCR
        #try:
            # ✨ Markdown dosyası zaten var mı kontrol et
            #markdown_filename = f"{normalized_filename}.md"
            #markdown_path = os.path.join(document_dir, markdown_filename)

            # ⚠️ Her chunking başlatıldığında markdown'ı yeniden oluştur
            #if os.path.exists(markdown_path):
                #logging.info(
                    #f"� Deleting existing markdown for re-extraction: {markdown_path}"
                #)
                #os.remove(markdown_path)

            # ✅ SADECE PRE-EXTRACTED IMAGES İLE GEMİNİ OCR YAPACAK
            #logging.info(
                #f"📝 Creating markdown using pre-extracted images for: {normalized_filename}"
            #)

            # 1️⃣ Local images_dir'de PNG dosyaları kontrol et
            #local_images = []
            #if os.path.exists(images_dir):
                #local_images = [
                    #os.path.join(images_dir, f)
                    #for f in os.listdir(images_dir)
                    #if f.endswith(".png")
                #]
                #local_images.sort()  # Sayfa sırasını koru

            # 2️⃣ Local'de yoksa, S3'ten download et
            #if not local_images and page_images:
                #logging.info(
                    #f"📥 Local images not found, attempting to download from S3 for: {normalized_filename}"
                #)
                
                #s3_bucket = os.environ.get(
                    #"S3_BACKUP_BUCKET", "llm-graph-builder-backup"
                #)
                #aws_access_key_id = os.environ.get("AWS_ACCESS_KEY_ID")
                #aws_secret_access_key = os.environ.get("AWS_SECRET_ACCESS_KEY")

                #if s3_bucket and aws_access_key_id and aws_secret_access_key:
                    #from src.document_sources.s3_upload_utils import (
                        #download_images_from_s3,
                    #)
                    #from pathlib import Path

                    #doc_name = Path(normalized_filename).stem

                    #def download_images():
                        #return download_images_from_s3(
                            #page_images,  # Database'deki image isimleri
                            #s3_bucket,
                            #doc_name,
                            #images_dir,
                            #aws_access_key_id,
                            #aws_secret_access_key,
                        #)

                    #downloaded_images = await loop.run_in_executor(
                        #executor, download_images
                    #)

                    # Check if all images were downloaded successfully
                    # If some images failed to download (404), we need to extract locally
                    #downloaded_count = len(downloaded_images) if downloaded_images else 0
                    #expected_count = len(page_images)

                    #if downloaded_count == expected_count and downloaded_count > 0:
                        # All images downloaded successfully
                        #local_images = sorted(downloaded_images)
                        #logging.info(
                            #f"✅ Downloaded {len(local_images)} images from S3 for: {normalized_filename}"
                        #)
                    #elif downloaded_count > 0:
                        # Some images downloaded successfully - use what we have
                        #local_images = sorted(downloaded_images)
                        #logging.warning(
                            #f"⚠️ Partially downloaded images from S3 for: {normalized_filename} "
                            #f"({downloaded_count}/{expected_count} downloaded), will use available images and extract missing ones from PDF"
                        #)
                        # Will try to extract missing images from PDF below
                    #else:
                        # No images downloaded - extract locally
                        #logging.warning(
                            #f"⚠️ Failed to download images from S3 for: {normalized_filename} "
                            #f"({downloaded_count}/{expected_count} downloaded), will extract locally from PDF"
                        #)
                        # Fallback: will extract from PDF below
                        #local_images = []
                #else:
                    #logging.warning(
                        #f"⚠️ S3 credentials not configured, cannot download images"
                    #)

            # 3️⃣ S3'te de yoksa veya download başarısız olduysa, PDF'den extract et
            #if not local_images:
                #logging.info(
                    #f"🖼️ No images found locally or in S3, extracting from PDF: {normalized_filename}"
                #)
                
                # PDF dosyasını bul
                #pdf_path = os.path.join(pdf_dir, normalized_filename)
                #if not os.path.exists(pdf_path):
                    # Alternatif olarak merged_file_path'i dene
                    #if os.path.exists(merged_file_path):
                        #pdf_path = merged_file_path
                    #else:
                        #error_msg = f"❌ PDF file not found: {pdf_path} or {merged_file_path}"
                        #logging.error(error_msg)
                        #raise Exception(error_msg)

                #from src.document_sources.local_file import (
                    #generate_page_images_with_pymupdf,
                #)

                #def extract_images():
                    #return generate_page_images_with_pymupdf(pdf_path, images_dir)

                #extracted_images = await loop.run_in_executor(
                    #executor, extract_images
                #)

                #if extracted_images:
                    #local_images = sorted(extracted_images)
                    #logging.info(
                        #f"✅ Extracted {len(local_images)} images from PDF for: {normalized_filename}"
                    #)
                #else:
                    # No images extracted - continue with chunking anyway (text-only processing)
                    #logging.warning(
                        #f"⚠️ No images extracted from PDF for: {normalized_filename}, will continue with text-only processing"
                    #)
                    # Set local_images to empty list - chunking will continue without images
                    #local_images = []

            # Final kontrol - eğer hala image yoksa PDF'den extract etmeyi dene
            #if not local_images:
                #logging.warning(
                    #f"⚠️ No images available from S3 or local, attempting final PDF extraction for: {normalized_filename}"
                #)
                # PDF dosyasını bul
                #pdf_path = os.path.join(pdf_dir, normalized_filename)
                #if not os.path.exists(pdf_path):
                    # Alternatif olarak merged_file_path'i dene
                    #if os.path.exists(merged_file_path):
                        #pdf_path = merged_file_path
                    #else:
                        #error_msg = f"❌ PDF file not found: {pdf_path} or {merged_file_path}"
                        #logging.error(error_msg)
                        #raise Exception(error_msg)

                #from src.document_sources.local_file import (
                    #generate_page_images_with_pymupdf,
                #)

                #def extract_images_final():
                    #return generate_page_images_with_pymupdf(pdf_path, images_dir)

                #extracted_images_final = await loop.run_in_executor(
                    #executor, extract_images_final
                #)

                #if extracted_images_final:
                    #local_images = sorted(extracted_images_final)
                    #logging.info(
                        #f"✅ Final extraction: Extracted {len(local_images)} images from PDF for: {normalized_filename}"
                    #)
                #else:
                    # Son çare: PDF'den text extraction yap (image olmadan)
                    #logging.warning(
                        #f"⚠️ No images available for Gemini OCR: {normalized_filename}, will try text extraction from PDF"
                    #)
                    # PDF'den direkt text extraction yapılabilir, ama şimdilik hata ver
                    #error_msg = f"❌ No images available for processing: {normalized_filename}. Please ensure PDF file exists and is valid."
                    #logging.error(error_msg)
                    #raise Exception(error_msg)

            #logging.info(
                #f"📸 Found {len(local_images)} images for Gemini OCR (local/S3/extracted)"
            #)

            # SADECE GEMİNİ OCR İLE MARKDOWN OLUŞTUR (metadata + markdown)
            #from langchain_core.documents import Document

            #ocr_result = await loop.run_in_executor(
                #executor, lambda: process_gemini_ocr(local_images, "generated")
            #)

            # OCR sonucunu kontrol et
            #if not ocr_result or not isinstance(ocr_result, dict):
                #error_msg = f"❌ Gemini OCR returned invalid result format"
                #logging.error(error_msg)
                #raise Exception(error_msg)

            #markdown_text = ocr_result.get("markdown", "")
            #metadata = ocr_result.get("metadata", {})
            #doc_type = metadata.get("docType", "MAIN_POLICY")

            # Gemini başarısız olduysa hata fırlat
            #if not markdown_text or not markdown_text.strip():
                #error_msg = f"❌ Gemini OCR failed to generate markdown from {len(local_images)} pre-extracted images"
                #logging.error(error_msg)
                #raise Exception(error_msg)

            #pages = [Document(page_content=markdown_text)]
            #logging.info(
                #f"✅ Gemini OCR completed: Generated markdown from {len(local_images)} pre-extracted images, detected docType: {doc_type}"
            #)

            # Markdown dosyasını oluştur ve kaydet
            #markdown_content = ""
            #if pages:
                #for idx, page in enumerate(pages, start=1):
                    #page_text = (
                        #page.page_content
                        #if hasattr(page, "page_content")
                        #else str(page)
                    #)
                    # Son sayfa değilse PAGE BREAK ekle
                    #if idx < len(pages):
                        #markdown_content += f"{page_text}\n\n[PAGE BREAK]\n\n"
                    #else:
                        # Son sayfa - PAGE BREAK ekleme
                        #markdown_content += page_text

                # Markdown dosyasını document klasörüne kaydet
                #markdown_filename = f"{normalized_filename}.md"
                #markdown_path = os.path.join(document_dir, markdown_filename)

                #with open(markdown_path, "w", encoding="utf-8") as md_file:
                    #md_file.write(markdown_content.strip())

                #logging.info(
                    #f"📝 Markdown file created: {markdown_path} ({len(pages)} pages)"
                #)

                # Markdown path'i kaydet
                #file_record.markdown_path = markdown_path

                # ☁️ MARKDOWN DOSYASINI S3'E UPLOAD ET
                #try:
                    #s3_bucket = os.environ.get(
                        #"S3_BACKUP_BUCKET", "llm-graph-builder-backup"
                    #)
                    #aws_access_key_id = os.environ.get("AWS_ACCESS_KEY_ID")
                    #aws_secret_access_key = os.environ.get("AWS_SECRET_ACCESS_KEY")

                    #if s3_bucket and aws_access_key_id and aws_secret_access_key:
                        #logging.info(
                            #f"☁️ Uploading markdown file to S3: {markdown_filename}"
                        #)

                        # S3 upload - markdown dosyası için ayrı executor (organized structure)
                        #with ThreadPoolExecutor(max_workers=1) as md_executor:
                            #from src.document_sources.s3_upload_utils import (
                                #upload_files_to_s3_with_structure,
                            #)
                            #from pathlib import Path

                            #doc_name = Path(normalized_filename).stem
                            #md_s3_prefix = (
                                #f"documents/{doc_name}/md"  # documents/{doc_name}/md/
                            #)

                            #def upload_markdown_to_s3():
                                #urls, failed = upload_files_to_s3_with_structure(
                                    #[markdown_path],  # Sadece markdown dosyası
                                    #s3_bucket,
                                    #md_s3_prefix,
                                    #aws_access_key_id,
                                    #aws_secret_access_key,
                                    #delete_local_after_upload=False,  # Local dosyayı sakla
                                #)
                                #return urls, failed

                            #md_urls, md_failed = await loop.run_in_executor(
                                #md_executor, upload_markdown_to_s3
                            #)

                        #if md_urls:
                            #logging.info(
                                #f"✅ Markdown uploaded to S3: {len(md_urls)} file"
                            #)
                            # Markdown S3 URL'ini database'e kaydet (opsiyonel)
                            #for url in md_urls:
                                #if url.endswith(f"/{markdown_filename}"):
                                    #file_record.markdown_s3_url = url
                                    #logging.info(f"📄 Markdown S3 URL: {url}")
                                    #break
                        #else:
                            #logging.warning(f"⚠️ Failed to upload markdown to S3")
                    #else:
                        #logging.info(
                            #f"ℹ️ S3 credentials not configured, markdown saved locally only"
                        #)

                #except Exception as s3_error:
                    #logging.warning(f"⚠️ S3 upload failed for markdown: {s3_error}")
                    # Continue with processing even if S3 upload fails

                # ✨ CHUNK NODE'LARI OLUŞTUR (tıpkı upload_file gibi)
                #try:
                    #logging.info(f"🔄 Creating chunk nodes for: {normalized_filename}")

                    # Neo4j bağlantısı kur
                    #uri = os.environ.get("NEO4J_URI")
                    #userName = os.environ.get("NEO4J_USERNAME")
                    #password = os.environ.get("NEO4J_PASSWORD")
                    #database = os.environ.get("NEO4J_DATABASE", "neo4j")

                    #if uri and userName and password:
                        #from src.shared.common_fn import (
                            #create_graph_database_connection,
                        #)
                        #from src.graphDB_dataAccess import graphDBdataAccess
                        #from src.entities.source_node import sourceNode

                        #graph = create_graph_database_connection(
                            #uri, userName, password, database
                        #)
                        #graphDb_data_Access = graphDBdataAccess(graph)

                        # 🧹 ÖNCE: Upload öncesi otomatik temizlik yap (duplicate prevention)
                        #logging.info(
                            #f"🧹 Starting pre-chunking cleanup check for: {normalized_filename}"
                        #)
                        #cleanup_result = (
                            #graphDb_data_Access.auto_clean_existing_file_data(
                                #normalized_filename
                            #)
                        #)
                        #if cleanup_result:
                            #logging.info(
                                #f"✅ Pre-chunking cleanup completed successfully"
                            #)
                        #else:
                            #logging.info(f"ℹ️ No cleanup needed or cleanup skipped")

                        # 1️⃣ Source node objesi oluştur (henüz kaydetme - upload_file gibi)
                        #obj_source_node = sourceNode()
                        #obj_source_node.file_name = normalized_filename
                        #obj_source_node.file_type = normalized_filename.split(".")[
                            #-1
                        #].lower()
                        #obj_source_node.file_size = (
                            #file_record.file_size if file_record.file_size else 0
                        #)
                        #obj_source_node.file_source = "local file"
                        #obj_source_node.model = "openai_gpt_4o_mini"
                        #obj_source_node.created_at = datetime.now()
                        #obj_source_node.chunkNodeCount = 0
                        #obj_source_node.chunkRelCount = 0
                        #obj_source_node.entityNodeCount = 0
                        #obj_source_node.entityEntityRelCount = 0
                        #obj_source_node.communityNodeCount = 0
                        #obj_source_node.communityRelCount = 0
                        #obj_source_node.total_chunks = 0
                        #obj_source_node.processed_chunk = 0
                        #obj_source_node.node_count = 0
                        #obj_source_node.relationship_count = 0
                        #obj_source_node.processing_time = 0

                        # Page images ve doc link'i ekle
                        #if doc_link:
                            #obj_source_node.doc_link = doc_link
                        #if page_images:
                            #obj_source_node.page_images = page_images

                        # 2️⃣ Chunk'ları oluştur (Semantic Chunking)
                        #import re
                        #chunk_pattern = re.compile(r'<CHUNK>(.*?)</CHUNK>', re.DOTALL)
                        #raw_chunks = chunk_pattern.findall(markdown_text)
                        
                        #chunks = []
                        #if not raw_chunks:
                            # Fallback: If no <CHUNK> tags found, use simple page-based chunks
                            # Note: Full chunking with CreateChunksofDocument is now handled by celery_worker
                            #logging.warning(f"⚠️ No <CHUNK> tags found in markdown for {normalized_filename}, using page-based chunks")
                            # Use pages directly as chunks (each page is a chunk)
                            #chunks = [{"page_content": page.get("page_content", ""), "metadata": page.get("metadata", {})} for page in pages]
                        #else:
                            # Merge small chunks (< 150 chars) with page tracking
                            #merged_chunks = []
                            #current_chunk_text = ""
                            #current_chunk_page = 1  # Start from page 1
                            
                            # Split markdown by [PAGE BREAK] to track pages
                            #page_sections = markdown_text.split('[PAGE BREAK]')
                            
                            #for page_idx, page_section in enumerate(page_sections, start=1):
                                # Extract chunks from this page section
                                #page_raw_chunks = chunk_pattern.findall(page_section)
                                
                                #for chunk_text in page_raw_chunks:
                                    #chunk_text = chunk_text.strip()
                                    #if not chunk_text:
                                        #continue
                                        
                                    #if not current_chunk_text:
                                        #current_chunk_text = chunk_text
                                        #current_chunk_page = page_idx
                                    #else:
                                        # Check if adding this chunk keeps it under limit or if current is too small
                                        #if len(current_chunk_text) < 150:
                                            # Current is small.
                                            # Check if the INCOMING chunk is big (>150) AND we have a previous chunk
                                            #if len(chunk_text) > 150 and merged_chunks:
                                                # User Rule: Current is small, Next is Big -> Merge Current to Previous
                                                #merged_chunks[-1]['text'] += "\n" + current_chunk_text
                                                # Set incoming (big) as new current
                                                #current_chunk_text = chunk_text
                                                #current_chunk_page = page_idx
                                            #else:
                                                # Standard: Merge incoming into current
                                                #current_chunk_text += "\n" + chunk_text
                                        #else:
                                            # Current chunk is big enough, save it and start new
                                            #merged_chunks.append({
                                                #'text': current_chunk_text,
                                                #'page': current_chunk_page
                                            #})
                                            #current_chunk_text = chunk_text
                                            #current_chunk_page = page_idx
                            
                            # Add the last chunk
                            #if current_chunk_text:
                                #merged_chunks.append({
                                    #'text': current_chunk_text,
                                    #'page': current_chunk_page
                                #})
                                
                            #logging.info(f"🧩 Parsed {len(raw_chunks)} raw chunks, merged into {len(merged_chunks)} semantic chunks (min 150 chars)")
                            
                            # Create Document objects from merged chunks with page metadata
                            #from langchain_core.documents import Document
                            #chunks = []
                            #for idx, chunk_data in enumerate(merged_chunks, 1):
                                #page_num = chunk_data['page']
                                # Generate page_link from page_images if available
                                #page_link = None
                                #if page_images and page_num <= len(page_images):
                                    #page_link = page_images[page_num - 1]  # 0-indexed
                                
                                #chunks.append(
                                    #Document(
                                        #page_content=chunk_data['text'], 
                                        #metadata={
                                            #"chunk_id": idx, 
                                            #"source": normalized_filename,
                                            #"page_number": page_num,
                                            #"page_link": page_link
                                        #}
                                    #)
                                #)

                        #if chunks:
                            # 3️⃣ Chunk node'ları veritabanına kaydet
                            #from src.make_relationships import create_chunks_for_upload

                            #chunkId_chunkDoc_list = await create_chunks_for_upload(
                                #graph=graph,
                                #chunks=chunks,
                                #file_name=normalized_filename,
                                #page_images=page_images if page_images else [],
                                #generate_embedding=False,
                            #)

                            #logging.info(
                                #f"✅ Created {len(chunkId_chunkDoc_list)} chunk nodes in Neo4j"
                            #)

                            # 4️⃣ Source node'a chunk count'ları ekle (upload_file gibi)
                            #obj_source_node.chunkNodeCount = len(chunkId_chunkDoc_list)
                            #obj_source_node.total_chunks = len(chunks)
                            #obj_source_node.processed_chunk = len(chunkId_chunkDoc_list)

                            # 5️⃣ Vector index oluştur/kontrol et
                            #try:
                                #from src.make_relationships import (
                                    #create_chunk_vector_index,
                                #)

                                #create_chunk_vector_index(graph)
                                #logging.info(f"✅ Vector index checked/created")
                            #except Exception as vector_error:
                                #logging.warning(
                                    #f"⚠️ Vector index warning: {vector_error}"
                                #)
                        #else:
                            #logging.warning(
                                #f"⚠️ No chunks created for: {normalized_filename}"
                            #)

                        # 6️⃣ Source node'u veritabanına kaydet (chunk count'larıyla birlikte - TEK SEFERDE)
                        # ⚠️ V2 Chunking: Entity extraction'ı atla (sadece Document ve Chunk node'ları oluştur)
                        # Entity extraction graph-create aşamasında yapılacak
                        #graphDb_data_Access.create_source_node(
                            #obj_source_node,
                            #model="openai_gpt_4o_mini",
                            #skip_entity_extraction=True,  # V2: Entity extraction'ı atla
                        #)
                        #logging.info(
                            #f"✅ Document node created with chunk counts: {obj_source_node.chunkNodeCount} chunks (entity extraction skipped for V2)"
                        #)

                        # 6️⃣.5️⃣ Document node'una metadata'yı kaydet (sadece docType kullanıyoruz)
                        #try:
                            #update_query = """
                            #MATCH (d:Document {fileName: $file_name})
                            #SET d.docType = $doc_type
                            #RETURN d
                            #"""
                            #graph.query(
                                #update_query,
                                #params={
                                    #"file_name": normalized_filename,
                                    #"doc_type": doc_type
                                #}
                            #)
                            #logging.info(
                                #f"✅ Document metadata updated: docType={doc_type} for {normalized_filename}"
                            #)
                        #except Exception as metadata_error:
                            #logging.warning(
                                #f"⚠️ Failed to update document metadata: {metadata_error}"
                            #)

                        # 7️⃣ Chunk'ları Document'e bağla
                        #if chunks:
                            #from src.make_relationships import link_chunks_to_document

                            #linked_count = link_chunks_to_document(
                                #graph, normalized_filename
                            #)
                            #if linked_count > 0:
                                #logging.info(
                                    #f"🔗 Linked {linked_count} chunks to Document"
                                #)
                            #else:
                                #logging.info(f"ℹ️ Chunks already linked to Document")

                        # Graph connection'ı kapat
                        #if (
                            #graph
                            #and hasattr(graph, "_driver")
                            #and not graph._driver._closed
                        #):
                            #graph._driver.close()
                            #logging.info("🔌 Neo4j connection closed")
                    #else:
                        #logging.warning(
                            #"⚠️ Neo4j credentials not configured, skipping chunk node creation"
                        #)

                #except Exception as chunk_node_error:
                    #logging.error(
                        #f"❌ Failed to create chunk nodes: {chunk_node_error}"
                    #)
                    #import traceback

                    #logging.error(f"Traceback: {traceback.format_exc()}")
            #else:
                #logging.warning(
                    #f"⚠️ No pages extracted for markdown creation: {normalized_filename}"
                #)

            # Chunking tamamlandı, status güncelle
            #file_record.chunking_status = "chunked"
            #file_record.chunking_completed_at = datetime.now(timezone.utc)
            #file_record.status = (
                #"uploaded"  # Chunking tamamlandı, status'u "uploaded" olarak güncelle
            #)

            # Metadata zaten upload sırasında kaydedildi, sadece markdown path ekle
            # doc_link ve page_images zaten database'de mevcut

            #db_session.commit()

            # Neo4j'ye sync et (Neo4j bağlantısı varsa)
            #try:
                #logging.info(
                    #f"📤 Attempting Neo4j sync: file_name={normalized_filename}, "
                    #f"upload_status={file_record.upload_status}, "
                    #f"chunking_status={file_record.chunking_status}, "
                    #f"graph_status={file_record.graph_status}, "
                    #f"embedding_status={file_record.embedding_status}"
                #)
                #from src.models.status_sync import sync_queue_db_status_to_neo4j
                #from src.shared.common_fn import create_graph_database_connection

                #graph_connection = create_graph_database_connection(
                    #file_record.neo4j_uri or os.environ.get("NEO4J_URI"),
                    #os.environ.get("NEO4J_USERNAME"),
                    #os.environ.get("NEO4J_PASSWORD"),
                    #file_record.neo4j_database
                    #or os.environ.get("NEO4J_DATABASE", "neo4j"),
                #)
                #sync_queue_db_status_to_neo4j(
                    #graph=graph_connection,
                    #file_name=normalized_filename,
                    #upload_status=file_record.upload_status,
                    #chunking_status=file_record.chunking_status,
                    #graph_status=file_record.graph_status,
                    #embedding_status=file_record.embedding_status,
                    #database=file_record.neo4j_database
                    #or os.environ.get("NEO4J_DATABASE", "neo4j"),
                #)
            #except Exception as sync_error:
                #logging.warning(
                    #f"⚠️ Could not sync chunking status to Neo4j: {str(sync_error)}"
                #)

            #logging.info(f"✅ V2 Chunking completed for: {original_name}")

        #except Exception as chunk_error:
            #error_message = str(chunk_error)
            #logging.error(
                #f"❌ V2 Chunking failed for {normalized_filename}: {error_message}"
            #)
            #import traceback
            #logging.error(f"Traceback: {traceback.format_exc()}")
            
            #file_record.chunking_status = "failed"
            #file_record.processing_error = error_message[:500] if len(error_message) > 500 else error_message
            #file_record.reason = f"Chunking failed: {error_message}"
            # Remove from queue so it doesn't block other files
            #if file_record.status in ("queued", "processing"):
                #file_record.status = "uploaded"
            #db_session.commit()

            # Neo4j'ye failed status sync et
            #try:
                #from src.models.status_sync import sync_queue_db_status_to_neo4j
                #from src.shared.common_fn import create_graph_database_connection

                #graph_connection = create_graph_database_connection(
                    #file_record.neo4j_uri or os.environ.get("NEO4J_URI"),
                    #os.environ.get("NEO4J_USERNAME"),
                    #os.environ.get("NEO4J_PASSWORD"),
                    #file_record.neo4j_database
                    #or os.environ.get("NEO4J_DATABASE", "neo4j"),
                #)
                #sync_queue_db_status_to_neo4j(
                    #graph=graph_connection,
                    #file_name=normalized_filename,
                    #upload_status=file_record.upload_status,
                    #chunking_status="failed",
                    #graph_status=file_record.graph_status,
                    #embedding_status=file_record.embedding_status,
                    #database=file_record.neo4j_database
                    #or os.environ.get("NEO4J_DATABASE", "neo4j"),
                #)
            #except Exception as sync_error:
                #logging.warning(
                    #f"⚠️ Could not sync chunking failure to Neo4j: {str(sync_error)}"
                #)

    #except Exception as e:
        #error_message = str(e)
        #logging.error(f"❌ process_chunking_v2 failed for file {file_id}: {error_message}")
        #import traceback
        #logging.error(f"Traceback: {traceback.format_exc()}")
        
        #if db_session and file_record:
            #file_record.chunking_status = "failed"
            #file_record.processing_error = error_message[:500] if len(error_message) > 500 else error_message
            #file_record.reason = f"Chunking failed: {error_message}"
            # Remove from queue so it doesn't block other files
            #if file_record.status in ("queued", "processing"):
                #file_record.status = "uploaded"
            #db_session.commit()
    #finally:
        #if db_session:
            #db_session.close()
        # Executor'ı kapat
        #if executor:
            #executor.shutdown(wait=False)


# process_graph_creation_v2 is now in celery_worker/src/processing_utils.py
# This function is removed from backend to avoid celery_worker dependencies
# V2 endpoints now use celery task: create_graph_task
# async def process_graph_creation_v2(
    #file_id: int,
    #original_name: str,
    #markdown_path: str,
    #file_path: str,
    #model: str,
    #uri: str,
    #userName: str,
    #password: str,
    #database: str,
    #generate_embedding: bool = False,
#):
    #"""
    #V2 Graph Creation Process: Extract entities and relationships from markdown
    #Uses processing_source_v2 for pages-based extraction (NO chunks)
    #"""
    # Import'ları fonksiyonun başında yap
    #from src.shared.common_fn import create_graph_database_connection
    #from src.models.status_sync import sync_queue_db_status_to_neo4j

    #db = None
    #db_session = None

    #try:
        #db = get_file_queue_db()
        #db_session = db.get_db_session()

        #file_record = db_session.query(UploadedFile).filter_by(id=file_id).first()
        #if not file_record:
            #logging.error(f"❌ File record not found for ID: {file_id}")
            #return

        #logging.info(f"🎨 Starting V2 graph creation for: {original_name}")

        # Normalize filename
        #from src.utf8_utils import normalize_file_name

        #normalized_filename = normalize_file_name(original_name)

        # Markdown dosyasını oku
        #if not os.path.exists(markdown_path):
            #logging.error(f"❌ Markdown file not found: {markdown_path}")
            #file_record.graph_status = "failed"
            #file_record.processing_error = "Markdown file not found"
            #file_record.reason = "Graph creation failed: Markdown file not found"
            #db_session.commit()
            #return

        # Markdown'ı pages olarak yükle
        #from langchain_core.documents import Document

        #with open(markdown_path, "r", encoding="utf-8") as md_file:
            #markdown_content = md_file.read()

        # Retrieve page_images from file_record BEFORE chunk parsing
        #page_images = []
        #if file_record.page_images:
            #try:
                #import json
                #page_images = json.loads(file_record.page_images)
                #logging.info(f"🖼️ Retrieved {len(page_images)} page images from file record")
            #except Exception as e:
                #logging.warning(f"⚠️ Failed to parse page_images from file record: {e}")

        # Page break'lere göre sayfalara bölmek yerine CHUNK'ları parse et
        #import re
        
        # Regex to find content within <CHUNK> tags
        #chunk_pattern = re.compile(r'<CHUNK>(.*?)</CHUNK>', re.DOTALL)
        #raw_chunks = chunk_pattern.findall(markdown_content)
        
        #if not raw_chunks:
            # Fallback to page splitting if no chunks found
            #logging.warning(f"⚠️ No <CHUNK> tags found in markdown for {normalized_filename}, falling back to page splitting")
            #page_texts = markdown_content.split("[PAGE BREAK]")
            #pages = [
                #Document(page_content=text.strip(), metadata={"page": idx, "chunk_id": idx})
                #for idx, text in enumerate(page_texts, 1)
                #if text.strip()
            #]
        #else:
            # Merge small chunks (< 150 chars) with page tracking
            #merged_chunks = []
            #current_chunk_text = ""
            #current_chunk_page = 1  # Start from page 1
            
            # Split markdown by [PAGE BREAK] to track pages
            #page_sections = markdown_content.split('[PAGE BREAK]')
            
            #for page_idx, page_section in enumerate(page_sections, start=1):
                # Extract chunks from this page section
                #page_raw_chunks = chunk_pattern.findall(page_section)
                
                #for chunk_text in page_raw_chunks:
                    #chunk_text = chunk_text.strip()
                    #if not chunk_text:
                        #continue
                        
                    #if not current_chunk_text:
                        #current_chunk_text = chunk_text
                        #current_chunk_page = page_idx
                    #else:
                        # Check if adding this chunk keeps it under limit or if current is too small
                        #if len(current_chunk_text) < 150:
                            # Current is small.
                            # Check if the INCOMING chunk is big (>150) AND we have a previous chunk
                            #if len(chunk_text) > 150 and merged_chunks:
                                # User Rule: Current is small, Next is Big -> Merge Current to Previous
                                #merged_chunks[-1]['text'] += "\n" + current_chunk_text
                                # Set incoming (big) as new current
                                #current_chunk_text = chunk_text
                                #current_chunk_page = page_idx
                            #else:
                                # Standard: Merge incoming into current
                                #current_chunk_text += "\n" + chunk_text
                        #else:
                            # Current chunk is big enough, save it and start new
                            #merged_chunks.append({
                                #'text': current_chunk_text,
                                #'page': current_chunk_page
                            #})
                            #current_chunk_text = chunk_text
                            #current_chunk_page = page_idx
            
            # Add the last chunk
            #if current_chunk_text:
                #merged_chunks.append({
                    #'text': current_chunk_text,
                    #'page': current_chunk_page
                #})
                
            #logging.info(f"🧩 Parsed {len(raw_chunks)} raw chunks, merged into {len(merged_chunks)} semantic chunks (min 150 chars)")
            
            # Create Document objects from merged chunks with page metadata
            #pages = []
            #for idx, chunk_data in enumerate(merged_chunks, 1):
                #page_num = chunk_data['page']
                # Generate page_link from page_images if available
                #page_link = None
                #if page_images and page_num <= len(page_images):
                    #page_link = page_images[page_num - 1]  # 0-indexed
                
                #pages.append(
                    #Document(
                        #page_content=chunk_data['text'], 
                        #metadata={
                            #"chunk_id": idx, 
                            #"source": normalized_filename,
                            #"page_number": page_num,
                            #"page_link": page_link
                        #}
                    #)
                #)

        #logging.info(
            #f"📄 Prepared {len(pages)} chunks for V2 graph extraction"
        #)

        # V2 processing_source_v2 fonksiyonunu kullan (NO chunks)
        #from src.main import processing_source_v2

        # Parametreler
        #allowedNodes = []  # Boş = tüm node'lar
        #allowedRelationship = []  # Boş = tüm relationship'ler
        #additional_instructions = None
        #max_pages = None

        #logging.info(f"🔄 Calling processing_source_v2 for: {normalized_filename}")
        #logging.info(
            #f"✨ V2 Mode: Pages-based extraction (NO chunks, NO embeddings, NO chunk-entity linking)"
        #)

        # V2 Graph extraction - pages-based, chunk'sız
        #latency, response = await processing_source_v2(
            #uri=uri,
            #userName=userName,
            #password=password,
            #database=database,
            #model=model,
            #file_name=normalized_filename,
            #pages=pages,
            #allowedNodes=allowedNodes,
            #allowedRelationship=allowedRelationship,
            #additional_instructions=additional_instructions,
            #max_pages=max_pages,
            #page_images=page_images,  # Pass page_images
        #)

        # Check if processing_source_v2 failed
        #if not response or response.get("status") == "Failed":
            #error_message = (
                #response.get("error", "Unknown error")
                #if response
                #else "No response from processing_source_v2"
            #)
            #logging.error(
                #f"❌ V2 Graph extraction failed for: {normalized_filename} - {error_message}"
            #)
            #file_record.graph_status = "failed"
            #file_record.processing_error = error_message[:500]
            #file_record.reason = f"Graph extraction failed: {error_message}"
            #db_session.commit()
            #return

        # Note: "Already Processing" status check removed
        # processing_source_v2 now continues even if Neo4j status is "Processing"
        # This handles restart scenarios where database was reset but Neo4j wasn't

        #logging.info(f"✅ V2 Graph extraction completed for: {normalized_filename}")
        #logging.info(
            #f"📊 Result: {response.get('nodeCount', 0)} nodes, {response.get('relationshipCount', 0)} relationships"
        #)
        #logging.info(f"⏱️ Latency details: {latency}")

        # Check if Policy node was created in Neo4j
        # If no Policy node exists, mark as failed
        #try:
            #import asyncio
            #from src.main import create_graph_database_connection

            #graph_connection = await asyncio.to_thread(
                #create_graph_database_connection,
                #uri, userName, password, database
            #)

            # Check if Policy or Endorsement node exists for this document
            # Endorsement dosyaları için Endorsement node, ana poliçeler için Policy node kontrol edilmeli
            # NOT: DOCUMENTED_IN hem Policy hem Endorsement için kullanılır (hangi Document'ta dokümante edildiğini gösterir)
            # HAS_ENDORSEMENT ise Policy'den Endorsement'a olan bağlantıdır (Policy node'unun bir Endorsement'a sahip olduğunu gösterir)
            #node_check_query = """
            #MATCH (d:Document {fileName: $file_name})
            #OPTIONAL MATCH (p:Policy)-[:DOCUMENTED_IN]->(d)
            #OPTIONAL MATCH (e:Endorsement)-[:DOCUMENTED_IN]->(d)
            #RETURN count(p) as policy_count, count(e) as endorsement_count, d.docType as doc_type
            #"""

            #node_result = await asyncio.to_thread(
                #graph_connection.query,
                #node_check_query,
                #{"file_name": normalized_filename}
            #)

            #policy_count = node_result[0]["policy_count"] if node_result else 0
            #endorsement_count = (
                #node_result[0]["endorsement_count"] if node_result else 0
            #)
            #doc_type = node_result[0].get("doc_type", "") if node_result else ""

            # Endorsement dosyaları için Endorsement node kontrolü yap
            # Ana poliçeler için Policy node kontrolü yap
            #if doc_type in ["ENDORSEMENT", "RENEWAL", "CANCELLATION"]:
                #if endorsement_count == 0:
                    #error_message = f"Endorsement node was not created for document: {normalized_filename}"
                    #logging.error(f"❌ {error_message}")
                    #file_record.graph_status = "failed"
                    #file_record.processing_error = error_message[:500]
                    #file_record.reason = f"Graph verification failed: {error_message}"
                    #db_session.commit()

                    # Sync failed status to Neo4j - async
                    #try:
                        #import asyncio
                        #from src.models.status_sync import sync_queue_db_status_to_neo4j

                        #await asyncio.to_thread(
                            #sync_queue_db_status_to_neo4j,
                            #graph_connection,
                            #normalized_filename,
                            #file_record.upload_status,
                            #file_record.chunking_status,
                            #"failed",
                            #file_record.embedding_status,
                            #database
                        #)
                    #except Exception as sync_error:
                        #logging.warning(
                            #f"⚠️ Could not sync failed status to Neo4j: {str(sync_error)}"
                        #)

                    #return
                #else:
                    #logging.info(
                        #f"✅ Endorsement node found for document: {normalized_filename} (count: {endorsement_count})"
                    #)
            #elif policy_count == 0:
                #error_message = (
                    #f"Policy node was not created for document: {normalized_filename}"
                #)
                #logging.error(f"❌ {error_message}")
                #file_record.graph_status = "failed"
                #file_record.processing_error = error_message[:500]
                #file_record.reason = f"Graph verification failed: {error_message}"
                #db_session.commit()

                # Sync failed status to Neo4j - async
                #try:
                    #import asyncio
                    #from src.models.status_sync import sync_queue_db_status_to_neo4j

                    #await asyncio.to_thread(
                        #sync_queue_db_status_to_neo4j,
                        #graph_connection,
                        #normalized_filename,
                        #file_record.upload_status,
                        #file_record.chunking_status,
                        #"failed",
                        #file_record.embedding_status,
                        #database
                    #)
                #except Exception as sync_error:
                    #logging.warning(
                        #f"⚠️ Could not sync failed status to Neo4j: {str(sync_error)}"
                    #)

                #return
            #else:
                #logging.info(
                    #f"✅ Policy node verified: {policy_count} Policy node(s) found for {normalized_filename}"
                #)
        #except Exception as policy_check_error:
            #logging.error(
                #f"❌ Error checking Policy node for {normalized_filename}: {str(policy_check_error)}"
            #)
            # Don't fail the entire process if policy check fails
            # But log the error for investigation
            #import traceback

            #logging.error(f"Traceback: {traceback.format_exc()}")

        # Embedding oluştur (eğer isteniyorsa) - async
        #if generate_embedding:
            #try:
                #logging.info(f"🔄 Creating embeddings for: {normalized_filename}")
                #import asyncio
                #from src.graphDB_dataAccess import graphDBdataAccess

                #graph = await asyncio.to_thread(
                    #create_graph_database_connection,
                    #uri, userName, password, database
                #)
                #graphDb_data_Access = graphDBdataAccess(graph)

                # Document için embedding oluştur - async
                #embedding_result = await asyncio.to_thread(
                    #graphDb_data_Access.create_embeddings_for_documents,
                    #[normalized_filename]
                #)
                #if embedding_result and not embedding_result.get("error"):
                    #logging.info(
                        #f"✅ Embeddings created successfully for: {normalized_filename}"
                    #)
                #else:
                    #logging.warning(
                        #f"⚠️ Embedding creation warning: {embedding_result.get('error', 'Unknown error')}"
                    #)
            #except Exception as emb_error:
                #logging.error(f"❌ Embedding creation failed: {emb_error}")
                # Continue without embeddings

        # Response'tan istatistikleri al
        #node_count = response.get("nodeCount", 0) if response else 0
        #relationship_count = response.get("relationshipCount", 0) if response else 0
        #processing_time = response.get("total_processing_time", 0) if response else 0

        # Check if graph creation actually created nodes and relationships
        # If both are 0, the processing might have failed silently
        #if node_count == 0 and relationship_count == 0:
            #logging.warning(
                #f"⚠️ V2 Graph extraction completed but no nodes or relationships created for: {normalized_filename}"
            #)
            # Still mark as completed, but log a warning
            # This might be a valid case for some documents

        # Graph creation tamamlandı, status güncelle
        #file_record.graph_status = "completed"
        #file_record.graph_completed_at = datetime.now(timezone.utc)
        #file_record.status = "completed"  # Graph creation tamamlandı, status'u "completed" olarak güncelle
        #file_record.node_count = node_count
        #file_record.relationship_count = relationship_count
        #file_record.processing_time = processing_time
        #file_record.reason = f"Graph creation completed successfully. Nodes: {node_count}, Relationships: {relationship_count}"

        #db_session.commit()

        # Neo4j'ye sync et - async
        #try:
            #logging.info(
                #f"📤 Attempting Neo4j sync for graph_creation: file_name={original_name}, "
                #f"upload_status={file_record.upload_status}, "
                #f"chunking_status={file_record.chunking_status}, "
                #f"graph_status={file_record.graph_status}, "
                #f"embedding_status={file_record.embedding_status}"
            #)
            #import asyncio
            #graph_connection = await asyncio.to_thread(
                #create_graph_database_connection,
                #uri, userName, password, database
            #)
            #await asyncio.to_thread(
                #sync_queue_db_status_to_neo4j,
                #graph_connection,
                #original_name,
                #file_record.upload_status,
                #file_record.chunking_status,
                #file_record.graph_status,
                #file_record.embedding_status,
                #database
            #)
        #except Exception as sync_error:
            #logging.warning(
                #f"⚠️ Could not sync graph status to Neo4j: {str(sync_error)}"
            #)

        #logging.info(
            #f"✅ V2 Graph creation completed for: {original_name} - Nodes: {file_record.node_count}, Rels: {file_record.relationship_count}"
        #)

    #except Exception as e:
        #logging.error(
            #f"❌ process_graph_creation_v2 failed for file {file_id}: {str(e)}"
        #)
        #import traceback

        #logging.error(f"Traceback: {traceback.format_exc()}")

        #if db_session and file_record:
            #file_record.graph_status = "failed"
            #file_record.processing_error = str(e)[:500]  # İlk 500 karakter
            #file_record.reason = f"Graph creation failed: {str(e)}"
            #db_session.commit()

            # Neo4j'ye failed status sync et - async
            #try:
                #logging.info(
                    #f"📤 Attempting Neo4j sync for graph_creation FAILURE: file_name={original_name}, "
                    #f"upload_status={file_record.upload_status}, "
                    #f"chunking_status={file_record.chunking_status}, "
                    #f"graph_status=failed, "
                    #f"embedding_status={file_record.embedding_status}"
                #)
                #import asyncio
                #from src.models.status_sync import sync_queue_db_status_to_neo4j
                #from src.shared.common_fn import create_graph_database_connection
                
                #graph_connection = await asyncio.to_thread(
                    #create_graph_database_connection,
                    #uri, userName, password, database
                #)
                #await asyncio.to_thread(
                    #sync_queue_db_status_to_neo4j,
                    #graph_connection,
                    #original_name,
                    #file_record.upload_status,
                    #file_record.chunking_status,
                    #"failed",
                    #file_record.embedding_status,
                    #database
                #)
            #except Exception as sync_error:
                #logging.warning(
                    #f"⚠️ Could not sync graph failure to Neo4j: {str(sync_error)}"
                #)
    #finally:
        #if db_session:
            #db_session.close()


#async def process_embedding_creation(
    #file_id: int,
    #original_name: str,
    #uri: str,
    #userName: str,
    #password: str,
    #database: str,
#):
    #"""
    #V2 Embedding Creation Process - Background Task
    #Creates embeddings for chunks of a completed file
    #"""
    #db = None
    #db_session = None

    #try:
        #db = get_file_queue_db()
        #db_session = db.get_db_session()

        #file_record = db_session.query(UploadedFile).filter_by(id=file_id).first()
        #if not file_record:
            #logging.error(f"❌ File record not found for ID: {file_id}")
            #return

        #logging.info(f"📊 Starting embedding creation for: {original_name}")

        #try:
            # Get graph connection
            #graph = create_graph_database_connection(uri, userName, password, database)
            #graphDb_data_Access = graphDBdataAccess(graph)

            # Create embeddings for chunks of this file
            #result = graphDb_data_Access.create_embeddings_for_documents(
                #[original_name]
            #)

            #if result.get("error"):
                #error_msg = f"Embedding creation failed: {result['error']}"
                #logging.error(f"❌ {error_msg}")

                # Update status to failed
                #file_record.embedding_status = "failed"
                #file_record.embedding_completed_at = datetime.now(timezone.utc)
                #file_record.reason = f"Embedding creation failed: {result['error']}"
                #db_session.commit()

                # Neo4j'ye failed status sync et
                #try:
                    #from src.models.status_sync import sync_queue_db_status_to_neo4j

                    #graph_connection = create_graph_database_connection(
                        #uri=uri, userName=userName, password=password, database=database
                    #)
                    #sync_queue_db_status_to_neo4j(
                        #graph=graph_connection,
                        #file_name=original_name,
                        #upload_status=file_record.upload_status,
                        #chunking_status=file_record.chunking_status,
                        #graph_status=file_record.graph_status,
                        #embedding_status="failed",
                        #database=database,
                    #)
                #except Exception as sync_error:
                    #logging.warning(
                        #f"⚠️ Could not sync embedding failure to Neo4j: {str(sync_error)}"
                    #)
                #return

            #total_chunks = result.get("total_chunks_updated", 0)
            #embedding_model = result.get("embedding_model", "Unknown")

            # Update status to completed
            #file_record.embedding_status = "completed"
            #file_record.embedding_completed_at = datetime.now(timezone.utc)
            #file_record.status = "uploaded"  # Reset status from 'processing' to 'uploaded'
            #file_record.reason = f"Embedding creation completed successfully. Updated {total_chunks} chunks."
            #db_session.commit()

            # Neo4j'ye completed status sync et
            #try:
                #logging.info(
                    #f"📤 Attempting Neo4j sync for embedding completion: file_name={original_name}, "
                    #f"upload_status={file_record.upload_status}, "
                    #f"chunking_status={file_record.chunking_status}, "
                    #f"graph_status={file_record.graph_status}, "
                    #f"embedding_status={file_record.embedding_status}"
                #)
                #from src.models.status_sync import sync_queue_db_status_to_neo4j

                #graph_connection = create_graph_database_connection(
                    #uri=uri, userName=userName, password=password, database=database
                #)
                #sync_queue_db_status_to_neo4j(
                    #graph=graph_connection,
                    #file_name=original_name,
                    #upload_status=file_record.upload_status,
                    #chunking_status=file_record.chunking_status,
                    #graph_status=file_record.graph_status,
                    #embedding_status=file_record.embedding_status,
                    #database=database,
                #)
            #except Exception as sync_error:
                #logging.warning(
                    #f"⚠️ Could not sync embedding status to Neo4j: {str(sync_error)}"
                #)

            #logging.info(f"✅ Embedding creation completed for: {original_name}")
            #logging.info(
                #f"📊 Updated {total_chunks} chunks with {embedding_model} embeddings"
            #)

        #except Exception as process_error:
            #error_msg = str(process_error)
            #logging.error(f"❌ Embedding creation error: {error_msg}")

            # Update status to failed
            #file_record.embedding_status = "failed"
            #file_record.embedding_completed_at = datetime.now(timezone.utc)
            #file_record.status = "uploaded"  # Reset status from 'processing' to 'uploaded'
            #file_record.reason = f"Embedding creation failed: {error_msg}"
            #db_session.commit()

            # Neo4j'ye failed status sync et
            #try:
                #logging.info(
                    #f"📤 Attempting Neo4j sync for embedding FAILURE: file_name={original_name}, "
                    #f"upload_status={file_record.upload_status}, "
                    #f"chunking_status={file_record.chunking_status}, "
                    #f"graph_status={file_record.graph_status}, "
                    #f"embedding_status=failed"
                #)
                #from src.models.status_sync import sync_queue_db_status_to_neo4j

                #graph_connection = create_graph_database_connection(
                    #uri=uri, userName=userName, password=password, database=database
                #)
                #sync_queue_db_status_to_neo4j(
                    #graph=graph_connection,
                    #file_name=original_name,
                    #upload_status=file_record.upload_status,
                    #chunking_status=file_record.chunking_status,
                    #graph_status=file_record.graph_status,
                    #embedding_status="failed",
                    #database=database,
                #)
            #except Exception as sync_error:
                #logging.warning(
                    #f"⚠️ Could not sync embedding failure to Neo4j: {str(sync_error)}"
                #)

    #except Exception as e:
        #error_message = str(e)
        #logging.error(
            #f"❌ Background embedding creation failed for file {file_id}: {error_message}"
        #)
    #finally:
        #if db_session:
            #db_session.close()


#@app.post("/api/v2/files/{file_id}/process-immediately")
#async def process_file_now(file_id: int):
    #"""Process a specific file immediately using celery task"""
    #try:
        # Queue celery pipeline task
        #celery_app.send_task("src.tasks.process_file_pipeline", args=[file_id])
        
        #return create_api_response(
            #"Success",
            #message="File processing started via celery worker",
            #data={"file_id": file_id, "status": "queued"},
        #)

    #except Exception as e:
        #error_message = str(e)
        #logging.error(
            #f"❌ Immediate processing failed for file {file_id}: {error_message}"
        #)
        #return create_api_response(
            #"Failed", message="Immediate processing failed", error=error_message
        #)


if __name__ == "__main__":
    # Uvicorn access logger'ını kapat (HTTP request logları)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    uvicorn.run(app)
