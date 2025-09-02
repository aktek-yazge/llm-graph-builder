#!/usr/bin/env python3
"""
Intelligent Agent V2 - Enhanced ReAct Pattern Agent
intelligent_agent.py'den esinlenerek geliştirilmiş, token-efficient, 
schema-aware, context-memory özellikli agent
"""

import os
import logging
import json
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_neo4j import Neo4jGraph
from langchain_core.messages import HumanMessage, SystemMessage
import numpy as np
from collections import defaultdict

# Load environment variables
load_dotenv()

# Logging konfigürasyonu
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@dataclass
class ChunkData:
    """Chunk bilgilerini saklar"""
    document_name: str
    page_number: int
    text: str
    chunk_id: str
    # embedding: Optional[List[float]] = None  # Token tasarrufu için kaldırıldı
    relevance_score: float = 0.0
    source_entities: List[str] = None
    
    def __post_init__(self):
        if self.source_entities is None:
            self.source_entities = []

@dataclass
class AgentState:
    """Agent'ın durumsal bilgilerini yönetir"""
    iteration_count: int = 0
    discovered_chunks: List[ChunkData] = None
    discovered_entities: List[Dict[str, Any]] = None
    context_memory: str = ""
    max_chunks_limit: int = 8  # Daha konservatif limit
    max_iterations: int = 8    # Daha az iteration
    relevance_threshold: float = 0.25  # Relevance eşiği
    
    def __post_init__(self):
        if self.discovered_chunks is None:
            self.discovered_chunks = []
        if self.discovered_entities is None:
            self.discovered_entities = []
    
    def add_chunk(self, chunk: ChunkData):
        """Chunk'ı relevance threshold kontrolü ile ekle"""
        if chunk.relevance_score >= self.relevance_threshold:
            # Duplicate kontrolü
            for existing_chunk in self.discovered_chunks:
                if (existing_chunk.document_name == chunk.document_name and 
                    existing_chunk.page_number == chunk.page_number and
                    existing_chunk.text[:100] == chunk.text[:100]):
                    return  # Duplicate, ekleme
            
            self.discovered_chunks.append(chunk)
            logger.info(f"Chunk eklendi: {chunk.document_name} P{chunk.page_number} (Relevance: {chunk.relevance_score:.3f})")
        else:
            logger.info(f"Chunk filtrelendi (düşük relevance): {chunk.relevance_score:.3f} < {self.relevance_threshold}")

