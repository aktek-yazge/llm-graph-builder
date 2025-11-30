# -*- coding: utf-8 -*-
"""
Status synchronization between Neo4j Document nodes and SQLite queue database
Maps v2 file queue statuses to Neo4j Document node statuses
"""

from enum import Enum
from typing import Optional, Tuple
import logging

# V2 Queue Database Statuses
class V2QueueStatus(str, Enum):
    """V2 File Queue Status (SQLite)"""
    # Upload stage
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    
    # Chunking stage
    CHUNKING = "chunking"
    CHUNKED = "chunked"
    
    # Graph creation stage
    GRAPH_PROCESSING = "processing"
    GRAPH_COMPLETED = "completed"
    
    # Embedding stage
    EMBEDDING_PROCESSING = "processing"
    EMBEDDING_COMPLETED = "completed"
    
    # Error states
    FAILED = "failed"


# Neo4j Document Node Statuses
class Neo4jDocumentStatus(str, Enum):
    """Neo4j Document Node Status"""
    NEW = "New"
    PROCESSING = "Processing"
    CHUNKED = "Chunked"
    COMPLETED = "Completed"
    FAILED = "Failed"
    CANCELLED = "Cancelled"
    PARTIALLY_COMPLETED = "Partially Completed"
    PARTIALLY_FAILED = "Partially Failed"


class StatusMapper:
    """Maps between V2 queue statuses and Neo4j document statuses"""
    
    @staticmethod
    def get_neo4j_status_from_v2_stages(
        upload_status: str,
        chunking_status: str,
        graph_status: str,
        embedding_status: str,
        has_errors: bool = False
    ) -> Neo4jDocumentStatus:
        """
        Convert V2 workflow statuses to Neo4j Document status
        
        Args:
            upload_status: "uploading", "uploaded", "failed"
            chunking_status: "pending", "chunking", "chunked", "failed"
            graph_status: "pending", "processing", "completed", "failed"
            embedding_status: "pending", "processing", "completed", "failed"
            has_errors: Whether processing has encountered errors
        
        Returns:
            Neo4jDocumentStatus value for Document node
        """
        
        # If upload failed
        if upload_status == "failed":
            return Neo4jDocumentStatus.FAILED
        
        # If chunking failed
        if chunking_status == "failed":
            return Neo4jDocumentStatus.FAILED
        
        # If graph creation failed
        if graph_status == "failed":
            return Neo4jDocumentStatus.PARTIALLY_FAILED
        
        # If embedding failed
        if embedding_status == "failed":
            return Neo4jDocumentStatus.PARTIALLY_FAILED
        
        # If still uploading
        if upload_status == "uploading":
            return Neo4jDocumentStatus.PROCESSING
        
        # If uploaded but chunking pending (reset durumu)
        if (upload_status == "uploaded" and 
            chunking_status == "pending"):
            return Neo4jDocumentStatus.NEW
        
        # If chunking in progress
        if chunking_status == "chunking":
            return Neo4jDocumentStatus.PROCESSING
        
        # If graph creation in progress
        if graph_status == "processing":
            return Neo4jDocumentStatus.PROCESSING
        
        # If embedding in progress
        if embedding_status == "processing":
            return Neo4jDocumentStatus.PROCESSING
        
        # If everything completed successfully
        if (upload_status == "uploaded" and
            chunking_status == "chunked" and
            graph_status == "completed" and
            embedding_status == "completed"):
            return Neo4jDocumentStatus.COMPLETED
        
        # Partial completion (graph done but embedding pending/not started)
        if (chunking_status == "chunked" and 
            graph_status == "completed" and
            embedding_status in ["pending", "processing"]):
            return Neo4jDocumentStatus.COMPLETED
        
        # Chunking completed, ready for graph creation
        if (chunking_status == "chunked" and
            graph_status == "pending"):
            return Neo4jDocumentStatus.CHUNKED
        
        # Partial completion (chunking done, graph in progress)
        if (chunking_status == "chunked" and
            graph_status == "processing"):
            return Neo4jDocumentStatus.PROCESSING
        
        # Default to processing if unclear
        return Neo4jDocumentStatus.PROCESSING
    
    @staticmethod
    def get_v2_stage_status_from_neo4j(neo4j_status: str) -> Tuple[str, str, str, str]:
        """
        Reverse mapping: Convert Neo4j status back to V2 stages
        (Useful for reading back from Neo4j to update queue DB)
        
        Returns: (upload_status, chunking_status, graph_status, embedding_status)
        """
        
        if neo4j_status == Neo4jDocumentStatus.PROCESSING.value:
            # Assume currently processing graph
            return ("uploaded", "chunked", "processing", "pending")
        
        elif neo4j_status == Neo4jDocumentStatus.COMPLETED.value:
            # All stages completed
            return ("uploaded", "chunked", "completed", "completed")
        
        elif neo4j_status == Neo4jDocumentStatus.PARTIALLY_COMPLETED.value:
            # Graph completed, embedding not done
            return ("uploaded", "chunked", "completed", "pending")
        
        elif neo4j_status == Neo4jDocumentStatus.FAILED.value:
            # Failed at some stage
            return ("uploaded", "chunked", "failed", "pending")
        
        elif neo4j_status == Neo4jDocumentStatus.PARTIALLY_FAILED.value:
            # Partially failed
            return ("uploaded", "chunked", "completed", "failed")
        
        elif neo4j_status == Neo4jDocumentStatus.CANCELLED.value:
            # Cancelled
            return ("uploaded", "chunked", "failed", "pending")
        
        # Default
        return ("uploaded", "chunked", "processing", "pending")


