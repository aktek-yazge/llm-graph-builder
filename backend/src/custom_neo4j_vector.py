

import logging
import re
from typing import Any, Dict, List, Optional, Tuple, Type
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_neo4j.vectorstores.neo4j_vector import Neo4jVector
from langchain_neo4j.graphs.neo4j_graph import Neo4jGraph
from langchain_core.language_models.base import BaseLanguageModel

class CustomNeo4jVector(Neo4jVector):
    """
    LLM Graph Builder için özelleştirilmiş Neo4j Vector Store
    
    Bu sınıf şu özellikleri sağlar:
    1. Soru analizi yaparak entity-first mı chunk-first mi karar verir
    2. Entity-first'te: LLM ile Cypher query oluşturur
    3. Chunk-first'te: Normal vector search yapar
    4. Hybrid yaklaşım ile her iki yöntemi birleştirir
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.llm: Optional[BaseLanguageModel] = None
        self.graph: Optional[Neo4jGraph] = None
        self.retrieval_query: Optional[str] = None  # Retrieval query'yi saklamak için
        
    def set_llm(self, llm: BaseLanguageModel):
        """LLM'i set et (Cypher generation için)"""
        self.llm = llm
        
    def set_graph(self, graph: Neo4jGraph):
        """Neo4j Graph connection'ı set et"""
        self.graph = graph
        
    def set_retrieval_query(self, retrieval_query: str):
        """Retrieval query'yi set et"""
        self.retrieval_query = retrieval_query

    def get_schema_info(self) -> Tuple[List[str], List[str]]:
        """Neo4j schema bilgilerini al"""
        if not self.graph:
            return [], []
            
        try:
            schema_query = """
            CALL db.labels() YIELD label
            WITH collect(label) AS node_labels
            CALL db.relationshipTypes() YIELD relationshipType
            WITH node_labels, collect(relationshipType) AS relationship_types
            RETURN node_labels, relationship_types
            """
            
            result = self.graph.query(schema_query)
            if result and len(result) > 0:
                node_labels = result[0].get('node_labels', [])
                relationship_types = result[0].get('relationship_types', [])
                
                # System node'ları filtrele
                filtered_labels = [label for label in node_labels 
                                 if label not in ['Document', 'Chunk', '__Community__']]
                filtered_rels = [rel for rel in relationship_types 
                               if rel not in ['PART_OF', 'FIRST_CHUNK']]
                
                return filtered_labels, filtered_rels
            return [], []
        except Exception as e:
            print(f"Schema info alınırken hata: {e}")
            return [], []

    def generate_cypher_with_llm(self, query: str) -> Optional[str]:
        """LLM ile dinamik Cypher query oluştur"""
        
        if not self.llm:
            print("LLM not set, cannot generate Cypher")
            return None
            
        print(f"========== LLM CYPHER GENERATION ==========")
        print(f"Question: {query}")
        
        try:
            node_labels, relationship_types = self.get_schema_info()
            
            cypher_prompt = f"""
Sen Neo4j Cypher query uzmanısın. Verilen kullanıcı sorusuna göre uygun Cypher query'si yazacaksın.


## QUERY ÖRNEKLERİ:

### İsim ve Yıl Araması Birlikte (Document-Centric):
```cypher
// Ayça hanımın 2020 yılı poliçeleri - Document döndüren versiyon
MATCH (d:Document)
WHERE (apoc.text.clean(d.fileName) CONTAINS apoc.text.clean("Ayça")) 
  AND (d.year = "2020" OR apoc.text.clean(d.fileName) CONTAINS poc.text.clean("2020"))
RETURN d AS node
```

### Sadece İsim Bilgisi Araması (Document-Centric):
```cypher
// Ayça hanımın tüm poliçeleri - Document döndüren versiyon
MATCH (d:Document)
WHERE apoc.text.clean(d.fileName) CONTAINS apoc.text.clean("Ayça")
RETURN d AS node
``` 


## GERİ DÖNDÜRME FORMATI:
Query mutlaka şu formatı kullanmalı
```cypher
// Query logic here...
RETURN d AS node
```

apoc.text.clean kodu çok önemli!
## KULLANICI SORUSU:
{query}

Şimdi bu soruya uygun Cypher query'sini yaz. Document döndüren sorgu yaz (chunk değil). Sadece query'yi döndür, başka açıklama yapma.
"""
            
            print("=" * 80)
            print("📝 LLM'E GÖNDERİLEN PROMPT:")
            print("=" * 80)
            print(cypher_prompt)
            print("=" * 80)
            
            from langchain_core.messages import HumanMessage, SystemMessage
            
            messages = [
                SystemMessage(content="Sen Neo4j Cypher query uzmanısın."),
                HumanMessage(content=cypher_prompt)
            ]
            
            print("🤖 LLM ÇAĞRILIYOR...")
            response = self.llm.invoke(messages)
            print("✅ LLM YANIT VERDİ!")
            
            print("=" * 80)
            print("🤖 LLM'DEN GELEN HAM YANIT:")
            print("=" * 80)
            print(response.content)
            print("=" * 80)
            
            generated_query = response.content.strip()
            
            # Query'yi temizle
            if "```cypher" in generated_query:
                generated_query = generated_query.split("```cypher")[1].split("```")[0].strip()
            elif "```" in generated_query:
                generated_query = generated_query.split("```")[1].split("```")[0].strip()
                
            print("=" * 80)
            print("🔧 TEMİZLENMİŞ CYPHER QUERY:")
            print("=" * 80)
            print(generated_query)
            print("=" * 80)
            
            return generated_query
            
        except Exception as e:
            print(f"LLM Cypher generation error: {e}")
            return None

    def execute_entity_first_search(self, query: str, k: int = 10) -> List[Document]:
        """Entity-first search stratejisi uygula"""
        
        if not self.graph:
            print("Graph connection not set")
            return []
            
        print(f"========== ENTITY-FIRST SEARCH ==========")
        
        # LLM ile query oluştur
        # generated_query = """
        # MATCH (d:Document)
        #   WHERE (apoc.text.clean(d.fileName) CONTAINS apoc.text.clean("Ayça"))
        #     AND (d.year = "2020" OR apoc.text.clean(d.fileName) CONTAINS apoc.text.clean("2020"))
        #   RETURN d AS node
        # """
        generated_query = self.generate_cypher_with_llm(query)
        if not generated_query:
            print("Failed to generate Cypher query")
            return []
        
        try:
            # Query embedding oluştur
            from src.utf8_utils import normalize_unicode_text
            normalized_query = normalize_unicode_text(query)
            query_embedding = self.embedding.embed_query(normalized_query)
            
            # Query parametrelerini hazırla
            params = {
                'query_vector': query_embedding,
                'question': query,
                'k': k
            }
            
            print("=" * 80)
            print("📊 CYPHER QUERY PARAMETRELER - DETAYLI LOG:")
            print("=" * 80)
            print(f"📋 PARAMS Dictionary İçeriği:")
            for key, value in params.items():
                if key == 'query_vector':
                    print(f"  🔢 {key}: [embedding vector] - Length: {len(value) if value else 0}")
                    print(f"     Vector Type: {type(value)}")
                    print(f"     First 5 values: {value[:5] if value else 'None'}")
                    print(f"     Vector Sample Stats: min={min(value) if value else 'N/A':.4f}, max={max(value) if value else 'N/A':.4f}")
                else:
                    print(f"  📝 {key}: {repr(value)}")
                    print(f"     Type: {type(value)}")
                    print(f"     Length/Size: {len(str(value))}")
            
            print(f"📊 Total Params Count: {len(params)}")
            print(f"🔍 Params Keys: {list(params.keys())}")
            print(f"🎯 Query Vector Valid: {params.get('query_vector') is not None}")
            print(f"❓ Question Valid: {params.get('question') is not None and len(str(params.get('question', ''))) > 0}")
            print(f"🔢 K Valid: {params.get('k') is not None and params.get('k') > 0}")
            print("=" * 80)
            
            # Eğer retrieval_query var ise, generated_query ile birleştir
            if hasattr(self, 'retrieval_query') and self.retrieval_query:
                print("🔗 GENERATED QUERY İLE RETRIEVAL QUERY BİRLEŞTİRİLİYOR...")
                
                # Generated query'nin sonuçlarını direkt retrieval_query'ye geçir
                combined_query = f"""
                CALL {{
                    {generated_query}
                }}
                {self.retrieval_query}
                """
                
                print("🔄 COMBINED QUERY ÇALIŞTIRILIYOR...")
                print("=" * 80)
                print("📝 TAM COMBINED QUERY:")
                print("=" * 80)
                print(combined_query)
                print("=" * 80)
                print("📋 COMBINED QUERY PARAMETRELERİ - DETAYLI:")
                print("=" * 80)
                print(f"📊 Params Object Type: {type(params)}")
                print(f"📊 Params Object ID: {id(params)}")
                print(f"📊 Params Keys Count: {len(params)}")
                print()
                
                for key, value in params.items():
                    print(f"🔑 Parameter: '{key}'")
                    print(f"   📝 Value Type: {type(value)}")
                    
                    if key == 'query_vector':
                        if value is not None:
                            print(f"   📏 Vector Length: {len(value)}")
                            print(f"   🔢 Vector Stats: min={min(value):.6f}, max={max(value):.6f}")
                            print(f"   📊 First 3 values: {value[:3]}")
                            print(f"   📊 Last 3 values: {value[-3:]}")
                        else:
                            print(f"   ⚠️ Vector is None!")
                    else:
                        print(f"   📋 Value: {repr(value)}")
                        print(f"   📏 Value Length: {len(str(value)) if value is not None else 0}")
                    print()
                
                # Parametrelerin Neo4j query'sine geçiş öncesi son kontrol
                print("🧪 NEO4J QUERY EXECUTION ÖNCESİ PARAMETRE KONTROLÜ:")
                print(f"   ✅ query_vector hazır: {params.get('query_vector') is not None}")
                print(f"   ✅ question hazır: {params.get('question') is not None}")
                print(f"   ✅ k hazır: {params.get('k') is not None}")
                print("=" * 80)
                
                print("🚀 NEO4J GRAPH.QUERY() ÇAĞRILIYOR...")
                print(f"   📝 Query Length: {len(combined_query)} characters")
                print(f"   📊 Params Count: {len(params)} parameters")
                print("=" * 80)
                
                final_results = self.graph.query(combined_query, params)
                
                print("✅ NEO4J COMBINED QUERY TAMAMLANDI!")
                print("=" * 80)
                print("📊 QUERY EXECUTION SONUÇLARI:")
                print("=" * 80)
                print(f"🎯 Final Results Count: {len(final_results)}")
                print(f"📊 Results Type: {type(final_results)}")
                
                if final_results:
                    print(f"📋 First Result Keys: {list(final_results[0].keys()) if final_results[0] else 'No keys'}")
                    print(f"📝 Sample Result Structure:")
                    
                    for i, result in enumerate(final_results[:3]):  # İlk 3 sonucu göster
                        print(f"   Result #{i+1}:")
                        for key, value in result.items():
                            if key == 'query_vector':
                                print(f"     {key}: [vector - length: {len(value) if value else 0}]")
                            elif isinstance(value, str) and len(value) > 100:
                                print(f"     {key}: '{value[:100]}...' (length: {len(value)})")
                            else:
                                print(f"     {key}: {repr(value)} ({type(value).__name__})")
                else:
                    print("⚠️ Sonuç boş!")
                    
                print("=" * 80)
            else:
                print("⚠️ Retrieval query bulunamadı, sadece LLM query çalıştırılıyor")
                print("🚀 LLM CYPHER QUERY ÇALIŞTIRILIYOR...")
                final_results = self.graph.query(generated_query, params)
                print("✅ LLM CYPHER QUERY TAMAMLANDI!")
            
            print(f"Entity-first query final results: {len(final_results)} items")
            
            # Sonuçları Document formatına dönüştür
            documents = []
            print("=" * 80)
            print("📄 DOCUMENT'LERE DÖNÜŞTÜRÜLÜYOR:")
            print("=" * 80)
            
            for i, result in enumerate(final_results):
                if 'text' in result and 'score' in result:
                    # Eğer retrieval query sonucu ise (text formatında)
                    text = result['text']
                    score = result['score']
                    metadata = result.get('metadata', {})
                    
                    print(f"Document {i+1} (from retrieval):")
                    print(f"  Score: {score}")
                    print(f"  Source: {metadata.get('source', 'N/A')}")
                    print(f"  Text Preview: {text[:150]}...")
                    
                    documents.append(Document(
                        page_content=text,
                        metadata={
                            **metadata,
                            "retrieval_method": "entity_first_with_retrieval",
                            "combined_score": score
                        }
                    ))
                    
                elif 'node' in result and 'score' in result:
                    # Eğer direkt LLM query sonucu ise (node formatında)
                    node = result['node']
                    score = result['score']
                    
                    # Document node mu Chunk node mu kontrol et
                    if hasattr(node, 'fileName'):  # Document node
                        text = f"Document: {getattr(node, 'fileName', 'Unknown')}"
                        if hasattr(node, 'fileSource'):
                            text += f"\nSource: {getattr(node, 'fileSource', '')}"
                        
                        print(f"Document {i+1} (from LLM - Document node):")
                        print(f"  FileName: {getattr(node, 'fileName', 'N/A')}")
                        print(f"  Score: {score}")
                        
                        documents.append(Document(
                            page_content=text,
                            metadata={
                                "source": getattr(node, 'fileName', 'Entity-First-Search-Direct'),
                                "score": score,
                                "node_type": "Document",
                                "file_name": getattr(node, 'fileName', None),
                                "retrieval_method": "entity_first_direct_document",
                                "combined_score": score
                            }
                        ))
                        
                    else:  # Chunk veya diğer node'lar
                        text = getattr(node, 'text', str(node))
                        
                        print(f"Document {i+1} (from LLM - Other node):")
                        print(f"  Node ID: {getattr(node, 'id', 'N/A')}")
                        print(f"  Score: {score}")
                        print(f"  Text Preview: {text[:150]}...")
                        
                        documents.append(Document(
                            page_content=text,
                            metadata={
                                "source": "Entity-First-Search-Direct",
                                "score": score,
                                "node_id": getattr(node, 'id', None),
                                "retrieval_method": "entity_first_direct",
                                "combined_score": score
                            }
                        ))
            
            print(f"✅ Toplam {len(documents)} document oluşturuldu")
            print("=" * 80)
            
            return documents
            
        except Exception as e:
            print(f"Entity-first search execution error: {e}")
            return []

    def similarity_search(
        self,
        query: str,
        k: int = 4,
        params: Dict[str, Any] = {},
        filter: Optional[Dict[str, Any]] = None,
        effective_search_ratio: int = 1,
        **kwargs: Any,
    ) -> List[Document]:
        """
        Override edilmiş similarity search - her zaman entity-first search kullanır
        """
        
        print("=" * 60)
        print("🔍 CUSTOM NEO4J VECTOR SIMILARITY_SEARCH ÇAĞRILDI!")
        print("=" * 60)
        print(f"Query: {query}")
        print(f"K: {k}")
        print(f"LLM Available: {self.llm is not None}")
        
        # Her zaman entity-first search kullan
        results = self.execute_entity_first_search(query, k)
        print(f"Entity-first search results: {len(results)}")
        
        print(f"========== SEARCH RESULTS SUMMARY ==========")
        print(f"Total Results: {len(results)}")
        for i, doc in enumerate(results):
            print(f"  {i+1}. {doc.metadata.get('retrieval_method', 'unknown')} "
                  f"(score: {doc.metadata.get('combined_score', 'N/A')})")
        print("============================================")
        
        return results

    def similarity_search_with_score(
        self,
        query: str,
        k: int = 4,
        params: Dict[str, Any] = {},
        filter: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> List[tuple]:
        """
        LangChain retriever'ın gerçekte çağırdığı metod - score ile birlikte döndürür
        """
        
        print("=" * 60)
        print("🔍 SIMILARITY_SEARCH_WITH_SCORE ÇAĞRILDI!")
        print("=" * 60)
        print(f"Query: {query}")
        print(f"K: {k}")
        
        # Normal similarity_search'u çağır
        docs = self.similarity_search(query, k, params, filter, **kwargs)
        
        # Score'ları ekle
        scored_docs = []
        for doc in docs:
            score = doc.metadata.get('combined_score', 0.5)
            scored_docs.append((doc, score))
        
        print(f"🎯 Returning {len(scored_docs)} scored documents")
        return scored_docs

    def _similarity_search_with_relevance_scores(
        self,
        query: str,
        k: int = 4,
        **kwargs: Any,
    ) -> List[tuple]:
        """
        LangChain'in internal çağırdığı başka bir metod
        """
        
        print("=" * 60)
        print("🔍 _SIMILARITY_SEARCH_WITH_RELEVANCE_SCORES ÇAĞRILDI!")
        print("=" * 60)
        
        return self.similarity_search_with_score(query, k, **kwargs)

    def as_retriever(self, **kwargs):
        """
        Custom retriever oluştur
        """
        print("=" * 60)
        print("🔧 CUSTOM AS_RETRIEVER ÇAĞRILDI!")
        print("=" * 60)
        print(f"Kwargs: {kwargs}")
        
        # Parent as_retriever'ı çağır ama bizim metodlarımızı kullanacak
        retriever = super().as_retriever(**kwargs)
        print("✅ Custom retriever oluşturuldu!")
        
        return retriever

    @classmethod
    def from_existing_graph_with_llm(
        cls: Type["CustomNeo4jVector"],
        embedding: Embeddings,
        node_label: str,
        embedding_node_property: str,
        text_node_properties: List[str],
        llm: BaseLanguageModel,
        graph: Neo4jGraph,
        **kwargs: Any,
    ) -> "CustomNeo4jVector":
        """
        LLM entegreli custom Neo4jVector oluştur
        """
        
        # Önce normal Neo4jVector oluştur
        store = cls.from_existing_graph(
            embedding=embedding,
            node_label=node_label,
            embedding_node_property=embedding_node_property,
            text_node_properties=text_node_properties,
            **kwargs
        )
        
        # LLM, Graph ve Retrieval Query'yi set et
        store.set_llm(llm)
        store.set_graph(graph)
        
        # Retrieval query'yi kwargs'tan al
        retrieval_query = kwargs.get('retrieval_query')
        if retrieval_query:
            store.set_retrieval_query(retrieval_query)
            print(f"🔗 Retrieval query set edildi: {len(retrieval_query)} karakter")
        else:
            print("⚠️ Retrieval query bulunamadı")
        
        print(f"========== CUSTOM NEO4J VECTOR STORE READY ==========")
        print(f"Node Label: {node_label}")
        print(f"Embedding Property: {embedding_node_property}")
        print(f"Text Properties: {text_node_properties}")
        print(f"LLM: {type(llm).__name__}")
        print(f"Graph: Connected")
        print("==================================================")
        
        return store
