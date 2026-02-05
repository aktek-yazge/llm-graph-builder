import logging
import asyncio
from datetime import datetime, timezone
from sqlalchemy.exc import OperationalError as SQLAlchemyOperationalError

from src.celery_app import app
from src.models.file_queue_models import get_file_queue_db, FileStatus, UploadedFile
from src.processing_utils import GeminiOCRException, GeminiRateLimitException

# Langfuse LLM Observability
from src.shared.langfuse_client import trace_document_processing, flush_langfuse

# Schema version - belge yüklenince version artırılıyor (minimal modül)
from src.shared.schema_version import increment_schema_version

# Import DB Write Queue helpers - async writes to PostgreSQL
from src.db_writer import (
    enqueue_db_write,
    enqueue_chunking_update,
    enqueue_graph_update,
    enqueue_embedding_update,
    enqueue_error_update,
)
# Lazy imports to avoid SIGSEGV on module load (especially with prefork pool + MPS)
# from src.processing_utils import FileProcessor
# from src.document_sources.s3_upload_utils import create_document_output_structure
# from src.create_chunks import CreateChunksofDocument
# from src.shared.common_fn import create_graph_database_connection
from pathlib import Path
import os

# Helper to get DB session
def get_db():
    return get_file_queue_db()


class TaskCancelledException(Exception):
    """Exception raised when a task is cancelled or file is deleted during processing"""
    pass


def check_task_cancelled(task_instance, file_id: int, db_session) -> bool:
    """
    Check if task should be cancelled.
    Returns True if task should stop, False if it should continue.
    
    Checks:
    1. If the Celery task itself was revoked
    2. If the file record no longer exists (deleted)
    3. If the file status is 'deleted' or 'cancelled'
    """
    # Check if Celery task was revoked
    if task_instance.request.id:
        try:
            # Check revoke state from backend
            result = app.AsyncResult(task_instance.request.id)
            if result.state == 'REVOKED':
                logging.warning(f"⚠️ Task {task_instance.request.id} was revoked for file {file_id}")
                return True
        except Exception:
            pass  # Ignore errors in checking revoke state
    
    # Check if file still exists and is not deleted
    try:
        file_record = db_session.query(UploadedFile).filter_by(id=file_id).first()
        if not file_record:
            logging.warning(f"⚠️ File {file_id} no longer exists in database - task cancelled")
            return True
        
        # Check if file was marked as deleted or cancelled
        if file_record.status in ['deleted', 'cancelled']:
            logging.warning(f"⚠️ File {file_id} status is '{file_record.status}' - task cancelled")
            return True
            
    except Exception as e:
        logging.warning(f"⚠️ Error checking file status for {file_id}: {e}")
        # Continue processing if we can't check
        return False
    
    return False


def raise_if_cancelled(task_instance, file_id: int, db_session, step_name: str = ""):
    """
    Raise TaskCancelledException if task should be cancelled.
    Use this at key checkpoints in long-running tasks.
    """
    if check_task_cancelled(task_instance, file_id, db_session):
        raise TaskCancelledException(f"Task cancelled at step '{step_name}' for file {file_id}")


# ============================================================================
# DEPRECATED TASKS - Kept for backward compatibility with old RabbitMQ messages
# These tasks do nothing but consume old messages that may be stuck in queues
# ============================================================================

@app.task(bind=True, name="src.tasks.extract_images_task")
def extract_images_task(self, file_id: int):
    """
    DEPRECATED: Image extraction is now done in backend during upload.
    This task is kept to consume old messages in RabbitMQ queues.
    """
    logging.warning(f"⚠️ DEPRECATED: extract_images_task called for file {file_id} - ignoring")
    return file_id


@app.task(bind=True, name="src.tasks.process_file_pipeline", acks_late=True)
def process_file_pipeline(self, file_id: int):
    """
    Orchestrator task that triggers the pipeline:
    Image Extraction -> Chunking -> Graph Creation
    """
    logging.info(f"🚀 Starting pipeline for file {file_id}")
    
    try:
        # Chain the tasks
        # 1. Extract Images
        # 2. Chunk File (depends on images)
        # 3. Create Graph (depends on chunks)
        
        # We use 'link' to chain them. 
        # Note: We need to pass file_id to each.
        
        # Chain tasks: extract_images -> chunk_file -> create_graph
        # Each task receives file_id and returns it for the next task
        from celery import chain
        
        # Create chain: chunk_file -> create_graph
        # Each task returns file_id, next task receives it as first argument
        workflow = chain(
            chunk_file_task.s(file_id),       # First task: Chunking (handles download if needed)
            create_graph_task.s()             # Receives file_id from previous task's return value
        )
        
        # Apply chain asynchronously - ensure it goes to default queue
        result = workflow.apply_async()
        logging.info(f"🔗 Pipeline chained for file {file_id}, chain ID: {result.id}")
        
        # Return the chain result ID for tracking
        return result.id
    except Exception as e:
        logging.error(f"❌ Pipeline chain failed: {e}")
        import traceback
        logging.error(f"Traceback: {traceback.format_exc()}")
        raise



