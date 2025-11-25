#!/usr/bin/env python3
"""
Neo4j Schema Extractor for LLM Prompts
Extracts only node types, relationships, and properties without counts
"""

import os
import json
from datetime import datetime
from langchain_neo4j import Neo4jGraph
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def get_node_types_with_properties(graph):
    """Get all node types that actually exist with their properties and data types"""
    query = "CALL db.labels() YIELD label RETURN label"
    labels = graph.query(query)
    
    node_types = {}
    for label_result in labels:
        label = label_result['label']
        
        # Check if nodes exist and get properties with types
        check_query = f"MATCH (n:`{label}`) RETURN properties(n) as props LIMIT 1"
        try:
            node_check = graph.query(check_query)
            if node_check and node_check[0]['props']:
                props = node_check[0]['props']
                properties_with_types = {}
                
                for prop_name, prop_value in props.items():
                    # Determine data type
                    if prop_value is None:
                        data_type = "null"
                    elif isinstance(prop_value, bool):
                        data_type = "boolean"
                    elif isinstance(prop_value, int):
                        data_type = "integer"
                    elif isinstance(prop_value, float):
                        data_type = "float"
                    elif isinstance(prop_value, str):
                        data_type = "string"
                    elif isinstance(prop_value, list):
                        if prop_value:
                            # Check type of first element
                            first_elem = prop_value[0]
                            if isinstance(first_elem, str):
                                data_type = "string[]"
                            elif isinstance(first_elem, int):
                                data_type = "integer[]"
                            elif isinstance(first_elem, float):
                                data_type = "float[]"
                            else:
                                data_type = "array"
                        else:
                            data_type = "array"
                    elif hasattr(prop_value, 'isoformat'):  # DateTime objects
                        data_type = "datetime"
                    else:
                        data_type = "unknown"
                    
                    properties_with_types[prop_name] = data_type
                
                node_types[label] = {
                    "properties": properties_with_types
                }
        except:
            continue
    
    return node_types

def get_relationship_types_with_properties(graph):
    """Get all relationship types with their properties and data types"""
    query = "CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType"
    rel_types_result = graph.query(query)
    
    rel_types = {}
    for rel_result in rel_types_result:
        rel_type = rel_result['relationshipType']
        
        # Check if relationships exist and get properties with types
        check_query = f"MATCH ()-[r:`{rel_type}`]->() RETURN properties(r) as props LIMIT 1"
        try:
            rel_check = graph.query(check_query)
            if rel_check and rel_check[0]['props']:
                props = rel_check[0]['props']
                properties_with_types = {}
                
                for prop_name, prop_value in props.items():
                    # Determine data type
                    if prop_value is None:
                        data_type = "null"
                    elif isinstance(prop_value, bool):
                        data_type = "boolean"
                    elif isinstance(prop_value, int):
                        data_type = "integer"
                    elif isinstance(prop_value, float):
                        data_type = "float"
                    elif isinstance(prop_value, str):
                        data_type = "string"
                    elif isinstance(prop_value, list):
                        if prop_value:
                            # Check type of first element
                            first_elem = prop_value[0]
                            if isinstance(first_elem, str):
                                data_type = "string[]"
                            elif isinstance(first_elem, int):
                                data_type = "integer[]"
                            elif isinstance(first_elem, float):
                                data_type = "float[]"
                            else:
                                data_type = "array"
                        else:
                            data_type = "array"
                    elif hasattr(prop_value, 'isoformat'):  # DateTime objects
                        data_type = "datetime"
                    else:
                        data_type = "unknown"
                    
                    properties_with_types[prop_name] = data_type
                
                rel_types[rel_type] = {
                    "properties": properties_with_types
                }
            else:
                # Relationship exists but has no properties
                rel_types[rel_type] = {
                    "properties": {}
                }
        except:
            continue
    
    return rel_types

def get_relationship_patterns(graph):
    """Get unique relationship patterns between node types"""
    query = """
    MATCH (a)-[r]->(b)
    WITH labels(a)[0] as source_label, type(r) as rel_type, labels(b)[0] as target_label
    RETURN DISTINCT source_label, rel_type, target_label
    ORDER BY source_label, rel_type, target_label
    """
    
    results = graph.query(query)
    patterns = []
    
    for result in results:
        patterns.append({
            "source": result['source_label'],
            "relationship": result['rel_type'],
            "target": result['target_label']
        })
    
    return patterns

