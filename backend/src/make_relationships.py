from langchain_neo4j import Neo4jGraph
from langchain.docstore.document import Document
from src.shared.common_fn import load_embedding_model,execute_graph_query
from src.shared.common_fn import load_embedding_model,execute_graph_query
from src.utf8_utils import normalize_unicode_text, normalize_file_name
import logging
from typing import List
import os
import hashlib
import time
import re
from datetime import datetime
from langchain_neo4j import Neo4jVector
import json
from src.shared.constants import CHUNK_CONTINUATION_PROMPT
from src.llm import get_llm

logging.basicConfig(format='%(asctime)s - %(message)s',level='INFO')

EMBEDDING_MODEL = os.getenv('EMBEDDING_MODEL')
EMBEDDING_FUNCTION , EMBEDDING_DIMENSION = load_embedding_model(EMBEDDING_MODEL)

def merge_relationship_between_chunk_and_entites(graph: Neo4jGraph, graph_documents_chunk_chunk_Id : list):
    batch_data = []
    logging.info("Create HAS_ENTITY relationship between chunks and entities")
    
    for graph_doc_chunk_id in graph_documents_chunk_chunk_Id:
        for node in graph_doc_chunk_id['graph_doc'].nodes:
            query_data={
                'chunk_id': graph_doc_chunk_id['chunk_id'],
                'node_type': node.type,
                'node_id': node.id
            }
            batch_data.append(query_data)
          
    if batch_data:
        unwind_query = """
                    UNWIND $batch_data AS data
                    MATCH (c:Chunk {id: data.chunk_id})
                    CALL apoc.merge.node([data.node_type], {id: data.node_id}) YIELD node AS n
                    MERGE (c)-[:HAS_ENTITY]->(n)
                """
        execute_graph_query(graph,unwind_query, params={"batch_data": batch_data})

    
def create_chunk_embeddings(graph, chunkId_chunkDoc_list, file_name):
    isEmbedding = os.getenv('IS_EMBEDDING')
    
    embeddings, dimension = EMBEDDING_FUNCTION , EMBEDDING_DIMENSION
    logging.info(f'embedding model:{embeddings} and dimesion:{dimension}')
    data_for_query = []
    logging.info(f"update embedding and vector index for chunks")
    for row in chunkId_chunkDoc_list:
        if isEmbedding.upper() == "TRUE":
            try:
                # Document objesi'nden page_content'i al
                chunk_doc = row['chunk_doc']
                if hasattr(chunk_doc, 'page_content'):
                    content = chunk_doc.page_content
                else:
                    content = str(chunk_doc)
                
                # Dosya içeriğini normalize et
                from src.utf8_utils import normalize_unicode_text
                normalized_content = normalize_unicode_text(content)
                embeddings_arr = embeddings.embed_query(normalized_content)
                data_for_query.append({
                    "chunkId": row['chunk_id'],
                    "embeddings": embeddings_arr
                })
            except Exception as e:
                logging.error(f"Embedding creation failed for chunk {row['chunk_id']}: {e}")
                continue
    
    query_to_create_embedding = """
        UNWIND $data AS row
        MATCH (d:Document {fileName: $fileName})
        MERGE (c:Chunk {id: row.chunkId})
        SET c.embedding = row.embeddings
        MERGE (c)-[:PART_OF]->(d)
    """       
    execute_graph_query(graph,query_to_create_embedding, params={"fileName":file_name, "data":data_for_query})
    
