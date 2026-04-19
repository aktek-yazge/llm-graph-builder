# -*- coding: utf-8 -*-
"""
Queue Manager - High-level operations for file upload queue system
Integrates with existing graphDBdataAccess and provides additional functionality
"""

import os
import logging
from typing import List, Optional, Dict, Any, Tuple
from pathlib import Path
from datetime import datetime

from src.models.file_queue_models import get_file_queue_db, FileStatus, UploadedFile
from src.graphDB_dataAccess import graphDBdataAccess
from src.shared.common_fn import create_graph_database_connection


class QueueManager:
    """High-level queue management operations"""

    def __init__(self):
        self.db = get_file_queue_db()

    def check_file_duplicate(
        self, file_path: str, filename: str
    ) -> Optional[UploadedFile]:
        """
        Check if file is duplicate based on hash and filename
        Returns existing file record if duplicate found
        """
        try:
            # Calculate file hash
            file_hash = UploadedFile.calculate_file_hash(file_path)
            if not file_hash:
                return None

            # Check for duplicate by hash
            existing_file = self.db.get_file_by_hash(file_hash)
            if existing_file:
                logging.info(
                    f"🔍 Duplicate file detected: {filename} matches existing {existing_file.filename} (ID: {existing_file.id})"
                )
                return existing_file

            return None

        except Exception as e:
            logging.error(f"❌ Error checking file duplicate: {e}")
            return None

    def check_processed_file_exists(self, filename: str, neo4j_uri: str = None) -> bool:
        """
        Check if file already exists in output directory (processed files)
        This prevents re-processing of already completed files
        """
        try:
            # Check in output directory (from env or default to output_celery for Celery volume mount)
            output_dir_name = os.environ.get("OUTPUT_DIR", "output_celery")
            output_dir = Path(__file__).parent.parent / output_dir_name

            # Look for exact filename or variations
            for output_file in output_dir.rglob(f"*{filename}*"):
                if output_file.is_file():
                    logging.info(f"📁 Processed file already exists: {output_file}")
                    return True

            # Also check in Neo4j database if URI provided
            if neo4j_uri:
                return self.check_file_in_neo4j(filename, neo4j_uri)

            return False

        except Exception as e:
            logging.error(f"❌ Error checking processed file: {e}")
            return False

    def check_file_in_neo4j(
        self,
        filename: str,
        neo4j_uri: str,
        username: str = "",
        password: str = "",
        database: str = "neo4j",
    ) -> bool:
        """
        Check if file data already exists in Neo4j database
        """
        try:
            graph = create_graph_database_connection(
                neo4j_uri, username, password, database
            )
            if not graph:
                return False

            graph_access = graphDBdataAccess(graph)

            # Check if document exists in graph
            # This is a simplified check - you might want to customize based on your schema
            query = """
            MATCH (d:Document)
            WHERE d.fileName CONTAINS $filename OR d.name CONTAINS $filename
            RETURN count(d) as doc_count
            """

            result = graph.query(query, {"filename": filename})
            doc_count = result[0]["doc_count"] if result else 0

            if doc_count > 0:
                logging.info(
                    f"📊 File already exists in Neo4j: {filename} ({doc_count} documents)"
                )
                return True

            return False

        except Exception as e:
            logging.error(f"❌ Error checking file in Neo4j: {e}")
            return False

    def add_file_with_checks(
        self,
        filename: str,
        original_name: str,
        file_path: str,
        file_size: int = None,
        neo4j_uri: str = None,
        neo4j_database: str = None,
        model: str = None,
        generate_embedding: str = None,
        override_duplicate: bool = False,
    ) -> Tuple[UploadedFile, bool, str]:
        """
        Add file to queue with comprehensive duplicate checking

        Returns:
            (file_record, is_duplicate, message)
        """
        try:
            # Check for duplicate upload
            duplicate_file = self.check_file_duplicate(file_path, filename)
            if duplicate_file and not override_duplicate:
                return duplicate_file, True, "File already uploaded to queue"

            # Check for already processed file
            if (
                self.check_processed_file_exists(filename, neo4j_uri)
                and not override_duplicate
            ):
                # Still add to queue but with completed status
                file_record = self.db.add_file(
                    filename=filename,
                    original_name=original_name,
                    file_path=file_path,
                    file_size=file_size,
                    neo4j_uri=neo4j_uri,
                    neo4j_database=neo4j_database,
                    model=model,
                    generate_embedding=generate_embedding,
                )

                # Mark as completed since it's already processed
                self.db.update_file_status(file_record.id, FileStatus.COMPLETED)

                return file_record, True, "File already processed - marked as completed"

            # Add new file
            file_record = self.db.add_file(
                filename=filename,
                original_name=original_name,
                file_path=file_path,
                file_size=file_size,
                neo4j_uri=neo4j_uri,
                neo4j_database=neo4j_database,
                model=model,
                generate_embedding=generate_embedding,
            )

            return file_record, False, "File added successfully"

        except Exception as e:
            logging.error(f"❌ Error adding file with checks: {e}")
            raise e

    def get_queue_summary(self) -> Dict[str, Any]:
        """Get comprehensive queue summary"""
        try:
            stats = self.db.get_queue_stats()

            # Get recent files (last 10)
            recent_files = self.db.get_all_files(limit=10, offset=0)

            # Get files by status
            queued_files = self.db.get_files_by_status(FileStatus.QUEUED)
            processing_files = self.db.get_files_by_status(FileStatus.PROCESSING)
            error_files = self.db.get_files_by_status(FileStatus.ERROR)

            return {
                "stats": stats,
                "recent_files": [
                    {
                        "id": f.id,
                        "filename": f.filename,
                        "status": f.status,
                        "created_at": f.created_at.isoformat(),
                        "file_size": f.file_size,
                    }
                    for f in recent_files
                ],
                "queued_count": len(queued_files),
                "processing_count": len(processing_files),
                "error_count": len(error_files),
                "oldest_queued": (
                    queued_files[0].created_at.isoformat() if queued_files else None
                ),
                "current_processing": (
                    processing_files[0].filename if processing_files else None
                ),
            }

        except Exception as e:
            logging.error(f"❌ Error getting queue summary: {e}")
            return {"error": str(e)}

    def cleanup_old_files(
        self, days_old: int = 30, status_filter: List[FileStatus] = None
    ) -> int:
        """
        Clean up old files from queue and filesystem

        Args:
            days_old: Files older than this many days
            status_filter: Only cleanup files with these statuses (default: completed, error)

        Returns:
            Number of files cleaned up
        """
        try:
            if status_filter is None:
                status_filter = [FileStatus.COMPLETED, FileStatus.ERROR]

            from datetime import timedelta

            cutoff_date = datetime.utcnow() - timedelta(days=days_old)

            cleaned_count = 0

            for status in status_filter:
                files = self.db.get_files_by_status(status)

                for file_record in files:
                    if file_record.created_at < cutoff_date:
                        # Delete file from filesystem
                        file_path = Path(file_record.file_path)
                        if file_path.exists():
                            file_path.unlink()
                            logging.info(f"🗑️ Deleted old file: {file_path}")

                        # Delete from database
                        if self.db.delete_file(file_record.id):
                            cleaned_count += 1
                            logging.info(
                                f"✅ Cleaned up old file record: {file_record.filename}"
                            )

            logging.info(f"🧹 Cleanup completed: {cleaned_count} files removed")
            return cleaned_count

        except Exception as e:
            logging.error(f"❌ Error during cleanup: {e}")
            return 0

    def retry_failed_files(self, max_retries: int = 3) -> int:
        """
        Retry failed files by moving them back to queued status

        Returns:
            Number of files queued for retry
        """
        try:
            error_files = self.db.get_files_by_status(FileStatus.ERROR)
            retry_count = 0

            for file_record in error_files:
                # Check retry logic - you might want to add retry_count to database schema
                # For now, just retry all error files

                # Reset status to queued
                success = self.db.update_file_status(file_record.id, FileStatus.QUEUED)
                if success:
                    retry_count += 1
                    logging.info(f"🔄 Queued for retry: {file_record.filename}")

            logging.info(f"🔄 {retry_count} files queued for retry")
            return retry_count

        except Exception as e:
            logging.error(f"❌ Error retrying failed files: {e}")
            return 0

    def validate_queue_integrity(self) -> Dict[str, Any]:
        """
        Validate queue integrity - check for missing files, inconsistent states, etc.
        """
        try:
            issues = []
            all_files = self.db.get_all_files(
                limit=1000
            )  # Get more files for validation

            missing_files = 0
            size_mismatches = 0

            for file_record in all_files:
                file_path = Path(file_record.file_path)

                # Check if file exists
                if not file_path.exists():
                    issues.append(
                        f"Missing file: {file_record.filename} (ID: {file_record.id})"
                    )
                    missing_files += 1
                    continue

                # Check file size consistency
                actual_size = file_path.stat().st_size
                if (
                    file_record.file_size
                    and abs(actual_size - file_record.file_size) > 100
                ):  # Allow small differences
                    issues.append(
                        f"Size mismatch: {file_record.filename} - Expected: {file_record.file_size}, Actual: {actual_size}"
                    )
                    size_mismatches += 1

            return {
                "total_files_checked": len(all_files),
                "missing_files": missing_files,
                "size_mismatches": size_mismatches,
                "issues": issues[:20],  # Return first 20 issues
                "total_issues": len(issues),
            }

        except Exception as e:
            logging.error(f"❌ Error validating queue integrity: {e}")
            return {"error": str(e)}


# Global queue manager instance
_queue_manager_instance = None


def get_queue_manager() -> QueueManager:
    """Get global queue manager instance"""
    global _queue_manager_instance

    if _queue_manager_instance is None:
        _queue_manager_instance = QueueManager()

    return _queue_manager_instance
