#!/usr/bin/env python3
"""
🧪 Chunk-First vs Node-First Search Comparison Test

Bu test iki farklı yaklaşımı karşılaştırır:
1. Mevcut: Node-first semantic search
2. Yeni: Chunk-first semantic search + Node enrichment

Author: Dinkal AI Agent  
Date: 2025-09-01
"""

import os
import sys
import json
import logging
import time
from typing import Dict, List, Tuple, Optional, Any
from datetime import datetime
import neo4j
from neo4j import GraphDatabase
from dotenv import load_dotenv
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity
import openai
from openai import OpenAI

# Load environment variables
load_dotenv()

# Import project modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from src.graph_query import get_graphDB_driver

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='🧪 %(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

class SearchApproachComparison:
    """Chunk-first vs Node-first search yaklaşımlarını karşılaştırır"""
    
    def __init__(self):
        self.uri = os.getenv('NEO4J_URI')
        self.username = os.getenv('NEO4J_USERNAME') 
        self.password = os.getenv('NEO4J_PASSWORD')
        self.driver = get_graphDB_driver(self.uri, self.username, self.password)
        self.openai_client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
        
    def _get_text_embedding(self, text: str) -> np.ndarray:
        """Generate text embedding using OpenAI API"""
        try:
            response = self.openai_client.embeddings.create(
                model="text-embedding-ada-002",
                input=text
            )
            return np.array(response.data[0].embedding)
        except Exception as e:
            logger.error(f"❌ Embedding generation hatası: {e}")
            return np.zeros(1536)
    
    def _calculate_cosine_similarity(self, embedding1: np.ndarray, embedding2: np.ndarray) -> float:
        """Calculate cosine similarity between two embeddings"""
        try:
            emb1 = embedding1.reshape(1, -1)
            emb2 = embedding2.reshape(1, -1)
            return cosine_similarity(emb1, emb2)[0][0]
        except Exception as e:
            logger.error(f"❌ Cosine similarity calculation hatası: {e}")
            return 0.0

    def node_first_search(self, question: str) -> Dict[str, Any]:
        """
        Mevcut yaklaşım: Önce Node-level semantic search
        """
        start_time = time.time()
        logger.info(f"🔍 NODE-FIRST: {question}")
        
        result = {
            "approach": "node_first",
            "question": question,
            "steps": [],
            "final_answer": None,
            "confidence": 0.0,
            "processing_time": 0.0,
            "nodes_found": 0,
            "chunks_found": 0
        }
        
        try:
            question_embedding = self._get_text_embedding(question)
            
            with self.driver.session() as session:
                # Step 1: Node-level semantic search (mevcut approach)
                node_query = """
                MATCH (n)
                WHERE n.embedding IS NOT NULL AND NOT n:Chunk
                RETURN n, labels(n)[0] as label, n.embedding as embedding
                LIMIT 50
                """
                
                node_result = session.run(node_query)
                node_matches = []
                
                for record in node_result:
                    node = dict(record["n"])
                    label = record["label"]
                    embedding_list = record["embedding"]
                    
                    if embedding_list and len(embedding_list) == 1536:
                        node_embedding = np.array(embedding_list)
                        similarity = self._calculate_cosine_similarity(question_embedding, node_embedding)
                        
                        if similarity > 0.7:
                            node_matches.append({
                                "node": node,
                                "label": label,
                                "similarity": float(similarity),
                                "source": "node_direct"
                            })
                
                node_matches.sort(key=lambda x: x["similarity"], reverse=True)
                result["nodes_found"] = len(node_matches)
                result["steps"].append({
                    "step": "node_semantic",
                    "matches_found": len(node_matches),
                    "top_similarity": node_matches[0]["similarity"] if node_matches else 0
                })
                
                if node_matches:
                    # Node bulundu, answer oluştur
                    top_matches = node_matches[:3]
                    answer_parts = []
                    for match in top_matches:
                        node_text = match["node"].get("id", "Unknown")[:100]
                        similarity = match["similarity"]
                        answer_parts.append(f"{node_text} (sim: {similarity:.2f})")
                    
                    result["final_answer"] = f"Node-first sonuç: {'; '.join(answer_parts)}"
                    result["confidence"] = top_matches[0]["similarity"]
                
        except Exception as e:
            logger.error(f"❌ Node-first search hatası: {e}")
            result["error"] = str(e)
        
        result["processing_time"] = time.time() - start_time
        return result

    def chunk_first_search(self, question: str) -> Dict[str, Any]:
        """
        Yeni yaklaşım: Önce Chunk-level semantic search, sonra Node enrichment
        """
        start_time = time.time()
        logger.info(f"🔍 CHUNK-FIRST: {question}")
        
        result = {
            "approach": "chunk_first",
            "question": question,
            "steps": [],
            "final_answer": None,
            "confidence": 0.0,
            "processing_time": 0.0,
            "nodes_found": 0,
            "chunks_found": 0
        }
        
        try:
            question_embedding = self._get_text_embedding(question)
            
            with self.driver.session() as session:
                # Step 1: Chunk-level semantic search (yeni approach)
                chunk_query = """
                MATCH (c:Chunk)
                WHERE c.embedding IS NOT NULL
                RETURN c, c.embedding as embedding
                LIMIT 20
                """
                
                chunk_result = session.run(chunk_query)
                chunk_matches = []
                
                for record in chunk_result:
                    chunk = dict(record["c"])
                    embedding_list = record["embedding"]
                    
                    if embedding_list and len(embedding_list) == 1536:
                        chunk_embedding = np.array(embedding_list)
                        similarity = self._calculate_cosine_similarity(question_embedding, chunk_embedding)
                        
                        if similarity > 0.7:
                            chunk_matches.append({
                                "chunk": chunk,
                                "similarity": float(similarity),
                                "chunk_id": chunk.get("id", "")
                            })
                
                chunk_matches.sort(key=lambda x: x["similarity"], reverse=True)
                result["chunks_found"] = len(chunk_matches)
                result["steps"].append({
                    "step": "chunk_semantic",
                    "matches_found": len(chunk_matches),
                    "top_similarity": chunk_matches[0]["similarity"] if chunk_matches else 0
                })
                
                # Step 2: Node enrichment (ilgili chunk'lardaki node'ları bul)
                if chunk_matches:
                    relevant_chunk_ids = [match["chunk_id"] for match in chunk_matches[:3]]  # Top 3 chunks
                    
                    # Bu chunk'lardan extract edilen node'ları bul
                    enrichment_query = """
                    MATCH (n)-[:EXTRACTED_FROM]->(c:Chunk)
                    WHERE c.id IN $chunk_ids
                    RETURN n, labels(n)[0] as label, c.id as chunk_id
                    """
                    
                    enrichment_result = session.run(enrichment_query, chunk_ids=relevant_chunk_ids)
                    enriched_nodes = []
                    
                    for record in enrichment_result:
                        node = dict(record["n"])
                        label = record["label"]
                        chunk_id = record["chunk_id"]
                        
                        enriched_nodes.append({
                            "node": node,
                            "label": label,
                            "chunk_id": chunk_id,
                            "source": "chunk_enriched"
                        })
                    
                    result["nodes_found"] = len(enriched_nodes)
                    result["steps"].append({
                        "step": "node_enrichment",
                        "nodes_found": len(enriched_nodes),
                        "from_chunks": len(relevant_chunk_ids)
                    })
                    
                    # Combined answer (chunk context + node details)
                    if chunk_matches and enriched_nodes:
                        top_chunk = chunk_matches[0]
                        chunk_text = top_chunk["chunk"].get("text", "")[:200]
                        chunk_similarity = top_chunk["similarity"]
                        
                        node_details = []
                        for node_data in enriched_nodes[:3]:
                            node_text = node_data["node"].get("id", "Unknown")[:100]
                            node_details.append(node_text)
                        
                        result["final_answer"] = f"Chunk-first sonuç: [{chunk_text}...] (sim: {chunk_similarity:.2f}) + Nodes: {'; '.join(node_details)}"
                        result["confidence"] = chunk_similarity
                
        except Exception as e:
            logger.error(f"❌ Chunk-first search hatası: {e}")
            result["error"] = str(e)
        
        result["processing_time"] = time.time() - start_time
        return result

    def compare_approaches(self, test_questions: List[str]) -> Dict[str, Any]:
        """İki yaklaşımı karşılaştır"""
        logger.info("🆚 Chunk-first vs Node-first karşılaştırması başlıyor...")
        
        comparison_results = {
            "total_questions": len(test_questions),
            "node_first_results": [],
            "chunk_first_results": [],
            "summary": {
                "node_first_avg_time": 0.0,
                "chunk_first_avg_time": 0.0,
                "node_first_avg_confidence": 0.0,
                "chunk_first_avg_confidence": 0.0,
                "node_first_success": 0,
                "chunk_first_success": 0
            }
        }
        
        for i, question in enumerate(test_questions, 1):
            logger.info(f"\n{'='*60}")
            logger.info(f"📝 Test {i}/{len(test_questions)}: {question}")
            logger.info('='*60)
            
            # Test 1: Node-first approach
            node_result = self.node_first_search(question)
            comparison_results["node_first_results"].append(node_result)
            
            # Test 2: Chunk-first approach
            chunk_result = self.chunk_first_search(question)
            comparison_results["chunk_first_results"].append(chunk_result)
            
            # Sonuçları karşılaştır
            logger.info(f"⚡ NODE-FIRST: {node_result['processing_time']:.2f}s, confidence: {node_result['confidence']:.2f}")
            logger.info(f"⚡ CHUNK-FIRST: {chunk_result['processing_time']:.2f}s, confidence: {chunk_result['confidence']:.2f}")
            
            if node_result['confidence'] > chunk_result['confidence']:
                logger.info("🏆 NODE-FIRST wins on confidence")
            elif chunk_result['confidence'] > node_result['confidence']:
                logger.info("🏆 CHUNK-FIRST wins on confidence")
            else:
                logger.info("🤝 TIE on confidence")
        
        # Summary statistics
        node_times = [r['processing_time'] for r in comparison_results["node_first_results"]]
        chunk_times = [r['processing_time'] for r in comparison_results["chunk_first_results"]]
        node_confidences = [r['confidence'] for r in comparison_results["node_first_results"]]
        chunk_confidences = [r['confidence'] for r in comparison_results["chunk_first_results"]]
        
        comparison_results["summary"]["node_first_avg_time"] = sum(node_times) / len(node_times)
        comparison_results["summary"]["chunk_first_avg_time"] = sum(chunk_times) / len(chunk_times)
        comparison_results["summary"]["node_first_avg_confidence"] = sum(node_confidences) / len(node_confidences)
        comparison_results["summary"]["chunk_first_avg_confidence"] = sum(chunk_confidences) / len(chunk_confidences)
        comparison_results["summary"]["node_first_success"] = sum(1 for c in node_confidences if c > 0.7)
        comparison_results["summary"]["chunk_first_success"] = sum(1 for c in chunk_confidences if c > 0.7)
        
        return comparison_results

    def close(self):
        if self.driver:
            self.driver.close()