def create_relation_between_chunks(graph, file_name, chunks: List[Document], page_images: List[str] = None)->list:
    logging.info("creating FIRST_CHUNK and NEXT_CHUNK relationships between chunks")
    
    # File name'i normalize et - Critical for consistency!
    file_name = normalize_file_name(file_name)
    logging.debug(f"Normalized file name for chunks: {file_name}")
    
    current_chunk_id = ""
    lst_chunks_including_hash = []
    batch_data = []
    relationships = []
    offset=0
    for i, chunk in enumerate(chunks):
        # UTF-8 ve Unicode normalization for consistent hashing
        content = normalize_unicode_text(chunk.page_content)
        
        # Chunk ID'yi dosya adı + içerik hash'i ile oluştur
        # Bu şekilde farklı dosyalardaki aynı içerikler farklı ID'lere sahip olur
        content_with_filename = f"{file_name}:::{content}"
        page_content_sha1 = hashlib.sha1(content_with_filename.encode('utf-8'))
        previous_chunk_id = current_chunk_id
        current_chunk_id = page_content_sha1.hexdigest()
        position = i + 1 
        if i>0:
            offset += len(chunks[i-1].page_content)
        if i == 0:
            firstChunk = True
        else:
            firstChunk = False  
        metadata = {"position": position,"length": len(chunk.page_content), "content_offset":offset}
        chunk_document = Document(
            page_content=content, metadata=metadata  # Normalized content kullan
        )
        
        chunk_data = {
            "id": current_chunk_id,
            "pg_content": chunk_document.page_content,
            "position": position,
            "length": chunk_document.metadata["length"],
            "f_name": file_name,  # Normalized file name kullan
            "previous_id" : previous_chunk_id,
            "content_offset" : offset
        }
        
        if 'page_number' in chunk.metadata:
            chunk_data['page_number'] = chunk.metadata['page_number']
            
            # Page link'i belirle (eğer page_images varsa)
            page_link = None
            if page_images and isinstance(page_images, list):
                page_number = chunk.metadata['page_number']
                # Page number'a göre uygun image dosya adını bul
                # page_images listesinde formatın şöyle olduğunu varsayıyoruz: 
                # "doc_name_page_001.png"
                for img_filename in page_images:
                    # Dosya adından page number'ı extract et
                    import re
                    match = re.search(r'_page_(\d+)\.png$', img_filename)
                    if match:
                        img_page_num = int(match.group(1))
                        if img_page_num == page_number:
                            page_link = img_filename  # Sadece dosya adı
                            break
            
            chunk_data['page_link'] = page_link
         
        if 'start_timestamp' in chunk.metadata and 'end_timestamp' in chunk.metadata:
            chunk_data['start_time'] = chunk.metadata['start_timestamp']
            chunk_data['end_time'] = chunk.metadata['end_timestamp'] 
               
        batch_data.append(chunk_data)
        
        lst_chunks_including_hash.append({'chunk_id': current_chunk_id, 'chunk_doc': chunk})
        
        # create relationships between chunks
        if firstChunk:
            relationships.append({"type": "FIRST_CHUNK", "chunk_id": current_chunk_id})
        else:
            relationships.append({
                "type": "NEXT_CHUNK",
                "previous_chunk_id": previous_chunk_id,  # ID of previous chunk
                "current_chunk_id": current_chunk_id
            })
          
    query_to_create_chunk_and_PART_OF_relation = """
        UNWIND $batch_data AS data
        MERGE (c:Chunk {id: data.id})
        SET c.text = data.pg_content, 
            c.chunkId = data.id,
            c.position = data.position, 
            c.length = data.length, 
            c.fileName = data.f_name, 
            c.content_offset = data.content_offset
        WITH data, c
        SET c.page_number = CASE WHEN data.page_number IS NOT NULL THEN data.page_number END,
            c.start_time = CASE WHEN data.start_time IS NOT NULL THEN data.start_time END,
            c.end_time = CASE WHEN data.end_time IS NOT NULL THEN data.end_time END,
            c.page_link = CASE WHEN data.page_link IS NOT NULL THEN data.page_link END
        WITH data, c
        MATCH (d:Document {fileName: data.f_name})
        MERGE (c)-[:PART_OF]->(d)
    """
    execute_graph_query(graph,query_to_create_chunk_and_PART_OF_relation, params={"batch_data": batch_data})
    
    query_to_create_FIRST_relation = """ 
        UNWIND $relationships AS relationship
        MATCH (d:Document {fileName: $f_name})
        MATCH (c:Chunk {id: relationship.chunk_id})
        FOREACH(r IN CASE WHEN relationship.type = 'FIRST_CHUNK' THEN [1] ELSE [] END |
                MERGE (d)-[:FIRST_CHUNK]->(c))
        """
    execute_graph_query(graph,query_to_create_FIRST_relation, params={"f_name": file_name, "relationships": relationships})
    
    query_to_create_NEXT_CHUNK_relation = """ 
        UNWIND $relationships AS relationship
        MATCH (c:Chunk {id: relationship.current_chunk_id})
        WITH c, relationship
        MATCH (pc:Chunk {id: relationship.previous_chunk_id})
        FOREACH(r IN CASE WHEN relationship.type = 'NEXT_CHUNK' THEN [1] ELSE [] END |
                MERGE (c)<-[:NEXT_CHUNK]-(pc))
        """  
    execute_graph_query(graph,query_to_create_NEXT_CHUNK_relation, params={"relationships": relationships})
    return lst_chunks_including_hash


