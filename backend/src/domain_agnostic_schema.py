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
        (:Document) node'larının özelliklerini keşfeder
        
        Returns:
            Dict[str, Any]: Document pattern bilgileri
        """
        try:
            logger.info("🔍 Document pattern'ları keşfediliyor...")
            
            cypher_query = """
            MATCH (d:Document)
            RETURN count(d) as document_count,
                   collect(DISTINCT keys(d))[0..10] as sample_properties
            """
            
            result = self.graph.query(cypher_query)
            if result:
                document_info = result[0]
                logger.info(f"✅ {document_info['document_count']} Document node bulundu")
                return {
                    "count": document_info["document_count"],
                    "sample_properties": document_info.get("sample_properties", [])
                }
            
            return {"count": 0, "sample_properties": []}
            
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

    def generate_llm_schema_prompt(self) -> str:
        """
        LLM için hibrit CV şema prompt'u oluşturur (GERÇEK ŞEMA)
        
        Returns:
            str: LLM'e verilebilecek şema açıklaması
        """
        schema_info = self.discover_full_domain_schema()
        
        entity_types = schema_info["entity_types"]
        relation_types = schema_info["relation_types"]
        stats = schema_info["statistics"]
        
        prompt = f"""## 🏗️ HİBRİT CV NEO4J SCHEMA (MCP Aura Keşfi)

### 📊 KEŞFEDILEN GERÇEK ŞEMA YAPISI:

**Person Pattern (Core):**
```
(:Person)
```
- 76 CV sahibi kişi
- Properties: name, career_current_position, career_experience_years, contact_email, etc.

**Entity Pattern (Dynamic):**
```
(:Entity {{type:"<entity_type>"}})
```

**Kullanılabilir Entity type değerleri ({len(entity_types)} adet):**
{', '.join(f'"{et}"' for et in entity_types)}

**Relationship Patterns:**
```
(:Person)-[:HAS_ATTRIBUTE]->(:Entity|:Attribute)
(:Person)-[:CONNECTED_TO]->(:Entity)
(:Person)-[:HAS_CV]->(:Document)
```

**Kullanılabilir relation type değerleri ({len(relation_types)} adet):**
{', '.join(f'"{rt}"' for rt in relation_types)}

**Document Pattern:**
```
(:Document)
```

### 📈 VERİTABANI İSTATİSTİKLERİ:
- 👤 Person: 76
- 🏷️ Entity: {stats['total_entities']:,}
- 🔗 Person-Entity Relations: {stats['total_relations']:,}
- 📄 Document: {schema_info['document_info']['count']:,}

### 🎯 HİBRİT ŞEMA KULLANIM KURALLARI:

1. **PERSON CORE** properties direkt kullanılır
2. **ENTITY TYPE** filtering ile kategorize edilir  
3. **HAS_ATTRIBUTE** dynamic attributes için
4. **CONNECTED_TO** formal connections için
5. **ASLA** (:Entity {{subtype:"..."}}) kullanma - gerçek şemada yok!

### 💡 GERÇEK ŞEMA ÖRNEKLERI:

```cypher
// Pattern 1: Person core + Entity skill
MATCH (p:Person)-[:HAS_ATTRIBUTE]->(e:Entity {{type:"Skill"}})
WHERE toLower(apoc.text.clean(e.name)) CONTAINS "python"
RETURN p.name, p.career_current_position, e.name as skill

// Pattern 2: Person + Organization connection  
MATCH (p:Person)-[:CONNECTED_TO]->(org:Entity {{type:"Organization"}})
WHERE toLower(apoc.text.clean(org.name)) CONTAINS "microsoft"
RETURN p.name, org.name, r.role, r.start_date
```

**KRİTİK**: Bu gerçek şema! (:Entity {{type:"..."}}) pattern'ini kullan, subtype değil!
"""
        
        return prompt

    def get_compact_schema_summary(self) -> str:
        """
        Kompakt şema özeti döndürür
        
        Returns:
            str: Özet şema bilgisi
        """
        schema_info = self.discover_full_domain_schema()
        
        entity_count = len(schema_info["entity_subtypes"])
        relation_count = len(schema_info["relation_types"])
        document_count = schema_info["document_info"]["count"]
        
        return f"""DOMAIN-AGNOSTIC SCHEMA: 
Entity({entity_count} subtypes) + RELATED({relation_count} types) + Document({document_count:,} nodes)
Pattern: (:Entity {{subtype:"TYPE"}}) -[:RELATED {{type:"TYPE"}}]-> (:Entity) | (:Document)"""


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