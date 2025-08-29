"""
LangExtract entegrasyonu için backend modülü
"""
import logging
import time
from typing import List, Dict, Any, Tuple
from langchain.docstore.document import Document
from langchain_community.graphs.graph_document import GraphDocument, Node, Relationship
from src.shared.llm_graph_builder_exception import LLMGraphBuilderException
from langextract_graph_integration import LangExtractGraphExtractor
from src.entity_resolver_simple import SimpleEntityResolver

# Configure logging
logging.basicConfig(level=logging.INFO)

# LangExtract debug loglarını kapat
langextract_logger = logging.getLogger('langextract')
langextract_logger.setLevel(logging.ERROR)

# LangExtract debug loglarını tamamen kapat
langextract_debug_logger = logging.getLogger('langextract.debug')
langextract_debug_logger.setLevel(logging.ERROR)

# LangExtract tüm submodüllerini kapat
logging.getLogger('langextract.core').setLevel(logging.ERROR)
logging.getLogger('langextract.core.tokenizer').setLevel(logging.ERROR)
logging.getLogger('langextract.core.annotator').setLevel(logging.ERROR)
logging.getLogger('langextract.core.aligner').setLevel(logging.ERROR)
logging.getLogger('langextract.core.chunker').setLevel(logging.ERROR)
logging.getLogger('langextract.core.resolver').setLevel(logging.ERROR)
logging.getLogger('langextract.annotators').setLevel(logging.ERROR)
logging.getLogger('langextract.providers').setLevel(logging.ERROR)
logging.getLogger('langextract.providers.openai').setLevel(logging.ERROR)
logging.getLogger('langextract.extractors').setLevel(logging.ERROR)

# ABSL (Google logging) loglarını da kapat
logging.getLogger('absl').setLevel(logging.ERROR)

def get_combined_chunks_for_langextract(chunkId_chunkDoc_list, chunks_to_combine):
    """
    LangExtract için chunk'ları combine et
    LangExtract tek string ile çalıştığı için chunk'ları birleştiriyoruz
    """
    combined_texts = []
    
    # chunks_to_combine kadar chunk'ı al ve birleştir
    for i in range(0, len(chunkId_chunkDoc_list), chunks_to_combine):
        chunk_group = chunkId_chunkDoc_list[i:i + chunks_to_combine]
        
        # Her chunk grubunu tek string'e birleştir
        combined_text = ""
        chunk_ids = []
        
        for chunk_data in chunk_group:
            chunk_doc = chunk_data["chunk_doc"]
            chunk_id = chunk_data["chunk_id"]
            
            # Chunk text'ini al
            chunk_text = chunk_doc.page_content if hasattr(chunk_doc, 'page_content') else str(chunk_doc)
            
            combined_text += chunk_text + "\n\n"
            chunk_ids.append(chunk_id)
        
        combined_texts.append({
            "text": combined_text.strip(),
            "chunk_ids": chunk_ids,
            "chunk_count": len(chunk_group)
        })
    
    return combined_texts

def get_full_document_for_langextract(chunkId_chunkDoc_list):
    """
    Tüm chunk'ları tek doküman olarak birleştir
    LangExtract'in tüm dokümanda global extraction yapmasını sağlar
    """
    full_text = ""
    all_chunk_ids = []
    
    logging.info(f"📄 Tüm dokümanı birleştiriliyor: {len(chunkId_chunkDoc_list)} chunk")
    
    for chunk_data in chunkId_chunkDoc_list:
        chunk_doc = chunk_data["chunk_doc"]
        chunk_id = chunk_data["chunk_id"]
        
        # Chunk text'ini al
        chunk_text = chunk_doc.page_content if hasattr(chunk_doc, 'page_content') else str(chunk_doc)
        
        full_text += chunk_text + "\n\n"
        all_chunk_ids.append(chunk_id)
    
    # Doküman istatistikleri
    word_count = len(full_text.split())
    char_count = len(full_text)
    
    logging.info(f"📊 Birleştirilmiş doküman:")
    logging.info(f"  📝 Toplam karakter: {char_count:,}")
    logging.info(f"  📝 Toplam kelime: {word_count:,}")
    logging.info(f"  📄 Chunk sayısı: {len(all_chunk_ids)}")
    
    return {
        "text": full_text.strip(),
        "chunk_ids": all_chunk_ids,
        "chunk_count": len(chunkId_chunkDoc_list),
        "word_count": word_count,
        "char_count": char_count
    }

