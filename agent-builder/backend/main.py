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
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# src klasörünü path'e ekle
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.router import router as agent_builder_router
from src.ontology import get_ontology_client, initialize_ontology_db

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
    Startup'da Ontology DB bağlantısı ve schema initialization.
    """
    logger.info("🚀 Agent Builder starting...")
    
    try:
        # Ontology DB bağlantısı
        client = await get_ontology_client()
        logger.info("✅ Connected to Ontology DB")
        
        # Schema initialization (ilk çalıştırmada)
        initialized = await initialize_ontology_db()
        if initialized:
            logger.info("✅ Ontology schema initialized")
    except Exception as e:
        logger.warning(f"⚠️ Ontology DB not available: {e}")
        logger.warning("   Agent Builder will run with limited functionality")
    
    yield
    
    # Shutdown
    logger.info("🛑 Agent Builder shutting down...")


# =============================================================================
# APPLICATION
# =============================================================================

app = FastAPI(
    title="Agent Builder API",
    description="Goal-driven ve Ontology-driven agent oluşturma servisi",
    version="0.1.0",
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

# Router
app.include_router(agent_builder_router, prefix="/api/v2/agent-builder")


# =============================================================================
# ROOT ENDPOINTS
# =============================================================================

@app.get("/")
async def root():
    """API root"""
    return {
        "service": "Agent Builder",
        "version": "0.1.0",
        "docs": "/docs",
        "api": "/api/v2/agent-builder"
    }


@app.get("/health")
async def health():
    """Health check"""
    from src.ontology import get_ontology_client
    
    try:
        client = await get_ontology_client()
        health_status = await client.health_check()
        return {
            "status": "healthy",
            "ontology_db": health_status
        }
    except Exception as e:
        return {
            "status": "degraded",
            "error": str(e)
        }


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
