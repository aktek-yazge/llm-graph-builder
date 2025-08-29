#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
LLM Extraction Log Viewer
JSONL dosyasını formatlanmış şekilde görüntülemek için araçlar
"""

import json
import sys
from datetime import datetime
from pathlib import Path

def view_extractions(log_file_path, limit=None, pretty=True):
    """
    LLM extraction log'larını formatlanmış şekilde görüntüle
    """
    if not Path(log_file_path).exists():
        print(f"❌ Log dosyası bulunamadı: {log_file_path}")
        return
    
    print("🔍 LLM EXTRACTION LOG VIEWER")
    print("=" * 60)
    
    count = 0
    with open(log_file_path, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            if limit and count >= limit:
                break
                
            try:
                data = json.loads(line.strip())
                count += 1
                
                print(f"\n📋 EXTRACTION #{count} (Line {line_num})")
                print("-" * 40)
                print(f"🕒 Timestamp: {data.get('timestamp', 'N/A')}")
                print(f"� Document: {data.get('document_name', 'N/A')}")
                print(f"�🔗 Chunk ID: {data.get('chunk_id', 'N/A')}")
                print(f"📊 Node Count: {data.get('node_count', 'N/A')}")
                print(f"🔗 Relationship Count: {data.get('relationship_count', 'N/A')}")
                print(f"📝 Content Length: {data.get('content_length', 'N/A')} chars")
                
                if 'relationships' in data and data['relationships']:
                    print(f"\n🔗 RELATIONSHIPS ({len(data['relationships'])}):")
                    for i, rel in enumerate(data['relationships'], 1):
                        print(f"  {i}. {rel.get('head', 'N/A')} ({rel.get('head_type', 'N/A')})")
                        print(f"     --[{rel.get('relation', 'N/A')}]-->")
                        print(f"     {rel.get('tail', 'N/A')} ({rel.get('tail_type', 'N/A')})")
                
                if 'unique_nodes' in data and data['unique_nodes']:
                    print(f"\n🏷️ UNIQUE NODES ({len(data['unique_nodes'])}):")
                    for i, node in enumerate(data['unique_nodes'], 1):
                        node_id = node[0] if isinstance(node, (list, tuple)) else str(node)
                        node_type = node[1] if isinstance(node, (list, tuple)) and len(node) > 1 else 'Unknown'
                        print(f"  {i}. {node_id} ({node_type})")
                
                if pretty and 'parsed_data' in data:
                    print(f"\n🔍 RAW PARSED DATA:")
                    print(json.dumps(data['parsed_data'], indent=2, ensure_ascii=False))
                    
            except json.JSONDecodeError as e:
                print(f"❌ Line {line_num} JSON parse error: {e}")
            except Exception as e:
                print(f"❌ Line {line_num} processing error: {e}")
    
    print(f"\n✅ Total extractions viewed: {count}")

def summary_stats(log_file_path):
    """
    Log dosyasından özet istatistikler çıkar
    """
    if not Path(log_file_path).exists():
        print(f"❌ Log dosyası bulunamadı: {log_file_path}")
        return
    
    total_extractions = 0
    total_nodes = 0
    total_relationships = 0
    node_types = {}
    relationship_types = {}
    document_stats = {}  # Document bazlı istatistikler
    
    with open(log_file_path, 'r', encoding='utf-8') as f:
        for line in f:
            try:
                data = json.loads(line.strip())
                total_extractions += 1
                
                # Document istatistikleri
                doc_name = data.get('document_name', 'unknown_document')
                if doc_name not in document_stats:
                    document_stats[doc_name] = {'extractions': 0, 'nodes': 0, 'relationships': 0}
                
                document_stats[doc_name]['extractions'] += 1
                document_stats[doc_name]['nodes'] += data.get('node_count', 0)
                document_stats[doc_name]['relationships'] += data.get('relationship_count', 0)
                
                # Node ve relationship sayıları
                total_nodes += data.get('node_count', 0)
                total_relationships += data.get('relationship_count', 0)
                
                # Node type istatistikleri
                if 'unique_nodes' in data:
                    for node in data['unique_nodes']:
                        node_type = node[1] if isinstance(node, (list, tuple)) and len(node) > 1 else 'Unknown'
                        node_types[node_type] = node_types.get(node_type, 0) + 1
                
                # Relationship type istatistikleri
                if 'relationships' in data:
                    for rel in data['relationships']:
                        rel_type = rel.get('relation', 'Unknown')
                        relationship_types[rel_type] = relationship_types.get(rel_type, 0) + 1
                        
            except json.JSONDecodeError:
                continue
    
    print("📊 LLM EXTRACTION SUMMARY STATISTICS")
    print("=" * 50)
    print(f"🔢 Total Extractions: {total_extractions}")
    print(f"🏷️ Total Nodes: {total_nodes}")
    print(f"🔗 Total Relationships: {total_relationships}")
    print(f"📄 Documents Processed: {len(document_stats)}")
    
    if total_extractions > 0:
        print(f"📈 Avg Nodes per Extraction: {total_nodes/total_extractions:.1f}")
        print(f"📈 Avg Relationships per Extraction: {total_relationships/total_extractions:.1f}")
    
    print(f"\n📄 DOCUMENT STATISTICS:")
    for doc_name, stats in sorted(document_stats.items(), key=lambda x: x[1]['extractions'], reverse=True):
        print(f"  📄 {doc_name}:")
        print(f"    Extractions: {stats['extractions']}")
        print(f"    Nodes: {stats['nodes']}")
        print(f"    Relationships: {stats['relationships']}")
    
    print(f"\n🏷️ NODE TYPE DISTRIBUTION:")
    for node_type, count in sorted(node_types.items(), key=lambda x: x[1], reverse=True):
        print(f"  {node_type}: {count}")
    
    print(f"\n🔗 RELATIONSHIP TYPE DISTRIBUTION:")
    for rel_type, count in sorted(relationship_types.items(), key=lambda x: x[1], reverse=True):
        print(f"  {rel_type}: {count}")

def export_to_json(log_file_path, output_file_path):
    """
    JSONL dosyasını tek JSON array'e çevir
    """
    if not Path(log_file_path).exists():
        print(f"❌ Log dosyası bulunamadı: {log_file_path}")
        return
    
    extractions = []
    with open(log_file_path, 'r', encoding='utf-8') as f:
        for line in f:
            try:
                data = json.loads(line.strip())
                extractions.append(data)
            except json.JSONDecodeError:
                continue
    
    with open(output_file_path, 'w', encoding='utf-8') as f:
        json.dump(extractions, f, indent=2, ensure_ascii=False)
    
    print(f"✅ {len(extractions)} extractions exported to: {output_file_path}")

if __name__ == "__main__":
    log_file = "/Users/mehmeterdogan/python-projects/llm-graph-builder/backend/logs/llm_extractions.jsonl"
    
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python log_viewer.py view [limit]     - View extractions")
        print("  python log_viewer.py summary          - Show summary stats")
        print("  python log_viewer.py export [output]  - Export to JSON")
        sys.exit(1)
    
    command = sys.argv[1]
    
    if command == "view":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else None
        view_extractions(log_file, limit=limit)
    elif command == "summary":
        summary_stats(log_file)
    elif command == "export":
        output_file = sys.argv[2] if len(sys.argv) > 2 else "extractions.json"
        export_to_json(log_file, output_file)
    else:
        print(f"❌ Unknown command: {command}")
