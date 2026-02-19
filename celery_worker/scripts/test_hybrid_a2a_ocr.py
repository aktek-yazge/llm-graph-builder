#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hybrid A2A OCR Test Script

HybridA2AOCR pipeline'ını test eder:
1. Gemini 2.0 Flash: Paralel full-page OCR
2. Claude Opus 4.5: Sequential extraction with continuation context

Usage:
    cd /workspace/celery_worker
    python scripts/test_hybrid_a2a_ocr.py [image_path_or_dir] [file_name]

Örnekler:
    # Varsayılan Aksa dosyası (3 sayfa)
    python scripts/test_hybrid_a2a_ocr.py

    # Klasördeki tüm sayfalar
    python scripts/test_hybrid_a2a_ocr.py /path/to/images/ "Şirket-Adı"
"""

import os
import sys
import asyncio
import json
import logging
from glob import glob

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
logger = logging.getLogger("test_hybrid_a2a")


# Varsayılan test dosyası
DEFAULT_IMAGE_DIR = os.path.join(
    os.path.dirname(__file__), "..",
    "output_celery",
    "Aksa-09.03.2016-9028-GENEL KURUL TOPLANTIYA ÇAĞIRI",
    "images",
)

DEFAULT_FILE_NAME = "Aksa-09.03.2016-9028-GENEL KURUL TOPLANTIYA ÇAĞIRI"


def get_image_list(path: str) -> list:
    """Path'ten görüntü listesi oluştur."""
    if os.path.isfile(path):
        return [path]
    if os.path.isdir(path):
        images = []
        for ext in ["*.png", "*.jpg", "*.jpeg"]:
            images.extend(glob(os.path.join(path, ext)))
        return sorted(images)
    return []


async def main():
    path_arg = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_IMAGE_DIR
    file_name = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_FILE_NAME

    image_list = get_image_list(path_arg)
    
    if not image_list:
        print(f"❌ Görüntü bulunamadı: {path_arg}")
        sys.exit(1)

    gemini_model = os.environ.get("GEMINI_OCR_MODEL", "gemini-2.0-flash")
    claude_model = os.environ.get("HYBRID_EXTRACT_MODEL", "claude-opus-4-5")

    print(f"\n{'='*70}")
    print(f"🧪 HYBRID A2A OCR TEST")
    print(f"{'='*70}")
    print(f"📄 Görüntüler: {len(image_list)} sayfa")
    for img in image_list:
        print(f"   - {os.path.basename(img)}")
    print(f"🏢 Dosya: {file_name}")
    print(f"🤖 Stage 1 (Parallel OCR): {gemini_model}")
    print(f"🧠 Stage 2 (Sequential Extract): {claude_model}")
    print(f"{'='*70}\n")

    # Neo4j bağlantısı
    graph = None
    try:
        from langchain_neo4j import Neo4jGraph
        neo4j_uri = os.environ.get("NEO4J_URI")
        if neo4j_uri:
            graph = Neo4jGraph(
                url=neo4j_uri,
                username=os.environ.get("NEO4J_USERNAME"),
                password=os.environ.get("NEO4J_PASSWORD"),
                database=os.environ.get("NEO4J_DATABASE", "neo4j"),
            )
            print("✅ Neo4j bağlantısı başarılı")
    except Exception as e:
        print(f"⚠️ Neo4j bağlantısı yok: {e}")

    # HybridA2AOCR başlat
    from src.hybrid_a2a_ocr import HybridA2AOCR

    ocr = HybridA2AOCR()
    await ocr.initialize()
    print("✅ HybridA2AOCR başlatıldı\n")

    # Output dizini
    output_dir = os.path.join(
        os.path.dirname(path_arg) if os.path.isfile(path_arg) else path_arg,
        "..",
        "test_hybrid_a2a_output",
    )

    # Pipeline çalıştır
    result = await ocr.process(
        image_list=image_list,
        file_name=file_name,
        graph=graph,
        output_dir=output_dir,
    )

    # Sonuçlar
    print(f"\n{'='*70}")
    print(f"📊 SONUÇLAR")
    print(f"{'='*70}")
    print(f"Status: {result.get('status', '?')}")

    metadata = result.get("metadata", {})
    if metadata:
        print(f"\n📈 Performance:")
        print(f"   - Stage 1 (Parallel OCR): {metadata.get('stage1_chars', '?')} chars, {metadata.get('stage1_duration_ms', '?')}ms")
        print(f"   - Stage 2 (Sequential Extract): {metadata.get('stage2_duration_ms', '?')}ms")
        print(f"   - Total: {metadata.get('total_duration_ms', '?')}ms")
        print(f"   - Pipeline: {metadata.get('pipeline', '?')}")

    if result.get("status") == "success":
        data = result.get("data", {})
        found = data.get("found", False)
        print(f"\n🎯 Hedef şirket bulundu: {'Evet ✅' if found else 'Hayır ❌'}")

        if found:
            chunks = data.get("chunks", [])
            nodes = data.get("nodes", [])
            rels = data.get("relationships", [])

            print(f"\n📝 Chunks ({len(chunks)}):")
            for c in chunks[:5]:
                text_preview = c.get("text", "")[:80].replace("\n", " ")
                print(f"   - [{c.get('id', '?')}] p{c.get('page', '?')}: {text_preview}...")
            if len(chunks) > 5:
                print(f"   ... ve {len(chunks) - 5} chunk daha")

            print(f"\n🔵 Nodes ({len(nodes)}):")
            for n in nodes[:10]:
                print(f"   - [{n.get('label', '?')}] {n.get('id', '?')}")
            if len(nodes) > 10:
                print(f"   ... ve {len(nodes) - 10} node daha")

            print(f"\n🔗 Relationships ({len(rels)}):")
            for r in rels[:10]:
                print(f"   - {r.get('from_id', '?')} --[{r.get('type', '?')}]--> {r.get('to_id', '?')}")
            if len(rels) > 10:
                print(f"   ... ve {len(rels) - 10} relationship daha")

    # OCR dosyalarını listele
    ocr_dir = os.path.join(output_dir, "ocr_pages")
    if os.path.exists(ocr_dir):
        print(f"\n📁 OCR Dosyaları ({ocr_dir}):")
        for f in sorted(os.listdir(ocr_dir)):
            fpath = os.path.join(ocr_dir, f)
            size_kb = os.path.getsize(fpath) // 1024
            print(f"   - {f} ({size_kb}KB)")

    print(f"\n{'='*70}")
    print(f"✅ TEST TAMAMLANDI")
    print(f"{'='*70}\n")

    await ocr.close()


if __name__ == "__main__":
    asyncio.run(main())