@app.task(bind=True, name="src.tasks.chunk_file_task", acks_late=True)
def chunk_file_task(self, file_id: int):
    """
    Task to chunk the file
    
    DB writes are now async via RabbitMQ queue to prevent connection pool exhaustion.
    """
    db = get_db()
    db_session = None
    file_record = None  # Initialize before try block to avoid UnboundLocalError
    try:
        db_session = db.get_db_session()
        file_record = db_session.query(UploadedFile).filter_by(id=file_id).first()
        if not file_record:
            logging.error(f"File {file_id} not found for chunking")
            return file_id  # Return file_id even if not found to keep chain working

        # Check if task was cancelled before starting
        raise_if_cancelled(self, file_id, db_session, "before_chunking_start")

        logging.info(f"📦 Starting chunking for file {file_id}")
        
        # 📊 Langfuse: Chunking başladı
        trace_document_processing(
            file_id=file_id,
            file_name=str(file_record.original_name) if file_record else "unknown",
            step="chunking",
            status="started",
        )
        
        # ✅ Async DB write - status update via queue
        enqueue_chunking_update(file_id, "chunking")

        # Re-use logic
        # Lazy import to avoid SIGSEGV
        from src.processing_utils import FileProcessor
        processor = FileProcessor()
        
        # Use get_event_loop() instead of new_event_loop() to avoid SIGSEGV
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        # Check cancellation before expensive processing
        raise_if_cancelled(self, file_id, db_session, "before_process_v2_chunking")
        
        try:
            # Since process_v2_chunking_batch takes a list, we can pass a list of one
            loop.run_until_complete(processor.process_v2_chunking_batch([file_record]))
        finally:
            # Don't close the loop - it might be reused
            pass
        
        # Final check after processing
        raise_if_cancelled(self, file_id, db_session, "after_chunking_complete")
        
        # 📊 Langfuse: Chunking tamamlandı
        trace_document_processing(
            file_id=file_id,
            file_name=str(file_record.original_name) if file_record else "unknown",
            step="chunking",
            status="completed",
        )
        flush_langfuse()
        
        return file_id

    except TaskCancelledException as e:
        logging.warning(f"⚠️ Chunking cancelled for file {file_id}: {e}")
        # Don't retry cancelled tasks, just return
        return file_id
    except GeminiRateLimitException as e:
        # Rate limit hit after 3 retries - mark as failed, don't retry task
        logging.error(f"🚫 Gemini API rate limit exceeded for file {file_id}: {e}")
        # ✅ Async DB write - error update via queue
        enqueue_error_update(
            file_id, 
            "chunking_status", 
            f"Gemini API rate limit exceeded: {str(e)[:500]}",
            reason="Rate limit - please try again later"
        )
        # Don't retry - rate limit means we need to wait
        return file_id
    except GeminiOCRException as e:
        # OCR failed after 3 retries - mark as failed, don't retry task
        logging.error(f"❌ Gemini OCR failed for file {file_id}: {e}")
        # ✅ Async DB write - error update via queue
        enqueue_error_update(
            file_id,
            "chunking_status",
            f"Gemini OCR failed: {str(e)[:500]}",
            reason="OCR extraction failed"
        )
        # Don't retry - OCR already retried 3 times internally
        return file_id
    except SQLAlchemyOperationalError as e:
        # PostgreSQL connection error (e.g., "too many clients")
        # Short retry with more attempts - connection issues are usually temporary
        error_msg = str(e)
        if "too many clients" in error_msg.lower():
            logging.warning(f"⚠️ PostgreSQL connection limit - will retry in 10s for file {file_id}")
            raise self.retry(exc=e, countdown=10, max_retries=10)
        else:
            logging.error(f"❌ Database connection error for file {file_id}: {e}")
            raise self.retry(exc=e, countdown=30, max_retries=5)
    except Exception as e:
        logging.error(f"❌ Chunking failed: {e}")
        # ✅ Async DB write - error update via queue
        enqueue_error_update(file_id, "chunking_status", str(e)[:500])
        raise self.retry(exc=e, countdown=60, max_retries=3)
    finally:
        if db_session:
            db_session.close()

