import logging
import os
import time
from neo4j.exceptions import TransientError
from langchain_neo4j import Neo4jGraph
from src.shared.common_fn import create_gcs_bucket_folder_name_hashed, delete_uploaded_local_file, load_embedding_model
from src.document_sources.gcs_bucket import delete_file_from_gcs
from src.shared.constants import BUCKET_UPLOAD,NODEREL_COUNT_QUERY_WITH_COMMUNITY, NODEREL_COUNT_QUERY_WITHOUT_COMMUNITY
from src.entities.source_node import sourceNode
from src.communities import MAX_COMMUNITY_LEVELS
from src.utf8_utils import normalize_unicode_text, normalize_file_name
from src.utils.log_helpers import log_delete, log_processing
import json
from dotenv import load_dotenv

load_dotenv()

# Neo4j notification loglarını kapat
def filter_neo4j_notifications(record):
    message = record.getMessage().lower()
    filtered_keywords = [
        "deprecation", "deprecated", "unknown label", "call subquery", 
        "variable scope clause", "notification", "severity", "category",
        "received notification from dbms server"
    ]
    return not any(keyword in message for keyword in filtered_keywords)

logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)
logging.getLogger("neo4j").setLevel(logging.WARNING)
logging.getLogger("neo4j").addFilter(filter_neo4j_notifications)

