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
    logging.info("Create business relationships and EXTRACTED_FROM between chunks and entities")
    
    for graph_doc_chunk_id in graph_documents_chunk_chunk_Id:
        for node in graph_doc_chunk_id['graph_doc'].nodes:
            query_data={
                'chunk_id': graph_doc_chunk_id['chunk_id'],
                'node_type': node.type,
                'node_id': node.id
            }
            batch_data.append(query_data)
          
    if batch_data:
        logging.info(f"📋 Creating {len(batch_data)} entity nodes with EXTRACTED_FROM relationships")
        
        # Sadece entity'leri oluştur ve chunk'a bağla - business logic gereksiz
        simple_query = """
        UNWIND $batch_data AS data
        MATCH (c:Chunk {id: data.chunk_id})-[:PART_OF]->(d:Document)
        
        // Entity node'unu güvenli şekilde oluştur veya bul
        CALL apoc.merge.node(['__Entity__'] + [data.node_type], {id: data.node_id}) YIELD node AS entity
        
        // EXTRACTED_FROM ilişkisini kur (tek gerekli ilişki)
        MERGE (entity)-[:EXTRACTED_FROM]->(c)
        
        RETURN count(DISTINCT entity) as entities_created
        """
        
        result = execute_graph_query(graph, simple_query, params={"batch_data": batch_data})
        
        if result:
            entities_created = result[0].get('entities_created', 0)
            logging.info(f"✅ Created {entities_created} entities with EXTRACTED_FROM relationships")
        else:
            logging.info("✅ Entities created with EXTRACTED_FROM relationships")
        
        # Business Entity Linking: PolicyType, InsuredItem entity'lerini business node'lara bağla
        logging.info("🔗 Linking business entities to their respective business nodes...")
        
        business_linking_query = """
        UNWIND $batch_data AS data
        MATCH (c:Chunk {id: data.chunk_id})-[:PART_OF]->(d:Document)
        MATCH (entity:__Entity__ {id: data.node_id})-[:EXTRACTED_FROM]->(c)
        
        // PolicyType entity'lerini Policy node'una bağla
        FOREACH (_ IN CASE WHEN data.node_type = 'PolicyType' THEN [1] ELSE [] END |
            MERGE (policy:Policy)-[:DOCUMENTED_IN]->(d)
            MERGE (policy)-[:HAS_TYPE]->(entity)
        )
        
        // InsuredItem entity'lerini Policy node'una bağla
        FOREACH (_ IN CASE WHEN data.node_type = 'InsuredItem' THEN [1] ELSE [] END |
            MERGE (policy:Policy)-[:DOCUMENTED_IN]->(d)
            MERGE (policy)-[:HAS_INSURED_ITEM]->(entity)
        )
        
        RETURN count(*) as processed
        """
        
        business_result = execute_graph_query(graph, business_linking_query, params={"batch_data": batch_data})
        
        if business_result:
            logging.info(f"🔗 Processed business entity linking for {business_result[0]['processed']} entities")
            
            # PolicyYear ve Customer işlemlerini ayrı query'lerde yap
            # PolicyYear entity'lerini kontrol et ve Policy'ye bağla
            policy_year_query = """
            UNWIND $batch_data AS data
            MATCH (c:Chunk {id: data.chunk_id})-[:PART_OF]->(d:Document)
            MATCH (entity:__Entity__ {id: data.node_id})-[:EXTRACTED_FROM]->(c)
            WHERE data.node_type = 'PolicyYear' AND data.node_id =~ '^(19|20)\\\\d{2}$'
            
            MERGE (policyYear:PolicyYear {year: toInteger(data.node_id), name: data.node_id})
            MERGE (policy:Policy)-[:DOCUMENTED_IN]->(d)
            MERGE (policy)-[:HAS_YEAR]->(policyYear)
            
            RETURN count(*) as policy_year_processed
            """
            
            policy_year_result = execute_graph_query(graph, policy_year_query, params={"batch_data": batch_data})
            if policy_year_result:
                logging.info(f"🔗 Processed {policy_year_result[0]['policy_year_processed']} PolicyYear entities")
            
            # Customer entity'lerini kontrol et ve Customer node'u oluştur
            customer_query = """
            UNWIND $batch_data AS data
            MATCH (c:Chunk {id: data.chunk_id})-[:PART_OF]->(d:Document)
            MATCH (entity:__Entity__ {id: data.node_id})-[:EXTRACTED_FROM]->(c)
            WHERE data.node_type = 'Customer'
            
            MERGE (customer:Customer {name: data.node_id, fullName: data.node_id})
            MERGE (customer)-[:HAS_DOC]->(d)
            
            RETURN count(*) as customer_processed
            """
            
            customer_result = execute_graph_query(graph, customer_query, params={"batch_data": batch_data})
            if customer_result:
                logging.info(f"🔗 Processed {customer_result[0]['customer_processed']} Customer entities")
        
        if business_result:
            logging.info(f"🔗 Processed business entity linking for {business_result[0]['processed']} entities")
            
            # Bağlantı sayılarını kontrol et
            check_query = """
            UNWIND $batch_data AS data
            MATCH (entity:__Entity__ {id: data.node_id})
            
            OPTIONAL MATCH (policy:Policy)-[:HAS_TYPE]->(entity)
            WHERE data.node_type = 'PolicyType'
            
            OPTIONAL MATCH (policy2:Policy)-[:HAS_INSURED_ITEM]->(entity)
            WHERE data.node_type = 'InsuredItem'
            
            OPTIONAL MATCH (policy3:Policy)-[:HAS_YEAR]->(py:PolicyYear)
            WHERE data.node_type = 'PolicyYear' AND py.name = data.node_id
            
            OPTIONAL MATCH (customer:Customer)-[:HAS_DOC]->(d:Document)
            WHERE data.node_type = 'Customer' AND customer.name = data.node_id
            
            RETURN 
                data.node_type as entity_type,
                data.node_id as entity_id,
                count(DISTINCT policy) as policy_type_links,
                count(DISTINCT policy2) as insured_item_links,
                count(DISTINCT policy3) as policy_year_links,
                count(DISTINCT customer) as customer_links
            """
            
            check_result = execute_graph_query(graph, check_query, params={"batch_data": batch_data})
            
            if check_result:
                for result in check_result:
                    entity_type = result['entity_type']
                    entity_id = result['entity_id']
                    
                    if entity_type == 'PolicyType' and result['policy_type_links'] > 0:
                        logging.info(f"  ✅ PolicyType '{entity_id}' linked to {result['policy_type_links']} Policy node(s)")
                    elif entity_type == 'InsuredItem' and result['insured_item_links'] > 0:
                        logging.info(f"  ✅ InsuredItem '{entity_id}' linked to {result['insured_item_links']} Policy node(s)")
                    elif entity_type == 'PolicyYear' and result['policy_year_links'] > 0:
                        logging.info(f"  ✅ PolicyYear '{entity_id}' linked to {result['policy_year_links']} Policy node(s)")
                    elif entity_type == 'Customer' and result['customer_links'] > 0:
                        logging.info(f"  ✅ Customer '{entity_id}' linked to {result['customer_links']} Document(s)")
    else:
        logging.info("ℹ️ No entities to create")

    
