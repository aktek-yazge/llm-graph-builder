#!/usr/bin/env python3
"""
Minimal Test - Tek soru ile token tracking test
"""

from src.intelligent_agent import IntelligentAgent
from langchain_neo4j import Neo4jGraph
import os
import json

def main():
    print("🚀 Token Tracking Test Başlatılıyor...")
    
    # Neo4j bağlantısını kur
    graph = Neo4jGraph(
        url=os.getenv('NEO4J_URI', 'bolt://localhost:7687'),
        username=os.getenv('NEO4J_USERNAME', 'neo4j'),
        password=os.getenv('NEO4J_PASSWORD', 'qwerty5555'),
        database=os.getenv('NEO4J_DATABASE', 'neo4j')
    )
    
    agent = IntelligentAgent(graph)
    query = "Ayça hanımın kaç adet poliçesi var?"
    
    print(f"🔍 Soru: {query}")
    print("=" * 50)
    
    result = agent.solve_question(query)
    
    # Token kullanımını göster
    token_usage = result.get('token_usage', {})
    print(f"📊 TOKEN KULLANIMI:")
    print(f"   Input Tokens: {token_usage.get('input_tokens', 0)}")
    print(f"   Output Tokens: {token_usage.get('output_tokens', 0)}")
    print(f"   Total Tokens: {token_usage.get('total_tokens', 0)}")
    
    # Başarılı bulguları göster
    findings = result.get('successful_findings', [])
    print(f"\n🎯 BAŞARILI BULGULAR ({len(findings)} adet):")
    for i, finding in enumerate(findings, 1):
        print(f"   {i}. Adım {finding['iteration']}: {finding['action']}")
        print(f"      Finding: {finding['finding'][:80]}...")
        print(f"      Score: {finding['relevance_score']:.3f}")
    
    # Context memory'i göster
    context_memory = result.get('context_memory', '')
    print(f"\n🧠 CONTEXT MEMORY ({len(context_memory)} karakter):")
    if context_memory:
        # İlk 300 karakteri göster
        print(context_memory[:300] + ("..." if len(context_memory) > 300 else ""))
    
    # LLM Prompt Structure'ı göster
    llm_prompt = result.get('llm_prompt_structure', '')
    print(f"\n📋 LLM PROMPT STRUCTURE ({len(llm_prompt)} karakter):")
    if llm_prompt:
        # İlk 500 karakteri göster
        print(llm_prompt[:500] + ("..." if len(llm_prompt) > 500 else ""))
    
    # Prompt'u dosyaya kaydet
    if llm_prompt:
        with open('agent_knowledge_report.md', 'w', encoding='utf-8') as f:
            f.write(llm_prompt)
        print(f"\n💾 Detaylı prompt structure 'agent_knowledge_report.md' dosyasına kaydedildi!")
    else:
        print("   Boş context memory")
    
    print(f"\n📋 CEVAP:")
    print(result['answer'][:300] + ("..." if len(result['answer']) > 300 else ""))
    
    print(f"\n✅ Test tamamlandı! İterasyon: {result['iterations']}")

if __name__ == "__main__":
    main()
