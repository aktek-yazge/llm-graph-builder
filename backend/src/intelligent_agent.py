#!/usr/bin/env python3
"""
Intelligent Neo4j ReAct Agent
Bu agent kullanıcı sorularını analiz eder, Neo4j schema'sını kullanarak
Cypher sorguları oluşturur ve gerekirse vector search yapar.
"""

import logging
import json
import re
import os
from typing import Dict, List, Optional, Tuple, Any
from langchain.schema import HumanMessage, SystemMessage
from langchain_neo4j import Neo4jGraph
from langchain.text_splitter import RecursiveCharacterTextSplitter
from src.llm import get_llm
from src.shared.common_fn import load_embedding_model
from src.utf8_utils import normalize_unicode_text
import time
from dotenv import load_dotenv
from dataclasses import dataclass, field
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np

load_dotenv()

logging.basicConfig(level=logging.INFO)
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
    max_chunks_limit: int = 20
    similarity_threshold: float = 0.3
    
    def add_chunk(self, chunk_info: ChunkInfo):
        """Yeni chunk bilgisi ekle"""
        # Duplikasyon kontrolü
        existing_ids = [c.chunk_id for c in self.discovered_chunks]
        if chunk_info.chunk_id not in existing_ids:
            self.discovered_chunks.append(chunk_info)
            
    def get_context_for_llm(self) -> str:
        """LLM için kontekst metni oluştur"""
        context_parts = []
        
        # Chunk bilgilerini relevance score'a göre sırala
        sorted_chunks = sorted(self.discovered_chunks, key=lambda x: x.relevance_score, reverse=True)
        
        for chunk in sorted_chunks[:10]:  # En iyi 10 chunk'ı al
            context_parts.append(f"""
### Belge: {chunk.document_name}
**Sayfa:** {chunk.page_number}
**Chunk ID:** {chunk.chunk_id}
**Relevance Score:** {chunk.relevance_score:.3f}

**İçerik:**
{chunk.text[:1000]}...

**En İlgili Bölümler:**
""")
            for i, (split_text, score) in enumerate(zip(chunk.split_texts[:3], chunk.split_scores[:3])):
                context_parts.append(f"- ({score:.3f}) {split_text[:200]}...")
        
        # Entity bilgileri ekle
        if self.discovered_entities:
            context_parts.append("\n### Keşfedilen Varlıklar:")
            for entity in self.discovered_entities[:20]:
                context_parts.append(f"- {entity}")
                
        return "\n".join(context_parts)

