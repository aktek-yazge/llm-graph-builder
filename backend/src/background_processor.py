# -*- coding: utf-8 -*-
"""
Background processing system for file queue
Adapts existing upload_file() logic for async queue processing
V2: Processes uploaded files automatically (chunking -> graph creation)
"""

import asyncio
import logging
import os
from typing import Optional, Dict, Any
from pathlib import Path
from datetime import datetime, timezone

from src.models.file_queue_models import get_file_queue_db, FileStatus, UploadedFile
from src.main import upload_file
from src.shared.common_fn import formatted_time, create_graph_database_connection


class BackgroundProcessor:
    """Background task processor for file queue"""

    def __init__(self):
        self.db = get_file_queue_db()
        self.is_processing = False
        self.current_task_id = None
        # Batch size from environment variable (default: 20)
        self.batch_size = int(os.environ.get("V2_BATCH_SIZE", "20"))
        # Wait time before starting processing (to allow all uploads to complete)
        self.upload_wait_time = int(
            os.environ.get("V2_UPLOAD_WAIT_TIME", "10")
        )  # seconds
        self.last_upload_check_time = None

    async def start_background_processing(self):
        """Start background processing loop"""
        if self.is_processing:
            logging.warning("Background processor already running")
            return

        self.is_processing = True
        logging.info("🚀 Background processor started")

        try:
            # Reset stuck processing files on startup
            await self.reset_stuck_processing_files()

            while self.is_processing:
                # Process V2 files first (uploaded -> chunking -> graph creation)
                await self.process_next_v2_file()
                # Then process V1 files (QUEUED -> PROCESSING -> COMPLETED)
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
        """Process next file in queue (V1: QUEUED files only, NOT V2 files)"""
        try:
            # Get next queued file
            queued_files = self.db.get_files_by_status(FileStatus.QUEUED)

            if not queued_files:
                return  # No files to process

            file_to_process = queued_files[0]  # Process oldest queued file

            # V2 dosyaları bu workflow'a girmemeli - sadece V1 dosyaları işlenmeli
            # V2 dosyaları upload_status="uploaded" olur ve process_next_v2_file() tarafından işlenir
            if (
                hasattr(file_to_process, "upload_status")
                and file_to_process.upload_status == "uploaded"
            ):
                logging.debug(
                    f"⏭️ Skipping V2 file in V1 workflow: {file_to_process.filename} (ID: {file_to_process.id})"
                )
                return  # V2 dosyası, V1 workflow'una girmemeli

            self.current_task_id = file_to_process.id

            logging.info(
                f"🔄 Starting V1 processing file: {file_to_process.filename} (ID: {file_to_process.id})"
            )

            # Update status to processing
            self.db.update_file_status(file_to_process.id, FileStatus.PROCESSING)

            # Process file using existing upload_file logic (V1 only)
            success = await self.process_file_content(file_to_process)

            if success:
                # Mark as completed
                self.db.update_file_status(file_to_process.id, FileStatus.COMPLETED, reason="Processing completed successfully")
                logging.info(
                    f"✅ File processing completed: {file_to_process.filename}"
                )
            else:
                # Mark as error
                self.db.update_file_status(
                    file_to_process.id, FileStatus.ERROR, "Processing failed", reason="Processing failed during content extraction"
                )
                logging.error(f"❌ File processing failed: {file_to_process.filename}")

        except Exception as e:
            error_message = str(e)
            logging.error(f"❌ Error processing file: {error_message}")

            if self.current_task_id:
                self.db.update_file_status(
                    self.current_task_id, FileStatus.ERROR, error_message, reason=f"Processing failed: {error_message}"
                )
        finally:
            self.current_task_id = None

    async def reset_stuck_processing_files(self):
        """Reset stuck processing files (chunking or graph creation) on startup"""
        try:
            db_session = self.db.get_db_session()
            try:
                reset_count = 0

                # Reset stuck chunking files (chunking_status == "chunking")
                stuck_chunking_files = (
                    db_session.query(UploadedFile)
                    .filter(UploadedFile.upload_status == "uploaded")
                    .filter(UploadedFile.chunking_status == "chunking")
                    .all()
                )

                for file_record in stuck_chunking_files:
                    # If chunking_status == "chunking", it means image extraction was already completed
                    # and chunking process was started but didn't finish. Reset to "ready" so chunking can continue.
                    file_record.chunking_status = "ready"
                    file_record.chunking_started_at = None
                    file_record.status = "uploaded"  # Status'u resetle (image extraction tamamlanmış, chunking'e hazır)
                    logging.info(
                        f"🔄 Reset stuck chunking file {file_record.id} ({file_record.original_name}) to ready (chunking was in progress, will continue)"
                    )
                    reset_count += 1

                # Reset stuck graph creation files (graph_status == "processing")
                stuck_graph_files = (
                    db_session.query(UploadedFile)
                    .filter(UploadedFile.upload_status == "uploaded")
                    .filter(UploadedFile.graph_status == "processing")
                    .all()
                )

                for file_record in stuck_graph_files:
                    file_record.graph_status = "pending"
                    file_record.graph_started_at = None
                    file_record.status = "uploaded"  # Status'u resetle (chunking tamamlanmış, graph creation'a hazır)
                    logging.info(
                        f"🔄 Reset stuck graph creation file {file_record.id} ({file_record.original_name}) to pending"
                    )
                    reset_count += 1

                # Reset stuck files with status == "processing" or "queued" but no active processing
                # (e.g., files that were in queue but server restarted)
                stuck_status_files = (
                    db_session.query(UploadedFile)
                    .filter(UploadedFile.upload_status == "uploaded")
                    .filter(
                        (UploadedFile.status == "processing")
                        | (UploadedFile.status == "queued")
                    )
                    .filter(UploadedFile.chunking_status != "chunking")
                    .filter(UploadedFile.chunking_status != "extracting")
                    .filter(UploadedFile.graph_status != "processing")
                    .all()
                )

                for file_record in stuck_status_files:
                    # Determine appropriate status based on chunking_status and graph_status
                    if (
                        file_record.chunking_status == "chunked"
                        and file_record.graph_status == "pending"
                    ):
                        # Chunking tamamlanmış, graph creation bekliyor
                        file_record.status = "uploaded"
                    elif file_record.chunking_status == "ready":
                        # Image extraction tamamlanmış, chunking bekliyor
                        file_record.status = "uploaded"
                    elif file_record.chunking_status == "pending":
                        # Image extraction bekliyor
                        file_record.status = "uploaded"
                    else:
                        # Default: uploaded
                        file_record.status = "uploaded"
                    logging.info(
                        f"🔄 Reset stuck status file {file_record.id} ({file_record.original_name}) status to uploaded"
                    )
                    reset_count += 1

                # Remove failed files from queue on startup (graph_status="failed" or chunking_status="failed")
                failed_graph_files = (
                    db_session.query(UploadedFile)
                    .filter(UploadedFile.upload_status == "uploaded")
                    .filter(UploadedFile.graph_status == "failed")
                    .filter(
                        (UploadedFile.status == "queued")
                        | (UploadedFile.status == "processing")
                    )
                    .all()
                )

                for file_record in failed_graph_files:
                    file_record.status = "uploaded"  # Remove from queue
                    logging.info(
                        f"🔄 Removed failed graph creation file {file_record.id} ({file_record.original_name}) from queue on startup"
                    )
                    reset_count += 1

                failed_chunking_files = (
                    db_session.query(UploadedFile)
                    .filter(UploadedFile.upload_status == "uploaded")
                    .filter(UploadedFile.chunking_status == "failed")
                    .filter(
                        (UploadedFile.status == "queued")
                        | (UploadedFile.status == "processing")
                    )
                    .all()
                )

                for file_record in failed_chunking_files:
                    file_record.status = "uploaded"  # Remove from queue
                    logging.info(
                        f"🔄 Removed failed chunking file {file_record.id} ({file_record.original_name}) from queue on startup"
                    )
                    reset_count += 1

                if reset_count > 0:
                    db_session.commit()
                    logging.info(
                        f"✅ Reset {reset_count} stuck/failed processing file(s) on startup"
                    )
                else:
                    logging.info("ℹ️ No stuck processing files found")

            finally:
                db_session.close()

        except Exception as e:
            logging.error(f"❌ Error resetting stuck processing files: {e}")

    async def _check_and_reset_stuck_files(self):
        """Check and reset stuck processing files (runs continuously during processing)"""
        try:
            db_session = self.db.get_db_session()
            try:
                from datetime import datetime, timezone, timedelta

                now = datetime.now(timezone.utc)
                reset_count = 0

                # Reset stuck chunking files that have been in "chunking" status for more than 10 minutes
                stuck_chunking_files = (
                    db_session.query(UploadedFile)
                    .filter(UploadedFile.upload_status == "uploaded")
                    .filter(UploadedFile.chunking_status == "chunking")
                    .filter(
                        (UploadedFile.chunking_started_at == None)
                        | (
                            UploadedFile.chunking_started_at
                            < now - timedelta(minutes=10)
                        )
                    )
                    .all()
                )

                for file_record in stuck_chunking_files:
                    # Reset to "ready" so chunking can be retried
                    file_record.chunking_status = "ready"
                    file_record.status = "uploaded"
                    file_record.chunking_started_at = None
                    logging.warning(
                        f"🔄 V2: Reset stuck chunking file {file_record.id} ({file_record.original_name}) - was stuck in 'chunking' status"
                    )
                    reset_count += 1

                # Reset stuck graph creation files that have been in "processing" status for more than 30 minutes
                stuck_graph_files = (
                    db_session.query(UploadedFile)
                    .filter(UploadedFile.upload_status == "uploaded")
                    .filter(UploadedFile.graph_status == "processing")
                    .filter(
                        (UploadedFile.graph_started_at == None)
                        | (UploadedFile.graph_started_at < now - timedelta(minutes=30))
                    )
                    .all()
                )

                for file_record in stuck_graph_files:
                    file_record.graph_status = "pending"
                    file_record.status = "uploaded"
                    file_record.graph_started_at = None
                    logging.warning(
                        f"🔄 V2: Reset stuck graph creation file {file_record.id} ({file_record.original_name}) - was stuck in 'processing' status"
                    )
                    reset_count += 1

                # Reset stuck extracting files that have been in "extracting" status for more than 10 minutes
                # Use updated_at as fallback since image_extraction_started_at doesn't exist in model
                stuck_extracting_files = (
                    db_session.query(UploadedFile)
                    .filter(UploadedFile.upload_status == "uploaded")
                    .filter(UploadedFile.chunking_status == "extracting")
                    .filter(
                        (UploadedFile.updated_at == None)
                        | (UploadedFile.updated_at < now - timedelta(minutes=10))
                    )
                    .all()
                )

                for file_record in stuck_extracting_files:
                    file_record.chunking_status = "pending"
                    file_record.status = "uploaded"
                    logging.warning(
                        f"🔄 V2: Reset stuck extracting file {file_record.id} ({file_record.original_name}) - was stuck in 'extracting' status"
                    )
                    reset_count += 1

                # Remove failed files from queue (graph_status="failed" but status="queued" or "processing")
                # This ensures failed files don't block other files from being processed
                failed_files_in_queue = (
                    db_session.query(UploadedFile)
                    .filter(UploadedFile.upload_status == "uploaded")
                    .filter(UploadedFile.graph_status == "failed")
                    .filter(
                        (UploadedFile.status == "queued")
                        | (UploadedFile.status == "processing")
                    )
                    .all()
                )

                for file_record in failed_files_in_queue:
                    file_record.status = "uploaded"  # Remove from queue
                    logging.info(
                        f"🔄 V2: Removed failed graph creation file {file_record.id} ({file_record.original_name}) from queue (graph_status=failed)"
                    )
                    reset_count += 1

                # Also handle failed chunking files in queue
                failed_chunking_files_in_queue = (
                    db_session.query(UploadedFile)
                    .filter(UploadedFile.upload_status == "uploaded")
                    .filter(UploadedFile.chunking_status == "failed")
                    .filter(
                        (UploadedFile.status == "queued")
                        | (UploadedFile.status == "processing")
                    )
                    .all()
                )

                for file_record in failed_chunking_files_in_queue:
                    file_record.status = "uploaded"  # Remove from queue
                    logging.info(
                        f"🔄 V2: Removed failed chunking file {file_record.id} ({file_record.original_name}) from queue (chunking_status=failed)"
                    )
                    reset_count += 1

                if reset_count > 0:
                    db_session.commit()
                    logging.info(
                        f"✅ V2: Reset {reset_count} stuck/failed processing file(s) during continuous check"
                    )

            finally:
                db_session.close()

        except Exception as e:
            logging.error(f"❌ Error checking stuck processing files: {e}")

    async def process_next_v2_file(self):
        """Process next V2 file (uploaded -> image extraction -> chunking -> graph creation)"""
        try:
            # First, check and reset any stuck processing files (runs continuously, not just on startup)
            await self._check_and_reset_stuck_files()

            db_session = self.db.get_db_session()
            try:
                from datetime import datetime, timezone, timedelta

                now = datetime.now(timezone.utc)

                # Check if there are pending files that were recently uploaded
                # This ensures we wait for all uploads to complete before starting processing
                # "pending" = upload sonrası image extraction bekliyor
                pending_files = (
                    db_session.query(UploadedFile)
                    .filter(UploadedFile.upload_status == "uploaded")
                    .filter(UploadedFile.chunking_status == "pending")
                    .all()
                )

                if pending_files:
                    # Check if the most recent upload is within wait time
                    most_recent_upload = max(pending_files, key=lambda f: f.created_at)

                    # Ensure created_at is timezone-aware (SQLite stores naive datetimes)
                    created_at = most_recent_upload.created_at
                    if created_at.tzinfo is None:
                        # If timezone-naive, assume UTC
                        created_at = created_at.replace(tzinfo=timezone.utc)

                    time_since_upload = (now - created_at).total_seconds()

                    if time_since_upload < self.upload_wait_time:
                        # Wait a bit more before starting processing
                        wait_remaining = self.upload_wait_time - time_since_upload
                        logging.info(
                            f"⏳ Waiting {wait_remaining:.1f}s for uploads to complete ({len(pending_files)} files pending) before starting processing"
                        )
                        return  # Return without processing, will check again next cycle

                # Step 1: Check for files that need image extraction (batch processing)
                # status = "queued" ve chunking_status = "pending" → kuyrukta bekliyor
                # status = "processing" ve chunking_status = "extracting" → işleniyor
                files_needing_extraction = (
                    db_session.query(UploadedFile)
                    .filter(UploadedFile.upload_status == "uploaded")
                    .filter(UploadedFile.chunking_status == "pending")
                    .filter(
                        (UploadedFile.status == "queued")
                        | (
                            UploadedFile.status == "uploaded"
                        )  # Henüz queue'ya alınmamış dosyalar
                    )
                    .order_by(UploadedFile.created_at.asc())
                    .limit(self.batch_size)
                    .all()
                )

                if files_needing_extraction:
                    # Önce tüm "pending" dosyalarını queue'ya al (status = "queued")
                    # (Eğer daha önce işaretlenmemişse)
                    all_pending_files = (
                        db_session.query(UploadedFile)
                        .filter(UploadedFile.upload_status == "uploaded")
                        .filter(UploadedFile.chunking_status == "pending")
                        .filter(
                            UploadedFile.status != "queued"
                        )  # Zaten queue'da değilse
                        .all()
                    )

                    if all_pending_files:
                        all_pending_file_ids = [f.id for f in all_pending_files]
                        db_session.query(UploadedFile).filter(
                            UploadedFile.id.in_(all_pending_file_ids)
                        ).update(
                            {
                                UploadedFile.status: "queued"
                            },  # status = "queued" ile kuyruğa al
                            synchronize_session=False,
                        )
                        db_session.commit()
                        logging.info(
                            f"📋 {len(all_pending_files)} dosya image extraction kuyruğuna alındı (status=queued, chunking_status=pending)"
                        )

                    # Şimdi batch'teki dosyaları seç (status = "queued" ve chunking_status = "pending")
                    batch_files = (
                        db_session.query(UploadedFile)
                        .filter(UploadedFile.upload_status == "uploaded")
                        .filter(UploadedFile.status == "queued")  # Queue'daki dosyalar
                        .filter(UploadedFile.chunking_status == "pending")
                        .order_by(UploadedFile.created_at.asc())
                        .limit(self.batch_size)
                        .all()
                    )

                    if not batch_files:
                        return  # No files in queue

                    # Batch'teki dosyaların status'unu "processing" ve chunking_status'unu "extracting" olarak güncelle
                    file_ids = [f.id for f in batch_files]
                    db_session.query(UploadedFile).filter(
                        UploadedFile.id.in_(file_ids)
                    ).update(
                        {
                            UploadedFile.status: "processing",  # status = "processing" ile işleme al
                            UploadedFile.chunking_status: "extracting",
                        },
                        synchronize_session=False,
                    )
                    db_session.commit()

                    logging.info(
                        f"🖼️ V2: Batch seçildi: {len(batch_files)} dosya işlenmeye başlanıyor (ID'ler: {file_ids})"
                    )

                    # Process image extraction for batch
                    await self.process_v2_image_extraction_batch(batch_files)
                    return  # Return after processing batch

                # Step 2: Check for files ready for chunking (image extraction completed) - batch processing
                # Only process files with auto_process=True
                # status = "uploaded" veya "queued" olabilir (henüz queue'ya alınmamış veya zaten queue'da)
                files_ready_for_chunking = (
                    db_session.query(UploadedFile)
                    .filter(UploadedFile.upload_status == "uploaded")
                    .filter(
                        UploadedFile.chunking_status == "ready"
                    )  # Image extraction completed
                    .filter(
                        (UploadedFile.status == "uploaded")
                        | (UploadedFile.status == "queued")
                    )  # Henüz queue'ya alınmamış veya zaten queue'da
                    .filter(
                        UploadedFile.auto_process == True
                    )  # Only auto-process files
                    .order_by(UploadedFile.created_at.asc())
                    .limit(self.batch_size)
                    .all()
                )

                if files_ready_for_chunking:
                    # Önce tüm "ready" dosyalarını queue'ya al (status = "queued")
                    all_ready_files = (
                        db_session.query(UploadedFile)
                        .filter(UploadedFile.upload_status == "uploaded")
                        .filter(UploadedFile.chunking_status == "ready")
                        .filter(
                            UploadedFile.status != "queued"
                        )  # Zaten queue'da değilse
                        .filter(UploadedFile.auto_process == True)
                        .all()
                    )

                    if all_ready_files:
                        all_ready_file_ids = [f.id for f in all_ready_files]
                        db_session.query(UploadedFile).filter(
                            UploadedFile.id.in_(all_ready_file_ids)
                        ).update(
                            {
                                UploadedFile.status: "queued"
                            },  # status = "queued" ile kuyruğa al
                            synchronize_session=False,
                        )
                        db_session.commit()
                        logging.info(
                            f"📋 {len(all_ready_files)} dosya chunking kuyruğuna alındı (status=queued, chunking_status=ready)"
                        )

                    # Şimdi batch'teki dosyaları seç (status = "queued" ve chunking_status = "ready")
                    batch_files = (
                        db_session.query(UploadedFile)
                        .filter(UploadedFile.upload_status == "uploaded")
                        .filter(UploadedFile.status == "queued")  # Queue'daki dosyalar
                        .filter(UploadedFile.chunking_status == "ready")
                        .filter(UploadedFile.auto_process == True)
                        .order_by(UploadedFile.created_at.asc())
                        .limit(self.batch_size)
                        .all()
                    )

                    if not batch_files:
                        return  # No files in queue

                    # Batch'teki dosyaların status'unu "processing" ve chunking_status'unu "chunking" olarak güncelle
                    batch_file_ids = [f.id for f in batch_files]
                    db_session.query(UploadedFile).filter(
                        UploadedFile.id.in_(batch_file_ids)
                    ).update(
                        {
                            UploadedFile.status: "processing",  # status = "processing" ile işleme al
                            UploadedFile.chunking_status: "chunking",
                            UploadedFile.chunking_started_at: datetime.now(
                                timezone.utc
                            ),
                        },
                        synchronize_session=False,
                    )
                    db_session.commit()

                    logging.info(
                        f"📦 Chunking batch seçildi: {len(batch_files)} dosya işlenmeye başlanıyor (ID'ler: {batch_file_ids})"
                    )

                    # Process chunking for batch
                    await self.process_v2_chunking_batch(batch_files)
                    return  # Return after processing batch

                # Step 3: Check for files ready for graph creation (chunking completed) - batch processing
                # Only process files with auto_process=True
                # status = "queued" ve graph_status = "pending" → graph creation kuyruğunda bekliyor
                # status = "processing" ve graph_status = "processing" → graph creation yapılıyor
                files_ready_for_graph = (
                    db_session.query(UploadedFile)
                    .filter(UploadedFile.upload_status == "uploaded")
                    .filter(UploadedFile.chunking_status == "chunked")
                    .filter(UploadedFile.graph_status == "pending")
                    .filter(
                        (UploadedFile.status == "queued")
                        | (
                            UploadedFile.status == "uploaded"
                        )  # Henüz queue'ya alınmamış dosyalar
                    )
                    .filter(
                        UploadedFile.auto_process == True
                    )  # Only auto-process files
                    .order_by(UploadedFile.created_at.asc())
                    .limit(self.batch_size)
                    .all()
                )

                if files_ready_for_graph:
                    # Önce tüm "pending" dosyalarını queue'ya al (status = "queued")
                    all_pending_files = (
                        db_session.query(UploadedFile)
                        .filter(UploadedFile.upload_status == "uploaded")
                        .filter(UploadedFile.chunking_status == "chunked")
                        .filter(UploadedFile.graph_status == "pending")
                        .filter(
                            UploadedFile.status != "queued"
                        )  # Zaten queue'da değilse
                        .filter(UploadedFile.auto_process == True)
                        .all()
                    )

                    if all_pending_files:
                        all_pending_file_ids = [f.id for f in all_pending_files]
                        db_session.query(UploadedFile).filter(
                            UploadedFile.id.in_(all_pending_file_ids)
                        ).update(
                            {
                                UploadedFile.status: "queued"
                            },  # status = "queued" ile kuyruğa al
                            synchronize_session=False,
                        )
                        db_session.commit()
                        logging.info(
                            f"📋 {len(all_pending_files)} dosya graph creation kuyruğuna alındı (status=queued, graph_status=pending)"
                        )

                    # Şimdi batch'teki dosyaları seç (status = "queued" ve graph_status = "pending")
                    batch_files = (
                        db_session.query(UploadedFile)
                        .filter(UploadedFile.upload_status == "uploaded")
                        .filter(UploadedFile.status == "queued")  # Queue'daki dosyalar
                        .filter(UploadedFile.chunking_status == "chunked")
                        .filter(UploadedFile.graph_status == "pending")
                        .filter(UploadedFile.auto_process == True)
                        .order_by(UploadedFile.created_at.asc())
                        .limit(self.batch_size)
                        .all()
                    )

                    if not batch_files:
                        return  # No files in queue

                    # Batch'teki dosyaların status'unu "processing" ve graph_status'unu "processing" olarak güncelle
                    batch_file_ids = [f.id for f in batch_files]
                    db_session.query(UploadedFile).filter(
                        UploadedFile.id.in_(batch_file_ids)
                    ).update(
                        {
                            UploadedFile.status: "processing",  # status = "processing" ile işleme al
                            UploadedFile.graph_status: "processing",
                            UploadedFile.graph_started_at: datetime.now(timezone.utc),
                        },
                        synchronize_session=False,
                    )
                    db_session.commit()

                    logging.info(
                        f"📦 Graph creation batch seçildi: {len(batch_files)} dosya işlenmeye başlanıyor (ID'ler: {batch_file_ids})"
                    )

                    # Process graph creation for batch
                    await self.process_v2_graph_creation_batch(batch_files)
                    return  # Return after processing batch

            finally:
                db_session.close()

        except Exception as e:
            error_message = str(e)
            logging.error(f"❌ Error processing V2 file: {error_message}")

            if self.current_task_id:
                db_session = self.db.get_db_session()
                try:
                    file_record = (
                        db_session.query(UploadedFile)
                        .filter_by(id=self.current_task_id)
                        .first()
                    )
                    if file_record:
                        file_record.chunking_status = "failed"
                        file_record.processing_error = error_message[:500]
                        db_session.commit()
                finally:
                    db_session.close()
        finally:
            self.current_task_id = None

    async def process_v2_image_extraction_batch(self, files: list):
        """Process image extraction for a batch of files (eş zamanlı olarak)"""
        from concurrent.futures import ThreadPoolExecutor
        from pathlib import Path
        import json

        logging.info(
            f"🖼️ V2: Starting image extraction batch for {len(files)} files (eş zamanlı)"
        )

        # Process all files concurrently using asyncio.gather
        tasks = [
            self._process_single_file_extraction(file_record) for file_record in files
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

        processed_count = len(files)
        logging.info(
            f"✅ V2: Image extraction batch completed for {processed_count} files"
        )

    async def _process_single_file_extraction(self, file_record: UploadedFile):
        """Process image extraction for a single file (used for concurrent processing)"""
        from concurrent.futures import ThreadPoolExecutor
        from pathlib import Path
        import json

        try:
            db_session = self.db.get_db_session()
            try:
                # Refresh file record
                file_record = (
                    db_session.query(UploadedFile).filter_by(id=file_record.id).first()
                )
                if not file_record:
                    return

                # Check file extension
                file_extension = Path(file_record.file_path).suffix.lower()
                if file_extension != ".pdf":
                    logging.info(
                        f"ℹ️ V2: Skipping image extraction for non-PDF file: {file_record.original_name}"
                    )
                    # Mark as ready for chunking (no images needed)
                    file_record.chunking_status = "ready"
                    db_session.commit()
                    return

                # Update status to extracting
                file_record.chunking_status = "extracting"
                db_session.commit()

                logging.info(
                    f"🖼️ V2: Starting image extraction for: {file_record.original_name} (ID: {file_record.id})"
                )

                # Get file paths
                file_path = file_record.file_path
                normalized_filename = file_record.filename

                # Create output directory structure
                from src.document_sources.s3_upload_utils import (
                    create_document_output_structure,
                )

                document_dir, pdf_dir, images_dir = create_document_output_structure(
                    normalized_filename, "output"
                )

                # Check if images already exist locally
                doc_name = Path(normalized_filename).stem
                local_images_exist = False
                local_image_files = []

                if os.path.exists(images_dir):
                    # Check for existing page images in local directory
                    for img_file in os.listdir(images_dir):
                        if img_file.startswith(
                            f"{doc_name}_page_"
                        ) and img_file.endswith(".png"):
                            local_image_files.append(os.path.join(images_dir, img_file))

                    if local_image_files:
                        local_images_exist = True
                        logging.info(
                            f"📁 V2: Found {len(local_image_files)} existing page images locally for: {file_record.original_name}"
                        )

                # Check if images exist in S3
                s3_bucket = os.environ.get(
                    "S3_BACKUP_BUCKET", "llm-graph-builder-backup"
                )
                aws_access_key_id = os.environ.get("AWS_ACCESS_KEY_ID")
                aws_secret_access_key = os.environ.get("AWS_SECRET_ACCESS_KEY")

                s3_images_exist = False
                s3_image_names = []

                if s3_bucket and aws_access_key_id and aws_secret_access_key:
                    from src.document_sources.s3_upload_utils import (
                        check_document_images_exist_in_s3,
                    )

                    s3_images_exist, s3_image_names = check_document_images_exist_in_s3(
                        doc_name, s3_bucket, aws_access_key_id, aws_secret_access_key
                    )

                    if s3_images_exist:
                        logging.info(
                            f"☁️ V2: Found {len(s3_image_names)} existing page images in S3 for: {file_record.original_name}"
                        )

                # If images exist locally or in S3, skip image generation
                if local_images_exist or s3_images_exist:
                    logging.info(
                        f"⏭️ V2: Skipping image extraction for: {file_record.original_name} (images already exist)"
                    )

                    # Use existing images
                    if local_images_exist:
                        from src.utf8_utils import normalize_file_name

                        generated_images = local_image_files
                        page_images = [
                            normalize_file_name(os.path.basename(img))
                            for img in local_image_files
                        ]
                        logging.info(
                            f"✅ V2: Using {len(page_images)} existing local page images for: {file_record.original_name}"
                        )
                    else:
                        # Images exist in S3 but not locally - download them
                        logging.info(
                            f"📥 V2: Images exist in S3 but not locally, downloading {len(s3_image_names)} images for: {file_record.original_name}"
                        )

                        # Download images from S3 to local directory
                        # Note: Use original S3 image names for download (they may not be normalized in S3)
                        loop = asyncio.get_event_loop()
                        with ThreadPoolExecutor(max_workers=1) as download_executor:
                            from src.document_sources.s3_upload_utils import (
                                download_images_from_s3,
                            )

                            def download_images():
                                return download_images_from_s3(
                                    s3_image_names,  # Use original S3 names for download
                                    s3_bucket,
                                    doc_name,
                                    images_dir,
                                    aws_access_key_id,
                                    aws_secret_access_key,
                                )

                            downloaded_images = await loop.run_in_executor(
                                download_executor, download_images
                            )

                        # Check if all images were downloaded successfully
                        # If some images failed to download (404), we need to extract locally
                        downloaded_count = (
                            len(downloaded_images) if downloaded_images else 0
                        )
                        expected_count = len(s3_image_names)

                        if downloaded_count == expected_count and downloaded_count > 0:
                            # All images downloaded successfully
                            from src.utf8_utils import normalize_file_name

                            generated_images = downloaded_images
                            # Normalize image names before saving to database
                            page_images = [
                                normalize_file_name(os.path.basename(img))
                                for img in downloaded_images
                            ]
                            logging.info(
                                f"✅ V2: Downloaded {len(page_images)} images from S3 for: {file_record.original_name}"
                            )
                        else:
                            # Some or all images failed to download - extract locally
                            logging.warning(
                                f"⚠️ V2: Failed to download all images from S3 for: {file_record.original_name} "
                                f"({downloaded_count}/{expected_count} downloaded), will extract locally from PDF"
                            )
                            # Fallback: generate new images from PDF
                            loop = asyncio.get_event_loop()
                            with ThreadPoolExecutor(max_workers=1) as image_executor:
                                from src.document_sources.local_file import (
                                    generate_page_images_with_pymupdf,
                                )

                                def gen_images():
                                    return generate_page_images_with_pymupdf(
                                        file_path, images_dir
                                    )

                                generated_images = await loop.run_in_executor(
                                    image_executor, gen_images
                                )

                            if not generated_images:
                                logging.warning(
                                    f"⚠️ V2: No images generated for: {file_record.original_name}"
                                )
                                file_record.chunking_status = (
                                    "ready"  # Ready for chunking even without images
                                )
                                db_session.commit()
                                return

                            from src.utf8_utils import normalize_file_name

                            page_images = [
                                normalize_file_name(os.path.basename(img))
                                for img in generated_images
                            ]
                            logging.info(
                                f"✅ V2: Generated {len(page_images)} new page images for: {file_record.original_name}"
                            )
                else:
                    # Generate page images with PyMuPDF
                    logging.info(
                        f"🖼️ V2: No existing images found, generating new images for: {file_record.original_name}"
                    )

                    loop = asyncio.get_event_loop()
                    with ThreadPoolExecutor(max_workers=1) as image_executor:
                        from src.document_sources.local_file import (
                            generate_page_images_with_pymupdf,
                        )

                        def gen_images():
                            return generate_page_images_with_pymupdf(
                                file_path, images_dir
                            )

                        generated_images = await loop.run_in_executor(
                            image_executor, gen_images
                        )

                    if not generated_images:
                        logging.warning(
                            f"⚠️ V2: No images generated for: {file_record.original_name}"
                        )
                        file_record.chunking_status = (
                            "ready"  # Ready for chunking even without images
                        )
                        db_session.commit()
                        return

                    logging.info(
                        f"✅ V2: Generated {len(generated_images)} page images for: {file_record.original_name}"
                    )
                    from src.utf8_utils import normalize_file_name

                    page_images = [
                        normalize_file_name(os.path.basename(img))
                        for img in generated_images
                    ]

                # S3 upload configuration
                doc_link = None

                # Only upload to S3 if images were newly generated (not if they already exist)
                if not local_images_exist and not s3_images_exist and generated_images:
                    if s3_bucket and aws_access_key_id and aws_secret_access_key:
                        logging.info(
                            f"☁️ V2: Starting S3 upload for document and {len(generated_images)} images: {file_record.original_name}"
                        )

                        loop = asyncio.get_event_loop()
                        # S3 Upload
                        with ThreadPoolExecutor(max_workers=1) as s3_executor:
                            from src.document_sources.s3_upload_utils import (
                                upload_files_to_s3_with_structure,
                            )

                            doc_name = Path(normalized_filename).stem
                            base_s3_prefix = f"documents/{doc_name}"

                            def upload_to_s3():
                                # Upload PDF to root of document folder
                                pdf_urls, pdf_failed = (
                                    upload_files_to_s3_with_structure(
                                        [file_path],
                                        s3_bucket,
                                        f"{base_s3_prefix}",
                                        aws_access_key_id,
                                        aws_secret_access_key,
                                        delete_local_after_upload=False,
                                    )
                                )

                                # Upload images to images/ subfolder
                                img_urls, img_failed = (
                                    upload_files_to_s3_with_structure(
                                        generated_images,
                                        s3_bucket,
                                        f"{base_s3_prefix}/images",
                                        aws_access_key_id,
                                        aws_secret_access_key,
                                        delete_local_after_upload=False,
                                    )
                                )

                                all_urls = pdf_urls + img_urls
                                all_failed = pdf_failed + img_failed
                                return all_urls, all_failed

                            uploaded_urls, failed_files = await loop.run_in_executor(
                                s3_executor, upload_to_s3
                            )

                        if uploaded_urls:
                            logging.info(
                                f"✅ V2: Uploaded {len(uploaded_urls)} files to S3 for: {file_record.original_name}"
                            )

                            # Extract document link
                            for url in uploaded_urls:
                                if url.endswith(f"/{normalized_filename}"):
                                    doc_link = os.path.basename(url)
                                    break

                        if failed_files:
                            logging.warning(
                                f"⚠️ V2: Failed to upload {len(failed_files)} files to S3 for: {file_record.original_name}"
                            )
                    else:
                        logging.warning(
                            f"⚠️ V2: S3 credentials not configured, keeping local images for: {file_record.original_name}"
                        )
                elif local_images_exist or s3_images_exist:
                    logging.info(
                        f"⏭️ V2: Skipping S3 upload for: {file_record.original_name} (images already exist in S3 or locally)"
                    )

                # Update file record with metadata
                file_record = (
                    db_session.query(UploadedFile).filter_by(id=file_record.id).first()
                )
                if file_record:
                    if doc_link:
                        file_record.doc_link = doc_link
                    if page_images:
                        # Use ensure_ascii=False to store Unicode characters directly (not escaped)
                        # Normalize işlemi zaten uygulanmış, sadece JSON serialization'da Unicode karakterleri koruyoruz
                        file_record.page_images = json.dumps(
                            page_images, ensure_ascii=False
                        )
                    # Mark as ready for chunking
                    file_record.chunking_status = "ready"
                    # Eğer auto_process=True ise, direkt queue'ya al (status="queued")
                    # Aksi halde status="uploaded" olarak bırak (manuel işlem için)
                    if file_record.auto_process:
                        file_record.status = "queued"
                        logging.info(
                            f"📋 V2: Image extraction completed, queued for chunking: {file_record.original_name}"
                        )
                    else:
                        file_record.status = "uploaded"
                        logging.info(
                            f"✅ V2: Image extraction completed (auto_process=False): {file_record.original_name}"
                        )
                    db_session.commit()
                    logging.info(
                        f"✅ V2: Image extraction completed for: {file_record.original_name} (ID: {file_record.id})"
                    )

            except Exception as ext_error:
                logging.error(
                    f"❌ V2: Image extraction failed for {file_record.original_name}: {str(ext_error)}"
                )
                import traceback

                logging.error(f"Traceback: {traceback.format_exc()}")
                # Mark as failed
                file_record = (
                    db_session.query(UploadedFile).filter_by(id=file_record.id).first()
                )
                if file_record:
                    file_record.chunking_status = "failed"
                    file_record.processing_error = str(ext_error)[:500]
                    # Remove from queue so it doesn't block other files
                    if file_record.status in ("queued", "processing"):
                        file_record.status = "uploaded"
                    db_session.commit()
                    logging.info(
                        f"🔄 V2: Removed failed image extraction file {file_record.id} ({file_record.original_name}) from queue"
                    )
            finally:
                db_session.close()

        except Exception as e:
            logging.error(
                f"❌ V2: Error processing image extraction for file {file_record.id}: {str(e)}"
            )
            import traceback

            logging.error(f"Traceback: {traceback.format_exc()}")

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
                from datetime import datetime, timezone

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

                # Process chunking
                await self.process_v2_chunking(file_record)

                # Refresh to check status
                db_session.refresh(file_record)
                if file_record.chunking_status == "chunked":
                    # Chunking tamamlandı, graph creation için queue'ya alınacak
                    # Status'u "uploaded" olarak bırak (graph creation queue'ya alınırken "queued" yapılacak)
                    # Sadece chunking_status="chunked" olduğundan emin ol
                    file_record.reason = "Chunking completed successfully"
                    db_session.commit()
                    logging.info(
                        f"✅ V2: Chunking completed for: {file_record.original_name} (ID: {file_record.id}), will be queued for graph creation"
                    )
                else:
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
        try:
            # Import process_chunking_v2 dynamically to avoid circular import
            # We'll import it from the score module at runtime
            import sys
            import importlib

            # Try to import from score module (it's already loaded)
            if "score" in sys.modules:
                score_module = sys.modules["score"]
            else:
                # If not loaded, import it
                import score as score_module

            logging.info(
                f"📖 V2: Starting chunking for: {file_record.original_name} (ID: {file_record.id})"
            )

            # Call process_chunking_v2
            await score_module.process_chunking_v2(
                file_id=file_record.id,
                original_name=file_record.original_name,
                merged_file_path=file_record.file_path,
            )

            logging.info(
                f"✅ V2: Chunking completed for: {file_record.original_name} (ID: {file_record.id})"
            )

        except Exception as e:
            logging.error(
                f"❌ V2: Chunking failed for {file_record.original_name}: {str(e)}"
            )
            raise

    async def process_v2_graph_creation(
        self,
        file_record: UploadedFile,
        model: str = None,
        generate_embedding: bool = False,
    ):
        """Process V2 graph creation for a file (after chunking)"""
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

            # Check if markdown exists
            if not file_record.markdown_path or not os.path.exists(
                file_record.markdown_path
            ):
                logging.warning(
                    f"⚠️ V2: Markdown file not found, skipping graph creation for: {file_record.original_name}"
                )
                return

            # Import process_graph_creation_v2 dynamically to avoid circular import
            import sys
            import importlib

            # Try to import from score module (it's already loaded)
            if "score" in sys.modules:
                score_module = sys.modules["score"]
            else:
                # If not loaded, import it
                import score as score_module

            logging.info(
                f"🎨 V2: Starting graph creation for: {file_record.original_name} (ID: {file_record.id}), Model: {model}"
            )

            # Status zaten batch seçiminde "processing" olarak güncellenmiş
            # Model, uri gibi bilgiler de batch seçiminde güncellenmiş
            # Sadece graph_started_at güncelle (eğer yoksa)
            db_session = self.db.get_db_session()
            try:
                file_record = (
                    db_session.query(UploadedFile).filter_by(id=file_record.id).first()
                )
                if file_record and not file_record.graph_started_at:
                    file_record.graph_started_at = datetime.now(timezone.utc)
                    db_session.commit()
            finally:
                db_session.close()

            # Call process_graph_creation_v2 asynchronously in background
            # This prevents blocking the server during long-running LLM operations
            asyncio.create_task(
                score_module.process_graph_creation_v2(
                    file_id=file_record.id,
                    original_name=file_record.original_name,
                    markdown_path=file_record.markdown_path,
                    file_path=file_record.file_path,
                    model=model,  # Use provided model
                    uri=uri,
                    userName=userName,
                    password=password,
                    database=database,
                    generate_embedding=generate_embedding,  # Use provided generate_embedding
                )
            )

            logging.info(
                f"✅ V2: Graph creation started in background for: {file_record.original_name} (ID: {file_record.id})"
            )

        except Exception as e:
            logging.error(
                f"❌ V2: Graph creation failed for {file_record.original_name}: {str(e)}"
            )
            raise

    async def process_v2_graph_creation_batch(self, files: list):
        """Process graph creation for a batch of files (eş zamanlı olarak)"""
        logging.info(
            f"🎨 V2: Starting graph creation batch for {len(files)} files (eş zamanlı)"
        )

        # Process all files concurrently using asyncio.gather
        tasks = [
            self._process_single_file_graph_creation(file_record)
            for file_record in files
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

        logging.info(f"✅ V2: Graph creation batch completed for {len(files)} files")

    async def _process_single_file_graph_creation(self, file_record: UploadedFile):
        """Process graph creation for a single file (used for concurrent processing)"""
        try:
            db_session = self.db.get_db_session()
            try:
                # Refresh file record
                file_record = (
                    db_session.query(UploadedFile).filter_by(id=file_record.id).first()
                )
                if not file_record:
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

                # Process graph creation asynchronously in background
                # This prevents blocking the server during long-running LLM operations
                # The status will be updated by process_graph_creation_v2 when it completes
                # Model ve generate_embedding parametrelerini geç
                asyncio.create_task(
                    self.process_v2_graph_creation(
                        file_record, model=model, generate_embedding=generate_embedding
                    )
                )

                logging.info(
                    f"✅ V2: Graph creation started in background for: {file_record.original_name} (ID: {file_record.id})"
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
            # If username/password not in file_record, will use environment variables
            graph = create_graph_database_connection(
                file_record.neo4j_uri,
                None,  # username - will use NEO4J_USERNAME from env if None
                None,  # password - will use NEO4J_PASSWORD from env if None
                file_record.neo4j_database,
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
            # FastAPI UploadFile has a 'file' attribute that is a file-like object
            class MockFile:
                """File-like object that wraps a file path"""

                def __init__(self, file_path):
                    self.file_path = file_path
                    self._file = None

                def read(self):
                    if self._file is None:
                        self._file = open(self.file_path, "rb")
                    return self._file.read()

                def close(self):
                    if self._file:
                        self._file.close()
                        self._file = None

            class MockUploadFile:
                """Mock FastAPI UploadFile for background processing"""

                def __init__(self, file_path):
                    self.file_path = file_path
                    self.filename = Path(file_path).name
                    self.file = MockFile(
                        file_path
                    )  # FastAPI UploadFile has 'file' attribute
                    # Get file size for size attribute
                    try:
                        self.size = os.path.getsize(file_path)
                    except OSError:
                        self.size = 0

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
                generate_embedding=file_record.generate_embedding or "false",
            )

            # Check if processing was successful
            if result and "Success" in str(result):
                logging.info(
                    f"✅ upload_file completed successfully for: {file_record.filename}"
                )
                return True
            else:
                logging.error(
                    f"❌ upload_file returned error for: {file_record.filename} - Result: {result}"
                )
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
            "queue_stats": self.db.get_queue_stats(),
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
            db.update_file_status(file_id, FileStatus.COMPLETED, reason="Immediate processing completed successfully")
        else:
            db.update_file_status(
                file_id, FileStatus.ERROR, "Immediate processing failed", reason="Immediate processing failed during content extraction"
            )

        return success

    except Exception as e:
        logging.error(f"❌ Immediate processing failed for file {file_id}: {e}")
        return False
