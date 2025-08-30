import hashlib
import logging
from src.document_sources.youtube import create_youtube_url
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_google_vertexai import VertexAIEmbeddings
from langchain_openai import OpenAIEmbeddings
from langchain_neo4j import Neo4jGraph
from neo4j.exceptions import TransientError
from langchain_community.graphs.graph_document import GraphDocument
from typing import List
import re
import os
import time
from pathlib import Path
from urllib.parse import urlparse
import boto3
from langchain_community.embeddings import BedrockEmbeddings

def check_url_source(source_type, yt_url:str=None, wiki_query:str=None):
    language=''
    try:
      logging.info(f"incoming URL: {yt_url}")
      if source_type == 'youtube':
        if re.match(r'(?:https?://)?(?:www\.)?youtu\.?be(?:\.com)?/?.*(?:watch|embed)?(?:.*v=|v/|/)([\w\-_]+)&?',yt_url.strip()):
          youtube_url = create_youtube_url(yt_url.strip())
          logging.info(youtube_url)
          return youtube_url,language
        else:
          raise Exception('Incoming URL is not youtube URL')
      
      elif  source_type == 'Wikipedia':
        wiki_query_id=''
        #pattern = r"https?:\/\/([a-zA-Z0-9\.\,\_\-\/]+)\.wikipedia\.([a-zA-Z]{2,3})\/wiki\/([a-zA-Z0-9\.\,\_\-\/]+)"
        wikipedia_url_regex = r'https?:\/\/(www\.)?([a-zA-Z]{2,3})\.wikipedia\.org\/wiki\/(.*)'
        wiki_id_pattern = r'^[a-zA-Z0-9 _\-\.\,\:\(\)\[\]\{\}\/]*$'
        
        match = re.search(wikipedia_url_regex, wiki_query.strip())
        if match:
                language = match.group(2)
                wiki_query_id = match.group(3)
          # else : 
          #       languages.append("en")
          #       wiki_query_ids.append(wiki_url.strip())
        else:
            raise Exception(f'Not a valid wikipedia url: {wiki_query} ')

        logging.info(f"wikipedia query id = {wiki_query_id}")     
        return wiki_query_id, language     
    except Exception as e:
      logging.error(f"Error in recognize URL: {e}")
      raise Exception(e)


def get_chunk_and_graphDocument(graph_document_list, chunkId_chunkDoc_list):
  logging.info("creating list of chunks and graph documents in get_chunk_and_graphDocument func")
  lst_chunk_chunkId_document=[]
  for graph_document in graph_document_list:            
          # Normal LLM metadata formatı: 'combined_chunk_ids'
          chunk_ids = graph_document.source.metadata.get('combined_chunk_ids')
          
          # LangExtract metadata formatı: 'chunk_ids' 
          if chunk_ids is None:
              chunk_ids = graph_document.source.metadata.get('chunk_ids')
          
          # Eğer ikisi de yoksa boş liste kullan
          if chunk_ids is None:
              logging.warning(f"GraphDocument metadata'sında ne 'combined_chunk_ids' ne de 'chunk_ids' bulunamadı: {graph_document.source.metadata}")
              chunk_ids = []
          
          # Her chunk ID için mapping oluştur
          for chunk_id in chunk_ids:
            lst_chunk_chunkId_document.append({'graph_doc':graph_document,'chunk_id':chunk_id})
                  
  logging.info(f"✅ Created {len(lst_chunk_chunkId_document)} chunk-graphDocument pairs")
  return lst_chunk_chunkId_document  
                 
