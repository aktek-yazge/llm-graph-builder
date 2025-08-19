import os
import json
import time
import logging
import asyncio
import threading
import tempfile
import base64
import requests
import re
from datetime import datetime
from typing import Any
from dotenv import load_dotenv
import requests
import tempfile
import os

from langchain_neo4j import Neo4jVector
from langchain_neo4j import Neo4jChatMessageHistory
from langchain_neo4j import GraphCypherQAChain
from langchain.prompts import ChatPromptTemplate, MessagesPlaceholder, HumanMessagePromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnableBranch
from langchain.retrievers import ContextualCompressionRetriever
from langchain_community.document_transformers import EmbeddingsRedundantFilter
from langchain.retrievers.document_compressors import EmbeddingsFilter, DocumentCompressorPipeline
from langchain_text_splitters import TokenTextSplitter
from langchain_core.messages import HumanMessage, AIMessage
from langchain_community.chat_message_histories import ChatMessageHistory 
from langchain_core.callbacks import StdOutCallbackHandler, BaseCallbackHandler

# LangChain chat models
from langchain_openai import ChatOpenAI, AzureChatOpenAI
from langchain_google_vertexai import ChatVertexAI
from langchain_groq import ChatGroq
from langchain_anthropic import ChatAnthropic
from langchain_fireworks import ChatFireworks
from langchain_aws import ChatBedrock
from langchain_community.chat_models import ChatOllama

# Local imports
from src.llm import get_llm
from src.shared.common_fn import load_embedding_model
from src.shared.constants import *
from src.custom_neo4j_vector import CustomNeo4jVector
from src.intelligent_agent import IntelligentAgent
from src.alternative_agent import AlternativeAgent
from src.neo4j_retry import retry_neo4j_operation
load_dotenv()

from typing import Dict, List
import base64

# Neo4j ve langchain loglama seviyelerini ayarla
# DEBUG seviyesi çok ayrıntılı log üretir, gerekirse açabilirsiniz
# logging.getLogger("neo4j").setLevel(logging.DEBUG)
# logging.getLogger("langchain_neo4j").setLevel(logging.DEBUG)
# logging.getLogger("langchain.retrievers").setLevel(logging.DEBUG)

# Daha az gürültülü loglar için INFO seviyesi kullan
logging.getLogger("neo4j").setLevel(logging.INFO)
logging.getLogger("langchain_neo4j").setLevel(logging.INFO)
logging.getLogger("langchain.retrievers").setLevel(logging.INFO)

EMBEDDING_MODEL = os.getenv('EMBEDDING_MODEL')
EMBEDDING_FUNCTION , _ = load_embedding_model(EMBEDDING_MODEL) 

class SessionChatHistory:
    history_dict = {}

    @classmethod
    def get_chat_history(cls, session_id):
        """Retrieve or create chat message history for a given session ID."""
        if session_id not in cls.history_dict:
            logging.info(f"Creating new ChatMessageHistory Local for session ID: {session_id}")
            cls.history_dict[session_id] = ChatMessageHistory()
        else:
            logging.info(f"Retrieved existing ChatMessageHistory Local for session ID: {session_id}")
        return cls.history_dict[session_id]

class CustomCallback(BaseCallbackHandler):

    def __init__(self):
        self.transformed_question = None
    
    def on_llm_end(
        self,response, **kwargs: Any
    ) -> None:
        logging.info("question transformed")
        self.transformed_question = response.generations[0][0].text.strip()
        print(f"========== QUESTION TRANSFORMED ==========")
        print(f"Original to Transformed: {self.transformed_question}")
        print("=========================================")
        
    def on_retriever_start(self, serialized, query, **kwargs):
        print(f"========== RETRIEVER QUERY BAŞLADI ==========")
        print(f"Serialized: {serialized}")
        print(f"Query: {query}")
        print("============================================")
        
    def on_retriever_end(self, documents, **kwargs):
        print(f"========== RETRIEVER QUERY BİTTİ ==========")
        print(f"Retrieved {len(documents)} documents")
        if documents:
            print(f"First document preview: {documents[0].page_content[:100]}...")
        print("===========================================")

def is_casual_conversation(question, llm):
    """
    LLM kullanarak kullanıcının mesajının günlük konuşma mı 
    yoksa bilgi gerektiren bir soru mu olduğunu tespit eder.
    """
    try:
        casual_detection_prompt = ChatPromptTemplate.from_messages([
            ("human", CASUAL_CONVERSATION_DETECTION_TEMPLATE.format(user_message=question))
        ])
        
        chain = casual_detection_prompt | llm | StrOutputParser()
        result = chain.invoke({}).strip().upper()
        
        logging.info(f"Casual conversation detection result: {result} for question: '{question[:50]}...'")
        
        return result == "CASUAL"
        
    except Exception as e:
        logging.error(f"Error in casual conversation detection: {e}")
        # Hata durumunda güvenli tarafta kalıp normal işlemi yapalım
        return False

def get_history_by_session_id(session_id):
    try:
        return SessionChatHistory.get_chat_history(session_id)
    except Exception as e:
        logging.error(f"Failed to get history for session ID '{session_id}': {e}")
        raise

def get_total_tokens(ai_response, llm):
    try:
        if isinstance(llm, (ChatOpenAI, AzureChatOpenAI, ChatFireworks, ChatGroq)):
            total_tokens = ai_response.response_metadata.get('token_usage', {}).get('total_tokens', 0)
        
        elif isinstance(llm, ChatVertexAI):
            total_tokens = ai_response.response_metadata.get('usage_metadata', {}).get('prompt_token_count', 0)
        
        elif isinstance(llm, ChatBedrock):
            total_tokens = ai_response.response_metadata.get('usage', {}).get('total_tokens', 0)
        
        elif isinstance(llm, ChatAnthropic):
            input_tokens = int(ai_response.response_metadata.get('usage', {}).get('input_tokens', 0))
            output_tokens = int(ai_response.response_metadata.get('usage', {}).get('output_tokens', 0))
            total_tokens = input_tokens + output_tokens
        
        elif isinstance(llm, ChatOllama):
            total_tokens = ai_response.response_metadata.get("prompt_eval_count", 0)
        
        else:
            logging.warning(f"Unrecognized language model: {type(llm)}. Returning 0 tokens.")
            total_tokens = 0

    except Exception as e:
        logging.error(f"Error retrieving total tokens: {e}")
        total_tokens = 0

    return total_tokens

def clear_chat_history(graph, session_id,local=False):
    try:
        if not local:
            history = Neo4jChatMessageHistory(
                graph=graph,
                session_id=session_id
            )
        else:
            history = get_history_by_session_id(session_id)
        
        # Neo4j işlemini retry ile koru
        if not local:
            retry_neo4j_operation(lambda: history.clear())
        else:
            history.clear()

        return {
            "session_id": session_id, 
            "message": "The chat history has been cleared.", 
            "user": "chatbot"
        }
    
    except Exception as e:
        logging.error(f"Error clearing chat history for session {session_id}: {e}")
        return {
            "session_id": session_id, 
            "message": "Failed to clear chat history.", 
            "user": "chatbot"
        }

def get_sources_and_chunks(sources_used, docs):
    chunkdetails_list = []
    sources_used_set = set(sources_used)
    seen_ids_and_scores = set()  

    for doc in docs:
        try:
            source = doc.metadata.get("source")
            chunkdetails = doc.metadata.get("chunkdetails", [])

            if source in sources_used_set:
                for chunkdetail in chunkdetails:
                    id = chunkdetail.get("id")
                    score = round(chunkdetail.get("score", 0), 4)

                    id_and_score = (id, score)

                    if id_and_score not in seen_ids_and_scores:
                        seen_ids_and_scores.add(id_and_score)
                        chunkdetails_list.append({**chunkdetail, "score": score})

        except Exception as e:
            logging.error(f"Error processing document: {e}")

    # sources_used'ın list olduğundan emin ol (set ise list'e çevir)
    sources_list = list(sources_used) if isinstance(sources_used, set) else sources_used
    
    result = {
        'sources': sources_list,
        'chunkdetails': chunkdetails_list,
    }
    return result

def get_rag_chain(llm, system_template=CHAT_SYSTEM_TEMPLATE):
    try:
        question_answering_prompt = ChatPromptTemplate.from_messages(
            [
                ("system", system_template),
                MessagesPlaceholder(variable_name="messages"),
                (
                    "human",
                    "User question: {input}"
                ),
            ]
        )

        question_answering_chain = question_answering_prompt | llm

        return question_answering_chain

    except Exception as e:
        logging.error(f"Error creating RAG chain: {e}")
        raise

def format_documents(documents, model,chat_mode_settings):
    prompt_token_cutoff = 4
    for model_names, value in CHAT_TOKEN_CUT_OFF.items():
        if model in model_names:
            prompt_token_cutoff = value
            break

    sorted_documents = sorted(documents, key=lambda doc: doc.state.get("query_similarity_score", 0), reverse=True)
    sorted_documents = sorted_documents[:prompt_token_cutoff]

    formatted_docs = list()
    sources = set()
    entities = dict()
    global_communities = list()
    person_policy_info = []  # YENI: Person Policy Info için
    document_list = set()  # YENI: Ana document listesi için
    total_documents = 0  # YENI: Toplam document sayısı için

    for doc in sorted_documents:
        try:
            source = doc.metadata.get('source', "local file")
            sources.add(source)
            
            # YENI: personPolicyInfo metadata'sını topla
            if 'personPolicyInfo' in doc.metadata:
                for ppi in doc.metadata['personPolicyInfo']:
                    # Duplicate check
                    if not any(existing.get('person_id') == ppi.get('person_id') and 
                              existing.get('policy_id') == ppi.get('policy_id') for existing in person_policy_info):
                        person_policy_info.append(ppi)
            
            # YENI: documentList metadata'sını topla
            if 'documentList' in doc.metadata:
                document_list.update(doc.metadata['documentList'])
            
            # YENI: totalDocuments metadata'sını topla
            if 'totalDocuments' in doc.metadata:
                total_documents = max(total_documents, doc.metadata['totalDocuments'])
            
            if 'entities' in doc.metadata:
                if chat_mode_settings["mode"] == CHAT_ENTITY_VECTOR_MODE:
                    entity_ids = [entry['entityids'] for entry in doc.metadata['entities'] if 'entityids' in entry]
                    entities.setdefault('entityids', set()).update(entity_ids)
                else:
                    if 'entityids' in doc.metadata['entities']:
                        entities.setdefault('entityids', set()).update(doc.metadata['entities']['entityids'])
                    if 'relationshipids' in doc.metadata['entities']:
                        entities.setdefault('relationshipids', set()).update(doc.metadata['entities']['relationshipids'])
                
            if 'communitydetails' in doc.metadata:
                existing_ids = {entry['id'] for entry in global_communities}
                new_entries = [entry for entry in doc.metadata["communitydetails"] if entry['id'] not in existing_ids]
                global_communities.extend(new_entries)

            formatted_doc = (
                "Document start\n"
                f"This Document belongs to the source {source}\n"
                f"Content: {doc.page_content}\n"
                "Document end\n"
            )
            formatted_docs.append(formatted_doc)
        
        except Exception as e:
            logging.error(f"Error formatting document: {e}")
    
    # Set tipindeki verileri list'e çevir (JSON serialization için)
    sources_list = list(sources)
    document_list_final = list(document_list)
    entities_list = {}
    for key, value in entities.items():
        if isinstance(value, set):
            entities_list[key] = list(value)
        else:
            entities_list[key] = value
    
    # YENI: PersonPolicyInfo'yu entities'e ekle
    if person_policy_info:
        entities_list['personPolicyInfo'] = person_policy_info
    
    # YENI: DocumentList ve TotalDocuments'i entities'e ekle
    if document_list_final:
        entities_list['documentList'] = document_list_final
    if total_documents > 0:
        entities_list['totalDocuments'] = total_documents
    
    return "\n\n".join(formatted_docs), sources_list, entities_list, global_communities

def process_documents(docs, question, messages, llm, model,chat_mode_settings):
    start_time = time.time()
    
    try:
        formatted_docs, sources, entitydetails, communities = format_documents(docs, model,chat_mode_settings)
        
        print(f"========== FORMATTED DOCUMENTS ==========")
        print(f"Total Documents Formatted: {len(docs)}")
        print(f"Sources Found: {sources}")
        print(f"Entity Details: {entitydetails}")
        print(f"Communities: {communities}")
        print("--- FORMATTED CONTEXT FOR LLM ---")
        print(f"{formatted_docs}...")  # İlk 1000 karakteri göster
        print("--- END OF FORMATTED CONTEXT ---")
        print("==========================================")
        
        rag_chain = get_rag_chain(llm=llm)
        
        ai_response = rag_chain.invoke({
            "messages": messages[:-1],
            "context": formatted_docs,
            "input": question
        })

        result = {'sources': list(), 'nodedetails': dict(), 'entities': dict()}
        node_details = {"chunkdetails":list(),"entitydetails":list(),"communitydetails":list()}
        entities = {'entityids':list(),"relationshipids":list()}

        if chat_mode_settings["mode"] == CHAT_ENTITY_VECTOR_MODE:
            node_details["entitydetails"] = entitydetails

        elif chat_mode_settings["mode"] == CHAT_GLOBAL_VECTOR_FULLTEXT_MODE:
            node_details["communitydetails"] = communities
        else:
            sources_and_chunks = get_sources_and_chunks(sources, docs)
            result['sources'] = sources_and_chunks['sources']
            node_details["chunkdetails"] = sources_and_chunks["chunkdetails"]
            entities.update(entitydetails)

        result["nodedetails"] = node_details
        result["entities"] = entities

        content = ai_response.content
        total_tokens = get_total_tokens(ai_response, llm)
        
        predict_time = time.time() - start_time
        logging.info(f"Final response predicted in {predict_time:.2f} seconds")

    except Exception as e:
        logging.error(f"Error processing documents: {e}")
        raise
    
    return content, result, total_tokens, formatted_docs

