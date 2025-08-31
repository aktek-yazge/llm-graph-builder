#!/usr/bin/env python3
"""
Enhanced Insurance SST Test - Node Types Kontrolü
"""

import os
import sys
import asyncio
from datetime import datetime
from dotenv import load_dotenv

# .env dosyasını yükle
load_dotenv()

from src.graph_transformer.transformer import LLMGraphTransformer
from src.llm import get_llm
from langchain.schema import Document
from src.graph_query import get_graphDB_driver
import json

def setup_test_logging():
    """Test sonuçları için versioned dosya oluştur"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    test_dir = "test_results"
    os.makedirs(test_dir, exist_ok=True)
    
    log_file = f"{test_dir}/enhanced_sst_test_{timestamp}.log"
    json_file = f"{test_dir}/enhanced_sst_results_{timestamp}.json"
    
    return log_file, json_file

class TestLogger:
    def __init__(self, log_file):
        self.log_file = log_file
        self.buffer = []
    
    def log(self, message):
        """Hem console'a hem dosyaya yaz"""
        print(message)
        self.buffer.append(message)
        
        # Dosyaya anlık yaz
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(message + "\n")

def get_sample_chunks():
    """Sample insurance text chunks"""
    return [
        {
            'id': 'sample1',
            'text': """
            Policy Number: N-206968954-0-0
            Policy Type: Yacht Insurance 
            Policyholder: YAPI KREDİ FİNANSAL KİRALAMA A.Ş.
            Tax Number: 9370017457
            Address: BARBAROS BULVARI MORBASAN İŞ HANI MERKEZİ C BLK 9 6 BALMUMCU 34000 BEŞİKTAŞ İSTANBUL
            
            Policy Period: 01/07/2020 - 01/07/2021
            Total Premium: 1900 EUR
            """,
            'order': 1
        },
        {
            'id': 'sample2', 
            'text': """
            Insured Vessel: KIRAZ
            Flag: TC (Turkey)
            Built Year: 2019
            Usage: Private
            
            Coverage Details:
            - Yacht Hull Coverage: 760,000 EUR
            - War/Strike Coverage: 760,000 EUR  
            - Personal Belongings: 20,000 EUR
            - Personal Accident: 25,000 EUR
            """,
            'order': 2
        }
    ]

async def test_enhanced_sst():
    """Enhanced SST test - Node tiplerini kontrol et"""
    
    # Logging setup
    log_file, json_file = setup_test_logging()
    logger = TestLogger(log_file)
    
    logger.log("🧪 Enhanced SST Test - Node Types Analysis")
    logger.log(f"📁 Log dosyası: {log_file}")
    logger.log(f"📁 JSON sonuç dosyası: {json_file}")
    logger.log(f"⏰ Test zamanı: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.log("")
    
    # Sample chunks
    chunks = get_sample_chunks()
    logger.log(f"📊 Test edilecek chunk sayısı: {len(chunks)}")
    
    # LLM ve transformer hazırlığı
    llm, _ = get_llm("openai_gpt_4o")
    
    logger.log("🔧 Enhanced SST Transformer başlatılıyor...")
    transformer = LLMGraphTransformer(
        llm=llm,
        use_sst_mode=True,
        strict_mode=False,
        enable_llm_logging=True
    )
    
    logger.log("✅ Transformer hazır")
    logger.log("")
    
    all_results = []
    
    for i, chunk in enumerate(chunks):
        logger.log(f"🔄 Chunk {i+1} işleniyor...")
        logger.log(f"📝 Text: {chunk['text'][:200]}...")
        
        doc = Document(
            page_content=chunk['text'],
            metadata={"chunk_id": chunk['id'], "order": chunk['order']}
        )
        
        try:
            # Enhanced SST extraction
            graph_docs = await transformer.aconvert_to_graph_documents([doc])
            
            if graph_docs and len(graph_docs) > 0:
                graph_doc = graph_docs[0]
                
                # Node tiplerini analiz et
                node_types = {}
                for node in graph_doc.nodes:
                    node_type = node.type
                    if node_type not in node_types:
                        node_types[node_type] = []
                    node_types[node_type].append(node.id)
                
                # Relationship'leri analiz et
                relationships = []
                for rel in graph_doc.relationships:
                    relationships.append({
                        "source": rel.source.id[:50],
                        "type": rel.type,
                        "target": rel.target.id[:50]
                    })
                
                chunk_result = {
                    "chunk_id": chunk['id'],
                    "total_nodes": len(graph_doc.nodes),
                    "total_relationships": len(graph_doc.relationships),
                    "node_types_breakdown": node_types,
                    "relationships": relationships
                }
                all_results.append(chunk_result)
                
                logger.log(f"✅ Chunk {i+1} SONUÇLARI:")
                logger.log(f"📊 Toplam node sayısı: {len(graph_doc.nodes)}")
                logger.log(f"🔗 Toplam relationship sayısı: {len(graph_doc.relationships)}")
                logger.log(f"🏷️  Node tipleri ve sayıları:")
                
                for node_type, nodes in node_types.items():
                    logger.log(f"   - {node_type}: {len(nodes)} adet")
                    for j, node_id in enumerate(nodes[:2]):  # İlk 2 örnek
                        logger.log(f"     • {node_id[:80]}")
                    if len(nodes) > 2:
                        logger.log(f"     ... ve {len(nodes) - 2} tane daha")
                
                if relationships:
                    logger.log(f"🔗 İlk 3 relationship:")
                    for j, rel in enumerate(relationships[:3]):
                        logger.log(f"   {j+1}. {rel['source']}... --[{rel['type']}]--> {rel['target']}...")
                
                logger.log("")
                
        except Exception as e:
            logger.log(f"❌ Chunk {i+1} hatası: {str(e)}")
            all_results.append({
                "chunk_id": chunk['id'],
                "error": str(e)
            })
    
    # Sonuçları kaydet
    final_result = {
        "test_info": {
            "timestamp": datetime.now().isoformat(),
            "log_file": log_file,
            "json_file": json_file
        },
        "chunks_tested": len(chunks),
        "results": all_results,
        "summary": {
            "total_nodes": sum(r.get("total_nodes", 0) for r in all_results),
            "total_relationships": sum(r.get("total_relationships", 0) for r in all_results),
            "unique_node_types": list(set([
                node_type 
                for r in all_results 
                for node_type in r.get("node_types_breakdown", {}).keys()
            ]))
        }
    }
    
    # JSON'a kaydet
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(final_result, f, ensure_ascii=False, indent=2)
    
    logger.log("💾 JSON sonuçlar kaydedildi")
    logger.log("")
    logger.log("📈 FINAL ÖZET:")
    logger.log(f"📊 Toplam node sayısı: {final_result['summary']['total_nodes']}")
    logger.log(f"🔗 Toplam relationship sayısı: {final_result['summary']['total_relationships']}")
    logger.log(f"🏷️  Bulunan node tipleri: {', '.join(final_result['summary']['unique_node_types'])}")
    
    return final_result

if __name__ == "__main__":
    result = asyncio.run(test_enhanced_sst())
    if result['summary']['total_nodes'] > 0:
        print("\n🎉 Enhanced SST test başarılı!")
    else:
        print("\n💥 Test başarısız!")