class graphDBdataAccess:

    def __init__(self, graph: Neo4jGraph):
        self.graph = graph

    def update_exception_db(self, file_name, exp_msg, retry_condition=None):
        try:
            job_status = "Failed"
            result = self.get_current_status_document_node(file_name)
            if len(result) > 0:
                is_cancelled_status = result[0]['is_cancelled']
                if bool(is_cancelled_status) == True:
                    job_status = 'Cancelled'
            if retry_condition is not None: 
                retry_condition = None
                self.graph.query("""MERGE(d:Document {fileName :$fName}) SET d.status = $status, d.errorMessage = $error_msg, d.retry_condition = $retry_condition""",
                            {"fName":file_name, "status":job_status, "error_msg":exp_msg, "retry_condition":retry_condition},session_params={"database":self.graph._database})
            else :    
                self.graph.query("""MERGE(d:Document {fileName :$fName}) SET d.status = $status, d.errorMessage = $error_msg""",
                            {"fName":file_name, "status":job_status, "error_msg":exp_msg},session_params={"database":self.graph._database})
        except Exception as e:
            error_message = str(e)
            logging.error(f"Error in updating document node status as failed: {error_message}")
            raise Exception(error_message)
        
    def create_source_node(self, obj_source_node_or_filename, document_type: str = "auto", text_content: str = None):
        """
        Document node oluşturur. sourceNode objesi veya sadece file_name string'i alabilir.
        
        Args:
            obj_source_node_or_filename: sourceNode objesi veya file_name string'i
            document_type: 'policy', 'cv', veya 'auto' (otomatik tespit)
            text_content: PDF'den extract edilen text content (CV extraction için)
        """
        try:
            # Eğer string ise, minimal Document node oluştur
            if isinstance(obj_source_node_or_filename, str):
                original_file_name = obj_source_node_or_filename
                
                # UTF-8 ve Unicode normalization - Critical for duplicate prevention
                file_name = normalize_file_name(original_file_name)
                
                logging.info(f"Document node oluşturma: '{original_file_name}' -> '{file_name}'")
                
                # Unicode karakter detayları için debug
                if original_file_name != file_name:
                    logging.warning(f"Filename normalization değişikliği: ")
                    logging.warning(f"  Original: {repr(original_file_name)}")
                    logging.warning(f"  Normalized: {repr(file_name)}")
                    logging.warning(f"  Original bytes: {original_file_name.encode('utf-8').hex()}")
                    logging.warning(f"  Normalized bytes: {file_name.encode('utf-8').hex()}")
                
                # Dosya bilgilerini file_name'den çıkar
                import os
                file_extension = os.path.splitext(file_name)[1].lower()
                
                # Dosya tipini uzantıdan belirle
                if file_extension in ['.pdf']:
                    file_type = 'PDF'
                elif file_extension in ['.txt']:
                    file_type = 'Text'
                elif file_extension in ['.docx', '.doc']:
                    file_type = 'Word Document'
                elif file_extension in ['.html', '.htm']:
                    file_type = 'HTML'
                elif file_extension in ['.json']:
                    file_type = 'JSON'
                elif file_extension in ['.csv']:
                    file_type = 'CSV'
                elif file_extension in ['.xml']:
                    file_type = 'XML'
                else:
                    file_type = f'Document{file_extension.upper()}'
                
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
                        d.status = 'New',
                        d.fileSource = 'local file',
                        d.fileType = $file_type,
                        d.fileSize = $file_size,
                        d.createdAt = datetime(),
                        d.updatedAt = datetime(),
                        d.processingTime = 0,
                        d.nodeCount = 0,
                        d.relationshipCount = 0,
                        d.total_chunks = 0,
                        d.processed_chunk = 0,
                        d.chunkNodeCount = 0,
                        d.chunkRelCount = 0,
                        d.entityNodeCount = 0,
                        d.entityEntityRelCount = 0,
                        d.communityNodeCount = 0,
                        d.communityRelCount = 0,
                        d.is_cancelled = false,
                        d.errorMessage = '',
                        d.model = 'unknown'
                    ON MATCH SET 
                        d.updatedAt = datetime(),
                        d.fileType = $file_type,
                        d.fileSize = $file_size
                    RETURN d.fileName as fileName, d.status as status
                """
                
                result = self.graph.query(merge_query, {
                    "file_name": file_name, 
                    "file_type": file_type,
                    "file_size": file_size
                }, session_params={"database": self.graph._database})
                
                if result:
                    status = result[0]['status'] if result else 'unknown'
                    logging.info(f"Document node işlendi: {file_name} (status: {status})")
                else:
                    logging.info(f"Document node oluşturuldu: {file_name}")
                
                # Document yaratıldıktan sonra belge tipine göre node'unu yarat ve bağla
                self._create_document_related_nodes(file_name, document_type, text_content)
                return
            
            # sourceNode objesi ise, orijinal işlemi yap
            obj_source_node = obj_source_node_or_filename
            
            # UTF-8 ve Unicode normalization for file_name - Critical!
            obj_source_node.file_name = normalize_file_name(obj_source_node.file_name)
            
            job_status = "New"
            logging.info(f"Tam Document node oluşturuluyor: {obj_source_node.file_name}")
            self.graph.query("""MERGE(d:Document {fileName :$fn}) SET d.fileSize = $fs, d.fileType = $ft ,
                            d.status = $st, d.url = $url, d.awsAccessKeyId = $awsacc_key_id, 
                            d.fileSource = $f_source, d.createdAt = $c_at, d.updatedAt = $u_at, 
                            d.processingTime = $pt, d.errorMessage = $e_message, d.nodeCount= $n_count, 
                            d.relationshipCount = $r_count, d.model= $model, d.gcsBucket=$gcs_bucket, 
                            d.gcsBucketFolder= $gcs_bucket_folder, d.language= $language,d.gcsProjectId= $gcs_project_id,
                            d.is_cancelled=False, d.total_chunks=$total_chunks, d.processed_chunk=$processed_chunk,
                            d.access_token=$access_token, d.doc_link=$doc_link, d.page_images=$page_images,
                            d.chunkNodeCount=$chunkNodeCount,d.chunkRelCount=$chunkRelCount,
                            d.entityNodeCount=$entityNodeCount,d.entityEntityRelCount=$entityEntityRelCount,
                            d.communityNodeCount=$communityNodeCount,d.communityRelCount=$communityRelCount""",
                            {"fn":obj_source_node.file_name, "fs":obj_source_node.file_size, "ft":obj_source_node.file_type, "st":job_status, 
                            "url":getattr(obj_source_node, 'url', ''),
                            "awsacc_key_id":getattr(obj_source_node, 'awsAccessKeyId', ''), "f_source":obj_source_node.file_source, "c_at":obj_source_node.created_at,
                            "u_at":obj_source_node.created_at, "pt":getattr(obj_source_node, 'processing_time', 0), "e_message":'', 
                            "n_count":getattr(obj_source_node, 'node_count', 0), "r_count":getattr(obj_source_node, 'relationship_count', 0), "model":obj_source_node.model,
                            "gcs_bucket": getattr(obj_source_node, 'gcsBucket', ''), "gcs_bucket_folder": getattr(obj_source_node, 'gcsBucketFolder', ''), 
                            "language":getattr(obj_source_node, 'language', ''), "gcs_project_id":getattr(obj_source_node, 'gcsProjectId', ''),
                            "access_token":getattr(obj_source_node, 'access_token', ''), "doc_link":getattr(obj_source_node, 'doc_link', ''), 
                            "page_images":getattr(obj_source_node, 'page_images', []),
                            "total_chunks":getattr(obj_source_node, 'total_chunks', 0), "processed_chunk":getattr(obj_source_node, 'processed_chunk', 0),
                            "chunkNodeCount":obj_source_node.chunkNodeCount,
                            "chunkRelCount":obj_source_node.chunkRelCount,
                            "entityNodeCount":obj_source_node.entityNodeCount,
                            "entityEntityRelCount":obj_source_node.entityEntityRelCount,
                            "communityNodeCount":obj_source_node.communityNodeCount,
                            "communityRelCount":obj_source_node.communityRelCount
                            },session_params={"database":self.graph._database})
            
            logging.info(f"Tam Document node oluşturuldu: {obj_source_node.file_name}")
            
            # Document yaratıldıktan sonra belge tipine göre node'unu yarat ve bağla
            self._create_document_related_nodes(obj_source_node.file_name, document_type, text_content)
            
        except Exception as e:
            error_message = str(e)
            logging.error(f"Document node oluşturma hatası: {error_message}")
            if not isinstance(obj_source_node_or_filename, str):
                self.update_exception_db(self, obj_source_node_or_filename.file_name, error_message)
            raise Exception(error_message)

    def _create_document_related_nodes(self, file_name: str, document_type: str = "auto", text_content: str = None):
        """
        Belge tipine göre uygun node'ları oluşturur (Policy veya CV)
        
        Args:
            file_name: Dosya adı
            document_type: 'policy', 'cv', veya 'auto' (otomatik tespit)
            text_content: PDF'den extract edilen text content (CV extraction için)
        """
        try:
            # Otomatik tespit
            if document_type == "auto":
                document_type = self._detect_document_type(file_name)
            
            # Belge tipine göre uygun node'u oluştur
            if document_type == "policy":
                logging.info(f"📋 Policy node oluşturuluyor: {file_name}")
                self.create_policy_node_from_document(file_name)
            elif document_type == "cv":
                logging.info(f"👤 CV node oluşturuluyor: {file_name}")
                self.create_cv_node_from_document(file_name, text_content)
            else:
                logging.info(f"ℹ️ Bilinmeyen belge tipi, varsayılan olarak policy node oluşturuluyor: {file_name}")
                self.create_policy_node_from_document(file_name)
                
        except Exception as e:
            logging.error(f"Document related nodes oluşturma hatası ({file_name}): {e}")

    def _detect_document_type(self, file_name: str) -> str:
        """
        Dosya adından belge tipini otomatik olarak tespit eder
        
        Args:
            file_name: Dosya adı
            
        Returns:
            'policy' veya 'cv'
        """
        try:
            file_name_lower = file_name.lower()
            
            # CV anahtar kelimeleri
            cv_keywords = [
                'cv', 'resume', 'özgeçmiş', 'ozgecmis', 'curriculum', 'vitae',
                'biography', 'biografi', 'experience', 'deneyim', 'kariyer', 'career'
            ]
            
            # Policy anahtar kelimeleri
            policy_keywords = [
                'poliçe', 'police', 'policy', 'sigorta', 'insurance', 'kasko', 'dask',
                'trafik', 'traffic', 'zorunlu', 'compulsory', 'hayat', 'life',
                'sağlık', 'saglik', 'health', 'seyahat', 'travel', 'konut', 'home',
                'işyeri', 'isyeri', 'workplace', 'ferdi', 'individual', 'kaza', 'accident'
            ]
            
            # CV kontrolü
            for keyword in cv_keywords:
                if keyword in file_name_lower:
                    logging.info(f"🔍 CV belgesi tespit edildi ('{keyword}' anahtar kelimesi): {file_name}")
                    return "cv"
            
            # Policy kontrolü
            for keyword in policy_keywords:
                if keyword in file_name_lower:
                    logging.info(f"🔍 Policy belgesi tespit edildi ('{keyword}' anahtar kelimesi): {file_name}")
                    return "policy"
            
            # Varsayılan olarak policy
            logging.info(f"🔍 Belge tipi tespit edilemedi, varsayılan 'policy' kullanılıyor: {file_name}")
            return "policy"
            
        except Exception as e:
            logging.error(f"Belge tipi tespit hatası: {e}")
            return "policy"  # Hata durumunda varsayılan
            
    def update_source_node(self, obj_source_node:sourceNode):
        try:

            params = {}
            if obj_source_node.file_name is not None and obj_source_node.file_name != '':
                params['fileName'] = obj_source_node.file_name

            if obj_source_node.status is not None and obj_source_node.status != '':
                params['status'] = obj_source_node.status

            if obj_source_node.created_at is not None:
                params['createdAt'] = obj_source_node.created_at

            if obj_source_node.updated_at is not None:
                params['updatedAt'] = obj_source_node.updated_at

            if obj_source_node.processing_time is not None and obj_source_node.processing_time != 0:
                params['processingTime'] = round(obj_source_node.processing_time.total_seconds(),2)

            if obj_source_node.node_count is not None :
                params['nodeCount'] = obj_source_node.node_count

            if obj_source_node.relationship_count is not None :
                params['relationshipCount'] = obj_source_node.relationship_count

            if obj_source_node.model is not None and obj_source_node.model != '':
                params['model'] = obj_source_node.model

            if obj_source_node.total_chunks is not None and obj_source_node.total_chunks != 0:
                params['total_chunks'] = obj_source_node.total_chunks

            if obj_source_node.is_cancelled is not None:
                params['is_cancelled'] = obj_source_node.is_cancelled

            if obj_source_node.processed_chunk is not None :
                params['processed_chunk'] = obj_source_node.processed_chunk
            
            if obj_source_node.retry_condition is not None :
                params['retry_condition'] = obj_source_node.retry_condition    

            param= {"props":params}
            
            logging.info(f'Base Param value 1 : {param}')
            
            # Token kullanımı için özel loglama
            if 'total_tokens' in params:
                logging.info(f"📊 Document {params.get('fileName', 'unknown')} için token kullanımı güncellendi:")
                logging.info(f"  🔢 Toplam token: {params.get('total_tokens', 0)}")
                logging.info(f"  📥 Input token: {params.get('input_tokens', 0)}")
                logging.info(f"  📤 Output token: {params.get('output_tokens', 0)}")
            
            query = "MERGE(d:Document {fileName :$props.fileName}) SET d += $props"
            logging.info("Update source node properties")
            self.graph.query(query,param,session_params={"database":self.graph._database})
        except Exception as e:
            error_message = str(e)
            self.update_exception_db(self,self.file_name,error_message)
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
        query = "MATCH(d:Document) WHERE d.fileName IS NOT NULL RETURN d ORDER BY d.updatedAt DESC"
        result = self.graph.query(query,session_params={"database":self.graph._database})
        list_of_json_objects = [entry['d'] for entry in result]
        return list_of_json_objects
        
    def update_KNN_graph(self):
        """
        Update the graph node with SIMILAR relationship where embedding scrore match
        """
        index = self.graph.query("""show indexes yield * where type = 'VECTOR' and name = 'vector'""",session_params={"database":self.graph._database})
        # logging.info(f'show index vector: {index}')
        knn_min_score = os.environ.get('KNN_MIN_SCORE')
        if len(index) > 0:
            logging.info('update KNN graph')
            self.graph.query("""MATCH (c:Chunk)
                                    WHERE c.embedding IS NOT NULL AND count { (c)-[:SIMILAR]-() } < 5
                                    CALL db.index.vector.queryNodes('vector', 6, c.embedding) yield node, score
                                    WHERE node <> c and score >= $score MERGE (c)-[rel:SIMILAR]-(node) SET rel.score = score
                                """,
                                {"score":float(knn_min_score)}
                                ,session_params={"database":self.graph._database})
        else:
            logging.info("Vector index does not exist, So KNN graph not update")

    def check_account_access(self, database):
        try:
            query_dbms_componenet = "call dbms.components() yield edition"
            result_dbms_componenet = self.graph.query(query_dbms_componenet,session_params={"database":self.graph._database})

            if  result_dbms_componenet[0]["edition"] == "enterprise":
                query = """
                SHOW USER PRIVILEGES 
                YIELD * 
                WHERE graph = $database AND action IN ['read'] 
                RETURN COUNT(*) AS readAccessCount
                """
            
                logging.info(f"Checking access for database: {database}")

                result = self.graph.query(query, params={"database": database},session_params={"database":self.graph._database})
                read_access_count = result[0]["readAccessCount"] if result else 0

                logging.info(f"Read access count: {read_access_count}")

                if read_access_count > 0:
                    logging.info("The account has read access.")
                    return False
                else:
                    logging.info("The account has write access.")
                    return True
            else:
                #Community version have no roles to execute admin command, so assuming write access as TRUE
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
            result = self.graph.query(gds_procedure_count,session_params={"database":self.graph._database})
            total_gds_procedures = result[0]['totalGdsProcedures'] if result else 0

            if total_gds_procedures > 0:
                logging.info("GDS is available in the database.")
                return True
            else:
                logging.info("GDS is not available in the database.")
                return False
        except Exception as e:
            logging.error(f"An error occurred while checking GDS version: {e}")
            return False
            
    def connection_check_and_get_vector_dimensions(self,database):
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
        
        db_vector_dimension = self.graph.query("""SHOW INDEXES YIELD *
                                    WHERE type = 'VECTOR' AND name = 'vector'
                                    RETURN options.indexConfig['vector.dimensions'] AS vector_dimensions
                                """,session_params={"database":self.graph._database})
        
        result_chunks = self.graph.query("""match (c:Chunk) return size(c.embedding) as embeddingSize, count(*) as chunks, 
                                                    count(c.embedding) as hasEmbedding
                                """,session_params={"database":self.graph._database})
        
        embedding_model = os.getenv('EMBEDDING_MODEL')
        embeddings, application_dimension = load_embedding_model(embedding_model)
        logging.info(f'embedding model:{embeddings} and dimesion:{application_dimension}')

        gds_status = self.check_gds_version()
        write_access = self.check_account_access(database=database)
        
        if self.graph:
            if len(db_vector_dimension) > 0:
                return {'db_vector_dimension': db_vector_dimension[0]['vector_dimensions'], 'application_dimension':application_dimension, 'message':"Connection Successful","gds_status":gds_status,"write_access":write_access}
            else:
                if len(db_vector_dimension) == 0 and len(result_chunks) == 0:
                    logging.info("Chunks and vector index does not exists in database")
                    return {'db_vector_dimension': 0, 'application_dimension':application_dimension, 'message':"Connection Successful","chunks_exists":False,"gds_status":gds_status,"write_access":write_access}
                elif len(db_vector_dimension) == 0 and result_chunks[0]['hasEmbedding']==0 and result_chunks[0]['chunks'] > 0:
                    return {'db_vector_dimension': 0, 'application_dimension':application_dimension, 'message':"Connection Successful","chunks_exists":True,"gds_status":gds_status,"write_access":write_access}
                else:
                    return {'message':"Connection Successful","gds_status": gds_status,"write_access":write_access}

    def execute_query(self, query, param=None, max_retries=3, delay=2):
        """
        Neo4j query'sini timeout ve connection hatalarına karşı retry mekanizması ile çalıştırır
        """
        import time
        from neo4j.exceptions import SessionExpired, ServiceUnavailable, TransientError
        
        retries = 0
        while retries < max_retries:
            try:
                return self.graph.query(query, param, session_params={"database": self.graph._database})
            except (SessionExpired, ServiceUnavailable) as e:
                retries += 1
                if retries >= max_retries:
                    logging.error(f"Neo4j bağlantı hatası - {max_retries} deneme sonrası başarısız: {str(e)}")
                    raise e
                logging.warning(f"Neo4j bağlantı hatası (deneme {retries}/{max_retries}): {str(e)}")
                logging.info(f"{delay} saniye bekleniyor...")
                time.sleep(delay)
                delay *= 2  # Exponential backoff
            except TransientError as e:
                if "DeadlockDetected" in str(e):
                    retries += 1
                    if retries >= max_retries:
                        logging.error(f"Deadlock hatası - {max_retries} deneme sonrası başarısız: {str(e)}")
                        raise e
                    logging.info(f"Deadlock detected. Retrying {retries}/{max_retries} in {delay} seconds...")
                    time.sleep(delay)
                else:
                    # Diğer TransientError'lar için de retry yap
                    retries += 1
                    if retries >= max_retries:
                        logging.error(f"Transient error - {max_retries} deneme sonrası başarısız: {str(e)}")
                        raise e
                    logging.warning(f"Transient error (deneme {retries}/{max_retries}): {str(e)}")
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
                d.chunkNodeCount AS chunkNodeCount,
                d.chunkRelCount AS chunkRelCount,
                d.entityNodeCount AS entityNodeCount,
                d.entityEntityRelCount AS entityEntityRelCount,
                d.communityNodeCount AS communityNodeCount,
                d.communityRelCount AS communityRelCount,
                d.createdAt AS created_time
                """
        param = {"file_name" : file_name}
        result = self.execute_query(query, param)
        
        # Eğer Document node bulunamazsa, otomatik olarak oluştur
        if not result or len(result) == 0:
            logging.warning(f"Document node bulunamadı: {file_name}. Otomatik olarak oluşturuluyor...")
            try:
                # Dosya bilgilerini file_name'den çıkar
                import os
                file_extension = os.path.splitext(file_name)[1].lower()
                
                # Dosya tipini uzantıdan belirle
                if file_extension in ['.pdf']:
                    file_type = 'PDF'
                elif file_extension in ['.txt']:
                    file_type = 'Text'
                elif file_extension in ['.docx', '.doc']:
                    file_type = 'Word Document'
                elif file_extension in ['.html', '.htm']:
                    file_type = 'HTML'
                elif file_extension in ['.json']:
                    file_type = 'JSON'
                elif file_extension in ['.csv']:
                    file_type = 'CSV'
                elif file_extension in ['.xml']:
                    file_type = 'XML'
                else:
                    file_type = f'Document{file_extension.upper()}'
                
                # Dosya boyutunu almaya çalış
                file_size = 0
                try:
                    if os.path.exists(file_name):
                        file_size = os.path.getsize(file_name)
                except:
                    file_size = 0
                
                # Basit bir Document node oluştur
                create_query = """
                    MERGE(d:Document {fileName: $file_name}) 
                    ON CREATE SET 
                        d.status = 'New',
                        d.fileSource = 'local file',
                        d.fileType = $file_type,
                        d.fileSize = $file_size,
                        d.createdAt = datetime(),
                        d.updatedAt = datetime(),
                        d.processingTime = 0,
                        d.nodeCount = 0,
                        d.relationshipCount = 0,
                        d.total_chunks = 0,
                        d.processed_chunk = 0,
                        d.chunkNodeCount = 0,
                        d.chunkRelCount = 0,
                        d.entityNodeCount = 0,
                        d.entityEntityRelCount = 0,
                        d.communityNodeCount = 0,
                        d.communityRelCount = 0,
                        d.is_cancelled = false
                    ON MATCH SET 
                        d.updatedAt = datetime(),
                        d.fileType = $file_type,
                        d.fileSize = $file_size
                """
                self.graph.query(create_query, {
                    "file_name": file_name,
                    "file_type": file_type,
                    "file_size": file_size
                }, session_params={"database": self.graph._database})
                logging.info(f"Document node otomatik oluşturuldu: {file_name}")
                
                # Tekrar sorgula
                result = self.execute_query(query, param)
                
            except Exception as e:
                logging.error(f"Document node oluştururken hata: {e}")
                return []
        
        return result
    
    def delete_file_from_graph(self, filenames, source_types, deleteEntities:str, merged_dir:str, uri):
        
        filename_list= list(map(str.strip, json.loads(filenames)))
        source_types_list= list(map(str.strip, json.loads(source_types)))
        gcs_file_cache = os.environ.get('GCS_FILE_CACHE')
        
        log_delete(f"Starting deletion process for {len(filename_list)} files: {filename_list}")
        log_delete(f"Delete entities mode: {deleteEntities}, Source types: {source_types_list}")
        
        for (file_name,source_type) in zip(filename_list, source_types_list):
            merged_file_path = os.path.join(merged_dir, file_name)
            if source_type == 'local file' and gcs_file_cache == 'True':
                folder_name = create_gcs_bucket_folder_name_hashed(uri, file_name)
                delete_file_from_gcs(BUCKET_UPLOAD,folder_name,file_name)
                log_delete(f"File deleted from GCS bucket: {file_name}")
            else:
                logging.info(f'Deleted File Path: {merged_file_path} and Deleted File Name : {file_name}')
                delete_uploaded_local_file(merged_file_path,file_name)
                log_delete(f"File deleted from local storage: {file_name}")
                
        query_to_delete_document="""
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
        param = {"filename_list" : filename_list, "source_types_list": source_types_list}
        community_param = {"max_level":MAX_COMMUNITY_LEVELS}
        if deleteEntities == "true":
            log_delete(f"Executing comprehensive deletion (documents + entities) for {len(filename_list)} files")
            result = self.execute_query(query_to_delete_document_and_entities, param)
            _ = self.execute_query(query_to_delete_communities,community_param)
            log_delete(f"Successfully deleted {len(filename_list)} documents with entities: {filename_list}")
            logging.info(f"Deleting {len(filename_list)} documents = '{filename_list}' from '{source_types_list}' from database")
        else :
            log_delete(f"Executing document-only deletion for {len(filename_list)} files")
            result = self.execute_query(query_to_delete_document, param)    
            log_delete(f"Successfully deleted {len(filename_list)} documents (entities preserved): {filename_list}")
            logging.info(f"Deleting {len(filename_list)} documents = '{filename_list}' from '{source_types_list}' with their entities from database")
        return len(filename_list)
    
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
    
    def delete_unconnected_nodes(self,unconnected_entities_list):
        entities_list = list(map(str.strip, json.loads(unconnected_entities_list)))
        log_delete(f"Starting deletion of {len(entities_list)} unconnected/orphan nodes")
        query = """
        MATCH (e) WHERE elementId(e) IN $elementIds
        DETACH DELETE e
        """
        param = {"elementIds":entities_list}
        result = self.execute_query(query,param)
        log_delete(f"Successfully deleted {len(entities_list)} orphan nodes from graph")
        return result
    
    def get_duplicate_nodes_list(self):
        score_value = float(os.environ.get('DUPLICATE_SCORE_VALUE'))
        text_distance = int(os.environ.get('DUPLICATE_TEXT_DISTANCE'))
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
        
        param = {"duplicate_score_value": score_value, "duplicate_text_distance" : text_distance}
        
        nodes_list = self.execute_query(query_duplicate_nodes.format(return_statement=return_query_duplicate_nodes),param=param)
        total_nodes = self.execute_query(query_duplicate_nodes.format(return_statement=total_duplicate_nodes),param=param)
        return nodes_list, total_nodes[0]
    
    def merge_duplicate_nodes(self,duplicate_nodes_list):
        nodes_list = json.loads(duplicate_nodes_list)
        logging.info(f'Nodes list to merge {nodes_list}')
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
        param = {"rows":nodes_list}
        return self.execute_query(query,param)
    
    def drop_create_vector_index(self, isVectorIndexExist):
        """
        drop and create the vector index when vector index dimesion are different.
        """
        embedding_model = os.getenv('EMBEDDING_MODEL')
        embeddings, dimension = load_embedding_model(embedding_model)
        
        if isVectorIndexExist == 'true':
            self.graph.query("""drop index vector""",session_params={"database":self.graph._database})
        
        self.graph.query("""CREATE VECTOR INDEX `vector` if not exists for (c:Chunk) on (c.embedding)
                            OPTIONS {indexConfig: {
                            `vector.dimensions`: $dimensions,
                            `vector.similarity_function`: 'cosine'
                            }}
                        """,
                        {
                            "dimensions" : dimension
                        },session_params={"database":self.graph._database}
                        )
        return "Drop and Re-Create vector index succesfully"


    def update_node_relationship_count(self,document_name):
        logging.info("updating node and relationship count")
        label_query = """CALL db.labels"""
        community_flag = {'label': '__Community__'} in self.execute_query(label_query)
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
                filename = record.get("filename",None)
                chunkNodeCount = int(record.get("chunkNodeCount",0))
                chunkRelCount = int(record.get("chunkRelCount",0))
                entityNodeCount = int(record.get("entityNodeCount",0))
                entityEntityRelCount = int(record.get("entityEntityRelCount",0))
                if (not document_name) and (community_flag):
                    communityNodeCount = int(record.get("communityNodeCount",0))
                    communityRelCount = int(record.get("communityRelCount",0))
                else:
                    communityNodeCount = 0
                    communityRelCount = 0
                nodeCount = int(chunkNodeCount) + int(entityNodeCount) + int(communityNodeCount)
                relationshipCount = int(chunkRelCount) + int(entityEntityRelCount) + int(communityRelCount)
                update_query = """
                MATCH (d:Document {fileName: $filename})
                SET d.chunkNodeCount = $chunkNodeCount,
                    d.chunkRelCount = $chunkRelCount,
                    d.entityNodeCount = $entityNodeCount,
                    d.entityEntityRelCount = $entityEntityRelCount,
                    d.communityNodeCount = $communityNodeCount,
                    d.communityRelCount = $communityRelCount,
                    d.nodeCount = $nodeCount,
                    d.relationshipCount = $relationshipCount
                """
                self.execute_query(update_query,{
                    "filename": filename,
                    "chunkNodeCount": chunkNodeCount,
                    "chunkRelCount": chunkRelCount,
                    "entityNodeCount": entityNodeCount,
                    "entityEntityRelCount": entityEntityRelCount,
                    "communityNodeCount": communityNodeCount,
                    "communityRelCount": communityRelCount,
                    "nodeCount" : nodeCount,
                    "relationshipCount" : relationshipCount
                    })
                
                response[filename] = {"chunkNodeCount": chunkNodeCount,
                    "chunkRelCount": chunkRelCount,
                    "entityNodeCount": entityNodeCount,
                    "entityEntityRelCount": entityEntityRelCount,
                    "communityNodeCount": communityNodeCount,
                    "communityRelCount": communityRelCount,
                    "nodeCount" : nodeCount,
                    "relationshipCount" : relationshipCount
                    }

        return response
    
    def get_nodelabels_relationships(self):
        node_query = """
                    CALL db.labels() YIELD label
                    WITH label
                    WHERE NOT label IN ['Document', 'Chunk', '_Bloom_Perspective_', '__Community__', '__Entity__']
                    CALL apoc.cypher.run("MATCH (n:`" + label + "`) RETURN count(n) AS count",{}) YIELD value
                    WHERE value.count > 0
                    RETURN label order by label
                    """

        relation_query = """
                CALL db.relationshipTypes() yield relationshipType
                WHERE NOT relationshipType  IN ['PART_OF', 'NEXT_CHUNK', 'HAS_ENTITY', '_Bloom_Perspective_','FIRST_CHUNK','SIMILAR','IN_COMMUNITY','PARENT_COMMUNITY'] 
                return relationshipType order by relationshipType
                """
            
        try:
            node_result = self.execute_query(node_query)
            node_labels = [record["label"] for record in node_result]
            relationship_result = self.execute_query(relation_query)
            relationship_types = [record["relationshipType"] for record in relationship_result]
            return node_labels,relationship_types
        except Exception as e:
            print(f"Error in getting node labels/relationship types from db: {e}")
            return []

    def create_policy_node_from_document(self, file_name: str):
        """
        Document file isminden Policy node oluşturur ve Policy ile Document arasında DOCUMENTED_IN ilişkisi kurar.
        ENDORSEMENT (zeyilname) belgeleri için ana poliçeyi bulup HAS_ENDORSEMENT ile bağlar.
        """
        try:
            # Dosya isminden poliçe bilgilerini çıkar
            policy_info = self.extract_policy_info_from_filename(file_name)
            
            if not policy_info:
                logging.info(f"Dosya isminden poliçe bilgisi çıkarılamadı: {file_name}")
                return
            
            doc_type = policy_info.get('document_type', 'MAIN_POLICY')
            
            # Document'a docType ve year ekle
            update_document_query = """
                MATCH (d:Document {fileName: $file_name})
                SET d.docType = $doc_type,
                    d.year = $policy_year,
                    d.updatedAt = datetime()
                RETURN d.fileName as updated_file
            """
            
            self.graph.query(update_document_query, {
                "file_name": file_name,
                "doc_type": doc_type,
                "policy_year": policy_info.get('year', '')
            }, session_params={"database": self.graph._database})
            
            # ENDORSEMENT (zeyilname) ise ana poliçeyi bul ve bağla, kendi Policy node'u oluşturma
            if doc_type == 'ENDORSEMENT':
                logging.info(f"🔗 Zeyilname tespit edildi, ana poliçe aranıyor: {file_name}")
                success = self._link_endorsement_to_main_policy(file_name, policy_info)
                if success:
                    logging.info(f"✅ Zeyilname ana poliçeye başarıyla bağlandı: {file_name}")
                else:
                    logging.warning(f"⚠️ Zeyilname ana poliçeye bağlanamadı: {file_name}")
                return  # Zeyilname için Policy node oluşturma, sadece bağlantı yap
            
            # Ana poliçe, yenileme, iptal vs. için kendi Policy node'unu oluştur
            policy_id = policy_info['policy_id']
            
            # Policy node'unu oluştur
            create_policy_query = """
                MERGE (p:Policy {id: $policy_id})
                ON CREATE SET 
                    p.name = $policy_name,
                    p.type = $policy_type,
                    p.policyNumber = $policy_number,
                    p.customer = $customer_name,
                    p.year = $year,
                    p.insuredItem = $insured_item,
                    p.createdAt = datetime(),
                    p.source_file = $filename,
                    p.extraction_method = $extraction_method
                ON MATCH SET 
                    p.updatedAt = datetime(),
                    p.source_file = $filename,
                    p.year = $year,
                    p.insuredItem = $insured_item,
                    p.extraction_method = $extraction_method,
                    p.policyNumber = $policy_number
                RETURN p.id as policy_id
            """
            
            result = self.graph.query(create_policy_query, {
                "policy_id": policy_id,
                "policy_name": policy_info.get('policy_name', policy_id),
                "policy_type": policy_info.get('policy_type', 'Insurance Policy'),
                "policy_number": policy_info.get('policy_number', ''),
                "customer_name": policy_info.get('customer_name', ''),
                "year": policy_info.get('year', ''),
                "insured_item": policy_info.get('insured_item', ''),
                "filename": file_name,
                "extraction_method": "LLM"
            }, session_params={"database": self.graph._database})
            
            if result:
                logging.info(f"Policy node oluşturuldu: {policy_id}")
                
                # Belge türüne göre farklı ilişkiler kur
                if doc_type == 'MAIN_POLICY':
                    link_query = """
                        MATCH (d:Document {fileName: $file_name})
                        MATCH (p:Policy {id: $policy_id})
                        MERGE (p)-[r:DOCUMENTED_IN]->(d)
                        SET r.created_at = datetime(),
                            r.source = 'filename_extraction'
                        RETURN count(r) as links_created
                    """
                    relationship_type = "DOCUMENTED_IN"
                elif doc_type == 'RENEWAL':
                    link_query = """
                        MATCH (d:Document {fileName: $file_name})
                        MATCH (p:Policy {id: $policy_id})
                        MERGE (p)-[r:HAS_RENEWAL]->(d)
                        SET r.created_at = datetime(),
                            r.source = 'filename_extraction'
                        RETURN count(r) as links_created
                    """
                    relationship_type = "HAS_RENEWAL"
                elif doc_type == 'CANCELLATION':
                    link_query = """
                        MATCH (d:Document {fileName: $file_name})
                        MATCH (p:Policy {id: $policy_id})
                        MERGE (p)-[r:HAS_CANCELLATION]->(d)
                        SET r.created_at = datetime(),
                            r.source = 'filename_extraction'
                        RETURN count(r) as links_created
                    """
                    relationship_type = "HAS_CANCELLATION"
                else:
                    # Fallback: varsayılan DOCUMENTED_IN
                    link_query = """
                        MATCH (d:Document {fileName: $file_name})
                        MATCH (p:Policy {id: $policy_id})
                        MERGE (p)-[r:DOCUMENTED_IN]->(d)
                        SET r.created_at = datetime(),
                            r.source = 'filename_extraction'
                        RETURN count(r) as links_created
                    """
                    relationship_type = "DOCUMENTED_IN"
                
                link_result = self.graph.query(link_query, {
                    "file_name": file_name,
                    "policy_id": policy_id
                }, session_params={"database": self.graph._database})
                
                if link_result and link_result[0]['links_created'] > 0:
                    logging.info(f"Policy-Document {relationship_type} ilişkisi oluşturuldu: {policy_id} -> {file_name}")
                else:
                    logging.warning(f"Policy-Document {relationship_type} ilişkisi oluşturulamadı: {policy_id} -> {file_name}")
                
                # Customer, PolicyYear, InsuredItem ve PolicyType node'larını oluştur
                self._create_policy_related_nodes(policy_info, policy_id, file_name)
            
        except Exception as e:
            logging.error(f"Policy node oluşturma hatası ({file_name}): {e}")

    def _create_policy_related_nodes(self, policy_info: dict, policy_id: str, file_name: str):
        """
        Policy bilgilerinden Customer, PolicyYear, InsuredItem ve PolicyType node'larını oluşturur
        """
        try:
            # Customer node oluştur
            customer_name = policy_info.get('customer_name', '').strip()
            if customer_name:
                self._create_customer_node(customer_name, policy_id, file_name)
            
            # PolicyYear node oluştur
            year = policy_info.get('year', '').strip()
            if year:
                self._create_policy_year_node(year, policy_id)
            
            # InsuredItem node oluştur
            insured_item = policy_info.get('insured_item', '').strip()
            if insured_item:
                self._create_insured_item_node(insured_item, policy_id)
            
            # PolicyType node oluştur
            policy_type = policy_info.get('policy_type', '').strip()
            if policy_type:
                self._create_policy_type_node(policy_type, policy_id)
                
        except Exception as e:
            logging.error(f"Policy related node'ları oluşturma hatası: {e}")

    def _create_customer_node(self, customer_name: str, policy_id: str, file_name: str):
        """Customer node oluşturur ve ilişkilendirir"""
        try:
            # Customer node oluştur veya güncelle
            create_customer_query = """
                MERGE (c:Customer {name: $customer_name})
                ON CREATE SET 
                    c.createdAt = datetime(),
                    c.fullName = $customer_name,
                    c.policyCount = 1
                ON MATCH SET 
                    c.updatedAt = datetime(),
                    c.policyCount = c.policyCount + 1
                RETURN c.name as customer_name
            """
            
            result = self.graph.query(create_customer_query, {
                "customer_name": customer_name
            }, session_params={"database": self.graph._database})
            
            if result:
                logging.info(f"Customer node oluşturuldu/güncellendi: {customer_name}")
                
                # Customer -> Document HAS_DOC ilişkisi
                customer_doc_query = """
                    MATCH (c:Customer {name: $customer_name})
                    MATCH (d:Document {fileName: $file_name})
                    MERGE (c)-[r:HAS_DOC]->(d)
                    SET r.created_at = datetime()
                    RETURN count(r) as links_created
                """
                
                self.graph.query(customer_doc_query, {
                    "customer_name": customer_name,
                    "file_name": file_name
                }, session_params={"database": self.graph._database})
                
                # Customer -> Policy HAS_POLICY ilişkisi
                customer_policy_query = """
                    MATCH (c:Customer {name: $customer_name})
                    MATCH (p:Policy {id: $policy_id})
                    MERGE (c)-[r:HAS_POLICY]->(p)
                    SET r.created_at = datetime()
                    RETURN count(r) as links_created
                """
                
                self.graph.query(customer_policy_query, {
                    "customer_name": customer_name,
                    "policy_id": policy_id
                }, session_params={"database": self.graph._database})
                
                logging.info(f"Customer ilişkileri oluşturuldu: {customer_name}")
                
        except Exception as e:
            logging.error(f"Customer node oluşturma hatası: {e}")

    def _create_policy_year_node(self, year: str, policy_id: str):
        """PolicyYear node oluşturur ve ilişkilendirir"""
        try:
            # PolicyYear node oluştur (sadece bir kez)
            create_year_query = """
                MERGE (py:PolicyYear {name: $year})
                ON CREATE SET 
                    py.year = toInteger($year),
                    py.createdAt = datetime(),
                    py.policyCount = 1
                ON MATCH SET 
                    py.updatedAt = datetime(),
                    py.policyCount = py.policyCount + 1
                RETURN py.name as year_name
            """
            
            result = self.graph.query(create_year_query, {
                "year": year
            }, session_params={"database": self.graph._database})
            
            if result:
                logging.info(f"PolicyYear node oluşturuldu/güncellendi: {year}")
                
                # Policy -> PolicyYear HAS_YEAR ilişkisi
                policy_year_query = """
                    MATCH (p:Policy {id: $policy_id})
                    MATCH (py:PolicyYear {name: $year})
                    MERGE (p)-[r:HAS_YEAR]->(py)
                    SET r.created_at = datetime()
                    RETURN count(r) as links_created
                """
                
                self.graph.query(policy_year_query, {
                    "policy_id": policy_id,
                    "year": year
                }, session_params={"database": self.graph._database})
                
                logging.info(f"Policy-PolicyYear ilişkisi oluşturuldu: {policy_id} -> {year}")
                
        except Exception as e:
            logging.error(f"PolicyYear node oluşturma hatası: {e}")

    def _create_insured_item_node(self, insured_item: str, policy_id: str):
        """InsuredItem node oluşturur ve ilişkilendirir"""
        try:
            # InsuredItem node oluştur
            create_item_query = """
                MERGE (ii:InsuredItem {name: $insured_item})
                ON CREATE SET 
                    ii.description = $insured_item,
                    ii.createdAt = datetime(),
                    ii.policyCount = 1
                ON MATCH SET 
                    ii.updatedAt = datetime(),
                    ii.policyCount = ii.policyCount + 1
                RETURN ii.name as item_name
            """
            
            result = self.graph.query(create_item_query, {
                "insured_item": insured_item
            }, session_params={"database": self.graph._database})
            
            if result:
                logging.info(f"InsuredItem node oluşturuldu/güncellendi: {insured_item}")
                
                # Policy -> InsuredItem HAS_INSURED_ITEM ilişkisi
                policy_item_query = """
                    MATCH (p:Policy {id: $policy_id})
                    MATCH (ii:InsuredItem {name: $insured_item})
                    MERGE (p)-[r:HAS_INSURED_ITEM]->(ii)
                    SET r.created_at = datetime()
                    RETURN count(r) as links_created
                """
                
                self.graph.query(policy_item_query, {
                    "policy_id": policy_id,
                    "insured_item": insured_item
                }, session_params={"database": self.graph._database})
                
                logging.info(f"Policy-InsuredItem ilişkisi oluşturuldu: {policy_id} -> {insured_item}")
                
        except Exception as e:
            logging.error(f"InsuredItem node oluşturma hatası: {e}")

    def _create_policy_type_node(self, policy_type: str, policy_id: str):
        """PolicyType node oluşturur ve ilişkilendirir"""
        try:
            # PolicyType node oluştur
            create_type_query = """
                MERGE (pt:PolicyType {name: $policy_type})
                ON CREATE SET 
                    pt.typeName = $policy_type,
                    pt.createdAt = datetime(),
                    pt.policyCount = 1
                ON MATCH SET 
                    pt.updatedAt = datetime(),
                    pt.policyCount = pt.policyCount + 1
                RETURN pt.name as type_name
            """
            
            result = self.graph.query(create_type_query, {
                "policy_type": policy_type
            }, session_params={"database": self.graph._database})
            
            if result:
                logging.info(f"PolicyType node oluşturuldu/güncellendi: {policy_type}")
                
                # Policy -> PolicyType HAS_TYPE ilişkisi
                policy_type_query = """
                    MATCH (p:Policy {id: $policy_id})
                    MATCH (pt:PolicyType {name: $policy_type})
                    MERGE (p)-[r:HAS_TYPE]->(pt)
                    SET r.created_at = datetime()
                    RETURN count(r) as links_created
                """
                
                self.graph.query(policy_type_query, {
                    "policy_id": policy_id,
                    "policy_type": policy_type
                }, session_params={"database": self.graph._database})
                
                logging.info(f"Policy-PolicyType ilişkisi oluşturuldu: {policy_id} -> {policy_type}")
                
        except Exception as e:
            logging.error(f"PolicyType node oluşturma hatası: {e}")

    def extract_policy_info_from_filename(self, file_name: str) -> dict:
        """
        LLM kullanarak önce dosya isminden, başarısız olursa poliçe görselinden bilgileri çıkarır.
        
        Örnek: "Ayça Dinçkök Galata Residance D6 Konut 2020.pdf"
        """
        import os
        import json
        
        try:
            # Dosya uzantısını kaldır
            base_name = os.path.splitext(file_name)[0]
            
            # Turkish karakterleri normalize et
            from src.utf8_utils import normalize_unicode_text
            base_name = normalize_unicode_text(base_name)
            
            logging.info(f"📝 Dosya isminden poliçe bilgisi çıkarma denemesi: {file_name}")
            
            try:
                # İlk olarak dosya isminden LLM ile çıkarma dene
                policy_info = self._extract_policy_info_with_llm(base_name)
                
                if policy_info and policy_info.get('customer_name'):
                    # Policy ID'yi oluştur
                    policy_id = base_name.strip()
                    policy_info['policy_id'] = policy_id
                    policy_info['policy_name'] = policy_id
                    policy_info['extraction_method'] = 'filename'
                    
                    # Zorunlu alanları kontrol et: sadece customer_name ve document_type
                    required_fields = ['customer_name', 'document_type']
                    missing_fields = []
                    
                    for field in required_fields:
                        if not policy_info.get(field, '').strip():
                            missing_fields.append(field)
                    
                    if missing_fields:
                        logging.info(f"📝 Dosya isminden çıkarıldı ama eksik alanlar var: {missing_fields}")
                        # Eksik alanlar için görsel analizi yap
                    else:
                        logging.info(f"✅ Dosya isminden LLM ile tam çıkarım başarılı ama görsel analizi de yapılacak: {policy_info}")
                        # Tam çıkarım başarılı olsa da, görsel analizini yap (daha detaylı ve güvenilir bilgi için)
                else:
                    logging.info(f"📝 Dosya isminden çıkarım başarısız veya eksik")
                    policy_info = {}  # Boş dict, image extraction için
                    
            except Exception as filename_error:
                logging.warning(f"⚠️ Dosya isminden çıkarma başarısız: {filename_error}")
            
            # Dosya isminden başarısız olduysa veya eksik alanlar varsa, görsel analizi dene
            logging.info(f"🖼️ Poliçe görselinden eksik bilgileri tamamlama denemesi: {file_name}")
            
            # İlk sayfa görsel yolunu al
            first_page_path = self._get_first_page_image_path(file_name)
            
            if first_page_path:
                # Görseldan LLM ile çıkarma dene
                image_policy_info = self._extract_policy_info_from_image(first_page_path, file_name)
                
                if image_policy_info and not image_policy_info.get('extraction_failed'):
                    # Dosya isminden çıkarılan bilgiler varsa, dosya ismi bilgilerini öncelikli tut
                    if policy_info and policy_info.get('customer_name'):
                        logging.info(f"📝 Dosya isminden mevcut bilgiler: {policy_info}")
                        logging.info(f"🖼️ Görseldan çıkarılan bilgiler: {image_policy_info}")
                        
                        # Dosya ismi bilgilerini öncelikli tut, görsel bilgileri ile tamamla
                        final_info = policy_info.copy()
                        
                        # Policy number'ı mutlaka görseldan al (dosya isminde aranmaz)
                        if image_policy_info.get('policy_number', '').strip():
                            final_info['policy_number'] = image_policy_info['policy_number']
                            logging.info(f"✅ Policy number görseldan alındı: {image_policy_info['policy_number']}")
                        
                        # Dosya isminde eksik olan diğer alanları görseldan tamamla
                        image_fields = ['policy_type', 'year', 'document_type', 'insured_item']
                        for field in image_fields:
                            if not final_info.get(field, '').strip() and image_policy_info.get(field, '').strip():
                                final_info[field] = image_policy_info[field]
                                logging.info(f"✅ Eksik alan görseldan tamamlandı - {field}: {image_policy_info[field]}")
                        
                        # Extraction method'u güncelle
                        final_info['extraction_method'] = 'filename+image'
                        
                        # Hala eksik olan önemli alanları default değerlerle doldur
                        if not final_info.get('policy_type', '').strip():
                            final_info['policy_type'] = 'Sigorta Poliçesi'
                            logging.info(f"⚙️ Policy type default atandı: {final_info['policy_type']}")
                        
                        if not final_info.get('document_type', '').strip():
                            final_info['document_type'] = 'MAIN_POLICY'
                            logging.info(f"⚙️ Document type default atandı: {final_info['document_type']}")
                        
                        if not final_info.get('year', '').strip():
                            final_info['year'] = '2024'
                            logging.info(f"⚙️ Year default atandı: {final_info['year']}")
                        
                        logging.info(f"✅ Final bilgiler (dosya ismi öncelikli + görsel tamamlama): {final_info}")
                        return final_info
                    else:
                        # Dosya isminden hiç bilgi çıkarılamamışsa, görsel bilgilerini kullan
                        if image_policy_info.get('customer_name'):
                            # Policy ID'yi oluştur
                            policy_id = base_name.strip()
                            image_policy_info['policy_id'] = policy_id
                            image_policy_info['policy_name'] = policy_id
                            image_policy_info['extraction_method'] = 'image_vision'
                            
                            logging.info(f"✅ Görseldan Vision LLM ile başarıyla çıkarıldı: {image_policy_info}")
                            return image_policy_info
                else:
                    logging.warning(f"⚠️ Görseldan çıkarma başarısız veya eksik bilgi")
            else:
                logging.warning(f"⚠️ İlk sayfa görseli bulunamadı: {file_name}")
            
            # Her iki yöntem de başarısız olduysa, fallback bilgileri oluştur
            logging.warning(f"⚠️ Hem dosya ismi hem görsel analizi başarısız, fallback bilgiler oluşturuluyor")
            
            # Dosya isminden en azından customer_name çıkarmaya çalış
            fallback_info = {
                'policy_id': base_name.strip(),
                'policy_name': base_name.strip(),
                'customer_name': base_name.strip(),  # Fallback: file name as customer
                'policy_type': 'Sigorta Poliçesi',
                'document_type': 'MAIN_POLICY',
                'extraction_method': 'fallback'
            }
            
            logging.info(f"⚙️ Fallback bilgiler oluşturuldu: {fallback_info}")
            return fallback_info
            
        except Exception as e:
            error_msg = f"Poliçe bilgisi çıkarma hatası ({file_name}): {e}"
            logging.error(error_msg)
            raise Exception(error_msg)

    def _extract_policy_info_with_llm(self, file_name: str) -> dict:
        """
        LLM kullanarak dosya isminden poliçe bilgilerini çıkarır.
        """
        try:
            from src.llm import get_llm
            
            # Sistem mevcut get_llm metodunu kullan
            llm, _ = get_llm('openai_gpt_4o_mini')
            
            # Prompt oluştur
            prompt = f"""
