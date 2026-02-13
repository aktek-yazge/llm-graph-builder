#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Grid + Crop + OCR Agent Test Script

AgenticOCR'ın draw_grid ve crop_by_cells tool'larını kullanarak
çok sütunlu TSG sayfalarında hedef şirketin ilanını doğru kırpıp
OCR yapabilmesini test eder.

Usage:
    cd /workspace/celery_worker
    python scripts/test_grid_ocr.py [image_path]

Varsayılan: Aksa-09.03.2016-9028 ilk sayfası
"""

import os
import sys
import asyncio
import json
import logging

# Path setup
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# .env yükle
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("test_grid_ocr")


# Varsayılan test görseli
DEFAULT_IMAGE = os.path.join(
    os.path.dirname(__file__), "..",
    "output_celery",
    "Aksa-09.03.2016-9028-GENEL KURUL TOPLANTIYA ÇAĞIRI",
    "images",
    "Aksa-09.03.2016-9028-GENEL KURUL TOPLANTIYA ÇAĞIRI_page_001.png",
)

DEFAULT_FILE_NAME = "Aksa-09.03.2016-9028-GENEL KURUL TOPLANTIYA ÇAĞIRI"


async def main():
    image_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_IMAGE
    file_name = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_FILE_NAME

    if not os.path.exists(image_path):
        print(f"❌ Görüntü bulunamadı: {image_path}")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"🧪 GRID + CROP + OCR AGENT TEST")
    print(f"{'='*60}")
    print(f"📄 Görüntü: {image_path}")
    print(f"🏢 Dosya: {file_name}")
    print(f"🤖 Model: {os.environ.get('OCR_VISION_MODEL', '?')}")
    print(f"📋 Mode: {os.environ.get('OCR_PROMPT_MODE', '?')}")
    print(f"{'='*60}\n")

    # Neo4j bağlantısı (opsiyonel)
    graph = None
    try:
        from langchain_neo4j import Neo4jGraph
        graph = Neo4jGraph(
            url=os.environ.get("NEO4J_URI"),
            username=os.environ.get("NEO4J_USERNAME"),
            password=os.environ.get("NEO4J_PASSWORD"),
            database=os.environ.get("NEO4J_DATABASE", "neo4j"),
        )
        print("✅ Neo4j bağlantısı başarılı")
    except Exception as e:
        print(f"⚠️ Neo4j bağlantısı yok: {e}")
        print("   (Agent neo4j_query tool'unu kullanamayacak)\n")

    # AgenticOCR başlat
    from src.agentic_ocr import AgenticOCR

    ocr = AgenticOCR()
    await ocr.initialize()
    print(f"✅ AgenticOCR başlatıldı (provider: {ocr._model_provider})\n")

    # Tek sayfayı işle
    output_dir = os.path.join(os.path.dirname(image_path), "..", "test_output")
    os.makedirs(output_dir, exist_ok=True)

    result = await ocr.process(
        image_list=[image_path],
        file_name=file_name,
        graph=graph,
        output_dir=output_dir,
    )

    # Sonuçları göster
    print(f"\n{'='*60}")
    print(f"📊 SONUÇLAR")
    print(f"{'='*60}")
    print(f"Status: {result.get('status', '?')}")

    if result.get("status") == "success":
        data = result.get("data", {})
        if isinstance(data, list) and data:
            data = data[0]

        # JSON varsa detayları göster
        if isinstance(data, dict):
            chunks = data.get("chunks", [])
            nodes = data.get("nodes", [])
            rels = data.get("relationships", [])

            print(f"📝 Chunks: {len(chunks)}")
            for c in chunks:
                text_preview = c.get("text", "")[:80]
                print(f"   - {c.get('id', '?')}: {text_preview}...")

            print(f"\n🔵 Nodes: {len(nodes)}")
            for n in nodes:
                print(f"   - [{n.get('label', '?')}] {n.get('id', '?')} → {n.get('properties', {}).get('name', '?')}")

            print(f"\n🔗 Relationships: {len(rels)}")
            for r in rels:
                print(f"   - {r.get('from_id', '?')} --[{r.get('type', '?')}]--> {r.get('to_id', '?')}")

        # Markdown varsa göster
        md = result.get("markdown", "")
        if md:
            print(f"\n📄 Markdown ({len(md)} chars):")
            print(md[:500])
            if len(md) > 500:
                print("...")

    else:
        print(f"❌ Hata: {result.get('error', 'Bilinmeyen hata')}")

    # JSON kaydet
    output_json = os.path.join(output_dir, "test_result.json")
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n💾 Sonuç kaydedildi: {output_json}")

    # Kırpılan görselleri listele (crops/ dizini)
    crops_dir = os.path.join(os.path.dirname(image_path), "..", "crops")
    if os.path.exists(crops_dir):
        crop_files = sorted(os.listdir(crops_dir))
        if crop_files:
            print(f"\n📸 Oluşturulan görseller ({crops_dir}):")
            for f in crop_files:
                fpath = os.path.join(crops_dir, f)
                size_kb = os.path.getsize(fpath) // 1024
                print(f"   - {f} ({size_kb}KB)")

    print(f"\n{'='*60}")
    print(f"✅ TEST TAMAMLANDI")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
