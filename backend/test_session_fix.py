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

# Test session'ı temizle
test_session = '221c62f3-9794-486c-9815-100ebde62c86'
history = Neo4jChatMessageHistory(graph=graph, session_id=test_session)

print('=== BEFORE CLEARING ===')
for i, msg in enumerate(history.messages):
    print(f'Message {i+1}: {type(msg).__name__} - {msg.content}')

# Session'ı temizle
history.clear()

print('\n=== AFTER CLEARING ===')
for i, msg in enumerate(history.messages):
    print(f'Message {i+1}: {type(msg).__name__} - {msg.content}')

print('\n*** SESSION CLEARED AND READY FOR TESTING ***')
print('Now when you ask questions via frontend, both HumanMessage and AIMessage should be saved to session.')
