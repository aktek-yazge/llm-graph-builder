#!/usr/bin/env python3

"""
Policy node yaratma testleri
"""

import os
import sys
import logging
from dotenv import load_dotenv

# Backend dizinini Python path'e ekle
sys.path.append('/Users/mehmeterdogan/python-projects/llm-graph-builder/backend')

# Environment variables
load_dotenv()

from langchain_neo4j import Neo4jGraph
from src.graphDB_dataAccess import graphDBdataAccess

# Logging configuration
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def test_policy_creation():
    """
    Policy node yaratma işlevini test eder
    """
    
    # Neo4j connection
    url = os.getenv("NEO4J_URI")
    username = os.getenv("NEO4J_USERNAME")
    password = os.getenv("NEO4J_PASSWORD")
    database = os.getenv("NEO4J_DATABASE") or "neo4j"
    
    print(f"Neo4j Connection: {url} / {database}")
    
    try:
        graph = Neo4jGraph(
            url=url,
            username=username,
            password=password,
            database=database
        )
        
        db_access = graphDBdataAccess(graph)
        
        # Test dosya isimleri
        test_files = [
            "Ayça Dinçkök Galata Residance D4 Konut Poliçesi.pdf",
            "Ayşe Semin Çiftçi Bebek Ankara Apt No21 D7 Dask Poliçesi.pdf",
            "Mehmet ERDOĞAN Kasko Poliçesi 2024.pdf",
            "Test Insurance Policy.pdf"
        ]
        
        for file_name in test_files:
            print(f"\n{'='*60}")
            print(f"TEST: {file_name}")
            print(f"{'='*60}")
            
            # Document node oluştur (bu otomatik olarak Policy node'u da yaratacak)
            db_access.create_source_node(file_name)
            
            # Sonuçları kontrol et
            check_query = """
                MATCH (d:Document {fileName: $file_name})
                OPTIONAL MATCH (d)-[r:HAS_METADATA]->(p:Policy)
                RETURN d.fileName as doc_name, p.id as policy_id, p.name as policy_name, 
                       p.type as policy_type, p.customer as customer_name,
                       count(r) as has_metadata_count
            """
            
            result = graph.query(check_query, {"file_name": file_name})
            
            if result:
                row = result[0]
                print(f"✅ Document: {row['doc_name']}")
                print(f"✅ Policy ID: {row['policy_id']}")
                print(f"✅ Policy Name: {row['policy_name']}")
                print(f"✅ Policy Type: {row['policy_type']}")
                print(f"✅ Customer: {row['customer_name']}")
                print(f"✅ HAS_METADATA ilişkisi: {row['has_metadata_count']} adet")
            else:
                print("❌ Document bulunamadı")
        
        print(f"\n{'='*60}")
        print("GENEL ÖZET")
        print(f"{'='*60}")
        
        # Genel istatistikler
        stats_query = """
            MATCH (d:Document)
            OPTIONAL MATCH (d)-[:HAS_METADATA]->(p:Policy)
            RETURN count(DISTINCT d) as total_documents,
                   count(DISTINCT p) as total_policies,
                   count(DISTINCT CASE WHEN p IS NOT NULL THEN d END) as documents_with_policies
        """
        
        stats = graph.query(stats_query)
        if stats:
            row = stats[0]
            print(f"📊 Toplam Document: {row['total_documents']}")
            print(f"📊 Toplam Policy: {row['total_policies']}")
            print(f"📊 Policy'li Document: {row['documents_with_policies']}")
            
    except Exception as e:
        print(f"❌ Test hatası: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_policy_creation()
