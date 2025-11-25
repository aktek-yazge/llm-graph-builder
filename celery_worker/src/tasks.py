import logging
import asyncio
from datetime import datetime, timezone
from src.celery_app import app
from src.models.file_queue_models import get_file_queue_db, FileStatus, UploadedFile
# Lazy imports to avoid SIGSEGV on module load (especially with prefork pool + MPS)
# from src.processing_utils import FileProcessor
# from src.document_sources.s3_upload_utils import create_document_output_structure
# from src.create_chunks import CreateChunksofDocument
# from src.shared.common_fn import create_graph_database_connection
from pathlib import Path
import os

# Helper to get DB session
def get_db():
    return get_file_queue_db()

@app.task(bind=True, name="src.tasks.process_file_pipeline")
def process_file_pipeline(self, file_id: int):
    """
    Orchestrator task that triggers the pipeline:
    Image Extraction -> Chunking -> Graph Creation
    """
    logging.info(f"🚀 Starting pipeline for file {file_id}")
    
    try:
        # Chain the tasks
        # 1. Extract Images
        # 2. Chunk File (depends on images)
        # 3. Create Graph (depends on chunks)
        
        # We use 'link' to chain them. 
        # Note: We need to pass file_id to each.
        
        # Chain tasks: extract_images -> chunk_file -> create_graph
        # Each task receives file_id and returns it for the next task
        from celery import chain
        
        # Create chain: each task returns file_id, next task receives it as first argument
        workflow = chain(
            extract_images_task.s(file_id),  # Returns file_id
            chunk_file_task.s(),              # Receives file_id from previous task's return value
            create_graph_task.s()             # Receives file_id from previous task's return value
        )
        
        # Apply chain asynchronously - ensure it goes to default queue
        result = workflow.apply_async()
        logging.info(f"🔗 Pipeline chained for file {file_id}, chain ID: {result.id}")
        
        # Return the chain result ID for tracking
        return result.id
    except Exception as e:
        logging.error(f"❌ Pipeline chain failed: {e}")
        import traceback
        logging.error(f"Traceback: {traceback.format_exc()}")
        raise

@app.task(bind=True, name="src.tasks.extract_images_task")
def extract_images_task(self, file_id: int):
    """
    Task to extract images from PDF
    """
    db = get_db()
    db_session = db.get_db_session()
    try:
        file_record = db_session.query(UploadedFile).filter_by(id=file_id).first()
        if not file_record:
            logging.error(f"File {file_id} not found")
            return file_id  # Return file_id even if not found to keep chain working

        logging.info(f"🖼️ Starting image extraction for file {file_id}")
        
        # Update status
        file_record.chunking_status = "extracting"
        file_record.status = "processing"
        db_session.commit()

        # Logic from background_processor.py (simplified)
        # In a real migration, we would move the logic to a shared utility
        # For now, I'll instantiate the processor just to use its method if possible, 
        # OR better, re-implement the specific extraction logic here to avoid the class overhead.
        
        # Re-implementing core extraction logic:
        # Lazy import to avoid SIGSEGV
        from src.processing_utils import FileProcessor
        processor = FileProcessor()
        
        # We need to run the async method in this sync task
        # Use get_event_loop() instead of new_event_loop() to avoid SIGSEGV
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        try:
            loop.run_until_complete(processor._process_single_file_extraction(file_record))
        finally:
            # Don't close the loop - it might be reused
            pass
        
        # Check if successful (status should be updated by the method or we update it here)
        # The _process_single_file_extraction method updates the DB.
        
        # Refresh record
        db_session.refresh(file_record)
        if file_record.chunking_status == "ready":
             logging.info(f"✅ Image extraction completed for file {file_id}")
             return file_id # Pass to next task
        else:
             # If it failed or didn't finish, we might raise an error
             # But _process_single_file_extraction handles errors gracefully usually
             return file_id

    except Exception as e:
        logging.error(f"❌ Image extraction failed: {e}")
        # Update DB
        if file_record:
            file_record.chunking_status = "failed"
            file_record.processing_error = str(e)
            db_session.commit()
        raise self.retry(exc=e, countdown=60, max_retries=3)
    finally:
        db_session.close()

