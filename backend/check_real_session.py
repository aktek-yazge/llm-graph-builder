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

print('=== CHECKING SESSIONS WITH MESSAGES ===')

# İlk gerçek session'ı bul (debug olmayan)
result = graph.query('''
MATCH (s:Session)
RETURN s.id as session_id
ORDER BY s.id
''')

for record in result:
    session_id = record['session_id']
    if 'debug' not in session_id and 'test' not in session_id:
        print(f'Testing session: {session_id}')
        
        history = Neo4jChatMessageHistory(graph=graph, session_id=session_id)
        print(f'Message count: {len(history.messages)}')
        
        for i, msg in enumerate(history.messages):
            print(f'  Message {i+1}: {type(msg).__name__} - {msg.content[:100]}...')
        
        if len(history.messages) > 0:
            print(f'*** This session has messages! ***')
            break
        print()

print('=========================================')
