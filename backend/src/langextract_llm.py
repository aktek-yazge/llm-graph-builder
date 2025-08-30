"""
LangExtract entegrasyonu için backend modülü
"""
import logging
import time
import os
from typing import List, Dict, Any, Tuple
from langchain.docstore.document import Document
from langchain_community.graphs.graph_document import GraphDocument, Node, Relationship
from src.shared.llm_graph_builder_exception import LLMGraphBuilderException
from langextract_graph_integration import LangExtractGraphExtractor
from src.entity_resolver_simple import SimpleEntityResolver

# Configure logging
logging.basicConfig(level=logging.INFO)

def suppress_langextract_logs():
    """
    LangExtract ve ilgili kütüphanelerin tüm loglarını tamamen kapat
    Environment variable LANGEXTRACT_LOG_LEVEL ile kontrol edilebilir
    """
    # Environment variable ile log seviyesini kontrol et
    log_level = os.environ.get('LANGEXTRACT_LOG_LEVEL', 'CRITICAL').upper()
    
    # Geçerli log seviyeleri
    valid_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
    if log_level not in valid_levels:
        log_level = 'CRITICAL'
    
    # Numeric log level
    numeric_level = getattr(logging, log_level)
    
    # Ana LangExtract logger'ları
    langextract_loggers = [
        'langextract',
        'langextract.debug',
        'langextract.core',
        'langextract.core.tokenizer',
        'langextract.core.annotator', 
        'langextract.core.aligner',
        'langextract.core.chunker',
        'langextract.core.resolver',
        'langextract.annotators',
        'langextract.providers',
        'langextract.providers.openai',
        'langextract.extractors',
        'langextract.models',
        'langextract.utils',
        'langextract.config',
        'langextract.graph',
        'langextract.pipeline',
        # Alt modüller
        'langextract.core.extractor',
        'langextract.core.nlp',
        'langextract.core.graph_builder',
        'langextract.providers.base',
        'langextract.annotators.base',
        'langextract.annotators.spacy',
        'langextract.annotators.transformers',
        # Google/ABSL logları
        'absl',
        'absl.logging',
        'google',
        'google.cloud',
        'googleapis',
        # TensorFlow/PyTorch logları
        'tensorflow',
        'torch',
        'transformers',
        'transformers.tokenization_utils_base',
        'transformers.generation_utils',
        # HTTP request logları
        'urllib3',
        'urllib3.connectionpool',
        'requests',
        'httpx',
        'httpcore',
        # Diğer olası verbose logger'lar
        'matplotlib',
        'PIL',
        'spacy',
        'numpy'
    ]
    
    # Tüm logger'ları belirlenen seviyeye ayarla
    for logger_name in langextract_loggers:
        logger = logging.getLogger(logger_name)
        logger.setLevel(numeric_level)
        
        # CRITICAL seviyesinde ise tamamen devre dışı bırak
        if log_level == 'CRITICAL':
            logger.disabled = True
            # Handler'ları da temizle
            for handler in logger.handlers[:]:
                logger.removeHandler(handler)
    
    # Root logger'da da filtreleme yap (sadece CRITICAL seviyesinde)
    if log_level == 'CRITICAL':
        root_logger = logging.getLogger()
        
        class LangExtractFilter(logging.Filter):
            def filter(self, record):
                # LangExtract ile ilgili herhangi bir log'u engelle
                return not any(pattern in record.name.lower() for pattern in 
                              ['langextract', 'absl', 'tensorflow', 'transformers', 'torch'])
        
        # Filter'ı root logger'a ekle (sadece henüz eklenmemişse)
        if not any(isinstance(f, LangExtractFilter) for f in root_logger.filters):
            root_logger.addFilter(LangExtractFilter())
    
    logging.info(f"🔇 LangExtract log seviyesi ayarlandı: {log_level}")

# LangExtract loglarını baştan kapat
suppress_langextract_logs()

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

