"""
Neo4j bağlantı sorunları için retry mekanizması
"""
import time
import logging
from typing import Callable, Any
from neo4j.exceptions import (
    ServiceUnavailable, 
    TransientError, 
    IncompleteCommit,
    SessionExpired,
    ConnectionUnavailable
)

def retry_neo4j_operation(operation: Callable, max_retries: int = 3, delay: float = 1.0) -> Any:
    """
    Neo4j işlemlerini retry mekanizması ile güvenli şekilde çalıştırır.
    
    Args:
        operation: Çalıştırılacak Neo4j işlemi (lambda fonksiyon)
        max_retries: Maksimum deneme sayısı
        delay: Denemeler arası bekleme süresi (saniye)
    
    Returns:
        İşlemin sonucu
        
    Raises:
        Son deneme sonrası alınan exception
    """
    last_exception = None
    
    for attempt in range(max_retries + 1):
        try:
            return operation()
        
        except (ServiceUnavailable, TransientError, IncompleteCommit, 
                SessionExpired, ConnectionUnavailable, OSError) as e:
            last_exception = e
            
            if attempt < max_retries:
                wait_time = delay * (2 ** attempt)  # Exponential backoff
                logging.warning(
                    f"Neo4j operation failed (attempt {attempt + 1}/{max_retries + 1}): {e}. "
                    f"Retrying in {wait_time:.1f} seconds..."
                )
                time.sleep(wait_time)
            else:
                logging.error(
                    f"Neo4j operation failed after {max_retries + 1} attempts: {e}"
                )
                
        except Exception as e:
            # Neo4j dışı hatalar için retry yapma
            logging.error(f"Non-Neo4j error in operation: {e}")
            raise
    
    # Tüm denemeler başarısız olduysa son exception'ı fırlat
    raise last_exception
