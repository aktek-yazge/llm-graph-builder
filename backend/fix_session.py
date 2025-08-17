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

# Problematik session'ı düzelt
session_id = '221c62f3-9794-486c-9815-100ebde62c86'
history = Neo4jChatMessageHistory(graph=graph, session_id=session_id)

print('=== BEFORE FIX ===')
for i, msg in enumerate(history.messages):
    print(f'Message {i+1}: {type(msg).__name__} - {msg.content[:100]}...')

# Eksik olan HumanMessage'ı başa ekle (ancak bu LangChain API'si ile doğrudan mümkün değil)
# Bu yüzden session'ı temizleyip doğru sıralarla yeniden oluşturacağız

print('\n=== CLEARING AND RECREATING SESSION ===')
history.clear()

# Doğru sırada mesajları ekle
history.add_message(HumanMessage(content='Ayça hanımın konut poliçeleri neler?'))
history.add_message(AIMessage(content='Ayça Hanım\'ın 2020 yılında 9 adet konut poliçesi bulunuyor. Poliçelerin detayları veya isimleri mevcut değil.'))

print('\n=== AFTER FIX ===')
for i, msg in enumerate(history.messages):
    print(f'Message {i+1}: {type(msg).__name__} - {msg.content[:100]}...')

print('\n=== SESSION FIXED! ===')
print('Now "bunlar neler" should have proper context.')