def retrieve_documents(doc_retriever, messages, intelligent_agent: IntelligentAgent = None, alternative_agent: AlternativeAgent = None):

    start_time = time.time()
    agent_token_usage = None  # Agent token kullanımını saklamak için
    agent_result = None  # Agent sonuçlarını saklamak için
    
    # Import HumanMessage at function level to avoid scope issues
    from langchain_core.messages import HumanMessage
    
    try:
        # Son mesajı (kullanıcı sorusu) al
        user_question = messages[-1].content if messages else ""
        print(f"========== DOCUMENT RETRIEVAL BAŞLADI ==========")
        print(f"Original User Question: {user_question}")
        print(f"Message Count: {len(messages)}")
        print("==============================================")
        # Debug: Log tüm mesajları ve sadece HumanMessage'ları (transform için kullanılacak)
        # ÖNEMLI: Mesaj dizisinde eksik HumanMessage'lar olabilir. Frontend'den gelen mesaj history'si
        # tam değilse, session history'den önceki kullanıcı mesajlarını almanın yollarını araştırmalıyız.
        
        # Use a robust extractor: messages may be HumanMessage, dict, or objects with .content/.role
        from types import SimpleNamespace
        human_messages = []
        for m in messages:
            try:
                if isinstance(m, HumanMessage):
                    human_messages.append(m)
                    continue
                # object with content and optional role
                content = getattr(m, 'content', None)
                role = getattr(m, 'role', None) or getattr(m, 'type', None)
                if content and (role is None or str(role).lower() in ('user', 'human')):
                    human_messages.append(m)
                    continue
                # dict-like message
                if isinstance(m, dict):
                    content = m.get('content') or m.get('text') or m.get('message')
                    role = m.get('role') or m.get('type') or m.get('sender')
                    if content and (role is None or str(role).lower() in ('user', 'human')):
                        human_messages.append(SimpleNamespace(content=content))
                        continue
            except Exception:
                # ignore unparsable message types
                continue
        
        logging.info(f"DEBUG: Full messages count: {len(messages)}")
        for i, msg in enumerate(messages):
            logging.info(f"DEBUG: Message {i+1} ({type(msg).__name__}): {str(msg.content)[:300]}")
            print(f"DEBUG: Message {i+1} ({type(msg).__name__}): {str(msg.content)[:300]}")

        logging.info(f"DEBUG: Human messages count: {len(human_messages)}")
        for i, msg in enumerate(human_messages):
            logging.info(f"DEBUG: Human message {i+1}: {str(msg.content)[:300]}")
            print(f"DEBUG: Human message {i+1}: {str(msg.content)[:300]}")
            
        # UYARI: Eğer human_messages sadece 1 mesaj içeriyorsa (son soru), önceki context kayıp!
        if len(human_messages) <= 1:
            logging.warning(f"WARNING: Only {len(human_messages)} human message(s) found in history!")
            logging.warning("This means previous user questions are missing from the message history.")
            logging.warning("Context transformation may be incomplete. Check frontend message passing.")
            print(f"WARNING: Only {len(human_messages)} human message(s) found - previous context may be missing!")
            print("Frontend should send complete conversation history including all user messages.")
        
        handler = CustomCallback()

        # Eğer bir AlternativeAgent verilmişse, onu kullanarak dokümanları oluştur
        if alternative_agent:
            # Alternative Agent'tan sonuç al
            user_question = messages[-1].content if messages else ""
            
            # Eğer message history varsa (birden fazla mesaj), QUESTION_TRANSFORM uygula
            transformed_question = user_question  # Default: original soru
            
            # Let the LLM decide whether to transform the question. Use the last N human messages as context.
            transformed_question = user_question  # default
            try:
                from src.shared.constants import QUESTION_TRANSFORM_TEMPLATE
                from langchain.prompts import ChatPromptTemplate, MessagesPlaceholder
                from langchain_core.output_parsers import StrOutputParser
                from src.llm import get_llm

                # Debug: Tüm mesajları tiplerle birlikte logla
                logging.info(f"DEBUG: Total messages in history: {len(messages)}")
                for i, msg in enumerate(messages):
                    msg_type = type(msg).__name__
                    logging.info(f"DEBUG: Message {i+1} ({msg_type}): {str(msg.content)[:100]}...")
                    print(f"DEBUG: Message {i+1} ({msg_type}): {str(msg.content)[:100]}...")

                # SADECE kullanıcı mesajlarını filtrele (HumanMessage)
                # Re-run robust extractor in case message shapes differ here
                from types import SimpleNamespace
                human_messages = []
                for m in messages:
                    try:
                        if isinstance(m, HumanMessage):
                            human_messages.append(m)
                            continue
                        content = getattr(m, 'content', None)
                        role = getattr(m, 'role', None) or getattr(m, 'type', None)
                        if content and (role is None or str(role).lower() in ('user', 'human')):
                            human_messages.append(m)
                            continue
                        if isinstance(m, dict):
                            content = m.get('content') or m.get('text') or m.get('message')
                            role = m.get('role') or m.get('type') or m.get('sender')
                            if content and (role is None or str(role).lower() in ('user', 'human')):
                                human_messages.append(SimpleNamespace(content=content))
                                continue
                    except Exception:
                        continue
                logging.info(f"DEBUG: Human messages count: {len(human_messages)}")

                # En son N kullanıcı mesajını al (transformation için) - daha fazla bağlam için 10 kullan
                transform_window = 10
                last_human_messages = human_messages[-transform_window:] if len(human_messages) >= transform_window else human_messages

                logging.info(f"DEBUG: Using {len(last_human_messages)} messages for transform (window={transform_window}):")
                for i, msg in enumerate(last_human_messages):
                    logging.info(f"DEBUG: Transform input {i+1}: {msg.content}")
                    print(f"DEBUG: Transform input {i+1}: {msg.content}")

                # LLM ve transform prompt hazırla
                llm, _ = get_llm("openai_gpt_4.1")  # Model parametresi ekle
                query_transform_prompt = ChatPromptTemplate.from_messages([
                    ("system", QUESTION_TRANSFORM_TEMPLATE),
                    MessagesPlaceholder(variable_name="messages")
                ])
                output_parser = StrOutputParser()
                transform_chain = query_transform_prompt | llm | output_parser

                # Transform'a gönderilecek mesajları format et ve logla
                messages_for_llm = []
                for i, msg in enumerate(last_human_messages):
                    formatted_msg = f"Mesaj {i+1}: {msg.content}"
                    messages_for_llm.append(formatted_msg)
                    logging.info(f"DEBUG: LLM'e gönderilecek mesaj {i+1}: {msg.content}")
                    print(f"DEBUG: LLM'e gönderilecek mesaj {i+1}: {msg.content}")

                # Final prompt'u manuel olarak oluştur ve logla (sadece debug amaçlı)
                combined_messages = "\n".join(messages_for_llm)
                final_prompt = f"{QUESTION_TRANSFORM_TEMPLATE}\n\nMesajlar:\n{combined_messages}"
                logging.info(f"DEBUG: Final combined prompt that will be sent to LLM:")
                logging.info(f"DEBUG: {final_prompt}")
                print(f"DEBUG: Final combined prompt that will be sent to LLM:")
                print(f"DEBUG: {final_prompt}")

                # Transform işlemini yap - LLM'e bırakıyoruz; model gereksizse orijinali dönmelidir
                transformed_question = transform_chain.invoke({"messages": last_human_messages})
                transformed_question = transformed_question.strip()

                logging.info(f"AlternativeAgent TRANSFORM: Original: {user_question}")
                logging.info(f"AlternativeAgent TRANSFORM: Transformed: {transformed_question}")
                logging.info(f"AlternativeAgent TRANSFORM: Human message count: {len(last_human_messages)}")
                print(f"=== ALTERNATIVE AGENT QUESTION TRANSFORM ===")
                print(f"Original: {user_question}")
                print(f"Transformed: {transformed_question}")
                print(f"Last {len(last_human_messages)} human messages used (LLM responses excluded)")
                print("===============================================")

            except Exception as e:
                logging.error(f"AlternativeAgent transform failed: {e}")
                print(f"AlternativeAgent transform error: {e}")
                transformed_question = user_question
            
            # Transform edilmiş soruyu AlternativeAgent'a gönder
            alternative_result = alternative_agent.answer_question(transformed_question)

            # Alternative Agent response parsing
            if alternative_result and alternative_result.get('mode') == 'vector':
                logging.info(f"AlternativeAgent mode: {alternative_result['mode']}")
                print(f"AlternativeAgent mode: {alternative_result['mode']}")
                
                # AlternativeAgent'in tam sayfa formatını kullan (response_text)
                # Bu test'teki gibi tam sayfa içeriklerini içerir
                full_page_content = alternative_result.get('response_text', '')
                
                # Simple wrapper for expected document shape - tam sayfa içeriği ile
                class SimpleDoc:
                    def __init__(self, page_content, metadata, state=None):
                        self.page_content = page_content
                        self.metadata = metadata
                        self.state = state or {}

                docs = []
                chunks = alternative_result.get('meta', {}).get('chunks', [])
                
                # Eğer chunks varsa, tam sayfa içeriğini tek bir document olarak döndür
                if chunks:
                    # İlk chunk'tan document adını al
                    first_chunk = chunks[0]
                    doc_name = first_chunk.get('document', 'unknown')
                    
                    # Ortalama score hesapla
                    avg_score = sum(chunk.get('score', 0.0) for chunk in chunks) / len(chunks) if chunks else 0.0
                    
                    metadata = {
                        'source': doc_name,
                        'chunkdetails': [{
                            'id': f"{doc_name}::full_page_content",
                            'score': avg_score
                        }],
                        'alternative_agent_mode': alternative_result['mode'],
                        'alternative_agent_filters': alternative_result.get('meta', {}).get('filters'),
                        'total_chunks_found': len(chunks),
                        'chunk_sources': list(set(chunk.get('document', '') for chunk in chunks))
                    }

                    state = {'query_similarity_score': avg_score}
                    # Tam sayfa içeriğini (response_text) page_content olarak kullan
                    docs.append(SimpleDoc(page_content=full_page_content, metadata=metadata, state=state))

                final_question = transformed_question
                logging.info(f"AlternativeAgent vector search returned {len(docs)} documents (full page format)")
                print(f"AlternativeAgent vector search returned {len(docs)} documents (full page format)")
                
                # Alternative agent'in sonucunu agent_result'a ata (vector mode)
                agent_result = alternative_result
                
            else:
                # Count mode veya fallback - tam sayfa formatını kullan
                full_page_content = alternative_result.get('response_text', '')
                
                logging.info(f"AlternativeAgent mode: {alternative_result.get('mode', 'unknown')} - no vector search")
                print(f"AlternativeAgent mode: {alternative_result.get('mode', 'unknown')} - no vector search")
                
                # Simple wrapper for expected document shape
                class SimpleDoc:
                    def __init__(self, page_content, metadata, state=None):
                        self.page_content = page_content
                        self.metadata = metadata
                        self.state = state or {}
                
                # Count/fallback mode için de response_text içeriğini document olarak döndür
                docs = []
                if full_page_content.strip():  # Eğer içerik varsa
                    mode = alternative_result.get('mode', 'unknown')
                    metadata = {
                        'source': f'AlternativeAgent_{mode}_Result',
                        'chunkdetails': [{
                            'id': f'alternative_agent::{mode}',
                            'score': 1.0  # Count/fallback için sabit score
                        }],
                        'alternative_agent_mode': mode,
                        'alternative_agent_filters': alternative_result.get('meta', {}).get('filters'),
                        'document_count': alternative_result.get('meta', {}).get('count', 0),
                        'documents_found': alternative_result.get('meta', {}).get('documents', [])
                    }
                    
                    state = {'query_similarity_score': 1.0}
                    docs.append(SimpleDoc(page_content=full_page_content, metadata=metadata, state=state))
                    
                    logging.info(f"AlternativeAgent {mode} mode returned full content document")
                    print(f"AlternativeAgent {mode} mode returned full content document")
                
                final_question = transformed_question
                
                # Alternative agent'in cevabını metadata olarak sakla (count/fallback mode)
                agent_result = alternative_result
                
        # Eğer bir IntelligentAgent verilmişse, onu kullanarak dokümanları oluştur
        elif intelligent_agent:
            # Agent'tan sonuç al
            user_question = messages[-1].content if messages else ""
            agent_result = intelligent_agent.solve_question(user_question)

            # Agent token bilgilerini çıkar
            agent_token_usage = agent_result.get('token_usage') if isinstance(agent_result, dict) else None
            if agent_token_usage:
                logging.info(f"IntelligentAgent Token Usage - Input: {agent_token_usage.get('input_tokens', 0)}, Output: {agent_token_usage.get('output_tokens', 0)}, Total: {agent_token_usage.get('total_tokens', 0)}")
                print(f"IntelligentAgent Token Usage - Input: {agent_token_usage.get('input_tokens', 0)}, Output: {agent_token_usage.get('output_tokens', 0)}, Total: {agent_token_usage.get('total_tokens', 0)}")

            # Log agent result summary for debugging (avoid full dump to prevent huge outputs)
            try:
                keys = list(agent_result.keys()) if isinstance(agent_result, dict) else []
                logging.info(f"IntelligentAgent result keys: {keys}")
                print(f"IntelligentAgent result keys: {keys}")

                chunk_count = len(agent_result.get('chunk_details', [])) if isinstance(agent_result, dict) else 0
                logging.info(f"Agent chunk_details count: {chunk_count}")
                print(f"Agent chunk_details count: {chunk_count}")

                llm_prompt = agent_result.get('llm_prompt_structure') if isinstance(agent_result, dict) else None
                if llm_prompt:
                    logging.debug("Agent llm_prompt_structure (truncated): %s", llm_prompt[:1000])
                    print("Agent llm_prompt_structure (truncated):", llm_prompt[:1000])

                context_mem = agent_result.get('context_memory') if isinstance(agent_result, dict) else None
                if context_mem:
                    logging.debug("Agent context_memory (truncated): %s", context_mem[:500])
                    print("Agent context_memory (truncated):", context_mem[:500])

            except Exception as e:
                logging.exception(f"Error while logging agent_result: {e}")
                print(f"Error while logging agent_result: {e}")

            # Simple wrapper for expected document shape
            class SimpleDoc:
                def __init__(self, page_content, metadata, state=None):
                    self.page_content = page_content
                    self.metadata = metadata
                    self.state = state or {}

            docs = []
            transformed_question = None

            # agent_result contains 'chunk_details' and 'llm_prompt_structure' etc.
            for c in agent_result.get('chunk_details', []):
                doc_name = c.get('document', 'local')
                page = c.get('page')
                score = c.get('relevance', 0)
                preview = c.get('preview', '')

                chunk_id = f"{doc_name}::{page}"
                metadata = {
                    'source': doc_name,
                    'chunkdetails': [{
                        'id': chunk_id,
                        'score': score
                    }],
                    # Agent tarafından üretilen prompt/context bilgilerini ekle
                    'agent_prompt': agent_result.get('llm_prompt_structure', ''),
                    'agent_context': agent_result.get('context_memory', '')
                }

                state = {'query_similarity_score': score}
                docs.append(SimpleDoc(page_content=preview, metadata=metadata, state=state))

            # Eğer agent hiçbir chunk dönmediyse, fallback ile retriever çağrısı yap
            if not docs:
                docs = doc_retriever.invoke({"messages": messages},{"callbacks":[handler]})
                final_question = handler.transformed_question
            else:
                final_question = user_question
        else:
            docs = doc_retriever.invoke({"messages": messages},{"callbacks":[handler]})
            final_question = handler.transformed_question
        
        print(f"========== DOCUMENT RETRIEVAL SONUÇLARI ==========")
        # Transform edilen soruyu sadece loglara yaz, print'e çıkarma
        if final_question:
            logging.info(f"Transformed question : {final_question}")
        else:
            logging.info(f"Original question used (no transformation): {user_question}")
            
        print(f"Retrieved Documents Count: {len(docs) if docs else 0}")
        
        if docs:
            print("========== RETRIEVED DOCUMENTS DETAILS ==========")
            for i, doc in enumerate(docs[:3]):  # İlk 3 dokümanı göster
                print(f"Document {i+1}:")
                print(f"  Content: {doc.page_content[:150]}...")
                print(f"  Metadata: {doc.metadata}")
                print("-" * 50)
        
        print("==============================================")
        
        doc_retrieval_time = time.time() - start_time
        logging.info(f"Documents retrieved in {doc_retrieval_time:.2f} seconds")
        
    except Exception as e:
        error_message = f"Error retrieving documents: {str(e)}"
        print(f"========== DOCUMENT RETRIEVAL ERROR ==========")
        print(f"Error: {error_message}")
        print("============================================")
        logging.error(error_message)
        docs = None
        final_question = None

    
    return docs, final_question, agent_token_usage, agent_result

