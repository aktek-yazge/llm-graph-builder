# -*- coding: utf-8 -*-
"""
Background processing system for file queue
Adapts existing upload_file() logic for async queue processing
V2: Processes uploaded files automatically (chunking -> graph creation)
"""

import asyncio
import logging
import os
import sys
import time
import json
from typing import Optional, Dict, Any
from pathlib import Path
from datetime import datetime, timezone
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from src.models.file_queue_models import get_file_queue_db, UploadedFile
from src.shared.common_fn import  create_graph_database_connection
# Async DB writes via RabbitMQ queue - ensures data persistence even if worker crashes
from src.db_writer import enqueue_db_write

# Gemini API for markdown extraction (New SDK: google-genai 1.48.0+)
try:
    from google import genai as genai_sdk
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    genai_sdk = None


# Retry configuration for Gemini API calls
GEMINI_RETRY_ATTEMPTS = 3
GEMINI_RETRY_WAIT_MIN = 2  # seconds
GEMINI_RETRY_WAIT_MAX = 10  # seconds


class GeminiOCRException(Exception):
    """Exception raised when Gemini OCR fails after all retries."""
    pass


class GeminiRateLimitException(GeminiOCRException):
    """Exception raised when Gemini API rate limit is hit."""
    pass


def _create_gemini_retry_decorator():
    """Create a retry decorator for Gemini API calls with exponential backoff."""
    return retry(
        stop=stop_after_attempt(GEMINI_RETRY_ATTEMPTS),
        wait=wait_exponential(multiplier=1, min=GEMINI_RETRY_WAIT_MIN, max=GEMINI_RETRY_WAIT_MAX),
        retry=retry_if_exception_type((Exception,)),
        before_sleep=lambda retry_state: logging.warning(
            f"🔄 Gemini API call failed, retrying in {retry_state.next_action.sleep} seconds... "
            f"(attempt {retry_state.attempt_number}/{GEMINI_RETRY_ATTEMPTS})"
        ),
        reraise=True
    )


def _call_gemini_with_retry(client, model: str, contents: list, page_info: str = ""):
    """
    Gemini API çağrısı yapar, başarısız olursa 3 kez retry eder.
    
    Args:
        client: Gemini client
        model: Model adı (örn: "models/gemini-2.0-flash")
        contents: Gemini'ye gönderilecek içerik
        page_info: Log mesajları için sayfa bilgisi
    
    Returns:
        Gemini response
    
    Raises:
        GeminiOCRException: 3 deneme sonrası başarısız olursa
        GeminiRateLimitException: Rate limit'e takılırsa
    """
    last_exception = None
    is_rate_limit = False
    
    for attempt in range(1, GEMINI_RETRY_ATTEMPTS + 1):
        try:
            response = client.models.generate_content(
                model=model,
                contents=contents,
            )
            return response
        except Exception as e:
            last_exception = e
            error_str = str(e).lower()
            
            # Check if it's a rate limit error
            if "rate" in error_str or "limit" in error_str or "quota" in error_str or "429" in error_str:
                is_rate_limit = True
            
            if attempt < GEMINI_RETRY_ATTEMPTS:
                wait_time = GEMINI_RETRY_WAIT_MIN * (2 ** (attempt - 1))  # Exponential backoff
                wait_time = min(wait_time, GEMINI_RETRY_WAIT_MAX)
                
                # Rate limit için daha uzun bekle
                if is_rate_limit:
                    wait_time = wait_time * 2
                    logging.warning(
                        f"🚫 Gemini API rate limit hit for {page_info}, waiting {wait_time}s... "
                        f"(attempt {attempt}/{GEMINI_RETRY_ATTEMPTS})"
                    )
                else:
                    logging.warning(
                        f"🔄 Gemini API call failed for {page_info}, retrying in {wait_time}s... "
                        f"(attempt {attempt}/{GEMINI_RETRY_ATTEMPTS}): {str(e)[:100]}"
                    )
                time.sleep(wait_time)
            else:
                logging.error(
                    f"❌ Gemini API call failed after {GEMINI_RETRY_ATTEMPTS} attempts for {page_info}: {e}"
                )
    
    # Raise appropriate exception after all retries failed
    if is_rate_limit:
        raise GeminiRateLimitException(
            f"Gemini API rate limit exceeded after {GEMINI_RETRY_ATTEMPTS} retries for {page_info}: {last_exception}"
        )
    else:
        raise GeminiOCRException(
            f"Gemini OCR failed after {GEMINI_RETRY_ATTEMPTS} retries for {page_info}: {last_exception}"
        )


