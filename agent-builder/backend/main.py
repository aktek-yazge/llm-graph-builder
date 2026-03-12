"""
Agent Builder - Standalone FastAPI Service
==========================================

Goal-driven ve Ontology-driven agent oluşturma servisi.

Bu servis ana LLM Graph Builder'dan bağımsız çalışabilir.
Port: 8001 (default)

Kullanım:
---------
    cd /workspace/agent-builder/backend
    uvicorn main:app --host 0.0.0.0 --port 8001 --reload

Veya ana projeye entegre:
---------
    # score.py'de sys.path'e ekleyerek import edilir
"""

import logging
import os
import sys

from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

# src klasörünü path'e ekle
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.router import router as agent_builder_router
from src.workspace_router import router as workspace_router
from src.event_router import router as event_router
from src.comms_router import router as comms_router
from src.dashboard_router import router as dashboard_router
from src.resource_router import router as resource_router
from src.chat_agent_router import router as chat_agent_router
from src.chat_agent_router import orchestrator_router
from src.agent.evolving.router import router as evolving_router
from src.ontology import get_ontology_client, initialize_ontology_db
from src.gateway import get_gateway_client
from src.event_store import get_postgres_client

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# =============================================================================
# LIFESPAN
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan handler.
    PostgreSQL is the primary metadata store; Neo4j is for KB graph only.
    """
    logger.info("Agent Builder starting...")

    pg = None

    # 1. PostgreSQL — primary metadata store (must succeed)
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
        logger.error("PostgreSQL not available: %s — service cannot start properly", e)

    # 2. Neo4j — KB graph store (optional at startup)
    try:
        client = await get_ontology_client()
        logger.info("Connected to Neo4j (KB graph store)")
        initialized = await initialize_ontology_db()
        if initialized:
            logger.info("Neo4j KB schema initialized")
    except Exception as e:
        logger.warning("Neo4j not available: %s (KB graph features limited)", e)

    # 3. MCP Gateway (optional)
    try:
        gateway = await get_gateway_client()
        health = await gateway.health_check()
        logger.info("Connected to MCP Gateway: %s", health.get("status", "ok"))
    except Exception as e:
        logger.warning("MCP Gateway not available: %s", e)

    # 4. Agent Event Bus (optional)
    try:
        from src.agent.event_bus import AgentEventBus
        bus = AgentEventBus.get_instance()
        connected = await bus.connect_rabbitmq()
        if connected:
            logger.info("Agent Event Bus connected to RabbitMQ")
        else:
            logger.info("Agent Event Bus running in-process mode")
    except Exception as e:
        logger.warning("Agent Event Bus init warning: %s", e)

    # 5. AgentRegistry — manages SelfEvolvingAgent instances
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


# =============================================================================
# APPLICATION
# =============================================================================

app = FastAPI(
    title="Agent Builder API",
    description="Goal-driven ve Ontology-driven agent oluşturma servisi",
    version="0.2.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Production'da kısıtla
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(agent_builder_router, prefix="/api/v2/agent-builder")
app.include_router(evolving_router, prefix="/api/v2/evolving")
app.include_router(workspace_router)
app.include_router(event_router)
app.include_router(comms_router)
app.include_router(dashboard_router)
app.include_router(resource_router)
app.include_router(chat_agent_router)
app.include_router(orchestrator_router)


# =============================================================================
# ROOT ENDPOINTS
# =============================================================================

@app.get("/")
async def root():
    """API root"""
    return {
        "service": "Agent Builder",
        "version": "0.2.0",
        "docs": "/docs",
        "api": "/api/v2/agent-builder"
    }


@app.get("/health")
async def health():
    """Health check"""
    from src.ontology import get_ontology_client

    status = "healthy"
    result: dict = {}

    try:
        client = await get_ontology_client()
        result["ontology_db"] = await client.health_check()
    except Exception as e:
        status = "degraded"
        result["ontology_db"] = {"status": "unavailable", "error": str(e)}

    try:
        pg = await get_postgres_client()
        result["event_store"] = await pg.health_check()
    except Exception as e:
        result["event_store"] = {"status": "unavailable", "error": str(e)}

    return {"status": status, **result}


# =============================================================================
# MAIN
# =============================================================================

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