def create_document_retriever_chain(llm, retriever):
    try:
        logging.info("Starting to create document retriever chain")

        query_transform_prompt = ChatPromptTemplate.from_messages(
            [
                ("system", QUESTION_TRANSFORM_TEMPLATE),
                MessagesPlaceholder(variable_name="messages")
            ]
        )

        output_parser = StrOutputParser()

        splitter = TokenTextSplitter(chunk_size=CHAT_DOC_SPLIT_SIZE, chunk_overlap=0)
        embeddings_filter = EmbeddingsFilter(
            embeddings=EMBEDDING_FUNCTION,
            similarity_threshold=CHAT_EMBEDDING_FILTER_SCORE_THRESHOLD
        )

        pipeline_compressor = DocumentCompressorPipeline(
            transformers=[splitter, embeddings_filter]
        )

        compression_retriever = ContextualCompressionRetriever(
            base_compressor=pipeline_compressor, base_retriever=retriever
        )

        query_transforming_retriever_chain = RunnableBranch(
            (
                lambda x: len(x.get("messages", [])) == 1,
                (lambda x: x["messages"][-1].content) | compression_retriever,
            ),
            query_transform_prompt | llm | output_parser | compression_retriever,
        ).with_config(run_name="chat_retriever_chain")

        logging.info("Successfully created document retriever chain")
        return query_transforming_retriever_chain

    except Exception as e:
        logging.error(f"Error creating document retriever chain: {e}", exc_info=True)
        raise

def initialize_neo4j_vector(graph, chat_mode_settings, llm=None):
    try:
        retrieval_query = chat_mode_settings.get("retrieval_query")
        index_name = chat_mode_settings.get("index_name")
        keyword_index = chat_mode_settings.get("keyword_index", "")
        node_label = chat_mode_settings.get("node_label")
        embedding_node_property = chat_mode_settings.get("embedding_node_property")
        text_node_properties = chat_mode_settings.get("text_node_properties")

        print(f"========== CUSTOM NEO4J VECTOR INITIALIZATION ==========")
        print(f"Index Name: {index_name}")
        print(f"Node Label: {node_label}")
        print(f"Embedding Property: {embedding_node_property}")
        print(f"Text Properties: {text_node_properties}")
        print(f"Keyword Index: {keyword_index}")
        print(f"LLM Available: {llm is not None}")
        print(f"Retrieval Query (first 300 chars):")
        print(f"{retrieval_query[:300] if retrieval_query else 'None'}...")
        print("========================================================")

        if not retrieval_query or not index_name:
            raise ValueError("Required settings 'retrieval_query' or 'index_name' are missing.")

        # LLM varsa CustomNeo4jVector kullan, yoksa normal Neo4jVector
        if llm:
            print("========== USING CUSTOM NEO4J VECTOR WITH LLM ==========")
            if keyword_index:
                neo_db = CustomNeo4jVector.from_existing_graph_with_llm(
                    embedding=EMBEDDING_FUNCTION,
                    index_name=index_name,
                    retrieval_query=retrieval_query,
                    graph=graph,
                    llm=llm,
                    search_type="hybrid",
                    node_label=node_label,
                    embedding_node_property=embedding_node_property,
                    text_node_properties=text_node_properties,
                    keyword_index_name=keyword_index
                )
            else:
                neo_db = CustomNeo4jVector.from_existing_graph_with_llm(
                    embedding=EMBEDDING_FUNCTION,
                    index_name=index_name,
                    retrieval_query=retrieval_query,
                    graph=graph,
                    llm=llm,
                    node_label=node_label,
                    embedding_node_property=embedding_node_property,
                    text_node_properties=text_node_properties
                )
            print("========== CUSTOM NEO4J VECTOR CREATED SUCCESSFULLY ==========")
        else:
            print("========== USING STANDARD NEO4J VECTOR ==========")
            if keyword_index:
                neo_db = Neo4jVector.from_existing_graph(
                    embedding=EMBEDDING_FUNCTION,
                    index_name=index_name,
                    retrieval_query=retrieval_query,
                    graph=graph,
                    search_type="hybrid",
                    node_label=node_label,
                    embedding_node_property=embedding_node_property,
                    text_node_properties=text_node_properties,
                    keyword_index_name=keyword_index
                )
                print(f"========== NEO4J VECTOR HYBRID INDEX CREATED ==========")
                print(f"Index: {index_name}, Keyword Index: {keyword_index}")
                print("======================================================")
                logging.info(f"Successfully retrieved Neo4jVector Fulltext index '{index_name}' and keyword index '{keyword_index}'")
            else:
                neo_db = Neo4jVector.from_existing_graph(
                    embedding=EMBEDDING_FUNCTION,
                    index_name=index_name,
                    retrieval_query=retrieval_query,
                    graph=graph,
                    node_label=node_label,
                    embedding_node_property=embedding_node_property,
                    text_node_properties=text_node_properties
                )
                print(f"========== NEO4J VECTOR INDEX CREATED ==========")
                print(f"Index: {index_name}")
                print("===============================================")
                logging.info(f"Successfully retrieved Neo4jVector index '{index_name}'")
    except Exception as e:
        index_name = chat_mode_settings.get("index_name")
        print(f"========== NEO4J VECTOR INDEX ERROR ==========")
        print(f"Index Name: {index_name}")
        print(f"Error: {str(e)}")
        print("===============================================")
        logging.error(f"Error retrieving Neo4jVector index {index_name} : {e}")
        raise
    return neo_db

def create_retriever(neo_db, document_names, chat_mode_settings,search_k, score_threshold,ef_ratio):
    if document_names and chat_mode_settings["document_filter"]:
        search_kwargs = {
            'top_k': search_k,
            'effective_search_ratio': ef_ratio,
            'score_threshold': score_threshold,
            'filter': {'fileName': {'$in': document_names}}
        }
        retriever = neo_db.as_retriever(
            search_type="similarity_score_threshold",
            search_kwargs=search_kwargs
        )
        print(f"========== RETRIEVER CREATED WITH DOCUMENT FILTER ==========")
        print(f"Chat Mode: {chat_mode_settings.get('mode', 'N/A')}")
        print(f"Index Name: {chat_mode_settings.get('index_name', 'N/A')}")
        print(f"Node Label: {chat_mode_settings.get('node_label', 'N/A')}")
        print(f"Search Type: similarity_score_threshold")
        print(f"Search Kwargs: {search_kwargs}")
        print(f"Document Names Filter: {document_names}")
        print(f"Retrieval Query: {chat_mode_settings.get('retrieval_query', 'N/A')[:200]}...")
        print("============================================================")
        logging.info(f"Successfully created retriever with search_k={search_k}, score_threshold={score_threshold} for documents {document_names}")
    else:
        search_kwargs = {'top_k': search_k,'effective_search_ratio': ef_ratio, 'score_threshold': score_threshold}
        retriever = neo_db.as_retriever(
            search_type="similarity_score_threshold",
            search_kwargs=search_kwargs
        )
        print(f"========== RETRIEVER CREATED WITHOUT DOCUMENT FILTER ==========")
        print(f"Chat Mode: {chat_mode_settings.get('mode', 'N/A')}")
        print(f"Index Name: {chat_mode_settings.get('index_name', 'N/A')}")
        print(f"Node Label: {chat_mode_settings.get('node_label', 'N/A')}")
        print(f"Search Type: similarity_score_threshold")
        print(f"Search Kwargs: {search_kwargs}")
        print(f"Retrieval Query: {chat_mode_settings.get('retrieval_query', 'N/A')[:200]}...")
        print("===============================================================")
        logging.info(f"Successfully created retriever with search_k={search_k}, score_threshold={score_threshold}")
    return retriever

def get_neo4j_retriever(graph, document_names, chat_mode_settings, score_threshold=CHAT_SEARCH_KWARG_SCORE_THRESHOLD, llm=None):
    try:

        neo_db = initialize_neo4j_vector(graph, chat_mode_settings, llm)
        # document_names= list(map(str.strip, json.loads(document_names)))
        search_k = chat_mode_settings["top_k"]
        ef_ratio = int(os.getenv("EFFECTIVE_SEARCH_RATIO", "2")) if os.getenv("EFFECTIVE_SEARCH_RATIO", "2").isdigit() else 2
        retriever = create_retriever(neo_db, document_names,chat_mode_settings, search_k, score_threshold,ef_ratio)
        return retriever
    except Exception as e:
        index_name = chat_mode_settings.get("index_name")
        logging.error(f"Error retrieving Neo4jVector index  {index_name} or creating retriever: {e}")
        raise Exception(f"An error occurred while retrieving the Neo4jVector index or creating the retriever. Please drop and create a new vector index '{index_name}': {e}") from e 


def setup_chat(model, graph, document_names, chat_mode_settings):
    start_time = time.time()
    try:
        if model == "diffbot":
            model = os.getenv('DEFAULT_DIFFBOT_CHAT_MODEL')
        
        llm, model_name = get_llm(model=model)
        logging.info(f"Model called in chat: {model} (version: {model_name})")

        retriever = get_neo4j_retriever(graph=graph, chat_mode_settings=chat_mode_settings, document_names=document_names, llm=llm)
        doc_retriever = create_document_retriever_chain(llm, retriever)
        
        chat_setup_time = time.time() - start_time
        logging.info(f"Chat setup completed in {chat_setup_time:.2f} seconds")
        
    except Exception as e:
        logging.error(f"Error during chat setup: {e}", exc_info=True)
        raise
    
    return llm, doc_retriever, model_name

def process_chat_response(messages, history, question, model, graph, document_names, chat_mode_settings, intelligent_agent=None, alternative_agent=None):
    agent_token_usage = None  # Agent token kullanımını saklamak için
    agent_result = None  # Agent sonuçlarını saklamak için
    
    try:
        llm, doc_retriever, model_version = setup_chat(model, graph, document_names, chat_mode_settings)
        
        # Eğer agent parametre olarak gelmemişse, oluştur
        if intelligent_agent is None:
            try:
                intelligent_agent = IntelligentAgent(graph)
            except Exception:
                intelligent_agent = None
        
        # Eğer alternative_agent parametre olarak gelmemişse, oluştur
        if alternative_agent is None:
            try:
                alternative_agent = AlternativeAgent(graph)
            except Exception:
                alternative_agent = None
        
        # Günlük konuşma tespiti yap
        if is_casual_conversation(question, llm):
            logging.info(f"Casual conversation detected for question: '{question}'. Skipping document retrieval.")
            
            # Günlük konuşma için retriever kullanmadan direkt cevap ver
            rag_chain = get_rag_chain(llm=llm)
            
            # Boş bağlam ile cevap üret
            ai_response = rag_chain.invoke({
                "messages": messages[:-1],
                "context": "",  # Boş bağlam
                "input": question
            })
            
            content = ai_response.content
            total_tokens = get_total_tokens(ai_response, llm)
            
            # Boş result yapısı
            result = {
                'sources': [], 
                'nodedetails': {"chunkdetails": [], "entitydetails": [], "communitydetails": []}, 
                'entities': {'entityids': [], "relationshipids": [], 'personPolicyInfo': [], 'documentList': [], 'totalDocuments': 0}
            }
            formatted_docs = ""
            agent_token_usage = None  # Casual conversation'da agent kullanılmıyor
            
        else:
            # Normal işlem: document retrieval yap
            docs, transformed_question, agent_token_usage, agent_result = retrieve_documents(doc_retriever, messages, intelligent_agent=intelligent_agent, alternative_agent=alternative_agent)  

            # AlternativeAgent count mode sonuçlarını kontrol et
            alternative_context = ""
            if (agent_result and isinstance(agent_result, dict) and 
                agent_result.get('mode') in ['count', 'cypher_fallback'] and 
                agent_result.get('response_text')):
                
                # AlternativeAgent'in cevabını context olarak kullan
                alternative_context = f"AlternativeAgent Sonucu:\n{agent_result.get('response_text', '')}\n\n"
                logging.info(f"AlternativeAgent {agent_result.get('mode')} sonucu context olarak eklendi")
                
                # Docs boşsa bile RAG chain'e geçir
                if not docs:
                    # Boş docs ile devam et, context alternative_context'den gelecek
                    pass

            if docs:
                content, result, total_tokens, formatted_docs = process_documents(docs, question, messages, llm, model, chat_mode_settings)
                
                # AlternativeAgent context'ini formatted_docs'a ekle
                if alternative_context:
                    formatted_docs = alternative_context + formatted_docs
                    
            elif alternative_context:
                # Docs yok ama AlternativeAgent sonucu var, RAG chain'e context olarak ver
                rag_chain = get_rag_chain(llm=llm)
                
                ai_response = rag_chain.invoke({
                    "messages": messages[:-1],
                    "context": alternative_context,
                    "input": question
                })
                
                content = ai_response.content
                total_tokens = get_total_tokens(ai_response, llm)
                formatted_docs = alternative_context
                
                # Boş result yapısı ama agent bilgileriyle
                result = {
                    'sources': [], 
                    'nodedetails': {"chunkdetails": [], "entitydetails": [], "communitydetails": []}, 
                    'entities': {'entityids': [], "relationshipids": [], 'personPolicyInfo': [], 'documentList': [], 'totalDocuments': 0}
                }
                
            else:
                content = "Sorunuza cevap verebilecek ilgili doküman bulamadım."
                result = {"sources": list(), "nodedetails": list(), "entities": {'entityids': [], "relationshipids": [], 'personPolicyInfo': [], 'documentList': [], 'totalDocuments': 0}}
                total_tokens = 0
                formatted_docs = ""
        
        ai_response = AIMessage(content=content)
        messages.append(ai_response)
        
        # ÖNEMLI: AIMessage'ı session history'sine kaydet
        history.add_message(ai_response)

        summarization_thread = threading.Thread(target=summarize_and_log, args=(history, messages, llm))
        summarization_thread.start()
        logging.info("Summarization thread started.")
        # summarize_and_log(history, messages, llm)
        metric_details = {"question":question,"contexts":formatted_docs,"answer":content}
        
        # Agent token bilgilerini response'a ekle
        response_info = {
            # "metrics" : metrics,
            "sources": result["sources"],
            "model": model_version,
            "nodedetails": result["nodedetails"],
            "total_tokens": total_tokens,
            "response_time": 0,
            "mode": chat_mode_settings["mode"],
            "entities": result["entities"],
            "metric_details": metric_details,
            "personPolicyInfo": result["entities"].get('personPolicyInfo', []),  # YENI: PersonPolicyInfo ekle
            "documentList": result["entities"].get('documentList', []),  # YENI: DocumentList ekle
            "totalDocuments": result["entities"].get('totalDocuments', 0)  # YENI: TotalDocuments ekle
        }
        
        # IntelligentAgent token bilgilerini ekle
        if agent_token_usage:
            response_info["agent_token_usage"] = agent_token_usage
            response_info["agent_input_tokens"] = agent_token_usage.get('input_tokens', 0)
            response_info["agent_output_tokens"] = agent_token_usage.get('output_tokens', 0)
            response_info["agent_total_tokens"] = agent_token_usage.get('total_tokens', 0)
            logging.info(f"Response Agent Token Usage - Input: {agent_token_usage.get('input_tokens', 0)}, Output: {agent_token_usage.get('output_tokens', 0)}, Total: {agent_token_usage.get('total_tokens', 0)}")
        
        # IntelligentAgent ve AlternativeAgent bilgilerini ekle
        if agent_result:
            # Hem IntelligentAgent hem de AlternativeAgent için uyumlu bilgiler
            if agent_result.get('mode') in ['count', 'cypher_fallback', 'vector']:
                # AlternativeAgent sonucu
                response_info["alternative_agent_mode"] = agent_result.get('mode')
                response_info["alternative_agent_meta"] = agent_result.get('meta', {})
                response_info["alternative_agent_response"] = agent_result.get('response_text', '')
                
                # AlternativeAgent'tan gelen chunk bilgileri varsa ekle
                if agent_result.get('meta', {}).get('chunks'):
                    response_info["agent_chunk_details"] = agent_result.get('meta', {}).get('chunks', [])
                
                # AlternativeAgent entities (eğer varsa)
                if agent_result.get('meta', {}).get('filters'):
                    response_info["alternative_agent_filters"] = agent_result.get('meta', {}).get('filters')
                
                logging.info(f"AlternativeAgent Found - Mode: {agent_result.get('mode')}, Meta: {len(str(agent_result.get('meta', {})))}")
                
            else:
                # IntelligentAgent sonucu
                response_info["agent_chunk_details"] = agent_result.get('chunk_details', [])
                response_info["agent_entity_details"] = agent_result.get('entity_details', [])
                response_info["agent_discovered_entities"] = agent_result.get('discovered_entities', 0)
                response_info["agent_discovered_chunks"] = agent_result.get('discovered_chunks', 0)
                response_info["agent_iterations"] = agent_result.get('iterations', 0)
                
                logging.info(f"IntelligentAgent Found - Entities: {agent_result.get('discovered_entities', 0)}, Chunks: {agent_result.get('discovered_chunks', 0)}, Iterations: {agent_result.get('iterations', 0)}")
            
            # Agent entity detaylarını entities formatına çevir
            if agent_result.get('entity_details', []):
                entity_ids = [entity['id'] for entity in agent_result.get('entity_details', [])]
                response_info["entities"] = entity_ids
        
        return {
            "session_id": "",  
            "message": content,
            "info": response_info,
            "user": "chatbot"
        }
    
    except Exception as e:
        logging.exception(f"Error processing chat response at {datetime.now()}: {str(e)}")
        return {
            "session_id": "",
            "message": "Bir şeyler ters gitti",
            "info": {
                "metrics" : [],
                "sources": [],
                "nodedetails": [],
                "total_tokens": 0,
                "response_time": 0,
                "error": f"{type(e).__name__}: {str(e)}",
                "mode": chat_mode_settings["mode"],
                "entities": [],
                "metric_details": {},
            },
            "user": "chatbot"
        }
        
