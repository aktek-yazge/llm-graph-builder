#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Langfuse Prompt Migration Script (Multi-Domain)

Bu script, farklı domain'ler için özelleştirilmiş prompt'ları
Langfuse Prompt Management sistemine yükler.

Kullanım:
    # Ortam değişkenlerini ayarla
    export LANGFUSE_PUBLIC_KEY="pk-..."
    export LANGFUSE_SECRET_KEY="sk-..."
    export LANGFUSE_HOST="http://localhost:3101"

    # Sigorta domain'i - Cypher mode (varsayılan)
    python scripts/migrate_prompt_to_langfuse.py --domain sigorta

    # Sigorta domain'i - DSL mode
    python scripts/migrate_prompt_to_langfuse.py --domain sigorta --mode dsl

    # Bakım domain'i (WAT Motor) - sadece Cypher
    python scripts/migrate_prompt_to_langfuse.py --domain bakim

Desteklenen Domain'ler:
    - sigorta: Sigorta poliçeleri, müşteriler, teminatlar (DSL + Cypher)
    - bakim: WAT Motor bakım/arıza yönetimi (sadece Cypher)

Notlar:
    - DSL mode: DSL thinking guide dahil (intent seçimi, DSL şablonları)
    - Cypher mode: Sadece Cypher tool kullanımı, DSL rehberi yok
    - Placeholder: {{schema_info}} - runtime'da gerçek şema ile değiştirilir
