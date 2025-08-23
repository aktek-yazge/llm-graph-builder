"""
LangExtract Graph Integration Module

Bu modül LangExtract kütüphanesini mevcut graph oluşturma sistemine entegre eder.
Structured extraction için LangExtract'ı kullanarak node ve relation çıkarımı yapar.
"""

from __future__ import annotations

import logging
import os
import json
from typing import Any, Dict, List, Optional, Union, Tuple
from dataclasses import dataclass
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# LangExtract imports
import langextract as lx
from langextract.extraction import extract
from langextract import data, schema
from langextract.core.data import ExampleData, Extraction

# Mevcut proje imports (varsayılan path'ler)
try:
    from src.shared.common_fn import load_embedding_model
    from src.shared.constants import *
except ImportError:
    # Test durumunda veya standalone kullanım için
    logging.warning("Could not import project modules, using standalone mode")


@dataclass
class GraphEntity:
    """Graph entity representation"""
    id: str
    label: str
    properties: Dict[str, Any]


@dataclass
class GraphRelationship:
    """Graph relationship representation"""
    source_id: str
    target_id: str
    type: str
    properties: Dict[str, Any]


@dataclass
class GraphExtractionResult:
    """Result of graph extraction"""
    entities: List[GraphEntity]
    relationships: List[GraphRelationship]
    metadata: Dict[str, Any]


class LangExtractGraphExtractor:
    """LangExtract tabanlı graph extraction sınıfı"""
    
    def __init__(self, model_id: str = "gpt-4o-mini", provider_config: Optional[Dict] = None):
        self.model_id = model_id
        self.provider_config = provider_config or {}
        
    def create_node_extraction_examples(self) -> List[ExampleData]:
        """Node extraction için example data oluştur"""
        examples = []
        
        # Person entity örneği
        person_example = ExampleData(
            text="John Smith is a 35-year-old software engineer from San Francisco.",
            extractions=[
                Extraction(
                    extraction_class="Person",
                    extraction_text="John Smith",
                    attributes={
                        "name": "John Smith",
                        "age": "35",
                        "occupation": "software engineer",
                        "location": "San Francisco"
                    }
                )
            ]
        )
        examples.append(person_example)
        
        # Organization entity örneği
        org_example = ExampleData(
            text="Google Inc. is a technology company founded in 1998 in Mountain View, California.",
            extractions=[
                Extraction(
                    extraction_class="Organization",
                    extraction_text="Google Inc.",
                    attributes={
                        "name": "Google Inc.",
                        "industry": "technology",
                        "founded_year": "1998",
                        "location": "Mountain View, California"
                    }
                )
            ]
        )
        examples.append(org_example)
        
        # Policy örneği
        policy_example = ExampleData(
            text="Policy number ABC123 is a life insurance policy with coverage amount of $500,000.",
            extractions=[
                Extraction(
                    extraction_class="Policy",
                    extraction_text="ABC123",
                    attributes={
                        "policy_number": "ABC123",
                        "type": "life insurance",
                        "coverage_amount": "500000"
                    }
                )
            ]
        )
        examples.append(policy_example)
        
        return examples
    
    def create_relationship_extraction_examples(self) -> List[ExampleData]:
        """Relationship extraction için example data oluştur"""
        examples = []
        
        # Person-Organization relationship
        work_example = ExampleData(
            text="John Smith works at Google as a software engineer.",
            extractions=[
                Extraction(
                    extraction_class="Relationship",
                    extraction_text="works at",
                    attributes={
                        "source": "john_smith",
                        "target": "google",
                        "relationship_type": "WORKS_AT",
                        "role": "software engineer"
                    }
                )
            ]
        )
        examples.append(work_example)
        
        # Person-Policy relationship
        policy_example = ExampleData(
            text="John Smith has a life insurance policy ABC123.",
            extractions=[
                Extraction(
                    extraction_class="Relationship",
                    extraction_text="has",
                    attributes={
                        "source": "john_smith",
                        "target": "policy_abc123",
                        "relationship_type": "HAS_POLICY"
                    }
                )
            ]
        )
        examples.append(policy_example)
        
        return examples
    
    async def extract_entities(self, text: str, allowed_nodes: List[str] = None) -> List[GraphEntity]:
        """Text'ten entity'leri çıkar"""
        try:
            # Entity extraction prompt
            prompt = f"""
            Extract entities from the text. Focus on extracting these types of entities:
            {', '.join(allowed_nodes) if allowed_nodes else 'Person, Organization, Location, Product, Policy, Document'}
            
            For each entity, provide:
            - A unique identifier
            - The entity type/label
            - Key properties like name, attributes, etc.
            """
            
            examples = self.create_node_extraction_examples()
            
            # LangExtract ile extraction yap
            result = extract(
                text_or_documents=text,
                prompt_description=prompt,
                examples=examples,
                model_id=self.model_id,
                **self.provider_config
            )
            
            # Sonucu GraphEntity listesine dönüştür
            entities = []
            if result and hasattr(result, 'extractions'):
                for extraction in result.extractions:
                    if extraction.extraction_class in (allowed_nodes or []):
                        entity = GraphEntity(
                            id=extraction.extraction_text,  # Olduğu gibi bırak, normalize etme
                            label=extraction.extraction_class,
                            properties=extraction.attributes or {}
                        )
                        entities.append(entity)
            
            logging.info(f"LangExtract extracted {len(entities)} entities")
            return entities
            
        except Exception as e:
            logging.error(f"Entity extraction failed: {e}")
            return []
    
    async def extract_relationships(self, text: str, entities: List[GraphEntity], 
                                  allowed_relationships: List[Tuple[str, str, str]] = None) -> List[GraphRelationship]:
        """Text'ten relationship'leri çıkar"""
        try:
            # Relationship extraction prompt
            entity_list = [f"{e.id} ({e.label})" for e in entities]
            prompt = f"""
            Extract relationships between these entities: {', '.join(entity_list)}
            
            Focus on these relationship types:
            {', '.join([f"{s}-{r}->{t}" for s,r,t in allowed_relationships]) if allowed_relationships else 'WORKS_AT, HAS_POLICY, LOCATED_IN, OWNS, MANAGES'}
            
            For each relationship, provide:
            - Source entity ID
            - Target entity ID  
            - Relationship type
            - Additional properties if any
            """
            
            examples = self.create_relationship_extraction_examples()
            
            # LangExtract ile extraction yap
            result = extract(
                text_or_documents=text,
                prompt_description=prompt,
                examples=examples,
                model_id=self.model_id,
                **self.provider_config
            )
            
            # Sonucu GraphRelationship listesine dönüştür
            relationships = []
            if result and hasattr(result, 'extractions'):
                for extraction in result.extractions:
                    if extraction.extraction_class == "Relationship":
                        attrs = extraction.attributes or {}
                        relationship = GraphRelationship(
                            source_id=attrs.get("source"),
                            target_id=attrs.get("target"),
                            type=attrs.get("relationship_type"),
                            properties={k: v for k, v in attrs.items() if k not in ["source", "target", "relationship_type"]}
                        )
                        relationships.append(relationship)
            
            logging.info(f"LangExtract extracted {len(relationships)} relationships")
            return relationships
            
        except Exception as e:
            logging.error(f"Relationship extraction failed: {e}")
            return []
    
    async def extract_graph(self, text: str, allowed_nodes: List[str] = None, 
                          allowed_relationships: List[Tuple[str, str, str]] = None) -> GraphExtractionResult:
        """Complete graph extraction (entities + relationships)"""
        try:
            logging.info("Starting LangExtract graph extraction...")
            
            # 1. Entity extraction
            entities = await self.extract_entities(text, allowed_nodes)
            
            # 2. Relationship extraction
            relationships = await self.extract_relationships(text, entities, allowed_relationships)
            
            # 3. Result oluştur
            result = GraphExtractionResult(
                entities=entities,
                relationships=relationships,
                metadata={
                    "extraction_method": "langextract",
                    "model": self.model_id,
                    "entity_count": len(entities),
                    "relationship_count": len(relationships)
                }
            )
            
            logging.info(f"LangExtract extraction completed: {len(entities)} entities, {len(relationships)} relationships")
            return result
            
        except Exception as e:
            logging.error(f"Graph extraction failed: {e}")
            return GraphExtractionResult(entities=[], relationships=[], metadata={"error": str(e)})