Verilen dosya isminden sigorta poliçesi bilgilerini çıkar ve JSON formatında döndür.

Dosya ismi: "{file_name}"

Çıkarılacak bilgiler (ZORUNLU alanlar işaretli):
- customer_name: Müşteri ismi (ad soyad veya kurum ismi) - ZORUNLU (dosya isminde net olarak varsa)
- year: Poliçe yılı - ZORUNLU (dosya isminde açıkça belirtilmişse, yoksa boş bırak)
- policy_type: Poliçe türü (Konut, DASK, Kasko, Trafik, Sağlık, Hayat, Ortak Alan, vb.) - ZORUNLU (dosya isminde belirtilmişse)
- insured_item: Sigortalanan eşya/konum (ev adresi, araç, vb.) - (varsa, net olarak belirtilmişse)
- policy_number: Poliçe numarası (dosya isminde yoksa boş bırak)
- renewal_number: Yenileme/ana poliçe numarası (zeyilnameler için, varsa)
- document_type: Belge türü (MAIN_POLICY, ENDORSEMENT, RENEWAL, CANCELLATION) - ZORUNLU

Poliçe türü belirleme kuralları:
- "Konut", "Residence", "Apartman" → "Konut Sigortası"
- "DASK", "Deprem" → "DASK Sigortası"
- "Kasko" → "Kasko Sigortası"
- "Trafik" → "Trafik Sigortası"
- "Ortak Alan", "Ortak", "Sitesi" → "Ortak Alan Sigortası"
- "Sağlık", "Health" → "Sağlık Sigortası"
- "Hayat", "Life" → "Hayat Sigortası"
- Belirtilmemişse → "Sigorta Poliçesi"