@app.task(bind=True, name="src.tasks.create_graph_task", acks_late=True)
def create_graph_task(self, file_id: int):
    """
    Task to create graph from chunks
    
    DB writes are now async via RabbitMQ queue to prevent connection pool exhaustion.
    """
    db = get_db()
    db_session = None
    file_record = None  # Initialize before try block
    try:
        db_session = db.get_db_session()
        file_record = db_session.query(UploadedFile).filter_by(id=file_id).first()
        if not file_record:
            logging.error(f"File {file_id} not found for graph creation")
            return file_id  # Return file_id even if not found to keep chain working

        # Check if task was cancelled before starting
        raise_if_cancelled(self, file_id, db_session, "before_graph_creation_start")

        logging.info(f"🕸️ Starting graph creation for file {file_id}")
        
        # 📊 Langfuse: Graph creation başladı
        trace_document_processing(
            file_id=file_id,
            file_name=str(file_record.original_name) if file_record else "unknown",
            step="graph_creation",
            status="started",
        )
        
        # Check if chunking is completed before starting graph creation
        if file_record.chunking_status == "failed":
            logging.warning(f"⚠️ Graph creation skipped: Chunking failed for file {file_id}")
            # ✅ Async DB write - error update via queue
            enqueue_error_update(
                file_id,
                "graph_status",
                f"Graph creation skipped: Chunking failed - {file_record.processing_error or 'Unknown error'}"
            )
            return file_id
        
        # ✅ Async DB write - status update via queue
        enqueue_graph_update(file_id, "processing")

        # Re-use logic
        # Lazy import to avoid SIGSEGV
        from src.processing_utils import FileProcessor
        processor = FileProcessor()
        
        # Use get_event_loop() instead of new_event_loop() to avoid SIGSEGV
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        # Check cancellation before expensive processing
        raise_if_cancelled(self, file_id, db_session, "before_process_v2_graph_creation")
        
        try:
            loop.run_until_complete(processor.process_v2_graph_creation_batch([file_record]))
            
            # Refresh record to check final status
            db_session.refresh(file_record)
            
            # If graph creation was skipped due to chunking failure, update status
            if file_record.chunking_status == "failed":
                logging.warning(f"⚠️ Graph creation skipped due to chunking failure for file {file_id}")
                # ✅ Async DB write - error update via queue
                enqueue_error_update(
                    file_id,
                    "graph_status",
                    f"Graph creation skipped: Chunking failed - {file_record.processing_error or 'Unknown error'}"
                )
            elif file_record.graph_status == "processing":
                # If still processing, check if it actually completed
                # The processing_utils should update this, but if not, we mark as failed
                logging.warning(f"⚠️ Graph creation status still 'processing' after task completion for file {file_id}")
                # Don't change it - let processing_utils handle it
        finally:
            # Don't close the loop - it might be reused
            pass
        
        # Final check after processing
        raise_if_cancelled(self, file_id, db_session, "after_graph_creation_complete")
        
        # 🔄 Schema version'ı artır - yeni belge eklendi, şema değişmiş olabilir
        try:
            neo4j_uri = file_record.neo4j_uri or os.environ.get("NEO4J_URI")
            if neo4j_uri:
                new_version = increment_schema_version(neo4j_uri)
                logging.info(f"📈 Schema version artırıldı: {neo4j_uri} → v{new_version} (file: {file_id})")
        except Exception as schema_error:
            # Schema version artırma başarısız olsa bile işleme devam et
            logging.warning(f"⚠️ Schema version artırılamadı (file {file_id}): {schema_error}")
        
        # 📊 Langfuse: Graph creation tamamlandı
        trace_document_processing(
            file_id=file_id,
            file_name=str(file_record.original_name) if file_record else "unknown",
            step="graph_creation",
            status="completed",
        )
        flush_langfuse()
        
        # 🔬 Entity Resolution - Graph creation sonrası otomatik çalıştır (async)
        try:
            from src.entity_resolver import ENTITY_RESOLUTION_ENABLED
            if ENTITY_RESOLUTION_ENABLED:
                # Entity resolution'ı ayrı task olarak başlat (non-blocking)
                entity_resolution_task.apply_async(
                    args=[file_id, neo4j_uri],
                    countdown=5,  # 5 saniye bekle (graph commit tamamlansın)
                )
                logging.info(f"🔬 Entity resolution task queued for file {file_id}")
        except Exception as er_error:
            logging.warning(f"⚠️ Could not queue entity resolution: {er_error}")
        
        return file_id

    except TaskCancelledException as e:
        logging.warning(f"⚠️ Graph creation cancelled for file {file_id}: {e}")
        # Don't retry cancelled tasks, just return
        return file_id
    except GeminiRateLimitException as e:
        # Rate limit hit - mark as failed, don't retry
        logging.error(f"🚫 Gemini API rate limit exceeded during graph creation for file {file_id}: {e}")
        # ✅ Async DB write - error update via queue
        enqueue_error_update(
            file_id,
            "graph_status",
            f"Gemini API rate limit exceeded: {str(e)[:500]}",
            reason="Rate limit - please try again later"
        )
        return file_id
    except GeminiOCRException as e:
        # Gemini processing failed - mark as failed, don't retry
        logging.error(f"❌ Gemini processing failed during graph creation for file {file_id}: {e}")
        # ✅ Async DB write - error update via queue
        enqueue_error_update(
            file_id,
            "graph_status",
            f"Gemini processing failed: {str(e)[:500]}",
            reason="LLM processing failed"
        )
        return file_id
    except SQLAlchemyOperationalError as e:
        # PostgreSQL connection error
        error_msg = str(e)
        if "too many clients" in error_msg.lower():
            logging.warning(f"⚠️ PostgreSQL connection limit - will retry in 10s for file {file_id} (graph)")
            raise self.retry(exc=e, countdown=10, max_retries=10)
        else:
            logging.error(f"❌ Database connection error for file {file_id} (graph): {e}")
            raise self.retry(exc=e, countdown=30, max_retries=5)
    except Exception as e:
        logging.error(f"❌ Graph creation failed: {e}")
        # ✅ Async DB write - error update via queue
        enqueue_error_update(file_id, "graph_status", str(e)[:500])
        raise self.retry(exc=e, countdown=60, max_retries=3)
    finally:
        if db_session:
            db_session.close()

