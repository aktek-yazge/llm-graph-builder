"""
OpenTelemetry Logging Setup for Loki Integration
Bu dosya mevcut kodda değişiklik yapmadan tüm logları Loki'ye göndermek için kullanılır.
"""
import os
import logging
import sys
import json
from datetime import datetime, timezone
from io import StringIO
from dotenv import load_dotenv
from logging.handlers import RotatingFileHandler


class ConsoleHandler(logging.StreamHandler):
    """Console'a sadece raw mesaj basan özel handler"""
    
    def emit(self, record):
        try:
            # Orijinal mesajı al (JSON handler değiştirmeden önce)
            if hasattr(record, 'msg') and record.args:
                try:
                    # Format edilmiş mesajı al
                    message = record.msg % record.args if record.args else record.msg
                except (TypeError, ValueError):
                    # Format hatası varsa sadece msg'yi kullan
                    message = str(record.msg)
            else:
                message = str(record.msg)
            
            # Direkt console'a yaz
            self.stream.write(message + '\n')
            self.flush()
        except Exception:
            # Hata durumunda sessizce geç
            pass


class JSONRotatingFileHandler(RotatingFileHandler):
    """JSON file handler with rotation support - log dosyası büyüdüğünde otomatik rotate eder"""
    
    def __init__(self, file_path: str = "logs/simple-logs.jsonl", 
                 max_bytes: int = 10*1024*1024,  # 10MB
                 backup_count: int = 5):         # 5 backup dosyası tut
        super().__init__(file_path, maxBytes=max_bytes, backupCount=backup_count, encoding='utf-8')
        self.last_messages = set()  # Son mesajları takip et (tekrar önlemek için)
        self.last_cleanup = datetime.now(timezone.utc)
        # Logs klasörünü oluştur
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
    
    def emit(self, record):
        """Log record'unu JSON formatında dosyaya yaz"""
        try:
            current_time = datetime.now(timezone.utc)
            
            # Her 60 saniyede bir deduplication cache'ini temizle
            if (current_time - self.last_cleanup).seconds > 60:
                self.last_messages.clear()
                self.last_cleanup = current_time
            
            # Format message güvenli şekilde
            try:
                # Önce record.getMessage() çağrısını güvenli hale getir
                try:
                    message = record.getMessage()
                except (TypeError, ValueError):
                    # String formatting hatası varsa, sadece msg'i kullan
                    message = str(record.msg)
                    if record.args:
                        message += f" [args: {record.args}]"
                
                # Şimdi format et
                message = self.format(record)
            except Exception as e:
                # Herhangi bir format hatası varsa, basit format kullan
                try:
                    message = str(record.msg)
                    if record.args:
                        message += f" [args: {record.args}]"
                except:
                    message = f"[Logging Error: {str(e)}]"
            
            # Tekrar eden mesajları filtrele (aynı mesajın 1 saniye içinde tekrarını engelle)
            message_key = f"{message[:100]}_{int(current_time.timestamp())}"  # Mesajı kısalt
            if message_key in self.last_messages:
                return
            self.last_messages.add(message_key)
            
            # Component ve operation bilgilerini güvenli şekilde extract et
            component = getattr(record, 'component', 'backend')
            operation = getattr(record, 'operation', 'general')

            # Logger name'den component/operation parse et (örn: "upload.file_processing")
            try:
                if isinstance(record.name, str) and '.' in record.name:
                    name_parts = record.name.split('.')
                    if len(name_parts) >= 2:
                        component = name_parts[0]
                        operation = name_parts[1]
            except Exception:
                # parsing hatası olursa mevcut component/operation kullan
                pass

            # HTTP request bilgilerini extract et (öncelik ver)
            if hasattr(record, 'method'):
                component = 'http_server'
                operation = 'http_request'
            
            log_record = {
                "timestamp": current_time.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z",
                "level": record.levelname,
                "message": message,
                "component": component,
                "operation": operation,
                "logger_name": record.name,
                "service_name": "llm-graph-builder"
            }
            
            # HTTP request detaylarını ekle
            if hasattr(record, 'method'):
                log_record.update({
                    "method": getattr(record, 'method', ''),
                    "path": getattr(record, 'path', ''),
                    "status_code": getattr(record, 'status_code', ''),
                    "client_ip": getattr(record, 'client_ip', '')
                })
            
            # JSON formatında yaz
            json_line = json.dumps(log_record, ensure_ascii=False) + '\n'
            
            # RotatingFileHandler'ın emit metodunu kullan (otomatik rotation ile)
            # record.args'ı temizle ki format hatası olmasın
            record.msg = json_line.rstrip()  # \n karakterini kaldır, RotatingFileHandler kendi ekleyecek
            record.args = None  # args'ı temizle ki string formatting hatası olmasın
            super().emit(record)
                
        except Exception as e:
            # Hata durumunda orijinal dosyaya fallback
            print(f"JSON log yazma hatası: {e}")