Belge türü belirleme kuralları (ÖNEMLİ - Kesin uygula):
- ENDORSEMENT: Zeyilname/Ek belge (dosya isminde şu kelimeler varsa MUTLAKA ENDORSEMENT): 
  * "zeyilname", "zeyl", "zeyli", "zeyil"
  * "ek", "ilave", "lave", "eklem"
  * "tadilat", "değişiklik", "düzeltme"
  * "teminat", "endorsement", "addendum"
  * "YMM", "İlave Zeyli", "Ek Teminat"
- RENEWAL: Yenileme (dosya isminde "yenileme", "renewal", "galileme" varsa)
- CANCELLATION: İptal (dosya isminde "iptal", "fesih", "cancellation" varsa)
- MAIN_POLICY: Ana poliçe (yukarıdaki hiçbiri yoksa)

Örnekler:
- "Ayça Dinçkök Galata Residance D6 Konut 2020.pdf" → customer_name: "Ayça Dinçkök", year: "2020", policy_type: "Konut Sigortası", insured_item: "Galata Residance D6", document_type: "MAIN_POLICY"
- "Mehmet Yılmaz BMW X5 Kasko Zeyilname 2023.pdf" → customer_name: "Mehmet Yılmaz", year: "2023", policy_type: "Kasko Sigortası", insured_item: "BMW X5", document_type: "ENDORSEMENT"
- "Asude Sitesi Yönetimi Ortak Alan Poliçesi.pdf" → customer_name: "Asude Sitesi Yönetimi", policy_type: "Ortak Alan Sigortası", insured_item: "Asude Sitesi", document_type: "MAIN_POLICY"

