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
    Chunk-based arama ve text splitting ile geliştirilmiş
    """
    
    def __init__(self, graph: Neo4jGraph, model_name: str = "openai_gpt_4o"):
        self.graph = graph
        self.llm, _ = get_llm(model_name)
        self.embedding_model, _ = load_embedding_model("openai")
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
        
        system_prompt = f"""Sen Neo4j veritabanında bilgi arayan akıllı bir agent'sın. ReAct pattern kullanarak çalışıyorsun.

{schema_text}

## Çalışma Şeklin:
1. **Observation**: Mevcut durumu gözlemle
2. **Thought**: Ne yapman gerektiğini düşün
3. **Action**: Bir eylem belirle (cypher_query, vector_search, veya final_answer)
4. **Result**: Eylemin sonucunu değerlendir

## Kurallar:
- İlk olarak basit Cypher sorguları dene
- Başarısızsa farklı node'lar veya ilişkiler dene
- Eğer hiç sonuç bulamazsan vector search kullan
- Her adımda açık düşüncelerini belirt
- Türkçe karakterleri normalize et
- Maksimum 5 iterasyon

## Eylem Formatı:
Action: cypher_query
Query: MATCH (n:NodeType) WHERE n.property = "value" RETURN n

veya

Action: vector_search
Query: semantic arama metni

veya

Action: final_answer
Answer: Kullanıcıya vereceğin final cevap

Şimdi kullanıcının sorusunu analiz et ve adım adım çöz."""

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
        """Chunk text'ini böl ve soru ile ilişkililik hesapla - optimize edilmiş"""
        try:
            if not chunk_info.text:
                return chunk_info
                
            # Text'i böl
            split_texts = self.text_splitter.split_text(chunk_info.text)
            
            # Kısa metinleri filtrele
            valid_splits = [(i, text) for i, text in enumerate(split_texts) if len(text.strip()) >= 20]
            
            if not valid_splits:
                chunk_info.relevance_score = 0.0
                return chunk_info
            
            # Batch embedding - tek seferde tüm split'ler için
            question_embedding = self.embedding_model.embed_query(normalize_unicode_text(question))
            
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
            chunk_info.relevance_score = max(scores) if scores else 0.0
            
            logger.debug(f"Chunk {chunk_info.chunk_id} relevance: {chunk_info.relevance_score:.3f}")
            return chunk_info
            
        except Exception as e:
            logger.error(f"Text relevance hesaplama hatası: {e}")
            chunk_info.relevance_score = 0.0
            return chunk_info
    
    def vector_search_with_chunks(self, query_text: str, state: AgentState, limit: int = 10) -> List[ChunkInfo]:
        """Vector search yap ve chunk bilgilerini döndür - optimize edilmiş"""
        try:
            logger.info(f"Vector search: {query_text}")
            normalized_query = normalize_unicode_text(query_text)
            query_embedding = self.embedding_model.embed_query(normalized_query)
            
            vector_query = """
            CALL db.index.vector.queryNodes('vector', $limit, $query_vector) 
            YIELD node, score
            OPTIONAL MATCH (node)-[:PART_OF]->(d:Document)
            RETURN 
                node.id as chunk_id,
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
            
            # Chunk bilgilerini hazırla
            chunks = []
            for row in result:
                chunk_info = ChunkInfo(
                    chunk_id=row['chunk_id'],
                    text=row['text'] or "",
                    page_number=row['page_number'],
                    document_name=row['document_name'] or "Unknown",
                    document_metadata=dict(row['document_metadata']) if row['document_metadata'] else {},
                    relevance_score=float(row['score'])
                )
                chunks.append(chunk_info)
            
            # Batch text processing - tüm chunk'ları tek seferde işle
            logger.info(f"Vector search'ten {len(chunks)} chunk alındı, text relevance hesaplanıyor...")
            for chunk_info in chunks:
                if chunk_info.text and len(chunk_info.text.strip()) >= 20:
                    chunk_info = self.calculate_text_relevance(chunk_info, query_text)
                    
            # Relevance'a göre sırala ve döndür
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
                
                conversation_history.append(f"Thought: {thought}\nAction: {action}\nContent: {action_content}")
                
                # Action'ı uygula
                if action == "final_answer":
                    # Final answer için context ekle
                    context = state.get_context_for_llm()
                    final_answer = f"{action_content}\n\n### Kaynak Bilgileri:\n{context}"
                    break
                    
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
                    current_observation = f"Bilinmeyen action: {action}. Geçerli action'lar: cypher_query, vector_search, graph_pattern_search, final_answer"
                
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
        
        system_prompt = f"""Sen Neo4j veritabanında bilgi arayan akıllı bir agent'sın. ReAct pattern kullanarak çalışıyorsun.

{schema_text}

## ÖNEMLİ: Chunk-Based Arama Sistemi
- Her sorgu entity'lere ulaşır, entity'ler chunk'lara bağlıdır (HAS_ENTITY ilişkisi)
- Chunk'lar document'lara bağlıdır (PART_OF ilişkisi)
- Her chunk'ın page_number bilgisi vardır
- Chunk text'leri küçük parçalara bölünür ve soru ile ilişkililik hesaplanır

## Çalışma Şeklin:
1. **Observation**: Mevcut durumu gözlemle (kaç chunk bulundu, relevance skorları)
2. **Thought**: Ne yapman gerektiğini düşün (hangi entity'leri ara, hangi pattern'i kullan)
3. **Action**: Bir eylem belirle
4. **Result**: Eylemin sonucunu değerlendir

## Action Türleri:
### cypher_query
- Entity'leri bul, chunk'lara ulaşmak için kullan
- Örnek: MATCH (p:Person) WHERE p.id CONTAINS "AYÇA" RETURN p

### vector_search  
- Semantic arama yap, doğrudan chunk'lara ulaş
- Örnek: Ayça hanımın poliçe bilgileri

### graph_pattern_search
- Karmaşık graph pattern'ları için
- Entity'den chunk'a giden query yazma

### final_answer
- Toplanan bilgilerle final cevabı ver

## Kurallar:
- İlk olarak basit Cypher ile entity bul
- Entity'lerden chunk'lara ulaş
- Chunk sayısı arttıkça daha iyi sonuç alırsın
- Relevance score'lara dikkat et (>0.3 iyi sayılır)
- En az 3-5 chunk toplamaya çalış
- Türkçe karakterleri normalize et
- Maksimum 8 iterasyon

## Eylem Formatı:
Action: cypher_query
Query: MATCH (p:Person) WHERE p.id CONTAINS "AYÇA" RETURN p.id

veya

Action: vector_search
Query: Ayça hanımın 2020 yılı poliçeleri

veya

Action: final_answer
Answer: Toplanan bilgilere dayanarak final cevap

Şimdi kullanıcının sorusunu analiz et ve chunk'ları keşfetmeye başla."""

        return system_prompt

def test_agent():
    """Test fonksiyonu"""
    from langchain_neo4j import Neo4jGraph
    
    # Neo4j bağlantısı - server environment'tan al
    graph = Neo4jGraph(
        url=os.getenv("NEO4J_URI_SERVER", "bolt://3.76.55.209:7687"),
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