def create_chunk_vector_index(graph):
    start_time = time.time()
    try:
        vector_index_query = "SHOW INDEXES YIELD name, type, labelsOrTypes, properties WHERE name = 'vector' AND type = 'VECTOR' AND 'Chunk' IN labelsOrTypes AND 'embedding' IN properties RETURN name"
        vector_index = execute_graph_query(graph,vector_index_query)
        if not vector_index:
            vector_store = Neo4jVector(embedding=EMBEDDING_FUNCTION,
                                    graph=graph,
                                    node_label="Chunk", 
                                    embedding_node_property="embedding",
                                    index_name="vector",
                                    embedding_dimension=EMBEDDING_DIMENSION
                                    )
            vector_store.create_new_index()
            logging.info(f"Index created successfully. Time taken: {time.time() - start_time:.2f} seconds")
        else:
            logging.info(f"Index already exist,Skipping creation. Time taken: {time.time() - start_time:.2f} seconds")
    except Exception as e:
        if ("EquivalentSchemaRuleAlreadyExists" in str(e) or "An equivalent index already exists" in str(e)):
            logging.info("Vector index already exists, skipping creation.")
        else:
            raise

def create_entity_vector_index(graph: Neo4jGraph):
    """
    Create a vector index for entity nodes to enable semantic search.
    """
    start_time = time.time()
    try:
        vector_index_query = "SHOW INDEXES YIELD name, type, labelsOrTypes, properties WHERE name = 'entity_vector' AND type = 'VECTOR' AND '__Entity__' IN labelsOrTypes AND 'embedding' IN properties RETURN name"
        vector_index = execute_graph_query(graph, vector_index_query)
        if not vector_index:
            vector_store = Neo4jVector(embedding=EMBEDDING_FUNCTION,
                                    graph=graph,
                                    node_label="__Entity__", 
                                    embedding_node_property="embedding",
                                    index_name="entity_vector",
                                    embedding_dimension=EMBEDDING_DIMENSION
                                    )
            vector_store.create_new_index()
            logging.info(f"Entity vector index created successfully. Time taken: {time.time() - start_time:.2f} seconds")
        else:
            logging.info(f"Entity vector index already exists. Time taken: {time.time() - start_time:.2f} seconds")
    except Exception as e:
        if ("EquivalentSchemaRuleAlreadyExists" in str(e) or "An equivalent index already exists" in str(e)):
            logging.info("Entity vector index already exists, skipping creation.")
        else:
            raise

