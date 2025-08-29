#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SIMPLE JSON + TÜRKÇe PROMPT MOD TESTİ
====================================
Sadece JSON format talimatları + Türkçe prompt içeren basit mod
"""

import os
import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

from langchain_openai import ChatOpenAI
from langchain_core.documents import Document
from src.graph_transformer.transformer import LLMGraphTransformer

def test_simple_json_turkish_mode():
    print("🇹🇷 SIMPLE JSON + TÜRKÇe PROMPT MOD TESTİ")
    print("=" * 60)
    
    # LLM'i oluştur
    llm = ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0,
        api_key=os.getenv("OPENAI_API_KEY")
    )
    
    # Transformer'ı simple JSON mode ile oluştur
    allowed_nodes = ['Customer', 'Policy', 'Company'] 
    allowed_relationships = ['HAS_POLICY', 'HAS_ENTITY']
    
    transformer = LLMGraphTransformer(
        llm=llm,
        allowed_nodes=allowed_nodes,
        allowed_relationships=allowed_relationships,
        ignore_tool_usage=True,  # Unstructured mode
        use_simple_json_mode=True,  # YENİ MOD!
        use_db_schema=False
    )
    
    print(f"🎯 Final allowed nodes: {transformer.allowed_nodes}")
    print(f"🔗 Final allowed relationships: {transformer.allowed_relationships}")
    print(f"⚙️ Function call modu: {transformer._function_call}")
    print(f"🎯 Allowed nodes: {allowed_nodes}")
    print(f"🔗 Allowed relationships: {allowed_relationships}")
    
    # Test metni
    test_text = """
Müşteri: Kemal Demir
Araç Plakası: 35 XYZ 456
Marka: Mercedes
Model: C-Class
Sigorta Şirketi: Zurich Sigorta
Acente: Güven Acentesi
Poliçe No: ZUR-2024-12345
"""

    print(f"\n🚀 LLM çağrılıyor (SIMPLE JSON + TÜRKÇe MODE)...")

    # Document oluştur ve transformer'ı çalıştır
    doc = Document(page_content=test_text.strip())
    
    try:
        # Transform et
        result = transformer.process_response(doc)
        
        print("✅ SONUÇLAR:")
        print(f"📊 Node sayısı: {len(result.nodes)}")
        print(f"📊 Relationship sayısı: {len(result.relationships)}")
        
        print(f"\n🏷️ ÇIKARILAN NODE'LAR:")
        for i, node in enumerate(result.nodes, 1):
            print(f"  {i}. {node.id} → {node.type}")
        
        print(f"\n🔗 ÇIKARILAN RELATIONSHIP'LER:")
        for i, rel in enumerate(result.relationships, 1):
            print(f"  {i}. {rel.source.id} --[{rel.type}]--> {rel.target.id}")
            
    except Exception as e:
        print(f"❌ Hata: {e}")
        import traceback
        traceback.print_exc()

    print("✅ Simple JSON + Türkçe test tamamlandı!")

if __name__ == "__main__":
    test_simple_json_turkish_mode()
