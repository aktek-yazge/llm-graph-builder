import hashlib
import logging
import numpy as np
# YouTube functions moved to celery_worker - import optionally
try:
    from src.document_sources.youtube import create_youtube_url
except (ImportError, ModuleNotFoundError):
    # Stub function for backend (YouTube processing is in celery_worker)
    def create_youtube_url(*args, **kwargs):
        raise NotImplementedError("YouTube functions are only available in celery_worker")
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_google_vertexai import VertexAIEmbeddings
from langchain_openai import OpenAIEmbeddings
from langchain_neo4j import Neo4jGraph
from neo4j.exceptions import TransientError, ClientError
from langchain_community.graphs.graph_document import GraphDocument
from typing import List, Dict, Tuple, Optional
import re
import os
import time
from pathlib import Path
from urllib.parse import urlparse
import boto3
from langchain_community.embeddings import BedrockEmbeddings
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()


# ============================================================================
# Embedding Cache - Global cache for all embedding operations
# ============================================================================
_embedding_cache: Dict[str, np.ndarray] = {}
_cache_hits = 0
_cache_misses = 0

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
          # Metadata'dan chunk_ids al
          chunk_ids = graph_document.source.metadata.get('combined_chunk_ids')
          
          # Eğer yoksa boş liste kullan
          if chunk_ids is None:
              logging.warning(f"GraphDocument metadata'sında 'combined_chunk_ids' bulunamadı: {graph_document.source.metadata}")
              chunk_ids = []
          
          # Her chunk ID için mapping oluştur
          for chunk_id in chunk_ids:
            lst_chunk_chunkId_document.append({'graph_doc':graph_document,'chunk_id':chunk_id})
                  
  logging.info(f"✅ Created {len(lst_chunk_chunkId_document)} chunk-graphDocument pairs")
  return lst_chunk_chunkId_document  
                 
def create_graph_database_connection(uri, userName, password, database):
  enable_user_agent = os.environ.get("ENABLE_USER_AGENT", "False").lower() in ("true", "1", "yes")
  
  # Eğer username veya password boş/None ise, environment variable'lardan al
  if not userName or (isinstance(userName, str) and userName.strip() == ""):
    userName = os.environ.get("NEO4J_USERNAME")
  if not password or (isinstance(password, str) and password.strip() == ""):
    password = os.environ.get("NEO4J_PASSWORD")
  if not database or (isinstance(database, str) and database.strip() == ""):
    database = os.environ.get("NEO4J_DATABASE", "neo4j")
  if not uri or (isinstance(uri, str) and uri.strip() == ""):
    uri = os.environ.get("NEO4J_URI")
  
  # Environment'tan timeout ve connection ayarlarını al
  connection_timeout = int(os.environ.get("NEO4J_CONNECTION_TIMEOUT", "30"))
  read_timeout = int(os.environ.get("NEO4J_READ_TIMEOUT", "120"))
  write_timeout = int(os.environ.get("NEO4J_WRITE_TIMEOUT", "120"))
  max_connection_lifetime = int(os.environ.get("NEO4J_MAX_CONNECTION_LIFETIME", "300"))
  # Default pool size: 50, but for V2 batch processing with 40 files and parallel batches, increase to 100
  # Each file can have multiple parallel batch queries, so we need more connections
  max_connection_pool_size = int(os.environ.get("NEO4J_MAX_CONNECTION_POOL_SIZE", "100"))
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


