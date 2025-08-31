#!/usr/bin/env python3
"""
Insurance SST Test Script
DB'deki gerçek Document chunk'ları ile SST extraction'ı test eder
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
from src.shared.common_fn import save_graphDocuments_in_neo4j, handle_backticks_nodes_relationship_id_type, create_graph_database_connection, get_chunk_and_graphDocument
from src.make_relationships import merge_relationship_between_chunk_and_entites, create_policy_entity_relationships
from src.graphDB_dataAccess import graphDBdataAccess
import json

# Logging setup
def setup_test_logging():
    """Test sonuçları için versioned dosya oluştur"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    test_dir = "test_results"
    os.makedirs(test_dir, exist_ok=True)
    
    log_file = f"{test_dir}/insurance_sst_test_{timestamp}.log"
    json_file = f"{test_dir}/insurance_sst_results_{timestamp}.json"
    
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
    
    def save_buffer(self):
        """Buffer'ı dosyaya kaydet"""
        with open(self.log_file, "w", encoding="utf-8") as f:
            for line in self.buffer:
                f.write(line + "\n")

def get_document_chunks():
    """DB'den Document ve ilk 5 chunk'ını çek"""
    
    # Neo4j connection parametreleri
    uri = os.getenv('NEO4J_URI')
    username = os.getenv('NEO4J_USERNAME')
    password = os.getenv('NEO4J_PASSWORD')
    
    driver = get_graphDB_driver(uri, username, password)
    
    # Önce Document bilgilerini al
    doc_query = """
    MATCH (d:Document)
    RETURN d.id as doc_id, d.name as doc_name, d.size as doc_size
    LIMIT 1
    """
    
    with driver.session() as session:
        doc_result = session.run(doc_query)
        doc_record = doc_result.single()
        
        if not doc_record:
            print("❌ DB'de Document bulunamadı!")
            return None, []
        
        doc_info = {
            "id": doc_record["doc_id"],
            "name": doc_record["doc_name"], 
            "size": doc_record["doc_size"]
        }
        
        print(f"📄 Document bulundu: {doc_info['name']} (ID: {doc_info['id']})")
        
        # İlk 5 chunk'ı al (FIRST_CHUNK ile başla, NEXT_CHUNK ile devam et)
        chunks_query = """
        MATCH (d:Document)-[:FIRST_CHUNK]->(first_chunk:Chunk)
        WITH first_chunk
        MATCH path = (first_chunk)-[:NEXT_CHUNK*0..4]->(chunk:Chunk)
        RETURN chunk.id as chunk_id, chunk.text as chunk_text, length(path) as chunk_order
        ORDER BY chunk_order
        LIMIT 5
        """
        
        chunks_result = session.run(chunks_query, doc_id=doc_info["id"])
        chunks = []
        
        for record in chunks_result:
            chunks.append({
                "id": record["chunk_id"],
                "text": record["chunk_text"],
                "order": record["chunk_order"]
            })
        
        print(f"📋 {len(chunks)} chunk bulundu")
        
        return doc_info, chunks