def create_chunk_embeddings(graph, chunkId_chunkDoc_list, file_name):
    isEmbedding = os.getenv('IS_EMBEDDING')
    
    if isEmbedding.upper() != "TRUE":
        logging.info("Embedding creation disabled (IS_EMBEDDING != TRUE)")
        return
    
    embeddings, dimension = EMBEDDING_FUNCTION , EMBEDDING_DIMENSION
    logging.info(f'embedding model:{embeddings} and dimesion:{dimension}')
    
    # İlk önce hangi chunk'larda embedding eksik olduğunu kontrol et
    chunk_ids = [row['chunk_id'] for row in chunkId_chunkDoc_list]
    
    missing_embeddings_query = """
        UNWIND $chunk_ids AS chunkId
        MATCH (c:Chunk {id: chunkId})
        WHERE c.embedding IS NULL OR size(c.embedding) = 0
        RETURN c.id as chunk_id
    """
    
    missing_chunks_result = execute_graph_query(graph, missing_embeddings_query, params={"chunk_ids": chunk_ids})
    missing_chunk_ids = [row['chunk_id'] for row in missing_chunks_result]
    
    logging.info(f"📊 Embedding kontrolü:")
    logging.info(f"  - Toplam chunk sayısı: {len(chunk_ids)}")
    logging.info(f"  - Embedding eksik chunk sayısı: {len(missing_chunk_ids)}")
    logging.info(f"  - Embedding mevcut chunk sayısı: {len(chunk_ids) - len(missing_chunk_ids)}")
    
    if not missing_chunk_ids:
        logging.info("✅ Tüm chunk'larda embedding mevcut, yeniden oluşturma gerekmiyor")
        return
    
    # Sadece embedding eksik olan chunk'lar için embedding oluştur
    data_for_query = []
    logging.info(f"🔄 {len(missing_chunk_ids)} chunk için embedding oluşturuluyor...")
    
    for row in chunkId_chunkDoc_list:
        if row['chunk_id'] in missing_chunk_ids:
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
                logging.debug(f"✅ Embedding oluşturuldu: {row['chunk_id']}")
            except Exception as e:
                logging.error(f"❌ Embedding creation failed for chunk {row['chunk_id']}: {e}")
                continue
    
    if data_for_query:
        # Sadece embedding eksik olan chunk'ları güncelle
        query_to_create_embedding = """
            UNWIND $data AS row
            MATCH (c:Chunk {id: row.chunkId})
            SET c.embedding = row.embeddings
            WITH c, row
            OPTIONAL MATCH (d:Document {fileName: $fileName})
            FOREACH (_ IN CASE WHEN d IS NOT NULL THEN [1] ELSE [] END |
                MERGE (c)-[:PART_OF]->(d)
            )
            RETURN count(c) as updated_count
        """       
        result = execute_graph_query(graph, query_to_create_embedding, params={"fileName": file_name, "data": data_for_query})
        updated_count = result[0]['updated_count'] if result else len(data_for_query)
        logging.info(f"✅ {updated_count} chunk için embedding başarıyla oluşturuldu")
    else:
        logging.warning("⚠️ Hiç embedding oluşturulamadı")