def create_graph_database_connection(uri, userName, password, database):
  enable_user_agent = os.environ.get("ENABLE_USER_AGENT", "False").lower() in ("true", "1", "yes")
  
  # Environment'tan timeout ve connection ayarlarını al
  connection_timeout = int(os.environ.get("NEO4J_CONNECTION_TIMEOUT", "30"))
  read_timeout = int(os.environ.get("NEO4J_READ_TIMEOUT", "120"))
  write_timeout = int(os.environ.get("NEO4J_WRITE_TIMEOUT", "120"))
  max_connection_lifetime = int(os.environ.get("NEO4J_MAX_CONNECTION_LIFETIME", "300"))
  max_connection_pool_size = int(os.environ.get("NEO4J_MAX_CONNECTION_POOL_SIZE", "50"))
  connection_acquisition_timeout = int(os.environ.get("NEO4J_CONNECTION_ACQUISITION_TIMEOUT", "60"))
  
  # SSL kullanımını devre dışı bırak - her zaman encrypted=False
  use_ssl = False
  
  # Driver config - SSL tamamen devre dışı
  driver_config = {
    'encrypted': False,
    'trust': 'TRUST_ALL_CERTIFICATES',
    'max_connection_lifetime': max_connection_lifetime,
    'max_connection_pool_size': max_connection_pool_size,
    'connection_acquisition_timeout': connection_acquisition_timeout,
    'connection_timeout': connection_timeout
  }
  
  logging.info(f"Neo4j bağlantısı kuruluyor: {uri} (SSL: DISABLED)")
  logging.info(f"Connection config: timeout={connection_timeout}s, pool_size={max_connection_pool_size}")
  
  if enable_user_agent:
    driver_config['user_agent'] = os.environ.get('NEO4J_USER_AGENT')
    graph = Neo4jGraph(url=uri, database=database, username=userName, password=password, 
                      refresh_schema=False, sanitize=False, driver_config=driver_config,
                      timeout=read_timeout)  
  else:
    graph = Neo4jGraph(url=uri, database=database, username=userName, password=password, 
                      refresh_schema=False, sanitize=False, driver_config=driver_config,
                      timeout=read_timeout)    
  return graph


def load_embedding_model(embedding_model_name: str):
    if embedding_model_name == "openai":
        embeddings = OpenAIEmbeddings()
        dimension = 1536
        logging.info(f"Embedding: Using OpenAI Embeddings , Dimension:{dimension}")
    elif embedding_model_name == "vertexai":        
        embeddings = VertexAIEmbeddings(
            model="textembedding-gecko@003"
        )
        dimension = 768
        logging.info(f"Embedding: Using Vertex AI Embeddings , Dimension:{dimension}")
    elif embedding_model_name == "titan":
        embeddings = get_bedrock_embeddings()
        dimension = 1536
        logging.info(f"Embedding: Using bedrock titan Embeddings , Dimension:{dimension}")
    else:
        embeddings = HuggingFaceEmbeddings(
            model_name="all-MiniLM-L6-v2"#, cache_folder="/embedding_model"
        )
        dimension = 384
        logging.info(f"Embedding: Using Langchain HuggingFaceEmbeddings , Dimension:{dimension}")
    return embeddings, dimension

def save_graphDocuments_in_neo4j(graph: Neo4jGraph, graph_document_list: List[GraphDocument], max_retries=3, delay=1):
   retries = 0
   while retries < max_retries:
       try:
           graph.add_graph_documents(graph_document_list, baseEntityLabel=True)
           return
       except TransientError as e:
           if "DeadlockDetected" in str(e):
               retries += 1
               logging.info(f"Deadlock detected. Retrying {retries}/{max_retries} in {delay} seconds...")
               time.sleep(delay)  # Wait before retrying
           else:
               raise
   logging.error("Failed to execute query after maximum retries due to persistent deadlocks.")
   raise RuntimeError("Query execution failed after multiple retries due to deadlock.")
           
def handle_backticks_nodes_relationship_id_type(graph_document_list:List[GraphDocument]):
  logging.info(f"🔍 handle_backticks_nodes_relationship_id_type başlıyor - GraphDocument sayısı: {len(graph_document_list)}")
  
  for i, graph_document in enumerate(graph_document_list):
    logging.info(f"🔍 GraphDocument #{i+1}: {len(graph_document.nodes)} node, {len(graph_document.relationships)} relationship")
    
    # Debug: Node'ları detaylı incele
    for j, node in enumerate(graph_document.nodes):
      logging.info(f"    Node #{j+1}: id='{node.id}' (strip='{node.id.strip()}'), type='{node.type}' (strip='{node.type.strip()}')")
    
    # Clean node id and types
    cleaned_nodes = []
    filtered_nodes = 0
    for node in graph_document.nodes:
      if node.type.strip() and node.id.strip():
        node.type = node.type.replace('`', '')
        cleaned_nodes.append(node)
      else:
        filtered_nodes += 1
        logging.warning(f"⚠️ Node filtrelendi: id='{node.id}', type='{node.type}'")
    
    logging.info(f"    Node temizleme: {len(graph_document.nodes)} -> {len(cleaned_nodes)} (filtrelenen: {filtered_nodes})")
    # Clean node id and types
    cleaned_nodes = []
    for node in graph_document.nodes:
      if node.type.strip() and node.id.strip():
        node.type = node.type.replace('`', '')
        cleaned_nodes.append(node)
    # Clean relationship id types and source/target node id and types
    cleaned_relationships = []
    filtered_relationships = 0
    for rel in graph_document.relationships:
      if rel.type.strip() and rel.source.id.strip() and rel.source.type.strip() and rel.target.id.strip() and rel.target.type.strip():
        rel.type = rel.type.replace('`', '')
        rel.source.type = rel.source.type.replace('`', '')
        rel.target.type = rel.target.type.replace('`', '')
        cleaned_relationships.append(rel)
      else:
        filtered_relationships += 1
        logging.warning(f"⚠️ Relationship filtrelendi: type='{rel.type}', source='{rel.source.id}' ({rel.source.type}), target='{rel.target.id}' ({rel.target.type})")
    
    logging.info(f"    Relationship temizleme: {len(graph_document.relationships)} -> {len(cleaned_relationships)} (filtrelenen: {filtered_relationships})")
    
    graph_document.relationships = cleaned_relationships
    graph_document.nodes = cleaned_nodes
  
  total_nodes = sum(len(doc.nodes) for doc in graph_document_list)
  total_rels = sum(len(doc.relationships) for doc in graph_document_list)
  logging.info(f"✅ Temizleme tamamlandı - Toplam: {total_nodes} node, {total_rels} relationship")
  
  return graph_document_list

