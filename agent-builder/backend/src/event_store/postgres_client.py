"""
PostgreSQL Event Store Client
==============================

Async connection pool for the event store database.
Singleton pattern - one pool per process.
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import asyncpg

logger = logging.getLogger(__name__)

EVENT_STORE_DSN = os.getenv(
    "EVENT_STORE_DSN",
    "postgresql://event_user:event_secret@localhost:5433/event_store",
)
EVENT_STORE_POOL_MIN = int(os.getenv("EVENT_STORE_POOL_MIN", "2"))
EVENT_STORE_POOL_MAX = int(os.getenv("EVENT_STORE_POOL_MAX", "20"))


class PostgresClient:
    """Async PostgreSQL connection pool (singleton)."""

    _instance: Optional["PostgresClient"] = None
    _lock = asyncio.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._pool: Optional[asyncpg.Pool] = None
        return cls._instance

    @property
    def is_connected(self) -> bool:
        return self._pool is not None

    async def connect(self, dsn: Optional[str] = None) -> None:
        async with self._lock:
            if self._pool is not None:
                return

            _dsn = dsn or EVENT_STORE_DSN
            logger.info("Connecting to Event Store: %s", _dsn[:40] + "...")

            try:
                self._pool = await asyncpg.create_pool(
                    _dsn,
                    min_size=EVENT_STORE_POOL_MIN,
                    max_size=EVENT_STORE_POOL_MAX,
                    command_timeout=30,
                )
                logger.info("Connected to Event Store (pool %d-%d)",
                            EVENT_STORE_POOL_MIN, EVENT_STORE_POOL_MAX)
            except Exception as e:
                logger.error("Failed to connect to Event Store: %s", e)
                self._pool = None
                raise ConnectionError(f"Event Store connection failed: {e}")

    async def disconnect(self) -> None:
        async with self._lock:
            if self._pool is not None:
                await self._pool.close()
                self._pool = None
                logger.info("Disconnected from Event Store")

    async def execute(self, query: str, *args: Any) -> str:
        if self._pool is None:
            raise RuntimeError("Not connected to Event Store")
        return await self._pool.execute(query, *args)

    async def fetch(self, query: str, *args: Any) -> List[asyncpg.Record]:
        if self._pool is None:
            raise RuntimeError("Not connected to Event Store")
        return await self._pool.fetch(query, *args)

    async def fetchrow(self, query: str, *args: Any) -> Optional[asyncpg.Record]:
        if self._pool is None:
            raise RuntimeError("Not connected to Event Store")
        return await self._pool.fetchrow(query, *args)

    async def fetchval(self, query: str, *args: Any) -> Any:
        if self._pool is None:
            raise RuntimeError("Not connected to Event Store")
        return await self._pool.fetchval(query, *args)

    async def initialize_schema(self) -> None:
        """Run schema.sql to create tables and indexes."""
        schema_path = Path(__file__).parent / "schema.sql"
        if not schema_path.exists():
            logger.warning("schema.sql not found at %s", schema_path)
            return

        sql = schema_path.read_text(encoding="utf-8")
        await self.execute(sql)
        logger.info("Event Store schema initialized")

    async def health_check(self) -> Dict[str, Any]:
        if self._pool is None:
            return {"status": "disconnected"}
        try:
            count = await self.fetchval("SELECT COUNT(*) FROM graph_events")
            return {"status": "healthy", "event_count": count}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}


_pg_client: Optional[PostgresClient] = None


async def get_postgres_client() -> PostgresClient:
    """Get the global PostgresClient instance (lazy connect)."""
    global _pg_client
    if _pg_client is None:
        _pg_client = PostgresClient()
    if not _pg_client.is_connected:
        await _pg_client.connect()
    return _pg_client
