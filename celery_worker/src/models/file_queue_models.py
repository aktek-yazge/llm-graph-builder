# -*- coding: utf-8 -*-
"""
SQLAlchemy models for file upload queue system
"""

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    DateTime,
    Text,
    BigInteger,
    Boolean,
    Index,
    event,
    text,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from datetime import datetime
import hashlib
import os
import subprocess
import platform
import logging
from pathlib import Path
from typing import Optional, List
from enum import Enum

Base = declarative_base()


class FileStatus(str, Enum):
    """File processing status enum"""

    UPLOADED = "uploaded"  # File uploaded but not queued for processing
    QUEUED = "queued"  # File queued for processing
    PROCESSING = "processing"  # File currently being processed
    COMPLETED = "completed"  # File processed successfully
    ERROR = "error"  # Processing failed


class ChunkingStatus(str, Enum):
    """Chunking stage status"""

    PENDING = "pending"  # Waiting to be chunked
    CHUNKING = "chunking"  # Currently chunking
    CHUNKED = "chunked"  # Chunking completed
    FAILED = "failed"  # Chunking failed


class GraphStatus(str, Enum):
    """Graph creation status"""

    PENDING = "pending"  # Waiting for graph creation
    PROCESSING = "processing"  # Currently creating graph
    COMPLETED = "completed"  # Graph creation completed
    FAILED = "failed"  # Graph creation failed
    PENDING_ENDORSEMENT = "pending_endorsement"  # Waiting for endorsement processing (after all policies are loaded)


class UploadedFile(Base):
    """Model for uploaded files tracking"""

    __tablename__ = "uploaded_files"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String(255), nullable=False, index=True)  # normalized filename
    original_name = Column(String(255), nullable=False)  # original filename from user
    file_path = Column(String(500), nullable=False)  # full path to uploaded file
    upload_date = Column(DateTime, default=datetime.utcnow, nullable=False)
    file_size = Column(BigInteger, nullable=True)  # file size in bytes
    file_hash = Column(
        String(64), nullable=True, index=True
    )  # SHA256 hash for duplicate detection
    status = Column(String(20), default=FileStatus.UPLOADED, nullable=False, index=True)

    # V2 Stage-based workflow
    upload_status = Column(
        String(20), default="uploading", nullable=False
    )  # uploading, uploaded, failed
    chunking_status = Column(
        String(20), default="pending", nullable=False
    )  # pending, chunking, chunked, failed
    graph_status = Column(
        String(20), default="pending", nullable=False
    )  # pending, processing, completed, failed
    embedding_status = Column(
        String(20), default="pending", nullable=False
    )  # pending, processing, completed, failed

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Stage completion timestamps
    chunking_started_at = Column(DateTime, nullable=True)
    chunking_completed_at = Column(DateTime, nullable=True)
    graph_started_at = Column(DateTime, nullable=True)
    graph_completed_at = Column(DateTime, nullable=True)
    embedding_started_at = Column(DateTime, nullable=True)
    embedding_completed_at = Column(DateTime, nullable=True)

    # Processing details
    processing_started_at = Column(DateTime, nullable=True)
    processing_completed_at = Column(DateTime, nullable=True)
    processing_error = Column(Text, nullable=True)
    reason = Column(Text, nullable=True)  # Detailed reason for status (success/failure)

    # Neo4j connection details used for processing
    neo4j_uri = Column(String(255), nullable=True)
    neo4j_database = Column(String(100), nullable=True)
    model_used = Column(String(100), nullable=True)
    generate_embedding = Column(String(10), nullable=True)  # "true"/"false" string

    # Chunking metadata (V2)
    doc_link = Column(String(500), nullable=True)  # S3 document link (filename only)
    page_images = Column(Text, nullable=True)  # JSON string of page image filenames
    markdown_path = Column(String(500), nullable=True)  # Local markdown file path
    auto_process = Column(
        Boolean, default=False, nullable=False
    )  # Auto start chunking and graph creation after image extraction
    
    # Celery task tracking for cancellation/reset
    celery_task_id = Column(String(100), nullable=True, index=True)  # Current active Celery task ID

    # Add composite indexes for common queries
    __table_args__ = (
        Index("ix_status_created", "status", "created_at"),
        Index("ix_hash_filename", "file_hash", "filename"),
    )

    def __repr__(self):
        return f"<UploadedFile(id={self.id}, filename='{self.filename}', status='{self.status}')>"

    @staticmethod
    def calculate_file_hash(file_path: str) -> Optional[str]:
        """Calculate SHA256 hash of a file"""
        try:
            hash_sha256 = hashlib.sha256()
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_sha256.update(chunk)
            return hash_sha256.hexdigest()
        except Exception as e:
            print(f"Error calculating hash for {file_path}: {e}")
            return None

    def update_status(self, new_status: FileStatus, error_message: str = None, reason: str = None):
        """Update file status with appropriate timestamps"""
        self.status = new_status
        self.updated_at = datetime.utcnow()
        
        if reason:
            self.reason = reason

        if new_status == FileStatus.PROCESSING:
            self.processing_started_at = datetime.utcnow()
            self.processing_error = None  # Clear previous errors
        elif new_status == FileStatus.COMPLETED:
            self.processing_completed_at = datetime.utcnow()
            self.processing_error = None
        elif new_status == FileStatus.ERROR:
            self.processing_completed_at = datetime.utcnow()
            self.processing_error = error_message