# Status mapping documentation
STATUS_MAPPING_DOC = """
Neo4j Document Status <-> V2 Queue Status Mapping

┌─────────────────────────────────────────────────────────────────┐
│ V2 Queue Stages → Neo4j Document Status                         │
├─────────────────────────────────────────────────────────────────┤
│ Upload → Chunking → Graph Creation → Embedding                  │
│                                                                  │
│ ✅ All Completed                 → "Completed"                  │
│ ⏳ Any stage processing          → "Processing"                 │
│ ✅ Graph done, embedding pending → "Completed"                  │
│ ⚠️  Graph done, embedding failed  → "Partially Failed"          │
│ ❌ Any stage failed              → "Failed" / "Partially Failed"│
│ 🚫 Cancelled                     → "Cancelled"                  │
└─────────────────────────────────────────────────────────────────┘

Detailed Stage Statuses:

upload_status:      "uploading", "uploaded", "failed"
chunking_status:    "pending", "chunking", "chunked", "failed"
graph_status:       "pending", "processing", "completed", "failed"
embedding_status:   "pending", "processing", "completed", "failed"
"""

def sync_neo4j_status_to_queue_db(
    graph,
    file_name: str,
    db_session,
    UploadedFile
) -> Optional[dict]:
    """
    Read current status from Neo4j Document node and sync to queue DB
    
    Args:
        graph: Neo4j graph connection
        file_name: Document file name
        db_session: SQLAlchemy session
        UploadedFile: ORM model class
    
    Returns:
        Status dictionary with sync details
    """
    try:
        # Query Neo4j Document node
        query = """
            MATCH (d:Document {fileName: $file_name})
            RETURN d.status AS neo4j_status, 
                   d.errorMessage AS error_message,
                   d.processingTime AS processing_time
        """
        
        result = graph.query(query, {"file_name": file_name})
        
        if not result:
            logging.warning(f"Document not found in Neo4j: {file_name}")
            return None
        
        neo4j_status = result[0].get('neo4j_status')
        
        # Convert to V2 stages
        upload_status, chunking_status, graph_status, embedding_status = \
            StatusMapper.get_v2_stage_status_from_neo4j(neo4j_status)
        
        # Update queue DB record
        file_record = db_session.query(UploadedFile).filter(
            UploadedFile.filename == file_name
        ).first()
        
        if file_record:
            file_record.upload_status = upload_status
            file_record.chunking_status = chunking_status
            file_record.graph_status = graph_status
            file_record.embedding_status = embedding_status
            db_session.commit()
            
            logging.info(f"✅ Synced Neo4j status to queue DB: {file_name}")
            logging.info(f"   Upload: {upload_status}, Chunking: {chunking_status}, "
                        f"Graph: {graph_status}, Embedding: {embedding_status}")
            
            return {
                "file_name": file_name,
                "neo4j_status": neo4j_status,
                "upload_status": upload_status,
                "chunking_status": chunking_status,
                "graph_status": graph_status,
                "embedding_status": embedding_status
            }
        else:
            logging.warning(f"File record not found in queue DB: {file_name}")
            return None
    
    except Exception as e:
        logging.error(f"Error syncing Neo4j status to queue DB: {str(e)}")
        raise


def sync_queue_db_status_to_neo4j(
    graph,
    file_name: str,
    upload_status: str = None,
    chunking_status: str = None,
    graph_status: str = None,
    embedding_status: str = None,
    database: str = None
) -> Optional[Neo4jDocumentStatus]:
    """
    Update Neo4j Document node status based on queue DB stages
    
    Args:
        graph: Neo4j graph connection
        file_name: Document file name
        upload_status: Current upload status
        chunking_status: Current chunking status
        graph_status: Current graph creation status
        embedding_status: Current embedding status
        database: Neo4j database name
    
    Returns:
        The Neo4j status that was set
    """
    try:
        # Calculate Neo4j status from stages
        neo4j_status = StatusMapper.get_neo4j_status_from_v2_stages(
            upload_status or "pending",
            chunking_status or "pending",
            graph_status or "pending",
            embedding_status or "pending"
        )
        
        logging.info(f"📊 Calculating Neo4j status: file={file_name}, "
                    f"upload={upload_status}, chunking={chunking_status}, "
                    f"graph={graph_status}, embedding={embedding_status} "
                    f"→ result={neo4j_status.value}")
        
        # Update Neo4j Document node
        query = """
            MERGE (d:Document {fileName: $file_name})
            SET d.status = $status,
                d.updatedAt = datetime(),
                d.lastSyncedAt = datetime()
            RETURN d.status AS updated_status
        """
        
        session_params = {}
        if database:
            session_params["database"] = database
        
        result = graph.query(
            query,
            {
                "file_name": file_name,
                "status": neo4j_status.value
            },
            session_params=session_params
        )
        
        logging.info(f"✅ Synced queue DB status to Neo4j: {file_name} → {neo4j_status.value}")
        if result:
            logging.info(f"   Neo4j response: {result[0]}")
        
        return neo4j_status
    
    except Exception as e:
        logging.error(f"Error syncing queue DB status to Neo4j: {str(e)}")
        raise

