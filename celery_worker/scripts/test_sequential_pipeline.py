#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sequential Pipeline Test Script

GeminiOCRAgent -> AgenticOCR (text mode) pipeline testleri.

Usage:
    cd celery_worker
    python scripts/test_sequential_pipeline.py [pdf_path]

Gerekli env vars:
    - GEMINI_API_KEY
    - ANTHROPIC_API_KEY
"""

import os
import sys
import time
import asyncio
import argparse
from pathlib import Path

# Path ayarları
script_dir = Path(__file__).parent.absolute()
celery_worker_dir = script_dir.parent
sys.path.insert(0, str(celery_worker_dir))

# Env vars
os.environ.setdefault("OCR_VISION_MODEL", "claude-opus-4.5")
os.environ.setdefault("GEMINI_OCR_MODEL", "gemini-2.0-flash")


def extract_images_from_pdf(pdf_path: str, output_dir: str) -> list:
    """PDF'den görselleri çıkar."""
    import fitz  # PyMuPDF

    doc = fitz.open(pdf_path)
    image_paths = []

    for page_num in range(len(doc)):
        page = doc.load_page(page_num)
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))  # 2x zoom

        image_path = os.path.join(output_dir, f"page_{page_num + 1:03d}.png")
        pix.save(image_path)
        image_paths.append(image_path)

    doc.close()
    return image_paths


async def test_sequential_pipeline(pdf_path: str, output_dir: str):
    """
    Sequential pipeline testi:
    1. GeminiOCRAgent ile paralel OCR
    2. AgenticOCR (text mode) ile entity extraction
    """
    from src.agents.gemini_ocr_agent import GeminiOCRAgent
    from src.agentic_ocr import AgenticOCR

    print(f"\n{'='*70}")
    print(f"📄 SEQUENTIAL PIPELINE TEST")
    print(f"   PDF: {pdf_path}")
    print(f"   Output: {output_dir}")
    print(f"{'='*70}\n")

    # Output dizini oluştur
    os.makedirs(output_dir, exist_ok=True)

    # ========================================================================
    # STAGE 1: PDF'den görselleri çıkar
    # ========================================================================
    print(f"📄 Extracting images from PDF...")
    start_time = time.time()

    images_dir = os.path.join(output_dir, "images")
    os.makedirs(images_dir, exist_ok=True)

    image_paths = extract_images_from_pdf(pdf_path, images_dir)
    extract_duration = time.time() - start_time

    print(f"   ✅ Extracted {len(image_paths)} images in {extract_duration:.1f}s")

    # ========================================================================
    # STAGE 2: Gemini OCR (paralel)
    # ========================================================================
    print(f"\n📖 STAGE 1: Gemini Parallel OCR")
    print(f"   Model: {os.environ.get('GEMINI_OCR_MODEL')}")
    print(f"   Pages: {len(image_paths)}")

    gemini_agent = GeminiOCRAgent()
    await gemini_agent.initialize()

    gemini_start = time.time()
    gemini_result = await gemini_agent.process(
        image_list=image_paths,
        output_dir=output_dir,
    )
    gemini_duration = time.time() - gemini_start

    print(f"\n📊 GEMINI RESULTS:")
    print(f"   Total chars: {gemini_result['total_chars']:,}")
    print(f"   Pages: {gemini_result['page_count']}")
    print(f"   Tokens: {gemini_result['token_usage']}")
    print(f"   Duration: {gemini_duration:.1f}s")

    # ========================================================================
    # STAGE 3: Claude Extraction (text mode)
    # ========================================================================
    print(f"\n📝 STAGE 2: Claude Entity Extraction (Text Mode)")
    print(f"   Model: {os.environ.get('OCR_VISION_MODEL')}")
    print(f"   Pages: {len(gemini_result['ocr_texts'])}")

    # File name'den şirket adı çıkar
    file_name = Path(pdf_path).stem

    agentic_ocr = AgenticOCR()
    await agentic_ocr.initialize()

    claude_start = time.time()
    claude_result = await agentic_ocr.process(
        text_mode=True,
        ocr_texts=gemini_result["ocr_texts"],
        file_name=file_name,
        output_dir=output_dir,
        batch_size=50,
    )
    claude_duration = time.time() - claude_start

    print(f"\n📊 CLAUDE RESULTS:")
    print(f"   Status: {claude_result.get('status')}")

    if claude_result.get("data"):
        data = claude_result["data"]
        print(f"   Found: {data.get('found', False)}")
        print(f"   Chunks: {len(data.get('chunks', []))}")
        print(f"   Nodes: {len(data.get('nodes', []))}")
        print(f"   Relationships: {len(data.get('relationships', []))}")

    print(f"   Duration: {claude_duration:.1f}s")

    # ========================================================================
    # SUMMARY
    # ========================================================================
    total_duration = extract_duration + gemini_duration + claude_duration

    print(f"\n{'='*70}")
    print(f"✅ SEQUENTIAL PIPELINE COMPLETE")
    print(f"   Stage 1 (Gemini OCR): {gemini_duration:.1f}s")
    print(f"   Stage 2 (Claude Extract): {claude_duration:.1f}s")
    print(f"   Total Duration: {total_duration:.1f}s")
    print(f"{'='*70}\n")

    return {
        "gemini_result": gemini_result,
        "claude_result": claude_result,
        "timings": {
            "extract": extract_duration,
            "gemini": gemini_duration,
            "claude": claude_duration,
            "total": total_duration,
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Sequential Pipeline Test")
    parser.add_argument(
        "pdf_path",
        nargs="?",
        default="/workspace/data/test/sample.pdf",
        help="PDF dosya yolu",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="/workspace/data/output/sequential_test",
        help="Output dizini",
    )

    args = parser.parse_args()

    if not os.path.exists(args.pdf_path):
        print(f"❌ PDF not found: {args.pdf_path}")
        sys.exit(1)

    asyncio.run(test_sequential_pipeline(args.pdf_path, args.output))


if __name__ == "__main__":
    main()