class IntelligentAgent:
    """
    ReAct pattern kullanan Neo4j intelligent agent
    LLM kendi arama stratejisini belirler ve iteratif olarak doğru veriye ulaşır
    """
    
    def __init__(self, graph: Neo4jGraph, model_name: str = "openai_gpt_4o"):
        self.graph = graph
        self.llm, _ = get_llm(model_name)
        self.embedding_model, _ = load_embedding_model("openai")
        self.max_iterations = 10  # Derinlemesine araştırma için
        self.max_iterations = 8
        self.schema_cache = None
        
        # Text splitter'ı başlat
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=300,
            chunk_overlap=50,
            length_function=len,
            separators=["\n\n", "\n", ". ", "! ", "? ", " ", ""]
        )
        
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
    
    def create_system_prompt(self, schema: Dict[str, Any]) -> str:
        """System prompt'u schema bilgileriyle oluştur"""
        
        schema_text = f"""
## Neo4j Veritabanı Schema Bilgileri

### Mevcut Node Labels:
{', '.join(schema['node_labels'])}

### Mevcut Relationship Types:
{', '.join(schema['relationship_types'])}

### Node Properties (örnek):
"""
        
        for label, props in schema['node_properties'].items():
            schema_text += f"\n- {label}: {', '.join(props)}"
        
        schema_text += "\n\n### Örnek İlişkiler:\n"
        for rel in schema['sample_relationships']:
            schema_text += f"- ({rel['from_label']})-[:{rel['rel_type']}]->({rel['to_label']})\n"
        
        system_prompt = f"""Sen bir Neo4j veritabanında bilgi arayan AKILLI BİLGİ MADENCİSİ'sin.

{schema_text}

## ANA STRATEJI: ENTITY-DRIVEN SEARCH

### İLK ADIM: Entity Search (ZORUNLU)
Kullanıcı sorusundan anahtar kelime çıkar ve:
Action: entity_search
[anahtar kelime]

ÖRNEK:
- "Ayça hanımın poliçeleri" → Action: entity_search, Ayça
- "2020 yılı poliçeleri" → Action: entity_search, 2020
- "DASK sigortası" → Action: entity_search, DASK

### BACKUP STRATEJİLER:
Sadece entity_search boş sonuç verirse:

**Sayısal Sorgular:**
Action: cypher_query
MATCH (p:Policy) RETURN count(*)

**Vector Search:**
Action: vector_search
[semantic arama metni]

## FORMAT (ZORUNLU):
Observation: [durum]
Thought: [düşünce]
Action: entity_search
[tek anahtar kelime]

veya

Action: final_answer
[cevap]

## KURALLAR:
1. İLK ACTION MUTLAKA entity_search OLMALI
2. Tek seferde tek anahtar kelime kullan
3. Boş sonuç alırsan farklı kelime dene
4. Maksimum 5 iterasyon

Şimdi MUTLAKA entity_search ile başla!"""

        return system_prompt
    
    def execute_cypher_query(self, query: str) -> Tuple[bool, Any]:
        """Cypher sorgusunu çalıştır"""
        try:
            logger.info(f"Cypher sorgusu çalıştırılıyor: {query}")
            result = self.graph.query(query)
            logger.info(f"Sonuç: {len(result) if result else 0} kayıt")
            return True, result
        except Exception as e:
            logger.error(f"Cypher sorgu hatası: {e}")
            return False, str(e)
    
    def entity_driven_search(self, search_term: str, state: AgentState) -> List[ChunkInfo]:
        """
        Entity-driven arama stratejisi:
        1. __Entity__ node'larında arama yap
        2. Bulunan entity'lerden chunk'lara ulaş
        3. Chunk relationship'lerini takip et
        4. Embedding ile semantic matching yap
        """
        logger.info(f"Entity-driven search başlatılıyor: {search_term}")
        
        # Adım 1: Entity'lerde arama
        entities = self.search_entities(search_term)
        if not entities:
            logger.info("Hiç entity bulunamadı")
            return []
        
        logger.info(f"{len(entities)} entity bulundu")
        
        # Adım 2: Entity'lerden chunk'lara ulaş
        primary_chunks = self.find_chunks_from_entities([e['id'] for e in entities], state)
        
        # Adım 3: İlişkili chunk'ları da bul
        all_chunks = primary_chunks.copy()
        for chunk in primary_chunks:
            related_chunks = self.find_related_chunks(chunk.chunk_id, state)
            all_chunks.extend(related_chunks)
        
        # Duplikasyon temizle
        unique_chunks = {}
        for chunk in all_chunks:
            if chunk.chunk_id not in unique_chunks:
                unique_chunks[chunk.chunk_id] = chunk
        
        chunks = list(unique_chunks.values())
        logger.info(f"Toplam {len(chunks)} unique chunk bulundu")
        
        # Adım 4: Embedding ile semantic matching
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
                WHERE apoc.text.clean(e.id) CONTAINS apoc.text.clean('{search_term}')
                RETURN e.id as id, e.entity_type as type, labels(e) as labels
                LIMIT 20
                """,
                # Daha geniş arama
                f"""
                MATCH (e:__Entity__)
                WHERE apoc.text.clean(e.id) =~ '(?i).*{search_term}.*'
                RETURN e.id as id, e.entity_type as type, labels(e) as labels
                LIMIT 20
                """,
                # Tüm property'lerde arama
                f"""
                MATCH (e:__Entity__)
                WHERE any(prop IN keys(e) WHERE apoc.text.clean(toString(e[prop])) CONTAINS apoc.text.clean('{search_term}'))
                RETURN e.id as id, e.entity_type as type, labels(e) as labels
                LIMIT 20
                """
            ]
            
            all_entities = []
            for strategy in strategies:
                success, result = self.execute_cypher_query(strategy)
                if success and result:
                    all_entities.extend(result)
                    if len(all_entities) >= 10:  # Yeterince entity bulundu
                        break
            
            # Duplikasyon temizle
            unique_entities = {}
            for entity in all_entities:
                entity_id = entity.get('id')
                if entity_id and entity_id not in unique_entities:
                    unique_entities[entity_id] = entity
            
            return list(unique_entities.values())
            
        except Exception as e:
            logger.error(f"Entity arama hatası: {e}")
            return []
    
    def find_related_chunks(self, chunk_id: str, state: AgentState) -> List[ChunkInfo]:
        """Bir chunk'ın ilişkili chunk'larını bul"""
        try:
            # SIMILAR ilişkileri takip et
            query = f"""
            MATCH (c1:Chunk {{chunkId: '{chunk_id}'}})-[:SIMILAR]->(c2:Chunk)
            OPTIONAL MATCH (c2)-[:PART_OF]->(d:Document)
            RETURN c2.chunkId as chunk_id, c2.text as text, c2.page_number as page_number,
                   d.fileName as document_name
            UNION
            MATCH (c1:Chunk {{chunkId: '{chunk_id}'}})<-[:SIMILAR]-(c2:Chunk)
            OPTIONAL MATCH (c2)-[:PART_OF]->(d:Document)
            RETURN c2.chunkId as chunk_id, c2.text as text, c2.page_number as page_number,
                   d.fileName as document_name
            LIMIT 10
            """
            
            success, result = self.execute_cypher_query(query)
            if not success or not result:
                return []
            
            chunks = []
            for row in result:
                chunk_info = ChunkInfo(
                    chunk_id=row['chunk_id'] or "",
                    text=row['text'] or "",
                    page_number=row['page_number'],
                    document_name=row['document_name'] or "Unknown"
                )
                chunks.append(chunk_info)
            
            logger.info(f"Chunk {chunk_id} için {len(chunks)} ilişkili chunk bulundu")
            return chunks
            
        except Exception as e:
            logger.error(f"İlişkili chunk arama hatası: {e}")
            return []
    
    def rank_chunks_by_semantic_similarity(self, chunks: List[ChunkInfo], query: str) -> List[ChunkInfo]:
        """Chunk'ları semantic similarity'ye göre sırala - Önceden hesaplanmış embedding'leri kullan"""
        try:
            if not chunks:
                return chunks
            
            # Query embedding'i al (sadece bir kez)
            query_embedding = self.embedding_model.embed_query(query)
            
            # Chunk'ların Neo4j'den embedding'lerini al
            chunk_ids = [chunk.chunk_id for chunk in chunks if chunk.chunk_id]
            if not chunk_ids:
                return chunks
            
            # Batch olarak embedding'leri çek
            chunk_ids_str = "', '".join(chunk_ids)
            embedding_query = f"""
            MATCH (c:Chunk)
            WHERE c.chunkId IN ['{chunk_ids_str}']
            RETURN c.chunkId as chunk_id, c.embedding as embedding
            """
            
            success, embedding_results = self.execute_cypher_query(embedding_query)
            if not success or not embedding_results:
                logger.warning("Chunk embedding'leri alınamadı, fallback similarity kullanılıyor")
                return self._fallback_similarity_ranking(chunks, query)
            
            # Embedding'leri chunk'lara eşle
            embedding_map = {}
            for result in embedding_results:
                embedding_map[result['chunk_id']] = result['embedding']
            
            # Similarity hesapla
            for chunk in chunks:
                if chunk.chunk_id in embedding_map:
                    chunk_embedding = embedding_map[chunk.chunk_id]
                    if chunk_embedding and len(chunk_embedding) == len(query_embedding):
                        similarity = float(cosine_similarity([query_embedding], [chunk_embedding])[0][0])
                        chunk.relevance_score = similarity
                    else:
                        chunk.relevance_score = 0.0
                else:
                    chunk.relevance_score = 0.0
            
            # Similarity'ye göre sırala
            chunks.sort(key=lambda x: x.relevance_score, reverse=True)
            
            logger.info(f"Chunk'lar önceden hesaplanmış embedding'lerle sıralandı. En yüksek score: {chunks[0].relevance_score:.3f}")
            return chunks
            
        except Exception as e:
            logger.error(f"Semantic ranking hatası: {e}")
            return self._fallback_similarity_ranking(chunks, query)
    
    def _fallback_similarity_ranking(self, chunks: List[ChunkInfo], query: str) -> List[ChunkInfo]:
        """Fallback: Text-based similarity ranking"""
        try:
            query_embedding = self.embedding_model.embed_query(query)
            
            for chunk in chunks:
                if chunk.text:
                    # Sadece chunk text'inin tamamı için embedding al
                    text_embedding = self.embedding_model.embed_query(chunk.text)
                    similarity = float(cosine_similarity([query_embedding], [text_embedding])[0][0])
                    chunk.relevance_score = similarity
                else:
                    chunk.relevance_score = 0.0
            
            chunks.sort(key=lambda x: x.relevance_score, reverse=True)
            logger.info(f"Fallback similarity ranking uygulandı. En yüksek score: {chunks[0].relevance_score:.3f}")
            return chunks
            
        except Exception as e:
            logger.error(f"Fallback similarity ranking hatası: {e}")
            return chunks

    def find_chunks_from_entities(self, entity_ids: List[str], state: AgentState) -> List[ChunkInfo]:
        """Entity'lerden chunk'lara ulaş"""
        try:
            if not entity_ids:
                return []
                
            # Entity'lere bağlı chunk'ları bul
            chunk_query = """
            MATCH (e)-[:HAS_ENTITY]-(c:Chunk)
            WHERE e.id IN $entity_ids
            OPTIONAL MATCH (c)-[:PART_OF]->(d:Document)
            RETURN DISTINCT 
                c.id as chunk_id,
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
                
            logger.info(f"Entity'lerden {len(chunks)} chunk bulundu")
            return chunks
            
        except Exception as e:
            logger.error(f"Chunk arama hatası: {e}")
            return []
    
    def find_chunks_by_graph_pattern(self, pattern_query: str, params: Dict = None, state: AgentState = None) -> List[ChunkInfo]:
        """Graph pattern ile chunk arama"""
        try:
            if params is None:
                params = {}
            if state is None:
                state = AgentState("")
                
            # Pattern query'yi chunk'lara yönlendir
            full_query = f"""
            {pattern_query}
            OPTIONAL MATCH (entity)-[:HAS_ENTITY]-(c:Chunk)
            OPTIONAL MATCH (c)-[:PART_OF]->(d:Document)
            RETURN DISTINCT 
                c.id as chunk_id,
                c.text as text,
                c.page_number as page_number,
                d.fileName as document_name,
                d as document_metadata,
                entity.id as related_entity
            LIMIT {state.max_chunks_limit}
            """
            
            result = self.graph.query(full_query, params)
            
            chunks = []
            for row in result:
                if row['chunk_id']:  # Chunk varsa
                    chunk_info = ChunkInfo(
                        chunk_id=row['chunk_id'],
                        text=row['text'] or "",
                        page_number=row['page_number'],
                        document_name=row['document_name'] or "Unknown",
                        document_metadata=dict(row['document_metadata']) if row['document_metadata'] else {}
                    )
                    chunks.append(chunk_info)
                    
            logger.info(f"Graph pattern ile {len(chunks)} chunk bulundu")
            return chunks
            
        except Exception as e:
            logger.error(f"Graph pattern chunk arama hatası: {e}")
            return []
    
    def calculate_text_relevance(self, chunk_info: ChunkInfo, question: str) -> ChunkInfo:
        """Chunk text'ini böl ve soru ile ilişkililik hesapla - Neo4j chunk embedding kullan"""
        try:
            if not chunk_info.text:
                return chunk_info
                
            # Chunk'ın Neo4j'deki embedding'ini al
            chunk_embedding_query = """
            MATCH (c:Chunk {id: $chunk_id})
            RETURN c.embedding as embedding
            """
            
            result = self.graph.query(chunk_embedding_query, {'chunk_id': chunk_info.chunk_id})
            if not result or not result[0].get('embedding'):
                logger.warning(f"Chunk {chunk_info.chunk_id} için embedding bulunamadı")
                chunk_info.relevance_score = 0.0
                return chunk_info
            
            chunk_embedding = result[0]['embedding']
            
            # Text'i böl
            split_texts = self.text_splitter.split_text(chunk_info.text)
            
            # Kısa metinleri filtrele
            valid_splits = [(i, text) for i, text in enumerate(split_texts) if len(text.strip()) >= 20]
            
            if not valid_splits:
                chunk_info.relevance_score = 0.0
                return chunk_info
            
            # Soru için embedding oluştur
            question_embedding = self.embedding_model.embed_query(normalize_unicode_text(question))
            
            # Chunk'ın mevcut embedding'i ile soru embedding'ini karşılaştır
            chunk_similarity = cosine_similarity([question_embedding], [chunk_embedding])[0][0]
            
            # Split'ler için ayrı ayrı embedding oluştur ve en iyisini bul
            if len(valid_splits) > 1:
                split_embeddings = self.embedding_model.embed_documents([
                    normalize_unicode_text(text) for _, text in valid_splits
                ])
                scores = []
                for split_embedding in split_embeddings:
                    similarity = cosine_similarity(
                        [question_embedding], 
                        [split_embedding]
                    )[0][0]
                    scores.append(float(similarity))
            else:
                # Tek split varsa direkt hesapla
                _, split_text = valid_splits[0]
                split_embedding = self.embedding_model.embed_query(normalize_unicode_text(split_text))
                scores = [cosine_similarity([question_embedding], [split_embedding])[0][0]]
            
            # En yüksek 3 score'u al
            scored_splits = [(valid_splits[i][1], scores[i]) for i in range(len(scores))]
            scored_splits.sort(key=lambda x: x[1], reverse=True)
            
            # Threshold üzerindeki ilk 3'ü al
            chunk_info.split_texts = [text for text, score in scored_splits[:3] if score > 0.1]
            chunk_info.split_scores = [score for text, score in scored_splits[:3] if score > 0.1]
            
            # Chunk embedding similarity ile split similarity'lerini birleştir
            max_split_score = max(scores) if scores else 0.0
            chunk_info.relevance_score = max(chunk_similarity, max_split_score)
            
            logger.debug(f"Chunk {chunk_info.chunk_id} relevance: {chunk_info.relevance_score:.3f} (chunk: {chunk_similarity:.3f}, max_split: {max_split_score:.3f})")
            return chunk_info
            
        except Exception as e:
            logger.error(f"Text relevance hesaplama hatası: {e}")
            chunk_info.relevance_score = 0.0
            return chunk_info
    
    def vector_search_with_chunks(self, query_text: str, state: AgentState, limit: int = 10) -> List[ChunkInfo]:
        """Vector search yap ve chunk bilgilerini döndür - Neo4j chunk embedding kullan"""
        try:
            logger.info(f"Vector search: {query_text}")
            normalized_query = normalize_unicode_text(query_text)
            query_embedding = self.embedding_model.embed_query(normalized_query)
            
            # Neo4j vector index'ini kullanarak chunk embedding'leri ile karşılaştır
            vector_query = """
            CALL db.index.vector.queryNodes('vector', $limit, $query_vector) 
            YIELD node, score
            OPTIONAL MATCH (node)-[:PART_OF]->(d:Document)
            RETURN 
                node.id as chunk_id,
                node.text as text, 
                node.page_number as page_number,
                node.embedding as chunk_embedding,
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
            
            # Chunk bilgilerini hazırla
            chunks = []
            for row in result:
                chunk_info = ChunkInfo(
                    chunk_id=row['chunk_id'],
                    text=row['text'] or "",
                    page_number=row['page_number'],
                    document_name=row['document_name'] or "Unknown",
                    document_metadata=dict(row['document_metadata']) if row['document_metadata'] else {},
                    relevance_score=float(row['score'])  # Neo4j vector search score'unu kullan
                )
                chunks.append(chunk_info)
            
            # Artık text relevance hesaplama yapmıyoruz çünkü Neo4j vector search'ü chunk embedding'leri kullanıyor
            logger.info(f"Vector search'ten {len(chunks)} chunk alındı (Neo4j chunk embeddings kullanıldı)")
                    
            # Relevance'a göre sırala ve döndür (zaten sıralı ama emin olmak için)
            chunks.sort(key=lambda x: x.relevance_score, reverse=True)
            
            logger.info(f"Vector search sonucu: {len(chunks)} chunk")
            return chunks
            
        except Exception as e:
            logger.error(f"Vector search hatası: {e}")
            return []

    def parse_agent_response(self, response: str) -> Tuple[str, str, str]:
        """Agent cevabını parse et"""
        # Observation, Thought, Action'ı ayır
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
            elif line.startswith('Query:'):
                action_content = line.replace('Query:', '').strip()
            elif line.startswith('Answer:'):
                action_content = line.replace('Answer:', '').strip()
            elif line.startswith('Content:'):
                # "Content:" prefix'ini kaldır ve action_content'e ekle
                action_content = line.replace('Content:', '').strip()
            elif current_section and line:
                if current_section == 'observation':
                    observation += ' ' + line
                elif current_section == 'thought':
                    thought += ' ' + line
                elif current_section == 'action':
                    if not action:
                        action = line
                    else:
                        # Eğer line "Content:" ile başlamıyorsa action_content'e ekle
                        if not line.startswith('Content:'):
                            action_content += ' ' + line
        
        return observation.strip(), thought.strip(), (action.strip(), action_content.strip())
    
    def solve_question(self, user_question: str) -> Dict[str, Any]:
        """Ana problem çözme fonksiyonu - ReAct pattern ile Chunk-based arama"""
        
        logger.info(f"Soru çözülüyor: {user_question}")
        
        # Agent state'i başlat
        state = AgentState(question=user_question)
        
        # Schema bilgilerini al
        schema = self.get_neo4j_schema()
        system_prompt = self.create_enhanced_system_prompt(schema)
        
        conversation_history = []
        final_answer = None
        
        # İlk observation
        current_observation = f"Kullanıcı sorusu: '{user_question}'"
        
        while state.iteration_count < self.max_iterations and not final_answer:
            state.iteration_count += 1
            logger.info(f"İterasyon {state.iteration_count}")
            
            # LLM'e gönderilecek mesaj - mevcut state bilgileriyle zenginleştir
            context_info = ""
            if state.discovered_chunks:
                context_info = f"\n\nMevcut Durum:\n- {len(state.discovered_chunks)} chunk keşfedildi\n- En yüksek relevance: {max([c.relevance_score for c in state.discovered_chunks]):.3f}\n- Toplanan dokümalar: {list(set([c.document_name for c in state.discovered_chunks]))}"
            
            prompt = f"{current_observation}{context_info}\n\nBu duruma göre next action'ını belirle:"
            
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=prompt)
            ]
            
            # Conversation history ekle
            for entry in conversation_history[-4:]:  # Son 4 adımı tut
                messages.append(HumanMessage(content=entry))
            
            try:
                response = self.llm.invoke(messages)
                agent_response = response.content
                
                logger.info(f"Agent response: {agent_response[:200]}...")
                
                # Response'u parse et
                observation, thought, (action, action_content) = self.parse_agent_response(agent_response)
                
                logger.info(f"Parse edildi - Action: '{action}', Content: '{action_content[:50]}...'")
                
                conversation_history.append(f"Thought: {thought}\nAction: {action}\nContent: {action_content}")
                
                # Action'ı uygula
                if action == "final_answer":
                    # Final answer için context ekle
                    context = state.get_context_for_llm()
                    final_answer = f"{action_content}\n\n### Kaynak Bilgileri:\n{context}"
                    break
                    
                elif action == "entity_search":
                    # Yeni entity-driven arama
                    chunks = self.entity_driven_search(action_content, state)
                    for chunk in chunks:
                        state.add_chunk(chunk)
                    
                    if chunks:
                        current_observation = f"Entity-driven search: {len(chunks)} chunk bulundu. En yüksek relevance: {max([c.relevance_score for c in chunks]):.3f}. Toplam chunk: {len(state.discovered_chunks)}"
                    else:
                        current_observation = f"'{action_content}' için entity bulunamadı. Farklı anahtar kelime dene."
                        
                elif action == "cypher_query":
                    success, result = self.execute_cypher_query(action_content)
                    if success and result:
                        # Sonuçlardan entity'leri çıkar ve chunk'lara ulaş
                        entity_ids = []
                        for row in result[:20]:  # İlk 20 sonuç
                            for key, value in row.items():
                                if key.endswith('_id') or key in ['id', 'name']:
                                    if isinstance(value, str) and len(value) > 0:
                                        entity_ids.append(value)
                                        state.discovered_entities.append({key: value})
                        
                        if entity_ids:
                            # Entity'lerden chunk'lara ulaş
                            chunks = self.find_chunks_from_entities(entity_ids, state)
                            for chunk in chunks:
                                chunk = self.calculate_text_relevance(chunk, user_question)
                                state.add_chunk(chunk)
                            
                            current_observation = f"Cypher sorgusu başarılı. {len(result)} sonuç bulundu. {len(chunks)} yeni chunk keşfedildi. Toplam chunk: {len(state.discovered_chunks)}"
                        else:
                            current_observation = f"Cypher sorgusu başarılı ama entity bulunamadı. Sonuç: {result[:2]}"
                    else:
                        current_observation = f"Cypher sorgusu başarısız: {result}. Farklı bir sorgu dene."
                        
                elif action == "vector_search":
                    chunks = self.vector_search_with_chunks(action_content, state)
                    for chunk in chunks:
                        state.add_chunk(chunk)
                    
                    if chunks:
                        current_observation = f"Vector search sonucu: {len(chunks)} chunk bulundu. En yüksek relevance: {max([c.relevance_score for c in chunks]):.3f}. Toplam chunk: {len(state.discovered_chunks)}"
                    else:
                        current_observation = "Vector search sonuç bulamadı."
                
                elif action == "graph_pattern_search":
                    # Yeni action tipi: Graph pattern ile arama
                    try:
                        chunks = self.find_chunks_by_graph_pattern(action_content, {}, state)
                        for chunk in chunks:
                            chunk = self.calculate_text_relevance(chunk, user_question)
                            state.add_chunk(chunk)
                        
                        current_observation = f"Graph pattern search: {len(chunks)} chunk bulundu. Toplam chunk: {len(state.discovered_chunks)}"
                    except Exception as e:
                        current_observation = f"Graph pattern search hatası: {e}"
                        
                else:
                    current_observation = f"Bilinmeyen action: {action}. Geçerli action'lar: entity_search, cypher_query, vector_search, graph_pattern_search, final_answer"
                
                # Chunk limit kontrolü
                if len(state.discovered_chunks) >= state.max_chunks_limit:
                    current_observation += f" (Chunk limiti {state.max_chunks_limit} aşıldı, artık yeni chunk aranmayacak)"
                
            except Exception as e:
                logger.error(f"İterasyon {state.iteration_count} hatası: {e}")
                current_observation = f"Hata oluştu: {e}. Farklı bir yaklaşım dene."
        
        # Sonuç döndür
        if not final_answer:
            context = state.get_context_for_llm()
            if context:
                final_answer = f"Mevcut bilgilerle tam bir cevap veremiyorum ama bulduğum bilgiler:\n\n{context}"
            else:
                final_answer = "Maalesef sorunuza cevap bulamadım. Lütfen sorunuzu farklı şekilde ifade edin."
        
        return {
            "answer": final_answer,
            "iterations": state.iteration_count,
            "conversation_history": conversation_history,
            "discovered_chunks": len(state.discovered_chunks),
            "discovered_entities": len(state.discovered_entities),
            "chunk_details": [
                {
                    "document": c.document_name,
                    "page": c.page_number,
                    "relevance": c.relevance_score,
                    "preview": c.text[:100] + "..."
                } for c in sorted(state.discovered_chunks, key=lambda x: x.relevance_score, reverse=True)[:5]
            ],
            "schema_info": schema
        }
            
    def create_enhanced_system_prompt(self, schema: Dict[str, Any]) -> str:
        """Gelişmiş system prompt'u schema bilgileriyle oluştur"""
        
        schema_text = f"""
## Neo4j Veritabanı Schema Bilgileri

### Mevcut Node Labels:
{', '.join(schema['node_labels'])}

### Mevcut Relationship Types:
{', '.join(schema['relationship_types'])}

### Node Properties (örnek):
"""
        
        for label, props in schema['node_properties'].items():
            schema_text += f"\n- {label}: {', '.join(props)}"
        
        schema_text += "\n\n### Örnek İlişkiler:\n"
        for rel in schema['sample_relationships']:
            schema_text += f"- ({rel['from_label']})-[:{rel['rel_type']}]->({rel['to_label']})\n"
        
        system_prompt = f"""Sen bir Neo4j veritabanında çalışan AKILLI BİLGİ ARAŞTIRMACISI ve VERİ MADENCİSİ'sin.
Kullanıcı sorusuna göre mevcut schema'yı analiz ederek en uygun entity'leri bulur, chunk'lara ulaşır ve doğru bilgiyi çıkarırsın.

{schema_text}

## ZORUNLU BAŞLAMA KURALI:
**HER SORU İÇİN İLK AKSİYON MUTLAKA 'entity_search' OLMALIDIR!**
**BAŞKA HİÇBİR AKSİYON KULLANMADAN ÖNCE entity_search YAPMAK ZORUNLUDUR!**

## BİLGİ ARAŞTIRMA SÜRECİ:

### ADIM 1: ZORUNLU ENTITY SEARCH
- MUTLAKA ilk adımda entity_search yap
- Sorudaki anahtar kelimeleri kullan
- Kişi isimleri, şirket isimleri, ürün isimleri ile başla
- Örnek: Soru "Ayça hanımın..." ise: entity_search → "Ayça"

### ADIM 2: ENTITY KEŞFİ STRATEJİSİ
- Schema'daki node türlerini sırayla dene:
  1. Anahtar kavramları içeren entity'leri ara
  2. Farklı node türlerinde benzer terimleri ara
  3. İlişkili entity'leri keşfet (relationship'ler boyunca)
- Her denemeyi değerlendir: "Bu entity'ler soruyla ilgili mi?"

### ADIM 3: CHUNK KEŞFİ VE EMBEDDİNG ANALİZİ  
- Bulunan entity'lerden chunk'lara ulaş
- Chunk embedding'lerini soru embedding'i ile karşılaştır
- Yüksek relevance score'lu chunk'ları öncelikle analiz et
- Chunk text'lerini detaylı incele

### ADIM 4: BİLGİ ÇIKARMA VE DOĞRULAMa
- Chunk'lardaki text'lerden soruyla ilgili bilgileri çıkar
- Sayısal veri arıyorsan: rakamları, miktarları bul
- İsim arıyorsan: kişi, yer, kurum isimlerini bul  
- Tarih arıyorsan: zaman bilgilerini bul
- Bulunan bilgiyi doğrula: "Bu bilgi soruyu tam karşılıyor mu?"

### ADIM 5: İTERATİF GELİŞTİRME
- Yeterli bilgi yoksa: farklı node türlerini dene
- Eksik bilgi varsa: ilişkili entity'leri ara  
- Belirsizlik varsa: daha spesifik query'ler yaz
- "Soruyu tam cevaplayabilir miyim?" kendine sor

## ACTION TÜRLERI:

### entity_search - ZORUNLU İLK ADIM
- Sorudaki anahtar kelimeyi ara
- __Entity__ node'larında arama yapar
- İlgili chunk'ları getirir
- Örnek: Action: entity_search, Content: Ayça

### cypher_query
- Neo4j schema'sına göre optimal query'ler yaz
- Farklı node türlerini sistematik olarak dene
- Relationship'leri kullanarak ilişkili entity'leri keşfet
- Örnek: MATCH (n:NodeType) WHERE n.property CONTAINS "anahtar_kelime" RETURN n

### vector_search  
- Soruyla semantik olarak benzer chunk'ları bul
- Doğrudan text-based arama yap
- Örnek: "2020 yılı poliçe bilgileri"

### final_answer
- Yeterli bilgi toplandığında sonucu ver
- Kaynak chunk'ları belirt
- Güven seviyeni belirt

## ÖRNek BİLGİ ARAŞTIRMA AKIŞI:

**Soru: "2020 yılında kaç adet X türü Y var?"**

1. **Schema Analysis**: PolicyType, Policy, Customer, Year node'larına bak
2. **Entity Search**: 
   - MATCH (pt:PolicyType) WHERE pt.id CONTAINS "X" → X türü pol
   - MATCH (y:Year) WHERE y.id = "2020" → 2020 yılı
3. **Relationship Exploration**:
   - MATCH (policy)-[:OF_TYPE]→(pt) → Poliçe türü bağlantısı
   - MATCH (policy)-[:FOR_YEAR]→(y) → Yıl bağlantısı  
4. **Combined Query**:
   - Hem tür hem yıl şartını sağlayan poliçeleri say
5. **Chunk Analysis**: 
   - Bulunan entity'lerden chunk'lara git
   - Chunk text'lerinde sayısal veri ara
6. **Result**: Kesin sayı ver

## ACTION FORMAT:
Observation: Mevcut durumu gözlemle
Thought: Hangi stratejiyi kullanacağını düşün
Action: cypher_query
Query: MATCH (pt:PolicyType) RETURN count(DISTINCT pt.id) as policy_type_count

VEYA

Observation: Vector search sonuçlarını değerlendir
Thought: Chunk'larda yeterli bilgi var mı kontrol et
Action: final_answer
Answer: Topladığım bilgilere göre cevap

KURALLAR:
- Her adımda "Bu bilgi soruyla ilgili mi?" diye sorgula
- Schema'daki TÜM node türlerini sistematik olarak dene
- Relationship'leri aktif kullan (bağlantıları takip et)
- Chunk relevance score'larına dikkat et (>0.3 iyi)
- En az 3-5 farklı query stratejisi dene
- Maksimum 10 iterasyon (derinlemesine araştırma)

Şimdi verilen soruyu bilimsel bir araştırmacı gibi incele ve adım adım çöz."""

        return system_prompt

