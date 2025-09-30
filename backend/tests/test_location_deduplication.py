#!/usr/bin/env python3
"""
Location entity deduplication test
Test different variations of location names
"""

import os
import sys
import tempfile
import asyncio
from pathlib import Path

# Add src to path
sys.path.append(str(Path(__file__).parent / "src"))

# from langextract_llm import get_graph_from_langextract  # LangExtract removed

async def test_location_deduplication():
    """Test location entity deduplication with different variations"""
    
    print("🔄 Testing location entity deduplication...")
    print("=" * 50)
    
    # Test cases with different location name variations
    test_cases = [
        {
            "name": "test_location1.txt",
            "content": "Ahmet İstanbul'da yaşıyor. İstanbul güzel bir şehirdir.",
            "description": "FIRST RUN - Creating Istanbul entity"
        },
        {
            "name": "test_location2.txt", 
            "content": "Mehmet ıstanbul'da çalışıyor. İstanbullu bir aileden geliyor.",
            "description": "SECOND RUN - Should reuse Istanbul (case difference)"
        },
        {
            "name": "test_location3.txt",
            "content": "Ayşe Istanbul şehrinde okuyor. Bu şehir çok büyük.",
            "description": "THIRD RUN - Should reuse Istanbul (English spelling)"
        }
    ]
    
    results = []
    
    for i, test_case in enumerate(test_cases, 1):
        print(f"\n{i}️⃣ {test_case['description']}")
        print("-" * 30)
        
        # Create temp file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write(test_case['content'])
            temp_path = f.name
        
        try:
            # Create chunk data format as expected by API
            chunk_data = [{
                "chunk_doc": test_case['content'],
                "chunk_id": f"chunk_{i}"
            }]
            
            # Extract graph
            documents = await get_graph_from_langextract(
                model="langextract",
                chunkId_chunkDoc_list=chunk_data,
                allowedNodes="Person,Location,Organization",
                allowedRelationship="Person,LIVES_IN,Location,Person,WORKS_IN,Location",
                chunks_to_combine=1,
                file_name=test_case['name']
            )
            
            # Collect results
            if documents:
                doc = documents[0]
                nodes = doc.nodes
                relationships = doc.relationships
                
                # Extract locations and persons from hybrid structure
                location_nodes = [n for n in nodes if hasattr(n, 'type') and 'Location' in n.type]
                person_nodes = [n for n in nodes if hasattr(n, 'type') and 'Person' in n.type]
                
                result = {
                    'run': i,
                    'total_nodes': len(nodes),
                    'total_relationships': len(relationships),
                    'locations': [n.id for n in location_nodes],
                    'persons': [n.id for n in person_nodes]
                }
                results.append(result)
                
                print(f"✅ Run {i} completed: {len(nodes)} documents")
                print(f"  📄 Nodes: {len(nodes)}, Relationships: {len(relationships)}")
                for node in nodes:
                    if hasattr(node, 'id') and hasattr(node, 'type'):
                        print(f"    📄 Node: {node.id} ({node.type})")
                        
        except Exception as e:
            print(f"❌ Error in run {i}: {e}")
            
        finally:
            # Cleanup
            os.unlink(temp_path)
    
    # Summary
    print(f"\n📊 LOCATION DEDUPLICATION SUMMARY")
    print("=" * 50)
    for result in results:
        print(f"Run {result['run']} nodes: {result['total_nodes']}")
    
    print(f"\nLocation IDs:")
    for result in results:
        print(f"  Run {result['run']}: {result['locations']}")
    
    print(f"\nPerson IDs:")
    for result in results:
        print(f"  Run {result['run']}: {result['persons']}")
        
    # Check if deduplication worked
    all_locations = []
    for result in results:
        all_locations.extend(result['locations'])
    
    unique_locations = set(all_locations)
    if len(unique_locations) == 1 and len(all_locations) > 1:
        print("✅ Location deduplication WORKING - same location ID reused!")
    elif len(unique_locations) == len(all_locations):
        print("⚠️ No deduplication detected - each run created new location")
    else:
        print(f"🔄 Partial deduplication - {len(unique_locations)} unique locations from {len(all_locations)} total")

if __name__ == "__main__":
    asyncio.run(test_location_deduplication())