class FileQueueDatabase:
    """Database manager for file queue operations"""

    def __init__(self, db_path: str = "queue.db", db_url: str = None):
        """Initialize database connection"""
        if db_url:
            self.db_url = db_url
        else:
            self.db_path = Path(db_path)
            self.db_url = f"sqlite:///{self.db_path}"
            
        # Pool configuration for PostgreSQL
        # pool_size: Number of connections to keep open
        # max_overflow: Maximum overflow connections allowed
        # pool_timeout: Seconds to wait for connection from pool
        # pool_recycle: Recycle connections after N seconds (prevent stale connections)
        pool_config = {}
        if "sqlite" not in self.db_url:
            pool_config = {
                "pool_size": 10,           # Base connections (reduced from 100)
                "max_overflow": 20,        # Extra connections when needed (reduced from 200)
                "pool_timeout": 30,        # Wait up to 30s for connection
                "pool_recycle": 300,       # Recycle connections every 5 min (faster cleanup)
                "pool_pre_ping": True,     # Check connection health before use
            }
        
        self.engine = create_engine(
            self.db_url,
            # connect_args={"check_same_thread": False}, # Only for SQLite
            echo=False,
            poolclass=StaticPool if "sqlite" in self.db_url else None, # StaticPool for SQLite
            **pool_config,
        )
        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine
        )

        # Create tables if they don't exist
        Base.metadata.create_all(bind=self.engine)

        # Migrate: Add auto_process column if it doesn't exist
        self._migrate_add_auto_process_column()
        # Migrate: Add reason column if it doesn't exist
        self._migrate_add_reason_column()
        # Migrate: Add celery_task_id column if it doesn't exist
        self._migrate_add_celery_task_id_column()

    def _migrate_add_auto_process_column(self):
        """Add auto_process column to uploaded_files table if it doesn't exist"""
        try:
            # Check if column exists
            with self.engine.connect() as conn:
                # Check if column exists (works for both SQLite and PostgreSQL)
                db_url = getattr(self, 'db_url', str(self.engine.url))
                if "sqlite" in db_url:
                    # SQLite specific: Check if column exists
                    result = conn.execute(
                        text("PRAGMA table_info(uploaded_files)")
                    ).fetchall()
                    column_names = [row[1] for row in result]
                else:
                    # PostgreSQL specific: Check if column exists
                    result = conn.execute(
                        text("""
                            SELECT column_name 
                            FROM information_schema.columns 
                            WHERE table_name = 'uploaded_files' AND column_name = 'auto_process'
                        """)
                    ).fetchall()
                    column_names = [row[0] for row in result] if result else []

                if "auto_process" not in column_names:
                    logging.info(
                        "🔄 Migrating: Adding auto_process column to uploaded_files table"
                    )
                    # Add column with default value (SQLite uses INTEGER for BOOLEAN)
                    conn.execute(
                        text(
                            "ALTER TABLE uploaded_files ADD COLUMN auto_process INTEGER DEFAULT 0 NOT NULL"
                        )
                    )
                    conn.commit()
                    logging.info("✅ Migration completed: auto_process column added")
                else:
                    logging.debug("ℹ️ auto_process column already exists")
        except Exception as e:
            logging.warning(f"⚠️ Migration warning: {str(e)}")
            # Continue even if migration fails (column might already exist)

    def _migrate_add_reason_column(self):
        """Add reason column to uploaded_files table if it doesn't exist"""
        try:
            # Check if column exists
            with self.engine.connect() as conn:
                # Check if column exists (works for both SQLite and PostgreSQL)
                db_url = getattr(self, 'db_url', str(self.engine.url))
                if "sqlite" in db_url:
                    # SQLite specific: Check if column exists
                    result = conn.execute(
                        text("PRAGMA table_info(uploaded_files)")
                    ).fetchall()
                    column_names = [row[1] for row in result]
                else:
                    # PostgreSQL specific: Check if column exists
                    result = conn.execute(
                        text("""
                            SELECT column_name 
                            FROM information_schema.columns 
                            WHERE table_name = 'uploaded_files' AND column_name = 'reason'
                        """)
                    ).fetchall()
                    column_names = [row[0] for row in result] if result else []

                if "reason" not in column_names:
                    logging.info(
                        "🔄 Migrating: Adding reason column to uploaded_files table"
                    )
                    # Add column
                    conn.execute(
                        text(
                            "ALTER TABLE uploaded_files ADD COLUMN reason TEXT"
                        )
                    )
                    conn.commit()
                    logging.info("✅ Migration completed: reason column added")
                else:
                    logging.debug("ℹ️ reason column already exists")
        except Exception as e:
            logging.warning(f"⚠️ Migration warning (reason): {str(e)}")
            # Continue even if migration fails

    def _migrate_add_celery_task_id_column(self):
        """Add celery_task_id column to uploaded_files table if it doesn't exist"""
        try:
            with self.engine.connect() as conn:
                db_url = getattr(self, 'db_url', str(self.engine.url))
                if "sqlite" in db_url:
                    result = conn.execute(
                        text("PRAGMA table_info(uploaded_files)")
                    ).fetchall()
                    column_names = [row[1] for row in result]
                else:
                    result = conn.execute(
                        text("""
                            SELECT column_name 
                            FROM information_schema.columns 
                            WHERE table_name = 'uploaded_files' AND column_name = 'celery_task_id'
                        """)
                    ).fetchall()
                    column_names = [row[0] for row in result] if result else []

                if "celery_task_id" not in column_names:
                    logging.info(
                        "🔄 Migrating: Adding celery_task_id column to uploaded_files table"
                    )
                    conn.execute(
                        text(
                            "ALTER TABLE uploaded_files ADD COLUMN celery_task_id VARCHAR(100)"
                        )
                    )
                    conn.commit()
                    logging.info("✅ Migration completed: celery_task_id column added")
                else:
                    logging.debug("ℹ️ celery_task_id column already exists")
        except Exception as e:
            logging.warning(f"⚠️ Migration warning (celery_task_id): {str(e)}")

    def get_db_session(self) -> Session:
        """Get database session"""
        return self.SessionLocal()

    def add_file(
        self,
        filename: str,
        original_name: str,
        file_path: str,
        file_size: int = None,
        neo4j_uri: str = None,
        neo4j_database: str = None,
        model: str = None,
        generate_embedding: str = None,
        auto_process: bool = False,
    ) -> UploadedFile:
        """Add new uploaded file to database"""

        db = self.get_db_session()
        try:
            # Calculate file hash
            file_hash = (
                UploadedFile.calculate_file_hash(file_path)
                if os.path.exists(file_path)
                else None
            )

            # Check for duplicates by hash and filename
            existing_file = None
            if file_hash:
                existing_file = (
                    db.query(UploadedFile)
                    .filter(
                        UploadedFile.file_hash == file_hash,
                        UploadedFile.filename == filename,
                    )
                    .first()
                )

            if existing_file:
                # File already exists, update metadata
                existing_file.original_name = original_name
                existing_file.file_path = file_path
                existing_file.file_size = file_size
                existing_file.updated_at = datetime.utcnow()
                existing_file.neo4j_uri = neo4j_uri
                existing_file.neo4j_database = neo4j_database
                existing_file.model_used = model
                existing_file.generate_embedding = generate_embedding

                db.commit()
                db.refresh(existing_file)
                return existing_file

            # Create new file record
            new_file = UploadedFile(
                filename=filename,
                original_name=original_name,
                file_path=file_path,
                file_size=file_size,
                file_hash=file_hash,
                status=FileStatus.UPLOADED,  # Old field for compatibility
                upload_status="uploaded",  # V2 workflow
                chunking_status="pending",  # V2 workflow: Upload sonrası "pending", batch seçildiğinde "extracting" olacak
                graph_status="pending",  # V2 workflow
                neo4j_uri=neo4j_uri,
                neo4j_database=neo4j_database,
                model_used=model,
                generate_embedding=generate_embedding,
                auto_process=auto_process,
            )

            db.add(new_file)
            db.commit()
            db.refresh(new_file)
            return new_file

        except Exception as e:
            db.rollback()
            raise e
        finally:
            db.close()

    def get_file_by_id(self, file_id: int) -> Optional[UploadedFile]:
        """Get file by ID"""
        db = self.get_db_session()
        try:
            return db.query(UploadedFile).filter(UploadedFile.id == file_id).first()
        finally:
            db.close()

    def get_file_by_hash(self, file_hash: str) -> Optional[UploadedFile]:
        """Get file by hash"""
        db = self.get_db_session()
        try:
            return (
                db.query(UploadedFile)
                .filter(UploadedFile.file_hash == file_hash)
                .first()
            )
        finally:
            db.close()

    def get_files_by_status(self, status: FileStatus) -> List[UploadedFile]:
        """Get files by status"""
        db = self.get_db_session()
        try:
            return (
                db.query(UploadedFile)
                .filter(UploadedFile.status == status)
                .order_by(UploadedFile.created_at)
                .all()
            )
        finally:
            db.close()

    def get_all_files(self, limit: int = None, offset: int = 0) -> List[UploadedFile]:
        """Get all files with optional pagination (if limit is None, returns all files)"""
        db = self.get_db_session()
        try:
            query = db.query(UploadedFile).order_by(UploadedFile.created_at.desc())
            if limit is not None:
                query = query.limit(limit)
            if offset > 0:
                query = query.offset(offset)
            return query.all()
        finally:
            db.close()

    def update_file_status(
        self, file_id: int, new_status: FileStatus, error_message: str = None, reason: str = None
    ) -> bool:
        """Update file status"""
        db = self.get_db_session()
        try:
            file_record = (
                db.query(UploadedFile).filter(UploadedFile.id == file_id).first()
            )
            if not file_record:
                return False

            file_record.update_status(new_status, error_message, reason)
            db.commit()
            return True

        except Exception as e:
            db.rollback()
            raise e
        finally:
            db.close()

    def delete_file(self, file_id: int) -> bool:
        """Delete file record from database"""
        db = self.get_db_session()
        try:
            file_record = (
                db.query(UploadedFile).filter(UploadedFile.id == file_id).first()
            )
            if not file_record:
                logging.warning(f"⚠️ File not found in database: ID={file_id}")
                return False

            db.delete(file_record)
            db.commit()
            logging.info(
                f"✅ File deleted from database: ID={file_id}, filename={file_record.filename}"
            )
            return True

        except Exception as e:
            db.rollback()
            logging.error(
                f"❌ Failed to delete file from database: ID={file_id}, error={str(e)}",
                exc_info=True,
            )
            raise e
        finally:
            db.close()

    def get_queue_stats(self) -> dict:
        """Get queue statistics"""
        db = self.get_db_session()
        try:
            stats = {}
            for status in FileStatus:
                count = (
                    db.query(UploadedFile).filter(UploadedFile.status == status).count()
                )
                stats[status.value] = count

            stats["total"] = db.query(UploadedFile).count()
            return stats

        finally:
            db.close()

    def update_stages_and_sync_neo4j(
        self,
        file_id: int,
        graph=None,
        database: str = None,
        upload_status: str = None,
        chunking_status: str = None,
        graph_status: str = None,
        embedding_status: str = None,
        error_message: str = None,
    ) -> bool:
        """
        Update file stages in queue DB and sync status to Neo4j Document node

        Args:
            file_id: File ID in queue DB
            graph: Neo4j graph connection (optional, for syncing)
            database: Neo4j database name (for syncing)
            upload_status: New upload status
            chunking_status: New chunking status
            graph_status: New graph creation status
            embedding_status: New embedding status
            error_message: Error message if any stage failed

        Returns:
            True if successful, False otherwise
        """
        db = self.get_db_session()
        try:
            file_record = (
                db.query(UploadedFile).filter(UploadedFile.id == file_id).first()
            )
            if not file_record:
                return False

            # Update provided stages
            if upload_status:
                file_record.upload_status = upload_status
            if chunking_status:
                file_record.chunking_status = chunking_status
            if graph_status:
                file_record.graph_status = graph_status
            if embedding_status:
                file_record.embedding_status = embedding_status

            if error_message:
                file_record.processing_error = error_message

            file_record.updated_at = datetime.utcnow()
            db.commit()

            # Sync to Neo4j if graph connection provided
            if graph:
                try:
                    from src.models.status_sync import sync_queue_db_status_to_neo4j

                    sync_queue_db_status_to_neo4j(
                        graph=graph,
                        file_name=file_record.filename,
                        upload_status=file_record.upload_status,
                        chunking_status=file_record.chunking_status,
                        graph_status=file_record.graph_status,
                        embedding_status=file_record.embedding_status,
                        database=database,
                    )
                    logging.info(
                        f"✅ Synced file {file_id} ({file_record.filename}) status to Neo4j"
                    )
                except Exception as sync_error:
                    logging.warning(f"⚠️ Could not sync to Neo4j: {str(sync_error)}")
                    # Don't fail the update if Neo4j sync fails

            return True

        except Exception as e:
            db.rollback()
            logging.error(f"Error updating stages and syncing: {str(e)}")
            raise e
        finally:
            db.close()


# Global database instance
_db_instance = None


def get_file_queue_db(db_path: str = None) -> FileQueueDatabase:
    """Get global database instance"""
    global _db_instance

    if _db_instance is None:
        # Check environment variable for full URL first (PostgreSQL)
        db_url = os.getenv("QUEUE_DB_URL")
        
        if not db_url:
            if db_path is None:
                # Check environment variable first
                env_db_path = os.getenv("QUEUE_DB_PATH")
                if env_db_path:
                    db_path = env_db_path
                else:
                    # Default path: backend/queue.db
                    current_dir = Path(__file__).parent.parent.parent  # backend/
                    db_path = current_dir / "queue.db"
            
            # SQLite fallback
            _db_instance = FileQueueDatabase(db_path=str(db_path))
        else:
            # PostgreSQL or other URL based DB
            _db_instance = FileQueueDatabase(db_url=db_url)
            
    return _db_instance
