# -*- coding: utf-8 -*-
"""
Prometheus Metrics Module - Extended Observability

LAYER 8 Features:
- Custom metrics: llm_tokens_total, llm_latency_seconds
- Cache hit rate, entity resolution success rate
- Celery task duration, queue depth metrics
- Grafana dashboard compatible

Kullanım:
    from src.shared.prometheus_metrics import (
        llm_tokens_counter,
        llm_latency_histogram,
        cache_hit_counter,
        track_llm_call,
    )
    
    # LLM çağrısını izle
    with track_llm_call("gpt-4", "chat"):
        response = llm.invoke(...)

Environment Variables:
    PROMETHEUS_METRICS_ENABLED: Enable/disable metrics (default: true)
    PROMETHEUS_PORT: Metrics endpoint port (default: 9090)
"""

import os
import time
import logging
from typing import Optional, Dict, Any
from functools import wraps
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# Environment configuration
PROMETHEUS_METRICS_ENABLED = os.getenv("PROMETHEUS_METRICS_ENABLED", "true").lower() in ("true", "1", "yes")
PROMETHEUS_PORT = int(os.getenv("PROMETHEUS_PORT", "9090"))

# Try to import prometheus_client
try:
    from prometheus_client import Counter, Histogram, Gauge, Info, start_http_server
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    logger.warning("⚠️ prometheus_client not installed. Run: pip install prometheus-client")


# ============================================================================
# METRIC DEFINITIONS
# ============================================================================

if PROMETHEUS_AVAILABLE and PROMETHEUS_METRICS_ENABLED:
    # LLM Metrics
    llm_tokens_counter = Counter(
        "llm_tokens_total",
        "Total LLM tokens used",
        ["model", "operation", "token_type"]  # input, output, cached
    )
    
    llm_latency_histogram = Histogram(
        "llm_latency_seconds",
        "LLM call latency in seconds",
        ["model", "operation"],
        buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0]
    )
    
    llm_calls_counter = Counter(
        "llm_calls_total",
        "Total LLM calls",
        ["model", "operation", "status"]  # success, error, cache_hit
    )
    
    llm_cost_counter = Counter(
        "llm_cost_usd_total",
        "Total LLM cost in USD",
        ["model"]
    )
    
    # Cache Metrics
    cache_hit_counter = Counter(
        "cache_hits_total",
        "Cache hits",
        ["cache_type"]  # query_cache, redis_cache, schema_cache
    )
    
    cache_miss_counter = Counter(
        "cache_misses_total",
        "Cache misses",
        ["cache_type"]
    )
    
    cache_size_gauge = Gauge(
        "cache_size_entries",
        "Current cache size",
        ["cache_type"]
    )
    
    # Entity Resolution Metrics
    entity_resolution_counter = Counter(
        "entity_resolution_total",
        "Entity resolution operations",
        ["operation", "status"]  # merge, skip, error
    )
    
    entity_clusters_gauge = Gauge(
        "entity_clusters_count",
        "Number of entity clusters"
    )
    
    # Celery Task Metrics
    celery_task_duration = Histogram(
        "celery_task_duration_seconds",
        "Celery task duration in seconds",
        ["task_name", "status"],
        buckets=[1.0, 5.0, 10.0, 30.0, 60.0, 300.0, 600.0]
    )
    
    celery_queue_depth = Gauge(
        "celery_queue_depth",
        "Celery queue depth",
        ["queue_name"]
    )
    
    celery_active_tasks = Gauge(
        "celery_active_tasks",
        "Currently active Celery tasks",
        ["task_name"]
    )
    
    # Document Processing Metrics
    documents_processed_counter = Counter(
        "documents_processed_total",
        "Documents processed",
        ["status"]  # success, failed, cancelled
    )
    
    chunks_created_counter = Counter(
        "chunks_created_total",
        "Chunks created"
    )
    
    entities_extracted_counter = Counter(
        "entities_extracted_total",
        "Entities extracted",
        ["entity_type"]
    )
    
    # API Metrics
    api_requests_counter = Counter(
        "api_requests_total",
        "API requests",
        ["endpoint", "method", "status_code"]
    )
    
    api_latency_histogram = Histogram(
        "api_latency_seconds",
        "API request latency",
        ["endpoint", "method"],
        buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 5.0]
    )
    
    rate_limit_counter = Counter(
        "rate_limit_exceeded_total",
        "Rate limit exceeded events",
        ["endpoint"]
    )
    
    # Application Info
    app_info = Info("app", "Application information")
    
    logger.info("✅ Prometheus metrics initialized")
    
