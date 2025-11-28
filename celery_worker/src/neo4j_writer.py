"""
Neo4j Write Queue - Async graph database writes via RabbitMQ

This module implements a dedicated task for writing to Neo4j,
reducing connection pool pressure and ensuring data persistence
even if the main worker crashes.

Features:
- Single persistent connection (no bolt pool exhaustion)
- Batch writes for better performance
- acks_late=True for guaranteed delivery
- Automatic retry on failure
"""

import logging
import os
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from neo4j.exceptions import ServiceUnavailable, SessionExpired, TransientError

from src.celery_app import app

logger = logging.getLogger(__name__)

# Singleton Neo4j driver instance
_neo4j_driver = None


def get_neo4j_driver():
    """Get or create singleton Neo4j driver"""
    global _neo4j_driver
    
    if _neo4j_driver is None:
        from neo4j import GraphDatabase
        
        uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
        username = os.environ.get("NEO4J_USERNAME", "neo4j")
        password = os.environ.get("NEO4J_PASSWORD", "password")
        
        _neo4j_driver = GraphDatabase.driver(
            uri,
            auth=(username, password),
            max_connection_lifetime=3600,
            max_connection_pool_size=10,
            connection_acquisition_timeout=60,
        )
        logger.info(f"🔗 Neo4j Writer: Connected to {uri}")
    
    return _neo4j_driver


def close_neo4j_driver():
    """Close the Neo4j driver (call on shutdown)"""
    global _neo4j_driver
    if _neo4j_driver:
        _neo4j_driver.close()
        _neo4j_driver = None
        logger.info("🔌 Neo4j Writer: Connection closed")


