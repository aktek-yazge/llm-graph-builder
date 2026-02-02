# -*- coding: utf-8 -*-
"""
Generic Graph Executor

LLM'den gelen generic nodes/relationships JSON formatını
Neo4j graph veritabanına yazan domain-agnostic modül.

Bu modül sayesinde yeni domain eklemek için kod değişikliği gerekmez,
sadece prompt dosyası güncellenir.
"""

import logging
import re
from typing import Dict, Any, Optional, Set
from langchain_neo4j import Neo4jGraph


def normalize_id(text: str) -> str:
    """
    Metni ID formatına normalize eder.

    - Türkçe karakterleri dönüştürür
    - Boşlukları _ ile değiştirir
    - Küçük harfe çevirir
    - Özel karakterleri kaldırır
    """
    if not text:
        return ""

    # Türkçe karakter dönüşümü
    tr_map = {
        "ı": "i",
        "İ": "I",
        "ğ": "g",
        "Ğ": "G",
        "ü": "u",
        "Ü": "U",
        "ş": "s",
        "Ş": "S",
        "ö": "o",
        "Ö": "O",
        "ç": "c",
        "Ç": "C",
    }

    for tr_char, en_char in tr_map.items():
        text = text.replace(tr_char, en_char)

    # Küçük harfe çevir
    text = text.lower()

    # Sadece alfanumerik ve _ karakterleri tut
    text = re.sub(r"[^a-z0-9_]", "_", text)

    # Ardışık _ karakterlerini tek _ yap
    text = re.sub(r"_+", "_", text)

    # Baş ve sondaki _ karakterlerini kaldır
    text = text.strip("_")

    return text


