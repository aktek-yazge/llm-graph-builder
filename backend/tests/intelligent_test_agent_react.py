#!/usr/bin/env python3
"""
🧠 Intelligent Test Agent - ReAct Pattern Implementation

Bu agent LLM-driven ReAct pattern kullanarak herhangi bir dilde sorulan sorulara cevap bulur.
LLM kendi stratejisini belirler, kendi Cypher query'lerini yazar ve iteratif olarak doğru bilgiye ulaşır.

Features:
- ReAct Pattern (Reason + Act)
- Multi-language support (English/Turkish/Any)
- LLM-driven strategy selection
- Self-generated Cypher queries
- Iterative learning and context building
- Vector search + Entity search + Graph patterns
- Intelligent chunk discovery

Author: Dinkal AI Agent (ReAct Implementation)
Date: 2025-09-01
"""

import os
import sys
import json
import logging
from typing import Dict, List, Tuple, Optional, Any
from datetime import datetime
from dotenv import load_dotenv
from dataclasses import dataclass, field
from langchain.schema import HumanMessage, SystemMessage
from langchain_neo4j import Neo4jGraph

# Load environment variables
load_dotenv()

# Import project modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from src.llm import get_llm
from src.shared.common_fn import load_embedding_model
from src.utf8_utils import normalize_unicode_text

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='🤖 %(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

@dataclass
class ChunkInfo:
    """Chunk bilgilerini tutan data class"""
    chunk_id: str
    text: str
    page_number: Optional[int] = None
    document_name: Optional[str] = None
    document_metadata: Dict[str, Any] = field(default_factory=dict)
    relevance_score: float = 0.0
    split_texts: List[str] = field(default_factory=list)
    split_scores: List[float] = field(default_factory=list)

@dataclass 
class AgentState:
    """Agent'ın mevcut durumu ve topladığı bilgileri tutan state"""
    question: str
    discovered_chunks: List[ChunkInfo] = field(default_factory=list)
    discovered_entities: List[Dict[str, Any]] = field(default_factory=list)
    discovered_relationships: List[Dict[str, Any]] = field(default_factory=list)
    query_attempts: List[str] = field(default_factory=list)
    iteration_count: int = 0
    max_chunks_limit: int = 15
    similarity_threshold: float = 0.3
    
    def add_chunk(self, chunk_info: ChunkInfo):
        """Yeni chunk bilgisi ekle"""
        # Duplikasyon kontrolü
        existing_ids = [c.chunk_id for c in self.discovered_chunks]
        if chunk_info.chunk_id not in existing_ids:
            self.discovered_chunks.append(chunk_info)

