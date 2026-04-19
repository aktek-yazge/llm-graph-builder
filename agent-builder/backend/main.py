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
from src.agent.auth.router import router as auth_router
from src.agent.platform_router import router as platform_router
from src.event_store import get_postgres_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def _run_migrations(pg) -> None:
    """
    Run all schema migrations step-by-step.

    Each statement runs in its own implicit transaction so a single
    failure doesn't block the rest.  ``IF NOT EXISTS`` / ``IF EXISTS``
    keeps everything idempotent.
    """
    from src.agent.evolving.knowledge_store import KnowledgeStore
    from src.agent.evolving.wiki_store import WIKI_MIGRATION_SQL

    # 1. Run schema.sql — each statement separately
    schema_path = os.path.join(os.path.dirname(__file__), "src", "event_store", "schema.sql")
    if os.path.exists(schema_path):
        with open(schema_path, encoding="utf-8") as f:
            raw_sql = f.read()

        lines = [l for l in raw_sql.splitlines() if not l.strip().startswith("--")]
        cleaned = "\n".join(lines)
        statements = [s.strip() for s in cleaned.split(";") if s.strip()]
        for stmt in statements:
            try:
                await pg.execute(stmt)
            except Exception as exc:
                logger.warning("Migration statement skipped: %.80s... -> %s", stmt, exc)

    # 2. Ensure agent_knowledge (KnowledgeStore's own migration)
    try:
        ks = KnowledgeStore(pg)
        await ks.ensure_table()
        logger.info("Migration: agent_knowledge table ensured")
    except Exception as exc:
        logger.warning("Migration: agent_knowledge skipped: %s", exc)

    # 3. Wiki full-text search & log indexes
    for stmt in [s.strip() for s in WIKI_MIGRATION_SQL.split(";") if s.strip()]:
        try:
            await pg.execute(stmt)
        except Exception as exc:
            logger.warning("Migration: wiki index skipped: %.60s... -> %s", stmt, exc)
    logger.info("Migration: wiki indexes ensured")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Agent Builder starting...")

    pg = None

    # 1. PostgreSQL connection
    try:
        pg = await get_postgres_client()
        es_health = await pg.health_check()
        logger.info("PostgreSQL connected: %s", es_health.get("status", "ok"))
    except Exception as e:
        logger.error("PostgreSQL unavailable: %s — service will start degraded", e)

    # 2. Migrations (idempotent, per-statement)
    if pg is not None:
        try:
            await _run_migrations(pg)
            logger.info("All migrations completed")
        except Exception as e:
            logger.error("Migration error: %s", e)

    app.state.pg = pg

    # 3. AgentRegistry
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

            safety_interval = int(os.getenv("RESUME_SAFETY_INTERVAL_SEC", "300"))
            if safety_interval > 0:
                registry.start_safety_net(interval_sec=safety_interval)

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
app.include_router(auth_router, prefix="/api/v2/auth")
app.include_router(platform_router, prefix="/api/v2/platform")


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
