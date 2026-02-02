import logging
import os
import time
import re
import difflib
from typing import Optional
from neo4j.exceptions import TransientError, ServiceUnavailable, SessionExpired
from langchain_neo4j import Neo4jGraph
from src.shared.common_fn import (
    delete_uploaded_local_file,
    load_embedding_model,
)
from src.entities.source_node import sourceNode
from src.utf8_utils import normalize_unicode_text, normalize_file_name
from src.utils.log_helpers import log_delete, log_processing

# Entity resolution pre-processing KALDIRILDI - post-processing LLM ile yapılıyor
# from src.entity_resolver import resolve_entity_before_creation
import json
from dotenv import load_dotenv
from functools import wraps

# Domain-specific prompts
from prompts import load_prompt, get_domain

# Generic graph executor for dynamic graph creation
from src.generic_graph_executor import GenericGraphExecutor, create_graph_from_llm_output

load_dotenv()


# ============================================================================
# Neo4j Connection Retry Helper
# ============================================================================
NEO4J_RETRY_ATTEMPTS = 3
NEO4J_RETRY_WAIT_MIN = 2
NEO4J_RETRY_WAIT_MAX = 10


def neo4j_retry(func):
    """
    Decorator to retry Neo4j operations on connection errors.
    Handles ServiceUnavailable, SessionExpired, and ConnectionResetError.
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        last_exception = None
        for attempt in range(1, NEO4J_RETRY_ATTEMPTS + 1):
            try:
                return func(*args, **kwargs)
            except (ServiceUnavailable, SessionExpired, ConnectionResetError) as e:
                last_exception = e
                wait_time = min(
                    NEO4J_RETRY_WAIT_MIN * (2 ** (attempt - 1)), NEO4J_RETRY_WAIT_MAX
                )
                logging.warning(
                    f"⚠️ Neo4j connection error (attempt {attempt}/{NEO4J_RETRY_ATTEMPTS}): {e}. "
                    f"Retrying in {wait_time}s..."
                )
                time.sleep(wait_time)
            except Exception as e:
                # Check if it's a wrapped connection error
                error_str = str(e).lower()
                if "connection" in error_str and (
                    "reset" in error_str or "defunct" in error_str
                ):
                    last_exception = e
                    wait_time = min(
                        NEO4J_RETRY_WAIT_MIN * (2 ** (attempt - 1)),
                        NEO4J_RETRY_WAIT_MAX,
                    )
                    logging.warning(
                        f"⚠️ Neo4j connection error (attempt {attempt}/{NEO4J_RETRY_ATTEMPTS}): {e}. "
                        f"Retrying in {wait_time}s..."
                    )
                    time.sleep(wait_time)
                else:
                    raise

        # All retries exhausted
        logging.error(
            f"❌ Neo4j operation failed after {NEO4J_RETRY_ATTEMPTS} attempts"
        )
        if last_exception is not None:
            raise last_exception
        raise RuntimeError(
            f"Neo4j operation failed after {NEO4J_RETRY_ATTEMPTS} attempts"
        )

    return wrapper


def execute_neo4j_query_with_retry(
    graph, query, params=None, max_retries=NEO4J_RETRY_ATTEMPTS
):
    """
    Execute a Neo4j query with automatic retry on connection errors.

    Args:
        graph: Neo4jGraph instance
        query: Cypher query string
        params: Query parameters dict
        max_retries: Maximum number of retry attempts

    Returns:
        Query result
    """
    last_exception = None
    for attempt in range(1, max_retries + 1):
        try:
            return graph.query(query, params=params)
        except (ServiceUnavailable, SessionExpired, ConnectionResetError) as e:
            last_exception = e
            wait_time = min(
                NEO4J_RETRY_WAIT_MIN * (2 ** (attempt - 1)), NEO4J_RETRY_WAIT_MAX
            )
            logging.warning(
                f"⚠️ Neo4j query error (attempt {attempt}/{max_retries}): {e}. "
                f"Retrying in {wait_time}s..."
            )
            time.sleep(wait_time)

            # Try to refresh the connection
            try:
                if hasattr(graph, "_driver") and graph._driver:
                    graph._driver.verify_connectivity()
            except Exception:
                pass
        except Exception as e:
            # Check if it's a wrapped connection error
            error_str = str(e).lower()
            if "connection" in error_str and (
                "reset" in error_str or "defunct" in error_str
            ):
                last_exception = e
                wait_time = min(
                    NEO4J_RETRY_WAIT_MIN * (2 ** (attempt - 1)), NEO4J_RETRY_WAIT_MAX
                )
                logging.warning(
                    f"⚠️ Neo4j query error (attempt {attempt}/{max_retries}): {e}. "
                    f"Retrying in {wait_time}s..."
                )
                time.sleep(wait_time)
            else:
                raise

    logging.error(f"❌ Neo4j query failed after {max_retries} attempts")
    if last_exception is not None:
        raise last_exception
    raise RuntimeError(f"Neo4j query failed after {max_retries} attempts")


# Neo4j notification loglarını kapat
def filter_neo4j_notifications(record):
    message = record.getMessage().lower()
    filtered_keywords = [
        "deprecation",
        "deprecated",
        "unknown label",
        "call subquery",
        "variable scope clause",
        "notification",
        "severity",
        "category",
        "received notification from dbms server",
    ]
    return not any(keyword in message for keyword in filtered_keywords)


logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)
logging.getLogger("neo4j").setLevel(logging.WARNING)
logging.getLogger("neo4j").addFilter(filter_neo4j_notifications)


class graphDBdataAccess:
    # Class-level cache for relationship type normalization
    _relationship_type_cache = {}  # {cache_key: normalized_type}
    _schema_cache = None
    _schema_cache_timestamp = None
    SCHEMA_CACHE_TTL = 3600  # 1 saat

    def __init__(self, graph: Neo4jGraph):
        self.graph = graph

    def query_with_retry(self, query, params=None, max_retries=NEO4J_RETRY_ATTEMPTS):
        """
        Execute a Neo4j query with automatic retry on connection errors.
        Use this for critical operations that should not fail due to transient connection issues.
        """
        return execute_neo4j_query_with_retry(self.graph, query, params, max_retries)

    def update_exception_db(self, file_name, exp_msg, retry_condition=None):
        try:
            job_status = "Failed"
            result = self.get_current_status_document_node(file_name)
            if len(result) > 0:
                is_cancelled_status = result[0]["is_cancelled"]
                if bool(is_cancelled_status) == True:
                    job_status = "Cancelled"
            if retry_condition is not None:
                retry_condition = None
                self.graph.query(
                    """MERGE(d:Document {fileName :$fName}) SET d.status = $status, d.errorMessage = $error_msg, d.retry_condition = $retry_condition""",
                    {
                        "fName": file_name,
                        "status": job_status,
                        "error_msg": exp_msg,
                        "retry_condition": retry_condition,
                    },
                    session_params={"database": self.graph._database},
                )
            else:
                self.graph.query(
                    """MERGE(d:Document {fileName :$fName}) SET d.status = $status, d.errorMessage = $error_msg""",
                    {"fName": file_name, "status": job_status, "error_msg": exp_msg},
                    session_params={"database": self.graph._database},
                )
        except Exception as e:
            error_message = str(e)
            logging.error(
                f"Error in updating document node status as failed: {error_message}"
            )
            raise Exception(error_message)

    def create_source_node(
        self,
        obj_source_node_or_filename,
        document_type: str = "auto",
        text_content: Optional[str] = None,
        model: str = "openai_gpt_4o_mini",
        skip_entity_extraction: bool = False,
    ):
        """
        Document node oluşturur. sourceNode objesi veya sadece file_name string'i alabilir.

        Upload işleminde aynı dosya zaten veritabanında varsa otomatik olarak temizler.

        Args:
            obj_source_node_or_filename: sourceNode objesi veya file_name string'i
            document_type: 'policy' veya 'auto' (otomatik tespit) - CV extraction kaldırıldı
            text_content: Kullanılmıyor (CV extraction kaldırıldı)
            model: LLM model adı (entity extraction için)
            skip_entity_extraction: True ise entity extraction atlanır (sadece Document ve Chunk node'ları oluşturulur)
                                   False (varsayılan) ise eski davranış korunur (entity extraction yapılır)
        """
        try:
            # Eğer string ise, minimal Document node oluştur
            if isinstance(obj_source_node_or_filename, str):
                original_file_name = obj_source_node_or_filename

                # UTF-8 ve Unicode normalization - Critical for duplicate prevention
                file_name = normalize_file_name(original_file_name)

                logging.info(
                    f"Document node oluşturma: '{original_file_name}' -> '{file_name}'"
                )

                # Unicode karakter detayları için debug
                if original_file_name != file_name:
                    logging.warning(f"Filename normalization değişikliği: ")
                    logging.warning(f"  Original: {repr(original_file_name)}")
                    logging.warning(f"  Normalized: {repr(file_name)}")
                    logging.warning(
                        f"  Original bytes: {original_file_name.encode('utf-8').hex()}"
                    )
                    logging.warning(
                        f"  Normalized bytes: {file_name.encode('utf-8').hex()}"
                    )

                # Dosya bilgilerini file_name'den çıkar
                import os

                file_extension = os.path.splitext(file_name)[1].lower()

                # Dosya tipini uzantıdan belirle
                if file_extension in [".pdf"]:
                    file_type = "PDF"
                elif file_extension in [".txt"]:
                    file_type = "Text"
                elif file_extension in [".docx", ".doc"]:
                    file_type = "Word Document"
                elif file_extension in [".html", ".htm"]:
                    file_type = "HTML"
                elif file_extension in [".json"]:
                    file_type = "JSON"
                elif file_extension in [".csv"]:
                    file_type = "CSV"
                elif file_extension in [".xml"]:
                    file_type = "XML"
                else:
                    file_type = f"Document{file_extension.upper()}"

                # Dosya boyutunu almaya çalış (eğer dosya mevcutsa)
                file_size = 0
                try:
                    if os.path.exists(file_name):
                        file_size = os.path.getsize(file_name)
                except:
                    file_size = 0

                # Document node için güvenli MERGE - normalizasyonlu filename kullan
                merge_query = """
                    MERGE(d:Document {fileName: $file_name}) 
                    ON CREATE SET 
                        d.id = $file_name,
                        d.status = 'New',
                        d.fileSource = 'local file',
                        d.fileType = $file_type,
                        d.fileSize = $file_size,
                        d.processedAt = datetime(),
                        d.lastProcessedAt = datetime(),
                        d.processingTime = 0,
                        d.is_cancelled = false,
                        d.errorMessage = '',
                        d.model = 'unknown'
                    ON MATCH SET 
                        d.id = $file_name,
                        d.lastProcessedAt = datetime(),
                        d.fileType = $file_type,
                        d.fileSize = $file_size
                    RETURN d.fileName as fileName, d.status as status
                """

                # Use retry wrapper for connection resilience
                result = self.query_with_retry(
                    merge_query,
                    {
                        "file_name": file_name,
                        "file_type": file_type,
                        "file_size": file_size,
                    },
                )

                if result:
                    status = result[0]["status"] if result else "unknown"
                    logging.info(
                        f"Document node işlendi: {file_name} (status: {status})"
                    )
                else:
                    logging.info(f"Document node oluşturuldu: {file_name}")

                # Document yaratıldıktan sonra belge tipine göre node'unu yarat ve bağla
                # skip_entity_extraction=True ise entity extraction atlanır (sadece chunking için)
                if not skip_entity_extraction:
                    logging.info(f"📋 Entity extraction başlatılıyor: {file_name}")
                    self._create_document_related_nodes(
                        file_name, document_type, text_content
                    )
                else:
                    logging.info(
                        f"⏭️ Entity extraction atlandı (skip_entity_extraction=True): {file_name}"
                    )
                return

            # sourceNode objesi ise, orijinal işlemi yap
            obj_source_node = obj_source_node_or_filename

            # UTF-8 ve Unicode normalization for file_name - Critical!
            obj_source_node.file_name = normalize_file_name(obj_source_node.file_name)

            job_status = "New"
            logging.info(
                f"Tam Document node oluşturuluyor: {obj_source_node.file_name}"
            )
            self.graph.query(
                """MERGE(d:Document {fileName :$fn}) SET d.id = $fn, d.fileSize = $fs, d.fileType = $ft ,
                            d.status = $st, d.url = $url, d.awsAccessKeyId = $awsacc_key_id, 
                            d.fileSource = $f_source, d.processedAt = $c_at, d.lastProcessedAt = $u_at, 
                            d.processingTime = $pt, d.errorMessage = $e_message, d.model= $model, d.language= $language,
                            d.is_cancelled=False, d.access_token=$access_token, d.doc_link=$doc_link, d.page_images=$page_images""",
                {
                    "fn": obj_source_node.file_name,
                    "fs": obj_source_node.file_size,
                    "ft": obj_source_node.file_type,
                    "st": job_status,
                    "url": getattr(obj_source_node, "url", ""),
                    "awsacc_key_id": getattr(obj_source_node, "awsAccessKeyId", ""),
                    "f_source": obj_source_node.file_source,
                    "c_at": obj_source_node.created_at,
                    "u_at": obj_source_node.created_at,
                    "pt": getattr(obj_source_node, "processing_time", 0),
                    "e_message": "",
                    "model": obj_source_node.model,
                    "language": getattr(obj_source_node, "language", ""),
                    "access_token": getattr(obj_source_node, "access_token", ""),
                    "doc_link": getattr(obj_source_node, "doc_link", ""),
                    "page_images": getattr(obj_source_node, "page_images", []),
                },
                session_params={"database": self.graph._database},
            )

            logging.info(f"Tam Document node oluşturuldu: {obj_source_node.file_name}")

            # Document yaratıldıktan sonra belge tipine göre node'unu yarat ve bağla
            # skip_entity_extraction=True ise entity extraction atlanır (sadece chunking için)
            if not skip_entity_extraction:
                logging.info(
                    f"📋 Entity extraction başlatılıyor: {obj_source_node.file_name}"
                )
                self._create_document_related_nodes(
                    obj_source_node.file_name, document_type, text_content, model
                )
            else:
                logging.info(
                    f"⏭️ Entity extraction atlandı (skip_entity_extraction=True): {obj_source_node.file_name}"
                )

        except Exception as e:
            error_message = str(e)
            logging.error(f"Document node oluşturma hatası: {error_message}")
            if not isinstance(obj_source_node_or_filename, str):
                self.update_exception_db(
                    self, obj_source_node_or_filename.file_name, error_message
                )
            raise Exception(error_message)

    def _create_document_related_nodes(
        self,
        file_name: str,
        document_type: str = "auto",
        text_content: Optional[str] = None,
        model: str = "openai_gpt_4o_mini",
    ):
        """
        Belge tipine göre uygun node'ları oluşturur (sadece Policy)

        Args:
            file_name: Dosya adı
            document_type: 'policy' veya 'auto' (otomatik tespit)
            text_content: Kullanılmıyor (CV extraction kaldırıldı)
        """
        try:
            # Otomatik tespit (artık sadece policy döndürür)
            if document_type == "auto":
                document_type = self._detect_document_type(file_name)

            # Policy node oluştur (CV seçeneği kaldırıldı)
            logging.info(f"📋 Policy node oluşturuluyor: {file_name}")
            self.create_policy_node_from_document(file_name, model)

        except Exception as e:
            logging.error(f"Document related nodes oluşturma hatası ({file_name}): {e}")
            raise e

    def _detect_document_type(self, file_name: str) -> str:
        """
        Dosya adından belge tipini otomatik olarak tespit eder

        Args:
            file_name: Dosya adı

        Returns:
            'policy' (CV detection kaldırıldı, sadece policy)
        """
        try:
            file_name_lower = file_name.lower()

            # Policy anahtar kelimeleri
            policy_keywords = [
                "poliçe",
                "police",
                "policy",
                "sigorta",
                "insurance",
                "kasko",
                "dask",
                "trafik",
                "traffic",
                "zorunlu",
                "compulsory",
                "hayat",
                "life",
                "sağlık",
                "saglik",
                "health",
                "seyahat",
                "travel",
                "konut",
                "home",
                "işyeri",
                "isyeri",
                "workplace",
                "ferdi",
                "individual",
                "kaza",
                "accident",
            ]

            # Policy kontrolü
            for keyword in policy_keywords:
                if keyword in file_name_lower:
                    logging.info(
                        f"🔍 Policy belgesi tespit edildi ('{keyword}' anahtar kelimesi): {file_name}"
                    )
                    return "policy"

            # Varsayılan olarak policy
            logging.info(
                f"🔍 Belge tipi tespit edilemedi, varsayılan 'policy' kullanılıyor: {file_name}"
            )
            return "policy"

        except Exception as e:
            logging.error(f"Belge tipi tespit hatası: {e}")
            return "policy"  # Hata durumunda varsayılan

    def update_source_node(self, obj_source_node: sourceNode):
        try:

            params = {}
            if (
                obj_source_node.file_name is not None
                and obj_source_node.file_name != ""
            ):
                params["fileName"] = obj_source_node.file_name

            if obj_source_node.status is not None and obj_source_node.status != "":
                params["status"] = obj_source_node.status

            if obj_source_node.created_at is not None:
                params["processedAt"] = obj_source_node.created_at

            if obj_source_node.updated_at is not None:
                params["lastProcessedAt"] = obj_source_node.updated_at

            if (
                obj_source_node.processing_time is not None
                and obj_source_node.processing_time != 0
            ):
                processing_time = obj_source_node.processing_time
                if hasattr(processing_time, "total_seconds"):
                    params["processingTime"] = round(processing_time.total_seconds(), 2)  # type: ignore[union-attr]
                else:
                    params["processingTime"] = round(float(processing_time), 2)

            if obj_source_node.model is not None and obj_source_node.model != "":
                params["model"] = obj_source_node.model

            if obj_source_node.is_cancelled is not None:
                params["is_cancelled"] = obj_source_node.is_cancelled

            if obj_source_node.retry_condition is not None:
                params["retry_condition"] = obj_source_node.retry_condition

            param = {"props": params}

            logging.info(f"Base Param value 1 : {param}")

            # Token kullanımı için özel loglama
            if "total_tokens" in params:
                logging.info(
                    f"📊 Document {params.get('fileName', 'unknown')} için token kullanımı güncellendi:"
                )
                logging.info(f"  🔢 Toplam token: {params.get('total_tokens', 0)}")
                logging.info(f"  📥 Input token: {params.get('input_tokens', 0)}")
                logging.info(f"  📤 Output token: {params.get('output_tokens', 0)}")

            query = "MERGE(d:Document {fileName :$props.fileName}) SET d += $props"
            logging.info("Update source node properties")
            self.graph.query(
                query, param, session_params={"database": self.graph._database}
            )
        except Exception as e:
            error_message = str(e)
            file_name = getattr(self, "file_name", "unknown")
            self.update_exception_db(self, file_name, error_message)
            raise Exception(error_message)

    def get_source_list(self):
        """
        Args:
            uri: URI of the graph to extract
            db_name: db_name is database name to connect to graph db
            userName: Username to use for graph creation ( if None will use username from config file )
            password: Password to use for graph creation ( if None will use password from config file )
            file: File object containing the PDF file to be used
            model: Type of model to use ('Diffbot'or'OpenAI GPT')
        Returns:
        Returns a list of sources that are in the database by querying the graph and
        sorting the list by the last updated date.
        """
        logging.info("Get existing files list from graph")
        query = "MATCH(d:Document) WHERE d.fileName IS NOT NULL RETURN d ORDER BY d.lastProcessedAt DESC"
        result = self.graph.query(
            query, session_params={"database": self.graph._database}
        )
        list_of_json_objects = [entry["d"] for entry in result]
        return list_of_json_objects

    def update_KNN_graph(self):
        """
        Update the graph node with SIMILAR relationship where embedding scrore match
        🚫 DEVRE DIŞI BIRAKILDI - SIMILAR ilişkiler oluşturulmuyor
        """
        logging.info(
            "🚫 KNN graph update devre dışı - SIMILAR ilişkileri oluşturulmuyor"
        )
        return  # Fonksiyonu erken sonlandır

        # Aşağıdaki kod artık çalışmayacak
        index = self.graph.query(
            """show indexes yield * where type = 'VECTOR' and name = 'vector'""",
            session_params={"database": self.graph._database},
        )
        # logging.info(f'show index vector: {index}')
        knn_min_score = os.environ.get("KNN_MIN_SCORE")
        if len(index) > 0:
            logging.info("update KNN graph")
            self.graph.query(
                """MATCH (c:Chunk)
                                    WHERE c.embedding IS NOT NULL AND count { (c)-[:SIMILAR]-() } < 5
                                    CALL db.index.vector.queryNodes('vector', 6, c.embedding) yield node, score
                                    WHERE node <> c and score >= $score MERGE (c)-[rel:SIMILAR]-(node) SET rel.score = score
                                """,
                {"score": float(knn_min_score)},
                session_params={"database": self.graph._database},
            )
        else:
            logging.info("Vector index does not exist, So KNN graph not update")

    def check_account_access(self, database):
        try:
            query_dbms_componenet = "call dbms.components() yield edition"
            result_dbms_componenet = self.graph.query(
                query_dbms_componenet, session_params={"database": self.graph._database}
            )

            if result_dbms_componenet[0]["edition"] == "enterprise":
                query = """
                SHOW USER PRIVILEGES 
                YIELD * 
                WHERE graph = $database AND action IN ['read'] 
                RETURN COUNT(*) AS readAccessCount
                """

                logging.info(f"Checking access for database: {database}")

                result = self.graph.query(
                    query,
                    params={"database": database},
                    session_params={"database": self.graph._database},
                )
                read_access_count = result[0]["readAccessCount"] if result else 0

                logging.info(f"Read access count: {read_access_count}")

                if read_access_count > 0:
                    logging.info("The account has read access.")
                    return False
                else:
                    logging.info("The account has write access.")
                    return True
            else:
                # Community version have no roles to execute admin command, so assuming write access as TRUE
                logging.info("The account has write access.")
                return True

        except Exception as e:
            logging.error(f"Error checking account access: {e}")
            return False

    def check_gds_version(self):
        try:
            gds_procedure_count = """
            SHOW FUNCTIONS YIELD name WHERE name STARTS WITH 'gds.version' RETURN COUNT(*) AS totalGdsProcedures
            """
            result = self.graph.query(
                gds_procedure_count, session_params={"database": self.graph._database}
            )
            total_gds_procedures = result[0]["totalGdsProcedures"] if result else 0

            if total_gds_procedures > 0:
                logging.info("GDS is available in the database.")
                return True
            else:
                logging.info("GDS is not available in the database.")
                return False
        except Exception as e:
            logging.error(f"An error occurred while checking GDS version: {e}")
            return False

    def connection_check_and_get_vector_dimensions(self, database):
        """
        Get the vector index dimension from database and application configuration and DB connection status

        Args:
            uri: URI of the graph to extract
            userName: Username to use for graph creation ( if None will use username from config file )
            password: Password to use for graph creation ( if None will use password from config file )
            db_name: db_name is database name to connect to graph db
        Returns:
        Returns a status of connection from NEO4j is success or failure
        """

        db_vector_dimension = self.graph.query(
            """SHOW INDEXES YIELD *
                                    WHERE type = 'VECTOR' AND name = 'vector'
                                    RETURN options.indexConfig['vector.dimensions'] AS vector_dimensions
                                """,
            session_params={"database": self.graph._database},
        )

        result_chunks = self.graph.query(
            """match (c:Chunk) return size(c.embedding) as embeddingSize, count(*) as chunks, 
                                                    count(c.embedding) as hasEmbedding
                                """,
            session_params={"database": self.graph._database},
        )

        embedding_model = os.getenv("EMBEDDING_MODEL", "openai")
        embeddings, application_dimension = load_embedding_model(embedding_model)
        logging.info(
            f"embedding model:{embeddings} and dimesion:{application_dimension}"
        )

        gds_status = self.check_gds_version()
        write_access = self.check_account_access(database=database)

        if self.graph:
            if len(db_vector_dimension) > 0:
                return {
                    "db_vector_dimension": db_vector_dimension[0]["vector_dimensions"],
                    "application_dimension": application_dimension,
                    "message": "Connection Successful",
                    "gds_status": gds_status,
                    "write_access": write_access,
                }
            else:
                if len(db_vector_dimension) == 0 and len(result_chunks) == 0:
                    logging.info("Chunks and vector index does not exists in database")
                    return {
                        "db_vector_dimension": 0,
                        "application_dimension": application_dimension,
                        "message": "Connection Successful",
                        "chunks_exists": False,
                        "gds_status": gds_status,
                        "write_access": write_access,
                    }
                elif (
                    len(db_vector_dimension) == 0
                    and result_chunks[0]["hasEmbedding"] == 0
                    and result_chunks[0]["chunks"] > 0
                ):
                    return {
                        "db_vector_dimension": 0,
                        "application_dimension": application_dimension,
                        "message": "Connection Successful",
                        "chunks_exists": True,
                        "gds_status": gds_status,
                        "write_access": write_access,
                    }
                else:
                    return {
                        "message": "Connection Successful",
                        "gds_status": gds_status,
                        "write_access": write_access,
                    }

    def execute_query(
        self, query, param: Optional[dict] = None, max_retries=3, delay=2
    ):
        """
        Neo4j query'sini timeout ve connection hatalarına karşı retry mekanizması ile çalıştırır
        """
        import time
        from neo4j.exceptions import SessionExpired, ServiceUnavailable, TransientError

        retries = 0
        query_param = param if param is not None else {}
        while retries < max_retries:
            try:
                return self.graph.query(
                    query,
                    query_param,
                    session_params={"database": self.graph._database},
                )
            except (SessionExpired, ServiceUnavailable) as e:
                retries += 1
                if retries >= max_retries:
                    logging.error(
                        f"Neo4j bağlantı hatası - {max_retries} deneme sonrası başarısız: {str(e)}"
                    )
                    raise e
                logging.warning(
                    f"Neo4j bağlantı hatası (deneme {retries}/{max_retries}): {str(e)}"
                )
                logging.info(f"{delay} saniye bekleniyor...")
                time.sleep(delay)
                delay *= 2  # Exponential backoff
            except TransientError as e:
                if "DeadlockDetected" in str(e):
                    retries += 1
                    if retries >= max_retries:
                        logging.error(
                            f"Deadlock hatası - {max_retries} deneme sonrası başarısız: {str(e)}"
                        )
                        raise e
                    logging.info(
                        f"Deadlock detected. Retrying {retries}/{max_retries} in {delay} seconds..."
                    )
                    time.sleep(delay)
                else:
                    # Diğer TransientError'lar için de retry yap
                    retries += 1
                    if retries >= max_retries:
                        logging.error(
                            f"Transient error - {max_retries} deneme sonrası başarısız: {str(e)}"
                        )
                        raise e
                    logging.warning(
                        f"Transient error (deneme {retries}/{max_retries}): {str(e)}"
                    )
                    time.sleep(delay)
            except Exception as e:
                # Retry edilemez hatalar
                logging.error(f"Neo4j query hatası (retry edilemez): {str(e)}")
                raise e

        # Eğer buraya geldiysek, tüm retry'lar tükendi
        logging.error("Neo4j query failed after maximum retries.")
        raise RuntimeError("Query execution failed after multiple retries.")

    def get_current_status_document_node(self, file_name):
        query = """
                MATCH(d:Document {fileName : $file_name}) RETURN d.status AS Status , d.processingTime AS processingTime, 
                d.model as model, d.fileSize as fileSize, 
                d.is_cancelled as is_cancelled, d.fileSource as fileSource,
                d.processedAt AS processed_time
                """
        param = {"file_name": file_name}
        result = self.execute_query(query, param)

        # Eğer Document node bulunamazsa, create_source_node ile oluştur
        if not result or len(result) == 0:
            logging.warning(
                f"Document node bulunamadı: {file_name}. create_source_node ile oluşturuluyor..."
            )
            try:
                # create_source_node kullanarak tutarlı Document node oluştur
                self.create_source_node(file_name, skip_entity_extraction=True)
                logging.info(
                    f"Document node create_source_node ile oluşturuldu: {file_name}"
                )

                # Tekrar sorgula
                result = self.execute_query(query, param)

            except Exception as e:
                logging.error(f"Document node oluştururken hata: {e}")
                return []

        return result

    def delete_file_from_graph(
        self, filenames, source_types, deleteEntities: str, merged_dir: str, uri
    ):

        filename_list = list(map(str.strip, json.loads(filenames)))
        source_types_list = list(map(str.strip, json.loads(source_types)))

        log_delete(
            f"Starting deletion process for {len(filename_list)} files: {filename_list}"
        )
        log_delete(
            f"Delete entities mode: {deleteEntities}, Source types: {source_types_list}"
        )

        for file_name, source_type in zip(filename_list, source_types_list):
            merged_file_path = os.path.join(merged_dir, file_name)
            logging.info(
                f"Deleted File Path: {merged_file_path} and Deleted File Name : {file_name}"
            )
            delete_uploaded_local_file(merged_file_path, file_name)
            log_delete(f"File deleted from local storage: {file_name}")

        query_to_delete_document = """
            MATCH (d:Document)
            WHERE d.fileName IN $filename_list AND coalesce(d.fileSource, "None") IN $source_types_list
            WITH COLLECT(d) AS documents
            CALL (documents) {
            UNWIND documents AS d
            optional match (d)<-[:PART_OF]-(c:Chunk) 
            detach delete c, d
            } IN TRANSACTIONS OF 1 ROWS
            """
        query_to_delete_document_and_entities = """
            MATCH (d:Document)
            WHERE d.fileName IN $filename_list AND coalesce(d.fileSource, "None") IN $source_types_list
            WITH COLLECT(d) AS documents
            CALL (documents) {
            UNWIND documents AS d
            
            // 1. Chunk'ları ve chunk-entity ilişkilerini topla
            OPTIONAL MATCH (d)<-[:PART_OF]-(c:Chunk)
            OPTIONAL MATCH (c)-[:HAS_ENTITY]->(ce)
            
            // 2. Document'a direkt bağlı entity'leri topla (Policy, Customer vs.)
            OPTIONAL MATCH (d)<-[:DOCUMENTED_IN]-(policy:Policy)
            OPTIONAL MATCH (d)<-[:HAS_DOC]-(customer:Customer)
            OPTIONAL MATCH (policy)-[:HAS_YEAR]->(py:PolicyYear)
            OPTIONAL MATCH (policy)-[:HAS_INSURED_ITEM]->(ii:InsuredItem)
            OPTIONAL MATCH (policy)-[:HAS_TYPE]->(pt:PolicyType)
            
            // 3. Document'a bağlı diğer node'ları topla (Agent, InsuranceCompany vs.)
            OPTIONAL MATCH (d)-[*0..2]-(other)
            WHERE other:Agent OR other:InsuranceCompany OR other:Address OR other:Phone OR other:Email
            
            WITH d, documents, 
                 COLLECT(DISTINCT c) AS chunks, 
                 COLLECT(DISTINCT ce) AS chunkEntities,
                 COLLECT(DISTINCT policy) + COLLECT(DISTINCT customer) + COLLECT(DISTINCT py) + COLLECT(DISTINCT ii) + COLLECT(DISTINCT pt) AS docEntities,
                 COLLECT(DISTINCT other) AS otherNodes
            
            // 4. Sadece başka document'larda kullanılmayan entity'leri sil
            WITH d, chunks, 
                 [entity IN chunkEntities WHERE entity IS NOT NULL AND NOT EXISTS {
                     MATCH (entity)<-[:HAS_ENTITY]-(c2:Chunk)-[:PART_OF]->(d2:Document)
                     WHERE NOT d2 IN documents
                 }] AS safeChunkEntities,
                 [entity IN docEntities WHERE entity IS NOT NULL AND NOT EXISTS {
                     MATCH (d2:Document)
                     WHERE NOT d2 IN documents AND (
                         (d2)<-[:DOCUMENTED_IN]-(entity) OR 
                         (d2)<-[:HAS_DOC]-(entity) OR
                         (d2)<-[:DOCUMENTED_IN]-(:Policy)-[:HAS_YEAR]->(entity) OR
                         (d2)<-[:DOCUMENTED_IN]-(:Policy)-[:HAS_INSURED_ITEM]->(entity) OR
                         (d2)<-[:DOCUMENTED_IN]-(:Policy)-[:HAS_TYPE]->(entity)
                     )
                 }] AS safeDocEntities,
                 [node IN otherNodes WHERE node IS NOT NULL AND NOT EXISTS {
                     MATCH (node)-[*0..2]-(d2:Document)
                     WHERE NOT d2 IN documents
                 }] AS safeOtherNodes
            
            // 5. Güvenli silme işlemi
            FOREACH (chunk IN chunks | DETACH DELETE chunk)
            FOREACH (entity IN safeChunkEntities | DETACH DELETE entity)
            FOREACH (entity IN safeDocEntities | DETACH DELETE entity) 
            FOREACH (node IN safeOtherNodes | DETACH DELETE node)
            DETACH DELETE d
            } IN TRANSACTIONS OF 1 ROWS
            """
        param = {"filename_list": filename_list, "source_types_list": source_types_list}
        if deleteEntities == "true":
            log_delete(
                f"Executing comprehensive deletion (documents + entities) for {len(filename_list)} files"
            )
            result = self.execute_query(query_to_delete_document_and_entities, param)
            log_delete(
                f"Successfully deleted {len(filename_list)} documents with entities: {filename_list}"
            )
            logging.info(
                f"Deleting {len(filename_list)} documents = '{filename_list}' from '{source_types_list}' from database"
            )
        else:
            log_delete(
                f"Executing document-only deletion for {len(filename_list)} files"
            )
            result = self.execute_query(query_to_delete_document, param)
            log_delete(
                f"Successfully deleted {len(filename_list)} documents (entities preserved): {filename_list}"
            )
            logging.info(
                f"Deleting {len(filename_list)} documents = '{filename_list}' from '{source_types_list}' with their entities from database"
            )
        return len(filename_list)

    def auto_clean_existing_file_data(self, file_name: str):
        """
        Upload işlemi öncesi aynı dosya varsa otomatik olarak o dosyaya ait
        tüm node'ları ve ilişkileri dinamik şekilde temizler.

        Bu fonksiyon:
        1. Dosya ile ilişkili Document node'u bulur
        2. O Document'e bağlı tüm node tiplerini dinamik olarak keşfeder
        3. Sadece o Document'e özel olan node'ları güvenli şekilde siler
        4. Başka Document'lerde de kullanılan node'ları korur

        Args:
            file_name: Temizlenecek dosya adı
        """
        try:
            logging.info(f"🧹 Otomatik temizlik başlıyor: {file_name}")

            # 1. Dosyanın var olup olmadığını kontrol et
            check_query = """
                MATCH (d:Document {fileName: $file_name})
                RETURN d.fileName as fileName, labels(d) as labels
            """

            result = self.execute_query(check_query, {"file_name": file_name})

            if not result:
                logging.info(
                    f"📄 Dosya veritabanında bulunamadı, temizlik gerekmez: {file_name}"
                )
                return True

            logging.info(f"🔍 Mevcut dosya bulundu, temizlik başlatılıyor: {file_name}")

            # 2. Bu Document'e bağlı tüm node'ları ve ilişki tiplerini dinamik olarak keşfet
            discovery_query = """
                MATCH (d:Document {fileName: $file_name})
                
                // Chunk'ları bul
                OPTIONAL MATCH (d)<-[:PART_OF]-(chunk:Chunk)
                
                // Chunk'lardan bağlı entity'leri bul
                OPTIONAL MATCH (chunk)-[:HAS_ENTITY]->(chunkEntity)
                
                // Document'a direkt bağlı tüm node'ları bul (1 seviye derinlik)
                OPTIONAL MATCH (d)-[r1]-(directConnected)
                WHERE NOT directConnected:Chunk
                
                // Document'a 2 seviye derinlikteki node'ları bul (Policy->Customer gibi)
                OPTIONAL MATCH (d)-[*1..2]-(indirectConnected)
                WHERE NOT indirectConnected:Chunk 
                  AND NOT indirectConnected:Document
                  AND NOT indirectConnected:`__Community__`
                
                RETURN 
                    collect(DISTINCT chunk) as chunks,
                    collect(DISTINCT chunkEntity) as chunkEntities,
                    collect(DISTINCT directConnected) as directNodes,
                    collect(DISTINCT indirectConnected) as indirectNodes,
                    collect(DISTINCT type(r1)) as relationshipTypes
            """

            discovery_result = self.execute_query(
                discovery_query, {"file_name": file_name}
            )

            if not discovery_result:
                logging.warning(f"⚠️ Dosya bağlantıları keşfedilemedi: {file_name}")
                return False

            discovery_data = discovery_result[0]
            chunks = discovery_data.get("chunks", [])
            chunk_entities = discovery_data.get("chunkEntities", [])
            direct_nodes = discovery_data.get("directNodes", [])
            indirect_nodes = discovery_data.get("indirectNodes", [])

            total_connected_nodes = (
                len(chunks)
                + len(chunk_entities)
                + len(direct_nodes)
                + len(indirect_nodes)
            )
            logging.info(f"📊 Keşfedilen bağlantı istatistikleri:")
            logging.info(f"   - Chunk'lar: {len(chunks)}")
            logging.info(f"   - Chunk Entity'leri: {len(chunk_entities)}")
            logging.info(f"   - Direkt bağlı node'lar: {len(direct_nodes)}")
            logging.info(f"   - Dolaylı bağlı node'lar: {len(indirect_nodes)}")
            logging.info(f"   - Toplam ilişkili node: {total_connected_nodes}")

            # 3. Güvenli silme işlemi - sadece bu Document'e özel olan node'ları sil
            safe_deletion_query = """
                MATCH (d:Document {fileName: $file_name})
                
                // 1. Chunk'ları ve onlara bağlı entity'leri sil
                OPTIONAL MATCH (d)<-[:PART_OF]-(chunk:Chunk)
                OPTIONAL MATCH (chunk)-[:HAS_ENTITY]->(chunkEntity)
                
                // Chunk entity'leri için güvenlik kontrolü
                WITH d, collect(DISTINCT chunk) AS chunksToDelete,
                     [entity IN collect(DISTINCT chunkEntity) WHERE entity IS NOT NULL 
                      AND NOT EXISTS {
                        MATCH (entity)<-[:HAS_ENTITY]-(otherChunk:Chunk)-[:PART_OF]->(otherDoc:Document)
                        WHERE otherDoc.fileName <> $file_name
                      }] AS safeChunkEntities
                
                // 2. Document'a direkt bağlı node'ları bul ve güvenlik kontrolü yap
                OPTIONAL MATCH (d)-[directRel]-(directNode)
                WHERE NOT directNode:Chunk 
                  AND NOT directNode:Document 
                  AND NOT directNode:`__Community__`
                
                WITH d, chunksToDelete, safeChunkEntities,
                     [node IN collect(DISTINCT directNode) WHERE node IS NOT NULL
                      AND (
                        // Endorsement node'ları için özel mantık: aynı dosyadan geliyorsa sil
                        (node:Endorsement AND node.id CONTAINS replace($file_name, '.pdf', '_pdf'))
                        OR
                        // Diğer node'lar için normal güvenlik kontrolü
                        (NOT node:Endorsement AND NOT EXISTS {
                          MATCH (otherDoc:Document) 
                          WHERE otherDoc.fileName <> $file_name 
                            AND (
                              (otherDoc)-[*1..2]-(node) OR
                              (node)-[*1..2]-(otherDoc)
                            )
                        })
                      )] AS safeDirectNodes
                
                // 3. Silme işlemini gerçekleştir
                FOREACH (chunk IN chunksToDelete | DETACH DELETE chunk)
                FOREACH (entity IN safeChunkEntities | DETACH DELETE entity)
                FOREACH (node IN safeDirectNodes | DETACH DELETE node)
                
                // 4. Son olarak Document node'unu sil
                DETACH DELETE d
                
                RETURN 
                    size(chunksToDelete) as deletedChunks,
                    size(safeChunkEntities) as deletedChunkEntities,  
                    size(safeDirectNodes) as deletedDirectNodes
            """

            deletion_result = self.execute_query(
                safe_deletion_query, {"file_name": file_name}
            )

            if deletion_result:
                deleted_chunks = deletion_result[0].get("deletedChunks", 0)
                deleted_chunk_entities = deletion_result[0].get(
                    "deletedChunkEntities", 0
                )
                deleted_direct_nodes = deletion_result[0].get("deletedDirectNodes", 0)

                total_deleted = (
                    deleted_chunks + deleted_chunk_entities + deleted_direct_nodes
                )

                logging.info(f"✅ Otomatik temizlik tamamlandı: {file_name}")
                logging.info(f"📊 Silinen node istatistikleri:")
                logging.info(f"   - Silinen Chunk'lar: {deleted_chunks}")
                logging.info(
                    f"   - Silinen Chunk Entity'leri: {deleted_chunk_entities}"
                )
                logging.info(f"   - Silinen Direkt Node'lar: {deleted_direct_nodes}")
                logging.info(f"   - Toplam silinen: {total_deleted}")
                logging.info(f"   - Document node da silindi: {file_name}")

                return True
            else:
                logging.error(f"❌ Otomatik temizlik başarısız oldu: {file_name}")
                return False

        except Exception as e:
            logging.error(f"❌ Otomatik temizlik hatası ({file_name}): {e}")
            return False

    def clear_entities_for_regraph(self, file_name: str) -> dict:
        """
        Re-graph creation öncesi Document'a bağlı entity'leri temizler.
        Document ve Chunk node'ları KORUNUR, sadece entity'ler silinir.

        Bu fonksiyon:
        1. Document ve Chunk'ları KORUR (PART_OF, FIRST_CHUNK, NEXT_CHUNK ilişkileri dahil)
        2. Chunk'lardan EXTRACTED_FROM ilişkilerini ve entity node'larını siler
        3. Document'a direkt/dolaylı bağlı diğer entity'leri siler (Policy, Customer vs.)
        4. Başka Document'lerde kullanılan shared entity'leri KORUR

        Args:
            file_name: Entity'leri temizlenecek dosya adı

        Returns:
            dict: Silinen entity istatistikleri
        """
        try:
            logging.info(f"🧹 Re-graph için entity temizliği başlıyor: {file_name}")

            # 1. Dosyanın var olup olmadığını kontrol et
            check_query = """
                MATCH (d:Document {fileName: $file_name})
                OPTIONAL MATCH (d)<-[:PART_OF]-(c:Chunk)
                RETURN d.fileName as fileName, count(c) as chunkCount
            """
            result = self.execute_query(check_query, {"file_name": file_name})

            if not result or not result[0].get("fileName"):
                logging.info(f"📄 Dosya veritabanında bulunamadı: {file_name}")
                return {"status": "not_found", "deleted_entities": 0}

            chunk_count = result[0].get("chunkCount", 0)
            logging.info(f"🔍 Dosya bulundu: {file_name} ({chunk_count} chunk)")

            # 2. Entity temizleme sorgusu - Document ve Chunk'ları KORUR
            # NOT: Entity'ler Chunk'lara EXTRACTED_FROM ilişkisi ile bağlı (Entity -> Chunk)
            clear_entities_query = """
                MATCH (d:Document {fileName: $file_name})
                
                // 1. Chunk'lardan çıkarılan entity'leri bul (EXTRACTED_FROM ile: Entity -> Chunk)
                OPTIONAL MATCH (d)<-[:PART_OF]-(chunk:Chunk)<-[extractedFrom:EXTRACTED_FROM]-(chunkEntity)
                
                // 2. Document'a direkt bağlı entity'leri bul (Policy, Customer vs.)
                OPTIONAL MATCH (d)<-[docRel:DOCUMENTED_IN|HAS_DOC]-(docEntity)
                WHERE NOT docEntity:Chunk AND NOT docEntity:Document
                
                // 3. Policy'ye bağlı alt entity'leri bul
                OPTIONAL MATCH (d)<-[:DOCUMENTED_IN]-(policy:Policy)-[policyRel]->(policyRelated)
                WHERE NOT policyRelated:Document AND NOT policyRelated:Chunk
                
                // Entity'leri topla
                WITH d, 
                     collect(DISTINCT extractedFrom) AS extractedFromRels,
                     collect(DISTINCT chunkEntity) AS chunkEntities,
                     collect(DISTINCT docEntity) AS docEntities,
                     collect(DISTINCT policy) AS policies,
                     collect(DISTINCT policyRelated) AS policyRelatedNodes
                
                // Güvenlik kontrolü - başka document'larda kullanılmayan entity'leri belirle
                WITH d, extractedFromRels,
                     [entity IN chunkEntities WHERE entity IS NOT NULL 
                      AND NOT EXISTS {
                        MATCH (entity)-[:EXTRACTED_FROM]->(otherChunk:Chunk)-[:PART_OF]->(otherDoc:Document)
                        WHERE otherDoc.fileName <> $file_name
                      }] AS safeChunkEntities,
                     [entity IN docEntities WHERE entity IS NOT NULL
                      AND NOT EXISTS {
                        MATCH (otherDoc:Document)
                        WHERE otherDoc.fileName <> $file_name 
                          AND ((otherDoc)<-[:DOCUMENTED_IN]-(entity) OR (otherDoc)<-[:HAS_DOC]-(entity))
                      }] AS safeDocEntities,
                     [p IN policies WHERE p IS NOT NULL
                      AND NOT EXISTS {
                        MATCH (p)-[:DOCUMENTED_IN]->(otherDoc:Document)
                        WHERE otherDoc.fileName <> $file_name
                      }] AS safePolicies,
                     [node IN policyRelatedNodes WHERE node IS NOT NULL
                      AND NOT EXISTS {
                        MATCH (node)<-[]-(:Policy)-[:DOCUMENTED_IN]->(otherDoc:Document)
                        WHERE otherDoc.fileName <> $file_name
                      }] AS safePolicyRelatedNodes
                
                // EXTRACTED_FROM ilişkilerini sil (Entity-Chunk bağlantısını kopar)
                FOREACH (rel IN extractedFromRels | DELETE rel)
                
                // Güvenli entity'leri sil
                FOREACH (entity IN safeChunkEntities | DETACH DELETE entity)
                FOREACH (entity IN safeDocEntities | DETACH DELETE entity)
                FOREACH (node IN safePolicyRelatedNodes | DETACH DELETE node)
                FOREACH (policy IN safePolicies | DETACH DELETE policy)
                
                RETURN 
                    size(extractedFromRels) as deletedExtractedFromRels,
                    size(safeChunkEntities) as deletedChunkEntities,
                    size(safeDocEntities) as deletedDocEntities,
                    size(safePolicies) as deletedPolicies,
                    size(safePolicyRelatedNodes) as deletedPolicyRelatedNodes
            """

            clear_result = self.execute_query(
                clear_entities_query, {"file_name": file_name}
            )

            if clear_result:
                stats = clear_result[0]
                deleted_extracted_from_rels = stats.get("deletedExtractedFromRels", 0)
                deleted_chunk_entities = stats.get("deletedChunkEntities", 0)
                deleted_doc_entities = stats.get("deletedDocEntities", 0)
                deleted_policies = stats.get("deletedPolicies", 0)
                deleted_policy_related = stats.get("deletedPolicyRelatedNodes", 0)

                total_deleted = (
                    deleted_chunk_entities
                    + deleted_doc_entities
                    + deleted_policies
                    + deleted_policy_related
                )

                logging.info(
                    f"✅ Re-graph için entity temizliği tamamlandı: {file_name}"
                )
                logging.info(f"📊 Temizlik istatistikleri:")
                logging.info(
                    f"   - Silinen EXTRACTED_FROM ilişkileri: {deleted_extracted_from_rels}"
                )
                logging.info(
                    f"   - Silinen Chunk Entity'leri: {deleted_chunk_entities}"
                )
                logging.info(
                    f"   - Silinen Document Entity'leri: {deleted_doc_entities}"
                )
                logging.info(f"   - Silinen Policy'ler: {deleted_policies}")
                logging.info(
                    f"   - Silinen Policy-related node'lar: {deleted_policy_related}"
                )
                logging.info(f"   - Toplam silinen entity: {total_deleted}")
                logging.info(
                    f"   ✅ Document ve Chunk'lar KORUNDU ({chunk_count} chunk)"
                )

                return {
                    "status": "success",
                    "file_name": file_name,
                    "chunks_preserved": chunk_count,
                    "deleted_extracted_from_rels": deleted_extracted_from_rels,
                    "deleted_chunk_entities": deleted_chunk_entities,
                    "deleted_doc_entities": deleted_doc_entities,
                    "deleted_policies": deleted_policies,
                    "deleted_policy_related": deleted_policy_related,
                    "total_deleted_entities": total_deleted,
                }
            else:
                logging.warning(f"⚠️ Entity temizleme sonucu alınamadı: {file_name}")
                return {"status": "no_result", "deleted_entities": 0}

        except Exception as e:
            logging.error(f"❌ Re-graph entity temizleme hatası ({file_name}): {e}")
            return {"status": "error", "error": str(e), "deleted_entities": 0}

    def list_unconnected_nodes(self):
        query = """
        MATCH (e:!Chunk&!Document&!`__Community__`) 
        WHERE NOT exists { (e)--(:!Chunk&!Document&!`__Community__`) }
        OPTIONAL MATCH (doc:Document)<-[:PART_OF]-(c:Chunk)-[:HAS_ENTITY]->(e)
        RETURN 
        e {
            .*,
            embedding: null,
            elementId: elementId(e),
            labels: CASE 
            WHEN size(labels(e)) > 1 THEN 
                apoc.coll.removeAll(labels(e), ["__Entity__"])
            ELSE 
                ["Entity"]
            END
        } AS e, 
        collect(distinct doc.fileName) AS documents, 
        count(distinct c) AS chunkConnections
        ORDER BY e.id ASC
        LIMIT 100
        """
        query_total_nodes = """
        MATCH (e:!Chunk&!Document&!`__Community__`) 
        WHERE NOT exists { (e)--(:!Chunk&!Document&!`__Community__`) }
        RETURN count(*) as total
        """
        nodes_list = self.execute_query(query)
        total_nodes = self.execute_query(query_total_nodes)
        return nodes_list, total_nodes[0]

    def delete_unconnected_nodes(self, unconnected_entities_list):
        entities_list = list(map(str.strip, json.loads(unconnected_entities_list)))
        log_delete(
            f"Starting deletion of {len(entities_list)} unconnected/orphan nodes"
        )
        query = """
        MATCH (e) WHERE elementId(e) IN $elementIds
        DETACH DELETE e
        """
        param = {"elementIds": entities_list}
        result = self.execute_query(query, param)
        log_delete(f"Successfully deleted {len(entities_list)} orphan nodes from graph")
        return result

    def get_duplicate_nodes_list(self):
        score_value = float(os.environ.get("DUPLICATE_SCORE_VALUE", "0.95"))
        text_distance = int(os.environ.get("DUPLICATE_TEXT_DISTANCE", "3"))
        query_duplicate_nodes = """
                MATCH (n:!Chunk&!Session&!Document&!`__Community__`) with n 
                WHERE n.embedding is not null and n.id is not null // and size(toString(n.id)) > 3
                WITH n ORDER BY count {{ (n)--() }} DESC, size(toString(n.id)) DESC // updated
                WITH collect(n) as nodes
                UNWIND nodes as n
                WITH n, [other in nodes 
                // only one pair, same labels e.g. Person with Person
                WHERE elementId(n) < elementId(other) and labels(n) = labels(other)
                // at least embedding similarity of X
                AND 
                (
                // either contains each other as substrings or has a text edit distinct of less than 3
                (size(toString(other.id)) > 2 AND toLower(toString(n.id)) CONTAINS toLower(toString(other.id))) OR 
                (size(toString(n.id)) > 2 AND toLower(toString(other.id)) CONTAINS toLower(toString(n.id)))
                OR (size(toString(n.id))>5 AND apoc.text.distance(toLower(toString(n.id)), toLower(toString(other.id))) < $duplicate_text_distance)
                OR
                vector.similarity.cosine(other.embedding, n.embedding) > $duplicate_score_value
                )] as similar
                WHERE size(similar) > 0 
                // remove duplicate subsets
                with collect([n]+similar) as all
                CALL {{ with all
                    unwind all as nodes
                    with nodes, all
                    // skip current entry if it's smaller and a subset of any other entry
                    where none(other in all where other <> nodes and size(other) > size(nodes) and size(apoc.coll.subtract(nodes, other))=0)
                    return head(nodes) as n, tail(nodes) as similar
                }}
                OPTIONAL MATCH (doc:Document)<-[:PART_OF]-(c:Chunk)-[:HAS_ENTITY]->(n)
                {return_statement}
                """
        return_query_duplicate_nodes = """
                RETURN n {.*, embedding:null, elementId:elementId(n), labels:labels(n)} as e, 
                [s in similar | s {.id, .description, labels:labels(s), elementId: elementId(s)}] as similar,
                collect(distinct doc.fileName) as documents, count(distinct c) as chunkConnections
                ORDER BY e.id ASC
                LIMIT 100
                """
        total_duplicate_nodes = "RETURN COUNT(DISTINCT(n)) as total"

        param = {
            "duplicate_score_value": score_value,
            "duplicate_text_distance": text_distance,
        }

        nodes_list = self.execute_query(
            query_duplicate_nodes.format(return_statement=return_query_duplicate_nodes),
            param=param,
        )
        total_nodes = self.execute_query(
            query_duplicate_nodes.format(return_statement=total_duplicate_nodes),
            param=param,
        )
        return nodes_list, total_nodes[0]

    def merge_duplicate_nodes(self, duplicate_nodes_list):
        nodes_list = json.loads(duplicate_nodes_list)
        logging.info(f"Nodes list to merge {nodes_list}")
        query = """
        UNWIND $rows AS row
        CALL { with row
        MATCH (first) WHERE elementId(first) = row.firstElementId
        MATCH (rest) WHERE elementId(rest) IN row.similarElementIds
        WITH first, collect (rest) as rest
        WITH [first] + rest as nodes
        CALL apoc.refactor.mergeNodes(nodes, 
        {properties:"discard",mergeRels:true, produceSelfRel:false, preserveExistingSelfRels:false, singleElementAsArray:true}) 
        YIELD node
        RETURN size(nodes) as mergedCount
        }
        RETURN sum(mergedCount) as totalMerged
        """
        param = {"rows": nodes_list}
        return self.execute_query(query, param)

    def drop_create_vector_index(self, isVectorIndexExist):
        """
        drop and create the vector index when vector index dimesion are different.
        """
        embedding_model = os.getenv("EMBEDDING_MODEL", "openai")
        embeddings, dimension = load_embedding_model(embedding_model)

        if isVectorIndexExist == "true":
            self.graph.query(
                """drop index vector""",
                session_params={"database": self.graph._database},
            )

        self.graph.query(
            """CREATE VECTOR INDEX `vector` if not exists for (c:Chunk) on (c.embedding)
                            OPTIONS {indexConfig: {
                            `vector.dimensions`: $dimensions,
                            `vector.similarity_function`: 'cosine'
                            }}
                        """,
            {"dimensions": dimension},
            session_params={"database": self.graph._database},
        )
        return "Drop and Re-Create vector index succesfully"

    def get_nodelabels_relationships(self):
        node_query = """
                    CALL db.labels() YIELD label
                    WITH label
                    WHERE NOT label IN ['_Bloom_Perspective_', '__Community__', '__Entity__', 'Session', 'Message']
                    CALL apoc.cypher.run("MATCH (n:`" + label + "`) RETURN count(n) AS count",{}) YIELD value
                    WHERE value.count > 0
                    RETURN label order by label
                    """

        relation_query = """
                CALL db.relationshipTypes() yield relationshipType
                WHERE NOT relationshipType  IN ['HAS_ENTITY', '_Bloom_Perspective_','SIMILAR','IN_COMMUNITY','PARENT_COMMUNITY', 'LAST_MESSAGE', 'NEXT'] 
                return relationshipType order by relationshipType
                """

        try:
            node_result = self.execute_query(node_query)
            node_labels = [record["label"] for record in node_result]
            relationship_result = self.execute_query(relation_query)
            relationship_types = [
                record["relationshipType"] for record in relationship_result
            ]
            return node_labels, relationship_types
        except Exception as e:
            print(f"Error in getting node labels/relationship types from db: {e}")
            return []

    def create_policy_node_from_document(
        self, file_name: str, model: str = "openai_gpt_4o_mini"
    ):
        """
        Belge içeriğinden LLM kullanarak kapsamlı poliçe/zeyilname bilgilerini çıkarır ve tüm ilgili node'ları oluşturur.

        Bu metod:
        1. Veritabanından belgenin Chunk'larını almır
        2. Chunk içeriğini birleştirerek belge metnini oluşturur
        3. LLM ile kapsamlı varlık çıkarımı yapar (document_type dahil)
        4. Document type'a göre Policy veya Endorsement node'u oluşturur
        5. Tüm ilgili entity'leri oluşturur ve ilişkiler kurar
        """
        try:
            logging.info(f"🔍 {file_name} için kapsamlı LLM extraction başlatılıyor...")

            # Belgenin tüm Chunk'larını veritabanından al
            document_content = self._get_document_content_from_chunks(file_name)

            # Domain'e göre farklı extraction yöntemi kullan
            domain = get_domain()
            logging.info(f"📋 Domain: {domain}")

            if domain == "sigorta":
                # ========================================
                # SIGORTA DOMAIN - Eski hardcoded yapı
                # ========================================
                logging.info(f"📋 Sigorta domain - hardcoded entity extraction kullanılıyor")
                
                # LLM ile kapsamlı varlık çıkarımı yap (belge içeriğini geçir)
                entities_data = self.extract_comprehensive_policy_entities_with_llm(
                    file_name, document_content, model
                )

                if not entities_data:
                    error_msg = f"⚠️ {file_name} için varlık çıkarımı başarısız. LLM extraction hatası."
                    logging.error(error_msg)
                    raise Exception(error_msg)

                # Document type'ı kontrol et
                document_type = entities_data.get("document_type", "MAIN_POLICY")

                if document_type in ["ENDORSEMENT", "CANCELLATION", "RENEWAL"]:
                    # Zeyilname/iptal/yenileme olarak işle
                    logging.info(f"📋 {file_name} → {document_type} olarak işleniyor...")
                    self.create_endorsement_entity(entities_data, file_name, document_type)
                else:
                    # Ana poliçe olarak işle
                    logging.info(f"📋 {file_name} → MAIN_POLICY olarak işleniyor...")
                    self.create_comprehensive_policy_entities(
                        entities_data, file_name, model
                    )

                # Document'a docType ve metadata ekle
                update_document_query = """
                    MATCH (d:Document {fileName: $file_name})
                    SET d.docType = $doc_type,
                        d.hasExtractedEntities = true,
                        d.entityExtractionMethod = 'LLM_comprehensive',
                        d.lastProcessedAt = datetime()
                    RETURN d.fileName as updated_file
                """
                self.graph.query(
                    update_document_query,
                    {"file_name": file_name, "doc_type": document_type},
                    session_params={"database": self.graph._database},
                )

            else:
                # ========================================
                # DİĞER DOMAIN'LER - Generic Graph Executor
                # ========================================
                logging.info(f"📋 {domain} domain - generic graph executor kullanılıyor")
                
                # Generic entity extraction (nodes/relationships format)
                llm_output = self._extract_entities_generic(file_name, document_content, model)
                
                if not llm_output:
                    error_msg = f"⚠️ {file_name} için generic varlık çıkarımı başarısız."
                    logging.error(error_msg)
                    raise Exception(error_msg)
                
                # Generic Graph Executor ile Neo4j'ye yaz
                executor = GenericGraphExecutor(self.graph)
                result = executor.create_graph_from_llm_output(llm_output, file_name)
                
                if not result.get("success"):
                    errors = result.get("errors", [])
                    logging.warning(f"⚠️ Generic graph creation partial failure: {errors}")
                
                document_type = llm_output.get("document_type", "DIGER")

            logging.info(
                f"✅ {file_name} için kapsamlı extraction tamamlandı ({document_type})"
            )

        except Exception as e:
            logging.error(
                f"Policy/Endorsement node oluşturma hatası ({file_name}): {e}"
            )
            raise e

    def create_embeddings_for_documents(self, file_names: list):
        """
        Belirtilen dosyalar için chunk embedding'leri oluşturur (manuel işlem)

        Bu fonksiyon:
        1. Belirtilen dosyalara ait Chunk node'ları bulur
        2. Embedding'i olmayan chunk'lar için embedding oluşturur
        3. Vector index'i kontrol eder/oluşturur
        4. KNN graph ilişkilerini günceller

        Args:
            file_names: Embedding oluşturulacak dosya adları listesi

        Returns:
            dict: İşlem sonuç raporu
        """
        try:
            from src.shared.common_fn import load_embedding_model
            from src.make_relationships import (
                create_chunk_vector_index,
                create_chunk_fulltext_index,
            )

            logging.info(
                f"🔄 {len(file_names)} dosya için embedding oluşturma başlatılıyor: {file_names}"
            )

            total_processed = 0
            total_updated = 0
            results = {}

            # Embedding model yükle
            embedding_model = os.getenv(
                "EMBEDDING_MODEL", "openai_text_embedding_3_small"
            )
            embeddings, dimension = load_embedding_model(embedding_model)
            logging.info(
                f"🤖 Embedding model loaded: {embedding_model} (dimension: {dimension})"
            )

            for file_name in file_names:
                try:
                    # Dosya adını normalize et
                    from src.utf8_utils import normalize_file_name

                    normalized_file_name = normalize_file_name(file_name)
                    logging.info(f"📁 {file_name} için embedding işlemi başlıyor...")
                    logging.info(f"🔄 Normalized file name: {normalized_file_name}")

                    # Dosyaya ait embedding'i olmayan chunk'ları bul
                    find_chunks_query = """
                        MATCH (d:Document {fileName: $file_name})<-[:PART_OF]-(c:Chunk)
                        WHERE c.embedding IS NULL
                        RETURN c.id as chunk_id, c.text as chunk_text
                        ORDER BY c.position
                    """

                    chunks_result = self.execute_query(
                        find_chunks_query, {"file_name": normalized_file_name}
                    )

                    logging.info(
                        f"🔍 Query result for {normalized_file_name}: {len(chunks_result) if chunks_result else 0} chunks found"
                    )
                    if chunks_result:
                        logging.info(
                            f"📋 First chunk sample: {chunks_result[0] if chunks_result else 'None'}"
                        )

                    if not chunks_result:
                        logging.info(
                            f"✅ {normalized_file_name}: Tüm chunk'lar zaten embedding'e sahip veya chunk bulunamadı"
                        )
                        results[file_name] = {
                            "status": "skipped",
                            "message": "Tüm chunk'lar zaten embedding'e sahip veya chunk bulunamadı",
                            "chunks_processed": 0,
                            "chunks_updated": 0,
                        }
                        continue

                    logging.info(
                        f"📊 {normalized_file_name}: {len(chunks_result)} chunk için embedding oluşturulacak"
                    )

                    # Batch halinde embedding oluştur
                    batch_data = []
                    chunks_processed = 0

                    for chunk_info in chunks_result:
                        try:
                            chunk_id = chunk_info["chunk_id"]
                            chunk_text = chunk_info["chunk_text"] or ""

                            if not chunk_text.strip():
                                logging.warning(f"⚠️ Boş chunk atlandı: {chunk_id}")
                                continue

                            # Text normalization
                            from src.utf8_utils import normalize_unicode_text

                            normalized_text = normalize_unicode_text(chunk_text)

                            # Embedding oluştur
                            embedding_vector = embeddings.embed_query(normalized_text)

                            batch_data.append(
                                {"chunk_id": chunk_id, "embedding": embedding_vector}
                            )

                            chunks_processed += 1

                            # Her 50 chunk'ta bir batch işle
                            if len(batch_data) >= 50:
                                updated_count = self._update_chunk_embeddings_batch(
                                    batch_data
                                )
                                total_updated += updated_count
                                logging.info(
                                    f"📦 Batch işlendi: {len(batch_data)} chunk, {updated_count} güncellendi"
                                )
                                batch_data = []

                        except Exception as chunk_error:
                            logging.error(
                                f"❌ Chunk embedding hatası ({chunk_id}): {chunk_error}"
                            )
                            continue

                    # Kalan batch'i işle
                    if batch_data:
                        updated_count = self._update_chunk_embeddings_batch(batch_data)
                        total_updated += updated_count
                        logging.info(
                            f"📦 Son batch işlendi: {len(batch_data)} chunk, {updated_count} güncellendi"
                        )

                    total_processed += chunks_processed

                    results[file_name] = {
                        "status": "success",
                        "message": f"{chunks_processed} chunk işlendi, {len(chunks_result)} embedding oluşturuldu",
                        "chunks_processed": chunks_processed,
                        "chunks_updated": len(chunks_result),
                    }

                    logging.info(
                        f"✅ {normalized_file_name}: {chunks_processed} chunk için embedding oluşturuldu"
                    )

                except Exception as file_error:
                    logging.error(
                        f"❌ {normalized_file_name} için embedding oluşturma hatası: {file_error}"
                    )
                    results[file_name] = {
                        "status": "error",
                        "message": str(file_error),
                        "chunks_processed": 0,
                        "chunks_updated": 0,
                    }

            # Vector index ve Fulltext index'i kontrol et/oluştur
            if total_updated > 0:
                try:
                    create_chunk_vector_index(self.graph)
                    logging.info(f"✅ Vector index checked/updated")

                    # Fulltext index oluştur (keyword search için)
                    create_chunk_fulltext_index(self.graph)
                    logging.info(f"✅ Fulltext index checked/updated")

                    # KNN graph ilişkilerini güncelle - DEVRE DIŞI BIRAKTI
                    # self.update_KNN_graph()
                    # logging.info(f"✅ KNN graph relationships updated")

                except Exception as index_error:
                    logging.warning(
                        f"⚠️ Vector/Fulltext index update warning: {index_error}"
                    )

            # Genel sonuç raporu
            summary = {
                "total_files": len(file_names),
                "total_chunks_processed": total_processed,
                "total_chunks_updated": total_updated,
                "files": results,
                "embedding_model": embedding_model,
                "embedding_dimension": dimension,
            }

            logging.info(
                f"🎉 Embedding oluşturma tamamlandı: {total_processed} chunk işlendi, {total_updated} embedding oluşturuldu"
            )

            return summary

        except Exception as e:
            error_msg = f"Embedding oluşturma hatası: {e}"
            logging.error(f"❌ {error_msg}")
            return {
                "total_files": len(file_names) if file_names else 0,
                "total_chunks_processed": 0,
                "total_chunks_updated": 0,
                "error": error_msg,
                "files": {},
            }

    def _update_chunk_embeddings_batch(self, batch_data):
        """
        Chunk embedding'lerini batch halinde güncelle

        Args:
            batch_data: [{"chunk_id": "...", "embedding": [...]}] formatında liste

        Returns:
            int: Güncellenen chunk sayısı
        """
        try:
            # CALL IN TRANSACTIONS ile her 25 chunk'ta commit - timeout önler
            update_query = """
                UNWIND $batch_data AS row
                CALL {
                    WITH row
                    MATCH (c:Chunk {id: row.chunk_id})
                    SET c.embedding = row.embedding
                    RETURN c
                } IN TRANSACTIONS OF 25 ROWS
                RETURN count(*) as updated_count
            """

            result = self.execute_query(update_query, {"batch_data": batch_data})
            return result[0]["updated_count"] if result else 0

        except Exception as e:
            logging.error(f"❌ Batch embedding update hatası: {e}")
            return 0

    def create_entity_embeddings(self, node_types: list):
        """
        Belirtilen entity node türleri için embedding'ler oluşturur

        Bu fonksiyon:
        1. Belirtilen node türlerini bulur
        2. Text/name/description özelliklerinden embedding oluşturur
        3. Entity embedding'lerini node'lara ekler

        Args:
            node_types: Embedding oluşturulacak node türleri listesi
                      (örn: ["Customer", "Policy", "Coverage", "all"])

        Returns:
            dict: İşlem sonuç raporu
        """
        try:
            from src.shared.common_fn import load_embedding_model

            logging.info(
                f"🔄 {len(node_types)} node türü için entity embedding oluşturma başlatılıyor: {node_types}"
            )

            # Embedding model yükle
            embedding_model = os.getenv(
                "EMBEDDING_MODEL", "openai_text_embedding_3_small"
            )
            embeddings, dimension = load_embedding_model(embedding_model)
            logging.info(
                f"🤖 Entity embedding model loaded: {embedding_model} (dimension: {dimension})"
            )

            # Mevcut entity node türlerini al
            available_node_types = self._get_available_entity_node_types()
            logging.info(
                f"📋 Mevcut entity node türleri: {list(available_node_types.keys())}"
            )

            # İşlenecek node türlerini belirle
            if "all" in node_types:
                target_node_types = list(available_node_types.keys())
            else:
                target_node_types = [
                    nt for nt in node_types if nt in available_node_types
                ]

            if not target_node_types:
                return {
                    "total_node_types": 0,
                    "total_entities_processed": 0,
                    "total_embeddings_created": 0,
                    "error": "Geçerli node türü bulunamadı",
                    "available_types": list(available_node_types.keys()),
                }

            logging.info(f"🎯 İşlenecek node türleri: {target_node_types}")

            total_processed = 0
            total_updated = 0
            results = {}

            for node_type in target_node_types:
                try:
                    logging.info(
                        f"📊 {node_type} node'ları için embedding oluşturuluyor..."
                    )

                    # Node'ları ve text özelliklerini al
                    entities_query = f"""
                        MATCH (n:{node_type})
                        WHERE n.embedding IS NULL
                        RETURN 
                            elementId(n) as node_id,
                            n.name as name,
                            n.description as description,
                            n.id as entity_id,
                            coalesce(n.name, n.description, n.id, '') as text_content
                        ORDER BY n.name, n.id
                        LIMIT 1000
                    """

                    entities_result = self.execute_query(entities_query)

                    if not entities_result:
                        logging.info(
                            f"✅ {node_type}: Tüm entity'ler zaten embedding'e sahip veya entity bulunamadı"
                        )
                        results[node_type] = {
                            "status": "skipped",
                            "message": "Tüm entity'ler zaten embedding'e sahip",
                            "entities_processed": 0,
                            "entities_updated": 0,
                        }
                        continue

                    logging.info(
                        f"📊 {node_type}: {len(entities_result)} entity için embedding oluşturulacak"
                    )

                    # Batch halinde embedding oluştur
                    batch_data = []
                    entities_processed = 0

                    for entity_info in entities_result:
                        try:
                            node_id = entity_info["node_id"]
                            text_content = entity_info["text_content"] or ""

                            if not text_content.strip():
                                logging.warning(
                                    f"⚠️ Boş text content atlandı: {node_id}"
                                )
                                continue

                            # Text normalization
                            from src.utf8_utils import normalize_unicode_text

                            normalized_text = normalize_unicode_text(text_content)

                            # Embedding oluştur
                            embedding_vector = embeddings.embed_query(normalized_text)

                            batch_data.append(
                                {"node_id": node_id, "embedding": embedding_vector}
                            )

                            entities_processed += 1

                            # Her 50 entity'de bir batch işle
                            if len(batch_data) >= 50:
                                updated_count = self._update_entity_embeddings_batch(
                                    batch_data
                                )
                                total_updated += updated_count
                                logging.info(
                                    f"📦 {node_type} batch işlendi: {len(batch_data)} entity, {updated_count} güncellendi"
                                )
                                batch_data = []

                        except Exception as entity_error:
                            logging.error(
                                f"❌ Entity embedding hatası ({node_id}): {entity_error}"
                            )
                            continue

                    # Kalan batch'i işle
                    if batch_data:
                        updated_count = self._update_entity_embeddings_batch(batch_data)
                        total_updated += updated_count
                        logging.info(
                            f"📦 {node_type} son batch işlendi: {len(batch_data)} entity, {updated_count} güncellendi"
                        )

                    total_processed += entities_processed

                    results[node_type] = {
                        "status": "success",
                        "message": f"{entities_processed} entity işlendi, {len(entities_result)} embedding oluşturuldu",
                        "entities_processed": entities_processed,
                        "entities_updated": len(entities_result),
                    }

                    logging.info(
                        f"✅ {node_type}: {entities_processed} entity için embedding oluşturuldu"
                    )

                except Exception as type_error:
                    logging.error(
                        f"❌ {node_type} için entity embedding hatası: {type_error}"
                    )
                    results[node_type] = {
                        "status": "error",
                        "message": str(type_error),
                        "entities_processed": 0,
                        "entities_updated": 0,
                    }

            # Entity embedding'leri için vector index oluştur/kontrol et
            if total_updated > 0:
                try:
                    self._create_entity_vector_indexes(target_node_types, dimension)
                    logging.info(
                        f"✅ Entity vector indexes checked/created for {len(target_node_types)} node types"
                    )
                except Exception as index_error:
                    logging.warning(
                        f"⚠️ Entity vector index creation warning: {index_error}"
                    )

            # Genel sonuç raporu
            summary = {
                "total_node_types": len(target_node_types),
                "total_entities_processed": total_processed,
                "total_embeddings_created": total_updated,
                "node_types": results,
                "embedding_model": embedding_model,
                "embedding_dimension": dimension,
                "available_types": list(available_node_types.keys()),
            }

            logging.info(
                f"🎉 Entity embedding oluşturma tamamlandı: {total_processed} entity işlendi, {total_updated} embedding oluşturuldu"
            )

            return summary

        except Exception as e:
            error_msg = f"Entity embedding oluşturma hatası: {e}"
            logging.error(f"❌ {error_msg}")
            return {
                "total_node_types": len(node_types) if node_types else 0,
                "total_entities_processed": 0,
                "total_embeddings_created": 0,
                "error": error_msg,
                "node_types": {},
            }

    def _get_available_entity_node_types(self):
        """
        Veritabanındaki entity node türlerini ve sayılarını al
        """
        try:
            query = """
                MATCH (n)
                WHERE NOT n:Chunk AND NOT n:Document AND NOT n:`__Community__` AND NOT n:Session
                RETURN DISTINCT labels(n) as node_labels, count(*) as count
                ORDER BY count DESC
            """

            result = self.execute_query(query)
            node_types = {}

            for row in result:
                labels = row["node_labels"]
                count = row["count"]
                if labels and len(labels) > 0:
                    # İlk label'ı al (çoğunlukla tek label olur)
                    main_label = labels[0]
                    node_types[main_label] = count

            return node_types

        except Exception as e:
            logging.error(f"Available node types alınırken hata: {e}")
            return {}

    def _update_entity_embeddings_batch(self, batch_data):
        """
        Entity embedding'lerini batch halinde güncelle

        Args:
            batch_data: [{"node_id": "...", "embedding": [...]}] formatında liste

        Returns:
            int: Güncellenen entity sayısı
        """
        try:
            # CALL IN TRANSACTIONS ile her 25 entity'de commit - timeout önler
            update_query = """
                UNWIND $batch_data AS row
                CALL {
                    WITH row
                    MATCH (n) WHERE elementId(n) = row.node_id
                    SET n.embedding = row.embedding
                    RETURN n
                } IN TRANSACTIONS OF 25 ROWS
                RETURN count(*) as updated_count
            """

            result = self.execute_query(update_query, {"batch_data": batch_data})
            return result[0]["updated_count"] if result else 0

        except Exception as e:
            logging.error(f"❌ Entity batch embedding update hatası: {e}")
            return 0

    def _create_entity_vector_indexes(self, node_types: list, dimension: int):
        """
        Entity node türleri için vector index'leri oluştur

        Args:
            node_types: Vector index oluşturulacak node türleri listesi
            dimension: Embedding vektörlerinin boyutu
        """
        try:
            logging.info(
                f"🔍 {len(node_types)} node türü için vector index kontrolü başlıyor..."
            )

            for node_type in node_types:
                try:
                    index_name = f"entity_{node_type.lower()}_embedding_vector"

                    # Index'in var olup olmadığını kontrol et
                    check_query = """
                        SHOW INDEXES 
                        YIELD name, type, labelsOrTypes, properties
                        WHERE name = $index_name AND type = 'VECTOR'
                        RETURN name
                    """

                    existing_index = self.execute_query(
                        check_query, {"index_name": index_name}
                    )

                    if existing_index:
                        logging.info(f"✅ Vector index zaten mevcut: {index_name}")
                        continue

                    # Vector index oluştur
                    create_index_query = f"""
                        CREATE VECTOR INDEX `{index_name}` IF NOT EXISTS
                        FOR (n:`{node_type}`) ON (n.embedding)
                        OPTIONS {{
                            indexConfig: {{
                                `vector.dimensions`: $dimensions,
                                `vector.similarity_function`: 'cosine'
                            }}
                        }}
                    """

                    self.execute_query(create_index_query, {"dimensions": dimension})
                    logging.info(
                        f"✅ Vector index oluşturuldu: {index_name} ({node_type}, {dimension}D)"
                    )

                except Exception as node_error:
                    logging.error(
                        f"❌ {node_type} için vector index oluşturma hatası: {node_error}"
                    )
                    continue

            logging.info(f"🎉 Entity vector index oluşturma işlemi tamamlandı")

        except Exception as e:
            logging.error(f"❌ Entity vector index oluşturma genel hatası: {e}")
            raise e

    def get_websource_url(self, file_name):
        logging.info("Checking if same title with different URL exist in db ")
        query = """
                MATCH(d:Document {fileName : $file_name}) WHERE d.fileSource = "web-url" 
                RETURN d.url AS url
                """
        param = {"file_name": file_name}
        return self.execute_query(query, param)

    def update_token_usage(
        self,
        file_name: str,
        total_tokens: int,
        input_tokens: int = 0,
        output_tokens: int = 0,
        processing_time: float = 0,
    ):
        """
        Document node'a token kullanım bilgilerini kaydet
        """
        try:
            logging.info(f"Token kullanım bilgileri kaydediliyor - Dosya: {file_name}")

            query = """
                MATCH (d:Document {fileName: $file_name})
                SET 
                    d.total_tokens = $total_tokens,
                    d.input_tokens = $input_tokens,
                    d.output_tokens = $output_tokens,
                    d.token_processing_time = $processing_time,
                    d.tokens_per_second = CASE 
                        WHEN $processing_time > 0 THEN toFloat($total_tokens) / $processing_time 
                        ELSE 0 
                    END,
                    d.token_updated_at = datetime()
                RETURN d.total_tokens as updated_tokens
            """

            result = self.execute_query(
                query,
                {
                    "file_name": file_name,
                    "total_tokens": total_tokens,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "processing_time": processing_time,
                },
            )

            if result:
                logging.info(
                    f"✅ Token bilgileri başarıyla kaydedildi: {total_tokens} token"
                )
                logging.info(
                    f"📊 Token detayları - Input: {input_tokens}, Output: {output_tokens}"
                )
                if processing_time > 0:
                    logging.info(f"⚡ Token/saniye: {total_tokens/processing_time:.1f}")
            else:
                logging.warning(f"⚠️ Token bilgileri kaydedilemedi: {file_name}")

        except Exception as e:
            logging.error(f"Token bilgisi kaydetme hatası ({file_name}): {e}")

    def _link_endorsement_to_main_policy(self, file_name: str, policy_info: dict):
        """
        Zeyilname dosyasını, policy_info'daki policy_number veya yenileme numarası ile
        veritabanında bulunan mevcut ana poliçe Policy node'una HAS_ENDORSEMENT ilişkisiyle bağlar.
        """
        try:
            # None değerlerini handle et (LLM bazen None dönebilir)
            policy_number = (policy_info.get("policy_number") or "").strip()
            renewal_number = (policy_info.get("renewal_number") or "").strip()
            customer_name = (policy_info.get("customer_name") or "").strip()
            policy_type = (policy_info.get("policy_type") or "").strip()

            logging.info(f"🔗 Zeyilname ana poliçe bağlantısı aranıyor: {file_name}")
            logging.info(f"   Policy Number: {policy_number}")
            logging.info(f"   Renewal Number: {renewal_number}")
            logging.info(f"   Customer: {customer_name}")
            logging.info(f"   Policy Type: {policy_type}")

            # Ana poliçeyi bulma stratejileri (öncelik sırasıyla)
            main_policy = None

            # 1. Policy Number ile ara (boşlukları normalize et)
            if policy_number:
                # Policy number'daki boşlukları kaldır ve normalize et
                normalized_policy_number = policy_number.replace(" ", "").strip()

                # Önce tam eşleşme dene
                find_policy_query = """
                    MATCH (p:Policy)
                    WHERE p.policyNumber = $policy_number
                       OR apoc.text.clean(p.policyNumber) = apoc.text.clean($policy_number)
                       OR replace(p.policyNumber, ' ', '') = $normalized_policy_number
                    RETURN p.id as policy_id, p.name as policy_name, p.policyNumber as policy_number
                    LIMIT 1
                """
                result = self.graph.query(
                    find_policy_query,
                    {
                        "policy_number": policy_number,
                        "normalized_policy_number": normalized_policy_number,
                    },
                    session_params={"database": self.graph._database},
                )

                if result:
                    main_policy = result[0]
                    logging.info(
                        f"✅ Policy Number ile ana poliçe bulundu: {main_policy['policy_id']} (Policy Number: {main_policy.get('policy_number', 'N/A')})"
                    )

            # 2. Renewal Number ile ara (eğer policy number ile bulunamadıysa)
            if not main_policy and renewal_number:
                find_policy_query = """
                    MATCH (p:Policy)
                    WHERE p.policyNumber = $renewal_number OR p.id CONTAINS $renewal_number
                    RETURN p.id as policy_id, p.name as policy_name, p.policyNumber as policy_number
                    LIMIT 1
                """
                result = self.graph.query(
                    find_policy_query,
                    {"renewal_number": renewal_number},
                    session_params={"database": self.graph._database},
                )

                if result:
                    main_policy = result[0]
                    logging.info(
                        f"✅ Renewal Number ile ana poliçe bulundu: {main_policy['policy_id']}"
                    )

            # 3. Customer name ve policy type ile ara (son çare) - apoc.text.clean ile güvenli arama
            if not main_policy and customer_name:
                policy_type = policy_info.get("policy_type", "").strip()

                # Önce policy type ile dene
                if policy_type:
                    policy_type_condition = """
                      AND (
                        EXISTS {
                          (p)-[:HAS_POLICY_TYPE]->(pt:PolicyType)
                          WHERE apoc.text.clean(pt.typeName) CONTAINS apoc.text.clean($policy_type)
                             OR apoc.text.clean(pt.name) CONTAINS apoc.text.clean($policy_type)
                        }
                        OR apoc.text.clean(coalesce(p.type, '')) CONTAINS apoc.text.clean($policy_type)
                        OR apoc.text.clean($policy_type) CONTAINS apoc.text.clean(coalesce(p.type, ''))
                      )"""

                    find_policy_query = f"""
                        MATCH (c:Customer)-[:HAS_POLICY]->(p:Policy)
                        WHERE (apoc.text.clean(c.name) CONTAINS apoc.text.clean($customer_name)
                           OR apoc.text.clean($customer_name) CONTAINS apoc.text.clean(c.name))
                        {policy_type_condition}
                        RETURN p.id as policy_id, p.name as policy_name, p.policyNumber as policy_number
                        ORDER BY p.createdAt DESC
                        LIMIT 1
                    """

                    result = self.graph.query(
                        find_policy_query,
                        {"customer_name": customer_name, "policy_type": policy_type},
                        session_params={"database": self.graph._database},
                    )

                    if result:
                        main_policy = result[0]
                        logging.info(
                            f"✅ Customer + Policy Type pattern ile ana poliçe bulundu: {main_policy['policy_id']} (Policy Type: {policy_type})"
                        )

                # Eğer policy type ile bulamazsa, sadece customer ile dene (zeyilname farklı poliçe tipine ait olabilir)
                if not main_policy:
                    logging.info(
                        f"ℹ️ Policy type ile bulunamadı, sadece customer pattern deneniyor..."
                    )

                    find_policy_query = """
                        MATCH (c:Customer)-[:HAS_POLICY]->(p:Policy)
                        WHERE apoc.text.clean(c.name) CONTAINS apoc.text.clean($customer_name)
                           OR apoc.text.clean($customer_name) CONTAINS apoc.text.clean(c.name)
                        RETURN p.id as policy_id, p.name as policy_name, p.policyNumber as policy_number, p.type as policy_type
                        ORDER BY p.createdAt DESC
                        LIMIT 1
                    """

                    result = self.graph.query(
                        find_policy_query,
                        {"customer_name": customer_name},
                        session_params={"database": self.graph._database},
                    )

                    if result:
                        main_policy = result[0]
                        found_policy_type = main_policy.get("policy_type", "N/A")
                        logging.info(
                            f"✅ Customer-only pattern ile ana poliçe bulundu: {main_policy['policy_id']} (Actual Policy Type: {found_policy_type})"
                        )
                        logging.info(
                            f"ℹ️ Zeyilname policy type '{policy_type}' ≠ Ana poliçe type '{found_policy_type}' - Bu normal olabilir (aksesuar zeyli vs.)"
                        )

            # 4. Yakınlık araması (similarity search) - %95 eşik değeri ile (son çare)
            if not main_policy and customer_name:
                logging.info(
                    f"🔍 Yakınlık araması deneniyor (similarity >= 0.95) - Customer: {customer_name}"
                )

                # Customer name ile yakınlık araması
                similarity_query = """
                    MATCH (c:Customer)-[:HAS_POLICY]->(p:Policy)
                    WITH c, p,
                         apoc.text.levenshteinSimilarity(apoc.text.clean(c.name), apoc.text.clean($customer_name)) as name_similarity
                    WHERE name_similarity >= 0.95
                    RETURN p.id as policy_id, p.name as policy_name, p.policyNumber as policy_number, 
                           p.type as policy_type, name_similarity
                    ORDER BY name_similarity DESC, p.createdAt DESC
                    LIMIT 1
                """

                try:
                    result = self.graph.query(
                        similarity_query,
                        {"customer_name": customer_name},
                        session_params={"database": self.graph._database},
                    )

                    if result:
                        main_policy = result[0]
                        similarity_score = main_policy.get("name_similarity", 0)
                        found_policy_type = main_policy.get("policy_type", "N/A")
                        similarity_percent = similarity_score * 100
                        logging.info(
                            f"✅ Yakınlık araması ile ana poliçe bulundu: {main_policy['policy_id']} (Similarity: {similarity_percent:.2f}%, Policy Type: {found_policy_type})"
                        )
                except Exception as similarity_error:
                    # APOC fonksiyonu yoksa veya hata olursa, bu seçeneği atla
                    logging.warning(
                        f"⚠️ Yakınlık araması yapılamadı (APOC gerekli olabilir): {str(similarity_error)}"
                    )

            if main_policy:
                # Ana poliçe bulundu, sadece Document'a metadata ekle (HAS_ENDORSEMENT kullanmayacağız)
                logging.info(
                    f"✅ Ana poliçe bulundu, kronolojik zincir için: {main_policy['policy_id']}"
                )

                # Document'a endorsement bilgisi ekle (year kaldırıldı)
                update_document_query = """
                    MATCH (d:Document {fileName: $file_name})
                    SET d.docType = 'ENDORSEMENT',
                        d.linkedMainPolicy = $main_policy_id,
                        d.updatedAt = datetime()
                    RETURN d.fileName as updated_file
                """

                self.graph.query(
                    update_document_query,
                    {
                        "file_name": file_name,
                        "main_policy_id": main_policy["policy_id"],
                    },
                    session_params={"database": self.graph._database},
                )

                # Bulunan policy bilgilerini döndür
                return {
                    "success": True,
                    "policy_id": main_policy["policy_id"],
                    "policy_number": main_policy.get("policy_number", ""),
                    "policy_name": main_policy.get("policy_name", ""),
                }
            else:
                logging.warning(
                    f"⚠️ Ana poliçe bulunamadı. Zeyilname bağımsız kalacak: {file_name}"
                )
                logging.warning(
                    f"   Aranan kriteler - Policy Number: {policy_number}, Renewal: {renewal_number}, Customer: {customer_name}, Policy Type: {policy_type}"
                )
                return {"success": False}

        except Exception as e:
            logging.error(f"❌ Zeyilname ana poliçe bağlantı hatası ({file_name}): {e}")
            return False

    def extract_comprehensive_policy_entities_with_llm(
        self,
        file_name: str,
        document_content: str = "",
        model: str = "openai_gpt_4o_mini",
    ) -> dict:
        """
        LLM kullanarak poliçe belgesinden kapsamlı varlık bilgilerini çıkarır.

        Çıkarılan varlıklar:
        - Customer (Müşteri)
        - Policy (Poliçe)
        - InsuranceCompany (Sigorta Şirketi)
        - Premium (Prim)
        - Date (Başlangıç/Bitiş tarihleri)
        - Coverage (Teminat)
        - Coverage (Teminat Türü)
        - Endorsement (Zeyilname)
        - Guarantee (Garanti)
        - Clause (Hüküm/Kloz)
        - Payment (Ödeme)
        - Address (Adres/Risk Adresi)

        Args:
            file_name: Belge adı
            document_content: Belgenin metin içeriği (Chunk'lardan alınan)
            model: Kullanılacak LLM modeli (upload endpoint'ten gelir)

        Returns:
            dict: Tüm çıkarılan varlıkları içeren dictionary
        """
        logging.info(
            f"🤖 LLM Entity Extraction başlatılıyor - Model: {model}, Dosya: {file_name}"
        )
        try:
            # Gemini kullanılıyor mu kontrol et
            use_gemini = False
            try:
                from google import genai as genai_sdk

                GEMINI_AVAILABLE = True
                use_gemini = True
            except ImportError:
                GEMINI_AVAILABLE = False
                use_gemini = False

            # Belge içeriğini kullan (Chunk'lardan alınan tüm metni, optimize ediliyor)
            # Eğer çok uzunsa, otomatik olarak önemli bölümleri seçer
            document_content_for_llm = document_content if document_content else ""

            # İlk 5 chunk'ın içeriğini logla (debug için)
            if document_content_for_llm:
                chunks_for_log = document_content_for_llm.split("\n")[:5]
                logging.info(
                    f"📄 LLM extraction için kullanılan ilk 5 chunk içeriği ({file_name}):"
                )
                for i, chunk_text in enumerate(chunks_for_log, 1):
                    chunk_preview = (
                        chunk_text[:200] + "..."
                        if len(chunk_text) > 200
                        else chunk_text
                    )
                    logging.info(f"   Chunk {i}: {chunk_preview}")

            # Kapsamlı extraction prompt'u - Domain-specific dosyadan yükle
            domain = get_domain()
            try:
                prompt_template = load_prompt("entity_extraction", domain)
                prompt = prompt_template.format(
                    file_name=file_name, document_content=document_content_for_llm
                )
                logging.info(f"📋 Using entity_extraction prompt for domain: {domain}")
            except (FileNotFoundError, KeyError) as e:
                logging.warning(
                    f"⚠️ External prompt not found ({e}), using fallback prompt"
                )
                # Fallback: Hardcoded sigorta prompt'u
                prompt = f"""
Verilen sigorta poliçesi belgesinden aşağıdaki bilgileri çıkar ve JSON formatında döndür.

Belge adı: "{file_name}"

Belge İçeriği:
---
{document_content_for_llm}
---

Çıkarılacak bilgiler (tüm alanlar opsiyonel, varsa doldur):

1. CUSTOMER (Müşteri) - ÖNEMLİ:
   - name: Müşteri adı (kişi adı veya şirket adı)
   - type: "Individual" veya "Corporate"
   - responsible_person: Sorumlu kişi (şirketi ise)
   
   MÜŞTERİ ADI BULMA KURALLARI:
   - "Sigortalı", "Müşteri", "Sigorta Ettiren" başlıklarından sonraki isim müşteri adıdır
   - "Ünvanı", "Adı", "Ad Soyad" etiketlerinden sonraki metin müşteri adıdır
   - Şirket adları "A.Ş.", "LTD. ŞTİ.", "ANONİM ŞİRKETİ" gibi kelimelerle biter
   - Belgede açıkça belirtilmemişse boş bırak, UYDURMA

2. INSURANCE_COMPANY (Sigorta Şirketi):
   - name: Şirket adı
   - responsible_person: Temsilci/Sorumlu kişi adı
   - agency: Acente bilgileri (varsa)
     * name: Acente adı
     * addressCode: ADRES KODU
     * easyLine: KOLAY HAT
     * phone: Telefon

3. POLICY (Poliçe):
   - policyNumber: Poliçe numarası (ana poliçe numarası)
   - companyPolicyNumber: Şirket Poliçe Numarası (Şirket Pol.No)
   - daskPolicyNumber: DASK Poliçe Numarası (varsa)
   - policySerialNumber: Poliçe Seri No
   - endorsementNumber: Ek Belge Numarası (zeyilname için)
   - issueDate: Tanzim Tarihi (YYYY-MM-DD formatında)
   - currency: Para birimi (TRY, USD, EUR, vb.)
   - status: Durum (Aktif, İptal, Yenilendi, Geçmiş, vb.)
   - type: Poliçe türü (Konut, DASK, Kasko, Trafik, Sağlık, Hayat, KOBİ/Ticari, Ortak Alan, Yangın, Sorumluluk, Hayat, Ferdi Kaza, vb.)

4. DOCUMENT_TYPE (Belge Türü) - ÖNEMLİ:
   - document_type: "MAIN_POLICY" (ana poliçe), "ENDORSEMENT" (zeyilname), "CANCELLATION" (iptal), "RENEWAL" (yenileme)
   - Belirleme kuralları:
     * Dosya isminde veya belgede "zeyilname", "zeyli", "zeyl" → ENDORSEMENT
     * Dosya isminde veya belgede "iptal", "fesih" → CANCELLATION
     * Dosya isminde veya belgede "yenileme" → RENEWAL
     * Hiçbiri yoksa → MAIN_POLICY
   - ÖNEMLİ: Bu alan ZORUNLU! Mutlaka bir değer dön!

5. DATE (Tarihler):
   - start_date: Başlangıç tarihi (YYYY-MM-DD formatında)
   - end_date: Bitiş tarihi (YYYY-MM-DD formatında)
   - renewal_date: Yenileme tarihi (varsa)

6. INSURANCE_AMOUNT (Sigorta Bedeli ve Prim Bilgileri) - ÖNEMLİ:
   - insuranceValue: Sigorta Bedeli (sayı, örn: 526190.00)
   - policyPremium: Poliçe Primi (sayı, örn: 1089.21)
   - endorsementInsuranceValue: Zeyil Sigorta Bedeli (zeyilname için, sayı)
   - endorsementPremium: Zeyil Poliçe Primi (zeyilname için, sayı)
   - tariffPrice: Tarife Fiyatı (sayı)
   - currency: Para birimi (TRY, USD, EUR, vb.)
   - NOT: Coverage ve Premium ayrı node'lar. Coverage teminat limitlerini, Premium prim tutarlarını tutar.

7. PREMIUM (Prim):
   - amount: Prim tutarı (sayı) - insurance_amount.policyPremium veya endorsementPremium'dan alınabilir
   - currency: Para birimi
   - commission_rate: Komisyon oranı (varsa)

8. COVERAGE (Teminat):
   - limit_value: Teminat limiti (sayı) - insurance_amount.insuranceValue veya endorsementInsuranceValue'dan alınabilir
   - limit_unit: Birim (TL, USD, Gün, Adet, vb.)
   - limit_count: Adet/Seans sayısı (varsa)
   - scope: Coğrafi kapsam ("Türkiye", "Yurtdışı", "Global", vb.)
   - NOT: Coverage sayısal teminat limitlerini tutar (limit_value, limit_unit, limit_count, scope)

9. COVERAGE_TYPES (Teminat Türleri - liste):
   - name: Teminat türü ("Deprem", "Yangın", "Sorumluluk", "Sağlık", vb.)
   - NOT: Coverage kategorik teminat türlerini tutar (Deprem, Yangın, vb.). Coverage ile ayrı node'lardır.

10. GUARANTEE (Garantiler - liste):
   - name: Garanti adı ("Ömür boyu yenileme", "Hasarsızlık indirimi", vb.)
   - value: Değeri ("true", "%10", "2 yıl", vb.)

11. CLAUSE (Hükümler/Klozlar - liste):
    - name: Kloz adı ("Muafiyet", "İstisna", "Özel Hüküm", vb.)
    - text: Kloz/Wording metni

12. ENDORSEMENT (Zeyilnameler - liste):
    - name: Zeyil tipi ("İptal", "Yenileme", "Teminat Ek", vb.)
    - description: Açıklama
    - NOT: Bu liste sadece ana poliçe içinde zeyilname referansları için. Zeyilname belgesi işlenirken document_type="ENDORSEMENT" olur.

13. PAYMENT (Ödeme):
    - amount: Ödeme tutarı
    - dueDate: Ödeme vadesi (YYYY-MM-DD formatında)
    - method: Ödeme şekli (Nakit, Çek, Havale, vb.)

14. ADDRESS (Risk Adresi):
    - address: Tam adres
    - city: İl
    - district: İlçe
    - neighborhood: Semt/Mahalle (varsa)

15. INSURED_PROPERTY (Sigortalanan Yer Bilgileri) - ÖNEMLİ:
    - city: İl
    - district: İlçe
    - neighborhood: Semt/Belde
    - address: Tam adres
    - deed: Tapu bilgileri
      * ada: Ada numarası
      * parsel: Parsel numarası
      * pafta: Pafta numarası
      * independentSectionNumber: Tapu Bağımsız Bölüm No
    - building: Bina bilgileri
      * constructionType: Bina İnşa Tarzı (ÇELİK, BETONARME, vb.)
      * constructionYear: Bina İnşa Yılı (örn: "1976 - 1999")
      * totalFloors: Toplam Kat Sayısı (örn: "01-03 ARASI")
      * usageType: Daire Kullanım Şekli (MESKEN, TİCARİ, vb.)
      * area: Daire Yüzölçümü (sayı, örn: 70)
      * areaUnit: Yüzölçüm birimi (m², vb.)
    - damageStatus: Hasar Durumu (HASARSIZ, HASARLI, vb.)

16. INSURED_PERSON (Sigortalı Bilgileri):
    - name: Sigortalının Adı Soyadı
    - nationality: Uyruk (T.C., vb.)
    - tcIdentityNumber: TC Kimlik No
    - mobilePhone: Cep Telefonu
    - landlinePhone: Sabit Telefonu
    - email: E-Posta
    - contactAddress: İletişim Adresi

17. POLICYHOLDER (Sigorta Ettiren Bilgileri):
    - name: Sigorta Ettirenin Adı
    - nationality: Uyruk (T.C., vb.)
    - tcIdentityNumber: TC Kimlik No
    - mobilePhone: Cep Telefonu
    - landlinePhone: Sabit Telefonu
    - email: E-Posta
    - role: Sıfatı (MAL SAHİBİ, vb.)

18. ENDORSEMENT_SPECIAL (Zeyilname Özel Durumları):
    - isPremiumFree: Primsiz zeyilname mi? (true/false) - "BU POLİÇE PRİMSİZ BİR ZEYİLDİR" ifadesi varsa true
    - discountType: İndirim Tipi (varsa)

19. POLICY_RELATIONSHIP (Poliçe İlişki Tipi) - ÖNEMLİ:
    - relationship_type: Müşteri ile poliçe arasında kullanılacak ilişki tipi adı
    - Kurallar:
      * Poliçe türüne göre anlam taşıyan bir ilişki adı oluştur
      * Format: "IS_[POLİÇE_TÜRÜ]_POLICY" şeklinde olsun
      * Örnek: "Kasko" → "IS_KASKO_POLICY"
      * Örnek: "Konut Sigortası" → "IS_KONUT_POLICY"
      * Örnek: "DASK" → "IS_DASK_POLICY"
      * Örnek: "KOBİ/Ticari" → "IS_KOBI_TICARI_POLICY"
      * Türkçe karakterleri İngilizceye çevir (İ→I, Ğ→G, Ü→U, Ö→O, Ş→S, Ç→C)
      * Boşluk ve özel karakterleri alt çizgi (_) ile değiştir
      * Büyük harfle yaz
    - properties: İlişkide taşınacak poliçe türüne özel property'ler
      * KASKO/TRAFİK POLİÇELERİ İÇİN: plateNumber, vehicleBrand, vehicleModel, modelYear, engineSize, vehicleValue, chassisNumber
      * KONUT/DASK POLİÇELERİ İÇİN: buildingType, floorNumber, totalFloors, squareMeters, buildingAge, hasElevator, constructionType
      * İŞVEREN SORUMLULUK İÇİN: employeeCount, workType, riskLevel, hasFood, workHours
      * SAĞLIK POLİÇELERİ İÇİN: inpatientLimit, outpatientLimit, internationalCoverage, maternityBenefit, dentalCoverage
      * GENERIC CLAIM PROPERTIES: hasClaimHistory, claimCount, claimFreeYears (tüm poliçe türleri için)
      * GENERIC FINANCIAL PROPERTIES: annualBudget, hasMortgage, mortgageBank (finansal bilgiler için)
      * GENERIC ASSET PROPERTIES: assetLength, assetAge, hasServiceAsset, assetType, assetSpecification (esnek asset bilgileri için)
    - ÖNEMLİ: Bu alan ZORUNLU! Policy türü varsa mutlaka ilişki tipi dön!

20. DEDUCTIBLE_INFO (Muafiyet Bilgileri):
    - deductibleAmount: Muafiyet tutarı (sayı)
    - deductibleType: Muafiyet türü (örn: "Sabit", "Oransal")
    - deductiblePercentage: Muafiyet oranı (varsa, %)
    - deductibleDescription: Muafiyet açıklaması

Kurallar:
- SADECE belge içeriğinde açıkça belirtilen bilgileri çıkar
- Emin olmadığın bilgileri UYDURMA
- Opsiyonel alanlar boş bırakılabilir
- Listeler için [] kullan, tek öğe için de dizi içinde gönder
- ⚠️ KRİTİK SAYI FORMATI: Sayıları JSON formatında yaz (İngilizce format):
  * ❌ YANLIŞ: 422.240,00 (Türkçe format - bin ayracı nokta, ondalık virgül)
  * ✅ DOĞRU: 422240.00 (JSON format - bin ayracı yok, ondalık nokta)
  * Belgede "422.240,00 TL" görürsen → JSON'a 422240.00 yaz
  * Belgede "1.089,21 TL" görürsen → JSON'a 1089.21 yaz
- ⚠️ LİSTE LİMİTLERİ (JSON boyutunu küçük tut!):
  * coverage_types: EN FAZLA 5 adet (en önemli teminatlar)
  * guarantees: EN FAZLA 3 adet (en önemli garantiler)
  * clauses: EN FAZLA 3 adet (ana klozlar, detaylı metinler YAZMA)
  * endorsements: EN FAZLA 3 adet
  * installments: EN FAZLA 12 adet (yıllık taksitler)
  * insured_persons: EN FAZLA 5 adet
  * Kloz/garanti metinlerini KISALT (max 100 karakter)

Yanıt formatı (sadece JSON, başka açıklama ekleme):
{{
    "document_type": "MAIN_POLICY",  # ÖNEMLİ: ZORUNLU ALAN! "MAIN_POLICY", "ENDORSEMENT", "RENEWAL", "CANCELLATION"
    "customer": {{
        "name": "...",
        "type": "...",
        "responsible_person": "..."
    }},
    "insurance_company": {{
        "name": "...",
        "responsible_person": "...",
        "agency": {{
            "name": "...",
            "addressCode": "...",
            "easyLine": "...",
            "phone": "..."
        }}
    }},
    "policy": {{
        "policyNumber": "...",
        "companyPolicyNumber": "...",
        "daskPolicyNumber": "...",
        "policySerialNumber": "...",
        "endorsementNumber": "...",
        "issueDate": "...",
        "currency": "...",
        "status": "...",
        "type": "..."
    }},
    "insurance_amount": {{
        "insuranceValue": 422240.00,
        "policyPremium": 1089.21,
        "endorsementInsuranceValue": null,
        "endorsementPremium": null,
        "tariffPrice": null,
        "currency": "TRY"
    }},
    "dates": {{
        "start_date": "...",
        "end_date": "...",
        "renewal_date": "..."
    }},
    "premium": {{
        "amount": null,
        "currency": "...",
        "commission_rate": null
    }},
    "coverage": {{
        "limit_value": null,
        "limit_unit": "...",
        "limit_count": null,
        "scope": "..."
    }},
    "coverage_types": [
        {{"name": "..."}},
    ],
    "guarantees": [
        {{"name": "...", "value": "..."}},
    ],
    "clauses": [
        {{"name": "...", "text": "..."}},
    ],
    "endorsements": [
        {{"name": "...", "description": "..."}},
    ],
    "payment": {{
        "amount": null,
        "dueDate": "...",
        "method": "..."
    }},
    "address": {{
        "address": "...",
        "city": "...",
        "district": "...",
        "neighborhood": "..."
    }},
    "insured_property": {{
        "city": "...",
        "district": "...",
        "neighborhood": "...",
        "address": "...",
        "deed": {{
            "ada": "...",
            "parsel": "...",
            "pafta": "...",
            "independentSectionNumber": null
        }},
        "building": {{
            "constructionType": "...",
            "constructionYear": "...",
            "totalFloors": "...",
            "usageType": "...",
            "area": null,
            "areaUnit": "..."
        }},
        "damageStatus": "..."
    }},
    "insured_person": {{
        "name": "...",
        "nationality": "...",
        "tcIdentityNumber": "...",
        "mobilePhone": "...",
        "landlinePhone": "...",
        "email": "...",
        "contactAddress": "..."
    }},
    "policyholder": {{
        "name": "...",
        "nationality": "...",
        "tcIdentityNumber": "...",
        "mobilePhone": "...",
        "landlinePhone": "...",
        "email": "...",
        "role": "..."
    }},
    "endorsement_special": {{
        "isPremiumFree": false,
        "discountType": null
    }},
    "policy_relationship": {{
        "relationship_type": "IS_KASKO_POLICY",  # Örnek: Policy türüne göre oluşturulan ilişki
        "properties": {{
            "plateNumber": "...",
            "vehicleBrand": "...",
            "vehicleModel": "...",
            "modelYear": null,
            "squareMeters": null,
            "employeeCount": null,
            "inpatientLimit": null,
            "buildingType": "...",
            "workType": "...",
            "outpatientLimit": null,
            "hasClaimHistory": null,
            "claimCount": null,
            "claimFreeYears": null,
            "annualBudget": null,
            "hasMortgage": null,
            "mortgageBank": "...",
            "assetLength": null,
            "assetAge": null,
            "hasServiceAsset": null,
            "assetType": "...",
            "assetSpecification": "..."
        }}
    }},
    "deductible_info": {{
        "deductibleAmount": null,
        "deductibleType": "...",
        "deductiblePercentage": null,
        "deductibleDescription": "..."
    }}
}}
"""

            # Gemini kullanılıyorsa Gemini SDK ile çağır, değilse eski LLM yöntemi
            if use_gemini:
                try:
                    api_key = os.environ.get("GEMINI_API_KEY")
                    if not api_key:
                        logging.warning(
                            "❌ GEMINI_API_KEY not found, falling back to regular LLM"
                        )
                        use_gemini = False
                    else:
                        # Create Gemini client
                        client = genai_sdk.Client(api_key=api_key)
                        from google.genai import types

                        logging.info("✅ Using Gemini 2.0 Flash for entity extraction")

                        # Gemini'ye prompt gönder - max output tokens ayarla
                        try:
                            generation_config = types.GenerateContentConfig(
                                max_output_tokens=8192,  # JSON response için yeterli token limit
                                temperature=0.1,  # Daha tutarlı JSON için düşük temperature
                            )

                            response = client.models.generate_content(
                                model="models/gemini-2.0-flash",
                                contents=[
                                    types.Part.from_text(text=prompt),
                                ],
                                config=generation_config,
                            )
                        except Exception as config_error:
                            # GenerationConfig hatası varsa, config olmadan dene
                            logging.warning(
                                f"⚠️ GenerationConfig hatası: {config_error}, config olmadan deneniyor..."
                            )
                            response = client.models.generate_content(
                                model="models/gemini-2.5-flash-lite",
                                contents=[
                                    types.Part.from_text(text=prompt),
                                ],
                            )

                        response_text = response.text.strip() if response.text else ""
                        logging.info(
                            f"🔍 Gemini entity extraction response length: {len(response_text)} chars"
                        )

                except Exception as gemini_error:
                    logging.warning(
                        f"⚠️ Gemini entity extraction failed: {gemini_error}, falling back to regular LLM"
                    )
                    use_gemini = False

            # Gemini kullanılamadıysa veya başarısız olduysa, eski LLM yöntemini kullan
            if not use_gemini:
                from src.llm import get_llm

                # Upload endpoint'ten gelen model parametresini kullan
                llm, _ = get_llm(model)

                # LLM'den yanıt al
                response = llm.invoke(prompt)  # type: ignore[union-attr]
                response_text = response.content.strip() if hasattr(response, "content") and response.content else ""  # type: ignore[union-attr]

            # JSON parse et
            try:
                if "{" in response_text and "}" in response_text:
                    start_idx = response_text.find("{")
                    end_idx = response_text.rfind("}") + 1
                    json_text = response_text[start_idx:end_idx]

                    # JSON parse dene
                    try:
                        entities_data = json.loads(json_text)
                    except json.JSONDecodeError as json_error:
                        # Escape karakteri hatalarını düzeltmeye çalış
                        logging.warning(
                            f"⚠️ JSON parse hatası (escape karakteri sorunu olabilir): {json_error}"
                        )
                        logging.info("🔄 JSON'u düzeltmeye çalışıyoruz...")

                        # Geçersiz escape karakterlerini ve control character'ları düzelt
                        # Önce markdown code block'ları temizle
                        json_text_cleaned = json_text
                        if json_text_cleaned.startswith("```json"):
                            json_text_cleaned = (
                                json_text_cleaned.replace("```json", "")
                                .replace("```", "")
                                .strip()
                            )
                        elif json_text_cleaned.startswith("```"):
                            json_text_cleaned = json_text_cleaned.replace(
                                "```", ""
                            ).strip()

                        # Control character'ları temizle (JSON'da geçersiz: \x00-\x1F arası, \x7F hariç \n, \t, \r)
                        import re
                        import string

                        # JSON'da geçerli control character'lar: \n (0x0A), \t (0x09), \r (0x0D)
                        # Diğer control character'ları (0x00-0x08, 0x0B-0x0C, 0x0E-0x1F, 0x7F) temizle
                        def remove_control_characters(text):
                            # Geçerli control character'ları koru: \n, \t, \r
                            # Diğerlerini boşluk veya kaldır
                            result = []
                            for char in text:
                                code = ord(char)
                                # Geçerli control character'lar: \n (10), \t (9), \r (13)
                                if code in [9, 10, 13]:
                                    result.append(char)
                                # Geçersiz control character'lar: 0-8, 11-12, 14-31, 127
                                elif code < 32 or code == 127:
                                    # Boşluk ile değiştir (JSON parse için daha güvenli)
                                    result.append(" ")
                                else:
                                    result.append(char)
                            return "".join(result)

                        json_text_cleaned = remove_control_characters(json_text_cleaned)

                        # Geçersiz escape karakterlerini düzelt
                        # Python'da geçerli escape karakterleri: \\, \", \', \n, \t, \r, \b, \f
                        # Geçersiz olanları (örn: \K, \A) düzelt
                        def fix_invalid_escapes(text):
                            # Geçerli escape karakterleri: \\, \", \', \n, \t, \r, \b, \f, \uXXXX, \xXX
                            # Geçersiz escape karakterlerini bul (örn: \K, \A, \1, vb.)
                            # Pattern: backslash + karakter, ama geçerli escape değilse
                            # Geçerli escape'ler: \\, \", \', \n, \t, \r, \b, \f, \u (4 hex), \x (2 hex)
                            pattern = r'\\(?![\\"\'ntrbfux0-9])'
                            # Geçersiz escape karakterlerini sadece backslash'i kaldırarak düzelt
                            # Yani \K -> K, \A -> A (backslash kaldırılır)
                            fixed = re.sub(pattern, "", text)
                            return fixed

                        json_text_cleaned = fix_invalid_escapes(json_text_cleaned)

                        # Tekrar parse dene
                        try:
                            entities_data = json.loads(json_text_cleaned)
                            logging.info("✅ JSON düzeltme başarılı, parse edildi")
                        except json.JSONDecodeError as retry_error:
                            # Hala parse edilemiyorsa, daha agresif temizleme yap
                            logging.warning(
                                f"⚠️ İlk düzeltme başarısız, daha agresif temizleme deneniyor: {retry_error}"
                            )

                            # Tüm backslash'leri temizle (son çare)
                            json_text_cleaned = json_text_cleaned.replace("\\", "")

                            try:
                                entities_data = json.loads(json_text_cleaned)
                                logging.info(
                                    "✅ Agresif temizleme başarılı, JSON parse edildi"
                                )
                            except json.JSONDecodeError as final_error:
                                # Son çare: sadece hata mesajını logla ve exception fırlat (retry için)
                                error_msg = f"LLM yanıtı JSON parse edilemedi: {final_error}. İlk hata: {json_error}"
                                logging.error(f"❌ {error_msg}")
                                logging.error(
                                    f"Response text (first 1000 chars): {response_text[:1000]}"
                                )
                                # Exception fırlat ki retry mekanizması çalışsın
                                raise ValueError(error_msg) from final_error

                    # OCR hatalarını düzelt, GPT-5 fallback ve filename'den fallback kullan
                    entities_data = self._validate_and_fix_customer_name(
                        entities_data, file_name, document_content_for_llm
                    )

                    extraction_method = "Gemini 2.0 Flash" if use_gemini else model
                    logging.info(
                        f"✅ {extraction_method} başarıyla kapsamlı varlık bilgilerini çıkardı ({len(document_content_for_llm)} karakter içerikten)"
                    )
                    return entities_data
                else:
                    error_msg = "LLM yanıtında JSON formatı bulunamadı"
                    logging.error(error_msg)
                    logging.error(
                        f"Response text (first 500 chars): {response_text[:500]}"
                    )
                    # Exception fırlat ki retry mekanizması çalışsın
                    raise ValueError(error_msg)

            except (json.JSONDecodeError, ValueError) as e:
                # JSON parse hatası veya ValueError - retry için exception fırlat
                error_msg = f"LLM yanıtı JSON parse edilemedi: {e}"
                logging.error(f"❌ {error_msg}")
                logging.error(
                    f"Response text (first 1000 chars): {response_text[:1000]}"
                )
                # Exception fırlat ki retry mekanizması çalışsın
                raise ValueError(error_msg) from e

        except Exception as e:
            logging.error(f"Kapsamlı varlık çıkarma hatası: {e}")
            return {}

    def _validate_and_fix_customer_name(
        self, entities_data: dict, file_name: str, document_content: str = ""
    ) -> dict:
        """
        LLM extraction'ı öncelikli kullanır, hatalı/eksikse GPT-5 ile retry yapar, son olarak filename'den fallback yapar
        """
        try:
            customer_data = entities_data.get("customer")
            # customer None olabilir, bu durumda boş dict kullan
            if customer_data is None:
                customer_data = {}
            # None.strip() hatasını önle - get("name") None dönerse boş string kullan
            extracted_name = (
                (customer_data.get("name") or "").strip() if customer_data else ""
            )

            # Filename'den müşteri adını çıkar (fallback için)
            filename_customer = self._extract_customer_name_from_filename(file_name)

            # 1. LLM başarıyla çıkardıysa, LLM'i kullan
            if extracted_name:
                logging.info(f"✅ LLM'den müşteri ismi alındı: '{extracted_name}'")
                customer_data["source"] = "llm_extraction"
                entities_data["customer"] = customer_data
                return entities_data

            # 2. Gemini müşteri ismi bulamadıysa, GPT-5 ile retry yap
            if not extracted_name and document_content:
                logging.info(
                    "🔄 Gemini müşteri ismi bulamadı, GPT-5 ile retry yapılıyor..."
                )
                gpt5_customer = self._retry_customer_extraction_with_gpt5(
                    document_content, file_name
                )
                if gpt5_customer:
                    logging.info(f"✅ GPT-5'den müşteri ismi alındı: '{gpt5_customer}'")
                    entities_data["customer"] = {
                        "name": gpt5_customer,
                        "type": customer_data.get(
                            "type",
                            (
                                "Corporate"
                                if any(
                                    kw in gpt5_customer.upper()
                                    for kw in ["A.Ş.", "AŞ", "ŞİRKET", "HOLDİNG", "LTD"]
                                )
                                else "Individual"
                            ),
                        ),
                        "source": "gpt5_fallback",
                    }
                    return entities_data

            # 3. GPT-5 de bulamadıysa filename'den al
            if not extracted_name and filename_customer:
                logging.info(
                    f"📝 LLM'ler müşteri ismi çıkaramadı, filename kullanılıyor: '{filename_customer}'"
                )
                entities_data["customer"] = {
                    "name": filename_customer,
                    "type": customer_data.get("type", "Individual"),
                    "source": "filename_fallback_no_llm",
                }

            # 4. Hiç isim yoksa boş bırak
            elif not extracted_name:
                logging.error(
                    f"❌ Hem LLM'ler hem filename'den müşteri ismi çıkarılamadı: {file_name}"
                )
                customer_data["source"] = "extraction_failed"
                entities_data["customer"] = customer_data

            return entities_data

        except Exception as e:
            logging.error(f"Customer name validation hatası: {e}")
            return entities_data

    def _retry_customer_extraction_with_gpt5(
        self, document_content: str, file_name: str
    ) -> str:
        """
        GPT-5 ile müşteri adı çıkarmayı dene (Gemini başarısız olduğunda fallback)
        """
        try:
            import openai

            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                logging.warning(
                    "❌ OPENAI_API_KEY bulunamadı, GPT-5 fallback atlanıyor"
                )
                return ""

            client = openai.OpenAI(api_key=api_key)

            # Sadece müşteri adı için focused prompt
            prompt = f"""Bu Türk sigorta poliçesi belgesinden SİGORTALI veya SİGORTA ETTİREN kısmındaki müşteri adını çıkar.

KURALLAR:
1. Sadece "Sigortalı:", "Sigorta Ettiren:", "Müşteri:" gibi başlıklardan sonra gelen ismi al
2. Şirket ise tam unvanı al (örn: "AKENERJİ ELEKTRİK ÜRETİM ANONİM ŞİRKETİ")
3. Bireysel müşteri ise ad-soyad al (örn: "MEHMET YILMAZ")
4. Adres, telefon, TC kimlik numarası dahil ETME
5. Bulamazsan boş string dön

BELGE İÇERİĞİ (ilk 8000 karakter):
{document_content[:8000]}

DOSYA ADI: {file_name}

Sadece müşteri adını yaz, başka bir şey yazma:"""

            response = client.chat.completions.create(
                model="gpt-5",
                messages=[
                    {
                        "role": "system",
                        "content": "Sen bir sigorta belgesi analiz uzmanısın. Sadece istenen bilgiyi ver, açıklama yapma.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
            )

            message_content = response.choices[0].message.content
            customer_name = message_content.strip() if message_content else ""

            # Boş veya çok kısa cevapları reddet
            if (
                customer_name
                and len(customer_name) > 2
                and customer_name.lower() not in ["yok", "bulunamadı", "none", ""]
            ):
                return customer_name
            return ""

        except Exception as e:
            logging.warning(f"⚠️ GPT-5 müşteri çıkarma hatası: {e}")
            return ""

    def _extract_customer_name_from_filename(self, file_name: str) -> str:
        """
        Dosya adından müşteri adını çıkarır
        Örnek: "Satvet Çiftçi Barclay 14 D1 Dask_2020.pdf" -> "Satvet Çiftçi"
        """
        try:
            import re

            # Dosya uzantısını kaldır
            base_name = file_name.replace(".pdf", "").replace(".PDF", "")

            # Yaygın pattern'ler - müşteri adı genelde başta
            patterns = [
                # "Ad Soyad Konum/Proje Bilgi_Yıl" formatı - greedy kullanarak tam ismi yakala
                r"^([A-ZÇĞIİÖŞÜa-zçğıiöşü\s]+)\s+(?:[A-Z0-9]+\s+|[A-ZÇĞIİÖŞÜa-zçğıiöşü]+\s+)*(?:Konut|Dask|Kasko|Trafik)",
                # "Ad Soyad SomethingElse_Year" formatı - büyük harf/rakamdan önce dur
                r"^([A-ZÇĞIİÖŞÜa-zçğıiöşü\s]+)\s+[A-Z0-9]",
                # "Ad Soyad" başlangıcı (en az 2 kelime) - tam isme izin ver
                r"^([A-ZÇĞIİÖŞÜ][a-zçğıiöşü]+(?:\s+[A-ZÇĞIİÖŞÜ][a-zçğıiöşü]+)+)",
            ]

            for pattern in patterns:
                match = re.match(pattern, base_name, re.IGNORECASE)
                if match:
                    customer_name = match.group(1).strip()
                    # Title case uygula (Türkçe karakterler için)
                    customer_name = " ".join(
                        word.capitalize() for word in customer_name.split()
                    )
                    logging.debug(
                        f"Filename'den çıkarılan müşteri: '{customer_name}' (pattern: {pattern})"
                    )
                    return customer_name

            logging.debug(f"Filename'den müşteri adı çıkarılamadı: {file_name}")
            return ""

        except Exception as e:
            logging.error(f"Filename parse hatası: {e}")
            return ""

    def _calculate_name_similarity_simple(self, name1: str, name2: str) -> float:
        """
        İki isim arasında basit benzerlik hesaplar
        """
        try:
            import difflib

            # Normalize et
            norm1 = name1.lower().strip()
            norm2 = name2.lower().strip()

            # Levenshtein similarity
            return difflib.SequenceMatcher(None, norm1, norm2).ratio()

        except Exception:
            return 0.0

    def merge_existing_duplicate_customers(self):
        """
        Sistemde mevcut olan text similarity ve normalize edilmiş isme göre duplicate Customer node'larını birleştirir

        Similarity kriterleri (EN YÜKSEK SKORLU - en sıkı kriterler):
        1. Normalize edilmiş isimler tamamen eşit
        2. Text edit distance <= 1 (sadece minimal yazım farkları)
        3. Bir isim diğerinin substring'i (contains ilişkisi - minimum 6 karakter)
        4. Jaro-Winkler similarity >= 0.95 (çok yüksek benzerlik threshold'u)
        """
        try:
            logging.info(
                "🔍 Mevcut duplicate Customer node'ları text similarity ile kontrol ediliyor..."
            )

            # Text similarity parametreleri - customer için EN SIKICI kriterler
            import os

            max_edit_distance = int(os.environ.get("CUSTOMER_EDIT_DISTANCE", "1"))
            min_jaro_similarity = float(
                os.environ.get("CUSTOMER_JARO_SIMILARITY", "0.95")
            )
            min_substring_length = int(
                os.environ.get("CUSTOMER_MIN_SUBSTRING_LENGTH", "6")
            )

            logging.info(f"📊 Customer similarity parametreleri (EN YÜKSEK SKORLU):")
            logging.info(f"   - Max edit distance: {max_edit_distance} (EN SIKI)")
            logging.info(
                f"   - Min Jaro-Winkler similarity: {min_jaro_similarity} (EN YÜKSEK)"
            )
            logging.info(f"   - Min substring length: {min_substring_length} (EN UZUN)")

            # Duplicate customer'ları text similarity ile bul
            find_duplicates_query = """
                MATCH (c1:Customer), (c2:Customer)
                WHERE elementId(c1) < elementId(c2)
                  AND (
                    // 1. Normalize edilmiş isimler tamamen eşit
                    apoc.text.clean(c1.name) = apoc.text.clean(c2.name)
                    OR
                    // 2. Text edit distance kontrolü (çok minimal yazım farkları)
                    apoc.text.distance(toLower(c1.name), toLower(c2.name)) <= $max_edit_distance
                    OR
                    // 3. Substring kontrolü (bir isim diğerinin içinde - uzun minimum)
                    (
                      size(c1.name) >= $min_substring_length AND 
                      size(c2.name) >= $min_substring_length AND
                      (
                        toLower(c2.name) CONTAINS toLower(c1.name) OR
                        toLower(c1.name) CONTAINS toLower(c2.name)
                      )
                    )
                    OR
                    // 4. Jaro-Winkler similarity kontrolü (çok yüksek threshold)
                    apoc.text.jaroWinklerDistance(toLower(c1.name), toLower(c2.name)) >= $min_jaro_similarity
                  )
                WITH c1, c2,
                     apoc.text.clean(c1.name) = apoc.text.clean(c2.name) as normalized_equal,
                     apoc.text.distance(toLower(c1.name), toLower(c2.name)) as edit_distance,
                     apoc.text.jaroWinklerDistance(toLower(c1.name), toLower(c2.name)) as jaro_similarity,
                     (toLower(c2.name) CONTAINS toLower(c1.name) OR toLower(c1.name) CONTAINS toLower(c2.name)) as is_substring
                RETURN {
                    c1: {
                        name: c1.name,
                        element_id: elementId(c1),
                        policy_count: count { (c1)-[:HAS_POLICY]->() }
                    },
                    c2: {
                        name: c2.name,
                        element_id: elementId(c2),
                        policy_count: count { (c2)-[:HAS_POLICY]->() }
                    },
                    similarity_info: {
                        normalized_equal: normalized_equal,
                        edit_distance: edit_distance,
                        jaro_similarity: jaro_similarity,
                        is_substring: is_substring
                    }
                } as duplicate_pair
                ORDER BY duplicate_pair.similarity_info.normalized_equal DESC, 
                         duplicate_pair.similarity_info.jaro_similarity DESC
            """

            duplicates_result = self.execute_query(
                find_duplicates_query,
                {
                    "max_edit_distance": max_edit_distance,
                    "min_jaro_similarity": min_jaro_similarity,
                    "min_substring_length": min_substring_length,
                },
            )

            if not duplicates_result:
                logging.info(
                    "✅ Text similarity ile duplicate Customer node'u bulunamadı"
                )
                return 0

            total_merged = 0
            processed_pairs = set()  # Aynı çiftin tekrar işlenmesini engellemek için

            logging.info(f"🎯 {len(duplicates_result)} duplicate customer çift bulundu")

            for record in duplicates_result:
                duplicate_pair = record["duplicate_pair"]
                c1 = duplicate_pair["c1"]
                c2 = duplicate_pair["c2"]
                similarity_info = duplicate_pair["similarity_info"]

                # Bu çift daha önce işlendi mi kontrol et
                pair_key = tuple(sorted([c1["element_id"], c2["element_id"]]))
                if pair_key in processed_pairs:
                    continue

                processed_pairs.add(pair_key)

                # Master'ı seç (daha çok policy ile bağlantısı olan, eşitse daha uzun isimli)
                if c1["policy_count"] > c2["policy_count"]:
                    master, duplicate = c1, c2
                elif c2["policy_count"] > c1["policy_count"]:
                    master, duplicate = c2, c1
                else:
                    # Policy sayısı eşitse, daha uzun ve detaylı ismi olan master olsun
                    if len(c1["name"]) >= len(c2["name"]):
                        master, duplicate = c1, c2
                    else:
                        master, duplicate = c2, c1

                logging.info(f"🔧 Merge işlemi (YÜKSEK SKORLU):")
                logging.info(
                    f"   Master: '{master['name']}' (Policy: {master['policy_count']})"
                )
                logging.info(
                    f"   Duplicate: '{duplicate['name']}' (Policy: {duplicate['policy_count']})"
                )
                logging.info(
                    f"   Similarity: Normalized={similarity_info['normalized_equal']}, "
                    f"Edit_dist={similarity_info['edit_distance']}, "
                    f"Jaro={similarity_info['jaro_similarity']:.3f}, "
                    f"Substring={similarity_info['is_substring']}"
                )

                # APOC ile merge et
                merge_query = """
                    MATCH (master) WHERE elementId(master) = $master_id
                    MATCH (duplicate) WHERE elementId(duplicate) = $duplicate_id
                    WITH [master, duplicate] as nodes
                    CALL apoc.refactor.mergeNodes(nodes, 
                        {properties:"discard", mergeRels:true, produceSelfRel:false, 
                         preserveExistingSelfRels:false, singleElementAsArray:true}) 
                    YIELD node
                    RETURN node.name as merged_name
                """

                result = self.execute_query(
                    merge_query,
                    {
                        "master_id": master["element_id"],
                        "duplicate_id": duplicate["element_id"],
                    },
                )

                if result:
                    total_merged += 1
                    logging.info(
                        f"  ✅ Merged: '{duplicate['name']}' -> '{master['name']}'"
                    )
                else:
                    logging.warning(f"  ⚠️ Merge işlemi başarısız: {duplicate['name']}")

            logging.info(
                f"🎉 Toplam {total_merged} duplicate Customer node birleştirildi"
            )
            return total_merged

        except Exception as e:
            logging.error(f"❌ Duplicate customer merge hatası: {e}")
            return 0

    def merge_existing_duplicate_insurance_companies(self):
        """
        Sistemde mevcut olan text similarity ve normalize edilmiş isme göre duplicate InsuranceCompany node'larını birleştirir

        Similarity kriterleri:
        1. Normalize edilmiş isimler tamamen eşit
        2. Text edit distance <= 2 (küçük yazım farkları, insurance company için daha sıkı)
        3. Bir isim diğerinin substring'i (contains ilişkisi)
        4. Jaro-Winkler similarity >= 0.90 (insurance company için daha yüksek threshold)
        """
        try:
            logging.info(
                "🔍 Mevcut duplicate InsuranceCompany node'ları text similarity ile kontrol ediliyor..."
            )

            # Text similarity parametreleri - insurance company için daha sıkı kriterler
            import os

            max_edit_distance = int(
                os.environ.get("INSURANCE_COMPANY_EDIT_DISTANCE", "2")
            )
            min_jaro_similarity = float(
                os.environ.get("INSURANCE_COMPANY_JARO_SIMILARITY", "0.90")
            )
            min_substring_length = int(
                os.environ.get("INSURANCE_COMPANY_MIN_SUBSTRING_LENGTH", "5")
            )

            logging.info(f"📊 Insurance Company similarity parametreleri:")
            logging.info(f"   - Max edit distance: {max_edit_distance}")
            logging.info(f"   - Min Jaro-Winkler similarity: {min_jaro_similarity}")
            logging.info(f"   - Min substring length: {min_substring_length}")

            # Duplicate insurance company'leri text similarity ile bul
            find_duplicates_query = """
                MATCH (ic1:InsuranceCompany), (ic2:InsuranceCompany)
                WHERE elementId(ic1) < elementId(ic2)
                  AND (
                    // 1. Normalize edilmiş isimler tamamen eşit
                    apoc.text.clean(ic1.name) = apoc.text.clean(ic2.name)
                    OR
                    // 2. Text edit distance kontrolü (küçük yazım farkları)
                    apoc.text.distance(toLower(ic1.name), toLower(ic2.name)) <= $max_edit_distance
                    OR
                    // 3. Substring kontrolü (bir isim diğerinin içinde)
                    (
                      size(ic1.name) >= $min_substring_length AND 
                      size(ic2.name) >= $min_substring_length AND
                      (
                        toLower(ic2.name) CONTAINS toLower(ic1.name) OR
                        toLower(ic1.name) CONTAINS toLower(ic2.name)
                      )
                    )
                    OR
                    // 4. Jaro-Winkler similarity kontrolü
                    apoc.text.jaroWinklerDistance(toLower(ic1.name), toLower(ic2.name)) >= $min_jaro_similarity
                  )
                WITH ic1, ic2,
                     apoc.text.clean(ic1.name) = apoc.text.clean(ic2.name) as normalized_equal,
                     apoc.text.distance(toLower(ic1.name), toLower(ic2.name)) as edit_distance,
                     apoc.text.jaroWinklerDistance(toLower(ic1.name), toLower(ic2.name)) as jaro_similarity,
                     (toLower(ic2.name) CONTAINS toLower(ic1.name) OR toLower(ic1.name) CONTAINS toLower(ic2.name)) as is_substring
                RETURN {
                    ic1: {
                        name: ic1.name,
                        element_id: elementId(ic1),
                        policy_count: count { ()-[:ISSUED_BY]->(ic1) }
                    },
                    ic2: {
                        name: ic2.name,
                        element_id: elementId(ic2),
                        policy_count: count { ()-[:ISSUED_BY]->(ic2) }
                    },
                    similarity_info: {
                        normalized_equal: normalized_equal,
                        edit_distance: edit_distance,
                        jaro_similarity: jaro_similarity,
                        is_substring: is_substring
                    }
                } as duplicate_pair
                ORDER BY duplicate_pair.similarity_info.normalized_equal DESC, 
                         duplicate_pair.similarity_info.jaro_similarity DESC
            """

            duplicates_result = self.execute_query(
                find_duplicates_query,
                {
                    "max_edit_distance": max_edit_distance,
                    "min_jaro_similarity": min_jaro_similarity,
                    "min_substring_length": min_substring_length,
                },
            )

            if not duplicates_result:
                logging.info(
                    "✅ Text similarity ile duplicate InsuranceCompany node'u bulunamadı"
                )
                return 0

            total_merged = 0
            processed_pairs = set()  # Aynı çiftin tekrar işlenmesini engellemek için

            logging.info(f"🎯 {len(duplicates_result)} duplicate company çift bulundu")

            for record in duplicates_result:
                duplicate_pair = record["duplicate_pair"]
                ic1 = duplicate_pair["ic1"]
                ic2 = duplicate_pair["ic2"]
                similarity_info = duplicate_pair["similarity_info"]

                # Bu çift daha önce işlendi mi kontrol et
                pair_key = tuple(sorted([ic1["element_id"], ic2["element_id"]]))
                if pair_key in processed_pairs:
                    continue

                processed_pairs.add(pair_key)

                # Master'ı seç (daha çok policy ile bağlantısı olan, eşitse daha uzun isimli)
                if ic1["policy_count"] > ic2["policy_count"]:
                    master, duplicate = ic1, ic2
                elif ic2["policy_count"] > ic1["policy_count"]:
                    master, duplicate = ic2, ic1
                else:
                    # Policy sayısı eşitse, daha uzun ve detaylı ismi olan master olsun
                    if len(ic1["name"]) >= len(ic2["name"]):
                        master, duplicate = ic1, ic2
                    else:
                        master, duplicate = ic2, ic1

                logging.info(f"🔧 Merge işlemi:")
                logging.info(
                    f"   Master: '{master['name']}' (Policy: {master['policy_count']})"
                )
                logging.info(
                    f"   Duplicate: '{duplicate['name']}' (Policy: {duplicate['policy_count']})"
                )
                logging.info(
                    f"   Similarity: Normalized={similarity_info['normalized_equal']}, "
                    f"Edit_dist={similarity_info['edit_distance']}, "
                    f"Jaro={similarity_info['jaro_similarity']:.3f}, "
                    f"Substring={similarity_info['is_substring']}"
                )

                # APOC ile merge et
                merge_query = """
                    MATCH (master) WHERE elementId(master) = $master_id
                    MATCH (duplicate) WHERE elementId(duplicate) = $duplicate_id
                    WITH [master, duplicate] as nodes
                    CALL apoc.refactor.mergeNodes(nodes, 
                        {properties:"discard", mergeRels:true, produceSelfRel:false, 
                         preserveExistingSelfRels:false, singleElementAsArray:true}) 
                    YIELD node
                    RETURN node.name as merged_name
                """

                result = self.execute_query(
                    merge_query,
                    {
                        "master_id": master["element_id"],
                        "duplicate_id": duplicate["element_id"],
                    },
                )

                if result:
                    total_merged += 1
                    logging.info(
                        f"  ✅ Merged: '{duplicate['name']}' -> '{master['name']}'"
                    )
                else:
                    logging.warning(f"  ⚠️ Merge işlemi başarısız: {duplicate['name']}")

            logging.info(
                f"🎉 Toplam {total_merged} duplicate InsuranceCompany node birleştirildi"
            )
            return total_merged

        except Exception as e:
            logging.error(f"❌ Duplicate insurance company merge hatası: {e}")
            return 0

    def merge_existing_duplicate_coverage_types(self):
        """
        Sistemde mevcut olan text similarity ve normalize edilmiş isme göre duplicate Coverage node'larını birleştirir

        Similarity kriterleri:
        1. Normalize edilmiş isimler tamamen eşit
        2. Text edit distance <= 3 (küçük yazım farkları)
        3. Bir isim diğerinin substring'i (contains ilişkisi)
        4. Jaro-Winkler similarity >= 0.85 (yakın benzerlik)
        """
        try:
            logging.info(
                "🔍 Mevcut duplicate Coverage node'ları text similarity ile kontrol ediliyor..."
            )

            # Text similarity parametreleri - coverage type için SIKI kriterler
            import os

            max_edit_distance = int(
                os.environ.get("COVERAGE_TYPE_EDIT_DISTANCE", "2")
            )  # Çok daha sıkı
            min_jaro_similarity = float(
                os.environ.get("COVERAGE_TYPE_JARO_SIMILARITY", "0.95")
            )  # Çok yüksek benzerlik
            min_substring_length = int(
                os.environ.get("COVERAGE_TYPE_MIN_SUBSTRING_LENGTH", "5")
            )  # Daha uzun substring
            min_common_words = int(
                os.environ.get("COVERAGE_TYPE_MIN_COMMON_WORDS", "3")
            )  # Minimum 3 ortak kelime

            logging.info(f"📊 Similarity parametreleri (SIKI):")
            logging.info(f"   - Max edit distance: {max_edit_distance} (SIKI)")
            logging.info(
                f"   - Min Jaro-Winkler similarity: {min_jaro_similarity} (ÇOK YÜKSEK)"
            )
            logging.info(f"   - Min substring length: {min_substring_length}")
            logging.info(f"   - Min common words: {min_common_words}")

            # Duplicate coverage type'ları text similarity ile bul
            find_duplicates_query = """
                MATCH (ct1:Coverage), (ct2:Coverage)
                WHERE elementId(ct1) < elementId(ct2)
                WITH ct1, ct2,
                     // Turkish character normalization için advanced cleaning
                     apoc.text.clean(replace(replace(replace(replace(replace(replace(
                         toLower(ct1.name), 'ı', 'i'), 'ğ', 'g'), 'ü', 'u'), 'ş', 's'), 'ö', 'o'), 'ç', 'c')) as clean1,
                     apoc.text.clean(replace(replace(replace(replace(replace(replace(
                         toLower(ct2.name), 'ı', 'i'), 'ğ', 'g'), 'ü', 'u'), 'ş', 's'), 'ö', 'o'), 'ç', 'c')) as clean2
                WHERE (
                    // 1. SADECE normalize edilmiş isimler tamamen eşit (en güvenli)
                    clean1 = clean2
                    OR
                    // 2. ÇOK SIKI edit distance + yüksek Jaro kombinasyonu
                    (
                        apoc.text.distance(clean1, clean2) <= $max_edit_distance
                        AND apoc.text.jaroWinklerDistance(clean1, clean2) >= $min_jaro_similarity
                        AND size(clean1) >= 4 AND size(clean2) >= 4  // Çok kısa isimler için koruma
                    )
                    OR
                    // 3. SADECE tam substring eşleşmesi (uzun isimler için)
                    (
                      size(clean1) >= $min_substring_length AND 
                      size(clean2) >= $min_substring_length AND
                      (
                        (clean2 CONTAINS clean1 AND size(clean1) >= 6) OR
                        (clean1 CONTAINS clean2 AND size(clean2) >= 6)
                      )
                      AND apoc.text.jaroWinklerDistance(clean1, clean2) >= 0.85  // Ek güvence
                    )
                    OR
                    // 4. ÇOK SIKI kelime bazlı benzerlik (aynı domain terimler)
                    (
                      size([word IN split(clean1, ' ') WHERE word IN split(clean2, ' ') AND size(word) >= 3 | word]) >= $min_common_words
                      AND abs(size(split(clean1, ' ')) - size(split(clean2, ' '))) <= 1  // Çok sıkı kelime sayısı farkı
                      AND apoc.text.jaroWinklerDistance(clean1, clean2) >= 0.80  // Ek güvence
                    )
                  )
                WITH ct1, ct2, clean1, clean2,
                     clean1 = clean2 as normalized_equal,
                     apoc.text.distance(clean1, clean2) as edit_distance,
                     apoc.text.jaroWinklerDistance(clean1, clean2) as jaro_similarity,
                     (clean2 CONTAINS clean1 OR clean1 CONTAINS clean2) as is_substring,
                     size([word IN split(clean1, ' ') WHERE word IN split(clean2, ' ') | word]) as common_words
                RETURN {
                    ct1: {
                        name: ct1.name,
                        element_id: elementId(ct1),
                        policy_count: count { (ct1)-[:APPLIED_TO]->() }
                    },
                    ct2: {
                        name: ct2.name,
                        element_id: elementId(ct2),
                        policy_count: count { (ct2)-[:APPLIED_TO]->() }
                    },
                    similarity_info: {
                        normalized_equal: normalized_equal,
                        edit_distance: edit_distance,
                        jaro_similarity: jaro_similarity,
                        is_substring: is_substring,
                        common_words: common_words
                    }
                } as duplicate_pair
                ORDER BY duplicate_pair.similarity_info.normalized_equal DESC, 
                         duplicate_pair.similarity_info.jaro_similarity DESC
            """

            duplicates_result = self.execute_query(
                find_duplicates_query,
                {
                    "max_edit_distance": max_edit_distance,
                    "min_jaro_similarity": min_jaro_similarity,
                    "min_substring_length": min_substring_length,
                    "min_common_words": min_common_words,
                },
            )

            if not duplicates_result:
                logging.info("✅ SIKI kriterlerle duplicate Coverage node'u bulunamadı")
                return 0

            total_merged = 0
            processed_pairs = set()  # Aynı çiftin tekrar işlenmesini engellemek için

            logging.info(f"🎯 {len(duplicates_result)} duplicate çift bulundu")

            for record in duplicates_result:
                duplicate_pair = record["duplicate_pair"]
                ct1 = duplicate_pair["ct1"]
                ct2 = duplicate_pair["ct2"]
                similarity_info = duplicate_pair["similarity_info"]

                # Bu çift daha önce işlendi mi kontrol et
                pair_key = tuple(sorted([ct1["element_id"], ct2["element_id"]]))
                if pair_key in processed_pairs:
                    continue

                processed_pairs.add(pair_key)

                # Master'ı seç (daha çok policy ile bağlantısı olan, eşitse daha uzun isimli)
                if ct1["policy_count"] > ct2["policy_count"]:
                    master, duplicate = ct1, ct2
                elif ct2["policy_count"] > ct1["policy_count"]:
                    master, duplicate = ct2, ct1
                else:
                    # Policy sayısı eşitse, daha uzun ve detaylı ismi olan master olsun
                    if len(ct1["name"]) >= len(ct2["name"]):
                        master, duplicate = ct1, ct2
                    else:
                        master, duplicate = ct2, ct1

                # Merge işlemi öncesi ek validasyon - şüpheli merge'leri engelle
                if (
                    similarity_info["jaro_similarity"] < 0.8
                    and not similarity_info["normalized_equal"]
                    and similarity_info["edit_distance"] > 2
                    and similarity_info["common_words"] < 2
                ):
                    logging.warning(
                        f"  ⚠️ Şüpheli Coverage merge - atlaniyor: '{duplicate['name']}' -> '{master['name']}'"
                    )
                    logging.warning(
                        f"     Jaro={similarity_info['jaro_similarity']:.3f}, Edit={similarity_info['edit_distance']}, Common={similarity_info['common_words']}"
                    )
                    continue

                logging.info(f"🔧 SIKI Kriterlerle Coverage Merge:")
                logging.info(
                    f"   Master: '{master['name']}' (Policy: {master['policy_count']})"
                )
                logging.info(
                    f"   Duplicate: '{duplicate['name']}' (Policy: {duplicate['policy_count']})"
                )
                logging.info(
                    f"   Similarity: Normalized={similarity_info['normalized_equal']}, "
                    f"Edit_dist={similarity_info['edit_distance']}, "
                    f"Jaro={similarity_info['jaro_similarity']:.3f}, "
                    f"Substring={similarity_info['is_substring']}, "
                    f"Common_words={similarity_info['common_words']}"
                )

                # APOC ile merge et
                merge_query = """
                    MATCH (master) WHERE elementId(master) = $master_id
                    MATCH (duplicate) WHERE elementId(duplicate) = $duplicate_id
                    WITH [master, duplicate] as nodes
                    CALL apoc.refactor.mergeNodes(nodes, 
                        {properties:"discard", mergeRels:true, produceSelfRel:false, 
                         preserveExistingSelfRels:false, singleElementAsArray:true}) 
                    YIELD node
                    RETURN node.name as merged_name
                """

                result = self.execute_query(
                    merge_query,
                    {
                        "master_id": master["element_id"],
                        "duplicate_id": duplicate["element_id"],
                    },
                )

                if result:
                    total_merged += 1
                    logging.info(
                        f"  ✅ Merged: '{duplicate['name']}' -> '{master['name']}'"
                    )
                else:
                    logging.warning(f"  ⚠️ Merge işlemi başarısız: {duplicate['name']}")

            logging.info(
                f"🎉 SIKI kriterlerle toplam {total_merged} duplicate Coverage node birleştirildi"
            )
            logging.info(f"   (Şüpheli merge'ler engellendi - daha güvenli sonuç)")
            return total_merged

        except Exception as e:
            logging.error(f"❌ Duplicate coverage type merge hatası: {e}")
            return 0

    def merge_duplicate_entities_selective(self, node_types: Optional[list] = None):
        """
        Seçilen node türlerine göre duplicate merge işlemi yapar.

        Args:
            node_types (list): Merge yapılacak node türleri
                             ['customers', 'insurance_companies', 'coverage_types'] veya 'all'

        Returns:
            dict: Her node türü için merge edilenerin sayısı
        """
        try:
            if not node_types:
                node_types = ["customers", "insurance_companies", "coverage_types"]
            elif node_types == ["all"] or "all" in node_types:
                node_types = ["customers", "insurance_companies", "coverage_types"]

            results = {}
            total_merged = 0

            logging.info(f"🔄 Selective duplicate merge başlatılıyor: {node_types}")

            if "customers" in node_types:
                logging.info("🔍 Customer duplicate merge işlemi...")
                customers_merged = self.merge_existing_duplicate_customers()
                results["customers"] = customers_merged
                total_merged += customers_merged
                logging.info(f"✅ {customers_merged} customer merge edildi")

            if "insurance_companies" in node_types:
                logging.info("🔍 Insurance Company duplicate merge işlemi...")
                companies_merged = self.merge_existing_duplicate_insurance_companies()
                results["insurance_companies"] = companies_merged
                total_merged += companies_merged
                logging.info(f"✅ {companies_merged} insurance company merge edildi")

            if "coverage_types" in node_types:
                logging.info("🔍 Coverage Type duplicate merge işlemi...")
                coverage_merged = self.merge_existing_duplicate_coverage_types()
                results["coverage_types"] = coverage_merged
                total_merged += coverage_merged
                logging.info(f"✅ {coverage_merged} coverage type merge edildi")

            results["total_merged"] = total_merged
            logging.info(
                f"🎉 Selective merge tamamlandı. Toplam: {total_merged} node merge edildi"
            )

            return results

        except Exception as e:
            logging.error(f"❌ Selective duplicate merge hatası: {e}")
            return {"error": str(e), "total_merged": 0}

    def create_comprehensive_policy_entities(
        self, entities_data: dict, file_name: str, model: str = "openai_gpt_4o_mini"
    ):
        """
        Çıkarılan varlık bilgilerinden Neo4j'de node ve ilişkiler oluşturur.

        Args:
            entities_data: extract_comprehensive_policy_entities_with_llm'den dönen dictionary
            file_name: Belge adı
            model: LLM modeli (relationship normalization için)
        """
        try:
            if not entities_data:
                logging.warning(f"Boş varlık verisi: {file_name}")
                return

            # Policy ID'yi LLM'den gelen customer name ile oluştur (eğer var ise)
            customer_data = entities_data.get("customer")
            # customer None olabilir, bu durumda boş dict kullan
            if customer_data is None:
                customer_data = {}
            customer_name = (
                customer_data.get("name", "").strip() if customer_data else ""
            )

            if customer_name:
                # Customer name'den safe ID oluştur (filename bilgisi eklenmez)
                safe_customer_name = normalize_file_name(customer_name)
                # Policy verilerinden daha spesifik ID oluştur
                policy_data = entities_data.get("policy")
                if policy_data is None:
                    policy_data = {}
                policy_type = policy_data.get("policyType", "") if policy_data else ""
                year = policy_data.get("year", "") if policy_data else ""

                if policy_type and year:
                    policy_id = f"policy_{safe_customer_name}_{normalize_file_name(policy_type)}_{year}".replace(
                        ".", "_"
                    )
                else:
                    # Sadece customer name ile unique ID oluştur
                    import time

                    timestamp = str(int(time.time()))[-6:]  # Son 6 hanesi
                    policy_id = f"policy_{safe_customer_name}_{timestamp}".replace(
                        ".", "_"
                    )
            else:
                # Fallback: filename'den oluştur
                policy_id = f"policy_{normalize_file_name(file_name).replace('.', '_')}"

            policy_data = entities_data.get("policy")
            if policy_data is None:
                policy_data = {}
            if not policy_data.get("policyNumber"):
                policy_data["policyNumber"] = policy_id

            # 1. Policy Node'u oluştur
            self._create_policy_node_comprehensive(policy_id, policy_data, file_name)

            # 2. Customer Node'u ve ilişkisini oluştur (önce HAS_POLICY yaratılmalı)
            customer_data = entities_data.get("customer")
            # customer None olabilir, bu durumda boş dict kullan
            if customer_data is None:
                customer_data = {}
            if customer_data.get("name"):
                self._create_customer_node_comprehensive(
                    customer_data, policy_id, file_name
                )

            # 2.5. Policy İlişki Türünü Oluştur (LLM'den gelen ilişki tipi ile - Customer bağlandıktan sonra)
            policy_relationship = entities_data.get("policy_relationship")
            if policy_relationship is None:
                policy_relationship = {}
            relationship_type = (
                policy_relationship.get("relationship_type", "")
                if policy_relationship
                else ""
            )
            relationship_properties = (
                policy_relationship.get("properties", {}) if policy_relationship else {}
            )
            deductible_info = entities_data.get("deductible_info")
            if deductible_info is None:
                deductible_info = {}
            policy_type = policy_data.get("type", "") if policy_data else ""
            if relationship_type:
                # OCR hatalarından kaynaklanan relationship type'ları LLM ile normalize et
                try:
                    schema_data = (
                        self._get_existing_policy_relationship_types_from_schema()
                    )
                    existing_types = [
                        rt["type"] for rt in schema_data.get("relationship_types", [])
                    ]

                    if existing_types:
                        normalization_result = (
                            self._normalize_relationship_type_with_llm(
                                new_relationship_type=relationship_type,
                                existing_relationship_types=existing_types,
                                policy_type=policy_type,
                            )
                        )
                        normalized_relationship_type = normalization_result.get(
                            "normalized_type", relationship_type
                        )

                        if normalized_relationship_type != relationship_type:
                            logging.info(
                                f"🔄 LLM Relationship normalization: {relationship_type} → {normalized_relationship_type} "
                                f"(Reason: {normalization_result.get('reason', 'N/A')})"
                            )
                        relationship_type = normalized_relationship_type
                except Exception as norm_error:
                    logging.warning(
                        f"⚠️ Relationship normalization atlandı: {norm_error}"
                    )

                self._create_policy_type_relationship(
                    policy_id,
                    relationship_type,
                    policy_type,
                    relationship_properties,
                    deductible_info,
                )

            # 3. InsuranceCompany Node'u ve ilişkisini oluştur
            company_data = entities_data.get("insurance_company")
            if company_data is None:
                company_data = {}
            if company_data.get("name"):
                self._create_insurance_company_node(company_data, policy_id)

            # 4. Date Node'larını (start/end) oluştur
            dates_data = entities_data.get("dates")
            if dates_data is None:
                dates_data = {}
            if dates_data:
                self._create_date_nodes_for_policy(dates_data, policy_id)

            # 5. Premium Node'u oluştur
            premium_data = entities_data.get("premium")
            if premium_data is None:
                premium_data = {}
            if premium_data.get("amount") is not None:
                self._create_premium_node(premium_data, policy_id)

            # 6. Coverage Node'u oluştur
            coverage_data = entities_data.get("coverage")
            if coverage_data is None:
                coverage_data = {}
            if coverage_data:
                self._create_coverage_limit_node(coverage_data, policy_id)

            # 7. Coverage Node'larını oluştur
            coverage_types = entities_data.get("coverage_types", [])
            if coverage_types:
                self._create_coverage_nodes(coverage_types, policy_id)

            # 8. Guarantee Node'larını oluştur
            guarantees = entities_data.get("guarantees", [])
            if guarantees:
                self._create_guarantee_nodes(guarantees, policy_id)

            # 9. Clause Node'larını oluştur
            clauses = entities_data.get("clauses", [])
            if clauses:
                self._create_clause_nodes(clauses, policy_id)

            # 10. Endorsement Node'larını oluştur
            endorsements = entities_data.get("endorsements", [])
            if endorsements:
                self._create_endorsement_nodes(endorsements, policy_id)

            # 11. Payment Node'u oluştur
            payment_data = entities_data.get("payment")
            if payment_data is None:
                payment_data = {}
            if payment_data.get("amount") is not None:
                self._create_payment_node(payment_data, policy_id)

            # 12. Address Node'u oluştur
            address_data = entities_data.get("address")
            if address_data is None:
                address_data = {}
            if address_data.get("address") or address_data.get("city"):
                self._create_risk_address_node(address_data, policy_id)

            # 13. InsuredProperty Node'u oluştur (yeni alan)
            insured_property_data = entities_data.get("insured_property")
            if isinstance(insured_property_data, list):
                if len(insured_property_data) > 0 and isinstance(
                    insured_property_data[0], dict
                ):
                    insured_property_data = insured_property_data[0]
                else:
                    insured_property_data = {}
            elif insured_property_data is None:
                insured_property_data = {}
            if (
                insured_property_data.get("address")
                or insured_property_data.get("city")
                or insured_property_data.get("damageStatus")
            ):
                self._create_insured_property_node(insured_property_data, policy_id)

            # 14. InsuredPerson Node'u oluştur (yeni alan)
            insured_person_data = entities_data.get("insured_person")
            if isinstance(insured_person_data, list):
                if len(insured_person_data) > 0 and isinstance(
                    insured_person_data[0], dict
                ):
                    insured_person_data = insured_person_data[0]
                else:
                    insured_person_data = {}
            elif insured_person_data is None:
                insured_person_data = {}
            if insured_person_data.get("name"):
                self._create_insured_person_node(insured_person_data, policy_id)

            # 15. Policyholder Node'u oluştur (yeni alan)
            policyholder_data = entities_data.get("policyholder")
            if isinstance(policyholder_data, list):
                if len(policyholder_data) > 0 and isinstance(
                    policyholder_data[0], dict
                ):
                    policyholder_data = policyholder_data[0]
                else:
                    policyholder_data = {}
            elif policyholder_data is None:
                policyholder_data = {}
            if policyholder_data.get("name"):
                self._create_policyholder_node(policyholder_data, policy_id)

            # 16. InsuranceAmount bilgilerini Coverage ve Premium'a aktar (yeni alan)
            insurance_amount_data = entities_data.get("insurance_amount")
            if isinstance(insurance_amount_data, list):
                if len(insurance_amount_data) > 0 and isinstance(
                    insurance_amount_data[0], dict
                ):
                    insurance_amount_data = insurance_amount_data[0]
                else:
                    insurance_amount_data = {}
            elif insurance_amount_data is None:
                insurance_amount_data = {}
            if insurance_amount_data:
                # Coverage ve Premium node'larına insurance_amount bilgilerini ekle
                self._update_coverage_premium_from_insurance_amount(
                    insurance_amount_data, policy_id
                )

            # 17. Chunk -> Entity ilişkileri
            # NOT: EXTRACTED_FROM ilişkileri make_relationships.py'deki
            # merge_relationship_between_chunk_and_entites fonksiyonunda oluşturuluyor.
            # O fonksiyon her chunk işlenirken entity'yi o chunk'a bağlıyor (Entity -> Chunk).
            # _create_chunk_entity_relationships yanlış bir şekilde TÜM chunk'ları TÜM entity'lere
            # bağlıyordu, bu nedenle devre dışı bırakıldı.
            # self._create_chunk_entity_relationships(file_name, policy_id, entities_data)

            logging.info(f"✅ Tüm varlık node'ları başarıyla oluşturuldu: {file_name}")

        except Exception as e:
            logging.error(f"Varlık node'ları oluşturma hatası: {e}")

    def _extract_entities_generic(
        self,
        file_name: str,
        document_content: str,
        model: str = "openai_gpt_4o_mini"
    ) -> dict:
        """
        Generic entity extraction - LLM'den nodes/relationships formatında JSON alır.
        
        Bu metod domain-agnostic olup, LLM'in ürettiği node tiplerini ve
        ilişkileri olduğu gibi Neo4j'ye yazar.
        
        Args:
            file_name: Belge adı
            document_content: Belgenin metin içeriği
            model: LLM modeli
        
        Returns:
            {
                "document_type": "GENEL_KURUL",
                "nodes": [...],
                "relationships": [...]
            }
        """
        logging.info(f"🤖 Generic Entity Extraction başlatılıyor - Model: {model}, Dosya: {file_name}")
        
        try:
            # Domain-specific prompt yükle
            domain = get_domain()
            prompt_template = load_prompt("entity_extraction", domain)
            prompt = prompt_template.format(
                file_name=file_name,
                document_content=document_content
            )
            logging.info(f"📋 Generic extraction prompt loaded for domain: {domain}")
            
            # Gemini ile çağır
            response_text = ""
            use_gemini = False
            
            try:
                from google import genai as genai_sdk
                from google.genai import types
                
                api_key = os.environ.get("GEMINI_API_KEY")
                if api_key:
                    client = genai_sdk.Client(api_key=api_key)
                    
                    generation_config = types.GenerateContentConfig(
                        max_output_tokens=8192,
                        temperature=0.1,
                    )
                    
                    response = client.models.generate_content(
                        model="models/gemini-2.0-flash",
                        contents=[types.Part.from_text(text=prompt)],
                        config=generation_config,
                    )
                    
                    response_text = response.text.strip() if response.text else ""
                    use_gemini = True
                    logging.info(f"✅ Gemini response received: {len(response_text)} chars")
                    
            except Exception as e:
                logging.warning(f"⚠️ Gemini failed, falling back to LLM: {e}")
            
            # Gemini kullanılamadıysa alternatif LLM kullan
            if not use_gemini or not response_text:
                from src.llm import get_llm
                llm, _ = get_llm(model)
                response = llm.invoke(prompt)
                response_text = response.content.strip() if hasattr(response, "content") and response.content else ""
            
            # JSON parse
            if not response_text:
                logging.error("❌ Empty response from LLM")
                return {}
            
            # Extract JSON from response
            if "{" in response_text and "}" in response_text:
                start_idx = response_text.find("{")
                end_idx = response_text.rfind("}") + 1
                json_text = response_text[start_idx:end_idx]
                
                # Clean markdown code blocks if present
                if json_text.startswith("```json"):
                    json_text = json_text.replace("```json", "").replace("```", "").strip()
                elif json_text.startswith("```"):
                    json_text = json_text.replace("```", "").strip()
                
                try:
                    result = json.loads(json_text)
                    
                    # Validate structure
                    if "nodes" not in result:
                        result["nodes"] = []
                    if "relationships" not in result:
                        result["relationships"] = []
                    if "document_type" not in result:
                        result["document_type"] = "DIGER"
                    
                    logging.info(
                        f"✅ Generic extraction parsed: "
                        f"{len(result['nodes'])} nodes, "
                        f"{len(result['relationships'])} relationships"
                    )
                    
                    return result
                    
                except json.JSONDecodeError as e:
                    logging.error(f"❌ JSON parse error: {e}")
                    logging.debug(f"Response text: {json_text[:500]}...")
                    return {}
            else:
                logging.error("❌ No JSON found in response")
                return {}
                
        except Exception as e:
            logging.error(f"❌ Generic entity extraction error: {e}")
            return {}

    def _get_existing_policy_relationship_types_from_schema(self) -> dict:
        """
        Veritabanı şemasından mevcut IS_*_POLICY relationship type'larını çeker.

        Returns:
            {
                "relationship_types": [
                    {
                        "type": "IS_DASK_POLICY",
                        "count": 150,
                        "sample_usage": "Customer -> Policy"
                    }
                ],
                "total_types": 10
            }
        """
        try:
            query = """
            // Tüm IS_*_POLICY relationship type'larını bul
            CALL db.relationshipTypes() YIELD relationshipType
            WHERE relationshipType STARTS WITH 'IS_' 
              AND relationshipType ENDS WITH '_POLICY'
            
            WITH relationshipType as rel_type
            
            // Her relationship type için kullanım sayısı
            OPTIONAL MATCH (c:Customer)-[r]->(p:Policy)
            WHERE type(r) = rel_type
            WITH rel_type, count(r) as usage_count
            
            RETURN rel_type as type,
                   usage_count as count
            ORDER BY usage_count DESC
            """

            result = self.execute_query(query, {})

            relationship_types = []
            for row in result:
                relationship_types.append(
                    {
                        "type": row["type"],
                        "count": row.get("count", 0),
                        "sample_usage": "Customer -> Policy",
                    }
                )

            return {
                "relationship_types": relationship_types,
                "total_types": len(relationship_types),
            }

        except Exception as e:
            logging.error(f"❌ Schema'dan relationship type'ları çekme hatası: {e}")
            return {"relationship_types": [], "total_types": 0}

    def _get_cached_schema_relationship_types(self) -> dict:
        """
        Schema relationship type'larını cache'den al veya çek.
        """
        current_time = time.time()

        # Cache kontrolü
        if (
            graphDBdataAccess._schema_cache is not None
            and graphDBdataAccess._schema_cache_timestamp is not None
            and (current_time - graphDBdataAccess._schema_cache_timestamp)
            < graphDBdataAccess.SCHEMA_CACHE_TTL
        ):
            logging.debug("📦 Schema cache'den alındı")
            return graphDBdataAccess._schema_cache

        # Cache yoksa veya expire olmuşsa çek
        logging.info(
            "🔄 Schema'dan relationship type'ları çekiliyor (cache yok/expire)..."
        )
        schema_data = self._get_existing_policy_relationship_types_from_schema()

        # Cache'e kaydet
        graphDBdataAccess._schema_cache = schema_data
        graphDBdataAccess._schema_cache_timestamp = current_time

        return schema_data

    def _normalize_relationship_type_with_llm(
        self,
        new_relationship_type: str,
        existing_relationship_types: list,
        policy_type: str = "",
        model: str = "openai_gpt_4o_mini",
    ) -> dict:
        """
        LLM'e yeni relationship type'ı gönderir, mevcut olanlardan en uygun olanı seçtirir.

        Args:
            new_relationship_type: Gemini'den gelen yeni relationship type (örn: "IS_DASK_KONUT_POLICY")
            existing_relationship_types: Şemadan çekilen mevcut relationship type'ları
            policy_type: Poliçe türü (opsiyonel, context için)
            model: Kullanılacak LLM modeli

        Returns:
            {
                "normalized_type": "IS_DASK_POLICY",
                "matched_existing": true,
                "reason": "DASK ana kategori, KONUT alt kategori. Mevcut IS_DASK_POLICY kullanılmalı",
                "confidence": 0.95
            }
        """
        try:
            # Mevcut relationship type'ları formatla
            # existing_relationship_types hem string listesi hem dict listesi olabilir
            if existing_relationship_types:
                if isinstance(existing_relationship_types[0], dict):
                    # Dict listesi: [{"type": "...", "count": N}, ...]
                    existing_list = "\n".join(
                        [
                            f"- {rt['type']} (kullanım: {rt.get('count', 'N/A')} kez)"
                            for rt in existing_relationship_types
                        ]
                    )
                else:
                    # String listesi: ["IS_KASKO_POLICY", ...]
                    existing_list = "\n".join(
                        [f"- {rt}" for rt in existing_relationship_types]
                    )
            else:
                existing_list = "Henüz hiç relationship type yok (ilk oluşturma)"

            prompt = f"""
Sen bir veritabanı uzmanısın. Yeni bir relationship type normalize edeceksin.

YENİ RELATIONSHIP TYPE (Gemini'den geldi):
"{new_relationship_type}"

POLİÇE TÜRÜ (context):
"{policy_type}"

VERİTABANINDA MEVCUT RELATIONSHIP TYPE'LAR:
{existing_list}

GÖREVİN:
1. Yeni relationship type'ı mevcut olanlarla karşılaştır
2. Eğer semantic olarak aynı anlama geliyorsa, mevcut olanlardan EN UYGUN OLANI seç
3. Eğer mevcut olanlardan hiçbiri uygun değilse, yeni bir STANDART format öner

ÖNEMLİ KURALLAR:
- "IS_DASK_KONUT_POLICY" → Mevcut "IS_DASK_POLICY" varsa onu kullan (DASK ana kategori)
- "IS_KONUT_SIGORTASI_POLICY" → Mevcut "IS_KONUT_POLICY" varsa onu kullan
- Format: "IS_[ANA_KATEGORI]_POLICY" şeklinde olmalı
- Alt kategoriler (KONUT, TICARI, vb.) relationship type'a eklenmemeli
- Mevcut olanları tercih et (duplicate oluşturma!)

ÇIKTI FORMATI (SADECE JSON, başka açıklama ekleme):
{{
    "normalized_type": "IS_DASK_POLICY",
    "matched_existing": true,
    "reason": "DASK ana kategori, KONUT alt kategori. Mevcut IS_DASK_POLICY kullanılmalı",
    "confidence": 0.95
}}

KRİTİK: 
- Eğer mevcut relationship type'lar varsa, MUTLAKA onlardan birini seç!
- Yeni relationship type oluşturma, sadece hiç uygun olan yoksa!
- Sadece JSON döndür, markdown code block kullanma!
"""

            # Gemini veya diğer LLM kullan
            use_gemini = False
            try:
                from google import genai as genai_sdk

                api_key = os.environ.get("GEMINI_API_KEY")
                if api_key:
                    use_gemini = True
                    client = genai_sdk.Client(api_key=api_key)
                    from google.genai import types

                    response = client.models.generate_content(
                        model="models/gemini-2.0-flash",
                        contents=[types.Part.from_text(text=prompt)],
                    )
                    response_text = response.text.strip() if response.text else ""
            except Exception:
                use_gemini = False

            if not use_gemini:
                # Fallback: Diğer LLM
                from src.llm import get_llm

                llm, _ = get_llm(model)
                response = llm.invoke(prompt)  # type: ignore[union-attr]
                response_text = response.content.strip() if hasattr(response, "content") and response.content else ""  # type: ignore[union-attr]

            # JSON parse et
            try:
                # Markdown code block varsa temizle
                if "```json" in response_text:
                    response_text = (
                        response_text.split("```json")[1].split("```")[0].strip()
                    )
                elif "```" in response_text:
                    response_text = (
                        response_text.split("```")[1].split("```")[0].strip()
                    )

                # JSON bul
                if "{" in response_text and "}" in response_text:
                    start_idx = response_text.find("{")
                    end_idx = response_text.rfind("}") + 1
                    json_text = response_text[start_idx:end_idx]
                    normalization_result = json.loads(json_text)

                    normalized_type = normalization_result.get(
                        "normalized_type", new_relationship_type
                    )
                    matched_existing = normalization_result.get(
                        "matched_existing", False
                    )
                    reason = normalization_result.get("reason", "")
                    confidence = normalization_result.get("confidence", 0.5)

                    if normalized_type != new_relationship_type:
                        logging.info(
                            f"🔄 Relationship type normalize edildi: "
                            f"{new_relationship_type} → {normalized_type} "
                            f"(Reason: {reason}, Confidence: {confidence:.2f})"
                        )

                    return {
                        "normalized_type": normalized_type,
                        "matched_existing": matched_existing,
                        "reason": reason,
                        "confidence": confidence,
                    }
                else:
                    logging.warning(
                        "LLM yanıtında JSON bulunamadı, orijinal type kullanılıyor"
                    )
                    return {
                        "normalized_type": new_relationship_type,
                        "matched_existing": False,
                        "reason": "LLM JSON döndüremedi",
                        "confidence": 0.0,
                    }

            except json.JSONDecodeError as e:
                logging.error(f"LLM yanıtı JSON parse edilemedi: {e}")
                logging.error(f"Response (first 500 chars): {response_text[:500]}")
                return {
                    "normalized_type": new_relationship_type,
                    "matched_existing": False,
                    "reason": f"JSON parse hatası: {e}",
                    "confidence": 0.0,
                }

        except Exception as e:
            logging.error(f"❌ LLM normalization hatası: {e}")
            return {
                "normalized_type": new_relationship_type,
                "matched_existing": False,
                "reason": f"LLM hatası: {e}",
                "confidence": 0.0,
            }

    def create_endorsement_entity(
        self, entities_data: dict, file_name: str, document_type: str = "ENDORSEMENT"
    ):
        """
        Zeyilname/İptal/Yenileme belgesinden Endorsement node'u ve ilgili entity'leri oluşturur.

        Ana poliçeyi bularak zeyilname zincirinin sonuna ekler.
        Policy node gibi aynı entity'leri çıkartır (Premium, Coverage, Clause, vb.)

        Args:
            entities_data: LLM'den çıkartılan varlık bilgileri
            file_name: Belge adı
            document_type: ENDORSEMENT, CANCELLATION, RENEWAL
        """
        try:
            if not entities_data:
                logging.warning(f"Boş endorsement verisi: {file_name}")
                return

            # Endorsement node'u oluştur
            policy_data = entities_data.get("policy", {})
            endorsement_id = (
                f"endorsement_{normalize_file_name(file_name).replace('.', '_')}"
            )

            # Endorsement ismini çıkart (document_type'tan)
            endorsement_name_map = {
                "ENDORSEMENT": "Zeyilname",
                "CANCELLATION": "İptal Zeyilnesi",
                "RENEWAL": "Yenileme",
            }
            endorsement_name = endorsement_name_map.get(document_type, "Zeyilname")

            # 1. Endorsement Node'u oluştur
            self._create_endorsement_node(
                endorsement_id, endorsement_name, document_type, policy_data, file_name
            )

            # 2. Premium Node'u oluştur
            premium_data = entities_data.get("premium", {})
            if premium_data.get("amount") is not None:
                self._create_premium_node(premium_data, endorsement_id)

            # 3. Coverage Node'u oluştur
            coverage_data = entities_data.get("coverage", {})
            if coverage_data:
                self._create_coverage_limit_node(coverage_data, endorsement_id)

            # 4. Coverage Node'larını oluştur
            coverage_types = entities_data.get("coverage_types", [])
            if coverage_types:
                self._create_coverage_nodes_for_endorsement(
                    coverage_types, endorsement_id
                )

            # 5. Guarantee Node'larını oluştur
            guarantees = entities_data.get("guarantees", [])
            if guarantees:
                self._create_guarantee_nodes(guarantees, endorsement_id)

            # 6. Clause Node'larını oluştur
            clauses = entities_data.get("clauses", [])
            if clauses:
                self._create_clause_nodes(clauses, endorsement_id)

            # 7. Date Node'larını oluştur (zeyilname tarihleri)
            dates_data = entities_data.get("dates", {})
            if dates_data:
                self._create_date_nodes_for_endorsement(dates_data, endorsement_id)

            # 8. Payment Node'u oluştur
            payment_data = entities_data.get("payment", {})
            if payment_data.get("amount") is not None:
                self._create_payment_node(payment_data, endorsement_id)

            # 9. Address Node'u oluştur
            address_data = entities_data.get("address", {})
            if address_data.get("address") or address_data.get("city"):
                self._create_risk_address_node(address_data, endorsement_id)

            # 10. InsuredProperty Node'u oluştur (yeni alan)
            insured_property_data = entities_data.get("insured_property", {})
            if (
                insured_property_data.get("address")
                or insured_property_data.get("city")
                or insured_property_data.get("damageStatus")
            ):
                self._create_insured_property_node(
                    insured_property_data, endorsement_id
                )

            # 11. InsuredPerson Node'u oluştur (yeni alan)
            insured_person_data = entities_data.get("insured_person", {})
            if insured_person_data.get("name"):
                self._create_insured_person_node(insured_person_data, endorsement_id)

            # 12. Policyholder Node'u oluştur (yeni alan)
            policyholder_data = entities_data.get("policyholder", {})
            if policyholder_data.get("name"):
                self._create_policyholder_node(policyholder_data, endorsement_id)

            # 13. InsuranceAmount bilgilerini Coverage ve Premium'a aktar (yeni alan)
            insurance_amount_data = entities_data.get("insurance_amount", {})
            if insurance_amount_data:
                # Coverage ve Premium node'larına insurance_amount bilgilerini ekle
                self._update_coverage_premium_from_insurance_amount(
                    insurance_amount_data, endorsement_id
                )

            # 10. Ana poliçeye bağla (3-aşamalı strateji ile: policy_number → renewal_number → customer+policy_type)

            # Debug: LLM'den gelen policy_data'yı logla
            logging.debug(f"🔍 LLM Policy Data: {policy_data}")
            logging.debug(f"🔍 Full entities_data keys: {list(entities_data.keys())}")

            policy_info = {
                "policy_number": policy_data.get("policyNumber", ""),
                "renewal_number": policy_data.get("renewalNumber", ""),
                "customer_name": policy_data.get("customer_name", ""),
                "policy_type": policy_data.get("policy_type", ""),
                "year": policy_data.get("year", ""),
            }

            # Eğer customer_name veya policy_type boşsa, file name'den parse etmeyi dene
            if not policy_info["customer_name"] or not policy_info["policy_type"]:
                logging.info(
                    f"🔄 LLM'den eksik bilgi, file name'den parse edilecek: {file_name}"
                )
                parsed_info = self._parse_info_from_filename(file_name)

                if not policy_info["customer_name"] and parsed_info.get(
                    "customer_name"
                ):
                    policy_info["customer_name"] = parsed_info["customer_name"]
                    logging.info(
                        f"📝 Customer name file name'den alındı: {parsed_info['customer_name']}"
                    )

                if not policy_info["policy_type"] and parsed_info.get("policy_type"):
                    policy_info["policy_type"] = parsed_info["policy_type"]
                    logging.info(
                        f"📝 Policy type file name'den alındı: {parsed_info['policy_type']}"
                    )

            logging.debug(f"🔍 Final policy_info: {policy_info}")

            # Gelişmiş 3-aşamalı policy eşleştirmeyi kullan
            match_result = self._link_endorsement_to_main_policy(file_name, policy_info)
            # match_result can be dict or False (on exception)
            if isinstance(match_result, dict) and match_result.get("success", False):
                logging.info(f"✅ Ana poliçe eşleştirmesi başarılı: {file_name}")

                # Kronolojik zinciri kur (FIRST_ENDORSEMENT/NEXT_ENDORSEMENT) - bulunan policy bilgilerini kullan
                found_policy_number = match_result.get("policy_number", "")
                found_policy_id = match_result.get("policy_id", "")

                if found_policy_id and isinstance(found_policy_id, str):
                    logging.info(
                        f"🔗 Kronolojik endorsement zinciri kuruluyor: {endorsement_id} -> Policy ID: {found_policy_id}"
                    )
                    self._link_endorsement_to_policy_chain(
                        endorsement_id, found_policy_id, file_name
                    )
                    logging.info(
                        f"✅ Endorsement kronolojik zincire eklendi: {file_name}"
                    )
                else:
                    logging.warning(
                        f"⚠️ Bulunan policy ID eksik, kronolojik zincir kurulamadı: {file_name}"
                    )
                    logging.warning(f"   Found policy_id: {found_policy_id}")
            else:
                logging.warning(
                    f"⚠️ Ana poliçe bulunamadı, endorsement bağımsız kalacak: {file_name}"
                )

            logging.info(f"✅ Endorsement entity başarıyla oluşturuldu: {file_name}")

        except Exception as e:
            logging.error(f"Endorsement entity oluşturma hatası: {e}")

    def _parse_info_from_filename(self, file_name: str) -> dict:
        """
        File name'den customer_name, policy_type, year gibi bilgileri parse eder
        Örnek: "Ayça Dinçkök 34EDG047 Kasko Aksesuar Zeyli_2021.pdf"
        """
        try:
            # .pdf uzantısını kaldır
            name_without_ext = file_name.replace(".pdf", "").replace(".PDF", "")

            # Policy type keywords
            policy_type_keywords = {
                "kasko": "Kasko Sigortası",
                "trafik": "Trafik Sigortası",
                "dask": "DASK",
                "konut": "Konut Sigortası",
                "seyahat": "Seyahat Sigortası",
                "saglik": "Sağlık Sigortası",
                "sağlık": "Sağlık Sigortası",
                "hayat": "Hayat Sigortası",
            }

            parsed_info = {}

            # Policy type'ı tespit et
            name_lower = name_without_ext.lower()
            for keyword, full_name in policy_type_keywords.items():
                if keyword in name_lower:
                    parsed_info["policy_type"] = full_name
                    break

            # Year'ı tespit et (4 basamaklı sayı)
            import re

            year_match = re.search(r"[_\s](\d{4})", name_without_ext)
            if year_match:
                parsed_info["year"] = year_match.group(1)

            # Customer name'i tespit et (ilk kelimeler genellikle isim)
            # Format genellikle: "İsim Soyisim [PolicyNumber] [PolicyType] [Zeyilname/Zeyli] [Year]"
            parts = name_without_ext.split()
            if len(parts) >= 2:
                # İlk 2 kelime genellikle isim soyisim
                potential_customer = f"{parts[0]} {parts[1]}"
                # Eğer policy number gibi görünmüyorsa customer name olarak al
                if not re.match(r"^\d+[A-Z]*\d*$", potential_customer):
                    parsed_info["customer_name"] = potential_customer

            logging.debug(f"🔍 File name parsing result: {parsed_info}")
            return parsed_info

        except Exception as e:
            logging.warning(f"⚠️ File name parsing hatası ({file_name}): {e}")
            return {}

    def _create_endorsement_node(
        self,
        endorsement_id: str,
        endorsement_name: str,
        document_type: str,
        policy_data: dict,
        file_name: str,
    ):
        """Endorsement node'u oluşturur ve Document'a bağlar"""
        try:
            dates_data = policy_data.get("dates", {})
            effective_date = (
                dates_data.get("start_date", "") if isinstance(dates_data, dict) else ""
            )

            query = """
                MERGE (e:Endorsement {id: $endorsement_id})
                ON CREATE SET 
                    e.name = $name,
                    e.document_type = $document_type,
                    e.endorsement_type = $document_type,
                    e.effective_date = $effective_date,
                    e.extraction_method = 'LLM_comprehensive',
                    e.createdAt = datetime()
                ON MATCH SET 
                    e.updatedAt = datetime()
                WITH e
                MATCH (d:Document {fileName: $file_name})
                MERGE (e)-[r:DOCUMENTED_IN]->(d)
                SET r.created_at = datetime(),
                    r.source = 'llm_extraction'
                RETURN e.id as endorsement_id
            """

            self.graph.query(
                query,
                {
                    "endorsement_id": endorsement_id,
                    "name": endorsement_name,
                    "document_type": document_type,
                    "effective_date": effective_date,
                    "file_name": file_name,
                },
                session_params={"database": self.graph._database},
            )

            logging.info(f"✅ Endorsement node oluşturuldu: {endorsement_id}")

        except Exception as e:
            logging.error(f"Endorsement node oluşturma hatası: {e}")

    def _get_endorsement_start_date(self, endorsement_id: str) -> str:
        """
        Endorsement'ın HAS_START_DATE ile bağlı Date node'unun değerini alır
        """
        try:
            query = """
                MATCH (e:Endorsement {id: $endorsement_id})-[:HAS_START_DATE]->(d:Date)
                RETURN d.value as date_value
            """

            result = self.graph.query(
                query,
                {"endorsement_id": endorsement_id},
                session_params={"database": self.graph._database},
            )

            if result:
                return result[0]["date_value"]
            else:
                logging.warning(
                    f"⚠️ Endorsement için start_date bulunamadı: {endorsement_id}"
                )
                return ""

        except Exception as e:
            logging.error(f"Endorsement date alma hatası: {e}")
            return ""

    def _get_policy_endorsements_with_dates(self, policy_id: str) -> list:
        """
        Policy'nin tüm endorsement'larını tarihleriyle birlikte döndürür (sıralı)
        """
        try:
            query = """
                MATCH (p:Policy {id: $policy_id})-[:FIRST_ENDORSEMENT*0..]->(e:Endorsement)
                OPTIONAL MATCH (e)-[:HAS_START_DATE]->(d:Date)
                RETURN e.id as endorsement_id, 
                       d.value as start_date,
                       d.year as year,
                       d.month as month
                ORDER BY CASE 
                    WHEN d.value IS NOT NULL THEN d.value 
                    ELSE '9999-12-31' 
                END ASC
            """

            result = self.graph.query(
                query,
                {"policy_id": policy_id},
                session_params={"database": self.graph._database},
            )

            return result if result else []

        except Exception as e:
            logging.error(f"Policy endorsement'ları alma hatası: {e}")
            return []

    def _clear_endorsement_chain(self, policy_id: str):
        """
        Mevcut tüm FIRST_ENDORSEMENT ve NEXT_ENDORSEMENT ilişkilerini temizler
        """
        try:
            clear_query = """
                MATCH (p:Policy {id: $policy_id})-[r:FIRST_ENDORSEMENT|NEXT_ENDORSEMENT]-()
                DELETE r
            """

            self.graph.query(
                clear_query,
                {"policy_id": policy_id},
                session_params={"database": self.graph._database},
            )

            logging.info(f"🧹 Policy endorsement zinciri temizlendi: {policy_id}")

        except Exception as e:
            logging.error(f"Endorsement zinciri temizleme hatası: {e}")

    def _rebuild_endorsement_chain_chronological(
        self, policy_id: str, endorsements_with_dates: list
    ):
        """
        Endorsement'ları tarih sırasına göre yeni zincir kurar
        """
        try:
            if not endorsements_with_dates:
                return

            # İlk endorsement (en eski tarihli) - FIRST_ENDORSEMENT
            first_endorsement = endorsements_with_dates[0]
            first_query = """
                MATCH (p:Policy {id: $policy_id})
                MATCH (e:Endorsement {id: $endorsement_id})
                MERGE (p)-[r:FIRST_ENDORSEMENT]->(e)
                SET r.sequence = 0,
                    r.created_at = datetime(),
                    r.source = 'chronological_rebuild'
                RETURN type(r) as rel_type
            """

            self.graph.query(
                first_query,
                {
                    "policy_id": policy_id,
                    "endorsement_id": first_endorsement["endorsement_id"],
                },
                session_params={"database": self.graph._database},
            )

            logging.info(
                f"✅ FIRST_ENDORSEMENT (kronolojik): {policy_id} → {first_endorsement['endorsement_id']} ({first_endorsement.get('start_date', 'tarihsiz')})"
            )

            # Sonraki endorsement'lar - NEXT_ENDORSEMENT zinciri
            for i in range(1, len(endorsements_with_dates)):
                prev_endorsement = endorsements_with_dates[i - 1]
                current_endorsement = endorsements_with_dates[i]

                next_query = """
                    MATCH (prev:Endorsement {id: $prev_id})
                    MATCH (curr:Endorsement {id: $curr_id})
                    MERGE (prev)-[r:NEXT_ENDORSEMENT]->(curr)
                    SET r.sequence = $sequence,
                        r.created_at = datetime(),
                        r.source = 'chronological_rebuild'
                    RETURN type(r) as rel_type
                """

                self.graph.query(
                    next_query,
                    {
                        "prev_id": prev_endorsement["endorsement_id"],
                        "curr_id": current_endorsement["endorsement_id"],
                        "sequence": i,
                    },
                    session_params={"database": self.graph._database},
                )

                logging.info(
                    f"✅ NEXT_ENDORSEMENT (kronolojik): {prev_endorsement['endorsement_id']} → {current_endorsement['endorsement_id']} ({current_endorsement.get('start_date', 'tarihsiz')})"
                )

        except Exception as e:
            logging.error(f"Kronolojik zincir kurma hatası: {e}")

    def _link_endorsement_to_policy_chain(
        self, endorsement_id: str, policy_id: str, file_name: str
    ):
        """
        Endorsement'ı ana poliçeye tarih sırasına göre kronolojik olarak bağlar.
        FIRST_ENDORSEMENT veya NEXT_ENDORSEMENT zincirini oluşturur.

        Args:
            endorsement_id: Endorsement node'unun ID'si
            policy_id: Ana Policy node'unun ID'si (artık policy_number değil)
            file_name: Dosya adı (log için)
        """
        try:
            # Policy_id doğrudan kullanılır (artık arama yapmaya gerek yok)
            logging.info(f"🔗 Kronolojik zincir kurulacak - Policy ID: {policy_id}")

            # Policy'nin var olduğunu kontrol et
            check_policy_query = """
                MATCH (p:Policy {id: $policy_id})
                RETURN p.id as policy_id, p.policyNumber as policy_number LIMIT 1
            """

            result = self.graph.query(
                check_policy_query,
                {"policy_id": policy_id},
                session_params={"database": self.graph._database},
            )

            if not result:
                logging.warning(f"⚠️ Policy ID bulunamadı: {policy_id}")
                return

            policy_number = result[0]["policy_number"]
            logging.info(
                f"✅ Ana poliçe doğrulandı - ID: {policy_id}, Number: {policy_number}"
            )

            # Yeni endorsement'ın tarihini al
            new_endorsement_date = self._get_endorsement_start_date(endorsement_id)
            logging.info(f"📅 Yeni endorsement tarihi: {new_endorsement_date}")

            # Mevcut endorsement'ları tarihleriyle al
            existing_endorsements = self._get_policy_endorsements_with_dates(policy_id)

            if not existing_endorsements:
                # İlk endorsement: FIRST_ENDORSEMENT
                link_query = """
                    MATCH (p:Policy {id: $policy_id})
                    MATCH (e:Endorsement {id: $endorsement_id})
                    MERGE (p)-[r:FIRST_ENDORSEMENT]->(e)
                    SET r.sequence = 0,
                        r.created_at = datetime(),
                        r.source = 'chronological_first'
                    RETURN type(r) as rel_type
                """

                self.graph.query(
                    link_query,
                    {"policy_id": policy_id, "endorsement_id": endorsement_id},
                    session_params={"database": self.graph._database},
                )

                logging.info(
                    f"✅ FIRST_ENDORSEMENT (ilk) oluşturuldu: {policy_id} → {endorsement_id}"
                )
            else:
                # Mevcut endorsement'lar var - kronolojik yeniden düzenleme
                logging.info(
                    f"🔄 Mevcut {len(existing_endorsements)} endorsement var, kronolojik yeniden düzenleme başlıyor..."
                )

                # Yeni endorsement'ı listeye ekle
                all_endorsements = existing_endorsements + [
                    {
                        "endorsement_id": endorsement_id,
                        "start_date": new_endorsement_date,
                        "year": (
                            new_endorsement_date[:4] if new_endorsement_date else None
                        ),
                        "month": (
                            new_endorsement_date[5:7]
                            if len(new_endorsement_date) >= 7
                            else None
                        ),
                    }
                ]

                # Tarihe göre sırala
                def sort_key(item):
                    date_str = item.get("start_date", "")
                    if not date_str:
                        return "9999-12-31"  # Tarihsiz olanlar en sona
                    return date_str

                all_endorsements_sorted = sorted(all_endorsements, key=sort_key)

                # Log ile sıralamayı göster
                logging.info("📊 Kronolojik sıralama:")
                for i, end in enumerate(all_endorsements_sorted):
                    logging.info(
                        f"  {i+1}. {end['endorsement_id']} → {end.get('start_date', 'tarihsiz')}"
                    )

                # Mevcut zinciri temizle
                self._clear_endorsement_chain(policy_id)

                # Yeni kronolojik zinciri kur
                self._rebuild_endorsement_chain_chronological(
                    policy_id, all_endorsements_sorted
                )

                logging.info(
                    f"✅ Kronolojik endorsement zinciri yeniden oluşturuldu: {len(all_endorsements_sorted)} endorsement"
                )

            logging.info(
                f"✅ Kronolojik endorsement bağlantısı tamamlandı: {endorsement_id}"
            )

        except Exception as e:
            logging.error(f"Kronolojik endorsement zincirlemesi hatası: {e}")

    def _create_coverage_nodes_for_endorsement(
        self, coverage_types: list, endorsement_id: str
    ):
        """Coverage node'larını Endorsement'a bağlar"""
        try:
            for coverage_type in coverage_types:
                name = coverage_type.get("name", "").strip()
                if not name:
                    continue

                normalized_name = normalize_unicode_text(name)
                query = """
                    MERGE (ct:Coverage {name: $name})
                    ON CREATE SET 
                        ct.createdAt = datetime()
                    ON MATCH SET 
                        ct.updatedAt = datetime()
                    WITH ct
                    MATCH (e:Endorsement {id: $endorsement_id})
                    MERGE (e)-[r:HAS_COVERAGE]->(ct)
                    SET r.created_at = datetime(),
                        r.source = 'llm_extraction'
                    RETURN ct.name as type_name
                """

                self.graph.query(
                    query,
                    {"name": normalized_name, "endorsement_id": endorsement_id},
                    session_params={"database": self.graph._database},
                )

                logging.info(f"✅ Coverage → Endorsement: {normalized_name}")

        except Exception as e:
            logging.error(f"Coverage nodes oluşturma hatası: {e}")

    def _create_date_nodes_for_endorsement(self, dates_data: dict, endorsement_id: str):
        """Endorsement'ın başlangıç ve bitiş tarihlerini oluşturur"""
        try:
            if not dates_data:
                return

            # Başlangıç tarihi
            start_date_value = dates_data.get("start_date")
            if start_date_value is None:
                start_date_value = ""
            start_date = str(start_date_value).strip() if start_date_value else ""
            if start_date:
                start_date_id = f"date_start_{endorsement_id}"
                query_start = """
                    MERGE (d:Date {id: $date_id})
                    ON CREATE SET 
                        d.value = $date_value,
                        d.year = toInteger($year),
                        d.month = toInteger($month),
                        d.createdAt = datetime()
                    ON MATCH SET 
                        d.updatedAt = datetime(),
                        d.value = $date_value
                    WITH d
                    MATCH (e:Endorsement {id: $endorsement_id})
                    MERGE (e)-[r:HAS_START_DATE]->(d)
                    SET r.created_at = datetime(),
                        r.source = 'llm_extraction'
                    RETURN d.id as date_id
                """

                year, month = self._parse_date_string(start_date)

                self.graph.query(
                    query_start,
                    {
                        "date_id": start_date_id,
                        "date_value": start_date,
                        "year": year,
                        "month": month,
                        "endorsement_id": endorsement_id,
                    },
                    session_params={"database": self.graph._database},
                )

                logging.info(
                    f"✅ Endorsement Start Date: {start_date} (year={year}, month={month})"
                )

            # Bitiş tarihi
            end_date_value = dates_data.get("end_date")
            if end_date_value is None:
                end_date_value = ""
            end_date = str(end_date_value).strip() if end_date_value else ""
            if end_date:
                end_date_id = f"date_end_{endorsement_id}"
                query_end = """
                    MERGE (d:Date {id: $date_id})
                    ON CREATE SET 
                        d.value = $date_value,
                        d.year = toInteger($year),
                        d.month = toInteger($month),
                        d.createdAt = datetime()
                    ON MATCH SET 
                        d.updatedAt = datetime(),
                        d.value = $date_value
                    WITH d
                    MATCH (e:Endorsement {id: $endorsement_id})
                    MERGE (e)-[r:HAS_END_DATE]->(d)
                    SET r.created_at = datetime(),
                        r.source = 'llm_extraction'
                    RETURN d.id as date_id
                """

                year, month = self._parse_date_string(end_date)

                self.graph.query(
                    query_end,
                    {
                        "date_id": end_date_id,
                        "date_value": end_date,
                        "year": year,
                        "month": month,
                        "endorsement_id": endorsement_id,
                    },
                    session_params={"database": self.graph._database},
                )

                logging.info(
                    f"✅ Endorsement End Date: {end_date} (year={year}, month={month})"
                )

        except Exception as e:
            logging.error(f"Endorsement date nodes oluşturma hatası: {e}")

    def _create_policy_node_comprehensive(
        self, policy_id: str, policy_data: dict, file_name: str
    ):
        """Policy node'u kapsamlı bilgilerle oluşturur ve Document'a bağlar"""
        try:
            query = """
                MERGE (p:Policy {id: $policy_id})
                ON CREATE SET 
                    p.policyNumber = $policy_number,
                    p.currency = $currency,
                    p.status = $status,
                    p.source_file = $file_name,
                    p.extraction_method = 'LLM_comprehensive',
                    p.createdAt = datetime()
                ON MATCH SET 
                    p.updatedAt = datetime(),
                    p.source_file = $file_name,
                    p.currency = $currency,
                    p.status = $status
                WITH p
                MATCH (d:Document {fileName: $file_name})
                MERGE (p)-[r:DOCUMENTED_IN]->(d)
                SET r.created_at = datetime(),
                    r.source = 'llm_extraction'
                RETURN p.id as policy_id
            """

            self.graph.query(
                query,
                {
                    "policy_id": policy_id,
                    "policy_number": policy_data.get("policyNumber", ""),
                    "currency": policy_data.get("currency", "TRY"),
                    "status": policy_data.get("status", "Aktif"),
                    "file_name": file_name,
                },
                session_params={"database": self.graph._database},
            )

            logging.info(
                f"✅ Policy node oluşturuldu ve Document'a bağlandı: {policy_id}"
            )

        except Exception as e:
            logging.error(f"Policy node oluşturma hatası: {e}")

    def _create_customer_node_comprehensive(
        self, customer_data: dict, policy_id: str, file_name: str
    ):
        """
        Customer node'u oluşturur ve Policy ile ilişkilendirir.

        NOT: Pre-processing entity resolution KALDIRILDI.
        - Her Customer kendi adıyla MERGE edilir (exact match)
        - Semantic duplicate'ler post-processing ile merge edilir (LLM doğrulamalı)
        - Bu yaklaşım daha güvenli: yanlış merge riski yok
        """
        try:
            # None değerlerini handle et (LLM bazen None dönebilir)
            customer_name = (
                (customer_data.get("name") or "").strip() if customer_data else ""
            )
            if not customer_name:
                return

            # Customer ID oluştur (name-based unique ID)
            customer_id = f"customer_{customer_name.replace(' ', '_').upper()}"

            # MERGE ile exact name match - aynı isim varsa update, yoksa create
            # Semantic benzerlik (A.Ş. vs ANONİM ŞİRKETİ) post-processing'de LLM ile merge edilir
            query = """
                MERGE (c:Customer {name: $customer_name})
                ON CREATE SET 
                    c.id = $customer_id,
                    c.type = $customer_type,
                    c.responsible_person = $responsible_person,
                    c.createdAt = datetime()
                ON MATCH SET 
                    c.updatedAt = datetime(),
                    c.id = COALESCE(c.id, $customer_id),
                    c.type = $customer_type,
                    c.responsible_person = $responsible_person
                WITH c
                MATCH (p:Policy {id: $policy_id})
                MERGE (c)-[r:HAS_POLICY]->(p)
                SET r.created_at = datetime(),
                    r.source = 'llm_extraction'
                RETURN c.name as customer_name, c.id as customer_id
            """

            self.graph.query(
                query,
                {
                    "customer_name": customer_name,
                    "customer_id": customer_id,
                    "customer_type": customer_data.get("type", "Individual"),
                    "responsible_person": customer_data.get("responsible_person", ""),
                    "policy_id": policy_id,
                    "file_name": file_name,
                },
                session_params={"database": self.graph._database},
            )

            logging.info(
                f"✅ Customer node oluşturuldu (Policy'ye bağlı): {customer_name}"
            )

        except Exception as e:
            logging.error(f"Customer node oluşturma hatası: {e}")

    def _create_insurance_company_node(self, company_data: dict, policy_id: str):
        """InsuranceCompany node'u oluşturur ve Policy ile ilişkilendirir - case insensitive normalization ile"""
        try:
            # None değerlerini handle et (LLM bazen None dönebilir)
            company_name = (
                (company_data.get("name") or "").strip() if company_data else ""
            )
            if not company_name:
                return

            # Case insensitive InsuranceCompany node oluşturma - Customer'daki gibi
            query = """
                // Önce normalize edilmiş isimle eşleşen company ara
                OPTIONAL MATCH (existing:InsuranceCompany)
                WHERE apoc.text.clean(existing.name) = apoc.text.clean($company_name)
                
                WITH existing, 
                     CASE WHEN existing IS NULL THEN $company_name ELSE existing.name END as final_name
                
                MERGE (ic:InsuranceCompany {name: final_name})
                ON CREATE SET 
                    ic.responsible_person = $responsible_person,
                    ic.createdAt = datetime(),
                    ic.fullName = final_name,
                    ic.normalizedName = apoc.text.clean($company_name)
                ON MATCH SET 
                    ic.updatedAt = datetime(),
                    ic.responsible_person = $responsible_person,
                    ic.normalizedName = apoc.text.clean($company_name)
                WITH ic
                MATCH (p:Policy {id: $policy_id})
                MERGE (p)-[r:ISSUED_BY]->(ic)
                SET r.created_at = datetime(),
                    r.source = 'llm_extraction'
                RETURN ic.name as company_name
            """

            result = self.graph.query(
                query,
                {
                    "company_name": company_name,
                    "responsible_person": company_data.get("responsible_person", ""),
                    "policy_id": policy_id,
                },
                session_params={"database": self.graph._database},
            )

            if result:
                final_company_name = result[0]["company_name"]
                logging.info(
                    f"✅ InsuranceCompany node oluşturuldu/güncellendi: {final_company_name}"
                )

        except Exception as e:
            logging.error(f"InsuranceCompany node oluşturma hatası: {e}")

    def _parse_date_string(self, date_string: str) -> tuple:
        """
        Tarih string'ini parse eder ve (year, month) tuple döner.
        Birden fazla tarih formatını destekler.

        Args:
            date_string: Parse edilecek tarih string'i (örn: "2024-10-07", "7/10/2024", "7-10-2024")

        Returns:
            tuple: (year: str, month: str)
        """
        year = "0"
        month = "0"

        if not date_string or not date_string.strip():
            return (year, month)

        date_string = date_string.strip()

        try:
            from datetime import datetime as dt
            import re

            # Farklı tarih formatlarını dene
            date_formats = [
                "%Y-%m-%d",  # 2024-10-07
                "%d/%m/%Y",  # 7/10/2024 veya 07/10/2024
                "%d-%m-%Y",  # 7-10-2024
                "%Y/%m/%d",  # 2024/10/07
                "%d.%m.%Y",  # 7.10.2024
                "%m/%d/%Y",  # 10/7/2024 (US format)
            ]

            parsed_date = None
            for date_format in date_formats:
                try:
                    parsed_date = dt.strptime(date_string, date_format)
                    break
                except ValueError:
                    continue

            if parsed_date:
                year = str(parsed_date.year)
                month = str(parsed_date.month)
            else:
                # Parse edilemedi, regex ile çıkarmaya çalış
                # Yıl için 4 haneli sayı ara (19xx veya 20xx)
                year_match = re.search(r"\b(19|20)\d{2}\b", date_string)
                if year_match:
                    year = year_match.group()

                # Ay için 1-2 haneli sayı ara (1-12 arası)
                # Önce yıldan önceki sayıları kontrol et (DD/MM/YYYY formatı için)
                parts = re.split(r"[/\-\.]", date_string)
                if len(parts) >= 3:
                    # Yıl hangi pozisyonda?
                    year_idx = None
                    for i, part in enumerate(parts):
                        if len(part) == 4 and part.startswith(("19", "20")):
                            year_idx = i
                            break

                    if year_idx is not None:
                        # Yıldan önceki sayı ay olabilir
                        if year_idx > 0:
                            month_candidate = parts[year_idx - 1]
                            if (
                                month_candidate.isdigit()
                                and 1 <= int(month_candidate) <= 12
                            ):
                                month = month_candidate
                        # Yıldan sonraki sayı ay olabilir (YYYY/MM/DD formatı için)
                        elif year_idx < len(parts) - 1:
                            month_candidate = parts[year_idx + 1]
                            if (
                                month_candidate.isdigit()
                                and 1 <= int(month_candidate) <= 12
                            ):
                                month = month_candidate

                # Hala ay bulunamadıysa, genel regex ile dene
                if month == "0":
                    month_match = re.search(r"\b(0?[1-9]|1[0-2])\b", date_string)
                    if month_match:
                        month = month_match.group()

                if year != "0" or month != "0":
                    logging.warning(
                        f"⚠️ Tarih parse edilemedi, regex ile çıkarıldı: {date_string} → year={year}, month={month}"
                    )
                else:
                    logging.error(f"❌ Tarih parse edilemedi: {date_string}")

        except Exception as parse_error:
            logging.error(f"❌ Tarih parse hatası: {date_string} - {parse_error}")
            year = "0"
            month = "0"

        return (year, month)

    def _create_date_nodes_for_policy(self, dates_data: dict, policy_id: str):
        """Policy başlangıç ve bitiş tarihlerini oluşturur"""
        try:
            if not dates_data:
                return

            # Başlangıç tarihi
            start_date_value = dates_data.get("start_date")
            if start_date_value is None:
                start_date_value = ""
            start_date = str(start_date_value).strip() if start_date_value else ""
            if start_date:
                start_date_id = f"date_start_{policy_id}"
                query_start = """
                    MERGE (d:Date {id: $date_id})
                    ON CREATE SET 
                        d.value = $date_value,
                        d.year = toInteger($year),
                        d.month = toInteger($month),
                        d.createdAt = datetime()
                    ON MATCH SET 
                        d.updatedAt = datetime(),
                        d.value = $date_value
                    WITH d
                    MATCH (p:Policy {id: $policy_id})
                    MERGE (p)-[r:HAS_START_DATE]->(d)
                    SET r.created_at = datetime(),
                        r.source = 'llm_extraction'
                    RETURN d.id as date_id
                """

                year, month = self._parse_date_string(start_date)

                self.graph.query(
                    query_start,
                    {
                        "date_id": start_date_id,
                        "date_value": start_date,
                        "year": year,
                        "month": month,
                        "policy_id": policy_id,
                    },
                    session_params={"database": self.graph._database},
                )

                logging.info(f"✅ Start Date node oluşturuldu: {start_date}")

            # Bitiş tarihi
            end_date_value = dates_data.get("end_date")
            if end_date_value is None:
                end_date_value = ""
            end_date = str(end_date_value).strip() if end_date_value else ""
            if end_date:
                end_date_id = f"date_end_{policy_id}"
                query_end = """
                    MERGE (d:Date {id: $date_id})
                    ON CREATE SET 
                        d.value = $date_value,
                        d.year = toInteger($year),
                        d.month = toInteger($month),
                        d.createdAt = datetime()
                    ON MATCH SET 
                        d.updatedAt = datetime(),
                        d.value = $date_value
                    WITH d
                    MATCH (p:Policy {id: $policy_id})
                    MERGE (p)-[r:HAS_END_DATE]->(d)
                    SET r.created_at = datetime(),
                        r.source = 'llm_extraction'
                    RETURN d.id as date_id
                """

                year, month = self._parse_date_string(end_date)

                self.graph.query(
                    query_end,
                    {
                        "date_id": end_date_id,
                        "date_value": end_date,
                        "year": year,
                        "month": month,
                        "policy_id": policy_id,
                    },
                    session_params={"database": self.graph._database},
                )

                logging.info(
                    f"✅ End Date node oluşturuldu: {end_date} (year={year}, month={month})"
                )

        except Exception as e:
            logging.error(f"Date nodes oluşturma hatası: {e}")

    def _create_premium_node(self, premium_data: dict, policy_id: str):
        """Premium node'u oluşturur"""
        try:
            amount = premium_data.get("amount")
            if amount is None:
                return

            premium_id = f"premium_{policy_id}"
            query = """
                MERGE (pr:Premium {id: $premium_id})
                ON CREATE SET 
                    pr.amount = $amount,
                    pr.currency = $currency,
                    pr.commissionRate = $commission_rate,
                    pr.createdAt = datetime()
                ON MATCH SET 
                    pr.updatedAt = datetime(),
                    pr.amount = $amount,
                    pr.currency = $currency,
                    pr.commissionRate = $commission_rate
                WITH pr
                MATCH (p:Policy {id: $policy_id})
                MERGE (p)-[r:HAS_PREMIUM]->(pr)
                SET r.created_at = datetime(),
                    r.source = 'llm_extraction'
                RETURN pr.id as premium_id
            """

            self.graph.query(
                query,
                {
                    "premium_id": premium_id,
                    "amount": float(amount) if amount else 0,
                    "currency": premium_data.get("currency", "TRY"),
                    "commission_rate": (
                        float(premium_data.get("commission_rate", 0))
                        if premium_data.get("commission_rate")
                        else 0
                    ),
                    "policy_id": policy_id,
                },
                session_params={"database": self.graph._database},
            )

            logging.info(f"✅ Premium node oluşturuldu: {premium_id}")

        except Exception as e:
            logging.error(f"Premium node oluşturma hatası: {e}")

    def _create_coverage_limit_node(self, coverage_data: dict, policy_id: str):
        """CoverageLimit node'u oluşturur (sayısal limitler)"""
        try:
            if not coverage_data:
                return

            coverage_id = f"coverage_{policy_id}"
            query = """
                MERGE (cv:CoverageLimit {id: $coverage_id})
                ON CREATE SET 
                    cv.limit_value = $limit_value,
                    cv.limit_unit = $limit_unit,
                    cv.limit_count = $limit_count,
                    cv.scope = $scope,
                    cv.createdAt = datetime()
                ON MATCH SET 
                    cv.updatedAt = datetime(),
                    cv.limit_value = $limit_value,
                    cv.limit_unit = $limit_unit,
                    cv.limit_count = $limit_count,
                    cv.scope = $scope
                WITH cv
                MATCH (p:Policy {id: $policy_id})
                MERGE (p)-[r:HAS_COVERAGE_LIMIT]->(cv)
                SET r.created_at = datetime(),
                    r.source = 'llm_extraction'
                RETURN cv.id as coverage_id
            """

            limit_value = coverage_data.get("limit_value")
            limit_count = coverage_data.get("limit_count")

            self.graph.query(
                query,
                {
                    "coverage_id": coverage_id,
                    "limit_value": float(limit_value) if limit_value is not None else 0,
                    "limit_unit": coverage_data.get("limit_unit", "TL"),
                    "limit_count": int(limit_count) if limit_count is not None else 0,
                    "scope": coverage_data.get("scope", "Türkiye"),
                    "policy_id": policy_id,
                },
                session_params={"database": self.graph._database},
            )

            logging.info(f"✅ Coverage node oluşturuldu: {coverage_id}")

        except Exception as e:
            logging.error(f"Coverage node oluşturma hatası: {e}")

    def _create_coverage_nodes(self, coverage_types: list, policy_id: str):
        """Coverage node'larını oluşturur"""
        try:
            # Eğer coverage_types None ise veya boş liste ise, erken çık
            if not coverage_types:
                return

            for coverage_type in coverage_types:
                # Eğer coverage_type bir liste ise (nested list durumu), düzleştir
                if isinstance(coverage_type, list):
                    # Nested list'i düzleştir ve her item için tekrar çağır
                    self._create_coverage_nodes(coverage_type, policy_id)
                    continue

                # Eğer coverage_type bir string ise, dict'e dönüştür
                if isinstance(coverage_type, str):
                    coverage_type = {"name": coverage_type}

                # Artık coverage_type bir dict olmalı
                if not isinstance(coverage_type, dict):
                    logging.warning(
                        f"⚠️ Geçersiz coverage_type formatı (type: {type(coverage_type)}): {coverage_type}"
                    )
                    continue

                name = coverage_type.get("name", "").strip()
                if not name:
                    continue

                # Normalize name for ID
                normalized_name = normalize_unicode_text(name)
                query = """
                    MERGE (ct:Coverage {name: $name})
                    ON CREATE SET 
                        ct.createdAt = datetime()
                    ON MATCH SET 
                        ct.updatedAt = datetime()
                    WITH ct
                    MATCH (p:Policy {id: $policy_id})
                    MERGE (p)-[r:HAS_COVERAGE]->(ct)
                    SET r.created_at = datetime(),
                        r.source = 'llm_extraction'
                    RETURN ct.name as type_name
                """

                self.graph.query(
                    query,
                    {"name": normalized_name, "policy_id": policy_id},
                    session_params={"database": self.graph._database},
                )

                logging.info(f"✅ Coverage node oluşturuldu: {normalized_name}")

        except Exception as e:
            logging.error(f"Coverage nodes oluşturma hatası: {e}")

    def _create_guarantee_nodes(self, guarantees: list, policy_id: str):
        """Guarantee node'larını oluşturur"""
        try:
            if not guarantees:
                return

            for guarantee in guarantees:
                # Handle nested lists
                if isinstance(guarantee, list):
                    self._create_guarantee_nodes(guarantee, policy_id)
                    continue

                # Handle strings
                if isinstance(guarantee, str):
                    guarantee = {"name": guarantee}

                # Validate dict
                if not isinstance(guarantee, dict):
                    logging.warning(f"⚠️ Geçersiz guarantee formatı: {guarantee}")
                    continue

                name = guarantee.get("name", "").strip()
                if not name:
                    continue

                normalized_name = normalize_unicode_text(name)
                guarantee_id = (
                    f"guarantee_{policy_id}_{normalized_name.replace(' ', '_')}"
                )

                query = """
                    MERGE (gua:Guarantee {id: $guarantee_id})
                    ON CREATE SET 
                        gua.name = $name,
                        gua.value = $value,
                        gua.createdAt = datetime()
                    ON MATCH SET 
                        gua.updatedAt = datetime(),
                        gua.name = $name,
                        gua.value = $value
                    WITH gua
                    MATCH (p:Policy {id: $policy_id})
                    MERGE (p)-[r:HAS_GUARANTEE]->(gua)
                    SET r.created_at = datetime(),
                        r.source = 'llm_extraction'
                    RETURN gua.id as guarantee_id
                """

                self.graph.query(
                    query,
                    {
                        "guarantee_id": guarantee_id,
                        "name": normalized_name,
                        "value": guarantee.get("value", ""),
                        "policy_id": policy_id,
                    },
                    session_params={"database": self.graph._database},
                )

                logging.info(f"✅ Guarantee node oluşturuldu: {normalized_name}")

        except Exception as e:
            logging.error(f"Guarantee nodes oluşturma hatası: {e}")

    def _create_clause_nodes(self, clauses: list, policy_id: str):
        """Clause node'larını oluşturur"""
        try:
            if not clauses:
                return

            for clause in clauses:
                # Handle nested lists
                if isinstance(clause, list):
                    self._create_clause_nodes(clause, policy_id)
                    continue

                # Handle strings
                if isinstance(clause, str):
                    clause = {"name": clause}

                # Validate dict
                if not isinstance(clause, dict):
                    logging.warning(f"⚠️ Geçersiz clause formatı: {clause}")
                    continue

                name = clause.get("name", "").strip()
                if not name:
                    continue

                normalized_name = normalize_unicode_text(name)
                clause_id = f"clause_{policy_id}_{normalized_name.replace(' ', '_')}"

                query = """
                    MERGE (cl:Clause {id: $clause_id})
                    ON CREATE SET 
                        cl.name = $name,
                        cl.text = $text,
                        cl.createdAt = datetime()
                    ON MATCH SET 
                        cl.updatedAt = datetime(),
                        cl.name = $name,
                        cl.text = $text
                    WITH cl
                    MATCH (p:Policy {id: $policy_id})
                    MERGE (p)-[r:HAS_CLAUSE]->(cl)
                    SET r.created_at = datetime(),
                        r.source = 'llm_extraction'
                    RETURN cl.id as clause_id
                """

                self.graph.query(
                    query,
                    {
                        "clause_id": clause_id,
                        "name": normalized_name,
                        "text": clause.get("text", ""),
                        "policy_id": policy_id,
                    },
                    session_params={"database": self.graph._database},
                )

                logging.info(f"✅ Clause node oluşturuldu: {normalized_name}")

        except Exception as e:
            logging.error(f"Clause nodes oluşturma hatası: {e}")

    def _create_endorsement_nodes(self, endorsements: list, policy_id: str):
        """
        Endorsement node'larını zincir şeklinde oluşturur.
        FIRST_ENDORSEMENT ile başlar, sonrası NEXT_ENDORSEMENT ile bağlanır.
        """
        try:
            if not endorsements:
                return

            previous_endorsement_id = None

            for index, endorsement in enumerate(endorsements):
                name = endorsement.get("name", "").strip()
                if not name:
                    continue

                normalized_name = normalize_unicode_text(name)
                # Index ekle ki aynı isimli zeyilnameler farklı ID alabilsin
                endorsement_id = f"endorsement_{policy_id}_{index}_{normalized_name.replace(' ', '_')}"

                query = """
                    MERGE (end:Endorsement {id: $endorsement_id})
                    ON CREATE SET 
                        end.name = $name,
                        end.description = $description,
                        end.sequence = $sequence,
                        end.createdAt = datetime()
                    ON MATCH SET 
                        end.updatedAt = datetime(),
                        end.name = $name,
                        end.description = $description,
                        end.sequence = $sequence
                    WITH end
                    MATCH (p:Policy {id: $policy_id})
                    """

                # İlk zeyilname: FIRST_ENDORSEMENT ilişkisi
                if index == 0:
                    query += """
                    MERGE (p)-[r:FIRST_ENDORSEMENT]->(end)
                    SET r.created_at = datetime(),
                        r.source = 'llm_extraction'
                    """
                # Diğer zeyilnameler: NEXT_ENDORSEMENT zinciri ile bağla
                elif previous_endorsement_id:
                    query += f"""
                    MATCH (prev:Endorsement {{id: $prev_endorsement_id}})
                    MERGE (prev)-[r_next:NEXT_ENDORSEMENT]->(end)
                    SET r_next.created_at = datetime(),
                        r_next.source = 'llm_extraction'
                    RETURN end.id as endorsement_id
                    """
                else:
                    query += """
                    RETURN end.id as endorsement_id
                    """

                params = {
                    "endorsement_id": endorsement_id,
                    "name": normalized_name,
                    "description": endorsement.get("description", ""),
                    "policy_id": policy_id,
                    "sequence": index,
                }

                # İlk zeyilname değilse, önceki zeyilname ID'sini ekle
                if index > 0 and previous_endorsement_id:
                    params["prev_endorsement_id"] = previous_endorsement_id

                self.graph.query(
                    query, params, session_params={"database": self.graph._database}
                )

                logging.info(
                    f"✅ Endorsement #{index+1} node oluşturuldu: {normalized_name}"
                )

                # Bu zeyilnameyi sonraki iterasyon için önceki olarak kaydet
                previous_endorsement_id = endorsement_id

        except Exception as e:
            logging.error(f"Endorsement nodes oluşturma hatası: {e}")

    def _create_payment_node(self, payment_data: dict, policy_id: str):
        """Payment node'u oluşturur"""
        try:
            amount = payment_data.get("amount")
            if amount is None:
                return

            payment_id = f"payment_{policy_id}"
            query = """
                MERGE (pay:Payment {id: $payment_id})
                ON CREATE SET 
                    pay.amount = $amount,
                    pay.dueDate = $due_date,
                    pay.method = $method,
                    pay.createdAt = datetime()
                ON MATCH SET 
                    pay.updatedAt = datetime(),
                    pay.amount = $amount,
                    pay.dueDate = $due_date,
                    pay.method = $method
                WITH pay
                MATCH (p:Policy {id: $policy_id})
                MERGE (p)-[r:HAS_PAYMENT]->(pay)
                SET r.created_at = datetime(),
                    r.source = 'llm_extraction'
                RETURN pay.id as payment_id
            """

            self.graph.query(
                query,
                {
                    "payment_id": payment_id,
                    "amount": float(amount) if amount else 0,
                    "due_date": payment_data.get("dueDate", ""),
                    "method": payment_data.get("method", ""),
                    "policy_id": policy_id,
                },
                session_params={"database": self.graph._database},
            )

            logging.info(f"✅ Payment node oluşturuldu: {payment_id}")

        except Exception as e:
            logging.error(f"Payment node oluşturma hatası: {e}")

    def _create_risk_address_node(self, address_data: dict, policy_id: str):
        """Risk Address node'u oluşturur"""
        try:
            address = address_data.get("address", "").strip()
            city = address_data.get("city", "").strip()

            if not (address or city):
                return

            address_id = f"address_{policy_id}"
            query = """
                MERGE (addr:RiskAddress {id: $address_id})
                ON CREATE SET 
                    addr.address = $address,
                    addr.city = $city,
                    addr.district = $district,
                    addr.createdAt = datetime()
                ON MATCH SET 
                    addr.updatedAt = datetime(),
                    addr.address = $address,
                    addr.city = $city,
                    addr.district = $district
                WITH addr
                MATCH (p:Policy {id: $policy_id})
                MERGE (p)-[r:HAS_ADDRESS]->(addr)
                SET r.created_at = datetime(),
                    r.source = 'llm_extraction'
                RETURN addr.id as address_id
            """

            self.graph.query(
                query,
                {
                    "address_id": address_id,
                    "address": address,
                    "city": city,
                    "district": address_data.get("district", ""),
                    "policy_id": policy_id,
                },
                session_params={"database": self.graph._database},
            )

            logging.info(f"✅ RiskAddress node oluşturuldu: {address_id}")

        except Exception as e:
            logging.error(f"RiskAddress node oluşturma hatası: {e}")

    def _create_insured_property_node(
        self, insured_property_data: dict, policy_id: str
    ):
        """InsuredProperty node'u oluşturur (sigortalanan yer bilgileri)"""
        try:
            address = (insured_property_data.get("address") or "").strip()
            city = (insured_property_data.get("city") or "").strip()
            district = (insured_property_data.get("district") or "").strip()
            neighborhood = (insured_property_data.get("neighborhood") or "").strip()
            damage_status = (insured_property_data.get("damageStatus") or "").strip()

            if not (address or city or damage_status):
                return

            property_id = f"insured_property_{policy_id}"

            # Tapu bilgileri
            deed_data = insured_property_data.get("deed", {})
            ada = deed_data.get("ada", "") if isinstance(deed_data, dict) else ""
            parsel = deed_data.get("parsel", "") if isinstance(deed_data, dict) else ""
            pafta = deed_data.get("pafta", "") if isinstance(deed_data, dict) else ""
            independent_section = (
                deed_data.get("independentSectionNumber", "")
                if isinstance(deed_data, dict)
                else ""
            )

            # Bina bilgileri
            building_data = insured_property_data.get("building", {})
            construction_type = (
                building_data.get("constructionType", "")
                if isinstance(building_data, dict)
                else ""
            )
            construction_year = (
                building_data.get("constructionYear", "")
                if isinstance(building_data, dict)
                else ""
            )
            total_floors = (
                building_data.get("totalFloors", "")
                if isinstance(building_data, dict)
                else ""
            )
            usage_type = (
                building_data.get("usageType", "")
                if isinstance(building_data, dict)
                else ""
            )
            area = building_data.get("area")
            area_unit = (
                building_data.get("areaUnit", "")
                if isinstance(building_data, dict)
                else ""
            )

            query = """
                MERGE (ip:InsuredProperty {id: $property_id})
                ON CREATE SET 
                    ip.address = $address,
                    ip.city = $city,
                    ip.district = $district,
                    ip.neighborhood = $neighborhood,
                    ip.damageStatus = $damage_status,
                    ip.ada = $ada,
                    ip.parsel = $parsel,
                    ip.pafta = $pafta,
                    ip.independentSectionNumber = $independent_section,
                    ip.constructionType = $construction_type,
                    ip.constructionYear = $construction_year,
                    ip.totalFloors = $total_floors,
                    ip.usageType = $usage_type,
                    ip.area = $area,
                    ip.areaUnit = $area_unit,
                    ip.createdAt = datetime()
                ON MATCH SET 
                    ip.updatedAt = datetime(),
                    ip.address = $address,
                    ip.city = $city,
                    ip.district = $district,
                    ip.neighborhood = $neighborhood,
                    ip.damageStatus = $damage_status,
                    ip.ada = $ada,
                    ip.parsel = $parsel,
                    ip.pafta = $pafta,
                    ip.independentSectionNumber = $independent_section,
                    ip.constructionType = $construction_type,
                    ip.constructionYear = $construction_year,
                    ip.totalFloors = $total_floors,
                    ip.usageType = $usage_type,
                    ip.area = $area,
                    ip.areaUnit = $area_unit
                WITH ip
                MATCH (p:Policy {id: $policy_id})
                MERGE (p)-[r:HAS_INSURED_PROPERTY]->(ip)
                SET r.created_at = datetime(),
                    r.source = 'llm_extraction'
                RETURN ip.id as property_id
            """

            self.graph.query(
                query,
                {
                    "property_id": property_id,
                    "address": address,
                    "city": city,
                    "district": district,
                    "neighborhood": neighborhood,
                    "damage_status": damage_status,
                    "ada": ada,
                    "parsel": parsel,
                    "pafta": pafta,
                    "independent_section": independent_section,
                    "construction_type": construction_type,
                    "construction_year": construction_year,
                    "total_floors": total_floors,
                    "usage_type": usage_type,
                    "area": float(area) if area is not None else None,
                    "area_unit": area_unit,
                    "policy_id": policy_id,
                },
                session_params={"database": self.graph._database},
            )

            logging.info(f"✅ InsuredProperty node oluşturuldu: {property_id}")

        except Exception as e:
            logging.error(f"InsuredProperty node oluşturma hatası: {e}")

    def _create_insured_person_node(self, insured_person_data: dict, policy_id: str):
        """InsuredPerson node'u oluşturur (sigortalı bilgileri)"""
        try:
            name = insured_person_data.get("name", "").strip()
            if not name:
                return

            person_id = f"insured_person_{policy_id}"

            query = """
                MERGE (ip:InsuredPerson {id: $person_id})
                ON CREATE SET 
                    ip.name = $name,
                    ip.nationality = $nationality,
                    ip.tcIdentityNumber = $tc_identity,
                    ip.mobilePhone = $mobile_phone,
                    ip.landlinePhone = $landline_phone,
                    ip.email = $email,
                    ip.contactAddress = $contact_address,
                    ip.createdAt = datetime()
                ON MATCH SET 
                    ip.updatedAt = datetime(),
                    ip.name = $name,
                    ip.nationality = $nationality,
                    ip.tcIdentityNumber = $tc_identity,
                    ip.mobilePhone = $mobile_phone,
                    ip.landlinePhone = $landline_phone,
                    ip.email = $email,
                    ip.contactAddress = $contact_address
                WITH ip
                MATCH (p:Policy {id: $policy_id})
                MERGE (p)-[r:HAS_INSURED_PERSON]->(ip)
                SET r.created_at = datetime(),
                    r.source = 'llm_extraction'
                RETURN ip.id as person_id
            """

            self.graph.query(
                query,
                {
                    "person_id": person_id,
                    "name": name,
                    "nationality": insured_person_data.get("nationality", ""),
                    "tc_identity": insured_person_data.get("tcIdentityNumber", ""),
                    "mobile_phone": insured_person_data.get("mobilePhone", ""),
                    "landline_phone": insured_person_data.get("landlinePhone", ""),
                    "email": insured_person_data.get("email", ""),
                    "contact_address": insured_person_data.get("contactAddress", ""),
                    "policy_id": policy_id,
                },
                session_params={"database": self.graph._database},
            )

            logging.info(f"✅ InsuredPerson node oluşturuldu: {person_id}")

        except Exception as e:
            logging.error(f"InsuredPerson node oluşturma hatası: {e}")

    def _create_policyholder_node(self, policyholder_data: dict, policy_id: str):
        """Policyholder node'u oluşturur (sigorta ettiren bilgileri)"""
        try:
            name = policyholder_data.get("name", "").strip()
            if not name:
                return

            holder_id = f"policyholder_{policy_id}"

            query = """
                MERGE (ph:Policyholder {id: $holder_id})
                ON CREATE SET 
                    ph.name = $name,
                    ph.nationality = $nationality,
                    ph.tcIdentityNumber = $tc_identity,
                    ph.mobilePhone = $mobile_phone,
                    ph.landlinePhone = $landline_phone,
                    ph.email = $email,
                    ph.role = $role,
                    ph.createdAt = datetime()
                ON MATCH SET 
                    ph.updatedAt = datetime(),
                    ph.name = $name,
                    ph.nationality = $nationality,
                    ph.tcIdentityNumber = $tc_identity,
                    ph.mobilePhone = $mobile_phone,
                    ph.landlinePhone = $landline_phone,
                    ph.email = $email,
                    ph.role = $role
                WITH ph
                MATCH (p:Policy {id: $policy_id})
                MERGE (p)-[r:HAS_POLICYHOLDER]->(ph)
                SET r.created_at = datetime(),
                    r.source = 'llm_extraction'
                RETURN ph.id as holder_id
            """

            self.graph.query(
                query,
                {
                    "holder_id": holder_id,
                    "name": name,
                    "nationality": policyholder_data.get("nationality", ""),
                    "tc_identity": policyholder_data.get("tcIdentityNumber", ""),
                    "mobile_phone": policyholder_data.get("mobilePhone", ""),
                    "landline_phone": policyholder_data.get("landlinePhone", ""),
                    "email": policyholder_data.get("email", ""),
                    "role": policyholder_data.get("role", ""),
                    "policy_id": policy_id,
                },
                session_params={"database": self.graph._database},
            )

            logging.info(f"✅ Policyholder node oluşturuldu: {holder_id}")

        except Exception as e:
            logging.error(f"Policyholder node oluşturma hatası: {e}")

    def _create_chunk_entity_relationships(
        self, file_name: str, policy_id: str, entities_data: dict
    ):
        """
        Document'a ait Chunk'ları, çıkarılan Entity'lere HAS_ENTITY ilişkisi ile bağlar.

        Bu ilişki sayesinde:
        - Agent "bu bilgi nereden geldi?" sorusuna cevap verebilir
        - Citation/kaynak gösterme imkanı sağlar
        - Entity'den geriye Chunk'a traversal yapılabilir
        """
        try:
            # Tüm entity tiplerini ve ID'lerini topla
            entity_ids = []

            # Policy
            if policy_id:
                entity_ids.append({"type": "Policy", "id": policy_id})

            # Customer - None kontrolü ile
            customer_data = entities_data.get("customer") or {}
            customer_name = (
                (customer_data.get("name") or "").strip() if customer_data else ""
            )
            if customer_name:
                entity_ids.append({"type": "Customer", "name": customer_name})

            # InsuranceCompany - None kontrolü ile
            company_data = entities_data.get("insurance_company") or {}
            company_name = (
                (company_data.get("name") or "").strip() if company_data else ""
            )
            if company_name:
                entity_ids.append({"type": "InsuranceCompany", "name": company_name})

            # Coverage - policy_id ile bağlı
            coverage_data = entities_data.get("coverage") or {}
            if coverage_data:
                entity_ids.append({"type": "Coverage", "policy_id": policy_id})

            # Premium - policy_id ile bağlı
            premium_data = entities_data.get("premium") or {}
            if premium_data.get("amount") is not None:
                entity_ids.append({"type": "Premium", "policy_id": policy_id})

            # RiskAddress - policy_id ile bağlı
            address_data = entities_data.get("address") or {}
            if address_data.get("address") or address_data.get("city"):
                entity_ids.append({"type": "RiskAddress", "policy_id": policy_id})

            # InsuredProperty - policy_id ile bağlı
            insured_property_data = entities_data.get("insured_property")
            if (
                isinstance(insured_property_data, list)
                and len(insured_property_data) > 0
            ):
                entity_ids.append({"type": "InsuredProperty", "policy_id": policy_id})
            elif isinstance(insured_property_data, dict) and insured_property_data:
                entity_ids.append({"type": "InsuredProperty", "policy_id": policy_id})

            # InsuredPerson - policy_id ile bağlı
            insured_person_data = entities_data.get("insured_person")
            if isinstance(insured_person_data, list) and len(insured_person_data) > 0:
                entity_ids.append({"type": "InsuredPerson", "policy_id": policy_id})
            elif isinstance(insured_person_data, dict) and insured_person_data.get(
                "name"
            ):
                entity_ids.append({"type": "InsuredPerson", "policy_id": policy_id})

            # Policyholder - policy_id ile bağlı
            policyholder_data = entities_data.get("policyholder")
            if isinstance(policyholder_data, list) and len(policyholder_data) > 0:
                entity_ids.append({"type": "Policyholder", "policy_id": policy_id})
            elif isinstance(policyholder_data, dict) and policyholder_data.get("name"):
                entity_ids.append({"type": "Policyholder", "policy_id": policy_id})

            if not entity_ids:
                logging.info(
                    f"ℹ️ Chunk-Entity ilişkisi için entity bulunamadı: {file_name}"
                )
                return

            # Chunk'ları Entity'lere bağla
            # Her entity tipi için ayrı query çalıştır
            for entity_info in entity_ids:
                entity_type = entity_info.get("type")

                if entity_type == "Policy":
                    query = """
                        MATCH (d:Document {fileName: $file_name})<-[:PART_OF]-(c:Chunk)
                        MATCH (e:Policy {id: $entity_id})
                        MERGE (c)-[r:HAS_ENTITY]->(e)
                        SET r.created_at = datetime(),
                            r.source = 'llm_extraction',
                            r.extraction_method = 'comprehensive'
                        RETURN count(r) as relationships_created
                    """
                    params = {
                        "file_name": file_name,
                        "entity_id": entity_info.get("id"),
                    }

                elif entity_type == "Customer":
                    query = """
                        MATCH (d:Document {fileName: $file_name})<-[:PART_OF]-(c:Chunk)
                        MATCH (e:Customer) WHERE toLower(e.name) = toLower($entity_name)
                        MERGE (c)-[r:HAS_ENTITY]->(e)
                        SET r.created_at = datetime(),
                            r.source = 'llm_extraction'
                        RETURN count(r) as relationships_created
                    """
                    params = {
                        "file_name": file_name,
                        "entity_name": entity_info.get("name"),
                    }

                elif entity_type == "InsuranceCompany":
                    query = """
                        MATCH (d:Document {fileName: $file_name})<-[:PART_OF]-(c:Chunk)
                        MATCH (e:InsuranceCompany) WHERE toLower(e.name) = toLower($entity_name)
                        MERGE (c)-[r:HAS_ENTITY]->(e)
                        SET r.created_at = datetime(),
                            r.source = 'llm_extraction'
                        RETURN count(r) as relationships_created
                    """
                    params = {
                        "file_name": file_name,
                        "entity_name": entity_info.get("name"),
                    }

                else:
                    # Diğer entity'ler için policy_id ile bağlantı kur
                    query = f"""
                        MATCH (d:Document {{fileName: $file_name}})<-[:PART_OF]-(c:Chunk)
                        MATCH (p:Policy {{id: $policy_id}})-[]->(e:{entity_type})
                        MERGE (c)-[r:HAS_ENTITY]->(e)
                        SET r.created_at = datetime(),
                            r.source = 'llm_extraction'
                        RETURN count(r) as relationships_created
                    """
                    params = {
                        "file_name": file_name,
                        "policy_id": entity_info.get("policy_id"),
                    }

                try:
                    result = self.graph.query(
                        query,
                        params,
                        session_params={"database": self.graph._database},
                    )
                    if result and result[0].get("relationships_created", 0) > 0:
                        logging.info(
                            f"✅ Chunk->{entity_type} HAS_ENTITY ilişkileri oluşturuldu: {result[0].get('relationships_created')}"
                        )
                except Exception as entity_error:
                    logging.warning(
                        f"⚠️ Chunk->{entity_type} ilişkisi oluşturulamadı: {entity_error}"
                    )

            logging.info(f"✅ Chunk-Entity ilişkileri tamamlandı: {file_name}")

        except Exception as e:
            logging.error(f"❌ Chunk-Entity ilişkileri oluşturma hatası: {e}")

    def _create_document_summary(
        self,
        file_name: str,
        entities_data: dict,
        document_type: str,
        model: str = "openai_gpt_4o_mini",
    ):
        """
        Document için LLM kullanarak akıllı özet node oluşturur.

        Bu özet:
        - Agent'ın hızlı erişimi için belgenin ana bilgilerini içerir
        - Semantic search için optimize edilmiş doğal dil metni
        - Embedding ile benzerlik araması yapılabilir
        - Document'a HAS_SUMMARY ilişkisi ile bağlanır

        Args:
            file_name: Belge adı
            entities_data: Çıkarılan entity verileri
            document_type: Belge türü (MAIN_POLICY, ENDORSEMENT, vb.)
            model: LLM modeli (özet oluşturmak için)
        """
        try:
            logging.info(f"📝 Document Summary oluşturuluyor (LLM ile): {file_name}")

            # Entity verilerini JSON olarak hazırla (LLM'e göndermek için)
            entities_json = json.dumps(
                entities_data, ensure_ascii=False, indent=2, default=str
            )

            # Özet oluşturma prompt'u
            summary_prompt = f"""
Aşağıdaki sigorta poliçesi/belgesi bilgilerinden Türkçe olarak doğal dil ile özet oluştur.

Belge adı: {file_name}
Belge türü: {document_type}

Çıkarılan bilgiler (JSON):
{entities_json}

ÖZET KURALLARI:
1. Özeti 2-4 cümle arasında tut (maksimum 500 karakter)
2. Doğal, akıcı Türkçe kullan (liste formatı KULLANMA)
3. Şu bilgileri mutlaka dahil et (varsa):
   - Müşteri adı
   - Sigorta şirketi
   - Poliçe türü (Kasko, Konut, DASK, vb.)
   - Sigorta bedeli veya prim tutarı
   - Geçerlilik tarihleri
   - Risk adresi/konumu (il/ilçe)
4. Semantic search için anahtar kelimeleri kullan
5. Belge zeyilname ise ana poliçeye referans ver

ÖRNEK ÖZET:
"Bu belge ÖMER DİNÇKÖK adına AXA Sigorta tarafından düzenlenmiş bir konut sigortası poliçesidir. 
Poliçe 15.03.2024 - 15.03.2025 tarihleri arasında geçerli olup, İstanbul Kadıköy'deki meskeni kapsamaktadır. 
Yıllık prim tutarı 2.500 TL, toplam teminat bedeli 1.500.000 TL'dir."

SADECE özet metnini döndür, başka açıklama ekleme:
"""

            summary_text = None

            # Gemini kullanılıyor mu kontrol et
            try:
                from google import genai as genai_sdk

                api_key = os.environ.get("GEMINI_API_KEY")

                if api_key:
                    client = genai_sdk.Client(api_key=api_key)
                    from google.genai import types

                    logging.info("✅ Gemini 2.0 Flash ile özet oluşturuluyor...")

                    response = client.models.generate_content(
                        model="models/gemini-2.0-flash",
                        contents=[
                            types.Part.from_text(text=summary_prompt),
                        ],
                    )

                    summary_text = response.text.strip() if response.text else None

                    if summary_text:
                        # Tırnak işaretlerini temizle (LLM bazen tırnak içinde döndürüyor)
                        summary_text = summary_text.strip('"').strip("'").strip()
                        logging.info(
                            f"✅ Gemini ile özet oluşturuldu ({len(summary_text)} karakter)"
                        )

            except Exception as gemini_error:
                logging.warning(
                    f"⚠️ Gemini özet oluşturma başarısız: {gemini_error}, fallback deneniyor..."
                )

            # Gemini başarısız olduysa langchain LLM kullan
            if not summary_text:
                try:
                    from src.llm import get_llm

                    llm, _ = get_llm(model)
                    response = llm.invoke(summary_prompt)  # type: ignore[union-attr]
                    summary_text = response.content.strip() if response.content else None  # type: ignore[union-attr]

                    if summary_text:
                        summary_text = summary_text.strip('"').strip("'").strip()
                        logging.info(
                            f"✅ LLM ({model}) ile özet oluşturuldu ({len(summary_text)} karakter)"
                        )

                except Exception as llm_error:
                    logging.warning(f"⚠️ LLM özet oluşturma başarısız: {llm_error}")

            # LLM başarısız olduysa fallback: entity verilerinden basit özet oluştur
            if not summary_text:
                summary_text = self._create_fallback_summary(
                    entities_data, document_type, file_name
                )

            if not summary_text or len(summary_text) < 20:
                logging.warning(f"⚠️ Özet oluşturulamadı: {file_name}")
                return

            # Summary node oluştur ve Document'a bağla
            summary_id = f"summary_{file_name.replace('.', '_').replace(' ', '_')}"

            query = """
                MERGE (s:Summary {id: $summary_id})
                ON CREATE SET 
                    s.text = $summary_text,
                    s.documentType = $document_type,
                    s.fileName = $file_name,
                    s.generatedBy = $generated_by,
                    s.createdAt = datetime()
                ON MATCH SET 
                    s.text = $summary_text,
                    s.documentType = $document_type,
                    s.generatedBy = $generated_by,
                    s.updatedAt = datetime()
                WITH s
                MATCH (d:Document {fileName: $file_name})
                MERGE (d)-[r:HAS_SUMMARY]->(s)
                SET r.created_at = datetime()
                RETURN s.id as summary_id
            """

            # LLM kullanıldı mı belirle
            generated_by = (
                "gemini"
                if "Gemini" in str(summary_text)
                else ("llm" if summary_text else "fallback")
            )

            result = self.graph.query(
                query,
                {
                    "summary_id": summary_id,
                    "summary_text": summary_text,
                    "document_type": document_type,
                    "file_name": file_name,
                    "generated_by": generated_by,
                },
                session_params={"database": self.graph._database},
            )

            if result:
                logging.info(
                    f"✅ Document Summary oluşturuldu: {summary_id} ({len(summary_text)} karakter)"
                )

        except Exception as e:
            logging.error(f"❌ Document Summary oluşturma hatası: {e}")

    def _create_fallback_summary(
        self, entities_data: dict, document_type: str, file_name: str
    ) -> str:
        """
        LLM başarısız olduğunda entity verilerinden basit özet oluşturur.
        """
        try:
            parts = []

            # Document type
            doc_type_tr = {
                "MAIN_POLICY": "ana poliçe",
                "ENDORSEMENT": "zeyilname",
                "CANCELLATION": "iptal belgesi",
                "RENEWAL": "yenileme belgesi",
            }.get(document_type, "belge")

            # Customer - None kontrolü ile
            customer_data = entities_data.get("customer") or {}
            customer_name = (customer_data.get("name") or "").strip()

            # Insurance Company - None kontrolü ile
            company_data = entities_data.get("insurance_company") or {}
            company_name = (company_data.get("name") or "").strip()

            # Policy - None kontrolü ile
            policy_data = entities_data.get("policy") or {}
            policy_type = (policy_data.get("type") or "").strip()

            # Build summary
            if customer_name and company_name:
                parts.append(
                    f"Bu belge {customer_name} adına {company_name} tarafından düzenlenmiş bir {doc_type_tr}"
                )
            elif customer_name:
                parts.append(
                    f"Bu belge {customer_name} adına düzenlenmiş bir {doc_type_tr}"
                )
            else:
                parts.append(f"Bu belge bir {doc_type_tr}")

            if policy_type:
                parts[-1] += f" ({policy_type})"
            parts[-1] += "."

            # Dates
            dates_data = entities_data.get("dates") or {}
            if dates_data.get("start_date") and dates_data.get("end_date"):
                parts.append(
                    f"Geçerlilik: {dates_data.get('start_date')} - {dates_data.get('end_date')}."
                )

            # Premium
            premium_data = entities_data.get("premium") or {}
            insurance_amount = entities_data.get("insurance_amount") or {}
            premium_amount = premium_data.get("amount") or insurance_amount.get(
                "policyPremium"
            )
            if premium_amount:
                currency = premium_data.get("currency") or insurance_amount.get(
                    "currency", "TRY"
                )
                parts.append(f"Prim: {premium_amount} {currency}.")

            # Address
            address_data = entities_data.get("address") or {}
            city = address_data.get("city", "")
            district = address_data.get("district", "")
            if city:
                location = f"{district}, {city}" if district else city
                parts.append(f"Konum: {location}.")

            return " ".join(parts) if parts else ""

        except Exception as e:
            logging.warning(f"⚠️ Fallback summary oluşturma hatası: {e}")
            return ""

    def _update_coverage_premium_from_insurance_amount(
        self, insurance_amount_data: dict, policy_id: str
    ):
        """
        InsuranceAmount bilgilerini Coverage ve Premium node'larına aktarır.
        Node yoksa oluşturur, varsa günceller.
        Policy veya Endorsement için çalışır (policy_id parametresi her ikisini de destekler).

        ÖNEMLİ: Endorsement için yeni değerler sadece Endorsement node'una yazılır,
        Policy node'u etkilenmez.
        """
        try:
            insurance_value = insurance_amount_data.get("insuranceValue")
            policy_premium = insurance_amount_data.get("policyPremium")
            endorsement_insurance_value = insurance_amount_data.get(
                "endorsementInsuranceValue"
            )
            endorsement_premium = insurance_amount_data.get("endorsementPremium")
            currency = insurance_amount_data.get("currency", "TRY")

            # Policy veya Endorsement node'unu kontrol et
            node_type_query = """
                MATCH (n) WHERE n.id = $policy_id
                RETURN labels(n) as labels
                LIMIT 1
            """
            node_result = self.graph.query(
                node_type_query,
                {"policy_id": policy_id},
                session_params={"database": self.graph._database},
            )

            is_endorsement = False
            if node_result and len(node_result) > 0:
                labels = node_result[0].get("labels", [])
                is_endorsement = "Endorsement" in labels
                node_label = "Endorsement" if is_endorsement else "Policy"
            else:
                node_label = "Policy"  # Default

            # Hangi değerleri kullanacağız?
            # Endorsement için: endorsement değerleri varsa onları kullan, yoksa policy değerlerini kullan
            # Policy için: sadece policy değerlerini kullan
            if is_endorsement:
                # Endorsement için: endorsement değerleri öncelikli, yoksa policy değerleri
                final_insurance_value = (
                    endorsement_insurance_value
                    if endorsement_insurance_value is not None
                    else insurance_value
                )
                final_premium = (
                    endorsement_premium
                    if endorsement_premium is not None
                    else policy_premium
                )
                logging.info(
                    f"📋 Endorsement için değerler: insuranceValue={final_insurance_value}, premium={final_premium}"
                )
            else:
                # Policy için: sadece policy değerlerini kullan (endorsement değerlerini görmezden gel)
                final_insurance_value = insurance_value
                final_premium = policy_premium
                logging.info(
                    f"📋 Policy için değerler: insuranceValue={final_insurance_value}, premium={final_premium}"
                )

            # Coverage node'unu oluştur veya güncelle (insuranceValue varsa)
            if final_insurance_value is not None:
                coverage_id = f"coverage_{policy_id}"
                coverage_query = (
                    """
                    MERGE (cv:CoverageLimit {id: $coverage_id})
                    ON CREATE SET 
                        cv.limit_value = $limit_value,
                        cv.limit_unit = $currency,
                        cv.currency = $currency,
                        cv.scope = 'Türkiye',
                        cv.createdAt = datetime()
                    ON MATCH SET 
                        cv.limit_value = $limit_value,
                        cv.currency = $currency,
                        cv.updatedAt = datetime()
                    WITH cv
                    MATCH (n:%s {id: $policy_id})
                    MERGE (n)-[r:HAS_COVERAGE_LIMIT]->(cv)
                    SET r.created_at = datetime(),
                        r.source = 'llm_extraction'
                    RETURN cv.id as coverage_id
                """
                    % node_label
                )

                self.graph.query(
                    coverage_query,
                    {
                        "coverage_id": coverage_id,
                        "limit_value": (
                            float(final_insurance_value) if final_insurance_value else 0
                        ),
                        "currency": currency,
                        "policy_id": policy_id,
                    },
                    session_params={"database": self.graph._database},
                )
                logging.info(
                    f"✅ Coverage {'oluşturuldu/güncellendi'}: insuranceValue={final_insurance_value}"
                )

            # Premium node'unu oluştur veya güncelle (policyPremium varsa)
            if final_premium is not None:
                premium_id = f"premium_{policy_id}"
                premium_query = (
                    """
                    MERGE (pr:Premium {id: $premium_id})
                    ON CREATE SET 
                        pr.amount = $amount,
                        pr.currency = $currency,
                        pr.createdAt = datetime()
                    ON MATCH SET 
                        pr.amount = $amount,
                        pr.currency = $currency,
                        pr.updatedAt = datetime()
                    WITH pr
                    MATCH (n:%s {id: $policy_id})
                    MERGE (n)-[r:HAS_PREMIUM]->(pr)
                    SET r.created_at = datetime(),
                        r.source = 'llm_extraction'
                    RETURN pr.id as premium_id
                """
                    % node_label
                )

                self.graph.query(
                    premium_query,
                    {
                        "premium_id": premium_id,
                        "amount": float(final_premium) if final_premium else 0,
                        "currency": currency,
                        "policy_id": policy_id,
                    },
                    session_params={"database": self.graph._database},
                )
                logging.info(
                    f"✅ Premium {'oluşturuldu/güncellendi'}: premium={final_premium}"
                )

        except Exception as e:
            logging.error(f"InsuranceAmount güncelleme hatası: {e}")

    def _get_document_content_from_chunks(self, file_name: str) -> str:
        """
        Veritabanından belgenin Chunk'larını alır ve birleştirir.

        Args:
            file_name: Belge adı

        Returns:
            str: Belgenin birleştirilmiş metni (ilk 50 Chunk ile sınırlı - daha kapsamlı analiz için)
        """
        try:
            query = """
            MATCH (c:Chunk {fileName: $file_name})
            RETURN c.text as text, c.position as position
            ORDER BY c.position ASC
            LIMIT 50
            """

            results = self.graph.query(
                query,
                {"file_name": file_name},
                session_params={"database": self.graph._database},
            )

            if not results:
                logging.warning(f"⚠️ {file_name} için Chunk bulunamadı")
                return ""

            # Chunk'ları sırasıyla birleştir
            document_content = "\n".join(
                [record.get("text", "") for record in results if record.get("text")]
            )

            logging.info(
                f"✅ Belge içeriği alındı ({len(results)} chunk, {len(document_content)} karakter)"
            )
            return document_content

        except Exception as e:
            logging.error(f"Chunk'lardan belge içeriği alma hatası ({file_name}): {e}")
            return ""

    def _create_policy_type_relationship(
        self,
        policy_id: str,
        relationship_type: str,
        policy_type: str = "",
        policy_details: Optional[dict] = None,
        deductible_info: Optional[dict] = None,
    ):
        """
        LLM'den gelen ilişki tipi ile Customer-Policy arasında dinamik ilişki kurar.

        Args:
            policy_id: Policy node ID'si
            relationship_type: LLM'den gelen ilişki tipi (örn: "IS_KASKO_POLICY")
            policy_type: Policy türü (logging için)

        Bu sayede Agent LLM ile sorudan "kasko poliçesi" çıkartınca doğrudan
        MATCH (c:Customer)-[:IS_KASKO_POLICY]->(p:Policy) yazabilir.
        """
        try:
            if not relationship_type:
                logging.warning(f"Boş relationship_type, atlanıyor: {policy_type}")
                return

            # İlişki tipini temizle ve validate et
            relationship_name = relationship_type.strip().upper()

            # Geçerli ilişki formatı kontrolü (IS_XXX_POLICY)
            if not relationship_name.startswith(
                "IS_"
            ) or not relationship_name.endswith("_POLICY"):
                logging.warning(
                    f"⚠️ Geçersiz ilişki formatı: '{relationship_name}'. IS_XXX_POLICY formatında olmalı."
                )
                # Fallback: basit format oluştur
                if policy_type:
                    import re

                    clean_type = re.sub(r"[^A-Z0-9_]", "_", policy_type.upper())
                    relationship_name = f"IS_{clean_type}_POLICY"
                else:
                    return

            logging.info(
                f"🔗 LLM İlişki Tipi: '{relationship_type}' → '{relationship_name}'"
            )

            # Customer-Policy arasında ilişki kurmak
            # Cypher'da dinamik ilişki adı için inline string kullanırız (parameterize edilemez)
            # Python f-string ile sorguyu oluşturuyoruz

            # Policy-specific details ve deductible bilgilerini prepare et
            set_properties = []
            query_params = {
                "policy_id": policy_id,
                "policy_type": policy_type,
                "relationship_type": relationship_type,
            }

            # Temel properties
            set_properties.extend(
                [
                    "r.created_at = datetime()",
                    "r.policy_type = $policy_type",
                    "r.source = 'llm_extraction'",
                    "r.llm_relationship_type = $relationship_type",
                ]
            )

            # Policy-specific details ekle
            if policy_details:
                # Tip kontrolü: dict değilse atla
                if not isinstance(policy_details, dict):
                    logging.warning(
                        f"⚠️ policy_details dict değil, atlanıyor. Tip: {type(policy_details)}, Değer: {policy_details}"
                    )
                else:
                    for key, value in policy_details.items():
                        if value is not None and value != "":
                            param_name = f"detail_{key}"
                            set_properties.append(f"r.{key} = ${param_name}")
                            query_params[param_name] = value

            # Deductible info ekle
            if deductible_info:
                # Tip kontrolü: dict değilse atla
                if not isinstance(deductible_info, dict):
                    logging.warning(
                        f"⚠️ deductible_info dict değil, atlanıyor. Tip: {type(deductible_info)}, Değer: {deductible_info}"
                    )
                else:
                    for key, value in deductible_info.items():
                        if value is not None and value != "":
                            param_name = f"deductible_{key}"
                            set_properties.append(f"r.{key} = ${param_name}")
                            query_params[param_name] = value

            set_clause = "SET " + ",\n                    ".join(set_properties)

            query = f"""
                MATCH (p:Policy {{id: $policy_id}})
                WITH p
                MATCH (p)<-[:HAS_POLICY]-(c:Customer)
                MERGE (c)-[r:{relationship_name}]->(p)
                {set_clause}
                RETURN r, type(r) as rel_type
            """

            logging.debug(f"📝 Cypher Query: {query[:100]}...")

            result = self.graph.query(
                query, query_params, session_params={"database": self.graph._database}
            )

            if result:
                logging.info(
                    f"✅ LLM Policy ilişkisi oluşturuldu: {relationship_name} ({policy_type})"
                )
            else:
                logging.warning(f"⚠️ Sorgu sonuç döndürmedi: {relationship_name}")

        except Exception as e:
            logging.error(
                f"LLM Policy ilişkisi oluşturma hatası ({relationship_type}): {e}"
            )
