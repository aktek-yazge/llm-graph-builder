#!/usr/bin/env python3
"""
Entity Resolution Debug Test Script
"""

import os
import sys
sys.path.append('/Users/mehmeterdogan/python-projects/llm-graph-builder/backend/src')

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

from entity_resolver import resolve_entity_before_creation
from neo4j import GraphDatabase
import logging

logging.basicConfig(level=logging.DEBUG)

# Neo4j connection setup from environment
NEO4J_URI = os.getenv('NEO4J_URI')
NEO4J_USERNAME = os.getenv('NEO4J_USERNAME')
NEO4J_PASSWORD = os.getenv('NEO4J_PASSWORD')

print(f"Neo4j URI: {NEO4J_URI}")

def test_entity_resolution():
    print("🧪 Entity Resolution Debug Test")
    
    # Neo4j driver setup
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))
    
    # Test entity
    test_entity = {
        'id': 'Ayça Dinçkök',
        'name': 'Ayça Dinçkök',
        'entity_type': 'Person'
    }
    
    print(f"🔍 Test entity: {test_entity}")
    
    # Test entity resolution
    try:
        with driver.session() as session:
            # Check existing entities
            existing_query = """
            MATCH (p:Person) 
            RETURN p.name as name, labels(p) as labels, properties(p) as props
            """
            existing_result = session.run(existing_query)
            existing_persons = list(existing_result)
            
            print(f"📊 Existing Person entities: {len(existing_persons)}")
            for person in existing_persons:
                print(f"  - {person['name']}: {person['props']}")
            
            # Test resolution
            print(f"\n🔧 Testing entity resolution...")
            resolved_id = resolve_entity_before_creation(
                test_entity, 
                session, 
                entity_type='Person'
            )
            
            print(f"✅ Resolution result: {resolved_id}")
            
            if resolved_id:
                print(f"🔗 Entity resolved to existing: {resolved_id}")
            else:
                print("🆕 New entity will be created")
    
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        driver.close()

if __name__ == "__main__":
    test_entity_resolution()