async def get_graph_from_langextract_full_document(
    model: str, 
    chunkId_chunkDoc_list: List[Dict], 
    allowedNodes: str, 
    allowedRelationship: str, 
    file_name: str = None, 
    additional_instructions: str = None, 
    graph=None,
    max_pages: int = None
) -> List[Any]:
    """
    LangExtract kullanarak TÜM DOKÜMAN için tek seferde graph extraction yap
    Chunk'ları birleştirip global extraction yapar
    
    Args:
        model: Model adı (LangExtract için kullanılmaz ama uyumluluk için)
        chunkId_chunkDoc_list: Chunk data listesi
        allowedNodes: İzin verilen node tipleri (comma separated)
        allowedRelationship: İzin verilen relationship'ler (comma separated triplets)
        file_name: Dosya adı
        additional_instructions: Ek talimatlar
        graph: Graph instance (entity resolution için)
        
    Returns:
        List[GraphDocument]: LangChain formatında graph document'lar
    """
    try:
        start_time = time.time()
        
        # LangExtract loglarını tamamen kapat
        import langextract
        langextract_root_logger = logging.getLogger('langextract')
        langextract_root_logger.setLevel(logging.CRITICAL)
        
        # Tüm LangExtract alt modüllerini kapat
        for logger_name in ['langextract.core', 'langextract.core.tokenizer', 'langextract.core.annotator',
                           'langextract.core.aligner', 'langextract.core.chunker', 'langextract.core.resolver',
                           'langextract.annotators', 'langextract.providers', 'langextract.providers.openai',
                           'langextract.extractors', 'langextract.debug']:
            logging.getLogger(logger_name).setLevel(logging.CRITICAL)
        
        # Giriş parametrelerini logla
        logging.info("=== get_graph_from_langextract_full_document BAŞLADI ===")
        logging.info(f"🚀 FULL DOCUMENT EXTRACTION MODE")
        logging.info(f"Model: {model} (LangExtract kullanılacak)")
        logging.info(f"File name: {file_name}")
        logging.info(f"Max pages: {max_pages}")
        logging.info(f"Additional instructions var mı: {additional_instructions is not None}")
        logging.info(f"Toplam chunk sayısı: {len(chunkId_chunkDoc_list)}")
        
        # Sayfa sınırlandırma - Eğer max_pages belirtilmişse sadece belirtilen sayfalardaki chunk'ları kullan
        filtered_chunk_list = chunkId_chunkDoc_list
        if max_pages is not None and max_pages > 0:
            logging.info(f"🔢 Sayfa sınırlandırma aktif: 1-{max_pages} arası sayfalar işlenecek")
            filtered_chunk_list = []
            for chunk_data in chunkId_chunkDoc_list:
                chunk_doc = chunk_data["chunk_doc"]
                chunk_id = chunk_data["chunk_id"]
                
                # Document metadata'sından page_number'ı al
                page_number = None
                if hasattr(chunk_doc, 'metadata') and chunk_doc.metadata:
                    page_number = chunk_doc.metadata.get('page_number')
                
                # Page number kontrolü
                if page_number is not None:
                    try:
                        page_num = int(page_number)
                        if 1 <= page_num <= max_pages:
                            filtered_chunk_list.append(chunk_data)
                            logging.info(f"✅ Chunk {chunk_id} (sayfa {page_num}) dahil edildi")
                        else:
                            logging.info(f"❌ Chunk {chunk_id} (sayfa {page_num}) sayfa sınırı dışında, atlandı")
                    except (ValueError, TypeError):
                        logging.warning(f"⚠️ Chunk {chunk_id} için geçersiz page_number: {page_number}, dahil edildi")
                        filtered_chunk_list.append(chunk_data)
                else:
                    logging.warning(f"⚠️ Chunk {chunk_id} için page_number bulunamadı, dahil edildi")
                    filtered_chunk_list.append(chunk_data)
            
            logging.info(f"🔢 Sayfa filtrelemesi sonrası: {len(filtered_chunk_list)} chunk kaldı")
        else:
            logging.info("🔢 Sayfa sınırlandırma yok, tüm chunk'lar işlenecek")
        
        # Raw giriş değerlerini logla
        logging.info(f"RAW allowedNodes: '{allowedNodes}'")
        logging.info(f"RAW allowedRelationship: '{allowedRelationship}'")
        
        # Tüm dokümanı birleştir (filtrelenmiş chunk'larla)
        full_document = get_full_document_for_langextract(filtered_chunk_list)
        
        # allowedNodes işleme
        allowed_nodes = []
        if allowedNodes:
            allowed_nodes = [node.strip() for node in allowedNodes.split(',') if node.strip()]
            logging.info(f"İşlenmiş allowed_nodes: {allowed_nodes}")
        
        # allowedRelationship işleme 
        allowed_relationships = []
        if allowedRelationship:
            items = [item.strip() for item in allowedRelationship.split(',') if item.strip()]
            if len(items) % 3 != 0:
                raise LLMGraphBuilderException("allowedRelationship must be a multiple of 3 (source, relationship, target)")
            
            for i in range(0, len(items), 3):
                source, relation, target = items[i:i + 3]
                allowed_relationships.append((source, relation, target))
            logging.info(f"İşlenmiş allowed_relationships: {allowed_relationships}")
        
        # LangExtract extractor oluştur
        extractor = LangExtractGraphExtractor()
        
        # Tek seferde tüm doküman için extraction
        logging.info(f"🔄 Tüm doküman extraction başlıyor...")
        logging.info(f"📄 İşlenecek text uzunluğu: {full_document['char_count']:,} karakter")
        
        # LangExtract ile extraction
        result = await extractor.extract_graph(
            text=full_document["text"],
            allowed_nodes=allowed_nodes if allowed_nodes else None,
            allowed_relationships=allowed_relationships if allowed_relationships else None
        )
        
        # GraphExtractionResult'tan entities ve relationships al
        entities = result.entities
        relationships = result.relationships
        
        logging.info(f"✅ Extraction tamamlandı:")
        logging.info(f"  🎯 Çıkarılan entity sayısı: {len(entities)}")
        logging.info(f"  🔗 Çıkarılan relationship sayısı: {len(relationships)}")
        
        # GraphDocument formatına çevir (mevcut sisteme uyumlu) - Entity Resolution ile
        graph_doc = convert_langextract_to_graph_document(
            entities=entities,
            relationships=relationships,
            chunk_ids=full_document["chunk_ids"],
            source_text=f"Full Document ({full_document['word_count']} words, {full_document['chunk_count']} chunks)",
            graph=graph  # Entity resolution için graph objesi geç
        )
        
        end_time = time.time()
        total_time = end_time - start_time
        
        # Sonuçları logla
        logging.info("📊 Full Document LangExtract extraction tamamlandı:")
        logging.info(f"  ⏱️ Toplam süre: {total_time:.2f} saniye")
        logging.info(f"  📄 İşlenen doküman boyutu: {full_document['char_count']:,} karakter")
        logging.info(f"  🎯 Çıkarılan entity sayısı: {len(entities)}")
        logging.info(f"  🔗 Çıkarılan relationship sayısı: {len(relationships)}")
        logging.info(f"  ⚡ Karakter başına süre: {total_time/full_document['char_count']*1000:.3f} ms/char")
        
        return [graph_doc]  # Tek GraphDocument döndür
        
    except Exception as e:
        logging.error(f"Error in get_graph_from_langextract_full_document: {e}", exc_info=True)
        raise LLMGraphBuilderException(f"Error in full document LangExtract extraction: {e}")