def generate_llm_prompt_schema(graph):
    """Generate schema information suitable for LLM prompts"""
    
    print("🔍 Extracting schema for LLM prompt...")
    
    # Get node types with properties
    node_types = get_node_types_with_properties(graph)
    
    # Get relationship types with properties
    relationship_types = get_relationship_types_with_properties(graph)
    
    # Get relationship patterns
    relationship_patterns = get_relationship_patterns(graph)
    
    schema = {
        "node_types": node_types,
        "relationship_types": relationship_types,
        "relationship_patterns": relationship_patterns
    }
    
    return schema

def print_llm_schema_summary(schema):
    """Print schema summary for LLM prompts"""
    
    print("\n" + "="*80)
    print("📋 NEO4J SCHEMA FOR LLM PROMPTS")
    print("="*80)
    
    print(f"\n🏷️  NODE TYPES ({len(schema['node_types'])})")
    print("-" * 40)
    for node_type, info in schema['node_types'].items():
        print(f"• {node_type}")
        if info['properties']:
            for prop_name, prop_type in info['properties'].items():
                print(f"  - {prop_name}: {prop_type}")
        else:
            print(f"  - no properties")
    
    print(f"\n🔗 RELATIONSHIP TYPES ({len(schema['relationship_types'])})")
    print("-" * 40)
    for rel_type, info in schema['relationship_types'].items():
        print(f"• {rel_type}")
        if info['properties']:
            for prop_name, prop_type in info['properties'].items():
                print(f"  - {prop_name}: {prop_type}")
        else:
            print(f"  - no properties")
    
    print(f"\n🔄 RELATIONSHIP PATTERNS ({len(schema['relationship_patterns'])})")
    print("-" * 40)
    for pattern in schema['relationship_patterns']:
        print(f"• ({pattern['source']})-[:{pattern['relationship']}]->({pattern['target']})")

def generate_simple_prompt_format(schema):
    """Generate a compact text format for LLM prompts (token efficient)"""
    
    prompt_text = "Neo4j Schema:\n"
    
    # Nodes - compact format
    prompt_text += "Nodes: "
    node_parts = []
    for node_type, info in schema['node_types'].items():
        if info['properties']:
            props = ", ".join([f"{k}:{v}" for k, v in info['properties'].items()])
            node_parts.append(f"{node_type}({props})")
        else:
            node_parts.append(node_type)
    prompt_text += "; ".join(node_parts) + "\n"
    
    # Relationships - compact format
    prompt_text += "Rels: "
    rel_parts = []
    for rel_type, info in schema['relationship_types'].items():
        if info['properties']:
            props = ", ".join([f"{k}:{v}" for k, v in info['properties'].items()])
            rel_parts.append(f"{rel_type}({props})")
        else:
            rel_parts.append(rel_type)
    prompt_text += "; ".join(rel_parts) + "\n"
    
    # Patterns - very compact
    prompt_text += "Patterns: "
    pattern_parts = []
    for pattern in schema['relationship_patterns']:
        pattern_parts.append(f"({pattern['source']})-[{pattern['relationship']}]->({pattern['target']})")
    prompt_text += "; ".join(pattern_parts)
    
    return prompt_text

def main():
    try:
        # Initialize Neo4j connection
        graph = Neo4jGraph(
            url=os.getenv("NEO4J_URI"),
            username=os.getenv("NEO4J_USERNAME"),
            password=os.getenv("NEO4J_PASSWORD")
        )
        
        # Generate schema
        schema = generate_llm_prompt_schema(graph)
        
        # Print summary
        print_llm_schema_summary(schema)
        
        # Generate simple prompt format
        prompt_format = generate_simple_prompt_format(schema)
        
        # Save files
        with open('llm_prompt_schema.json', 'w', encoding='utf-8') as f:
            json.dump(schema, f, indent=2, ensure_ascii=False)
        
        with open('llm_prompt_text.txt', 'w', encoding='utf-8') as f:
            f.write(prompt_format)
        
        print(f"\n💾 Files saved:")
        print(f"   • llm_prompt_schema.json - Detailed JSON schema")
        print(f"   • llm_prompt_text.txt - Simple text format for prompts")
        
        print(f"\n📝 LLM PROMPT TEXT FORMAT:")
        print("-" * 40)
        print(prompt_format)
        
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    main()