def summarize_and_log(history, stored_messages, llm):
    logging.info("Starting summarization in a separate thread.")
    if not stored_messages:
        logging.info("No messages to summarize.")
        return False

    try:
        start_time = time.time()
        total_len = len(stored_messages)
        keep_last = 15
        logging.info(f"stored_messages length: {len(stored_messages)}")
        logging.info(f"stored_messages: {stored_messages}")

        # Hazırlanacak mesajları önceden belirle
        messages_to_add = []
        
        if total_len > keep_last:
            # 1. Yavaş olan özetleme işlemini kilidin DIŞINDA yap
            to_summarize = stored_messages[: total_len - keep_last]
            remaining = stored_messages[total_len - keep_last :]

            summarization_prompt = ChatPromptTemplate.from_messages(
                [
                    MessagesPlaceholder(variable_name="chat_history"),
                    (
                        "human",
                        "Yukarıdaki chat mesajlarını temel noktalara ve gelecekteki konuşmalar için faydalı olabilecek ilgili detaylara odaklanarak kısa bir özet halinde özetleyin. Tüm giriş ve gereksiz bilgileri hariç tutun."
                    ),
                ]
            )
            summarization_chain = summarization_prompt | llm
            summary_message = summarization_chain.invoke({"chat_history": to_summarize})

            # Eklenecek mesaj listesini hazırla
            messages_to_add = [
                summary_message,
                *remaining
            ]
        else:
            # Özetlemeye gerek yoksa tüm mesajları kullan
            messages_to_add = stored_messages

        with threading.Lock():
            def safe_history_update():
                """Neo4j işlemlerini güvenli şekilde yap"""
                retry_neo4j_operation(lambda: history.clear())
                print("messages_to_add: ", messages_to_add)
                for msg in messages_to_add:
                    retry_neo4j_operation(lambda: history.add_message(msg))
            
            # Neo4j işlemlerini retry ile koru
            try:
                safe_history_update()
            except Exception as neo4j_error:
                logging.error(f"Neo4j connection error in summarization: {neo4j_error}")
                # Neo4j hatası durumunda sessizce devam et, chat devam etsin
                return False

        history_summarized_time = time.time() - start_time
        logging.info(f"Chat History summarized in {history_summarized_time:.2f} seconds")

        return True

    except Exception as e:
        logging.error(f"An error occurred while summarizing messages: {e}", exc_info=True)
        return False 

# def summarize_and_log(history, stored_messages, llm):
#     logging.info("Starting summarization in a separate thread.")
#     if not stored_messages:
#         logging.info("No messages to summarize.")
#         return False

#     try:
#         start_time = time.time()

#         summarization_prompt = ChatPromptTemplate.from_messages(
#             [
#                 MessagesPlaceholder(variable_name="chat_history"),
#                 (
#                     "human",
#                     "Yukarıdaki chat mesajlarını temel noktalara ve gelecekteki konuşmalar için faydalı olabilecek ilgili detaylara odaklanarak kısa bir özet halinde özetleyin. Tüm giriş ve gereksiz bilgileri hariç tutun."
#                 ),
#             ]
#         )
#         summarization_chain = summarization_prompt | llm

#         summary_message = summarization_chain.invoke({"chat_history": stored_messages})

#         with threading.Lock():
#             # ÖNEMLI: History'yi tamamen temizlemek yerine, sadece AIMessage'ları özetleyelim
#             # HumanMessage'ları koruyarak context transformation'ın çalışmasını sağlayalım
            
#             # Mevcut HumanMessage'ları sakla
#             from langchain_core.messages import HumanMessage, AIMessage
#             human_messages = [msg for msg in history.messages if isinstance(msg, HumanMessage)]
            
#             # History'yi temizle
#             history.clear()
            
#             # HumanMessage'ları geri ekle
#             for human_msg in human_messages:
#                 history.add_message(human_msg)
            
#             # Özet AIMessage'ı ekle
#             try:
#                 # summary_message may be a string or an object with .content
#                 if hasattr(summary_message, 'content') and getattr(summary_message, 'content'):
#                     ai_content = summary_message.content
#                 else:
#                     ai_content = str(summary_message)

#                 ai_msg = AIMessage(content=ai_content)
#                 history.add_message(ai_msg)
#             except Exception:
#                 # Fallback: if constructing AIMessage fails, store raw summary_message
#                 history.add_message(summary_message)

#         history_summarized_time = time.time() - start_time
#         logging.info(f"Chat History summarized in {history_summarized_time:.2f} seconds")

#         return True

#     except Exception as e:
#         logging.error(f"An error occurred while summarizing messages: {e}", exc_info=True)
#         return False 
    
def create_graph_chain(model, graph):
    try:
        logging.info(f"Graph QA Chain using LLM model: {model}")

        cypher_llm,model_name = get_llm(model)
        qa_llm,model_name = get_llm(model)
        graph_chain = GraphCypherQAChain.from_llm(
            cypher_llm=cypher_llm,
            qa_llm=qa_llm,
            validate_cypher= True,
            graph=graph,
            # verbose=True, 
            allow_dangerous_requests=True,
            return_intermediate_steps = True,
            top_k=3
        )

        logging.info("GraphCypherQAChain instance created successfully.")
        return graph_chain,qa_llm,model_name

    except Exception as e:
        logging.error(f"An error occurred while creating the GraphCypherQAChain instance. : {e}") 

def get_graph_response(graph_chain, question):
    try:
        cypher_res = graph_chain.invoke({"query": question})
        
        response = cypher_res.get("result")
        cypher_query = ""
        context = []

        for step in cypher_res.get("intermediate_steps", []):
            if "query" in step:
                cypher_string = step["query"]
                cypher_query = cypher_string.replace("cypher\n", "").replace("\n", " ").strip() 
            elif "context" in step:
                context = step["context"]
        return {
            "response": response,
            "cypher_query": cypher_query,
            "context": context
        }
    
    except Exception as e:
        logging.error(f"An error occurred while getting the graph response : {e}")

def process_graph_response(model, graph, question, messages, history):
    try:
        graph_chain, qa_llm, model_version = create_graph_chain(model, graph)
        
        graph_response = get_graph_response(graph_chain, question)
        
        ai_response_content = graph_response.get("response", "Bir şeyler ters gitti")
        ai_response = AIMessage(content=ai_response_content)
        
        messages.append(ai_response)
        # summarize_and_log(history, messages, qa_llm)
        summarization_thread = threading.Thread(target=summarize_and_log, args=(history, messages, qa_llm))
        summarization_thread.start()
        logging.info("Summarization thread started.")
        metric_details = {"question":question,"contexts":graph_response.get("context", ""),"answer":ai_response_content}
        result = {
            "session_id": "", 
            "message": ai_response_content,
            "info": {
                "model": model_version,
                "cypher_query": graph_response.get("cypher_query", ""),
                "context": graph_response.get("context", ""),
                "mode": "graph",
                "response_time": 0,
                "metric_details": metric_details,
            },
            "user": "chatbot"
        }
        
        return result
    
    except Exception as e:
        logging.exception(f"Error processing graph response at {datetime.now()}: {str(e)}")
        return {
            "session_id": "",  
            "message": "Bir şeyler ters gitti",
            "info": {
                "model": model_version,
                "cypher_query": "",
                "context": "",
                "mode": "graph",
                "response_time": 0,
                "error": f"{type(e).__name__}: {str(e)}"
            },
            "user": "chatbot"
        }

def create_neo4j_chat_message_history(graph, session_id, write_access=True):
    """
    Creates and returns a Neo4jChatMessageHistory instance.

    """
    try:
        if write_access: 
            history = Neo4jChatMessageHistory(
                graph=graph,
                session_id=session_id
            )
            return history
        
        history = get_history_by_session_id(session_id)
        return history

    except Exception as e:
        logging.error(f"Error creating Neo4jChatMessageHistory: {e}")
        raise 

def get_chat_mode_settings(mode,settings_map=CHAT_MODE_CONFIG_MAP):
    default_settings = settings_map[CHAT_DEFAULT_MODE]
    try:
        chat_mode_settings = settings_map.get(mode, default_settings)
        chat_mode_settings["mode"] = mode
        
        # print(f"========== CHAT MODE SETTINGS ==========")
        # print(f"Requested Mode: {mode}")
        # print(f"Default Mode: {CHAT_DEFAULT_MODE}")
        # print(f"Selected Settings:")
        # for key, value in chat_mode_settings.items():
        #     if key == "retrieval_query":
        #         print(f"  {key}: {str(value) if value else 'None'}...")
        #     else:
        #         print(f"  {key}: {value}")
        # print("=======================================")
        
        # logging.info(f"Chat mode settings: {chat_mode_settings}")
    
    except Exception as e:
        logging.error(f"Unexpected error: {e}", exc_info=True)
        raise

    return chat_mode_settings

async def analyze_single_file(vision_llm, file_info: Dict[str, str]) -> str:
    """
    Tek bir görseli GPT-4o ile analiz eder ve string döner.
    """
    base64_str = file_info["base64"]
    if "," not in base64_str:
        base64_str = "data:image/png;base64," + base64_str

    try:
        logging.info(f"{file_info['fileName']} belgesinin detayı çıkartılıyor.")
        # Prompt Template
        analyze_prompt = ChatPromptTemplate.from_messages([
            HumanMessage(content=f"'{file_info['fileName']}' adlı görselin içeriğini detaylı olarak açıkla."),
            HumanMessagePromptTemplate.from_template(
                [{'image_url': {"url": base64_str}}]
            )
            # (
            #     "human",
            #     f"'{file_info['fileName']}' adlı görselin içeriğini detaylı olarak açıkla."
            # ),
            # (
            #     "human",
            #     {"type": "image_url", "image_url": {"url": base64_str}}
            # )
        ])

        chain = analyze_prompt | vision_llm

        resp = await chain.ainvoke({})

        total_tokens = get_total_tokens(resp, vision_llm)
        logging.info(f"{file_info['fileName']} için total_tokens {total_tokens}")

        description = resp.content.strip()
        return f"[{file_info['fileName']}]: {description}"

    except Exception as e:
        logging.exception(f"Görsel analizi başarısız ({file_info['fileName']}): {str(e)}")
        return f"[{file_info['fileName']}]: Analiz başarısız."

async def analyze_files_with_llm(files: Dict[str, List[Dict[str, str]]], model, question, history, messages):
    """
    Tüm görselleri paralel olarak analiz eder ve tek string döner.
    """
    try:
        start_time = time.time()
        qa_llm, model_name = get_llm(model)

        # vision_llm = ChatOpenAI(
        #     model="gpt-4.1",
        #     streaming=False
        # )

        yield {
            "type": "message_chunk",
            "content": "Belge analiz ediliyor..\n" + " ",
            "full_message": "Belge analiz ediliyor..",
            "is_complete": False,
            "user": "chatbot"
        }

        # Paralel analiz
        tasks = [
            analyze_single_file(qa_llm, file_info)
            for category, file_list in files.items()
            for file_info in file_list
        ]
        results = await asyncio.gather(*tasks)
        ai_response_content = "\n".join(results)

        # Prompt Template ile özet/cevap üretme
        analyze_prompt = ChatPromptTemplate.from_messages([
            (
                "human",
                question if question else "Bu bir belgenin içeriğidir. Belgenin içerigi hakkında kullanıcıya özet ver."
            ),
            ("human", ai_response_content)
        ])

        chain = analyze_prompt | qa_llm
        resp = await chain.ainvoke({})

        total_tokens = get_total_tokens(resp, qa_llm)
        # logging.info(f"{file_info['fileName']} için total_tokens {total_tokens}")

        ai_response_content2 = resp.content.strip()

        # Streaming efekti - newline karakterlerini koruyarak
        # Metni kelimeler ve newline karakterlerine göre böl
        tokens = re.findall(r'\S+|\n+', ai_response_content2)
        streamed_content = ""
        
        for i, token in enumerate(tokens):
            if token.startswith('\n'):
                # Newline karakterleri için
                streamed_content += token
                yield {
                    "type": "message_chunk",
                    "content": token,
                    "full_message": streamed_content,
                    "is_complete": i == len(tokens) - 1,
                    "user": "chatbot"
                }
            else:
                # Normal kelimeler için
                streamed_content += token + " "
                yield {
                    "type": "message_chunk",
                    "content": token + " ",
                    "full_message": streamed_content.rstrip(),
                    "is_complete": i == len(tokens) - 1,
                    "user": "chatbot"
                }
            await asyncio.sleep(0.05)

        # Mesajları history'e kaydet
        ai_response = AIMessage(content=ai_response_content)
        messages.append(ai_response)

        ai_response2 = AIMessage(content=ai_response_content2)
        messages.append(ai_response2)

        # Background summarization
        summarization_future = asyncio.get_event_loop().run_in_executor(
            None, summarize_and_log, history, messages, qa_llm
        )
        logging.info(f"Summarization task started: {summarization_future}")

        analyze_files_with_llm_time = time.time() - start_time
        logging.info(f"Files analyzed processed in {analyze_files_with_llm_time:.2f} seconds")

    except Exception as e:
        logging.exception(f"Error in analyze_files_with_llm: {str(e)}")
        yield {
            "type": "error",
            "message": "Bir hata oluştu",
            "error": str(e),
            "user": "chatbot"
        }

