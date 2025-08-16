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
from src.llm import get_llm
from src.shared.common_fn import load_embedding_model
from src.utf8_utils import normalize_unicode_text
import time
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class IntelligentAgent:
    """
    ReAct pattern kullanan Neo4j intelligent agent
    """
    
    def __init__(self, graph: Neo4jGraph, model_name: str = "openai_gpt_4o"):
        self.graph = graph
        self.llm, _ = get_llm(model_name)
        self.embedding_model, _ = load_embedding_model("openai")
        self.max_iterations = 5
        self.schema_cache = None
        
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
    
    def vector_search(self, query_text: str, limit: int = 10) -> List[Dict]:
        """Vector search yap"""
        try:
            logger.info(f"Vector search: {query_text}")
            normalized_query = normalize_unicode_text(query_text)
            query_embedding = self.embedding_model.embed_query(normalized_query)
            
            vector_query = """
            CALL db.index.vector.queryNodes('vector', $limit, $query_vector) 
            YIELD node, score
            RETURN node.text as text, node.fileName as fileName, score
            ORDER BY score DESC
            """
            
            result = self.graph.query(vector_query, {
                'query_vector': query_embedding,
                'limit': limit
            })
            
            logger.info(f"Vector search sonucu: {len(result)} chunk")
            return result
            
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
        """Ana problem çözme fonksiyonu - ReAct pattern"""
        
        logger.info(f"Soru çözülüyor: {user_question}")
        
        # Schema bilgilerini al
        schema = self.get_neo4j_schema()
        system_prompt = self.create_system_prompt(schema)
        
        conversation_history = []
        iteration = 0
        final_answer = None
        
        # İlk observation
        current_observation = f"Kullanıcı sorusu: '{user_question}'"
        
        while iteration < self.max_iterations and not final_answer:
            iteration += 1
            logger.info(f"İterasyon {iteration}")
            
            # LLM'e gönderilecek mesaj
            prompt = f"{current_observation}\n\nBu duruma göre next action'ını belirle:"
            
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=prompt)
            ]
            
            # Conversation history ekle
            for entry in conversation_history[-6:]:  # Son 6 adımı tut
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
                    final_answer = action_content
                    break
                    
                elif action == "cypher_query":
                    success, result = self.execute_cypher_query(action_content)
                    if success:
                        if result and len(result) > 0:
                            current_observation = f"Cypher sorgusu başarılı. {len(result)} sonuç bulundu: {result[:3]}"
                        else:
                            current_observation = "Cypher sorgusu başarılı ama sonuç bulunamadı. Farklı bir yaklaşım dene."
                    else:
                        current_observation = f"Cypher sorgusu başarısız: {result}. Farklı bir sorgu dene."
                        
                elif action == "vector_search":
                    vector_results = self.vector_search(action_content)
                    if vector_results:
                        current_observation = f"Vector search sonucu: {len(vector_results)} chunk bulundu. İlk sonuçlar: {vector_results[:2]}"
                    else:
                        current_observation = "Vector search sonuç bulamadı."
                        
                else:
                    current_observation = f"Bilinmeyen action: {action}. Geçerli action'lar: cypher_query, vector_search, final_answer"
                
            except Exception as e:
                logger.error(f"İterasyon {iteration} hatası: {e}")
                current_observation = f"Hata oluştu: {e}. Farklı bir yaklaşım dene."
        
        # Sonuç döndür
        if not final_answer:
            final_answer = "Maalesef sorunuza cevap bulamadım. Lütfen sorunuzu farklı şekilde ifade edin."
        
        return {
            "answer": final_answer,
            "iterations": iteration,
            "conversation_history": conversation_history,
            "schema_info": schema
        }

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
        "En son eklenen belge hangisi?"
    ]
    
    for question in test_questions:
        print(f"\n{'='*60}")
        print(f"SORU: {question}")
        print('='*60)
        
        result = agent.solve_question(question)
        
        print(f"CEVAP: {result['answer']}")
        print(f"İTERASYON: {result['iterations']}")
        print("\nKONVERSASYON:")
        for i, entry in enumerate(result['conversation_history'], 1):
            print(f"{i}. {entry[:200]}...")

if __name__ == "__main__":
    test_agent()