class IntelligentTestAgentReAct:
    """
    ReAct pattern kullanan Neo4j intelligent test agent
    LLM kendi arama stratejisini belirler ve iteratif olarak doğru veriye ulaşır
    """
    
    def __init__(self, uri: str = None, user: str = None, password: str = None, model_name: str = "openai_gpt_4o"):
        """Initialize the agent with Neo4j connection and LLM"""
        # Use environment variables if parameters not provided
        self.uri = uri or os.getenv('NEO4J_URI')
        self.username = user or os.getenv('NEO4J_USERNAME') 
        self.password = password or os.getenv('NEO4J_PASSWORD')
        
        # Initialize Neo4j graph (LangChain Neo4jGraph)
        self.graph = Neo4jGraph(
            url=self.uri,
            username=self.username,
            password=self.password,
            database=os.getenv('NEO4J_DATABASE', 'neo4j')
        )
        
        # Initialize LLM and embedding model
        self.llm, _ = get_llm(model_name)
        self.embedding_model, _ = load_embedding_model("openai")
        
        # Agent configuration
        self.max_iterations = 8  # ReAct pattern iterations
        self.schema_cache = None
        
        # Progress tracking ve context memory
        self.successful_findings = []  # Her iterasyonda başarılı bulunanlar
        self.context_memory = ""  # Birikimli context prompt
        self.token_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        
        logger.info("🚀 Intelligent Test Agent (ReAct) başlatılıyor...")
        self._verify_connection()
    
    def _verify_connection(self):
        """Verify Neo4j connection"""
        try:
            # LangChain Neo4jGraph test
            result = self.graph.query("RETURN 1 as test")
            if result and result[0]["test"] == 1:
                logger.info("✅ Neo4j bağlantısı başarılı (LangChain Neo4jGraph)")
            else:
                raise Exception("Connection test failed")
        except Exception as e:
            logger.error(f"❌ Neo4j bağlantı hatası: {e}")
            raise
    
    def get_neo4j_schema(self) -> Dict[str, Any]:
        """Neo4j veritabanından schema bilgilerini al"""
        if self.schema_cache:
            return self.schema_cache
            
        try:
            # Node labels
            node_labels_query = "CALL db.labels() YIELD label RETURN collect(label) as labels"
            node_result = self.graph.query(node_labels_query)
            node_labels = node_result[0]['labels'] if node_result else []
            
            # Relationship types
            rel_types_query = "CALL db.relationshipTypes() YIELD relationshipType RETURN collect(relationshipType) as types"
            rel_result = self.graph.query(rel_types_query)
            relationship_types = rel_result[0]['types'] if rel_result else []
            
            # Node properties (sample from each label)
            node_properties = {}
            for label in node_labels[:10]:  # İlk 10 label için
                try:
                    prop_query = f"MATCH (n:`{label}`) RETURN keys(n) as props LIMIT 1"
                    prop_result = self.graph.query(prop_query)
                    if prop_result and prop_result[0]['props']:
                        node_properties[label] = prop_result[0]['props']
                except Exception as e:
                    logger.warning(f"Could not get properties for {label}: {e}")
                    node_properties[label] = []
            
            # Sample relationships
            sample_relationships = []
            for rel_type in relationship_types[:15]:  # İlk 15 ilişki tipi
                try:
                    rel_query = f"""
                    MATCH (a)-[r:`{rel_type}`]->(b) 
                    RETURN labels(a)[0] as from_label, type(r) as rel_type, labels(b)[0] as to_label 
                    LIMIT 1
                    """
                    rel_result = self.graph.query(rel_query)
                    if rel_result:
                        sample_relationships.append(rel_result[0])
                except Exception as e:
                    logger.warning(f"Could not sample relationship {rel_type}: {e}")
            
            self.schema_cache = {
                "node_labels": node_labels,
                "relationship_types": relationship_types,
                "node_properties": node_properties,
                "sample_relationships": sample_relationships
            }
            
            return self.schema_cache
            
        except Exception as e:
            logger.error(f"Schema bilgisi alınamadı: {e}")
            return {
                "node_labels": [],
                "relationship_types": [],
                "node_properties": {},
                "sample_relationships": []
            }
    
    def execute_cypher_query(self, query: str) -> Tuple[bool, Any]:
        """Cypher sorgusunu çalıştır ve syntax hatalarını düzelt"""
        try:
            # Common syntax error fixes
            query = self.fix_cypher_syntax(query)
            
            logger.info(f"🔍 Cypher Query: {query[:100]}...")
            result = self.graph.query(query)
            logger.info(f"📊 Result: {len(result) if result else 0} records")
            return True, result
        except Exception as e:
            error_msg = str(e)
            logger.error(f"❌ Cypher error: {error_msg}")
            
            # Try to fix common syntax errors automatically
            if "colon in the separation of alternative relationship types" in error_msg:
                fixed_query = self.fix_relationship_syntax(query)
                if fixed_query != query:
                    logger.info(f"🔧 Trying to fix relationship syntax...")
                    try:
                        result = self.graph.query(fixed_query)
                        logger.info(f"✅ Fixed query succeeded: {len(result) if result else 0} records")
                        return True, result
                    except Exception as e2:
                        logger.error(f"❌ Fixed query also failed: {e2}")
            
            return False, error_msg
    
    def fix_cypher_syntax(self, query: str) -> str:
        """Common Cypher syntax hatalarını düzelt"""
        # Fix relationship type syntax: [:TYPE1|:TYPE2] -> [:TYPE1|TYPE2]
        import re
        
        # Pattern: [:RELATIONSHIP1|:RELATIONSHIP2] -> [:RELATIONSHIP1|RELATIONSHIP2]
        pattern = r'\[:([A-Z_]+)\|:([A-Z_]+)([*]*)\]'
        fixed_query = re.sub(pattern, r'[:\1|\2\3]', query)
        
        # Pattern: [:TYPE1|:TYPE2*] -> [:TYPE1|TYPE2*]
        pattern2 = r'\[:([A-Z_]+)\|:([A-Z_]+)\*\]'
        fixed_query = re.sub(pattern2, r'[:\1|\2*]', fixed_query)
        
        return fixed_query
    
    def fix_relationship_syntax(self, query: str) -> str:
        """Relationship syntax hatalarını özel olarak düzelt"""
        import re
        
        # Neo4j 4.0+ syntax fix: [:TYPE1|:TYPE2*] -> [:TYPE1|TYPE2*]
        patterns = [
            (r'\[:([A-Z_]+)\|:([A-Z_]+)\*\]', r'[:\1|\2*]'),
            (r'\[:([A-Z_]+)\|:([A-Z_]+)\]', r'[:\1|\2]'),
            (r'\[:\s*([A-Z_]+)\s*\|\s*:([A-Z_]+)\s*\*\s*\]', r'[:\1|\2*]'),
            (r'\[:\s*([A-Z_]+)\s*\|\s*:([A-Z_]+)\s*\]', r'[:\1|\2]')
        ]
        
        fixed_query = query
        for pattern, replacement in patterns:
            fixed_query = re.sub(pattern, replacement, fixed_query)
            
        return fixed_query
    
    def should_stop_search(self, state, iteration: int) -> Optional[str]:
        """Akıllı durma koşulları"""
        # Token efficiency - çok fazla token kullanımı
        if self.token_usage["total_tokens"] > 40000:
            return f"Token limit reached ({self.token_usage['total_tokens']} tokens)"
        
        # Çok fazla iterasyon
        if iteration >= 8:
            return f"Maximum iterations reached ({iteration})"
        
        # Yeterli kaliteli chunk bulundu
        high_quality_chunks = [c for c in state.discovered_chunks if c.relevance_score > 0.85]
        if len(high_quality_chunks) >= 3:
            return f"High quality chunks found ({len(high_quality_chunks)} chunks with >0.85 relevance)"
        
        # Orta kaliteli chunk'lar yeterli sayıda
        good_chunks = [c for c in state.discovered_chunks if c.relevance_score > 0.7]
        if len(good_chunks) >= 5:
            return f"Good quality chunks found ({len(good_chunks)} chunks with >0.7 relevance)"
        
        # Çok sayıda chunk, kalite kontrolü
        if len(state.discovered_chunks) >= 8:
            avg_relevance = sum(c.relevance_score for c in state.discovered_chunks) / len(state.discovered_chunks)
            if avg_relevance > 0.6:
                return f"Sufficient chunks with decent quality ({len(state.discovered_chunks)} chunks, avg relevance: {avg_relevance:.3f})"
        
        # Herhangi bir chunk bulunamadı ve çok denendi
        if iteration >= 5 and len(state.discovered_chunks) == 0:
            return f"No relevant chunks found after {iteration} iterations"
        
        return None
    
    def filter_relevant_chunks(self, chunks: List, min_relevance: float = 0.3) -> List:
        """Relevance score'a göre chunk'ları filtrele"""
        filtered = [c for c in chunks if hasattr(c, 'relevance_score') and c.relevance_score >= min_relevance]
        if len(filtered) < len(chunks):
            logger.info(f"🔍 Filtered {len(chunks)} → {len(filtered)} chunks (min relevance: {min_relevance})")
        return filtered
    
    def log_token_usage(self, response, iteration: int):
        """Token kullanımını logla"""
        try:
            # LangChain OpenAI response structure
            if hasattr(response, 'usage_metadata') and response.usage_metadata:
                usage = response.usage_metadata
                input_tokens = usage.get('input_tokens', 0)
                output_tokens = usage.get('output_tokens', 0) 
                total_tokens = usage.get('total_tokens', 0)
            elif hasattr(response, 'response_metadata') and 'token_usage' in response.response_metadata:
                usage = response.response_metadata['token_usage']
                input_tokens = usage.get('prompt_tokens', 0)
                output_tokens = usage.get('completion_tokens', 0)
                total_tokens = usage.get('total_tokens', 0)
            else:
                # Manuel token sayımı (yaklaşık)
                input_text = str(response.content) if hasattr(response, 'content') else ""
                input_tokens = len(input_text.split()) * 1.3
                output_tokens = len(input_text.split()) * 0.7
                total_tokens = input_tokens + output_tokens
                logger.warning(f"Token usage metadata bulunamadı, yaklaşık hesaplama yapıldı")
            
            self.token_usage["input_tokens"] += int(input_tokens)
            self.token_usage["output_tokens"] += int(output_tokens)
            self.token_usage["total_tokens"] += int(total_tokens)
            
            logger.info(f"🔢 Iteration {iteration} Tokens - Input: {int(input_tokens)}, Output: {int(output_tokens)}")
            
        except Exception as e:
            logger.error(f"Token logging hatası: {e}")
    
    def add_successful_finding(self, iteration: int, action: str, finding: str, relevance_score: float = 0.0):
        """Başarılı bulguyu context memory'e ekle"""
        finding_entry = {
            "iteration": iteration,
            "action": action,
            "finding": finding,
            "relevance_score": relevance_score,
            "timestamp": f"Step {iteration}"
        }
        self.successful_findings.append(finding_entry)
        
        # Context memory'i güncelle
        self.update_context_memory()
        
        logger.info(f"✅ Successful finding - Iteration {iteration}: {action} -> {finding[:80]}...")
    
    def update_context_memory(self):
        """Başarılı bulgulardan context prompt oluştur"""
        if not self.successful_findings:
            self.context_memory = ""
            return
        
        context_prompt = "## PREVIOUS SUCCESSFUL FINDINGS:\n\n"
        
        for finding in self.successful_findings[-3:]:  # Son 3 başarılı bulguyu al
            context_prompt += f"**{finding['timestamp']} - {finding['action'].upper()}:**\n"
            context_prompt += f"Finding: {finding['finding']}\n"
            if finding['relevance_score'] > 0:
                context_prompt += f"Relevance Score: {finding['relevance_score']:.3f}\n"
            context_prompt += "---\n"
        
        context_prompt += "\n**CONSIDER THESE FINDINGS FOR YOUR NEXT ACTION!**\n\n"
        self.context_memory = context_prompt
    
    def entity_driven_search(self, search_term: str, state: AgentState) -> List[ChunkInfo]:
        """Entity-driven arama stratejisi"""
        logger.info(f"🎯 Entity search: {search_term}")
        
        # Entity'lerde arama
        entities = self.search_entities(search_term)
        if not entities:
            logger.info("No entities found")
            return []
        
        logger.info(f"Found {len(entities)} entities")
        
        # Entity'leri state'e ekle
        for entity in entities:
            state.discovered_entities.append(entity)
        
        # Entity'lerden chunk'lara ulaş
        chunks = self.find_chunks_from_entities([e['id'] for e in entities], state)
        
        # Semantic similarity ile sırala
        if chunks:
            chunks = self.rank_chunks_by_semantic_similarity(chunks, search_term)
        
        return chunks[:state.max_chunks_limit]
    
    def search_entities(self, search_term: str) -> List[Dict[str, Any]]:
        """__Entity__ node'larında arama yap"""
        try:
            # Farklı arama stratejileri dene
            strategies = [
                # CONTAINS arama
                f"""
                MATCH (e:__Entity__)
                WHERE toLower(e.id) CONTAINS toLower('{search_term}')
                RETURN e.id as id, e.entity_type as type, labels(e) as labels
                LIMIT 10
                """,
                # Regex arama
                f"""
                MATCH (e:__Entity__)
                WHERE e.id =~ '(?i).*{search_term}.*'
                RETURN e.id as id, e.entity_type as type, labels(e) as labels
                LIMIT 10
                """,
            ]
            
            all_entities = []
            for strategy in strategies:
                success, result = self.execute_cypher_query(strategy)
                if success and result:
                    all_entities.extend(result)
                    if len(all_entities) >= 5:  # Yeterince entity bulundu
                        break
            
            # Duplikasyon temizle
            unique_entities = {}
            for entity in all_entities:
                entity_id = entity.get('id')
                if entity_id and entity_id not in unique_entities:
                    unique_entities[entity_id] = entity
            
            return list(unique_entities.values())
            
        except Exception as e:
            logger.error(f"Entity search error: {e}")
            return []
    
    def find_chunks_from_entities(self, entity_ids: List[str], state: AgentState) -> List[ChunkInfo]:
        """Entity'lerden chunk'lara ulaş"""
        try:
            if not entity_ids:
                return []
                
            # HAS_ENTITY relationship ile chunk'lara ulaş
            chunk_query = """
            UNWIND $entity_ids as entity_id
            MATCH (e:__Entity__ {id: entity_id})-[:HAS_ENTITY]-(c:Chunk)
            OPTIONAL MATCH (c)-[:PART_OF]->(d:Document)
            RETURN DISTINCT 
                c.chunkId as chunk_id,
                c.text as text,
                c.page_number as page_number,
                d.fileName as document_name,
                d as document_metadata
            LIMIT $limit
            """
            
            result = self.graph.query(chunk_query, {
                'entity_ids': entity_ids,
                'limit': state.max_chunks_limit
            })
            
            # Eğer HAS_ENTITY ile sonuç bulunamazsa, text-based arama
            if not result:
                logger.info("HAS_ENTITY relationship not found, trying text search...")
                fallback_query = """
                UNWIND $entity_ids as entity_id
                MATCH (c:Chunk)
                WHERE c.text CONTAINS entity_id
                OPTIONAL MATCH (c)-[:PART_OF]->(d:Document)
                RETURN DISTINCT 
                    c.chunkId as chunk_id,
                    c.text as text,
                    c.page_number as page_number,
                    d.fileName as document_name,
                    d as document_metadata
                LIMIT $limit
                """
                
                result = self.graph.query(fallback_query, {
                    'entity_ids': entity_ids,
                    'limit': state.max_chunks_limit
                })
            
            chunks = []
            for row in result:
                chunk_info = ChunkInfo(
                    chunk_id=row['chunk_id'],
                    text=row['text'] or "",
                    page_number=row['page_number'],
                    document_name=row['document_name'] or "Unknown",
                    document_metadata=dict(row['document_metadata']) if row['document_metadata'] else {}
                )
                chunks.append(chunk_info)
                
            logger.info(f"Found {len(chunks)} chunks from entities")
            return chunks
            
        except Exception as e:
            logger.error(f"Chunk search error: {e}")
            return []
    
    def rank_chunks_by_semantic_similarity(self, chunks: List[ChunkInfo], query: str) -> List[ChunkInfo]:
        """Chunk'ları semantic similarity'ye göre sırala"""
        try:
            if not chunks:
                return chunks
            
            # Query embedding'i al
            query_embedding = self.embedding_model.embed_query(query)
            
            # Chunk'ların Neo4j'den embedding'lerini al
            chunk_ids = [chunk.chunk_id for chunk in chunks if chunk.chunk_id]
            if not chunk_ids:
                return chunks
            
            # Embedding'leri çek
            chunk_ids_str = "', '".join(chunk_ids)
            embedding_query = f"""
            MATCH (c:Chunk)
            WHERE c.chunkId IN ['{chunk_ids_str}']
            RETURN c.chunkId as chunk_id, c.embedding as embedding
            """
            
            success, embedding_results = self.execute_cypher_query(embedding_query)
            if not success or not embedding_results:
                logger.warning("Chunk embeddings not found, using text similarity")
                return self._fallback_similarity_ranking(chunks, query)
            
            # Embedding'leri chunk'lara eşle
            embedding_map = {}
            for result in embedding_results:
                chunk_id = result.get('chunk_id')
                embedding = result.get('embedding')
                if chunk_id and embedding and isinstance(embedding, (list, tuple)) and len(embedding) > 0:
                    embedding_map[chunk_id] = embedding
            
            # Similarity hesapla
            from sklearn.metrics.pairwise import cosine_similarity
            
            for chunk in chunks:
                if chunk.chunk_id in embedding_map:
                    chunk_embedding = embedding_map[chunk.chunk_id]
                    try:
                        if len(chunk_embedding) == len(query_embedding):
                            similarity = float(cosine_similarity([query_embedding], [chunk_embedding])[0][0])
                            chunk.relevance_score = similarity
                        else:
                            chunk.relevance_score = 0.0
                    except Exception:
                        chunk.relevance_score = 0.0
                else:
                    chunk.relevance_score = 0.0
            
            # Similarity'ye göre sırala
            chunks.sort(key=lambda x: x.relevance_score, reverse=True)
            
            logger.info(f"Chunks ranked by embedding similarity. Best score: {chunks[0].relevance_score:.3f}")
            return chunks
            
        except Exception as e:
            logger.error(f"Semantic ranking error: {e}")
            return self._fallback_similarity_ranking(chunks, query)
    
    def _fallback_similarity_ranking(self, chunks: List[ChunkInfo], query: str) -> List[ChunkInfo]:
        """Fallback: Text-based similarity ranking"""
        try:
            query_embedding = self.embedding_model.embed_query(query)
            
            for chunk in chunks:
                if chunk.text:
                    text_embedding = self.embedding_model.embed_query(chunk.text)
                    from sklearn.metrics.pairwise import cosine_similarity
                    similarity = float(cosine_similarity([query_embedding], [text_embedding])[0][0])
                    chunk.relevance_score = similarity
                else:
                    chunk.relevance_score = 0.0
            
            chunks.sort(key=lambda x: x.relevance_score, reverse=True)
            logger.info(f"Fallback text similarity applied. Best score: {chunks[0].relevance_score:.3f}")
            return chunks
            
        except Exception as e:
            logger.error(f"Fallback similarity error: {e}")
            return chunks
    
    def vector_search_with_chunks(self, query_text: str, state: AgentState, limit: int = 10) -> List[ChunkInfo]:
        """Vector search yap ve chunk bilgilerini döndür"""
        try:
            logger.info(f"🔮 Vector search: {query_text}")
            normalized_query = normalize_unicode_text(query_text)
            query_embedding = self.embedding_model.embed_query(normalized_query)
            
            # Neo4j vector index'ini kullan
            vector_query = """
            CALL db.index.vector.queryNodes('vector', $limit, $query_vector) 
            YIELD node, score
            WHERE node:Chunk
            OPTIONAL MATCH (node)-[:PART_OF]->(d:Document)
            RETURN 
                node.chunkId as chunk_id,
                node.text as text, 
                node.page_number as page_number,
                d.fileName as document_name,
                d as document_metadata,
                score
            ORDER BY score DESC
            """
            
            result = self.graph.query(vector_query, {
                'query_vector': query_embedding,
                'limit': limit
            })
            
            if not result:
                return []
            
            chunks = []
            for row in result:
                relevance_score = float(row['score'])
                
                # Relevance filtering - çok düşük score'ları filtrele
                if relevance_score < 0.3:
                    continue
                    
                chunk_info = ChunkInfo(
                    chunk_id=row['chunk_id'],
                    text=row['text'] or "",
                    page_number=row['page_number'],
                    document_name=row['document_name'] or "Unknown",
                    document_metadata=dict(row['document_metadata']) if row['document_metadata'] else {},
                    relevance_score=relevance_score
                )
                chunks.append(chunk_info)
            
            logger.info(f"Vector search result: {len(chunks)} chunks")
            if chunks:
                best_score = max(c.relevance_score for c in chunks)
                avg_score = sum(c.relevance_score for c in chunks) / len(chunks)
                logger.info(f"📊 Best relevance: {best_score:.3f}, Average: {avg_score:.3f}")
            
            return chunks
            
        except Exception as e:
            logger.error(f"Vector search error: {e}")
            return []
    
    def parse_agent_response(self, response: str) -> Tuple[str, str, Tuple[str, str]]:
        """Agent cevabını parse et - ReAct format"""
        observation = ""
        thought = ""
        action = ""
        action_content = ""
        
        lines = response.strip().split('\n')
        current_section = None
        
        for line in lines:
            line = line.strip()
            if line.startswith('Observation:'):
                current_section = 'observation'
                observation = line.replace('Observation:', '').strip()
            elif line.startswith('Thought:'):
                current_section = 'thought'
                thought = line.replace('Thought:', '').strip()
            elif line.startswith('Action:'):
                current_section = 'action'
                action = line.replace('Action:', '').strip()
            elif line.startswith('Query:') or line.startswith('Content:'):
                action_content = line.replace('Query:', '').replace('Content:', '').strip()
            elif current_section and line:
                if current_section == 'observation':
                    observation += ' ' + line
                elif current_section == 'thought':
                    thought += ' ' + line
                elif current_section == 'action':
                    if not action:
                        action = line
                    else:
                        action_content += ' ' + line
        
        return observation.strip(), thought.strip(), (action.strip(), action_content.strip())
    
    def solve_question(self, user_question: str) -> Dict[str, Any]:
        """Ana problem çözme fonksiyonu - ReAct pattern ile"""
        
        # Cache temizle
        self.successful_findings = []
        self.context_memory = ""
        self.token_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        
        logger.info(f"🔍 Solving question: {user_question}")
        
        # Agent state'i başlat
        state = AgentState(question=user_question)
        
        # Schema bilgilerini al
        schema = self.get_neo4j_schema()
        system_prompt = self.create_system_prompt(schema)
        
        conversation_history = []
        
        # İlk observation
        current_observation = f"User question: '{user_question}'"
        
        while state.iteration_count < self.max_iterations:
            state.iteration_count += 1
            logger.info(f"🔄 Iteration {state.iteration_count}")
            
            # Context info hazırla
            context_info = ""
            if state.discovered_chunks:
                context_info = f"\n\nCurrent State:\n- {len(state.discovered_chunks)} chunks discovered\n- Best relevance: {max([c.relevance_score for c in state.discovered_chunks]):.3f}\n- Documents: {list(set([c.document_name for c in state.discovered_chunks]))}"
            
            # LLM'e gönderilecek prompt
            prompt = f"{self.context_memory}{current_observation}{context_info}\n\nDetermine your next action based on this situation:"
            
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=prompt)
            ]
            
            # Conversation history ekle
            for entry in conversation_history[-3:]:  # Son 3 adımı tut
                messages.append(HumanMessage(content=entry))
            
            try:
                response = self.llm.invoke(messages)
                agent_response = response.content
                
                # Token kullanımını logla
                self.log_token_usage(response, state.iteration_count)
                
                logger.info(f"🤖 Agent response: {agent_response[:150]}...")
                
                # Response'u parse et
                observation, thought, (action, action_content) = self.parse_agent_response(agent_response)
                
                logger.info(f"📋 Parsed - Action: '{action}', Content: '{action_content[:40]}...'")
                
                conversation_history.append(f"Thought: {thought}\nAction: {action}\nContent: {action_content}")
                
                # Action'ı uygula
                if action == "entity_search":
                    chunks = self.entity_driven_search(action_content, state)
                    for chunk in chunks:
                        state.add_chunk(chunk)
                    
                    if chunks:
                        best_relevance = max([c.relevance_score for c in chunks])
                        current_observation = f"Entity search result: {len(chunks)} chunks found. Best relevance: {best_relevance:.3f}. Total chunks: {len(state.discovered_chunks)}"
                        
                        self.add_successful_finding(
                            state.iteration_count,
                            "entity_search",
                            f"'{action_content}' → {len(chunks)} chunks. Best relevance: {best_relevance:.3f}",
                            best_relevance
                        )
                    else:
                        current_observation = f"No entities found for '{action_content}'. Try different keywords."
                        
                elif action == "cypher_query":
                    success, result = self.execute_cypher_query(action_content)
                    if success and result:
                        # Sonuçlardan entity'leri çıkar
                        entity_ids = []
                        for row in result[:10]:  # İlk 10 sonuç
                            for key, value in row.items():
                                if key.endswith('_id') or key in ['id', 'name']:
                                    if isinstance(value, str) and len(value) > 0:
                                        entity_ids.append(value)
                                        state.discovered_entities.append({key: value})
                        
                        if entity_ids:
                            chunks = self.find_chunks_from_entities(entity_ids, state)
                            for chunk in chunks:
                                state.add_chunk(chunk)
                            
                            current_observation = f"Cypher query successful. {len(result)} results found. {len(chunks)} new chunks discovered. Total chunks: {len(state.discovered_chunks)}"
                            
                            if chunks:
                                avg_relevance = sum([c.relevance_score for c in chunks]) / len(chunks) if chunks else 0
                                self.add_successful_finding(
                                    state.iteration_count,
                                    "cypher_query", 
                                    f"Cypher: {len(result)} results, {len(chunks)} chunks. Avg relevance: {avg_relevance:.3f}",
                                    avg_relevance
                                )
                        else:
                            current_observation = f"Cypher successful but no entities found. Result: {str(result[:2])}"
                    else:
                        current_observation = f"Cypher query failed: {result}. Try different query."
                        
                elif action == "vector_search":
                    chunks = self.vector_search_with_chunks(action_content, state)
                    
                    # Relevance filtering
                    filtered_chunks = self.filter_relevant_chunks(chunks, min_relevance=0.4)
                    
                    for chunk in filtered_chunks:
                        state.add_chunk(chunk)
                    
                    if filtered_chunks:
                        best_relevance = max([c.relevance_score for c in filtered_chunks])
                        avg_relevance = sum([c.relevance_score for c in filtered_chunks]) / len(filtered_chunks)
                        current_observation = f"Vector search: {len(chunks)} found, {len(filtered_chunks)} relevant (>0.4). Best: {best_relevance:.3f}, Avg: {avg_relevance:.3f}. Total chunks: {len(state.discovered_chunks)}"
                        
                        self.add_successful_finding(
                            state.iteration_count,
                            "vector_search",
                            f"'{action_content}' vector search: {len(filtered_chunks)} chunks. Best relevance: {best_relevance:.3f}",
                            best_relevance
                        )
                    else:
                        current_observation = f"Vector search found {len(chunks)} chunks but none relevant (>0.4 threshold). Try different keywords."
                
                else:
                    current_observation = f"Unknown action: {action}. Valid actions: entity_search, cypher_query, vector_search"
                
                # Chunk limit kontrolü
                if len(state.discovered_chunks) >= state.max_chunks_limit:
                    current_observation += f" (Chunk limit {state.max_chunks_limit} reached)"
                
            except Exception as e:
                logger.error(f"Iteration {state.iteration_count} error: {e}")
                current_observation = f"Error occurred: {e}. Try different approach."
                
            # Intelligent stopping conditions
            stop_reason = self.should_stop_search(state, state.iteration_count)
            if stop_reason:
                logger.info(f"🛑 {stop_reason}")
                break
        
        # Final results
        logger.info(f"🎯 TOTAL TOKEN USAGE - Input: {self.token_usage['input_tokens']}, Output: {self.token_usage['output_tokens']}, Total: {self.token_usage['total_tokens']}")
        
        return {
            "question": user_question,
            "iterations": state.iteration_count,
            "conversation_history": conversation_history,
            "discovered_chunks": len(state.discovered_chunks),
            "discovered_entities": len(state.discovered_entities),
            "successful_findings": self.successful_findings.copy(),
            "context_memory": self.context_memory,
            "token_usage": self.token_usage.copy(),
            "top_chunks": [
                {
                    "document": c.document_name,
                    "page": c.page_number,
                    "relevance": c.relevance_score,
                    "preview": c.text[:150] + "..."
                } for c in sorted(state.discovered_chunks, key=lambda x: x.relevance_score, reverse=True)[:5]
            ],
            "final_answer": self._generate_final_answer(state, user_question)
        }
    
    def _generate_final_answer(self, state: AgentState, question: str) -> str:
        """Toplanan bilgilerden final cevap oluştur"""
        if not state.discovered_chunks:
            return "No relevant information found in the database."
        
        # En yüksek relevance'a sahip chunk'ları al
        top_chunks = sorted(state.discovered_chunks, key=lambda x: x.relevance_score, reverse=True)[:3]
        
        # Chunk'lardan bilgi çıkar
        answer_parts = []
        for chunk in top_chunks:
            if chunk.relevance_score > 0.3:  # Threshold
                text_preview = chunk.text[:200] + "..." if len(chunk.text) > 200 else chunk.text
                answer_parts.append(f"From {chunk.document_name}: {text_preview}")
        
        if answer_parts:
            return "Based on discovered information:\n\n" + "\n\n".join(answer_parts)
        else:
            return "Information found but relevance scores are low. Please refine your question."
    
    def create_system_prompt(self, schema: Dict[str, Any]) -> str:
        """Enhanced system prompt with schema info - Multi-language support"""
        
        schema_text = f"""
## Neo4j Database Schema Information

### Available Node Labels:
{', '.join(schema['node_labels'])}

### Available Relationship Types:
{', '.join(schema['relationship_types'])}

### Node Properties (examples):
"""
        
        for label, props in schema['node_properties'].items():
            schema_text += f"\n- {label}: {', '.join(props)}"
        
        schema_text += "\n\n### Sample Relationships:\n"
        for rel in schema['sample_relationships']:
            schema_text += f"- ({rel['from_label']})-[:{rel['rel_type']}]->({rel['to_label']})\n"
        
        system_prompt = f"""You are an INTELLIGENT KNOWLEDGE RESEARCHER and DATA MINER working with a Neo4j database.
Based on user questions, you analyze the available schema and find the most relevant entities, reach chunks, and extract correct information.

{schema_text}

## MANDATORY STARTING RULE:
**THE FIRST ACTION FOR EVERY QUESTION MUST BE 'entity_search'!**
**YOU MUST PERFORM entity_search BEFORE USING ANY OTHER ACTION!**

## KNOWLEDGE RESEARCH PROCESS:

### STEP 1: MANDATORY ENTITY SEARCH
- ALWAYS start with entity_search first
- Use keywords from the question
- Start with person names, company names, product names
- Example: Question "What about Ayça..." → entity_search → "Ayça"

### STEP 2: ENTITY DISCOVERY STRATEGY
- Try node types from schema systematically:
  1. Search for entities containing key concepts
  2. Search similar terms in different node types
  3. Discover related entities (following relationships)
- Evaluate each attempt: "Are these entities related to the question?"

### STEP 3: CHUNK DISCOVERY AND EMBEDDING ANALYSIS
- Reach chunks from found entities
- Compare chunk embeddings with question embedding
- Prioritize chunks with high relevance scores
- Analyze chunk texts in detail

### STEP 4: INFORMATION EXTRACTION AND VALIDATION
- Extract question-related information from chunk texts
- For numerical data: find numbers, quantities
- For names: find person, place, organization names
- For dates: find time information
- Validate found information: "Does this fully answer the question?"

### STEP 5: ITERATIVE IMPROVEMENT
- If insufficient info: try different node types
- If missing info: search related entities
- If unclear: write more specific queries
- Ask yourself: "Can I fully answer the question?"

## ACTION TYPES:

### entity_search - MANDATORY FIRST STEP
- Search for keyword from question
- Searches in __Entity__ nodes
- Returns related chunks
- Example: Action: entity_search, Content: Ayça

### cypher_query
- Write optimal queries based on Neo4j schema
- Try different node types systematically
- Use relationships to discover related entities
- **IMPORTANT SYNTAX RULES:**
  * Relationship alternatives: `[:TYPE1|TYPE2]` (NOT `[:TYPE1|:TYPE2]`)
  * Variable length: `[:TYPE1|TYPE2*]` (NOT `[:TYPE1|:TYPE2*]`)
  * Multiple hops: `[:FIRST_CHUNK|NEXT_CHUNK*1..5]`
  * No double colons in relationship alternatives

Examples:
- `MATCH (n:Customer) WHERE n.name CONTAINS "keyword" RETURN n`
- `MATCH (d:Document)-[:FIRST_CHUNK|NEXT_CHUNK*]->(c:Chunk) RETURN c`
- `MATCH (p:Policy)-[:HAS_TYPE]->(pt:PolicyType) WHERE pt.name = "type" RETURN p`

### vector_search
- Find chunks semantically similar to question
- Direct text-based search
- Example: "2020 policy information"

## EXAMPLE RESEARCH FLOW:

**Question: "How many X type Y are there in 2020?"**

1. **Schema Analysis**: Look at PolicyType, Policy, Customer, Year nodes
2. **Entity Search**: 
   - MATCH (pt:PolicyType) WHERE pt.id CONTAINS "X" → X type policies
   - MATCH (y:Year) WHERE y.id = "2020" → 2020 year
3. **Relationship Exploration**:
   - MATCH (policy)-[:OF_TYPE]→(pt) → Policy type connection
   - MATCH (policy)-[:FOR_YEAR]→(y) → Year connection
4. **Combined Query**:
   - Count policies satisfying both type and year conditions
5. **Chunk Analysis**: 
   - Go to chunks from found entities
   - Search for numerical data in chunk texts
6. **Stop when 3-5 chunks collected**

## ACTION FORMAT:
Observation: Observe current situation
Thought: Think about which strategy to use
Action: cypher_query
Query: MATCH (pt:PolicyType) RETURN count(DISTINCT pt.id) as policy_type_count

OR

Observation: Evaluate vector search results
Thought: Check if enough chunks available
Action: vector_search
Content: 2020 policy count

## LANGUAGE SUPPORT:
- **English**: "What yacht information do you have?" → Understand and plan actions
- **Turkish**: "Yat bilgileri neler?" → Understand and plan actions  
- **Any Language**: The schema is the same, adapt your searches accordingly

RULES:
- **CONTEXT MATCHING**: Always verify if found information matches the question context
  * For person names: "Ayça" ≠ "Ahmet" - they are different people!
  * For specific terms: "DASK" ≠ "yacht insurance" - different policy types!
  * For numbers: Exact counts vs general information
- Only collect chunks and entities, don't give final answers
- Stop when 3-5 relevant chunks found (relevance > 0.4)
- Ask "Is this info related to question?" at each step
- Try ALL node types from schema systematically
- Actively use relationships (follow connections)
- Pay attention to chunk relevance scores (>0.4 is good, >0.7 is excellent)
- Try at least 3-5 different query strategies
- Maximum 8 iterations (deep research)
- If no relevant results found after 5 tries, admit failure

Now analyze the given question like a scientific researcher and solve step by step."""

        return system_prompt
    
    def close(self):
        """Close connections"""
        # LangChain Neo4jGraph doesn't need explicit close
        logger.info("🔌 Agent connections closed")

