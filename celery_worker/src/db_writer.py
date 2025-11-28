"""
PostgreSQL DB Write Queue - Async database writes via RabbitMQ

This module implements a dedicated task for writing to PostgreSQL,
reducing connection pool pressure and ensuring data persistence
even if the main worker crashes.

Features:
- Single persistent connection (no pool exhaustion)
- Batch commits for better performance
- acks_late=True for guaranteed delivery
- Automatic retry on failure
"""

import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from sqlalchemy.exc import OperationalError, IntegrityError

from src.celery_app import app
from src.models.file_queue_models import get_file_queue_db, UploadedFile

logger = logging.getLogger(__name__)

# Singleton DB instance for the writer
_db_instance = None


def get_writer_db():
    """Get or create singleton DB instance for writer"""
    global _db_instance
    if _db_instance is None:
        _db_instance = get_file_queue_db()
    return _db_instance


@app.task(
    bind=True,
    name="src.db_writer.db_write_task",
    acks_late=True,  # Don't ACK until task completes successfully
    reject_on_worker_lost=True,  # Requeue if worker dies
    max_retries=10,
    default_retry_delay=5,
)
def db_write_task(self, file_id: int, updates: Dict[str, Any]):
    """
    Write updates to PostgreSQL for a single file.
    
    Args:
        file_id: The ID of the file to update
        updates: Dictionary of field names and values to update
        
    Example:
        db_write_task.delay(123, {
            "chunking_status": "completed",
            "chunking_completed_at": "2024-01-01T00:00:00Z"
        })
    """
    db = get_writer_db()
    db_session = None
    
    try:
        db_session = db.get_db_session()
        
        file_record = db_session.query(UploadedFile).filter_by(id=file_id).first()
        if not file_record:
            logger.warning(f"⚠️ DB Write: File {file_id} not found, skipping update")
            return {"status": "skipped", "reason": "file_not_found"}
        
        # Apply updates
        updated_fields = []
        for field, value in updates.items():
            if hasattr(file_record, field):
                # Handle datetime strings
                if field.endswith("_at") and isinstance(value, str):
                    try:
                        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
                    except ValueError:
                        pass
                
                setattr(file_record, field, value)
                updated_fields.append(field)
            else:
                logger.warning(f"⚠️ DB Write: Unknown field '{field}' for file {file_id}")
        
        # Commit changes
        db_session.commit()
        
        logger.info(f"✅ DB Write: Updated file {file_id} - fields: {updated_fields}")
        return {"status": "success", "file_id": file_id, "updated_fields": updated_fields}
        
    except OperationalError as e:
        logger.error(f"❌ DB Write: Connection error for file {file_id}: {e}")
        if db_session:
            db_session.rollback()
        # Retry with exponential backoff
        raise self.retry(exc=e, countdown=min(5 * (2 ** self.request.retries), 60))
        
    except IntegrityError as e:
        logger.error(f"❌ DB Write: Integrity error for file {file_id}: {e}")
        if db_session:
            db_session.rollback()
        # Don't retry integrity errors
        return {"status": "failed", "reason": "integrity_error", "error": str(e)}
        
    except Exception as e:
        logger.error(f"❌ DB Write: Error updating file {file_id}: {e}")
        if db_session:
            db_session.rollback()
        raise self.retry(exc=e, countdown=10)
        
    finally:
        if db_session:
            db_session.close()


@app.task(
    bind=True,
    name="src.db_writer.db_batch_write_task",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=10,
    default_retry_delay=5,
)
def db_batch_write_task(self, operations: List[Dict[str, Any]]):
    """
    Write multiple updates to PostgreSQL in a single transaction.
    
    Args:
        operations: List of operations, each containing:
            - file_id: The ID of the file to update
            - updates: Dictionary of field names and values
            
    Example:
        db_batch_write_task.delay([
            {"file_id": 123, "updates": {"status": "completed"}},
            {"file_id": 456, "updates": {"status": "failed", "error": "..."}}
        ])
    """
    if not operations:
        return {"status": "skipped", "reason": "no_operations"}
    
    db = get_writer_db()
    db_session = None
    
    try:
        db_session = db.get_db_session()
        
        results = []
        for op in operations:
            file_id = op.get("file_id")
            updates = op.get("updates", {})
            
            if not file_id:
                results.append({"file_id": None, "status": "skipped", "reason": "no_file_id"})
                continue
            
            file_record = db_session.query(UploadedFile).filter_by(id=file_id).first()
            if not file_record:
                results.append({"file_id": file_id, "status": "skipped", "reason": "not_found"})
                continue
            
            # Apply updates
            updated_fields = []
            for field, value in updates.items():
                if hasattr(file_record, field):
                    # Handle datetime strings
                    if field.endswith("_at") and isinstance(value, str):
                        try:
                            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
                        except ValueError:
                            pass
                    
                    setattr(file_record, field, value)
                    updated_fields.append(field)
            
            results.append({"file_id": file_id, "status": "updated", "fields": updated_fields})
        
        # Single commit for all operations
        db_session.commit()
        
        success_count = sum(1 for r in results if r["status"] == "updated")
        logger.info(f"✅ DB Batch Write: Updated {success_count}/{len(operations)} files")
        
        return {"status": "success", "results": results}
        
    except OperationalError as e:
        logger.error(f"❌ DB Batch Write: Connection error: {e}")
        if db_session:
            db_session.rollback()
        raise self.retry(exc=e, countdown=min(5 * (2 ** self.request.retries), 60))
        
    except Exception as e:
        logger.error(f"❌ DB Batch Write: Error: {e}")
        if db_session:
            db_session.rollback()
        raise self.retry(exc=e, countdown=10)
        
    finally:
        if db_session:
            db_session.close()


