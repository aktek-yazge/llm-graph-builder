#!/usr/bin/env python3
"""
Chunk-based Semantic Search Analysis
Bu script chunk node'larının semantic search performansını analiz eder
"""

import os
import sys
import logging
import json
from datetime import datetime
from typing import Dict, List, Any
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

# Path ayarlamaları
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
sys.path.append(project_root)

from neo4j import GraphDatabase
from openai import OpenAI
from dotenv import load_dotenv

# Load environment variables FIRST
load_dotenv()

# Import project modules
sys.path.append(current_dir)
from src.graph_query import get_graphDB_driver

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='🔍 %(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

class ChunkSemanticAnalyzer:
    def __init__(self):
        self.driver = None
        self.openai_client = None
        self._initialize_connections()
    
    def _initialize_connections(self):
        """Initialize database and OpenAI connections"""
        try:
            # Neo4j connection using project's method
            uri = os.getenv('NEO4J_URI')
            username = os.getenv('NEO4J_USERNAME') 
            password = os.getenv('NEO4J_PASSWORD')
            
            self.driver = get_graphDB_driver(uri, username, password)
            logger.info("✅ Neo4j bağlantısı başarılı")
            
            # OpenAI connection
            self.openai_client = OpenAI()
            logger.info("✅ OpenAI bağlantısı başarılı")
            
        except Exception as e:
            logger.error(f"❌ Bağlantı hatası: {e}")
            raise
    
    def get_chunk_embedding(self, text: str) -> np.ndarray:
        """Generate embedding for text"""
        try:
            response = self.openai_client.embeddings.create(
                model="text-embedding-ada-002",
                input=text
            )
            return np.array(response.data[0].embedding)
        except Exception as e:
            logger.error(f"❌ Embedding generation hatası: {e}")
            return np.zeros(1536)
    
    def analyze_chunk_semantic_search(self, question: str) -> Dict[str, Any]:
        """Analyze chunk-based semantic search performance"""
        logger.info(f"🔍 Chunk semantic search analizi başlıyor: {question}")
        
        result = {
            "question": question,
            "chunk_analysis": {},
            "node_analysis": {},
            "comparison": {}
        }
        
        # Generate question embedding
        question_embedding = self.get_chunk_embedding(question)
        
        with self.driver.session() as session:
            # 1. CHUNK NODE'LARI ANALİZİ
            logger.info("📄 Chunk node'ları analiz ediliyor...")
            chunk_query = """
            MATCH (c:Chunk)
            WHERE c.embedding IS NOT NULL
            RETURN c.id as text, c.embedding as embedding, 
                   c.document_name as document, c.chunk_index as index
            LIMIT 50
            """
            
            chunk_results = session.run(chunk_query)
            chunk_matches = []
            
            for record in chunk_results:
                text = record["text"]
                embedding_list = record["embedding"]
                document = record["document"]
                index = record["index"]
                
                if embedding_list and len(embedding_list) == 1536:
                    chunk_embedding = np.array(embedding_list)
                    similarity = cosine_similarity(
                        question_embedding.reshape(1, -1),
                        chunk_embedding.reshape(1, -1)
                    )[0][0]
                    
                    if similarity > 0.5:  # Threshold for relevant chunks
                        chunk_matches.append({
                            "text": text[:200] + "..." if len(text) > 200 else text,
                            "similarity": float(similarity),
                            "document": document,
                            "index": index,
                            "type": "chunk"
                        })
            
            # Chunk matches'ı similarity'ye göre sırala
            chunk_matches.sort(key=lambda x: x["similarity"], reverse=True)
            result["chunk_analysis"] = {
                "total_found": len(chunk_matches),
                "top_matches": chunk_matches[:5],
                "avg_similarity": np.mean([m["similarity"] for m in chunk_matches]) if chunk_matches else 0
            }
            
            # 2. NODE-BASED ANALİZİ (chunk olmayan node'lar)
            logger.info("🔗 Node-based analizi yapılıyor...")
            node_query = """
            MATCH (n)
            WHERE n.embedding IS NOT NULL AND NOT n:Chunk
            RETURN n.id as text, n.embedding as embedding, 
                   labels(n)[0] as label
            LIMIT 50
            """
            
            node_results = session.run(node_query)
            node_matches = []
            
            for record in node_results:
                text = record["text"]
                embedding_list = record["embedding"]
                label = record["label"]
                
                if embedding_list and len(embedding_list) == 1536:
                    node_embedding = np.array(embedding_list)
                    similarity = cosine_similarity(
                        question_embedding.reshape(1, -1),
                        node_embedding.reshape(1, -1)
                    )[0][0]
                    
                    if similarity > 0.5:
                        node_matches.append({
                            "text": text[:200] + "..." if len(text) > 200 else text,
                            "similarity": float(similarity),
                            "label": label,
                            "type": "node"
                        })
            
            # Node matches'ı similarity'ye göre sırala
            node_matches.sort(key=lambda x: x["similarity"], reverse=True)
            result["node_analysis"] = {
                "total_found": len(node_matches),
                "top_matches": node_matches[:5],
                "avg_similarity": np.mean([m["similarity"] for m in node_matches]) if node_matches else 0
            }
            
            # 3. KARŞILAŞTIRMA
            result["comparison"] = {
                "chunk_vs_node": {
                    "chunk_avg_similarity": result["chunk_analysis"]["avg_similarity"],
                    "node_avg_similarity": result["node_analysis"]["avg_similarity"],
                    "chunk_count": result["chunk_analysis"]["total_found"],
                    "node_count": result["node_analysis"]["total_found"]
                },
                "best_approach": "chunk" if result["chunk_analysis"]["avg_similarity"] > result["node_analysis"]["avg_similarity"] else "node",
                "similarity_difference": abs(result["chunk_analysis"]["avg_similarity"] - result["node_analysis"]["avg_similarity"])
            }
        
        return result
    
    def run_test_questions(self):
        """Run test questions for chunk semantic analysis"""
        test_questions = [
            "Yat sigortası hakkında ne biliyorsun?",
            "Kiraz isimli yat ile ilgili hangi bilgiler var?",
            "Ödeme planları nasıl düzenlenmiş?",
            "YAPI KREDİ FİNANSALKİRALAMA firmasının hangi varlıkları sigortalı?",
            "2020 yılında başlayan poliçelerin ödeme detayları neler?"
        ]
        
        all_results = []
        
        for i, question in enumerate(test_questions, 1):
            logger.info(f"\n{'='*60}")
            logger.info(f"📋 Test {i}: {question}")
            logger.info(f"{'='*60}")
            
            result = self.analyze_chunk_semantic_search(question)
            all_results.append(result)
            
            # Sonuçları yazdır
            logger.info(f"📄 CHUNK ANALİZİ:")
            logger.info(f"   Toplam match: {result['chunk_analysis']['total_found']}")
            logger.info(f"   Ortalama similarity: {result['chunk_analysis']['avg_similarity']:.3f}")
            
            if result['chunk_analysis']['top_matches']:
                logger.info(f"   Top 3 chunk matches:")
                for j, match in enumerate(result['chunk_analysis']['top_matches'][:3], 1):
                    logger.info(f"     {j}. {match['text'][:100]}... (sim: {match['similarity']:.3f})")
            
            logger.info(f"🔗 NODE ANALİZİ:")
            logger.info(f"   Toplam match: {result['node_analysis']['total_found']}")
            logger.info(f"   Ortalama similarity: {result['node_analysis']['avg_similarity']:.3f}")
            
            if result['node_analysis']['top_matches']:
                logger.info(f"   Top 3 node matches:")
                for j, match in enumerate(result['node_analysis']['top_matches'][:3], 1):
                    logger.info(f"     {j}. {match['text'][:100]}... (sim: {match['similarity']:.3f})")
            
            logger.info(f"⚖️  KARŞILAŞTIRMA:")
            logger.info(f"   En iyi yaklaşım: {result['comparison']['best_approach'].upper()}")
            logger.info(f"   Similarity farkı: {result['comparison']['similarity_difference']:.3f}")
        
        # Genel sonuçları analiz et
        self._analyze_overall_results(all_results)
        
        return all_results
    
    def _analyze_overall_results(self, results: List[Dict]):
        """Analyze overall test results"""
        logger.info(f"\n{'='*60}")
        logger.info(f"📊 GENEL SONUÇ ANALİZİ")
        logger.info(f"{'='*60}")
        
        chunk_wins = 0
        node_wins = 0
        total_chunk_similarity = 0
        total_node_similarity = 0
        
        for result in results:
            if result['comparison']['best_approach'] == 'chunk':
                chunk_wins += 1
            else:
                node_wins += 1
            
            total_chunk_similarity += result['chunk_analysis']['avg_similarity']
            total_node_similarity += result['node_analysis']['avg_similarity']
        
        avg_chunk_similarity = total_chunk_similarity / len(results)
        avg_node_similarity = total_node_similarity / len(results)
        
        logger.info(f"🏆 CHUNK WINS: {chunk_wins}/{len(results)} test")
        logger.info(f"🏆 NODE WINS: {node_wins}/{len(results)} test")
        logger.info(f"📈 CHUNK ortalama similarity: {avg_chunk_similarity:.3f}")
        logger.info(f"📈 NODE ortalama similarity: {avg_node_similarity:.3f}")
        
        if avg_chunk_similarity > avg_node_similarity:
            logger.info(f"🎯 SONUÇ: CHUNK-BASED semantic search daha başarılı!")
            logger.info(f"   Fark: {avg_chunk_similarity - avg_node_similarity:.3f}")
        else:
            logger.info(f"🎯 SONUÇ: NODE-BASED semantic search daha başarılı!")
            logger.info(f"   Fark: {avg_node_similarity - avg_chunk_similarity:.3f}")
    
    def close(self):
        """Close database connection"""
        if self.driver:
            self.driver.close()
            logger.info("🔌 Neo4j bağlantısı kapatıldı")

def main():
    """Main execution function"""
    logger.info("🔍 Chunk Semantic Analysis başlıyor...")
    
    analyzer = ChunkSemanticAnalyzer()
    
    try:
        results = analyzer.run_test_questions()
        
        # Save results to file
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"chunk_semantic_analysis_{timestamp}.json"
        
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        
        logger.info(f"📁 Sonuçlar kaydedildi: {filename}")
        
    except Exception as e:
        logger.error(f"❌ Hata: {e}")
    finally:
        analyzer.close()

if __name__ == "__main__":
    main()