# async def analyze_files_together(files: Dict[str, List[Dict[str, str]]]) -> str:
#     """
#     Tüm görselleri tek bir GPT-4o çağrısıyla analiz eder.
#     """
#     vision_llm = ChatOpenAI(
#         model="gpt-4o",
#         temperature=0,
#         streaming=False
#     )

#     # Tüm görselleri tek prompt içinde birleştir
#     prompt_lines = []
#     for category, file_list in files.items():
#         for file_info in file_list:
#             base64_str = file_info["base64"]
#             if "," not in base64_str:
#                 base64_str = "data:image/png;base64," + base64_str
#             prompt_lines.append(f"{file_info['fileName']}: {base64_str}")

#     prompt_text = "Aşağıdaki tüm görselleri detaylı olarak inceleyip metin olarak açıkla:\n\n" + "\n\n".join(prompt_lines)

#     resp = vision_llm.invoke([HumanMessage(content=prompt_text)])
#     return resp.content.strip()


# async def analyze_single_file(vision_llm, file_info: Dict[str, str]) -> str:
#     """
#     Tek bir görseli GPT-4o ile analiz eder ve string döner.
#     """
#     base64_str = file_info["base64"]
#     if "," not in base64_str:
#         base64_str = "data:image/png;base64," + base64_str

#     try:
#         resp = await vision_llm.ainvoke([   # <-- async versiyon
#             HumanMessage(content=[
#                 {"type": "text", "text": f"'{file_info['fileName']}' adlı görselin içeriğini detaylı olarak açıkla."},
#                 {
#                     "type": "image_url",
#                     "image_url": {
#                         "url": base64_str
#                     }
#                 }
#             ])
#         ])
#         description = resp.content.strip()
#         return f"[{file_info['fileName']}]: {description}"
#     except Exception as e:
#         logging.exception(f"Görsel analizi başarısız ({file_info['fileName']}): {str(e)}")
#         return f"[{file_info['fileName']}]: Analiz başarısız."

# async def analyze_files_with_llm(files: Dict[str, List[Dict[str, str]]], messages, history, model) -> str:
#     """
#     Tüm görselleri paralel olarak analiz eder ve tek string döner.
#     """
#     try:
#         vision_llm = ChatOpenAI(
#             model="gpt-4.1",
#             # temperature=0,
#             streaming=False
#         )

#         tasks = [
#             analyze_single_file(vision_llm, file_info)
#             for category, file_list in files.items()
#             for file_info in file_list
#         ]

#         results = await asyncio.gather(*tasks)

#         ai_response_content = "\n".join(results)

#         resp = vision_llm.invoke([   # <-- async versiyon
#             HumanMessage(content=[
#                 {"type": "text", "text": "Bu bir belgenin içeriğidir. Belgenin içerigi hakkında kullanıcıya özet ver."},
#                 {
#                     "type": "text",
#                     "text": f"{ai_response_content}"
#                 }
#             ])
#         ])

#         ai_response_content2 = resp.content.strip()

#         words = ai_response_content2.split()
#         streamed_content = ""

#         for i, word in enumerate(words):
#             streamed_content += word + " "
            
#             yield {
#                 "type": "message_chunk",
#                 "content": word + " ",
#                 "full_message": streamed_content.strip(),
#                 "is_complete": i == len(words) - 1,
#                 "user": "chatbot"
#             }
            
#             # Gerçekçi streaming efekti
#             await asyncio.sleep(0.05)

#         ai_response = AIMessage(content=ai_response_content)
#         messages.append(ai_response)

#         ai_response2 = AIMessage(content=ai_response_content2)
#         messages.append(ai_response2)

#         qa_llm,model_name = get_llm(model)

#         # Background summarization
#         summarization_future = asyncio.get_event_loop().run_in_executor(
#             None, summarize_and_log, history, messages, qa_llm
#         )
#         logging.info(f"Graph summarization task started: {summarization_future}")

#         # return "\n".join(results)

#     except Exception as e:
#         logging.exception(f"Error in analyze_files_with_llm: {str(e)}")
#         yield {
#             "type": "error",
#             # "session_id": session_id,
#             "message": "Bir hata oluştu",
#             "error": str(e),
#             "user": "chatbot"
#         }

    
def QA_RAG(graph,model, question, document_names, session_id, mode, write_access=True, intelligent_agent=None, alternative_agent=None):
    logging.info(f"Chat Mode: {mode}")

    history = create_neo4j_chat_message_history(graph, session_id, write_access)
    messages = history.messages

    user_question = HumanMessage(content=question)
    messages.append(user_question)
    
    # ÖNEMLI: HumanMessage'ı session history'sine kaydet
    history.add_message(user_question)

    if mode == CHAT_GRAPH_MODE:
        result = process_graph_response(model, graph, question, messages, history)
    else:
        chat_mode_settings = get_chat_mode_settings(mode=mode)
        document_names= list(map(str.strip, json.loads(document_names)))
        if document_names and not chat_mode_settings["document_filter"]:
            result =  {
              "session_id": "",  
              "message": "Lütfen bu sohbet modunu kullanmadan önce tablodaki tüm dokümanların seçimini kaldırın.",
              "info": {
                "sources": [],
                "model": "",
                "nodedetails": [],
                "total_tokens": 0,
                "response_time": 0,
                "mode": chat_mode_settings["mode"],
                "entities": [],
                "metric_details": [],
              },
              "user": "chatbot"
            }
        else:
            result = process_chat_response(messages,history, question, model, graph, document_names,chat_mode_settings, intelligent_agent=intelligent_agent, alternative_agent=alternative_agent)

    result["session_id"] = session_id
    
    return result

