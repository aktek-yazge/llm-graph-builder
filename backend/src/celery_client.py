import os
import logging
from celery import Celery
from typing import Optional, List

# Get broker URL from environment or use default
broker_url = os.environ.get("CELERY_BROKER_URL", "amqp://guest:guest@localhost:5672//")
result_backend = os.environ.get("CELERY_RESULT_BACKEND", "db+postgresql://postgres:postgres@localhost:5432/llm_graph_builder")

# Create a lightweight Celery app just for sending tasks
celery_app = Celery("llm_graph_builder", broker=broker_url, backend=result_backend)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_send_sent_event=True,
)


def revoke_celery_task(task_id: str, terminate: bool = False) -> bool:
    """
    Revoke (cancel) a Celery task by its ID.
    
    Args:
        task_id: The Celery task ID to revoke
        terminate: If True, terminate the task even if it's already running (SIGTERM)
                   NOTE: threads pool does NOT support terminate=True, so we default to False.
                   Running tasks should check for cancellation via raise_if_cancelled().
    
    Returns:
        True if revoke command was sent successfully, False otherwise
    """
    if not task_id:
        logging.warning("⚠️ Cannot revoke task: task_id is None or empty")
        return False
    
    try:
        # Revoke the task - this sends a message to all workers
        # NOTE: terminate=True only works with prefork pool, not threads/gevent/eventlet
        # For threads pool, tasks must check for cancellation themselves
        celery_app.control.revoke(task_id, terminate=terminate)
        logging.info(f"🛑 Celery task revoked: {task_id} (terminate={terminate})")
        return True
    except Exception as e:
        logging.error(f"❌ Failed to revoke Celery task {task_id}: {str(e)}")
        return False


def revoke_celery_tasks(task_ids: List[str], terminate: bool = False) -> dict:
    """
    Revoke multiple Celery tasks by their IDs.
    
    Args:
        task_ids: List of Celery task IDs to revoke
        terminate: If True, terminate tasks even if they're already running
    
    Returns:
        Dict with revoked_count and failed_count
    """
    revoked_count = 0
    failed_count = 0
    
    for task_id in task_ids:
        if task_id and revoke_celery_task(task_id, terminate):
            revoked_count += 1
        else:
            failed_count += 1
    
    return {"revoked_count": revoked_count, "failed_count": failed_count}


def get_task_status(task_id: str) -> Optional[str]:
    """
    Get the status of a Celery task.
    
    Args:
        task_id: The Celery task ID
    
    Returns:
        Task status string (PENDING, STARTED, SUCCESS, FAILURE, REVOKED) or None if error
    """
    if not task_id:
        return None
    
    try:
        from celery.result import AsyncResult
        result = AsyncResult(task_id, app=celery_app)
        return result.status
    except Exception as e:
        logging.error(f"❌ Failed to get task status for {task_id}: {str(e)}")
        return None