# ============================================================================
# Helper functions for enqueueing writes from other tasks
# ============================================================================

def enqueue_db_write(file_id: int, updates: Dict[str, Any], priority: int = 5):
    """
    Enqueue a database write operation.
    
    Args:
        file_id: The ID of the file to update
        updates: Dictionary of field names and values to update
        priority: Task priority (0-9, lower is higher priority)
        
    Returns:
        AsyncResult for the enqueued task
    """
    # Convert datetime objects to ISO strings for JSON serialization
    serializable_updates = {}
    for key, value in updates.items():
        if isinstance(value, datetime):
            serializable_updates[key] = value.isoformat()
        else:
            serializable_updates[key] = value
    
    return db_write_task.apply_async(
        args=[file_id, serializable_updates],
        priority=priority,
        queue="db_write",  # Dedicated queue for DB writes
    )


def enqueue_db_batch_write(operations: List[Dict[str, Any]], priority: int = 5):
    """
    Enqueue a batch database write operation.
    
    Args:
        operations: List of operations
        priority: Task priority
        
    Returns:
        AsyncResult for the enqueued task
    """
    # Convert datetime objects to ISO strings
    serializable_ops = []
    for op in operations:
        serializable_updates = {}
        for key, value in op.get("updates", {}).items():
            if isinstance(value, datetime):
                serializable_updates[key] = value.isoformat()
            else:
                serializable_updates[key] = value
        serializable_ops.append({
            "file_id": op["file_id"],
            "updates": serializable_updates
        })
    
    return db_batch_write_task.apply_async(
        args=[serializable_ops],
        priority=priority,
        queue="db_write",
    )


# Convenience functions for common updates
def enqueue_status_update(file_id: int, status: str, **extra_fields):
    """Update file status"""
    updates = {"status": status, **extra_fields}
    return enqueue_db_write(file_id, updates)


def enqueue_chunking_update(file_id: int, chunking_status: str, **extra_fields):
    """Update chunking status"""
    updates = {"chunking_status": chunking_status}
    if chunking_status == "chunking":
        updates["chunking_started_at"] = datetime.now(timezone.utc)
    elif chunking_status in ("completed", "failed"):
        updates["chunking_completed_at"] = datetime.now(timezone.utc)
    updates.update(extra_fields)
    return enqueue_db_write(file_id, updates)


def enqueue_graph_update(file_id: int, graph_status: str, **extra_fields):
    """Update graph status"""
    updates = {"graph_status": graph_status}
    if graph_status == "processing":
        updates["graph_started_at"] = datetime.now(timezone.utc)
    elif graph_status in ("completed", "failed"):
        updates["graph_completed_at"] = datetime.now(timezone.utc)
    updates.update(extra_fields)
    return enqueue_db_write(file_id, updates)


def enqueue_embedding_update(file_id: int, embedding_status: str, **extra_fields):
    """Update embedding status"""
    updates = {"embedding_status": embedding_status}
    if embedding_status == "processing":
        updates["embedding_started_at"] = datetime.now(timezone.utc)
    elif embedding_status in ("completed", "failed"):
        updates["embedding_completed_at"] = datetime.now(timezone.utc)
    updates.update(extra_fields)
    return enqueue_db_write(file_id, updates)


def enqueue_error_update(file_id: int, status_field: str, error_message: str, reason: str = None):
    """Update with error information"""
    updates = {
        status_field: "failed",
        "processing_error": error_message[:500] if error_message else None,
    }
    if reason:
        updates["reason"] = reason
    return enqueue_db_write(file_id, updates, priority=3)  # Higher priority for errors

