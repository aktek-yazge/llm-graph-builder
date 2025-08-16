#!/usr/bin/env python3
"""
Entity-Driven ReAct Agent
1. Entity'lerde arama yapar (__Entity__ nodes)
2. İlgili chunk'lara ulaşır
3. Chunk relationship'lerini takip eder
4. Embedding ile semantic matching yapar
"""

import logging
import json
import re
from typing import Dict, List, Optional, Tuple, Any
from langchain.schema import HumanMessage, SystemMessage
from langchain_neo4j import Neo4jGraph
from src.llm import get_llm
from src.shared.common_fn import load_embedding_model
from dataclasses import dataclass
import time
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class ChunkResult:
    """Chunk sonuç bilgileri"""
    chunk_id: str
    text: str
    page_number: Optional[int]
    document_name: str
    similarity_score: float = 0.0
    related_entities: List[str] = None
    
    def __post_init__(self):
        if self.related_entities is None:
            self.related_entities = []

class ReactAgent:
    """
    Basit ReAct Agent - Farklı stratejilerle graph'te arama yapar
    """
    
    def __init__(self, graph: Neo4jGraph, model_name: str = "openai_gpt_4o"):
        self.graph = graph
        self.llm, _ = get_llm(model_name)
        self.max_iterations = 6
        self.search_strategies = self._init_strategies()
        
    def _init_strategies(self) -> List[SearchStrategy]:
        """Farklı arama stratejilerini tanımla"""
        return [
            SearchStrategy(
                name="direct_policy_search",
                description="Policy node'larından direkt arama",
                query_template="MATCH (p:Policy) WHERE {condition} RETURN {fields}",
                success_criteria="Policy bilgileri bulundu"
            ),
            SearchStrategy(
                name="customer_policy_search", 
                description="Customer üzerinden poliçe arama",
                query_template="MATCH (c:Customer)-[:OWNS]->(p:Policy) WHERE {condition} RETURN {fields}",
                success_criteria="Müşteri poliçeleri bulundu"
            ),
            SearchStrategy(
                name="document_search",
                description="Document node'larından arama",
                query_template="MATCH (d:Document)-[:PART_OF]-(c:Chunk) WHERE {condition} RETURN {fields}",
                success_criteria="Belge bilgileri bulundu"
            ),
            SearchStrategy(
                name="entity_relationship_search",
                description="Entity'ler arası ilişki arama",
                query_template="MATCH (e1)-[r]->(e2) WHERE {condition} RETURN {fields}",
                success_criteria="Entity ilişkileri bulundu"
            ),
            SearchStrategy(
                name="year_based_search",
                description="Yıl bazlı arama",
                query_template="MATCH (py:PolicyYear)-[r]-(n) WHERE {condition} RETURN {fields}",
                success_criteria="Yıl bilgileri bulundu"
            )
        ]
    
    def create_system_prompt(self) -> str:
        """Kısa ve odaklı system prompt"""
        return """Sen Neo4j'de bilgi arayan bir ReAct agent'sın.

GÖREV: Kullanıcı sorusunu analiz et ve adım adım çöz.

FORMAT (TAM OLARAK BU ŞEKİLDE):
Observation: [Mevcut durum]
Thought: [Ne düşünüyorsun]  
Action: strateji_adı|Cypher_sorgusu

ÖRNEK:
Observation: Kullanıcı poliçe türlerini soruyor
Thought: Policy node'larından direkt arama yapmalı  
Action: direct_policy_search|MATCH (p:Policy) RETURN DISTINCT p.type

VEYA FINAL CEVAP İÇİN:
Action: final_answer|Cevabın

STRATEJI SEÇENEKLERİ:
- direct_policy_search: Policy node'larından direkt arama
- customer_policy_search: Customer üzerinden poliçe arama  
- document_search: Document node'larından arama
- entity_relationship_search: Entity'ler arası ilişki arama
- year_based_search: Yıl bazlı arama

KURALLAR:
- Her iterasyonda farklı strateji dene
- WHERE koşullarında apoc.text.clean kullanabilirsin
- Boş sonuç alırsan farklı strateji dene
- Action formatını KESINLIKLE takip et: strateji|sorgu"""

    def parse_response(self, response: str) -> Tuple[str, str, str, str]:
        """Agent response'unu parse et"""
        try:
            logger.info(f"Response parse ediliyor: {response}")
            
            # Observation
            obs_match = re.search(r'Observation:\s*(.*?)(?=Thought:|$)', response, re.DOTALL | re.IGNORECASE)
            observation = obs_match.group(1).strip() if obs_match else ""
            
            # Thought
            thought_match = re.search(r'Thought:\s*(.*?)(?=Action:|$)', response, re.DOTALL | re.IGNORECASE)
            thought = thought_match.group(1).strip() if thought_match else ""
            
            # Action ve Query'yi birlikte ara
            action_match = re.search(r'Action:\s*(.*?)$', response, re.DOTALL | re.IGNORECASE)
            action_full = action_match.group(1).strip() if action_match else ""
            
            # Action ve Query'yi ayır
            if "|" in action_full:
                action, query = action_full.split("|", 1)
                action = action.strip()
                query = query.strip()
            else:
                # Query: formatını ara
                query_match = re.search(r'Query:\s*(.*?)$', response, re.DOTALL | re.IGNORECASE)
                answer_match = re.search(r'Answer:\s*(.*?)$', response, re.DOTALL | re.IGNORECASE)
                
                action = action_full
                if query_match:
                    query = query_match.group(1).strip()
                elif answer_match:
                    query = answer_match.group(1).strip()
                else:
                    query = ""
            
            logger.info(f"Parse sonucu: action='{action}', query='{query[:100]}...'")
            return observation, thought, action, query
            
        except Exception as e:
            logger.error(f"Response parse hatası: {e}")
            return "", "", "final_answer", "Sorguyu anlayamadım, farklı şekilde sorar mısınız?"

    def execute_query(self, query: str) -> Tuple[bool, Any]:
        """Cypher sorgusunu çalıştır"""
        try:
            logger.info(f"Sorgu: {query}")
            result = self.graph.query(query)
            logger.info(f"Sonuç: {len(result) if result else 0} kayıt")
            return True, result
        except Exception as e:
            logger.error(f"Sorgu hatası: {e}")
            return False, str(e)

    def solve_question(self, question: str) -> Dict[str, Any]:
        """Ana problem çözme fonksiyonu"""
        logger.info(f"Soru: {question}")
        
        system_prompt = self.create_system_prompt()
        conversation = []
        
        # İlk observation
        current_obs = f"Kullanıcı sorusu: '{question}'"
        
        for iteration in range(1, self.max_iterations + 1):
            logger.info(f"İterasyon {iteration}")
            
            # Conversation history ekle
            history_text = ""
            if conversation:
                history_text = "\n\nÖnceki Adımlar:\n" + "\n".join(conversation[-2:])
            
            # LLM'e prompt gönder
            full_prompt = f"{current_obs}{history_text}\n\nŞimdi ne yapacaksın?"
            
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=full_prompt)
            ]
            
            try:
                response = self.llm.invoke(messages)
                agent_response = response.content
                
                logger.info(f"Agent: {agent_response[:150]}...")
                
                # Response'u parse et
                observation, thought, action, query = self.parse_response(agent_response)
                
                # Conversation'a ekle
                conversation.append(f"İter{iteration}: {thought} -> {action}")
                
                if action == "final_answer":
                    return {
                        "answer": query,
                        "iterations": iteration,
                        "conversation": conversation
                    }
                
                # Cypher sorgusu çalıştır
                success, result = self.execute_query(query)
                
                if success and result:
                    # Sonuç ile yeni observation oluştur
                    result_summary = self._summarize_result(result)
                    current_obs = f"Sorgu başarılı. Sonuç: {result_summary}"
                    
                    # Eğer soru basit bir sayı sorusuysa ve cevap net ise final answer ver
                    if self._is_question_answered(question, result):
                        return {
                            "answer": self._generate_final_answer(question, result),
                            "iterations": iteration,
                            "conversation": conversation
                        }
                else:
                    current_obs = f"Sorgu başarısız veya boş. Hata: {result}"
                    
            except Exception as e:
                logger.error(f"İterasyon hatası: {e}")
                current_obs = f"Hata oluştu: {e}"
        
        return {
            "answer": "Maksimum iterasyon sayısına ulaşıldı. Cevap bulunamadı.",
            "iterations": self.max_iterations,
            "conversation": conversation
        }
    
    def _summarize_result(self, result: List[Dict]) -> str:
        """Sorgu sonucunu özetle"""
        if not result:
            return "Boş sonuç"
        
        if len(result) == 1:
            return f"1 kayıt: {result[0]}"
        
        # İlk birkaç kaydı göster
        summary = f"{len(result)} kayıt bulundu. İlk kayıtlar: "
        for i, record in enumerate(result[:3]):
            summary += f"[{i+1}: {record}] "
        
        if len(result) > 3:
            summary += "..."
            
        return summary
    
    def _is_question_answered(self, question: str, result: List[Dict]) -> bool:
        """Soru basit sayısal bir soru mu ve cevap net mi?"""
        question_lower = question.lower()
        
        # Sayı soruları
        if any(word in question_lower for word in ["kaç", "sayı", "adet", "miktar", "ne kadar"]):
            if len(result) == 1 and len(result[0]) == 1:
                # Tek değer döndü
                return True
                
        # Liste soruları
        if any(word in question_lower for word in ["hangi", "neler", "listele"]):
            if result and len(result) <= 10:  # Makul bir liste
                return True
                
        return False
    
    def _generate_final_answer(self, question: str, result: List[Dict]) -> str:
        """Net bir final answer oluştur"""
        question_lower = question.lower()
        
        if "kaç" in question_lower and "poliçe türü" in question_lower:
            # Poliçe türü sayısı sorusu
            if len(result) == 1:
                count = list(result[0].values())[0]
                return f"Sistemde {count} farklı poliçe türü mevcut."
                
        if "hangi" in question_lower and "türü" in question_lower:
            # Poliçe türü listesi sorusu
            types = [list(r.values())[0] for r in result if list(r.values())[0]]
            types = [t for t in types if t and t != 'Policy']  # Boş ve generic değerleri çıkar
            return f"Sistemdeki poliçe türleri: {', '.join(types)}"
        
        # Genel format
        return f"Sorgu sonucu: {result}"