class IntelligentAgentV2:
    """Enhanced ReAct pattern agent with optimizations"""
    
    def __init__(self, neo4j_graph: Neo4jGraph):
        self.graph = neo4j_graph
        self.llm = ChatOpenAI(
            model="gpt-4o",
            temperature=0,
            max_tokens=2000  # Daha konservatif token limit
        )
        # self.embeddings = OpenAIEmbeddings()  # Kullanılmıyor, token tasarrufu için kaldırıldı
        
        # Token tracking
        self.token_usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0
        }
        
        # Successful findings tracking
        self.successful_findings = []
        
        # Entity cache for efficiency
        self.entity_cache = {}
        
        # Context memory
        self.context_memory = ""
        
    def track_tokens(self, response):
        """Token kullanımını takip et"""
        if hasattr(response, 'usage'):
            usage = response.usage
            self.token_usage["input_tokens"] += usage.prompt_tokens
            self.token_usage["output_tokens"] += usage.completion_tokens
            self.token_usage["total_tokens"] += usage.total_tokens
    
    def add_successful_finding(self, iteration: int, action: str, finding: str, relevance: float):
        """Başarılı bulguları kaydet"""
        self.successful_findings.append({
            "iteration": iteration,
            "action": action,
            "finding": finding,
            "relevance_score": relevance,
            "timestamp": datetime.now().strftime("%H:%M:%S")
        })
    
    def get_graph_schema(self) -> Dict[str, Any]:
        """Neo4j schema bilgilerini al - her çağrıda güncel veri"""
        try:
            schema = {
                'node_labels': [],
                'relationship_types': [],
                'node_properties': defaultdict(list),
                'sample_relationships': [],
                'sample_nodes': []
            }
            
            # Node labels
            result = self.graph.query("CALL db.labels() YIELD label RETURN label")
            schema['node_labels'] = [row['label'] for row in result]
            
            # Relationship types
            result = self.graph.query("CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType")
            schema['relationship_types'] = [row['relationshipType'] for row in result]
            
            # Node properties - her label için
            for label in schema['node_labels'][:10]:  # İlk 10 label
                try:
                    prop_query = f"""
                        MATCH (n:`{label}`) 
                        WITH keys(n) as props 
                        UNWIND props as prop 
                        RETURN DISTINCT prop 
                        LIMIT 5
                    """
                    props_result = self.graph.query(prop_query)
                    schema['node_properties'][label] = [row['prop'] for row in props_result]
                except:
                    schema['node_properties'][label] = []
            
            # Örnek relationships
            result = self.graph.query("""
                MATCH (a)-[r]->(b) 
                RETURN DISTINCT labels(a)[0] as from_label, type(r) as rel_type, labels(b)[0] as to_label 
                LIMIT 15
            """)
            schema['sample_relationships'] = result
            
            # Örnek node'lar - her label'dan - embedding'leri filtrele
            for label in schema['node_labels'][:5]:  # İlk 5 label
                try:
                    sample_query = f"""
                        MATCH (n:`{label}`) 
                        RETURN n 
                        LIMIT 3
                    """
                    sample_result = self.graph.query(sample_query)
                    for row in sample_result:
                        node_props = dict(row['n'])
                        # Embedding field'larını filtrele
                        filtered_props = {}
                        for key, value in node_props.items():
                            if key.lower() not in ['embedding', 'embeddings', 'vector', 'vectors']:
                                # Çok uzun değerleri kısalt
                                if isinstance(value, str) and len(value) > 100:
                                    filtered_props[key] = value[:100] + "..."
                                else:
                                    filtered_props[key] = value
                        
                        schema['sample_nodes'].append({
                            'label': label, 
                            'properties': filtered_props
                        })
                except:
                    pass
            
            logger.info(f"Schema alındı: {len(schema['node_labels'])} label, {len(schema['relationship_types'])} rel type")
            return schema
            
        except Exception as e:
            logger.error(f"Schema alınamadı: {e}")
            return {'node_labels': [], 'relationship_types': [], 'node_properties': {}, 'sample_relationships': [], 'sample_nodes': []}
    
    def calculate_text_relevance(self, chunk: ChunkData, user_question: str) -> ChunkData:
        """Text-based relevance hesapla"""
        try:
            # Basit keyword matching
            question_words = user_question.lower().split()
            chunk_text = chunk.text.lower()
            
            # Keyword overlap score
            keyword_score = 0
            for word in question_words:
                if len(word) > 2:  # Kısa kelimeleri atla
                    if word in chunk_text:
                        keyword_score += 1
            
            # Normalize
            relevance = min(keyword_score / len(question_words), 1.0) if question_words else 0
            
            # Bonus for proper nouns, numbers, dates
            import re
            if re.search(r'\b[A-ZÇĞIİÖŞÜ][a-zçğıöşü]+', chunk.text):  # Proper nouns
                relevance += 0.1
            if re.search(r'\d+', chunk.text):  # Numbers
                relevance += 0.1
            if re.search(r'\d{4}', chunk.text):  # Years
                relevance += 0.1
                
            chunk.relevance_score = min(relevance, 1.0)
            return chunk
            
        except Exception as e:
            logger.error(f"Relevance hesaplama hatası: {e}")
            chunk.relevance_score = 0.0
            return chunk
    
    def entity_search(self, search_term: str, state: AgentState) -> List[ChunkData]:
        """Entity-driven search - gerçek schema'ya uygun"""
        try:
            # Cache kontrolü
            cache_key = f"entity_{search_term}"
            if cache_key in self.entity_cache:
                logger.info(f"Entity cache hit: {search_term}")
                return self.entity_cache[cache_key]
            
            # Güncel schema'yı al
            schema = self.get_graph_schema()
            chunks = []
            
            # Her node label'ında arama yap
            for label in schema['node_labels']:
                try:
                    # Bu label'ın property'lerini al
                    props = schema['node_properties'].get(label, [])
                    
                    # Text property'leri için arama query'si oluştur
                    search_conditions = []
                    for prop in props:
                        if prop and isinstance(prop, str):
                            search_conditions.append(f"toLower(toString(n.{prop})) CONTAINS toLower($search_term)")
                    
                    if search_conditions:
                        query = f"""
                            MATCH (n:`{label}`)
                            WHERE {' OR '.join(search_conditions)}
                            RETURN n, '{label}' as node_label
                            LIMIT 5
                        """
                        
                        result = self.graph.query(query, {"search_term": search_term})
                        
                        # Sonuçları chunk'a dönüştür
                        for row in result:
                            node_data = dict(row['n'])
                            node_label = row['node_label']
                            
                            # Node'un text representation'ını oluştur - embedding'leri filtrele
                            text_parts = []
                            for key, value in node_data.items():
                                # Embedding field'larını atla - token tasarrufu için
                                if key.lower() in ['embedding', 'embeddings', 'vector', 'vectors']:
                                    continue
                                if value and str(value).strip():
                                    # Çok uzun değerleri kısalt
                                    value_str = str(value)
                                    if len(value_str) > 200:
                                        value_str = value_str[:200] + "..."
                                    text_parts.append(f"{key}: {value_str}")
                            
                            if text_parts:
                                chunk = ChunkData(
                                    document_name=f"{node_label}_node",
                                    page_number=1,
                                    text=f"[{node_label}] " + ", ".join(text_parts),
                                    chunk_id=f"{node_label}_{node_data.get('id', hash(str(node_data)))}"
                                )
                                
                                # Relevance hesapla
                                chunk = self.calculate_text_relevance(chunk, search_term)
                                if chunk.relevance_score > 0:
                                    chunks.append(chunk)
                                
                except Exception as e:
                    logger.warning(f"Label {label} aramasında hata: {e}")
                    continue
            
            # Cache'le
            self.entity_cache[cache_key] = chunks
            logger.info(f"Entity search '{search_term}': {len(chunks)} chunk bulundu")
            return chunks
            
        except Exception as e:
            logger.error(f"Entity search hatası: {e}")
            return []
    
    def execute_cypher_query(self, cypher_query: str) -> Tuple[bool, Any]:
        """Cypher query'i çalıştır - syntax kontrollü"""
        try:
            # Basit syntax kontrolü
            if not cypher_query.strip().upper().startswith(('MATCH', 'OPTIONAL MATCH', 'WITH', 'RETURN', 'CALL')):
                return False, "Invalid Cypher syntax: must start with MATCH, OPTIONAL MATCH, WITH, RETURN, or CALL"
            
            result = self.graph.query(cypher_query)
            return True, result
            
        except Exception as e:
            logger.error(f"Cypher query hatası: {e}")
            return False, str(e)
    
    def vector_search(self, search_text: str, state: AgentState) -> List[ChunkData]:
        """Vector-based semantic search - gerçek schema'ya uygun"""
        try:
            # Cache kontrolü
            cache_key = f"vector_{search_text}"
            if cache_key in self.entity_cache:
                logger.info(f"Vector cache hit: {search_text}")
                return self.entity_cache[cache_key]
            
            # Güncel schema'yı al
            schema = self.get_graph_schema()
            chunks = []
            
            # Text içeren node'larda genel arama
            for label in schema['node_labels']:
                try:
                    props = schema['node_properties'].get(label, [])
                    
                    # Text property'leri için arama
                    search_conditions = []
                    for prop in props:
                        if prop and isinstance(prop, str):
                            search_conditions.append(f"toLower(toString(n.{prop})) CONTAINS toLower($search_text)")
                    
                    if search_conditions:
                        query = f"""
                            MATCH (n:`{label}`)
                            WHERE {' OR '.join(search_conditions)}
                            RETURN n, '{label}' as node_label
                            ORDER BY size(toString(n)) DESC
                            LIMIT 8
                        """
                        
                        result = self.graph.query(query, {"search_text": search_text})
                        
                        for row in result:
                            node_data = dict(row['n'])
                            node_label = row['node_label']
                            
                            # Node'un text representation'ını oluştur - embedding'leri filtrele
                            text_parts = []
                            for key, value in node_data.items():
                                # Embedding field'larını atla - token tasarrufu için
                                if key.lower() in ['embedding', 'embeddings', 'vector', 'vectors']:
                                    continue
                                if value and str(value).strip():
                                    # Çok uzun değerleri kısalt
                                    value_str = str(value)
                                    if len(value_str) > 200:
                                        value_str = value_str[:200] + "..."
                                    text_parts.append(f"{key}: {value_str}")
                            
                            if text_parts:
                                chunk = ChunkData(
                                    document_name=f"{node_label}_search",
                                    page_number=1,
                                    text=f"[{node_label}] " + ", ".join(text_parts),
                                    chunk_id=f"search_{node_label}_{hash(str(node_data))}"
                                )
                                
                                # Relevance hesapla
                                chunk = self.calculate_text_relevance(chunk, search_text)
                                if chunk.relevance_score > 0:
                                    chunks.append(chunk)
                                
                except Exception as e:
                    logger.warning(f"Label {label} vector search'te hata: {e}")
                    continue
            
            # Cache'le
            self.entity_cache[cache_key] = chunks
            logger.info(f"Vector search '{search_text}': {len(chunks)} chunk bulundu")
            return chunks
            
        except Exception as e:
            logger.error(f"Vector search hatası: {e}")
            return []
    
    def should_stop_search(self, state: AgentState, user_question: str) -> bool:
        """Arama durdurma koşulları - intelligent_agent.py'den esinlenildi"""
        
        # Chunk limit aşıldı
        if len(state.discovered_chunks) >= state.max_chunks_limit:
            logger.info(f"Chunk limit aşıldı: {len(state.discovered_chunks)} >= {state.max_chunks_limit}")
            return True
        
        # Yeterli yüksek relevance'lı chunk var
        high_relevance_chunks = [c for c in state.discovered_chunks if c.relevance_score > 0.5]
        if len(high_relevance_chunks) >= 3:
            logger.info(f"Yeterli yüksek relevance chunk: {len(high_relevance_chunks)}")
            return True
        
        # Iteration limit
        if state.iteration_count >= state.max_iterations:
            logger.info(f"Iteration limit aşıldı: {state.iteration_count}")
            return True
        
        # Token limit (konservatif)
        if self.token_usage["total_tokens"] > 20000:
            logger.info(f"Token limit aşıldı: {self.token_usage['total_tokens']}")
            return True
            
        return False
    
    def create_system_prompt(self, schema: Dict[str, Any]) -> str:
        """Schema-aware system prompt - her işlemde güncel schema ile"""
        
        # Schema bilgilerini detaylı formatla
        schema_text = f"""
## NEO4J VERİTABANI SCHEMA (GÜNCEL)

### MEVCUT NODE LABELS ({len(schema['node_labels'])}):
{', '.join(schema['node_labels'])}

### MEVCUT RELATIONSHIP TYPES ({len(schema['relationship_types'])}):
{', '.join(schema['relationship_types'])}

### NODE PROPERTIES (label: [property_list]):
"""
        for label, props in schema['node_properties'].items():
            if props:
                schema_text += f"- {label}: {', '.join(props)}\n"
        
        schema_text += f"""
### ÖRNEK İLİŞKİLER:
"""
        for rel in schema['sample_relationships']:
            schema_text += f"- ({rel['from_label']})-[:{rel['rel_type']}]->({rel['to_label']})\n"
        
        schema_text += f"""
### ÖRNEK NODE VERİLERİ:
"""
        for node in schema['sample_nodes'][:8]:  # İlk 8 örnek node
            props_str = ', '.join([f"{k}: {str(v)[:50]}" for k, v in node['properties'].items()][:3])
            schema_text += f"- {node['label']}: {props_str}\n"
        
        return f"""Sen Neo4j veritabanında çalışan AKILLI BİLGİ ARAŞTIRMACISI'sın.

{schema_text}

## ARAŞTIRMA STRATEJİSİ:

### 1. SCHEMA ANALİZİ
- Yukarıdaki GÜNCEL schema bilgilerini kullan
- Node labels'da aradığın kavramı kontrol et
- Relationship'leri takip ederek ilişkili node'lara ulaş

### 2. ARAMA YÖNTEMLERİ:
1. **entity_search**: Genel arama (text contains)
2. **cypher_query**: Schema'ya özel targeted query
3. **vector_search**: Semantik benzerlik

### 3. CYPHER QUERY KURALLARI:
- SADECE yukarıdaki node labels'ı kullan
- SADECE yukarıdaki relationship types'ı kullan
- Property names'i örnek node verilerinden öğren
- MATCH clauses'da exact label names kullan

### 4. ENTITY SEARCH KURALLARI:
- Herhangi bir node'da text arama yapar
- Label'a bakarak en uygun node'ları seç

### 5. DURDURMA KOŞULLARI:
- Maksimum {AgentState().max_iterations} iterasyon
- En az 3 chunk, maksimum {AgentState().max_chunks_limit} chunk
- Relevance > {AgentState().relevance_threshold} olanları al

### ACTION FORMAT:
Observation: [Durum analizi]
Thought: [Strateji kararı - hangi node/relationship kullanacağım]
Action: [entity_search/cypher_query/vector_search]
Content: [Arama terimi/Cypher query]

ÖRNEK CYPHER:
```
MATCH (n:ActualNodeLabel) 
WHERE toLower(n.actual_property) CONTAINS toLower("search_term")
RETURN n
```

Sadece bilgi topla, cevap verme!"""
    
    def solve_question(self, user_question: str) -> Dict[str, Any]:
        """Ana soru çözme logic'i - intelligent_agent.py'den esinlenildi"""
        
        logger.info(f"=== SORU ARAŞTIRMASI BAŞLADI ===")
        logger.info(f"Soru: {user_question}")
        
        # State'i başlat
        state = AgentState()
        schema = self.get_graph_schema()
        system_prompt = self.create_system_prompt(schema)
        
        conversation_history = []
        current_observation = "Araştırma başladı. İlk adım: entity_search yapılmalı."
        
        # Ana araştırma döngüsü
        while not self.should_stop_search(state, user_question):
            state.iteration_count += 1
            
            logger.info(f"\n--- İTERASYON {state.iteration_count} ---")
            
            try:
                # Her iterasyonda güncel schema'yı al
                current_schema = self.get_graph_schema()
                current_system_prompt = self.create_system_prompt(current_schema)
                
                # LLM'den bir sonraki aksiyonu al
                prompt = f"""
{current_observation}

Önceki bulgular: {len(state.discovered_chunks)} chunk, {len(state.discovered_entities)} entity

Şimdiye kadarki conversation:
{chr(10).join(conversation_history[-4:])}  # Son 4 adım

Soru: {user_question}

Yukarıdaki GÜNCEL SCHEMA bilgilerini kullanarak bir sonraki adımı belirle:"""

                response = self.llm.invoke([
                    SystemMessage(content=current_system_prompt),
                    HumanMessage(content=prompt)
                ])
                
                self.track_tokens(response)
                
                # Response'u parse et
                content = response.content.strip()
                logger.info(f"LLM Response: {content[:200]}...")
                
                # Action pattern'i ayıkla
                thought = ""
                action = ""
                action_content = ""
                
                lines = content.split('\n')
                for line in lines:
                    line = line.strip()
                    if line.startswith('Thought:'):
                        thought = line.replace('Thought:', '').strip()
                    elif line.startswith('Action:'):
                        action = line.replace('Action:', '').strip()
                    elif line.startswith('Content:') or line.startswith('Query:'):
                        action_content = line.replace('Content:', '').replace('Query:', '').strip()
                
                if not action or not action_content:
                    logger.warning("Action/Content bulunamadı, varsayılan entity_search kullanılıyor")
                    action = "entity_search"
                    action_content = user_question.split()[0] if user_question.split() else "search"
                
                conversation_history.append(f"Thought: {thought}\nAction: {action}\nContent: {action_content}")
                
                # Action'ı uygula
                if action == "entity_search":
                    chunks = self.entity_search(action_content, state)
                    for chunk in chunks:
                        chunk = self.calculate_text_relevance(chunk, user_question)
                        state.add_chunk(chunk)
                    
                    if chunks:
                        best_relevance = max([c.relevance_score for c in chunks])
                        current_observation = f"Entity search: {len(chunks)} chunk bulundu. En yüksek relevance: {best_relevance:.3f}. Toplam: {len(state.discovered_chunks)}"
                        
                        self.add_successful_finding(
                            state.iteration_count,
                            "entity_search",
                            f"'{action_content}' için {len(chunks)} chunk",
                            best_relevance
                        )
                    else:
                        current_observation = f"'{action_content}' için entity bulunamadı."
                        
                elif action == "cypher_query":
                    success, result = self.execute_cypher_query(action_content)
                    if success and result:
                        # Basitleştirilmiş entity çıkarma
                        entity_count = len(result)
                        state.discovered_entities.extend(result[:10])  # İlk 10 sonuç
                        
                        current_observation = f"Cypher sorgusu başarılı. {entity_count} sonuç bulundu."
                        
                        self.add_successful_finding(
                            state.iteration_count,
                            "cypher_query",
                            f"Cypher: {entity_count} sonuç",
                            0.5
                        )
                    else:
                        current_observation = f"Cypher sorgusu başarısız: {result}"
                        
                elif action == "vector_search":
                    chunks = self.vector_search(action_content, state)
                    for chunk in chunks:
                        chunk = self.calculate_text_relevance(chunk, user_question)
                        state.add_chunk(chunk)
                    
                    if chunks:
                        best_relevance = max([c.relevance_score for c in chunks])
                        current_observation = f"Vector search: {len(chunks)} chunk bulundu. En yüksek relevance: {best_relevance:.3f}. Toplam: {len(state.discovered_chunks)}"
                        
                        self.add_successful_finding(
                            state.iteration_count,
                            "vector_search",
                            f"'{action_content}' vector search: {len(chunks)} chunk",
                            best_relevance
                        )
                    else:
                        current_observation = "Vector search sonuç bulamadı."
                
                else:
                    current_observation = f"Bilinmeyen action: {action}"
                
            except Exception as e:
                logger.error(f"İterasyon {state.iteration_count} hatası: {e}")
                current_observation = f"Hata oluştu: {e}"
        
        # Token kullanımını logla
        logger.info(f"TOPLAM TOKEN - Input: {self.token_usage['input_tokens']}, Output: {self.token_usage['output_tokens']}, Total: {self.token_usage['total_tokens']}")
        
        # Sonuçları döndür
        top_chunks = sorted(state.discovered_chunks, key=lambda x: x.relevance_score, reverse=True)[:5]
        
        return {
            "iterations": state.iteration_count,
            "conversation_history": conversation_history,
            "discovered_chunks": len(state.discovered_chunks),
            "discovered_entities": len(state.discovered_entities),
            "chunk_details": [
                {
                    "document": c.document_name,
                    "page": c.page_number,
                    "relevance": c.relevance_score,
                    "preview": c.text[:150] + "..."
                } for c in top_chunks
            ],
            "token_usage": self.token_usage.copy(),
            "successful_findings": self.successful_findings.copy(),
            "context_memory": self.context_memory,
            "efficiency_metrics": {
                "chunks_per_iteration": len(state.discovered_chunks) / state.iteration_count if state.iteration_count > 0 else 0,
                "tokens_per_chunk": self.token_usage['total_tokens'] / len(state.discovered_chunks) if state.discovered_chunks else 0,
                "high_relevance_ratio": len([c for c in state.discovered_chunks if c.relevance_score > 0.5]) / len(state.discovered_chunks) if state.discovered_chunks else 0
            }
        }

