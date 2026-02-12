# -*- coding: utf-8 -*-
"""
Shared Package - Backend ve Celery Worker tarafından ortak kullanılan modüller.

Bu paket, her iki projenin de ihtiyaç duyduğu ortak fonksiyonları barındırır:
- langfuse_client: LLM observability (Langfuse entegrasyonu)
- common_fn: Neo4j bağlantı, embedding, graph işlemleri
- llm_graph_builder_exception: Özel exception sınıfı

Kullanım:
    from shared.langfuse_client import get_langfuse, trace_llm_call
    from shared.common_fn import create_graph_database_connection
    from shared.llm_graph_builder_exception import LLMGraphBuilderException
"""