def create_document_metadata_entities(graph: Neo4jGraph, file_name: str):
    """
    Create entity nodes for document metadata: year, owner, document name, page count, document type
    """
    logging.info(f"Creating document metadata entities for {file_name}")
    
    # Query to get document metadata
    query = """
    MATCH (d:Document {fileName: $fileName})
    RETURN d.fileName as fileName, 
           d.fileType as fileType, 
           d.fileSize as fileSize, 
           d.createdAt as createdAt
    """
    
    result = execute_graph_query(graph, query, params={"fileName": file_name})
    if not result:
        logging.warning(f"Document {file_name} not found or has no metadata")
        return
    
    doc_data = result[0]
    year = None
    if doc_data.get("createdAt"):
        try:
            year = str(datetime.fromisoformat(doc_data.get("createdAt")).year)
        except:
            # Extract year from filename if possible
            year_match = re.search(r'(19|20)\d{2}', file_name)
            if year_match:
                year = year_match.group(0)
    
    # Extract owner from filename
    owner = None
    name_match = re.search(r'^([A-Za-zÀ-ÖØ-öø-ÿ\s]+)', file_name)
    if name_match:
        owner = name_match.group(1).strip()
    
    # Document name is the filename
    doc_name = file_name
    
    # File type from metadata
    doc_type = doc_data.get("fileType", "unknown")
    
    # Get actual page count from chunks' page_number metadata
    page_count_query = """
    MATCH (d:Document {fileName: $fileName})<-[:PART_OF]-(c:Chunk)
    WHERE c.page_number IS NOT NULL
    RETURN max(c.page_number) as maxPageNumber, count(DISTINCT c.page_number) as distinctPages, count(c) as totalChunks
    """
    page_count_result = execute_graph_query(graph, page_count_query, params={"fileName": file_name})
    
    if page_count_result and page_count_result[0]:
        result_data = page_count_result[0]
        # Önce maksimum sayfa numarasını kullan, yoksa farklı sayfa sayısını, son çare chunk sayısı
        if result_data["maxPageNumber"] is not None:
            page_count = str(result_data["maxPageNumber"])
        elif result_data["distinctPages"] is not None and result_data["distinctPages"] > 0:
            page_count = str(result_data["distinctPages"])
        else:
            page_count = str(result_data["totalChunks"])
    else:
        page_count = "0"
    
    # Create entity nodes and relationships to document
    entities = []
    
    if year:
        entities.append({"type": "Year", "id": year, "property": "year"})
    if owner:
        entities.append({"type": "Owner", "id": owner, "property": "owner"})
    if doc_name:
        entities.append({"type": "DocumentName", "id": doc_name, "property": "documentName"})
    if page_count:
        entities.append({"type": "PageCount", "id": page_count, "property": "pageCount"})
    if doc_type:
        entities.append({"type": "DocumentType", "id": doc_type, "property": "documentType"})
    
    # Create entities and relationships in batch
    if entities:
        entity_query = """
        MATCH (d:Document {fileName: $fileName})
        UNWIND $entities as entity
        
        // Güvenli node oluşturma - mevcut node'u bul veya yenisini oluştur
        MERGE (node:__Entity__ {id: entity.id})
        ON CREATE SET node.entity_type = entity.type
        ON MATCH SET node.entity_type = COALESCE(node.entity_type, entity.type)
        
        SET d[entity.property] = entity.id
        MERGE (d)-[:HAS_METADATA]->(node)
        RETURN node
        """
        execute_graph_query(graph, entity_query, params={"fileName": file_name, "entities": entities})
        
        # Document metadata entities'leri tüm chunk'lara da bağla
        chunk_entity_query = """
        MATCH (d:Document {fileName: $fileName})<-[:PART_OF]-(c:Chunk)
        MATCH (d)-[:HAS_METADATA]->(meta:__Entity__)
        MERGE (c)-[:HAS_ENTITY]->(meta)
        """
        execute_graph_query(graph, chunk_entity_query, params={"fileName": file_name})
        
        # Create embeddings for the new entity nodes
        for entity in entities:
            from src.utf8_utils import normalize_unicode_text
            normalized_entity_id = normalize_unicode_text(entity["id"])
            embedding = EMBEDDING_FUNCTION.embed_query(normalized_entity_id)
            embedding_query = """
            MATCH (e:__Entity__ {id: $id})
            SET e.embedding = $embedding
            """
            execute_graph_query(graph, embedding_query, params={"id": entity["id"], "embedding": embedding})

