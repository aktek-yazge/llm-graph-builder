# -*- coding: utf-8 -*-
"""
Langfuse Client - Proxy Module

Bu dosya /workspace/shared/langfuse_client.py'dan re-export yapar.
Tüm fonksiyonlar ve sınıflar ortak shared modülünden gelir.

Kullanım değişmedi:
    from src.shared.langfuse_client import get_langfuse, trace_llm_call, observe
"""

from shared.langfuse_client import *  # noqa: F401,F403
from shared.langfuse_client import (  # noqa: F401 - explicit re-exports for IDE support
    is_langfuse_enabled,
    get_langfuse,
    get_langfuse_callback_handler,
    DummySpan,
    trace_llm_call,
    observe,
    log_llm_usage,
    trace_document_processing,
    langfuse_session,
    start_langfuse_session,
    end_langfuse_session,
    flush_langfuse,
    shutdown_langfuse,
    get_prompt,
    create_prompt,
    clear_prompt_cache,
    get_prompt_with_tracing,
    create_dataset,
    get_dataset,
    add_dataset_item,
    add_trace_to_dataset,
    run_experiment,
    get_dataset_items,
)