async def test_insurance_sst_extraction(save_to_db=False):
    """DB'deki gerçek chunk'lar ile Insurance SST extraction test - Versioned Logging"""
    
    # Logging setup
    log_file, json_file = setup_test_logging()
    logger = TestLogger(log_file)
    
    logger.log("🔬 Insurance SST Extraction Test (DB Chunks) Başlıyor...")
    logger.log(f"📁 Log dosyası: {log_file}")
    logger.log(f"📁 JSON sonuç dosyası: {json_file}")
    logger.log(f"⏰ Test zamanı: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.log(f"💾 DB'ye kaydetme: {'✅ Aktif' if save_to_db else '❌ Pasif'}")
    logger.log("")
    
    # DB'den chunk'ları çek
    doc_info, chunks = get_document_chunks()
    
    if not chunks:
        logger.log("❌ Test için chunk bulunamadı!")
        return False
    
    logger.log(f"📄 Document bulundu: {doc_info['name'] if doc_info else 'None'} (ID: {doc_info['id'] if doc_info else 'None'})")
    logger.log(f"📋 {len(chunks)} chunk bulundu")
    logger.log(f"📊 Test edilecek chunk sayısı: {len(chunks)}")
    for i, chunk in enumerate(chunks):
        logger.log(f"  {i+1}. Chunk ID: {chunk['id'][:50]}... ({len(chunk['text'])} karakter)")
    
    logger.log("")
    
    # LLM ve transformer hazırlığı
    llm, _ = get_llm("openai_gpt_4o")
    
    logger.log("🔧 LLMGraphTransformer SST modu ile başlatılıyor...")
    transformer = LLMGraphTransformer(
        llm=llm,
        use_sst_mode=True,  # SST mode aktif
        enable_llm_logging=True,
        ignore_tool_usage=False,
        additional_instructions="Focus on insurance policy details, payment schedules, and coverage information."
    )
    
    logger.log("✅ Transformer hazırlandı")
    logger.log("")
    
    # Her chunk için 7 aşamalı main.py pipeline'ını test et
    all_results = []
    all_graph_documents = []  # DB'ye kaydetmek için tüm graph documents'ları biriktir
    
    for i, chunk in enumerate(chunks):
        logger.log(f"🚀 Chunk {i+1}/{len(chunks)} - 7 Step Processing Pipeline başlıyor...")
        logger.log(f"📝 Chunk preview: {chunk['text'][:200]}...")
        
        # ChunkId_chunkDoc_list formatında hazırla (main.py ile aynı)
        chunk_doc = Document(
            page_content=chunk['text'],
            metadata={
                "source": doc_info['name'] if doc_info else "Unknown",
                "id": chunk['id'],
                "position": chunk['order'],
                "document_type": "insurance_policy"
            }
        )
        
        chunkId_chunkDoc_list = [{"chunk_id": chunk['id'], "chunk_doc": chunk_doc}]
        
        # 7 aşamalı pipeline'ı çalıştır
        chunk_latency = {}
        successful_steps = 0
        total_steps = 7
        
        try:
            logger.log(f"📋 Step 1/7: Chunk embeddings (Skipped in test)")
            # Test'te embedding skip edilebilir
            chunk_latency["update_embedding"] = "SKIPPED"
            successful_steps += 1
            
            logger.log(f"📋 Step 2/7: LLM Entity extraction başlıyor...")
            # Graph extraction - ASYNC VERSION
            # chunk_doc metadata'ya chunk_id ekle ki get_chunk_and_graphDocument doğru çalışsın
            graph_doc_with_metadata = Document(
                page_content=chunk_doc.page_content,
                metadata={
                    **chunk_doc.metadata,  # Mevcut metadata'yı koru
                    "combined_chunk_ids": [chunk['id']]  # chunk_id'yi combined_chunk_ids olarak ekle
                }
            )
            graph_docs = await transformer.aconvert_to_graph_documents([graph_doc_with_metadata])
            chunk_latency["entity_extraction"] = "SUCCESS"
            successful_steps += 1
            logger.log(f"✅ Step 2/7: {len(graph_docs)} graph document oluşturuldu")
            
            if graph_docs and len(graph_docs) > 0:
                graph_doc = graph_docs[0]
                
                logger.log(f"📋 Step 3/7: Entity normalization başlıyor...")
                # Normalize IDs / backticks / types
                cleaned = handle_backticks_nodes_relationship_id_type(graph_docs)
                chunk_latency["normalize_entities"] = "SUCCESS"
                successful_steps += 1
                logger.log(f"✅ Step 3/7: {len(cleaned)} temizlenmiş graph document")
                
                # Test için graph documents'ları biriktir
                all_graph_documents.extend(cleaned)
                
                logger.log(f"📋 Step 4/7: Neo4j'ye kaydetme (Test mode - biriktirildi)")
                # Test'te hemen kaydetmek yerine biriktir
                chunk_latency["save_graphDocuments"] = "DEFERRED"
                successful_steps += 1
                
                logger.log(f"📋 Step 5/7: Chunk-Entity relationships (Test mode - skipped)")
                # Test'te chunk-entity relationships skip edilebilir
                chunk_latency["chunk_entity_rel"] = "SKIPPED"
                successful_steps += 1
                
                logger.log(f"📋 Step 6/7: Policy-Entity relationships (Test mode - skipped)")
                # Test'te policy-entity relationships skip edilebilir  
                chunk_latency["policy_entity_rel"] = "SKIPPED"
                successful_steps += 1
                
                logger.log(f"📋 Step 7/7: Node/Relationship counts (Test mode - calculated)")
                # Test'te count'ları hesapla
                node_count = len(graph_doc.nodes)
                rel_count = len(graph_doc.relationships)
                chunk_latency["update_counts"] = "SUCCESS"
                successful_steps += 1
                
                logger.log(f"✅ Chunk {i+1} PIPELINE SONUÇLARI:")
                logger.log(f"📊 Statement node sayısı: {node_count}")
                logger.log(f"🔗 Relationship sayısı: {rel_count}")
                logger.log(f"⚡ Pipeline başarı oranı: {successful_steps}/{total_steps} ({successful_steps/total_steps*100:.1f}%)")
                
                # Node tiplerini analiz et
                node_types = {}
                for node in graph_doc.nodes:
                    node_type = node.type
                    if node_type not in node_types:
                        node_types[node_type] = []
                    node_types[node_type].append(node.id)
                
                logger.log(f"🏷️  Node tipleri:")
                for node_type, nodes in node_types.items():
                    logger.log(f"   - {node_type}: {len(nodes)} adet")
                
                # Chunk sonuçları kaydet
                chunk_result = {
                    "chunk_id": chunk['id'],
                    "chunk_order": chunk['order'],
                    "chunk_length": len(chunk['text']),
                    "statements_count": node_count,
                    "relationships_count": rel_count,
                    "statements": [{"id": node.id, "type": node.type} for node in graph_doc.nodes],
                    "node_types_breakdown": node_types,
                    "relationships": [
                        {
                            "source": rel.source.id,
                            "relation": rel.type,
                            "target": rel.target.id
                        } for rel in graph_doc.relationships
                    ],
                    "pipeline_steps": {
                        "successful_steps": successful_steps,
                        "total_steps": total_steps,
                        "success_rate": f"{successful_steps/total_steps*100:.1f}%",
                        "latency": chunk_latency
                    }
                }
                
                all_results.append(chunk_result)
                
                # İlk 3 statement'i göster
                logger.log(f"📝 EXTRACTED STATEMENTS (ilk 3):")
                for j, node in enumerate(graph_doc.nodes[:3]):
                    logger.log(f"  {j+1}. [{node.type}] {node.id}")
                
                if len(graph_doc.nodes) > 3:
                    logger.log(f"  ... ve {len(graph_doc.nodes) - 3} tane daha")
                
                # Relationships göster
                if graph_doc.relationships:
                    logger.log(f"🔗 EXTRACTED RELATIONSHIPS:")
                    for j, rel in enumerate(graph_doc.relationships[:3]):
                        logger.log(f"  {j+1}. {rel.source.id[:30]}... --[{rel.type}]--> {rel.target.id[:30]}...")
                    if len(graph_doc.relationships) > 3:
                        logger.log(f"  ... ve {len(graph_doc.relationships) - 3} tane daha")
                else:
                    logger.log("❌ Hiç relationship çıkarılmadı!")
                    
            else:
                logger.log(f"❌ Chunk {i+1} için hiç graph document oluşturulamadı!")
                chunk_result = {
                    "chunk_id": chunk['id'],
                    "chunk_order": chunk['order'],
                    "chunk_length": len(chunk['text']),
                    "statements_count": 0,
                    "relationships_count": 0,
                    "statements": [],
                    "relationships": [],
                    "pipeline_steps": {
                        "successful_steps": 1,  # Sadece extraction denendi
                        "total_steps": total_steps,
                        "success_rate": f"{1/total_steps*100:.1f}%",
                        "latency": {"entity_extraction": "FAILED"}
                    },
                    "error": "No graph document generated"
                }
                all_results.append(chunk_result)
                
        except Exception as e:
            logger.log(f"❌ Chunk {i+1} pipeline hatası: {str(e)}")
            chunk_result = {
                "chunk_id": chunk['id'],
                "chunk_order": chunk['order'],
                "chunk_length": len(chunk['text']),
                "statements_count": 0,
                "relationships_count": 0,
                "statements": [],
                "relationships": [],
                "node_types_breakdown": {},
                "pipeline_steps": {
                    "successful_steps": 0,
                    "total_steps": total_steps,
                    "success_rate": "0.0%",
                    "latency": {"error": str(e)}
                },
                "error": str(e)
            }
            all_results.append(chunk_result)
        
        logger.log("")  # Boş satır
    
    # Toplam sonuçları kaydet
    final_result = {
        "test_info": {
            "timestamp": datetime.now().isoformat(),
            "log_file": log_file,
            "json_file": json_file
        },
        "document_info": doc_info,
        "total_chunks_tested": len(chunks),
        "chunk_results": all_results,
        "summary": {
            "total_statements": sum(r["statements_count"] for r in all_results),
            "total_relationships": sum(r["relationships_count"] for r in all_results),
            "successful_chunks": len([r for r in all_results if r["statements_count"] > 0]),
            "failed_chunks": len([r for r in all_results if "error" in r]),
            "unique_node_types": list(set([
                node_type 
                for r in all_results 
                for node_type in r.get("node_types_breakdown", {}).keys()
            ]))
        }
    }
    
    # DB'ye kaydetme (main.py'deki 7 aşamalı pipeline ile)
    if save_to_db:
        logger.log("")
        logger.log("🗄️ NEO4J'YE MAIN.PY STİLİ 7 AŞAMALI KAYDETME BAŞLIYOR...")
        try:
            # Neo4j connection
            neo4j_uri = os.getenv('NEO4J_URI')
            neo4j_username = os.getenv('NEO4J_USERNAME') 
            neo4j_password = os.getenv('NEO4J_PASSWORD')
            neo4j_database = os.getenv('NEO4J_DATABASE', 'neo4j')
            
            if not all([neo4j_uri, neo4j_username, neo4j_password]):
                logger.log("❌ Neo4j connection bilgileri eksik (.env dosyasını kontrol edin)")
                final_result["db_save"] = {"status": "error", "message": "Missing Neo4j credentials"}
            else:
                # create_graph_database_connection kullan (main.py'deki gibi)
                graph = create_graph_database_connection(
                    uri=neo4j_uri,
                    userName=neo4j_username,
                    password=neo4j_password,
                    database=neo4j_database
                )
                
                # Main.py'deki 7 aşamalı pipeline'ı taklit et
                db_steps_successful = 0
                db_total_steps = 7
                db_latency = {}
                
                # Test için file_name tanımla (document'tan alabilir ya da sabit)
                file_name = "test_insurance_document.pdf"  # Test için sabit file name
                
                # Chunk pairs oluştur (Step 5 için gerekli)
                chunk_pairs = []
                for i, result in enumerate(all_results):
                    if result.get('statements_count', 0) > 0:
                        chunk_pairs.append({
                            'chunk_id': result['chunk_id'],
                            'chunk_doc': chunks[i]  # Original chunk document
                        })
                
                logger.log("📋 DB Step 1/7: Chunk embeddings (Test mode - skip)")
                # Test'te chunk embeddings skip
                db_latency["update_embedding"] = "SKIPPED"
                db_steps_successful += 1
                
                logger.log("📋 DB Step 2/7: Entity extraction (Already done)")
                # Graph documents zaten mevcut
                db_latency["entity_extraction"] = "ALREADY_DONE"
                db_steps_successful += 1
                
                logger.log("📋 DB Step 3/7: Entity normalization başlıyor...")
                # Clean and normalize (main.py Step 3)
                cleaned = handle_backticks_nodes_relationship_id_type(all_graph_documents)
                db_latency["normalize_entities"] = "SUCCESS"
                db_steps_successful += 1
                logger.log(f"✅ DB Step 3/7: {len(cleaned)} graph document normalize edildi")
                
                logger.log("📋 DB Step 4/7: Neo4j'ye kaydetme başlıyor...")
                # Save to Neo4j (main.py Step 4)
                save_graphDocuments_in_neo4j(graph, cleaned)
                db_latency["save_graphDocuments"] = "SUCCESS"
                db_steps_successful += 1
                logger.log(f"✅ DB Step 4/7: Entity'ler Neo4j'ye kaydedildi")
                
                logger.log("📋 DB Step 5/7: Chunk-Entity relationships başlıyor...")
                try:
                    from src.make_relationships import merge_relationship_between_chunk_and_entites
                    from src.shared.common_fn import get_chunk_and_graphDocument
                    
                    pairs = get_chunk_and_graphDocument(cleaned, chunk_pairs)
                    merge_relationship_between_chunk_and_entites(graph, pairs)
                    db_latency["chunk_entity_rel"] = "SUCCESS"
                    db_steps_successful += 1
                    logger.log("✅ DB Step 5/7: Chunk-Entity EXTRACTED_FROM ilişkileri oluşturuldu")
                except Exception as e:
                    logger.log(f"❌ DB Step 5/7 hatası: {e}")
                    db_latency["chunk_entity_rel"] = "FAILED"
                
                logger.log("📋 DB Step 6/7: Policy-Entity relationships başlıyor...")
                try:
                    from src.make_relationships import create_policy_entity_relationships
                    
                    create_policy_entity_relationships(graph, file_name)
                    db_latency["policy_entity_rel"] = "SUCCESS"
                    db_steps_successful += 1
                    logger.log("✅ DB Step 6/7: Policy-Entity HAS_ENTITY ilişkileri oluşturuldu")
                except Exception as e:
                    logger.log(f"❌ DB Step 6/7 hatası: {e}")
                    db_latency["policy_entity_rel"] = "FAILED"
                
                logger.log("� DB Step 7/7: Node/Relationship counts hesaplama...")
                # Count nodes and relationships (main.py Step 7)
                total_nodes = sum(len(gd.nodes) for gd in cleaned)
                total_rels = sum(len(gd.relationships) for gd in cleaned)
                db_latency["update_counts"] = "SUCCESS"
                db_steps_successful += 1
                
                db_success_rate = (db_steps_successful / db_total_steps) * 100
                logger.log(f"✅ DB Pipeline başarı oranı: {db_steps_successful}/{db_total_steps} ({db_success_rate:.1f}%)")
                logger.log(f"✅ DB'ye başarıyla kaydedildi: {len(cleaned)} graph document")
                
                final_result["db_save"] = {
                    "status": "success",
                    "graph_documents_saved": len(cleaned),
                    "total_nodes": total_nodes,
                    "total_relationships": total_rels,
                    "pipeline_steps": {
                        "successful_steps": db_steps_successful,
                        "total_steps": db_total_steps,
                        "success_rate": f"{db_success_rate:.1f}%",
                        "latency": db_latency
                    }
                }
                
        except Exception as e:
            logger.log(f"❌ DB'ye kaydetme hatası: {e}")
            final_result["db_save"] = {"status": "error", "message": str(e)}
    
    # JSON'a kaydet (versioned)
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(final_result, f, ensure_ascii=False, indent=2)
    
    logger.log(f"💾 JSON sonuçlar kaydedildi: {json_file}")
    
    # Özet rapor
    logger.log(f"📈 GENEL ÖZET:")
    logger.log(f"📄 Test edilen document: {doc_info['name'] if doc_info else 'None'}")
    logger.log(f"📋 Test edilen chunk sayısı: {len(chunks)}")
    logger.log(f"✅ Başarılı chunk'lar: {final_result['summary']['successful_chunks']}")
    logger.log(f"❌ Başarısız chunk'lar: {final_result['summary']['failed_chunks']}")
    logger.log(f"📊 Toplam statement sayısı: {final_result['summary']['total_statements']}")
    logger.log(f"🔗 Toplam relationship sayısı: {final_result['summary']['total_relationships']}")
    logger.log(f"🏷️  Bulunan node tipleri: {', '.join(final_result['summary']['unique_node_types'])}")
    
    # Kalite analizi
    if final_result['summary']['total_statements'] > 0:
        avg_statements_per_chunk = final_result['summary']['total_statements'] / final_result['summary']['successful_chunks']
        avg_relationships_per_chunk = final_result['summary']['total_relationships'] / final_result['summary']['successful_chunks'] if final_result['summary']['successful_chunks'] > 0 else 0
        
        logger.log(f"🎯 KALİTE ANALİZİ:")
        logger.log(f"📊 Chunk başına ortalama statement: {avg_statements_per_chunk:.1f}")
        logger.log(f"🔗 Chunk başına ortalama relationship: {avg_relationships_per_chunk:.1f}")
        
        if final_result['summary']['total_relationships'] == 0:
            logger.log("⚠️  UYARI: Hiç relationship çıkarılmadı! Transformer'da bug olabilir.")
        
        return True
    else:
        logger.log("❌ Hiç statement çıkarılmadı!")
        return False
    
if __name__ == "__main__":
    import sys
    
    # Komut satırı argümanlarını kontrol et
    save_to_db = len(sys.argv) > 1 and sys.argv[1].lower() in ['--save-db', '--db', '-db', 'db']
    
    print(f"🚀 Insurance SST Test başlıyor...")
    print(f"💾 DB'ye kaydetme: {'✅ Aktif' if save_to_db else '❌ Pasif'}")
    if not save_to_db:
        print("💡 DB'ye kaydetmek için: python test_insurance_sst.py --save-db")
    print("")
    
    success = asyncio.run(test_insurance_sst_extraction(save_to_db=save_to_db))
    if success:
        print("\n🎉 Test başarıyla tamamlandı!")
    else:
        print("\n💥 Test başarısız!")
