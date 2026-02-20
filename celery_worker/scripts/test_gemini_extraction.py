#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gemini OCR + Hedef Şirket Extraction Test Scripti

Bu script:
1. PDF'den image'ları çıkarır
2. GeminiOCRAgent.process(target_company="...") çağırır
3. Extracted markdown'ı kontrol eder
4. Kooperatif içeriği var mı kontrol eder
5. Sonuçları raporlar

Usage:
    cd /workspace/celery_worker
    python scripts/test_gemini_extraction.py --pdf /path/to/file.pdf --target "Aksa"
    
    # Veya mevcut image'ları kullan:
    python scripts/test_gemini_extraction.py --images output_celery/test/images/*.png --target "Aksa"
    
    # Varsayılan test:
    python scripts/test_gemini_extraction.py
"""

import os
import sys
import argparse
import asyncio
import json
from pathlib import Path
from datetime import datetime

# Proje root'unu path'e ekle
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()


def extract_images_from_pdf(pdf_path: str, output_dir: str) -> list:
    """PDF'den image'ları çıkar."""
    import fitz  # PyMuPDF
    
    os.makedirs(output_dir, exist_ok=True)
    doc = fitz.open(pdf_path)
    
    image_list = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        pix = page.get_pixmap(dpi=200)
        image_path = os.path.join(output_dir, f"page_{page_num + 1:03d}.png")
        pix.save(image_path)
        image_list.append(image_path)
        print(f"   📄 Page {page_num + 1} extracted: {image_path}")
    
    doc.close()
    return image_list


def check_extraction_quality(extracted_text: str, target_company: str) -> dict:
    """Extraction kalitesini kontrol et."""
    # Kooperatif anahtar kelimeleri
    kooperatif_keywords = [
        "kooperatif",
        "Şarkikaraaağaç",
        "Hakan Apak",
        "Sami Demirkıran",
        "huzur hakkı",
        "harcırah",
        "Isparta",
        "S.S.",
        "Tarımsal Kalkınma",
    ]
    
    # Hedef şirket anahtar kelimeleri
    target_keywords = [
        target_company.lower(),
        "ticaret ünvanı",
        "sicil no",
        "yönetim kurulu",
        "genel kurul",
    ]
    
    text_lower = extracted_text.lower()
    
    # Kooperatif içeriği var mı?
    found_kooperatif = []
    for kw in kooperatif_keywords:
        if kw.lower() in text_lower:
            found_kooperatif.append(kw)
    
    # Hedef şirket içeriği var mı?
    found_target = []
    for kw in target_keywords:
        if kw.lower() in text_lower:
            found_target.append(kw)
    
    # Sayfa bilgisi korunmuş mu?
    page_markers = extracted_text.count("[[PAGE:")
    
    return {
        "has_kooperatif_content": len(found_kooperatif) > 0,
        "kooperatif_keywords_found": found_kooperatif,
        "has_target_content": len(found_target) > 0,
        "target_keywords_found": found_target,
        "page_markers_count": page_markers,
        "char_count": len(extracted_text),
        "success": len(found_kooperatif) == 0 and len(found_target) > 0,
    }


async def run_test(
    image_list: list,
    output_dir: str,
    target_company: str,
    file_name: str,
) -> dict:
    """Test'i çalıştır."""
    from src.agents.gemini_ocr_agent import GeminiOCRAgent
    
    print(f"\n{'='*70}")
    print(f"🧪 GEMINI EXTRACTION TEST")
    print(f"   Target Company: {target_company}")
    print(f"   Images: {len(image_list)}")
    print(f"   Output: {output_dir}")
    print(f"{'='*70}\n")
    
    # Agent oluştur
    agent = GeminiOCRAgent()
    await agent.initialize()
    
    # Process with extraction
    result = await agent.process(
        image_list=image_list,
        output_dir=output_dir,
        target_company=target_company,
        file_name=file_name,
    )
    
    # Kalite kontrolü
    quality = None
    if "extraction" in result:
        extracted_text = result["extraction"].get("extracted_text", "")
        quality = check_extraction_quality(extracted_text, target_company)
    
    return {
        "result": result,
        "quality": quality,
    }


def print_report(test_result: dict, target_company: str):
    """Test sonuç raporu yazdır."""
    result = test_result["result"]
    quality = test_result["quality"]
    
    print(f"\n{'='*70}")
    print(f"📊 TEST REPORT")
    print(f"{'='*70}\n")
    
    # OCR Stage
    print("## Stage 1: OCR")
    print(f"   Pages: {result.get('page_count', 0)}")
    print(f"   Total Chars: {result.get('total_chars', 0):,}")
    print(f"   Errors: {len(result.get('errors', []))}")
    
    # Extraction Stage
    if "extraction" in result:
        ext = result["extraction"]
        print(f"\n## Stage 2: Extraction")
        print(f"   Found: {ext.get('found', False)}")
        print(f"   Output Chars: {ext.get('char_count', 0):,}")
        print(f"   File: {ext.get('extracted_file', 'N/A')}")
    
    # Token Usage
    token = result.get("token_usage", {})
    print(f"\n## Token Usage")
    print(f"   Input: {token.get('input_tokens', 0):,}")
    print(f"   Output: {token.get('output_tokens', 0):,}")
    print(f"   Total: {token.get('total_tokens', 0):,}")
    print(f"   Cost: ${token.get('cost_usd', 0):.4f}")
    
    # Duration
    print(f"\n## Duration")
    print(f"   Total: {result.get('duration_ms', 0):,}ms ({result.get('duration_ms', 0)/1000:.1f}s)")
    
    # Quality Check
    if quality:
        print(f"\n## Quality Check")
        print(f"   ✅ Target Company Content: {quality['has_target_content']}")
        print(f"      Keywords: {quality['target_keywords_found']}")
        print(f"   {'❌' if quality['has_kooperatif_content'] else '✅'} No Kooperatif Content: {not quality['has_kooperatif_content']}")
        if quality['has_kooperatif_content']:
            print(f"      Found: {quality['kooperatif_keywords_found']}")
        print(f"   Page Markers: {quality['page_markers_count']}")
        
        print(f"\n## RESULT: {'✅ SUCCESS' if quality['success'] else '❌ FAILED'}")
    
    print(f"\n{'='*70}\n")


def main():
    parser = argparse.ArgumentParser(description="Gemini OCR + Extraction Test")
    parser.add_argument(
        "--pdf",
        type=str,
        help="PDF dosya yolu",
    )
    parser.add_argument(
        "--images",
        type=str,
        nargs="+",
        help="Image dosya yolları",
    )
    parser.add_argument(
        "--target",
        type=str,
        default="Aksa",
        help="Hedef şirket adı (default: Aksa)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="output_celery/test_extraction",
        help="Çıktı dizini",
    )
    
    args = parser.parse_args()
    
    # Test için image'ları belirle
    image_list = []
    file_name = "test_document"
    
    if args.pdf:
        # PDF'den image çıkar
        pdf_path = args.pdf
        file_name = Path(pdf_path).stem
        images_dir = os.path.join(args.output, "images")
        print(f"📄 Extracting images from PDF: {pdf_path}")
        image_list = extract_images_from_pdf(pdf_path, images_dir)
    elif args.images:
        # Verilen image'ları kullan
        image_list = sorted(args.images)
        print(f"📄 Using provided images: {len(image_list)}")
    else:
        # Varsayılan test PDF'i
        default_pdf = "data/pdfs/Aksa-09.03.2016-9028-GENEL KURUL TOPLANTIYA ÇAĞIRI.pdf"
        if os.path.exists(default_pdf):
            file_name = Path(default_pdf).stem
            images_dir = os.path.join(args.output, "images")
            print(f"📄 Using default test PDF: {default_pdf}")
            image_list = extract_images_from_pdf(default_pdf, images_dir)
        else:
            # Mevcut OCR output'larını kontrol et
            ocr_dir = "output_celery/Aksa-09.03.2016-9028-GENEL KURUL TOPLANTIYA ÇAĞIRI"
            if os.path.exists(ocr_dir):
                # Mevcut extracted images'ı bul
                from glob import glob
                image_pattern = os.path.join(ocr_dir, "*.png")
                image_list = sorted(glob(image_pattern))
                if not image_list:
                    # images subdirectory kontrol et
                    image_pattern = os.path.join(ocr_dir, "images", "*.png")
                    image_list = sorted(glob(image_pattern))
                file_name = "Aksa-09.03.2016-9028-GENEL KURUL TOPLANTIYA ÇAĞIRI"
            
            if not image_list:
                print("❌ No images found. Please provide --pdf or --images")
                print("\nUsage examples:")
                print("  python scripts/test_gemini_extraction.py --pdf data/pdfs/test.pdf --target 'Aksa'")
                print("  python scripts/test_gemini_extraction.py --images output/*.png --target 'Aksa'")
                sys.exit(1)
    
    if not image_list:
        print("❌ No images to process")
        sys.exit(1)
    
    print(f"\n📷 Found {len(image_list)} images")
    for img in image_list[:3]:
        print(f"   - {img}")
    if len(image_list) > 3:
        print(f"   ... and {len(image_list) - 3} more")
    
    # Test çalıştır
    test_result = asyncio.run(
        run_test(
            image_list=image_list,
            output_dir=args.output,
            target_company=args.target,
            file_name=file_name,
        )
    )
    
    # Rapor yazdır
    print_report(test_result, args.target)
    
    # Sonucu JSON olarak kaydet
    report_file = os.path.join(args.output, "test_report.json")
    with open(report_file, "w", encoding="utf-8") as f:
        # Result'tan non-serializable alanları çıkar
        serializable_result = {
            "timestamp": datetime.now().isoformat(),
            "target_company": args.target,
            "page_count": test_result["result"].get("page_count", 0),
            "total_chars": test_result["result"].get("total_chars", 0),
            "token_usage": test_result["result"].get("token_usage", {}),
            "duration_ms": test_result["result"].get("duration_ms", 0),
            "extraction": {
                "found": test_result["result"].get("extraction", {}).get("found", False),
                "char_count": test_result["result"].get("extraction", {}).get("char_count", 0),
                "extracted_file": test_result["result"].get("extraction", {}).get("extracted_file", ""),
            } if "extraction" in test_result["result"] else None,
            "quality": test_result["quality"],
        }
        json.dump(serializable_result, f, indent=2, ensure_ascii=False)
    
    print(f"📄 Report saved: {report_file}")
    
    # Exit code
    if test_result["quality"] and test_result["quality"]["success"]:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