@app.task(
    bind=True,
    name="src.neo4j_writer.neo4j_write_task",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=10,
    default_retry_delay=5,
)
def neo4j_write_task(self, operation: str, params: Dict[str, Any], database: str = None):
    """
    Execute a single write operation on Neo4j.
    
    Args:
        operation: The operation type (e.g., "create_chunk", "create_relationship")
        params: Parameters for the operation
        database: Neo4j database name (optional, uses default if not specified)
        
    Example:
        neo4j_write_task.delay("create_chunk", {
            "chunk_id": "doc1_chunk_0",
            "text": "...",
            "embedding": [...]
        })
    """
    driver = get_neo4j_driver()
    database = database or os.environ.get("NEO4J_DATABASE", "neo4j")
    
    try:
        with driver.session(database=database) as session:
            result = _execute_operation(session, operation, params)
            logger.info(f"✅ Neo4j Write: {operation} completed - {params.get('chunk_id', params.get('file_name', 'unknown'))}")
            return {"status": "success", "operation": operation, "result": result}
            
    except (ServiceUnavailable, SessionExpired, TransientError) as e:
        logger.error(f"❌ Neo4j Write: Transient error for {operation}: {e}")
        # Retry with exponential backoff
        raise self.retry(exc=e, countdown=min(5 * (2 ** self.request.retries), 120))
        
    except Exception as e:
        logger.error(f"❌ Neo4j Write: Error for {operation}: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise self.retry(exc=e, countdown=10)


@app.task(
    bind=True,
    name="src.neo4j_writer.neo4j_batch_write_task",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=10,
    default_retry_delay=5,
)
def neo4j_batch_write_task(self, operations: List[Dict[str, Any]], database: str = None):
    """
    Execute multiple write operations on Neo4j in a single transaction.
    
    Args:
        operations: List of operations, each containing:
            - operation: The operation type
            - params: Parameters for the operation
        database: Neo4j database name
        
    Example:
        neo4j_batch_write_task.delay([
            {"operation": "create_chunk", "params": {...}},
            {"operation": "create_relationship", "params": {...}}
        ])
    """
    if not operations:
        return {"status": "skipped", "reason": "no_operations"}
    
    driver = get_neo4j_driver()
    database = database or os.environ.get("NEO4J_DATABASE", "neo4j")
    
    try:
        with driver.session(database=database) as session:
            results = []
            
            # Execute all operations in a single transaction
            def tx_function(tx):
                tx_results = []
                for op in operations:
                    operation = op.get("operation")
                    params = op.get("params", {})
                    result = _execute_operation_tx(tx, operation, params)
                    tx_results.append({
                        "operation": operation,
                        "status": "success",
                        "result": result
                    })
                return tx_results
            
            results = session.execute_write(tx_function)
            
            logger.info(f"✅ Neo4j Batch Write: Completed {len(operations)} operations")
            return {"status": "success", "results": results}
            
    except (ServiceUnavailable, SessionExpired, TransientError) as e:
        logger.error(f"❌ Neo4j Batch Write: Transient error: {e}")
        raise self.retry(exc=e, countdown=min(5 * (2 ** self.request.retries), 120))
        
    except Exception as e:
        logger.error(f"❌ Neo4j Batch Write: Error: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise self.retry(exc=e, countdown=10)


def _execute_operation(session, operation: str, params: Dict[str, Any]) -> Any:
    """Execute a single operation within a session"""
    def tx_function(tx):
        return _execute_operation_tx(tx, operation, params)
    return session.execute_write(tx_function)


def _execute_operation_tx(tx, operation: str, params: Dict[str, Any]) -> Any:
    """Execute a single operation within a transaction"""
    
    if operation == "create_document":
        # Create or merge a Document node
        query = """
        MERGE (d:Document {fileName: $file_name})
        SET d.fileSource = $file_source,
            d.fileType = $file_type,
            d.fileSize = $file_size,
            d.processingTime = $processing_time,
            d.createdAt = datetime($created_at),
            d.updatedAt = datetime()
        RETURN d.fileName as fileName
        """
        result = tx.run(query, **params)
        record = result.single()
        return {"fileName": record["fileName"]} if record else None
        
    elif operation == "create_chunk":
        # Create or merge a Chunk node
        query = """
        MERGE (c:Chunk {id: $chunk_id})
        SET c.text = $text,
            c.position = $position,
            c.length = $length,
            c.fileName = $file_name,
            c.embedding = $embedding
        RETURN c.id as chunkId
        """
        result = tx.run(query, **params)
        record = result.single()
        return {"chunkId": record["chunkId"]} if record else None
        
    elif operation == "create_chunk_relationship":
        # Create relationship between Document and Chunk
        query = """
        MATCH (d:Document {fileName: $file_name})
        MATCH (c:Chunk {id: $chunk_id})
        MERGE (d)-[r:HAS_CHUNK]->(c)
        SET r.position = $position
        RETURN type(r) as relationship
        """
        result = tx.run(query, **params)
        record = result.single()
        return {"relationship": record["relationship"]} if record else None
        
    elif operation == "create_first_chunk_relationship":
        # Create FIRST_CHUNK relationship
        query = """
        MATCH (d:Document {fileName: $file_name})
        MATCH (c:Chunk {id: $chunk_id})
        MERGE (d)-[r:FIRST_CHUNK]->(c)
        RETURN type(r) as relationship
        """
        result = tx.run(query, **params)
        record = result.single()
        return {"relationship": record["relationship"]} if record else None
        
    elif operation == "create_next_chunk_relationship":
        # Create NEXT_CHUNK relationship between chunks
        query = """
        MATCH (c1:Chunk {id: $from_chunk_id})
        MATCH (c2:Chunk {id: $to_chunk_id})
        MERGE (c1)-[r:NEXT_CHUNK]->(c2)
        RETURN type(r) as relationship
        """
        result = tx.run(query, **params)
        record = result.single()
        return {"relationship": record["relationship"]} if record else None
        
    elif operation == "create_entity":
        # Create or merge an Entity node
        query = """
        MERGE (e:Entity {id: $entity_id})
        SET e.name = $name,
            e.type = $type,
            e.description = $description
        RETURN e.id as entityId
        """
        result = tx.run(query, **params)
        record = result.single()
        return {"entityId": record["entityId"]} if record else None
        
    elif operation == "create_entity_relationship":
        # Create relationship between Chunk and Entity
        query = """
        MATCH (c:Chunk {id: $chunk_id})
        MATCH (e:Entity {id: $entity_id})
        MERGE (c)-[r:MENTIONS]->(e)
        RETURN type(r) as relationship
        """
        result = tx.run(query, **params)
        record = result.single()
        return {"relationship": record["relationship"]} if record else None
        
    elif operation == "update_chunk_embedding":
        # Update chunk embedding
        query = """
        MATCH (c:Chunk {id: $chunk_id})
        SET c.embedding = $embedding
        RETURN c.id as chunkId
        """
        result = tx.run(query, **params)
        record = result.single()
        return {"chunkId": record["chunkId"]} if record else None
        
    elif operation == "delete_document":
        # Delete document and all related nodes
        query = """
        MATCH (d:Document {fileName: $file_name})
        OPTIONAL MATCH (d)-[:HAS_CHUNK]->(c:Chunk)
        DETACH DELETE d, c
        RETURN count(c) as deletedChunks
        """
        result = tx.run(query, **params)
        record = result.single()
        return {"deletedChunks": record["deletedChunks"]} if record else None
        
    elif operation == "custom_query":
        # Execute a custom Cypher query
        query = params.get("query")
        query_params = params.get("params", {})
        result = tx.run(query, **query_params)
        records = [dict(record) for record in result]
        return {"records": records, "count": len(records)}
        
    else:
        logger.warning(f"⚠️ Unknown Neo4j operation: {operation}")
        return {"status": "unknown_operation"}


# ============================================================================
# Helper functions for enqueueing writes from other tasks
# ============================================================================

def enqueue_neo4j_write(operation: str, params: Dict[str, Any], database: str = None, priority: int = 5):
    """
    Enqueue a Neo4j write operation.
    
    Args:
        operation: The operation type
        params: Parameters for the operation
        database: Neo4j database name
        priority: Task priority (0-9, lower is higher priority)
        
    Returns:
        AsyncResult for the enqueued task
    """
    return neo4j_write_task.apply_async(
        args=[operation, params, database],
        priority=priority,
        queue="neo4j_write",  # Dedicated queue for Neo4j writes
    )


def enqueue_neo4j_batch_write(operations: List[Dict[str, Any]], database: str = None, priority: int = 5):
    """
    Enqueue a batch Neo4j write operation.
    
    Args:
        operations: List of operations
        database: Neo4j database name
        priority: Task priority
        
    Returns:
        AsyncResult for the enqueued task
    """
    return neo4j_batch_write_task.apply_async(
        args=[operations, database],
        priority=priority,
        queue="neo4j_write",
    )


# Convenience functions for common operations
def enqueue_create_document(file_name: str, file_source: str = "local", file_type: str = "pdf",
                           file_size: int = 0, processing_time: float = 0, database: str = None):
    """Create or update a Document node"""
    return enqueue_neo4j_write("create_document", {
        "file_name": file_name,
        "file_source": file_source,
        "file_type": file_type,
        "file_size": file_size,
        "processing_time": processing_time,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }, database)


def enqueue_create_chunk(chunk_id: str, text: str, position: int, file_name: str,
                        embedding: List[float] = None, database: str = None):
    """Create or update a Chunk node"""
    return enqueue_neo4j_write("create_chunk", {
        "chunk_id": chunk_id,
        "text": text,
        "position": position,
        "length": len(text),
        "file_name": file_name,
        "embedding": embedding or [],
    }, database)


def enqueue_create_chunk_relationship(file_name: str, chunk_id: str, position: int, database: str = None):
    """Create HAS_CHUNK relationship between Document and Chunk"""
    return enqueue_neo4j_write("create_chunk_relationship", {
        "file_name": file_name,
        "chunk_id": chunk_id,
        "position": position,
    }, database)


def enqueue_create_first_chunk_relationship(file_name: str, chunk_id: str, database: str = None):
    """Create FIRST_CHUNK relationship"""
    return enqueue_neo4j_write("create_first_chunk_relationship", {
        "file_name": file_name,
        "chunk_id": chunk_id,
    }, database)


def enqueue_create_next_chunk_relationship(from_chunk_id: str, to_chunk_id: str, database: str = None):
    """Create NEXT_CHUNK relationship between chunks"""
    return enqueue_neo4j_write("create_next_chunk_relationship", {
        "from_chunk_id": from_chunk_id,
        "to_chunk_id": to_chunk_id,
    }, database)


def enqueue_update_chunk_embedding(chunk_id: str, embedding: List[float], database: str = None):
    """Update chunk embedding"""
    return enqueue_neo4j_write("update_chunk_embedding", {
        "chunk_id": chunk_id,
        "embedding": embedding,
    }, database, priority=7)  # Lower priority for embeddings


def enqueue_delete_document(file_name: str, database: str = None):
    """Delete document and all related nodes"""
    return enqueue_neo4j_write("delete_document", {
        "file_name": file_name,
    }, database, priority=3)  # Higher priority for deletions


def enqueue_custom_query(query: str, params: Dict[str, Any] = None, database: str = None):
    """Execute a custom Cypher query"""
    return enqueue_neo4j_write("custom_query", {
        "query": query,
        "params": params or {},
    }, database)