def main():
    """Main test function"""
    # Test questions focused on semantic understanding
    semantic_test_questions = [
        "Yat sigortası hakkında ne biliyorsun?",
        "Kiraz isimli yat ile ilgili hangi bilgiler var?", 
        "Ödeme planları nasıl düzenlenmiş?",
        "YAPI KREDİ FİNANSALKİRALAMA firmasının hangi varlıkları sigortalı?",
        "Yat ve teminatlar arasındaki ilişki nedir?",
        "2020 yılında başlayan poliçelerin detayları neler?"
    ]
    
    comparator = SearchApproachComparison()
    
    try:
        results = comparator.compare_approaches(semantic_test_questions)
        
        # Save results
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        result_file = f"search_approach_comparison_{timestamp}.json"
        
        with open(result_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2, default=str)
        
        logger.info(f"\n🎉 Karşılaştırma tamamlandı! Sonuçlar: {result_file}")
        
        # Print summary
        summary = results["summary"]
        logger.info(f"\n📊 ÖZET SONUÇLAR:")
        logger.info(f"⏱️ Ortalama süre - Node-first: {summary['node_first_avg_time']:.2f}s, Chunk-first: {summary['chunk_first_avg_time']:.2f}s")
        logger.info(f"🎯 Ortalama güven - Node-first: {summary['node_first_avg_confidence']:.2f}, Chunk-first: {summary['chunk_first_avg_confidence']:.2f}")
        logger.info(f"✅ Başarı (>0.7) - Node-first: {summary['node_first_success']}/{len(semantic_test_questions)}, Chunk-first: {summary['chunk_first_success']}/{len(semantic_test_questions)}")
        
        if summary['chunk_first_avg_confidence'] > summary['node_first_avg_confidence']:
            logger.info("🏆 CHUNK-FIRST approach kazandı!")
        elif summary['node_first_avg_confidence'] > summary['chunk_first_avg_confidence']:
            logger.info("🏆 NODE-FIRST approach kazandı!")
        else:
            logger.info("🤝 İki approach da eşit performans gösterdi")
            
    finally:
        comparator.close()

if __name__ == "__main__":
    main()
