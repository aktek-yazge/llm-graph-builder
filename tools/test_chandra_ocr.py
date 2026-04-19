#!/usr/bin/env python3
"""
Chandra OCR 2 - Quick Test Script

Mac'te Chandra OCR 2 modelini hizli test etmek icin.

Kullanim:
    # Tek gorsel test
    python tools/test_chandra_ocr.py /path/to/page.png

    # PDF test (PyMuPDF ile sayfaya cevirir)
    python tools/test_chandra_ocr.py /path/to/document.pdf

    # Sadece model yukleme testi (dosya gerekmez)
    python tools/test_chandra_ocr.py --check

    # Ciktiyi dosyaya kaydet
    python tools/test_chandra_ocr.py /path/to/doc.pdf --save /path/to/output.md
"""

import argparse
import asyncio
import os
import sys
import time

CELERY_WORKER_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "celery_worker")
)
sys.path.insert(0, os.path.join(CELERY_WORKER_ROOT, "src"))
sys.path.insert(0, CELERY_WORKER_ROOT)


def check_environment():
    """Verify that all dependencies are available."""
    print("=== Chandra OCR 2 Environment Check ===\n")

    try:
        import torch
        device = "cpu"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            device = "mps"
        elif torch.cuda.is_available():
            device = "cuda"
        print(f"  torch {torch.__version__}  device={device}")
    except ImportError:
        print("  torch: NOT INSTALLED")
        return False

    try:
        import transformers
        print(f"  transformers {transformers.__version__}")
    except ImportError:
        print("  transformers: NOT INSTALLED")
        return False

    try:
        import chandra
        ver = getattr(chandra, "__version__", "?")
        print(f"  chandra-ocr {ver}")
    except ImportError:
        print("  chandra-ocr: NOT INSTALLED  ->  pip install 'chandra-ocr[hf]'")
        return False

    try:
        from PIL import Image
        print("  Pillow OK")
    except ImportError:
        print("  Pillow: NOT INSTALLED")
        return False

    print("\n  All dependencies OK.\n")
    return True


async def test_single_image(image_path: str, save_path: str | None = None):
    """Test Chandra OCR on a single image."""
    from agents.chandra_ocr_agent import ChandraOCRAgent
    import tempfile

    agent = ChandraOCRAgent()
    await agent.initialize()

    output_dir = tempfile.mkdtemp(prefix="chandra_test_")
    result = await agent.process(
        image_list=[image_path],
        output_dir=output_dir,
    )

    print(f"\n{'='*60}")
    print("RESULT")
    print(f"{'='*60}")
    print(f"  Pages: {result['page_count']}")
    print(f"  Chars: {result['total_chars']:,}")
    print(f"  Duration: {result['duration_ms']}ms")
    print(f"  Cost: ${result['token_usage']['cost_usd']:.4f}")
    print(f"  Output dir: {output_dir}")

    if result["ocr_texts"]:
        text = result["ocr_texts"][0]
        if save_path:
            _save_result(save_path, result)
        else:
            print(f"\n--- OCR Text (first 2000 chars) ---\n")
            print(text[:2000])
            if len(text) > 2000:
                print(f"\n... ({len(text) - 2000} more chars)")

    return result


async def test_pdf(pdf_path: str, save_path: str | None = None):
    """Test Chandra OCR on a PDF (converts to images first)."""
    import fitz
    import tempfile

    print(f"Converting PDF to images: {pdf_path}")
    doc = fitz.open(pdf_path)
    tmp_dir = tempfile.mkdtemp(prefix="chandra_pdf_")
    image_paths = []

    max_pages = 5
    total_pages = len(doc)
    pages_to_process = min(total_pages, max_pages)

    for page_num in range(pages_to_process):
        page = doc.load_page(page_num)
        pix = page.get_pixmap(dpi=200)
        img_path = os.path.join(tmp_dir, f"page_{page_num + 1:03d}.png")
        pix.save(img_path)
        image_paths.append(img_path)
        print(f"  Page {page_num + 1}: {img_path}")

    doc.close()
    if total_pages > max_pages:
        print(f"  (showing {max_pages} of {total_pages} pages)")
    print(f"  {len(image_paths)} pages extracted\n")

    from agents.chandra_ocr_agent import ChandraOCRAgent

    agent = ChandraOCRAgent()
    await agent.initialize()

    output_dir = tempfile.mkdtemp(prefix="chandra_test_out_")
    result = await agent.process(
        image_list=image_paths,
        output_dir=output_dir,
    )

    print(f"\n{'='*60}")
    print("RESULT")
    print(f"{'='*60}")
    print(f"  Pages: {result['page_count']}")
    print(f"  Chars: {result['total_chars']:,}")
    print(f"  Duration: {result['duration_ms']}ms")
    print(f"  Cost: ${result['token_usage']['cost_usd']:.4f}")
    print(f"  Output dir: {output_dir}")
    print(f"  Errors: {len(result['errors'])}")

    if save_path:
        _save_result(save_path, result)
    else:
        if result["merged_text"]:
            print(f"\n--- Merged Text (first 3000 chars) ---\n")
            print(result["merged_text"][:3000])

    return result


def _save_result(save_path: str, result: dict):
    """Save OCR result to a markdown file."""
    os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
    with open(save_path, "w", encoding="utf-8") as f:
        f.write(f"# Chandra OCR Result\n\n")
        f.write(f"- Pages: {result['page_count']}\n")
        f.write(f"- Total chars: {result['total_chars']:,}\n")
        f.write(f"- Duration: {result['duration_ms']}ms\n")
        f.write(f"- Errors: {len(result['errors'])}\n\n")
        f.write("---\n\n")
        f.write(result.get("merged_text", ""))
    print(f"\n  Saved to: {save_path}")


def main():
    parser = argparse.ArgumentParser(description="Test Chandra OCR 2")
    parser.add_argument("file", nargs="?", help="Image or PDF file path")
    parser.add_argument("--check", action="store_true", help="Check environment only")
    parser.add_argument("--save", type=str, default=None, help="Save result to file")
    args = parser.parse_args()

    if args.check:
        ok = check_environment()
        sys.exit(0 if ok else 1)

    if not args.file:
        check_environment()
        print("Usage: python tools/test_chandra_ocr.py <image_or_pdf> [--save output.md]")
        sys.exit(0)

    file_path = os.path.abspath(args.file)
    if not os.path.exists(file_path):
        print(f"File not found: {file_path}")
        sys.exit(1)

    if file_path.lower().endswith(".pdf"):
        asyncio.run(test_pdf(file_path, save_path=args.save))
    else:
        asyncio.run(test_single_image(file_path, save_path=args.save))


if __name__ == "__main__":
    main()