UYARI: 
- Dosya isminde NET OLARAK belirtilmeyen bilgileri UYDURMA
- Emin olmadığın alanları boş bırak
- Sadece dosya isminde AÇIKÇA görünen bilgileri çıkar
- ZORUNLU alanlar (customer_name, document_type) dosya isminde çıkarılamazsa boş JSON döndür

Sadece JSON formatında yanıt ver, başka açıklama ekleme:
{{
    "customer_name": "...",
    "year": "...",
    "policy_type": "...",
    "insured_item": "...",
    "policy_number": "...",
    "renewal_number": "...",
    "document_type": "..."
}}
"""
            
            # LLM'den yanıt al
            response = llm.invoke(prompt)
            response_text = response.content.strip()
            
            # JSON parse et
            try:
                # JSON kısmını ayıkla
                if '{' in response_text and '}' in response_text:
                    start_idx = response_text.find('{')
                    end_idx = response_text.rfind('}') + 1
                    json_text = response_text[start_idx:end_idx]
                    policy_info = json.loads(json_text)
                    
                    # Boş değerleri temizle ve UTF-8 normalize et
                    cleaned_info = {}
                    for key, value in policy_info.items():
                        if value and value.strip() and value.strip() != "...":
                            # UTF-8 normalizasyon uygula
                            from src.utf8_utils import normalize_unicode_text
                            normalized_value = normalize_unicode_text(value.strip())
                            cleaned_info[key] = normalized_value
                    
                    logging.info(f"✅ LLM başarıyla poliçe bilgilerini çıkardı (UTF-8 normalized): {cleaned_info}")
                    
                    # Zorunlu alanları kontrol et: sadece customer_name ve document_type (diğerleri varsa çıkar, yoksa boş)
                    required_fields = ['customer_name', 'document_type']
                    missing_fields = []
                    
                    for field in required_fields:
                        if not cleaned_info.get(field, '').strip():
                            missing_fields.append(field)
                    
                    if missing_fields:
                        logging.warning(f"LLM zorunlu alanları çıkaramadı - Eksik alanlar: {missing_fields}")
                        return {}  # Boş dict döndür, üst seviyede image extraction yapılacak
                    
                    return cleaned_info
                else:
                    error_msg = "LLM yanıtında JSON formatı bulunamadı"
                    logging.error(error_msg)
                    logging.error(f"LLM yanıtı: {response_text}")
                    raise ValueError(error_msg)
                    
            except json.JSONDecodeError as e:
                error_msg = f"LLM yanıtı JSON parse edilemedi: {e}"
                logging.error(error_msg)
                logging.error(f"LLM yanıtı: {response_text}")
                raise ValueError(error_msg)
                
        except Exception as e:
            error_msg = f"LLM ile poliçe bilgisi çıkarma hatası: {e}"
            logging.error(error_msg)
            raise Exception(error_msg)

    def _extract_policy_info_from_image(self, image_path: str, file_name: str) -> dict:
        """
        LLM kullanarak poliçe sayfa görselinden poliçe bilgilerini çıkarır.
        """
        try:
            from src.llm import get_llm
            import base64
            import os
            import requests
            import urllib.parse
            
            # Vision model kullan
            llm, _ = get_llm('openai_gpt_4o_mini')
            
            # Image'ı base64'e çevir
            image_base64 = None
            
            # Önce local dosya sisteminde dene
            if os.path.exists(image_path):
                with open(image_path, "rb") as image_file:
                    image_base64 = base64.b64encode(image_file.read()).decode('utf-8')
                logging.info(f"Resim local dosya sisteminden okundu: {image_path}")
            else:
                # Local dosya bulunamazsa, images endpoint'ini kullan
                try:
                    # Image path'den dosya adını çıkar
                    image_filename = os.path.basename(image_path)
                    # URL encode et
                    encoded_image_name = urllib.parse.quote(image_filename, safe='')
                    # Images endpoint URL'i oluştur
                    base_url = os.getenv("BASE_URL", "http://localhost:8000")
                    image_url = f"{base_url}/images/{encoded_image_name}"
                    
                    # HTTP isteği ile resmi al
                    response = requests.get(image_url, timeout=30)
                    if response.status_code == 200:
                        image_base64 = base64.b64encode(response.content).decode('utf-8')
                        logging.info(f"Resim images endpoint'inden okundu: {image_url}")
                    else:
                        logging.error(f"Images endpoint'den resim alınamadı: {image_url} (Status: {response.status_code})")
                        return {}
                except Exception as e:
                    logging.error(f"Images endpoint'den resim okuma hatası: {e}")
                    return {}
            
            if not image_base64:
                logging.error(f"Resim okunamadı: {image_path}")
                return {}
            
            # Prompt oluştur
            prompt = f"""