@app.task(bind=True, name="src.tasks.create_embeddings_task", acks_late=True)
def create_embeddings_task(self, file_id: int):
    """
    Task to create embeddings for chunks of a completed file
    Copied from backend/score.py process_embedding_creation
    
    DB writes are now async via RabbitMQ queue to prevent connection pool exhaustion.
    """
    db = get_db()
    db_session = None
    file_record = None  # Initialize before try block
    try:
        db_session = db.get_db_session()
        file_record = db_session.query(UploadedFile).filter_by(id=file_id).first()
        if not file_record:
            logging.error(f"❌ File record not found for ID: {file_id}")
            return file_id

        # Check if task was cancelled before starting
        raise_if_cancelled(self, file_id, db_session, "before_embedding_creation_start")

        logging.info(f"📊 Starting embedding creation for: {file_record.original_name}")

        try:
            # Get Neo4j credentials
            uri = file_record.neo4j_uri or os.environ.get("NEO4J_URI")
            userName = os.environ.get("NEO4J_USERNAME")
            password = os.environ.get("NEO4J_PASSWORD")
            database = file_record.neo4j_database or os.environ.get("NEO4J_DATABASE", "neo4j")

            if not all([uri, userName, password]):
                error_msg = "Neo4j credentials not configured"
                logging.error(f"❌ {error_msg}")
                # ✅ Async DB write - error update via queue
                enqueue_embedding_update(
                    file_id, "failed",
                    processing_error=error_msg,
                    reason=f"Embedding creation failed: {error_msg}"
                )
                return file_id

            # Get graph connection
            from src.shared.common_fn import create_graph_database_connection
            from src.graphDB_dataAccess import graphDBdataAccess
            from src.models.status_sync import sync_queue_db_status_to_neo4j

            # Use asyncio to run sync functions
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            # Create graph connection
            graph = loop.run_until_complete(
                asyncio.to_thread(
                    create_graph_database_connection,
                    uri, userName, password, database
                )
            )
            graphDb_data_Access = graphDBdataAccess(graph)

            # Normalize filename
            from src.utf8_utils import normalize_file_name
            normalized_filename = normalize_file_name(file_record.original_name)

            # Create embeddings for chunks of this file
            result = graphDb_data_Access.create_embeddings_for_documents(
                [normalized_filename]
            )

            if result.get("error"):
                error_msg = f"Embedding creation failed: {result['error']}"
                logging.error(f"❌ {error_msg}")

                # ✅ Async DB write - error update via queue
                enqueue_embedding_update(
                    file_id, "failed",
                    processing_error=error_msg[:500],
                    reason=f"Embedding creation failed: {result['error']}"
                )

                # Neo4j'ye failed status sync et
                try:
                    graph_connection = loop.run_until_complete(
                        asyncio.to_thread(
                            create_graph_database_connection,
                            uri, userName, password, database
                        )
                    )
                    loop.run_until_complete(
                        asyncio.to_thread(
                            sync_queue_db_status_to_neo4j,
                            graph_connection,
                            file_record.original_name,
                            file_record.upload_status,
                            file_record.chunking_status,
                            file_record.graph_status,
                            "failed",
                            database
                        )
                    )
                except Exception as sync_error:
                    logging.warning(
                        f"⚠️ Could not sync embedding failure to Neo4j: {str(sync_error)}"
                    )
                return file_id

            total_chunks = result.get("total_chunks_updated", 0)
            embedding_model = result.get("embedding_model", "Unknown")

            # ✅ Async DB write - success update via queue
            enqueue_embedding_update(
                file_id, "completed",
                status="uploaded",  # Reset status from 'processing' to 'uploaded'
                reason=f"Embedding creation completed successfully. Updated {total_chunks} chunks."
            )

            logging.info(
                f"✅ Embedding creation completed for: {file_record.original_name} - {total_chunks} chunks updated"
            )

            # Neo4j'ye completed status sync et
            try:
                logging.info(
                    f"📤 Attempting Neo4j sync for embedding completion: file_name={file_record.original_name}, "
                    f"upload_status={file_record.upload_status}, "
                    f"chunking_status={file_record.chunking_status}, "
                    f"graph_status={file_record.graph_status}, "
                    f"embedding_status=completed"
                )

                graph_connection = loop.run_until_complete(
                    asyncio.to_thread(
                        create_graph_database_connection,
                        uri, userName, password, database
                    )
                )
                loop.run_until_complete(
                    asyncio.to_thread(
                        sync_queue_db_status_to_neo4j,
                        graph_connection,
                        file_record.original_name,
                        file_record.upload_status,
                        file_record.chunking_status,
                        file_record.graph_status,
                        "completed",  # Use the value we just set
                        database
                    )
                )
            except Exception as sync_error:
                logging.warning(
                    f"⚠️ Could not sync embedding completion to Neo4j: {str(sync_error)}"
                )

        except Exception as emb_error:
            logging.error(f"❌ Embedding creation failed: {emb_error}")
            import traceback
            logging.error(f"Traceback: {traceback.format_exc()}")
            
            # ✅ Async DB write - error update via queue
            enqueue_embedding_update(
                file_id, "failed",
                processing_error=str(emb_error)[:500],
                reason=f"Embedding creation failed: {str(emb_error)}"
            )
            raise self.retry(exc=emb_error, countdown=60, max_retries=3)

        return file_id

    except TaskCancelledException as e:
        logging.warning(f"⚠️ Embedding creation cancelled for file {file_id}: {e}")
        # Don't retry cancelled tasks, just return
        return file_id
    except SQLAlchemyOperationalError as e:
        # PostgreSQL connection error
        error_msg = str(e)
        if "too many clients" in error_msg.lower():
            logging.warning(f"⚠️ PostgreSQL connection limit - will retry in 10s for file {file_id} (embedding)")
            raise self.retry(exc=e, countdown=10, max_retries=10)
        else:
            logging.error(f"❌ Database connection error for file {file_id} (embedding): {e}")
            raise self.retry(exc=e, countdown=30, max_retries=5)
    except Exception as e:
        logging.error(f"❌ Embedding creation task failed: {e}")
        import traceback
        logging.error(f"Traceback: {traceback.format_exc()}")
        # ✅ Async DB write - error update via queue
        enqueue_error_update(file_id, "embedding_status", str(e)[:500])
        raise self.retry(exc=e, countdown=60, max_retries=3)
    finally:
        if db_session:
            db_session.close()

