"""
Neo4j Connection Retry Utilities
Bağlantı sorunları için retry mekanizmalarını içerir.
"""

import time
import logging
from functools import wraps
from neo4j.exceptions import SessionExpired, ServiceUnavailable, TransientError

def retry_neo4j_operation(max_retries=3, delay=1, backoff=2):
    """
    Neo4j operasyonları için retry decorator
    
    Args:
        max_retries (int): Maksimum retry sayısı
        delay (float): İlk retry'dan önceki bekleme süresi (saniye)
        backoff (float): Her retry'da bekleme süresinin çarpanı
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            current_delay = delay
            last_exception = None
            
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except (SessionExpired, ServiceUnavailable, TransientError, ConnectionError) as e:
                    last_exception = e
                    if attempt == max_retries:
                        logging.error(f"Neo4j operasyonu {max_retries} deneme sonrası başarısız: {func.__name__}")
                        raise e
                    
                    logging.warning(f"Neo4j bağlantı hatası (deneme {attempt + 1}/{max_retries + 1}): {str(e)}")
                    logging.info(f"{current_delay} saniye bekleniyor...")
                    time.sleep(current_delay)
                    current_delay *= backoff
                except Exception as e:
                    # Retry edilemez hatalar
                    logging.error(f"Neo4j operasyonu geri alınamaz hata: {func.__name__} - {str(e)}")
                    raise e
            
            # Bu noktaya gelmemeli ama safety için
            raise last_exception
        
        return wrapper
    return decorator

def test_neo4j_connection(graph):
    """
    Neo4j bağlantısını test eder
    
    Args:
        graph: Neo4jGraph instance
    
    Returns:
        bool: Bağlantı başarılı ise True
    """
    try:
        # Basit bir test query'si
        result = graph.query("RETURN 1 AS test")
        if result and len(result) > 0 and result[0].get('test') == 1:
            logging.info("✅ Neo4j bağlantı testi başarılı")
            return True
        else:
            logging.error("❌ Neo4j bağlantı testi başarısız - beklenen sonuç alınamadı")
            return False
    except Exception as e:
        logging.error(f"❌ Neo4j bağlantı testi başarısız: {str(e)}")
        return False

def get_neo4j_connection_info(graph):
    """
    Neo4j bağlantı bilgilerini döndürür
    
    Args:
        graph: Neo4jGraph instance
    
    Returns:
        dict: Bağlantı bilgileri
    """
    try:
        # Database bilgilerini al
        result = graph.query("""
        CALL dbms.components() YIELD name, versions, edition
        RETURN name, versions[0] AS version, edition
        UNION
        CALL db.info() YIELD name AS dbName
        RETURN 'Database' AS name, dbName AS version, '' AS edition
        """)
        
        info = {}
        for row in result:
            if row['name'] == 'Neo4j Kernel':
                info['neo4j_version'] = row['version']
                info['edition'] = row['edition']
            elif row['name'] == 'Database':
                info['database_name'] = row['version']
        
        # Node ve relationship sayıları
        count_result = graph.query("""
        MATCH (n) 
        OPTIONAL MATCH ()-[r]->()
        RETURN count(DISTINCT n) AS node_count, count(DISTINCT r) AS rel_count
        """)
        
        if count_result:
            info['node_count'] = count_result[0]['node_count']
            info['relationship_count'] = count_result[0]['rel_count']
        
        logging.info(f"Neo4j Bilgileri: {info}")
        return info
        
    except Exception as e:
        logging.error(f"Neo4j bağlantı bilgileri alınamadı: {str(e)}")
        return {"error": str(e)}