async def get_graph_from_langextract(
    model: str, 
    chunkId_chunkDoc_list: List[Dict], 
    allowedNodes: str, 
    allowedRelationship: str, 
    chunks_to_combine: int, 
    file_name: str = None, 
    additional_instructions: str = None, 
    graph=None
) -> List[Any]:
    """
    LangExtract kullanarak graph extraction yap
    
    Args:
        model: Model adı (LangExtract için kullanılmaz ama uyumluluk için)
        chunkId_chunkDoc_list: Chunk data listesi
        allowedNodes: İzin verilen node tipleri (comma separated)
        allowedRelationship: İzin verilen relationship'ler (comma separated triplets)
        chunks_to_combine: Combine edilecek chunk sayısı
        file_name: Dosya adı
        additional_instructions: Ek talimatlar
        graph: Graph instance (kullanılmaz)
        
    Returns:
        List[GraphDocument]: LangChain formatında graph document'lar
    """
    try:
        start_time = time.time()
        
        # Giriş parametrelerini logla
        logging.info("=== get_graph_from_langextract BAŞLADI ===")
        logging.info(f"Model: {model} (LangExtract kullanılacak)")
        logging.info(f"File name: {file_name}")
        logging.info(f"Chunks to combine: {chunks_to_combine}")
        logging.info(f"Additional instructions var mı: {additional_instructions is not None}")
        logging.info(f"Toplam chunk sayısı: {len(chunkId_chunkDoc_list)}")
        
        # Raw giriş değerlerini logla
        logging.info(f"RAW allowedNodes: '{allowedNodes}'")
        logging.info(f"RAW allowedRelationship: '{allowedRelationship}'")
        
        # Chunk'ları combine et
        combined_chunks = get_combined_chunks_for_langextract(chunkId_chunkDoc_list, chunks_to_combine)
        logging.info(f"Combined chunks oluşturuldu: {len(combined_chunks)} grup")
        
        # allowedNodes işleme
        allowed_nodes = []
        if allowedNodes:
            allowed_nodes = [node.strip() for node in allowedNodes.split(',') if node.strip()]
            logging.info(f"İşlenmiş allowed_nodes: {allowed_nodes}")
        
        # allowedRelationship işleme 
        allowed_relationships = []
        if allowedRelationship:
            items = [item.strip() for item in allowedRelationship.split(',') if item.strip()]
            if len(items) % 3 != 0:
                raise LLMGraphBuilderException("allowedRelationship must be a multiple of 3 (source, relationship, target)")
            
            for i in range(0, len(items), 3):
                source, relation, target = items[i:i + 3]
                allowed_relationships.append((source, relation, target))
            logging.info(f"İşlenmiş allowed_relationships: {allowed_relationships}")
        
        # LangExtract extractor oluştur
        extractor = LangExtractGraphExtractor()
        
        # Her combined chunk için extraction yap
        all_graph_documents = []
        total_entities = 0
        total_relationships = 0
        
        for i, chunk_data in enumerate(combined_chunks):
            logging.info(f"İşleniyor chunk grubu {i+1}/{len(combined_chunks)}")
            
            # LangExtract ile extraction
            result = await extractor.extract_graph(
                text=chunk_data["text"],
                allowed_nodes=allowed_nodes if allowed_nodes else None,
                allowed_relationships=allowed_relationships if allowed_relationships else None
            )
            
            # GraphExtractionResult'tan entities ve relationships al
            entities = result.entities
            relationships = result.relationships
            
            total_entities += len(entities)
            total_relationships += len(relationships)
            
            # GraphDocument formatına çevir (mevcut sisteme uyumlu) - Entity Resolution ile
            graph_doc = convert_langextract_to_graph_document(
                entities=entities,
                relationships=relationships,
                chunk_ids=chunk_data["chunk_ids"],
                source_text=chunk_data["text"][:200] + "..." if len(chunk_data["text"]) > 200 else chunk_data["text"],
                graph=graph  # Entity resolution için graph objesi geç
            )
            
            all_graph_documents.append(graph_doc)
        
        end_time = time.time()
        total_time = end_time - start_time
        
        # Sonuçları logla
        logging.info("📊 LangExtract extraction tamamlandı:")
        logging.info(f"  ⏱️ Toplam süre: {total_time:.2f} saniye")
        logging.info(f"  📄 İşlenen chunk grubu sayısı: {len(combined_chunks)}")
        logging.info(f"  🎯 Çıkarılan entity sayısı: {total_entities}")
        logging.info(f"  🔗 Çıkarılan relationship sayısı: {total_relationships}")
        logging.info(f"  ⚡ Chunk grubu başına ortalama süre: {total_time/len(combined_chunks):.2f} saniye")
        
        return all_graph_documents
        
    except Exception as e:
        logging.error(f"Error in get_graph_from_langextract: {e}", exc_info=True)
        raise LLMGraphBuilderException(f"Error in getting graph from LangExtract: {e}")