async def convert_files_to_markdown(files: Dict[str, List[Dict[str, str]]], model=None) -> str:
    """
    Dosyaları Docling ile markdown formatına çevirir. Hata durumunda PDF sayfalarını PNG'ye çevirip 
    analyze_single_file fonksiyonu ile LLM vision analizi yapar.
    Çıktısı belgelerin markdown formatında sayfalar arası page_break ile birleştirilmiş hali.
    
    files dict formatı:
    - "path" alanı varsa: local dosya path'i kullanılır
    - "url" alanı varsa: URL'den dosya indirilir (eski davranış)
    """
    try:
        import tempfile
        import requests
        import base64
        import re
        import subprocess
        from pathlib import Path
        from langchain_docling import DoclingLoader
        from langchain_docling.loader import ExportType
        from docling_core.types.doc import DocItemLabel
        from docling_core.types.doc.document import DEFAULT_EXPORT_LABELS
        from urllib.parse import urlparse
        from pdf2image import convert_from_path

        all_documents_content = []

        for category, file_list in files.items():
            for file_info in file_list:
                try:
                    file_name = file_info["fileName"]
                    file_path = file_info.get("path") or file_info.get("filePath") or file_info.get("file_path")
                    file_url = file_info.get("url") or file_info.get("fileUrl") or file_info.get("file_url")
                    
                    # Local path varsa onu kullan, yoksa URL'den indir
                    if file_path:
                        # Local dosya işlemi
                        if not os.path.exists(file_path):
                            error_content = f"## Belge: {file_name}\n\n**[HATA]** Dosya bulunamadı: {file_path}\n\n"
                            all_documents_content.append(error_content)
                            continue
                        
                        logging.info(f"{file_name} belgesi local path'den okunuyor: {file_path}")
                        
                        try:
                            # Docling ile belgeyi yükle
                            labels = [
                                label
                                for label in DEFAULT_EXPORT_LABELS
                                if label not in (DocItemLabel.PICTURE, DocItemLabel.PAGE_FOOTER)
                            ]
                            
                            loader = DoclingLoader(
                                file_path=file_path,
                                export_type=ExportType.MARKDOWN,
                                md_export_kwargs={
                                    "page_break_placeholder": "\n\n--- PAGE_BREAK ---\n\n",
                                    "labels": labels,
                                },
                            )
                            
                            # Timeout ile belgeyi yükle
                            import concurrent.futures
                            
                            def load_document_with_timeout():
                                return loader.load()
                            
                            # 60 saniye timeout ile executor kullan
                            with concurrent.futures.ThreadPoolExecutor() as executor:
                                future = executor.submit(load_document_with_timeout)
                                try:
                                    documents = future.result(timeout=180)  # 2 dakikaya çıkardık
                                except concurrent.futures.TimeoutError:
                                    raise TimeoutError(f"Docling processing timeout for {file_name}")
                            
                            # Sayfa numaralarını ekleyerek içeriği birleştir
                            document_content = f"## Belge: {file_name}\n**Path:** {file_path}\n**İşlem:** Docling markdown extraction\n\n"
                            
                            for doc in documents:
                                content = doc.page_content
                                
                                # Page break'leri sayfa numarası ile değiştir
                                pages = content.split("--- PAGE_BREAK ---")
                                
                                formatted_pages = []
                                for page_num, page_content in enumerate(pages, 1):
                                    if page_content.strip():  # Boş sayfaları atla
                                        formatted_page = f"**[Sayfa {page_num}]**\n\n{page_content.strip()}\n\n"
                                        formatted_pages.append(formatted_page)
                                
                                document_content += "\n".join(formatted_pages)
                            
                            all_documents_content.append(document_content)
                            
                            logging.info(f"{file_name} belgesi Docling ile başarıyla işlendi. Sayfa sayısı: {len(pages)}")
                            
                        except Exception as docling_error:
                            # Docling hatası durumunda önce LibreOffice ile yeniden oluşturmayı dene
                            logging.warning(f"{file_name} belgesi için Docling hatası: {str(docling_error)}")
                            
                            # Dosya uzantısını kontrol et
                            file_extension = Path(file_path).suffix.lower()
                            
                            # LibreOffice ile yeniden oluşturma deneme
                            libreoffice_success = False
                            converted_pdf_path = None
                            
                            try:
                                logging.info(f"{file_name} dosyası LibreOffice ile yeniden oluşturuluyor...")
                                
                                # Geçici PDF klasörü oluştur
                                temp_pdf_dir = os.path.join(tempfile.gettempdir(), "llm_graph_pdf_temp")
                                os.makedirs(temp_pdf_dir, exist_ok=True)
                                
                                filename_without_ext = Path(file_name).stem
                                safe_filename = re.sub(r'[^\w\-_\.]', '_', filename_without_ext)
                                pdf_output_path = os.path.join(temp_pdf_dir, safe_filename + "_libreoffice.pdf")
                                
                                # LibreOffice convert komutu
                                subprocess.run([
                                    "libreoffice", "--headless", "--convert-to", "pdf", 
                                    "--outdir", temp_pdf_dir, file_path
                                ], check=True, timeout=60)
                                
                                # Dönüştürülen PDF dosyası path'ini kontrol et
                                converted_pdf_path = pdf_output_path
                                if not os.path.exists(pdf_output_path):
                                    # LibreOffice bazen farklı isimle kaydediyor, kontrol et
                                    original_filename = Path(file_path).stem
                                    alternative_pdf_path = os.path.join(temp_pdf_dir, original_filename + ".pdf")
                                    if os.path.exists(alternative_pdf_path):
                                        converted_pdf_path = alternative_pdf_path
                                    else:
                                        raise FileNotFoundError(f"PDF dönüştürme başarısız: {pdf_output_path}")
                                
                                logging.info(f"{file_name} başarıyla LibreOffice ile PDF'e dönüştürüldü: {converted_pdf_path}")
                                
                                # LibreOffice ile oluşan PDF'i Docling ile tekrar dene
                                logging.info(f"{file_name} belgesi LibreOffice PDF'i ile Docling yeniden deneniyor...")
                                
                                # Dosya sisteminin stabilize olması için kısa bir bekleme
                                import time
                                time.sleep(1)
                                
                                try:
                                    # Daha basit bir Docling konfigürasyonu kullan
                                    simple_labels = [
                                        DocItemLabel.TEXT,
                                        DocItemLabel.TITLE,
                                        DocItemLabel.SECTION_HEADER
                                    ]
                                    
                                    loader_retry = DoclingLoader(
                                        file_path=converted_pdf_path,
                                        export_type=ExportType.MARKDOWN,
                                        md_export_kwargs={
                                            "page_break_placeholder": "\n\n--- PAGE_BREAK ---\n\n",
                                            "labels": simple_labels,  # Sadece temel etiketler
                                        },
                                    )
                                    
                                    # Timeout ile belgeyi yükle
                                    def load_document_with_timeout_retry():
                                        return loader_retry.load()
                                    
                                    with concurrent.futures.ThreadPoolExecutor() as executor:
                                        future = executor.submit(load_document_with_timeout_retry)
                                        try:
                                            documents_retry = future.result(timeout=180)  # 3 dakikaya çıkardık
                                        except concurrent.futures.TimeoutError:
                                            raise TimeoutError(f"Docling processing timeout for LibreOffice PDF: {file_name}")
                                    
                                    # Sayfa numaralarını ekleyerek içeriği birleştir
                                    document_content = f"## Belge: {file_name}\n**Path:** {file_path}\n**İşlem:** LibreOffice + Docling markdown extraction\n**İlk Docling Hatası:** {str(docling_error)}\n\n"
                                    
                                    for doc in documents_retry:
                                        content = doc.page_content
                                        
                                        # Page break'leri sayfa numarası ile değiştir
                                        pages = content.split("--- PAGE_BREAK ---")
                                        
                                        formatted_pages = []
                                        for page_num, page_content in enumerate(pages, 1):
                                            if page_content.strip():  # Boş sayfaları atla
                                                formatted_page = f"**[Sayfa {page_num}]**\n\n{page_content.strip()}\n\n"
                                                formatted_pages.append(formatted_page)
                                        
                                        document_content += "\n".join(formatted_pages)
                                    
                                    all_documents_content.append(document_content)
                                    libreoffice_success = True
                                    
                                    logging.info(f"{file_name} belgesi LibreOffice + Docling ile başarıyla işlendi. Sayfa sayısı: {len(pages)}")
                                    
                                except Exception as docling_retry_error:
                                    logging.warning(f"{file_name} belgesi LibreOffice PDF'i ile Docling yeniden denemesi başarısız: {str(docling_retry_error)}")
                                    # Bu durumda analyze_single_file'a geçeceğiz
                                    pass
                                    
                            except subprocess.TimeoutExpired:
                                logging.warning(f"LibreOffice dönüştürme timeout: {file_name}")
                            except subprocess.CalledProcessError as e:
                                logging.warning(f"LibreOffice dönüştürme hatası: {e}")
                            except Exception as convert_error:
                                logging.warning(f"LibreOffice PDF dönüştürme hatası: {str(convert_error)}")
                            
                            # LibreOffice + Docling başarısız olduysa analyze_single_file ile LLM analizi yap
                            if not libreoffice_success:
                                logging.info(f"{file_name} belgesi için LLM analizi deneniyor...")
                                
                                try:
                                    # PDF değilse önce PDF'e dönüştür
                                    if file_extension != '.pdf':
                                        logging.info(f"{file_name} dosyası LLM analizi için PDF'e dönüştürülüyor...")
                                        try:
                                            # LibreOffice ile PDF'e dönüştür (eğer daha önce başarısızsa tekrar dene)
                                            if converted_pdf_path and os.path.exists(converted_pdf_path):
                                                # Zaten LibreOffice'ten PDF var, onu kullan
                                                pdf_file_path = converted_pdf_path
                                            else:
                                                # Yeniden LibreOffice ile PDF'e dönüştür
                                                temp_pdf_dir = os.path.join(tempfile.gettempdir(), "llm_graph_pdf_temp")
                                                os.makedirs(temp_pdf_dir, exist_ok=True)
                                                
                                                filename_without_ext = Path(file_name).stem
                                                safe_filename = re.sub(r'[^\w\-_\.]', '_', filename_without_ext)
                                                pdf_output_path = os.path.join(temp_pdf_dir, safe_filename + ".pdf")
                                                
                                                # LibreOffice convert komutu
                                                subprocess.run([
                                                    "libreoffice", "--headless", "--convert-to", "pdf", 
                                                    "--outdir", temp_pdf_dir, file_path
                                                ], check=True, timeout=60)
                                                
                                                # Dönüştürülen PDF dosyası path'ini güncelle
                                                converted_pdf_path = pdf_output_path
                                                if not os.path.exists(pdf_output_path):
                                                    # LibreOffice bazen farklı isimle kaydediyor, kontrol et
                                                    original_filename = Path(file_path).stem
                                                    alternative_pdf_path = os.path.join(temp_pdf_dir, original_filename + ".pdf")
                                                    if os.path.exists(alternative_pdf_path):
                                                        converted_pdf_path = alternative_pdf_path
                                                    else:
                                                        raise FileNotFoundError(f"PDF dönüştürme başarısız: {pdf_output_path}")
                                                
                                                logging.info(f"{file_name} başarıyla PDF'e dönüştürüldü: {converted_pdf_path}")
                                                pdf_file_path = converted_pdf_path
                                            
                                        except subprocess.TimeoutExpired:
                                            raise Exception(f"LibreOffice dönüştürme timeout: {file_name}")
                                        except subprocess.CalledProcessError as e:
                                            raise Exception(f"LibreOffice dönüştürme hatası: {e}")
                                        except Exception as convert_error:
                                            raise Exception(f"PDF dönüştürme hatası: {str(convert_error)}")
                                    else:
                                        # Zaten PDF
                                        pdf_file_path = file_path
                                    
                                    # PDF sayfalarını PNG'ye çevir (score.py'deki yaklaşım)
                                    # Geçici klasörler oluştur
                                    temp_image_dir = os.path.join(tempfile.gettempdir(), "llm_graph_images")
                                    os.makedirs(temp_image_dir, exist_ok=True)
                                    
                                    # PDF sayfalarını PNG'ye çevir
                                    filename_without_ext = Path(file_name).stem
                                    safe_filename = re.sub(r'[^\w\-_\.]', '_', filename_without_ext)
                                    
                                    images = convert_from_path(
                                        pdf_file_path,
                                        dpi=200,
                                        output_folder=temp_image_dir,
                                        output_file=safe_filename,
                                        fmt="png",
                                        size=(1200, 1600)
                                    )
                                    
                                    # Her sayfa için ayrı ayrı LLM analizi yap
                                    analysis_results = []

                                    # LLM modeli al
                                    if model:
                                        vision_llm, _ = get_llm(model)
                                    else:
                                        # Default model kullan
                                        vision_llm, _ = get_llm("gpt-4o")
                                    
                                    for i, img in enumerate(images, start=1):
                                        try:
                                            # PNG dosyasını kaydet
                                            image_path = os.path.join(temp_image_dir, f"{safe_filename}_sayfa{i}.png")
                                            img.save(image_path, "PNG")
                                            
                                            # PNG'yi base64'e çevir
                                            with open(image_path, "rb") as f:
                                                image_bytes = f.read()
                                            encoded_image = base64.b64encode(image_bytes).decode("utf-8")
                                            
                                            # analyze_single_file için gerekli format (PNG için)
                                            file_info_for_analysis = {
                                                "fileName": f"{file_name} - Sayfa {i}",
                                                "base64": f"data:image/png;base64,{encoded_image}"
                                            }
                                            
                                            
                                            # analyze_single_file fonksiyonu ile analiz et
                                            page_analysis = await analyze_single_file(vision_llm, file_info_for_analysis)
                                            analysis_results.append(f"**[Sayfa {i}]**\n\n{page_analysis}\n")
                                            
                                            # Geçici resim dosyasını temizle
                                            try:
                                                os.unlink(image_path)
                                            except:
                                                pass
                                                
                                        except Exception as page_error:
                                            logging.warning(f"Sayfa {i} analizi başarısız: {str(page_error)}")
                                            analysis_results.append(f"**[Sayfa {i}]**\n\n**[HATA]** Bu sayfa analiz edilemedi: {str(page_error)}\n")
                                    
                                    # Geçici PDF dosyasını temizle (eğer dönüştürme yapıldıysa)
                                    if file_extension != '.pdf' and 'pdf_file_path' in locals() and pdf_file_path != file_path:
                                        try:
                                            os.unlink(pdf_file_path)
                                        except:
                                            pass
                                    
                                    # Tüm sayfa analizlerini birleştir
                                    combined_analysis = "\n".join(analysis_results)
                                    
                                    # Sonucu markdown formatında ekle
                                    document_content = f"## Belge: {file_name}\n**Path:** {file_path}\n**İşlem:** LLM vision analizi (LibreOffice + Docling başarısız)\n**İlk Docling Hatası:** {str(docling_error)}\n**Toplam Sayfa:** {len(images)}\n\n"
                                    document_content += f"**[LLM Vision Analizi]**\n\n{combined_analysis}\n"
                                    
                                    all_documents_content.append(document_content)
                                    logging.info(f"{file_name} belgesi LLM vision analizi ile başarıyla işlendi. Sayfa sayısı: {len(images)}")
                                    
                                except Exception as llm_error:
                                    logging.exception(f"LLM vision analizi ile {file_name} belgesi işlenemedi: {str(llm_error)}")
                                    error_content = f"## Belge: {file_name}\n**Path:** {file_path}\n\n**[HATA]** Tüm yöntemler başarısız oldu.\n\n**İlk Docling Hatası:** {str(docling_error)}\n**LLM Hatası:** {str(llm_error)}\n\n"
                                    all_documents_content.append(error_content)
                            
                            # LibreOffice ile oluşturulan geçici PDF dosyasını temizle
                            if converted_pdf_path and os.path.exists(converted_pdf_path):
                                try:
                                    os.unlink(converted_pdf_path)
                                except:
                                    pass
                        
                    elif file_url:
                        # URL işlemi
                        logging.info(f"{file_name} belgesi URL'den indiriliyor: {file_url}")
                        
                        try:
                            # URL'den dosyayı indir
                            response = requests.get(file_url, timeout=30)
                            response.raise_for_status()
                            
                            # Geçici dosya oluştur
                            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as temp_file:
                                temp_file.write(response.content)
                                temp_file_path = temp_file.name
                            
                            try:
                                # Docling ile belgeyi yükle
                                labels = [
                                    label
                                    for label in DEFAULT_EXPORT_LABELS
                                    if label not in (DocItemLabel.PICTURE, DocItemLabel.PAGE_FOOTER)
                                ]
                                
                                loader = DoclingLoader(
                                    file_path=temp_file_path,
                                    export_type=ExportType.MARKDOWN,
                                    md_export_kwargs={
                                        "page_break_placeholder": "\n\n--- PAGE_BREAK ---\n\n",
                                        "labels": labels,
                                    },
                                )
                                
                                # Timeout ile belgeyi yükle
                                import concurrent.futures
                                
                                def load_document_with_timeout():
                                    return loader.load()
                                
                                # 60 saniye timeout ile executor kullan
                                with concurrent.futures.ThreadPoolExecutor() as executor:
                                    future = executor.submit(load_document_with_timeout)
                                    try:
                                        documents = future.result(timeout=180)  # 2 dakikaya çıkardık
                                    except concurrent.futures.TimeoutError:
                                        raise TimeoutError(f"Docling processing timeout for {file_name}")
                                
                                # Sayfa numaralarını ekleyerek içeriği birleştir
                                document_content = f"## Belge: {file_name}\n**URL:** {file_url}\n**İşlem:** Docling markdown extraction\n\n"
                                
                                for doc in documents:
                                    content = doc.page_content
                                    
                                    # Page break'leri sayfa numarası ile değiştir
                                    pages = content.split("--- PAGE_BREAK ---")
                                    
                                    formatted_pages = []
                                    for page_num, page_content in enumerate(pages, 1):
                                        if page_content.strip():  # Boş sayfaları atla
                                            formatted_page = f"**[Sayfa {page_num}]**\n\n{page_content.strip()}\n\n"
                                            formatted_pages.append(formatted_page)
                                    
                                    document_content += "\n".join(formatted_pages)
                                
                                all_documents_content.append(document_content)
                                
                                logging.info(f"{file_name} belgesi URL'den Docling ile başarıyla işlendi. Sayfa sayısı: {len(pages)}")
                            
                            except Exception as docling_error:
                                # Docling hatası durumunda önce LibreOffice ile yeniden oluşturmayı dene
                                logging.warning(f"{file_name} belgesi için Docling hatası: {str(docling_error)}")
                                
                                # LibreOffice ile yeniden oluşturma deneme
                                libreoffice_success = False
                                converted_pdf_path_url = None
                                
                                try:
                                    logging.info(f"{file_name} (URL'den indirilen) dosyası LibreOffice ile yeniden oluşturuluyor...")
                                    
                                    # Geçici PDF klasörü oluştur
                                    temp_pdf_dir = os.path.join(tempfile.gettempdir(), "llm_graph_pdf_temp")
                                    os.makedirs(temp_pdf_dir, exist_ok=True)
                                    
                                    filename_without_ext = Path(file_name).stem
                                    safe_filename = re.sub(r'[^\w\-_\.]', '_', filename_without_ext)
                                    pdf_output_path = os.path.join(temp_pdf_dir, safe_filename + "_libreoffice.pdf")
                                    
                                    # LibreOffice convert komutu
                                    subprocess.run([
                                        "libreoffice", "--headless", "--convert-to", "pdf", 
                                        "--outdir", temp_pdf_dir, temp_file_path
                                    ], check=True, timeout=60)
                                    
                                    # Dönüştürülen PDF dosyası path'ini kontrol et
                                    converted_pdf_path_url = pdf_output_path
                                    if not os.path.exists(pdf_output_path):
                                        # LibreOffice bazen farklı isimle kaydediyor, kontrol et
                                        original_filename = Path(temp_file_path).stem
                                        alternative_pdf_path = os.path.join(temp_pdf_dir, original_filename + ".pdf")
                                        if os.path.exists(alternative_pdf_path):
                                            converted_pdf_path_url = alternative_pdf_path
                                        else:
                                            raise FileNotFoundError(f"PDF dönüştürme başarısız: {pdf_output_path}")
                                    
                                    logging.info(f"{file_name} başarıyla LibreOffice ile PDF'e dönüştürüldü: {converted_pdf_path_url}")
                                    
                                    # LibreOffice ile oluşan PDF'i Docling ile tekrar dene
                                    logging.info(f"{file_name} belgesi LibreOffice PDF'i ile Docling yeniden deneniyor...")
                                    
                                    # Dosya sisteminin stabilize olması için kısa bir bekleme
                                    import time
                                    time.sleep(1)
                                    
                                    try:
                                        # Daha basit bir Docling konfigürasyonu kullan
                                        simple_labels = [
                                            DocItemLabel.TEXT,
                                            DocItemLabel.TITLE,
                                            DocItemLabel.SECTION_HEADER
                                        ]
                                        
                                        loader_retry = DoclingLoader(
                                            file_path=converted_pdf_path_url,
                                            export_type=ExportType.MARKDOWN,
                                            md_export_kwargs={
                                                "page_break_placeholder": "\n\n--- PAGE_BREAK ---\n\n",
                                                "labels": simple_labels,  # Sadece temel etiketler
                                            },
                                        )
                                        
                                        # Timeout ile belgeyi yükle
                                        def load_document_with_timeout_retry():
                                            return loader_retry.load()
                                        
                                        with concurrent.futures.ThreadPoolExecutor() as executor:
                                            future = executor.submit(load_document_with_timeout_retry)
                                            try:
                                                documents_retry = future.result(timeout=180)  # 3 dakikaya çıkardık
                                            except concurrent.futures.TimeoutError:
                                                raise TimeoutError(f"Docling processing timeout for LibreOffice PDF: {file_name}")
                                        
                                        # Sayfa numaralarını ekleyerek içeriği birleştir
                                        document_content = f"## Belge: {file_name}\n**URL:** {file_url}\n**İşlem:** LibreOffice + Docling markdown extraction\n**İlk Docling Hatası:** {str(docling_error)}\n\n"
                                        
                                        for doc in documents_retry:
                                            content = doc.page_content
                                            
                                            # Page break'leri sayfa numarası ile değiştir
                                            pages = content.split("--- PAGE_BREAK ---")
                                            
                                            formatted_pages = []
                                            for page_num, page_content in enumerate(pages, 1):
                                                if page_content.strip():  # Boş sayfaları atla
                                                    formatted_page = f"**[Sayfa {page_num}]**\n\n{page_content.strip()}\n\n"
                                                    formatted_pages.append(formatted_page)
                                            
                                            document_content += "\n".join(formatted_pages)
                                        
                                        all_documents_content.append(document_content)
                                        libreoffice_success = True
                                        
                                        logging.info(f"{file_name} belgesi LibreOffice + Docling ile başarıyla işlendi. Sayfa sayısı: {len(pages)}")
                                        
                                    except Exception as docling_retry_error:
                                        logging.warning(f"{file_name} belgesi LibreOffice PDF'i ile Docling yeniden denemesi başarısız: {str(docling_retry_error)}")
                                        # Bu durumda analyze_single_file'a geçeceğiz
                                        pass
                                        
                                except subprocess.TimeoutExpired:
                                    logging.warning(f"LibreOffice dönüştürme timeout: {file_name}")
                                except subprocess.CalledProcessError as e:
                                    logging.warning(f"LibreOffice dönüştürme hatası: {e}")
                                except Exception as convert_error:
                                    logging.warning(f"LibreOffice PDF dönüştürme hatası: {str(convert_error)}")
                                
                                # LibreOffice + Docling başarısız olduysa analyze_single_file ile LLM analizi yap
                                if not libreoffice_success:
                                    logging.info(f"{file_name} belgesi için LLM analizi deneniyor...")
                                    
                                    try:
                                        # İndirilen dosyanın uzantısını kontrol et
                                        temp_file_extension = Path(temp_file_path).suffix.lower()
                                        
                                        # PDF değilse önce PDF'e dönüştür (score.py mantığı)
                                        if temp_file_extension != '.pdf':
                                            logging.info(f"{file_name} (URL'den indirilen) dosyası PDF'e dönüştürülüyor...")
                                            try:
                                                # LibreOffice ile PDF'e dönüştür (eğer daha önce başarısızsa tekrar dene)
                                                if converted_pdf_path_url and os.path.exists(converted_pdf_path_url):
                                                    # Zaten LibreOffice'ten PDF var, onu kullan
                                                    pdf_file_path_url = converted_pdf_path_url
                                                else:
                                                    # Yeniden LibreOffice ile PDF'e dönüştür
                                                    temp_pdf_dir = os.path.join(tempfile.gettempdir(), "llm_graph_pdf_temp")
                                                    os.makedirs(temp_pdf_dir, exist_ok=True)
                                                    
                                                    filename_without_ext = Path(file_name).stem
                                                    safe_filename = re.sub(r'[^\w\-_\.]', '_', filename_without_ext)
                                                    pdf_output_path = os.path.join(temp_pdf_dir, safe_filename + ".pdf")
                                                    
                                                    # LibreOffice convert komutu
                                                    subprocess.run([
                                                        "libreoffice", "--headless", "--convert-to", "pdf", 
                                                        "--outdir", temp_pdf_dir, temp_file_path
                                                    ], check=True, timeout=60)
                                                    
                                                    # Dönüştürülen PDF dosyası path'ini güncelle
                                                    converted_pdf_path_url = pdf_output_path
                                                    if not os.path.exists(pdf_output_path):
                                                        # LibreOffice bazen farklı isimle kaydediyor, kontrol et
                                                        original_filename = Path(temp_file_path).stem
                                                        alternative_pdf_path = os.path.join(temp_pdf_dir, original_filename + ".pdf")
                                                        if os.path.exists(alternative_pdf_path):
                                                            converted_pdf_path_url = alternative_pdf_path
                                                        else:
                                                            raise FileNotFoundError(f"PDF dönüştürme başarısız: {pdf_output_path}")
                                                    
                                                    logging.info(f"{file_name} başarıyla PDF'e dönüştürüldü: {converted_pdf_path_url}")
                                                    pdf_file_path_url = converted_pdf_path_url
                                                
                                            except subprocess.TimeoutExpired:
                                                raise Exception(f"LibreOffice dönüştürme timeout: {file_name}")
                                            except subprocess.CalledProcessError as e:
                                                raise Exception(f"LibreOffice dönüştürme hatası: {e}")
                                            except Exception as convert_error:
                                                raise Exception(f"PDF dönüştürme hatası: {str(convert_error)}")
                                        else:
                                            # Zaten PDF
                                            pdf_file_path_url = temp_file_path
                                        
                                        # PDF sayfalarını PNG'ye çevir (score.py'deki yaklaşım)
                                        # Geçici klasörler oluştur
                                        temp_image_dir = os.path.join(tempfile.gettempdir(), "llm_graph_images")
                                        os.makedirs(temp_image_dir, exist_ok=True)
                                        
                                        # PDF sayfalarını PNG'ye çevir
                                        filename_without_ext = Path(file_name).stem
                                        safe_filename = re.sub(r'[^\w\-_\.]', '_', filename_without_ext)
                                        
                                        images = convert_from_path(
                                            pdf_file_path_url,
                                            dpi=200,
                                            output_folder=temp_image_dir,
                                            output_file=safe_filename,
                                            fmt="png",
                                            size=(1200, 1600)
                                        )
                                        
                                        # Her sayfa için ayrı ayrı LLM analizi yap
                                        analysis_results = []
                                        
                                        for i, img in enumerate(images, start=1):
                                            try:
                                                # PNG dosyasını kaydet
                                                image_path = os.path.join(temp_image_dir, f"{safe_filename}_sayfa{i}.png")
                                                img.save(image_path, "PNG")
                                                
                                                # PNG'yi base64'e çevir
                                                with open(image_path, "rb") as f:
                                                    image_bytes = f.read()
                                                encoded_image = base64.b64encode(image_bytes).decode("utf-8")
                                                
                                                # analyze_single_file için gerekli format (PNG için)
                                                file_info_for_analysis = {
                                                    "fileName": f"{file_name} - Sayfa {i}",
                                                    "base64": f"data:image/png;base64,{encoded_image}"
                                                }
                                                
                                                # LLM modeli al
                                                if model:
                                                    vision_llm, _ = get_llm(model)
                                                else:
                                                    # Default model kullan
                                                    vision_llm, _ = get_llm("gpt-4o")
                                                
                                                # analyze_single_file fonksiyonu ile analiz et
                                                page_analysis = await analyze_single_file(vision_llm, file_info_for_analysis)
                                                analysis_results.append(f"**[Sayfa {i}]**\n\n{page_analysis}\n")
                                                
                                                # Geçici resim dosyasını temizle
                                                try:
                                                    os.unlink(image_path)
                                                except:
                                                    pass
                                                    
                                            except Exception as page_error:
                                                logging.warning(f"Sayfa {i} analizi başarısız: {str(page_error)}")
                                                analysis_results.append(f"**[Sayfa {i}]**\n\n**[HATA]** Bu sayfa analiz edilemedi: {str(page_error)}\n")
                                        
                                        # Geçici PDF dosyasını temizle (eğer dönüştürme yapıldıysa)
                                        if temp_file_extension != '.pdf' and 'pdf_file_path_url' in locals() and pdf_file_path_url != temp_file_path:
                                            try:
                                                os.unlink(pdf_file_path_url)
                                            except:
                                                pass
                                        
                                        # Tüm sayfa analizlerini birleştir
                                        combined_analysis = "\n".join(analysis_results)
                                        
                                        # Sonucu markdown formatında ekle
                                        document_content = f"## Belge: {file_name}\n**URL:** {file_url}\n**İşlem:** LLM vision analizi (LibreOffice + Docling başarısız)\n**İlk Docling Hatası:** {str(docling_error)}\n**Toplam Sayfa:** {len(images)}\n\n"
                                        document_content += f"**[LLM Vision Analizi]**\n\n{combined_analysis}\n"
                                        
                                        all_documents_content.append(document_content)
                                        logging.info(f"{file_name} belgesi LLM vision analizi ile başarıyla işlendi. Sayfa sayısı: {len(images)}")
                                        
                                    except Exception as llm_error:
                                        logging.exception(f"LLM vision analizi ile {file_name} belgesi işlenemedi: {str(llm_error)}")
                                        error_content = f"## Belge: {file_name}\n**URL:** {file_url}\n\n**[HATA]** Tüm yöntemler başarısız oldu.\n\n**İlk Docling Hatası:** {str(docling_error)}\n**LLM Hatası:** {str(llm_error)}\n\n"
                                        all_documents_content.append(error_content)
                                
                                # LibreOffice ile oluşturulan geçici PDF dosyasını temizle
                                if converted_pdf_path_url and os.path.exists(converted_pdf_path_url):
                                    try:
                                        os.unlink(converted_pdf_path_url)
                                    except:
                                        pass
                            
                            finally:
                                # Geçici dosyayı temizle
                                try:
                                    os.unlink(temp_file_path)
                                except:
                                    pass
                        
                        except requests.RequestException as url_error:
                            logging.exception(f"URL indirme hatası ({file_name}): {str(url_error)}")
                            error_content = f"## Belge: {file_name}\n**URL:** {file_url}\n\n**[HATA]** URL'den dosya indirilemedi: {str(url_error)}\n\n"
                            all_documents_content.append(error_content)
                    
                    else:
                        # Ne path ne de URL var
                        error_content = f"## Belge: {file_name}\n\n**[HATA]** Dosya path'i veya URL'si bulunamadı.\n\n"
                        all_documents_content.append(error_content)
                        
                except Exception as e:
                    logging.exception(f"Genel dosya işleme hatası ({file_info.get('fileName', 'bilinmeyen')}): {str(e)}")
                    error_content = f"## Belge: {file_info.get('fileName', 'bilinmeyen')}\n\n**[HATA]** Genel dosya işleme hatası: {str(e)}\n\n"
                    all_documents_content.append(error_content)

        # Tüm belgeleri birleştir
        combined_content = "\n\n---\n\n".join(all_documents_content)
        
        # Belge sayısı bilgisini ekle
        total_docs = sum(len(file_list) for file_list in files.values())
        result = f"**Toplam {total_docs} belge işlendi:**\n\n{combined_content}"
        
        logging.info(f"convert_files_to_markdown tamamlandı. Toplam belge sayısı: {total_docs}")
        return result

    except Exception as e:
        logging.exception(f"Error in convert_files_to_markdown: {str(e)}")
        return f"**[HATA]** Belgeler markdown'a çevrilirken hata oluştu: {str(e)}"


