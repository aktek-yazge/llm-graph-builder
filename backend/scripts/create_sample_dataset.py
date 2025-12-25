#!/usr/bin/env python3
"""
Örnek Dataset Oluşturma Script'i

Bu script, test amaçlı örnek bir Langfuse dataset oluşturur.

Kullanım:
    cd backend
    uv run python scripts/create_sample_dataset.py
"""

import os
import sys

# Backend modüllerini import edebilmek için path ekle
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, backend_dir)

# .preview.env dosyasını yükle
from dotenv import load_dotenv
env_file = os.path.join(backend_dir, ".preview.env")
if os.path.exists(env_file):
    load_dotenv(env_file)
    print(f"✅ Loaded env from: {env_file}")

from src.shared.langfuse_client import (
    create_dataset, add_dataset_item, get_dataset, is_langfuse_enabled
)


def main():
    if not is_langfuse_enabled():
        print("❌ Langfuse aktif değil!")
        print("   .preview.env dosyasında şunları kontrol edin:")
        print("   LANGFUSE_ENABLED=true")
        print("   LANGFUSE_HOST=http://localhost:3101")
        print("   LANGFUSE_PUBLIC_KEY=pk-lf-...")
        print("   LANGFUSE_SECRET_KEY=sk-lf-...")
        return
    
    # ==========================================================
    # 1. DATASET OLUŞTUR
    # ==========================================================
    dataset_name = "sigorta-qa-test"
    
    print(f"\n📦 Dataset oluşturuluyor: {dataset_name}")
    
    try:
        dataset = create_dataset(
            name=dataset_name,
            description="Sigorta RAG sistemi için test soruları",
            metadata={
                "version": "1.0",
                "created_by": "admin",
                "purpose": "regression_testing"
            }
        )
        if dataset:
            print(f"✅ Dataset oluşturuldu: {getattr(dataset, 'id', 'unknown')}")
        else:
            print("⚠️ Dataset oluşturuldu ama id alınamadı")
    except Exception as e:
        if "already exists" in str(e).lower():
            print(f"ℹ️ Dataset zaten mevcut: {dataset_name}")
            dataset = get_dataset(dataset_name)
        else:
            print(f"❌ Dataset oluşturulamadı: {e}")
            return
    
    # ==========================================================
    # 2. TEST SORULARI EKLE
    # ==========================================================
    
    test_questions = [
        # Basit sayım soruları
        {
            "input": {
                "question": "Kaç tane poliçe var?"
            },
            "expected_output": {
                "answer_type": "count",
                "must_contain": ["poliçe", "adet"]
            },
            "metadata": {
                "category": "count",
                "difficulty": "easy",
                "expected_tool": "neo4j_cypher"
            }
        },
        {
            "input": {
                "question": "Sistemde kaç müşteri kayıtlı?"
            },
            "expected_output": {
                "answer_type": "count",
                "must_contain": ["müşteri"]
            },
            "metadata": {
                "category": "count",
                "difficulty": "easy",
                "expected_tool": "neo4j_cypher"
            }
        },
        
        # Detay sorguları
        {
            "input": {
                "question": "Ahmet Yılmaz'ın poliçe detayları nelerdir?"
            },
            "expected_output": {
                "answer_type": "detail",
                "must_contain": ["poliçe", "prim"],
                "should_have_structure": True
            },
            "metadata": {
                "category": "detail",
                "difficulty": "medium",
                "expected_tool": "neo4j_cypher"
            }
        },
        {
            "input": {
                "question": "POL-2024-001 numaralı poliçenin kapsamı nedir?"
            },
            "expected_output": {
                "answer_type": "detail",
                "must_contain": ["kapsam", "teminat"]
            },
            "metadata": {
                "category": "detail",
                "difficulty": "medium",
                "expected_tool": "vector_search"
            }
        },
        
        # Karşılaştırma/Sıralama
        {
            "input": {
                "question": "En yüksek primli 5 poliçeyi listele"
            },
            "expected_output": {
                "answer_type": "list",
                "must_contain": ["poliçe", "prim", "TL"],
                "expected_count": 5
            },
            "metadata": {
                "category": "ranking",
                "difficulty": "hard",
                "expected_tool": "neo4j_cypher"
            }
        },
        
        # Zaman bazlı sorgular
        {
            "input": {
                "question": "2024 yılında oluşturulan poliçeler hangileri?"
            },
            "expected_output": {
                "answer_type": "list",
                "must_contain": ["2024", "poliçe"]
            },
            "metadata": {
                "category": "temporal",
                "difficulty": "medium",
                "expected_tool": "neo4j_cypher"
            }
        },
        
        # İlişki sorguları
        {
            "input": {
                "question": "Hangi müşteriler birden fazla poliçeye sahip?"
            },
            "expected_output": {
                "answer_type": "list",
                "must_contain": ["müşteri", "poliçe"]
            },
            "metadata": {
                "category": "relationship",
                "difficulty": "hard",
                "expected_tool": "neo4j_cypher"
            }
        },
        
        # Doküman içerik sorguları (RAG)
        {
            "input": {
                "question": "Kasko poliçesi hangi durumları kapsamaz?"
            },
            "expected_output": {
                "answer_type": "explanation",
                "must_contain": ["kapsam dışı", "istisna"],
                "source": "document"
            },
            "metadata": {
                "category": "rag",
                "difficulty": "medium",
                "expected_tool": "vector_search"
            }
        },
        
        # Edge case - belirsiz sorular
        {
            "input": {
                "question": "En iyi poliçe hangisi?"
            },
            "expected_output": {
                "answer_type": "clarification_needed",
                "should_ask_clarification": True
            },
            "metadata": {
                "category": "edge_case",
                "difficulty": "hard",
                "test_type": "ambiguous_query"
            }
        },
        
        # Edge case - veritabanında olmayan bilgi
        {
            "input": {
                "question": "2030 yılının poliçe projeksiyonları nelerdir?"
            },
            "expected_output": {
                "answer_type": "not_found",
                "should_acknowledge_missing": True
            },
            "metadata": {
                "category": "edge_case",
                "difficulty": "medium",
                "test_type": "future_data"
            }
        }
    ]
    
    print(f"\n📝 {len(test_questions)} test sorusu ekleniyor...\n")
    
    for i, item in enumerate(test_questions, 1):
        try:
            result = add_dataset_item(
                dataset_name=dataset_name,
                input_data=item["input"],
                expected_output=item.get("expected_output"),
                metadata=item.get("metadata")
            )
            category = item.get("metadata", {}).get("category", "unknown")
            print(f"  [{i}/{len(test_questions)}] ✅ {category}: {item['input']['question'][:40]}...")
        except Exception as e:
            print(f"  [{i}/{len(test_questions)}] ❌ Hata: {e}")
    
    # ==========================================================
    # 3. ÖZET
    # ==========================================================
    print("\n" + "=" * 60)
    print("📊 Dataset Oluşturma Tamamlandı")
    print("=" * 60)
    print(f"   Dataset: {dataset_name}")
    print(f"   Toplam Soru: {len(test_questions)}")
    print(f"\n🔗 Langfuse UI'da görüntülemek için:")
    print(f"   http://localhost:3101 → Datasets → {dataset_name}")
    print(f"\n🧪 Experiment çalıştırmak için:")
    print(f"   python scripts/run_dataset_experiment.py --dataset {dataset_name} --dry-run")


if __name__ == "__main__":
    main()