def create_chunk_embeddings_immediate(graph, chunkId_chunkDoc_list, file_name):
    """
    Upload aşamasında chunk'lar oluşturulduktan hemen sonra embedding'leri oluştur
    """
    isEmbedding = os.getenv('IS_EMBEDDING')
    
    if isEmbedding and isEmbedding.upper() != "TRUE":
        logging.info("Embedding creation disabled (IS_EMBEDDING != TRUE)")
        return
    
    embeddings, dimension = EMBEDDING_FUNCTION, EMBEDDING_DIMENSION
    logging.info(f'🔄 Immediate embedding creation - model: {embeddings}, dimension: {dimension}')
    
    # Chunk'lar yeni oluşturuldu, tümü için embedding oluştur
    data_for_query = []
    logging.info(f"🔄 {len(chunkId_chunkDoc_list)} yeni chunk için embedding oluşturuluyor...")
    
    for row in chunkId_chunkDoc_list:
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
            logging.debug(f"✅ Embedding oluşturuldu: {row['chunk_id']}")
        except Exception as e:
            logging.error(f"❌ Embedding creation failed for chunk {row['chunk_id']}: {e}")
            continue
    
    if data_for_query:
        # Chunk'ları embedding ile güncelle (chunk'lar yeni oluşturuldu, direkt güncelle)
        query_to_create_embedding = """
            UNWIND $data AS row
            MATCH (c:Chunk {id: row.chunkId})
            SET c.embedding = row.embeddings
            RETURN count(c) as updated_count
        """       
        result = execute_graph_query(graph, query_to_create_embedding, params={"data": data_for_query})
        updated_count = result[0]['updated_count'] if result else len(data_for_query)
        logging.info(f"✅ {updated_count} yeni chunk için embedding başarıyla oluşturuldu")
    else:
        logging.warning("⚠️ Hiç embedding oluşturulamadı")
    
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
        
        # Position ve lokasyon bazlı unique ID oluştur (content duplicate'lar için)
        # Bu yaklaşım aynı content'in farklı lokasyonlarda farklı chunk'lar olmasını sağlar
        position = i + 1
        page_num = chunk.metadata.get('page_number', 0) if hasattr(chunk, 'metadata') and chunk.metadata else 0
        
        # Lokasyon bazlı unique ID: filename + position + page + content_hash
        content_hash = hashlib.sha1(content.encode('utf-8')).hexdigest()[:16]  # Kısa hash
        location_identifier = f"{file_name}::pos_{position}::page_{page_num}::content_{content_hash}"
        current_chunk_id = hashlib.sha1(location_identifier.encode('utf-8')).hexdigest()
        
        previous_chunk_id = current_chunk_id if i == 0 else lst_chunks_including_hash[i-1]['chunk_id'] 
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
                MERGE (pc)-[:NEXT_CHUNK]->(c))
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