@app.task(bind=True, name="src.tasks.delete_files_task", acks_late=True)
def delete_files_task(self, file_ids: list):
    """
    Task to delete files from queue, filesystem, and Neo4j database
    Copied from backend/score.py delete_file_background_task
    """
    db = get_db()
    deleted_count = 0
    failed_count = 0
    
    try:
        from src.shared.common_fn import create_graph_database_connection
        
        # Neo4j bağlantısını kur
        graph_connection = None
        neo4j_uri = os.environ.get("NEO4J_URI")
        neo4j_username = os.environ.get("NEO4J_USERNAME")
        neo4j_password = os.environ.get("NEO4J_PASSWORD")
        neo4j_database = os.environ.get("NEO4J_DATABASE", "neo4j")
        
        if neo4j_uri:
            try:
                graph_connection = create_graph_database_connection(
                    uri=neo4j_uri,
                    userName=neo4j_username,
                    password=neo4j_password,
                    database=neo4j_database,
                )
                logging.info(f"🔗 Neo4j connection established for deletion task")
            except Exception as conn_error:
                logging.error(f"❌ Failed to establish Neo4j connection: {str(conn_error)}")
                graph_connection = None
        
        # Process each file
        for file_id_int in file_ids:
            try:
                db_session = db.get_db_session()
                file_record = db_session.query(UploadedFile).filter_by(id=file_id_int).first()
                
                if not file_record:
                    logging.warning(f"⚠️ File not found: ID={file_id_int}")
                    failed_count += 1
                    db_session.close()
                    continue
                
                # Revoke any active Celery task for this file before deletion
                if file_record.celery_task_id:
                    try:
                        app.control.revoke(file_record.celery_task_id, terminate=True)
                        logging.info(f"🛑 Revoked Celery task {file_record.celery_task_id} for file {file_id_int}")
                        file_record.celery_task_id = None
                        db_session.commit()
                    except Exception as revoke_error:
                        logging.warning(f"⚠️ Could not revoke task: {revoke_error}")
                
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
                        session_params = {}
                        if use_database:
                            session_params["database"] = use_database
                        
                        # Step 1: Delete chunks
                        delete_chunks_query = """
                        MATCH (d:Document {fileName: $filename})<-[:PART_OF]-(c:Chunk)
                        DETACH DELETE c
                        RETURN count(c) as deletedChunks
                        """
                        chunk_result = use_connection.query(
                            delete_chunks_query,
                            {"filename": filename},
                            session_params=session_params,
                        )
                        deleted_chunks = chunk_result[0]["deletedChunks"] if chunk_result else 0
                        
                        # Step 2: Delete document-related entities (generic __Entity__ nodes)
                        delete_entities_query = """
                        MATCH (d:Document {fileName: $filename})
                        OPTIONAL MATCH (d)<-[:PART_OF]-(c:Chunk)
                        
                        // Find all __Entity__ nodes extracted from chunks
                        OPTIONAL MATCH (entity:__Entity__)-[:EXTRACTED_FROM]->(c)
                        
                        // Safety check: only delete if not connected to other documents
                        WITH d, collect(DISTINCT entity) as entities
                        WITH d, [entity IN entities WHERE entity IS NOT NULL 
                            AND NOT EXISTS {
                                MATCH (entity)-[:EXTRACTED_FROM]->(:Chunk)-[:PART_OF]->(otherDoc:Document)
                                WHERE otherDoc.fileName <> $filename
                            }] AS safeEntities
                        
                        // Delete safe entities
                        FOREACH (entity IN safeEntities | DETACH DELETE entity)
                        
                        RETURN size(safeEntities) as deletedRelated
                        """
                        entity_result = use_connection.query(
                            delete_entities_query,
                            {"filename": filename},
                            session_params=session_params,
                        )
                        deleted_entities = entity_result[0]["deletedRelated"] if entity_result and entity_result[0]["deletedRelated"] else 0
                        
                        # Step 3: Delete the Document node itself
                        delete_doc_query = """
                        MATCH (d:Document {fileName: $filename})
                        DETACH DELETE d
                        RETURN count(d) as deletedDocuments
                        """
                        result = use_connection.query(
                            delete_doc_query,
                            {"filename": filename},
                            session_params=session_params,
                        )
                        
                        # Step 4: Clean up orphan nodes (nodes with no relationships)
                        cleanup_orphans_query = """
                        MATCH (n)
                        WHERE NOT n:Document 
                          AND NOT n:Chunk 
                          AND NOT n:`__Community__`
                          AND NOT EXISTS { (n)--() }
                        DETACH DELETE n
                        RETURN count(n) as deletedOrphans
                        """
                        orphan_result = use_connection.query(cleanup_orphans_query, session_params=session_params)
                        deleted_orphans = orphan_result[0]["deletedOrphans"] if orphan_result else 0
                        
                        total_neo4j_deleted = deleted_chunks + deleted_entities + deleted_orphans + 1  # +1 for document
                        logging.info(f"🗑️ Neo4j cleanup for {original_name}: {deleted_chunks} chunks, {deleted_entities} entities, {deleted_orphans} orphans, 1 document")
                        
                        # Özel bağlantı kullanıldıysa kapat
                        if use_connection != graph_connection and hasattr(use_connection, 'close'):
                            try:
                                use_connection.close()
                            except:
                                pass

                        neo4j_deleted = True
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
                
                # Delete markdown file if exists
                if file_record.markdown_path:
                    md_path = file_record.markdown_path
                    if not os.path.isabs(md_path):
                        # Try celery_worker directory first, then backend
                        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                        celery_worker_path = os.path.join(project_root, "celery_worker", md_path)
                        backend_path = os.path.join(project_root, "backend", md_path)
                        
                        if os.path.exists(celery_worker_path):
                            md_path = celery_worker_path
                        elif os.path.exists(backend_path):
                            md_path = backend_path
                    
                    if os.path.exists(md_path):
                        try:
                            os.remove(md_path)
                            logging.info(f"🗑️ Deleted markdown file: {md_path}")
                        except Exception as md_error:
                            logging.warning(f"⚠️ Could not delete markdown file: {str(md_error)}")

                # Delete from database
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
                
            except Exception as file_error:
                logging.error(
                    f"❌ Failed to delete file {file_id_int}: {str(file_error)}"
                )
                import traceback
                logging.error(f"Traceback: {traceback.format_exc()}")
                failed_count += 1
                continue
        
        # Close Neo4j connection
        if graph_connection:
            try:
                if hasattr(graph_connection, 'close'):
                    graph_connection.close()
                logging.info(f"🔌 Neo4j connection closed")
            except Exception as close_error:
                logging.warning(f"⚠️ Error closing Neo4j connection: {str(close_error)}")
        
        logging.info(
            f"✅ Deletion task completed: {deleted_count} deleted, {failed_count} failed"
        )
        
        return {"deleted_count": deleted_count, "failed_count": failed_count}
    
    except SQLAlchemyOperationalError as e:
        # PostgreSQL connection error
        error_msg = str(e)
        if "too many clients" in error_msg.lower():
            logging.warning(f"⚠️ PostgreSQL connection limit - will retry in 10s (delete task)")
            raise self.retry(exc=e, countdown=10, max_retries=10)
        else:
            logging.error(f"❌ Database connection error (delete task): {e}")
            raise self.retry(exc=e, countdown=30, max_retries=5)
    except Exception as e:
        logging.error(f"❌ Deletion task error: {str(e)}")
        import traceback
        logging.error(f"Traceback: {traceback.format_exc()}")
        raise self.retry(exc=e, countdown=60, max_retries=3)


