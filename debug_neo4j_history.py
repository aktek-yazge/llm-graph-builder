#!/usr/bin/env python3
"""
Debug script to understand Neo4jChatMessageHistory behavior
"""
import os
import sys
sys.path.append('/workspace/backend')

from langchain_neo4j import Neo4jGraph, Neo4jChatMessageHistory
from dotenv import load_dotenv

load_dotenv()

def debug_session_messages():
    # Neo4j bağlantısı
    uri = os.getenv("NEO4J_URI")
    username = os.getenv("NEO4J_USERNAME")
    password = os.getenv("NEO4J_PASSWORD")
    database = os.getenv("NEO4J_DATABASE", "neo4j")

    graph = Neo4jGraph(
        url=uri,
        username=username,
        password=password,
        database=database,
        sanitize=True
    )

    session_id = "eaf518a3-f1f9-4abe-a2a4-2fba720617d4"

    print(f"🔍 Debugging session: {session_id}")

    # Neo4jChatMessageHistory oluştur
    history = Neo4jChatMessageHistory(
        graph=graph,
        session_id=session_id,
        window=50
    )

    print(f"📊 Messages count: {len(history.messages)}")

    # Tüm mesajları detaylı göster
    for i, msg in enumerate(history.messages):
        print(f"Message {i}:")
        print(f"  Type: {type(msg)}")
        print(f"  Content length: {len(msg.content) if hasattr(msg, 'content') else 'N/A'}")
        print(f"  Role: {getattr(msg, 'role', 'N/A')}")
        print(f"  Content preview: {msg.content[:100] if hasattr(msg, 'content') else 'N/A'}...")
        print()

if __name__ == "__main__":
    debug_session_messages()