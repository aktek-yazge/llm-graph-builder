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
        
    def create_source_node(self, obj_source_node_or_filename):
        """
        Document node oluşturur. sourceNode objesi veya sadece file_name string'i alabilir.
        """
        try:
            # Eğer string ise, minimal Document node oluştur
            if isinstance(obj_source_node_or_filename, str):
                file_name = obj_source_node_or_filename
                
                # UTF-8 ve Unicode normalization
                file_name = normalize_file_name(file_name)
                
                logging.info(f"Minimal Document node oluşturuluyor: {file_name}")
                
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
                
                # Önce node'ın var olup olmadığını kontrol et
                check_query = "MATCH (d:Document {fileName: $file_name}) RETURN count(d) as count"
                result = self.execute_query(check_query, {"file_name": file_name})
                
                if result and result[0]['count'] > 0:
                    logging.info(f"Document node zaten mevcut: {file_name}")
                    # Var olan node'ın özelliklerini güncelle (sadece gerekli alanları)
                    update_query = """
                    MATCH (d:Document {fileName: $file_name})
                    SET d.updatedAt = datetime()
                    """
                    self.execute_query(update_query, {"file_name": file_name})
                    return
                
                # Minimal Document node oluştur
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
                        d.is_cancelled = false,
                        d.errorMessage = '',
                        d.model = 'unknown'
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
                logging.info(f"Minimal Document node oluşturuldu: {file_name}")
                
                # Document yaratıldıktan sonra Policy node'unu da yarat ve bağla
                self.create_policy_node_from_document(file_name)
                return
            
            # sourceNode objesi ise, orijinal işlemi yap
            obj_source_node = obj_source_node_or_filename
            
            # UTF-8 ve Unicode normalization for file_name
            obj_source_node.file_name = normalize_file_name(obj_source_node.file_name)
            
            job_status = "New"
            logging.info(f"Tam Document node oluşturuluyor: {obj_source_node.file_name}")
            self.graph.query("""MERGE(d:Document {fileName :$fn}) SET d.fileSize = $fs, d.fileType = $ft ,
                            d.status = $st, d.url = $url, d.awsAccessKeyId = $awsacc_key_id, 
                            d.fileSource = $f_source, d.createdAt = $c_at, d.updatedAt = $u_at, 
                            d.processingTime = $pt, d.errorMessage = $e_message, d.nodeCount= $n_count, 
                            d.relationshipCount = $r_count, d.model= $model, d.gcsBucket=$gcs_bucket, 
                            d.gcsBucketFolder= $gcs_bucket_folder, d.language= $language,d.gcsProjectId= $gcs_project_id,
                            d.is_cancelled=False, d.total_chunks=0, d.processed_chunk=0,
                            d.access_token=$access_token, d.doc_link=$doc_link, d.page_images=$page_images,
                            d.chunkNodeCount=$chunkNodeCount,d.chunkRelCount=$chunkRelCount,
                            d.entityNodeCount=$entityNodeCount,d.entityEntityRelCount=$entityEntityRelCount,
                            d.communityNodeCount=$communityNodeCount,d.communityRelCount=$communityRelCount""",
                            {"fn":obj_source_node.file_name, "fs":obj_source_node.file_size, "ft":obj_source_node.file_type, "st":job_status, 
                            "url":obj_source_node.url,
                            "awsacc_key_id":obj_source_node.awsAccessKeyId, "f_source":obj_source_node.file_source, "c_at":obj_source_node.created_at,
                            "u_at":obj_source_node.created_at, "pt":0, "e_message":'', "n_count":0, "r_count":0, "model":obj_source_node.model,
                            "gcs_bucket": obj_source_node.gcsBucket, "gcs_bucket_folder": obj_source_node.gcsBucketFolder, 
                            "language":obj_source_node.language, "gcs_project_id":obj_source_node.gcsProjectId,
                            "access_token":obj_source_node.access_token, "doc_link":obj_source_node.doc_link, "page_images":obj_source_node.page_images,
                            "chunkNodeCount":obj_source_node.chunkNodeCount,
                            "chunkRelCount":obj_source_node.chunkRelCount,
                            "entityNodeCount":obj_source_node.entityNodeCount,
                            "entityEntityRelCount":obj_source_node.entityEntityRelCount,
                            "communityNodeCount":obj_source_node.communityNodeCount,
                            "communityRelCount":obj_source_node.communityRelCount
                            },session_params={"database":self.graph._database})
            
            logging.info(f"Tam Document node oluşturuldu: {obj_source_node.file_name}")
            
            # Document yaratıldıktan sonra Policy node'unu da yarat ve bağla
            self.create_policy_node_from_document(obj_source_node.file_name)
            
        except Exception as e:
            error_message = str(e)
            logging.error(f"Document node oluşturma hatası: {error_message}")
            if not isinstance(obj_source_node_or_filename, str):
                self.update_exception_db(self, obj_source_node_or_filename.file_name, error_message)
            raise Exception(error_message)
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
        
        for (file_name,source_type) in zip(filename_list, source_types_list):
            merged_file_path = os.path.join(merged_dir, file_name)
            if source_type == 'local file' and gcs_file_cache == 'True':
                folder_name = create_gcs_bucket_folder_name_hashed(uri, file_name)
                delete_file_from_gcs(BUCKET_UPLOAD,folder_name,file_name)
            else:
                logging.info(f'Deleted File Path: {merged_file_path} and Deleted File Name : {file_name}')
                delete_uploaded_local_file(merged_file_path,file_name)
                
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
            OPTIONAL MATCH (d)<-[:PART_OF]-(c:Chunk)
            OPTIONAL MATCH (c:Chunk)-[:HAS_ENTITY]->(e)
            WITH d, c, e, documents
            WHERE NOT EXISTS {
                MATCH (e)<-[:HAS_ENTITY]-(c2)-[:PART_OF]->(d2:Document)
                WHERE NOT d2 IN documents
                }
            WITH d, COLLECT(c) AS chunks, COLLECT(e) AS entities
            FOREACH (chunk IN chunks | DETACH DELETE chunk)
            FOREACH (entity IN entities | DETACH DELETE entity)
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
            result = self.execute_query(query_to_delete_document_and_entities, param)
            _ = self.execute_query(query_to_delete_communities,community_param)
            logging.info(f"Deleting {len(filename_list)} documents = '{filename_list}' from '{source_types_list}' from database")
        else :
            result = self.execute_query(query_to_delete_document, param)    
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
        query = """
        MATCH (e) WHERE elementId(e) IN $elementIds
        DETACH DELETE e
        """
        param = {"elementIds":entities_list}
        return self.execute_query(query,param)
    
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
        """
        try:
            # Dosya isminden poliçe bilgilerini çıkar
            policy_info = self.extract_policy_info_from_filename(file_name)
            
            if not policy_info:
                logging.info(f"Dosya isminden poliçe bilgisi çıkarılamadı: {file_name}")
                return
            
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
                
                # Document'a docType ekle
                update_document_query = """
                    MATCH (d:Document {fileName: $file_name})
                    SET d.docType = $doc_type,
                        d.updatedAt = datetime()
                    RETURN d.fileName as updated_file
                """
                
                doc_type = policy_info.get('document_type', 'MAIN_POLICY')
                self.graph.query(update_document_query, {
                    "file_name": file_name,
                    "doc_type": doc_type
                }, session_params={"database": self.graph._database})
                
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
                elif doc_type == 'ENDORSEMENT':
                    link_query = """
                        MATCH (d:Document {fileName: $file_name})
                        MATCH (p:Policy {id: $policy_id})
                        MERGE (p)-[r:HAS_ENDORSEMENT]->(d)
                        SET r.created_at = datetime(),
                            r.source = 'filename_extraction'
                        RETURN count(r) as links_created
                    """
                    relationship_type = "HAS_ENDORSEMENT"
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
        LLM kullanarak dosya isminden poliçe bilgilerini çıkarır.
        Fallback yok - LLM başarısız olursa hata fırlatır.
        
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
            
            # LLM'den poliçe bilgilerini al
            policy_info = self._extract_policy_info_with_llm(base_name)
            
            if not policy_info:
                error_msg = f"LLM dosya isminden poliçe bilgisi çıkaramadı: {file_name}"
                logging.error(error_msg)
                raise ValueError(error_msg)
            
            # Policy ID'yi oluştur
            policy_id = base_name.strip()
            policy_info['policy_id'] = policy_id
            policy_info['policy_name'] = policy_id
            
            logging.info(f"LLM ile çıkarılan poliçe bilgisi: {policy_info}")
            return policy_info
            
        except Exception as e:
            error_msg = f"Dosya isminden poliçe bilgisi çıkarma hatası ({file_name}): {e}"
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

Çıkarılacak bilgiler:
- customer_name: Müşteri ismi (ad soyad)
- year: Poliçe yılı (varsa)
- policy_type: Poliçe türü (Konut, DASK, Kasko, Trafik, Sağlık, Hayat, vb.)
- insured_item: Sigortalanan eşya/konum (ev adresi, araç, vb.)
- policy_number: Poliçe numarası (varsa)
- document_type: Belge türü (MAIN_POLICY, ENDORSEMENT, RENEWAL, CANCELLATION)

Belge türü belirleme kuralları:
- MAIN_POLICY: Ana poliçe (zeyilname, yenileme, iptal belirtisi yoksa)
- ENDORSEMENT: Zeyilname (dosya isminde "zeyilname", "ek", "tadilat" varsa)
- RENEWAL: Yenileme (dosya isminde "yenileme", "renewal" varsa)
- CANCELLATION: İptal (dosya isminde "iptal", "fesih" varsa)

Örnekler:
- "Ayça Dinçkök Galata Residance D6 Konut 2020.pdf" → customer_name: "Ayça Dinçkök", year: "2020", policy_type: "Konut Sigortası", insured_item: "Galata Residance D6", document_type: "MAIN_POLICY"
- "Mehmet Yılmaz BMW X5 Kasko Zeyilname 2023.pdf" → customer_name: "Mehmet Yılmaz", year: "2023", policy_type: "Kasko Sigortası", insured_item: "BMW X5", document_type: "ENDORSEMENT"

Sadece JSON formatında yanıt ver, başka açıklama ekleme:
{{
    "customer_name": "...",
    "year": "...",
    "policy_type": "...",
    "insured_item": "...",
    "policy_number": "...",
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
                    
                    # Minimum gerekli alanları kontrol et
                    required_fields = ['customer_name', 'policy_type', 'document_type']
                    missing_fields = [field for field in required_fields if not cleaned_info.get(field, '').strip()]
                    
                    if missing_fields:
                        error_msg = f"LLM eksik bilgi döndürdü. Eksik alanlar: {missing_fields}"
                        logging.error(error_msg)
                        raise ValueError(error_msg)
                    
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