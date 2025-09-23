#!/usr/bin/env python3
"""
Domain-Agnostic Neo4j Schema Discovery
Runtime'da veritabanından subtype'ları ve relation type'ları çıkarır
"""

import logging
from typing import Dict, List, Any, Tuple

logger = logging.getLogger(__name__)


class DomainAgnosticSchemaDiscovery:
    """
    Runtime'da Neo4j'den domain-agnostic şema bilgilerini çıkarır.
    (:Entity {subtype:"..."}) ve [:RELATED {type:"..."}] pattern'ini destekler.
    """

    def __init__(self, graph):
        self.graph = graph

    def discover_entity_subtypes(self) -> List[str]:
        """
        (:Entity {type:"..."}) pattern'indeki tüm type değerlerini çıkarır (GERÇEK ŞEMA)
        
        Returns:
            List[str]: Bulunan tüm Entity type değerleri
        """
        try:
            logger.info("🔍 Entity type'ları keşfediliyor...")
            
            cypher_query = """
            MATCH (e:Entity)
            WHERE e.type IS NOT NULL
            RETURN DISTINCT e.type AS entity_type
            ORDER BY entity_type
            """
            
            result = self.graph.query(cypher_query)
            entity_types = [record["entity_type"] for record in result if record["entity_type"]]
            
            logger.info(f"✅ {len(entity_types)} farklı Entity type bulundu: {entity_types}")
            return entity_types
            
        except Exception as e:
            logger.error(f"❌ Entity type keşfi hatası: {e}")
            return []

    def discover_relation_types(self) -> List[str]:
        """
        Person-Entity arasındaki gerçek relation type'ları çıkarır (GERÇEK ŞEMA)
        HAS_ATTRIBUTE, CONNECTED_TO gibi
        
        Returns:
            List[str]: Bulunan tüm relation type değerleri
        """
        try:
            logger.info("🔍 Person-Entity relation type'ları keşfediliyor...")
            
            cypher_query = """
            MATCH (p:Person)-[r]-(e:Entity)
            RETURN DISTINCT type(r) AS relation_type
            ORDER BY relation_type
            """
            
            result = self.graph.query(cypher_query)
            relation_types = [record["relation_type"] for record in result if record["relation_type"]]
            
            logger.info(f"✅ {len(relation_types)} farklı relation type bulundu: {relation_types}")
            return relation_types
            
        except Exception as e:
            logger.error(f"❌ Relation type keşfi hatası: {e}")
            return []

    def discover_document_patterns(self) -> Dict[str, Any]:
        """
        (:Document) node'larının özelliklerini keşfeder (Runtime)
        
        Returns:
            Dict[str, Any]: Document pattern bilgileri
        """
        try:
            logger.info("🔍 Document pattern'ları keşfediliyor...")
            
            cypher_query = """
            MATCH (d:Document)
            RETURN count(d) as document_count,
                   collect(DISTINCT keys(d)) as all_properties
            """
            
            count_query = "MATCH (d:Document) RETURN count(d) as total_count"
            
            # Önce toplam sayıyı al
            count_result = self.graph.query(count_query)
            total_count = count_result[0]["total_count"] if count_result else 0
            
            # Sonra sample properties al
            result = self.graph.query(cypher_query)
            if result and len(result) > 0 and result[0].get("all_properties"):
                # Nested listleri düzleştir
                all_props = []
                for prop_list in result[0]["all_properties"]:
                    if isinstance(prop_list, list):
                        all_props.extend(prop_list)
                    else:
                        all_props.append(prop_list)
                
                unique_properties = list(set(all_props))
                logger.info(f"✅ {total_count} Document node bulundu, {len(unique_properties)} farklı property")
                
                return {
                    "count": total_count,
                    "sample_properties": sorted(unique_properties)
                }
            
            return {"count": total_count, "sample_properties": []}
            
        except Exception as e:
            logger.error(f"❌ Document pattern keşfi hatası: {e}")
            return {"count": 0, "sample_properties": []}

    def discover_full_domain_schema(self) -> Dict[str, Any]:
        """
        Tam hibrit şema keşfi yapar (GERÇEK ŞEMA)
        
        Returns:
            Dict[str, Any]: Keşfedilen şema bilgileri
        """
        logger.info("🚀 Hibrit CV şema keşfi başlıyor...")
        
        # Tüm discovery'leri paralel çalıştır
        entity_types = self.discover_entity_subtypes()  # Gerçekte entity type'ları
        relation_types = self.discover_relation_types() 
        document_info = self.discover_document_patterns()
        
        # İstatistikler
        total_entities = self._get_entity_count()
        total_relations = self._get_relation_count()
        
        schema_info = {
            "entity_types": entity_types,  # subtype yerine type
            "relation_types": relation_types,
            "document_info": document_info,
            "statistics": {
                "total_entities": total_entities,
                "total_relations": total_relations,
                "unique_entity_types": len(entity_types),
                "unique_relation_types": len(relation_types)
            },
            "patterns": {
                "entity_pattern": "(:Entity {type:\"<entity_type>\"})",  # Gerçek pattern
                "person_pattern": "(:Person)",
                "relation_patterns": ["[:HAS_ATTRIBUTE]", "[:CONNECTED_TO]", "[:HAS_CV]"],
                "document_pattern": "(:Document)"
            }
        }
        
        logger.info(f"🎯 Hibrit CV şema keşfi tamamlandı:")
        logger.info(f"   📊 {len(entity_types)} Entity type")
        logger.info(f"   🔗 {len(relation_types)} Relation type") 
        logger.info(f"   📄 {document_info['count']} Document")
        
        return schema_info

    def _get_entity_count(self) -> int:
        """Entity node sayısını döndürür"""
        try:
            result = self.graph.query("MATCH (e:Entity) RETURN count(e) as count")
            return result[0]["count"] if result else 0
        except:
            return 0

    def _get_relation_count(self) -> int:
        """Person-Entity relation sayısını döndürür"""
        try:
            result = self.graph.query("MATCH (p:Person)-[r]-(e:Entity) RETURN count(r) as count")
            return result[0]["count"] if result else 0
        except:
            return 0

    def _get_person_count(self) -> int:
        """Person node sayısını döndürür"""
        try:
            result = self.graph.query("MATCH (p:Person) RETURN count(p) as count")
            return result[0]["count"] if result else 0
        except:
            return 0

    def _get_person_properties(self) -> List[str]:
        """Person node'larının property isimlerini döndürür"""
        try:
            result = self.graph.query("""
            MATCH (p:Person) 
            RETURN DISTINCT keys(p) as properties
            """)
            
            all_properties = set()
            for record in result:
                all_properties.update(record["properties"])
            
            return sorted(list(all_properties))
        except:
            return ["name"]  # Fallback

    def _get_all_relation_types(self) -> List[str]:
        """TÜM relation type'ları döndürür (sadece Person-Entity değil)"""
        try:
            result = self.graph.query("""
            MATCH ()-[r]-()
            RETURN DISTINCT type(r) as relation_type
            ORDER BY relation_type
            """)
            
            return [record["relation_type"] for record in result if record["relation_type"]]
        except:
            return ["CONNECTED_TO"]  # Fallback

    def _get_entity_relation_mapping(self) -> Dict[str, str]:
        """Entity type'ları ile kullanılan relation'ları eşleştirir (RUNTIME MAPPING)"""
        try:
            result = self.graph.query("""
            MATCH (p:Person)-[r]->(e:Entity)
            RETURN e.type as entity_type, type(r) as relation_type, count(*) as count
            ORDER BY entity_type, count DESC
            """)
            
            # Her entity type için en çok kullanılan relation'ı al
            mapping = {}
            for record in result:
                entity_type = record["entity_type"]
                relation_type = record["relation_type"]
                
                # İlk karşılaşılan (en çok kullanılan) relation'ı kaydet
                if entity_type not in mapping:
                    mapping[entity_type] = relation_type
            
            return mapping
        except:
            return {"Language": "HAS_ATTRIBUTE", "Organization": "CONNECTED_TO"}  # Fallback

    def generate_llm_schema_prompt(self) -> str:
        """
        Ultra-minimal token-efficient FULL şema prompt'u - RELATION MAPPING öncelikli!
        
        Returns:
            str: Minimal şema prompt'u
        """
        schema_info = self.discover_full_domain_schema()
        
        # TÜM bilgileri al (limit yok!)
        entity_types = schema_info["entity_types"]
        relation_types = schema_info["relation_types"]
        person_props = self._get_person_properties()
        doc_props = schema_info["document_info"]["sample_properties"]
        
        # TÜM relation type'ları keşfet (sadece Person-Entity değil)
        all_relations = self._get_all_relation_types()
        
        # RUNTIME ENTITY-RELATION MAPPING keşfet
        entity_relation_map = self._get_entity_relation_mapping()
        
        # Ultra-compact format
        et = ','.join(entity_types) if entity_types else 'TYPE'
        rt = ','.join(relation_types) if relation_types else 'REL'
        all_rt = ','.join(all_relations) if all_relations else 'REL'
        pp = ','.join(person_props) if person_props else 'name'
        dp = ','.join(doc_props) if doc_props else 'name'
        
        # Entity-Relation mapping'i daha vurgulu format'ta
        mapping_rules = []
        for etype, rel in entity_relation_map.items():
            mapping_rules.append(f"Person-[:{rel}]->Entity{{type:\"{etype}\"}}")
        
        prompt = f"""SCHEMA:Person[{pp}]|Entity[type:{et}]|Document[{dp}]
RELATIONS:[{all_rt}]
⚠️CRITICAL_MAPPING:{';'.join(mapping_rules)}
PATTERNS:Person-Entity,Person-Document,Entity-Document,Entity-Entity
CORRECT_EXAMPLE:MATCH(p:Person)-[:HAS_ATTRIBUTE]->(e:Entity{{type:"Language"}})WHERE apoc.text.clean(e.name)=~".*almanca.*"RETURN p.name,e.name"""
        
        return prompt

    def get_compact_schema_summary(self) -> str:
        """
        Kompakt şema özeti döndürür (Runtime Data)
        
        Returns:
            str: Özet şema bilgisi
        """
        schema_info = self.discover_full_domain_schema()
        
        entity_count = len(schema_info["entity_types"])  # Düzeltildi: entity_subtypes -> entity_types
        relation_count = len(schema_info["relation_types"])
        document_count = schema_info["document_info"]["count"]
        person_count = self._get_person_count()
        
        return f"""DOMAIN-AGNOSTIC SCHEMA (Runtime): 
Person({person_count}) + Entity({entity_count} types) + Relations({relation_count} types) + Document({document_count:,})
Pattern: (:Entity {{type:"TYPE"}}) -[:RELATION_TYPE]-> (:Person|:Entity) | (:Document)"""


def get_domain_agnostic_schema(graph) -> Dict[str, Any]:
    """
    Convenience function - domain-agnostic şema keşfi yapar
    
    Args:
        graph: Neo4j graph instance
        
    Returns:
        Dict[str, Any]: Keşfedilen şema bilgileri
    """
    discoverer = DomainAgnosticSchemaDiscovery(graph)
    return discoverer.discover_full_domain_schema()


def get_llm_schema_prompt(graph) -> str:
    """
    Convenience function - LLM için şema prompt'u oluşturur
    
    Args:
        graph: Neo4j graph instance
        
    Returns:
        str: LLM'e verilebilecek şema prompt'u
    """
    discoverer = DomainAgnosticSchemaDiscovery(graph)
    return discoverer.generate_llm_schema_prompt()


if __name__ == "__main__":
    # Test için (sadece development)
    print("Domain-Agnostic Schema Discovery modülü yüklendi")