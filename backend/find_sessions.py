from langchain_neo4j import Neo4jGraph
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

# Tüm session'ları ve mesajları bul - LangChain'in kullandığı yapı
result = graph.query('''
MATCH (s:Session)
OPTIONAL MATCH (s)-[:LAST_MESSAGE]->(lm:Message)
OPTIONAL MATCH (m:Message {session_id: s.id})
RETURN s.id as session_id, 
       count(m) as message_count,
       collect(m.role) as roles,
       collect(substring(m.content, 0, 50)) as content_previews
ORDER BY s.id
''')

print('=== ALL NEO4J CHAT SESSIONS ===')
for record in result:
    print(f"Session: {record['session_id']}")
    print(f"  Messages: {record['message_count']}")
    print(f"  Roles: {record['roles']}")
    print(f"  Content: {record['content_previews']}")
    print()
print('================================')