# Eski JSONFileHandler'ı yedek olarak tut
class JSONFileHandler(logging.Handler):
    """Simple JSON file handler that writes logs directly to file"""
    
    def __init__(self, file_path: str = "logs/simple-logs.jsonl"):
        super().__init__()
        self.file_path = file_path
        self.last_messages = set()  # Son mesajları takip et (tekrar önlemek için)
        self.last_cleanup = datetime.now(timezone.utc)
        # Logs klasörünü oluştur
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
    
    def emit(self, record):
        """Log record'unu JSON formatında dosyaya yaz"""
        try:
            current_time = datetime.now(timezone.utc)
            
            # Her 60 saniyede bir deduplication cache'ini temizle
            if (current_time - self.last_cleanup).seconds > 60:
                self.last_messages.clear()
                self.last_cleanup = current_time
            
            # Format message güvenli şekilde
            try:
                # Önce record.getMessage() çağrısını güvenli hale getir
                try:
                    message = record.getMessage()
                except (TypeError, ValueError):
                    # String formatting hatası varsa, sadece msg'i kullan
                    message = str(record.msg)
                    if record.args:
                        message += f" [args: {record.args}]"
                
                # Şimdi format et
                message = self.format(record)
            except Exception as e:
                # Herhangi bir format hatası varsa, basit format kullan
                try:
                    message = str(record.msg)
                    if record.args:
                        message += f" [args: {record.args}]"
                except:
                    message = f"[Logging Error: {str(e)}]"
            
            # Tekrar eden mesajları filtrele (aynı mesajın 1 saniye içinde tekrarını engelle)
            message_key = f"{message[:100]}_{int(current_time.timestamp())}"  # Mesajı kısalt
            if message_key in self.last_messages:
                return
            self.last_messages.add(message_key)
            
            # Component ve operation bilgilerini güvenli şekilde extract et
            component = getattr(record, 'component', 'backend')
            operation = getattr(record, 'operation', 'general')
            
            # HTTP request bilgilerini extract et
            if hasattr(record, 'method'):
                component = 'http_server'
                operation = 'http_request'
            
            log_record = {
                "timestamp": current_time.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z",
                "level": record.levelname,
                "message": message,
                "component": component,
                "operation": operation,
                "logger_name": record.name,
                "service_name": "llm-graph-builder"
            }
            
            # HTTP request detaylarını ekle
            if hasattr(record, 'method'):
                log_record.update({
                    "method": getattr(record, 'method', ''),
                    "path": getattr(record, 'path', ''),
                    "status_code": getattr(record, 'status_code', ''),
                    "client_ip": getattr(record, 'client_ip', '')
                })
            
            # Dosyaya yaz (append mode) ve hemen flush et
            with open(self.file_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_record, ensure_ascii=False) + '\n')
                f.flush()  # Hemen disk'e yaz
                
        except Exception as e:
            # Hata durumunda console'a yazdır ama logging loop'una girme
            print(f"❌ JSON log yazma hatası: {e}", file=sys.__stderr__)


# Eski OpenTelemetry kodlarını yorum yap
"""
try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.instrumentation.logging import LoggingInstrumentor
    from opentelemetry._logs import set_logger_provider
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor, LogExporter
    try:
        from opentelemetry.sdk._logs.export import LogData
    except ImportError:
        # Eski SDK versiyonları için fallback
        from opentelemetry.sdk._logs import LogRecord as LogData
    from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
    OPENTELEMETRY_AVAILABLE = True
except ImportError as e:
    print(f"⚠️ OpenTelemetry import hatası: {e} - Basit JSON logging kullanılacak")
    OPENTELEMETRY_AVAILABLE = False
"""