# ============================================================================
# LAYER 3: ENTITY RESOLUTION TASK
# ============================================================================

@app.task(bind=True, name="tasks.entity_resolution_task", max_retries=2)
def entity_resolution_task(self, file_id: int = None, neo4j_uri: str = None):
    """
    Entity resolution pipeline'ı çalıştırır.
    Graph creation sonrası benzer entity'leri tespit ve merge eder.
    
    Args:
        file_id: Processed file ID (optional, for logging)
        neo4j_uri: Neo4j connection URI
    
    Returns:
        Pipeline sonuçları
    """
    from src.shared.common_fn import create_graph_database_connection
    from src.entity_resolver import run_entity_resolution_pipeline, ENTITY_RESOLUTION_ENABLED
    
    if not ENTITY_RESOLUTION_ENABLED:
        logging.info("⏭️ Entity resolution disabled")
        return {"status": "disabled"}
    
    logging.info(f"🔬 Entity Resolution Task started (file_id: {file_id})")
    
    # Get file info if file_id provided
    file_name = None
    if file_id:
        try:
            db_session = get_file_queue_db()
            file_record = db_session.query(UploadedFile).filter(UploadedFile.id == file_id).first()
            if file_record:
                file_name = str(file_record.original_name)
                neo4j_uri = neo4j_uri or str(file_record.neo4j_uri)
            db_session.close()
        except Exception as e:
            logging.warning(f"⚠️ Could not fetch file info: {e}")
    
    # Use default Neo4j URI if not provided
    neo4j_uri = neo4j_uri or os.environ.get("NEO4J_URI")
    
    if not neo4j_uri:
        logging.error("❌ No Neo4j URI provided")
        return {"status": "error", "error": "No Neo4j URI"}
    
    try:
        # Create Neo4j connection
        graph = create_graph_database_connection(neo4j_uri)
        
        if not graph:
            logging.error("❌ Could not connect to Neo4j")
            return {"status": "error", "error": "Neo4j connection failed"}
        
        # Run pipeline
        result = run_entity_resolution_pipeline(
            graph=graph,
            file_id=file_id,
            file_name=file_name,
        )
        
        # Close connection
        if hasattr(graph, 'close'):
            graph.close()
        
        logging.info(f"✅ Entity Resolution Task completed: {result.get('summary', {})}")
        
        # Flush Langfuse
        flush_langfuse()
        
        return result
        
    except Exception as e:
        logging.error(f"❌ Entity Resolution Task error: {e}")
        import traceback
        logging.error(f"Traceback: {traceback.format_exc()}")
        raise self.retry(exc=e, countdown=60, max_retries=2)


