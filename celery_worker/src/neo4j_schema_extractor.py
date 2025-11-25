#!/usr/bin/env python3
"""
Neo4j Schema Extractor
Veritabanındaki tüm node türlerini, property'lerini ve ilişkilerini 
tekil olarak çıkarır ve detaylı şema bilgisi sağlar.
"""

import os
import sys
import json
from typing import Dict, List, Set, Tuple
from langchain_neo4j import Neo4jGraph
from dotenv import load_dotenv

# Path ayarla
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'backend'))

load_dotenv()

class Neo4jSchemaExtractor:
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

    def get_all_node_labels(self) -> List[str]:
        """Tüm node label'larını getirir"""
        query = "CALL db.labels()"
        result = self.graph.query(query)
        labels = [record['label'] for record in result]
        return sorted(labels)

    def get_all_relationship_types(self) -> List[str]:
        """Tüm relationship türlerini getirir"""
        query = "CALL db.relationshipTypes()"
        result = self.graph.query(query)
        rel_types = [record['relationshipType'] for record in result]
        return sorted(rel_types)

    def get_node_properties(self, label: str) -> Dict[str, Set[str]]:
        """Belirli bir node label'ı için tüm property'leri ve türlerini getirir"""
        query = f"""
        MATCH (n:`{label}`)
        WITH keys(n) as props, n
        UNWIND props as prop
        RETURN prop, 
               collect(DISTINCT apoc.meta.type(n[prop])) as types,
               count(*) as usage_count
        ORDER BY usage_count DESC
        """
        
        try:
            result = self.graph.query(query)
            properties = {}
            for record in result:
                prop_name = record['prop']
                prop_types = record['types']
                usage_count = record['usage_count']
                properties[prop_name] = {
                    'types': prop_types,
                    'usage_count': usage_count
                }
            return properties
        except Exception as e:
            print(f"⚠️  APOC kullanılamıyor, basit property analizi yapılıyor: {e}")
            # APOC olmadan basit analiz
            query = f"""
            MATCH (n:`{label}`)
            RETURN keys(n) as props, count(*) as node_count
            LIMIT 100
            """
            result = self.graph.query(query)
            all_props = set()
            for record in result:
                if record['props']:
                    all_props.update(record['props'])
            
            # Basit property bilgisi döndür
            return {prop: {'types': ['unknown'], 'usage_count': 0} for prop in sorted(all_props)}

    def get_relationship_properties(self, rel_type: str) -> Dict[str, Set[str]]:
        """Belirli bir relationship türü için tüm property'leri getirir"""
        query = f"""
        MATCH ()-[r:`{rel_type}`]-()
        WITH keys(r) as props, r
        UNWIND props as prop
        RETURN prop, 
               collect(DISTINCT apoc.meta.type(r[prop])) as types,
               count(*) as usage_count
        ORDER BY usage_count DESC
        LIMIT 50
        """
        
        try:
            result = self.graph.query(query)
            properties = {}
            for record in result:
                prop_name = record['prop']
                prop_types = record['types']
                usage_count = record['usage_count']
                properties[prop_name] = {
                    'types': prop_types,
                    'usage_count': usage_count
                }
            return properties
        except Exception as e:
            # APOC olmadan basit analiz
            query = f"""
            MATCH ()-[r:`{rel_type}`]-()
            RETURN keys(r) as props, count(*) as rel_count
            LIMIT 50
            """
            result = self.graph.query(query)
            all_props = set()
            for record in result:
                if record['props']:
                    all_props.update(record['props'])
            
            return {prop: {'types': ['unknown'], 'usage_count': 0} for prop in sorted(all_props)}

    def get_node_relationships(self, label: str) -> Dict[str, Dict]:
        """Belirli bir node label'ının tüm ilişkilerini getirir"""
        query = f"""
        MATCH (n:`{label}`)-[r]-(m)
        RETURN 
            type(r) as relationship_type,
            labels(m) as target_labels,
            startNode(r) = n as is_outgoing,
            count(*) as frequency
        ORDER BY frequency DESC
        """
        
        result = self.graph.query(query)
        relationships = {}
        
        for record in result:
            rel_type = record['relationship_type']
            target_labels = record['target_labels']
            is_outgoing = record['is_outgoing']
            frequency = record['frequency']
            
            direction = "OUTGOING" if is_outgoing else "INCOMING"
            
            if rel_type not in relationships:
                relationships[rel_type] = {
                    'outgoing': {},
                    'incoming': {}
                }
            
            direction_key = 'outgoing' if is_outgoing else 'incoming'
            
            for target_label in target_labels:
                if target_label not in relationships[rel_type][direction_key]:
                    relationships[rel_type][direction_key][target_label] = 0
                relationships[rel_type][direction_key][target_label] += frequency
                
        return relationships

    def get_database_statistics(self) -> Dict:
        """Veritabanı istatistiklerini getirir"""
        stats = {}
        
        # Toplam node sayısı
        result = self.graph.query("MATCH (n) RETURN count(n) as total_nodes")
        stats['total_nodes'] = result[0]['total_nodes']
        
        # Toplam relationship sayısı  
        result = self.graph.query("MATCH ()-[r]-() RETURN count(r) as total_relationships")
        stats['total_relationships'] = result[0]['total_relationships']
        
        # Label bazında node sayıları
        stats['node_counts_by_label'] = {}
        labels = self.get_all_node_labels()
        for label in labels:
            result = self.graph.query(f"MATCH (n:`{label}`) RETURN count(n) as count")
            stats['node_counts_by_label'][label] = result[0]['count']
        
        # Relationship türü bazında sayılar
        stats['relationship_counts_by_type'] = {}
        rel_types = self.get_all_relationship_types()
        for rel_type in rel_types:
            result = self.graph.query(f"MATCH ()-[r:`{rel_type}`]-() RETURN count(r) as count")
            stats['relationship_counts_by_type'][rel_type] = result[0]['count']
            
        return stats

    def extract_complete_schema(self) -> Dict:
        """Tüm şemayı çıkarır"""
        print("📊 Neo4j şeması çıkarılıyor...")
        
        schema = {
            'database_statistics': {},
            'node_labels': [],
            'relationship_types': [],
            'nodes': {},
            'relationships': {},
            'node_relationships': {}
        }
        
        # İstatistikler
        print("📈 Veritabanı istatistikleri alınıyor...")
        schema['database_statistics'] = self.get_database_statistics()
        
        # Node label'ları
        print("🏷️  Node label'ları alınıyor...")
        schema['node_labels'] = self.get_all_node_labels()
        
        # Relationship türleri
        print("🔗 Relationship türleri alınıyor...")
        schema['relationship_types'] = self.get_all_relationship_types()
        
        # Her node label için detaylı bilgi
        print(f"🔍 {len(schema['node_labels'])} node label'ı analiz ediliyor...")
        for label in schema['node_labels']:
            print(f"   📋 {label} analiz ediliyor...")
            
            # Node properties
            schema['nodes'][label] = {
                'properties': self.get_node_properties(label),
                'count': schema['database_statistics']['node_counts_by_label'].get(label, 0)
            }
            
            # Node relationships
            schema['node_relationships'][label] = self.get_node_relationships(label)
        
        # Her relationship türü için detaylı bilgi
        print(f"🔗 {len(schema['relationship_types'])} relationship türü analiz ediliyor...")
        for rel_type in schema['relationship_types']:
            print(f"   🔗 {rel_type} analiz ediliyor...")
            schema['relationships'][rel_type] = {
                'properties': self.get_relationship_properties(rel_type),
                'count': schema['database_statistics']['relationship_counts_by_type'].get(rel_type, 0)
            }
        
        return schema

    def print_schema_summary(self, schema: Dict):
        """Şema özetini yazdırır"""
        print("\n" + "="*80)
        print("📊 NEO4J VERİTABANI ŞEMA ÖZETİ")
        print("="*80)
        
        stats = schema['database_statistics']
        print(f"\n📈 İstatistikler:")
        print(f"   • Toplam Node Sayısı: {stats['total_nodes']:,}")
        print(f"   • Toplam İlişki Sayısı: {stats['total_relationships']:,}")
        print(f"   • Node Label Türü: {len(schema['node_labels'])}")
        print(f"   • İlişki Türü: {len(schema['relationship_types'])}")
        
        print(f"\n🏷️  Node Label'ları:")
        for label in schema['node_labels']:
            count = schema['nodes'][label]['count']
            prop_count = len(schema['nodes'][label]['properties'])
            print(f"   • {label}: {count:,} node, {prop_count} property")
        
        print(f"\n🔗 İlişki Türleri:")
        for rel_type in schema['relationship_types']:
            count = schema['relationships'][rel_type]['count']
            prop_count = len(schema['relationships'][rel_type]['properties'])
            print(f"   • {rel_type}: {count:,} ilişki, {prop_count} property")
        
        print(f"\n📋 Node Properties Detayı:")
        for label, node_info in schema['nodes'].items():
            print(f"\n   🏷️  {label} ({node_info['count']:,} node):")
            for prop_name, prop_info in node_info['properties'].items():
                types_str = ", ".join(prop_info['types'])
                usage = prop_info['usage_count']
                print(f"      • {prop_name}: {types_str} (kullanım: {usage:,})")
        
        print(f"\n🔗 Relationship Properties Detayı:")
        for rel_type, rel_info in schema['relationships'].items():
            if rel_info['properties']:
                print(f"\n   🔗 {rel_type} ({rel_info['count']:,} ilişki):")
                for prop_name, prop_info in rel_info['properties'].items():
                    types_str = ", ".join(prop_info['types'])
                    usage = prop_info['usage_count']
                    print(f"      • {prop_name}: {types_str} (kullanım: {usage:,})")
        
        print(f"\n🌐 Node İlişki Haritası:")
        for label, relationships in schema['node_relationships'].items():
            print(f"\n   🏷️  {label}:")
            for rel_type, directions in relationships.items():
                # Outgoing relationships
                if directions['outgoing']:
                    for target_label, frequency in directions['outgoing'].items():
                        print(f"      • ({label})-[{rel_type}]->({target_label}): {frequency:,}")
                
                # Incoming relationships  
                if directions['incoming']:
                    for source_label, frequency in directions['incoming'].items():
                        print(f"      • ({source_label})-[{rel_type}]->({label}): {frequency:,}")

def main():
    """Ana fonksiyon"""
    try:
        print("🚀 Neo4j Schema Extractor başlatılıyor...")
        
        extractor = Neo4jSchemaExtractor()
        
        # Şemayı çıkar
        schema = extractor.extract_complete_schema()
        
        # Özeti yazdır
        extractor.print_schema_summary(schema)
        
        # JSON dosyasına kaydet
        output_file = "neo4j_complete_schema.json"
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(schema, f, indent=2, ensure_ascii=False)
        
        print(f"\n💾 Detaylı şema bilgisi '{output_file}' dosyasına kaydedildi.")
        print(f"📄 Dosya boyutu: {os.path.getsize(output_file) / 1024:.1f} KB")
        
    except Exception as e:
        print(f"❌ Hata oluştu: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
