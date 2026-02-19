#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Two-Stage OCR Test Script

TwoStageOCR pipeline'ını test eder:
1. Gemini 2.0 Flash ile full-page OCR
2. Claude Opus 4.5 ile filtreleme + entity extraction

Usage:
    cd /workspace/celery_worker
    python scripts/test_two_stage_ocr.py [image_path_or_dir] [file_name]

Örnekler:
    # Varsayılan Aksa dosyası (3 sayfa)
    python scripts/test_two_stage_ocr.py

    # Tek sayfa
    python scripts/test_two_stage_ocr.py /path/to/page.png "Şirket-Adı"

    # Klasördeki tüm sayfalar
    python scripts/test_two_stage_ocr.py /path/to/images/ "Şirket-Adı"
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
logger = logging.getLogger("test_two_stage_ocr")


# Varsayılan test dosyası
DEFAULT_IMAGE_DIR = os.path.join(
    os.path.dirname(__file__), "..",
    "output_celery",
    "Aksa-09.03.2016-9028-GENEL KURUL TOPLANTIYA ÇAĞIRI",
    "images",
)

DEFAULT_FILE_NAME = "Aksa-09.03.2016-9028-GENEL KURUL TOPLANTIYA ÇAĞIRI"


def get_image_list(path: str) -> list:
    """
    Path'ten görüntü listesi oluştur.
    
    - Dosya ise tek elemanlı liste döndür
    - Klasör ise içindeki tüm .png/.jpg dosyalarını döndür
    """
    if os.path.isfile(path):
        return [path]
    
    if os.path.isdir(path):
        images = []
        for ext in ["*.png", "*.jpg", "*.jpeg"]:
            images.extend(glob(os.path.join(path, ext)))
        return sorted(images)
    
    return []


async def main():
    # Argümanları parse et
    path_arg = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_IMAGE_DIR
    file_name = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_FILE_NAME

    # Görüntü listesini oluştur
    image_list = get_image_list(path_arg)
    
    if not image_list:
        print(f"❌ Görüntü bulunamadı: {path_arg}")
        sys.exit(1)

    # Environment bilgileri
    gemini_model = os.environ.get("GEMINI_OCR_MODEL", "gemini-2.0-flash")
    claude_model = os.environ.get("TWO_STAGE_EXTRACT_MODEL", "claude-opus-4-5")

    print(f"\n{'='*70}")
    print(f"🧪 TWO-STAGE OCR TEST")
    print(f"{'='*70}")
    print(f"📄 Görüntüler: {len(image_list)} sayfa")
    for img in image_list:
        print(f"   - {os.path.basename(img)}")
    print(f"🏢 Dosya: {file_name}")
    print(f"🤖 Stage 1 (OCR): {gemini_model}")
    print(f"🧠 Stage 2 (Extract): {claude_model}")
    print(f"{'='*70}\n")

    # Neo4j bağlantısı (opsiyonel)
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
            print("✅ Neo4j bağlantısı başarılı (şema alınacak)")
        else:
            print("⚠️ NEO4J_URI tanımlı değil, şemasız çalışılacak")
    except Exception as e:
        print(f"⚠️ Neo4j bağlantısı yok: {e}")
        print("   (Fallback şema kullanılacak)\n")

    # TwoStageOCR başlat
    from src.two_stage_ocr import TwoStageOCR

    ocr = TwoStageOCR()
    await ocr.initialize()
    print("✅ TwoStageOCR başlatıldı\n")

    # Output dizini
    if os.path.isdir(path_arg):
        output_dir = os.path.join(path_arg, "..", "test_two_stage_output")
    else:
        output_dir = os.path.join(os.path.dirname(path_arg), "..", "test_two_stage_output")
    os.makedirs(output_dir, exist_ok=True)

    # Pipeline'ı çalıştır
    result = await ocr.process(
        image_list=image_list,
        file_name=file_name,
        graph=graph,
        output_dir=output_dir,
    )

    # Sonuçları göster
    print(f"\n{'='*70}")
    print(f"📊 SONUÇLAR")
    print(f"{'='*70}")
    print(f"Status: {result.get('status', '?')}")

    # Metadata
    metadata = result.get("metadata", {})
    if metadata:
        print(f"\n📈 Metadata:")
        print(f"   - Target Company: {metadata.get('target_company', '?')}")
        print(f"   - Pages Processed: {metadata.get('pages_processed', '?')}")
        print(f"   - Stage 1 (OCR): {metadata.get('stage1_chars', '?')} chars, {metadata.get('stage1_duration_ms', '?')}ms")
        print(f"   - Stage 2 (Extract): {metadata.get('stage2_duration_ms', '?')}ms")
        print(f"   - Total: {metadata.get('total_duration_ms', '?')}ms")

    if result.get("status") == "success":
        data = result.get("data", {})

        # JSON detayları
        if isinstance(data, dict):
            found = data.get("found", False)
            print(f"\n🎯 Hedef şirket bulundu: {'Evet ✅' if found else 'Hayır ❌'}")

            if found:
                doc_type = data.get("document_type", "?")
                print(f"📋 Belge tipi: {doc_type}")

                chunks = data.get("chunks", [])
                nodes = data.get("nodes", [])
                rels = data.get("relationships", [])

                print(f"\n📝 Chunks ({len(chunks)}):")
                for c in chunks[:5]:  # İlk 5 chunk
                    text_preview = c.get("text", "")[:100].replace("\n", " ")
                    print(f"   - [{c.get('id', '?')}] {text_preview}...")
                if len(chunks) > 5:
                    print(f"   ... ve {len(chunks) - 5} chunk daha")

                print(f"\n🔵 Nodes ({len(nodes)}):")
                for n in nodes[:10]:  # İlk 10 node
                    label = n.get("label", "?")
                    node_id = n.get("id", "?")
                    name = n.get("properties", {}).get("name", "")
                    print(f"   - [{label}] {node_id}")
                    if name:
                        print(f"     name: {name}")
                if len(nodes) > 10:
                    print(f"   ... ve {len(nodes) - 10} node daha")

                print(f"\n🔗 Relationships ({len(rels)}):")
                for r in rels[:10]:  # İlk 10 relationship
                    print(f"   - {r.get('from_id', '?')} --[{r.get('type', '?')}]--> {r.get('to_id', '?')}")
                if len(rels) > 10:
                    print(f"   ... ve {len(rels) - 10} relationship daha")

    else:
        print(f"\n❌ Hata: {result.get('error', 'Bilinmeyen hata')}")

    # JSON kaydet
    output_json = os.path.join(output_dir, "test_two_stage_result.json")
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n💾 Sonuç kaydedildi: {output_json}")

    # OCR ham metni kaydet (debug için)
    if result.get("status") == "success" and "stage1_chars" in metadata:
        # Stage 1 çıktısı ayrıca kaydedilebilir
        pass

    print(f"\n{'='*70}")
    print(f"✅ TEST TAMAMLANDI")
    print(f"{'='*70}\n")

    # Cleanup
    await ocr.close()


if __name__ == "__main__":
    asyncio.run(main())
