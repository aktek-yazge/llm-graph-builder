"""
Agent Builder - Standalone FastAPI Service
==========================================

Self-Evolving Agent platform.
deepagents + MultiServerMCPClient + Celery pipeline.

Port: 8001 (default)

Kullanim:
    cd /workspace/agent-builder/backend
    uvicorn main:app --host 0.0.0.0 --port 8001 --reload
"""

import logging
import os
import sys

from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.agent.evolving.router import router as evolving_router
from src.event_store import get_postgres_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Agent Builder starting...")

    pg = None

    # 1. PostgreSQL -- primary metadata store
    try:
        pg = await get_postgres_client()
        await pg.initialize_schema()
        es_health = await pg.health_check()
        logger.info("PostgreSQL connected: %s", es_health.get("status", "ok"))

        from src.agent.evolving.knowledge_store import KnowledgeStore
        ks = KnowledgeStore(pg)
        await ks.ensure_table()
        logger.info("agent_knowledge table ensured")
    except Exception as e:
        logger.error("PostgreSQL not available: %s -- service cannot start properly", e)

    # 2. AgentRegistry -- manages SelfEvolvingAgent instances
    registry = None
    if pg is not None:
        try:
            celery_app = None
            try:
                from celery import Celery
                celery_broker = os.getenv("CELERY_BROKER_URL", "amqp://rabbitmq:RabbitMQ!654*@localhost:5672//")
                celery_app = Celery("evolving_agent", broker=celery_broker)
            except Exception:
                logger.warning("Celery not available for AgentRegistry")

            from src.agent.evolving.agent_registry import AgentRegistry
            registry = AgentRegistry(pg=pg, celery_app=celery_app)
            await registry.reload_all()
            app.state.agent_registry = registry
            logger.info("AgentRegistry initialized")
        except Exception as e:
            logger.warning("AgentRegistry init failed: %s", e)

    yield

    logger.info("Agent Builder shutting down...")

    if registry is not None:
        try:
            await registry.shutdown()
        except Exception:
            pass

    try:
        pg = await get_postgres_client()
        await pg.disconnect()
    except Exception:
        pass


app = FastAPI(
    title="Agent Builder API",
    description="Self-Evolving Agent platform - deepagents + Celery pipeline",
    version="0.3.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(evolving_router, prefix="/api/v2/evolving")


@app.get("/")
async def root():
    return {
        "service": "Agent Builder",
        "version": "0.3.0",
        "docs": "/docs",
        "api": "/api/v2/evolving",
    }


@app.get("/health")
async def health():
    status = "healthy"
    result: dict = {}

    try:
        pg = await get_postgres_client()
        result["postgres"] = await pg.health_check()
    except Exception as e:
        status = "degraded"
        result["postgres"] = {"status": "unavailable", "error": str(e)}

    registry = getattr(app.state, "agent_registry", None)
    if registry:
        agent_ids = await registry.list_agent_ids()
        result["agents"] = {"count": len(agent_ids), "ids": agent_ids[:10]}
    else:
        result["agents"] = {"count": 0, "status": "registry not initialized"}

    return {"status": status, **result}


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("AGENT_BUILDER_PORT", "8001"))
    host = os.getenv("AGENT_BUILDER_HOST", "0.0.0.0")

    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=True,
        log_level="info"
    )
