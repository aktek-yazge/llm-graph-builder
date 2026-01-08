#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Langfuse Prompt Migration Script

Bu script, react_agent.py'deki prompt constant'larını
Langfuse Prompt Management sistemine yükler.

Kullanım:
    # Ortam değişkenlerini ayarla
    export LANGFUSE_PUBLIC_KEY="pk-..."
    export LANGFUSE_SECRET_KEY="sk-..."
    export LANGFUSE_HOST="http://localhost:3101"
    
    # Cypher modu için (varsayılan)
    python scripts/migrate_prompt_to_langfuse.py
    
    # DSL modu için
    python scripts/migrate_prompt_to_langfuse.py --mode dsl

Notlar:
    - DSL mode: DSL thinking guide dahil (intent seçimi, DSL şablonları)
    - Cypher mode: Sadece Cypher tool kullanımı, DSL rehberi yok
    - Prompt adı: react-agent-system (mode label'da belirtilir)
    - Placeholder: {{schema_info}} - runtime'da gerçek şema ile değiştirilir
"""

import os
import sys
import argparse

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from src.shared.langfuse_client import get_langfuse, create_prompt, get_prompt
from src.langchain_deepagents.react_agent import (
    SHARED_SYSTEM_BASE,
    DSL_TOOL_USAGE,
    CYPHER_TOOL_USAGE,
    DSL_THINKING_GUIDE,
    SHARED_CONTENT,
)


def build_prompt_template(mode: str) -> str:
    """
    Mode'a göre prompt template oluştur.
    
    Args:
        mode: "dsl" veya "cypher"
        
    Returns:
        Tam prompt template ({{schema_info}} placeholder ile)
    """
    if mode == "cypher":
        # CYPHER MODE: DSL thinking guide dahil değil
        base_prompt = SHARED_SYSTEM_BASE + CYPHER_TOOL_USAGE + SHARED_CONTENT
    else:
        # DSL MODE: DSL thinking guide dahil
        base_prompt = SHARED_SYSTEM_BASE + DSL_TOOL_USAGE + DSL_THINKING_GUIDE + SHARED_CONTENT
    
    # Schema placeholder ekle
    return base_prompt + "{{schema_info}}"


def main():
    """Ana migration fonksiyonu"""
    # Argüman parser
    parser = argparse.ArgumentParser(
        description="Langfuse Prompt Migration Script",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Modlar:
  dsl     DSL thinking guide dahil (intent seçimi, DSL şablonları, vb.)
          Tool: execute_graph_dsl (önerilen), execute_cypher_query (fallback)
          
  cypher  Sadece Cypher tool kullanımı, DSL rehberi yok
          Tool: execute_cypher_query, execute_cypher_query_with_embedding

Örnekler:
  python scripts/migrate_prompt_to_langfuse.py              # cypher (varsayılan)
  python scripts/migrate_prompt_to_langfuse.py --mode dsl   # dsl modu
        """
    )
    parser.add_argument(
        "--mode",
        choices=["dsl", "cypher"],
        default="cypher",
        help="Prompt modu: cypher (varsayılan) veya dsl"
    )
    parser.add_argument(
        "--name",
        default=None,
        help="Özel prompt adı (varsayılan: react-agent-system)"
    )
    
    args = parser.parse_args()
    
    # Prompt Configuration
    prompt_name = args.name or "react-agent-system"
    prompt_type = "text"
    prompt_labels = ["production", args.mode]
    
    print("=" * 60)
    print("🚀 Langfuse Prompt Migration Script")
    print("=" * 60)
    print(f"📋 Mode: {args.mode.upper()}")
    
    # Mode açıklaması
    if args.mode == "cypher":
        print("   → Cypher mode: Doğrudan Cypher yazma")
        print("   → DSL thinking guide DAHİL DEĞİL")
        print("   → Tool: execute_cypher_query, execute_cypher_query_with_embedding")
    else:
        print("   → DSL mode: Ontology-driven sorgulama")
        print("   → DSL thinking guide DAHİL")
        print("   → Tool: execute_graph_dsl (önerilen), execute_cypher_query (fallback)")
    
    # Ortam değişkenlerini kontrol et
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    host = os.environ.get("LANGFUSE_HOST", "http://localhost:3101")
    
    if not public_key or not secret_key:
        print("\n❌ LANGFUSE_PUBLIC_KEY ve LANGFUSE_SECRET_KEY ortam değişkenleri gerekli!")
        print("\nÖrnek:")
        print('  export LANGFUSE_PUBLIC_KEY="pk-..."')
        print('  export LANGFUSE_SECRET_KEY="sk-..."')
        print('  export LANGFUSE_HOST="http://localhost:3101"')
        sys.exit(1)
    
    print(f"\n📡 Langfuse Host: {host}")
    print(f"📋 Prompt Name: {prompt_name}")
    print(f"🏷️ Labels: {prompt_labels}")
    
    # Langfuse bağlantısını test et
    langfuse = get_langfuse()
    if not langfuse:
        print("❌ Langfuse bağlantısı kurulamadı!")
        sys.exit(1)
    
    print("✅ Langfuse bağlantısı başarılı")
    
    # Prompt template oluştur
    prompt_template = build_prompt_template(args.mode)
    print(f"📏 Template uzunluğu: {len(prompt_template)} karakter")
    
    # Mevcut prompt var mı kontrol et
    print(f"\n🔍 Mevcut prompt kontrol ediliyor: {prompt_name}")
    existing = get_prompt(
        name=prompt_name,
        prompt_type=prompt_type,
        label=prompt_labels[0] if prompt_labels else "production",
    )
    
    if existing:
        version = getattr(existing, 'version', 'unknown')
        print(f"⚠️ Prompt zaten mevcut: {prompt_name} (v{version})")
        
        response = input("\nMevcut prompt'u güncellemek ister misiniz? (y/N): ")
        if response.lower() != 'y':
            print("❌ İşlem iptal edildi.")
            sys.exit(0)
        
        print("📝 Prompt güncellenecek...")
    else:
        print(f"📝 Yeni prompt oluşturulacak: {prompt_name}")
    
    # Prompt oluştur/güncelle
    success = create_prompt(
        name=prompt_name,
        prompt=prompt_template,
        prompt_type=prompt_type,
        labels=prompt_labels,
        config={
            "description": f"ReAct Agent system prompt for Neo4j graph queries ({args.mode.upper()} mode)",
            "mode": args.mode,
            "placeholder": "{{schema_info}}",
            "usage": f"This prompt is used by the ReAct Agent in {args.mode.upper()} mode. The schema_info placeholder is replaced with the actual database schema at runtime.",
            "tools": ["execute_cypher_query", "execute_cypher_query_with_embedding"] if args.mode == "cypher" 
                     else ["execute_graph_dsl", "execute_cypher_query"],
        },
    )
    
    if success:
        print("\n" + "=" * 60)
        print("✅ Prompt başarıyla Langfuse'a yüklendi!")
        print("=" * 60)
        print(f"\n📋 Prompt: {prompt_name}")
        print(f"🏷️ Labels: {prompt_labels}")
        print(f"📋 Mode: {args.mode.upper()}")
        print(f"📏 Template uzunluğu: {len(prompt_template)} karakter")
        print(f"\n🔗 Langfuse UI: {host}")
        print("\n💡 Artık prompt'u Langfuse UI'dan düzenleyebilirsiniz!")
        print(f"\n⚠️ NOT: Bu prompt'u kullanmak için REACT_TOOL_MODE={args.mode} ayarlayın!")
    else:
        print("\n❌ Prompt yüklenemedi!")
        sys.exit(1)


if __name__ == "__main__":
    main()
