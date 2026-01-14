#!/usr/bin/env python3
"""
Schema fonksiyonunu test eder
Kullanım: python test_schema.py
"""

import os
import sys
import time
sys.path.insert(0, 'src')

from langchain_community.graphs import Neo4jGraph

# Neo4j bağlantısı (environment'tan veya default)
NEO4J_URL = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASS = os.getenv("NEO4J_PASSWORD", "password")
NEO4J_DB = os.getenv("NEO4J_DATABASE", "neo4j")


def fetch_schema_apoc(graph) -> str:
    """
    APOC ile şema çekme - TEK SORGU ile tüm bilgi
    
    Format:
    # NODES
    (NodeName:count){prop1:type,prop2:type,...}
    
    # RELATIONSHIPS
    (FromNode)-[:REL_TYPE]->(ToNode)
    """
    print("🔍 APOC meta.schema() ile şema çekiliyor...")
    start_time = time.time()
    
    try:
        # APOC meta.schema() - TEK SORGU
        result = graph.query("CALL apoc.meta.schema() YIELD value RETURN value")
        if not result:
            print("❌ APOC sonuç döndürmedi")
            return ""
        
        schema_data = result[0]["value"]
        elapsed = time.time() - start_time
        print(f"⏱️ APOC sorgusu: {elapsed:.2f} saniye")
        
        # Tip dönüşümü
        type_map = {
            "STRING": "str", "INTEGER": "int", "FLOAT": "float",
            "BOOLEAN": "bool", "DATE_TIME": "dt", "DATE": "dt",
            "LOCAL_DATE_TIME": "dt", "LIST": "list"
        }
        
        # Node'ları ve Relationship'leri ayır
        nodes = {}
        relationships = []
        
        for name, info in schema_data.items():
            if info.get("type") == "node":
                # Node bilgisi
                count = info.get("count", 0)
                props = []
                for prop_name, prop_info in info.get("properties", {}).items():
                    if prop_name not in ["embedding", "id", "uuid", "elementId"]:
                        prop_type = type_map.get(prop_info.get("type", "STRING"), "str")
                        props.append(f"{prop_name}:{prop_type}")
                nodes[name] = {"count": count, "props": props}
                
                # Bu node'un relationship'leri
                for rel_name, rel_info in info.get("relationships", {}).items():
                    direction = rel_info.get("direction", "out")
                    target_labels = rel_info.get("labels", [])
                    for target in target_labels:
                        if direction == "out":
                            relationships.append((name, rel_name, target))
                        else:
                            relationships.append((target, rel_name, name))
            
            elif info.get("type") == "relationship":
                # Relationship type bilgisi (ayrıca tutulabilir)
                pass
        
        # Format oluştur
        lines = ["# NODES"]
        # Count'a göre sırala
        sorted_nodes = sorted(nodes.items(), key=lambda x: x[1]["count"], reverse=True)
        for label, info in sorted_nodes:
            count = info["count"]
            props = info["props"]
            if props:
                lines.append(f"({label}:{count}){{{','.join(props)}}}")  # TÜM property'ler
            else:
                lines.append(f"({label}:{count})")
        
        lines.append("")
        lines.append("# RELATIONSHIPS")
        
        # Unique pattern'ler - HİÇBİR FİLTRE YOK
        seen = set()
        for from_l, rel, to_l in relationships:
            pattern = f"({from_l})-[:{rel}]->({to_l})"
            if pattern not in seen:
                seen.add(pattern)
                lines.append(pattern)
        
        schema = "\n".join(lines)
        print(f"✅ APOC Schema: {len(schema)} karakter, {len(sorted_nodes)} node, {len(seen)} pattern")
        return schema
        
    except Exception as e:
        print(f"❌ APOC hatası: {e}")
        return ""