def convert_langextract_to_graph_document(entities, relationships, chunk_ids, source_text, graph=None):
    """
    LangExtract çıktısını LangChain GraphDocument formatına çevir
    Entity Resolution ile duplicate entity'leri önle
    """
    try:
        # Import GraphDocument ve Node/Relationship classları
        from langchain_community.graphs.graph_document import GraphDocument, Node, Relationship
        
        # Entity Resolution için SimpleEntityResolver kullan
        entity_id_mapping = {}
        
        if graph:
            resolver = SimpleEntityResolver(similarity_threshold=0.6)  # Düşük threshold
            entity_list = [
                {
                    'id': entity.id,
                    'name': getattr(entity, 'name', entity.id),
                    'entity_type': entity.label
                }
                for entity in entities
            ]
            
            final_entities, entity_id_mapping = resolver.resolve_entities(graph, entity_list)
            logging.info(f"✅ Entity Resolution sonucu:")
            logging.info(f"  - Yeni node sayısı: {len(final_entities)}")
            logging.info(f"  - Mevcut entity kullanımı: {len(entity_list) - len(final_entities)}")
        else:
            # Graph yok ise, direkt mapping yap
            for entity in entities:
                entity_id_mapping[entity.id] = entity.id
        
        # Node'ları oluştur (sadece yeni entity'ler için)
        nodes = []
        for entity in entities:
            final_id = entity_id_mapping.get(entity.id, entity.id)
            
            # Eğer entity yeniden kullanılıyorsa (mapping farklı ID'ye işaret ediyorsa), node yaratma
            if final_id != entity.id:
                logging.debug(f"🔗 Entity yeniden kullanılıyor: '{entity.id}' -> '{final_id}'")
                continue
                
            # Yeni node oluştur - Hybrid approach: Type-specific + Generic labels
            node_properties = {
                **entity.properties,  # Original properties
                "entity_type": entity.label,  # For generic queries
                "name": getattr(entity, 'name', entity.id),  # Ensure name property
                "extracted_by": "langextract"  # Extraction method
            }
            
            node = Node(
                id=entity.id,
                type=f"{entity.label}:__Entity__",  # Hybrid labels: Person:__Entity__
                properties=node_properties
            )
            nodes.append(node)
        
        # Relationship'ler için reverse mapping oluştur (normalized -> original)
        reverse_mapping = {}
        for entity in entities:
            normalized_id = entity.id.lower().replace(' ', '_').replace('ç', 'c').replace('ö', 'o').replace('ş', 's').replace('ı', 'i').replace('ü', 'u').replace('ğ', 'g')
            reverse_mapping[normalized_id] = entity.id
            
            # Policy entities için "policy_" prefix'i ile de reverse mapping ekle
            if entity.label.lower() == 'policy':
                policy_prefixed_id = f"policy_{normalized_id}"
                reverse_mapping[policy_prefixed_id] = entity.id
        
        logging.info(f"Entity ID mapping (sample): {dict(list(entity_id_mapping.items())[:5])}")
        
        # Relationship'leri oluştur
        rels = []
        for rel in relationships:
            # Önce normalized ID'leri gerçek isimlere çevir
            original_source_id = reverse_mapping.get(rel.source_id, rel.source_id)
            original_target_id = reverse_mapping.get(rel.target_id, rel.target_id)
            
            # Sonra gerçek isimlerden actual ID'leri bul
            actual_source_id = entity_id_mapping.get(original_source_id, original_source_id)
            actual_target_id = entity_id_mapping.get(original_target_id, original_target_id)
            
            logging.info(f"🔗 Relationship mapping: {rel.source_id} -> {original_source_id} -> {actual_source_id}, {rel.target_id} -> {original_target_id} -> {actual_target_id}")
            
            # Source ve target node'ları bul
            source_node = next((n for n in nodes if n.id == actual_source_id), None)
            target_node = next((n for n in nodes if n.id == actual_target_id), None)
            
            if source_node and target_node:
                # Hybrid relationship approach: HAS_RELATION with semantic properties
                relationship_properties = {
                    **rel.properties,  # Original properties
                    "semantic_type": rel.type,  # Original relationship type
                    "relation_type": rel.type.lower(),  # Normalized for LLM discovery
                    "source_entity_type": source_node.type,  # For cross-domain queries
                    "target_entity_type": target_node.type,  # For cross-domain queries
                    "extracted_by": "langextract"  # Extraction method
                }
                
                relationship = Relationship(
                    source=source_node,
                    target=target_node,
                    type="HAS_RELATION",
                    properties=relationship_properties
                )
                rels.append(relationship)
                logging.info(f"✅ Relationship oluşturuldu: {source_node.id} -[HAS_RELATION {{semantic_type: '{rel.type}'}}]-> {target_node.id}")
            else:
                logging.warning(f"Relationship için node bulunamadı: GraphRelationship(source_id='{rel.source_id}', target_id='{rel.target_id}', type='{rel.type}', properties={rel.properties}) (original: {original_source_id}->{original_target_id}, mapped: {actual_source_id}->{actual_target_id})")
        
        # Document oluştur
        document = Document(
            page_content=source_text,
            metadata={
                "chunk_ids": chunk_ids,
                "entity_mapping": entity_id_mapping
            }
        )
        
        # GraphDocument oluştur
        graph_document = GraphDocument(
            nodes=nodes,
            relationships=rels,
            source=document
        )
        
        logging.info(f"✅ GraphDocument oluşturuldu: {len(nodes)} node, {len(rels)} relationship")
        return graph_document
        
    except Exception as e:
        logging.error(f"Error in convert_langextract_to_graph_document: {e}", exc_info=True)
        raise LLMGraphBuilderException(f"Error converting LangExtract to graph: {e}")


# Entity resolution helper fonksiyonları kaldırıldı - SimpleEntityResolver kullanıyoruz
        graph_document = GraphDocument(
            nodes=nodes,
            relationships=rels,
            source=document
        )
        
        logging.info(f"✅ GraphDocument oluşturuldu: {len(nodes)} node, {len(rels)} relationship")
        return graph_document
        
    except Exception as e:
        logging.error(f"Error converting LangExtract output to GraphDocument: {e}")
        raise LLMGraphBuilderException(f"Error converting LangExtract output: {e}")
