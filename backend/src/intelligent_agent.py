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
import sys
from typing import Dict, List, Optional, Tuple, Any
from langchain.schema import HumanMessage, SystemMessage
from langchain_neo4j import Neo4jGraph
from langchain.text_splitter import RecursiveCharacterTextSplitter
import neo4j.time

# Path ayarla
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'backend'))

from src.llm import get_llm
from src.shared.common_fn import load_embedding_model
from src.utf8_utils import normalize_unicode_text
import time
from dotenv import load_dotenv
from dataclasses import dataclass, field
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np

# Load environment variables
load_dotenv()

# Base URL for reference links
BASE_URL = os.getenv("BASE_URL", "http://localhost:8001")

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def serialize_neo4j_data(obj):
    """Neo4j özel tiplerini JSON serializable hale getir"""
    if isinstance(obj, neo4j.time.DateTime):
        return obj.iso_format()
    elif isinstance(obj, (neo4j.time.Date, neo4j.time.Time)):
        return str(obj)
    elif isinstance(obj, dict):
        return {k: serialize_neo4j_data(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [serialize_neo4j_data(item) for item in obj]
    else:
        return obj

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
class SuccessfulFinding:
    """Başarılı bulguları tutan data class"""
    iteration: int
    action_type: str  # cypher_query, vector_search
    summary: str
    relevance_score: float
    raw_data: Any = None

@dataclass
class AgentState:
    """Agent'ın mevcut durumu ve topladığı bilgileri tutan state"""
    question: str  # Bu tek non-default field, en başta olmalı
    original_question: str = ""  # Orijinal kullanıcı sorusu (referans tipi analizi için)
    iteration_count: int = 0
    max_chunks_limit: int = 20
    similarity_threshold: float = 0.3
    discovered_chunks: List[ChunkInfo] = field(default_factory=list)
    discovered_entities: List[Dict[str, Any]] = field(default_factory=list)
    discovered_relationships: List[Dict[str, Any]] = field(default_factory=list)
    query_attempts: List[str] = field(default_factory=list)
    successful_findings: List['SuccessfulFinding'] = field(default_factory=list)  # Başarılı bulgular
    
    def add_chunk(self, chunk_info: ChunkInfo):
        """Yeni chunk bilgisi ekle"""
        # Duplikasyon kontrolü
        existing_ids = [c.chunk_id for c in self.discovered_chunks]
        if chunk_info.chunk_id not in existing_ids:
            self.discovered_chunks.append(chunk_info)

class IntelligentAgent:
    """
    ReAct pattern kullanan Neo4j intelligent agent
    LLM kendi arama stratejisini belirler ve iteratif olarak doğru veriye ulaşır
    """
    
    def __init__(self, graph: Neo4jGraph, model_name: str = "openai_gpt_4.1", enable_llm_interpretation: bool = False):
        self.graph = graph
        self.llm, _ = get_llm(model_name)
        self.embedding_model, _ = load_embedding_model("openai")
        self.max_iterations = 10  # Derinlemesine araştırma için
        # self.max_iterations = 5
        self.schema_cache = None
        self.system_prompt_cache = None  # Schema-based system prompt cache - PROMPT güncellendi: vector_search kaldırıldı, domain-agnostic yapıldı
        self.enable_llm_interpretation = enable_llm_interpretation  # LLM yorumlama açık/kapalı
        
        # Progress tracking ve context memory
        self.context_memory = ""  # Birikimli context prompt
        self.token_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        self.detailed_token_usage = []  # Action bazında detaylı token tracking
        
        # Text splitter'ı başlat
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=300,
            chunk_overlap=50,
            length_function=len,
            separators=["\n\n", "\n", ". ", "! ", "? ", " ", ""]
        )
    
    def interpret_final_answer_with_llm(self, raw_answer: str, user_question: str, chunks: List[ChunkInfo], cypher_results: Any = None) -> str:
        """LLM ile final answer'ı yorumla ve zenginleştir"""
        try:
            if not self.enable_llm_interpretation:
                logger.info("LLM yorumlama kapalı, raw data döndürülüyor")
                return raw_answer
                
            logger.info("LLM ile final answer yorumlanıyor...")
            
            # Chunk'lardan context oluştur
            context_sources = []
            if chunks:
                for i, chunk in enumerate(chunks[:5], 1):  # En iyi 5 chunk
                    context_sources.append(f"Kaynak {i} ({chunk.document_name}, Sayfa {chunk.page_number}):\n{chunk.text[:300]}...")
            
            context_text = "\n\n".join(context_sources) if context_sources else "Kaynak bilgi yok"
            
            # Cypher sonuçlarını da ekle
            raw_data_detail = raw_answer
            if cypher_results:
                raw_data_detail += f"\n\nCYPHER SORGU SONUÇLARI:\n{json.dumps(cypher_results, ensure_ascii=False, indent=2)}"
            
            interpretation_prompt = f"""Sen bir uzman analist olarak kullanıcı sorusunu yanıtlayacaksın.

KULLANICI SORUSU: {user_question}

HAM VERİ/BULGULAR:
{raw_data_detail}

KAYNAK BİLGİLER:
{context_text}

GÖREV:
1. Ham veriyi AYNEN analiz et ve yorumla - veri kaybetme!
2. Sayısal veriler ve isimler varsa MUTLAKA belirt
3. Kullanıcının sorusuna kapsamlı bir cevap ver
4. Kaynak bilgileri referans göster
5. Sonucu net ve anlaşılır şekilde özetle

ÇIKTI FORMAT:

## Bulgular
[Bulunan sonuçlar]

## Kaynaklar
[Kaynak bilgilerin referansları]
"""

            messages = [
                SystemMessage(content="Sen uzman bir veri analisti ve raporlama uzmanısın. Verilen bilgileri analiz ederek kullanıcı dostu, kapsamlı raporlar hazırlarsın."),
                HumanMessage(content=interpretation_prompt)
            ]
            
            response = self.llm.invoke(messages)
            interpreted_answer = response.content.strip()
            
            # Token kullanımını logla
            self.log_token_usage(response, -1, "llm_interpretation")  # -1 iteration: extra step
            
            logger.info(f"LLM yorumlama tamamlandı: {len(interpreted_answer)} karakter")
            return interpreted_answer
            
        except Exception as e:
            logger.error(f"LLM yorumlama hatası: {e}")
            return raw_answer  # Fallback: raw answer'ı döndür
    
    def format_final_answer_with_references(self, clean_answer: str, state: 'AgentState') -> str:
        """
        Temiz cevabı alır, bağlamsal olarak uygun referans tipini seçer ve ekler
        """
        try:
            logger.info("📝 Final answer'a referans bilgileri ekleniyor...")
            
            # Ana cevabı başlat
            formatted_answer = clean_answer.strip()
            
            # LLM ile referans tipini belirle
            reference_type = self._determine_reference_type(clean_answer, state.original_question)
            logger.info(f"🎯 Belirlenen referans tipi: {reference_type}")
            
            # Referans bilgileri ekle
            references = []
            referenced_docs = set()
            
            # 1. Discovered chunks'dan referanslar (eğer varsa)
            if state.discovered_chunks and reference_type == "pages":
                unique_docs = {}
                for chunk in state.discovered_chunks[:10]:  # En relevan 10 chunk
                    doc_name = chunk.document_name
                    if doc_name not in unique_docs:
                        unique_docs[doc_name] = []
                    unique_docs[doc_name].append(chunk.page_number)
                    referenced_docs.add(doc_name)
                
                if unique_docs:
                    for doc_name, pages in unique_docs.items():
                        page_list = ", ".join(map(str, sorted(set(pages))))
                        # Page image linklerini oluştur
                        for page_num in sorted(set(pages))[:3]:  # Her belgeden max 3 sayfa
                            image_name = f"{doc_name.replace('.pdf', '')}_page_{page_num:03d}.png"
                            # URL encode the image name for proper handling of Turkish characters
                            import urllib.parse
                            encoded_image_name = urllib.parse.quote(image_name, safe='', encoding='utf-8')
                            image_link = f"{BASE_URL}/images/{encoded_image_name}"
                            references.append(f"- [Sayfa {page_num} - {doc_name}]({image_link})")
            
            # 2. Successful findings'den belge referansları (cypher_query sonuçlarından)
            if state.successful_findings:
                logger.info(f"🔍 DEBUG - Successful findings'den referans ekleniyor: {len(state.successful_findings)} finding")
                for finding in state.successful_findings:
                    logger.info(f"🔍 DEBUG - Finding: action_type={finding.action_type}, raw_data={bool(finding.raw_data)}")
                    if finding.action_type == 'cypher_query' and finding.raw_data:
                        # Cypher sonuçlarından Customer ve Policy bilgilerini çıkar
                        logger.info(f"🔍 DEBUG - Raw data: {finding.raw_data}")
                        for row in finding.raw_data:
                            logger.info(f"🔍 DEBUG - Row: {row}")
                            if isinstance(row, dict):
                                # Customer ismi varsa ona bağlı belgeleri bul
                                customer_name = None
                                if 'customer' in row:
                                    customer_name = row['customer']
                                elif 'fullName' in row:
                                    customer_name = row['fullName']
                                elif 'customerName' in row:
                                    customer_name = row['customerName']
                                
                                logger.info(f"🔍 DEBUG - Customer name found: {customer_name}")
                                if customer_name:
                                    if reference_type == "documents":
                                        # PDF belgeleri döndür
                                        doc_query = """
                                        MATCH (c:Customer {fullName: $customer_name})-[:HAS_DOC]->(d:Document)
                                        RETURN DISTINCT d.fileName as file_name
                                        ORDER BY d.fileName
                                        """
                                        try:
                                            doc_results = self.graph.query(doc_query, {"customer_name": customer_name})
                                            logger.info(f"🔍 DEBUG - Document query results: {doc_results}")
                                            for doc_row in doc_results:
                                                file_name = doc_row.get('file_name')
                                                if file_name and file_name.endswith('.pdf'):
                                                    # URL encode the filename for proper handling of Turkish characters
                                                    import urllib.parse
                                                    encoded_filename = urllib.parse.quote(file_name, safe='', encoding='utf-8')
                                                    pdf_link = f"{BASE_URL}/files/{encoded_filename}"
                                                    doc_ref = f"- [{file_name}]({pdf_link})"
                                                    if doc_ref not in references:
                                                        references.append(doc_ref)
                                                        referenced_docs.add(file_name)
                                        except Exception as e:
                                            logger.error(f"Document arama hatası: {e}")
                                    
                                    elif reference_type == "pages":
                                        # Page image'ları döndür
                                        chunk_query = """
                                        MATCH (c:Customer {fullName: $customer_name})-[:HAS_DOC]->(d:Document)-[:FIRST_CHUNK]->(ch:Chunk)
                                        OPTIONAL MATCH (ch)-[:NEXT_CHUNK*]->(ch2:Chunk)
                                        WITH collect(ch) + collect(ch2) as all_chunks
                                        UNWIND all_chunks as chunk
                                        WITH chunk
                                        WHERE chunk.page_link IS NOT NULL
                                        RETURN DISTINCT chunk.page_link as page_link, chunk.page_number as page_number, chunk.fileName as file_name
                                        ORDER BY chunk.page_number
                                        LIMIT 10
                                        """
                                        try:
                                            chunk_results = self.graph.query(chunk_query, {"customer_name": customer_name})
                                            logger.info(f"🔍 DEBUG - Chunk query results: {chunk_results}")
                                            for chunk_row in chunk_results:
                                                page_link = chunk_row.get('page_link')
                                                page_number = chunk_row.get('page_number')
                                                file_name = chunk_row.get('file_name')
                                                
                                                if page_link:
                                                    # Page link'ten image name'i çıkar (path'in son kısmı)
                                                    import os
                                                    import urllib.parse
                                                    image_name = os.path.basename(page_link)
                                                    
                                                    # URL encode the image name for proper handling of Turkish characters
                                                    encoded_image_name = urllib.parse.quote(image_name, safe='', encoding='utf-8')
                                                    image_link = f"{BASE_URL}/images/{encoded_image_name}"
                                                    page_ref = f"- [Sayfa {page_number} - {file_name}]({image_link})"
                                                    if page_ref not in references:
                                                        references.append(page_ref)
                                                        referenced_docs.add(f"{file_name}_page_{page_number}")
                                        except Exception as e:
                                            logger.error(f"Chunk arama hatası: {e}")
            
            # Referansları cevaba ekle - sadece belgeler varsa
            if references:
                formatted_answer += "\n\n**📋 Kaynaklar:**\n" + "\n".join(references)
                logger.info(f"📝 Referans ekleme tamamlandı: {len(references)} referans")
            else:
                logger.info("📝 Referans ekleme tamamlandı: 0 referans")
            
            return formatted_answer
            
        except Exception as e:
            logger.error(f"Referans ekleme hatası: {e}")
            return clean_answer  # Fallback: sadece temiz cevabı döndür
    
    def _determine_reference_type(self, answer: str, question: str) -> str:
        """
        LLM ile cevap ve soru bağlamında hangi tip referans döndürüleceğini belirle
        Returns: "documents" (PDF belgeleri) veya "pages" (sayfa görselleri)
        """
        try:
            analysis_prompt = f"""Sen bir belge referans analizci asistanısın. Kullanıcının sorusu ve verilen cevabı analiz ederek, en uygun referans tipini belirle.

SORU: "{question}"
CEVAP: "{answer}"

Referans tipleri:
1. "documents" - Genel bilgi, sayısal veriler, özet bilgiler için PDF belgelerinin tamamı
2. "pages" - Spesifik detaylar, madde detayları, tablolar, formlar için sayfa görselleri

Analiz kriterleri:
- Eğer cevap sayısal bilgi, özet, genel bilgi içeriyorsa -> "documents"
- Eğer cevap spesifik madde, taksit, detay bilgi içeriyorsa -> "pages"
- Eğer soru "kaç", "toplam", "sayısı" gibi kelimeler içeriyorsa -> "documents"
- Eğer soru spesifik poliçe numarası, madde detayı içeriyorsa -> "pages"

Sadece "documents" veya "pages" olarak yanıtla."""

            try:
                llm = self.llm  # IntelligentAgent sınıfında zaten var olan LLM instance'ı kullan
                result = llm.invoke(analysis_prompt)
                
                if hasattr(result, 'content'):
                    response = result.content.strip().lower()
                else:
                    response = str(result).strip().lower()
                
                if "documents" in response:
                    return "documents"
                elif "pages" in response:
                    return "pages"
                else:
                    # Fallback: eğer belirsizse, keyword analizi yap
                    question_lower = question.lower()
                    answer_lower = answer.lower()
                    
                    # Sayısal/özet soruları için documents
                    summary_keywords = ["kaç", "toplam", "sayısı", "adet", "liste", "hangi", "kimler"]
                    if any(keyword in question_lower for keyword in summary_keywords):
                        return "documents"
                    
                    # Detay soruları için pages
                    detail_keywords = ["taksit", "madde", "detay", "içerik", "bilgi", "şart"]
                    if any(keyword in question_lower for keyword in detail_keywords):
                        return "pages"
                    
                    # Default: documents
                    return "documents"
                    
            except Exception as llm_error:
                logger.error(f"LLM referans tipi belirlemede hata: {llm_error}")
                # Fallback: basit keyword analizi
                question_lower = question.lower()
                summary_keywords = ["kaç", "toplam", "sayısı", "adet", "liste"]
                if any(keyword in question_lower for keyword in summary_keywords):
                    return "documents"
                return "pages"
                
        except Exception as e:
            logger.error(f"Referans tipi belirleme hatası: {e}")
            return "documents"  # Safe fallback
    
    # def refresh_schema_cache(self):
    #     """Schema cache'i temizle ve yeniden yükle"""
    #     logger.info("Schema cache temizleniyor ve yeniden yükleniyor...")
    #     self.schema_cache = None
    #     self.system_prompt_cache = None
    #     # Yeniden yükle
    #     self.get_neo4j_schema()
        
    # def get_neo4j_schema(self) -> Dict[str, Any]:
    #     """Neo4j veritabanından schema bilgilerini al - bir kez cache'le"""
    #     if self.schema_cache:
    #         logger.info("Schema cache'den alınıyor")
    #         return self.schema_cache
            
    #     logger.info("Neo4j schema bilgileri veritabanından alınıyor...")
    #     try:
    #         # Node labels
    #         node_labels_query = "CALL db.labels() YIELD label RETURN collect(label) as labels"
    #         node_result = self.graph.query(node_labels_query)
    #         node_labels = node_result[0]['labels'] if node_result else []
            
    #         # Relationship types
    #         rel_types_query = "CALL db.relationshipTypes() YIELD relationshipType RETURN collect(relationshipType) as types"
    #         rel_result = self.graph.query(rel_types_query)
    #         relationship_types = rel_result[0]['types'] if rel_result else []
            
    #         # Node properties (sample from each label with sample values)
    #         node_properties = {}
    #         for label in node_labels[:10]:  # İlk 10 label için
    #             try:
    #                 # Properties ve sample values
    #                 prop_query = f"MATCH (n:`{label}`) RETURN n LIMIT 1"
    #                 prop_result = self.graph.query(prop_query)
    #                 if prop_result:
    #                     sample_node = prop_result[0]['n']
    #                     # Field adları ve sample values
    #                     props_with_samples = []
    #                     for key, value in sample_node.items():
    #                         if value is not None:
    #                             # Değer tipini belirle
    #                             if isinstance(value, int):
    #                                 props_with_samples.append(f"{key} (INTEGER: {value})")
    #                             elif isinstance(value, str):
    #                                 props_with_samples.append(f"{key} (STRING: \"{value[:20]}{'...' if len(str(value)) > 20 else ''}\")")
    #                             elif isinstance(value, bool):
    #                                 props_with_samples.append(f"{key} (BOOLEAN: {value})")
    #                             elif isinstance(value, float):
    #                                 props_with_samples.append(f"{key} (FLOAT: {value})")
    #                             else:
    #                                 props_with_samples.append(f"{key} ({type(value).__name__}: {str(value)[:20]})")
    #                         else:
    #                             props_with_samples.append(f"{key}")
    #                     node_properties[label] = props_with_samples
    #                 else:
    #                     node_properties[label] = []
    #             except Exception as e:
    #                 logger.warning(f"Could not get properties for {label}: {e}")
    #                 node_properties[label] = []
            
    #         # Sample relationships
    #         sample_relationships = []
    #         for rel_type in relationship_types[:15]:  # İlk 15 ilişki tipi
    #             try:
    #                 rel_query = f"""
    #                 MATCH (a)-[r:`{rel_type}`]->(b) 
    #                 RETURN labels(a)[0] as from_label, type(r) as rel_type, labels(b)[0] as to_label 
    #                 LIMIT 1
    #                 """
    #                 rel_result = self.graph.query(rel_query)
    #                 if rel_result:
    #                     sample_relationships.append(rel_result[0])
    #             except Exception as e:
    #                 logger.warning(f"Could not sample relationship {rel_type}: {e}")
            
    #         self.schema_cache = {
    #             "node_labels": node_labels,
    #             "relationship_types": relationship_types,
    #             "node_properties": node_properties,
    #             "sample_relationships": sample_relationships
    #         }
            
    #         # Schema değiştiğinde system prompt'u da güncelle
    #         self.system_prompt_cache = self.create_enhanced_system_prompt(self.schema_cache)
    #         logger.info(f"Schema ve system prompt cache'lendi: {len(node_labels)} node label, {len(relationship_types)} relationship type")
            
    #         return self.schema_cache
            
    #     except Exception as e:
    #         logger.error(f"Schema bilgisi alınamadı: {e}")
    #         return {
    #             "node_labels": [],
    #             "relationship_types": [],
    #             "node_properties": {},
    #             "sample_relationships": []
    #         }
    
    def execute_cypher_query(self, query: str) -> Tuple[bool, Any]:
        """Cypher sorgusunu çalıştır"""
        try:
            logger.info(f"Cypher sorgusu çalıştırılıyor: {query}")
            result = self.graph.query(query)
            logger.info(f"Sonuç: {len(result) if result else 0} kayıt")
            
            # Sonuç detaylarını logla
            if result:
                for i, row in enumerate(result[:3]):  # İlk 3 satırı göster
                    logger.info(f"Satır {i+1}: {row}")
            
            return True, result
        except Exception as e:
            logger.error(f"Cypher sorgu hatası: {e}")
            return False, str(e)
    
    def execute_vector_search(self, query_text: str, limit: int = 10, document_names: List[str] = None) -> Tuple[bool, Any]:
        """LLM'in kullanabileceği vector search fonksiyonu - OpenAI embedding ile
        
        Args:
            query_text: Arama sorgusu
            limit: Maksimum sonuç sayısı
            document_names: Aramayı sınırlandırmak için belge adları listesi (opsiyonel)
        """
        try:
            scope_info = f" (Belge filtresi: {document_names})" if document_names else " (Tüm DB)"
            logger.info(f"Vector search çalıştırılıyor: {query_text}{scope_info}")
            
            # OpenAI embedding al - dışarıdan sağlanan embedding model kullan
            normalized_query = normalize_unicode_text(query_text)
            query_embedding = self.embedding_model.embed_query(normalized_query)
            
            # Belge filtresi varsa sınırlandırılmış arama, yoksa tüm DB
            if document_names and len(document_names) > 0:
                # Belirli belgelerde sınırlandırılmış arama
                vector_query = """
                CALL db.index.vector.queryNodes('vector', $limit, $query_vector) 
                YIELD node, score
                MATCH (node)-[:PART_OF]->(d:Document)
                WHERE d.fileName IN $document_names
                RETURN 
                    node.chunkId as chunk_id,
                    node.text as text, 
                    node.page_number as page_number,
                    d.fileName as document_name,
                    score
                ORDER BY score DESC
                """
                query_params = {
                    'query_vector': query_embedding,
                    'limit': limit,
                    'document_names': document_names
                }
            else:
                # Tüm veritabanında arama (mevcut davranış)
                vector_query = """
                CALL db.index.vector.queryNodes('vector', $limit, $query_vector) 
                YIELD node, score
                OPTIONAL MATCH (node)-[:PART_OF]->(d:Document)
                RETURN 
                    node.chunkId as chunk_id,
                    node.text as text, 
                    node.page_number as page_number,
                    d.fileName as document_name,
                    score
                ORDER BY score DESC
                """
                query_params = {
                    'query_vector': query_embedding,
                    'limit': limit
                }
            
            result = self.graph.query(vector_query, query_params)
            
            if not result:
                logger.info("Vector search: sonuç bulunamadı")
                return True, []
            
            # Sonuçları formatla
            formatted_results = []
            for row in result:
                formatted_results.append({
                    'chunk_id': row['chunk_id'],
                    'text': row['text'] or "",
                    'page_number': row['page_number'],
                    'document_name': row['document_name'] or "Unknown",
                    'relevance_score': float(row['score'])
                })
            
            logger.info(f"Vector search sonucu: {len(formatted_results)} chunk, en yüksek score: {formatted_results[0]['relevance_score']:.3f}")
            
            # İlk birkaç sonucu logla
            for i, row in enumerate(formatted_results[:3], 1):
                logger.info(f"Vector Sonuç {i} (Score: {row['relevance_score']:.3f}): {row['document_name']} - {row['text'][:100]}...")
            
            return True, formatted_results
            
        except Exception as e:
            logger.error(f"Vector search hatası: {e}")
            return False, str(e)
    
    def get_available_documents(self) -> List[str]:
        """Veritabanında mevcut belgelerin listesini al"""
        try:
            query = """
            MATCH (d:Document)
            RETURN DISTINCT d.fileName as document_name
            ORDER BY d.fileName
            """
            result = self.graph.query(query)
            
            documents = [row['document_name'] for row in result if row['document_name']]
            logger.info(f"Veritabanında {len(documents)} belge bulundu")
            return documents
            
        except Exception as e:
            logger.error(f"Belge listesi alma hatası: {e}")
            return []
    
    def execute_document_filtered_vector_search(self, query_text: str, user_question: str, limit: int = 10, relevant_documents: List[str] = None) -> Tuple[bool, Any]:
        """İlk önce hangi belgelerde arama yapılacağını belirle, sonra vector search yap"""
        try:
            # Eğer önceki bulgulardan belge listesi varsa direkt kullan
            if relevant_documents:
                logger.info(f"Önceki bulgulardan {len(relevant_documents)} belge kullanılıyor: {relevant_documents}")
                return self.execute_vector_search(query_text, limit, relevant_documents)
            
            # Eğer önceki bulgulardan belge yok ise, tüm belgeleri al
            available_docs = self.get_available_documents()
            if not available_docs:
                logger.warning("Veritabanında belge bulunamadı")
                return self.execute_vector_search(query_text, limit)
            
            # LLM'den hangi belgelerde arama yapacağını sor
            document_selection_prompt = f"""
Kullanıcı sorusu: "{user_question}"
Arama sorgusu: "{query_text}"

Mevcut belgeler:
{chr(10).join([f"- {doc}" for doc in available_docs])}

GÖREV: Bu sorguyu cevaplamak için hangi belgelerde arama yapılması gerektiğini belirle.

KURALLAR:
1. Eğer soruda belirli bir kişi/poliçe/dosya adı geçiyorsa, sadece o belgeleri seç
2. Genel sorular için tüm belgelerde ara (boş liste döndür)
3. İlgisiz belgeleri dahil etme

CEVAP FORMATI:
Arama yapılacak belgeler: [belge1.pdf, belge2.pdf] 
(Eğer tüm belgelerde arama yapılacaksa: [])

Kısa açıklama: ...
"""
            
            response = self.llm.invoke([
                SystemMessage(content="Sen bir belge analiz uzmanısın. Kullanıcı sorularına göre hangi belgelerde arama yapılması gerektiğini belirlersin."),
                HumanMessage(content=document_selection_prompt)
            ])
            
            # Cevabı parse et
            response_text = response.content.strip()
            logger.info(f"Belge seçim cevabı: {response_text}")
            
            # Belge listesini çıkar - basit regex/string parsing
            selected_docs = []
            if "[]" in response_text or "tüm belgelerde" in response_text.lower():
                # Tüm belgelerde ara
                selected_docs = None
                logger.info("LLM kararı: Tüm belgelerde arama yap")
            else:
                # Belirli belgeleri seç - response'ta geçen belge adlarını bul
                for doc in available_docs:
                    if doc in response_text:
                        selected_docs.append(doc)
                
                if not selected_docs:
                    # Hiç belge bulunamazsa tüm belgelerde ara
                    selected_docs = None
                    logger.info("LLM cevabında belirli belge bulunamadı, tüm belgelerde arama yapılacak")
                else:
                    logger.info(f"LLM seçimi: {len(selected_docs)} belge - {selected_docs}")
            
            # Seçilen belgelerde vector search yap
            return self.execute_vector_search(query_text, limit, selected_docs)
            
        except Exception as e:
            logger.error(f"Belgeli vector search hatası: {e}")
            # Fallback: normal vector search
            return self.execute_vector_search(query_text, limit)
    
    def log_detailed_token_report(self):
        """Detaylı token kullanım raporunu logla"""
        try:
            if not self.detailed_token_usage:
                logger.info("📊 Henüz token kullanım verisi yok")
                return
                
            logger.info("\n" + "="*80)
            logger.info("📊 DETAYLI TOKEN KULLANIM RAPORU")
            logger.info("="*80)
            
            # Action tipine göre grupla
            action_totals = {}
            for usage in self.detailed_token_usage:
                action_type = usage['action_type']
                if action_type not in action_totals:
                    action_totals[action_type] = {
                        "count": 0,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "total_tokens": 0
                    }
                action_totals[action_type]["count"] += 1
                action_totals[action_type]["input_tokens"] += usage['input_tokens']
                action_totals[action_type]["output_tokens"] += usage['output_tokens']
                action_totals[action_type]["total_tokens"] += usage['total_tokens']
            
            # Action bazında özet
            logger.info("🎯 ACTION BAZINDA TOKEN KULLANIMI:")
            for action_type, totals in sorted(action_totals.items()):
                avg_total = totals["total_tokens"] / totals["count"] if totals["count"] > 0 else 0
                logger.info(f"   {action_type.upper()}:")
                logger.info(f"     - Çağrı Sayısı: {totals['count']}")
                logger.info(f"     - Toplam Token: {totals['total_tokens']:,}")
                logger.info(f"     - Input: {totals['input_tokens']:,} | Output: {totals['output_tokens']:,}")
                logger.info(f"     - Ortalama/Çağrı: {avg_total:.1f}")
                logger.info("")
            
            # İterasyon bazında detay
            logger.info("🔄 İTERASYON BAZINDA DETAY:")
            for usage in self.detailed_token_usage:
                logger.info(f"   İterasyon {usage['iteration']} ({usage['action_type']}):")
                logger.info(f"     Input: {usage['input_tokens']:,} | Output: {usage['output_tokens']:,} | Total: {usage['total_tokens']:,}")
            
            # En yüksek/en düşük token kullananlar
            if len(self.detailed_token_usage) > 1:
                max_usage = max(self.detailed_token_usage, key=lambda x: x['total_tokens'])
                min_usage = min(self.detailed_token_usage, key=lambda x: x['total_tokens'])
                
                logger.info("📈 EN YÜKSEK TOKEN KULLANAN:")
                logger.info(f"   İterasyon {max_usage['iteration']} ({max_usage['action_type']}): {max_usage['total_tokens']:,} token")
                
                logger.info("📉 EN DÜŞÜK TOKEN KULLANAN:")
                logger.info(f"   İterasyon {min_usage['iteration']} ({min_usage['action_type']}): {min_usage['total_tokens']:,} token")
            
            # Genel istatistikler
            total_calls = len(self.detailed_token_usage)
            avg_per_call = self.token_usage['total_tokens'] / total_calls if total_calls > 0 else 0
            
            logger.info("📊 GENEL İSTATİSTİKLER:")
            logger.info(f"   Toplam LLM Çağrısı: {total_calls}")
            logger.info(f"   Toplam Token: {self.token_usage['total_tokens']:,}")
            logger.info(f"   Ortalama Token/Çağrı: {avg_per_call:.1f}")
            logger.info(f"   Input/Output Oranı: {self.token_usage['input_tokens'] / max(self.token_usage['output_tokens'], 1):.2f}")
            
            logger.info("="*80)
            
        except Exception as e:
            logger.error(f"Token raporu oluşturma hatası: {e}")

    def log_token_usage(self, response, iteration: int, action_type: str = "unknown"):
        """Token kullanımını logla - action bazında detaylarla"""
        try:
            # LangChain OpenAI response structure
            if hasattr(response, 'usage_metadata') and response.usage_metadata:
                usage = response.usage_metadata
                # usage_metadata dict olarak geliyor
                input_tokens = usage.get('input_tokens', 0)
                output_tokens = usage.get('output_tokens', 0) 
                total_tokens = usage.get('total_tokens', 0)
            elif hasattr(response, 'response_metadata') and 'token_usage' in response.response_metadata:
                # Alternatif structure
                usage = response.response_metadata['token_usage']
                input_tokens = usage.get('prompt_tokens', 0)
                output_tokens = usage.get('completion_tokens', 0)
                total_tokens = usage.get('total_tokens', 0)
            else:
                # Manuel token sayımı (yaklaşık)
                input_text = str(response.content) if hasattr(response, 'content') else ""
                input_tokens = len(input_text.split()) * 1.3  # Yaklaşık token hesabı
                output_tokens = len(input_text.split()) * 0.7
                total_tokens = input_tokens + output_tokens
                
                logger.warning(f"Token usage metadata bulunamadı, yaklaşık hesaplama yapıldı")
            
            self.token_usage["input_tokens"] += int(input_tokens)
            self.token_usage["output_tokens"] += int(output_tokens)
            self.token_usage["total_tokens"] += int(total_tokens)
            
            # Detaylı token tracking kaydet
            action_token_info = {
                "iteration": iteration,
                "action_type": action_type,
                "input_tokens": int(input_tokens),
                "output_tokens": int(output_tokens),
                "total_tokens": int(total_tokens),
                "timestamp": time.time()
            }
            self.detailed_token_usage.append(action_token_info)
            
            logger.info(f"🎯 İterasyon {iteration} ({action_type}) - Token: Input: {int(input_tokens)}, Output: {int(output_tokens)}, Total: {int(total_tokens)}")
            logger.info(f"📊 Toplam Token: Input: {self.token_usage['input_tokens']}, Output: {self.token_usage['output_tokens']}, Total: {self.token_usage['total_tokens']}")
            
        except Exception as e:
            logger.error(f"Token logging hatası: {e}")
            # Debug için response structure'ını logla
            logger.debug(f"Response attributes: {dir(response)}")
            if hasattr(response, '__dict__'):
                logger.debug(f"Response dict: {response.__dict__}")
    
    def log_llm_prompt(self, system_prompt: str, user_prompt: str, iteration: int):
        """LLM'e gönderilen prompt'u detaylı olarak logla"""
        try:
            logger.info(f"\n{'='*60}")
            logger.info(f"LLM PROMPT LOGGING - İterasyon {iteration}")
            logger.info(f"{'='*60}")
            
            # System prompt özeti
            system_lines = system_prompt.split('\n')
            schema_start = -1
            schema_end = -1
            
            # Yeni schema formatını ara
            for i, line in enumerate(system_lines):
                if "Neo4j Schema Yapısı:" in line or "Neo4j Schema:" in line:
                    schema_start = i
                elif schema_start > -1 and (line.startswith("## 📊 DOMAIN ARCHITECTURE:") or line.startswith("## 🧠 DECISION FRAMEWORK:")):
                    schema_end = i
                    break
            
            logger.info(f"📋 SYSTEM PROMPT ÖZET:")
            logger.info(f"   - Toplam satır: {len(system_lines)}")
            logger.info(f"   - Toplam karakter: {len(system_prompt)}")
            
            if schema_start > -1:
                logger.info(f"   - ✅ Schema bilgisi başlangıç: Satır {schema_start + 1}")
                if schema_end > -1:
                    logger.info(f"   - Schema bilgisi bitiş: Satır {schema_end}")
                    schema_content = '\n'.join(system_lines[schema_start:schema_end])
                    logger.info(f"   - Schema bilgisi uzunluk: {len(schema_content)} karakter")
                    
                    # Yeni schema formatında Nodes ve Rels satırlarını ara
                    nodes_line = None
                    rels_line = None
                    patterns_line = None
                    
                    for line in system_lines[schema_start:schema_end]:
                        if line.startswith("Nodes:"):
                            nodes_line = line
                        elif line.startswith("Rels:"):
                            rels_line = line
                        elif line.startswith("Patterns:"):
                            patterns_line = line
                    
                    if nodes_line:
                        logger.info(f"   - ✅ Nodes satırı bulundu: {nodes_line[:100]}...")
                    if rels_line:
                        logger.info(f"   - ✅ Rels satırı bulundu: {rels_line[:100]}...")
                    if patterns_line:
                        logger.info(f"   - ✅ Patterns satırı bulundu: {patterns_line[:100]}...")
                        
                    # Schema kompaktlığını kontrol et
                    if nodes_line and rels_line and patterns_line:
                        logger.info(f"   - ✅ Kompakt schema formatı: Token-optimized")
                    else:
                        logger.warning(f"   - ⚠️ Schema formatı eksik olabilir")
            else:
                logger.warning("   ⚠️ Schema bilgisi system prompt'ta bulunamadı!")
            
            # User prompt özeti
            user_lines = user_prompt.split('\n')
            logger.info(f"📨 USER PROMPT ÖZET:")
            logger.info(f"   - Toplam satır: {len(user_lines)}")
            logger.info(f"   - Toplam karakter: {len(user_prompt)}")
            logger.info(f"   - İlk 3 satır: {user_lines[:3]}")
            
            # Context memory kontrol
            if "DAHA ÖNCE BULUNAN BAŞARILI BİLGİLER" in user_prompt:
                logger.info(f"   - ✅ Context memory bulundu")
            else:
                logger.info(f"   - ℹ️ Context memory yok (ilk iterasyon)")
            
            # Mevcut durum bilgisi kontrol
            if "Mevcut Durum:" in user_prompt:
                logger.info(f"   - ✅ Mevcut durum bilgisi bulundu")
            else:
                logger.info(f"   - ℹ️ Mevcut durum bilgisi yok")
            
            logger.info(f"{'='*60}\n")
            
        except Exception as e:
            logger.error(f"LLM prompt logging hatası: {e}")
    
    def summarize_finding(self, action_description: str, result, finding_type: str) -> str:
        """Bulguları LLM ile özetle - token tasarrufu için"""
        try:
            if finding_type == "structured_data":
                if isinstance(result, list) and len(result) > 0:
                    # İlk birkaç sonucu özetle
                    sample_data = result[:3] if len(result) > 3 else result
                    sample_text = str(sample_data)[:500]  # İlk 500 karakter
                    
                    summary_prompt = f"""
Bu structured data sonuçlarını kısa ve öz şekilde özetle (max 150 kelime):

Action: {action_description}
Sonuç sayısı: {len(result)}
Örnek data: {sample_text}

Önemli bulgular ve anahtar bilgiler nedir?
"""
                else:
                    return f"Structured data: {len(result) if result else 0} kayıt"
                    
            elif finding_type == "vector_search":
                if isinstance(result, list) and len(result) > 0:
                    top_chunks = result[:3]  # İlk 3 chunk
                    chunk_info = []
                    for chunk in top_chunks:
                        # Dict formatında geliyorsa dict olarak erişim
                        if isinstance(chunk, dict):
                            doc_name = chunk.get('document_name', 'Unknown')
                            score = chunk.get('relevance_score', 0.0)
                            text = chunk.get('text', '')
                            chunk_info.append(f"Doc: {doc_name}, Score: {score:.3f}, Preview: {text[:100]}...")
                        else:
                            # ChunkInfo objesi ise normal erişim
                            chunk_info.append(f"Doc: {chunk.document_name}, Score: {chunk.relevance_score:.3f}, Preview: {chunk.text[:100]}...")
                    
                    summary_prompt = f"""
Bu vector search sonuçlarını kısa ve öz şekilde özetle (max 150 kelime):

Search: {action_description}
Toplam chunk: {len(result)}
En iyi sonuçlar:
{chr(10).join(chunk_info)}

Anahtar bulgular ve önemli bilgiler nedir?
"""
                else:
                    return f"Vector search: 0 sonuç"
            else:
                return f"{finding_type}: {str(result)[:200]}..."
            
            # LLM ile özetle
            response = self.llm.invoke([
                SystemMessage(content="Sen başarılı bulgları özetleyen bir asistansın. Kısa, net ve anahtar bilgileri vurgulayan özetler yap."),
                HumanMessage(content=summary_prompt)
            ])
            
            return response.content.strip()
            
        except Exception as e:
            logger.error(f"Özet oluşturma hatası: {e}")
            # Fallback: basit özet
            if finding_type == "structured_data":
                return f"Structured data: {len(result) if result else 0} kayıt. Örnek: {str(result[:1])[:100]}..." if result else "Sonuç yok"
            elif finding_type == "vector_search":
                # Dict format için güvenli erişim
                if result and isinstance(result[0], dict):
                    return f"Vector search: {len(result)} chunk. En iyi relevance: {result[0].get('relevance_score', 0.0):.3f}"
                elif result:
                    return f"Vector search: {len(result)} chunk. En iyi relevance: {result[0].relevance_score:.3f}"
                else:
                    return "Vector search: 0 sonuç"
            return f"{finding_type}: {str(result)[:100]}..."

    def add_successful_finding(self, state: 'AgentState', iteration: int, action: str, finding: str, relevance_score: float = 0.0, raw_data: Any = None):
        """Başarılı bulguyu context memory ve state'e ekle"""
        # State'e SuccessfulFinding objesi olarak ekle (ham veri ile)
        state_finding = SuccessfulFinding(
            iteration=iteration,
            action_type=action,
            summary=finding,
            relevance_score=relevance_score,
            raw_data=raw_data
        )
        state.successful_findings.append(state_finding)
        
        # Context memory'i güncelle
        self.update_context_memory(state)
        
        logger.info(f"Başarılı bulgu eklendi - İterasyon {iteration}: {action} -> {finding}...")
    
    def update_context_memory(self, state: 'AgentState'):
        """Başarılı bulgulardan context prompt oluştur - AgentState ile uyumlu"""
        if not state.successful_findings:
            self.context_memory = ""
            return
        
        context_prompt = "## DAHA ÖNCE BULUNAN BAŞARILI BİLGİLER:\n\n"
        
        for finding in state.successful_findings[-5:]:  # Son 5 başarılı bulguyu al
            context_prompt += f"**Adım {finding.iteration} - {finding.action_type.upper()}:**\n"
            context_prompt += f"{finding.summary}\n"
            
            # Önemli ham veri örnekleri ekle (özellikle cypher_query için)
            if finding.action_type == 'cypher_query' and finding.raw_data:
                context_prompt += f"\n**HAM VERİ ÖRNEKLERİ (İLK 3 SATIR):**\n"
                raw_data = finding.raw_data
                if isinstance(raw_data, list) and len(raw_data) > 0:
                    for i, row in enumerate(raw_data[:3], 1):  # İlk 3 satır
                        if isinstance(row, dict):
                            # Policy bilgilerini özel olarak çıkar
                            if 'p' in row and isinstance(row['p'], dict):
                                policy_name = row['p'].get('name', 'Bilinmeyen')
                                context_prompt += f"  Satır {i}: Poliçe adı: '{policy_name}'\n"
                            elif 'c' in row and isinstance(row['c'], dict):
                                customer_name = row['c'].get('fullName', row['c'].get('name', 'Bilinmeyen'))
                                context_prompt += f"  Satır {i}: Müşteri adı: '{customer_name}'\n"
                            else:
                                # Genel dict gösterimi
                                context_prompt += f"  Satır {i}: {str(row)[:100]}...\n"
                context_prompt += "\n"
            
            context_prompt += "---\n"
        
        context_prompt += "\n**BU BİLGİLERİ DİKKATE ALARAK SONRAKI ADIMI BELİRLE!**\n\n"
        self.context_memory = context_prompt
    
    # def entity_driven_search(self, search_term: str, state: AgentState) -> List[ChunkInfo]:
    #     """
    #     Entity-driven arama stratejisi:
    #     1. __Entity__ node'larında arama yap
    #     2. Bulunan entity'lerden chunk'lara ulaş
    #     3. Chunk relationship'lerini takip et
    #     4. Embedding ile semantic matching yap
    #     """
    #     logger.info(f"Entity-driven search başlatılıyor: {search_term}")
        
    #     # Adım 1: Entity'lerde arama
    #     entities = self.search_entities(search_term)
    #     if not entities:
    #         logger.info("Hiç entity bulunamadı")
    #         return []
        
    #     logger.info(f"{len(entities)} entity bulundu")
        
    #     # Entity'leri state'e ekle
    #     for entity in entities:
    #         state.discovered_entities.append(entity)
        
    #     # Adım 2: Entity'lerden chunk'lara ulaş
    #     primary_chunks = self.find_chunks_from_entities([e['id'] for e in entities], state)
        
    #     # Adım 3: İlişkili chunk'ları da bul
    #     all_chunks = primary_chunks.copy()
    #     for chunk in primary_chunks:
    #         related_chunks = self.find_related_chunks(chunk.chunk_id, state)
    #         all_chunks.extend(related_chunks)
        
    #     # Duplikasyon temizle
    #     unique_chunks = {}
    #     for chunk in all_chunks:
    #         if chunk.chunk_id not in unique_chunks:
    #             unique_chunks[chunk.chunk_id] = chunk
        
    #     chunks = list(unique_chunks.values())
    #     logger.info(f"Toplam {len(chunks)} unique chunk bulundu")
        
    #     # Adım 4: Embedding ile semantic matching
    #     if chunks:
    #         chunks = self.rank_chunks_by_semantic_similarity(chunks, search_term)
        
    #     return chunks[:state.max_chunks_limit]
    
    # def search_entities(self, search_term: str) -> List[Dict[str, Any]]:
    #     """Gerçek schema'ya göre entity arama - Customer, Policy, PolicyType vb."""
    #     try:
    #         # Gerçek node tiplerinde arama stratejileri
    #         strategies = [
    #             # Customer araması
    #             f"""
    #             MATCH (c:Customer)
    #             WHERE any(prop IN keys(c) WHERE 
    #                 prop <> 'embedding' AND 
    #                 c[prop] IS NOT NULL AND 
    #                 apoc.text.clean(toString(c[prop])) CONTAINS apoc.text.clean('{search_term}'))
    #             RETURN c.name as id, 'Customer' as type, labels(c) as labels, c as entity
    #             LIMIT 10
    #             """,
    #             # Policy araması  
    #             f"""
    #             MATCH (p:Policy)
    #             WHERE any(prop IN keys(p) WHERE 
    #                 prop <> 'embedding' AND 
    #                 p[prop] IS NOT NULL AND 
    #                 apoc.text.clean(toString(p[prop])) CONTAINS apoc.text.clean('{search_term}'))
    #             RETURN p.policy_number as id, 'Policy' as type, labels(p) as labels, p as entity
    #             LIMIT 10
    #             """,
    #             # PolicyType araması
    #             f"""
    #             MATCH (pt:PolicyType)
    #             WHERE any(prop IN keys(pt) WHERE 
    #                 prop <> 'embedding' AND 
    #                 pt[prop] IS NOT NULL AND 
    #                 apoc.text.clean(toString(pt[prop])) CONTAINS apoc.text.clean('{search_term}'))
    #             RETURN pt.name as id, 'PolicyType' as type, labels(pt) as labels, pt as entity
    #             LIMIT 10
    #             """,
    #             # PolicyYear araması
    #             f"""
    #             MATCH (py:PolicyYear)
    #             WHERE py.year CONTAINS '{search_term}'
    #             RETURN py.year as id, 'PolicyYear' as type, labels(py) as labels, py as entity
    #             LIMIT 10
    #             """
    #         ]
            
    #         all_entities = []
    #         for strategy in strategies:
    #             success, result = self.execute_cypher_query(strategy)
    #             if success and result:
    #                 all_entities.extend(result)
    #                 if len(all_entities) >= 15:  # Yeterince entity bulundu
    #                     break
            
    #         # Duplikasyon temizle
    #         unique_entities = {}
    #         for entity in all_entities:
    #             entity_id = entity.get('id')
    #             if entity_id and entity_id not in unique_entities:
    #                 unique_entities[entity_id] = entity
            
    #         logger.info(f"Gerçek schema'da {len(unique_entities)} entity bulundu: {list(unique_entities.keys())[:5]}")
    #         return list(unique_entities.values())
            
    #     except Exception as e:
    #         logger.error(f"Entity arama hatası: {e}")
    #         return []
    
    # def find_related_chunks(self, chunk_id: str, state: AgentState) -> List[ChunkInfo]:
    #     """Bir chunk'ın ilişkili chunk'larını bul - NEXT_CHUNK relationship'ini kullan"""
    #     try:
    #         # NEXT_CHUNK ilişkileri takip et (sıralı chunk'lar)
    #         query = f"""
    #         MATCH (c1:Chunk {{chunkId: '{chunk_id}'}})
    #         OPTIONAL MATCH (c1)-[:NEXT_CHUNK]->(next:Chunk)
    #         OPTIONAL MATCH (prev:Chunk)-[:NEXT_CHUNK]->(c1)
    #         WITH collect(DISTINCT next) + collect(DISTINCT prev) as related_chunks
    #         UNWIND related_chunks as c2
    #         WITH c2 WHERE c2 IS NOT NULL AND c2.chunkId <> '{chunk_id}'
    #         OPTIONAL MATCH (c2)-[:PART_OF]->(d:Document)
    #         RETURN c2.chunkId as chunk_id, c2.text as text, c2.page_number as page_number,
    #                d.fileName as document_name
    #         LIMIT 10
    #         """
            
    #         success, result = self.execute_cypher_query(query)
    #         if not success or not result:
    #             logger.info(f"Chunk {chunk_id} için ilişkili chunk bulunamadı")
    #             return []
            
    #         chunks = []
    #         for row in result:
    #             chunk_info = ChunkInfo(
    #                 chunk_id=row['chunk_id'] or "",
    #                 text=row['text'] or "",
    #                 page_number=row['page_number'],
    #                 document_name=row['document_name'] or "Unknown"
    #             )
    #             chunks.append(chunk_info)
            
    #         logger.info(f"Chunk {chunk_id} için {len(chunks)} ilişkili chunk bulundu")
    #         return chunks
            
    #     except Exception as e:
    #         logger.error(f"İlişkili chunk arama hatası: {e}")
    #         return []
    
    # def rank_chunks_by_semantic_similarity(self, chunks: List[ChunkInfo], query: str) -> List[ChunkInfo]:
    #     """Chunk'ları semantic similarity'ye göre sırala - Önceden hesaplanmış embedding'leri kullan"""
    #     try:
    #         if not chunks:
    #             return chunks
            
    #         # Query embedding'i al (sadece bir kez)
    #         query_embedding = self.embedding_model.embed_query(query)
            
    #         # Chunk'ların Neo4j'den embedding'lerini al
    #         chunk_ids = [chunk.chunk_id for chunk in chunks if chunk.chunk_id]
    #         if not chunk_ids:
    #             return chunks
            
    #         # Batch olarak embedding'leri çek
    #         chunk_ids_str = "', '".join(chunk_ids)
    #         embedding_query = f"""
    #         MATCH (c:Chunk)
    #         WHERE c.chunkId IN ['{chunk_ids_str}']
    #         RETURN c.chunkId as chunk_id, c.embedding as embedding
    #         """
            
    #         success, embedding_results = self.execute_cypher_query(embedding_query)
    #         if not success or not embedding_results:
    #             logger.warning("Chunk embedding'leri alınamadı, fallback similarity kullanılıyor")
    #             return self._fallback_similarity_ranking(chunks, query)
            
    #         # Embedding'leri chunk'lara eşle - güvenli erişim
    #         embedding_map = {}
    #         for result in embedding_results:
    #             chunk_id = result.get('chunk_id')
    #             embedding = result.get('embedding')
    #             if chunk_id and embedding is not None:
    #                 # Embedding'in list/array olup olmadığını kontrol et
    #                 if isinstance(embedding, (list, tuple, np.ndarray)) and len(embedding) > 0:
    #                     embedding_map[chunk_id] = embedding
    #                 else:
    #                     logger.warning(f"Chunk {chunk_id} için geçersiz embedding: {type(embedding)}")
            
    #         # Similarity hesapla
    #         for chunk in chunks:
    #             if chunk.chunk_id in embedding_map:
    #                 chunk_embedding = embedding_map[chunk.chunk_id]
    #                 try:
    #                     if len(chunk_embedding) == len(query_embedding):
    #                         similarity = float(cosine_similarity([query_embedding], [chunk_embedding])[0][0])
    #                         chunk.relevance_score = similarity
    #                     else:
    #                         logger.warning(f"Chunk {chunk.chunk_id} embedding dimension mismatch: {len(chunk_embedding)} vs {len(query_embedding)}")
    #                         chunk.relevance_score = 0.0
    #                 except Exception as embed_error:
    #                     logger.warning(f"Chunk {chunk.chunk_id} similarity hesaplama hatası: {embed_error}")
    #                     chunk.relevance_score = 0.0
    #             else:
    #                 chunk.relevance_score = 0.0
            
    #         # Similarity'ye göre sırala
    #         chunks.sort(key=lambda x: x.relevance_score, reverse=True)
            
    #         logger.info(f"Chunk'lar önceden hesaplanmış embedding'lerle sıralandı. En yüksek score: {chunks[0].relevance_score:.3f}")
    #         return chunks
            
    #     except Exception as e:
    #         logger.error(f"Semantic ranking hatası: {e}")
    #         return self._fallback_similarity_ranking(chunks, query)
    
    # def _fallback_similarity_ranking(self, chunks: List[ChunkInfo], query: str) -> List[ChunkInfo]:
    #     """Fallback: Text-based similarity ranking"""
    #     try:
    #         query_embedding = self.embedding_model.embed_query(query)
            
    #         for chunk in chunks:
    #             if chunk.text:
    #                 # Sadece chunk text'inin tamamı için embedding al
    #                 text_embedding = self.embedding_model.embed_query(chunk.text)
    #                 similarity = float(cosine_similarity([query_embedding], [text_embedding])[0][0])
    #                 chunk.relevance_score = similarity
    #             else:
    #                 chunk.relevance_score = 0.0
            
    #         chunks.sort(key=lambda x: x.relevance_score, reverse=True)
    #         logger.info(f"Fallback similarity ranking uygulandı. En yüksek score: {chunks[0].relevance_score:.3f}")
    #         return chunks
            
    #     except Exception as e:
    #         logger.error(f"Fallback similarity ranking hatası: {e}")
    #         return chunks

    def find_chunks_from_entities(self, entity_ids: List[str], state: AgentState) -> List[ChunkInfo]:
        """Entity'lerden chunk'lara ulaş - Yeni schema'ya göre"""
        try:
            if not entity_ids:
                return []
                
            # Yeni schema'da entity'lerden document'lara, oradan chunk'lara ulaşma stratejisi
            strategies = [
                # Customer → Policy → Document → Chunk
                """
                UNWIND $entity_ids as entity_id
                MATCH (c:Customer) WHERE c.name = entity_id
                MATCH (c)-[:HAS_POLICY]->(p:Policy)-[:DOCUMENTED_IN]->(d:Document)
                MATCH (d)-[:FIRST_CHUNK]->(first:Chunk)
                MATCH (first)-[:NEXT_CHUNK*0..20]->(chunks:Chunk)
                RETURN DISTINCT 
                    chunks.chunkId as chunk_id,
                    chunks.text as text,
                    chunks.page_number as page_number,
                    d.fileName as document_name,
                    d as document_metadata
                LIMIT $limit
                """,
                
                # Policy direkt → Document → Chunk
                """
                UNWIND $entity_ids as entity_id
                MATCH (p:Policy) WHERE p.policy_number = entity_id OR p.name = entity_id
                MATCH (p)-[:DOCUMENTED_IN]->(d:Document)
                MATCH (d)-[:FIRST_CHUNK]->(first:Chunk)
                MATCH (first)-[:NEXT_CHUNK*0..20]->(chunks:Chunk)
                RETURN DISTINCT 
                    chunks.chunkId as chunk_id,
                    chunks.text as text,
                    chunks.page_number as page_number,
                    d.fileName as document_name,
                    d as document_metadata
                LIMIT $limit
                """,
                
                # PolicyType → Policy → Document → Chunk
                """
                UNWIND $entity_ids as entity_id
                MATCH (pt:PolicyType) WHERE pt.name = entity_id
                MATCH (p:Policy)-[:HAS_TYPE]->(pt)
                MATCH (p)-[:DOCUMENTED_IN]->(d:Document)
                MATCH (d)-[:FIRST_CHUNK]->(first:Chunk)
                MATCH (first)-[:NEXT_CHUNK*0..10]->(chunks:Chunk)
                RETURN DISTINCT 
                    chunks.chunkId as chunk_id,
                    chunks.text as text,
                    chunks.page_number as page_number,
                    d.fileName as document_name,
                    d as document_metadata
                LIMIT $limit
                """
            ]
            
            all_chunks = []
            for strategy in strategies:
                result = self.graph.query(strategy, {
                    'entity_ids': entity_ids,
                    'limit': state.max_chunks_limit
                })
                
                if result:
                    for row in result:
                        chunk_info = ChunkInfo(
                            chunk_id=row['chunk_id'],
                            text=row['text'] or "",
                            page_number=row['page_number'],
                            document_name=row['document_name'] or "Unknown",
                            document_metadata=dict(row['document_metadata']) if row['document_metadata'] else {}
                        )
                        all_chunks.append(chunk_info)
                    
                    logger.info(f"Entity strategy başarılı: {len(result)} chunk bulundu")
                    break  # İlk başarılı strategy ile devam et
            
            # Eğer hiç sonuç bulunamazsa, chunk text'inde entity araması yap
            if not all_chunks:
                logger.info("Entity relationship'leri ile chunk bulunamadı, text araması yapılıyor...")
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
                
                for row in result:
                    chunk_info = ChunkInfo(
                        chunk_id=row['chunk_id'],
                        text=row['text'] or "",
                        page_number=row['page_number'],
                        document_name=row['document_name'] or "Unknown",
                        document_metadata=dict(row['document_metadata']) if row['document_metadata'] else {}
                    )
                    all_chunks.append(chunk_info)
                    
            logger.info(f"Entity'lerden toplam {len(all_chunks)} chunk bulundu")
            return all_chunks
            
        except Exception as e:
            logger.error(f"Chunk arama hatası: {e}")
            return []
    
    # def find_chunks_by_graph_pattern(self, pattern_query: str, params: Dict = None, state: AgentState = None) -> List[ChunkInfo]:
    #     """Graph pattern ile chunk arama"""
    #     try:
    #         if params is None:
    #             params = {}
    #         if state is None:
    #             state = AgentState("")
                
    #         # Pattern query'yi chunk'lara yönlendir
    #         full_query = f"""
    #         {pattern_query}
    #         OPTIONAL MATCH (entity)-[:HAS_ENTITY]-(c:Chunk)
    #         OPTIONAL MATCH (c)-[:PART_OF]->(d:Document)
    #         RETURN DISTINCT 
    #             c.chunkId as chunk_id,
    #             c.text as text,
    #             c.page_number as page_number,
    #             d.fileName as document_name,
    #             d as document_metadata,
    #             entity.id as related_entity
    #         LIMIT {state.max_chunks_limit}
    #         """
            
    #         result = self.graph.query(full_query, params)
            
    #         chunks = []
    #         for row in result:
    #             if row['chunk_id']:  # Chunk varsa
    #                 chunk_info = ChunkInfo(
    #                     chunk_id=row['chunk_id'],
    #                     text=row['text'] or "",
    #                     page_number=row['page_number'],
    #                     document_name=row['document_name'] or "Unknown",
    #                     document_metadata=dict(row['document_metadata']) if row['document_metadata'] else {}
    #                 )
    #                 chunks.append(chunk_info)
                    
    #         logger.info(f"Graph pattern ile {len(chunks)} chunk bulundu")
    #         return chunks
            
    #     except Exception as e:
    #         logger.error(f"Graph pattern chunk arama hatası: {e}")
    #         return []
    
    def calculate_text_relevance(self, chunk_info: ChunkInfo, question: str) -> ChunkInfo:
        """Chunk text'ini böl ve soru ile ilişkililik hesapla - Neo4j chunk embedding kullan"""
        try:
            if not chunk_info.text:
                return chunk_info
                
            # Chunk'ın Neo4j'deki embedding'ini al (chunkId field kullan)
            chunk_embedding_query = """
            MATCH (c:Chunk {chunkId: $chunk_id})
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
                node.chunkId as chunk_id,
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
            
            # Bulunan chunk'ları detaylı logla
            logger.info(f"🔍 VECTOR SEARCH SONUÇLARI:")
            for i, chunk in enumerate(chunks[:5], 1):  # İlk 5 chunk'ı göster
                logger.info(f"📄 Chunk {i} (Score: {chunk.relevance_score:.3f}):")
                logger.info(f"   - Document: {chunk.document_name}")
                logger.info(f"   - Page: {chunk.page_number}")
                logger.info(f"   - Chunk ID: {chunk.chunk_id}")
                logger.info(f"   - Text Length: {len(chunk.text)} karakter")
                logger.info(f"   - FULL TEXT:")
                logger.info(f"     {chunk.text}")
                logger.info("   " + "="*80)
                    
            # Relevance'a göre sırala ve döndür (zaten sıralı ama emin olmak için)
            chunks.sort(key=lambda x: x.relevance_score, reverse=True)
            
            logger.info(f"Vector search sonucu: {len(chunks)} chunk")
            return chunks
            
        except Exception as e:
            logger.error(f"Vector search hatası: {e}")
            return []

    def parse_agent_response(self, response: str) -> Tuple[str, str, str]:
        """Agent cevabını parse et - yıldızlı formatları da destekle"""
        # Observation, Thought, Action'ı ayır
        observation = ""
        thought = ""
        action = ""
        action_content = ""
        
        lines = response.strip().split('\n')
        current_section = None
        
        for line in lines:
            line = line.strip()
            # Yıldızlı formatları da destekle
            if line.startswith('Observation:') or line.startswith('**Observation:**'):
                current_section = 'observation'
                observation = line.replace('**Observation:**', '').replace('Observation:', '').strip()
            elif line.startswith('Thought:') or line.startswith('**Thought:**'):
                current_section = 'thought'
                thought = line.replace('**Thought:**', '').replace('Thought:', '').strip()
            elif line.startswith('Action:') or line.startswith('**Action:**'):
                current_section = 'action'
                action = line.replace('**Action:**', '').replace('Action:', '').strip()
            elif line.startswith('Query:'):
                action_content = line.replace('Query:', '').strip()
            elif line.startswith('Answer:'):
                action_content = line.replace('Answer:', '').strip()
            elif line.startswith('Content:') or line.startswith('**Content:**'):
                # "Content:" prefix'ini kaldır ve action_content'e ekle
                content_on_same_line = line.replace('**Content:**', '').replace('Content:', '').strip()
                if content_on_same_line:
                    action_content = content_on_same_line
                current_section = 'content'  # Content section'a geç
            elif current_section and line and not line.startswith('```'):
                # Kod blokları hariç
                if current_section == 'observation':
                    observation += ' ' + line
                elif current_section == 'thought':
                    thought += ' ' + line
                elif current_section == 'action':
                    if not action:
                        action = line
                    else:
                        # Eğer line "Content:" ile başlamıyorsa ve cypher/query değilse action_content'e ekle
                        if not line.startswith('Content:') and not line.startswith('**Content:**'):
                            # Kod blokları action_content'e git
                            if 'MATCH' in line or 'RETURN' in line or 'WHERE' in line:
                                action_content += ' ' + line
                            elif action_content == "":  # İlk content satırı
                                action_content = line
                            else:
                                action_content += ' ' + line
                elif current_section == 'content':
                    # Content section'dayken tüm satırları action_content'e ekle
                    if action_content:
                        action_content += '\n' + line  # Çok satırlı content için yeni satır ekle
                    else:
                        action_content = line  # İlk content satırı
        
        # Kod bloklarını temizle
        if action_content:
            action_content = action_content.replace('```cypher', '').replace('```', '').strip()
        
        return observation.strip(), thought.strip(), (action.strip(), action_content.strip())
    
    def solve_question(self, user_question: str) -> Dict[str, Any]:
        """Ana problem çözme fonksiyonu - ReAct pattern ile Chunk-based arama"""
        
        # Her soru için cache'i temizle
        self.context_memory = ""
        self.token_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        self.detailed_token_usage = []  # Detaylı token tracking'i temizle
        
        logger.info(f"Soru çözülüyor: {user_question}")
        
        # Agent state'i başlat
        state = AgentState(question=user_question, original_question=user_question)
        
        # Schema-based system prompt'u al (cache'den veya oluştur)
        system_prompt = self.get_system_prompt()
        
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
            
            # Context memory ve mevcut durum bilgilerini birleştir
            prompt = f"{self.context_memory}{current_observation}{context_info}\n\nBu duruma göre next action'ını belirle:"
            
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=prompt)
            ]
            
            # Conversation history ekle
            for entry in conversation_history[-4:]:  # Son 4 adımı tut
                messages.append(HumanMessage(content=entry))
            
            # LLM prompt'unu logla
            self.log_llm_prompt(system_prompt, prompt, state.iteration_count)
            
            try:
                response = self.llm.invoke(messages)
                agent_response = response.content
                
                # Response'u parse et - action type'ını almak için önce parse
                observation, thought, (action, action_content) = self.parse_agent_response(agent_response)
                
                # Token kullanımını action type ile logla
                self.log_token_usage(response, state.iteration_count, action)
                
                # DEBUG: LLM response'unu logla
                logger.info(f"🔍 DEBUG - Raw LLM Response:\n{agent_response}")
                logger.info(f"🔍 DEBUG - Parsed: Action='{action}', Content='{action_content}'")
                
                
                conversation_history.append(f"Thought: {thought}\nAction: {action}\nContent: {action_content}")
                
                # Action'ı uygula
                if action == "final_answer":
                    # Final answer - temiz cevabı direkt kullan, sadece referansları ekle
                    clean_answer = action_content.strip()
                    
                    # LLM yorumlama aktifse eski metodu kullan, değilse sadece referans ekle
                    if self.enable_llm_interpretation:
                        logger.info("🤖 LLM yorumlama aktif - Ham veriler yorumlanıyor...")
                        
                        # Ham veri bölümlerini topla (eski yöntem için)
                        raw_data_sections = []
                        
                        # Successful findings (cypher ve vector_search raw sonuçları)
                        if state.successful_findings:
                            findings_data = []
                            for finding in state.successful_findings:
                                if finding.raw_data:  # Ham JSON data varsa
                                    # Neo4j DateTime objelerini serialize edilebilir hale getir
                                    serialized_data = serialize_neo4j_data(finding.raw_data)
                                    findings_data.append({
                                        "action_type": finding.action_type,
                                        "iteration": finding.iteration,
                                        "raw_data": serialized_data,
                                        "summary": finding.summary
                                    })
                            if findings_data:
                                raw_data_sections.append(f"=== CYPHER & VECTOR SEARCH HAM VERİLER ===\n{json.dumps(findings_data, ensure_ascii=False, indent=2)}")
                        
                        # Vector search chunk'ları (varsa)
                        if state.discovered_chunks:
                            sorted_chunks = sorted(state.discovered_chunks, key=lambda x: x.relevance_score, reverse=True)
                            chunk_content = []
                            for i, chunk in enumerate(sorted_chunks[:10], 1):  # En iyi 10 chunk
                                chunk_content.append(f"[Kaynak {i}: {chunk.document_name}, Sayfa {chunk.page_number}]\n{chunk.text.strip()}")
                            raw_data_sections.append(f"=== KAYNAK BİLGİLER ===\n" + "\n\n".join(chunk_content))
                        
                        # Raw final answer'ı oluştur
                        if raw_data_sections:
                            raw_final_answer = f"{clean_answer}\n\n--- HAM VERİLER ---\n" + "\n\n".join(raw_data_sections)
                        else:
                            raw_final_answer = clean_answer
                        
                        # Ham veriyi LLM'e aktar
                        final_answer = self.interpret_final_answer_with_llm(raw_final_answer, user_question, state.discovered_chunks)
                    else:
                        logger.info("� Temiz cevap + referans modu - Gereksiz LLM çağrısı yok")
                        # Sadece temiz cevabı kullan ve referansları ekle
                        final_answer = self.format_final_answer_with_references(clean_answer, state)
                        
                    current_observation = f"Final answer verildi: {final_answer[:200]}..."
                    logger.info(f"Agent final answer verdi: {final_answer[:200]}...")
                    break
                        
                elif action == "cypher_query":
                    success, result = self.execute_cypher_query(action_content)
                    if success and result:
                        # Cypher sonuçlarını basit şekilde işle - otomatik chunk arama yapmadan
                        data_summary = []
                        for row in result[:5]:  # İlk 5 sonucu özetle
                            row_summary = []
                            for key, value in row.items():
                                if value is not None and str(value).strip():
                                    if isinstance(value, list):
                                        row_summary.append(f"{key}: {', '.join(map(str, value))}")
                                    else:
                                        row_summary.append(f"{key}: {value}")
                            if row_summary:
                                data_summary.append(", ".join(row_summary))
                        
                        current_observation = f"Cypher sorgusu başarılı: {len(result)} sonuç bulundu. Örnek veriler: {'; '.join(data_summary[:2])}. Bu veri soru için yeterliyse final_answer ver, eğer detaylı içerik gerekiyorsa vector_search yap."
                        
                        # Başarılı cypher sorgu bulgusunu kaydet
                        summary = self.summarize_finding(f"Cypher Query: {action_content}", result, "structured_data")
                        self.add_successful_finding(
                            state,  # state parametresi eklendi
                            state.iteration_count,
                            "cypher_query", 
                            summary,
                            0.9,  # Yüksek relevance - sonuçlar mevcut
                            result  # Ham cypher sonuçları
                        )
                        
                    else:
                        current_observation = f"Cypher sorgusu başarısız: {result}. Farklı bir sorgu dene."
                        
                elif action == "vector_search":
                    # Önceki Cypher bulgularından ilgili belgeleri çıkar
                    relevant_documents = []
                    for finding in state.successful_findings:
                        if finding.action_type == 'cypher_query' and finding.raw_data:
                            # Cypher sorgu sonuçlarından belge adlarını çıkar
                            raw_data = finding.raw_data
                            if isinstance(raw_data, list):
                                for row in raw_data:
                                    if isinstance(row, dict) and 'documentFileName' in row:
                                        doc_name = row['documentFileName']
                                        if doc_name and doc_name not in relevant_documents:
                                            relevant_documents.append(doc_name)
                                    elif isinstance(row, dict) and 'd' in row and isinstance(row['d'], dict):
                                        doc_name = row['d'].get('fileName')
                                        if doc_name and doc_name not in relevant_documents:
                                            relevant_documents.append(doc_name)
                    
                    # Akıllı belge filtrelemesi ile vector search
                    success, result = self.execute_document_filtered_vector_search(
                        action_content, 
                        user_question, 
                        limit=15, 
                        relevant_documents=relevant_documents if relevant_documents else None
                    )
                    if success and result:
                        # Vector search sonuçlarını işle
                        doc_names = list(set([chunk['document_name'] for chunk in result if chunk.get('document_name')]))
                        doc_info = f" (Belgeler: {', '.join(doc_names[:3])}{'...' if len(doc_names) > 3 else ''})" if doc_names else ""
                        current_observation = f"Vector search başarılı: {len(result)} chunk bulundu{doc_info}. En yüksek relevance score: {result[0]['relevance_score']:.3f}. Chunk'lar dokümanlarda '{action_content}' ile ilgili bilgileri içeriyor."
                        
                        # Başarılı vector search bulgusunu kaydet
                        summary = self.summarize_finding(f"Vector Search: {action_content}", result, "vector_search")
                        self.add_successful_finding(
                            state,  # state parametresi eklendi
                            state.iteration_count,
                            "vector_search", 
                            summary,
                            result[0]['relevance_score'] if result else 0.0,
                            result  # Ham vector search sonuçları
                        )
                        
                        # State'deki chunk'ları güncelle
                        for chunk_data in result:
                            chunk_info = ChunkInfo(
                                chunk_id=chunk_data['chunk_id'],
                                text=chunk_data['text'],
                                page_number=chunk_data['page_number'],
                                document_name=chunk_data['document_name'],
                                relevance_score=chunk_data['relevance_score'],
                                # Eksik alanları varsayılan değerlerle ekle
                                document_metadata={},
                                split_texts=[],
                                split_scores=[]
                            )
                            state.discovered_chunks.append(chunk_info)
                    else:
                        current_observation = f"Vector search başarısız: {result}. Farklı arama terimleri dene veya cypher_query kullan."
                        
                else:
                    current_observation = f"Bilinmeyen action: {action}. Geçerli action'lar: cypher_query, vector_search, final_answer"
                
                # Chunk limit kontrolü
                if len(state.discovered_chunks) >= state.max_chunks_limit:
                    current_observation += f" (Chunk limiti {state.max_chunks_limit} aşıldı, artık yeni chunk aranmayacak)"
                
            except Exception as e:
                logger.error(f"İterasyon {state.iteration_count} hatası: {e}")
                current_observation = f"Hata oluştu: {e}. Farklı bir yaklaşım dene."
                
            # Chunk limit kontrolü (sadece uyarı ver, LLM karar versin)
            if len(state.discovered_chunks) >= state.max_chunks_limit:
                current_observation += f" (Chunk limiti {state.max_chunks_limit} aşıldı, istersen final_answer verebilirsin)"
        
        # Token kullanımını logla ve detaylı rapor oluştur
        logger.info(f"TOPLAM TOKEN KULLANIMI - Input: {self.token_usage['input_tokens']}, Output: {self.token_usage['output_tokens']}, Total: {self.token_usage['total_tokens']}")
        
        # Detaylı token raporu
        self.log_detailed_token_report()
        
       # Final answer varsa döndür, yoksa chunk ve entity verilerini döndür
        if final_answer:
            logger.info(f"Agent final answer verdi 1: {final_answer}")
            return {
                "final_answer": final_answer,
                "iterations": state.iteration_count,
                "discovered_chunks": len(state.discovered_chunks),
                "discovered_entities": len(state.discovered_entities),
                "token_usage": self.token_usage.copy(),
                "detailed_token_usage": self.detailed_token_usage.copy(),
                "chunk_details": [
                    {
                        "document": chunk.document_name,
                        "page": chunk.page_number,
                        "relevance": chunk.relevance_score,
                        "preview": chunk.text[:200] + "..." if len(chunk.text) > 200 else chunk.text
                    } for chunk in state.discovered_chunks[:10]  # En iyi 10 chunk
                ]
            }
        # Daha detaylı analiz logu ekle
        logger.info(f"Final answer yok: Detaylı analiz verileri: {conversation_history}, {state.discovered_chunks}")
        # Final answer yoksa detaylı analiz verileri döndür
        return {
            "iterations": state.iteration_count,
            "conversation_history": conversation_history,
            "discovered_chunks": len(state.discovered_chunks),
            "discovered_entities": len(state.discovered_entities),
            "entity_details": [
                {
                    "id": e.get('id', ''),
                    "type": e.get('type', ''),
                    "labels": e.get('labels', [])
                } for e in state.discovered_entities[:10]  # En iyi 10 entity
            ],
            "chunk_details": [
                {
                    "document": c.document_name,
                    "page": c.page_number,
                    "relevance": c.relevance_score,
                    "preview": c.text[:100] + "..."
                } for c in sorted(state.discovered_chunks, key=lambda x: x.relevance_score, reverse=True)[:5]
            ],
            "schema_info": self.schema_cache,
            "token_usage": self.token_usage.copy(),
            "detailed_token_usage": self.detailed_token_usage.copy(),
            "successful_findings": [
                {
                    "iteration": f.iteration,
                    "action": f.action_type,
                    "finding": f.summary,
                    "relevance_score": f.relevance_score
                } for f in state.successful_findings
            ],
            "context_memory": self.context_memory,
            "llm_prompt_structure": self.create_llm_prompt_structure(state, user_question)
        }
            
    def create_llm_prompt_structure(self, state: AgentState, user_question: str) -> str:
        """Agent'ın bulduğu bilgileri LLM prompt yapısı olarak oluştur"""
        
        prompt_structure = f"""# INTELLIGENT AGENT KNOWLEDGE EXTRACTION REPORT

## 🔍 USER QUESTION
{user_question}

## 📊 SEARCH RESULTS SUMMARY
- **Total Iterations**: {state.iteration_count}
- **Chunks Discovered**: {len(state.discovered_chunks)}
- **Entities Found**: {len(state.discovered_entities)}
- **Token Usage**: Input: {self.token_usage['input_tokens']}, Output: {self.token_usage['output_tokens']}

## 🎯 SUCCESSFUL FINDINGS PER ITERATION
"""
        for i, finding in enumerate(state.successful_findings, 1):
            prompt_structure += f"""
### Iteration {finding.iteration} - {finding.action_type.upper()}
- **Action**: {finding.action_type}
- **Finding**: {finding.summary}
- **Relevance Score**: {finding.relevance_score:.3f}
"""

        # En yüksek relevance'a sahip chunk'ları listele
        top_chunks = sorted(state.discovered_chunks, key=lambda x: x.relevance_score, reverse=True)[:10]
        
        prompt_structure += """
## 📄 TOP RELEVANT CHUNKS

"""
        for i, chunk in enumerate(top_chunks, 1):
            prompt_structure += f"""
### Chunk {i} (Relevance: {chunk.relevance_score:.3f})
- **Document**: {chunk.document_name}
- **Page**: {chunk.page_number}
- **Text Preview**: {chunk.text[:200]}...

"""

        # Entity bilgilerini ekle
        if state.discovered_entities:
            prompt_structure += """
## 🏷️ DISCOVERED ENTITIES

"""
            for i, entity in enumerate(state.discovered_entities[:20], 1):
                prompt_structure += f"""
### Entity {i}
{entity}

"""

        # Context memory ekle
        prompt_structure += f"""
## 🧠 CONTEXT MEMORY
{self.context_memory}

## 📋 FINAL KNOWLEDGE BASE
"""
        
        # En iyi chunk'ların tam text'lerini ekle
        for i, chunk in enumerate(top_chunks[:5], 1):
            prompt_structure += f"""
### Knowledge Piece {i} (Score: {chunk.relevance_score:.3f})
**Source**: {chunk.document_name}, Page {chunk.page_number}
**Content**: {chunk.text}

---
"""

        prompt_structure += """
## 🤖 LLM PROMPT TEMPLATE

Yukarıdaki bilgileri kullanarak aşağıdaki prompt template'i doldurabilirsiniz:

```
Sistem: Sen expert bir bilgi analisti olarak görev yapıyorsun.

Kullanıcı Sorusu: {user_question}

Mevcut Bilgi Kaynakları:
{chunk_information}

Entity Bilgileri:
{entity_information}

Lütfen bu bilgileri analiz ederek kullanıcının sorusuna kapsamlı bir cevap ver.
```

## 📈 SEARCH STRATEGY ANALYSIS
"""
        
        # Kullanılan stratejileri analiz et
        strategies_used = set([f.action_type for f in state.successful_findings])
        prompt_structure += f"""
**Strategies Used**: {', '.join(strategies_used)}
**Most Effective Strategy**: {max(state.successful_findings, key=lambda x: x.relevance_score).action_type if state.successful_findings else 'None'}
**Best Relevance Score**: {max([f.relevance_score for f in state.successful_findings]) if state.successful_findings else 0:.3f}
"""

        return prompt_structure
            
    def get_system_prompt(self) -> str:
        """System prompt'u cache'den al veya oluştur - token-optimized"""
        if self.system_prompt_cache:
            logger.info("📋 System prompt cache'den alınıyor")
            return self.system_prompt_cache
            
        logger.info("📋 System prompt ilk kez oluşturuluyor (sabit şema ile)...")
        # Artık schema çekmiyoruz, sabit prompt kullanıyoruz
        self.system_prompt_cache = self.create_enhanced_system_prompt({})
        logger.info(f"✅ System prompt oluşturuldu ve cache'lendi: {len(self.system_prompt_cache)} karakter")
        return self.system_prompt_cache
    
    def create_enhanced_system_prompt(self, schema: Dict[str, Any] = None) -> str:
        """Token-optimized system prompt - domain-agnostic decision making"""
        
        # Kısa schema formatı - token tasarrufu için
        schema_text = """## 🗄️ Neo4j Schema Yapısı:
Nodes: Document(errorMessage:string, model:string, fileType:string, communityNodeCount:integer, docType:string, status:string, page_images:string[], processingTime:integer, total_chunks:integer, fileSource:string, chunkRelCount:integer, entityNodeCount:integer, is_cancelled:boolean, fileSize:integer, updatedAt:datetime, entityEntityRelCount:integer, relationshipCount:integer, chunkNodeCount:integer, createdAt:datetime, processed_chunk:integer, fileName:string, communityRelCount:integer, nodeCount:integer); Chunk(position:integer, id:string, text:string, content_offset:integer, fileName:string, page_number:integer, length:integer, chunkId:string, embedding:float[], page_link:string); Policy(id:string, insuredItem:string, source_file:string, createdAt:datetime, name:string, year:string, policyNumber:string, extraction_method:string, type:string, customer:string); PolicyType(typeName:string, policyCount:integer, createdAt:datetime, name:string); InsuredItem(policyCount:integer, createdAt:datetime, description:string, name:string); PolicyYear(policyCount:integer, createdAt:datetime, name:string, year:integer); Customer(policyCount:integer, createdAt:datetime, name:string, fullName:string)
Rels: PART_OF; FIRST_CHUNK; NEXT_CHUNK; HAS_YEAR(created_at:datetime); HAS_TYPE(created_at:datetime); HAS_POLICY(created_at:datetime); MENTIONS; HAS_MEMBER; DOCUMENTED_IN(source:string, created_at:datetime); RELATES_TO; HAS_DOC(created_at:datetime); HAS_INSURED_ITEM(created_at:datetime)
Patterns: (Chunk)-[NEXT_CHUNK]->(Chunk); (Chunk)-[PART_OF]->(Document); (Customer)-[HAS_DOC]->(Document); (Customer)-[HAS_POLICY]->(Policy); (Document)-[FIRST_CHUNK]->(Chunk); (Policy)-[DOCUMENTED_IN]->(Document); (Policy)-[HAS_INSURED_ITEM]->(InsuredItem); (Policy)-[HAS_TYPE]->(PolicyType); (Policy)-[HAS_YEAR]->(PolicyYear)

## 📊 DOMAIN ARCHITECTURE:
- **Document**: Policy'lerin metadata'larını içeren ana kaynak belgeler
- **Chunk**: Document'lerin parçalara bölünmüş text içerikleri (semantic search için)
- **Policy/Customer/PolicyType**: Yapılandırılmış business entity'leri
"""

        system_prompt = f"""Sen bir graph veritabanı analiz uzmanısın. Kullanıcı sorularını analiz ederek en uygun arama stratejisini KENDI KARAR VER.

{schema_text}## 🧠 DECISION FRAMEWORK:

### 🔍 SORU TİPİ ANALİZİ:
1. **METADATA SORULARI**: Sayısal/yapısal veriler (count, ID, tip, liste)
   → Node property'leriyle cevaplanabilir → SADECE cypher_query kullan
   
2. **CONTENT SORULARI**: Belge içeriği, detaylı açıklamalar, text-based bilgiler  
   → Chunk text'lerinde aranmalı → cypher_query ile veri araştır ve chunk'ları topla

3. **BELGE ANALİZİ SORULARI**: Belirli entity için tüm belgelerinin detaylı analizi
   → cypher_query ile entity bul + ilgili chunk'ları topla

### ⚡ LLM-DRIVEN KARAR VERİCİ KURALLAR:
1. **İLK ADIM**: Her zaman cypher_query ile başla
   - Node property'lerini ve temel metadata'yı çek
   - Hangi entity'ler mevcut, hangi belgeler var, chunk'lar nasıl organize?
   
2. **İKİNCİ KARAR**: Cypher sonucuna BAK ve şunu sor:
   - ✅ Soru metadata ile tam cevaplanıyor mu? → final_answer
   - ❌ Çok fazla veri var mı? Daha spesifik arama gerekli mi? → refined cypher_query
   - ❌ İçerik detaylarına ihtiyaç var mı? → vector_search (semantic arama) VEYA cypher_query (chunk'ları topla)
   - ❌ Belge metinlerini okumak gerekli mi? → vector_search (direkt chunk arama) VEYA cypher_query (relationship takip)
   - 5+ belge/poliçe bulunduğunda analiz gerekli ise:
     * KULLANICIYA SOR: "X adet belge bulundu, hangisinin detayını analiz etmek istiyorsunuz?"
     * VEYA KENDİ SEÇ: En güncel/önemli 2-3 tanesini analiz et  
     * Karar senin - çok fazla içerik okumak uzun sürer!

3. **VECTOR SEARCH KULLANIM KARARI**:
   - Belirli kelimeleri/kavramları chunk'larda aramak için → vector_search
   - "prim bilgileri", "teminat detayları", "hasar bilgileri" gibi içerik arama → vector_search  
   - Cypher'da CONTAINS kullanmak yerine → vector_search kullan (daha akıllı arama)
   - Text'te geçen bilgileri bulmak için → vector_search öncelikli
   - **ÖNEMLİ**: LLM önce hangi belgelerde arama yapacağını belirler, sonra o belgelerde semantic arama yapar
   
4. **CHUNK ARAMA**: İçerik gerekiyorsa:
   - **Önce vector_search dene**: Semantic olarak ilgili chunk'ları hızlıca bul (akıllı belge seçimi ile)
   - **Sonra cypher_query**: Entity→document→chunk relationship'leri ile ek chunk'lar topla
   - Text içerik analizi için vector_search sonuçlarını kullan

### 📝 ACTION FORMAT:
```
Observation: [Mevcut durum ve önceki adım sonuçları]
Thought: [Cypher sonucuna bakarak: Bu yeterli mi? İçerik detayına ihtiyaç var mı?]
Action: [cypher_query | vector_search | final_answer]
Content: [Sorgu/arama metni/cevap]
```

### 🎯 ACTION STRATEJİLERİ:

**cypher_query**: Schema'daki node/relationship'leri kullanarak veri araştırması
- Node sayıları, liste'ler, ID'ler, tipler için
- Entity'leri bul ve ilişkilerini araştır
- Chunk'ları topla: entity → document → chunk chain'i takip et
- Cypher sonucunu DEĞERLENDİR: Bu yeterli mi, yoksa daha fazla chunk lazım mı?

**vector_search**: OpenAI embedding ile AKILLI semantic chunk arama
- ÖNCEKI BULGULARDAN FAYDALAN: Zaten belirli belgeler bulunduysa, o belgelerde spesifik terimler ara
- LLM önce hangi belgelerde arama yapacağını otomatik belirler (veya önceki bulgulardan alır)
- Belge içeriklerinde semantic arama yap (seçilen belgelerde)
- ARAMA STRATEJİSİ:
  * Genel kişi/poliçe bilgileri zaten bulunduysa → SPESİFİK terimleri ara (sadece "prim tutarı", "hasar bedeli", "teminat limiti")
  * Henüz kişi/belge bulunmadıysa → kişi adını da dahil et ("Mehmet'in prim bilgileri")
- Format: `Action: vector_search` `Content: arama metni`
- DOĞRU örnekler: 
  * Cypher'da Ayça'nın poliçeleri bulunduysa → "prim tutarı" (kısa ve spesifik)
  * Hiç bilgi yoksa → "Ayça Dinçkök'ün prim bilgileri" (kişi dahil)
- YANLIŞ örnekler:
  * Ayça zaten bulunduysa → "Ayça Dinçkök'ün prim bilgileri" (gereksiz tekrar)

**final_answer**: 
- Metadata yeterli ise: cypher_query sonuçlarını organize et
- İçerik toplandı ise: chunk text'lerini ve metadata'yı birleştir
- RAW DATA modunda: Bulunan verileri organize et (yorumsuz)
- LLM INTERPRETATION modunda: Sistem otomatik olarak LLM ile yorumlayacak
- CEVAP FORMATI: Normal, doğal konuşma tarzında yanıt ver (liste formatı değil)

### ‼️ ZORUNLU KURALLAR:
- Her soru için İLK ADIM cypher_query olmalı
- Cypher sonucuna bakarak daha fazla chunk'a ihtiyaç olup olmadığını KENDİN karar ver
- final_answer'da doğal konuşma tarzında cevap ver (liste formatı YASAK)  
- Belge analizi: cypher_query (entity bul) → cypher_query (chunk topla) → final_answer
- Schema ve cypher sonuçlarını kullanarak optimal stratejiyi KENDİN belirle"""

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
    
    # Agent'ı oluştur - önce raw data mode, sonra LLM interpretation mode test et
    test_modes = [
        {"enable_llm_interpretation": False, "mode_name": "RAW DATA MODE"},
        # {"enable_llm_interpretation": True, "mode_name": "LLM INTERPRETATION MODE"}
    ]
    
    # Test soruları
    test_questions = [
        # "Kaç poliçe var ve kimin adına",
        # "Ayça Dinçkök'un poliçesini özetle",
        "Ayça Hanım’ın D5 poliçesinin primi ne kadar?",
        # "Kaç tane müşteri var?",
        # "Sistemde hangi poliçe türleri mevcut?",
        # "DASK poliçeleri hakkında ne tür bilgiler var?",
        # "Galata Residence ile ilgili hangi bilgiler mevcut?"
    ]
    
    for mode_config in test_modes:
        print(f"\n{'='*80}")
        print(f"🔬 TEST MODU: {mode_config['mode_name']}")
        print(f"LLM Interpretation: {mode_config['enable_llm_interpretation']}")
        print('='*80)
        
        # Agent'ı bu mode'da oluşturf
        agent = IntelligentAgent(graph, enable_llm_interpretation=mode_config['enable_llm_interpretation'])
        
        for question in test_questions:
            print(f"\n{'-'*60}")
            print(f"SORU: {question}")
            print('-'*60)
            
            result = agent.solve_question(question)
            
            # Final answer varsa onu göster
            if 'final_answer' in result:
                print(f"🎯 FINAL ANSWER:\n{result['final_answer']}")
                print(f"\n📊 STATİSTİKLER:")
                print(f"- İTERASYON: {result['iterations']}")
                print(f"- CHUNK SAYISI: {result['discovered_chunks']}")
                print(f"- ENTITY SAYISI: {result['discovered_entities']}")
                print(f"- TOKEN KULLANIMI: {result['token_usage']['total_tokens']}")
            else:
                print(f"📊 STATİSTİKLER:")
                print(f"- İTERASYON: {result['iterations']}")
                print(f"- CHUNK SAYISI: {result['discovered_chunks']}")
                print(f"- ENTITY SAYISI: {result['discovered_entities']}")
                
                print("\nEN İLGİLİ CHUNK'LAR:")
                for chunk in result['chunk_details']:
                    print(f"- {chunk['document']} (Sayfa {chunk['page']}) - Relevance: {chunk['relevance']:.3f}")
                    print(f"  {chunk['preview']}")
        
        print(f"\n{mode_config['mode_name']} TEST TAMAMLANDI\n")

if __name__ == "__main__":
    test_agent()
