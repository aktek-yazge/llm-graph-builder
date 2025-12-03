# -*- coding: utf-8 -*-
"""
Global Schema Version Cache System

PostgreSQL'de version, RAM'de schema tutar.
Version değiştiğinde schema yenilenir.

Kullanım:
- Chat isteğinde: schema = get_schema_cache().get_schema(database_url, graph)
- Yeni belge yüklenince: get_schema_cache().increment_version(database_url)
"""

import logging
import os
import time
from typing import Optional, Dict, Any
from datetime import datetime

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    DateTime,
    Text,
    text,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Graph database type detection
GRAPH_DB_TYPE = os.environ.get("GRAPH_DB_TYPE", "neo4j").lower()

logger = logging.getLogger(__name__)

Base = declarative_base()


class SchemaVersion(Base):
    """Schema version tracking table"""
    
    __tablename__ = "schema_version"
    
    id = Column(Integer, primary_key=True)
    database_url = Column(String(500), unique=True, nullable=False, index=True)
    version = Column(Integer, default=1, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


# Global RAM cache
_cached_schema: Optional[str] = None
_cached_version: Optional[int] = None
_cached_database_url: Optional[str] = None


class SchemaVersionCache:
    """
    Version-based global schema cache
    
    PostgreSQL: Sadece version (integer) tutar
    RAM: Schema string + version tutar
    
    Akış:
    1. PostgreSQL'den version al
    2. RAM version == DB version? → RAM'den kullan
    3. Version farklı? → Neo4j'den çek, RAM'e yükle
    """
    
    def __init__(self, db_url: str = None):
        """Initialize with database URL"""
        if db_url:
            self.db_url = db_url
        else:
            # Environment variable'dan al
            self.db_url = os.getenv("QUEUE_DB_URL")
            
            if not self.db_url:
                # SQLite fallback
                from pathlib import Path
                current_dir = Path(__file__).parent.parent.parent  # backend/
                db_path = current_dir / "queue.db"
                self.db_url = f"sqlite:///{db_path}"
        
        # Pool configuration for PostgreSQL
        pool_config = {}
        if "sqlite" not in self.db_url:
            pool_config = {
                "pool_size": 5,
                "max_overflow": 10,
                "pool_timeout": 30,
                "pool_recycle": 1800,
                "pool_pre_ping": True,
            }
        
        self.engine = create_engine(
            self.db_url,
            echo=False,
            poolclass=StaticPool if "sqlite" in self.db_url else None,
            **pool_config,
        )
        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine
        )
        
        # Create table if not exists
        Base.metadata.create_all(bind=self.engine)
        
        logger.info(f"✅ SchemaVersionCache initialized with DB: {self.db_url[:50]}...")
    
    def get_schema(self, database_url: str, graph) -> str:
        """
        Version kontrolü ile şema al
        
        Args:
            database_url: Neo4j database URL (e.g., "bolt://host:port/dbname")
            graph: Neo4j graph connection object
            
        Returns:
            Schema string
        """
        global _cached_schema, _cached_version, _cached_database_url
        
        # 1. PostgreSQL'den güncel version'ı al
        db_version = self._get_version(database_url)
        
        # 2. RAM cache kontrolü
        if (_cached_schema 
            and _cached_version == db_version 
            and _cached_database_url == database_url):
            logger.info(f"✅ Schema RAM'den alındı (version: {db_version}, url_match: True)")
            return _cached_schema
        
        # 3. Version değişmiş veya RAM boş veya URL farklı → Neo4j'den çek
        url_match = _cached_database_url == database_url
        logger.info(
            f"🔄 Schema yenileniyor - "
            f"RAM_version: {_cached_version}, DB_version: {db_version}, "
            f"URL_match: {url_match}, "
            f"Cached_URL: {_cached_database_url[:30] if _cached_database_url else 'None'}..., "
            f"Request_URL: {database_url[:30] if database_url else 'None'}..."
        )
        
        schema = self._fetch_from_neo4j(graph)
        
        # RAM cache'i güncelle
        _cached_schema = schema
        _cached_version = db_version
        _cached_database_url = database_url
        
        logger.info(f"✅ Schema güncellendi ve RAM'e yüklendi ({len(schema)} karakter)")
        return schema
    
    def increment_version(self, database_url: str) -> int:
        """
        Şema değişince version artır
        Celery worker belge işleme sonunda çağırır
        
        Args:
            database_url: Neo4j database URL
            
        Returns:
            New version number
        """
        db = self.SessionLocal()
        try:
            # UPSERT: Insert veya Update
            if "sqlite" in self.db_url:
                # SQLite için INSERT OR REPLACE
                db.execute(text("""
                    INSERT INTO schema_version (database_url, version, updated_at)
                    VALUES (:url, 1, :now)
                    ON CONFLICT(database_url) 
                    DO UPDATE SET version = schema_version.version + 1, updated_at = :now
                """), {"url": database_url, "now": datetime.utcnow()})
            else:
                # PostgreSQL için ON CONFLICT
                db.execute(text("""
                    INSERT INTO schema_version (database_url, version, updated_at)
                    VALUES (:url, 1, :now)
                    ON CONFLICT (database_url) 
                    DO UPDATE SET version = schema_version.version + 1, updated_at = :now
                """), {"url": database_url, "now": datetime.utcnow()})
            
            db.commit()
            
            # Yeni version'ı al
            new_version = self._get_version(database_url)
            logger.info(f"📈 Schema version artırıldı: {database_url} → v{new_version}")
            
            return new_version
            
        except Exception as e:
            db.rollback()
            logger.error(f"❌ Version increment hatası: {e}")
            raise e
        finally:
            db.close()
    
    def get_current_version(self, database_url: str) -> int:
        """Get current version without modifying"""
        return self._get_version(database_url)
    
    def get_cache_status(self) -> Dict[str, Any]:
        """Get current cache status for debugging"""
        global _cached_schema, _cached_version, _cached_database_url
        
        return {
            "has_cached_schema": _cached_schema is not None,
            "cached_version": _cached_version,
            "cached_database_url": _cached_database_url,
            "cached_schema_length": len(_cached_schema) if _cached_schema else 0,
        }
    
    def invalidate_cache(self):
        """RAM cache'i temizle (debug/test için)"""
        global _cached_schema, _cached_version, _cached_database_url
        
        _cached_schema = None
        _cached_version = None
        _cached_database_url = None
        
        logger.info("🗑️ Schema RAM cache temizlendi")
    
    def _get_version(self, database_url: str) -> int:
        """PostgreSQL'den version al"""
        db = self.SessionLocal()
        try:
            result = db.execute(
                text("SELECT version FROM schema_version WHERE database_url = :url"),
                {"url": database_url}
            ).fetchone()
            
            if result:
                return result[0]
            
            # Version yok, 0 döndür (ilk çekimde Neo4j'den alınacak)
            return 0
            
        finally:
            db.close()
    
    def _fetch_from_neo4j(self, graph) -> str:
        """
        Neo4j veya Memgraph'tan şema çek
        Mevcut fast_agent_integration_simple.py'deki mantığı kullanır
        """
        try:
            if GRAPH_DB_TYPE == "memgraph":
                # Memgraph için basitleştirilmiş sorgular
                # Node'ları al
                get_nodes_query = """
                MATCH (n)
                WITH labels(n) as lbls, n
                UNWIND lbls as lbl
                WITH lbl, count(n) as cnt, collect(keys(n))[0] as props
                RETURN lbl as nodeType, cnt as nodeCount, props as properties
                ORDER BY lbl
                """
                
                # Relationship'leri al
                get_rels_query = """
                MATCH (a)-[r]->(b)
                WITH type(r) as relationshipType, labels(a)[0] as from_node, labels(b)[0] as to_node, 
                     collect(keys(r))[0] as rel_props, count(*) as cnt
                RETURN DISTINCT relationshipType, from_node, to_node, rel_props
                ORDER BY relationshipType
                """
            else:
                # Neo4j için orijinal sorgular
                get_nodes_query = """
                CALL db.labels() YIELD label
                WITH collect(label) as labels
                UNWIND labels as lbl
                CALL {
                  WITH lbl
                  MATCH (n) WHERE lbl IN labels(n)
                  WITH count(n) as cnt, collect(properties(n))[0] as sample_props
                  RETURN cnt, keys(sample_props) as props
                }
                RETURN lbl as nodeType, cnt as nodeCount, props as properties
                ORDER BY lbl
                """
                
                # Relationship'leri al
                get_rels_query = """
                CALL db.relationshipTypes() YIELD relationshipType
                CALL {
                  WITH relationshipType
                  MATCH (a)-[r]->(b) WHERE type(r) = relationshipType
                  WITH labels(a)[0] as from_node, labels(b)[0] as to_node, 
                       collect(properties(r))[0] as sample_props, count(*) as cnt
                  ORDER BY cnt DESC
                  LIMIT 1
                  RETURN from_node, to_node, keys(sample_props) as rel_props
                }
                RETURN relationshipType, from_node, to_node, rel_props
                ORDER BY relationshipType
                """
            
            nodes_result = graph.query(get_nodes_query)
            rels_result = graph.query(get_rels_query)
            
            return self._create_schema_format(nodes_result, rels_result)
            
        except Exception as e:
            logger.error(f"❌ Neo4j şema çekme hatası: {e}")
            return f"⚠️ Şema alınamadı: {str(e)}"
    
    def _create_schema_format(self, nodes_result, rels_result) -> str:
        """Cypher sonuçlarından minimal şema formatı oluştur"""
        lines = []
        
        # Tip kısaltmaları
        type_mapping = {
            "createdAt": "dt", "updatedAt": "dt",
            "created_at": "dt", "updated_at": "dt",
            "amount": "float", "count": "int",
            "year": "int", "month": "int",
        }
        
        # Node'ları işle
        nodes_section = []
        for node_data in (nodes_result or []):
            if not node_data:
                continue
            
            node_name = node_data.get("nodeType") if isinstance(node_data, dict) else None
            node_count = node_data.get("nodeCount") if isinstance(node_data, dict) else None
            properties = node_data.get("properties", []) if isinstance(node_data, dict) else []
            
            if properties is None:
                properties = []
            
            # İlk 6 property'yi kısa tip bilgisiyle al
            props_with_types = []
            for prop_name in properties[:6]:
                if prop_name in type_mapping:
                    prop_type = type_mapping[prop_name]
                elif any(x in prop_name.lower() for x in ["id", "name", "address", "content"]):
                    prop_type = "str"
                else:
                    prop_type = "str"
                props_with_types.append(f"{prop_name}:{prop_type}")
            
            nodes_section.append(f"({node_name}:{node_count}){{{','.join(props_with_types)}}}")
        
        # Relationship'leri işle
        relationships_section = []
        for rel_data in (rels_result or []):
            if not rel_data:
                continue
            
            rel_name = rel_data.get("relationshipType") if isinstance(rel_data, dict) else None
            from_node = rel_data.get("from_node") if isinstance(rel_data, dict) else None
            to_node = rel_data.get("to_node") if isinstance(rel_data, dict) else None
            rel_props = rel_data.get("rel_props", []) if isinstance(rel_data, dict) else []
            
            # Relationship properties (ilk 3)
            rel_props_with_types = []
            for prop_name in (rel_props or [])[:3]:
                prop_type = type_mapping.get(prop_name, "str")
                rel_props_with_types.append(f"{prop_name}:{prop_type}")
            
            if rel_props_with_types:
                pattern = f"({from_node})-[:{rel_name} {{{','.join(rel_props_with_types)}}}]->({to_node})"
            else:
                pattern = f"({from_node})-[:{rel_name}]->({to_node})"
            
            relationships_section.append(pattern)
        
        # Sonucu birleştir
        if nodes_section:
            lines.append("# NODES")
            lines.extend(nodes_section)
        
        if relationships_section:
            lines.append("")
            lines.append("# RELATIONSHIPS")
            lines.extend(relationships_section)
        
        return "\n".join(lines)


# Global instance
_schema_cache_instance: Optional[SchemaVersionCache] = None


def get_schema_cache() -> SchemaVersionCache:
    """Get global schema cache instance"""
    global _schema_cache_instance
    
    if _schema_cache_instance is None:
        _schema_cache_instance = SchemaVersionCache()
    
    return _schema_cache_instance


# Convenience functions
def get_cached_schema(database_url: str, graph) -> str:
    """Shortcut: Get schema with version check"""
    return get_schema_cache().get_schema(database_url, graph)


def increment_schema_version(database_url: str) -> int:
    """Shortcut: Increment schema version (call after document processing)"""
    return get_schema_cache().increment_version(database_url)

