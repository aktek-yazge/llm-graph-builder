#!/usr/bin/env python3
"""
Neo4j Comprehensive Schema Generator
Sistemdeki tüm node'ları ve ilişkileri benzersiz şema halinde çıkarır.
"""

import os
import sys
import json
from typing import Dict, List
from langchain_neo4j import Neo4jGraph
from dotenv import load_dotenv

# Path ayarla
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'backend'))

load_dotenv()

class Neo4jSchemaGenerator:
    def __init__(self):
        """Neo4j bağlantısını kurar"""
        self.uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        self.username = os.getenv("NEO4J_USERNAME", "neo4j")
        self.password = os.getenv("NEO4J_PASSWORD", "password")
        
        print(f"🔗 Neo4j'ye bağlanıyor: {self.uri}")
        self.graph = Neo4jGraph(
            url=self.uri,
            username=self.username,
            password=self.password
        )
        print("✅ Neo4j bağlantısı başarılı!")

    def get_unique_relationship_patterns(self) -> List[Dict]:
        """Benzersiz ilişki desenlerini çıkarır"""
        query = """
        MATCH (a)-[r]->(b)
        RETURN DISTINCT 
            labels(a) as source_labels,
            type(r) as relationship_type,
            labels(b) as target_labels,
            count(*) as frequency
        ORDER BY frequency DESC
        """
        
        result = self.graph.query(query)
        patterns = []
        
        for record in result:
            source_labels = record['source_labels']
            rel_type = record['relationship_type']
            target_labels = record['target_labels']
            frequency = record['frequency']
            
            # Her source label kombinasyonu için
            for source_label in source_labels:
                for target_label in target_labels:
                    pattern = {
                        'source': source_label,
                        'relationship': rel_type,
                        'target': target_label,
                        'frequency': frequency,
                        'pattern': f"({source_label})-[{rel_type}]->({target_label})"
                    }
                    patterns.append(pattern)
        
        return patterns

    def get_node_property_samples(self, label: str, limit: int = 3) -> List[Dict]:
        """Node property örneklerini getirir"""
        query = f"""
        MATCH (n:`{label}`)
        RETURN properties(n) as props
        LIMIT {limit}
        """
        
        result = self.graph.query(query)
        samples = []
        
        for record in result:
            props = record['props']
            if props and isinstance(props, dict):
                sample = {}
                for key, value in props.items():
                    # Embedding gibi uzun değerleri kısalt
                    if isinstance(value, list) and len(value) > 10:
                        sample[key] = f"[array of {len(value)} elements]"
                    elif isinstance(value, str) and len(value) > 100:
                        sample[key] = value[:100] + "..."
                    elif hasattr(value, 'isoformat'):  # DateTime objects
                        sample[key] = value.isoformat()
                    else:
                        sample[key] = value
                samples.append(sample)
            else:
                samples.append({})
        
        return samples

    def get_relationship_property_samples(self, rel_type: str, limit: int = 3) -> List[Dict]:
        """Relationship property örneklerini getirir"""
        query = f"""
        MATCH ()-[r:`{rel_type}`]-()
        RETURN properties(r) as props
        LIMIT {limit}
        """
        
        result = self.graph.query(query)
        samples = []
        
        for record in result:
            props = record['props']
            if props and isinstance(props, dict):
                sample = {}
                for key, value in props.items():
                    if isinstance(value, str) and len(value) > 100:
                        sample[key] = value[:100] + "..."
                    elif hasattr(value, 'isoformat'):  # DateTime objects
                        sample[key] = value.isoformat()
                    else:
                        sample[key] = value
                samples.append(sample)
            else:
                samples.append({})
        
        return samples

    def generate_cypher_schema(self) -> str:
        """Cypher CREATE statement formatında şema üretir"""
        patterns = self.get_unique_relationship_patterns()
        
        cypher_lines = []
        cypher_lines.append("// Neo4j Database Schema - Cypher CREATE Statements")
        cypher_lines.append("// Generated automatically from existing data")
        cypher_lines.append("")
        
        # Node'ları topla
        all_nodes = set()
        for pattern in patterns:
            all_nodes.add(pattern['source'])
            all_nodes.add(pattern['target'])
        
        # Node örnekleri
        cypher_lines.append("// === NODE EXAMPLES ===")
        for node_label in sorted(all_nodes):
            samples = self.get_node_property_samples(node_label, 1)
            if samples and samples[0]:
                sample = samples[0]
                props = []
                for key, value in sample.items():
                    if isinstance(value, str):
                        # Escape quotes in strings
                        escaped_value = value.replace('"', '\\"')
                        props.append(f'{key}: "{escaped_value}"')
                    elif isinstance(value, (int, float)):
                        props.append(f'{key}: {value}')
                    elif isinstance(value, bool):
                        props.append(f'{key}: {str(value).lower()}')
                    else:
                        props.append(f'{key}: "{str(value)}"')
                
                if props:
                    props_str = ", ".join(props[:5])  # İlk 5 property
                    cypher_lines.append(f"CREATE (:{node_label} {{{props_str}}})")
                else:
                    cypher_lines.append(f"CREATE (:{node_label})")
            else:
                cypher_lines.append(f"CREATE (:{node_label})")
        cypher_lines.append("")
        cypher_lines.append("// === RELATIONSHIP PATTERNS ===")
        
        # Benzersiz patternları grupla
        unique_patterns = {}
        for pattern in patterns:
            key = pattern['pattern']
            if key not in unique_patterns:
                unique_patterns[key] = pattern
            else:
                unique_patterns[key]['frequency'] += pattern['frequency']
        
        for pattern_key, pattern in sorted(unique_patterns.items(), key=lambda x: x[1]['frequency'], reverse=True):
            freq = pattern['frequency']
            cypher_lines.append(f"// Frequency: {freq}")
            cypher_lines.append(f"MATCH (a:{pattern['source']}), (b:{pattern['target']})")
            cypher_lines.append(f"CREATE (a)-[:{pattern['relationship']}]->(b)")
            cypher_lines.append("")
        
        return "\n".join(cypher_lines)

    def generate_mermaid_diagram(self) -> str:
        """Mermaid diagram formatında şema üretir"""
        patterns = self.get_unique_relationship_patterns()
        
        mermaid_lines = []
        mermaid_lines.append("graph TD")
        mermaid_lines.append("    %% Neo4j Database Schema")
        mermaid_lines.append("")
        
        # Node stilleri
        all_nodes = set()
        for pattern in patterns:
            all_nodes.add(pattern['source'])
            all_nodes.add(pattern['target'])
        
        # Node tanımları
        for i, node in enumerate(sorted(all_nodes)):
            node_id = f"N{i}"
            mermaid_lines.append(f"    {node_id}[{node}]")
        
        mermaid_lines.append("")
        
        # Node mapping
        node_map = {node: f"N{i}" for i, node in enumerate(sorted(all_nodes))}
        
        # İlişkileri grupla ve benzersiz yap
        unique_connections = set()
        for pattern in patterns:
            source_id = node_map[pattern['source']]
            target_id = node_map[pattern['target']]
            rel_type = pattern['relationship']
            connection = (source_id, target_id, rel_type)
            unique_connections.add(connection)
        
        # İlişki çizgileri
        for source_id, target_id, rel_type in sorted(unique_connections):
            mermaid_lines.append(f"    {source_id} -->|{rel_type}| {target_id}")
        
        # Stil tanımları
        mermaid_lines.append("")
        mermaid_lines.append("    %% Styling")
        mermaid_lines.append("    classDef nodeClass fill:#e1f5fe,stroke:#01579b,stroke-width:2px")
        
        for node_id in node_map.values():
            mermaid_lines.append(f"    class {node_id} nodeClass")
        
        return "\n".join(mermaid_lines)

    def generate_comprehensive_schema(self) -> Dict:
        """Kapsamlı şema bilgisi üretir"""
        print("📊 Kapsamlı şema analizi başlatılıyor...")
        
        # Temel istatistikler
        total_nodes_result = self.graph.query("MATCH (n) RETURN count(n) as count")
        total_relationships_result = self.graph.query("MATCH ()-[r]-() RETURN count(r) as count")
        
        total_nodes = total_nodes_result[0]['count']
        total_relationships = total_relationships_result[0]['count']
        
        print(f"📈 Toplam {total_nodes} node, {total_relationships} ilişki analiz ediliyor...")
        
        # Node labels
        labels_result = self.graph.query("CALL db.labels()")
        all_labels = [record['label'] for record in labels_result]
        
        # Relationship types
        rel_types_result = self.graph.query("CALL db.relationshipTypes()")
        all_rel_types = [record['relationshipType'] for record in rel_types_result]
        
        # Benzersiz ilişki desenleri
        patterns = self.get_unique_relationship_patterns()
        
        # Her node türü için örnekler ve sayılar
        node_details = {}
        for label in all_labels:
            count_result = self.graph.query(f"MATCH (n:`{label}`) RETURN count(n) as count")
            count = count_result[0]['count']
            
            samples = self.get_node_property_samples(label, 2)
            
            # Property'leri analiz et
            all_properties = set()
            for sample in samples:
                all_properties.update(sample.keys())
            
            node_details[label] = {
                'count': count,
                'properties': list(all_properties),
                'samples': samples
            }
        
        # Her relationship türü için örnekler ve sayılar
        relationship_details = {}
        for rel_type in all_rel_types:
            count_result = self.graph.query(f"MATCH ()-[r:`{rel_type}`]-() RETURN count(r) as count")
            count = count_result[0]['count']
            
            samples = self.get_relationship_property_samples(rel_type, 2)
            
            # Property'leri analiz et
            all_properties = set()
            for sample in samples:
                all_properties.update(sample.keys())
            
            relationship_details[rel_type] = {
                'count': count,
                'properties': list(all_properties),
                'samples': samples
            }
        
        # Benzersiz pattern'ları grupla
        unique_patterns = {}
        for pattern in patterns:
            key = pattern['pattern']
            if key not in unique_patterns:
                unique_patterns[key] = {
                    'source': pattern['source'],
                    'relationship': pattern['relationship'],
                    'target': pattern['target'],
                    'frequency': pattern['frequency']
                }
            else:
                unique_patterns[key]['frequency'] += pattern['frequency']
        
        schema = {
            'metadata': {
                'total_nodes': total_nodes,
                'total_relationships': total_relationships,
                'node_types_count': len(all_labels),
                'relationship_types_count': len(all_rel_types),
                'unique_patterns_count': len(unique_patterns)
            },
            'node_types': all_labels,
            'relationship_types': all_rel_types,
            'node_details': node_details,
            'relationship_details': relationship_details,
            'relationship_patterns': list(unique_patterns.values()),
            'cypher_schema': self.generate_cypher_schema(),
            'mermaid_diagram': self.generate_mermaid_diagram()
        }
        
        return schema

    def print_unique_schema_summary(self, schema: Dict):
        """Benzersiz şema özetini yazdırır"""
        print("\n" + "="*80)
        print("🏗️  NEO4J BENZERSİZ ŞEMA ANALİZİ")
        print("="*80)
        
        metadata = schema['metadata']
        print(f"\n📊 Genel İstatistikler:")
        print(f"   • Toplam Node: {metadata['total_nodes']:,}")
        print(f"   • Toplam İlişki: {metadata['total_relationships']:,}")
        print(f"   • Node Türü: {metadata['node_types_count']}")
        print(f"   • İlişki Türü: {metadata['relationship_types_count']}")
        print(f"   • Benzersiz Pattern: {metadata['unique_patterns_count']}")
        
        print(f"\n🏷️  Node Türleri ve Örnekleri:")
        for label, details in schema['node_details'].items():
            print(f"\n   📋 {label} ({details['count']:,} adet)")
            print(f"      Properties: {', '.join(details['properties'])}")
            
            if details['samples']:
                print(f"      Örnek:")
                for i, sample in enumerate(details['samples'][:1], 1):
                    sample_str = []
                    for key, value in sample.items():
                        if len(str(value)) > 50:
                            sample_str.append(f"{key}: {str(value)[:50]}...")
                        else:
                            sample_str.append(f"{key}: {value}")
                    print(f"        {', '.join(sample_str[:3])}")
        
        print(f"\n🔗 İlişki Türleri ve Örnekleri:")
        for rel_type, details in schema['relationship_details'].items():
            print(f"\n   🔗 {rel_type} ({details['count']:,} adet)")
            if details['properties']:
                print(f"      Properties: {', '.join(details['properties'])}")
            else:
                print(f"      Properties: None")
        
        print(f"\n🌐 Benzersiz İlişki Desenleri:")
        patterns = sorted(schema['relationship_patterns'], key=lambda x: x['frequency'], reverse=True)
        for pattern in patterns:
            freq = pattern['frequency']
            source = pattern['source']
            rel = pattern['relationship']
            target = pattern['target']
            print(f"   ({source})-[{rel}]->({target}) | Frequency: {freq:,}")
        
        print(f"\n📝 Schema Dosyaları:")
        print(f"   • Cypher Schema: cypher_schema.cyp")
        print(f"   • Mermaid Diagram: schema_diagram.mmd")
        print(f"   • JSON Schema: comprehensive_schema.json")

