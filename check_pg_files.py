#!/usr/bin/env python
"""Check last file operations in PostgreSQL"""
import os
import sys
from sqlalchemy import create_engine, text

# Connection URL
POSTGRES_URL = os.getenv(
    "QUEUE_DB_URL",
    "postgresql://postgres:postgres@localhost:5432/llm_graph_builder",
)

engine = create_engine(POSTGRES_URL)

with engine.connect() as conn:
    result = conn.execute(text("""
        SELECT 
            id, 
            filename, 
            original_name, 
            upload_status, 
            chunking_status, 
            graph_status, 
            embedding_status,
            created_at, 
            updated_at
        FROM uploaded_files 
        ORDER BY id DESC 
        LIMIT 15
    """))
    
    rows = result.fetchall()
    
    if rows:
        print("\n📊 Son 15 Dosya İşlemi:\n")
        print(f"{'ID':<6} {'Original Name':<50} {'Upload':<12} {'Chunking':<12} {'Graph':<12} {'Embedding':<12} {'Updated At':<20}")
        print("-" * 140)
        
        for row in rows:
            id_val, filename, original_name, upload_status, chunking_status, graph_status, embedding_status, created_at, updated_at = row
            original_name_short = (original_name[:47] + '...') if original_name and len(original_name) > 50 else (original_name or 'N/A')
            updated_at_str = str(updated_at)[:19] if updated_at else 'N/A'
            upload_status = upload_status or 'N/A'
            chunking_status = chunking_status or 'N/A'
            graph_status = graph_status or 'N/A'
            embedding_status = embedding_status or 'N/A'
            
            print(f"{id_val:<6} {original_name_short:<50} {upload_status:<12} {chunking_status:<12} {graph_status:<12} {embedding_status:<12} {updated_at_str:<20}")
        
        print("\n")
    else:
        print("No rows found in uploaded_files table.")