def create_cross_chunk_relations(graph: Neo4jGraph, file_name: str, similarity_threshold: float = None):
    """
    Create cross-chunk relationships based on vector similarity using Neo4j vector index.
    """
    # fallback to environment threshold if not provided
    if similarity_threshold is None:
        similarity_threshold = float(os.getenv('KNN_MIN_SCORE', '0.7'))
    query = """
    // Only consider chunks that have embeddings
    MATCH (c:Chunk {fileName: $fileName})
    WHERE c.embedding IS NOT NULL
    CALL db.index.vector.queryNodes('vector', 5, c.embedding) YIELD node AS other, score
    WHERE other <> c AND score >= $threshold AND id(c) < id(other)
    // Tek yönlü ilişki kurma (duplikasyon önlemek için ID karşılaştırması)
    MERGE (c)-[r:SIMILAR]->(other)
    SET r.score = score
    """
    execute_graph_query(graph, query, params={"fileName": file_name, "threshold": similarity_threshold})
    
    # Create entity vector index if it doesn't exist
    create_entity_vector_index(graph)

def create_document_relationships(graph: Neo4jGraph, target_document: str = None) -> dict:
    """
    Person node'larını HAS_POLICY ilişkisiyle document'lara bağlar
    Args:
        target_document: Eğer belirtilirse, sadece bu document için ilişkiler kurar
    Returns: {'person_policy_connections': int, 'policy_connections': int, 'company_connections': int}
    """
    if target_document:
        logging.info(f"Creating person-document relationships for specific document: {target_document}")
    else:
        logging.info("Creating PERSON-HAS_POLICY-Document relationships")
    
    results = {}
    
    # 1. Person node'larını document'lara HAS_POLICY ile bağla
    person_policy_query = """
    // Her kişinin hangi dokümanlarda geçtiğini bul
    MATCH (person:Person)<-[:HAS_ENTITY]-(c:Chunk)-[:PART_OF]->(d:Document)
    """ + (f" WHERE d.fileName = $target_document" if target_document else "") + """
    
    // Kişi ile doküman arasında HAS_POLICY ilişkisi kur
    WITH person, d, count(DISTINCT c) AS chunk_count
    WHERE chunk_count >= 1
    
    MERGE (person)-[r:HAS_POLICY]->(d)
    ON CREATE SET 
        r.chunk_count = chunk_count,
        r.created_at = datetime(),
        r.confidence = CASE 
            WHEN chunk_count >= 5 THEN 'HIGH'
            WHEN chunk_count >= 2 THEN 'MEDIUM'
            ELSE 'LOW'
        END
    ON MATCH SET 
        r.chunk_count = chunk_count,
        r.updated_at = datetime()
    
    RETURN count(DISTINCT r) AS connections_created
    """
    
    try:
        params = {"target_document": target_document} if target_document else {}
        result = execute_graph_query(graph, person_policy_query, params=params)
        results['person_policy_connections'] = result[0]['connections_created'] if result else 0
        logging.info(f"Created {results['person_policy_connections']} PERSON-HAS_POLICY-Document connections")
    except Exception as e:
        logging.error(f"Error creating person-policy connections: {e}")
        results['person_policy_connections'] = 0
    
    # 2. Poliçe türü bağlantısı iptal edildi - SAME_POLICY_TYPE relationship kaldırıldı
    results['policy_connections'] = 0
    
    # 3. Aynı şirkete ait document'ları birbirine bağla
    company_query = """
    // Company entity'lere göre bağla
    MATCH (company)<-[:HAS_ENTITY]-(c1:Chunk)-[:PART_OF]->(d1:Document)
    MATCH (company)<-[:HAS_ENTITY]-(c2:Chunk)-[:PART_OF]->(d2:Document)
    WHERE d1 <> d2 
    AND (company:Company OR company.id =~ '(?i).*(sigorta|insurance|axa|allianz|mapfre).*')
    """ + (f" AND (d1.fileName = $target_document OR d2.fileName = $target_document)" if target_document else "") + """
    
    WITH d1, d2, company, count(*) AS shared_chunks
    WHERE shared_chunks >= 1
    
    MERGE (d1)-[r:SAME_INSURANCE_COMPANY]->(d2)
    ON CREATE SET 
        r.company_name = company.id,
        r.shared_chunks = shared_chunks,
        r.created_at = datetime()
    ON MATCH SET 
        r.shared_chunks = shared_chunks,
        r.updated_at = datetime()
    
    RETURN count(DISTINCT r) AS connections_created
    """
    
    try:
        params = {"target_document": target_document} if target_document else {}
        result = execute_graph_query(graph, company_query, params=params)
        results['company_connections'] = result[0]['connections_created'] if result else 0
        logging.info(f"Created {results['company_connections']} company-based document connections")
    except Exception as e:
        logging.error(f"Error creating company-based document connections: {e}")
        results['company_connections'] = 0
    
    # 4. Document metadata'ya göre de bağlayalım (aynı yıl, aynı owner)
    metadata_query = """
    // Aynı owner'a ait document'ları bağla
    MATCH (d1:Document), (d2:Document)
    WHERE d1 <> d2 
    AND d1.owner IS NOT NULL 
    AND d1.owner = d2.owner
    """ + (f" AND (d1.fileName = $target_document OR d2.fileName = $target_document)" if target_document else "") + """
    
    MERGE (d1)-[r:SAME_OWNER]->(d2)
    ON CREATE SET 
        r.owner_name = d1.owner,
        r.created_at = datetime()
    ON MATCH SET 
        r.updated_at = datetime()
    
    RETURN count(DISTINCT r) AS connections_created
    """
    
    try:
        params = {"target_document": target_document} if target_document else {}
        result = execute_graph_query(graph, metadata_query, params=params)
        results['metadata_connections'] = result[0]['connections_created'] if result else 0
        logging.info(f"Created {results['metadata_connections']} metadata-based document connections")
    except Exception as e:
        logging.error(f"Error creating metadata-based document connections: {e}")
        results['metadata_connections'] = 0
    
    total_connections = sum(results.values())
    logging.info(f"Total document connections created: {total_connections}")
    
    return results


