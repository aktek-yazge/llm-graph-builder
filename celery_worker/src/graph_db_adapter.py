"""
Graph Database Adapter - Neo4j and Memgraph abstraction layer

This module provides a unified interface for working with both Neo4j and Memgraph.
The database type is selected via the GRAPH_DB_TYPE environment variable.

Supported values:
- "neo4j" (default)
- "memgraph"
"""

import os
import logging
from typing import Optional, Dict, Any, List
from abc import ABC, abstractmethod

# Database type selection
GRAPH_DB_TYPE = os.environ.get("GRAPH_DB_TYPE", "neo4j").lower()

logger = logging.getLogger(__name__)


class GraphDatabaseAdapter(ABC):
    """Abstract base class for graph database adapters"""
    
    @abstractmethod
    def create_vector_index(self, index_name: str, label: str, property_name: str, 
                           dimension: int, similarity_function: str = "cosine") -> str:
        """Generate CREATE VECTOR INDEX query"""
        pass
    
    @abstractmethod
    def drop_index(self, index_name: str) -> str:
        """Generate DROP INDEX query"""
        pass
    
    @abstractmethod
    def check_vector_index_exists(self, index_name: str, label: str, property_name: str) -> str:
        """Generate query to check if vector index exists"""
        pass
    
    @abstractmethod
    def create_fulltext_index(self, index_name: str, labels: List[str], properties: List[str]) -> str:
        """Generate CREATE FULLTEXT INDEX query"""
        pass
    
    @abstractmethod
    def vector_similarity_search(self, index_name: str, k: int, embedding_param: str = "$embedding") -> str:
        """Generate vector similarity search query"""
        pass
    
    @abstractmethod
    def get_labels_query(self) -> str:
        """Generate query to get all labels"""
        pass
    
    @abstractmethod
    def show_indexes_query(self) -> str:
        """Generate query to show all indexes"""
        pass


class Neo4jAdapter(GraphDatabaseAdapter):
    """Neo4j specific query adapter"""
    
    def create_vector_index(self, index_name: str, label: str, property_name: str, 
                           dimension: int, similarity_function: str = "cosine") -> str:
        return f"""
        CREATE VECTOR INDEX `{index_name}` IF NOT EXISTS
        FOR (n:`{label}`) ON (n.{property_name})
        OPTIONS {{
            indexConfig: {{
                `vector.dimensions`: {dimension},
                `vector.similarity_function`: '{similarity_function}'
            }}
        }}
        """
    
    def drop_index(self, index_name: str) -> str:
        return f"DROP INDEX `{index_name}` IF EXISTS"
    
    def check_vector_index_exists(self, index_name: str, label: str, property_name: str) -> str:
        return f"""
        SHOW INDEXES 
        YIELD name, type, labelsOrTypes, properties 
        WHERE name = '{index_name}' AND type = 'VECTOR' 
        AND '{label}' IN labelsOrTypes AND '{property_name}' IN properties 
        RETURN name
        """
    
    def create_fulltext_index(self, index_name: str, labels: List[str], properties: List[str]) -> str:
        labels_str = "|".join([f"`{l}`" for l in labels])
        props_str = ", ".join([f"n.{p}" for p in properties])
        return f"CREATE FULLTEXT INDEX `{index_name}` IF NOT EXISTS FOR (n:{labels_str}) ON EACH [{props_str}]"
    
    def vector_similarity_search(self, index_name: str, k: int, embedding_param: str = "$embedding") -> str:
        return f"CALL db.index.vector.queryNodes('{index_name}', {k}, {embedding_param}) YIELD node, score"
    
    def get_labels_query(self) -> str:
        return "CALL db.labels() YIELD label RETURN label"
    
    def show_indexes_query(self) -> str:
        return "SHOW INDEXES YIELD name, type, labelsOrTypes, properties, state RETURN *"