# ============================================================================
# LAYER 6: COMMUNITY DETECTION TASK
# ============================================================================

@app.task(bind=True, name="tasks.community_detection_task", max_retries=2)
def community_detection_task(
    self,
    neo4j_uri: str = None,
    neo4j_username: str = None,
    neo4j_password: str = None,
    neo4j_database: str = None,
    model: str = "openai_gpt_4o",
):
    """
    Community detection pipeline çalıştırır.
    Neo4j GDS Leiden algoritması ile entity'leri gruplar.
    
    Args:
        neo4j_uri: Neo4j connection URI
        neo4j_username: Neo4j username
        neo4j_password: Neo4j password
        neo4j_database: Neo4j database
        model: LLM model for summary generation
    
    Returns:
        Pipeline sonuçları
    """
    logging.info("🎯 Community Detection Task started")
    
    # Get Neo4j credentials from environment if not provided
    neo4j_uri = neo4j_uri or os.environ.get("NEO4J_URI")
    neo4j_username = neo4j_username or os.environ.get("NEO4J_USERNAME")
    neo4j_password = neo4j_password or os.environ.get("NEO4J_PASSWORD")
    neo4j_database = neo4j_database or os.environ.get("NEO4J_DATABASE", "neo4j")
    
    if not neo4j_uri:
        logging.error("❌ No Neo4j URI provided")
        return {"status": "error", "error": "No Neo4j URI"}
    
    try:
        # Import communities module (may not be available in all workers)
        try:
            from src.communities import create_communities, COMMUNITY_DETECTION_ENABLED
        except ImportError:
            # Try backend path
            import sys
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'backend'))
            from src.communities import create_communities, COMMUNITY_DETECTION_ENABLED
        
        if not COMMUNITY_DETECTION_ENABLED:
            logging.info("⏭️ Community detection disabled")
            return {"status": "disabled"}
        
        # Run community detection
        result = create_communities(
            uri=neo4j_uri,
            username=neo4j_username,
            password=neo4j_password,
            database=neo4j_database,
            model=model,
        )
        
        logging.info(f"✅ Community Detection Task completed: {result.get('stats', {})}")
        
        # Flush Langfuse
        flush_langfuse()
        
        return result
        
    except Exception as e:
        logging.error(f"❌ Community Detection Task error: {e}")
        import traceback
        logging.error(f"Traceback: {traceback.format_exc()}")
        raise self.retry(exc=e, countdown=120, max_retries=2)


