import os
import logging
import signal
import sys
from celery import Celery
from celery.signals import worker_init, worker_shutdown, worker_process_init, task_failure, celeryd_after_setup
from kombu import Queue, Exchange
from dotenv import load_dotenv

# Configure logging for worker events
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Load environment variables from .env file
load_dotenv()

# Get broker URL from environment or use default
broker_url = os.environ.get("CELERY_BROKER_URL", "amqp://guest:guest@localhost:5672//")
result_backend = os.environ.get("CELERY_RESULT_BACKEND", "db+sqlite:///queue.db")

app = Celery("llm_graph_builder", broker=broker_url, backend=result_backend)

# Define exchanges
default_exchange = Exchange("default", type="direct")
db_exchange = Exchange("db_write", type="direct")
neo4j_exchange = Exchange("neo4j_write", type="direct")
workspace_exchange = Exchange("workspace", type="direct")
resource_exchange = Exchange("resource", type="direct")

# Configure Celery
app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_send_sent_event=True,
    
    # Fix for Celery 6.0 deprecation warning
    broker_connection_retry_on_startup=True,
    
    # Prevent stdout reentrant errors with threads pool
    worker_redirect_stdouts=False,  # Don't redirect stdout (prevents reentrant issues)
    
    # Define queues
    task_queues=(
        Queue("celery", default_exchange, routing_key="celery"),
        Queue("db_write", db_exchange, routing_key="db_write"),
        Queue("neo4j_write", neo4j_exchange, routing_key="neo4j_write"),
        Queue("workspace", workspace_exchange, routing_key="workspace"),
        Queue("resource", resource_exchange, routing_key="resource"),
    ),
    
    # Default queue
    task_default_queue="celery",
    task_default_exchange="default",
    task_default_routing_key="celery",
    
    # Task routing - DB and Neo4j writes go to dedicated queues
    task_routes={
        "src.db_writer.db_write_task": {"queue": "db_write"},
        "src.db_writer.db_batch_write_task": {"queue": "db_write"},
        "src.neo4j_writer.neo4j_write_task": {"queue": "neo4j_write"},
        "src.neo4j_writer.neo4j_batch_write_task": {"queue": "neo4j_write"},
        # Main tasks stay in default queue
        "src.tasks.*": {"queue": "celery"},
        # Agent Builder skill processing task
        "celery_worker.src.tasks.skill_processing.process_file_with_skill": {"queue": "celery"},
        # Workspace document processing tasks (hybrid + MCP)
        "workspace.*": {"queue": "workspace"},
        "resource.*": {"queue": "resource"},
    },
    
    # Worker prefetch - reduce for DB writer to ensure ordered processing
    # Main workers can prefetch more
    worker_prefetch_multiplier=4,
)

# Auto-discover tasks from all modules
app.autodiscover_tasks(["src.tasks", "src.db_writer", "src.neo4j_writer", "src.workspace_tasks", "src.resource_tasks"])


# ============================================================================
# WORKER SIGNAL HANDLERS - Monitor worker lifecycle events
# ============================================================================

@worker_init.connect
def on_worker_init(sender=None, **kwargs):
    """Called when worker starts initializing"""
    worker_name = sender.hostname if sender else "unknown"
    logger.info(f"🚀 WORKER INIT: {worker_name} starting...")
    logger.info(f"   PID: {os.getpid()}")
    logger.info(f"   Queues: {sender.app.conf.task_queues if sender else 'N/A'}")


@worker_process_init.connect
def on_worker_process_init(sender=None, **kwargs):
    """Called when worker process is forked (for prefork pool)"""
    logger.info(f"🔄 WORKER PROCESS INIT: PID={os.getpid()}")


@worker_shutdown.connect
def on_worker_shutdown(sender=None, **kwargs):
    """Called when worker is shutting down"""
    worker_name = sender.hostname if sender else "unknown"
    logger.warning(f"🛑 WORKER SHUTDOWN: {worker_name} is shutting down!")
    logger.warning(f"   PID: {os.getpid()}")
    logger.warning(f"   This may indicate a crash, SIGTERM, or manual stop.")


@celeryd_after_setup.connect
def on_celeryd_after_setup(sender=None, instance=None, **kwargs):
    """Called after worker setup is complete"""
    worker_name = instance.hostname if instance else "unknown"
    logger.info(f"✅ WORKER READY: {worker_name}")
    logger.info(f"   Pool: {instance.pool_cls if instance else 'N/A'}")
    logger.info(f"   Concurrency: {instance.concurrency if instance else 'N/A'}")


@task_failure.connect
def on_task_failure(sender=None, task_id=None, exception=None, traceback=None, **kwargs):
    """Called when a task fails with an exception"""
    task_name = sender.name if sender else "unknown"
    logger.error(f"❌ TASK FAILURE: {task_name}")
    logger.error(f"   Task ID: {task_id}")
    logger.error(f"   Exception: {exception}")
    if traceback:
        logger.error(f"   Traceback:\n{traceback}")


# System signal handlers for unexpected shutdowns
def signal_handler(signum, frame):
    """Handle system signals (SIGTERM, SIGINT, etc.)"""
    signal_name = signal.Signals(signum).name
    logger.warning(f"⚠️ RECEIVED SIGNAL: {signal_name} ({signum})")
    logger.warning(f"   PID: {os.getpid()}")
    logger.warning(f"   Frame: {frame.f_code.co_filename}:{frame.f_lineno}" if frame else "N/A")


# Register signal handlers
try:
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGHUP, signal_handler)
except (ValueError, OSError) as e:
    # May fail in non-main thread
    logger.debug(f"Could not register signal handlers: {e}")
