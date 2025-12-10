"""
OpenTelemetry Tracing Setup for Tempo Integration
Distributed tracing - tüm HTTP isteklerini, LLM çağrılarını ve Neo4j sorgularını izler
"""
import os
import logging
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Check if tracing is enabled
OTEL_TRACING_ENABLED = os.getenv("OTEL_TRACING_ENABLED", "true").lower() in ("true", "1", "yes")
OTEL_EXPORTER_OTLP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
OTEL_SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "llm-graph-builder")
OTEL_ENVIRONMENT = os.getenv("OTEL_ENVIRONMENT", "development")

# Global tracer
_tracer = None
_initialized = False


def initialize_tracing():
    """
    OpenTelemetry Tracing'i başlat
    Bu fonksiyon uygulama başlangıcında bir kez çağrılmalı
    """
    global _tracer, _initialized
    
    if _initialized:
        logging.info("⚠️ OpenTelemetry tracing zaten başlatılmış")
        return _tracer
    
    if not OTEL_TRACING_ENABLED:
        logging.info("ℹ️ OpenTelemetry tracing devre dışı (OTEL_TRACING_ENABLED=false)")
        _initialized = True
        return None
    
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        
        logging.info(f"🔧 OpenTelemetry Tracing başlatılıyor...")
        logging.info(f"📡 OTLP Endpoint: {OTEL_EXPORTER_OTLP_ENDPOINT}")
        logging.info(f"🏷️ Service Name: {OTEL_SERVICE_NAME}")
        logging.info(f"🌍 Environment: {OTEL_ENVIRONMENT}")
        
        # Resource tanımla - servis bilgileri
        resource = Resource.create({
            SERVICE_NAME: OTEL_SERVICE_NAME,
            SERVICE_VERSION: "1.0.0",
            "deployment.environment": OTEL_ENVIRONMENT,
            "service.namespace": "llm-graph-builder",
        })
        
        # TracerProvider oluştur
        provider = TracerProvider(resource=resource)
        
        # OTLP Exporter - Tempo'ya gönderir
        otlp_exporter = OTLPSpanExporter(
            endpoint=OTEL_EXPORTER_OTLP_ENDPOINT,
            insecure=True  # TLS olmadan bağlan (local development için)
        )
        
        # BatchSpanProcessor - span'ları batch halinde gönderir (performans için)
        span_processor = BatchSpanProcessor(otlp_exporter)
        provider.add_span_processor(span_processor)
        
        # Global TracerProvider olarak ayarla
        trace.set_tracer_provider(provider)
        
        # Tracer al
        _tracer = trace.get_tracer(__name__)
        _initialized = True
        
        logging.info("✅ OpenTelemetry Tracing başarıyla başlatıldı")
        logging.info("📊 Trace'ler Grafana Tempo'ya gönderiliyor")
        
        return _tracer
        
    except ImportError as e:
        logging.warning(f"⚠️ OpenTelemetry modülleri bulunamadı: {e}")
        logging.warning("pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp-proto-grpc")
        _initialized = True
        return None
    except Exception as e:
        logging.error(f"❌ OpenTelemetry Tracing başlatma hatası: {e}")
        _initialized = True
        return None


def instrument_fastapi(app):
    """
    FastAPI uygulamasını OpenTelemetry ile instrument et
    Tüm HTTP endpoint'lerini otomatik olarak trace eder
    """
    if not OTEL_TRACING_ENABLED:
        logging.info("ℹ️ FastAPI instrumentation atlandı (tracing devre dışı)")
        return
    
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        
        FastAPIInstrumentor.instrument_app(app)
        logging.info("✅ FastAPI instrumentation aktif - tüm HTTP istekleri trace ediliyor")
        
    except ImportError as e:
        logging.warning(f"⚠️ FastAPI instrumentor bulunamadı: {e}")
        logging.warning("pip install opentelemetry-instrumentation-fastapi")
    except Exception as e:
        logging.error(f"❌ FastAPI instrumentation hatası: {e}")