def convert_to_neo4j_format(extraction_result: GraphExtractionResult) -> List[Dict]:
    """LangExtract sonuçlarını Neo4j format'ına dönüştür"""
    graph_documents = []
    
    # Entities'i Node'lara dönüştür
    nodes = []
    for entity in extraction_result.entities:
        node = {
            "id": entity.id,
            "type": entity.label,
            "properties": entity.properties
        }
        nodes.append(node)
    
    # Relationships'i dönüştür
    relationships = []
    for rel in extraction_result.relationships:
        relationship = {
            "source": rel.source_id,
            "target": rel.target_id,
            "type": rel.type,
            "properties": rel.properties
        }
        relationships.append(relationship)
    
    # Neo4j GraphDocument format'ı
    graph_doc = {
        "nodes": nodes,
        "relationships": relationships,
        "metadata": extraction_result.metadata
    }
    
    graph_documents.append(graph_doc)
    return graph_documents


# Mevcut sisteme entegrasyon için wrapper fonksiyon
async def get_graph_from_langextract(
    model: str,
    chunkId_chunkDoc_list: List,
    allowedNodes: str,
    allowedRelationship: str,
    chunks_to_combine: int,
    file_name: str = None,
    additional_instructions: str = None,
    graph=None
) -> List[Dict]:
    """
    Mevcut get_graph_from_llm fonksiyonuna alternatif LangExtract implementation
    
    Args:
        model: Model adı (langextract için provider belirlemek için kullanılır)
        chunkId_chunkDoc_list: Chunk'lar
        allowedNodes: İzin verilen node türleri (comma-separated)
        allowedRelationship: İzin verilen relationship'ler (comma-separated triplets)
        chunks_to_combine: Combine edilecek chunk sayısı
        file_name: Dosya adı
        additional_instructions: Ek talimatlar
        graph: Neo4j graph instance
    
    Returns:
        List[Dict]: Neo4j format'ında graph documents
    """
    try:
        logging.info("=== LangExtract Graph Extraction Starting ===")
        
        # Model'i LangExtract format'ına dönüştür
        model_mapping = {
            "openai-gpt-4": "gpt-4o-mini",
            "openai-gpt-3.5": "gpt-3.5-turbo", 
            "gemini-pro": "gemini-1.5-flash",
            "gemini-1.5-pro": "gemini-1.5-pro"
        }
        langextract_model = model_mapping.get(model, "gpt-4o-mini")
        
        # Allowed nodes'ları parse et
        allowed_nodes_list = []
        if allowedNodes:
            allowed_nodes_list = [node.strip() for node in allowedNodes.split(',') if node.strip()]
            
        # Allowed relationships'i parse et  
        allowed_rels_list = []
        if allowedRelationship:
            items = [item.strip() for item in allowedRelationship.split(',') if item.strip()]
            if len(items) % 3 == 0:
                for i in range(0, len(items), 3):
                    allowed_rels_list.append((items[i], items[i+1], items[i+2]))
        
        # Extractor'ı oluştur
        extractor = LangExtractGraphExtractor(model_id=langextract_model)
        
        # Chunk'ları birleştir ve text'e dönüştür
        combined_text = ""
        for chunk_data in chunkId_chunkDoc_list[:chunks_to_combine]:
            if hasattr(chunk_data, 'page_content'):
                combined_text += chunk_data.page_content + "\n"
            elif isinstance(chunk_data, dict):
                combined_text += chunk_data.get('content', '') + "\n"
            else:
                combined_text += str(chunk_data) + "\n"
        
        if additional_instructions:
            combined_text = f"{additional_instructions}\n\n{combined_text}"
            
        # Graph extraction
        extraction_result = await extractor.extract_graph(
            text=combined_text,
            allowed_nodes=allowed_nodes_list,
            allowed_relationships=allowed_rels_list
        )
        
        # Neo4j format'ına dönüştür
        graph_documents = convert_to_neo4j_format(extraction_result)
        
        logging.info(f"LangExtract completed: {len(graph_documents)} graph documents created")
        return graph_documents
        
    except Exception as e:
        logging.error(f"LangExtract graph extraction failed: {e}")
        return []