@app.task(bind=True, name="src.tasks.chunk_file_task")
def chunk_file_task(self, file_id: int):
    """
    Task to chunk the file
    """
    db = get_db()
    db_session = db.get_db_session()
    try:
        file_record = db_session.query(UploadedFile).filter_by(id=file_id).first()
        if not file_record:
            logging.error(f"File {file_id} not found for chunking")
            return file_id  # Return file_id even if not found to keep chain working

        logging.info(f"📦 Starting chunking for file {file_id}")
        
        file_record.chunking_status = "chunking"
        file_record.chunking_started_at = datetime.now(timezone.utc)
        db_session.commit()

        # Re-use logic
        # Lazy import to avoid SIGSEGV
        from src.processing_utils import FileProcessor
        processor = FileProcessor()
        
        # Use get_event_loop() instead of new_event_loop() to avoid SIGSEGV
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        try:
            # Since process_v2_chunking_batch takes a list, we can pass a list of one
            loop.run_until_complete(processor.process_v2_chunking_batch([file_record]))
        finally:
            # Don't close the loop - it might be reused
            pass
        
        return file_id

    except Exception as e:
        logging.error(f"❌ Chunking failed: {e}")
        if file_record:
            file_record.chunking_status = "failed"
            file_record.processing_error = str(e)
            db_session.commit()
        raise self.retry(exc=e, countdown=60, max_retries=3)
    finally:
        db_session.close()

@app.task(bind=True, name="src.tasks.create_graph_task")
def create_graph_task(self, file_id: int):
    """
    Task to create graph from chunks
    """
    db = get_db()
    db_session = db.get_db_session()
    try:
        file_record = db_session.query(UploadedFile).filter_by(id=file_id).first()
        if not file_record:
            logging.error(f"File {file_id} not found for chunking")
            return file_id  # Return file_id even if not found to keep chain working

        logging.info(f"🕸️ Starting graph creation for file {file_id}")
        
        # Check if chunking is completed before starting graph creation
        if file_record.chunking_status == "failed":
            logging.warning(f"⚠️ Graph creation skipped: Chunking failed for file {file_id}")
            file_record.graph_status = "failed"
            file_record.processing_error = f"Graph creation skipped: Chunking failed - {file_record.processing_error or 'Unknown error'}"
            db_session.commit()
            return file_id
        
        file_record.graph_status = "processing"
        file_record.graph_started_at = datetime.now(timezone.utc)
        db_session.commit()

        # Re-use logic
        # Lazy import to avoid SIGSEGV
        from src.processing_utils import FileProcessor
        processor = FileProcessor()
        
        # Use get_event_loop() instead of new_event_loop() to avoid SIGSEGV
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        try:
            loop.run_until_complete(processor.process_v2_graph_creation_batch([file_record]))
            
            # Refresh record to check final status
            db_session.refresh(file_record)
            
            # If graph creation was skipped due to chunking failure, update status
            if file_record.chunking_status == "failed":
                logging.warning(f"⚠️ Graph creation skipped due to chunking failure for file {file_id}")
                file_record.graph_status = "failed"
                file_record.processing_error = f"Graph creation skipped: Chunking failed - {file_record.processing_error or 'Unknown error'}"
                db_session.commit()
            elif file_record.graph_status == "processing":
                # If still processing, check if it actually completed
                # The processing_utils should update this, but if not, we mark as failed
                logging.warning(f"⚠️ Graph creation status still 'processing' after task completion for file {file_id}")
                # Don't change it - let processing_utils handle it
        finally:
            # Don't close the loop - it might be reused
            pass
        
        return file_id

    except Exception as e:
        logging.error(f"❌ Graph creation failed: {e}")
        if file_record:
            file_record.graph_status = "failed"
            file_record.processing_error = str(e)
            db_session.commit()
        raise self.retry(exc=e, countdown=60, max_retries=3)
    finally:
        db_session.close()