class GenericGraphExecutor:
    """
    LLM'den gelen generic JSON formatını Neo4j'ye yazan executor.

    Kullanım:
        executor = GenericGraphExecutor(graph)
        result = executor.create_graph_from_llm_output(llm_output, file_name)
    
    İlk çalışmada otomatik olarak entity fulltext index'leri oluşturur.
    """
    
    # Class-level flag: Index kontrolü sadece bir kez yapılır
    _indexes_ensured = False

    def __init__(self, graph: Neo4jGraph):
        """
        Args:
            graph: Neo4j graph bağlantısı (langchain_neo4j.Neo4jGraph)
        """
        self.graph = graph
        self._database = getattr(graph, "_database", None)
    
    def _ensure_entity_indexes(self):
        """
        Entity fulltext index'lerinin varlığını kontrol eder ve gerekirse oluşturur.
        Bu metod sadece ilk çağrıda çalışır (class-level flag ile).
        """
        if GenericGraphExecutor._indexes_ensured:
            return
        
        try:
            from src.make_relationships import create_entity_fulltext_indexes
            create_entity_fulltext_indexes(self.graph)
            GenericGraphExecutor._indexes_ensured = True
            logging.info("✅ Entity fulltext indexes checked/created")
        except ImportError as e:
            logging.warning(f"⚠️ Could not import create_entity_fulltext_indexes: {e}")
            GenericGraphExecutor._indexes_ensured = True  # Tekrar deneme
        except Exception as e:
            logging.warning(f"⚠️ Entity fulltext index creation failed: {e}")
            GenericGraphExecutor._indexes_ensured = True  # Tekrar deneme

    def create_graph_from_llm_output(
        self, llm_output: Dict[str, Any], file_name: str
    ) -> Dict[str, Any]:
        """
        LLM'den gelen JSON çıktısını Neo4j node ve relationship'lere dönüştürür.

        Args:
            llm_output: LLM'den gelen JSON (nodes, relationships, document_type)
            file_name: Belge adı (Document node'a bağlantı için)

        Returns:
            {
                "success": True/False,
                "nodes_created": int,
                "relationships_created": int,
                "errors": [...]
            }
        """
        result = {
            "success": True,
            "nodes_created": 0,
            "relationships_created": 0,
            "document_linked": False,
            "errors": [],
        }

        try:
            # 1. Validate input
            if not isinstance(llm_output, dict):
                raise ValueError(f"llm_output must be dict, got {type(llm_output)}")

            nodes = llm_output.get("nodes", [])
            relationships = llm_output.get("relationships", [])
            document_type = llm_output.get("document_type", "DIGER")

            logging.info(
                f"📊 GenericGraphExecutor: {len(nodes)} nodes, {len(relationships)} relationships"
            )

            # 2. Create nodes
            created_node_ids = set()
            for node in nodes:
                try:
                    node_id = self._create_node(node)
                    if node_id:
                        created_node_ids.add(node_id)
                        result["nodes_created"] += 1
                except Exception as e:
                    error_msg = (
                        f"Node creation error: {node.get('id', 'unknown')} - {e}"
                    )
                    logging.error(f"❌ {error_msg}")
                    result["errors"].append(error_msg)

            # 3. Create relationships
            for rel in relationships:
                try:
                    if self._create_relationship(rel, created_node_ids):
                        result["relationships_created"] += 1
                except Exception as e:
                    error_msg = f"Relationship creation error: {rel.get('type', 'unknown')} - {e}"
                    logging.error(f"❌ {error_msg}")
                    result["errors"].append(error_msg)

            # 4. Link entities to Document node
            try:
                linked_count = self._link_entities_to_document(
                    file_name, created_node_ids
                )
                result["document_linked"] = linked_count > 0
                logging.info(
                    f"📎 {linked_count} entities linked to Document: {file_name}"
                )
            except Exception as e:
                error_msg = f"Document linking error: {e}"
                logging.error(f"❌ {error_msg}")
                result["errors"].append(error_msg)

            # 5. Update Document metadata
            try:
                self._update_document_metadata(file_name, document_type)
            except Exception as e:
                error_msg = f"Document metadata update error: {e}"
                logging.error(f"❌ {error_msg}")
                result["errors"].append(error_msg)

            # Set success based on errors
            if result["errors"]:
                result["success"] = (
                    result["nodes_created"] > 0
                )  # Partial success if any nodes created

            logging.info(
                f"✅ GenericGraphExecutor completed: "
                f"{result['nodes_created']} nodes, "
                f"{result['relationships_created']} relationships"
            )

            return result

        except Exception as e:
            logging.error(f"❌ GenericGraphExecutor fatal error: {e}")
            result["success"] = False
            result["errors"].append(str(e))
            return result

    def _create_node(self, node: Dict[str, Any]) -> Optional[str]:
        """
        Tek bir node oluşturur veya günceller (MERGE).

        Args:
            node: {"label": "Company", "id": "company_123", "properties": {...}}

        Returns:
            Node ID if successful, None otherwise
        """
        label = node.get("label")
        node_id = node.get("id")
        properties = node.get("properties", {}).copy()

        if not label or not node_id:
            logging.warning(f"⚠️ Invalid node: missing label or id - {node}")
            return None

        # Sanitize label (only alphanumeric and underscore)
        label = re.sub(r"[^a-zA-Z0-9_]", "", label)

        # Add id to properties
        properties["id"] = node_id

        # Auto-generate normalized_name from 'name' if not provided
        # This ensures consistent matching across different documents
        if "name" in properties and "normalized_name" not in properties:
            properties["normalized_name"] = normalize_id(properties["name"])
            logging.debug(
                f"Auto-normalized: {properties['name']} → {properties['normalized_name']}"
            )

        # Handle aliases - need special merge logic
        has_aliases = "aliases" in properties
        aliases = properties.pop("aliases", []) if has_aliases else []

        # Build Cypher query
        # Using MERGE to avoid duplicates
        if has_aliases and aliases:
            # For nodes with aliases, merge existing aliases with new ones
            query = f"""
                MERGE (n:{label} {{id: $node_id}})
                SET n += $properties
                WITH n
                SET n.aliases = CASE 
                    WHEN n.aliases IS NULL THEN $aliases
                    ELSE [x IN (n.aliases + $aliases) WHERE x IS NOT NULL | x]
                END
                WITH n
                SET n.aliases = apoc.coll.toSet(n.aliases)
                RETURN n.id as created_id
            """
            params = {"node_id": node_id, "properties": properties, "aliases": aliases}
        else:
            query = f"""
                MERGE (n:{label} {{id: $node_id}})
                SET n += $properties
                RETURN n.id as created_id
            """
            params = {"node_id": node_id, "properties": properties}

        try:
            self.graph.query(
                query,
                params,
                session_params={"database": self._database} if self._database else {},
            )

            logging.debug("Node created/updated: %s (%s)", label, node_id)
            return node_id

        except Exception as e:
            # If APOC not available, try without alias deduplication
            if "apoc" in str(e).lower():
                logging.warning("APOC not available, aliases may have duplicates")
                fallback_query = f"""
                    MERGE (n:{label} {{id: $node_id}})
                    SET n += $properties
                    SET n.aliases = COALESCE(n.aliases, []) + $aliases
                    RETURN n.id as created_id
                """
                self.graph.query(
                    fallback_query,
                    {"node_id": node_id, "properties": properties, "aliases": aliases},
                    session_params=(
                        {"database": self._database} if self._database else {}
                    ),
                )
                return node_id

            logging.error(f"❌ Failed to create node {label} ({node_id}): {e}")
            raise

    def _create_relationship(
        self,
        rel: Dict[str, Any],
        valid_node_ids: Set[str],  # noqa: ARG002 - Reserved for future validation
    ) -> bool:
        """
        İki node arasında ilişki oluşturur.

        Args:
            rel: {"from_id": "...", "to_id": "...", "type": "HAS_MEMBER", "properties": {...}}
            valid_node_ids: Bu batch'te oluşturulan geçerli node ID'leri (future validation)

        Returns:
            True if successful
        """
        from_id = rel.get("from_id")
        to_id = rel.get("to_id")
        rel_type = rel.get("type")
        properties = rel.get("properties", {})

        if not all([from_id, to_id, rel_type]):
            logging.warning(
                f"⚠️ Invalid relationship: missing from_id, to_id, or type - {rel}"
            )
            return False

        # Validate node IDs exist (either in this batch or in DB)
        # For now, we'll try to create the relationship and let Neo4j handle missing nodes

        # Sanitize relationship type (only alphanumeric and underscore)
        rel_type = re.sub(r"[^a-zA-Z0-9_]", "_", rel_type).upper()

        # Build Cypher query using APOC for dynamic relationship type
        # If APOC not available, use standard Cypher with fixed type
        query = f"""
            MATCH (from {{id: $from_id}})
            MATCH (to {{id: $to_id}})
            MERGE (from)-[r:{rel_type}]->(to)
            SET r += $properties
            RETURN type(r) as rel_type
        """

        try:
            result = self.graph.query(
                query,
                {"from_id": from_id, "to_id": to_id, "properties": properties},
                session_params={"database": self._database} if self._database else {},
            )

            if result:
                logging.debug(
                    f"✅ Relationship created: ({from_id})-[{rel_type}]->({to_id})"
                )
                return True
            else:
                logging.warning(
                    f"⚠️ Relationship not created (nodes may not exist): ({from_id})-[{rel_type}]->({to_id})"
                )
                return False

        except Exception as e:
            logging.error(f"❌ Failed to create relationship {rel_type}: {e}")
            raise

    def _link_entities_to_document(self, file_name: str, entity_ids: set) -> int:
        """
        Oluşturulan entity'leri Document node'a bağlar.

        Args:
            file_name: Document fileName
            entity_ids: Bağlanacak entity ID'leri

        Returns:
            Bağlanan entity sayısı
        """
        if not entity_ids:
            return 0

        linked_count = 0

        for entity_id in entity_ids:
            query = """
                MATCH (d:Document {fileName: $file_name})
                MATCH (e {id: $entity_id})
                MERGE (d)-[r:HAS_ENTITY]->(e)
                RETURN count(r) as linked
            """

            try:
                result = self.graph.query(
                    query,
                    {"file_name": file_name, "entity_id": entity_id},
                    session_params=(
                        {"database": self._database} if self._database else {}
                    ),
                )

                if result and result[0].get("linked", 0) > 0:
                    linked_count += 1

            except Exception as e:
                logging.warning(f"⚠️ Failed to link entity {entity_id} to document: {e}")

        return linked_count

    def _update_document_metadata(self, file_name: str, document_type: str):
        """
        Document node'un metadata'sını günceller.

        Args:
            file_name: Document fileName
            document_type: Belge türü (GENEL_KURUL, KURULUS, etc.)
        """
        query = """
            MATCH (d:Document {fileName: $file_name})
            SET d.docType = $doc_type,
                d.hasExtractedEntities = true,
                d.entityExtractionMethod = 'generic_graph_executor',
                d.lastProcessedAt = datetime()
            RETURN d.fileName as updated
        """

        self.graph.query(
            query,
            {"file_name": file_name, "doc_type": document_type},
            session_params={"database": self._database} if self._database else {},
        )

        logging.info(f"📝 Document metadata updated: {file_name} ({document_type})")


def create_graph_from_llm_output(
    graph: Neo4jGraph, llm_output: Dict[str, Any], file_name: str
) -> Dict[str, Any]:
    """
    Convenience function for creating graph from LLM output.

    Args:
        graph: Neo4j graph connection
        llm_output: LLM JSON output with nodes/relationships
        file_name: Document file name

    Returns:
        Execution result dict
    """
    executor = GenericGraphExecutor(graph)
    return executor.create_graph_from_llm_output(llm_output, file_name)
