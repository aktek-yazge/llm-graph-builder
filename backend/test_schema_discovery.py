#!/usr/bin/env python3
"""
Schema Discovery Test - Sadece keşif fonksiyonlarını test eder
"""

import sys
import os
import logging

# Path ayarla
sys.path.append(os.path.join(os.path.dirname(__file__), "backend"))

from src.domain_agnostic_schema import DomainAgnosticSchemaDiscovery
from langchain_neo4j import Neo4jGraph
from dotenv import load_dotenv

# Environment variables yükle
load_dotenv()

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def setup_neo4j_connection():
    """Neo4j bağlantısını kur"""
    try:
        NEO4J_URI = os.getenv("NEO4J_URI", "bolt://neo4j:7687")
        NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
        NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "langchain")
        NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

        logger.info(f"🔌 Neo4j bağlantısı kuruluyor: {NEO4J_URI}")
        
        graph = Neo4jGraph(
            url=NEO4J_URI,
            username=NEO4J_USERNAME,
            password=NEO4J_PASSWORD,
            database=NEO4J_DATABASE,
            refresh_schema=False
        )
        
        logger.info("✅ Neo4j bağlantısı başarılı")
        return graph
        
    except Exception as e:
        logger.error(f"❌ Neo4j bağlantı hatası: {e}")
        return None

def main():
    """Ana test fonksiyonu"""
    print("🔍 === SCHEMA DISCOVERY TEST ===")
    
    # Neo4j bağlantısı
    graph = setup_neo4j_connection()
    if not graph:
        print("❌ Neo4j bağlantısı kurulamadı")
        return False
    
    try:
        # Schema discoverer oluştur
        discoverer = DomainAgnosticSchemaDiscovery(graph)
        
        # 1. Entity type'ları keşfet
        print("\n📊 Entity Types:")
        entity_types = discoverer.discover_entity_subtypes()  # Gerçekte type'ları keşfediyor
        for i, entity_type in enumerate(entity_types, 1):
            print(f"  {i}. {entity_type}")
        
        # 2. Relation type'ları keşfet
        print(f"\n🔗 Relation Types:")
        relation_types = discoverer.discover_relation_types()
        for i, rel_type in enumerate(relation_types, 1):
            print(f"  {i}. {rel_type}")
        
        # 3. Full schema discovery
        print(f"\n🎯 Full Schema Discovery:")
        schema_info = discoverer.discover_full_domain_schema()
        stats = schema_info["statistics"]
        print(f"  📈 Total Entities: {stats['total_entities']:,}")
        print(f"  🔗 Total Relations: {stats['total_relations']:,}")
        print(f"  🏷️ Unique Entity Types: {stats['unique_entity_types']}")
        print(f"  📋 Unique Relation Types: {stats['unique_relation_types']}")
        
        # 4. LLM Prompt preview
        print(f"\n📋 LLM Schema Prompt (ilk 1000 karakter):")
        llm_prompt = discoverer.generate_llm_schema_prompt()
        print(llm_prompt[:1000] + "...")
        
        if len(entity_types) > 0:
            print(f"\n✅ Schema discovery başarılı!")
            print(f"🎯 Keşfedilen Entity Types: {', '.join(entity_types[:5])}...")
            print(f"🔗 Keşfedilen Relations: {', '.join(relation_types)}")
            return True
        else:
            print(f"\n⚠️ Entity type'lar bulunamadı")
            return False
        
    except Exception as e:
        logger.error(f"❌ Schema discovery test hatası: {e}")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)