@app.task(bind=True, name="tasks.batch_entity_resolution_task", max_retries=1)
def batch_entity_resolution_task(self, neo4j_uri: str = None):
    """
    Tüm entity'ler için toplu resolution çalıştırır.
    Periyodik olarak veya manuel olarak çağrılabilir.
    
    Args:
        neo4j_uri: Neo4j connection URI
    
    Returns:
        Batch sonuçları
    """
    from src.shared.common_fn import create_graph_database_connection
    from src.entity_resolver import entity_resolver, ENTITY_RESOLUTION_ENABLED
    
    if not ENTITY_RESOLUTION_ENABLED:
        logging.info("⏭️ Entity resolution disabled")
        return {"status": "disabled"}
    
    neo4j_uri = neo4j_uri or os.environ.get("NEO4J_URI")
    
    if not neo4j_uri:
        logging.error("❌ No Neo4j URI provided")
        return {"status": "error", "error": "No Neo4j URI"}
    
    logging.info(f"🔬 Batch Entity Resolution started...")
    
    try:
        graph = create_graph_database_connection(neo4j_uri)
        
        if not graph:
            return {"status": "error", "error": "Neo4j connection failed"}
        
        results = {
            "entity_types": [],
            "total_merged": 0,
        }
        
        # Run resolution for __Entity__ nodes (domain-agnostic)
        entity_types = ["__Entity__"]
        
        for entity_type in entity_types:
            logging.info(f"📊 Processing {entity_type}...")
            
            try:
                clustering = entity_resolver.run_gds_similarity_clustering(graph, entity_type)
                merge = entity_resolver.auto_merge_clusters(graph)
                
                type_result = {
                    "type": entity_type,
                    "clustered": clustering.get("node_count", 0),
                    "communities": clustering.get("community_count", 0),
                    "merged": merge.get("merged_count", 0),
                }
                
                results["entity_types"].append(type_result)
                results["total_merged"] += merge.get("merged_count", 0)
                
            except Exception as e:
                logging.warning(f"⚠️ Failed for {entity_type}: {e}")
                results["entity_types"].append({
                    "type": entity_type,
                    "error": str(e),
                })
        
        # Get final stats
        results["final_stats"] = entity_resolver.get_resolution_stats(graph)
        
        if hasattr(graph, 'close'):
            graph.close()
        
        logging.info(f"✅ Batch Entity Resolution completed: {results['total_merged']} total merged")
        
        flush_langfuse()
        
        return results
        
    except Exception as e:
        logging.error(f"❌ Batch Entity Resolution error: {e}")
        import traceback
        logging.error(f"Traceback: {traceback.format_exc()}")
        raise self.retry(exc=e, countdown=120, max_retries=1)