def fetch_schema_lightweight(graph) -> str:
    """
    Hafif şema sorgusu - Token tasarrufu için minimal format
    
    Format:
    # NODES
    (NodeName:count){prop1:type,prop2:type,...}
    
    # RELATIONSHIPS
    (FromNode)-[:REL_TYPE]->(ToNode)
    """
    # Tip kısaltmaları
    type_mapping = {
        "createdAt": "dt", "updatedAt": "dt",
        "created_at": "dt", "updated_at": "dt",
        "amount": "float", "count": "int", "year": "int", "month": "int",
    }
    
    print("🔍 Neo4j'den şema çekiliyor (hafif sorgu)...")
    
    # 1. Node label'larını ve sayılarını al
    labels_query = """
    CALL db.labels() YIELD label
    CALL {
        WITH label
        MATCH (n) WHERE label IN labels(n)
        RETURN count(n) as cnt
    }
    RETURN label, cnt
    ORDER BY cnt DESC
    """
    
    try:
        labels_result = graph.query(labels_query)
    except Exception as e:
        print(f"⚠️ Count sorgusu başarısız: {e}")
        # Fallback: sadece label listesi
        labels_result = graph.query("CALL db.labels() YIELD label RETURN label, 0 as cnt")
    
    labels_with_count = [(r["label"], r["cnt"]) for r in labels_result] if labels_result else []
    print(f"📋 {len(labels_with_count)} node label bulundu")
    
    # 2. Her label için property'leri al (sampling)
    node_props = {}
    for label, _ in labels_with_count:  # Tüm label'lar
        try:
            prop_query = f"MATCH (n:`{label}`) RETURN keys(n) as props LIMIT 1"
            prop_result = graph.query(prop_query)
            if prop_result and prop_result[0].get("props"):
                # Gereksiz property'leri filtrele
                props = [p for p in prop_result[0]["props"] 
                        if p not in ["embedding", "id", "uuid", "elementId"]]
                node_props[label] = props  # Tüm property'ler
        except:
            pass
    
    # 3. Relationship pattern'larını al (APOC olmadan - sampling ile)
    rel_patterns_query = """
    CALL db.relationshipTypes() YIELD relationshipType as type
    RETURN type
    """
    rel_types_result = graph.query(rel_patterns_query)
    rel_types = [r["type"] for r in rel_types_result] if rel_types_result else []
    
    # Her rel type için TÜM unique pattern'leri bul
    patterns = []
    for rel_type in rel_types:  # Tüm relationship type'lar
        try:
            # DISTINCT ile tüm unique from->to kombinasyonlarını al
            pattern_query = f"""
            MATCH (a)-[r:`{rel_type}`]->(b)
            RETURN DISTINCT labels(a)[0] as fromLabel, labels(b)[0] as toLabel
            """
            pattern_result = graph.query(pattern_query)
            for row in pattern_result:
                from_label = row.get("fromLabel", "?")
                to_label = row.get("toLabel", "?")
                patterns.append((from_label, rel_type, to_label))
        except:
            pass
    
    print(f"📋 {len(patterns)} relationship pattern bulundu")
    
    # 4. Format oluştur
    lines = ["# NODES"]
    for label, count in labels_with_count:
        props = node_props.get(label, [])
        props_with_types = []
        for prop in props:
            if prop in type_mapping:
                prop_type = type_mapping[prop]
            elif any(x in prop.lower() for x in ["id", "name", "text", "content", "title"]):
                prop_type = "str"
            elif any(x in prop.lower() for x in ["date", "time"]):
                prop_type = "dt"
            elif any(x in prop.lower() for x in ["count", "number", "amount", "year"]):
                prop_type = "int"
            else:
                prop_type = "str"
            props_with_types.append(f"{prop}:{prop_type}")
        
        if props_with_types:
            lines.append(f"({label}:{count}){{{','.join(props_with_types)}}}")
        else:
            lines.append(f"({label}:{count})")
    
    lines.append("")
    lines.append("# RELATIONSHIPS")
    
    seen_patterns = set()
    for from_label, rel_type, to_label in patterns:
        pattern = f"({from_label})-[:{rel_type}]->({to_label})"
        if pattern not in seen_patterns:
            seen_patterns.add(pattern)
            lines.append(pattern)
    
    schema = "\n".join(lines)
    print(f"✅ Schema oluşturuldu: {len(schema)} karakter, {len(labels_with_count)} node, {len(seen_patterns)} pattern")
    return schema


if __name__ == "__main__":
    print(f"Bağlanılıyor: {NEO4J_URL}")
    
    graph = Neo4jGraph(
        url=NEO4J_URL,
        username=NEO4J_USER,
        password=NEO4J_PASS,
        database=NEO4J_DB
    )
    
    # ========== APOC TEST ==========
    print("\n" + "="*60)
    print("1️⃣ APOC META.SCHEMA() TESTİ")
    print("="*60)
    start1 = time.time()
    schema_apoc = fetch_schema_apoc(graph)
    time_apoc = time.time() - start1
    
    print("\n--- APOC ÇIKTISI ---")
    print(schema_apoc)
    # if len(schema_apoc) > 2000:
    #     print(f"... ({len(schema_apoc) - 2000} karakter daha)")
    
    # ========== LIGHTWEIGHT TEST ==========
    # print("\n" + "="*60)
    # print("2️⃣ LIGHTWEIGHT (MEVCUT) TESTİ")
    # print("="*60)
    # start2 = time.time()
    # schema_light = fetch_schema_lightweight(graph)
    # time_light = time.time() - start2
    
    # print("\n--- LIGHTWEIGHT ÇIKTISI ---")
    # print(schema_light[:2000] if len(schema_light) > 2000 else schema_light)
    # if len(schema_light) > 2000:
    #     print(f"... ({len(schema_light) - 2000} karakter daha)")
    
    # ========== SchemaVersionCache ENTEGRASYON TESTİ ==========
    print("\n" + "="*60)
    print("3️⃣ SchemaVersionCache ENTEGRASYON TESTİ")
    print("="*60)
    from src.shared.schema_cache import SchemaVersionCache
    
    cache = SchemaVersionCache()
    cache.invalidate_cache()  # RAM cache'i temizle
    
    start3 = time.time()
    schema_cached = cache.get_schema(NEO4J_URL, graph)
    time_cached = time.time() - start3
    
    # print(f"\n⏱️ SchemaVersionCache süresi: {time_cached:.2f} saniye")
    # print(f"📏 Schema boyutu: {len(schema_cached)} karakter")
    # print("\n--- İLK 1500 KARAKTER ---")
    # print(schema_cached[:1500])
    
    # ========== KARŞILAŞTIRMA ==========
    # print("\n" + "="*60)
    # print("📊 KARŞILAŞTIRMA")
    # print("="*60)
    # print(f"{'Yöntem':<25} {'Süre (sn)':<12} {'Boyut (char)':<15}")
    # print("-"*55)
    # print(f"{'APOC (direkt)':<25} {time_apoc:<12.2f} {len(schema_apoc):<15}")
    # print(f"{'Lightweight (direkt)':<25} {time_light:<12.2f} {len(schema_light):<15}")
    # print(f"{'SchemaVersionCache':<25} {time_cached:<12.2f} {len(schema_cached):<15}")
    # print("-"*55)
    
    # if time_apoc < time_light:
    #     print(f"✅ APOC {time_light/time_apoc:.1f}x daha hızlı!")
    # else:
    #     print(f"✅ Lightweight {time_apoc/time_light:.1f}x daha hızlı!")
    
    # print(f"\n🎯 SchemaVersionCache artık APOC kullanıyor: {'✅ Evet' if time_cached < 10 else '❌ Hayır'}")