def instrument_requests():
    """
    requests kütüphanesini instrument et (outgoing HTTP calls)
    """
    if not OTEL_TRACING_ENABLED:
        return
    
    try:
        from opentelemetry.instrumentation.requests import RequestsInstrumentor
        RequestsInstrumentor().instrument()
        logging.info("✅ Requests instrumentation aktif")
    except ImportError:
        pass  # Optional
    except Exception as e:
        logging.warning(f"⚠️ Requests instrumentation hatası: {e}")


def instrument_httpx():
    """
    httpx kütüphanesini instrument et (async HTTP calls - LangChain için)
    """
    if not OTEL_TRACING_ENABLED:
        return
    
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        HTTPXClientInstrumentor().instrument()
        logging.info("✅ HTTPX instrumentation aktif")
    except ImportError:
        pass  # Optional
    except Exception as e:
        logging.warning(f"⚠️ HTTPX instrumentation hatası: {e}")


def get_tracer(name: str = __name__):
    """
    Tracer instance al - custom span'lar oluşturmak için kullan
    
    Kullanım:
        from src.otel_tracing_setup import get_tracer
        
        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("my_operation") as span:
            span.set_attribute("custom.attribute", "value")
            # ... işlem yap
    """
    global _tracer, _initialized
    
    if not _initialized:
        initialize_tracing()
    
    if not OTEL_TRACING_ENABLED or _tracer is None:
        # Tracing kapalıysa no-op tracer döndür
        from opentelemetry import trace
        return trace.get_tracer(name)
    
    from opentelemetry import trace
    return trace.get_tracer(name)


def create_span(name: str, attributes: dict | None = None):
    """
    Yeni bir span oluştur (context manager olarak kullanılabilir)
    
    Kullanım:
        with create_span("llm_call", {"model": "gpt-4", "tokens": 1000}):
            # ... LLM çağrısı
    """
    tracer = get_tracer()
    span = tracer.start_as_current_span(name)
    
    if attributes and OTEL_TRACING_ENABLED:
        from opentelemetry import trace
        current_span = trace.get_current_span()
        for key, value in attributes.items():
            current_span.set_attribute(key, value)
    
    return span


def add_span_attribute(key: str, value):
    """
    Mevcut span'a attribute ekle
    
    Kullanım:
        add_span_attribute("llm.model", "gpt-4o")
        add_span_attribute("llm.tokens", 1500)
    """
    if not OTEL_TRACING_ENABLED:
        return
    
    try:
        from opentelemetry import trace
        current_span = trace.get_current_span()
        if current_span:
            current_span.set_attribute(key, value)
    except Exception:
        pass


def add_span_event(name: str, attributes: dict | None = None):
    """
    Mevcut span'a event ekle (log-like bilgi)
    
    Kullanım:
        add_span_event("chunk_processed", {"chunk_id": "123", "size": 1024})
    """
    if not OTEL_TRACING_ENABLED:
        return
    
    try:
        from opentelemetry import trace
        current_span = trace.get_current_span()
        if current_span:
            current_span.add_event(name, attributes or {})
    except Exception:
        pass


def record_exception(exception: Exception):
    """
    Mevcut span'a exception kaydet
    
    Kullanım:
        try:
            ...
        except Exception as e:
            record_exception(e)
            raise
    """
    if not OTEL_TRACING_ENABLED:
        return
    
    try:
        from opentelemetry import trace
        current_span = trace.get_current_span()
        if current_span:
            current_span.record_exception(exception)
            current_span.set_status(trace.Status(trace.StatusCode.ERROR, str(exception)))
    except Exception:
        pass


# Test için
if __name__ == "__main__":
    initialize_tracing()
    
    tracer = get_tracer("test")
    with tracer.start_as_current_span("test_operation") as span:
        span.set_attribute("test.attribute", "value")
        add_span_event("test_event", {"key": "value"})
        logging.info("Test span created")
    
    logging.info("Test completed - check Grafana Tempo for traces")

