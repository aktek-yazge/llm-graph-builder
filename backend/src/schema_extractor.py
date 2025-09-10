#!/usr/bin/env python3
"""
Neo4j Schema Extractor
Gerçek veritabanından schema bilgilerini çeker ve formatlı string olarak döndürür.

Usage:
    # Komut satırından
    python schema_extractor.py
    
    # Import olarak
    from schema_extractor import Neo4jSchemaExtractor, get_compact_schema
    
    # Direkt kullanım
    schema = get_compact_schema()
    print(schema)
    
    # Class instance
    extractor = Neo4jSchemaExtractor()
    schema = extractor.format_schema_string()
"""

import os
import sys
from typing import Dict, List, Any
from langchain_neo4j import Neo4jGraph
import logging

# Logger ayarla - sadece ERROR seviyesi
logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger(__name__)

class Neo4jSchemaExtractor:
    def __init__(self):
        """Neo4j bağlantısını başlat"""
        try:
            self.graph = Neo4jGraph(
                url=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
                username=os.getenv("NEO4J_USERNAME", "neo4j"),
                password=os.getenv("NEO4J_PASSWORD", "qwerty5555"),
                database=os.getenv("NEO4J_DATABASE", "neo4j")
            )
        except Exception as e:
            print(f"❌ Neo4j bağlantı hatası: {e}")
            raise
    
    def get_node_schemas(self) -> Dict[str, List[str]]:
        """Tüm node label'ları ve property'lerini çek"""
        query = """
        CALL db.schema.nodeTypeProperties()
        YIELD nodeType, nodeLabels, propertyName, propertyTypes, mandatory
        RETURN nodeLabels[0] as label, 
               collect({property: propertyName, types: propertyTypes, mandatory: mandatory}) as properties
        ORDER BY label
        """
        
        try:
            result = self.graph.query(query)
            node_schemas = {}
            
            for row in result:
                label = row['label']
                properties = []
                
                for prop in row['properties']:
                    prop_name = prop['property']
                    prop_types = prop['types']
                    
                    # Type'ı Neo4j formatından Python/Cypher formatına çevir
                    if 'String' in prop_types:
                        type_str = 'string'
                    elif 'Long' in prop_types:
                        type_str = 'integer'
                    elif 'Double' in prop_types or 'Float' in prop_types:
                        type_str = 'float'
                    elif 'Boolean' in prop_types:
                        type_str = 'boolean'
                    elif 'DateTime' in prop_types:
                        type_str = 'datetime'
                    elif 'Array' in str(prop_types):
                        if 'String' in str(prop_types):
                            type_str = 'string[]'
                        elif 'Float' in str(prop_types) or 'Double' in str(prop_types):
                            type_str = 'float[]'
                        else:
                            type_str = 'array'
                    else:
                        type_str = str(prop_types[0]).lower() if prop_types else 'unknown'
                    
                    properties.append(f"{prop_name}:{type_str}")
                
                node_schemas[label] = properties
            
            return node_schemas
            
        except Exception as e:
            return {}
    
    def get_relationship_schemas(self) -> List[Dict[str, Any]]:
        """Tüm relationship type'larını ve property'lerini çek"""
        query = """
        CALL db.schema.relTypeProperties()
        YIELD relType, propertyName, propertyTypes, mandatory
        RETURN relType, 
               collect({property: propertyName, types: propertyTypes, mandatory: mandatory}) as properties
        ORDER BY relType
        """
        
        try:
            result = self.graph.query(query)
            rel_schemas = []
            
            for row in result:
                rel_type = row['relType']
                # Başındaki ':' ve backtick karakterlerini kaldır
                rel_type = rel_type.strip(':`')
                
                properties = []
                
                for prop in row['properties']:
                    if prop['property']:  # Boş property'leri atla
                        prop_name = prop['property']
                        prop_types = prop['types']
                        
                        # Type'ı çevir
                        if 'String' in prop_types:
                            type_str = 'string'
                        elif 'DateTime' in prop_types:
                            type_str = 'datetime'
                        elif 'Long' in prop_types:
                            type_str = 'integer'
                        elif 'Boolean' in prop_types:
                            type_str = 'boolean'
                        else:
                            type_str = str(prop_types[0]).lower() if prop_types else 'unknown'
                        
                        properties.append(f"{prop_name}:{type_str}")
                
                rel_schemas.append({
                    'type': rel_type,
                    'properties': properties
                })
            
            return rel_schemas
            
        except Exception as e:
            return []
    
    def get_relationship_patterns(self) -> List[str]:
        """Relationship pattern'lerini çek"""
        query = """
        MATCH (start)-[r]->(end)
        RETURN DISTINCT labels(start)[0] as startLabel, 
               type(r) as relType,
               labels(end)[0] as endLabel
        ORDER BY startLabel, relType, endLabel
        """
        
        try:
            result = self.graph.query(query)
            patterns = []
            
            for row in result:
                if row['startLabel'] and row['endLabel'] and row['relType']:
                    pattern = f"({row['startLabel']})-[{row['relType']}]->({row['endLabel']})"
                    patterns.append(pattern)
            
            # Duplicates'leri kaldır
            patterns = list(set(patterns))
            patterns.sort()
            
            return patterns
            
        except Exception as e:
            return []
    
    def get_field_type_analysis(self) -> Dict[str, Any]:
        """Kritik field'ların tip analizini yap - APOC olmadan"""
        logger.info("🔍 Field tip analizi yapılıyor (APOC olmadan)...")
        
        queries = {
            "PolicyYear.year": "MATCH (py:PolicyYear) RETURN py.year as value, toString(py.year) as str_value, py.year + 0 as numeric_test LIMIT 5",
            "Policy.year": "MATCH (p:Policy) RETURN p.year as value, size(p.year) as str_length LIMIT 5",
            "Document.total_chunks": "MATCH (d:Document) RETURN d.total_chunks as value, d.total_chunks + 0 as numeric_test LIMIT 5",
            "Customer.fullName": "MATCH (c:Customer) RETURN c.fullName as value, size(c.fullName) as str_length LIMIT 5"
        }
        
        analysis = {}
        
        for field_name, query in queries.items():
            try:
                result = self.graph.query(query)
                if result:
                    sample_data = []
                    for row in result:
                        sample_info = {'value': row['value']}
                        
                        # Tip çıkarımı yap
                        if 'numeric_test' in row:
                            sample_info['inferred_type'] = 'integer/numeric'
                        elif 'str_length' in row:
                            sample_info['inferred_type'] = 'string'
                            sample_info['length'] = row['str_length']
                        
                        if 'str_value' in row:
                            sample_info['str_representation'] = row['str_value']
                        
                        sample_data.append(sample_info)
                    
                    analysis[field_name] = sample_data
                else:
                    analysis[field_name] = "No data found"
            except Exception as e:
                analysis[field_name] = f"Query Error: {str(e)}"
        
        return analysis
        """Schema bilgilerini formatlı string olarak döndür"""
        logger.info("📝 Schema formatlanıyor...")
        
        # Node'ları çek
        node_schemas = self.get_node_schemas()
        
        # Relationship'leri çek
        rel_schemas = self.get_relationship_schemas()
        
        # Pattern'leri çek
        patterns = self.get_relationship_patterns()
        
        # Format et
        schema_lines = []
        
        # Nodes formatı
        nodes_line = "Nodes: "
        node_parts = []
        for label, properties in node_schemas.items():
            if properties:
                props_str = ", ".join(properties)
                node_parts.append(f"{label}({props_str})")
            else:
                node_parts.append(label)
        nodes_line += "; ".join(node_parts)
        schema_lines.append(nodes_line)
        
        # Relationships formatı
        rels_line = "Rels: "
        rel_parts = []
        for rel in rel_schemas:
            if rel['properties']:
                props_str = ", ".join(rel['properties'])
                rel_parts.append(f"{rel['type']}({props_str})")
            else:
                rel_parts.append(rel['type'])
        rels_line += "; ".join(rel_parts)
        schema_lines.append(rels_line)
        
        # Patterns formatı
        patterns_line = "Patterns: " + "; ".join(patterns)
        schema_lines.append(patterns_line)
        
        schema_string = "\n".join(schema_lines)
        
    def print_detailed_schema_info(self):
        """Tüm schema bilgilerini detaylı formatta yazdır"""
        print("\n" + "="*100)
        print("📊 DETAYLI NEO4J SCHEMA BİLGİLERİ")
        print("="*100)
        
        # Node'ları çek ve detaylı yazdır
        node_schemas = self.get_node_schemas()
        print(f"\n🏷️  NODE SCHEMAS ({len(node_schemas)} adet):")
        print("-" * 80)
        
        for i, (label, properties) in enumerate(node_schemas.items(), 1):
            print(f"\n{i}. {label}")
            print(f"   Property'ler ({len(properties)} adet):")
            if properties:
                for j, prop in enumerate(properties, 1):
                    prop_name, prop_type = prop.split(":")
                    print(f"      {j:2d}. {prop_name:<25} → {prop_type}")
            else:
                print("      (Property yok)")
        
        # Relationship'leri çek ve detaylı yazdır
        rel_schemas = self.get_relationship_schemas()
        print(f"\n🔗 RELATIONSHIP SCHEMAS ({len(rel_schemas)} adet):")
        print("-" * 80)
        
        for i, rel in enumerate(rel_schemas, 1):
            print(f"\n{i}. :{rel['type']}")
            print(f"   Property'ler ({len(rel['properties'])} adet):")
            if rel['properties']:
                for j, prop in enumerate(rel['properties'], 1):
                    prop_name, prop_type = prop.split(":")
                    print(f"      {j:2d}. {prop_name:<25} → {prop_type}")
            else:
                print("      (Property yok)")
        
        # Pattern'leri çek ve detaylı yazdır
        patterns = self.get_relationship_patterns()
        print(f"\n🔄 RELATIONSHIP PATTERNS ({len(patterns)} adet):")
        print("-" * 80)
        
        for i, pattern in enumerate(patterns, 1):
            print(f"{i:2d}. {pattern}")
        
        # Field tip analizi
        field_analysis = self.get_field_type_analysis()
        print(f"\n🔍 FIELD TYPE ANALYSIS ({len(field_analysis)} adet):")
        print("-" * 80)
        
        for field, data in field_analysis.items():
            print(f"\n{field}:")
            if isinstance(data, list):
                for sample in data:
                    print(f"   Value: {sample.get('value', 'N/A')} → Type: {sample.get('inferred_type', 'unknown')}")
                    if 'length' in sample:
                        print(f"   String Length: {sample['length']}")
                    if 'str_representation' in sample:
                        print(f"   String Repr: {sample['str_representation']}")
            else:
                print(f"   {data}")
        
        print("\n" + "="*100)

    def format_schema_string(self) -> str:
        """En sade schema format - sadece essential bilgiler"""
        # Node'ları çek
        node_schemas = self.get_node_schemas()
        
        # Relationship'leri çek
        rel_schemas = self.get_relationship_schemas()
        
        # Pattern'leri çek
        patterns = self.get_relationship_patterns()
        
        # Format et
        schema_lines = []
        
        # Nodes formatı
        nodes_line = "Nodes: "
        node_parts = []
        for label, properties in node_schemas.items():
            if properties:
                props_str = ", ".join(properties)
                node_parts.append(f"{label}({props_str})")
            else:
                node_parts.append(label)
        nodes_line += "; ".join(node_parts)
        schema_lines.append(nodes_line)
        
        # Relationships formatı - backtick'siz
        rels_line = "Rels: "
        rel_parts = []
        for rel in rel_schemas:
            if rel['properties']:
                props_str = ", ".join(rel['properties'])
                rel_parts.append(f"{rel['type']}({props_str})")
            else:
                rel_parts.append(rel['type'])
        rels_line += "; ".join(rel_parts)
        schema_lines.append(rels_line)
        
        # Patterns formatı
        patterns_line = "Patterns: " + "; ".join(patterns)
        schema_lines.append(patterns_line)
        
        return "\n".join(schema_lines)
    
    def generate_cypher_rules(self) -> str:
        """Field tiplerine göre Cypher kuralları oluştur - sistematik yaklaşım"""
        logger.info("📋 Cypher kuralları oluşturuluyor...")
        
        node_schemas = self.get_node_schemas()
        
        rules = []
        rules.append("## 🔧 FIELD TYPE SPECIFIC CYPHER RULES:")
        rules.append("")
        rules.append("**Her field için doğru tip kullanımı:**")
        rules.append("")
        
        # Organize field types for better clarity
        field_examples = {
            'string': [],
            'integer': [],
            'float': [],
            'boolean': [],
            'datetime': [],
            'array': []
        }
        
        # Her node için field tiplerini analiz et
        for label, properties in node_schemas.items():
            if not properties:
                continue
                
            for prop in properties:
                if ":" not in prop:
                    continue
                    
                prop_name, prop_type = prop.split(":", 1)
                
                if prop_type == "string":
                    field_examples['string'].append(f"{label}.{prop_name}")
                elif prop_type == "integer":
                    field_examples['integer'].append(f"{label}.{prop_name}")
                elif prop_type == "float":
                    field_examples['float'].append(f"{label}.{prop_name}")
                elif prop_type == "boolean":
                    field_examples['boolean'].append(f"{label}.{prop_name}")
                elif prop_type == "datetime":
                    field_examples['datetime'].append(f"{label}.{prop_name}")
                elif "[]" in prop_type or "array" in prop_type:
                    field_examples['array'].append(f"{label}.{prop_name}")
        
        # String fields
        if field_examples['string']:
            rules.append("### STRING FIELDS:")
            rules.append("**ZORUNLU FORMAT**: `toLower(apoc.text.clean(field)) CONTAINS toLower(apoc.text.clean(\"value\"))`")
            rules.append("**Examples**:")
            for field in field_examples['string'][:5]:  # İlk 5 örnek
                rules.append(f"- {field}: `toLower(apoc.text.clean({field})) CONTAINS toLower(apoc.text.clean(\"search_term\"))`")
            rules.append("")
        
        # Integer fields
        if field_examples['integer']:
            rules.append("### INTEGER FIELDS:")
            rules.append("**ZORUNLU FORMAT**: Direct numeric comparison - NO string conversion")
            rules.append("**Examples**:")
            for field in field_examples['integer'][:5]:
                variable = field.split('.')[0].lower()
                rules.append(f"- {field}: `{variable}.{field.split('.')[1]} = 2020` (NOT toString() or apoc.text.clean())")
            rules.append("")
        
        # Float fields
        if field_examples['float']:
            rules.append("### FLOAT FIELDS:")
            rules.append("**FORMAT**: Direct numeric comparison")
            rules.append("**Examples**:")
            for field in field_examples['float'][:3]:
                variable = field.split('.')[0].lower()
                rules.append(f"- {field}: `{variable}.{field.split('.')[1]} > 1000.0`")
            rules.append("")
        
        # Boolean fields
        if field_examples['boolean']:
            rules.append("### BOOLEAN FIELDS:")
            rules.append("**FORMAT**: Direct boolean comparison")
            rules.append("**Examples**:")
            for field in field_examples['boolean'][:3]:
                variable = field.split('.')[0].lower()
                rules.append(f"- {field}: `{variable}.{field.split('.')[1]} = true`")
            rules.append("")
        
        # Array fields
        if field_examples['array']:
            rules.append("### ARRAY FIELDS:")
            rules.append("**FORMAT**: Use IN operator or array functions")
            rules.append("**Examples**:")
            for field in field_examples['array'][:3]:
                variable = field.split('.')[0].lower()
                rules.append(f"- {field}: `\"value\" IN {variable}.{field.split('.')[1]}`")
            rules.append("")
        
        # Critical warnings based on common mistakes
        rules.append("## ⚠️ CRITICAL TYPE WARNINGS:")
        rules.append("")
        
        # Find year fields and warn about common mistakes
        year_fields = [f for f in field_examples['integer'] if 'year' in f.lower()]
        if year_fields:
            rules.append("**YEAR FIELDS (INTEGER):**")
            for field in year_fields:
                variable = field.split('.')[0].lower()
                rules.append(f"- {field}: USE `{variable}.{field.split('.')[1]} = 2020` NOT `toString({variable}.{field.split('.')[1]}) = \"2020\"`")
            rules.append("")
        
        # Common string fields that are often misused
        name_fields = [f for f in field_examples['string'] if any(keyword in f.lower() for keyword in ['name', 'title', 'filename'])]
        if name_fields:
            rules.append("**NAME/TITLE FIELDS (STRING):**")
            for field in name_fields[:3]:
                rules.append(f"- {field}: MUST use apoc.text.clean() and toLower() for searches")
            rules.append("")
        
        # Error examples
        rules.append("## ❌ COMMON MISTAKES TO AVOID:")
        rules.append("```cypher")
        rules.append("// WRONG - Integer field treated as string")
        rules.append("WHERE toLower(apoc.text.clean(py.year)) CONTAINS toLower(apoc.text.clean(\"2020\"))")
        rules.append("")
        rules.append("// CORRECT - Integer field with numeric comparison")
        rules.append("WHERE py.year = 2020")
        rules.append("")
        rules.append("// WRONG - String field without cleaning")
        rules.append("WHERE c.fullName CONTAINS \"ayça\"")
        rules.append("")
        rules.append("// CORRECT - String field with proper cleaning")
        rules.append("WHERE toLower(apoc.text.clean(c.fullName)) CONTAINS toLower(apoc.text.clean(\"ayça\"))")
        rules.append("```")
        
        return "\n".join(rules)
    
    def save_to_file(self, filename: str = "neo4j_schema.txt"):
        """Schema'yı dosyaya kaydet"""
        schema_string = self.format_schema_string()
        cypher_rules = self.generate_cypher_rules()
        
        filepath = os.path.join(os.path.dirname(__file__), filename)
        
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write("# Neo4j Database Schema\n")
                f.write(f"# Generated on: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                f.write(schema_string)
                f.write("\n\n")
                f.write(cypher_rules)
            
            logger.info(f"✅ Schema dosyaya kaydedildi: {filepath}")
            return filepath
        except Exception as e:
            logger.error(f"❌ Dosya kaydetme hatası: {e}")
            return None
    
    def update_intelligent_agent_schema(self):
        """intelligent_agent.py dosyasındaki schema'yı güncelle"""
        logger.info("🔄 intelligent_agent.py schema'sı güncelleniyor...")
        
        new_schema = self.format_schema_string()
        
        # intelligent_agent.py dosyasını oku
        agent_file = os.path.join(os.path.dirname(__file__), "intelligent_agent.py")
        
        try:
            with open(agent_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Schema bölümünü bul ve değiştir
            schema_start = content.find("Nodes: Document(")
            if schema_start == -1:
                logger.warning("❌ Schema başlangıcı bulunamadı")
                return False
            
            # Schema bitiş noktasını bul (Patterns satırının bitimi)
            patterns_start = content.find("Patterns: ", schema_start)
            if patterns_start == -1:
                logger.warning("❌ Patterns başlangıcı bulunamadı")
                return False
            
            # Patterns satırının sonunu bul
            schema_end = content.find("\n", patterns_start)
            if schema_end == -1:
                schema_end = len(content)
            
            # Eski schema'yı yeni ile değiştir
            old_schema = content[schema_start:schema_end]
            new_content = content.replace(old_schema, new_schema)
            
            # Dosyayı geri yaz
            with open(agent_file, 'w', encoding='utf-8') as f:
                f.write(new_content)
            
            logger.info("✅ intelligent_agent.py schema'sı güncellendi")
            
            # Değişiklikleri göster
            print("\n" + "="*80)
            print("SCHEMA GÜNCELLEME RAPORU")
            print("="*80)
            print(f"Eski Schema ({len(old_schema)} karakter):")
            print(old_schema[:200] + "..." if len(old_schema) > 200 else old_schema)
            print("\n" + "-"*40)
            print(f"Yeni Schema ({len(new_schema)} karakter):")
            print(new_schema[:200] + "..." if len(new_schema) > 200 else new_schema)
            print("="*80)
            
            return True
            
        except Exception as e:
            logger.error(f"❌ intelligent_agent.py güncelleme hatası: {e}")
            return False

def get_compact_schema(graph=None) -> str:
    """
    Basit kullanım için wrapper fonksiyon.
    Direkt compact schema string döndürür.
    
    Args:
        graph: Neo4jGraph instance (optional). Eğer verilmezse yeni bağlantı oluşturur.
    
    Returns:
        str: Compact schema format string
        
    Example:
        >>> from schema_extractor import get_compact_schema
        >>> schema = get_compact_schema()
        >>> print(schema)
        
        # Existing graph connection ile
        >>> schema = get_compact_schema(existing_graph)
    """
    try:
        if graph:
            # Mevcut graph connection'ı kullan
            extractor = Neo4jSchemaExtractor()
            extractor.graph = graph  # Mevcut graph'ı override et
            return extractor.format_schema_string()
        else:
            # Yeni bağlantı oluştur
            extractor = Neo4jSchemaExtractor()
            return extractor.format_schema_string()
    except Exception as e:
        return f"Error: {e}"

def get_detailed_schema() -> dict:
    """
    Detaylı schema bilgilerini dict olarak döndürür.
    
    Returns:
        dict: {
            'nodes': dict,
            'relationships': list,
            'patterns': list,
            'compact_string': str
        }
        
    Example:
        >>> from schema_extractor import get_detailed_schema
        >>> schema_info = get_detailed_schema()
        >>> print(schema_info['nodes'])
        >>> print(schema_info['compact_string'])
    """
    try:
        extractor = Neo4jSchemaExtractor()
        
        return {
            'nodes': extractor.get_node_schemas(),
            'relationships': extractor.get_relationship_schemas(),
            'patterns': extractor.get_relationship_patterns(),
            'compact_string': extractor.format_schema_string()
        }
    except Exception as e:
        return {'error': str(e)}

# Export edilen fonksiyonlar ve class'lar
__all__ = [
    'Neo4jSchemaExtractor',
    'get_compact_schema', 
    'get_detailed_schema'
]

def main():
    """Ana fonksiyon - Sadece compact schema çıktısı"""
    try:
        schema_string = get_compact_schema()
        print(schema_string)
        
    except Exception as e:
        print(f"❌ Hata: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