def load_embedding_model(embedding_model_name: str = "openai"):
    """
    Embedding modeli yükler.
    
    OpenAI için gelişmiş özellikler:
    - Batch embedding (embed_texts)
    - In-memory caching
    - Cosine similarity hesaplama
    
    Args:
        embedding_model_name: "openai", "vertexai", "titan" veya HuggingFace model
        
    Returns:
        (embeddings, dimension) tuple
        
    OpenAI için ek metodlar:
        embeddings.embed_text(text) - Tek metin için embedding
        embeddings.embed_texts(texts) - Batch embedding (cached)
        embeddings.cosine_similarity(vec1, vec2) - Similarity hesaplama
        embeddings.get_cache_stats() - Cache istatistikleri
        embeddings.clear_cache() - Cache temizleme
    """
    if embedding_model_name == "openai":
        api_key = os.getenv("OPENAI_API_KEY")
        model_name = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
        dimension = 1536
        
        # Wrapper class - Pydantic'e attribute ekleyemeyiz
        class OpenAIEmbeddingWrapper:
            """OpenAI embedding wrapper with batch, cache and similarity support"""
            
            def __init__(self):
                self.api_key = api_key
                self.model_name = model_name
                self.dimension = dimension
                self._langchain_embeddings = OpenAIEmbeddings(api_key=api_key, model=model_name) if api_key else OpenAIEmbeddings()
                self.openai_client = OpenAI(api_key=api_key) if api_key else None
            
            def embed_query(self, text: str) -> List[float]:
                """LangChain uyumluluğu için"""
                return self._langchain_embeddings.embed_query(text)
            
            def embed_documents(self, texts: List[str]) -> List[List[float]]:
                """LangChain uyumluluğu için"""
                return self._langchain_embeddings.embed_documents(texts)
            
            def embed_text(self, text: str, use_cache: bool = True) -> Optional[np.ndarray]:
                """Tek metin için embedding (cached)"""
                global _embedding_cache, _cache_hits, _cache_misses
                
                if not self.openai_client or not text:
                    return None
                
                cache_key = text.strip()
                if use_cache and cache_key in _embedding_cache:
                    _cache_hits += 1
                    return _embedding_cache[cache_key]
                
                _cache_misses += 1
                try:
                    response = self.openai_client.embeddings.create(model=self.model_name, input=cache_key)
                    result = np.array(response.data[0].embedding)
                    if use_cache:
                        _embedding_cache[cache_key] = result
                    return result
                except Exception as e:
                    logging.error(f"OpenAI embedding error: {e}")
                    return None
            
            def embed_texts(self, texts: List[str], use_cache: bool = True) -> List[Optional[np.ndarray]]:
                """Batch embedding (cached, 2000/batch)"""
                global _embedding_cache, _cache_hits, _cache_misses
                
                if not self.openai_client or not texts:
                    return [None] * len(texts)
                
                results = [None] * len(texts)
                to_embed = []
                to_embed_indices = []
                
                for i, text in enumerate(texts):
                    if not text:
                        results[i] = np.zeros(self.dimension)
                        continue
                    cache_key = text.strip()
                    if use_cache and cache_key in _embedding_cache:
                        _cache_hits += 1
                        results[i] = _embedding_cache[cache_key]
                    else:
                        _cache_misses += 1
                        to_embed.append(cache_key)
                        to_embed_indices.append(i)
                
                if to_embed:
                    try:
                        batch_size = 2000
                        all_emb = []
                        for start in range(0, len(to_embed), batch_size):
                            batch = to_embed[start:start + batch_size]
                            resp = self.openai_client.embeddings.create(model=self.model_name, input=batch)
                            all_emb.extend([np.array(d.embedding) for d in resp.data])
                        
                        for j, idx in enumerate(to_embed_indices):
                            results[idx] = all_emb[j]
                            if use_cache:
                                _embedding_cache[to_embed[j]] = all_emb[j]
                    except Exception as e:
                        logging.error(f"OpenAI batch embedding error: {e}")
                
                return results
            
            def cosine_similarity(self, vec1: np.ndarray, vec2: np.ndarray) -> float:
                """Cosine similarity hesapla"""
                if vec1 is None or vec2 is None:
                    return 0.0
                dot = np.dot(vec1, vec2)
                n1, n2 = np.linalg.norm(vec1), np.linalg.norm(vec2)
                return float(dot / (n1 * n2)) if n1 > 0 and n2 > 0 else 0.0
            
            def get_cache_stats(self) -> Dict:
                """Cache istatistikleri"""
                total = _cache_hits + _cache_misses
                return {
                    "size": len(_embedding_cache),
                    "hits": _cache_hits,
                    "misses": _cache_misses,
                    "hit_rate": _cache_hits / total if total > 0 else 0
                }
            
            def clear_cache(self):
                """Cache temizle"""
                global _embedding_cache, _cache_hits, _cache_misses
                size = len(_embedding_cache)
                _embedding_cache.clear()
                _cache_hits = _cache_misses = 0
                logging.info(f"🧹 Embedding cache cleared: {size} entries")
        
        embeddings = OpenAIEmbeddingWrapper()
        logging.info(f"✅ Embedding: OpenAI {model_name}, Dimension:{dimension}, Batch+Cache enabled")
        
    elif embedding_model_name == "vertexai":        
        embeddings = VertexAIEmbeddings(model="textembedding-gecko@003")
        dimension = 768
        logging.info(f"Embedding: Using Vertex AI Embeddings, Dimension:{dimension}")
        
    elif embedding_model_name == "titan":
        embeddings = get_bedrock_embeddings()
        dimension = 1536
        logging.info(f"Embedding: Using Bedrock Titan Embeddings, Dimension:{dimension}")
        
    else:
        # HuggingFace - DEVRE DIŞI, OpenAI kullan
        logging.warning(f"⚠️ HuggingFace embedding devre dışı, OpenAI kullanılıyor")
        return load_embedding_model("openai")
    
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
   current_delay = delay
   while retries < max_retries:
       try:
           return graph.query(query, params) 
       except TransientError as e:
           if "DeadlockDetected" in str(e):
               retries += 1
               if retries < max_retries:
                   logging.warning(f"⚠️ Deadlock detected. Retrying {retries}/{max_retries} in {current_delay} seconds...")
                   time.sleep(current_delay)
                   current_delay *= 2  # Exponential backoff
               else:
                   logging.error("❌ Failed to execute query after maximum retries due to persistent deadlocks.")
                   raise
           else:
               raise 
       except ClientError as e:
           # Transaction timeout hataları için retry
           error_str = str(e)
           if "TransactionTimedOut" in error_str or ("Transaction" in error_str and "Timeout" in error_str):
               retries += 1
               if retries < max_retries:
                   logging.warning(f"⚠️ Transaction timeout detected. Retrying {retries}/{max_retries} in {current_delay} seconds...")
                   time.sleep(current_delay)
                   current_delay *= 2  # Exponential backoff
               else:
                   logging.error(f"❌ Transaction timeout after {max_retries} retries. Query: {query[:100]}...")
                   raise
           else:
               # Diğer ClientError'ları direkt fırlat
               raise
   
   logging.error("❌ Failed to execute query after maximum retries.")
   raise RuntimeError("Query execution failed after multiple retries.")

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
