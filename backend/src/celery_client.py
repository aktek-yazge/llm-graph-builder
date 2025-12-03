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


def purge_all_queues() -> dict:
    """
    Purge (clear) all messages from RabbitMQ queues.
    This removes all pending tasks that haven't been picked up by workers yet.
    
    Returns:
        Dict with purged message count or error info
    """
    try:
        # Method 1: Use Celery's built-in purge (clears default queue)
        purged_count = celery_app.control.purge()
        logging.info(f"🧹 Purged {purged_count} messages from Celery queues")
        
        return {
            "success": True,
            "purged_count": purged_count,
            "message": f"Successfully purged {purged_count} pending tasks from queues"
        }
    except Exception as e:
        error_msg = str(e)
        logging.error(f"❌ Failed to purge queues: {error_msg}")
        return {
            "success": False,
            "purged_count": 0,
            "message": f"Failed to purge queues: {error_msg}"
        }


def purge_specific_queue(queue_name: str = "celery") -> dict:
    """
    Purge a specific RabbitMQ queue by name.
    
    Args:
        queue_name: Name of the queue to purge (default: "celery")
    
    Returns:
        Dict with purged message count or error info
    """
    try:
        from kombu import Connection
        
        with Connection(broker_url) as conn:
            channel = conn.channel()
            # queue_purge returns the number of messages deleted
            message_count = channel.queue_purge(queue_name)
            logging.info(f"🧹 Purged {message_count} messages from queue '{queue_name}'")
            
            return {
                "success": True,
                "queue_name": queue_name,
                "purged_count": message_count,
                "message": f"Successfully purged {message_count} messages from '{queue_name}'"
            }
    except Exception as e:
        error_msg = str(e)
        logging.error(f"❌ Failed to purge queue '{queue_name}': {error_msg}")
        return {
            "success": False,
            "queue_name": queue_name,
            "purged_count": 0,
            "message": f"Failed to purge queue: {error_msg}"
        }


def get_queue_stats() -> dict:
    """
    Get statistics about RabbitMQ queues.
    
    Returns:
        Dict with queue statistics
    """
    try:
        # Use Celery's inspect to get active queues and tasks
        inspect = celery_app.control.inspect()
        
        active_tasks = inspect.active() or {}
        reserved_tasks = inspect.reserved() or {}
        scheduled_tasks = inspect.scheduled() or {}
        
        total_active = sum(len(tasks) for tasks in active_tasks.values())
        total_reserved = sum(len(tasks) for tasks in reserved_tasks.values())
        total_scheduled = sum(len(tasks) for tasks in scheduled_tasks.values())
        
        return {
            "success": True,
            "active_tasks": total_active,      # Currently executing
            "reserved_tasks": total_reserved,  # Fetched but not yet executing
            "scheduled_tasks": total_scheduled, # Waiting in queue
            "workers": list(active_tasks.keys()),
            "message": f"Active: {total_active}, Reserved: {total_reserved}, Scheduled: {total_scheduled}"
        }
    except Exception as e:
        error_msg = str(e)
        logging.error(f"❌ Failed to get queue stats: {error_msg}")
        return {
            "success": False,
            "message": f"Failed to get queue stats: {error_msg}"
        }