def test_react_agent():
    """Test the ReAct Agent with various questions"""
    logger.info("🧪 Testing Intelligent ReAct Agent...")
    
    # Test questions - Multi-language
    test_questions = [
        # English questions
        "How many customers are in the database?",
        "What yacht information do you have?",
        "What policy types are available?",
        
        # Turkish questions  
        "Ayça hanımın 2020 yılında kaç adet poliçesi var?",
        "Yat sigortası hakkında ne biliyorsun?",
        "DASK poliçeleri ile ilgili hangi bilgiler var?",
        "Kiraz isimli yat hakkında ne var?",
    ]
    
    agent = IntelligentTestAgentReAct()
    
    try:
        for i, question in enumerate(test_questions, 1):
            logger.info(f"\n{'='*80}")
            logger.info(f"🔍 TEST {i}: {question}")
            logger.info('='*80)
            
            result = agent.solve_question(question)
            
            logger.info(f"✅ Iterations: {result['iterations']}")
            logger.info(f"📊 Chunks: {result['discovered_chunks']}")
            logger.info(f"🏷️ Entities: {result['discovered_entities']}")
            logger.info(f"🔢 Tokens: {result['token_usage']['total_tokens']}")
            
            logger.info(f"\n📄 TOP CHUNKS:")
            for chunk in result['top_chunks']:
                logger.info(f"- {chunk['document']} (Page {chunk['page']}) - Relevance: {chunk['relevance']:.3f}")
                logger.info(f"  {chunk['preview']}")
            
            logger.info(f"\n🎯 FINAL ANSWER:")
            logger.info(result['final_answer'])
            
            # Save detailed results
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            result_file = f"react_agent_test_{i}_{timestamp}.json"
            
            with open(result_file, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2, default=str)
            logger.info(f"📁 Detailed results saved: {result_file}")
            
    finally:
        agent.close()
    
    logger.info("🎉 ReAct Agent testing completed!")

if __name__ == "__main__":
    test_react_agent()