def main():
    """Ana fonksiyon"""
    try:
        print("🚀 Neo4j Comprehensive Schema Generator başlatılıyor...")
        
        generator = Neo4jSchemaGenerator()
        
        # Kapsamlı şemayı üret
        schema = generator.generate_comprehensive_schema()
        
        # Özeti yazdır
        generator.print_unique_schema_summary(schema)
        
        # Dosyalara kaydet
        
        # 1. JSON Schema
        json_file = "comprehensive_schema.json"
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(schema, f, indent=2, ensure_ascii=False)
        print(f"\n💾 Kapsamlı şema '{json_file}' dosyasına kaydedildi.")
        
        # 2. Cypher Schema
        cypher_file = "cypher_schema.cyp"
        with open(cypher_file, 'w', encoding='utf-8') as f:
            f.write(schema['cypher_schema'])
        print(f"💾 Cypher şema '{cypher_file}' dosyasına kaydedildi.")
        
        # 3. Mermaid Diagram
        mermaid_file = "schema_diagram.mmd"
        with open(mermaid_file, 'w', encoding='utf-8') as f:
            f.write(schema['mermaid_diagram'])
        print(f"💾 Mermaid diagram '{mermaid_file}' dosyasına kaydedildi.")
        
        # Dosya boyutları
        print(f"\n📄 Dosya Boyutları:")
        print(f"   • {json_file}: {os.path.getsize(json_file) / 1024:.1f} KB")
        print(f"   • {cypher_file}: {os.path.getsize(cypher_file) / 1024:.1f} KB")
        print(f"   • {mermaid_file}: {os.path.getsize(mermaid_file) / 1024:.1f} KB")
        
    except Exception as e:
        print(f"❌ Hata oluştu: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
