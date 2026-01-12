#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WAT Motor Prompt Migration Script

Bu script, WAT Motor bakım domain'i için özelleştirilmiş prompt'u
Langfuse Prompt Management sistemine yükler.

Kullanım:
    # Ortam değişkenlerini ayarla
    export LANGFUSE_PUBLIC_KEY="pk-..."
    export LANGFUSE_SECRET_KEY="sk-..."
    export LANGFUSE_HOST="http://localhost:3101"
    
    # WAT Motor prompt'unu yükle
    python scripts/migrate_wat_motor_prompt.py

Notlar:
    - Sadece Cypher mode (DSL yok)
    - Bakım/arıza domain'ine özel
    - Placeholder: {{schema_info}} - runtime'da gerçek şema ile değiştirilir
    - Prompt adı: react-agent-wat-motor
"""

import os
import sys
import argparse

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from src.shared.langfuse_client import get_langfuse, create_prompt, get_prompt
from src.langchain_deepagents.prompts.bakim import (
    WAT_SYSTEM_BASE,
    WAT_TOOL_USAGE,
    WAT_CONTENT,
)


def build_wat_motor_prompt() -> str:
    """
    WAT Motor için prompt template oluştur.
    
    Returns:
        Tam prompt template ({{schema_info}} placeholder ile)
    """
    # WAT Motor prompt - sadece Cypher mode
    base_prompt = WAT_SYSTEM_BASE + WAT_TOOL_USAGE + WAT_CONTENT
    
    # Schema placeholder ekle
    return base_prompt + "{{schema_info}}"


def main():
    """Ana migration fonksiyonu"""
    # Argüman parser
    parser = argparse.ArgumentParser(
        description="WAT Motor Prompt Migration Script",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
WAT Motor - Bakım ve Arıza Yönetim Agent'ı

Bu script, endüstriyel bakım domain'i için özelleştirilmiş prompt'u
Langfuse'a yükler.

Özellikler:
  - Sadece Cypher mode (DSL yok)
  - Bakım domain'ine özel örnekler
  - Semantic search: task_embedding_index
  - ~200 satır (vs sigorta ~700 satır)

Örnekler:
  python scripts/migrate_wat_motor_prompt.py
  python scripts/migrate_wat_motor_prompt.py --name custom-name
        """
    )
    parser.add_argument(
        "--name",
        default="react-agent-wat-motor",
        help="Prompt adı (varsayılan: react-agent-wat-motor)"
    )
    parser.add_argument(
        "--label",
        default="production",
        help="Prompt label (varsayılan: production)"
    )
    
    args = parser.parse_args()
    
    # Prompt Configuration
    prompt_name = args.name
    prompt_type = "text"
    prompt_labels = [args.label, "bakim", "wat-motor"]
    
    print("=" * 60)
    print("🔧 WAT Motor Prompt Migration Script")
    print("=" * 60)
    print("📋 Domain: Bakım ve Arıza Yönetimi")
    print("   → Sadece Cypher mode")
    print("   → Tool: execute_cypher_query, execute_cypher_query_with_embedding")
    print("   → Vector Index: task_embedding_index")
    
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
    prompt_template = build_wat_motor_prompt()
    print(f"📏 Template uzunluğu: {len(prompt_template)} karakter")
    
    # Token tahmini (~4 char = 1 token)
    estimated_tokens = len(prompt_template) // 4
    print(f"📊 Tahmini token sayısı: ~{estimated_tokens}")
    
    # Mevcut prompt var mı kontrol et
    print(f"\n🔍 Mevcut prompt kontrol ediliyor: {prompt_name}")
    existing = get_prompt(
        name=prompt_name,
        prompt_type=prompt_type,
        label=args.label,
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
            "description": "WAT Motor - Bakım ve Arıza Yönetim Agent'ı için sistem prompt'u",
            "domain": "bakim",
            "mode": "cypher",
            "placeholder": "{{schema_info}}",
            "vector_index": "task_embedding_index",
            "usage": "Bu prompt, WAT Motor bakım yönetim sistemi için özelleştirilmiştir. "
                     "schema_info placeholder'ı runtime'da gerçek veritabanı şeması ile değiştirilir.",
            "tools": [
                "execute_cypher_query",
                "execute_cypher_query_with_embedding",
                "add_source",
                "read_finding",
            ],
            "nodes": ["Task", "Person", "Equipment", "CostCenter", "Status", "ImpactType"],
            "relationships": [
                "CREATED_REQUEST",
                "WORKED_ON", 
                "FOR_EQUIPMENT",
                "HAS_STATUS",
                "BELONGS_TO",
                "HAS_IMPACT",
            ],
        },
    )
    
    if success:
        print("\n" + "=" * 60)
        print("✅ WAT Motor prompt başarıyla Langfuse'a yüklendi!")
        print("=" * 60)
        print(f"\n📋 Prompt: {prompt_name}")
        print(f"🏷️ Labels: {prompt_labels}")
        print(f"📏 Template uzunluğu: {len(prompt_template)} karakter")
        print(f"📊 Tahmini token: ~{estimated_tokens}")
        print(f"\n🔗 Langfuse UI: {host}")
        print("\n💡 Artık prompt'u Langfuse UI'dan düzenleyebilirsiniz!")
        print("\n📌 Kullanım:")
        print("   1. Neo4j: bolt://localhost:7688 (neo4j/watmotor123)")
        print("   2. Agent çalıştırılırken prompt_name='react-agent-wat-motor' kullan")
    else:
        print("\n❌ Prompt yüklenemedi!")
        sys.exit(1)


if __name__ == "__main__":
    main()
