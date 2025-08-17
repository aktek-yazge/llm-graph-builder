#!/usr/bin/env python3
import sys
import os
sys.path.append('/Users/mehmeterdogan/python-projects/llm-graph-builder/backend/src')

from langchain_neo4j import Neo4jChatMessageHistory, Neo4jGraph
from langchain_core.messages import HumanMessage, AIMessage

# Test session'dan mesajları oku
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

# History oluştur
history = Neo4jChatMessageHistory(
    graph=graph,
    session_id=session_id
)

print("=" * 60)
print(f"SESSION: {session_id}")
print("=" * 60)

messages = history.messages
print(f"Total messages in session: {len(messages)}")

for i, message in enumerate(messages, 1):
    msg_type = "HumanMessage" if isinstance(message, HumanMessage) else "AIMessage"
    content = message.content[:100] + "..." if len(message.content) > 100 else message.content
    print(f"{i}. {msg_type}: {content}")

print("\n" + "=" * 60)
print("HUMAN MESSAGES ONLY:")
print("=" * 60)

human_messages = [msg for msg in messages if isinstance(msg, HumanMessage)]
print(f"Total human messages: {len(human_messages)}")

for i, message in enumerate(human_messages, 1):
    content = message.content[:100] + "..." if len(message.content) > 100 else message.content
    print(f"{i}. {content}")
