#!/usr/bin/env python3
import sys
import os
sys.path.append('/Users/mehmeterdogan/python-projects/llm-graph-builder/backend/src')

from langchain_neo4j import Neo4jChatMessageHistory, Neo4jGraph

# Test session'ı temizle
session_id = "221c62f3-9794-486c-9815-100ebde62c86"

# Neo4j bağlantısı
URI = os.getenv('NEO4J_URI', 'neo4j://localhost:7687')
USERNAME = os.getenv('NEO4J_USERNAME', 'neo4j')
PASSWORD = os.getenv('NEO4J_PASSWORD', 'qwerty5555')
DATABASE = os.getenv('NEO4J_DATABASE', 'neo4j')

# Graph bağlantısı oluştur
graph = Neo4jGraph(
    url=URI,
    username=USERNAME,
    password=PASSWORD,
    database=DATABASE
)

# History oluştur ve temizle
history = Neo4jChatMessageHistory(
    graph=graph,
    session_id=session_id
)

print(f"Before clear: {len(history.messages)} messages")
history.clear()
print(f"After clear: {len(history.messages)} messages")
print("Session cleared!")
