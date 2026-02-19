import logging
import os
import time
import re
import difflib
from neo4j.exceptions import TransientError
from langchain_neo4j import Neo4jGraph
from src.shared.common_fn import (
    create_gcs_bucket_folder_name_hashed,
    delete_uploaded_local_file,
    load_embedding_model,
)
from src.document_sources.gcs_bucket import delete_file_from_gcs
from src.shared.constants import (
    BUCKET_UPLOAD,
    NODEREL_COUNT_QUERY_WITH_COMMUNITY,
    NODEREL_COUNT_QUERY_WITHOUT_COMMUNITY,
)
from src.entities.source_node import sourceNode
from src.communities import MAX_COMMUNITY_LEVELS
from src.utf8_utils import normalize_unicode_text, normalize_file_name
from src.utils.log_helpers import log_delete, log_processing
# Entity resolution pre-processing KALDIRILDI - post-processing LLM ile yapılıyor
# from src.entity_resolver import resolve_entity_before_creation
import json
from dotenv import load_dotenv

load_dotenv()


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
        text_content: str = None,
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
                        d.nodeCount = 0,
                        d.relationshipCount = 0,
                        d.total_chunks = 0,
                        d.processed_chunk = 0,
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

                result = self.graph.query(
                    merge_query,
                    {
                        "file_name": file_name,
                        "file_type": file_type,
                        "file_size": file_size,
                    },
                    session_params={"database": self.graph._database},
                )

                if result:
                    status = result[0]["status"] if result else "unknown"
                    logging.info(
                        f"Document node işlendi: {file_name} (status: {status})"
                    )
                else:
                    logging.info(f"Document node oluşturuldu: {file_name}")

                # Entity extraction artık celery_worker tarafından yapılıyor
                # Backend sadece Document node oluşturur
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
                            d.processingTime = $pt, d.errorMessage = $e_message, d.nodeCount= $n_count, 
                            d.relationshipCount = $r_count, d.model= $model, d.gcsBucket=$gcs_bucket, 
                            d.gcsBucketFolder= $gcs_bucket_folder, d.language= $language,d.gcsProjectId= $gcs_project_id,
                            d.is_cancelled=False, d.total_chunks=$total_chunks, d.processed_chunk=$processed_chunk,
                            d.access_token=$access_token, d.doc_link=$doc_link, d.page_images=$page_images""",
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
                    "n_count": getattr(obj_source_node, "node_count", 0),
                    "r_count": getattr(obj_source_node, "relationship_count", 0),
                    "model": obj_source_node.model,
                    "gcs_bucket": getattr(obj_source_node, "gcsBucket", ""),
                    "gcs_bucket_folder": getattr(
                        obj_source_node, "gcsBucketFolder", ""
                    ),
                    "language": getattr(obj_source_node, "language", ""),
                    "gcs_project_id": getattr(obj_source_node, "gcsProjectId", ""),
                    "access_token": getattr(obj_source_node, "access_token", ""),
                    "doc_link": getattr(obj_source_node, "doc_link", ""),
                    "page_images": getattr(obj_source_node, "page_images", []),
                    "total_chunks": getattr(obj_source_node, "total_chunks", 0),
                    "processed_chunk": getattr(obj_source_node, "processed_chunk", 0),
                },
                session_params={"database": self.graph._database},
            )

            logging.info(f"Tam Document node oluşturuldu: {obj_source_node.file_name}")

            # Entity extraction artık celery_worker tarafından yapılıyor
            # Backend sadece Document node oluşturur

        except Exception as e:
            error_message = str(e)
            logging.error(f"Document node oluşturma hatası: {error_message}")
            if not isinstance(obj_source_node_or_filename, str):
                self.update_exception_db(
                    self, obj_source_node_or_filename.file_name, error_message
                )
            raise Exception(error_message)

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
                params["processingTime"] = round(
                    obj_source_node.processing_time.total_seconds(), 2
                )

            if obj_source_node.node_count is not None:
                params["nodeCount"] = obj_source_node.node_count

            if obj_source_node.relationship_count is not None:
                params["relationshipCount"] = obj_source_node.relationship_count

            if obj_source_node.model is not None and obj_source_node.model != "":
                params["model"] = obj_source_node.model

            if (
                obj_source_node.total_chunks is not None
                and obj_source_node.total_chunks != 0
            ):
                params["total_chunks"] = obj_source_node.total_chunks

            if obj_source_node.is_cancelled is not None:
                params["is_cancelled"] = obj_source_node.is_cancelled

            if obj_source_node.processed_chunk is not None:
                params["processed_chunk"] = obj_source_node.processed_chunk

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
            self.update_exception_db(self, self.file_name, error_message)
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

        # Optimize: Sadece chunk var mı kontrol et (tüm chunk'ları saymak çok yavaş - 500K+ chunk olabilir)
        result_chunks = self.graph.query(
            """MATCH (c:Chunk) 
               WITH c LIMIT 1
               RETURN size(c.embedding) as embeddingSize, 1 as chunks, 
                      CASE WHEN c.embedding IS NOT NULL THEN 1 ELSE 0 END as hasEmbedding
            """,
            session_params={"database": self.graph._database},
        )

        embedding_model = os.getenv("EMBEDDING_MODEL")
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

    def execute_query(self, query, param=None, max_retries=3, delay=2):
        """
        Neo4j query'sini timeout ve connection hatalarına karşı retry mekanizması ile çalıştırır
        """
        import time
        from neo4j.exceptions import SessionExpired, ServiceUnavailable, TransientError

        retries = 0
        while retries < max_retries:
            try:
                return self.graph.query(
                    query, param, session_params={"database": self.graph._database}
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
                d.nodeCount AS nodeCount, d.model as model, d.relationshipCount as relationshipCount,
                d.total_chunks AS total_chunks , d.fileSize as fileSize, 
                d.is_cancelled as is_cancelled, d.processed_chunk as processed_chunk, d.fileSource as fileSource,
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
                logging.info(f"Document node create_source_node ile oluşturuldu: {file_name}")

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
        gcs_file_cache = os.environ.get("GCS_FILE_CACHE")

        log_delete(
            f"Starting deletion process for {len(filename_list)} files: {filename_list}"
        )
        log_delete(
            f"Delete entities mode: {deleteEntities}, Source types: {source_types_list}"
        )

        for file_name, source_type in zip(filename_list, source_types_list):
            merged_file_path = os.path.join(merged_dir, file_name)
            if source_type == "local file" and gcs_file_cache == "True":
                folder_name = create_gcs_bucket_folder_name_hashed(uri, file_name)
                delete_file_from_gcs(BUCKET_UPLOAD, folder_name, file_name)
                log_delete(f"File deleted from GCS bucket: {file_name}")
            else:
                logging.info(
                    f"Deleted File Path: {merged_file_path} and Deleted File Name : {file_name}"
                )
                delete_uploaded_local_file(merged_file_path, file_name)
                log_delete(f"File deleted from local storage: {file_name}")

        query_to_delete_document = """
            MATCH (d:Document)
            WHERE d.fileName IN $filename_list AND coalesce(d.fileSource, "None") IN $source_types_list
            WITH COLLECT(d) AS documents, COLLECT(d.fileName) AS fileNames
            CALL (documents, fileNames) {
            UNWIND documents AS d
            // PART_OF ilişkisi ile bağlı chunk'lar
            OPTIONAL MATCH (d)<-[:PART_OF]-(c1:Chunk) 
            WITH d, fileNames, COLLECT(DISTINCT c1) AS partOfChunks
            // fileName ile eşleşen chunk'lar (PART_OF ilişkisi olmayan)
            OPTIONAL MATCH (c2:Chunk) WHERE c2.fileName IN fileNames
            WITH d, partOfChunks, COLLECT(DISTINCT c2) AS fileNameChunks
            // Tüm chunk'ları birleştir
            WITH d, partOfChunks + fileNameChunks AS allChunks
            FOREACH (chunk IN allChunks | DETACH DELETE chunk)
            DETACH DELETE d
            } IN TRANSACTIONS OF 1 ROWS
            """
        # Dinamik silme query'si - tüm node tiplerini ve ilişkileri dinamik olarak bulur
        # Hardcoded node tipleri yerine generic graph traversal kullanır
        query_to_delete_document_and_entities = """
            MATCH (d:Document)
            WHERE d.fileName IN $filename_list AND coalesce(d.fileSource, "None") IN $source_types_list
            WITH COLLECT(d) AS documents, COLLECT(d.fileName) AS fileNames
            CALL (documents, fileNames) {
            UNWIND documents AS d
            
            // 1. Chunk'ları topla - hem PART_OF ilişkisi hem fileName ile
            OPTIONAL MATCH (d)<-[:PART_OF]-(c1:Chunk)
            WITH d, documents, fileNames, COLLECT(DISTINCT c1) AS partOfChunks
            OPTIONAL MATCH (c2:Chunk) WHERE c2.fileName IN fileNames
            WITH d, documents, fileNames, partOfChunks + COLLECT(DISTINCT c2) AS chunks
            
            // 2. Chunk'lara bağlı entity'leri topla (HAS_ENTITY ilişkisi ile)
            OPTIONAL MATCH (c:Chunk)-[:HAS_ENTITY]->(chunkEntity) WHERE c IN chunks
            WITH d, documents, chunks, COLLECT(DISTINCT chunkEntity) AS chunkEntities
            
            // 3. Document'a direkt bağlı TÜM node'ları topla (herhangi bir ilişki ile)
            // Document ve Chunk hariç tüm node tiplerini yakala
            OPTIONAL MATCH (d)--(directNode)
            WHERE NOT directNode:Document AND NOT directNode:Chunk
            WITH d, documents, chunks, chunkEntities, COLLECT(DISTINCT directNode) AS directNodes
            
            // 4. İkinci seviye node'ları topla (Document'a bağlı node'lara bağlı node'lar)
            // Örn: Document <- Policy -> CoverageLimit, Policy -> RiskAddress
            OPTIONAL MATCH (d)--(firstLevel)--(secondLevel)
            WHERE NOT firstLevel:Document AND NOT firstLevel:Chunk
              AND NOT secondLevel:Document AND NOT secondLevel:Chunk
              AND NOT secondLevel IN directNodes
            WITH d, documents, chunks, chunkEntities, directNodes, 
                 COLLECT(DISTINCT secondLevel) AS secondLevelNodes
            
            // 5. Üçüncü seviye node'ları topla (derin ilişkiler için)
            OPTIONAL MATCH (d)--(l1)--(l2)--(l3)
            WHERE NOT l1:Document AND NOT l1:Chunk
              AND NOT l2:Document AND NOT l2:Chunk
              AND NOT l3:Document AND NOT l3:Chunk
              AND NOT l3 IN directNodes AND NOT l3 IN secondLevelNodes
            WITH d, documents, chunks, chunkEntities, directNodes, secondLevelNodes,
                 COLLECT(DISTINCT l3) AS thirdLevelNodes
            
            // 6. Tüm potansiyel silinecek node'ları birleştir
            WITH d, documents, chunks,
                 chunkEntities + directNodes + secondLevelNodes + thirdLevelNodes AS allRelatedNodes
            
            // 7. Sadece başka Document'lara bağlı OLMAYAN node'ları sil
            // (Diğer document'larda da kullanılan node'ları korur)
            WITH d, chunks,
                 [node IN allRelatedNodes WHERE node IS NOT NULL AND NOT EXISTS {
                     MATCH (node)-[*1..3]-(otherDoc:Document)
                     WHERE NOT otherDoc IN documents
                 }] AS safeToDeleteNodes
            
            // 8. Silme işlemi
            FOREACH (chunk IN chunks | DETACH DELETE chunk)
            FOREACH (node IN safeToDeleteNodes | DETACH DELETE node)
            DETACH DELETE d
            } IN TRANSACTIONS OF 1 ROWS
            """
        query_to_delete_communities = """
            MATCH (c:`__Community__`)
            WHERE c.level = 0 AND NOT EXISTS { ()-[:IN_COMMUNITY]->(c) }
            DETACH DELETE c
            WITH 1 AS dummy
            UNWIND range(1, $max_level)  AS level
            CALL (level) {
                MATCH (c:`__Community__`)
                WHERE c.level = level AND NOT EXISTS { ()-[:PARENT_COMMUNITY]->(c) }
                DETACH DELETE c
                }
        """
        param = {"filename_list": filename_list, "source_types_list": source_types_list}
        community_param = {"max_level": MAX_COMMUNITY_LEVELS}
        if deleteEntities == "true":
            log_delete(
                f"Executing comprehensive deletion (documents + entities) for {len(filename_list)} files"
            )
            result = self.execute_query(query_to_delete_document_and_entities, param)
            _ = self.execute_query(query_to_delete_communities, community_param)
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
            # Dinamik olarak tüm seviyeleri tarar (Document -> Policy -> CoverageLimit gibi)
            safe_deletion_query = """
                MATCH (d:Document {fileName: $file_name})
                
                // 1. Chunk'ları topla - hem PART_OF ilişkisi hem fileName ile
                OPTIONAL MATCH (d)<-[:PART_OF]-(c1:Chunk)
                WITH d, COLLECT(DISTINCT c1) AS partOfChunks
                OPTIONAL MATCH (c2:Chunk {fileName: d.fileName})
                WITH d, partOfChunks + COLLECT(DISTINCT c2) AS chunksToDelete
                
                // 2. Chunk'lara bağlı entity'leri topla
                OPTIONAL MATCH (chunk:Chunk)-[:HAS_ENTITY]->(chunkEntity) WHERE chunk IN chunksToDelete
                WITH d, chunksToDelete, COLLECT(DISTINCT chunkEntity) AS chunkEntities
                
                // 3. Document'a direkt bağlı TÜM node'ları topla (1. seviye)
                OPTIONAL MATCH (d)--(directNode)
                WHERE NOT directNode:Document AND NOT directNode:Chunk AND NOT directNode:`__Community__`
                WITH d, chunksToDelete, chunkEntities, COLLECT(DISTINCT directNode) AS directNodes
                
                // 4. İkinci seviye node'ları topla (Document -> X -> Y)
                OPTIONAL MATCH (d)--(l1)--(l2)
                WHERE NOT l1:Document AND NOT l1:Chunk AND NOT l1:`__Community__`
                  AND NOT l2:Document AND NOT l2:Chunk AND NOT l2:`__Community__`
                  AND NOT l2 IN directNodes
                WITH d, chunksToDelete, chunkEntities, directNodes, COLLECT(DISTINCT l2) AS secondLevelNodes
                
                // 5. Üçüncü seviye node'ları topla (Document -> X -> Y -> Z)
                OPTIONAL MATCH (d)--(l1)--(l2)--(l3)
                WHERE NOT l1:Document AND NOT l1:Chunk AND NOT l1:`__Community__`
                  AND NOT l2:Document AND NOT l2:Chunk AND NOT l2:`__Community__`
                  AND NOT l3:Document AND NOT l3:Chunk AND NOT l3:`__Community__`
                  AND NOT l3 IN directNodes AND NOT l3 IN secondLevelNodes
                WITH d, chunksToDelete, 
                     chunkEntities + directNodes + secondLevelNodes + COLLECT(DISTINCT l3) AS allRelatedNodes
                
                // 6. Güvenlik kontrolü - sadece başka Document'lara bağlı olmayan node'ları sil
                WITH d, chunksToDelete,
                     [node IN allRelatedNodes WHERE node IS NOT NULL AND NOT EXISTS {
                         MATCH (node)-[*1..3]-(otherDoc:Document)
                         WHERE otherDoc.fileName <> $file_name
                     }] AS safeToDeleteNodes
                
                // 7. Silme işlemini gerçekleştir
                FOREACH (chunk IN chunksToDelete | DETACH DELETE chunk)
                FOREACH (node IN safeToDeleteNodes | DETACH DELETE node)
                DETACH DELETE d
                
                RETURN 
                    size(chunksToDelete) as deletedChunks,
                    size(safeToDeleteNodes) as deletedNodes
            """

            deletion_result = self.execute_query(
                safe_deletion_query, {"file_name": file_name}
            )

            if deletion_result:
                deleted_chunks = deletion_result[0].get("deletedChunks", 0)
                deleted_nodes = deletion_result[0].get("deletedNodes", 0)

                total_deleted = deleted_chunks + deleted_nodes

                logging.info(f"✅ Otomatik temizlik tamamlandı: {file_name}")
                logging.info(f"📊 Silinen node istatistikleri:")
                logging.info(f"   - Silinen Chunk'lar: {deleted_chunks}")
                logging.info(f"   - Silinen Entity/Node'lar: {deleted_nodes}")
                logging.info(f"   - Toplam silinen: {total_deleted}")
                logging.info(f"   - Document node da silindi: {file_name}")

                return True
            else:
                logging.error(f"❌ Otomatik temizlik başarısız oldu: {file_name}")
                return False

        except Exception as e:
            logging.error(f"❌ Otomatik temizlik hatası ({file_name}): {e}")
            return False

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
        score_value = float(os.environ.get("DUPLICATE_SCORE_VALUE"))
        text_distance = int(os.environ.get("DUPLICATE_TEXT_DISTANCE"))
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
        embedding_model = os.getenv("EMBEDDING_MODEL")
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

    def update_node_relationship_count(self, document_name):
        logging.info("updating node and relationship count")
        label_query = """CALL db.labels"""
        community_flag = {"label": "__Community__"} in self.execute_query(label_query)
        if (not document_name) and (community_flag):
            result = self.execute_query(NODEREL_COUNT_QUERY_WITH_COMMUNITY)
        elif (not document_name) and (not community_flag):
            return []
        else:
            param = {"document_name": document_name}
            result = self.execute_query(NODEREL_COUNT_QUERY_WITHOUT_COMMUNITY, param)
        response = {}
        if result:
            for record in result:
                filename = record.get("filename", None)
                chunkNodeCount = int(record.get("chunkNodeCount", 0))
                chunkRelCount = int(record.get("chunkRelCount", 0))
                entityNodeCount = int(record.get("entityNodeCount", 0))
                entityEntityRelCount = int(record.get("entityEntityRelCount", 0))
                if (not document_name) and (community_flag):
                    communityNodeCount = int(record.get("communityNodeCount", 0))
                    communityRelCount = int(record.get("communityRelCount", 0))
                else:
                    communityNodeCount = 0
                    communityRelCount = 0
                # Sadece toplamları hesapla, ayrıntıları Document'a kaydetme
                nodeCount = chunkNodeCount + entityNodeCount + communityNodeCount
                relationshipCount = (
                    chunkRelCount + entityEntityRelCount + communityRelCount
                )
                update_query = """
                MATCH (d:Document {fileName: $filename})
                SET d.nodeCount = $nodeCount,
                    d.relationshipCount = $relationshipCount
                """
                self.execute_query(
                    update_query,
                    {
                        "filename": filename,
                        "nodeCount": nodeCount,
                        "relationshipCount": relationshipCount,
                    },
                )

                response[filename] = {
                    "nodeCount": nodeCount,
                    "relationshipCount": relationshipCount,
                }

        return response

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
            # create_chunk_vector_index kaldırıldı - celery_worker'da yapılıyor

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

            # Vector index işlemi celery_worker'da yapılıyor
            if total_updated > 0:
                logging.info(f"✅ {total_updated} chunks updated - vector index should be managed by celery_worker")

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
            update_query = """
                UNWIND $batch_data AS row
                MATCH (c:Chunk {id: row.chunk_id})
                SET c.embedding = row.embedding
                RETURN count(c) as updated_count
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
                      (örn: ["Customer", "Policy", "CoverageType", "all"])

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
            update_query = """
                UNWIND $batch_data AS row
                MATCH (n) WHERE elementId(n) = row.node_id
                SET n.embedding = row.embedding
                RETURN count(n) as updated_count
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

