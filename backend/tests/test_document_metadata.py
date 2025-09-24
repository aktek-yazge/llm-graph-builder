#!/usr/bin/env python3
"""
Test script to check document metadata entities creation and retrieval
"""

import os
import sys
sys.path.append('/home/ubuntu/llm-graph-builder/backend')

from dotenv import load_dotenv
from src.shared.common_fn import create_graph_database_connection
from src.make_relationships import create_document_metadata_entities
from src.shared.constants import VECTOR_GRAPH_SEARCH_QUERY

load_dotenv('/home/ubuntu/llm-graph-builder/.env')

def test_document_metadata():
    # Database connection
    uri = os.getenv('NEO4J_URL')
    username = os.getenv('NEO4J_USERNAME') 
    password = os.getenv('NEO4J_PASSWORD')
    database = os.getenv('NEO4J_DATABASE')
    
    print(f"Connecting to: {uri}")
    
    try:
        graph = create_graph_database_connection(uri, username, password, database)
        print("✅ Database connection successful!")
        
        # Check existing documents
        result = graph.query('MATCH (d:Document) RETURN d.fileName LIMIT 10')
        print("\n📄 Documents in database:")
        for doc in result:
            print(f"  - {doc['fileName']}")
        
        # Look for Ayça Dinçkök document specifically
        ayca_query = """
        MATCH (d:Document) 
        WHERE d.fileName CONTAINS 'Ayça' OR d.fileName CONTAINS 'Dinçkök'
        RETURN d.fileName, d.fileSize, d.createdAt
        """
        ayca_docs = graph.query(ayca_query)
        
        if ayca_docs:
            print(f"\n🔍 Found Ayça Dinçkök document:")
            for doc in ayca_docs:
                file_name = doc['fileName']
                print(f"  File: {file_name}")
                print(f"  Size: {doc['fileSize']}")
                print(f"  Created: {doc['createdAt']}")
                
                # Check existing metadata entities
                metadata_query = """
                MATCH (d:Document {fileName: $fileName})-[:HAS_METADATA]->(meta:__Entity__)
                RETURN meta.id as metadataValue, labels(meta) as metadataType
                """
                existing_metadata = graph.query(metadata_query, {"fileName": file_name})
                
                print(f"\n📊 Existing metadata entities for {file_name}:")
                if existing_metadata:
                    for meta in existing_metadata:
                        print(f"  - {meta['metadataType']}: {meta['metadataValue']}")
                else:
                    print("  ❌ No metadata entities found - creating them...")
                    create_document_metadata_entities(graph, file_name)
                    
                    # Check again after creation
                    new_metadata = graph.query(metadata_query, {"fileName": file_name})
                    print(f"\n📊 New metadata entities created:")
                    for meta in new_metadata:
                        print(f"  - {meta['metadataType']}: {meta['metadataValue']}")
                
                # Test chunk page numbers
                chunk_page_query = """
                MATCH (d:Document {fileName: $fileName})<-[:PART_OF]-(c:Chunk)
                WHERE c.page_number IS NOT NULL
                RETURN min(c.page_number) as minPage, max(c.page_number) as maxPage, 
                       count(DISTINCT c.page_number) as distinctPages, count(c) as totalChunks
                """
                chunk_info = graph.query(chunk_page_query, {"fileName": file_name})
                if chunk_info:
                    info = chunk_info[0]
                    print(f"\n📑 Page information:")
                    print(f"  - Min page: {info['minPage']}")
                    print(f"  - Max page: {info['maxPage']}")
                    print(f"  - Distinct pages: {info['distinctPages']}")
                    print(f"  - Total chunks: {info['totalChunks']}")
                
        else:
            print("\n❌ Ayça Dinçkök document not found in database")
            
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_document_metadata()