Bu bir sigorta poliçesi belgesinin ilk sayfasıdır. Görüntüden poliçe bilgilerini çıkar ve JSON formatında döndür.

Dosya ismi referansı: "{file_name}"

Çıkarılacak bilgiler (ZORUNLU alanlar işaretli):
- customer_name: Poliçe sahibinin tam ismi (ad soyad veya kurum ismi) - ZORUNLU
- year: Poliçe yılı (Tanzim tarihi, Başlangıç tarihi, Başlama tarihi, Yürürlük tarihi'nden çıkar - sadece yılı al) - ZORUNLU
- policy_type: Poliçe türü (Konut, DASK, Kasko, Trafik, Sağlık, Hayat, Ortak Alan Sigortası, vb.) - ZORUNLU
- insured_item: Sigortalanan eşya/konum (ev adresi, araç plakası/modeli, vb.) - ZORUNLU
- policy_number: Poliçe numarası - ZORUNLU (belgede mutlaka bulunur, "Poliçe No", "Policy No", "Poliçe Numarası" gibi alanları ara)
- renewal_number: Yenileme/ana poliçe numarası (zeyilnameler için, varsa)
- document_type: Belge türü (MAIN_POLICY, ENDORSEMENT, RENEWAL, CANCELLATION) - ZORUNLU

ÖNEMLİ - Year (Yıl) Çıkarımı İçin:
- "Tanzim Tarihi", "Başlangıç Tarihi", "Başlama Tarihi", "Yürürlük Tarihi", "Poliçe Başlangıcı" gibi alanları ara
- Bu tarihlerden sadece YIL kısmını al (örn: 15.03.2023 tarihinden sadece "2023")
- Doğum tarihi, kayıt tarihi gibi kişisel tarihleri kullanma
- Belge üzerinde birden fazla tarih varsa, poliçe başlangıç/tanzim tarihini öncelikle

Poliçe türü belirleme kuralları:
- Konut/Residence/Apartman sigortası → "Konut Sigortası"
- DASK/Deprem sigortası → "DASK Sigortası"
- Kasko sigortası → "Kasko Sigortası"
- Trafik sigortası → "Trafik Sigortası"
- Ortak Alan/Site sigortası → "Ortak Alan Sigortası"
- Sağlık sigortası → "Sağlık Sigortası"
- Hayat sigortası → "Hayat Sigortası"
- Belirsizse → "Sigorta Poliçesi"

Belge türü belirleme:
- Ana poliçe belgesi ise → "MAIN_POLICY"
- Zeyilname/Ek/Tadilat ise → "ENDORSEMENT"
- Yenileme belgesi ise → "RENEWAL"
- İptal/Fesih belgesi ise → "CANCELLATION"

Metin NET OKUNMUYORSA veya ZORUNLU alanlar (customer_name, year, policy_type, insured_item, policy_number, document_type) çıkarılamazsa, boş bir JSON döndür: {{"extraction_failed": true}}

UYARI: policy_number çıkarılamazsa extraction_failed: true döndür.

Sadece JSON formatında yanıt ver, başka açıklama ekleme:
{{
    "customer_name": "...",
    "year": "...",
    "policy_type": "...",
    "insured_item": "...",
    "policy_number": "...",
    "renewal_number": "...",
    "document_type": "..."
}}
"""
            
            # Vision API çağrısı
            from langchain_core.messages import HumanMessage
            
            message = HumanMessage(
                content=[
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{image_base64}"
                        }
                    }
                ]
            )
            
            response = llm.invoke([message])
            response_text = response.content.strip()
            
            # JSON parse et
            try:
                if '{' in response_text and '}' in response_text:
                    start_idx = response_text.find('{')
                    end_idx = response_text.rfind('}') + 1
                    json_text = response_text[start_idx:end_idx]
                    policy_info = json.loads(json_text)
                    
                    # Extraction failed kontrolü
                    if policy_info.get('extraction_failed'):
                        logging.warning(f"Vision LLM görüntüden bilgi çıkaramadı: {image_path}")
                        return {}
                    
                    # Boş değerleri temizle ve UTF-8 normalize et
                    cleaned_info = {}
                    for key, value in policy_info.items():
                        if value and value.strip() and value.strip() != "...":
                            from src.utf8_utils import normalize_unicode_text
                            normalized_value = normalize_unicode_text(value.strip())
                            cleaned_info[key] = normalized_value
                    
                    # Zorunlu alanları kontrol et: customer_name, policy_type, year, document_type, policy_number
                    required_fields = ['customer_name', 'policy_type', 'year', 'document_type', 'policy_number']
                    missing_fields = []
                    
                    for field in required_fields:
                        if not cleaned_info.get(field, '').strip():
                            missing_fields.append(field)
                    
                    if missing_fields:
                        logging.warning(f"Vision LLM zorunlu alanları çıkaramadı - Eksik alanlar: {missing_fields}")
                        return {}  # Boş dict döndür
                    
                    logging.info(f"✅ Vision LLM başarıyla poliçe bilgilerini çıkardı: {cleaned_info}")
                    return cleaned_info
                else:
                    logging.error(f"Vision LLM yanıtında JSON formatı bulunamadı: {response_text}")
                    return {}
                    
            except json.JSONDecodeError as e:
                logging.error(f"Vision LLM yanıtı JSON parse edilemedi: {e}")
                return {}
                
        except Exception as e:
            logging.error(f"Vision LLM ile poliçe bilgisi çıkarma hatası: {e}")
            return {}

    def _get_first_page_image_path(self, file_name: str) -> str:
        """
        Document node'dan ilk sayfa görsel dosyasının yolunu alır.
        Local dosya yoksa images endpoint için dosya adını döndürür.
        """
        try:
            # Document node'dan page_images listesini al
            query = """
                MATCH (d:Document {fileName: $file_name}) 
                RETURN d.page_images AS page_images
            """
            
            result = self.execute_query(query, {"file_name": file_name})
            
            if result and len(result) > 0 and result[0].get('page_images'):
                page_images = result[0]['page_images']
                if isinstance(page_images, list) and len(page_images) > 0:
                    first_page_path = page_images[0]
                    # Path'in var olduğunu kontrol et
                    import os
                    if os.path.exists(first_page_path):
                        logging.info(f"İlk sayfa görsel dosyası bulundu: {first_page_path}")
                        return first_page_path
                    else:
                        # Local dosya yoksa, images endpoint için dosya adını döndür
                        # Bu durumda path sadece dosya adı olacak (S3'ten)
                        logging.info(f"İlk sayfa görsel dosyası local'da yok, images endpoint kullanılacak: {first_page_path}")
                        return first_page_path
                        
            logging.warning(f"Document için page_images bulunamadı: {file_name}")
            return None
            
        except Exception as e:
            logging.error(f"İlk sayfa görsel yolu alma hatası: {e}")
            return None

    def get_websource_url(self,file_name):
        logging.info("Checking if same title with different URL exist in db ")
        query = """
                MATCH(d:Document {fileName : $file_name}) WHERE d.fileSource = "web-url" 
                RETURN d.url AS url
                """
        param = {"file_name" : file_name}
        return self.execute_query(query, param)

    def update_token_usage(self, file_name: str, total_tokens: int, input_tokens: int = 0, output_tokens: int = 0, processing_time: float = 0):
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
            
            result = self.execute_query(query, {
                "file_name": file_name,
                "total_tokens": total_tokens,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "processing_time": processing_time
            })
            
            if result:
                logging.info(f"✅ Token bilgileri başarıyla kaydedildi: {total_tokens} token")
                logging.info(f"📊 Token detayları - Input: {input_tokens}, Output: {output_tokens}")
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
            policy_number = policy_info.get('policy_number', '').strip()
            renewal_number = policy_info.get('renewal_number', '').strip()
            customer_name = policy_info.get('customer_name', '').strip()
            
            logging.info(f"🔗 Zeyilname ana poliçe bağlantısı aranıyor: {file_name}")
            logging.info(f"   Policy Number: {policy_number}")
            logging.info(f"   Renewal Number: {renewal_number}")
            logging.info(f"   Customer: {customer_name}")
            
            # Ana poliçeyi bulma stratejileri (öncelik sırasıyla)
            main_policy = None
            
            # 1. Policy Number ile ara
            if policy_number:
                find_policy_query = """
                    MATCH (p:Policy)
                    WHERE p.policyNumber = $policy_number
                    RETURN p.id as policy_id, p.name as policy_name, p.policyNumber as policy_number
                    LIMIT 1
                """
                result = self.graph.query(find_policy_query, {
                    "policy_number": policy_number
                }, session_params={"database": self.graph._database})
                
                if result:
                    main_policy = result[0]
                    logging.info(f"✅ Policy Number ile ana poliçe bulundu: {main_policy['policy_id']}")
            
            # 2. Renewal Number ile ara (eğer policy number ile bulunamadıysa)
            if not main_policy and renewal_number:
                find_policy_query = """
                    MATCH (p:Policy)
                    WHERE p.policyNumber = $renewal_number OR p.id CONTAINS $renewal_number
                    RETURN p.id as policy_id, p.name as policy_name, p.policyNumber as policy_number
                    LIMIT 1
                """
                result = self.graph.query(find_policy_query, {
                    "renewal_number": renewal_number
                }, session_params={"database": self.graph._database})
                
                if result:
                    main_policy = result[0]
                    logging.info(f"✅ Renewal Number ile ana poliçe bulundu: {main_policy['policy_id']}")
            
            # 3. Customer name ve benzer policy pattern ile ara (son çare)
            if not main_policy and customer_name:
                find_policy_query = """
                    MATCH (c:Customer {name: $customer_name})-[:HAS_POLICY]->(p:Policy)
                    WHERE NOT EXISTS {
                        (p)-[:HAS_ENDORSEMENT]->(:Document)
                    }
                    RETURN p.id as policy_id, p.name as policy_name, p.policyNumber as policy_number
                    ORDER BY p.createdAt DESC
                    LIMIT 1
                """
                result = self.graph.query(find_policy_query, {
                    "customer_name": customer_name
                }, session_params={"database": self.graph._database})
                
                if result:
                    main_policy = result[0]
                    logging.info(f"✅ Customer pattern ile ana poliçe bulundu: {main_policy['policy_id']}")
            
            if main_policy:
                # Ana poliçe bulundu, zeyilnameyi bağla
                link_endorsement_query = """
                    MATCH (d:Document {fileName: $file_name})
                    MATCH (p:Policy {id: $main_policy_id})
                    MERGE (p)-[r:HAS_ENDORSEMENT]->(d)
                    SET r.created_at = datetime(),
                        r.source = 'endorsement_linking',
                        r.endorsement_type = 'automatic_link'
                    RETURN count(r) as links_created
                """
                
                link_result = self.graph.query(link_endorsement_query, {
                    "file_name": file_name,
                    "main_policy_id": main_policy['policy_id']
                }, session_params={"database": self.graph._database})
                
                if link_result and link_result[0]['links_created'] > 0:
                    logging.info(f"✅ Zeyilname ana poliçeye bağlandı: {main_policy['policy_id']} -> {file_name}")
                    
                    # Document'a endorsement bilgisi ve year ekle
                    update_document_query = """
                        MATCH (d:Document {fileName: $file_name})
                        SET d.docType = 'ENDORSEMENT',
                            d.linkedMainPolicy = $main_policy_id,
                            d.year = $policy_year,
                            d.updatedAt = datetime()
                        RETURN d.fileName as updated_file
                    """
                    
                    self.graph.query(update_document_query, {
                        "file_name": file_name,
                        "main_policy_id": main_policy['policy_id'],
                        "policy_year": policy_info.get('year', '')
                    }, session_params={"database": self.graph._database})
                    
                    return True
                else:
                    logging.warning(f"⚠️ Zeyilname bağlantısı oluşturulamadı: {file_name}")
                    return False
            else:
                logging.warning(f"⚠️ Ana poliçe bulunamadı. Zeyilname bağımsız kalacak: {file_name}")
                logging.warning(f"   Aranan kriteler - Policy Number: {policy_number}, Renewal: {renewal_number}, Customer: {customer_name}")
                return False
                
        except Exception as e:
            logging.error(f"❌ Zeyilname ana poliçe bağlantı hatası ({file_name}): {e}")
            return False

    async def extract_cv_info_from_document(self, file_path: str, model_name: str = "openai_gpt_4o_mini", text_content: str = None) -> dict:
        """
        LLM kullanarak yerel dosyadan veya verilen text content'den CV bilgilerini çıkarır.

        Args:
            file_path: CV dosyasının tam yolu
            model_name: Kullanılacak LLM modeli
            text_content: Önceden extract edilmiş text content (opsiyonel)

        Returns:
            CV metadatalarını içeren dict
        """
        import json
        import os
        from pathlib import Path
        from PyPDF2 import PdfReader
        from src.llm import get_llm

        try:
            logging.info(f"📄 CV extraction başlıyor: {file_path}")

            # Eğer text_content verilmişse direkt kullan
            if text_content:
                logging.info(f"📝 Önceden extract edilmiş text content kullanılıyor: {len(text_content)} karakter")
                return await self.extract_cv_info_from_text(text_content, os.path.basename(file_path), model_name)

            # text_content yoksa dosya varlığını kontrol et
            logging.info(f"📁 text_content verilmedi, dosyadan okuma yapılacak: {file_path}")
            if not os.path.exists(file_path):
                raise FileNotFoundError(f"CV dosyası bulunamadı: {file_path}")

            # Dosya uzantısını kontrol et
            file_extension = Path(file_path).suffix.lower()
            if file_extension != '.pdf':
                raise ValueError(f"Sadece PDF dosyaları desteklenir. Dosya: {file_path}")

            # PDF içeriğini oku
            extracted_text_content = ""
            try:
                reader = PdfReader(file_path)
                for page in reader.pages:
                    extracted_text_content += page.extract_text() + "\n"
            except Exception as e:
                logging.error(f"PDF okuma hatası: {e}")
                raise Exception(f"PDF içeriği okunamadı: {e}")

            if not extracted_text_content.strip():
                raise Exception("PDF'den metin çıkarılamadı")

            logging.info(f"📝 PDF metni çıkarıldı: {len(extracted_text_content)} karakter")

            # Text content'i extract_cv_info_from_text fonksiyonuna gönder
            return await self.extract_cv_info_from_text(extracted_text_content, os.path.basename(file_path), model_name)

        except Exception as e:
            error_msg = f"CV bilgi çıkarma hatası: {e}"
            logging.error(error_msg)
            raise Exception(error_msg)

    async def extract_cv_info_from_text(self, text_content: str, file_name: str = "unknown", model_name: str = "openai_gpt_4o_mini") -> dict:
        """
        LLM kullanarak verilen text içeriğinden CV bilgilerini çıkarır.
        Domain-agnostic hibrit yaklaşım kullanır.

        Args:
            text_content: CV'nin text içeriği
            file_name: Dosya adı (logging için)
            model_name: Kullanılacak LLM modeli

        Returns:
            CV metadatalarını içeren dict
        """
        import json
        import time
        from src.llm import get_llm

        try:
            logging.info(f"📄 CV extraction başlıyor (domain-agnostic hibrit): {file_name}")

            if not text_content.strip():
                raise Exception("Text içeriği boş")

            logging.info(f"📝 Text içeriği alındı: {len(text_content)} karakter")

            # LLM ile CV bilgilerini çıkar - Domain Agnostic Prompt
            llm, _ = get_llm(model_name)

            prompt = f"""
Aşağıdaki belge metninden kişi ve ilişkili entity'leri çıkar. Domain-agnostic hibrit yaklaşım kullan.

METIN:
{text_content}

ÇIKTI FORMATRI (JSON):
{{
    "person": {{
        "name": "Tam adı",
        "attributes": [
            {{"type": "contact", "key": "email", "value": "email@domain.com"}},
            {{"type": "contact", "key": "phone", "value": "+90xxx"}},
            {{"type": "profile", "key": "summary", "value": "Kişi özeti"}},
            {{"type": "profile", "key": "location", "value": "Şehir, Ülke"}},
            {{"type": "social", "key": "linkedin", "value": "linkedin_url"}},
            {{"type": "social", "key": "github", "value": "github_url"}},
            {{"type": "social", "key": "website", "value": "website_url"}},
            {{"type": "career", "key": "current_position", "value": "Mevcut pozisyon"}},
            {{"type": "career", "key": "current_company", "value": "Mevcut şirket"}},
            {{"type": "career", "key": "experience_years", "value": "5"}}
        ]
    }},
    "entities": [
        {{
            "type": "Organization",
            "name": "Şirket Adı",
            "attributes": [
                {{"key": "industry", "value": "Teknoloji"}},
                {{"key": "location", "value": "İstanbul"}}
            ]
        }},
        {{
            "type": "Position",
            "name": "Yazılım Geliştirici",
            "attributes": [
                {{"key": "level", "value": "Senior"}},
                {{"key": "department", "value": "IT"}}
            ]
        }},
        {{
            "type": "Institution",
            "name": "Üniversite Adı",
            "attributes": [
                {{"key": "type", "value": "University"}},
                {{"key": "location", "value": "Ankara"}}
            ]
        }},
        {{
            "type": "Degree",
            "name": "Lisans",
            "attributes": [
                {{"key": "field", "value": "Bilgisayar Mühendisliği"}},
                {{"key": "graduation_year", "value": "2020"}}
            ]
        }},
        {{
            "type": "Skill",
            "name": "Python",
            "attributes": [
                {{"key": "category", "value": "Programming Language"}},
                {{"key": "proficiency", "value": "Expert"}}
            ]
        }},
        {{
            "type": "Language",
            "name": "İngilizce",
            "attributes": [
                {{"key": "proficiency", "value": "C1"}}
            ]
        }}
    ],
    "relationships": [
        {{
            "from": "person",
            "to": "Organization:Şirket Adı",
            "type": "CONNECTED_TO",
            "attributes": [
                {{"key": "relationship_type", "value": "employment"}},
                {{"key": "role", "value": "Yazılım Geliştirici"}},
                {{"key": "start_date", "value": "2021-01"}},
                {{"key": "end_date", "value": "current"}},
                {{"key": "duration", "value": "3 yıl"}},
                {{"key": "description", "value": "React ve Node.js projelerinde çalıştı"}}
            ]
        }},
        {{
            "from": "person",
            "to": "Institution:Üniversite Adı",
            "type": "CONNECTED_TO",
            "attributes": [
                {{"key": "relationship_type", "value": "education"}},
                {{"key": "degree", "value": "Lisans"}},
                {{"key": "field", "value": "Bilgisayar Mühendisliği"}},
                {{"key": "start_date", "value": "2016"}},
                {{"key": "end_date", "value": "2020"}}
            ]
        }},
        {{
            "from": "person",
            "to": "Skill:Python",
            "type": "HAS_ATTRIBUTE",
            "attributes": [
                {{"key": "attribute_type", "value": "skill"}},
                {{"key": "proficiency", "value": "Expert"}},
                {{"key": "years_experience", "value": "5"}},
                {{"key": "context", "value": "Backend development"}}
            ]
        }},
        {{
            "from": "person",
            "to": "Language:İngilizce",
            "type": "HAS_ATTRIBUTE",
            "attributes": [
                {{"key": "attribute_type", "value": "language"}},
                {{"key": "proficiency", "value": "C1"}},
                {{"key": "context", "value": "Business communication"}}
            ]
        }}
    ],
    "meta": {{
        "extraction_confidence": 0.95,
        "detected_domain": "resume/cv",
        "language": "turkish",
        "document_type": "professional_profile"
    }}
}}

KURALAR:
1. Her entity için domain-agnostic type kullan (Organization, Institution, Skill, Language, Position, etc.)
2. Spesifik bilgileri attributes array'inde sakla
3. İlişkiler generic olmalı: CONNECTED_TO, HAS_ATTRIBUTE, RELATED_TO
4. Tüm relationship'ler attributes ile detaylandırılmalı
5. Meta bilgiler extraction kalitesi için ekle
6. Sadece JSON döndür, açıklama yapma
"""

            # LLM'den yanıt al
            response = llm.invoke(prompt)
            response_text = response.content.strip()

            # JSON parse et
            # JSON parse et
            try:
                # JSON kısmını ayıkla
                if '{' in response_text and '}' in response_text:
                    start_idx = response_text.find('{')
                    end_idx = response_text.rfind('}') + 1
                    json_text = response_text[start_idx:end_idx]
                    cv_info = json.loads(json_text)

                    # UTF-8 normalizasyon uygula - Hibrit yapı için
                    from src.utf8_utils import normalize_unicode_text
                    
                    def normalize_hybrid_structure(data):
                        """Hibrit yapı için recursive normalization"""
                        if isinstance(data, str):
                            return normalize_unicode_text(data)
                        elif isinstance(data, dict):
                            normalized_dict = {}
                            for key, value in data.items():
                                normalized_dict[key] = normalize_hybrid_structure(value)
                            return normalized_dict
                        elif isinstance(data, list):
                            return [normalize_hybrid_structure(item) for item in data]
                        else:
                            return data
                    
                    normalized_cv_info = normalize_hybrid_structure(cv_info)

                    # Meta bilgiler ekle - Domain agnostic
                    normalized_cv_info['extraction_meta'] = {
                        'timestamp': time.time(),
                        'source_file': file_name,
                        'extraction_method': 'llm_hybrid_domain_agnostic',
                        'model_used': model_name,
                        'approach': 'hybrid_schema'
                    }

                    person_name = normalized_cv_info.get('person', {}).get('name', 'İsim bulunamadı')
                    entity_count = len(normalized_cv_info.get('entities', []))
                    relationship_count = len(normalized_cv_info.get('relationships', []))
                    
                    logging.info(f"✅ Hibrit CV bilgileri çıkarıldı: {person_name}")
                    logging.info(f"📊 Entity sayısı: {entity_count}, İlişki sayısı: {relationship_count}")
                    
                    return normalized_cv_info

                else:
                    error_msg = "LLM yanıtında JSON formatı bulunamadı"
                    logging.error(error_msg)
                    logging.error(f"LLM yanıtı: {response_text[:500]}...")
                    raise ValueError(error_msg)

            except json.JSONDecodeError as e:
                error_msg = f"LLM yanıtı JSON parse edilemedi: {e}"
                logging.error(error_msg)
                logging.error(f"LLM yanıtı: {response_text[:500]}...")
                raise ValueError(error_msg)

        except Exception as e:
            error_msg = f"Hibrit CV bilgi çıkarma hatası: {e}"
            logging.error(error_msg)
            raise Exception(error_msg)

    def create_cv_node_from_document(self, file_name: str, text_content: str = None):
        """
        CV dosyasından hibrit yapı kullanarak Person ve ilişkili entity'leri oluşturur.
        Domain-agnostic yaklaşım ile generic node'lar ve attribute'lar oluşturur.
        
        Args:
            file_name: CV dosyasının adı
            text_content: PDF'den önceden extract edilmiş text content (opsiyonel)
        """
        try:
            # CV dosyasının tam yolunu oluştur (merged_files klasöründe)
            import os
            import asyncio
            
            merged_dir = os.path.join(os.path.dirname(__file__), "..", "..", "merged_files")
            cv_file_path = os.path.join(merged_dir, file_name)
            
            # Debug: text_content kontrolü
            if text_content:
                logging.info(f"🔍 Hibrit CV extraction'a text_content geçiliyor: {len(text_content)} karakter")
            else:
                logging.info(f"⚠️ text_content None veya boş, dosyadan okuma yapılacak: {cv_file_path}")

            # CV bilgilerini çıkar
            cv_info = asyncio.run(self.extract_cv_info_from_document(cv_file_path, text_content=text_content))

            if not cv_info:
                logging.info(f"CV bilgileri çıkarılamadı: {file_name}")
                return

            logging.info(f"🚀 Hibrit node creation başlıyor: {file_name}")

            # Document'a docType olarak 'CV' ekle
            update_document_query = """
                MATCH (d:Document {fileName: $file_name})
                SET d.docType = 'CV',
                    d.cvExtracted = true,
                    d.updatedAt = datetime(),
                    d.extractionApproach = 'hybrid_domain_agnostic'
                RETURN d.fileName as updated_file
            """

            self.graph.query(update_document_query, {
                "file_name": file_name
            }, session_params={"database": self.graph._database})

            # Hibrit yaklaşım ile node'ları oluştur
            self._create_hybrid_nodes_from_cv_info(cv_info, file_name)

        except Exception as e:
            logging.error(f"Hibrit CV node oluşturma hatası ({file_name}): {e}")

    def _create_hybrid_nodes_from_cv_info(self, cv_info: dict, file_name: str):
        """
        Hibrit yaklaşım kullanarak CV bilgilerinden node'lar ve ilişkiler oluşturur.
        Domain-agnostic generic yapı kullanır.
        """
        try:
            person_data = cv_info.get('person', {})
            entities = cv_info.get('entities', [])
            relationships = cv_info.get('relationships', [])
            
            if not person_data.get('name'):
                logging.warning(f"Person name bulunamadı: {file_name}")
                return

            # 1. CORE ENTITY: Person node'unu oluştur
            person_name = person_data['name']
            self._create_core_person_node(person_name, person_data, file_name)
            
            # 2. GENERIC ENTITIES: Domain-agnostic entity'leri oluştur
            created_entities = {}
            for entity in entities:
                entity_key = self._create_generic_entity(entity)
                if entity_key:
                    created_entities[f"{entity['type']}:{entity['name']}"] = entity_key
            
            # 3. HYBRID RELATIONSHIPS: Generic relationship'ler oluştur
            for relationship in relationships:
                self._create_hybrid_relationship(
                    relationship, 
                    person_name, 
                    created_entities, 
                    file_name
                )
            
            # 4. PERSON-DOCUMENT LINK: HAS_CV ilişkisi
            self._link_person_to_document(person_name, file_name)
            
            logging.info(f"✅ Hibrit node creation tamamlandı: {person_name}")
            logging.info(f"📊 Oluşturulan entity sayısı: {len(created_entities)}, İlişki sayısı: {len(relationships)}")

        except Exception as e:
            logging.error(f"Hibrit node oluşturma hatası: {e}")

    def _create_core_person_node(self, person_name: str, person_data: dict, file_name: str):
        """Core Person node'unu hibrit yaklaşımla oluşturur"""
        try:
            # Person attributes'larını generic formata çevir
            core_attributes = {}
            for attr in person_data.get('attributes', []):
                key = f"{attr['type']}_{attr['key']}"
                core_attributes[key] = attr['value']
            
            create_person_query = """
                MERGE (p:Person {name: $person_name})
                ON CREATE SET
                    p.createdAt = datetime(),
                    p.source_file = $filename,
                    p.extraction_method = 'hybrid_domain_agnostic',
                    p.entity_type = 'core_person'
                ON MATCH SET
                    p.updatedAt = datetime(),
                    p.source_file = $filename,
                    p.extraction_method = 'hybrid_domain_agnostic'
                
                // Dynamic attribute setting
                SET p += $attributes
                
                RETURN p.name as person_name
            """

            result = self.graph.query(create_person_query, {
                "person_name": person_name,
                "filename": file_name,
                "attributes": core_attributes
            }, session_params={"database": self.graph._database})

            if result:
                logging.info(f"✅ Core Person node oluşturuldu: {person_name}")
                
                # Person attributes'larını HAS_ATTRIBUTE ilişkileri olarak da kaydet
                for attr in person_data.get('attributes', []):
                    self._create_person_attribute_relationship(person_name, attr)

        except Exception as e:
            logging.error(f"Core Person node oluşturma hatası: {e}")

    def _create_generic_entity(self, entity: dict) -> str:
        """Generic entity oluşturur - Domain agnostic"""
        try:
            entity_type = entity.get('type', 'Unknown')
            entity_name = entity.get('name', 'Unknown')
            entity_key = f"{entity_type}:{entity_name}"
            
            # Generic entity attributes
            entity_attributes = {}
            for attr in entity.get('attributes', []):
                entity_attributes[attr['key']] = attr['value']
            
            create_entity_query = f"""
                MERGE (e:Entity {{type: $entity_type, name: $entity_name}})
                ON CREATE SET
                    e.createdAt = datetime(),
                    e.entity_category = 'generic',
                    e.domain_agnostic = true
                ON MATCH SET
                    e.updatedAt = datetime()
                
                // Dynamic attribute setting
                SET e += $attributes
                
                RETURN e.type + ':' + e.name as entity_key
            """

            result = self.graph.query(create_entity_query, {
                "entity_type": entity_type,
                "entity_name": entity_name,
                "attributes": entity_attributes
            }, session_params={"database": self.graph._database})

            if result:
                logging.info(f"✅ Generic entity oluşturuldu: {entity_key}")
                return entity_key

        except Exception as e:
            logging.error(f"Generic entity oluşturma hatası: {e}")
            return None

    def _create_hybrid_relationship(self, relationship: dict, person_name: str, created_entities: dict, file_name: str):
        """Hibrit relationship oluşturur - Generic yaklaşım"""
        try:
            from_node = relationship.get('from', '')
            to_node = relationship.get('to', '')
            rel_type = relationship.get('type', 'RELATED_TO')
            
            # Relationship attributes
            rel_attributes = {}
            for attr in relationship.get('attributes', []):
                rel_attributes[attr['key']] = attr['value']
            
            # From ve To node'ları belirle
            if from_node == 'person':
                from_match = f"(from:Person {{name: $person_name}})"
                from_params = {"person_name": person_name}
            else:
                return  # Şimdilik sadece person'dan çıkan ilişkiler
            
            if to_node in created_entities:
                to_parts = to_node.split(':', 1)
                to_match = f"(to:Entity {{type: $to_type, name: $to_name}})"
                to_params = {"to_type": to_parts[0], "to_name": to_parts[1]}
            else:
                return  # Target entity bulunamazsa skip
            
            create_relationship_query = f"""
                MATCH {from_match}
                MATCH {to_match}
                MERGE (from)-[r:{rel_type}]->(to)
                ON CREATE SET
                    r.createdAt = datetime(),
                    r.source_file = $file_name,
                    r.hybrid_approach = true
                ON MATCH SET
                    r.updatedAt = datetime()
                
                // Dynamic attribute setting
                SET r += $rel_attributes
                
                RETURN count(r) as relationships_created
            """

            params = {**from_params, **to_params, "file_name": file_name, "rel_attributes": rel_attributes}
            
            result = self.graph.query(create_relationship_query, params, 
                                    session_params={"database": self.graph._database})

            if result and result[0]['relationships_created'] > 0:
                logging.info(f"✅ Hibrit relationship oluşturuldu: {person_name} -{rel_type}-> {to_node}")

        except Exception as e:
            logging.error(f"Hibrit relationship oluşturma hatası: {e}")

    def _create_person_attribute_relationship(self, person_name: str, attribute: dict):
        """Person için HAS_ATTRIBUTE relationship'i oluşturur"""
        try:
            attr_type = attribute.get('type', 'unknown')
            attr_key = attribute.get('key', 'unknown')
            attr_value = attribute.get('value', '')
            
            create_attr_query = """
                MATCH (p:Person {name: $person_name})
                MERGE (p)-[r:HAS_ATTRIBUTE {type: $attr_type, key: $attr_key}]->(a:Attribute {value: $attr_value})
                ON CREATE SET
                    a.createdAt = datetime(),
                    a.attribute_category = $attr_type,
                    a.source = 'cv_extraction',
                    r.createdAt = datetime()
                RETURN count(r) as attr_created
            """

            result = self.graph.query(create_attr_query, {
                "person_name": person_name,
                "attr_type": attr_type,
                "attr_key": attr_key,
                "attr_value": attr_value
            }, session_params={"database": self.graph._database})

        except Exception as e:
            logging.error(f"Person attribute relationship hatası: {e}")

    def _link_person_to_document(self, person_name: str, file_name: str):
        """Person ile Document arasında HAS_CV ilişkisi oluşturur"""
        try:
            link_query = """
                MATCH (p:Person {name: $person_name})
                MATCH (d:Document {fileName: $file_name})
                MERGE (p)-[r:HAS_CV]->(d)
                SET r.created_at = datetime(),
                    r.source = 'hybrid_cv_extraction',
                    r.approach = 'domain_agnostic'
                RETURN count(r) as links_created
            """

            result = self.graph.query(link_query, {
                "person_name": person_name,
                "file_name": file_name
            }, session_params={"database": self.graph._database})

            if result and result[0]['links_created'] > 0:
                logging.info(f"✅ Person-Document HAS_CV ilişkisi oluşturuldu: {person_name} -> {file_name}")

        except Exception as e:
            logging.error(f"Person-Document link hatası: {e}")