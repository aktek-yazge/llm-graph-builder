import os
from celery import Celery
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Get broker URL from environment or use default
broker_url = os.environ.get("CELERY_BROKER_URL", "amqp://guest:guest@localhost:5672//")
result_backend = os.environ.get("CELERY_RESULT_BACKEND", "db+sqlite:///queue.db")

app = Celery("llm_graph_builder", broker=broker_url, backend=result_backend)

# Configure Celery
app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_send_sent_event=True,
    # Task routing disabled for now - chain tasks need to be in same queue
    # task_routes={
    #     "src.tasks.create_graph_task": {"queue": "heavy_tasks"},
    #     "src.tasks.extract_images_task": {"queue": "default"},
    #     "src.tasks.chunk_file_task": {"queue": "default"},
    # },
)

# Auto-discover tasks
app.autodiscover_tasks(["src.tasks"])
