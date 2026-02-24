"""
Neo4j Ontology DB Client
========================

Bu modül Neo4j Ontology veritabanı ile bağlantı ve temel operasyonları sağlar.

ÖNEMLI NOTLAR:
--------------
- Bu client ONTOLOGY DB içindir, Documents DB değil!
- Ontology DB: Goals, Skills, Schemas, Agents, Learnings
- Documents DB: Document, Chunk, Entity (tenant-specific belgeler)

Kullanım:
---------
    from backend.src.agent_builder.ontology import OntologyDBClient
    
    client = OntologyDBClient()
    await client.connect()
    
    # Schema oluştur (ilk kurulumda)
    await client.initialize_schema()
    
    # Sorgu çalıştır
    result = await client.execute_query(
        "MATCH (s:Skill) WHERE s.is_global = true RETURN s"
    )
"""

import asyncio
import hashlib
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from neo4j import AsyncDriver, AsyncGraphDatabase, Query
from neo4j.exceptions import Neo4jError

logger = logging.getLogger(__name__)

# =============================================================================
# CONFIGURATION
# =============================================================================

# Ontology DB bağlantı bilgileri (Documents DB'den FARKLI!)
ONTOLOGY_NEO4J_URI = os.getenv("ONTOLOGY_NEO4J_URI", "bolt://localhost:7688")
ONTOLOGY_NEO4J_USERNAME = os.getenv("ONTOLOGY_NEO4J_USERNAME", "neo4j")
ONTOLOGY_NEO4J_PASSWORD = os.getenv("ONTOLOGY_NEO4J_PASSWORD", "password")
ONTOLOGY_NEO4J_DATABASE = os.getenv("ONTOLOGY_NEO4J_DATABASE", "ontology")

# Connection pool settings
ONTOLOGY_MAX_POOL_SIZE = int(os.getenv("ONTOLOGY_MAX_POOL_SIZE", "50"))
ONTOLOGY_CONNECTION_TIMEOUT = int(os.getenv("ONTOLOGY_CONNECTION_TIMEOUT", "30"))


