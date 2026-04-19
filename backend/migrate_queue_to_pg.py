import os
from pathlib import Path
# Add project root (two levels up) to PYTHONPATH so that 'src' package is importable
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.models.file_queue_models import Base, UploadedFile

# ----------------------------------------------------------------------
# 1️⃣ Paths / connection URLs
# ----------------------------------------------------------------------
SQLITE_PATH = Path(__file__).parent / "queue.db"  # backend/queue.db
POSTGRES_URL = os.getenv(
    "POSTGRES_MIGRATION_URL",
    "postgresql://postgres:postgres@localhost:5432/llm_graph_builder",
)

# ----------------------------------------------------------------------
# 2️⃣ Engines & sessions
# ----------------------------------------------------------------------
sqlite_engine = create_engine(f"sqlite:///{SQLITE_PATH}", future=True)
postgres_engine = create_engine(POSTGRES_URL, future=True)

SQLiteSession = sessionmaker(bind=sqlite_engine, future=True)
PostgresSession = sessionmaker(bind=postgres_engine, future=True)

# ----------------------------------------------------------------------
# 3️⃣ Ensure PostgreSQL schema exists (creates tables if missing)
# ----------------------------------------------------------------------
Base.metadata.create_all(bind=postgres_engine)

# ----------------------------------------------------------------------
# 4️⃣ Copy data
# ----------------------------------------------------------------------
sqlite_session = SQLiteSession()
postgres_session = PostgresSession()

copied = 0
try:
    # Load all rows from SQLite (as ORM objects)
    sqlite_rows = sqlite_session.query(UploadedFile).all()
    for row in sqlite_rows:
        # Build a new UploadedFile instance for PostgreSQL without the primary key
        new_row = UploadedFile(
            filename=row.filename,
            original_name=row.original_name,
            file_path=row.file_path,
            upload_date=row.upload_date,
            file_size=row.file_size,
            file_hash=row.file_hash,
            status=row.status,
            upload_status=row.upload_status,
            chunking_status=row.chunking_status,
            graph_status=row.graph_status,
            embedding_status=row.embedding_status,
            created_at=row.created_at,
            updated_at=row.updated_at,
            chunking_started_at=row.chunking_started_at,
            chunking_completed_at=row.chunking_completed_at,
            graph_started_at=row.graph_started_at,
            graph_completed_at=row.graph_completed_at,
            embedding_started_at=row.embedding_started_at,
            embedding_completed_at=row.embedding_completed_at,
            processing_started_at=row.processing_started_at,
            processing_completed_at=row.processing_completed_at,
            processing_error=row.processing_error,
            neo4j_uri=row.neo4j_uri,
            neo4j_database=row.neo4j_database,
            model_used=row.model_used,
            generate_embedding=row.generate_embedding,
            doc_link=row.doc_link,
            page_images=row.page_images,
            markdown_path=row.markdown_path,
            auto_process=row.auto_process,
            reason=row.reason,
        )
        postgres_session.add(new_row)
        copied += 1
    postgres_session.commit()
    print(f"✅ Migration finished – {copied} rows copied to PostgreSQL.")
except Exception as e:
    postgres_session.rollback()
    print(f"❌ Migration failed: {e}")
finally:
    sqlite_session.close()
    postgres_session.close()
