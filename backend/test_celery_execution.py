import os
import time
from celery.result import AsyncResult
from src.celery_client import celery_app

# Set env vars to match local setup
os.environ["CELERY_BROKER_URL"] = "amqp://guest:guest@localhost:5672//"
os.environ["CELERY_RESULT_BACKEND"] = "db+postgresql://postgres:postgres@localhost:5432/llm_graph_builder"

def test_task():
    print("🚀 Sending test task...")
    # Send a task with a dummy file_id using send_task
    result = celery_app.send_task("src.tasks.process_file_pipeline", args=[99999])
    print(f"✅ Task sent! ID: {result.id}")
    
    print("⏳ Waiting for result...")
    # Wait a bit
    time.sleep(2)
    
    # Check status
    print(f"📊 Task Status: {result.status}")
    
    if result.status == "FAILURE":
        print(f"❌ Task Failed: {result.result}")
    elif result.status == "SUCCESS":
        print(f"✅ Task Success: {result.result}")
    else:
        print(f"🔄 Task is {result.status}")

if __name__ == "__main__":
    test_task()
