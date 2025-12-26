#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Langfuse Prompt Migration Script

Bu script, react_agent.py'deki CACHED_SYSTEM_PREFIX prompt'unu
Langfuse Prompt Management sistemine yükler.

Kullanım:
    # Ortam değişkenlerini ayarla
    export LANGFUSE_PUBLIC_KEY="pk-..."
    export LANGFUSE_SECRET_KEY="sk-..."
    export LANGFUSE_HOST="http://localhost:3101"
    
    # Script'i çalıştır
    python scripts/migrate_prompt_to_langfuse.py

Notlar:
    - Bu script sadece bir kez çalıştırılmalıdır (ilk kurulum)
    - Sonraki güncellemeler Langfuse UI'dan yapılmalıdır
    - Prompt adı: react-agent-system
    - Placeholder: {{schema_info}} - runtime'da gerçek şema ile değiştirilir
"""

import os
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from src.shared.langfuse_client import get_langfuse, create_prompt, get_prompt
from src.langchain_deepagents.react_agent import CACHED_SYSTEM_PREFIX


# Prompt Configuration
PROMPT_NAME = "react-agent-system"
PROMPT_TYPE = "text"
PROMPT_LABELS = ["production"]

# Prompt Template - react_agent.py'den import ediliyor
# {{schema_info}} placeholder'ı ekleniyor (runtime'da gerçek şema ile değiştirilir)
PROMPT_TEMPLATE = CACHED_SYSTEM_PREFIX + "{{schema_info}}"



def main():
    """Ana migration fonksiyonu"""
    print("=" * 60)
    print("🚀 Langfuse Prompt Migration Script")
    print("=" * 60)
    
    # Ortam değişkenlerini kontrol et
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    host = os.environ.get("LANGFUSE_HOST", "http://localhost:3101")
    
    if not public_key or not secret_key:
        print("❌ LANGFUSE_PUBLIC_KEY ve LANGFUSE_SECRET_KEY ortam değişkenleri gerekli!")
        print("\nÖrnek:")
        print('  export LANGFUSE_PUBLIC_KEY="pk-..."')
        print('  export LANGFUSE_SECRET_KEY="sk-..."')
        print('  export LANGFUSE_HOST="http://localhost:3101"')
        sys.exit(1)
    
    print(f"📡 Langfuse Host: {host}")
    print(f"📋 Prompt Name: {PROMPT_NAME}")
    print(f"🏷️ Labels: {PROMPT_LABELS}")
    
    # Langfuse bağlantısını test et
    langfuse = get_langfuse()
    if not langfuse:
        print("❌ Langfuse bağlantısı kurulamadı!")
        sys.exit(1)
    
    print("✅ Langfuse bağlantısı başarılı")
    
    # Mevcut prompt var mı kontrol et
    print(f"\n🔍 Mevcut prompt kontrol ediliyor: {PROMPT_NAME}")
    existing = get_prompt(
        name=PROMPT_NAME,
        prompt_type=PROMPT_TYPE,
        label=PROMPT_LABELS[0] if PROMPT_LABELS else "production",
    )
    
    if existing:
        version = getattr(existing, 'version', 'unknown')
        print(f"⚠️ Prompt zaten mevcut: {PROMPT_NAME} (v{version})")
        
        response = input("\nMevcut prompt'u güncellemek ister misiniz? (y/N): ")
        if response.lower() != 'y':
            print("❌ İşlem iptal edildi.")
            sys.exit(0)
        
        print("📝 Prompt güncellenecek...")
    else:
        print(f"📝 Yeni prompt oluşturulacak: {PROMPT_NAME}")
    
    # Prompt oluştur/güncelle
    success = create_prompt(
        name=PROMPT_NAME,
        prompt=PROMPT_TEMPLATE,
        prompt_type=PROMPT_TYPE,
        labels=PROMPT_LABELS,
        config={
            "description": "ReAct Agent system prompt for Neo4j graph queries",
            "placeholder": "{{schema_info}}",
            "usage": "This prompt is used by the ReAct Agent to query Neo4j database. The schema_info placeholder is replaced with the actual database schema at runtime.",
        },
    )
    
    if success:
        print("\n" + "=" * 60)
        print("✅ Prompt başarıyla Langfuse'a yüklendi!")
        print("=" * 60)
        print(f"\n📋 Prompt: {PROMPT_NAME}")
        print(f"🏷️ Labels: {PROMPT_LABELS}")
        print(f"📏 Template uzunluğu: {len(PROMPT_TEMPLATE)} karakter")
        print(f"\n🔗 Langfuse UI: {host}")
        print("\n💡 Artık prompt'u Langfuse UI'dan düzenleyebilirsiniz!")
    else:
        print("\n❌ Prompt yüklenemedi!")
        sys.exit(1)


if __name__ == "__main__":
    main()

