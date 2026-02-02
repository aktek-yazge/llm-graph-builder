# -*- coding: utf-8 -*-
"""
Neo4j Full-text Index Creator

Bu script graph veritabanında full-text search index'leri oluşturur.
Full-text indexler fuzzy matching yaparak entity çeşitliliklerini bulur.

Kullanım:
    python scripts/create_fulltext_indexes.py

Örnek Sorgu (index oluşturduktan sonra):
    CALL db.index.fulltext.queryNodes('entity_names', 'allianz~')
    YIELD node, score
    RETURN node.name, node.normalized_name, score
    ORDER BY score DESC
    LIMIT 10
"""

import os
import logging
from neo4j import GraphDatabase

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Full-text index definitions
FULLTEXT_INDEXES = [
    {
        "name": "entity_names",
        "labels": ["Customer", "InsuranceCompany", "Agent", "Company", "Person"],
        "properties": ["name", "normalized_name"],
        "description": "Entity isimlerinde fuzzy search için",
    },
    {
        "name": "entity_aliases",
        "labels": ["Customer", "InsuranceCompany", "Agent", "Company", "Person"],
        "properties": ["aliases"],
        "description": "Entity alias'larında arama için",
    },
    {
        "name": "address_search",
        "labels": ["Address", "Property"],
        "properties": ["full_address", "address", "city", "district"],
        "description": "Adres araması için",
    },
    {
        "name": "policy_search",
        "labels": ["Policy"],
        "properties": ["policy_number", "company_policy_number"],
        "description": "Poliçe numarası araması için",
    },
    {
        "name": "document_content",
        "labels": ["Chunk"],
        "properties": ["text"],
        "description": "Chunk içerik araması için",
    },
]


def create_fulltext_indexes(driver, database: str = "neo4j"):
    """
    Full-text indexleri oluşturur.

    Args:
        driver: Neo4j driver
        database: Veritabanı adı
    """
    with driver.session(database=database) as session:
        # Mevcut indexleri listele
        existing_indexes = set()
        result = session.run("SHOW INDEXES")
        for record in result:
            existing_indexes.add(record.get("name"))

        logger.info(f"📋 Mevcut indexler: {existing_indexes}")

        for index_def in FULLTEXT_INDEXES:
            index_name = index_def["name"]
            labels = index_def["labels"]
            properties = index_def["properties"]
            description = index_def["description"]

            if index_name in existing_indexes:
                logger.info(f"⏭️ Index zaten var: {index_name}")
                continue

            # Label string oluştur
            label_str = "|".join(labels)

            # Property string oluştur
            prop_str = ", ".join([f"n.{p}" for p in properties])

            try:
                # Full-text index oluştur
                query = f"""
                    CREATE FULLTEXT INDEX {index_name} IF NOT EXISTS
                    FOR (n:{label_str})
                    ON EACH [{prop_str}]
                """

                session.run(query)
                logger.info(f"✅ Index oluşturuldu: {index_name} - {description}")

            except Exception as e:
                # Eğer birden fazla label desteklenmiyorsa, tek tek dene
                if "multiple labels" in str(e).lower():
                    logger.warning(
                        f"⚠️ Multi-label index desteklenmiyor, tek label ile deneniyor..."
                    )
                    for label in labels:
                        try:
                            single_name = f"{index_name}_{label.lower()}"
                            query = f"""
                                CREATE FULLTEXT INDEX {single_name} IF NOT EXISTS
                                FOR (n:{label})
                                ON EACH [{prop_str}]
                            """
                            session.run(query)
                            logger.info(f"✅ Index oluşturuldu: {single_name}")
                        except Exception as e2:
                            logger.error(f"❌ Index oluşturulamadı {single_name}: {e2}")
                else:
                    logger.error(f"❌ Index oluşturulamadı {index_name}: {e}")


def test_fulltext_search(driver, database: str = "neo4j"):
    """
    Full-text search'ün çalıştığını test eder.
    """
    test_queries = [
        # Fuzzy search (~)
        ("entity_names", "allianz~"),
        ("entity_names", "ahmet~"),
        # Wildcard search (*)
        ("entity_names", "alli*"),
        # Phrase search
        ("address_search", "istanbul kadikoy"),
    ]

    with driver.session(database=database) as session:
        for index_name, search_term in test_queries:
            try:
                result = session.run(
                    f"""
                    CALL db.index.fulltext.queryNodes('{index_name}', $search_term)
                    YIELD node, score
                    RETURN labels(node)[0] as label, node.name as name, 
                           node.normalized_name as normalized, score
                    ORDER BY score DESC
                    LIMIT 5
                """,
                    {"search_term": search_term},
                )

                records = list(result)
                logger.info(f"\n🔍 '{search_term}' in {index_name}:")
                if records:
                    for r in records:
                        logger.info(
                            f"   [{r['label']}] {r['name']} (normalized: {r['normalized']}) - score: {r['score']:.3f}"
                        )
                else:
                    logger.info("   (no results)")

            except Exception as e:
                logger.warning(f"⚠️ Search failed for {index_name}: {e}")


def main():
    # Environment'tan connection bilgilerini al
    neo4j_uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user = os.environ.get("NEO4J_USER", "neo4j")
    neo4j_password = os.environ.get("NEO4J_PASSWORD", "password")
    neo4j_database = os.environ.get("NEO4J_DATABASE", "neo4j")

    logger.info(f"🔗 Connecting to Neo4j: {neo4j_uri}")

    driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password))

    try:
        # Connection test
        driver.verify_connectivity()
        logger.info("✅ Neo4j connection successful")

        # Index oluştur
        create_fulltext_indexes(driver, neo4j_database)

        # Test et
        logger.info("\n📊 Testing full-text search...")
        test_fulltext_search(driver, neo4j_database)

    finally:
        driver.close()
        logger.info("🔒 Connection closed")


if __name__ == "__main__":
    main()