async def analyze_markdown_with_llm(markdown_content: str, model, question, history, messages):
    """
    Markdown içeriğini LLM ile analiz eder ve streaming response döner.
    """
    try:
        start_time = time.time()
        qa_llm, model_name = get_llm(model)

        yield {
            "type": "message_chunk",
            "content": "Belgeler LLM ile analiz ediliyor..\n",
            "full_message": "Belgeler LLM ile analiz ediliyor..",
            "is_complete": False,
            "user": "chatbot"
        }

        # LLM ile analiz et
        analyze_prompt = ChatPromptTemplate.from_messages([
            (
                "human",
                question if question else "Bu belgeler hakkında kullanıcıya kapsamlı bir özet ve analiz ver."
            ),
            ("human", f"Belgeler:\n\n{markdown_content}")
        ])

        chain = analyze_prompt | qa_llm
        resp = await chain.ainvoke({})

        total_tokens = get_total_tokens(resp, qa_llm)
        logging.info(f"LLM analizi için total_tokens: {total_tokens}")

        ai_response_content = resp.content.strip()

        # Streaming efekti - newline karakterlerini koruyarak
        # Metni kelimeler ve newline karakterlerine göre böl
        tokens = re.findall(r'\S+|\n+', ai_response_content)
        streamed_content = ""
        
        for i, token in enumerate(tokens):
            if token.startswith('\n'):
                # Newline karakterleri için
                streamed_content += token
                yield {
                    "type": "message_chunk",
                    "content": token,
                    "full_message": streamed_content,
                    "is_complete": i == len(tokens) - 1,
                    "user": "chatbot"
                }
            else:
                # Normal kelimeler için
                streamed_content += token + " "
                yield {
                    "type": "message_chunk",
                    "content": token + " ",
                    "full_message": streamed_content.rstrip(),
                    "is_complete": i == len(tokens) - 1,
                    "user": "chatbot"
                }
            await asyncio.sleep(0.03)

        # Mesajları history'e kaydet
        ai_response_raw = AIMessage(content=markdown_content)
        messages.append(ai_response_raw)

        ai_response_final = AIMessage(content=ai_response_content)
        messages.append(ai_response_final)

        # Background summarization
        summarization_future = asyncio.get_event_loop().run_in_executor(
            None, summarize_and_log, history, messages, qa_llm
        )
        logging.info(f"LLM summarization task started: {summarization_future}")

        analyze_time = time.time() - start_time
        logging.info(f"LLM analysis completed in {analyze_time:.2f} seconds")

    except Exception as e:
        logging.exception(f"Error in analyze_markdown_with_llm: {str(e)}")
        yield {
            "type": "error",
            "message": "LLM analizi sırasında hata oluştu",
            "error": str(e),
            "user": "chatbot"
        }


async def analyze_files_with_docling(files: Dict[str, List[Dict[str, str]]], model, question, history, messages, graph):
    """
    Dosyaları Docling ile okuyup analiz eder ve streaming response döner.
    Çıktısı belgenin markdown formatında sayfalar arası page_break ile birleştirilmiş hali.
    """
    try:
        start_time = time.time()

        yield {
            "type": "message_chunk",
            "content": "Belgeler Docling ile işleniyor (local/URL)...\n",
            "full_message": "Belgeler Docling ile işleniyor (local/URL)...",
            "is_complete": False,
            "user": "chatbot"
        }

        # 1. Adım: Dosyaları markdown'a çevir
        markdown_content = await convert_files_to_markdown(files, model)
        
        # 2. Adım: Question kontrolü
        if not question or question.strip() == "":
            # Question boş ise kullanıcıya soru sor
            response_message = "Belgeniz başarıyla yüklendi. Ne öğrenmek istersiniz?"
            
            # Streaming efekti ile mesajı gönder
            tokens = re.findall(r'\S+|\n+', response_message)
            streamed_content = ""
            
            for i, token in enumerate(tokens):
                if token.startswith('\n'):
                    streamed_content += token
                    yield {
                        "type": "message_chunk",
                        "content": token,
                        "full_message": streamed_content,
                        "is_complete": i == len(tokens) - 1,
                        "user": "chatbot"
                    }
                else:
                    streamed_content += token + " "
                    yield {
                        "type": "message_chunk", 
                        "content": token + " ",
                        "full_message": streamed_content.rstrip(),
                        "is_complete": i == len(tokens) - 1,
                        "user": "chatbot"
                    }
                await asyncio.sleep(0.03)
            
            # Markdown içeriğini history'e kaydet (gelecekteki sorular için)
            ai_response_markdown = AIMessage(content=markdown_content)
            messages.append(ai_response_markdown)
            
            ai_response_final = AIMessage(content=response_message)
            messages.append(ai_response_final)
            
            # Background summarization için LLM al
            qa_llm, _ = get_llm(model)
            
            # Background summarization
            summarization_future = asyncio.get_event_loop().run_in_executor(
                None, summarize_and_log, history, messages, qa_llm
            )
            logging.info(f"LLM summarization task started: {summarization_future}")
            
        else:
            # Question dolu ise markdown içeriği LLM ile analiz et
            async for chunk in analyze_markdown_with_llm(markdown_content, model, question, history, messages):
                yield chunk

        analyze_time = time.time() - start_time
        logging.info(f"Docling files analyzed in {analyze_time:.2f} seconds")

    except Exception as e:
        logging.exception(f"Error in analyze_files_with_docling: {str(e)}")
        yield {
            "type": "error",
            "message": "Docling belge analizi sırasında hata oluştu",
            "error": str(e),
            "user": "chatbot"
        }