def create_chunks_for_upload(graph, chunks, file_name, page_images=None, generate_embedding=False):
    """
    Upload aşamasında chunk node'ları oluştur (extract'a uyumlu yapı)
    
    Args:
        graph: Neo4j graph connection
        chunks: List of langchain Document objects
        file_name: İşlenen dosya adı
        page_images: Sayfa resim dosyalarının listesi (dosya adları)
        generate_embedding: Chunk'lar oluşturulduktan sonra embedding oluşturulsun mu
    
    Returns:
        List of created chunk IDs with chunk documents (extract format)
    """
    logging.info(f"🔄 STARTING CHUNK CREATION FOR UPLOAD")
    logging.info(f"📁 File: {file_name}")
    logging.info(f"🧩 Input chunks count: {len(chunks)}")
    logging.info(f"🖼️ Page images: {len(page_images) if page_images else 0}")
    logging.info(f"⚡ Generate embedding: {generate_embedding}")
    
    # Mevcut chunk'ları kontrol et (bilgi amaçlı - otomatik temizlik önceden yapıldı)
    existing_check_query = """
        MATCH (c:Chunk {fileName: $file_name})
        RETURN count(c) as existing_count
    """
    existing_result = execute_graph_query(graph, existing_check_query, params={"file_name": file_name})
    existing_count = existing_result[0]['existing_count'] if existing_result else 0
    
    if existing_count > 0:
        logging.info(f"ℹ️ Found {existing_count} existing chunks (should have been cleaned by auto-cleanup)")
    else:
        logging.info(f"✅ No existing chunks found - ready for fresh creation")
    
    logging.info(f"� Creating {len(chunks)} chunk nodes for upload (extract-compatible structure)")
    
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
        
        # Position ve lokasyon bazlı unique ID oluştur (content duplicate'lar için)
        # Bu yaklaşım aynı content'in farklı lokasyonlarda farklı chunk'lar olmasını sağlar
        position = i + 1
        page_num = chunk.metadata.get('page_number', 0) if hasattr(chunk, 'metadata') and chunk.metadata else 0
        
        # Lokasyon bazlı unique ID: filename + position + page + content_hash
        content_hash = hashlib.sha1(content.encode('utf-8')).hexdigest()[:16]  # Kısa hash
        location_identifier = f"{file_name}::pos_{position}::page_{page_num}::content_{content_hash}"
        current_chunk_id = hashlib.sha1(location_identifier.encode('utf-8')).hexdigest()
        
        logging.info(f"� CHUNK #{position}: ID={current_chunk_id[:8]}..., Page={page_num}, Length={len(content)}")
        logging.info(f"   📍 Location ID: pos_{position}::page_{page_num}::content_{content_hash}")
        
        if i > 0:
            offset += len(chunks[i-1].page_content)
        
        firstChunk = (i == 0)
        
        # Detaylı chunk loglama
        content_preview = content[:100] + "..." if len(content) > 100 else content
        
        # Aynı content'in farklı yerlerde olup olmadığını kontrol et (bilgi amaçlı)
        similar_content_count = sum(1 for item in lst_chunks_including_hash 
                                   if item['chunk_doc'].page_content.strip() == content)
        if similar_content_count > 0:
            logging.info(f"   � INFO: Similar content found in {similar_content_count} previous chunk(s) - this is normal for headers/footers")
        
        logging.info(f"   📝 Content preview: '{content_preview}'")
        
        if i > 0:
            logging.info(f"🔗 RELATIONSHIP: Chunk #{position-1} (ID={previous_chunk_id[:8] if previous_chunk_id else 'None'}...) -> Chunk #{position} (ID={current_chunk_id[:8]}...)")
        else:
            logging.info(f"🏁 FIRST_CHUNK: Chunk #{position} (ID={current_chunk_id[:8]}...)")
        
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
            logging.info(f"📝 RELATIONSHIP ADDED: FIRST_CHUNK -> {current_chunk_id[:8]}...")
        else:
            relationships.append({
                "type": "NEXT_CHUNK",
                "previous_chunk_id": previous_chunk_id,
                "current_chunk_id": current_chunk_id
            })
            logging.info(f"📝 RELATIONSHIP ADDED: NEXT_CHUNK {previous_chunk_id[:8] if previous_chunk_id else 'None'}... -> {current_chunk_id[:8]}...")
        
        previous_chunk_id = current_chunk_id
    
    # Chunk node'ları ve PART_OF ilişkilerini oluştur (extract'daki gibi)
    logging.info(f"🔄 Creating chunk nodes and PART_OF relationships for {len(batch_data)} chunks")
    logging.info(f"📊 BATCH_DATA SUMMARY: Total chunks to create: {len(batch_data)}")
    
    for i, chunk_data in enumerate(batch_data[:5]):  # İlk 5 chunk'ı logla
        logging.info(f"   CHUNK {i+1}: ID={chunk_data['id'][:8]}..., Position={chunk_data['position']}, FileName={chunk_data['f_name']}")
    
    if len(batch_data) > 5:
        logging.info(f"   ... ve {len(batch_data) - 5} chunk daha")
    
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
    
    # NEXT_CHUNK ilişkilerini position bazlı oluştur (daha güvenli)
    logging.info(f"🔄 Creating NEXT_CHUNK relationships using position-based approach")
    
    # Önce mevcut chunk'ların position'larını kontrol et
    position_check_query = """
        MATCH (c:Chunk {fileName: $file_name})
        RETURN c.position as position, c.id as chunk_id
        ORDER BY c.position
    """
    existing_positions = execute_graph_query(graph, position_check_query, params={"file_name": file_name})
    
    if existing_positions:
        logging.info(f"📊 EXISTING CHUNKS: Found {len(existing_positions)} chunks with positions:")
        for i, pos_data in enumerate(existing_positions[:10]):  # İlk 10'unu logla
            logging.info(f"   Position {pos_data['position']}: ID={pos_data['chunk_id'][:8]}...")
        if len(existing_positions) > 10:
            logging.info(f"   ... ve {len(existing_positions) - 10} chunk daha")
    
    query_to_create_NEXT_relation = """
        MATCH (c1:Chunk {fileName: $file_name})
        MATCH (c2:Chunk {fileName: $file_name})
        WHERE c2.position = c1.position + 1
        MERGE (c1)-[:NEXT_CHUNK]->(c2)
        RETURN count(*) as created_count
    """
    next_result = execute_graph_query(graph, query_to_create_NEXT_relation, params={"file_name": file_name})
    logging.info(f"✅ Created {next_result[0]['created_count'] if next_result else 0} NEXT_CHUNK relationships using position-based approach")
    
    # Embedding'leri oluştur (eğer isteniyorsa)
    if generate_embedding:
        logging.info(f"🔄 Upload sırasında embedding oluşturuluyor...")
        try:
            # Embedding oluşturma için mevcut fonksiyonu kullan
            create_chunk_embeddings_immediate(graph, lst_chunks_including_hash, file_name)
            logging.info(f"✅ Upload sırasında {len(lst_chunks_including_hash)} chunk için embedding oluşturuldu")
            
            # Embedding'ler oluşturulduktan sonra vector index'i kontrol et/oluştur
            try:
                create_chunk_vector_index(graph)
                logging.info(f"✅ Vector index checked/created after upload embeddings")
            except Exception as vector_error:
                logging.warning(f"⚠️ Vector index creation warning after upload: {vector_error}")
                
        except Exception as e:
            logging.error(f"❌ Upload sırasında embedding oluşturma hatası: {e}")
            # Embedding hatası chunk oluşturmayı durdurmasın
    
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