@app.task(bind=True, name="src.tasks.create_embeddings_task")
def create_embeddings_task(self, file_id: int):
    """
    Task to create embeddings for chunks of a completed file
    Copied from backend/score.py process_embedding_creation
    """
    db = get_db()
    db_session = db.get_db_session()
    try:
        file_record = db_session.query(UploadedFile).filter_by(id=file_id).first()
        if not file_record:
            logging.error(f"❌ File record not found for ID: {file_id}")
            return file_id

        logging.info(f"📊 Starting embedding creation for: {file_record.original_name}")

        try:
            # Get Neo4j credentials
            uri = file_record.neo4j_uri or os.environ.get("NEO4J_URI")
            userName = os.environ.get("NEO4J_USERNAME")
            password = os.environ.get("NEO4J_PASSWORD")
            database = file_record.neo4j_database or os.environ.get("NEO4J_DATABASE", "neo4j")

            if not all([uri, userName, password]):
                error_msg = "Neo4j credentials not configured"
                logging.error(f"❌ {error_msg}")
                file_record.embedding_status = "failed"
                file_record.embedding_completed_at = datetime.now(timezone.utc)
                file_record.processing_error = error_msg
                file_record.reason = f"Embedding creation failed: {error_msg}"
                db_session.commit()
                return file_id

            # Get graph connection
            from src.shared.common_fn import create_graph_database_connection
            from src.graphDB_dataAccess import graphDBdataAccess
            from src.models.status_sync import sync_queue_db_status_to_neo4j

            # Use asyncio to run sync functions
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            # Create graph connection
            graph = loop.run_until_complete(
                asyncio.to_thread(
                    create_graph_database_connection,
                    uri, userName, password, database
                )
            )
            graphDb_data_Access = graphDBdataAccess(graph)

            # Normalize filename
            from src.utf8_utils import normalize_file_name
            normalized_filename = normalize_file_name(file_record.original_name)

            # Create embeddings for chunks of this file
            result = graphDb_data_Access.create_embeddings_for_documents(
                [normalized_filename]
            )

            if result.get("error"):
                error_msg = f"Embedding creation failed: {result['error']}"
                logging.error(f"❌ {error_msg}")

                # Update status to failed
                file_record.embedding_status = "failed"
                file_record.embedding_completed_at = datetime.now(timezone.utc)
                file_record.processing_error = error_msg[:500]
                file_record.reason = f"Embedding creation failed: {result['error']}"
                db_session.commit()

                # Neo4j'ye failed status sync et
                try:
                    graph_connection = loop.run_until_complete(
                        asyncio.to_thread(
                            create_graph_database_connection,
                            uri, userName, password, database
                        )
                    )
                    loop.run_until_complete(
                        asyncio.to_thread(
                            sync_queue_db_status_to_neo4j,
                            graph_connection,
                            file_record.original_name,
                            file_record.upload_status,
                            file_record.chunking_status,
                            file_record.graph_status,
                            "failed",
                            database
                        )
                    )
                except Exception as sync_error:
                    logging.warning(
                        f"⚠️ Could not sync embedding failure to Neo4j: {str(sync_error)}"
                    )
                return file_id

            total_chunks = result.get("total_chunks_updated", 0)
            embedding_model = result.get("embedding_model", "Unknown")

            # Update status to completed
            file_record.embedding_status = "completed"
            file_record.embedding_completed_at = datetime.now(timezone.utc)
            file_record.status = "uploaded"  # Reset status from 'processing' to 'uploaded'
            file_record.reason = f"Embedding creation completed successfully. Updated {total_chunks} chunks."
            db_session.commit()

            logging.info(
                f"✅ Embedding creation completed for: {file_record.original_name} - {total_chunks} chunks updated"
            )

            # Neo4j'ye completed status sync et
            try:
                logging.info(
                    f"📤 Attempting Neo4j sync for embedding completion: file_name={file_record.original_name}, "
                    f"upload_status={file_record.upload_status}, "
                    f"chunking_status={file_record.chunking_status}, "
                    f"graph_status={file_record.graph_status}, "
                    f"embedding_status={file_record.embedding_status}"
                )

                graph_connection = loop.run_until_complete(
                    asyncio.to_thread(
                        create_graph_database_connection,
                        uri, userName, password, database
                    )
                )
                loop.run_until_complete(
                    asyncio.to_thread(
                        sync_queue_db_status_to_neo4j,
                        graph_connection,
                        file_record.original_name,
                        file_record.upload_status,
                        file_record.chunking_status,
                        file_record.graph_status,
                        file_record.embedding_status,
                        database
                    )
                )
            except Exception as sync_error:
                logging.warning(
                    f"⚠️ Could not sync embedding completion to Neo4j: {str(sync_error)}"
                )

        except Exception as emb_error:
            logging.error(f"❌ Embedding creation failed: {emb_error}")
            import traceback
            logging.error(f"Traceback: {traceback.format_exc()}")
            
            # Update status to failed
            file_record.embedding_status = "failed"
            file_record.embedding_completed_at = datetime.now(timezone.utc)
            file_record.processing_error = str(emb_error)[:500]
            file_record.reason = f"Embedding creation failed: {str(emb_error)}"
            db_session.commit()
            raise self.retry(exc=emb_error, countdown=60, max_retries=3)

        return file_id

    except Exception as e:
        logging.error(f"❌ Embedding creation task failed: {e}")
        import traceback
        logging.error(f"Traceback: {traceback.format_exc()}")
        if file_record:
            file_record.embedding_status = "failed"
            file_record.processing_error = str(e)[:500]
            db_session.commit()
        raise self.retry(exc=e, countdown=60, max_retries=3)
    finally:
        db_session.close()

