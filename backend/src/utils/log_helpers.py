"""
Log Helper Functions - Grafana Dashboard için özel loggerlar

Bu modül tüm işlem tiplerini Grafana dashboard'unda filtreleme için 
uygun tag'lerle logger'lar sağlar.

Grafana Dashboard Keywords:
- UPLOAD: Dosya yükleme işlemleri
- DELETE: Dosya/node silme işlemleri  
- EXTRACT: Entity extraction işlemleri
- CHUNKING: Metin parçalama işlemleri
- PROCESSING: Genel işlem durumları
"""

import logging


def get_tagged_logger(component: str, operation: str):
    """Component ve operation tag'li logger döndür"""
    logger = logging.getLogger(f"{component}.{operation}")
    return logger


def log_upload(message: str, level: str = "debug"):
    """Upload işlemleri için özel logger - UPLOAD tag'i ile"""
    logger = get_tagged_logger("upload", "file_processing")
    getattr(logger, level)(f"📤 UPLOAD: {message}")


def log_delete(message: str, level: str = "debug"):
    """Delete işlemleri için özel logger - DELETE tag'i ile"""
    logger = get_tagged_logger("delete", "file_processing")
    getattr(logger, level)(f"🗑️ DELETE: {message}")


def log_chunking(message: str, level: str = "debug"):
    """Chunking işlemleri için özel logger - CHUNKING tag'i ile"""
    logger = get_tagged_logger("chunking", "text_processing")
    getattr(logger, level)(f"✂️ CHUNKING: {message}")


def log_extraction(message: str, level: str = "debug"):
    """Extraction işlemleri için özel logger - EXTRACT tag'i ile"""
    logger = get_tagged_logger("extraction", "entity_processing")
    getattr(logger, level)(f"🎯 EXTRACT: {message}")


def log_processing(message: str, level: str = "debug"):
    """Genel processing işlemleri için özel logger - PROCESSING tag'i ile"""
    logger = get_tagged_logger("processing", "general")
    getattr(logger, level)(f"⚙️ PROCESSING: {message}")
