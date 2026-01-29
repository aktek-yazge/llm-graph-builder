# -*- coding: utf-8 -*-
"""
Schema Version Increment - Celery Worker için Minimal Modül

Sadece belge işleme sonrası schema version'ı artırmak için kullanılır.
Tam schema cache işlemleri backend/src/shared/schema_cache.py'da yapılır.

Kullanım:
    from src.shared.schema_version import increment_schema_version
    increment_schema_version(neo4j_uri)
"""

import logging
import os
from datetime import datetime
from typing import Optional

from sqlalchemy import create_engine, Column, Integer, String, DateTime, text
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


# Singleton instance
_version_manager: Optional["SchemaVersionManager"] = None


class SchemaVersionManager:
    """
    Schema version yönetimi - sadece increment işlemi
    
    PostgreSQL'deki schema_version tablosunda version artırır.
    Backend tarafı bu version'ı kontrol ederek cache'i yeniler.
    """
    
    def __init__(self, db_url: Optional[str] = None):
        if db_url:
            self.db_url = db_url
        else:
            self.db_url = os.getenv("POSTGRES_URL")
            
            if not self.db_url:
                from pathlib import Path
                current_dir = Path(__file__).parent.parent.parent
                db_path = current_dir / "queue.db"
                self.db_url = f"sqlite:///{db_path}"
        
        pool_config = {}
        if self.db_url and "sqlite" not in self.db_url:
            pool_config = {
                "pool_size": 2,
                "max_overflow": 3,
                "pool_timeout": 30,
                "pool_recycle": 1800,
                "pool_pre_ping": True,
            }
        
        self.engine = create_engine(
            self.db_url,
            echo=False,
            poolclass=StaticPool if self.db_url and "sqlite" in self.db_url else None,
            **pool_config,
        )
        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine
        )
        
        Base.metadata.create_all(bind=self.engine)
        logger.info(f"✅ SchemaVersionManager initialized")
    
    def increment_version(self, database_url: str) -> int:
        """
        Schema version'ı artır
        
        Belge işleme tamamlandığında çağrılır.
        Backend bu değişikliği görerek cache'i yeniler.
        
        Args:
            database_url: Neo4j database URL
            
        Returns:
            Yeni version numarası
        """
        db = self.SessionLocal()
        try:
            if self.db_url and "sqlite" in self.db_url:
                db.execute(text("""
                    INSERT INTO schema_version (database_url, version, updated_at)
                    VALUES (:url, 1, :now)
                    ON CONFLICT(database_url) 
                    DO UPDATE SET version = schema_version.version + 1, updated_at = :now
                """), {"url": database_url, "now": datetime.utcnow()})
            else:
                db.execute(text("""
                    INSERT INTO schema_version (database_url, version, updated_at)
                    VALUES (:url, 1, :now)
                    ON CONFLICT (database_url) 
                    DO UPDATE SET version = schema_version.version + 1, updated_at = :now
                """), {"url": database_url, "now": datetime.utcnow()})
            
            db.commit()
            
            result = db.execute(
                text("SELECT version FROM schema_version WHERE database_url = :url"),
                {"url": database_url}
            ).fetchone()
            
            new_version = result[0] if result else 1
            logger.info(f"📈 Schema version artırıldı: v{new_version}")
            return new_version
            
        except Exception as e:
            db.rollback()
            logger.error(f"❌ Version artırma hatası: {e}")
            raise
        finally:
            db.close()


def get_version_manager() -> SchemaVersionManager:
    """Singleton instance döndür"""
    global _version_manager
    
    if _version_manager is None:
        _version_manager = SchemaVersionManager()
    
    return _version_manager


def increment_schema_version(database_url: str) -> int:
    """
    Schema version'ı artır (convenience function)
    
    Belge işleme sonrası çağrılır.
    Backend tarafı bu değişikliği algılayarak schema cache'i yeniler.
    
    Args:
        database_url: Neo4j database URL (örn: "bolt://localhost:7687")
        
    Returns:
        Yeni version numarası
    """
    return get_version_manager().increment_version(database_url)