def get_full_document_for_langextract_combined(combined_documents):
    """
    Combined chunk document'larını tek doküman olarak birleştir
    LangExtract'in tüm dokümanda global extraction yapmasını sağlar
    """
    full_text = ""
    all_chunk_ids = []
    
    logging.info(f"📄 Combined chunk'lar birleştiriliyor: {len(combined_documents)} combined chunk")
    
    for document in combined_documents:
        # Document text'ini al
        chunk_text = document.page_content if hasattr(document, 'page_content') else str(document)
        
        # Combined chunk ID'lerini al
        combined_chunk_ids = document.metadata.get('combined_chunk_ids', [])
        if not combined_chunk_ids:
            # Eğer combined_chunk_ids yoksa, chunk_id'yi kullan
            chunk_id = document.metadata.get('chunk_id', ['unknown'])
            if isinstance(chunk_id, list):
                combined_chunk_ids = chunk_id
            else:
                combined_chunk_ids = [chunk_id]
        
        full_text += chunk_text + "\n\n"
        all_chunk_ids.extend(combined_chunk_ids)
    
    logging.info(f"📄 Birleştirilmiş doküman uzunluğu: {len(full_text)} karakter")
    logging.info(f"📄 Toplam chunk ID sayısı: {len(all_chunk_ids)}")
    
    # Doküman istatistikleri
    word_count = len(full_text.split())
    char_count = len(full_text)
    
    logging.info(f"📊 Birleştirilmiş doküman:")
    logging.info(f"  📝 Toplam karakter: {char_count:,}")
    logging.info(f"  📝 Toplam kelime: {word_count:,}")
    logging.info(f"  📄 Combined chunk sayısı: {len(combined_documents)}")
    
    return {
        "text": full_text.strip(),
        "chunk_ids": all_chunk_ids,
        "chunk_count": len(combined_documents),
        "word_count": word_count,
        "char_count": char_count
    }

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
    combined_chunk_document_list: List[Document], 
    allowedNodes: str, 
    allowedRelationship: str, 
    file_name: str = None, 
    additional_instructions: str = None, 
    graph=None,
    max_pages: int = None
) -> List[Any]:
    """
    LangExtract kullanarak TÜM DOKÜMAN için tek seferde graph extraction yap
    Combined chunk'ları kullanarak global extraction yapar
    
    Args:
        model: Model adı (LangExtract için kullanılmaz ama uyumluluk için)
        combined_chunk_document_list: Combined chunk document listesi
        allowedNodes: İzin verilen node tipleri (comma separated)
        allowedRelationship: İzin verilen relationship'ler (comma separated triplets)
        file_name: Dosya adı
        additional_instructions: Ek talimatlar
        graph: Graph instance (entity resolution için)
        max_pages: Maksimum sayfa sayısı (sayfa sınırlandırma için)
        
    Returns:
        List[GraphDocument]: Graph dokümanları listesi
    """
    try:
        start_time = time.time()
        
        # LangExtract logları zaten baştan kapatıldı - suppress_langextract_logs() ile
        
        # Giriş parametrelerini logla
        logging.info("=== get_graph_from_langextract_full_document BAŞLADI ===")
        logging.info(f"🚀 FULL DOCUMENT EXTRACTION MODE")
        logging.info(f"Model: {model} (LangExtract kullanılacak)")
        logging.info(f"File name: {file_name}")
        logging.info(f"Max pages: {max_pages}")
        logging.info(f"Additional instructions var mı: {additional_instructions is not None}")
        logging.info(f"Toplam combined chunk sayısı: {len(combined_chunk_document_list)}")
        
        # Sayfa sınırlandırma - Eğer max_pages belirtilmişse sadece belirtilen sayfalardaki chunk'ları kullan
        filtered_documents = combined_chunk_document_list
        if max_pages is not None and max_pages > 0:
            logging.info(f"🔢 Sayfa sınırlandırma aktif: 1-{max_pages} arası sayfalar işlenecek")
            filtered_documents = []
            for document in combined_chunk_document_list:
                # Combined chunks için metadata'dan chunk_ids'i al
                combined_chunk_ids = document.metadata.get('combined_chunk_ids', [])
                if not combined_chunk_ids:
                    # Eğer combined_chunk_ids yoksa, bu single chunk olabilir
                    chunk_id = document.metadata.get('chunk_id', ['unknown'])
                    if isinstance(chunk_id, list):
                        combined_chunk_ids = chunk_id
                    else:
                        combined_chunk_ids = [chunk_id]
                
                # Bu combined chunk'ın hangi sayfalarda olduğunu kontrol et
                include_chunk = False
                chunk_pages = []
                has_valid_page_info = False
                
                # Document'ın page_number'ını kontrol et
                page_number = document.metadata.get('page_number')
                if page_number is not None:
                    try:
                        page_num = int(page_number)
                        chunk_pages.append(page_num)
                        has_valid_page_info = True
                        if 1 <= page_num <= max_pages:
                            include_chunk = True
                    except (ValueError, TypeError):
                        logging.warning(f"⚠️ Combined chunk için geçersiz page_number: {page_number}")
                
                # Eğer hiç sayfa bilgisi bulunamadıysa chunk'ı dahil et (backward compatibility)
                if not has_valid_page_info:
                    include_chunk = True
                    logging.warning(f"⚠️ Combined chunk {combined_chunk_ids} için page_number bulunamadı, dahil edildi")
                
                if include_chunk:
                    filtered_documents.append(document)
                    if chunk_pages:
                        logging.info(f"✅ Combined chunk {combined_chunk_ids} (sayfalar {chunk_pages}) dahil edildi")
                    else:
                        logging.info(f"✅ Combined chunk {combined_chunk_ids} (sayfa bilgisi yok) dahil edildi")
                else:
                    logging.info(f"❌ Combined chunk {combined_chunk_ids} (sayfalar {chunk_pages}) sayfa sınırı dışında, atlandı")
            
            logging.info(f"🔢 Sayfa filtrelemesi sonrası: {len(filtered_documents)} chunk kaldı")
        else:
            logging.info("🔢 Sayfa sınırlandırma yok, tüm chunk'lar işlenecek")
        
        # Raw giriş değerlerini logla
        logging.info(f"RAW allowedNodes: '{allowedNodes}'")
        logging.info(f"RAW allowedRelationship: '{allowedRelationship}'")
        
        # Tüm dokümanı birleştir (filtrelenmiş combined chunk'larla)
        full_document = get_full_document_for_langextract_combined(filtered_documents)
        
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
        
        # LangExtract extractor oluştur - önce logları tekrar bastır
        suppress_langextract_logs()  # Ekstra güvenlik için tekrar çağır
        extractor = LangExtractGraphExtractor()
        suppress_langextract_logs()  # Extractor oluşturduktan sonra da çağır
        
        # Tek seferde tüm doküman için extraction
        logging.info(f"🔄 Tüm doküman extraction başlıyor...")
        logging.info(f"📄 İşlenecek text uzunluğu: {full_document['char_count']:,} karakter")
        
        # LangExtract ile extraction
        result = await extractor.extract_graph(
            text=full_document["text"],
            allowed_nodes=allowed_nodes if allowed_nodes else None,
            allowed_relationships=allowed_relationships if allowed_relationships else None
        )
        
        # Extraction sonrası da logları bastır
        suppress_langextract_logs()
        
        # GraphExtractionResult'tan entities ve relationships al
        entities = result.entities
        relationships = result.relationships
        
        # LangExtract result debug logging
        logging.info(f"🔍 LangExtract RAW result inspection:")
        logging.info(f"  🧩 result type: {type(result)}")
        logging.info(f"  🧩 result.entities type: {type(entities)}")
        logging.info(f"  🧩 result.relationships type: {type(relationships)}")
        logging.info(f"  🧩 len(entities): {len(entities) if hasattr(entities, '__len__') else 'NO LEN'}")
        logging.info(f"  🧩 len(relationships): {len(relationships) if hasattr(relationships, '__len__') else 'NO LEN'}")
        
        if hasattr(entities, '__iter__') and len(entities) > 0:
            logging.info(f"  🧩 First entity: {entities[0]}")
            logging.info(f"  🧩 First entity type: {type(entities[0])}")
            if hasattr(entities[0], '__dict__'):
                logging.info(f"  🧩 First entity attrs: {entities[0].__dict__}")
        else:
            logging.info(f"  🧩 entities is empty or not iterable")
        
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
        
        # Debug: Gelen entity'leri logla
        logging.info(f"🔍 convert_langextract_to_graph_document başlıyor:")
        logging.info(f"  - Gelen entity sayısı: {len(entities) if hasattr(entities, '__len__') else 'UNKNOWN'}")
        logging.info(f"  - Gelen relationship sayısı: {len(relationships) if hasattr(relationships, '__len__') else 'UNKNOWN'}")
        logging.info(f"  - Chunk ID sayısı: {len(chunk_ids)}")
        logging.info(f"  - entities type: {type(entities)}")
        logging.info(f"  - relationships type: {type(relationships)}")
        
        # Debug: Entity'lerin içeriğini incele
        if hasattr(entities, '__iter__'):
            if len(entities) == 0:
                logging.info(f"  ⚠️ Hiç entity gelmedi!")
            else:
                logging.info(f"  ✅ {len(entities)} entity var, ilk birkaçını inceleyelim:")
                for i, entity in enumerate(entities[:3]):
                    logging.info(f"    Entity #{i+1}: {entity}")
                    logging.info(f"    Entity #{i+1} type: {type(entity)}")
                    if hasattr(entity, '__dict__'):
                        logging.info(f"    Entity #{i+1} attrs: {entity.__dict__}")
        else:
            logging.info(f"  ❌ entities iterable değil!")
        
        if entities:
            logging.info(f"  - İlk 5 entity: {[f'{e.id} ({e.label})' for e in entities[:5]]}")
        else:
            logging.warning(f"  ⚠️ Hiç entity gelmedi!")
        
        # Entity Resolution için SimpleEntityResolver kullan
        entity_id_mapping = {}
        
        # GEÇİCİ: Entity Resolution'ı devre dışı bırak debug için
        logging.info("⚠️ Entity Resolution DEVRE DIŞI - debug modu")
        # Graph yok ise, direkt mapping yap
        for entity in entities:
            entity_id_mapping[entity.id] = entity.id
        
        # ESKI KOD - Entity Resolution'ı tekrar açmak için
        # if graph:
        #     resolver = SimpleEntityResolver(similarity_threshold=0.6)  # Düşük threshold
        #     entity_list = [
        #         {
        #             'id': entity.id,
        #             'name': getattr(entity, 'name', entity.id),
        #             'entity_type': entity.label
        #         }
        #         for entity in entities
        #     ]
        #     
        #     logging.info(f"🔍 Entity Resolution öncesi entity_list: {len(entity_list)}")
        #     final_entities, entity_id_mapping = resolver.resolve_entities(graph, entity_list)
        #     logging.info(f"✅ Entity Resolution sonucu:")
        #     logging.info(f"  - Yeni node sayısı: {len(final_entities)}")
        #     logging.info(f"  - Mevcut entity kullanımı: {len(entity_list) - len(final_entities)}")
        #     logging.info(f"  - Entity mapping sample: {dict(list(entity_id_mapping.items())[:5])}")
        # else:
        #     logging.info("Graph objesi yok, Entity Resolution atlanıyor")
        #     # Graph yok ise, direkt mapping yap
        #     for entity in entities:
        #         entity_id_mapping[entity.id] = entity.id
        
        # Node'ları oluştur (sadece yeni entity'ler için)
        nodes = []
        logging.info(f"🔍 Node oluşturma başlıyor - entity sayısı: {len(entities)}")
        
        # Entity detaylarını tek tek logla
        for i, entity in enumerate(entities):
            logging.info(f"🔍 Entity #{i+1}: id='{entity.id}', label='{entity.label}', props={entity.properties}")
        
        for entity in entities:
            final_id = entity_id_mapping.get(entity.id, entity.id)
            
            logging.info(f"🔍 Entity işleniyor: '{entity.id}' -> '{final_id}' (label: {entity.label})")
            
            # Eğer entity yeniden kullanılıyorsa (mapping farklı ID'ye işaret ediyorsa), node yaratma
            if final_id != entity.id:
                logging.info(f"🔗 Entity yeniden kullanılıyor, node yaratılmıyor: '{entity.id}' -> '{final_id}'")
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
            logging.info(f"✅ Node oluşturuldu: {entity.id} ({entity.label})")
        
        logging.info(f"✅ Toplam oluşturulan node sayısı: {len(nodes)}")
        
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
        
        # Document oluştur - chunk ID'lerini metadata'ya ekle
        # Normal processing pipeline chunk-entity bağlantılarını oluşturacak
        document = Document(
            page_content=source_text,
            metadata={
                "chunk_ids": chunk_ids,  # LangExtract formatı
                "entity_mapping": entity_id_mapping
            }
        )
        
        # GraphDocument oluştur
        graph_document = GraphDocument(
            nodes=nodes,
            relationships=rels,
            source=document
        )
        
        # Final GraphDocument'ı detaylı logla
        logging.info(f"✅ GraphDocument oluşturuldu:")
        logging.info(f"  📄 Node sayısı: {len(nodes)}")
        logging.info(f"  🔗 Relationship sayısı: {len(rels)}")
        logging.info(f"  📝 Source metadata: {document.metadata}")
        
        # Node ve relationship detaylarını da logla (ilk 3'ünü)
        for i, node in enumerate(nodes[:3]):
            logging.info(f"    Node #{i+1}: id='{node.id}', type='{node.type}', props keys={list(node.properties.keys())}")
        for i, rel in enumerate(rels[:3]):
            logging.info(f"    Rel #{i+1}: {rel.source.id} -[{rel.type}]-> {rel.target.id}")
        
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