class PrintCapture:
    """Print fonksiyonlarını yakalayıp logging'e yönlendiren sınıf"""
    
    def __init__(self, original_stdout):
        self.original_stdout = original_stdout
        self.logger = logging.getLogger('print_capture')
        self._otel_captured: bool = False  # Flag to prevent double capture
        
    def write(self, text):
        # Boş satırları filtrele
        if text.strip():
            # Emoji'leri ve özel karakterleri koruyarak log yaz
            self.logger.info(f"PRINT: {text.strip()}")
        # Orijinal stdout'a da yaz (konsol çıktısını koru)
        self.original_stdout.write(text)
        
    def flush(self):
        self.original_stdout.flush()


def setup_simple_json_logging():
    """
    Basit JSON logging'i başlat - OpenTelemetry yerine
    Environment variables:
    - OTEL_SERVICE_NAME: Servis adı (default: llm-graph-builder)
    - OTEL_ENVIRONMENT: Ortam (default: development)
    """
    
    # .env dosyasını yükle
    load_dotenv()
    
    # Environment variables
    service_name = os.getenv('OTEL_SERVICE_NAME', 'llm-graph-builder')
    environment = os.getenv('OTEL_ENVIRONMENT', 'development')
    log_file_path = "logs/simple-logs.jsonl"  # Log dosya yolu tanımla
    
    print(f"🔧 JSON Logging başlatılıyor...")
    print(f"🏷️ Service Name: {service_name}")
    print(f"🌍 Environment: {environment}")
    
    # Rotating JSON handler'ı oluştur ve yapılandır (10MB boyutunda, 5 backup)
    json_handler = JSONRotatingFileHandler(
        log_file_path,
        max_bytes=10*1024*1024,  # 10MB
        backup_count=5           # 5 backup dosyası
    )
    json_handler.setLevel(logging.INFO)  # INFO ve üzeri logları yakala
    
    # Root logger'ı yapılandır
    root_logger = logging.getLogger()
    
    # Mevcut JSON handler'larını temizle (tekrar eden logları önlemek için)
    existing_json_handlers = [h for h in root_logger.handlers if isinstance(h, (JSONFileHandler, JSONRotatingFileHandler))]
    for handler_to_remove in existing_json_handlers:
        root_logger.removeHandler(handler_to_remove)
    
    # Console handler ekle (terminale de basılması için) - JSON handler'dan ÖNCE
    console_handler = ConsoleHandler()
    console_handler.setLevel(logging.INFO)  # INFO ve üzeri logları terminale bas
    
    # Mevcut console handler'larını temizle (tekrar önlemek için)
    existing_console_handlers = [h for h in root_logger.handlers if isinstance(h, (logging.StreamHandler, ConsoleHandler)) and hasattr(h, 'stream') and h.stream.name == '<stdout>']
    for handler_to_remove in existing_console_handlers:
        root_logger.removeHandler(handler_to_remove)
    
    # Console handler'ı ÖNCE ekle
    root_logger.addHandler(console_handler)
    
    # Yeni JSON handler'ı ekle
    root_logger.addHandler(json_handler)
    root_logger.setLevel(logging.INFO)  # INFO ve üzeri logları yakala
    
    # Gürültülü loggerları sustur (performans için)
    noisy_loggers = [
        'urllib3.connectionpool',
        'httpx',
        'requests.packages.urllib3.connectionpool',
        'neo4j.pool',
        'neo4j.io',
        'transformers.tokenization_utils_base',
        'datasets.arrow_dataset',
        'datasets.builder',
        'datasets.info'
    ]
    
    for logger_name in noisy_loggers:
        logging.getLogger(logger_name).setLevel(logging.WARNING)
    
    print("✅ JSON logging başarıyla yapılandırıldı")
    print("�️ Console logging eklendi (INFO+ terminale)")
    print("�🔇 Gürültülü loggerlar susturuldu")
    return json_handler
    """Print fonksiyonlarını yakalayıp logging'e yönlendiren sınıf"""
    
    def __init__(self, original_stdout):
        self.original_stdout = original_stdout
        self.logger = logging.getLogger('print_capture')
        
    def write(self, text):
        # Boş satırları filtrele
        if text.strip():
            # Emoji'leri ve özel karakterleri koruyarak log yaz
            self.logger.info(f"PRINT: {text.strip()}")
        # Orijinal stdout'a da yaz (konsol çıktısını koru)
        self.original_stdout.write(text)
        
    def flush(self):
        self.original_stdout.flush()


