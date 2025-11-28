import os
from celery import Celery
from kombu import Queue, Exchange
from dotenv import load_dotenv

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

# Configure Celery
app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_send_sent_event=True,
    
    # Define queues
    task_queues=(
        Queue("celery", default_exchange, routing_key="celery"),
        Queue("default", default_exchange, routing_key="default"),
        Queue("db_write", db_exchange, routing_key="db_write"),
        Queue("neo4j_write", neo4j_exchange, routing_key="neo4j_write"),
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
    },
    
    # Worker prefetch - reduce for DB writer to ensure ordered processing
    # Main workers can prefetch more
    worker_prefetch_multiplier=4,
)

# Auto-discover tasks from all modules
app.autodiscover_tasks(["src.tasks", "src.db_writer", "src.neo4j_writer"])
