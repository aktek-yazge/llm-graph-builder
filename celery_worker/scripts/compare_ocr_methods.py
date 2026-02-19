#!/usr/bin/env python3
"""
OCR Yöntemlerini Karşılaştırma Testi

1. Hybrid A2A OCR (Gemini + Claude)
2. Agentic OCR (Claude + crop tools)

Karşılaştırma: Süre, Token, Maliyet, Kalite
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

from src.hybrid_a2a_ocr import HybridA2AOCR
from src.agentic_ocr import AgenticOCR


def find_test_images(test_dir: str) -> list:
    """Test görüntülerini bul."""
    image_dir = os.path.join(test_dir, "images")
    images = sorted(Path(image_dir).glob("*.png"))
    if not images:
        images = sorted(Path(image_dir).glob("*.jpg"))
    return [str(img) for img in images]


def extract_company_name(test_dir: str) -> str:
    """Dizin adından şirket adını çıkar."""
    dir_name = os.path.basename(test_dir)
    # "Aksa-09.03.2016-9028-GENEL KURUL..." -> "Aksa"
    return dir_name.split("-")[0]


async def test_hybrid_a2a(images: list, output_dir: str, file_name: str) -> dict:
    """Hybrid A2A OCR testi."""
    print("\n" + "=" * 60)
    print("TEST 1: HYBRID A2A OCR (Gemini + Claude)")
    print("=" * 60)

    start_time = time.time()

    ocr = HybridA2AOCR()
    result = await ocr.process(
        image_list=images,
        file_name=file_name,
        output_dir=output_dir,
    )

    total_time = time.time() - start_time

    # Metadata'dan bilgi al
    metadata = result.get("metadata", {})
    data = result.get("data", {})

    # Token ve maliyet hesapla
    stats = {
        "method": "Hybrid A2A",
        "total_time_sec": round(total_time, 2),
        "pages": len(images),
        "stage1_time_sec": metadata.get("stage1_duration_ms", 0) / 1000,
        "stage2_time_sec": metadata.get("stage2_duration_ms", 0) / 1000,
        "gemini_chars": metadata.get("stage1_chars", 0),
        "total_duration_ms": metadata.get("total_duration_ms", 0),
        "chunks_count": len(data.get("chunks", [])),
        "nodes_count": len(data.get("nodes", [])),
        "relationships_count": len(data.get("relationships", [])),
    }

    print(f"\n✅ Hybrid A2A tamamlandı: {total_time:.1f}s")

    return stats


async def test_agentic_ocr(images: list, output_dir: str, file_name: str) -> dict:
    """Agentic OCR testi."""
    print("\n" + "=" * 60)
    print("TEST 2: AGENTIC OCR (Claude + Crop Tools)")
    print("=" * 60)

    start_time = time.time()

    # AgenticOCR model ve mode'u env variable'dan alır:
    # OCR_VISION_MODEL=claude-opus-4-5-20250514
    # OCR_PROMPT_MODE=goal_driven (tool kullanımı için)
    ocr = AgenticOCR()

    result = await ocr.process(
        image_list=images,
        file_name=file_name,  # Şirket adı buradan çıkarılıyor
        output_dir=output_dir,
    )

    total_time = time.time() - start_time

    # Metadata'dan token bilgisi al
    metadata = result.get("metadata", {})

    # Token ve maliyet hesapla
    stats = {
        "method": "Agentic OCR",
        "total_time_sec": round(total_time, 2),
        "pages": len(images),
        "input_tokens": metadata.get("input_tokens", 0),
        "output_tokens": metadata.get("output_tokens", 0),
        "total_cost_usd": metadata.get("total_cost_usd", 0),
        "chunks_count": len(result.get("chunks", [])),
        "nodes_count": len(result.get("nodes", [])),
        "relationships_count": len(result.get("relationships", [])),
        "tool_calls": metadata.get("tool_calls_count", 0),
    }

    print(f"\n✅ Agentic OCR tamamlandı: {total_time:.1f}s")

    return stats


def print_comparison(hybrid_stats: dict, agentic_stats: dict):
    """Karşılaştırma tablosu yazdır."""
    print("\n" + "=" * 70)
    print("KARŞILAŞTIRMA SONUÇLARI")
    print("=" * 70)

    print(f"\n{'Metrik':<35} {'Hybrid A2A':<20} {'Agentic OCR':<20}")
    print("-" * 75)

    # Süre
    print(f"{'Toplam Süre (s)':<35} {hybrid_stats['total_time_sec']:<20} {agentic_stats['total_time_sec']:<20}")

    # Stage süreleri (Hybrid)
    if 'stage1_time_sec' in hybrid_stats:
        print(f"{'  - Stage 1 (Gemini OCR)':<35} {hybrid_stats['stage1_time_sec']:<20.1f} {'N/A':<20}")
        print(f"{'  - Stage 2 (Claude Extract)':<35} {hybrid_stats['stage2_time_sec']:<20.1f} {'N/A':<20}")

    # Gemini chars
    if 'gemini_chars' in hybrid_stats:
        print(f"{'Gemini OCR Chars':<35} {hybrid_stats['gemini_chars']:<20} {'N/A':<20}")

    # Token
    agentic_tokens = agentic_stats.get('input_tokens', 0) + agentic_stats.get('output_tokens', 0)
    print(f"{'Claude Tokens (input+output)':<35} {'N/A':<20} {agentic_tokens:<20}")

    # Tool calls
    if 'tool_calls' in agentic_stats:
        print(f"{'Tool Calls':<35} {'N/A':<20} {agentic_stats['tool_calls']:<20}")

    # Maliyet
    hybrid_cost = hybrid_stats.get('total_cost_usd', 0)
    agentic_cost = agentic_stats.get('total_cost_usd', 0)
    print(f"{'Tahmini Maliyet (USD)':<35} {'~' + str(hybrid_cost):<20} {'~' + str(agentic_cost):<20}")

    # Sonuç
    print("-" * 75)
    print(f"{'Chunk Sayısı':<35} {hybrid_stats['chunks_count']:<20} {agentic_stats['chunks_count']:<20}")
    print(f"{'Node Sayısı':<35} {hybrid_stats['nodes_count']:<20} {agentic_stats['nodes_count']:<20}")
    print(f"{'Relationship Sayısı':<35} {hybrid_stats['relationships_count']:<20} {agentic_stats['relationships_count']:<20}")

    # Kazanan
    print("\n" + "=" * 75)
    print("KAZANANLAR")
    print("-" * 75)
    
    time_winner = "Hybrid A2A" if hybrid_stats['total_time_sec'] < agentic_stats['total_time_sec'] else "Agentic OCR"
    time_diff = abs(hybrid_stats['total_time_sec'] - agentic_stats['total_time_sec'])
    
    print(f"{'Süre:':<20} {time_winner} ({time_diff:.1f}s daha hızlı)")


async def main():
    # Test dizini
    test_dir = "/workspace/celery_worker/output_celery/Aksa-09.03.2016-9028-GENEL KURUL TOPLANTIYA ÇAĞIRI"

    # Custom path varsa
    if len(sys.argv) > 1:
        test_dir = sys.argv[1]

    # Görüntüleri bul
    images = find_test_images(test_dir)
    if not images:
        print(f"Hata: {test_dir}/images dizininde görüntü bulunamadı")
        sys.exit(1)

    company = extract_company_name(test_dir)

    print(f"\n{'#' * 60}")
    print(f"OCR YÖNTEMLERİ KARŞILAŞTIRMA TESTİ")
    print(f"{'#' * 60}")
    print(f"Şirket: {company}")
    print(f"Görüntü sayısı: {len(images)}")
    print(f"Test dizini: {test_dir}")

    # Output dizinleri
    hybrid_output = os.path.join(test_dir, "compare_hybrid_a2a")
    agentic_output = os.path.join(test_dir, "compare_agentic")

    # Dosya adı (AgenticOCR için)
    file_name = os.path.basename(test_dir)

    # Test 1: Hybrid A2A
    hybrid_stats = await test_hybrid_a2a(images, hybrid_output, file_name)

    # Test 2: Agentic OCR
    agentic_stats = await test_agentic_ocr(images, agentic_output, file_name)

    # Karşılaştırma
    print_comparison(hybrid_stats, agentic_stats)

    # Sonuçları kaydet
    results = {
        "test_dir": test_dir,
        "company": company,
        "image_count": len(images),
        "hybrid_a2a": hybrid_stats,
        "agentic_ocr": agentic_stats,
    }

    results_file = os.path.join(test_dir, "comparison_results.json")
    with open(results_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\n📊 Sonuçlar kaydedildi: {results_file}")


if __name__ == "__main__":
    asyncio.run(main())
