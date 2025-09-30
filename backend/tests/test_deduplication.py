#!/usr/bin/env python3
"""
Test entity deduplication by running the same content twice
to see if the second run reuses existing entities.
"""

import asyncio
import os
import sys
sys.path.append('/Users/mehmeterdogan/python-projects/llm-graph-builder/backend/src')

# from langextract_llm import get_graph_from_langextract  # LangExtract removed

# Test content - same as before
TEST_CONTENT = """Ayça Dinçkök, 34 yaşında bir pazarlama uzmanıdır. İstanbul'da yaşamaktadır ve 
ABC Sigorta şirketinde çalışmaktadır. Kendisinin ABC123 numaralı bir hayat sigortası 
poliçesi bulunmaktadır."""

async def test_deduplication():
    print("🔄 Testing entity deduplication...")
    print("=" * 50)
    
    # First run
    print("\n1️⃣ FIRST RUN - Creating new entities")
    print("-" * 30)
    
    # Create chunk data structure
    from langchain.docstore.document import Document
    chunk_data1 = [{"chunk_id": "chunk1", "chunk_doc": Document(page_content=TEST_CONTENT)}]
    
    result1 = await get_graph_from_langextract(
        model="langextract",
        chunkId_chunkDoc_list=chunk_data1,
        allowedNodes="Person,Organization,Policy",
        allowedRelationship="Person,WORKS_AT,Organization,Person,HAS_POLICY,Policy",
        chunks_to_combine=1,
        file_name="test_dedup1.txt"
    )
    
    print(f"✅ First run completed: {len(result1)} documents")
    if result1:
        first_doc = result1[0]
        print(f"  📄 Nodes: {len(first_doc.nodes)}, Relationships: {len(first_doc.relationships)}")
        for node in first_doc.nodes:
            print(f"    📄 Node: {node.id} ({node.type})")
    
    # Second run - should reuse entities
    print("\n2️⃣ SECOND RUN - Should reuse existing entities")
    print("-" * 30)
    
    chunk_data2 = [{"chunk_id": "chunk2", "chunk_doc": Document(page_content=TEST_CONTENT)}]
    
    result2 = await get_graph_from_langextract(
        model="langextract",
        chunkId_chunkDoc_list=chunk_data2,
        allowedNodes="Person,Organization,Policy",
        allowedRelationship="Person,WORKS_AT,Organization,Person,HAS_POLICY,Policy",
        chunks_to_combine=1,
        file_name="test_dedup2.txt"
    )
    
    print(f"✅ Second run completed: {len(result2)} documents")
    if result2:
        second_doc = result2[0]
        print(f"  📄 Nodes: {len(second_doc.nodes)}, Relationships: {len(second_doc.relationships)}")
        for node in second_doc.nodes:
            print(f"    📄 Node: {node.id} ({node.type})")
    
    # Third run with slightly different name to test similarity matching
    print("\n3️⃣ THIRD RUN - Similar entity name")
    print("-" * 30)
    similar_content = """Ayça Dinçkök Dinkal, 34 yaşında bir pazarlama uzmanıdır. İstanbul'da yaşamaktadır ve 
ABC Sigorta şirketinde çalışmaktadır. Kendisinin ABC123 numaralı bir hayat sigortası 
poliçesi bulunmaktadır."""
    
    chunk_data3 = [{"chunk_id": "chunk3", "chunk_doc": Document(page_content=similar_content)}]
    
    result3 = await get_graph_from_langextract(
        model="langextract",
        chunkId_chunkDoc_list=chunk_data3,
        allowedNodes="Person,Organization,Policy",
        allowedRelationship="Person,WORKS_AT,Organization,Person,HAS_POLICY,Policy",
        chunks_to_combine=1,
        file_name="test_dedup3.txt"
    )
    
    print(f"✅ Third run completed: {len(result3)} documents")
    if result3:
        third_doc = result3[0]
        print(f"  📄 Nodes: {len(third_doc.nodes)}, Relationships: {len(third_doc.relationships)}")
        for node in third_doc.nodes:
            print(f"    📄 Node: {node.id} ({node.type})")
    
    print("\n📊 DEDUPLICATION SUMMARY")
    print("=" * 50)
    if result1 and result2 and result3:
        doc1, doc2, doc3 = result1[0], result2[0], result3[0]
        print(f"Run 1 nodes: {len(doc1.nodes)}")
        print(f"Run 2 nodes: {len(doc2.nodes)}")
        print(f"Run 3 nodes: {len(doc3.nodes)}")
        
        # Check if entity IDs are reused
        run1_person_ids = [n.id for n in doc1.nodes if n.type == 'Person']
        run2_person_ids = [n.id for n in doc2.nodes if n.type == 'Person']
        run3_person_ids = [n.id for n in doc3.nodes if n.type == 'Person']
        
        print(f"\nPerson IDs:")
        print(f"  Run 1: {run1_person_ids}")
        print(f"  Run 2: {run2_person_ids}")
        print(f"  Run 3: {run3_person_ids}")
        
        if run1_person_ids and run2_person_ids:
            if run1_person_ids[0] == run2_person_ids[0]:
                print("✅ Entity deduplication WORKING - same entity ID reused!")
            else:
                print("❌ Entity deduplication NOT WORKING - different entity IDs")

if __name__ == "__main__":
    # Set environment variable
    os.environ["LLM_MODEL_CONFIG_langextract"] = "langextract,placeholder"
    
    asyncio.run(test_deduplication())