@app.task(bind=True, name="src.tasks.delete_files_task")
def delete_files_task(self, file_ids: list):
    """
    Task to delete files from queue, filesystem, and Neo4j database
    Copied from backend/score.py delete_file_background_task
    """
    db = get_db()
    deleted_count = 0
    failed_count = 0
    
    try:
        from src.shared.common_fn import create_graph_database_connection
        
        # Neo4j bağlantısını kur
        graph_connection = None
        neo4j_uri = os.environ.get("NEO4J_URI")
        neo4j_username = os.environ.get("NEO4J_USERNAME")
        neo4j_password = os.environ.get("NEO4J_PASSWORD")
        neo4j_database = os.environ.get("NEO4J_DATABASE", "neo4j")
        
        if neo4j_uri:
            try:
                graph_connection = create_graph_database_connection(
                    uri=neo4j_uri,
                    userName=neo4j_username,
                    password=neo4j_password,
                    database=neo4j_database,
                )
                logging.info(f"🔗 Neo4j connection established for deletion task")
            except Exception as conn_error:
                logging.error(f"❌ Failed to establish Neo4j connection: {str(conn_error)}")
                graph_connection = None
        
        # Process each file
        for file_id_int in file_ids:
            try:
                db_session = db.get_db_session()
                file_record = db.get_file_by_id(file_id_int)
                
                if not file_record:
                    logging.warning(f"⚠️ File not found: ID={file_id_int}")
                    failed_count += 1
                    db_session.close()
                    continue
                
                file_path = Path(file_record.file_path)
                original_name = file_record.original_name
                filename = file_record.filename

                # Neo4j'den Document ve ilişkili node'ları sil
                neo4j_deleted = False
                try:
                    # Dosya kaydında özel Neo4j URI varsa kullan, yoksa batch bağlantısını kullan
                    file_neo4j_uri = file_record.neo4j_uri or neo4j_uri
                    file_neo4j_database = file_record.neo4j_database or neo4j_database
                    
                    # Eğer dosya farklı URI/database kullanıyorsa, özel bağlantı kur
                    if file_neo4j_uri and (file_neo4j_uri != neo4j_uri or file_neo4j_database != neo4j_database):
                        # Dosya özel Neo4j kullanıyor, özel bağlantı kur
                        file_graph_connection = create_graph_database_connection(
                            uri=file_neo4j_uri,
                            userName=neo4j_username,
                            password=neo4j_password,
                            database=file_neo4j_database,
                        )
                        use_connection = file_graph_connection
                        use_database = file_neo4j_database
                    elif graph_connection:
                        # Batch bağlantısını kullan
                        use_connection = graph_connection
                        use_database = neo4j_database
                    else:
                        # Neo4j yapılandırılmamış
                        use_connection = None
                        use_database = None

                    if use_connection:
                        # V2 Document deletion query (copied from backend)
                        delete_query = """
                            MATCH (d:Document {fileName: $filename})
                            
                            // 1. Document'a bağlı Chunk node'ları topla
                            OPTIONAL MATCH (d)<-[:PART_OF]-(c:Chunk)
                            
                            // 2. Document'a DOCUMENTED_IN ile bağlı Policy node'ları topla
                            OPTIONAL MATCH (d)<-[:DOCUMENTED_IN]-(p:Policy)
                            
                            // 3. Document'a DOCUMENTED_IN ile bağlı Endorsement node'ları topla
                            OPTIONAL MATCH (d)<-[:DOCUMENTED_IN]-(e:Endorsement)
                            
                            // 4. Policy'den -> yönünde bağlı tüm node'ları topla
                            OPTIONAL MATCH (p)-[*1..2]->(relatedNodes)
                            WHERE relatedNodes:PolicyYear OR relatedNodes:InsuredItem OR 
                                  relatedNodes:PolicyType OR relatedNodes:Customer OR
                                  relatedNodes:Agent OR relatedNodes:InsuranceCompany OR
                                  relatedNodes:Address OR relatedNodes:Phone OR relatedNodes:Email
                            
                            // 5. Policy'den bağlı Endorsement node'ları topla
                            OPTIONAL MATCH (p)-[:FIRST_ENDORSEMENT|NEXT_ENDORSEMENT*]->(policyEndorsements:Endorsement)
                            
                            // 6. Endorsement'lardan bağlı node'ları topla
                            OPTIONAL MATCH (e)-[*1..2]->(endorsementRelatedNodes)
                            WHERE endorsementRelatedNodes:Premium OR endorsementRelatedNodes:Coverage OR
                                  endorsementRelatedNodes:Clause OR endorsementRelatedNodes:Payment OR
                                  endorsementRelatedNodes:Address OR endorsementRelatedNodes:Phone OR
                                  endorsementRelatedNodes:Email
                            
                            OPTIONAL MATCH (policyEndorsements)-[*1..2]->(policyEndorsementRelatedNodes)
                            WHERE policyEndorsementRelatedNodes:Premium OR policyEndorsementRelatedNodes:Coverage OR
                                  policyEndorsementRelatedNodes:Clause OR policyEndorsementRelatedNodes:Payment OR
                                  policyEndorsementRelatedNodes:Address OR policyEndorsementRelatedNodes:Phone OR
                                  policyEndorsementRelatedNodes:Email
                            
                            // 7. Sadece başka Document'larda kullanılmayan node'ları sil
                            WITH d, 
                                 COLLECT(DISTINCT c) AS chunks,
                                 COLLECT(DISTINCT p) AS policies,
                                 COLLECT(DISTINCT e) + COLLECT(DISTINCT policyEndorsements) AS allEndorsements,
                                 COLLECT(DISTINCT relatedNodes) AS relatedNodesList,
                                 COLLECT(DISTINCT endorsementRelatedNodes) + COLLECT(DISTINCT policyEndorsementRelatedNodes) AS endorsementRelatedNodesList
                            
                            // Güvenli silme: Başka document'larda kullanılmayan Policy'leri kontrol et
                            WITH d, chunks,
                                 [policy IN policies WHERE policy IS NOT NULL AND NOT EXISTS {
                                     MATCH (d2:Document)
                                     WHERE d2 <> d AND (d2)<-[:DOCUMENTED_IN]-(policy)
                                 }] AS safePolicies,
                                 [endorsement IN allEndorsements WHERE endorsement IS NOT NULL AND NOT EXISTS {
                                     MATCH (d2:Document)
                                     WHERE d2 <> d AND (
                                         (d2)<-[:DOCUMENTED_IN]-(endorsement) OR
                                         (d2)<-[:DOCUMENTED_IN]-(:Policy)-[:FIRST_ENDORSEMENT|NEXT_ENDORSEMENT*]->(endorsement)
                                     )
                                 }] AS safeEndorsements,
                                 [node IN relatedNodesList WHERE node IS NOT NULL AND NOT EXISTS {
                                     MATCH (d2:Document)<-[:DOCUMENTED_IN]-(p2:Policy)
                                     WHERE d2 <> d AND (
                                         (p2)-[*1..2]->(node) OR
                                         (p2)<-[:HAS_DOC]-(node) OR
                                         (p2)<-[:DOCUMENTED_IN]-(node)
                                     )
                                 }] AS safeRelatedNodes,
                                 [node IN endorsementRelatedNodesList WHERE node IS NOT NULL AND NOT EXISTS {
                                     MATCH (d2:Document)
                                     WHERE d2 <> d AND (
                                         (d2)<-[:DOCUMENTED_IN]-(:Endorsement)-[*1..2]->(node) OR
                                         (d2)<-[:DOCUMENTED_IN]-(:Policy)-[:FIRST_ENDORSEMENT|NEXT_ENDORSEMENT*]->(:Endorsement)-[*1..2]->(node)
                                     )
                                 }] AS safeEndorsementRelatedNodes
                            
                            // 8. Silme işlemi
                            FOREACH (chunk IN chunks | DETACH DELETE chunk)
                            FOREACH (endorsement IN safeEndorsements | 
                                FOREACH (relNode IN safeEndorsementRelatedNodes | DETACH DELETE relNode)
                            )
                            FOREACH (endorsement IN safeEndorsements | DETACH DELETE endorsement)
                            FOREACH (policy IN safePolicies | 
                                FOREACH (relNode IN safeRelatedNodes | DETACH DELETE relNode)
                            )
                            FOREACH (policy IN safePolicies | DETACH DELETE policy)
                            DETACH DELETE d
                            
                            RETURN count(d) AS deletedDocuments
                        """

                        session_params = {}
                        if use_database:
                            session_params["database"] = use_database

                        result = use_connection.query(
                            delete_query,
                            {"filename": filename},
                            session_params=session_params,
                        )
                        
                        # Özel bağlantı kullanıldıysa kapat
                        if use_connection != graph_connection and hasattr(use_connection, 'close'):
                            try:
                                use_connection.close()
                            except:
                                pass

                        if result and len(result) > 0:
                            deleted_count_neo4j = result[0]["deletedDocuments"]
                            logging.info(
                                f"✅ Deleted {deleted_count_neo4j} Document nodes from Neo4j for: {original_name}"
                            )
                            neo4j_deleted = True
                        else:
                            neo4j_deleted = True  # Not an error if document doesn't exist
                    else:
                        neo4j_deleted = True  # Not an error if Neo4j is not configured

                except Exception as neo4j_error:
                    logging.error(
                        f"❌ Failed to delete from Neo4j for {original_name}: {str(neo4j_error)}"
                    )
                    neo4j_deleted = False
                    failed_count += 1
                    db_session.close()
                    continue

                if not neo4j_deleted:
                    failed_count += 1
                    db_session.close()
                    continue

                # Delete file from filesystem if exists
                if file_path.exists():
                    file_path.unlink()
                    logging.info(f"🗑️ Deleted file from filesystem: {file_path}")

                # Delete from database
                success = db.delete_file(file_id_int)
                if success:
                    logging.info(
                        f"✅ File deleted from queue: ID={file_id_int}, Name={original_name}"
                    )
                    deleted_count += 1
                else:
                    logging.warning(
                        f"⚠️ Failed to delete file from database: ID={file_id_int}, Name={original_name}"
                    )
                    failed_count += 1
                
                db_session.close()
                
            except Exception as file_error:
                logging.error(
                    f"❌ Failed to delete file {file_id_int}: {str(file_error)}"
                )
                import traceback
                logging.error(f"Traceback: {traceback.format_exc()}")
                failed_count += 1
                continue
        
        # Close Neo4j connection
        if graph_connection:
            try:
                if hasattr(graph_connection, 'close'):
                    graph_connection.close()
                logging.info(f"🔌 Neo4j connection closed")
            except Exception as close_error:
                logging.warning(f"⚠️ Error closing Neo4j connection: {str(close_error)}")
        
        logging.info(
            f"✅ Deletion task completed: {deleted_count} deleted, {failed_count} failed"
        )
        
        return {"deleted_count": deleted_count, "failed_count": failed_count}
        
    except Exception as e:
        logging.error(f"❌ Deletion task error: {str(e)}")
        import traceback
        logging.error(f"Traceback: {traceback.format_exc()}")
        raise self.retry(exc=e, countdown=60, max_retries=3)