class MemgraphAdapter(GraphDatabaseAdapter):
    """Memgraph specific query adapter"""
    
    def create_vector_index(self, index_name: str, label: str, property_name: str, 
                           dimension: int, similarity_function: str = "cosine") -> str:
        # Memgraph uses different syntax and metric names
        metric = "cos" if similarity_function == "cosine" else similarity_function
        return f"""
        CREATE VECTOR INDEX `{index_name}` ON :`{label}`({property_name})
        WITH CONFIG {{
            "dimension": {dimension},
            "capacity": 100000,
            "metric": "{metric}",
            "resize_coefficient": 2
        }}
        """
    
    def drop_index(self, index_name: str) -> str:
        return f"DROP INDEX ON :`{index_name}`"
    
    def check_vector_index_exists(self, index_name: str, label: str, property_name: str) -> str:
        # Memgraph uses SHOW INDEX INFO
        return f"""
        CALL mg.get_module_files('vector_search') YIELD path
        RETURN path
        """
    
    def create_fulltext_index(self, index_name: str, labels: List[str], properties: List[str]) -> str:
        # Memgraph text search uses different approach
        # For each label, create text index
        label = labels[0] if labels else ""
        prop = properties[0] if properties else ""
        return f"CREATE TEXT INDEX ON :`{label}`({prop})"
    
    def vector_similarity_search(self, index_name: str, k: int, embedding_param: str = "$embedding") -> str:
        # Memgraph vector search syntax
        return f"""
        CALL vector_search.search('{index_name}', {k}, {embedding_param}) 
        YIELD node, distance
        WITH node, 1.0 - distance AS score
        """
    
    def get_labels_query(self) -> str:
        return "MATCH (n) RETURN DISTINCT labels(n) AS label"
    
    def show_indexes_query(self) -> str:
        return "SHOW INDEX INFO"


def get_adapter() -> GraphDatabaseAdapter:
    """Get the appropriate database adapter based on environment configuration"""
    if GRAPH_DB_TYPE == "memgraph":
        logger.info("Using Memgraph database adapter")
        return MemgraphAdapter()
    else:
        logger.info("Using Neo4j database adapter")
        return Neo4jAdapter()


# Singleton adapter instance
_adapter: Optional[GraphDatabaseAdapter] = None


def get_db_adapter() -> GraphDatabaseAdapter:
    """Get or create singleton adapter instance"""
    global _adapter
    if _adapter is None:
        _adapter = get_adapter()
    return _adapter


# Convenience functions
def create_vector_index_query(index_name: str, label: str, property_name: str, 
                              dimension: int, similarity_function: str = "cosine") -> str:
    """Generate CREATE VECTOR INDEX query for current database type"""
    return get_db_adapter().create_vector_index(index_name, label, property_name, dimension, similarity_function)


def drop_index_query(index_name: str) -> str:
    """Generate DROP INDEX query for current database type"""
    return get_db_adapter().drop_index(index_name)


def check_vector_index_query(index_name: str, label: str, property_name: str) -> str:
    """Generate check vector index query for current database type"""
    return get_db_adapter().check_vector_index_exists(index_name, label, property_name)


def create_fulltext_index_query(index_name: str, labels: List[str], properties: List[str]) -> str:
    """Generate CREATE FULLTEXT INDEX query for current database type"""
    return get_db_adapter().create_fulltext_index(index_name, labels, properties)


def vector_search_query(index_name: str, k: int, embedding_param: str = "$embedding") -> str:
    """Generate vector similarity search query for current database type"""
    return get_db_adapter().vector_similarity_search(index_name, k, embedding_param)


def get_labels() -> str:
    """Generate get labels query for current database type"""
    return get_db_adapter().get_labels_query()


def show_indexes() -> str:
    """Generate show indexes query for current database type"""
    return get_db_adapter().show_indexes_query()


def is_memgraph() -> bool:
    """Check if using Memgraph database"""
    return GRAPH_DB_TYPE == "memgraph"


def is_neo4j() -> bool:
    """Check if using Neo4j database"""
    return GRAPH_DB_TYPE == "neo4j"


def get_db_type() -> str:
    """Get current database type"""
    return GRAPH_DB_TYPE