def execute_graph_query(graph: Neo4jGraph, query, params=None, max_retries=3, delay=2):
   retries = 0
   while retries < max_retries:
       try:
           return graph.query(query, params) 
       except TransientError as e:
           if "DeadlockDetected" in str(e):
               retries += 1
               logging.info(f"Deadlock detected. Retrying {retries}/{max_retries} in {delay} seconds...")
               time.sleep(delay)  # Wait before retrying
           else:
               raise 
   logging.error("Failed to execute query after maximum retries due to persistent deadlocks.")
   raise RuntimeError("Query execution failed after multiple retries due to deadlock.")

def delete_uploaded_local_file(merged_file_path, file_name):
  file_path = Path(merged_file_path)
  if file_path.exists():
    try:
      file_path.unlink()
      logging.info(f'file {file_name} deleted successfully')
    except FileNotFoundError:
      logging.info(f'file {file_name} was already deleted')
    except Exception as e:
      logging.error(f'Error deleting file {file_name}: {e}')
  else:
    logging.info(f'file {file_name} does not exist, no deletion needed')
   
def close_db_connection(graph, api_name):
  if not graph._driver._closed:
      logging.info(f"closing connection for {api_name} api")
      # graph._driver.close()   
  
def create_gcs_bucket_folder_name_hashed(uri, file_name):
  folder_name = uri + file_name
  folder_name_sha1 = hashlib.sha1(folder_name.encode())
  folder_name_sha1_hashed = folder_name_sha1.hexdigest()
  return folder_name_sha1_hashed

def formatted_time(current_time):
  formatted_time = current_time.strftime('%Y-%m-%d %H:%M:%S %Z')
  return str(formatted_time)

def last_url_segment(url):
  parsed_url = urlparse(url)
  path = parsed_url.path.strip("/")  # Remove leading and trailing slashes
  last_url_segment = path.split("/")[-1] if path else parsed_url.netloc.split(".")[0]
  return last_url_segment

def get_bedrock_embeddings():
   """
   Creates and returns a BedrockEmbeddings object using the specified model name.
   Args:
       model (str): The name of the model to use for embeddings.
   Returns:
       BedrockEmbeddings: An instance of the BedrockEmbeddings class.
   """
   try:
       env_value = os.getenv("BEDROCK_EMBEDDING_MODEL")
       if not env_value:
           raise ValueError("Environment variable 'BEDROCK_EMBEDDING_MODEL' is not set.")
       try:
           model_name, aws_access_key, aws_secret_key, region_name = env_value.split(",")
       except ValueError:
           raise ValueError(
               "Environment variable 'BEDROCK_EMBEDDING_MODEL' is improperly formatted. "
               "Expected format: 'model_name,aws_access_key,aws_secret_key,region_name'."
           )
       bedrock_client = boto3.client(
               service_name="bedrock-runtime",
               region_name=region_name.strip(),
               aws_access_key_id=aws_access_key.strip(),
               aws_secret_access_key=aws_secret_key.strip(),
           )
       bedrock_embeddings = BedrockEmbeddings(
           model_id=model_name.strip(),
           client=bedrock_client
       )
       return bedrock_embeddings
   except Exception as e:
       print(f"An unexpected error occurred: {e}")
       raise
