"""
Migration script to replace BELONGS_TO_SAME_PERSON relationships with HAS_POLICY relationships
"""
import logging
from src.shared.common_fn import execute_graph_query
from langchain_neo4j import Neo4jGraph
import os

logging.basicConfig(level=logging.INFO)

def cleanup_old_relationships(graph: Neo4jGraph) -> int:
    """
    Eski BELONGS_TO_SAME_PERSON ilişkilerini temizler
    """
    logging.info("Removing old BELONGS_TO_SAME_PERSON relationships...")
    
    cleanup_query = """
    MATCH ()-[r:BELONGS_TO_SAME_PERSON]-()
    DELETE r
    RETURN count(r) AS deleted_relationships
    """
    
    result = execute_graph_query(graph, cleanup_query)
    deleted_count = result[0]['deleted_relationships'] if result else 0
    
    logging.info(f"Deleted {deleted_count} BELONGS_TO_SAME_PERSON relationships")
    return deleted_count

def create_has_policy_relationships(graph: Neo4jGraph) -> int:
    """
    Yeni HAS_POLICY ilişkilerini oluşturur
    """
    logging.info("Creating new HAS_POLICY relationships...")
    
    # Import create_document_relationships function
    from src.make_relationships import create_document_relationships
    
    # Create HAS_POLICY relationships
    results = create_document_relationships(graph)
    
    policy_connections = results.get('person_policy_connections', 0)
    logging.info(f"Created {policy_connections} new HAS_POLICY relationships")
    
    return policy_connections

def verify_migration(graph: Neo4jGraph):
    """
    Migration'ın başarılı olduğunu doğrular
    """
    logging.info("Verifying migration...")
    
    # Check for remaining BELONGS_TO_SAME_PERSON relationships
    old_rels_query = """
    MATCH ()-[r:BELONGS_TO_SAME_PERSON]-()
    RETURN count(r) AS old_relationships
    """
    
    new_rels_query = """
    MATCH (p:Person)-[r:HAS_POLICY]->(d:Document)
    RETURN count(r) AS new_relationships
    """
    
    old_result = execute_graph_query(graph, old_rels_query)
    new_result = execute_graph_query(graph, new_rels_query)
    
    old_count = old_result[0]['old_relationships'] if old_result else 0
    new_count = new_result[0]['new_relationships'] if new_result else 0
    
    logging.info(f"Remaining BELONGS_TO_SAME_PERSON relationships: {old_count}")
    logging.info(f"New HAS_POLICY relationships: {new_count}")
    
    if old_count == 0 and new_count > 0:
        logging.info("✅ Migration completed successfully!")
    else:
        logging.warning("⚠️  Migration may not be complete")

def run_migration():
    """
    Complete migration process
    """
    try:
        # Initialize Neo4j connection
        NEO4J_URI = os.getenv('NEO4J_URI')
        NEO4J_USERNAME = os.getenv('NEO4J_USERNAME') 
        NEO4J_PASSWORD = os.getenv('NEO4J_PASSWORD')
        
        graph = Neo4jGraph(
            url=NEO4J_URI,
            username=NEO4J_USERNAME,
            password=NEO4J_PASSWORD
        )
        
        logging.info("🚀 Starting relationship migration...")
        
        # Step 1: Create new HAS_POLICY relationships
        new_relationships = create_has_policy_relationships(graph)
        
        # Step 2: Clean up old relationships
        deleted_relationships = cleanup_old_relationships(graph)
        
        # Step 3: Verify migration
        verify_migration(graph)
        
        logging.info(f"📊 Migration Summary:")
        logging.info(f"   - Created: {new_relationships} HAS_POLICY relationships")
        logging.info(f"   - Deleted: {deleted_relationships} BELONGS_TO_SAME_PERSON relationships")
        
    except Exception as e:
        logging.error(f"Migration failed: {e}")
        raise

if __name__ == "__main__":
    run_migration()
