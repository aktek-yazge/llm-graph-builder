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
        Neo4j'den şema çek - ÖNCE hafif sorgu, sonra Langchain
        """
        try:
            # 1. ÖNCE en hafif sorguyu dene (timeout riski düşük)
            lightweight = self._fetch_schema_lightweight(graph)
            if lightweight and not lightweight.startswith("⚠️"):
                logger.info("✅ Lightweight schema başarılı")
                return lightweight
            
            # 2. Hafif sorgu başarısız olursa, mevcut schema'yı kullan
            if hasattr(graph, 'schema') and graph.schema:
                logger.info("✅ Mevcut graph.schema kullanılıyor")
                return graph.schema
            
            return lightweight  # Hata mesajını döndür
            
        except Exception as e:
            logger.error(f"❌ Neo4j şema çekme hatası: {e}")
            return f"⚠️ Şema alınamadı: {str(e)}"
    
    def _format_langchain_schema(self, structured: dict) -> str:
        """Langchain structured schema'yı formatla"""
        lines = []
        
        # Node properties
        node_props = structured.get("node_props", {})
        for node_type, props in node_props.items():
            prop_str = ",".join([f"{p['property']}:{p.get('type', 'str')[:3]}" for p in props])
            lines.append(f"({node_type}){{{prop_str}}}")
        
        # Relationships
        relationships = structured.get("relationships", [])
        for rel in relationships:
            start = rel.get("start", "?")
            rel_type = rel.get("type", "?")
            end = rel.get("end", "?")
            lines.append(f"({start})-[:{rel_type}]->({end})")
        
        if not lines:
            return "⚠️ Şema boş"
        
        return "\n".join(lines)
    
    def _fetch_schema_lightweight(self, graph) -> str:
        """
        Hafif şema sorgusu - Token tasarrufu için minimal format
        
        Format:
        # NODES
        (NodeName:count){prop1:type,prop2:type,...}
        
        # RELATIONSHIPS
        (FromNode)-[:REL_TYPE]->(ToNode)
        """
        try:
            logger.info("🔍 Neo4j'den şema çekiliyor (hafif sorgu)...")
            
            # Tip kısaltmaları
            type_mapping = {
                "createdAt": "dt", "updatedAt": "dt",
                "created_at": "dt", "updated_at": "dt",
                "amount": "float", "count": "int", "year": "int", "month": "int",
            }
            
            # 1. Node label'larını ve sayılarını al
            labels_query = """
            CALL db.labels() YIELD label
            CALL {
                WITH label
                MATCH (n) WHERE label IN labels(n)
                RETURN count(n) as cnt
            }
            RETURN label, cnt
            ORDER BY cnt DESC
            """
            
            try:
                labels_result = graph.query(labels_query)
            except:
                # Fallback: sadece label listesi
                labels_result = graph.query("CALL db.labels() YIELD label RETURN label, 0 as cnt")
            
            labels_with_count = [(r["label"], r["cnt"]) for r in labels_result] if labels_result else []
            logger.info(f"📋 {len(labels_with_count)} node label bulundu")
            
            # 2. Her label için property'leri al (sampling)
            node_props = {}
            for label, _ in labels_with_count:  # Tüm label'lar
                try:
                    prop_query = f"MATCH (n:`{label}`) RETURN keys(n) as props LIMIT 1"
                    prop_result = graph.query(prop_query)
                    if prop_result and prop_result[0].get("props"):
                        # Gereksiz property'leri filtrele
                        props = [p for p in prop_result[0]["props"] 
                                if p not in ["embedding", "id", "uuid", "elementId"]]
                        node_props[label] = props  # Tüm property'ler
                except:
                    pass
            
            # 3. Relationship pattern'larını al (APOC olmadan - sampling ile)
            rel_patterns_query = """
            CALL db.relationshipTypes() YIELD relationshipType as type
            RETURN type
            """
            rel_types_result = graph.query(rel_patterns_query)
            rel_types = [r["type"] for r in rel_types_result] if rel_types_result else []
            
            # Her rel type için TÜM unique pattern'leri bul
            patterns = []
            for rel_type in rel_types:  # Tüm relationship type'lar
                try:
                    # DISTINCT ile tüm unique from->to kombinasyonlarını al
                    pattern_query = f"""
                    MATCH (a)-[r:`{rel_type}`]->(b)
                    RETURN DISTINCT labels(a)[0] as fromLabel, labels(b)[0] as toLabel
                    """
                    pattern_result = graph.query(pattern_query)
                    for row in pattern_result:
                        from_label = row.get("fromLabel", "?")
                        to_label = row.get("toLabel", "?")
                        patterns.append((from_label, rel_type, to_label))
                except:
                    pass
            
            logger.info(f"📋 {len(patterns)} relationship pattern bulundu")
            
            # 4. Format oluştur
            lines = ["# NODES"]
            for label, count in labels_with_count:
                props = node_props.get(label, [])
                props_with_types = []
                for prop in props:
                    if prop in type_mapping:
                        prop_type = type_mapping[prop]
                    elif any(x in prop.lower() for x in ["id", "name", "text", "content", "title"]):
                        prop_type = "str"
                    elif any(x in prop.lower() for x in ["date", "time"]):
                        prop_type = "dt"
                    elif any(x in prop.lower() for x in ["count", "number", "amount", "year"]):
                        prop_type = "int"
                    else:
                        prop_type = "str"
                    props_with_types.append(f"{prop}:{prop_type}")
                
                if props_with_types:
                    lines.append(f"({label}:{count}){{{','.join(props_with_types)}}}")
                else:
                    lines.append(f"({label}:{count})")
            
            lines.append("")
            lines.append("# RELATIONSHIPS")
            
            seen_patterns = set()
            for from_label, rel_type, to_label in patterns:
                pattern = f"({from_label})-[:{rel_type}]->({to_label})"
                if pattern not in seen_patterns:
                    seen_patterns.add(pattern)
                    lines.append(pattern)
            
            schema = "\n".join(lines)
            logger.info(f"✅ Schema oluşturuldu: {len(schema)} karakter, {len(labels_with_count)} node, {len(seen_patterns)} pattern")
            return schema
            
        except Exception as e:
            logger.error(f"❌ Lightweight schema hatası: {e}")
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
            for prop_name in properties:
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
            for prop_name in (rel_props or []):
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

