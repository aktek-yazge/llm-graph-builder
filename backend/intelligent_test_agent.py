#!/usr/bin/env python3
"""
🧠 Intelligent Test Agent for Neo4j Graph Database

Bu agent graph veritabanını sistematik olarak keşfeder ve sorulara cevap bulmak için 
node relationships ve vector search kullanarak 5 adımda akıllı arama yapar.

Features:
- 5 adımlı sistematik arama
- Node relationships analizi
- Graph vector search
- Akıllı ipucu takibi
- Türkçe sorular için optimize

Author: Dinkal AI Agent
Date: 2025-09-01
"""

import os
import sys
import json
import logging
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
    format='🤖 %(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

class IntelligentTestAgent:
    """
    🧠 Intelligent Test Agent for Graph Database Analysis
    
    Bu agent 6 adımda sistematik arama yaparak sorulara cevap bulur:
    1. Initial Schema Analysis
    2. Keyword-based Node Search
    3. Semantic Vector Search (YENİ!)
    4. Relationship Pattern Discovery
    5. Deep Graph Traversal
    6. Contextual Information Synthesis
    """
    
    def __init__(self, uri: str = None, user: str = None, password: str = None):
        """Initialize the agent with Neo4j connection and OpenAI client"""
        # Use environment variables if parameters not provided
        self.uri = uri or os.getenv('NEO4J_URI')
        self.username = user or os.getenv('NEO4J_USERNAME') 
        self.password = password or os.getenv('NEO4J_PASSWORD')
        
        # Use project's connection method
        self.driver = get_graphDB_driver(self.uri, self.username, self.password)
        
        # Initialize OpenAI client for embeddings
        self.openai_client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
        
        self.search_history = []
        self.discovered_patterns = []
        self.context_cache = {}
        self.embedding_cache = {}  # Cache for embeddings
        
        logger.info("🚀 Intelligent Test Agent başlatılıyor...")
        self._verify_connection()
    
    def _verify_connection(self):
        """Verify Neo4j connection"""
        try:
            with self.driver.session() as session:
                result = session.run("RETURN 1 as test")
                test_value = result.single()["test"]
                if test_value == 1:
                    logger.info("✅ Neo4j bağlantısı başarılı")
                else:
                    raise Exception("Connection test failed")
        except Exception as e:
            logger.error(f"❌ Neo4j bağlantı hatası: {e}")
            raise
    
    def _clean_data_for_logging(self, data):
        """Embedding gibi uzun veriyi log'dan temizle"""
        if isinstance(data, dict):
            cleaned = {}
            for key, value in data.items():
                if key == 'embedding':
                    cleaned[key] = f"[embedding vector length: {len(value) if isinstance(value, list) else 'unknown'}]"
                elif key == 'embedding_text':
                    # embedding_text'i de log'dan çıkar
                    continue
                elif key == 'found_nodes':
                    # found_nodes içindeki her node'un embedding'ini temizle
                    cleaned[key] = {}
                    for label, keyword_dict in value.items():
                        cleaned[key][label] = {}
                        for keyword, nodes in keyword_dict.items():
                            cleaned_nodes = []
                            for node in nodes:
                                cleaned_node = {k: v for k, v in node.items() if k not in ['embedding', 'embedding_text']}
                                if 'embedding' in node:
                                    cleaned_node['embedding'] = f"[{len(node['embedding'])} dims]"
                                cleaned_nodes.append(cleaned_node)
                            cleaned[key][label][keyword] = cleaned_nodes
                else:
                    cleaned[key] = self._clean_data_for_logging(value)
            return cleaned
        elif isinstance(data, list):
            return [self._clean_data_for_logging(item) for item in data]
        else:
            return data
    
    def search_for_answer(self, question: str) -> Dict[str, Any]:
        """
        5 adımda sistematik arama yaparak soruya cevap bulur
        
        Args:
            question: Araştırılacak soru (Türkçe)
            
        Returns:
            Dict containing search results and answer
        """
        logger.info(f"🔍 Soru analiz ediliyor: {question}")
        
        search_session = {
            "question": question,
            "timestamp": datetime.now().isoformat(),
            "steps": [],
            "final_answer": None,
            "confidence": 0.0
        }
        
        # Step 1: Schema Analysis
        step1_result = self._step1_schema_analysis(question)
        search_session["steps"].append(step1_result)
        
        if step1_result.get("answer_found"):
            search_session["final_answer"] = step1_result.get("answer")
            search_session["confidence"] = step1_result.get("confidence", 0.8)
            logger.info("✅ Adım 1'de cevap bulundu!")
            return search_session
        
        # Step 2: Keyword-based Node Search
        step2_result = self._step2_keyword_search(question, step1_result)
        search_session["steps"].append(step2_result)
        
        if step2_result.get("answer_found"):
            search_session["final_answer"] = step2_result.get("answer")
            search_session["confidence"] = step2_result.get("confidence", 0.8)
            logger.info("✅ Adım 2'de cevap bulundu!")
            return search_session
        
        # Step 3: Semantic Vector Search (YENİ!)
        step3_result = self._step3_semantic_search(question, step1_result, step2_result)
        search_session["steps"].append(step3_result)
        
        if step3_result.get("answer_found"):
            search_session["final_answer"] = step3_result.get("answer")
            search_session["confidence"] = step3_result.get("confidence", 0.8)
            logger.info("✅ Adım 3'te (Semantic Search) cevap bulundu!")
            return search_session
        
        # Step 4: Relationship Pattern Discovery
        step4_result = self._step4_relationship_discovery(question, step1_result, step2_result, step3_result)
        search_session["steps"].append(step4_result)
        
        if step4_result.get("answer_found"):
            search_session["final_answer"] = step4_result.get("answer")
            search_session["confidence"] = step4_result.get("confidence", 0.8)
            logger.info("✅ Adım 4'te cevap bulundu!")
            return search_session
        
        # Step 5: Deep Graph Traversal
        step5_result = self._step5_deep_traversal(question, step1_result, step2_result, step3_result, step4_result)
        search_session["steps"].append(step5_result)
        
        if step5_result.get("answer_found"):
            search_session["final_answer"] = step5_result.get("answer")
            search_session["confidence"] = step5_result.get("confidence", 0.8)
            logger.info("✅ Adım 5'te cevap bulundu!")
            return search_session
        
        # Step 6: Contextual Information Synthesis
        step6_result = self._step6_synthesis(question, search_session["steps"])
        search_session["steps"].append(step6_result)
        search_session["final_answer"] = step6_result.get("answer", "Cevap bulunamadı")
        search_session["confidence"] = step6_result.get("confidence", 0.5)
        
        logger.info("✅ 6 adımlı arama tamamlandı!")
        return search_session
    
    def _step1_schema_analysis(self, question: str) -> Dict[str, Any]:
        """
        Adım 1: Schema Analysis + Count Query Optimization - Veritabanı yapısını analiz eder
        """
        logger.info("📊 Adım 1: Schema analizi + Count query optimization başlıyor...")
        
        step_result = {
            "step": 1,
            "name": "Schema Analysis + Count Optimization",
            "description": "Veritabanı yapısı ve optimized count queries",
            "answer_found": False,
            "data": {},
            "insights": []
        }
        
        try:
            with self.driver.session() as session:
                # Get all node labels
                labels_result = session.run("CALL db.labels()")
                labels = [record["label"] for record in labels_result]
                step_result["data"]["node_labels"] = labels
                
                # Get relationship types
                rels_result = session.run("CALL db.relationshipTypes()")
                relationships = [record["relationshipType"] for record in rels_result]
                step_result["data"]["relationship_types"] = relationships
                
                # Get node counts for each label
                node_counts = {}
                for label in labels:
                    count_result = session.run(f"MATCH (n:{label}) RETURN count(n) as count")
                    count = count_result.single()["count"]
                    node_counts[label] = count
                step_result["data"]["node_counts"] = node_counts
                
                # COUNT QUERY OPTIMIZATION - Türkçe soru analizi
                question_lower = question.lower()
                
                # "Kaç tane" soruları için özel logic
                if any(word in question_lower for word in ["kaç", "sayı", "toplam", "count", "number"]):
                    count_answer = self._handle_count_query(question_lower, node_counts, labels, session)
                    if count_answer:
                        step_result["answer_found"] = True
                        step_result["answer"] = count_answer
                        step_result["confidence"] = 0.9
                        step_result["insights"].append("Count query optimization aktif - direkt cevap bulundu")
                
                # Analyze question keywords against schema
                relevant_labels = []
                keyword_mappings = {
                    "poliçe": ["Policy", "PolicyInfo", "PolicyType"],
                    "müşteri": ["Customer", "CustomerInfo"],
                    "ödeme": ["Payment", "PaymentInfo"],
                    "varlık": ["Asset", "AssetInfo", "InsuredItem"],
                    "teminat": ["Coverage", "CoverageInfo"],
                    "entity": ["__Entity__", "Entity"],
                    "node": labels,  # "node" sorularında tüm labels'ları dahil et
                    "dokument": ["Document"],
                    "chunk": ["Chunk"]
                }
                
                for keyword, mapped_labels in keyword_mappings.items():
                    if keyword in question_lower:
                        for mapped_label in mapped_labels:
                            if mapped_label in labels and mapped_label not in relevant_labels:
                                relevant_labels.append(mapped_label)
                
                step_result["data"]["relevant_labels"] = relevant_labels
                step_result["insights"].append(f"Toplam {len(labels)} node tipi bulundu")
                step_result["insights"].append(f"Toplam {len(relationships)} relationship tipi bulundu")
                step_result["insights"].append(f"Soruyla ilgili olabilecek node tipleri: {relevant_labels}")
                
        except Exception as e:
            logger.error(f"❌ Adım 1 hatası: {e}")
            step_result["error"] = str(e)
        
        logger.info(f"📊 Adım 1 tamamlandı. Relevant labels: {step_result['data'].get('relevant_labels', [])}")
        return step_result
    
    def _handle_count_query(self, question_lower: str, node_counts: dict, labels: list, session) -> str:
        """Count sorularını optimize ederek cevaplar"""
        
        # Genel count soruları
        if "toplam" in question_lower and any(word in question_lower for word in ["entity", "node", "varlık"]):
            total_entities = sum(node_counts.values())
            return f"Toplam {total_entities} entity bulundu: {dict(node_counts)}"
        
        # Specific entity count soruları
        entity_mappings = {
            "müşteri": ["Customer", "CustomerInfo"],
            "poliçe": ["Policy", "PolicyInfo", "PolicyType"], 
            "dokument": ["Document"],
            "chunk": ["Chunk"],
            "varlık": ["AssetInfo", "InsuredItem"],
            "teminat": ["CoverageInfo"]
        }
        
        for entity_word, label_list in entity_mappings.items():
            if entity_word in question_lower:
                total_count = sum(node_counts.get(label, 0) for label in label_list)
                relevant_labels = [label for label in label_list if label in node_counts and node_counts[label] > 0]
                
                if total_count > 0:
                    details = ", ".join([f"{label}: {node_counts[label]}" for label in relevant_labels])
                    return f"{entity_word.title()} toplam {total_count} adet bulundu ({details})"
        
        # Relationship count soruları
        if "ilişki" in question_lower or "relationship" in question_lower:
            try:
                rel_count_result = session.run("MATCH ()-[r]->() RETURN count(r) as total_rels")
                total_rels = rel_count_result.single()["total_rels"]
                return f"Toplam {total_rels} relationship bulundu"
            except:
                pass
        
        return None
    
    def _step2_keyword_search(self, question: str, step1_result: Dict) -> Dict[str, Any]:
        """
        Adım 2: Keyword-based Node Search - Anahtar kelimelerle node arama
        """
        logger.info("🔎 Adım 2: Keyword-based node search başlıyor...")
        
        step_result = {
            "step": 2,
            "name": "Keyword Search",
            "description": "Anahtar kelimelerle node ve property arama",
            "answer_found": False,
            "data": {},
            "insights": []
        }
        
        try:
            with self.driver.session() as session:
                relevant_labels = step1_result.get("data", {}).get("relevant_labels", [])
                
                # Extract keywords from question
                keywords = self._extract_keywords(question)
                step_result["data"]["extracted_keywords"] = keywords
                
                found_nodes = {}
                for label in relevant_labels:
                    # Search in node properties for keywords
                    for keyword in keywords:
                        query = f"""
                        MATCH (n:{label})
                        WHERE toLower(toString(n.id)) CONTAINS $keyword 
                           OR toLower(toString(n.name)) CONTAINS $keyword
                           OR toLower(toString(n.description)) CONTAINS $keyword
                        RETURN n LIMIT 10
                        """
                        result = session.run(query, keyword=keyword.lower())
                        
                        nodes = []
                        for record in result:
                            node = dict(record["n"])
                            nodes.append(node)
                        
                        if nodes:
                            if label not in found_nodes:
                                found_nodes[label] = {}
                            found_nodes[label][keyword] = nodes
                
                step_result["data"]["found_nodes"] = found_nodes
                
                # Analyze findings
                total_matches = sum(len(nodes) for label_data in found_nodes.values() 
                                  for nodes in label_data.values())
                step_result["insights"].append(f"Toplam {total_matches} eşleşme bulundu")
                
                # Check for direct answers in found nodes
                if found_nodes and self._contains_answer_indicators(question):
                    answer_parts = []
                    for label, keyword_data in found_nodes.items():
                        for keyword, nodes in keyword_data.items():
                            answer_parts.extend([str(node.get('id', '')) for node in nodes[:3]])
                    
                    if answer_parts:
                        step_result["answer_found"] = True
                        step_result["answer"] = f"Bulunan bilgiler: {', '.join(answer_parts[:5])}"
                        step_result["confidence"] = 0.7
                
        except Exception as e:
            logger.error(f"❌ Adım 2 hatası: {e}")
            step_result["error"] = str(e)
        
        # Embedding'leri temizleyerek log'a yaz
        cleaned_found_nodes = self._clean_data_for_logging(step_result['data'].get('found_nodes', {}))
        logger.info(f"🔎 Adım 2 tamamlandı. Toplam eşleşme: {cleaned_found_nodes}")
        return step_result
    
    def _step3_semantic_search(self, question: str, step1_result: Dict, step2_result: Dict) -> Dict[str, Any]:
        """
        Adım 3: Semantic Vector Search - Önce DB'deki embedding'leri kullan, yoksa gerçek zamanlı oluştur
        """
        logger.info("🔮 Adım 3: Semantic vector search başlıyor...")
        
        step_result = {
            "step": 3,
            "name": "Semantic Search",
            "description": "Önce DB embedding'leri, sonra real-time embedding similarity arama",
            "answer_found": False,
            "data": {},
            "insights": []
        }
        
        try:
            # Generate embedding for the question
            question_embedding = self._get_text_embedding(question)
            step_result["data"]["question_embedding_generated"] = True
            
            with self.driver.session() as session:
                # Önce DB'de embedding'li node'ları ara
                embedding_query = """
                MATCH (n)
                WHERE n.embedding IS NOT NULL
                RETURN n, labels(n)[0] as label, n.embedding as embedding
                LIMIT 100
                """
                
                result = session.run(embedding_query)
                db_semantic_matches = []
                fallback_nodes = []
                
                for record in result:
                    node = dict(record["n"])
                    label = record["label"]
                    embedding_list = record["embedding"]
                    # id alanı zaten metni içeriyor, embedding_text gereksiz
                    node_text = node.get("id", "")
                    
                    if embedding_list and len(embedding_list) == 1536:  # Ada-002 dimension
                        # DB'den embedding kullan
                        node_embedding = np.array(embedding_list)
                        
                        # Calculate similarity
                        similarity = self._calculate_cosine_similarity(question_embedding, node_embedding)
                        
                        if similarity > 0.7:  # High similarity threshold
                            db_semantic_matches.append({
                                "node": node,
                                "label": label,
                                "text_content": node_text,
                                "similarity_score": float(similarity),
                                "source": "db_embedding"
                            })
                    else:
                        # Embedding eksik, fallback listesine ekle
                        fallback_nodes.append({
                            "node": node,
                            "label": label
                        })
                
                step_result["data"]["db_embeddings_found"] = len(db_semantic_matches)
                step_result["data"]["fallback_nodes_count"] = len(fallback_nodes)
                
                # Fallback: Embedding'i olmayan node'lar için real-time generation
                realtime_matches = []
                if len(db_semantic_matches) < 5 and fallback_nodes:  # En az 5 match istiyoruz
                    step_result["insights"].append(f"DB'de yeterli embedding yok, {len(fallback_nodes)} node için real-time generation")
                    
                    for fallback in fallback_nodes[:20]:  # Max 20 node process et
                        node = fallback["node"]
                        label = fallback["label"]
                        
                        # Extract text content from node
                        text_content = self._extract_node_text_content(node)
                        
                        if text_content and len(text_content.strip()) > 3:
                            # Get or compute node embedding
                            node_embedding = self._get_text_embedding(text_content)
                            
                            # Calculate similarity
                            similarity = self._calculate_cosine_similarity(question_embedding, node_embedding)
                            
                            if similarity > 0.7:  # High similarity threshold
                                realtime_matches.append({
                                    "node": node,
                                    "label": label,
                                    "text_content": text_content[:200],  # First 200 chars
                                    "similarity_score": float(similarity),
                                    "source": "realtime_embedding"
                                })
                
                # Combine all matches
                all_semantic_matches = db_semantic_matches + realtime_matches
                
                # Sort by similarity score
                all_semantic_matches.sort(key=lambda x: x["similarity_score"], reverse=True)
                step_result["data"]["semantic_matches"] = all_semantic_matches[:10]  # Top 10
                
                step_result["insights"].append(f"Toplam {len(all_semantic_matches)} semantic match bulundu")
                step_result["insights"].append(f"DB embedding'den: {len(db_semantic_matches)}")
                step_result["insights"].append(f"Real-time embedding'den: {len(realtime_matches)}")
                
                if all_semantic_matches:
                    # Create answer from top semantic matches
                    top_matches = all_semantic_matches[:3]
                    answer_parts = []
                    
                    for match in top_matches:
                        node_id = match["node"].get("id", match["node"].get("name", "Unknown"))
                        similarity = match["similarity_score"]
                        source = match["source"]
                        answer_parts.append(f"{node_id} (similarity: {similarity:.2f}, {source})")
                    
                    step_result["answer_found"] = True
                    step_result["answer"] = f"Semantic search sonucu: {'; '.join(answer_parts)}"
                    step_result["confidence"] = 0.85  # High confidence for semantic matches
                
        except Exception as e:
            logger.error(f"❌ Adım 3 hatası: {e}")
            step_result["error"] = str(e)
        
        # Semantic matches'ı temizleyerek log'a yaz
        semantic_matches = step_result.get('data', {}).get('semantic_matches', [])
        cleaned_matches = []
        for match in semantic_matches:
            cleaned_match = match.copy()
            if 'node' in cleaned_match and 'embedding' in cleaned_match['node']:
                cleaned_match['node'] = {k: v for k, v in cleaned_match['node'].items() if k != 'embedding'}
            cleaned_matches.append(cleaned_match)
        
        logger.info(f"🔮 Adım 3 tamamlandı. Semantic matches: {len(semantic_matches)} bulundu")
        if cleaned_matches:
            logger.info(f"🔮 Top matches: {[m.get('text_content', '')[:50] + '...' for m in cleaned_matches[:3]]}")
        return step_result
    
    def _get_text_embedding(self, text: str) -> np.ndarray:
        """Generate text embedding using OpenAI API"""
        # Check cache first
        if text in self.embedding_cache:
            return self.embedding_cache[text]
        
        try:
            response = self.openai_client.embeddings.create(
                model="text-embedding-ada-002",
                input=text
            )
            embedding = np.array(response.data[0].embedding)
            
            # Cache the embedding
            self.embedding_cache[text] = embedding
            return embedding
            
        except Exception as e:
            logger.error(f"❌ Embedding generation hatası: {e}")
            # Return zero vector if embedding fails
            return np.zeros(1536)  # Ada-002 embedding dimension
    
    def _extract_node_text_content(self, node: Dict) -> str:
        """Extract meaningful text content from a node"""
        text_parts = []
        
        # Common text fields to check
        text_fields = ["id", "name", "description", "text", "content", "title", "summary"]
        
        for field in text_fields:
            if field in node and node[field]:
                text_parts.append(str(node[field]))
        
        return " ".join(text_parts).strip()
    
    def _calculate_cosine_similarity(self, embedding1: np.ndarray, embedding2: np.ndarray) -> float:
        """Calculate cosine similarity between two embeddings"""
        try:
            # Reshape for sklearn cosine_similarity
            emb1 = embedding1.reshape(1, -1)
            emb2 = embedding2.reshape(1, -1)
            
            similarity = cosine_similarity(emb1, emb2)[0][0]
            return similarity
            
        except Exception as e:
            logger.error(f"❌ Cosine similarity calculation hatası: {e}")
            return 0.0

    def _step4_relationship_discovery(self, question: str, step1_result: Dict, step2_result: Dict, step3_result: Dict) -> Dict[str, Any]:
        """
        Adım 4: Relationship Pattern Discovery - İlişki kalıplarını keşfeder
        """
        logger.info("🔗 Adım 4: Relationship pattern discovery başlıyor...")
        
        step_result = {
            "step": 4,
            "name": "Relationship Discovery",
            "description": "Node'lar arası ilişki kalıplarını keşfet",
            "answer_found": False,
            "data": {},
            "insights": []
        }
        
        try:
            with self.driver.session() as session:
                # Get common relationship patterns
                pattern_query = """
                MATCH (a)-[r]->(b)
                RETURN labels(a)[0] as source_label, type(r) as relationship, labels(b)[0] as target_label, count(*) as frequency
                ORDER BY frequency DESC
                LIMIT 20
                """
                
                patterns = []
                result = session.run(pattern_query)
                for record in result:
                    patterns.append({
                        "source": record["source_label"],
                        "relationship": record["relationship"],
                        "target": record["target_label"],
                        "frequency": record["frequency"]
                    })
                
                step_result["data"]["relationship_patterns"] = patterns
                step_result["insights"].append(f"Toplam {len(patterns)} relationship pattern bulundu")
                
                # Look for specific patterns related to the question
                question_patterns = self._identify_question_patterns(question, patterns)
                step_result["data"]["relevant_patterns"] = question_patterns
                
                # If we found relevant patterns, use them to search for answers
                if question_patterns:
                    answer_data = []
                    for pattern in question_patterns[:3]:  # Top 3 patterns
                        query = f"""
                        MATCH (a:{pattern['source']})-[r:{pattern['relationship']}]->(b:{pattern['target']})
                        RETURN a, r, b
                        LIMIT 5
                        """
                        result = session.run(query)
                        
                        for record in result:
                            source_node = dict(record["a"])
                            target_node = dict(record["b"])
                            answer_data.append({
                                "source": source_node.get("id", str(source_node)),
                                "target": target_node.get("id", str(target_node)),
                                "relationship": pattern["relationship"]
                            })
                    
                    if answer_data:
                        step_result["answer_found"] = True
                        answer_text = self._format_relationship_answer(question, answer_data)
                        step_result["answer"] = answer_text
                        step_result["confidence"] = 0.8
                
        except Exception as e:
            logger.error(f"❌ Adım 3 hatası: {e}")
            step_result["error"] = str(e)
        
        logger.info(f"🔗 Adım 4 tamamlandı. Pattern sayısı: {len(step_result.get('data', {}).get('relationship_patterns', []))}")
        return step_result
    
    def _step5_deep_traversal(self, question: str, step1_result: Dict, step2_result: Dict, step3_result: Dict, step4_result: Dict) -> Dict[str, Any]:
        """
        Adım 5: Deep Graph Traversal - Derin graph gezintisi
        """
        logger.info("🌊 Adım 5: Deep graph traversal başlıyor...")
        
        step_result = {
            "step": 5,
            "name": "Deep Traversal",
            "description": "Multi-hop graph traversal ile derin arama",
            "answer_found": False,
            "data": {},
            "insights": []
        }
        
        try:
            with self.driver.session() as session:
                # Find paths between different types of nodes
                path_queries = [
                    # Customer to Document paths
                    """
                    MATCH path = (c:Customer)-[*1..3]-(d:Document)
                    RETURN path
                    LIMIT 5
                    """,
                    # Policy to various entity paths
                    """
                    MATCH path = (p:Policy)-[*1..3]-(e)
                    WHERE NOT e:Policy
                    RETURN path
                    LIMIT 5
                    """,
                    # Entity to Chunk to Document paths
                    """
                    MATCH path = (e)-[:EXTRACTED_FROM]->(c:Chunk)-[:PART_OF]->(d:Document)
                    RETURN path
                    LIMIT 10
                    """
                ]
                
                all_paths = []
                for query in path_queries:
                    result = session.run(query)
                    paths = []
                    for record in result:
                        path = record["path"]
                        path_info = {
                            "start_node": dict(path.start_node),
                            "end_node": dict(path.end_node),
                            "length": len(path.relationships),
                            "relationships": [rel.type for rel in path.relationships]
                        }
                        paths.append(path_info)
                    all_paths.extend(paths)
                
                step_result["data"]["discovered_paths"] = all_paths
                step_result["insights"].append(f"Toplam {len(all_paths)} path bulundu")
                
                # Analyze paths for question-relevant information
                relevant_paths = self._filter_relevant_paths(question, all_paths)
                step_result["data"]["relevant_paths"] = relevant_paths
                
                if relevant_paths:
                    # Construct answer from path analysis
                    answer_components = []
                    for path in relevant_paths[:3]:
                        start_id = path["start_node"].get("id", "Unknown")
                        end_id = path["end_node"].get("id", "Unknown")
                        answer_components.append(f"{start_id} -> {end_id}")
                    
                    step_result["answer_found"] = True
                    step_result["answer"] = f"Graph traversal sonucu: {'; '.join(answer_components)}"
                    step_result["confidence"] = 0.7
                
        except Exception as e:
            logger.error(f"❌ Adım 4 hatası: {e}")
            step_result["error"] = str(e)
        
        logger.info(f"🌊 Adım 5 tamamlandı. Path sayısı: {len(step_result.get('data', {}).get('discovered_paths', []))}")
        return step_result
    
    def _step6_synthesis(self, question: str, previous_steps: List[Dict]) -> Dict[str, Any]:
        """
        Adım 6: Contextual Information Synthesis - Tüm bilgileri sentezler
        """
        logger.info("🧬 Adım 6: Information synthesis başlıyor...")
        
        step_result = {
            "step": 6,
            "name": "Information Synthesis",
            "description": "Tüm adımlardan toplanan bilgileri sentezle",
            "answer_found": True,  # Always try to provide an answer
            "data": {},
            "insights": []
        }
        
        try:
            # Collect all insights from previous steps
            all_insights = []
            all_data = {}
            
            for step in previous_steps:
                if step.get("insights"):
                    all_insights.extend(step["insights"])
                if step.get("data"):
                    all_data[f"step_{step['step']}"] = step["data"]
            
            step_result["data"]["collected_insights"] = all_insights
            step_result["data"]["all_step_data"] = all_data
            
            # Try to construct a comprehensive answer
            answer_parts = []
            
            # From schema analysis
            if "step_1" in all_data and "node_counts" in all_data["step_1"]:
                node_counts = all_data["step_1"]["node_counts"]
                answer_parts.append(f"Veritabanında {sum(node_counts.values())} toplam entity var")
            
            # From keyword search
            if "step_2" in all_data and "found_nodes" in all_data["step_2"]:
                found_nodes = all_data["step_2"]["found_nodes"]
                if found_nodes:
                    answer_parts.append(f"Anahtar kelime aramasında {len(found_nodes)} kategori bulundu")
            
            # From relationship discovery
            if "step_3" in all_data and "relationship_patterns" in all_data["step_3"]:
                patterns = all_data["step_3"]["relationship_patterns"]
                if patterns:
                    top_pattern = patterns[0]
                    answer_parts.append(f"En yaygın ilişki: {top_pattern['source']} -> {top_pattern['relationship']} -> {top_pattern['target']}")
            
            # From path traversal
            if "step_4" in all_data and "discovered_paths" in all_data["step_4"]:
                paths = all_data["step_4"]["discovered_paths"]
                if paths:
                    answer_parts.append(f"Graph traversal ile {len(paths)} bağlantı yolu bulundu")
            
            # Combine all information
            if answer_parts:
                step_result["answer"] = ". ".join(answer_parts) + "."
                step_result["confidence"] = 0.6
            else:
                step_result["answer"] = "Araştırma tamamlandı ancak spesifik bir cevap bulunamadı. Veritabanı yapısı analiz edildi."
                step_result["confidence"] = 0.4
            
            step_result["insights"].append(f"Toplam {len(all_insights)} insight toplandı")
            step_result["insights"].append("Tüm adımlardan bilgi sentezi yapıldı")
            
        except Exception as e:
            logger.error(f"❌ Adım 5 hatası: {e}")
            step_result["error"] = str(e)
            step_result["answer"] = "Sentez aşamasında hata oluştu"
            step_result["confidence"] = 0.1
        
        logger.info("🧬 Adım 5 tamamlandı - Bilgi sentezi yapıldı")
        return step_result
    
    def _extract_keywords(self, question: str) -> List[str]:
        """Sorudan anahtar kelimeleri çıkarır"""
        # Simple keyword extraction for Turkish
        stop_words = {"ne", "nedir", "nasıl", "kim", "nerede", "niçin", "neden", "hangi", "kaç", "kaçtane", "var", "yok", "mi", "mı", "mu", "mü"}
        words = question.lower().split()
        keywords = [word.strip(".,!?:;") for word in words if word not in stop_words and len(word) > 2]
        return keywords[:5]  # Top 5 keywords
    
    def _contains_answer_indicators(self, question: str) -> bool:
        """Sorunun doğrudan cevap aradığını kontrol eder"""
        indicators = ["kaç", "ne", "kim", "hangi", "nerede", "toplam", "sayı", "adet"]
        return any(indicator in question.lower() for indicator in indicators)
    
    def _identify_question_patterns(self, question: str, patterns: List[Dict]) -> List[Dict]:
        """Soruyla ilgili relationship pattern'leri belirler"""
        question_lower = question.lower()
        relevant = []
        
        for pattern in patterns:
            # Check if pattern labels are relevant to question
            if any(keyword in question_lower for keyword in [
                pattern['source'].lower(),
                pattern['target'].lower(),
                pattern['relationship'].lower()
            ]):
                relevant.append(pattern)
        
        return relevant[:5]  # Top 5 relevant patterns
    
    def _format_relationship_answer(self, question: str, answer_data: List[Dict]) -> str:
        """Relationship verilerini cevap formatına dönüştürür"""
        if not answer_data:
            return "İlişki analizi sonucu bulunmadı"
        
        answer_parts = []
        for item in answer_data[:3]:
            source = item['source'][:50] if len(item['source']) > 50 else item['source']
            target = item['target'][:50] if len(item['target']) > 50 else item['target']
            rel = item['relationship']
            answer_parts.append(f"{source} --[{rel}]--> {target}")
        
        return "İlişki analizi: " + "; ".join(answer_parts)
    
    def _filter_relevant_paths(self, question: str, paths: List[Dict]) -> List[Dict]:
        """Soruyla ilgili path'leri filtreler"""
        question_lower = question.lower()
        relevant = []
        
        for path in paths:
            # Check if path contains relevant information
            start_id = str(path["start_node"].get("id", "")).lower()
            end_id = str(path["end_node"].get("id", "")).lower()
            
            if any(keyword in start_id or keyword in end_id for keyword in self._extract_keywords(question)):
                relevant.append(path)
        
        return relevant
    
    def close(self):
        """Close Neo4j connection"""
        if self.driver:
            self.driver.close()
            logger.info("🔌 Neo4j bağlantısı kapatıldı")