"""

import os
import sys
import argparse
from src.shared.langfuse_client import get_langfuse, create_prompt, get_prompt

# Merkezi Domain Registry - tüm domain tanımlamaları burada
from src.config.domains import get_domain_configs_dict

# Prompt yükleyici - merkezi registry kullanır
from src.langchain_deepagents.prompts import get_domain_prompts
from dotenv import load_dotenv

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

# =============================================================================
# DOMAIN CONFIGURATIONS (Merkezi registry'den)
# =============================================================================
# NOT: Yeni domain eklemek için src/config/domains.py dosyasını düzenleyin
DOMAIN_CONFIGS = get_domain_configs_dict()


def load_domain_prompts(domain: str):
    """
    Domain'e göre prompt modüllerini yükle.
    (Merkezi registry üzerinden)

    Args:
        domain: Domain adı (src/config/domains.py'de tanımlı)

    Returns:
        Dict of prompt components
    """
    prompts = get_domain_prompts(domain)
    return {
        "base": prompts["system_base"],
        "cypher_tools": prompts["tool_usage"],
        "content": prompts["content"],
    }


def build_prompt_template(domain: str, mode: str = "cypher") -> str:  # noqa: ARG001
    """
    Domain'e göre prompt template oluştur.

    Args:
        domain: Domain adı (src/config/domains.py'de tanımlı)
        mode: "cypher" (sadece cypher destekleniyor, ileride DSL eklenebilir)

    Returns:
        Tam prompt template ({{schema_info}} placeholder ile)
    """
    prompts = load_domain_prompts(domain)

    # Cypher mode - tüm domain'ler için
    base_prompt = prompts["base"] + prompts["cypher_tools"] + prompts["content"]

    # Schema placeholder ekle
    return base_prompt + "{{schema_info}}"


def main():
    """Ana migration fonksiyonu"""
    # Argüman parser
    parser = argparse.ArgumentParser(
        description="Langfuse Prompt Migration Script (Multi-Domain)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Domain'ler:
  sigorta   Sigorta poliçeleri, müşteriler, teminatlar
            Vector Index: vector
            Fulltext Index: chunk_text_fulltext
            
  bakim     WAT Motor bakım/arıza yönetimi  
            Vector Index: task_embedding_index

Tool'lar:
  - execute_cypher_query: Graph sorguları, metadata
  - execute_cypher_query_with_embedding: Semantic arama
  - Fulltext arama: execute_cypher_query + db.index.fulltext.queryNodes

Örnekler:
  python scripts/migrate_prompt_to_langfuse.py --domain sigorta   # sigorta
  python scripts/migrate_prompt_to_langfuse.py --domain bakim     # bakım (wat motor)
        """,
    )
    parser.add_argument(
        "--domain",
        choices=list(DOMAIN_CONFIGS.keys()),
        default="sigorta",
        help="Domain: sigorta (varsayılan) veya bakim",
    )
    parser.add_argument(
        "--name",
        default=None,
        help="Özel prompt adı (varsayılan: domain'e göre otomatik)",
    )

    args = parser.parse_args()

    # Domain config al
    domain_config = DOMAIN_CONFIGS[args.domain]

    # Prompt adı
    if args.name:
        prompt_name = args.name
    else:
        prompt_name = domain_config["prompt_name_template"]

    prompt_type = "text"
    prompt_labels = domain_config["labels"] + ["cypher"]

    print("=" * 60)
    print("🚀 Langfuse Prompt Migration Script (Multi-Domain)")
    print("=" * 60)
    print(f"🏷️ Domain: {args.domain.upper()}")
    print(f"   → {domain_config['description']}")
    print("   → Tool: execute_cypher_query, execute_cypher_query_with_embedding")
    if domain_config.get("fulltext_index"):
        print(f"   → Fulltext Index: {domain_config['fulltext_index']}")
    print(f"   → Vector Index: {domain_config['vector_index']}")

    # Ortam değişkenlerini kontrol et
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    host = os.environ.get("LANGFUSE_HOST", "http://localhost:3101")

    if not public_key or not secret_key:
        print(
            "\n❌ LANGFUSE_PUBLIC_KEY ve LANGFUSE_SECRET_KEY ortam değişkenleri gerekli!"
        )
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
    prompt_template = build_prompt_template(args.domain, "cypher")
    print(f"📏 Template uzunluğu: {len(prompt_template)} karakter")

    # Token tahmini (~4 char = 1 token)
    estimated_tokens = len(prompt_template) // 4
    print(f"📊 Tahmini token sayısı: ~{estimated_tokens}")

    # Mevcut prompt var mı kontrol et
    print(f"\n🔍 Mevcut prompt kontrol ediliyor: {prompt_name}")
    existing = get_prompt(
        name=prompt_name,
        prompt_type=prompt_type,
        label=prompt_labels[0] if prompt_labels else "production",
    )

    if existing:
        version = getattr(existing, "version", "unknown")
        print(f"⚠️ Prompt zaten mevcut: {prompt_name} (v{version})")

        response = input("\nMevcut prompt'u güncellemek ister misiniz? (y/N): ")
        if response.lower() != "y":
            print("❌ İşlem iptal edildi.")
            sys.exit(0)

        print("📝 Prompt güncellenecek...")
    else:
        print(f"📝 Yeni prompt oluşturulacak: {prompt_name}")

    # Tools listesi
    tools = [
        "execute_cypher_query",
        "execute_cypher_query_with_embedding",
        "add_source",
        "read_finding",
    ]

    # Prompt oluştur/güncelle
    success = create_prompt(
        name=prompt_name,
        prompt=prompt_template,
        prompt_type=prompt_type,
        labels=prompt_labels,
        config={
            "description": f"ReAct Agent - {domain_config['description']}",
            "domain": args.domain,
            "placeholder": "{{schema_info}}",
            "vector_index": domain_config["vector_index"],
            "fulltext_index": domain_config.get("fulltext_index"),
            "usage": f"Bu prompt, {args.domain} domain'i için kullanılır. "
            "schema_info placeholder'ı runtime'da gerçek veritabanı şeması ile değiştirilir.",
            "tools": tools,
        },
    )

    if success:
        print("\n" + "=" * 60)
        print("✅ Prompt başarıyla Langfuse'a yüklendi!")
        print("=" * 60)
        print(f"\n🏷️ Domain: {args.domain.upper()}")
        print(f"📋 Prompt: {prompt_name}")
        print(f"🏷️ Labels: {prompt_labels}")
        print(f"📏 Template uzunluğu: {len(prompt_template)} karakter")
        print(f"📊 Tahmini token: ~{estimated_tokens}")
        print(f"\n🔗 Langfuse UI: {host}")
        print("\n💡 Artık prompt'u Langfuse UI'dan düzenleyebilirsiniz!")
    else:
        print("\n❌ Prompt yüklenemedi!")
        sys.exit(1)


if __name__ == "__main__":
    main()
