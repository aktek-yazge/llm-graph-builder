import hashlib
import logging
from datetime import datetime
from typing import Any, Dict, Tuple

from neo4j import AsyncGraphDatabase

logger = logging.getLogger("mcp_services.connection_pool")


class ConnectionPoolManager:
    """
    Multi-tenant Neo4j connection pool manager.
    Caches drivers per unique db_url+username+password combination.
    """

    def __init__(self, max_idle_seconds: int = 3600):
        self._pools: Dict[str, Tuple[Any, datetime]] = {}
        self._max_idle_seconds = max_idle_seconds

    def _get_key(self, db_url: str, username: str, password: str) -> str:
        return hashlib.sha256(f"{db_url}:{username}:{password}".encode()).hexdigest()[:16]

    async def get_driver(self, db_url: str, username: str, password: str) -> Any:
        key = self._get_key(db_url, username, password)

        if key in self._pools:
            driver, _ = self._pools[key]
            self._pools[key] = (driver, datetime.now())
            return driver

        driver = AsyncGraphDatabase.driver(
            db_url,
            auth=(username, password),
            max_connection_pool_size=50,
            connection_acquisition_timeout=60,
            max_connection_lifetime=3600,
        )
        self._pools[key] = (driver, datetime.now())
        logger.info("New connection pool for %s (total: %d)", db_url[:30], len(self._pools))
        return driver

    async def cleanup_idle(self):
        now = datetime.now()
        to_remove = []
        for key, (driver, last_used) in self._pools.items():
            if (now - last_used).total_seconds() > self._max_idle_seconds:
                try:
                    await driver.close()
                except Exception as e:
                    logger.warning("Error closing idle driver: %s", e)
                to_remove.append(key)
        for key in to_remove:
            del self._pools[key]

    async def close_all(self):
        for _, (driver, _) in self._pools.items():
            try:
                await driver.close()
            except Exception as e:
                logger.warning("Error closing driver: %s", e)
        self._pools.clear()


pool_manager = ConnectionPoolManager()
