#!/bin/bash

# LangExtract test için environment variable
export LLM_MODEL_CONFIG_langextract="langextract,placeholder"

echo "✅ LangExtract environment variable set!"
echo "Model: LLM_MODEL_CONFIG_langextract=$LLM_MODEL_CONFIG_langextract"

# Test the integration
cd /Users/mehmeterdogan/python-projects/llm-graph-builder/backend
python -c "
import asyncio
from src.llm import get_graph_from_llm

# Test data
test_text = '''
Ayça Dinçkök, 34 yaşında bir pazarlama uzmanıdır. İstanbul'da yaşamaktadır ve 
ABC Sigorta şirketinde çalışmaktadır. Kendisinin ABC123 numaralı bir hayat sigortası 
poliçesi bulunmaktadır.
'''

# Mock chunk data
class MockChunkDoc:
    def __init__(self, page_content):
        self.page_content = page_content

chunks = [{
    'chunk_doc': MockChunkDoc(page_content=test_text),
    'chunk_id': 'test_chunk_1'
}]

async def test_langextract():
    try:
        print('🧪 Testing LangExtract integration...')
        
        result = await get_graph_from_llm(
            model='langextract',
            chunkId_chunkDoc_list=chunks,
            allowedNodes='Person,Organization,Policy',
            allowedRelationship='Person,WORKS_AT,Organization,Person,HAS_POLICY,Policy',
            chunks_to_combine=1,
            file_name='test.txt'
        )
        
        print(f'✅ LangExtract test successful!')
        print(f'📊 Results: {len(result)} graph documents')
        
        if result and len(result) > 0:
            doc = result[0]
            print(f'  - Nodes: {len(doc.nodes)}')
            print(f'  - Relationships: {len(doc.relationships)}')
            
            # Show first few entities
            for i, node in enumerate(doc.nodes[:3]):
                print(f'    Node {i+1}: {node.id} ({node.type})')
            
            # Show first few relationships  
            for i, rel in enumerate(doc.relationships[:3]):
                print(f'    Rel {i+1}: {rel.source.id} -{rel.type}-> {rel.target.id}')
        
    except Exception as e:
        print(f'❌ Test failed: {e}')

# Run test
asyncio.run(test_langextract())
"