def serialize_neo4j_objects(obj):
    """Neo4j objelerini JSON'a serialize edilebilir hale getir"""
    if isinstance(obj, (neo4j.time.DateTime, neo4j.time.Date, neo4j.time.Time)):
        # Neo4j time objesi
        return str(obj)
    elif hasattr(obj, 'items') and callable(getattr(obj, 'items')):
        # Dict-like object
        try:
            return {k: serialize_neo4j_objects(v) for k, v in obj.items()}
        except:
            return str(obj)
    elif isinstance(obj, (list, tuple)):
        # List-like object
        return [serialize_neo4j_objects(item) for item in obj]
    elif hasattr(obj, '__dict__'):
        # Object with attributes
        try:
            return {k: serialize_neo4j_objects(v) for k, v in obj.__dict__.items()}
        except:
            return str(obj)
    else:
        # Simple types
        try:
            # Test if it's JSON serializable
            json.dumps(obj)
            return obj
        except:
            return str(obj)

def test_intelligent_agent():
    """Test the Intelligent Agent with sample questions"""
    logger.info("🧪 Intelligent Test Agent test başlıyor...")
    
    # Test questions in Turkish - Basic and Advanced
    test_questions = [
        # Basic count/schema questions (Adım 1)
        "Veritabanında kaç tane müşteri var?",
        "Toplam kaç entity bulunuyor?",
        
        # Keyword search questions (Adım 2)
        "Hangi poliçe türleri mevcut?",
        "Poliçe sahipleri kimler?",
        
        # Semantic search questions (Adım 3) - Most important!
        "Yat sigortası hakkında ne biliyorsun?",
        "Kiraz isimli yat ile ilgili hangi bilgiler var?",
        "Ödeme planları nasıl düzenlenmiş?",
        
        # Relationship discovery questions (Adım 4)
        "Müşteriler ile poliçeler arasında nasıl bağlantılar var?",
        "Yat ve teminatlar arasındaki ilişki nedir?",
        
        # Complex multi-hop questions (Adım 5+)
        "YAPI KREDİ FİNANSALKİRALAMA firmasının hangi varlıkları sigortalı?",
        "2020 yılında başlayan poliçelerin ödeme detayları neler?"
    ]
    
    agent = IntelligentTestAgent()
    
    try:
        for i, question in enumerate(test_questions, 1):
            logger.info(f"\n" + "="*60)
            logger.info(f"🔍 Test {i}: {question}")
            logger.info("="*60)
            
            result = agent.search_for_answer(question)
            
            logger.info(f"✅ Cevap bulundu: {result.get('final_answer', 'Cevap yok')}")
            logger.info(f"📊 Güven skoru: {result.get('confidence', 0.0):.2f}")
            logger.info(f"🔧 Adım sayısı: {len(result.get('steps', []))}")
            
            # Save detailed results with proper serialization
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            result_file = f"intelligent_agent_test_{i}_{timestamp}.json"
            
            # Serialize Neo4j objects before saving
            serialized_result = serialize_neo4j_objects(result)
            
            with open(result_file, 'w', encoding='utf-8') as f:
                json.dump(serialized_result, f, ensure_ascii=False, indent=2)
            logger.info(f"📁 Detaylı sonuç kaydedildi: {result_file}")
            
    finally:
        agent.close()
    
    logger.info("🎉 Intelligent Test Agent testi tamamlandı!")

if __name__ == "__main__":
    test_intelligent_agent()
