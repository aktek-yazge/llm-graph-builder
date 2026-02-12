# -*- coding: utf-8 -*-
"""
Neo4j Schema Provider

Neo4j'den mevcut graf şemasını çeker ve OCR prompt'u için formatlar.
Bu sayede Opus 4.5 mevcut yapıya uygun entity ve relationship üretir.

Çekilen bilgiler:
- Node label'ları ve property'leri
- Relationship type'ları ve pattern'leri
- Mevcut entity ID örnekleri
"""

import logging
from typing import Any, Dict, List, Optional

from langchain_neo4j import Neo4jGraph

logger = logging.getLogger(__name__)


# Sistem node'ları - şemada gösterilmeyecek
SYSTEM_LABELS = {"Document", "Chunk", "__Entity__", "_Bloom_Perspective_", "_Bloom_Scene_"}
SYSTEM_RELATIONSHIPS = {"HAS_CHUNK", "PART_OF", "FIRST_CHUNK", "NEXT_CHUNK"}


class Neo4jSchemaProvider:
    """
    Neo4j'den şema bilgisi çeker ve prompt için formatlar.
    
    Kullanım:
        provider = Neo4jSchemaProvider(graph)
        schema_text = provider.get_schema_for_prompt()
    """
    
    def __init__(self, graph: Neo4jGraph):
        self.graph = graph
        self._database = getattr(graph, "_database", None)
    
    def get_schema_for_prompt(self, max_examples: int = 5) -> str:
        """
        Prompt için formatlanmış şema bilgisi döndürür.
        
        Args:
            max_examples: Her label için maksimum örnek ID sayısı
            
        Returns:
            Markdown formatında şema bilgisi
        """
        try:
            labels = self._get_node_labels()
            relationships = self._get_relationship_types()
            patterns = self._get_relationship_patterns()
            node_props = self._get_node_properties(labels, max_examples)
            rel_props = self._get_relationship_properties(relationships)
            
            return self._format_schema(
                labels=labels,
                node_props=node_props,
                relationships=relationships,
                rel_props=rel_props,
                patterns=patterns,
            )
        except Exception as e:
            logger.error(f"❌ Schema extraction failed: {e}")
            return self._get_fallback_schema()
    
    def _get_node_labels(self) -> List[str]:
        """Tüm node label'larını çeker (sistem label'ları hariç)."""
        query = "CALL db.labels() YIELD label RETURN label"
        try:
            result = self.graph.query(
                query,
                session_params={"database": self._database} if self._database else {},
            )
            labels = [r["label"] for r in result if r["label"] not in SYSTEM_LABELS]
            logger.debug(f"Found {len(labels)} entity labels: {labels}")
            return labels
        except Exception as e:
            logger.warning(f"⚠️ Could not get labels: {e}")
            return []
    
    def _get_relationship_types(self) -> List[str]:
        """Tüm relationship type'larını çeker (sistem relationship'leri hariç)."""
        query = "CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType"
        try:
            result = self.graph.query(
                query,
                session_params={"database": self._database} if self._database else {},
            )
            types = [r["relationshipType"] for r in result if r["relationshipType"] not in SYSTEM_RELATIONSHIPS]
            logger.debug(f"Found {len(types)} relationship types: {types}")
            return types
        except Exception as e:
            logger.warning(f"⚠️ Could not get relationship types: {e}")
            return []
    
    def _get_node_properties(self, labels: List[str], max_examples: int) -> Dict[str, Dict[str, Any]]:
        """
        Her label için property'leri ve örnek değerleri çeker.
        
        Returns:
            {
                "Company": {
                    "properties": ["id", "name", "trade_registry_number"],
                    "example_ids": ["company_aksa", "company_aygaz"],
                    "example_node": {"id": "...", "name": "..."}
                }
            }
        """
        node_props = {}
        
        for label in labels:
            try:
                # Property'leri ve örnek değerleri çek
                query = f"""
                    MATCH (n:{label})
                    WITH n LIMIT {max_examples}
                    RETURN properties(n) as props, n.id as node_id
                """
                result = self.graph.query(
                    query,
                    session_params={"database": self._database} if self._database else {},
                )
                
                if result:
                    # Tüm property key'lerini topla
                    all_props = set()
                    example_ids = []
                    example_node = None
                    
                    for row in result:
                        props = row.get("props", {})
                        if props:
                            all_props.update(props.keys())
                            if example_node is None:
                                example_node = props
                        
                        node_id = row.get("node_id")
                        if node_id:
                            example_ids.append(node_id)
                    
                    node_props[label] = {
                        "properties": sorted(list(all_props)),
                        "example_ids": example_ids[:max_examples],
                        "example_node": example_node,
                    }
                else:
                    node_props[label] = {
                        "properties": [],
                        "example_ids": [],
                        "example_node": None,
                    }
                    
            except Exception as e:
                logger.warning(f"⚠️ Could not get properties for {label}: {e}")
                node_props[label] = {"properties": [], "example_ids": [], "example_node": None}
        
        return node_props
    
    def _get_relationship_properties(self, rel_types: List[str]) -> Dict[str, List[str]]:
        """Her relationship type için property'leri çeker."""
        rel_props = {}
        
        for rel_type in rel_types:
            try:
                query = f"""
                    MATCH ()-[r:{rel_type}]->()
                    WITH r LIMIT 5
                    UNWIND keys(properties(r)) as prop_key
                    RETURN DISTINCT prop_key
                """
                result = self.graph.query(
                    query,
                    session_params={"database": self._database} if self._database else {},
                )
                rel_props[rel_type] = [r["prop_key"] for r in result] if result else []
            except Exception as e:
                logger.warning(f"⚠️ Could not get properties for relationship {rel_type}: {e}")
                rel_props[rel_type] = []
        
        return rel_props
    
    def _get_relationship_patterns(self) -> List[Dict[str, str]]:
        """
        İlişki pattern'lerini çeker: (Label1)-[:TYPE]->(Label2)
        """
        query = """
            MATCH (a)-[r]->(b)
            WHERE NOT any(l IN labels(a) WHERE l IN $system_labels)
              AND NOT any(l IN labels(b) WHERE l IN $system_labels)
              AND NOT type(r) IN $system_rels
            WITH labels(a)[0] as from_label, type(r) as rel_type, labels(b)[0] as to_label
            RETURN DISTINCT from_label, rel_type, to_label
            LIMIT 50
        """
        try:
            result = self.graph.query(
                query,
                {"system_labels": list(SYSTEM_LABELS), "system_rels": list(SYSTEM_RELATIONSHIPS)},
                session_params={"database": self._database} if self._database else {},
            )
            patterns = [
                {
                    "from": r["from_label"],
                    "type": r["rel_type"],
                    "to": r["to_label"],
                }
                for r in result
                if r["from_label"] and r["rel_type"] and r["to_label"]
            ]
            logger.debug(f"Found {len(patterns)} relationship patterns")
            return patterns
        except Exception as e:
            logger.warning(f"⚠️ Could not get relationship patterns: {e}")
            return []
    
    def _format_schema(
        self,
        labels: List[str],
        node_props: Dict[str, Dict[str, Any]],
        relationships: List[str],
        rel_props: Dict[str, List[str]],
        patterns: List[Dict[str, str]],
    ) -> str:
        """Şemayı prompt için markdown formatında oluşturur."""
        
        sections = []
        
        # Node Tipleri ve Property'leri
        if labels and node_props:
            node_section = "## Mevcut Node Tipleri\n\n"
            node_section += "| Label | Properties | Örnek ID |\n"
            node_section += "|-------|------------|----------|\n"
            
            for label in sorted(labels):
                props = node_props.get(label, {})
                prop_list = ", ".join(props.get("properties", [])[:8])  # İlk 8 property
                if len(props.get("properties", [])) > 8:
                    prop_list += ", ..."
                
                example_ids = props.get("example_ids", [])
                example_id = example_ids[0] if example_ids else "-"
                
                node_section += f"| {label} | {prop_list} | `{example_id}` |\n"
            
            sections.append(node_section)
        
        # İlişki Pattern'leri
        if patterns:
            pattern_section = "## İlişki Pattern'leri\n\n"
            for p in patterns:
                props = rel_props.get(p["type"], [])
                props_str = f" {{{', '.join(props)}}}" if props else ""
                pattern_section += f"- `({p['from']})-[:{p['type']}{props_str}]->({p['to']})`\n"
            sections.append(pattern_section)
        
        # Mevcut Entity ID Örnekleri (gruplandırılmış)
        if node_props:
            id_section = "## Mevcut Entity ID Örnekleri\n\n"
            id_section += "**Aynı entity varsa mevcut ID'yi kullan. Yeni entity için benzer pattern izle.**\n\n"
            
            for label in sorted(labels):
                props = node_props.get(label, {})
                example_ids = props.get("example_ids", [])
                if example_ids:
                    id_list = ", ".join([f"`{eid}`" for eid in example_ids[:3]])
                    id_section += f"- **{label}**: {id_list}\n"
            
            sections.append(id_section)
        
        if not sections:
            return self._get_fallback_schema()
        
        return "\n".join(sections)
    
    def _get_fallback_schema(self) -> str:
        """Şema çekilemezse kullanılacak fallback."""
        return """## Graf Şeması

Mevcut şema bilgisi alınamadı. Aşağıdaki genel kuralları uygula:

### ID Formatı
- Şirket: `company_[normalized_name]` veya `company_[ticaret_sicil_no]`
- Kişi: `person_[normalized_name]` veya `person_[tc_kimlik_no]`
- Diğer: `[label]_[normalized_identifier]`

### Normalization
- Küçük harf
- Türkçe karakterler: ş→s, ğ→g, ü→u, ö→o, ç→c, ı→i
- Boşluklar → underscore
- Özel karakterleri kaldır
"""


def get_schema_for_ocr(graph: Neo4jGraph, max_examples: int = 5) -> str:
    """
    OCR prompt'u için şema bilgisi çeker.
    
    Convenience function.
    
    Args:
        graph: Neo4j graph bağlantısı
        max_examples: Her label için maksimum örnek ID sayısı
        
    Returns:
        Markdown formatında şema bilgisi
    """
    provider = Neo4jSchemaProvider(graph)
    return provider.get_schema_for_prompt(max_examples=max_examples)
