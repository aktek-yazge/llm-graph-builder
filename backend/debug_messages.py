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

# Aynı test session'a erişim
test_session = 'debug_session_456'
history = Neo4jChatMessageHistory(graph=graph, session_id=test_session)

print('=== MESSAGES BEFORE NEW QUESTION ===')
for i, msg in enumerate(history.messages):
    print(f'Message {i+1}: {type(msg).__name__} - {msg.content}')
print('======================================')

# Yeni kullanıcı sorusu ekle (QA_RAG'deki gibi)
new_question = HumanMessage(content='bunlar neler')
messages = history.messages
messages.append(new_question)

print('=== MESSAGES AFTER NEW QUESTION ===')
for i, msg in enumerate(messages):
    print(f'Message {i+1}: {type(msg).__name__} - {msg.content}')
print('=====================================')

print()
print('=== HUMAN MESSAGES EXTRACTION TEST ===')
human_messages = [msg for msg in messages if isinstance(msg, HumanMessage)]
print(f'Human messages count: {len(human_messages)}')
for i, msg in enumerate(human_messages):
    print(f'Human message {i+1}: {msg.content}')
print('========================================')
