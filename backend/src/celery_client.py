import os
from celery import Celery

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