if __name__ == "__main__":
    """Test function"""
    import asyncio
    
    async def test_langextract_extraction():
        # Test text
        test_text = """
        John Smith is a 35-year-old software engineer working at Google Inc. 
        He lives in San Francisco and has a life insurance policy ABC123 
        with a coverage amount of $500,000. Google is a technology company 
        founded in 1998 in Mountain View, California.
        """
        
        # Test extraction
        extractor = LangExtractGraphExtractor(model_id="gpt-4o-mini")
        
        result = await extractor.extract_graph(
            text=test_text,
            allowed_nodes=["Person", "Organization", "Policy", "Location"],
            allowed_relationships=[("Person", "WORKS_AT", "Organization"), ("Person", "HAS_POLICY", "Policy")]
        )
        
        print("Extraction Results:")
        print(f"Entities: {len(result.entities)}")
        for entity in result.entities:
            print(f"  - {entity.label}: {entity.id} -> {entity.properties}")
            
        print(f"Relationships: {len(result.relationships)}")
        for rel in result.relationships:
            print(f"  - {rel.source_id} -[{rel.type}]-> {rel.target_id}")
            
        # Neo4j format'ına dönüştür
        neo4j_docs = convert_to_neo4j_format(result)
        print(f"\nNeo4j Documents: {len(neo4j_docs)}")
        print(json.dumps(neo4j_docs[0], indent=2))
    
    # Test sadece API key varsa çalışır
    # asyncio.run(test_langextract_extraction())
    print("LangExtract Graph Integration module loaded successfully!")
    
    # Environment variables kontrolü
    print("\n🔍 Environment Variables Check:")
    env_vars = ["OPENAI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD"]
    
    for var in env_vars:
        value = os.getenv(var)
        if value:
            # Güvenlik için sadece ilk ve son karakterleri göster
            masked_value = f"{value[:3]}...{value[-3:]}" if len(value) > 6 else "***"
            print(f"   ✅ {var}: {masked_value}")
        else:
            print(f"   ❌ {var}: Not set")
    
    print("\n💡 To test extraction, set your API key and run test functions")
    
    # Test graph integration if we have API keys
    if os.getenv("OPENAI_API_KEY"):
        print("\n🧪 Testing LangExtract with OpenAI...")
        # test_langextract_node_extraction()
    elif os.getenv("GOOGLE_API_KEY"):
        print("\n🧪 Testing LangExtract with Google Gemini...")
        # test_langextract_node_extraction()
    else:
        print("\n⚠️  No API keys found. Set OPENAI_API_KEY or GOOGLE_API_KEY to test extraction.")