else:
    # Dummy metrics when Prometheus not available
    class DummyMetric:
        def labels(self, *args, **kwargs):
            return self
        def inc(self, *args, **kwargs):
            pass
        def dec(self, *args, **kwargs):
            pass
        def set(self, *args, **kwargs):
            pass
        def observe(self, *args, **kwargs):
            pass
        def info(self, *args, **kwargs):
            pass
    
    llm_tokens_counter = DummyMetric()
    llm_latency_histogram = DummyMetric()
    llm_calls_counter = DummyMetric()
    llm_cost_counter = DummyMetric()
    cache_hit_counter = DummyMetric()
    cache_miss_counter = DummyMetric()
    cache_size_gauge = DummyMetric()
    entity_resolution_counter = DummyMetric()
    entity_clusters_gauge = DummyMetric()
    celery_task_duration = DummyMetric()
    celery_queue_depth = DummyMetric()
    celery_active_tasks = DummyMetric()
    documents_processed_counter = DummyMetric()
    chunks_created_counter = DummyMetric()
    entities_extracted_counter = DummyMetric()
    api_requests_counter = DummyMetric()
    api_latency_histogram = DummyMetric()
    rate_limit_counter = DummyMetric()
    app_info = DummyMetric()


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

@contextmanager
def track_llm_call(model: str, operation: str = "chat"):
    """
    Context manager for tracking LLM calls.
    
    Usage:
        with track_llm_call("gpt-4", "entity_extraction") as tracker:
            response = llm.invoke(...)
            tracker.set_tokens(input=100, output=50, cached=80)
    """
    start_time = time.time()
    
    class Tracker:
        def __init__(self):
            self.input_tokens = 0
            self.output_tokens = 0
            self.cached_tokens = 0
            self.status = "success"
            self.cost = 0.0
        
        def set_tokens(self, input: int = 0, output: int = 0, cached: int = 0):
            self.input_tokens = input
            self.output_tokens = output
            self.cached_tokens = cached
        
        def set_status(self, status: str):
            self.status = status
        
        def set_cost(self, cost: float):
            self.cost = cost
    
    tracker = Tracker()
    
    try:
        yield tracker
    except Exception as e:
        tracker.status = "error"
        raise
    finally:
        duration = time.time() - start_time
        
        # Record metrics
        llm_latency_histogram.labels(model=model, operation=operation).observe(duration)
        llm_calls_counter.labels(model=model, operation=operation, status=tracker.status).inc()
        
        if tracker.input_tokens:
            llm_tokens_counter.labels(model=model, operation=operation, token_type="input").inc(tracker.input_tokens)
        if tracker.output_tokens:
            llm_tokens_counter.labels(model=model, operation=operation, token_type="output").inc(tracker.output_tokens)
        if tracker.cached_tokens:
            llm_tokens_counter.labels(model=model, operation=operation, token_type="cached").inc(tracker.cached_tokens)
        
        if tracker.cost:
            llm_cost_counter.labels(model=model).inc(tracker.cost)


def track_cache_access(cache_type: str, hit: bool):
    """Track cache hit/miss"""
    if hit:
        cache_hit_counter.labels(cache_type=cache_type).inc()
    else:
        cache_miss_counter.labels(cache_type=cache_type).inc()


def track_celery_task(task_name: str, status: str, duration: float):
    """Track Celery task completion"""
    celery_task_duration.labels(task_name=task_name, status=status).observe(duration)


def track_document_processed(status: str):
    """Track document processing"""
    documents_processed_counter.labels(status=status).inc()


def track_api_request(endpoint: str, method: str, status_code: int, duration: float):
    """Track API request"""
    api_requests_counter.labels(endpoint=endpoint, method=method, status_code=str(status_code)).inc()
    api_latency_histogram.labels(endpoint=endpoint, method=method).observe(duration)


def start_metrics_server(port: int = None):
    """
    Start Prometheus metrics HTTP server.
    
    Args:
        port: Port number (default: PROMETHEUS_PORT)
    """
    if not PROMETHEUS_AVAILABLE or not PROMETHEUS_METRICS_ENABLED:
        return
    
    port = port or PROMETHEUS_PORT
    
    try:
        start_http_server(port)
        logger.info(f"✅ Prometheus metrics server started on port {port}")
    except Exception as e:
        logger.warning(f"⚠️ Failed to start Prometheus metrics server: {e}")


def get_metrics_summary() -> Dict[str, Any]:
    """Get current metrics summary"""
    # This would require collecting current metric values
    # For now, return a placeholder
    return {
        "enabled": PROMETHEUS_METRICS_ENABLED,
        "available": PROMETHEUS_AVAILABLE,
        "port": PROMETHEUS_PORT,
    }

