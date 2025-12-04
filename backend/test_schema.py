#!/usr/bin/env python3
"""
Schema fonksiyonunu test eder
Kullanım: python test_schema.py
"""

import sys
sys.path.insert(0, 'src')

from langchain_community.graphs import Neo4jGraph

# Neo4j bağlantısı
NEO4J_URL = 'bolt://3.76.55.209:7688'
NEO4J_USER = 'neo4j'
NEO4J_PASS = 'qwerty5555'
NEO4J_DB = 'neo4j'

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
    
    schema = fetch_schema_lightweight(graph)
    
    print("\n" + "="*60)
    print("SCHEMA ÇIKTISI:")
    print("="*60)
    print(schema)
    print("="*60)
    print(f"\nToplam: {len(schema)} karakter")

