from langchain_neo4j import Neo4jGraph
from langchain.docstore.document import Document
from src.shared.common_fn import load_embedding_model,execute_graph_query
from src.shared.common_fn import load_embedding_model,execute_graph_query
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
        execute_graph_query(graph,unwind_query, params={"batch_data": batch_data})

    
def create_chunk_embeddings(graph, chunkId_chunkDoc_list, file_name):
    isEmbedding = os.getenv('IS_EMBEDDING')
    
    embeddings, dimension = EMBEDDING_FUNCTION , EMBEDDING_DIMENSION
    logging.info(f'embedding model:{embeddings} and dimesion:{dimension}')
    data_for_query = []
    logging.info(f"update embedding and vector index for chunks")
    for row in chunkId_chunkDoc_list:
        if isEmbedding.upper() == "TRUE":
            embeddings_arr = embeddings.embed_query(row['chunk_doc'].page_content)
                                    
            data_for_query.append({
                "chunkId": row['chunk_id'],
                "embeddings": embeddings_arr
            })
    
    query_to_create_embedding = """
        UNWIND $data AS row
        MATCH (d:Document {fileName: $fileName})
        MERGE (c:Chunk {id: row.chunkId})
        SET c.embedding = row.embeddings
        MERGE (c)-[:PART_OF]->(d)
    """       
    execute_graph_query(graph,query_to_create_embedding, params={"fileName":file_name, "data":data_for_query})
    execute_graph_query(graph,query_to_create_embedding, params={"fileName":file_name, "data":data_for_query})
    
def create_relation_between_chunks(graph, file_name, chunks: List[Document])->list:
    logging.info("creating FIRST_CHUNK and NEXT_CHUNK relationships between chunks")
    current_chunk_id = ""
    lst_chunks_including_hash = []
    batch_data = []
    relationships = []
    offset=0
    for i, chunk in enumerate(chunks):
        page_content_sha1 = hashlib.sha1(chunk.page_content.encode())
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
            page_content=chunk.page_content, metadata=metadata
        )
        
        chunk_data = {
            "id": current_chunk_id,
            "pg_content": chunk_document.page_content,
            "position": position,
            "length": chunk_document.metadata["length"],
            "f_name": file_name,
            "previous_id" : previous_chunk_id,
            "content_offset" : offset
        }
        
        if 'page_number' in chunk.metadata:
            chunk_data['page_number'] = chunk.metadata['page_number']
         
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
        SET c.text = data.pg_content, c.position = data.position, c.length = data.length, c.fileName=data.f_name, c.content_offset=data.content_offset
        WITH data, c
        SET c.page_number = CASE WHEN data.page_number IS NOT NULL THEN data.page_number END,
            c.start_time = CASE WHEN data.start_time IS NOT NULL THEN data.start_time END,
            c.end_time = CASE WHEN data.end_time IS NOT NULL THEN data.end_time END
        WITH data, c
        MATCH (d:Document {fileName: data.f_name})
        MERGE (c)-[:PART_OF]->(d)
    """
    execute_graph_query(graph,query_to_create_chunk_and_PART_OF_relation, params={"batch_data": batch_data})
    execute_graph_query(graph,query_to_create_chunk_and_PART_OF_relation, params={"batch_data": batch_data})
    
    query_to_create_FIRST_relation = """ 
        UNWIND $relationships AS relationship
        MATCH (d:Document {fileName: $f_name})
        MATCH (c:Chunk {id: relationship.chunk_id})
        FOREACH(r IN CASE WHEN relationship.type = 'FIRST_CHUNK' THEN [1] ELSE [] END |
                MERGE (d)-[:FIRST_CHUNK]->(c))
        """
    execute_graph_query(graph,query_to_create_FIRST_relation, params={"f_name": file_name, "relationships": relationships})
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
        CALL apoc.merge.node([entity.type, '__Entity__'], {id: entity.id}) YIELD node
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
            embedding = EMBEDDING_FUNCTION.embed_query(entity["id"])
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
    MATCH (c:Chunk {fileName: $fileName})
    WHERE c.embedding IS NOT NULL
    CALL db.index.vector.queryNodes('vector', 5, c.embedding) YIELD node AS other, score
    WHERE other <> c AND score >= $threshold
    MERGE (c)-[r:SIMILAR]->(other)
    SET r.score = score
    """
    execute_graph_query(graph, query, params={"fileName": file_name, "threshold": similarity_threshold})
    
    # Create entity vector index if it doesn't exist
    create_entity_vector_index(graph)

async def create_llm_chunk_relations(graph, model, chunk_list, allowed_rel, additional_instructions=None):
    """
    Use LLM to detect continuation between consecutive chunks and create CONTINUES relationships.
    """
    llm, _ = get_llm(model)
    for i in range(len(chunk_list) - 1):
        src_id = chunk_list[i]['chunk_id']
        tgt_id = chunk_list[i+1]['chunk_id']
        first_text = chunk_list[i]['chunk_doc'].page_content
        second_text = chunk_list[i+1]['chunk_doc'].page_content
        prompt = CHUNK_CONTINUATION_PROMPT.format(first_text=first_text, second_text=second_text)
        # call LLM
        response = await llm.apredict_messages([{"role": "system", "content": prompt}]) if hasattr(llm, 'apredict_messages') else llm([{"role": "system", "content": prompt}])
        # parse JSON
        try:
            resp_json = json.loads(response.content if hasattr(response, 'content') else response)
            relations = resp_json.get('relations', [])
        except Exception:
            continue
        # write relations in Neo4j
        for rel in relations:
            # expect format "<src_id>-CONTINUES-><tgt_id>"
            parts = rel.split('-CONTINUES->')
            if len(parts) == 2:
                s, t = parts
                query = (
                    "MATCH (a:Chunk {id:$src}), (b:Chunk {id:$tgt})"
                    " MERGE (a)-[:CONTINUES]->(b)"
                )
                execute_graph_query(graph, query, params={"src": s, "tgt": t})