

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
Sen Neo4j Cypher query uzmanısın. Verilen schema ve kullanıcı sorusuna göre uygun Cypher query'si yazacaksın.



## QUERY YAZMA KURALLARI:

1. **Document Odaklı Başlangıç**: Önce doğru Document'i bul
2. **Entity Farkındalığı**: Document'in entity'lerini göz önünde bulundur  
3. **Document Dönüşü**: Sonuçta mutlaka Document'ları döndür (chunk değil)
4. **Skor Hesabı**: Document uygunluğuna göre skoru belirle
6. **apoc.text.clean**: Türkçe karakter sorunları için mutlaka kullan

## QUERY ÖRNEKLERİ:


Custom Neo4j Vector Store for LLM Graph Builder
Önce entity-based arama yapar, sonra chunk-level ### İsim ve Yıl Araması Birlikte:
```cypher## QUERY ÖRNEKLERİ:

### İsim ve Yıl Araması Birlikte (Document-Centric):
```cypher
// Ayça hanımın 2020 yılı poliçeleri - Document döndüren versiyon
MATCH (d:Document)
WHERE (apoc.text.clean(d.fileName) CONTAINS apoc.text.clean("Ayça")) 
  AND (d.year = "2020" OR d.fileName CONTAINS "2020")
WITH d,
     CASE WHEN d.year = "2020" AND apoc.text.clean(d.fileName) CONTAINS apoc.text.clean("Ayça") THEN 0.95
          WHEN d.fileName CONTAINS "2020" AND apoc.text.clean(d.fileName) CONTAINS apoc.text.clean("Ayça") THEN 0.85
          ELSE 0.75 
     END AS relevance_score
RETURN d AS node, relevance_score AS score
```

### Sadece İsim Bilgisi Araması (Document-Centric):
```cypher
// Ayça hanımın tüm poliçeleri - Document döndüren versiyon
MATCH (d:Document)
WHERE apoc.text.clean(d.fileName) CONTAINS apoc.text.clean("Ayça")
RETURN d AS node, 0.9 AS score

``` 
MATCH (d:Document)
WHERE (apoc.text.clean(d.fileName) CONTAINS apoc.text.clean("Ayça")) 
  AND (d.year = "2020" OR d.fileName CONTAINS "2020")
WITH d,
     CASE WHEN d.year = "2020" AND apoc.text.clean(d.fileName) CONTAINS apoc.text.clean("Ayça") THEN 0.95
          WHEN d.fileName CONTAINS "2020" AND apoc.text.clean(d.fileName) CONTAINS apoc.text.clean("Ayça") THEN 0.85
          ELSE 0.75 
     END AS relevance_score
RETURN d AS node, relevance_score AS score

```

### Poliçe Türü Araması:
```cypher
// Belirli poliçe türü araması - Document döndüren versiyon
MATCH (d:Document)
WHERE apoc.text.clean(d.fileName) CONTAINS apoc.text.clean("DASK")
   OR apoc.text.clean(d.fileName) CONTAINS apoc.text.clean("Konut")
   OR apoc.text.clean(d.fileName) CONTAINS apoc.text.clean("Trafik")
RETURN d AS node, 0.85 AS score

```

## GERİ DÖNDÜRME FORMATI:
Query mutlaka şu formatı kullanmalı
```cypher
// Query logic here...
RETURN d AS node, [score] AS score

```ar

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
        generated_query = self.generate_cypher_with_llm(query)
        if not generated_query:
            print("Failed to generate Cypher query")
            return []
        
        try:
            # Query embedding oluştur
            query_embedding = self.embedding.embed_query(query)
            
            # Query parametrelerini hazırla
            params = {
                'query_vector': query_embedding,
                'question': query,
                'k': k
            }
            
            print("=" * 80)
            print("📊 CYPHER QUERY PARAMETRELER:")
            print("=" * 80)
            print(f"Query Vector Length: {len(query_embedding)}")
            print(f"Question: {query}")
            print(f"K: {k}")
            print("=" * 80)
            
            # Eğer retrieval_query var ise, generated_query ile birleştir
            if hasattr(self, 'retrieval_query') and self.retrieval_query:
                print("🔗 GENERATED QUERY İLE RETRIEVAL QUERY BİRLEŞTİRİLİYOR...")
                
                # Generated query'nin sonuçlarını direkt retrieval_query'ye geçir
                combined_query = f"""
                CALL {{
                    {generated_query}
                }}
                WITH node, score
                {self.retrieval_query}
                """
                
                print("🔄 COMBINED QUERY ÇALIŞTIRILIYOR...")
                print("=" * 80)
                print("📝 TAM COMBINED QUERY:")
                print("=" * 80)
                print(combined_query)
                print("=" * 80)
                print("📋 COMBINED QUERY PARAMETRELERİ:")
                print("=" * 80)
                print(f"Query parametreleri: {list(params.keys())}")
                for key, value in params.items():
                    if key != 'query_vector':  # Vector'ü print etme, çok uzun
                        print(f"  {key}: {value}")
                print("=" * 80)
                
                final_results = self.graph.query(combined_query, params)
                print("✅ COMBINED QUERY TAMAMLANDI!")
                print(f"🎯 Final Results Count: {len(final_results)}")
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
