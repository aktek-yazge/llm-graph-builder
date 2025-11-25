#!/usr/bin/env python
"""Simple query to list first 10 uploaded files from PostgreSQL."""
import os
from sqlalchemy import create_engine, text

# Connection URL – same as used by the app
POSTGRES_URL = os.getenv(
    "POSTGRES_MIGRATION_URL",
    "postgresql://postgres:postgres@localhost:5432/llm_graph_builder",
)

engine = create_engine(POSTGRES_URL)

with engine.connect() as conn:
    result = conn.execute(text("SELECT id, filename, status FROM uploaded_files ORDER BY id LIMIT 10"))
    rows = result.fetchall()
    if rows:
        for row in rows:
            print(row)
    else:
        print("No rows found in uploaded_files table.")
