import logging
import asyncio
from datetime import datetime, timezone
from src.celery_app import app
from src.models.file_queue_models import get_file_queue_db, FileStatus, UploadedFile
from src.processing_utils import FileProcessor
# Ideally we should import the logic directly, but for now let's copy the necessary parts or import helper functions
# To avoid circular imports and keep it clean, I will re-implement the core logic here using the existing helper functions
from src.document_sources.s3_upload_utils import create_document_output_structure
from src.create_chunks import CreateChunksofDocument
from src.main import extract_graph_from_file_local_file
from src.shared.common_fn import create_graph_database_connection
from pathlib import Path
import os

# Helper to get DB session
def get_db():
    return get_file_queue_db()

@app.task(bind=True, name="src.tasks.process_file_pipeline")
def process_file_pipeline(self, file_id: int):
    """
    Orchestrator task that triggers the pipeline:
    Image Extraction -> Chunking -> Graph Creation
    """
    logging.info(f"🚀 Starting pipeline for file {file_id}")
    
    # Chain the tasks
    # 1. Extract Images
    # 2. Chunk File (depends on images)
    # 3. Create Graph (depends on chunks)
    
    # We use 'link' to chain them. 
    # Note: We need to pass file_id to each.
    
    chain = (
        extract_images_task.s(file_id) |
        chunk_file_task.s(file_id) |
        create_graph_task.s(file_id)
    )
    
    chain.apply_async()
    logging.info(f"🔗 Pipeline chained for file {file_id}")

@app.task(bind=True, name="src.tasks.extract_images_task")
def extract_images_task(self, file_id: int):
    """
    Task to extract images from PDF
    """
    db = get_db()
    db_session = db.get_db_session()
    try:
        file_record = db_session.query(UploadedFile).filter_by(id=file_id).first()
        if not file_record:
            logging.error(f"File {file_id} not found")
            return

        logging.info(f"🖼️ Starting image extraction for file {file_id}")
        
        # Update status
        file_record.chunking_status = "extracting"
        file_record.status = "processing"
        db_session.commit()

        # Logic from background_processor.py (simplified)
        # In a real migration, we would move the logic to a shared utility
        # For now, I'll instantiate the processor just to use its method if possible, 
        # OR better, re-implement the specific extraction logic here to avoid the class overhead.
        
        # Re-implementing core extraction logic:
        from src.processing_utils import FileProcessor
        processor = FileProcessor()
        
        # We need to run the async method in this sync task
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(processor._process_single_file_extraction(file_record))
        loop.close()
        
        # Check if successful (status should be updated by the method or we update it here)
        # The _process_single_file_extraction method updates the DB.
        
        # Refresh record
        db_session.refresh(file_record)
        if file_record.chunking_status == "ready":
             logging.info(f"✅ Image extraction completed for file {file_id}")
             return file_id # Pass to next task
        else:
             # If it failed or didn't finish, we might raise an error
             # But _process_single_file_extraction handles errors gracefully usually
             return file_id

    except Exception as e:
        logging.error(f"❌ Image extraction failed: {e}")
        # Update DB
        if file_record:
            file_record.chunking_status = "failed"
            file_record.processing_error = str(e)
            db_session.commit()
        raise self.retry(exc=e, countdown=60, max_retries=3)
    finally:
        db_session.close()

@app.task(bind=True, name="src.tasks.chunk_file_task")
def chunk_file_task(self, file_id: int):
    """
    Task to chunk the file
    """
    db = get_db()
    db_session = db.get_db_session()
    try:
        file_record = db_session.query(UploadedFile).filter_by(id=file_id).first()
        if not file_record:
            return

        logging.info(f"📦 Starting chunking for file {file_id}")
        
        file_record.chunking_status = "chunking"
        file_record.chunking_started_at = datetime.now(timezone.utc)
        db_session.commit()

        # Re-use logic
        from src.processing_utils import FileProcessor
        processor = FileProcessor()
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        # We need to adapt process_v2_chunking_batch to single file or create a new method
        # Let's use a helper that processes a single file
        
        # Since process_v2_chunking_batch takes a list, we can pass a list of one
        loop.run_until_complete(processor.process_v2_chunking_batch([file_record]))
        loop.close()
        
        return file_id

    except Exception as e:
        logging.error(f"❌ Chunking failed: {e}")
        if file_record:
            file_record.chunking_status = "failed"
            file_record.processing_error = str(e)
            db_session.commit()
        raise self.retry(exc=e, countdown=60, max_retries=3)
    finally:
        db_session.close()

@app.task(bind=True, name="src.tasks.create_graph_task")
def create_graph_task(self, file_id: int):
    """
    Task to create graph from chunks
    """
    db = get_db()
    db_session = db.get_db_session()
    try:
        file_record = db_session.query(UploadedFile).filter_by(id=file_id).first()
        if not file_record:
            return

        logging.info(f"🕸️ Starting graph creation for file {file_id}")
        
        file_record.graph_status = "processing"
        file_record.graph_started_at = datetime.now(timezone.utc)
        db_session.commit()

        # Re-use logic
        from src.processing_utils import FileProcessor
        processor = FileProcessor()
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(processor.process_v2_graph_creation_batch([file_record]))
        loop.close()
        
        return file_id

    except Exception as e:
        logging.error(f"❌ Graph creation failed: {e}")
        if file_record:
            file_record.graph_status = "failed"
            file_record.processing_error = str(e)
            db_session.commit()
        raise self.retry(exc=e, countdown=60, max_retries=3)
    finally:
        db_session.close()
