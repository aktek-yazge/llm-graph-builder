#!/usr/bin/env python3
"""
Quick Test - Sadece import ve basic test
"""

print("🔄 Importing modules...")

try:
    from src.intelligent_agent import IntelligentAgent
    print("✅ IntelligentAgent imported successfully")
    
    from langchain_neo4j import Neo4jGraph
    print("✅ Neo4jGraph imported successfully")
    
    import os
    
    # Agent'ı test et
    graph = Neo4jGraph(
        url=os.getenv('NEO4J_URI', 'bolt://localhost:7687'),
        username=os.getenv('NEO4J_USERNAME', 'neo4j'),
        password=os.getenv('NEO4J_PASSWORD', 'qwerty5555'),
        database=os.getenv('NEO4J_DATABASE', 'neo4j')
    )
    print("✅ Neo4j bağlantısı kuruldu")
    
    agent = IntelligentAgent(graph)
    print("✅ IntelligentAgent oluşturuldu")
    
    # Yeni özellikleri kontrol et
    print(f"📊 Token usage initialized: {agent.token_usage}")
    print(f"🎯 Successful findings initialized: {len(agent.successful_findings)}")
    print(f"🧠 Context memory initialized: '{agent.context_memory}'")
    
    print("\n🚀 Enhanced Agent başarıyla yüklendi!")
    
except Exception as e:
    print(f"❌ HATA: {e}")
    import traceback
    traceback.print_exc()