def process_gemini_ocr(image_list: list, image_source: str = "generated"):
    """
    Gemini 2.0 Flash ile image'ları markdown'a çevirme ve belge tipini tespit etme (sync function for executor)
    Copied from backend/score.py
    
    RETRY MECHANISM: Her Gemini API çağrısı 3 kez denenir (exponential backoff ile).

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
                
                # Call Gemini with retry (3 attempts with exponential backoff)
                doc_type_response = _call_gemini_with_retry(
                    client=client,
                    model="models/gemini-2.0-flash",
                    contents=parts,
                    page_info="document_type_detection"
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

                # Call Gemini with retry (3 attempts with exponential backoff)
                response = _call_gemini_with_retry(
                    client=client,
                    model="models/gemini-2.0-flash",
                    contents=[
                        types.Part.from_text(text=prompt_text),
                        types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
                    ],
                    page_info=f"page_{idx}/{len(sorted_images)}"
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
            except (GeminiOCRException, GeminiRateLimitException) as e:
                # Re-raise Gemini specific exceptions - these should fail the task
                logging.error(f"❌ Gemini OCR failed for {img_ref}: {e}")
                raise
            except Exception as e:
                logging.warning(f"Gemini processing failed for {img_ref}: {e}")
                # For non-Gemini errors, continue with other pages

        result["markdown"] = markdown_text

        if markdown_text:
            logging.info(
                f"✅ Gemini 2.0 Flash generated {len(markdown_text)} characters of markdown from {len(image_list)} images"
            )
        else:
            # Empty markdown is a failure - raise exception
            error_msg = f"Gemini OCR failed: No markdown generated from {len(image_list)} images"
            logging.error(f"❌ {error_msg}")
            raise GeminiOCRException(error_msg)

    except (GeminiOCRException, GeminiRateLimitException):
        # Re-raise Gemini exceptions to caller
        raise
    except Exception as e:
        logging.error(f"Gemini processing error: {e}")
        raise GeminiOCRException(f"Gemini processing failed: {e}")

    # Upload Markdown to S3 if generated
    if result.get("markdown"):
        try:
            s3_bucket = os.environ.get("S3_BACKUP_BUCKET", "llm-graph-builder-backup")
            aws_access_key_id = os.environ.get("AWS_ACCESS_KEY_ID")
            aws_secret_access_key = os.environ.get("AWS_SECRET_ACCESS_KEY")
            
            if s3_bucket and aws_access_key_id and aws_secret_access_key:
                from src.document_sources.s3_upload_utils import upload_single_file_to_s3
                
                # Save markdown to a temporary file for upload
                doc_name = Path(sorted_images[0]).stem.split("_page_")[0] if sorted_images else "document"
                # If image_source is local, try to get doc_name from parent folder
                if image_source == "local" and sorted_images:
                     # output/images/doc_name_page_1.png -> doc_name
                     pass

                # Create a temp file for markdown
                md_filename = f"{doc_name}.md"
                md_path = os.path.join(os.environ.get("OUTPUT_DIR", "output"), doc_name, md_filename)
                
                # Ensure directory exists
                os.makedirs(os.path.dirname(md_path), exist_ok=True)
                
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(result["markdown"])
                
                result["local_path"] = md_path
                
                # Upload to S3: documents/{doc_name}/md/{doc_name}.md
                s3_key = f"documents/{doc_name}/md/{md_filename}"
                
                s3_url = upload_single_file_to_s3(
                    file_path=md_path,
                    bucket_name=s3_bucket,
                    s3_key=s3_key,
                    aws_access_key_id=aws_access_key_id,
                    aws_secret_access_key=aws_secret_access_key,
                    delete_local_after_upload=False # Don't delete yet, might be needed for graph creation
                )
                
                if s3_url:
                    logging.info(f"✅ Markdown uploaded to S3: {s3_url}")
                    result["s3_url"] = s3_url
                    
        except Exception as s3_error:
            logging.error(f"❌ Failed to upload markdown to S3: {s3_error}")

    return result


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
    # Note: os, sys, time, datetime, asyncio, logging are already imported at module level
    from src.graphDB_dataAccess import graphDBdataAccess
    from src.make_relationships import create_chunks_for_upload
    from src.entities.source_node import sourceNode
    from src.shared.llm_graph_builder_exception import LLMGraphBuilderException
    from src.utf8_utils import normalize_file_name
    
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
        
        # Chunk'ları kontrol et - chunking aşamasında oluşturulmuş olmalı
        # Eğer yoksa (eski versiyon veya hata durumu) oluştur
        existing_check_query = """
            MATCH (c:Chunk {fileName: $file_name})
            RETURN count(c) as chunk_count
        """
        existing_result = await asyncio.to_thread(
            lambda: graph.query(existing_check_query, {"file_name": file_name})
        )
        existing_chunk_count = existing_result[0]['chunk_count'] if existing_result else 0
        
        if existing_chunk_count > 0:
            logging.info(f"✅ Found {existing_chunk_count} existing chunks for: {file_name} (created in chunking phase)")
            
            # Re-graph creation: Mevcut entity'leri temizle (Document ve Chunk'ları koru)
            logging.info(f"🧹 Re-graph creation: Clearing existing entities for: {file_name}")
            clear_result = await asyncio.to_thread(
                graphDb_data_Access.clear_entities_for_regraph, file_name
            )
            if clear_result.get("status") == "success":
                logging.info(f"✅ Entities cleared for re-graph: {clear_result.get('total_deleted_entities', 0)} entities deleted")
            elif clear_result.get("status") == "error":
                logging.warning(f"⚠️ Entity clearing failed: {clear_result.get('error')}, continuing anyway...")
        elif pages:
            # Chunk'lar yok, oluştur (geriye dönük uyumluluk için)
            logging.info(f"🧩 No existing chunks found, creating {len(pages)} chunks for V2 file: {file_name}")
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
        elif current_status == "Processing":
            logging.warning(
                f"⚠️ File is in Processing status in Neo4j, but continuing anyway (may be restart scenario): {file_name}"
            )
        else:
            logging.info(f"📋 Current file status: {current_status} for {file_name}")
        
        # Status'u Processing olarak güncelle - async
        obj_source_node = sourceNode()
        obj_source_node.file_name = normalize_file_name(file_name)
        obj_source_node.status = "Processing"
        obj_source_node.model = model
        
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
        
        # Status'u Completed olarak güncelle
        end_time = datetime.now()
        processed_time = end_time - start_time
        
        obj_source_node = sourceNode()
        obj_source_node.file_name = normalize_file_name(file_name)
        obj_source_node.status = "Completed"
        obj_source_node.processing_time = processed_time
        obj_source_node.updated_at = end_time
        
        # Final status update - async
        await asyncio.to_thread(graphDb_data_Access.update_source_node, obj_source_node)
        
        total_processing_time = time.time() - start_time.timestamp()
        uri_latency["total_processing_time"] = f"{total_processing_time:.2f}"
        
        logging.info(f"✅ V2 Processing completed for: {file_name} in {total_processing_time:.2f}s")
        
        # Response
        response = {
            "fileName": file_name,
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

    finally:
        # Cleanup temporary files if enabled
        cleanup_enabled = os.environ.get("CLEANUP_TEMP_FILES", "false").lower() == "true"
        if cleanup_enabled:
            try:
                doc_name = Path(file_name).stem
                output_dir = os.environ.get("OUTPUT_DIR", "output")
                doc_output_dir = os.path.join(output_dir, doc_name)
                
                if os.path.exists(doc_output_dir):
                    import shutil
                    shutil.rmtree(doc_output_dir)
                    logging.info(f"🧹 Cleaned up temporary directory: {doc_output_dir}")
            except Exception as cleanup_error:
                logging.warning(f"⚠️ Failed to cleanup temporary directory: {cleanup_error}")


class FileProcessor:
    """Background task processor for file queue"""

    def __init__(self):
        self.db = get_file_queue_db()
        self.is_processing = False
        self.current_task_id = None
        # Batch size from environment variable (default: 20)
        self.batch_size = int(os.environ.get("V2_BATCH_SIZE", "100"))
        # Wait time before starting processing (to allow all uploads to complete)
        self.upload_wait_time = int(
            os.environ.get("V2_UPLOAD_WAIT_TIME", "10")
        )  # seconds
        self.last_upload_check_time = None





    async def process_v2_chunking_batch(self, files: list):
        """Process chunking for a batch of files (eş zamanlı olarak)"""
        logging.info(
            f"📖 V2: Starting chunking batch for {len(files)} files (eş zamanlı)"
        )

        # Process all files concurrently using asyncio.gather
        tasks = [
            self._process_single_file_chunking(file_record) for file_record in files
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Check for failed files and reset their status
        failed_file_ids = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                failed_file_ids.append(files[i].id)
                logging.error(
                    f"❌ V2: Chunking failed for file {files[i].id} ({files[i].original_name}): {str(result)}"
                )

        # Reset failed files back to "ready" status so they can be retried
        if failed_file_ids:
            db_session = self.db.get_db_session()
            try:
                # Note: datetime and timezone are already imported at module level

                stuck_files = (
                    db_session.query(UploadedFile)
                    .filter(UploadedFile.id.in_(failed_file_ids))
                    .filter(UploadedFile.chunking_status == "chunking")
                    .all()
                )

                for file_record in stuck_files:
                    file_record.chunking_status = "ready"
                    # Status'u "uploaded" olarak bırak, queue'ya alınırken "queued" yapılacak
                    # Ama eğer auto_process=True ise, queue'ya alınması için status="uploaded" yeterli
                    file_record.status = "uploaded"
                    file_record.chunking_started_at = None
                    logging.info(
                        f"🔄 V2: Reset failed chunking file {file_record.id} ({file_record.original_name}) back to ready for retry"
                    )

                if stuck_files:
                    db_session.commit()
                    logging.info(
                        f"✅ V2: Reset {len(stuck_files)} failed chunking file(s) back to ready status"
                    )
            finally:
                db_session.close()

        logging.info(f"✅ V2: Chunking batch completed for {len(files)} files")

    async def _process_single_file_chunking(self, file_record: UploadedFile):
        """Process chunking for a single file (used for concurrent processing)"""
        try:
            db_session = self.db.get_db_session()
            try:
                # Refresh file record
                file_record = (
                    db_session.query(UploadedFile).filter_by(id=file_record.id).first()
                )
                if not file_record:
                    return

                # Check if already chunked
                if file_record.chunking_status == "chunked":
                    logging.info(
                        f"ℹ️ V2: File already chunked: {file_record.original_name}"
                    )
                    return

                # Status zaten batch seçiminde "chunking" olarak güncellenmiş
                # Sadece chunking_started_at güncelle (eğer yoksa)
                if not file_record.chunking_started_at:
                    file_record.chunking_started_at = datetime.now(timezone.utc)
                    db_session.commit()

                logging.info(
                    f"📖 V2: Starting chunking for: {file_record.original_name} (ID: {file_record.id})"
                )

                # Check if markdown already exists
                if file_record.markdown_path and os.path.exists(file_record.markdown_path):
                    # Markdown exists - read it and create Neo4j nodes
                    logging.info(f"📝 Markdown file exists, reading and creating Neo4j nodes: {file_record.markdown_path}")
                    
                    from concurrent.futures import ThreadPoolExecutor
                    from src.utf8_utils import normalize_file_name
                    from langchain_core.documents import Document
                    
                    normalized_filename = normalize_file_name(file_record.original_name)
                    
                    # Markdown dosyasını oku
                    with open(file_record.markdown_path, "r", encoding="utf-8") as md_file:
                        markdown_text = md_file.read()
                    
                    # page_images'ı al
                    page_images = []
                    if file_record.page_images:
                        try:
                            page_images = json.loads(file_record.page_images)
                        except:
                            pass
                    
                    # Neo4j Document ve Chunk node'larını oluştur
                    try:
                        uri = file_record.neo4j_uri or os.environ.get("NEO4J_URI")
                        userName = os.environ.get("NEO4J_USERNAME")
                        password = os.environ.get("NEO4J_PASSWORD")
                        database = file_record.neo4j_database or os.environ.get("NEO4J_DATABASE", "neo4j")
                        
                        if uri and userName and password:
                            graph = create_graph_database_connection(uri, userName, password, database)
                            
                            # Document node oluştur
                            from src.graphDB_dataAccess import graphDBdataAccess
                            graphDb_data_Access = graphDBdataAccess(graph)
                            graphDb_data_Access.create_source_node(normalized_filename, skip_entity_extraction=True)
                            logging.info(f"✅ Document node created/updated: {normalized_filename}")
                            
                            # Chunk'lara ayır ve Neo4j'ye yaz
                            min_chunk_size = 150
                            raw_chunks = []
                            separators = ["## ", "\n\n", "\n"]
                            
                            # <CHUNK> tag'lerini temizle (markdown'da kalmış olabilir)
                            import re
                            chunk_tag_pattern = re.compile(r'<CHUNK>(.*?)</CHUNK>', re.DOTALL)
                            # Önce tag'lerin içindeki içeriği al, tag'ler yoksa orijinal text'i kullan
                            chunk_matches = chunk_tag_pattern.findall(markdown_text)
                            if chunk_matches:
                                # Tag'li markdown - tag'lerin içini al
                                current_text = "\n\n".join(match.strip() for match in chunk_matches if match.strip())
                                logging.info(f"🧹 Cleaned {len(chunk_matches)} <CHUNK> tags from markdown")
                            else:
                                # Tag'siz markdown - orijinal text'i kullan
                                current_text = markdown_text
                            
                            for sep in separators:
                                if sep in current_text:
                                    parts = current_text.split(sep)
                                    for i, part in enumerate(parts):
                                        if part.strip():
                                            if i > 0 and sep == "## ":
                                                raw_chunks.append({"text": sep + part.strip(), "page": 1})
                                            else:
                                                raw_chunks.append({"text": part.strip(), "page": 1})
                                    break
                            else:
                                if current_text.strip():
                                    raw_chunks.append({"text": current_text.strip(), "page": 1})
                            
                            # Küçük chunk'ları birleştir
                            merged_chunks = []
                            current_chunk_text = ""
                            current_chunk_page = 1
                            
                            for chunk_data in raw_chunks:
                                text = chunk_data['text']
                                page_idx = chunk_data.get('page', 1)
                                
                                if len(current_chunk_text) + len(text) < min_chunk_size:
                                    if current_chunk_text:
                                        current_chunk_text += "\n\n" + text
                                    else:
                                        current_chunk_text = text
                                        current_chunk_page = page_idx
                                else:
                                    if current_chunk_text:
                                        merged_chunks.append({'text': current_chunk_text, 'page': current_chunk_page})
                                    current_chunk_text = text
                                    current_chunk_page = page_idx
                            
                            if current_chunk_text:
                                merged_chunks.append({'text': current_chunk_text, 'page': current_chunk_page})
                            
                            logging.info(f"🧩 Created {len(merged_chunks)} chunks from existing markdown")
                            
                            # Document objects oluştur
                            chunk_documents = []
                            for idx, chunk_data in enumerate(merged_chunks, 1):
                                page_num = chunk_data['page']
                                page_link = None
                                if page_images and page_num <= len(page_images):
                                    page_link = page_images[page_num - 1]
                                
                                chunk_documents.append(
                                    Document(
                                        page_content=chunk_data['text'],
                                        metadata={
                                            "chunk_id": idx,
                                            "source": normalized_filename,
                                            "page_number": page_num,
                                            "page_link": page_link
                                        }
                                    )
                                )
                            
                            # Chunk node'larını Neo4j'ye yaz
                            if chunk_documents:
                                from src.make_relationships import create_chunks_for_upload
                                await create_chunks_for_upload(graph, chunk_documents, normalized_filename, page_images=page_images)
                                logging.info(f"✅ Created {len(chunk_documents)} Chunk nodes in Neo4j")
                        else:
                            logging.warning("⚠️ Neo4j credentials not configured")
                    except Exception as neo4j_error:
                        logging.error(f"❌ Failed to create Neo4j nodes: {str(neo4j_error)}")
                        import traceback
                        logging.error(f"Traceback: {traceback.format_exc()}")
                        
                        # Rollback: Yarım kalan chunk'ları temizle
                        try:
                            from src.make_relationships import rollback_chunks_for_file, update_document_status_on_error
                            logging.info(f"🔄 Attempting rollback for partial chunks: {normalized_filename}")
                            rollback_result = await rollback_chunks_for_file(graph, normalized_filename)
                            if rollback_result.get('success'):
                                logging.info(f"✅ Rollback successful: deleted {rollback_result.get('deleted_chunks', 0)} chunks")
                            else:
                                logging.warning(f"⚠️ Rollback failed: {rollback_result.get('error', 'Unknown error')}")
                            
                            # Neo4j Document status'ünü Failed yap
                            await update_document_status_on_error(graph, normalized_filename, str(neo4j_error)[:500])
                        except Exception as rollback_error:
                            logging.error(f"❌ Rollback error: {rollback_error}")
                        
                        # Neo4j hatası kritik - chunking failed olarak işaretle ve exception fırlat
                        file_record.chunking_status = "failed"
                        file_record.processing_error = f"Neo4j error: {str(neo4j_error)[:500]}"
                        file_record.chunking_completed_at = datetime.now(timezone.utc)
                        db_session.commit()
                        logging.error(f"❌ V2: Chunking FAILED due to Neo4j error for: {file_record.original_name} (ID: {file_record.id})")
                        raise  # Celery retry mekanizmasını tetikle
                    
                    # Mark as chunked - sadece Neo4j başarılı olursa buraya ulaşır
                    # ✅ Async DB write via RabbitMQ - ensures persistence even if worker crashes
                    enqueue_db_write(file_record.id, {
                        "chunking_status": "chunked",
                        "chunking_completed_at": datetime.now(timezone.utc),
                        "status": "uploaded",
                        "markdown_path": file_record.markdown_path,
                        "reason": "Chunking completed successfully (markdown exists + Neo4j nodes)"
                    })
                    logging.info(
                        f"✅ V2: Chunking completed (markdown exists + Neo4j nodes) for: {file_record.original_name} (ID: {file_record.id}) - DB update queued"
                    )
                else:
                    # V2 Chunking: Use pre-extracted images with Gemini OCR (copied from backend/score.py)
                    from concurrent.futures import ThreadPoolExecutor
                    from src.utf8_utils import normalize_file_name
                    from src.document_sources.s3_upload_utils import create_document_output_structure
                    
                    normalized_filename = normalize_file_name(file_record.original_name)
                    document_dir, pdf_dir, images_dir = create_document_output_structure(
                        normalized_filename, "output"
                    )
                    
                    # Get file path (convert to absolute if needed)
                    file_path = file_record.file_path
                    if not os.path.isabs(file_path):
                        backend_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "backend", file_path)
                        if os.path.exists(backend_path):
                            file_path = backend_path
                        else:
                            celery_worker_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "celery_worker", file_path)
                            if os.path.exists(celery_worker_path):
                                file_path = celery_worker_path
                    
                    # Database'den page images bilgisini al (upload sırasında kaydedildi)
                    page_images = []
                    if file_record.page_images:
                        try:
                            page_images = json.loads(file_record.page_images)
                            logging.info(
                                f"📸 Using {len(page_images)} page images from upload step"
                            )
                        except:
                            logging.warning("⚠️ Failed to parse page_images from database")
                    
                    # 1️⃣ Local images_dir'de PNG dosyaları kontrol et
                    local_images = []
                    
                    # Check if we should use S3-only mode (for distributed workers)
                    use_s3_only = os.environ.get("USE_S3_ONLY", "false").lower() == "true"
                    
                    if not use_s3_only and os.path.exists(images_dir):
                        local_images = [
                            os.path.join(images_dir, f)
                            for f in os.listdir(images_dir)
                            if f.endswith(".png")
                        ]
                        local_images.sort()  # Sayfa sırasını koru
                    elif use_s3_only:
                        logging.info(f"☁️ V2 Chunking: USE_S3_ONLY mode enabled, skipping local disk check for: {normalized_filename}")
                    
                    # 2️⃣ Local'de yoksa, S3'ten download et
                    if not local_images and page_images:
                        logging.info(
                            f"📥 Local images not found, attempting to download from S3 for: {normalized_filename}"
                        )
                        
                        s3_bucket = os.environ.get(
                            "S3_BACKUP_BUCKET", "llm-graph-builder-backup"
                        )
                        aws_access_key_id = os.environ.get("AWS_ACCESS_KEY_ID")
                        aws_secret_access_key = os.environ.get("AWS_SECRET_ACCESS_KEY")

                        if s3_bucket and aws_access_key_id and aws_secret_access_key:
                            from src.document_sources.s3_upload_utils import (
                                download_images_from_s3,
                            )

                            doc_name = Path(normalized_filename).stem

                            def download_images():
                                return download_images_from_s3(
                                    page_images,  # Database'deki image isimleri
                                    s3_bucket,
                                    doc_name,
                                    images_dir,
                                    aws_access_key_id,
                                    aws_secret_access_key,
                                )

                            loop = asyncio.get_event_loop()
                            executor = ThreadPoolExecutor(max_workers=1)
                            downloaded_images = await loop.run_in_executor(
                                executor, download_images
                            )
                            executor.shutdown(wait=False)

                            # Check if all images were downloaded successfully
                            downloaded_count = len(downloaded_images) if downloaded_images else 0
                            expected_count = len(page_images)

                            if downloaded_count == expected_count and downloaded_count > 0:
                                # All images downloaded successfully
                                local_images = sorted(downloaded_images)
                                logging.info(
                                    f"✅ Downloaded {len(local_images)} images from S3 for: {normalized_filename}"
                                )
                            elif downloaded_count > 0:
                                # Some images downloaded successfully - use what we have
                                local_images = sorted(downloaded_images)
                                logging.warning(
                                    f"⚠️ Partially downloaded images from S3 for: {normalized_filename} "
                                    f"({downloaded_count}/{expected_count} downloaded), will use available images and extract missing ones from PDF"
                                )
                            else:
                                # No images downloaded - extract locally
                                logging.warning(
                                    f"⚠️ Failed to download images from S3 for: {normalized_filename} "
                                    f"({downloaded_count}/{expected_count} downloaded), will extract locally from PDF"
                                )
                                local_images = []
                        else:
                            logging.warning(
                                f"⚠️ S3 credentials not configured, cannot download images"
                            )

                    # 3️⃣ S3'te de yoksa veya download başarısız olduysa, PDF'den extract et
                    if not local_images:
                        logging.info(
                            f"🖼️ No images found locally or in S3, extracting from PDF: {normalized_filename}"
                        )
                        
                        # PDF dosyasını bul
                        pdf_path = os.path.join(pdf_dir, normalized_filename)
                        if not os.path.exists(pdf_path):
                            # Alternatif olarak file_path'i dene
                            if os.path.exists(file_path):
                                pdf_path = file_path
                            else:
                                error_msg = f"❌ PDF file not found: {pdf_path} or {file_path}"
                                logging.error(error_msg)
                                raise Exception(error_msg)

                        from src.document_sources.local_file import (
                            generate_page_images_with_pymupdf,
                        )

                        def extract_images():
                            return generate_page_images_with_pymupdf(pdf_path, images_dir)

                        loop = asyncio.get_event_loop()
                        executor = ThreadPoolExecutor(max_workers=1)
                        extracted_images = await loop.run_in_executor(
                            executor, extract_images
                        )
                        executor.shutdown(wait=False)

                        if extracted_images:
                            local_images = sorted(extracted_images)
                            logging.info(
                                f"✅ Extracted {len(local_images)} images from PDF for: {normalized_filename}"
                            )
                        else:
                            # No images extracted - continue with chunking anyway (text-only processing)
                            logging.warning(
                                f"⚠️ No images extracted from PDF for: {normalized_filename}, will continue with text-only processing"
                            )
                            local_images = []

                    # Final kontrol - eğer hala image yoksa PDF'den extract etmeyi dene
                    if not local_images:
                        logging.warning(
                            f"⚠️ No images available from S3 or local, attempting final PDF extraction for: {normalized_filename}"
                        )
                        # PDF dosyasını bul
                        pdf_path = os.path.join(pdf_dir, normalized_filename)
                        if not os.path.exists(pdf_path):
                            if os.path.exists(file_path):
                                pdf_path = file_path
                            else:
                                error_msg = f"❌ PDF file not found: {pdf_path} or {file_path}"
                                logging.error(error_msg)
                                raise Exception(error_msg)

                        from src.document_sources.local_file import (
                            generate_page_images_with_pymupdf,
                        )

                        def extract_images_final():
                            return generate_page_images_with_pymupdf(pdf_path, images_dir)

                        loop = asyncio.get_event_loop()
                        executor = ThreadPoolExecutor(max_workers=1)
                        extracted_images_final = await loop.run_in_executor(
                            executor, extract_images_final
                        )
                        executor.shutdown(wait=False)

                        if extracted_images_final:
                            local_images = sorted(extracted_images_final)
                            logging.info(
                                f"✅ Final extraction: Extracted {len(local_images)} images from PDF for: {normalized_filename}"
                            )
                        else:
                            # Son çare: PDF'den text extraction yap (image olmadan)
                            error_msg = f"❌ No images available for processing: {normalized_filename}. Please ensure PDF file exists and is valid."
                            logging.error(error_msg)
                            raise Exception(error_msg)

                    logging.info(
                        f"📸 Found {len(local_images)} images for Gemini OCR (local/S3/extracted)"
                    )

                    # SADECE GEMİNİ OCR İLE MARKDOWN OLUŞTUR (metadata + markdown)
                    from langchain_core.documents import Document

                    loop = asyncio.get_event_loop()
                    executor = ThreadPoolExecutor(max_workers=1)
                    ocr_result = await loop.run_in_executor(
                        executor, lambda: process_gemini_ocr(local_images, "generated")
                    )
                    executor.shutdown(wait=False)

                    # OCR sonucunu kontrol et
                    if not ocr_result or not isinstance(ocr_result, dict):
                        error_msg = f"❌ Gemini OCR returned invalid result format"
                        logging.error(error_msg)
                        raise Exception(error_msg)

                    markdown_text = ocr_result.get("markdown", "")
                    metadata = ocr_result.get("metadata", {})
                    doc_type = metadata.get("docType", "MAIN_POLICY")

                    # Gemini başarısız olduysa hata fırlat
                    if not markdown_text or not markdown_text.strip():
                        error_msg = f"❌ Gemini OCR failed to generate markdown from {len(local_images)} pre-extracted images"
                        logging.error(error_msg)
                        raise Exception(error_msg)

                    pages = [Document(page_content=markdown_text)]
                    logging.info(
                        f"✅ Gemini OCR completed: Generated markdown from {len(local_images)} pre-extracted images, detected docType: {doc_type}"
                    )

                    # Markdown path'i al (process_gemini_ocr tarafından oluşturuldu)
                    markdown_path = ocr_result.get("local_path")
                    
                    if not markdown_path or not os.path.exists(markdown_path):
                        # Fallback: Eğer process_gemini_ocr oluşturmadıysa burada oluştur
                        doc_name = Path(normalized_filename).stem
                        markdown_filename = f"{doc_name}.md"
                        markdown_path = os.path.join(document_dir, markdown_filename)
                        
                        with open(markdown_path, "w", encoding="utf-8") as md_file:
                            md_file.write(markdown_text.strip())
                            
                        logging.info(f"📝 Markdown file created (fallback): {markdown_path}")
                    else:
                        logging.info(f"📝 Using existing markdown file: {markdown_path}")

                    # ========================================
                    # NEO4J: Document ve Chunk node'larını oluştur
                    # ========================================
                    try:
                        logging.info(f"🔄 Creating Document and Chunk nodes in Neo4j for: {normalized_filename}")
                        
                        # Neo4j bağlantısı
                        uri = file_record.neo4j_uri or os.environ.get("NEO4J_URI")
                        userName = os.environ.get("NEO4J_USERNAME")
                        password = os.environ.get("NEO4J_PASSWORD")
                        database = file_record.neo4j_database or os.environ.get("NEO4J_DATABASE", "neo4j")
                        
                        if uri and userName and password:
                            graph = create_graph_database_connection(uri, userName, password, database)
                            
                            # 1. Document node oluştur
                            from src.graphDB_dataAccess import graphDBdataAccess
                            graphDb_data_Access = graphDBdataAccess(graph)
                            
                            # Document node'u oluştur veya güncelle
                            graphDb_data_Access.create_source_node(
                                normalized_filename,
                                document_type=doc_type if doc_type else "auto",
                                skip_entity_extraction=True  # Entity extraction graph creation'da yapılacak
                            )
                            logging.info(f"✅ Document node created/updated in Neo4j: {normalized_filename}")
                            
                            # 2. Markdown'ı chunk'lara ayır
                            # Minimum chunk boyutu (karakter)
                            min_chunk_size = 150
                            
                            # Markdown'ı raw chunk'lara ayır (separator bazlı)
                            raw_chunks = []
                            separators = ["## ", "\n\n", "\n"]
                            
                            # <CHUNK> tag'lerini temizle (markdown'da kalmış olabilir)
                            import re
                            chunk_tag_pattern = re.compile(r'<CHUNK>(.*?)</CHUNK>', re.DOTALL)
                            # Önce tag'lerin içindeki içeriği al, tag'ler yoksa orijinal text'i kullan
                            chunk_matches = chunk_tag_pattern.findall(markdown_text)
                            if chunk_matches:
                                # Tag'li markdown - tag'lerin içini al
                                current_text = "\n\n".join(match.strip() for match in chunk_matches if match.strip())
                                logging.info(f"🧹 Cleaned {len(chunk_matches)} <CHUNK> tags from markdown")
                            else:
                                # Tag'siz markdown - orijinal text'i kullan
                                current_text = markdown_text
                            
                            for sep in separators:
                                if sep in current_text:
                                    parts = current_text.split(sep)
                                    for i, part in enumerate(parts):
                                        if part.strip():
                                            # İlk parça değilse separator'ı başına ekle
                                            if i > 0 and sep == "## ":
                                                raw_chunks.append({"text": sep + part.strip(), "page": 1})
                                            else:
                                                raw_chunks.append({"text": part.strip(), "page": 1})
                                    break
                            else:
                                # Hiçbir separator bulunamadıysa tüm metni tek chunk yap
                                if current_text.strip():
                                    raw_chunks.append({"text": current_text.strip(), "page": 1})
                            
                            # Küçük chunk'ları birleştir
                            merged_chunks = []
                            current_chunk_text = ""
                            current_chunk_page = 1
                            
                            for chunk_data in raw_chunks:
                                text = chunk_data['text']
                                page_idx = chunk_data.get('page', 1)
                                
                                if len(current_chunk_text) + len(text) < min_chunk_size:
                                    # Chunk çok küçük, bir sonrakiyle birleştir
                                    if current_chunk_text:
                                        current_chunk_text += "\n\n" + text
                                    else:
                                        current_chunk_text = text
                                        current_chunk_page = page_idx
                                else:
                                    # Mevcut chunk'ı kaydet ve yenisine başla
                                    if current_chunk_text:
                                        merged_chunks.append({
                                            'text': current_chunk_text,
                                            'page': current_chunk_page
                                        })
                                    current_chunk_text = text
                                    current_chunk_page = page_idx
                            
                            # Son chunk'ı ekle
                            if current_chunk_text:
                                merged_chunks.append({
                                    'text': current_chunk_text,
                                    'page': current_chunk_page
                                })
                            
                            logging.info(f"🧩 Parsed {len(raw_chunks)} raw chunks, merged into {len(merged_chunks)} semantic chunks (min {min_chunk_size} chars)")
                            
                            # 3. Document objects oluştur (LangChain format)
                            chunk_documents = []
                            for idx, chunk_data in enumerate(merged_chunks, 1):
                                page_num = chunk_data['page']
                                # page_link'i page_images'dan al
                                page_link = None
                                if page_images and page_num <= len(page_images):
                                    page_link = page_images[page_num - 1]  # 0-indexed
                                
                                chunk_documents.append(
                                    Document(
                                        page_content=chunk_data['text'],
                                        metadata={
                                            "chunk_id": idx,
                                            "source": normalized_filename,
                                            "page_number": page_num,
                                            "page_link": page_link
                                        }
                                    )
                                )
                            
                            # 4. Chunk node'larını Neo4j'ye yaz
                            if chunk_documents:
                                from src.make_relationships import create_chunks_for_upload
                                await create_chunks_for_upload(
                                    graph, 
                                    chunk_documents, 
                                    normalized_filename, 
                                    page_images=page_images,
                                    generate_embedding=False  # Embedding ayrı aşamada yapılacak
                                )
                                logging.info(f"✅ Created {len(chunk_documents)} Chunk nodes in Neo4j for: {normalized_filename}")
                            else:
                                logging.warning(f"⚠️ No chunks to create for: {normalized_filename}")
                        else:
                            logging.warning(f"⚠️ Neo4j credentials not configured, skipping Document/Chunk node creation")
                    
                    except Exception as neo4j_error:
                        logging.error(f"❌ Failed to create Document/Chunk nodes in Neo4j: {str(neo4j_error)}")
                        import traceback
                        logging.error(f"Traceback: {traceback.format_exc()}")
                        # Neo4j hatası kritik - chunking failed olarak işaretle ve exception fırlat
                        # Markdown oluşturuldu ama Neo4j'ye yazılamadı - retry gerekli
                        file_record.markdown_path = markdown_path  # Markdown path'i yine de kaydet
                        file_record.chunking_status = "failed"
                        file_record.processing_error = f"Neo4j error: {str(neo4j_error)[:500]}"
                        file_record.chunking_completed_at = datetime.now(timezone.utc)
                        db_session.commit()
                        logging.error(f"❌ V2: Chunking FAILED due to Neo4j error for: {file_record.original_name} (ID: {file_record.id})")
                        raise  # Celery retry mekanizmasını tetikle
                    
                    # ========================================
                    # END: Neo4j Document ve Chunk node oluşturma
                    # ========================================

                    # Markdown path'i kaydet - sadece Neo4j başarılı olursa buraya ulaşır
                    # ✅ Async DB write via RabbitMQ - ensures persistence even if worker crashes
                    enqueue_db_write(file_record.id, {
                        "markdown_path": markdown_path,
                        "chunking_status": "chunked",
                        "chunking_completed_at": datetime.now(timezone.utc),
                        "status": "uploaded",  # Reset status so frontend can proceed
                        "reason": "Chunking completed successfully (markdown created + Neo4j nodes)"
                    })
                    
                    logging.info(
                        f"✅ V2: Chunking completed (markdown created + Neo4j nodes) for: {file_record.original_name} (ID: {file_record.id}) - DB update queued"
                    )

                # Neo4j yazımı başarılı olduysa (exception fırlatılmadı), chunking tamamlandı kabul et
                # DB güncellemesi async yapıldı, refresh yapmaya gerek yok
                logging.info(
                    f"✅ V2: Chunking completed for: {file_record.original_name} (ID: {file_record.id}), will be queued for graph creation"
                )
                
                # Not: Aşağıdaki else bloğu artık kullanılmıyor çünkü Neo4j hatası exception fırlatır
                if False:  # Backward compatibility için tutuldu, çalışmayacak
                    logging.warning(
                        f"⚠️ V2: Chunking status unexpected for: {file_record.original_name} (status: {file_record.chunking_status})"
                    )

            except Exception as chunk_error:
                logging.error(
                    f"❌ V2: Chunking failed for {file_record.original_name}: {str(chunk_error)}"
                )
                import traceback

                logging.error(f"Traceback: {traceback.format_exc()}")
                # Mark as failed
                file_record = (
                    db_session.query(UploadedFile).filter_by(id=file_record.id).first()
                )
                if file_record:
                    file_record.chunking_status = "failed"
                    file_record.processing_error = str(chunk_error)[:500]
                    file_record.reason = f"Chunking failed: {str(chunk_error)}"
                    # Remove from queue so it doesn't block other files
                    if file_record.status in ("queued", "processing"):
                        file_record.status = "uploaded"
                    db_session.commit()
                    logging.info(
                        f"🔄 V2: Removed failed chunking file {file_record.id} ({file_record.original_name}) from queue"
                    )
            finally:
                db_session.close()

        except Exception as e:
            logging.error(
                f"❌ V2: Error processing chunking for file {file_record.id}: {str(e)}"
            )
            import traceback

            logging.error(f"Traceback: {traceback.format_exc()}")

    async def process_v2_chunking(self, file_record: UploadedFile):
        """Process V2 chunking for a file"""
        # NOTE: This function is deprecated - chunking is now handled by _process_single_file_chunking
        # which uses process_v2_chunking_batch. This function should not be called directly.
        # Keeping it for backward compatibility but it will raise an error.
        raise NotImplementedError(
            "process_v2_chunking is deprecated. Use process_v2_chunking_batch instead. "
            "This function attempted to import 'score' module which is not available in celery_worker."
        )

    async def process_v2_graph_creation(
        self,
        file_record: UploadedFile,
        model: str = None,
        generate_embedding: bool = False,
    ):
        """
        Process V2 graph creation for a file (after chunking)
        Copied from backend/score.py process_graph_creation_v2
        """
        db_session = None
        try:
            # Check if graph creation is needed (status should be "processing" from batch selection)
            if file_record.graph_status not in ("pending", "processing"):
                logging.info(
                    f"ℹ️ V2: Graph creation not needed for: {file_record.original_name} (status: {file_record.graph_status})"
                )
                return

            # Use provided model or fallback to file_record's model
            model = model or file_record.model_used or "openai_gpt_4o_mini"

            # Get Neo4j credentials
            uri = file_record.neo4j_uri or os.environ.get("NEO4J_URI")
            userName = os.environ.get("NEO4J_USERNAME")
            password = os.environ.get("NEO4J_PASSWORD")
            database = file_record.neo4j_database or os.environ.get(
                "NEO4J_DATABASE", "neo4j"
            )

            if not all([uri, userName, password]):
                logging.warning(
                    f"⚠️ V2: Neo4j credentials not configured, skipping graph creation for: {file_record.original_name}"
                )
                return

            db_session = self.db.get_db_session()
            file_record = (
                db_session.query(UploadedFile).filter_by(id=file_record.id).first()
            )
            if not file_record:
                logging.error(f"❌ File record not found for ID: {file_record.id}")
                return

            logging.info(f"🎨 Starting V2 graph creation for: {file_record.original_name}")

            # Normalize filename
            from src.utf8_utils import normalize_file_name
            normalized_filename = normalize_file_name(file_record.original_name)

            # Markdown dosyasını oku
            markdown_path = file_record.markdown_path
            if not markdown_path or not os.path.exists(markdown_path):
                logging.error(f"❌ Markdown file not found: {markdown_path}")
                file_record.graph_status = "failed"
                file_record.processing_error = "Markdown file not found"
                file_record.reason = "Graph creation failed: Markdown file not found"
                db_session.commit()
                return

            # Markdown'ı pages olarak yükle
            from langchain_core.documents import Document

            with open(markdown_path, "r", encoding="utf-8") as md_file:
                markdown_content = md_file.read()

            # Retrieve page_images from file_record BEFORE chunk parsing
            page_images = []
            if file_record.page_images:
                try:
                    page_images = json.loads(file_record.page_images)
                    logging.info(f"🖼️ Retrieved {len(page_images)} page images from file record")
                except Exception as e:
                    logging.warning(f"⚠️ Failed to parse page_images from file record: {e}")

            # Page break'lere göre sayfalara bölmek yerine CHUNK'ları parse et
            import re
            
            # Regex to find content within <CHUNK> tags
            chunk_pattern = re.compile(r'<CHUNK>(.*?)</CHUNK>', re.DOTALL)
            raw_chunks = chunk_pattern.findall(markdown_content)
            
            if not raw_chunks:
                # Fallback to page splitting if no chunks found
                logging.warning(f"⚠️ No <CHUNK> tags found in markdown for {normalized_filename}, falling back to page splitting")
                page_texts = markdown_content.split("[PAGE BREAK]")
                pages = [
                    Document(page_content=text.strip(), metadata={"page": idx, "chunk_id": idx})
                    for idx, text in enumerate(page_texts, 1)
                    if text.strip()
                ]
            else:
                # Merge small chunks (< 150 chars) with page tracking
                merged_chunks = []
                current_chunk_text = ""
                current_chunk_page = 1  # Start from page 1
                
                # Split markdown by [PAGE BREAK] to track pages
                page_sections = markdown_content.split('[PAGE BREAK]')
                
                for page_idx, page_section in enumerate(page_sections, start=1):
                    # Extract chunks from this page section
                    page_raw_chunks = chunk_pattern.findall(page_section)
                    
                    for chunk_text in page_raw_chunks:
                        chunk_text = chunk_text.strip()
                        if not chunk_text:
                            continue
                            
                        if not current_chunk_text:
                            current_chunk_text = chunk_text
                            current_chunk_page = page_idx
                        else:
                            # Check if adding this chunk keeps it under limit or if current is too small
                            if len(current_chunk_text) < 150:
                                # Current is small.
                                # Check if the INCOMING chunk is big (>150) AND we have a previous chunk
                                if len(chunk_text) > 150 and merged_chunks:
                                    # User Rule: Current is small, Next is Big -> Merge Current to Previous
                                    merged_chunks[-1]['text'] += "\n" + current_chunk_text
                                    # Set incoming (big) as new current
                                    current_chunk_text = chunk_text
                                    current_chunk_page = page_idx
                                else:
                                    # Standard: Merge incoming into current
                                    current_chunk_text += "\n" + chunk_text
                            else:
                                # Current chunk is big enough, save it and start new
                                merged_chunks.append({
                                    'text': current_chunk_text,
                                    'page': current_chunk_page
                                })
                                current_chunk_text = chunk_text
                                current_chunk_page = page_idx
                
                # Add the last chunk
                if current_chunk_text:
                    merged_chunks.append({
                        'text': current_chunk_text,
                        'page': current_chunk_page
                    })
                    
                logging.info(f"🧩 Parsed {len(raw_chunks)} raw chunks, merged into {len(merged_chunks)} semantic chunks (min 150 chars)")
                
                # Create Document objects from merged chunks with page metadata
                pages = []
                for idx, chunk_data in enumerate(merged_chunks, 1):
                    page_num = chunk_data['page']
                    # Generate page_link from page_images if available
                    page_link = None
                    if page_images and page_num <= len(page_images):
                        page_link = page_images[page_num - 1]  # 0-indexed
                    
                    pages.append(
                        Document(
                            page_content=chunk_data['text'], 
                            metadata={
                                "chunk_id": idx, 
                                "source": normalized_filename,
                                "page_number": page_num,
                                "page_link": page_link
                            }
                        )
                    )

            logging.info(
                f"📄 Prepared {len(pages)} chunks for V2 graph extraction"
            )

            # V2 processing_source_v2 fonksiyonunu kullan (aynı dosyada tanımlı, doğrudan çağır)

            # Parametreler
            allowedNodes = []  # Boş = tüm node'lar
            allowedRelationship = []  # Boş = tüm relationship'ler
            additional_instructions = None
            max_pages = None

            logging.info(f"🔄 Calling processing_source_v2 for: {normalized_filename}")
            logging.info(
                f"✨ V2 Mode: Pages-based extraction (NO chunks, NO embeddings, NO chunk-entity linking)"
            )

            # V2 Graph extraction - pages-based, chunk'sız
            latency, response = await processing_source_v2(
                uri=uri,
                userName=userName,
                password=password,
                database=database,
                model=model,
                file_name=normalized_filename,
                pages=pages,
                allowedNodes=allowedNodes,
                allowedRelationship=allowedRelationship,
                additional_instructions=additional_instructions,
                max_pages=max_pages,
                page_images=page_images,  # Pass page_images
            )

            # Check if processing_source_v2 failed
            if not response or response.get("status") == "Failed":
                error_message = (
                    response.get("error", "Unknown error")
                    if response
                    else "No response from processing_source_v2"
                )
                logging.error(
                    f"❌ V2 Graph extraction failed for: {normalized_filename} - {error_message}"
                )
                file_record.graph_status = "failed"
                file_record.processing_error = error_message[:500]
                file_record.reason = f"Graph extraction failed: {error_message}"
                db_session.commit()
                return

            logging.info(f"✅ V2 Graph extraction completed for: {normalized_filename}")
            logging.info(f"⏱️ Latency details: {latency}")

            # Check if Policy node was created in Neo4j
            # If no Policy node exists, mark as failed
            try:
                from src.shared.common_fn import create_graph_database_connection

                graph_connection = await asyncio.to_thread(
                    create_graph_database_connection,
                    uri, userName, password, database
                )

                # Check if Policy or Endorsement node exists for this document
                node_check_query = """
                MATCH (d:Document {fileName: $file_name})
                OPTIONAL MATCH (p:Policy)-[:DOCUMENTED_IN]->(d)
                OPTIONAL MATCH (e:Endorsement)-[:DOCUMENTED_IN]->(d)
                RETURN count(p) as policy_count, count(e) as endorsement_count, d.docType as doc_type
                """

                node_result = await asyncio.to_thread(
                    graph_connection.query,
                    node_check_query,
                    {"file_name": normalized_filename}
                )

                policy_count = node_result[0]["policy_count"] if node_result else 0
                endorsement_count = (
                    node_result[0]["endorsement_count"] if node_result else 0
                )
                doc_type = node_result[0].get("doc_type", "") if node_result else ""

                # Endorsement dosyaları için Endorsement node kontrolü yap
                # Ana poliçeler için Policy node kontrolü yap
                if doc_type in ["ENDORSEMENT", "RENEWAL", "CANCELLATION"]:
                    if endorsement_count == 0:
                        error_message = f"Endorsement node was not created for document: {normalized_filename}"
                        logging.error(f"❌ {error_message}")
                        file_record.graph_status = "failed"
                        file_record.processing_error = error_message[:500]
                        file_record.reason = f"Graph verification failed: {error_message}"
                        db_session.commit()
                        return
                    else:
                        logging.info(
                            f"✅ Endorsement node found for document: {normalized_filename} (count: {endorsement_count})"
                        )
                elif policy_count == 0:
                    error_message = (
                        f"Policy node was not created for document: {normalized_filename}"
                    )
                    logging.error(f"❌ {error_message}")
                    file_record.graph_status = "failed"
                    file_record.processing_error = error_message[:500]
                    file_record.reason = f"Graph verification failed: {error_message}"
                    db_session.commit()
                    return
                else:
                    logging.info(
                        f"✅ Policy node verified: {policy_count} Policy node(s) found for {normalized_filename}"
                    )
            except Exception as policy_check_error:
                logging.error(
                    f"❌ Error checking Policy node for {normalized_filename}: {str(policy_check_error)}"
                )
                # Don't fail the entire process if policy check fails
                import traceback
                logging.error(f"Traceback: {traceback.format_exc()}")

            # Embedding oluştur (eğer isteniyorsa) - async
            if generate_embedding:
                try:
                    logging.info(f"🔄 Creating embeddings for: {normalized_filename}")
                    from src.graphDB_dataAccess import graphDBdataAccess

                    graph = await asyncio.to_thread(
                        create_graph_database_connection,
                        uri, userName, password, database
                    )
                    graphDb_data_Access = graphDBdataAccess(graph)

                    # Document için embedding oluştur - async
                    embedding_result = await asyncio.to_thread(
                        graphDb_data_Access.create_embeddings_for_documents,
                        [normalized_filename]
                    )
                    if embedding_result and not embedding_result.get("error"):
                        logging.info(
                            f"✅ Embeddings created successfully for: {normalized_filename}"
                        )
                    else:
                        logging.warning(
                            f"⚠️ Embedding creation warning: {embedding_result.get('error', 'Unknown error')}"
                        )
                except Exception as emb_error:
                    logging.error(f"❌ Embedding creation failed: {emb_error}")
                    # Continue without embeddings

            # Response'tan istatistikleri al
            processing_time = response.get("total_processing_time", 0) if response else 0

            # Graph creation tamamlandı, status güncelle
            file_record.graph_status = "completed"
            file_record.graph_completed_at = datetime.now(timezone.utc)
            file_record.status = "completed"
            file_record.processing_time = processing_time
            file_record.reason = "Graph creation completed successfully"

            db_session.commit()

            # Neo4j'ye sync et - async
            try:
                # Import from backend (status_sync is in backend)
                # Note: os and sys are already imported at module level
                current_file_dir = os.path.dirname(os.path.abspath(__file__))
                celery_worker_dir = os.path.dirname(current_file_dir)
                project_root = os.path.dirname(celery_worker_dir)
                backend_path = os.path.join(project_root, 'backend')
                if backend_path not in sys.path:
                    sys.path.insert(0, backend_path)
                from src.models.status_sync import sync_queue_db_status_to_neo4j
                logging.info(
                    f"📤 Attempting Neo4j sync for graph_creation: file_name={file_record.original_name}, "
                    f"upload_status={file_record.upload_status}, "
                    f"chunking_status={file_record.chunking_status}, "
                    f"graph_status={file_record.graph_status}, "
                    f"embedding_status={file_record.embedding_status}"
                )
                graph_connection = await asyncio.to_thread(
                    create_graph_database_connection,
                    uri, userName, password, database
                )
                await asyncio.to_thread(
                    sync_queue_db_status_to_neo4j,
                    graph_connection,
                    file_record.original_name,
                    file_record.upload_status,
                    file_record.chunking_status,
                    file_record.graph_status,
                    file_record.embedding_status,
                    database
                )
            except Exception as sync_error:
                logging.warning(
                    f"⚠️ Could not sync graph status to Neo4j: {str(sync_error)}"
                )

            # Schema değişti - Global schema cache version'ını artır
            # Bu sayede chat tarafı yeni şemayı alacak
            try:
                # Backend path'ini ekle (schema_cache backend'de)
                if backend_path not in sys.path:
                    sys.path.insert(0, backend_path)
                from src.shared.schema_cache import increment_schema_version
                database_url = uri  # Neo4j connection URL
                new_version = increment_schema_version(database_url)
                logging.info(
                    f"📈 Schema version artırıldı: {database_url} → v{new_version}"
                )
            except Exception as schema_version_error:
                logging.warning(
                    f"⚠️ Schema version artırılamadı: {str(schema_version_error)}"
                )

            logging.info(
                f"✅ V2 Graph creation completed for: {file_record.original_name}"
            )

        except Exception as e:
            logging.error(
                f"❌ process_v2_graph_creation failed for file {file_record.id}: {str(e)}"
            )
            import traceback
            logging.error(f"Traceback: {traceback.format_exc()}")

            if db_session and file_record:
                file_record.graph_status = "failed"
                file_record.processing_error = str(e)[:500]
                file_record.reason = f"Graph creation failed: {str(e)}"
                db_session.commit()

                # Neo4j'ye failed status sync et - async
                try:
                    # Import from backend (status_sync is in backend)
                    # Note: os and sys are already imported at module level
                    current_file_dir = os.path.dirname(os.path.abspath(__file__))
                    celery_worker_dir = os.path.dirname(current_file_dir)
                    project_root = os.path.dirname(celery_worker_dir)
                    backend_path = os.path.join(project_root, 'backend')
                    if backend_path not in sys.path:
                        sys.path.insert(0, backend_path)
                    from src.models.status_sync import sync_queue_db_status_to_neo4j
                    from src.shared.common_fn import create_graph_database_connection
                    
                    uri = file_record.neo4j_uri or os.environ.get("NEO4J_URI")
                    userName = os.environ.get("NEO4J_USERNAME")
                    password = os.environ.get("NEO4J_PASSWORD")
                    database = file_record.neo4j_database or os.environ.get("NEO4J_DATABASE", "neo4j")
                    
                    if all([uri, userName, password]):
                        graph_connection = await asyncio.to_thread(
                            create_graph_database_connection,
                            uri, userName, password, database
                        )
                        await asyncio.to_thread(
                            sync_queue_db_status_to_neo4j,
                            graph_connection,
                            file_record.original_name,
                            file_record.upload_status,
                            file_record.chunking_status,
                            "failed",
                            file_record.embedding_status,
                            database
                        )
                except Exception as sync_error:
                    logging.warning(
                        f"⚠️ Could not sync graph failure to Neo4j: {str(sync_error)}"
                    )
            raise
        finally:
            if db_session:
                db_session.close()

    async def process_v2_graph_creation_batch(self, files: list):
        """Process graph creation for a batch of files (eş zamanlı olarak)"""
        file_ids = [f.id for f in files]
        file_names = [f.original_name for f in files]
        logging.info(
            f"🎨 V2: Starting graph creation batch for {len(files)} files (eş zamanlı)"
        )
        logging.info(f"📋 Batch file IDs: {file_ids}")
        logging.info(f"📋 Batch file names: {file_names[:5]}{'...' if len(file_names) > 5 else ''}")

        # Process all files concurrently using asyncio.gather
        tasks = [
            self._process_single_file_graph_creation(file_record)
            for file_record in files
        ]
        logging.info(f"✅ V2: Created {len(tasks)} concurrent tasks, starting execution...")
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Log results
        success_count = sum(1 for r in results if not isinstance(r, Exception))
        error_count = sum(1 for r in results if isinstance(r, Exception))
        logging.info(f"✅ V2: Graph creation batch completed - Success: {success_count}, Errors: {error_count}")
        
        # Log any errors
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logging.error(f"❌ V2: Error processing file {file_ids[i]} ({file_names[i]}): {result}")

    async def _process_single_file_graph_creation(self, file_record: UploadedFile):
        """Process graph creation for a single file (used for concurrent processing)"""
        file_id = file_record.id
        file_name = file_record.original_name
        logging.info(f"🚀 V2: _process_single_file_graph_creation başladı - File ID: {file_id}, Name: {file_name}")
        try:
            db_session = self.db.get_db_session()
            try:
                # Refresh file record
                file_record = (
                    db_session.query(UploadedFile).filter_by(id=file_record.id).first()
                )
                if not file_record:
                    logging.warning(f"⚠️ V2: File record not found for ID: {file_id}")
                    return

                # Check if already processed
                if file_record.graph_status == "completed":
                    logging.info(
                        f"ℹ️ V2: Graph already created: {file_record.original_name}"
                    )
                    return

                # Check if chunking is completed
                if file_record.chunking_status != "chunked":
                    logging.warning(
                        f"⚠️ V2: Chunking not completed for: {file_record.original_name} (status: {file_record.chunking_status})"
                    )
                    # If chunking failed, mark graph creation as failed too
                    if file_record.chunking_status == "failed":
                        file_record.graph_status = "failed"
                        file_record.processing_error = f"Graph creation skipped: Chunking failed - {file_record.processing_error or 'Unknown error'}"
                        db_session.commit()
                        logging.info(f"❌ V2: Graph creation marked as failed due to chunking failure for: {file_record.original_name}")
                    return

                # Check if markdown exists
                if not file_record.markdown_path or not os.path.exists(
                    file_record.markdown_path
                ):
                    logging.warning(
                        f"⚠️ V2: Markdown file not found for: {file_record.original_name}"
                    )
                    return

                # Check docType before graph creation (Neo4j'den direkt okuyoruz, LLM'den tekrar çıkarmıyoruz)
                # If docType is not MAIN_POLICY, skip graph creation and mark as pending_endorsement
                try:
                    from src.shared.common_fn import create_graph_database_connection

                    # Get Neo4j credentials for docType check
                    uri = file_record.neo4j_uri or os.environ.get("NEO4J_URI")
                    userName = os.environ.get("NEO4J_USERNAME")
                    password = os.environ.get("NEO4J_PASSWORD")
                    database = file_record.neo4j_database or os.environ.get(
                        "NEO4J_DATABASE", "neo4j"
                    )

                    if all([uri, userName, password]):
                        # Create graph connection for docType check (async olarak thread pool'da çalıştır)
                        graph = await asyncio.to_thread(
                            create_graph_database_connection,
                            uri,
                            userName,
                            password,
                            database,
                        )

                        if graph:
                            # Neo4j'den docType'ı direkt oku (chunking aşamasında kaydedilmiş)
                            doc_type_query = """
                            MATCH (d:Document {fileName: $file_name})
                            RETURN d.docType as docType
                            LIMIT 1
                            """
                            
                            doc_type_result = await asyncio.to_thread(
                                graph.query,
                                doc_type_query,
                                {"file_name": file_record.filename}
                            )

                            doc_type = None
                            if doc_type_result and len(doc_type_result) > 0:
                                doc_type = doc_type_result[0].get("docType")
                            
                            # Eğer docType yoksa veya MAIN_POLICY değilse, pending_endorsement olarak işaretle
                            if doc_type and doc_type not in ["MAIN_POLICY", None, ""]:
                                logging.info(
                                    f"📋 V2: Document docType is {doc_type} (not MAIN_POLICY), skipping graph creation for: {file_record.original_name}"
                                )
                                # Mark as pending_endorsement
                                file_record.graph_status = "pending_endorsement"
                                # Remove from processing queue
                                if file_record.status in (
                                    "queued",
                                    "processing",
                                ):
                                    file_record.status = "uploaded"
                                db_session.commit()
                                logging.info(
                                    f"✅ V2: File {file_record.id} ({file_record.original_name}) marked as pending_endorsement"
                                )
                                
                                # Close graph connection
                                if hasattr(graph, "_driver") and not graph._driver._closed:
                                    graph._driver.close()
                                
                                return  # Skip graph creation
                            elif doc_type == "MAIN_POLICY":
                                logging.info(
                                    f"✅ V2: Document docType is MAIN_POLICY, proceeding with graph creation for: {file_record.original_name}"
                                )
                            else:
                                # docType yoksa veya None ise, varsayılan olarak MAIN_POLICY kabul et
                                logging.info(
                                    f"ℹ️ V2: Document docType not found or None for {file_record.original_name}, assuming MAIN_POLICY and proceeding with graph creation"
                                )
                            
                            # Close graph connection (graph creation'da tekrar açılacak)
                            if hasattr(graph, "_driver") and not graph._driver._closed:
                                graph._driver.close()
                except Exception as doc_type_error:
                    # If docType check fails, proceed with graph creation (fallback to MAIN_POLICY)
                    logging.warning(
                        f"⚠️ V2: Document docType check failed for {file_record.original_name}: {str(doc_type_error)}. Proceeding with graph creation (assuming MAIN_POLICY)."
                    )

                # Status zaten batch seçiminde "processing" olarak güncellenmiş
                # Model, uri gibi bilgiler de batch seçiminde güncellenmiş
                # Sadece graph_started_at güncelle (eğer yoksa)
                if not file_record.graph_started_at:
                    file_record.graph_started_at = datetime.now(timezone.utc)
                    db_session.commit()

                # Get Neo4j credentials
                uri = file_record.neo4j_uri or os.environ.get("NEO4J_URI")
                userName = os.environ.get("NEO4J_USERNAME")
                password = os.environ.get("NEO4J_PASSWORD")
                database = file_record.neo4j_database or os.environ.get(
                    "NEO4J_DATABASE", "neo4j"
                )

                # Get model and generate_embedding from file_record (batch seçiminde güncellenmiş)
                model = file_record.model_used or "openai_gpt_4o_mini"
                generate_embedding = (
                    file_record.generate_embedding
                    and file_record.generate_embedding.lower()
                    in ("true", "1", "yes", "on")
                )

                if not all([uri, userName, password]):
                    logging.warning(
                        f"⚠️ V2: Neo4j credentials not configured for: {file_record.original_name}"
                    )
                    return

                # Status zaten batch seçiminde "processing" olarak güncellenmiş
                # Model, uri gibi bilgiler de batch seçiminde güncellenmiş
                logging.info(
                    f"🎨 V2: Starting graph creation for: {file_record.original_name} (ID: {file_record.id}), Model: {model}"
                )

                # Process graph creation asynchronously (await edilerek eş zamanlı çalışması sağlanıyor)
                # Model ve generate_embedding parametrelerini geç
                await self.process_v2_graph_creation(
                    file_record, model=model, generate_embedding=generate_embedding
                )

                logging.info(
                    f"✅ V2: Graph creation completed for: {file_record.original_name} (ID: {file_record.id})"
                )

            except Exception as graph_error:
                logging.error(
                    f"❌ V2: Graph creation failed for {file_record.original_name}: {str(graph_error)}"
                )
                import traceback

                logging.error(f"Traceback: {traceback.format_exc()}")
                # Mark as failed
                file_record = (
                    db_session.query(UploadedFile).filter_by(id=file_record.id).first()
                )
                if file_record:
                    file_record.graph_status = "failed"
                    file_record.processing_error = str(graph_error)[:500]
                    file_record.reason = f"Graph creation failed: {str(graph_error)}"
                    # Remove from queue so it doesn't block other files
                    if file_record.status in ("queued", "processing"):
                        file_record.status = "uploaded"
                    db_session.commit()
                    logging.info(
                        f"🔄 V2: Removed failed graph creation file {file_record.id} ({file_record.original_name}) from queue"
                    )
            finally:
                db_session.close()

        except Exception as e:
            logging.error(
                f"❌ V2: Error processing graph creation for file {file_record.id}: {str(e)}"
            )
            import traceback

            logging.error(f"Traceback: {traceback.format_exc()}")


    def get_processing_status(self) -> Dict[str, Any]:
        """Get current processing status"""
        return {
            "is_processing": self.is_processing,
            "current_task_id": self.current_task_id,
            "queue_stats": self.db.get_queue_stats(),
        }


# Global processor instance
_processor_instance = None


def get_background_processor() -> FileProcessor:
    """Get global background processor instance"""
    global _processor_instance

    if _processor_instance is None:
        _processor_instance = FileProcessor()

    return _processor_instance


async def start_processing_loop():
    """Start the background processing loop"""
    processor = get_background_processor()
    await processor.start_background_processing()


def stop_processing_loop():
    """Stop the background processing loop"""
    processor = get_background_processor()
    processor.stop_background_processing()