def test_agent_v2():
    """Test fonksiyonu"""
    from langchain_neo4j import Neo4jGraph
    
    # Neo4j bağlantısı
    graph = Neo4jGraph(
        url=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        username=os.getenv("NEO4J_USERNAME", "neo4j"),
        password=os.getenv("NEO4J_PASSWORD", "qwerty5555"),
        database=os.getenv("NEO4J_DATABASE", "neo4j")
    )
    
    # Agent'ı oluştur
    agent = IntelligentAgentV2(graph)
    
    # Test soruları
    test_questions = [
        "Ayça hanımın 2020 yılında kaç adet poliçesi var?",
        "Galata Residence ile ilgili hangi bilgiler mevcut?",
        "DASK poliçeleri hakkında ne biliyorsun?"
    ]
    
    print("=== INTELLIGENT AGENT V2 TEST ===\n")
    
    for i, question in enumerate(test_questions, 1):
        print(f"\n{'='*60}")
        print(f"TEST {i}: {question}")
        print('='*60)
        
        result = agent.solve_question(question)
        
        print(f"\n📊 SONUÇLAR:")
        print(f"İterasyon: {result['iterations']}")
        print(f"Chunk sayısı: {result['discovered_chunks']}")
        print(f"Entity sayısı: {result['discovered_entities']}")
        print(f"Token kullanımı: {result['token_usage']['total_tokens']}")
        
        print(f"\n📈 VERİMLİLİK:")
        metrics = result['efficiency_metrics']
        print(f"Chunk/İterasyon: {metrics['chunks_per_iteration']:.2f}")
        print(f"Token/Chunk: {metrics['tokens_per_chunk']:.0f}")
        print(f"Yüksek Relevance Oranı: {metrics['high_relevance_ratio']:.2f}")
        
        print(f"\n📄 EN İLGİLİ CHUNK'LAR:")
        for j, chunk in enumerate(result['chunk_details'], 1):
            print(f"{j}. {chunk['document']} (P{chunk['page']}) - Score: {chunk['relevance']:.3f}")
            print(f"   {chunk['preview']}")
        
        print(f"\n🎯 BAŞARILI BULGULAR:")
        for finding in result['successful_findings']:
            print(f"   {finding['action']}: {finding['finding']} (Score: {finding['relevance_score']:.3f})")

if __name__ == "__main__":
    test_agent_v2()
