from langchain_neo4j import Neo4jGraph, Neo4jChatMessageHistory
from langchain_core.messages import HumanMessage, AIMessage
import os
from dotenv import load_dotenv

load_dotenv()

# Neo4j bağlantısı oluştur
graph = Neo4jGraph(
    url=os.getenv('NEO4J_URI'),
    username=os.getenv('NEO4J_USERNAME'), 
    password=os.getenv('NEO4J_PASSWORD'),
    database=os.getenv('NEO4J_DATABASE')
)

# Düzeltilmiş session'ı test et
session_id = '221c62f3-9794-486c-9815-100ebde62c86'
history = Neo4jChatMessageHistory(graph=graph, session_id=session_id)

print('=== FIXED SESSION STATE ===')
for i, msg in enumerate(history.messages):
    print(f'Message {i+1}: {type(msg).__name__} - {msg.content}')

# QA_RAG'deki gibi yeni soru ekle
new_question = HumanMessage(content='bunlar neler')
messages = history.messages
messages.append(new_question)

print('\n=== AFTER ADDING NEW QUESTION ===')
for i, msg in enumerate(messages):
    print(f'Message {i+1}: {type(msg).__name__} - {msg.content}')

print('\n=== HUMAN MESSAGES EXTRACTION ===')
human_messages = [msg for msg in messages if isinstance(msg, HumanMessage)]
print(f'Human messages count: {len(human_messages)}')
for i, msg in enumerate(human_messages):
    print(f'Human message {i+1}: {msg.content}')

print('\n*** NOW CONTEXT SHOULD BE CORRECT! ***')
print('LLM will receive both human messages for transformation.')
