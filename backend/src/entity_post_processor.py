"""
Entity Post-Processing Module
Bu modül LLM'den gelen ham entity'leri ve ilişkileri ADDITIONAL_INSTRUCTIONS'a göre 
temizler, düzeltir ve doğrular.
"""

import logging
import re
from typing import List, Dict, Any, Optional
from langchain_neo4j import Neo4jGraph
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_experimental.graph_transformers.llm import GraphDocument, Node, Relationship
from src.shared.common_fn import execute_graph_query
from src.shared.constants import ADDITIONAL_INSTRUCTIONS
from src.llm import get_llm
import os

logging.basicConfig(format='%(asctime)s - %(message)s', level='INFO')

class EntityPostProcessor:
    """
    LLM'den gelen ham entity'leri ve ilişkileri post-process eden sınıf
    """
    
    def __init__(self, graph: Neo4jGraph):
        self.graph = graph
        self.forbidden_entity_types = [
            "Document", "Policy", "Form", "Container", 
            "Existing Insurance Policy", "Konut Poliçesi",
            "DASK Poliçesi", "Kasko Poliçesi", "Trafik Poliçesi"
        ]
        self.allowed_entity_types = [
            "Person", "Company", "PolicyNumber", "Address", 
            "PlateNumber", "IdentityNumber", "Date", "Year", "DateRange"
        ]
        
    def process_entities(self, graph_documents, file_name: str):
        """
        Ana post-processing fonksiyonu
        
        Args:
            graph_documents: LLM'den gelen ham GraphDocument listesi
            file_name: Dosya adı
            
        Returns:
            Temizlenmiş GraphDocument listesi
        """
        logging.info(f"Entity post-processing başlatılıyor: {file_name}")
        
        cleaned_documents = []
        for doc in graph_documents:
            cleaned_doc = self._process_single_document(doc, file_name)
            if cleaned_doc:
                cleaned_documents.append(cleaned_doc)
                
        logging.info(f"Post-processing tamamlandı. {len(cleaned_documents)} temiz döküman üretildi.")
        return cleaned_documents
    
    def _process_single_document(self, graph_doc, file_name: str):
        """Tek bir GraphDocument'ı işler"""
        
        # 1. Yasaklı entity'leri filtrele
        filtered_nodes = self._filter_forbidden_entities(graph_doc.nodes)
        
        # 2. İlişkileri düzelt
        corrected_relationships = self._correct_relationships(
            graph_doc.relationships, filtered_nodes, file_name
        )
        
        # 3. Entity'leri normalize et
        normalized_nodes = self._normalize_entities(filtered_nodes)
        
        # 4. Eksik entity'leri ekle
        enhanced_nodes, enhanced_relationships = self._enhance_entities(
            normalized_nodes, corrected_relationships, file_name
        )
        
        # 5. Yeni GraphDocument oluştur        
        return GraphDocument(
            nodes=enhanced_nodes,
            relationships=enhanced_relationships,
            source=graph_doc.source
        )
    
    def _filter_forbidden_entities(self, nodes):
        """Yasaklı entity türlerini filtreler"""
        filtered_nodes = []
        
        for node in nodes:
            # Yasaklı türleri kontrol et
            if any(forbidden in node.type for forbidden in self.forbidden_entity_types):
                logging.warning(f"Yasaklı entity filtrelendi: {node.type} - {node.id}")
                continue
                
            # Yasaklı ID'leri kontrol et  
            if any(forbidden in node.id for forbidden in self.forbidden_entity_types):
                logging.warning(f"Yasaklı ID filtrelendi: {node.id}")
                continue
                
            filtered_nodes.append(node)
            
        logging.info(f"Entity filtreleme: {len(nodes)} -> {len(filtered_nodes)}")
        return filtered_nodes
    
    def _correct_relationships(self, relationships, nodes, file_name: str):
        """İlişkileri düzeltir - özellikle Date/Year entity'lerinin Document'a bağlanması"""
        corrected_relationships = []
        node_ids = {node.id for node in nodes}
        
        for rel in relationships:
            # Kaynak ve hedef node'lar mevcut mu kontrol et
            if rel.source.id not in node_ids or rel.target.id not in node_ids:
                logging.warning(f"İlişki filtrelendi - eksik node: {rel.source.id} -> {rel.target.id}")
                continue
                
            # Date/Year entity'lerinin Document'a bağlanmasını sağla
            corrected_rel = self._redirect_temporal_relationships(rel, file_name)
            corrected_relationships.append(corrected_rel)
            
        return corrected_relationships
    
    def _redirect_temporal_relationships(self, relationship, file_name: str):
        """Date/Year entity'lerini Document'a yönlendirir"""
        temporal_types = ["Date", "Year", "DateRange"]
        
        # Hedef temporal entity ise ve policy/document tarihiyse
        if (relationship.target.type in temporal_types and 
            self._is_document_related_date(relationship.target.id)):
            
            # Document node'unu oluştur
            document_node = Node(id=file_name, type="Document")
            
            # İlişkiyi Document'a yönlendir
            return Relationship(
                source=document_node,
                target=relationship.target,
                type=self._get_temporal_relationship_type(relationship.target.type)
            )
            
        return relationship
    
    def _is_document_related_date(self, date_id: str) -> bool:
        """Tarihin döküman/poliçe ile ilgili olup olmadığını kontrol eder"""
        document_keywords = [
            "başlangıç", "bitiş", "oluştur", "düzenle", "imza", 
            "start", "end", "create", "issue", "sign",
            "poliçe", "policy", "döküman", "document"
        ]
        
        date_lower = date_id.lower()
        return any(keyword in date_lower for keyword in document_keywords)
    
    def _get_temporal_relationship_type(self, entity_type: str) -> str:
        """Temporal entity'ler için uygun ilişki tipini döner"""
        mapping = {
            "Date": "HAS_DATE",
            "Year": "HAS_YEAR", 
            "DateRange": "HAS_DATE_RANGE"
        }
        return mapping.get(entity_type, "RELATED_TO")
    
    def _normalize_entities(self, nodes):
        """Entity'leri normalize eder"""
        normalized_nodes = []
        
        for node in nodes:
            # Türkçe karakterleri düzelt
            normalized_id = self._normalize_turkish_text(node.id)
            
            # Adres formatını düzelt
            if node.type == "Address":
                normalized_id = self._normalize_address(normalized_id)
            
            # Tarih formatını düzelt
            elif node.type in ["Date", "Year", "DateRange"]:
                normalized_id = self._normalize_date(normalized_id)
                
            # Kişi isimlerini düzelt
            elif node.type == "Person":
                normalized_id = self._normalize_person_name(normalized_id)
            
            normalized_node = Node(
                id=normalized_id,
                type=node.type,
                properties=node.properties
            )
            normalized_nodes.append(normalized_node)
            
        return normalized_nodes
    
    def _normalize_turkish_text(self, text: str) -> str:
        """Türkçe metni normalize eder"""
        if not text:
            return text
            
        # Fazla boşlukları temizle
        text = re.sub(r'\s+', ' ', text.strip())
        
        # Büyük/küçük harf düzeltmeleri (özel durumlar hariç)
        return text
    
    def _normalize_address(self, address: str) -> str:
        """Adres formatını düzeltir"""
        # Adres normalizasyonu
        address = re.sub(r'\s+', ' ', address.strip())
        
        # Standart kısaltmaları düzelt
        replacements = {
            r'\bMah\b\.?': 'Mahallesi',
            r'\bCad\b\.?': 'Caddesi', 
            r'\bSok\b\.?': 'Sokağı',
            r'\bApt\b\.?': 'Apartmanı',
            r'\bNo\b\.?': 'No:',
            r'\bD\b\.?': 'Daire:'
        }
        
        for pattern, replacement in replacements.items():
            address = re.sub(pattern, replacement, address, flags=re.IGNORECASE)
            
        return address
    
    def _normalize_date(self, date_str: str) -> str:
        """Tarih formatını normalize eder"""
        if not date_str:
            return date_str
            
        # Türkçe tarih formatlarını ISO formatına çevir
        # 01.06.2024 -> 2024-06-01
        turkish_date_pattern = r'(\d{1,2})\.(\d{1,2})\.(\d{4})'
        match = re.match(turkish_date_pattern, date_str.strip())
        
        if match:
            day, month, year = match.groups()
            return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
            
        return date_str
    
    def _normalize_person_name(self, name: str) -> str:
        """Kişi isimlerini normalize eder"""
        if not name:
            return name
            
        # Her kelimenin ilk harfini büyük yap
        words = name.strip().split()
        normalized_words = [word.capitalize() for word in words if word]
        
        return ' '.join(normalized_words)
    
    def _enhance_entities(self, nodes, relationships, file_name: str):
        """Eksik entity'leri ve ilişkileri ekler"""
        
        # Year entity'lerini kontrol et ve ekle
        enhanced_nodes = list(nodes)
        enhanced_relationships = list(relationships)
        
        # Date entity'lerinden Year entity'leri türet
        date_nodes = [node for node in nodes if node.type == "Date"]
        existing_years = {node.id for node in nodes if node.type == "Year"}
        
        for date_node in date_nodes:
            year = self._extract_year_from_date(date_node.id)
            if year and year not in existing_years:
                # Year node'u ekle
                year_node = Node(id=year, type="Year")
                enhanced_nodes.append(year_node)
                existing_years.add(year)
                
                # Document ile ilişkilendir
                if self._is_document_related_date(date_node.id):
                    document_node = Node(id=file_name, type="Document")
                    year_relationship = Relationship(
                        source=document_node,
                        target=year_node,
                        type="HAS_YEAR"
                    )
                    enhanced_relationships.append(year_relationship)
                    
                logging.info(f"Year entity eklendi: {year}")
        
        return enhanced_nodes, enhanced_relationships
    
    def _extract_year_from_date(self, date_str: str) -> str:
        """Tarihten yılı çıkarır"""
        year_patterns = [
            r'(\d{4})',  # 2024
            r'(\d{4})-\d{2}-\d{2}',  # 2024-06-01
            r'\d{2}\.\d{2}\.(\d{4})'  # 01.06.2024
        ]
        
        for pattern in year_patterns:
            match = re.search(pattern, date_str)
            if match:
                return match.group(1) if '(\d{4})' in pattern else match.group(1)
                
        return None
    
    def validate_against_instructions(self, graph_documents) -> List[str]:
        """
        ADDITIONAL_INSTRUCTIONS'a göre entity'leri doğrular
        
        Returns:
            Doğrulama hatalarının listesi
        """
        validation_errors = []
        
        for doc in graph_documents:
            # Document node yaratılmış mı kontrol et
            document_nodes = [node for node in doc.nodes if node.type == "Document"]
            if document_nodes:
                validation_errors.append("HATA: Document node'u yaratılmış")
                
            # Yasaklı entity türleri var mı kontrol et
            for node in doc.nodes:
                if any(forbidden in node.type for forbidden in self.forbidden_entity_types):
                    validation_errors.append(f"HATA: Yasaklı entity türü: {node.type}")
                    
            # Date entity'lerin chunk'a değil Document'a bağlı olması
            temporal_rels = [
                rel for rel in doc.relationships 
                if rel.target.type in ["Date", "Year", "DateRange"]
            ]
            
            for rel in temporal_rels:
                if (rel.source.type == "Chunk" and 
                    self._is_document_related_date(rel.target.id)):
                    validation_errors.append(
                        f"HATA: Date entity chunk'a bağlı: {rel.target.id}"
                    )
                    
        return validation_errors


def apply_entity_post_processing(graph_documents, file_name: str, graph: Optional[Neo4jGraph] = None):
    """
    Post-processing pipeline'ını uygular
    
    Args:
        graph_documents: LLM'den gelen ham GraphDocument listesi
        file_name: Dosya adı
        graph: Neo4j graph instance
        
    Returns:
        Temizlenmiş GraphDocument listesi
    """
    processor = EntityPostProcessor(graph)
    
    # 1. Ana post-processing
    cleaned_documents = processor.process_entities(graph_documents, file_name)
    
    # 2. Doğrulama
    validation_errors = processor.validate_against_instructions(cleaned_documents)
    
    if validation_errors:
        logging.warning("Post-processing doğrulama hataları:")
        for error in validation_errors:
            logging.warning(f"  - {error}")
    else:
        logging.info("Post-processing doğrulaması başarılı")
        
    return cleaned_documents