def create_policy_entity_relationships(graph: Neo4jGraph, file_name: str):
    """
    İşlem yapılan chunk'ın bağlı olduğu Policy node'unu bulur ve 
    o chunk'tan çıkarılan entity'lere HAS_ENTITY ile bağlar.
    
    Args:
        graph: Neo4j graph instance
        file_name: İşlem yapılacak dosya adı
    """
    logging.info(f"Creating Policy-Entity relationships for file: {file_name}")
    
    # Önce tüm LLM'den çıkan node'ların __Entity__ label'ına sahip olduğundan emin ol
    ensure_entity_labels_query = """
    MATCH (d:Document {fileName: $file_name})
    MATCH (d)<-[:PART_OF]-(c:Chunk)<-[:EXTRACTED_FROM]-(n)
    WHERE NOT n:__Entity__
    SET n:__Entity__
    RETURN count(n) AS updated_nodes
    """
    
    try:
        entity_result = execute_graph_query(graph, ensure_entity_labels_query, params={"file_name": file_name})
        updated_nodes = entity_result[0].get('updated_nodes', 0) if entity_result else 0
        if updated_nodes > 0:
            logging.info(f"✅ Added __Entity__ label to {updated_nodes} nodes")
    except Exception as e:
        logging.warning(f"Warning: Could not ensure __Entity__ labels: {e}")
    
    # Policy node'ları chunk'lardan çıkarılan entity'lere bağla
    policy_entity_query = """
    // Her chunk için: o chunk'tan çıkarılan Policy node'unu bul
    MATCH (d:Document {fileName: $file_name})
    MATCH (d)<-[:PART_OF]-(c:Chunk)<-[:EXTRACTED_FROM]-(policy:__Entity__)
    WHERE 'Policy' in labels(policy) OR policy.entity_type = 'Policy' OR toLower(policy.id) CONTAINS 'policy'
    
    // Aynı chunk'tan çıkarılan diğer entity'leri bul (Policy hariç)
    MATCH (entity:__Entity__)-[:EXTRACTED_FROM]->(c)
    WHERE entity <> policy 
    AND NOT 'Policy' in labels(entity) 
    AND entity.entity_type <> 'Policy'
    AND NOT toLower(entity.id) CONTAINS 'policy'
    
    // Policy'yi aynı chunk'tan çıkarılan entity'lere HAS_ENTITY ile bağla (business logic)
    MERGE (policy)-[r:HAS_ENTITY]->(entity)
    ON CREATE SET 
        r.created_at = datetime(),
        r.source_chunk = c.id,
        r.relationship_type = 'policy_to_chunk_entity',
        r.source = 'chunk_based_linking'
    ON MATCH SET 
        r.updated_at = datetime()
    
    RETURN count(DISTINCT r) AS relationships_created, 
           count(DISTINCT policy) AS policy_nodes_processed,
           count(DISTINCT entity) AS entities_linked,
           count(DISTINCT c) AS chunks_processed
    """
    
    try:
        result = execute_graph_query(graph, policy_entity_query, params={"file_name": file_name})
        
        if result and len(result) > 0:
            relationships_created = result[0].get('relationships_created', 0)
            policy_nodes_processed = result[0].get('policy_nodes_processed', 0)
            entities_linked = result[0].get('entities_linked', 0)
            chunks_processed = result[0].get('chunks_processed', 0)
            
            logging.info(f"✅ Policy-Entity relationships created: {relationships_created} relationships")
            logging.info(f"   Policy nodes processed: {policy_nodes_processed}")
            logging.info(f"   Entities linked: {entities_linked}")
            logging.info(f"   Chunks processed: {chunks_processed}")
            
            return {
                'relationships_created': relationships_created,
                'policy_nodes_processed': policy_nodes_processed,
                'entities_linked': entities_linked,
                'chunks_processed': chunks_processed
            }
        else:
            logging.info("No Policy nodes found or no relationships created")
            return {
                'relationships_created': 0,
                'policy_nodes_processed': 0,
                'entities_linked': 0,
                'chunks_processed': 0
            }
            
    except Exception as e:
        logging.error(f"Error creating Policy-Entity relationships: {e}")
        raise