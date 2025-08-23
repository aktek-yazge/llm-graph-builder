"""
LangExtract Graph Integration Test Script

Bu script, mevcut LLM-based graph extraction ile LangExtract-based extraction'ı karşılaştırır.
"""

import asyncio
import json
import logging
import os
import sys
from typing import List, Dict, Any
import time
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Logging ayarları
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Backend path'ini ekle
sys.path.append('/Users/mehmeterdogan/python-projects/llm-graph-builder/backend')

try:
    # LangExtract integration'ı import et
    from langextract_graph_integration import (
        LangExtractGraphExtractor, 
        get_graph_from_langextract,
        convert_to_neo4j_format
    )
    
    # Mevcut sistem modüllerini import et
    from src.llm import get_graph_from_llm
    from src.shared.common_fn import create_graph_database_connection
    
    IMPORTS_SUCCESS = True
except ImportError as e:
    logging.warning(f"Import error: {e}")
    IMPORTS_SUCCESS = False


class GraphExtractionComparison:
    """Mevcut LLM vs LangExtract karşılaştırması"""
    
    def __init__(self):
        self.test_text = """
        Ayça Dinçkök, 34 yaşında bir pazarlama uzmanıdır. İstanbul'da yaşamaktadır ve 
        ABC Sigorta şirketinde çalışmaktadır. Kendisinin ABC123 numaralı bir hayat sigortası 
        poliçesi bulunmaktadır. Bu poliçe 1.000.000 TL teminat tutarına sahiptir.
        
        ABC Sigorta, 1995 yılında kurulmuş bir sigorta şirketidir ve merkezi Ankara'dadır.
        Şirket, hayat sigortası, kasko sigortası ve konut sigortası ürünleri sunmaktadır.
        
        Ayça'nın ayrıca DEF456 numaralı bir konut sigortası poliçesi de vardır.
        Bu poliçe Galata Residance D4 konutu için yapılmıştır ve 500.000 TL teminat tutarındadır.
        """
        
        # Test parametreleri
        self.allowed_nodes = "Person,Organization,Policy,Location,Property"
        self.allowed_relationships = "Person,WORKS_AT,Organization,Person,HAS_POLICY,Policy,Person,LIVES_IN,Location,Policy,COVERS,Property,Organization,OFFERS,Policy"
        
    async def test_langextract_extraction(self) -> Dict[str, Any]:
        """LangExtract ile extraction test et"""
        try:
            logging.info("=== LangExtract Extraction Test ===")
            start_time = time.time()
            
            # LangExtract extractor oluştur
            extractor = LangExtractGraphExtractor(model_id="gpt-4o-mini")
            
            # Allowed nodes'ları parse et
            allowed_nodes_list = [node.strip() for node in self.allowed_nodes.split(',')]
            
            # Allowed relationships'i parse et
            allowed_rels_list = []
            items = [item.strip() for item in self.allowed_relationships.split(',')]
            for i in range(0, len(items), 3):
                if i + 2 < len(items):
                    allowed_rels_list.append((items[i], items[i+1], items[i+2]))
            
            # Extraction yap
            result = await extractor.extract_graph(
                text=self.test_text,
                allowed_nodes=allowed_nodes_list,
                allowed_relationships=allowed_rels_list
            )
            
            extraction_time = time.time() - start_time
            
            # Neo4j format'ına dönüştür
            neo4j_docs = convert_to_neo4j_format(result)
            
            return {
                "method": "langextract",
                "success": True,
                "entities": len(result.entities),
                "relationships": len(result.relationships),
                "extraction_time": extraction_time,
                "graph_documents": neo4j_docs,
                "raw_result": result
            }
            
        except Exception as e:
            logging.error(f"LangExtract extraction failed: {e}")
            return {
                "method": "langextract",
                "success": False,
                "error": str(e),
                "entities": 0,
                "relationships": 0,
                "extraction_time": 0
            }
    
    async def test_existing_llm_extraction(self) -> Dict[str, Any]:
        """Mevcut LLM sistemi ile extraction test et"""
        try:
            logging.info("=== Existing LLM Extraction Test ===")
            start_time = time.time()
            
            # Mock chunk oluştur (LLM extraction için ChunkDoc objesiyle)
            from dataclasses import dataclass
            @dataclass
            class MockChunkDoc:
                page_content: str
            
            chunks = [{
                "chunk_doc": MockChunkDoc(page_content=self.test_text),
                "chunk_id": "test_chunk_1"
            }]
            
            # Mevcut sistem ile extraction
            graph_documents = await get_graph_from_llm(
                model="openai_gpt_4.1_mini",
                chunkId_chunkDoc_list=chunks,
                allowedNodes=self.allowed_nodes,
                allowedRelationship=self.allowed_relationships,
                chunks_to_combine=1,
                file_name="test_document.txt",
                additional_instructions="Turkish text extraction",
                graph=None
            )
            
            extraction_time = time.time() - start_time
            
            # Entity ve relationship sayılarını hesapla
            total_entities = 0
            total_relationships = 0
            
            for doc in graph_documents:
                if 'nodes' in doc:
                    total_entities += len(doc['nodes'])
                if 'relationships' in doc:
                    total_relationships += len(doc['relationships'])
            
            return {
                "method": "existing_llm",
                "success": True,
                "entities": total_entities,
                "relationships": total_relationships,
                "extraction_time": extraction_time,
                "graph_documents": graph_documents
            }
            
        except Exception as e:
            logging.error(f"Existing LLM extraction failed: {e}")
            return {
                "method": "existing_llm", 
                "success": False,
                "error": str(e),
                "entities": 0,
                "relationships": 0,
                "extraction_time": 0
            }
    
    async def run_comparison(self):
        """Her iki metodu da test et ve karşılaştır"""
        logging.info("Starting graph extraction comparison...")
        
        results = {}
        
        # 1. LangExtract test
        if IMPORTS_SUCCESS:
            langextract_result = await self.test_langextract_extraction()
            results["langextract"] = langextract_result
        else:
            results["langextract"] = {"method": "langextract", "success": False, "error": "Import failed"}
        
        # 2. Existing LLM test  
        try:
            existing_result = await self.test_existing_llm_extraction()
            results["existing_llm"] = existing_result
        except Exception as e:
            results["existing_llm"] = {"method": "existing_llm", "success": False, "error": str(e)}
        
        # 3. Sonuçları karşılaştır
        self.print_comparison_results(results)
        
        return results
    
    def print_comparison_results(self, results: Dict[str, Any]):
        """Karşılaştırma sonuçlarını yazdır"""
        print("\n" + "="*60)
        print("GRAPH EXTRACTION COMPARISON RESULTS")
        print("="*60)
        
        for method, result in results.items():
            print(f"\n{method.upper()} METHOD:")
            print(f"  Success: {result.get('success', False)}")
            
            if result.get('success'):
                print(f"  Entities: {result.get('entities', 0)}")
                print(f"  Relationships: {result.get('relationships', 0)}")
                print(f"  Extraction time: {result.get('extraction_time', 0):.2f}s")
                
                # Sample entities göster
                if 'graph_documents' in result and result['graph_documents']:
                    doc = result['graph_documents'][0]
                    if 'nodes' in doc and doc['nodes']:
                        print(f"  Sample entities:")
                        for node in doc['nodes'][:3]:
                            print(f"    - {node.get('type', 'Unknown')}: {node.get('id', 'No ID')}")
                    
                    if 'relationships' in doc and doc['relationships']:
                        print(f"  Sample relationships:")
                        for rel in doc['relationships'][:3]:
                            print(f"    - {rel.get('source', '?')} -[{rel.get('type', '?')}]-> {rel.get('target', '?')}")
            else:
                print(f"  Error: {result.get('error', 'Unknown error')}")
        
        print("\n" + "="*60)
        
        # Recommendation
        if results.get("langextract", {}).get("success") and results.get("existing_llm", {}).get("success"):
            le_time = results["langextract"].get("extraction_time", float('inf'))
            llm_time = results["existing_llm"].get("extraction_time", float('inf'))
            
            print("RECOMMENDATION:")
            if le_time < llm_time:
                print("✅ LangExtract is faster for this extraction task")
            else:
                print("✅ Existing LLM method is faster for this extraction task")
                
            print("💡 Consider using LangExtract for:")
            print("   - Structured, schema-based extractions")
            print("   - Consistent entity/relationship formats")
            print("   - Better error handling and validation")
            
            print("💡 Consider using existing LLM method for:")
            print("   - Complex, context-dependent extractions")
            print("   - Custom prompting strategies")
            print("   - Domain-specific adaptations")