class OntologyDBClient:
    """
    Neo4j Ontology veritabanı client'ı.
    
    Goal-driven ve Ontology-driven reasoning için temel bağlantı katmanı.
    Singleton pattern kullanır - aynı process'te tek instance.
    
    Attributes:
        driver: Neo4j async driver instance
        database: Hedef database adı
        _initialized: Schema başlatıldı mı
    """
    
    _instance: Optional["OntologyDBClient"] = None
    _lock = asyncio.Lock()
    
    def __new__(cls):
        """Singleton pattern implementation"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._driver = None
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        """
        Client initialization.
        Bağlantı connect() çağrılana kadar kurulmaz.
        """
        self.database = ONTOLOGY_NEO4J_DATABASE
        self._schema_version: Optional[str] = None
    
    @property
    def driver(self) -> Optional[AsyncDriver]:
        """Neo4j driver instance"""
        return self._driver
    
    @property
    def is_connected(self) -> bool:
        """Bağlantı durumu"""
        return self._driver is not None
    
    async def connect(
        self,
        uri: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        database: Optional[str] = None
    ) -> None:
        """
        Neo4j Ontology DB'ye bağlan.
        
        Args:
            uri: Neo4j bolt URI (default: env var)
            username: Kullanıcı adı (default: env var)
            password: Şifre (default: env var)
            database: Database adı (default: env var)
            
        Raises:
            ConnectionError: Bağlantı başarısız olursa
        """
        async with self._lock:
            if self._driver is not None:
                logger.info("Ontology DB already connected, reusing connection")
                return
            
            _uri = uri or ONTOLOGY_NEO4J_URI
            _username = username or ONTOLOGY_NEO4J_USERNAME
            _password = password or ONTOLOGY_NEO4J_PASSWORD
            _database = database or ONTOLOGY_NEO4J_DATABASE
            
            logger.info(f"Connecting to Ontology DB: {_uri[:30]}...")
            
            try:
                self._driver = AsyncGraphDatabase.driver(
                    _uri,
                    auth=(_username, _password),
                    max_connection_pool_size=ONTOLOGY_MAX_POOL_SIZE,
                    connection_acquisition_timeout=ONTOLOGY_CONNECTION_TIMEOUT,
                )
                
                # Connection test
                async with self._driver.session(database=_database) as session:
                    result = await session.run("RETURN 1 as test")
                    await result.consume()
                
                self.database = _database
                logger.info(f"✅ Connected to Ontology DB: {_database}")
                
            except Exception as e:
                logger.error(f"❌ Failed to connect to Ontology DB: {e}")
                self._driver = None
                raise ConnectionError(f"Ontology DB connection failed: {e}")
    
    async def disconnect(self) -> None:
        """Bağlantıyı kapat"""
        async with self._lock:
            if self._driver is not None:
                await self._driver.close()
                self._driver = None
                self._initialized = False
                logger.info("Disconnected from Ontology DB")
    
    async def execute_query(
        self,
        query: str,
        params: Optional[Dict[str, Any]] = None,
        write: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Cypher sorgusu çalıştır.
        
        Args:
            query: Cypher sorgu string'i
            params: Sorgu parametreleri
            write: Write sorgusu mu (transaction routing için)
            
        Returns:
            Sorgu sonuçları (list of dicts)
            
        Raises:
            RuntimeError: Bağlantı yoksa
            Neo4jError: Sorgu hatası
        """
        if self._driver is None:
            raise RuntimeError("Not connected to Ontology DB. Call connect() first.")
        
        params = params or {}
        
        try:
            async with self._driver.session(database=self.database) as session:
                if write:
                    result = await session.run(query, params)
                else:
                    result = await session.run(query, params)
                
                records = await result.data()
                return records
                
        except Neo4jError as e:
            logger.error(f"Ontology DB query error: {e}\nQuery: {query[:200]}")
            raise
    
    async def execute_write(
        self,
        query: str,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Write sorgusu çalıştır ve özet döndür.
        
        Args:
            query: Cypher write sorgusu
            params: Sorgu parametreleri
            
        Returns:
            Sorgu özeti (counters)
        """
        if self._driver is None:
            raise RuntimeError("Not connected to Ontology DB. Call connect() first.")
        
        params = params or {}
        
        try:
            async with self._driver.session(database=self.database) as session:
                result = await session.run(query, params)
                summary = await result.consume()
                
                return {
                    "nodes_created": summary.counters.nodes_created,
                    "nodes_deleted": summary.counters.nodes_deleted,
                    "relationships_created": summary.counters.relationships_created,
                    "relationships_deleted": summary.counters.relationships_deleted,
                    "properties_set": summary.counters.properties_set,
                }
                
        except Neo4jError as e:
            logger.error(f"Ontology DB write error: {e}\nQuery: {query[:200]}")
            raise
    
    async def initialize_schema(self, force: bool = False) -> bool:
        """
        Ontology schema'yı başlat (constraints, indexes, seed data).
        
        Schema dosyaları backend/src/agent_builder/ontology/schema/ altında.
        Sıralama önemli: constraints -> nodes -> relationships -> seed
        
        Args:
            force: Mevcut schema'yı yeniden oluştur
            
        Returns:
            True başarılıysa
        """
        if self._initialized and not force:
            logger.info("Ontology schema already initialized")
            return True
        
        schema_dir = Path(__file__).parent / "schema"
        
        # Schema dosyaları sıralı
        schema_files = sorted(schema_dir.glob("*.cypher"))
        
        if not schema_files:
            logger.warning(f"No schema files found in {schema_dir}")
            return False
        
        logger.info(f"Initializing Ontology schema from {len(schema_files)} files...")
        
        for schema_file in schema_files:
            logger.info(f"  Executing: {schema_file.name}")
            
            cypher_content = schema_file.read_text(encoding="utf-8")
            
            # Cypher dosyasını statement'lara böl (// ile başlayan satırları atla)
            statements = self._parse_cypher_statements(cypher_content)
            
            for stmt in statements:
                if stmt.strip():
                    try:
                        await self.execute_write(stmt)
                    except Neo4jError as e:
                        # Constraint/index zaten varsa devam et
                        if "already exists" in str(e).lower():
                            logger.debug(f"Skipping existing: {stmt[:50]}...")
                        else:
                            logger.error(f"Schema error: {e}\nStatement: {stmt[:100]}")
                            raise
        
        self._initialized = True
        self._schema_version = self._compute_schema_hash(schema_dir)
        
        logger.info(f"✅ Ontology schema initialized (version: {self._schema_version[:8]})")
        return True
    
    def _parse_cypher_statements(self, cypher_content: str) -> List[str]:
        """
        Cypher dosyasını bireysel statement'lara böl.
        
        Rules:
        - // ile başlayan satırlar comment
        - ; statement sonu
        - Boş satırlar atlanır
        """
        statements = []
        current_stmt = []
        
        for line in cypher_content.split("\n"):
            stripped = line.strip()
            
            # Yorum satırı - atla ama mevcut statement'ı bitirme
            if stripped.startswith("//"):
                continue
            
            # Boş satır
            if not stripped:
                continue
            
            current_stmt.append(line)
            
            # Statement sonu
            if stripped.endswith(";"):
                stmt = "\n".join(current_stmt)
                # Sondaki ; kaldır
                stmt = stmt.rstrip().rstrip(";")
                if stmt.strip():
                    statements.append(stmt)
                current_stmt = []
        
        # Son statement (eğer ; ile bitmiyorsa)
        if current_stmt:
            stmt = "\n".join(current_stmt).strip()
            if stmt:
                statements.append(stmt)
        
        return statements
    
    def _compute_schema_hash(self, schema_dir: Path) -> str:
        """Schema dosyalarının hash'ini hesapla (versiyonlama için)"""
        hasher = hashlib.sha256()
        
        for schema_file in sorted(schema_dir.glob("*.cypher")):
            hasher.update(schema_file.read_bytes())
        
        return hasher.hexdigest()
    
    async def health_check(self) -> Dict[str, Any]:
        """
        Ontology DB sağlık kontrolü.
        
        Returns:
            Sağlık durumu bilgileri
        """
        if self._driver is None:
            return {
                "status": "disconnected",
                "database": self.database,
                "initialized": False
            }
        
        try:
            result = await self.execute_query(
                """
                CALL dbms.components() YIELD name, versions
                RETURN name, versions[0] as version
                """
            )
            
            # Node counts
            counts = await self.execute_query(
                """
                MATCH (n)
                RETURN labels(n)[0] as label, count(n) as count
                ORDER BY count DESC
                LIMIT 10
                """
            )
            
            return {
                "status": "healthy",
                "database": self.database,
                "initialized": self._initialized,
                "schema_version": self._schema_version,
                "neo4j_version": result[0]["version"] if result else "unknown",
                "node_counts": {r["label"]: r["count"] for r in counts}
            }
            
        except Exception as e:
            return {
                "status": "unhealthy",
                "database": self.database,
                "error": str(e)
            }


# =============================================================================
# MODULE-LEVEL FUNCTIONS
# =============================================================================

_client: Optional[OntologyDBClient] = None


async def get_ontology_client() -> OntologyDBClient:
    """
    Global Ontology DB client instance'ı al.
    Lazy initialization - ilk çağrıda bağlanır.
    """
    global _client
    
    if _client is None:
        _client = OntologyDBClient()
    
    if not _client.is_connected:
        await _client.connect()
    
    return _client


async def initialize_ontology_db(force: bool = False) -> bool:
    """
    Ontology DB'yi başlat (schema + seed data).
    
    Bu fonksiyon uygulama başlatılırken çağrılmalı.
    """
    client = await get_ontology_client()
    return await client.initialize_schema(force=force)