def test_agent():
    """Test fonksiyonu"""
    from langchain_neo4j import Neo4jGraph
    
    # Neo4j bağlantısı - server environment'tan al
    graph = Neo4jGraph(
        url=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        username=os.getenv("NEO4J_USERNAME", "neo4j"),
        password=os.getenv("NEO4J_PASSWORD", "qwerty5555"),
        database=os.getenv("NEO4J_DATABASE", "neo4j")
    )
    
    # Agent'ı oluştur
    agent = IntelligentAgent(graph)
    
    # Test soruları
    test_questions = [
        "Ayça hanımın 2020 yılında kaç adet poliçesi var?",
        "Sistemde hangi poliçe türleri mevcut?",
        "DASK poliçeleri hakkında ne tür bilgiler var?",
        "Galata Residence ile ilgili hangi bilgiler mevcut?"
    ]
    
    for question in test_questions:
        print(f"\n{'='*60}")
        print(f"SORU: {question}")
        print('='*60)
        
        result = agent.solve_question(question)
        
        print(f"CEVAP: {result['answer'][:500]}...")
        print(f"İTERASYON: {result['iterations']}")
        print(f"CHUNK SAYISI: {result['discovered_chunks']}")
        print(f"ENTITY SAYISI: {result['discovered_entities']}")
        
        print("\nEN İLGİLİ CHUNK'LAR:")
        for chunk in result['chunk_details']:
            print(f"- {chunk['document']} (Sayfa {chunk['page']}) - Relevance: {chunk['relevance']:.3f}")
            print(f"  {chunk['preview']}")

if __name__ == "__main__":
    test_agent()