def create_chunks_for_upload(graph, chunks, file_name, page_images=None):
    """
    Upload aşamasında chunk node'ları oluştur (extract'a uyumlu yapı)
    
    Args:
        graph: Neo4j graph connection
        chunks: List of langchain Document objects
        file_name: İşlenen dosya adı
        page_images: Sayfa resim dosyalarının listesi (dosya adları)
    
    Returns:
        List of created chunk IDs with chunk documents (extract format)
    """
    logging.info(f"🔄 Creating {len(chunks)} chunk nodes for upload (extract-compatible structure)")
    
    # Extract'daki content normalizasyon fonksiyonunu kullan
    from src.utf8_utils import normalize_unicode_text
    
    batch_data = []
    relationships = []
    lst_chunks_including_hash = []  # Extract format için
    previous_chunk_id = None
    offset = 0
    
    for i, chunk in enumerate(chunks):
        # Extract'daki gibi content normalizasyon
        content = chunk.page_content.strip()
        content = normalize_unicode_text(content)
        
        # Extract'daki gibi SHA1 hash ID oluştur (filename ile kombine)
        content_with_filename = f"{file_name}:::{content}"
        page_content_sha1 = hashlib.sha1(content_with_filename.encode('utf-8'))
        current_chunk_id = page_content_sha1.hexdigest()
        
        position = i + 1
        if i > 0:
            offset += len(chunks[i-1].page_content)
        
        firstChunk = (i == 0)
        
        # Extract'daki gibi metadata yapısı
        metadata = {"position": position, "length": len(chunk.page_content), "content_offset": offset}
        chunk_document = Document(
            page_content=content, metadata=metadata
        )
        
        # Chunk verilerini hazırla (extract format)
        chunk_data = {
            "id": current_chunk_id,
            "pg_content": chunk_document.page_content,
            "position": position,
            "length": chunk_document.metadata["length"],
            "f_name": file_name,
            "previous_id": previous_chunk_id,
            "content_offset": offset
        }
        
                
        # Page number ve page_link ekle (extract'daki gibi)
        if 'page_number' in chunk.metadata:
            chunk_data['page_number'] = chunk.metadata['page_number']
            
            # Page link'i belirle (eğer page_images varsa)
            page_link = None
            if page_images and isinstance(page_images, list):
                page_number = chunk.metadata['page_number']
                # Page number'a göre uygun image dosya adını bul
                for img_filename in page_images:
                    # Dosya adından page number'ı extract et
                    import re
                    match = re.search(r'_page_(\d+)\.png$', img_filename)
                    if match:
                        img_page_num = int(match.group(1))
                        if img_page_num == page_number:
                            page_link = img_filename  # Sadece dosya adı
                            break
            
            chunk_data['page_link'] = page_link
         
        # Timestamp bilgileri (video dosyaları için)
        if 'start_timestamp' in chunk.metadata and 'end_timestamp' in chunk.metadata:
            chunk_data['start_time'] = chunk.metadata['start_timestamp']
            chunk_data['end_time'] = chunk.metadata['end_timestamp'] 
               
        batch_data.append(chunk_data)
        
        # Extract format için chunk list oluştur
        lst_chunks_including_hash.append({'chunk_id': current_chunk_id, 'chunk_doc': chunk_document})
        
        # Chunk relationship'leri hazırla (extract'daki gibi)
        if firstChunk:
            relationships.append({"type": "FIRST_CHUNK", "chunk_id": current_chunk_id})
        else:
            relationships.append({
                "type": "NEXT_CHUNK",
                "previous_chunk_id": previous_chunk_id,
                "current_chunk_id": current_chunk_id
            })
        
        previous_chunk_id = current_chunk_id
    
    # Chunk node'ları ve PART_OF ilişkilerini oluştur (extract'daki gibi)
    logging.info(f"🔄 Creating chunk nodes and PART_OF relationships for {len(batch_data)} chunks")
    query_to_create_chunk_and_PART_OF_relation = """
        UNWIND $batch_data AS data
        MERGE (c:Chunk {id: data.id})
        SET c.text = data.pg_content, 
            c.chunkId = data.id,
            c.position = data.position, 
            c.length = data.length, 
            c.fileName = data.f_name, 
            c.content_offset = data.content_offset
        WITH data, c
        SET c.page_number = CASE WHEN data.page_number IS NOT NULL THEN data.page_number END,
            c.start_time = CASE WHEN data.start_time IS NOT NULL THEN data.start_time END,
            c.end_time = CASE WHEN data.end_time IS NOT NULL THEN data.end_time END,
            c.page_link = CASE WHEN data.page_link IS NOT NULL THEN data.page_link END
        WITH data, c
        // Document node'u bulamazsa chunk'ı yine de oluştur, ilişki daha sonra kurulacak
        OPTIONAL MATCH (d:Document {fileName: data.f_name})
        FOREACH (_ IN CASE WHEN d IS NOT NULL THEN [1] ELSE [] END |
            MERGE (c)-[:PART_OF]->(d)
        )
    """
    execute_graph_query(graph, query_to_create_chunk_and_PART_OF_relation, params={"batch_data": batch_data})
    
    # FIRST_CHUNK ilişkilerini oluştur (extract'daki gibi)
    first_relationships = [r for r in relationships if r["type"] == "FIRST_CHUNK"]
    logging.info(f"🔄 Creating FIRST_CHUNK relationships for {len(first_relationships)} chunks")
    query_to_create_FIRST_relation = """ 
        UNWIND $relationships AS relationship
        OPTIONAL MATCH (d:Document {fileName: $f_name})
        MATCH (c:Chunk {id: relationship.chunk_id})
        FOREACH (_ IN CASE WHEN relationship.type = 'FIRST_CHUNK' AND d IS NOT NULL THEN [1] ELSE [] END |
                MERGE (d)-[:FIRST_CHUNK]->(c))
        """
    execute_graph_query(graph, query_to_create_FIRST_relation, params={"f_name": file_name, "relationships": relationships})
    
    # Debug: FIRST_CHUNK ilişkilerini kontrol et
    first_check_query = "MATCH (d:Document {fileName: $file_name})-[:FIRST_CHUNK]->(c:Chunk) RETURN count(*) as first_count"
    first_check_result = execute_graph_query(graph, first_check_query, params={"file_name": file_name})
    logging.info(f"🔍 DEBUG - FIRST_CHUNK relationships after creation: {first_check_result[0]['first_count'] if first_check_result else 0}")
    
    # NEXT_CHUNK ilişkilerini oluştur (extract'daki gibi)
    next_relationships = [r for r in relationships if r["type"] == "NEXT_CHUNK"]
    logging.info(f"🔄 Creating NEXT_CHUNK relationships for {len(next_relationships)} chunk pairs")
    query_to_create_NEXT_relation = """
        UNWIND $relationships AS relationship
        MATCH (c1:Chunk {id: relationship.previous_chunk_id})
        MATCH (c2:Chunk {id: relationship.current_chunk_id})
        WHERE relationship.type = 'NEXT_CHUNK'
        MERGE (c1)-[:NEXT_CHUNK]->(c2)
        RETURN count(*) as created_count
    """
    next_result = execute_graph_query(graph, query_to_create_NEXT_relation, params={"relationships": relationships})
    logging.info(f"✅ Created {next_result[0]['created_count'] if next_result else 0} NEXT_CHUNK relationships")
    
    logging.info(f"✅ Created {len(lst_chunks_including_hash)} chunk nodes and relationships for: {file_name}")
    return lst_chunks_including_hash  # Extract format: chunk_id ve chunk_doc içeren list