async def main():
    """Ana test fonksiyonu"""
    print("LangExtract Graph Integration Test")
    print("==================================")
    
    # API key kontrolü
    print("\n🔍 Environment Variables Check:")
    if os.getenv("OPENAI_API_KEY"):
        print("   ✅ OPENAI_API_KEY: Found")
    else:
        print("   ❌ OPENAI_API_KEY: Not found")
        
    if os.getenv("GOOGLE_API_KEY"):
        print("   ✅ GOOGLE_API_KEY: Found")  
    else:
        print("   ❌ GOOGLE_API_KEY: Not found")
    
    if not os.getenv("OPENAI_API_KEY") and not os.getenv("GOOGLE_API_KEY"):
        print("\n⚠️  No API keys found. Set OPENAI_API_KEY or GOOGLE_API_KEY to run tests.")
        print("For testing purposes, you can:")
        print("export OPENAI_API_KEY='your-openai-key'")
        print("or")
        print("export GOOGLE_API_KEY='your-google-key'")
        return
    
    # Test başlat
    comparison = GraphExtractionComparison()
    results = await comparison.run_comparison()
    
    # JSON olarak da kaydet
    with open('/Users/mehmeterdogan/python-projects/llm-graph-builder/backend/extraction_comparison_results.json', 'w', encoding='utf-8') as f:
        # Raw result'ları JSON'a serialize etmek için convert et
        serializable_results = {}
        for method, result in results.items():
            serializable_result = {k: v for k, v in result.items() if k != 'raw_result'}
            serializable_results[method] = serializable_result
        
        json.dump(serializable_results, f, indent=2, ensure_ascii=False)
    
    print(f"\n📄 Detailed results saved to: extraction_comparison_results.json")


if __name__ == "__main__":
    # Standalone test
    asyncio.run(main())