def setup_opentelemetry_logging():
    """
    Basit JSON logging'i başlat - OpenTelemetry yerine
    Environment variables:
    - OTEL_SERVICE_NAME: Servis adı (default: llm-graph-builder)
    - OTEL_ENVIRONMENT: Ortam (default: development)
    """
    return setup_simple_json_logging()


def setup_print_capture():
    """Print fonksiyonlarını yakalayıp logging'e yönlendir"""
    if not hasattr(sys.stdout, '_otel_captured'):
        original_stdout = sys.stdout
        sys.stdout = PrintCapture(original_stdout)
        sys.stdout._otel_captured = True
        print("✅ Print capture başarıyla yapılandırıldı")


# Global flag to prevent double initialization
_otel_logging_initialized = False


def initialize_otel_logging():
    """Ana başlatma fonksiyonu - bu fonksiyon main.py'de çağrılacak"""
    global _otel_logging_initialized
    
    # Tekrar başlatmayı önle
    if _otel_logging_initialized:
        print("⚠️ JSON logging zaten başlatılmış, tekrar başlatma atlanıyor")
        return
    
    try:
        # JSON logging'i başlat
        handler = setup_simple_json_logging()
        
        # Print capture'ı başlat
        setup_print_capture()
        
        # Uvicorn logger'ını JSON formatına yönlendir
        setup_uvicorn_logging()
        
        # Başlatma bayrağını işaretle
        _otel_logging_initialized = True
        
        # Test log'u gönder
        logger = logging.getLogger(__name__)
        logger.info("🚀 JSON logging sistemi aktif - tüm loglar Loki'ye gönderiliyor")
        print("🎯 Print capture aktif - print() çağrıları da loglanıyor")
        
        return handler
        
    except Exception as e:
        print(f"❌ JSON logging başlatma hatası: {e}")
        return None


def setup_uvicorn_logging():
    """Uvicorn HTTP loglarını JSON formatına yönlendir"""
    try:
        # Uvicorn access logger'ını al
        uvicorn_access_logger = logging.getLogger("uvicorn.access")
        uvicorn_logger = logging.getLogger("uvicorn")
        
        # Custom formatter for HTTP requests
        class HTTPRequestFormatter(logging.Formatter):
            def format(self, record):
                # Parse uvicorn log message
                message = super().format(record)
                
                # Extract HTTP info from uvicorn format: "127.0.0.1:61495 - "POST /sources_list HTTP/1.1" 200 OK"
                import re
                http_pattern = r'(\d+\.\d+\.\d+\.\d+):(\d+) - "(\w+) ([^"]+) HTTP/[\d.]+" (\d+)'
                match = re.search(http_pattern, message)
                
                if match:
                    ip, port, method, path, status = match.groups()
                    record.component = 'http_server'
                    record.operation = 'http_request'
                    record.method = method
                    record.path = path
                    record.status_code = status
                    record.client_ip = ip
                    return f"🌐 HTTP {method} {path} - {status}"
                
                return f"📡 {message}"
        
        # Add custom formatter to uvicorn loggers
        for handler in uvicorn_access_logger.handlers:
            handler.setFormatter(HTTPRequestFormatter())
            
        print("✅ Uvicorn HTTP logging JSON formatına yönlendirildi")
        
    except Exception as e:
        print(f"⚠️ Uvicorn logging yönlendirme hatası: {e}")


if __name__ == "__main__":
    # Test için
    initialize_otel_logging()
    
    # Test logları
    logger = logging.getLogger("test")
    logger.info("Test info log")
    logger.warning("Test warning log")
    logger.error("Test error log")
    
    print("Test print çağrısı")
    print("🔥 Emoji'li test print")
    
    print("Test tamamlandı - loglar JSON dosyasına yazıldı")
