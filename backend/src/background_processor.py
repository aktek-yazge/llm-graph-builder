# -*- coding: utf-8 -*-
"""
Background processing system for file queue
Adapts existing upload_file() logic for async queue processing
"""

import asyncio
import logging
from typing import Optional, Dict, Any
from pathlib import Path
from datetime import datetime

from src.models.file_queue_models import get_file_queue_db, FileStatus, UploadedFile
from src.main import upload_file, create_graph_database_connection
from src.shared.common_fn import formatted_time


class BackgroundProcessor:
    """Background task processor for file queue"""
    
    def __init__(self):
        self.db = get_file_queue_db()
        self.is_processing = False
        self.current_task_id = None
        
    async def start_background_processing(self):
        """Start background processing loop"""
        if self.is_processing:
            logging.warning("Background processor already running")
            return
            
        self.is_processing = True
        logging.info("🚀 Background processor started")
        
        try:
            while self.is_processing:
                await self.process_next_file()
                await asyncio.sleep(2)  # Check every 2 seconds
                
        except Exception as e:
            logging.error(f"❌ Background processor error: {e}")
        finally:
            self.is_processing = False
            logging.info("⏹️ Background processor stopped")
    
    def stop_background_processing(self):
        """Stop background processing"""
        self.is_processing = False
        
    async def process_next_file(self):
        """Process next file in queue"""
        try:
            # Get next queued file
            queued_files = self.db.get_files_by_status(FileStatus.QUEUED)
            
            if not queued_files:
                return  # No files to process
                
            file_to_process = queued_files[0]  # Process oldest queued file
            self.current_task_id = file_to_process.id
            
            logging.info(f"🔄 Starting processing file: {file_to_process.filename} (ID: {file_to_process.id})")
            
            # Update status to processing
            self.db.update_file_status(file_to_process.id, FileStatus.PROCESSING)
            
            # Process file using existing upload_file logic
            success = await self.process_file_content(file_to_process)
            
            if success:
                # Mark as completed
                self.db.update_file_status(file_to_process.id, FileStatus.COMPLETED)
                logging.info(f"✅ File processing completed: {file_to_process.filename}")
            else:
                # Mark as error
                self.db.update_file_status(file_to_process.id, FileStatus.ERROR, "Processing failed")
                logging.error(f"❌ File processing failed: {file_to_process.filename}")
                
        except Exception as e:
            error_message = str(e)
            logging.error(f"❌ Error processing file: {error_message}")
            
            if self.current_task_id:
                self.db.update_file_status(
                    self.current_task_id, 
                    FileStatus.ERROR, 
                    error_message
                )
        finally:
            self.current_task_id = None
    
    async def process_file_content(self, file_record: UploadedFile) -> bool:
        """
        Process file content using adapted upload_file logic
        Returns True if successful, False otherwise
        """
        try:
            # Validate file exists
            file_path = Path(file_record.file_path)
            if not file_path.exists():
                logging.error(f"File not found: {file_path}")
                return False
            
            # Create graph database connection
            graph = create_graph_database_connection(
                file_record.neo4j_uri,
                "", "", # username/password handled in connection
                file_record.neo4j_database
            )
            
            if not graph:
                logging.error("Failed to create graph database connection")
                return False
            
            # Prepare file for processing - move to merged directory
            merged_dir = Path(__file__).parent.parent / "merged_files"
            merged_dir.mkdir(exist_ok=True)
            
            merged_file_path = merged_dir / file_record.filename
            
            # Copy file to merged directory (upload_file expects it there)
            import shutil
            shutil.copy2(file_path, merged_file_path)
            logging.info(f"📁 File copied to processing directory: {merged_file_path}")
            
            # Create a mock UploadFile object for upload_file function
            class MockUploadFile:
                def __init__(self, file_path):
                    self.file_path = file_path
                    self.filename = Path(file_path).name
                    
                def read(self):
                    with open(self.file_path, 'rb') as f:
                        return f.read()
            
            mock_file = MockUploadFile(merged_file_path)
            
            # Use asyncio.to_thread for CPU-intensive upload_file operation
            result = await asyncio.to_thread(
                upload_file,
                graph=graph,
                model=file_record.model_used or "openai_gpt_4o_mini",
                chunk=mock_file,
                chunk_number=1,  # Single file (already merged)
                total_chunks=1,
                originalname=file_record.filename,
                uri=file_record.neo4j_uri,
                chunk_dir=str(merged_dir / "chunks"),  # Won't be used for single file
                merged_dir=str(merged_dir),
                generate_embedding=file_record.generate_embedding or "false"
            )
            
            # Check if processing was successful
            if result and "Success" in str(result):
                logging.info(f"✅ upload_file completed successfully for: {file_record.filename}")
                return True
            else:
                logging.error(f"❌ upload_file returned error for: {file_record.filename} - Result: {result}")
                return False
                
        except Exception as e:
            logging.error(f"❌ Error in process_file_content: {e}")
            import traceback
            logging.error(traceback.format_exc())
            return False
    
    def get_processing_status(self) -> Dict[str, Any]:
        """Get current processing status"""
        return {
            "is_processing": self.is_processing,
            "current_task_id": self.current_task_id,
            "queue_stats": self.db.get_queue_stats()
        }


# Global processor instance
_processor_instance = None

def get_background_processor() -> BackgroundProcessor:
    """Get global background processor instance"""
    global _processor_instance
    
    if _processor_instance is None:
        _processor_instance = BackgroundProcessor()
    
    return _processor_instance

async def start_processing_loop():
    """Start the background processing loop"""
    processor = get_background_processor()
    await processor.start_background_processing()

def stop_processing_loop():
    """Stop the background processing loop"""
    processor = get_background_processor()
    processor.stop_background_processing()

# Manual processing function for immediate use
async def process_file_immediately(file_id: int) -> bool:
    """
    Process a specific file immediately (bypass queue)
    Returns True if successful
    """
    try:
        db = get_file_queue_db()
        file_record = db.get_file_by_id(file_id)
        
        if not file_record:
            logging.error(f"File not found: {file_id}")
            return False
            
        processor = BackgroundProcessor()
        processor.current_task_id = file_id
        
        # Update to processing status
        db.update_file_status(file_id, FileStatus.PROCESSING)
        
        # Process the file
        success = await processor.process_file_content(file_record)
        
        # Update final status
        if success:
            db.update_file_status(file_id, FileStatus.COMPLETED)
        else:
            db.update_file_status(file_id, FileStatus.ERROR, "Immediate processing failed")
            
        return success
        
    except Exception as e:
        logging.error(f"❌ Immediate processing failed for file {file_id}: {e}")
        return False