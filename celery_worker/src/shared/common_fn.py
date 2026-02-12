# -*- coding: utf-8 -*-
"""
Common Functions - Proxy Module (Celery Worker)

Bu dosya /workspace/shared/common_fn.py'dan re-export yapar.
Tüm fonksiyonlar ortak shared modülünden gelir.

Kullanım değişmedi:
    from src.shared.common_fn import create_graph_database_connection, load_embedding_model
"""

from shared.common_fn import *  # noqa: F401,F403
from shared.common_fn import (  # noqa: F401 - explicit re-exports for IDE support
    check_url_source,
    get_chunk_and_graphDocument,
    create_graph_database_connection,
    load_embedding_model,
    save_graphDocuments_in_neo4j,
    handle_backticks_nodes_relationship_id_type,
    execute_graph_query,
    delete_uploaded_local_file,
    close_db_connection,
    create_gcs_bucket_folder_name_hashed,
    formatted_time,
    last_url_segment,
    get_bedrock_embeddings,
)