async def QA_RAG_stream(graph, model, question, document_names, session_id, mode, files, write_access=True, intelligent_agent=None, alternative_agent=None):
    """
    Asenkron streaming QA_RAG implementasyonu
    LLM'den token-by-token cevap alır ve frontend'e streamer
    """
    logging.info(f"🔴 QA_RAG_stream CALLED - Session: {session_id}, Question: {question[:50]}...")
    logging.info(f"Streaming Chat Mode: {mode}")
    
    try:
        history = create_neo4j_chat_message_history(graph, session_id, write_access)
        messages = history.messages

        # print("history: ", history)
        print("messages: ", messages)
        # print("files: ", files)

        user_question = None
        if question and question.strip() != '':
            user_question = HumanMessage(content=question)
            messages.append(user_question)
            # ÖNEMLI: HumanMessage'ı session history'sine kaydet
            logging.info(f"🔴 SAVING HumanMessage to session: {question[:50]}...")
            history.add_message(user_question)
            logging.info(f"🔴 HumanMessage SAVED. Total messages in session: {len(history.messages)}")

        # Files parse + görsel analizi
        # image_analysis_text = ""
        if files:
            try:
                files_data = json.loads(files) if isinstance(files, str) else files

                # Environment variable ile analiz metodunu belirle
                # USE_DOCLING=true ise Docling, yoksa LLM görsel analizi kullan
                use_docling = os.getenv('USE_DOCLING', 'true').lower() == 'true'
                
                if use_docling:
                    logging.info("Docling ile belge analizi yapılıyor...")
                    async for chunk in analyze_files_with_docling(files_data, model, question, history, messages, graph):
                        yield chunk
                else:
                    logging.info("LLM ile görsel analizi yapılıyor...")
                    async for chunk in analyze_files_with_llm(files_data, model, question, history, messages):
                        yield chunk

                # Files analizi tamamlandıktan sonra return - gereksiz processing'i engelle  
                return
                
            except Exception as e:
                logging.exception(f"Files parse/analyze error: {str(e)}")
                return

        if mode == CHAT_GRAPH_MODE:
            async for chunk in process_graph_response_stream(model, graph, question, messages, history):
                yield chunk
        else:
            chat_mode_settings = get_chat_mode_settings(mode=mode)
            document_names = list(map(str.strip, json.loads(document_names)))
            
            if document_names and not chat_mode_settings["document_filter"]:
                yield {
                    "type": "error",
                    "session_id": session_id,
                    "message": "Lütfen bu sohbet modunu kullanmadan önce tablodaki tüm dokümanların seçimini kaldırın.",
                    "info": {
                        "sources": [],
                        "model": "",
                        "nodedetails": [],
                        "total_tokens": 0,
                        "response_time": 0,
                        "mode": chat_mode_settings["mode"],
                        "entities": [],
                        "metric_details": [],
                    },
                    "user": "chatbot"
                }
                return
                
            async for chunk in process_chat_response_stream(
                messages, history, question, model, graph, document_names, chat_mode_settings, session_id, intelligent_agent=intelligent_agent, alternative_agent=alternative_agent
            ):
                yield chunk
                
    except Exception as e:
        logging.exception(f"Error in QA_RAG_stream: {str(e)}")
        yield {
            "type": "error",
            "session_id": session_id,
            "message": "Bir hata oluştu",
            "error": str(e),
            "user": "chatbot"
        }

async def process_chat_response_stream(messages, history, question, model, graph, document_names, chat_mode_settings, session_id, intelligent_agent=None, alternative_agent=None):
    """
    Streaming chat response işleme fonksiyonu
    LLM'den gelen her token'i anında frontend'e gönderir
    """
    agent_token_usage = None  # Agent token kullanımını saklamak için
    agent_result = None  # Agent sonuçlarını saklamak için
    
    try:
        # Setup aşaması
        yield {
            "type": "status",
            "session_id": session_id,
            "message": "Bağlantı kuriliyor...",
            "user": "chatbot"
        }
        
        llm, doc_retriever, model_version = setup_chat(model, graph, document_names, chat_mode_settings)
        
        # Casual conversation kontrolü
        yield {
            "type": "status",
            "session_id": session_id, 
            "message": "Soru analiz ediliyor...",
            "user": "chatbot"
        }
        
        is_casual = await asyncio.get_event_loop().run_in_executor(
            None, is_casual_conversation, question, llm
        )
        
        formatted_docs = ""
        sources = []
        entities = {'entityids': [], "relationshipids": []}
        nodedetails = {"chunkdetails": [], "entitydetails": [], "communitydetails": []}
        
        if not is_casual:
            # Document retrieval
            yield {
                "type": "status",
                "session_id": session_id,
                "message": "İlgili dokümanlar aranıyor...",
                "user": "chatbot"
            }
            
            # Instantiate intelligent agent for streaming path as well
            intelligent_agent = None
            try:
                intelligent_agent = IntelligentAgent(graph)
            except Exception:
                intelligent_agent = None

            # Instantiate alternative agent for streaming path as well
            alternative_agent = None
            try:
                alternative_agent = AlternativeAgent(graph)
            except Exception:
                alternative_agent = None

            docs, transformed_question, agent_token_usage, agent_result = await asyncio.get_event_loop().run_in_executor(
                None, retrieve_documents, doc_retriever, messages, intelligent_agent, alternative_agent
            )
            
            # AlternativeAgent'tan gelen sonuçları kontrol et ve formatted_docs'a ekle
            alternative_context = ""
            if (agent_result and isinstance(agent_result, dict) and 
                agent_result.get('mode') in ['count', 'cypher_fallback'] and 
                agent_result.get('response_text')):
                
                yield {
                    "type": "status",
                    "session_id": session_id,
                    "message": f"AlternativeAgent {agent_result.get('mode')} sonuçları alındı...",
                    "user": "chatbot"
                }
                
                # AlternativeAgent'in cevabını context olarak kullan
                alternative_context = f"AlternativeAgent Sonucu:\n{agent_result.get('response_text', '')}\n\n"
                # Docs'u boş bırak çünkü AlternativeAgent direkt cevap verdi
                docs = []
            
            if docs:
                yield {
                    "type": "status",
                    "session_id": session_id,
                    "message": "Bağlam hazırlanıyor...",
                    "user": "chatbot"
                }
                
                formatted_docs, sources_list, entities_dict, communities = format_documents(docs, model, chat_mode_settings)
                sources = sources_list
                entities = entities_dict
                
                print(f"========== STREAMING FORMATTED DOCUMENTS ==========")
                print(f"Total Documents Formatted: {len(docs)}")
                print(f"Sources Found: {sources}")
                print(f"Entity Details: {entities}")
                print(f"Communities: {communities}")
                print("--- FORMATTED CONTEXT FOR STREAMING LLM ---")
                print(f"{formatted_docs[:1000]}...")  # İlk 1000 karakteri göster
                print("--- END OF FORMATTED CONTEXT ---")
                print("==================================================")
                
                if chat_mode_settings["mode"] == CHAT_ENTITY_VECTOR_MODE:
                    nodedetails["entitydetails"] = entities_dict
                elif chat_mode_settings["mode"] == CHAT_GLOBAL_VECTOR_FULLTEXT_MODE:
                    nodedetails["communitydetails"] = communities
                else:
                    sources_and_chunks = get_sources_and_chunks(sources, docs)
                    sources = sources_and_chunks['sources']
                    nodedetails["chunkdetails"] = sources_and_chunks["chunkdetails"]
            else:
                # Docs yoksa (AlternativeAgent count mode durumu) boş formatted_docs başlat
                formatted_docs = ""
            
            # AlternativeAgent context'ini formatted_docs'a ekle
            if alternative_context:
                formatted_docs = alternative_context + formatted_docs
                print(f"========== ALTERNATIVE AGENT CONTEXT ADDED ==========")
                print(f"Alternative Context: {alternative_context[:200]}...")
                print("====================================================")
        
        # Streaming response başlat
        yield {
            "type": "status",
            "session_id": session_id,
            "message": "Cevap üretiliyor...",
            "user": "chatbot"
        }
        
        # RAG Chain ile streaming
        rag_chain = get_rag_chain_stream(llm=llm)
        
        full_response = ""
        total_tokens_count = 0
        
        async for chunk in rag_chain.astream({
            "messages": messages[:-1],
            "context": formatted_docs,
            "input": question
        }):
            if hasattr(chunk, 'content') and chunk.content:
                content_chunk = chunk.content
                full_response += content_chunk
                
                # Token count estimation (yaklaşık)
                total_tokens_count += len(content_chunk.split())
                
                yield {
                    "type": "message_chunk",
                    "session_id": session_id,
                    "content": content_chunk,
                    "full_message": full_response,
                    "is_complete": False,
                    "user": "chatbot"
                }
        
        # Chat history'ye ekleme
        ai_response = AIMessage(content=full_response)
        messages.append(ai_response)
        
        # ÖNEMLI: AIMessage'ı session history'sine kaydet
        history.add_message(ai_response)
        
        # Background summarization başlat
        summarization_future = asyncio.get_event_loop().run_in_executor(
            None, summarize_and_log, history, messages, llm
        )
        logging.info(f"Summarization task started: {summarization_future}")
        
        # Final response
        metric_details = {
            "question": question,
            "contexts": formatted_docs,
            "answer": full_response
        }
        
        # Response info hazırla
        response_info = {
            "sources": sources,
            "model": model_version,
            "nodedetails": nodedetails,
            "total_tokens": total_tokens_count,
            "response_time": 0,  # Bu daha sonra API seviyesinde hesaplanacak
            "mode": chat_mode_settings["mode"],
            "entities": entities,
            "context": sources,  # Frontend context için sources'ı kullan
            "cypher_query": "",  # Streaming'de cypher query yok
            "error": "",
            "metric_details": metric_details,
            "personPolicyInfo": entities.get('personPolicyInfo', []),  # YENI: PersonPolicyInfo ekle
            "documentList": entities.get('documentList', []),  # YENI: DocumentList ekle
            "totalDocuments": entities.get('totalDocuments', 0)  # YENI: TotalDocuments ekle
        }
        
        # IntelligentAgent token bilgilerini ekle
        if agent_token_usage:
            response_info["agent_input_tokens"] = agent_token_usage.get('input_tokens', 0)
            response_info["agent_output_tokens"] = agent_token_usage.get('output_tokens', 0)
            response_info["agent_total_tokens"] = agent_token_usage.get('total_tokens', 0)
        
        # IntelligentAgent ve AlternativeAgent bilgilerini ekle
        if agent_result:
            # Hem IntelligentAgent hem de AlternativeAgent için uyumlu bilgiler
            if agent_result.get('mode') in ['count', 'cypher_fallback', 'vector']:
                # AlternativeAgent sonucu
                response_info["alternative_agent_mode"] = agent_result.get('mode')
                response_info["alternative_agent_meta"] = agent_result.get('meta', {})
                response_info["alternative_agent_response"] = agent_result.get('response_text', '')
                
                # AlternativeAgent'tan gelen chunk bilgileri varsa ekle
                if agent_result.get('meta', {}).get('chunks'):
                    response_info["agent_chunk_details"] = agent_result.get('meta', {}).get('chunks', [])
                
                # AlternativeAgent entities (eğer varsa)
                if agent_result.get('meta', {}).get('filters'):
                    response_info["alternative_agent_filters"] = agent_result.get('meta', {}).get('filters')
                
            else:
                # IntelligentAgent sonucu
                response_info["agent_chunk_details"] = agent_result.get('chunk_details', [])
                response_info["agent_entity_details"] = agent_result.get('entity_details', [])
                response_info["agent_discovered_entities"] = agent_result.get('discovered_entities', 0)
                response_info["agent_discovered_chunks"] = agent_result.get('discovered_chunks', 0)
                response_info["agent_iterations"] = agent_result.get('iterations', 0)
            
            # Agent entity detaylarını entities formatına çevir
            if agent_result.get('entity_details', []):
                entity_ids = [entity['id'] for entity in agent_result.get('entity_details', [])]
                response_info["entities"] = entity_ids
            else:
                response_info["entities"] = entities.get('entityids', [])
        else:
            response_info["entities"] = entities.get('entityids', [])
        
        yield {
            "type": "complete",
            "session_id": session_id,
            "message": full_response,
            "info": response_info,
            "is_complete": True,
            "user": "chatbot"
        }
        
    except Exception as e:
        logging.exception(f"Error in process_chat_response_stream: {str(e)}")
        yield {
            "type": "error",
            "session_id": session_id,
            "message": "Cevap üretilirken bir hata oluştu",
            "error": str(e),
            "user": "chatbot"
        }

async def process_graph_response_stream(model, graph, question, messages, history):
    """
    Graph mode için streaming response işleme fonksiyonu
    """
    try:
        yield {
            "type": "status",
            "message": "Graph chain hazırlanıyor...",
            "user": "chatbot"
        }
        
        graph_chain, qa_llm, model_version = create_graph_chain(model, graph)
        
        yield {
            "type": "status", 
            "message": "Cypher sorgusu çalıştırılıyor...",
            "user": "chatbot"
        }
        
        # Graph response al (bu kısım şu an için batch, gelecekte graph streaming eklenebilir)
        graph_response = await asyncio.get_event_loop().run_in_executor(
            None, get_graph_response, graph_chain, question
        )
        
        ai_response_content = graph_response.get("response", "Bir şeyler ters gitti")
        
        yield {
            "type": "status",
            "message": "Cevap hazırlanıyor...",
            "user": "chatbot"
        }
        
        # Graph response'u streaming olarak gönder (simüle streaming)
        # Metni kelimeler ve newline karakterlerine göre böl
        tokens = re.findall(r'\S+|\n+', ai_response_content)
        streamed_content = ""
        
        for i, token in enumerate(tokens):
            if token.startswith('\n'):
                # Newline karakterleri için
                streamed_content += token
                yield {
                    "type": "message_chunk",
                    "content": token,
                    "full_message": streamed_content,
                    "is_complete": i == len(tokens) - 1,
                    "user": "chatbot"
                }
            else:
                # Normal kelimeler için
                streamed_content += token + " "
                yield {
                    "type": "message_chunk",
                    "content": token + " ",
                    "full_message": streamed_content.rstrip(),
                    "is_complete": i == len(tokens) - 1,
                    "user": "chatbot"
                }
            
            # Gerçekçi streaming efekti
            await asyncio.sleep(0.05)
        
        # Messages'a ekle
        ai_response = AIMessage(content=ai_response_content)
        messages.append(ai_response)
        
        # ÖNEMLI: AIMessage'ı session history'sine kaydet
        history.add_message(ai_response)
        
        # Background summarization
        summarization_future = asyncio.get_event_loop().run_in_executor(
            None, summarize_and_log, history, messages, qa_llm
        )
        logging.info(f"Graph summarization task started: {summarization_future}")
        
        # Final result
        metric_details = {
            "question": question,
            "contexts": graph_response.get("context", ""),
            "answer": ai_response_content
        }
        
        yield {
            "type": "complete",
            "message": ai_response_content,
            "info": {
                "model": model_version,
                "cypher_query": graph_response.get("cypher_query", ""),
                "context": graph_response.get("context", ""),
                "mode": "graph",
                "response_time": 0,
                "metric_details": metric_details,
            },
            "is_complete": True,
            "user": "chatbot"
        }
        
    except Exception as e:
        logging.exception(f"Error in process_graph_response_stream: {str(e)}")
        yield {
            "type": "error",
            "message": "Graph sorgusu sırasında bir hata oluştu",
            "error": str(e),
            "user": "chatbot"
        }

def get_rag_chain_stream(llm, system_template=CHAT_SYSTEM_TEMPLATE):
    """
    Streaming destekli RAG chain oluşturur
    """
    try:
        question_answering_prompt = ChatPromptTemplate.from_messages([
            ("system", system_template),
            MessagesPlaceholder(variable_name="messages"),
            ("human", "User question: {input}")
        ])

        # Streaming chain oluştur
        streaming_chain = question_answering_prompt | llm
        
        return streaming_chain

    except Exception as e:
        logging.error(f"Error creating streaming RAG chain: {e}")
        raise