def link_chunks_to_document(graph, file_name):
    """
    Upload sonrası chunk'ları Document node'una bağla (eğer henüz bağlanmadıysa)
    """
    logging.info(f"🔗 Linking chunks to Document node for: {file_name}")
    
    # Chunk'ları Document'e bağla
    link_chunks_query = """
    MATCH (c:Chunk {fileName: $file_name})
    MATCH (d:Document {fileName: $file_name})
    WHERE NOT (c)-[:PART_OF]->(d)
    MERGE (c)-[:PART_OF]->(d)
    RETURN count(*) as linked_count
    """
    result = execute_graph_query(graph, link_chunks_query, params={"file_name": file_name})
    linked_count = result[0]['linked_count'] if result else 0
    logging.info(f"🔗 Linked {linked_count} chunks to Document with PART_OF relationship")
    
    # FIRST_CHUNK ilişkisini kur
    first_chunk_query = """
    MATCH (d:Document {fileName: $file_name})
    MATCH (c:Chunk {fileName: $file_name})
    WHERE c.position = 1 AND NOT (d)-[:FIRST_CHUNK]->(c)
    MERGE (d)-[:FIRST_CHUNK]->(c)
    RETURN count(*) as first_linked_count
    """
    first_result = execute_graph_query(graph, first_chunk_query, params={"file_name": file_name})
    first_linked_count = first_result[0]['first_linked_count'] if first_result else 0
    logging.info(f"🔗 Linked {first_linked_count} first chunk to Document with FIRST_CHUNK relationship")
    
    return linked_count + first_linked_count