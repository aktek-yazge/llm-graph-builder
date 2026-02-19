#!/usr/bin/env python3
"""
Gemini OCR performans testi - sadece Stage 1
Kolon sıralamasını kontrol etmek için.
"""

import asyncio
import os
import sys
import time
from pathlib import Path
import google.generativeai as genai
from PIL import Image

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()


async def test_single_page_ocr(image_path: str) -> dict:
    """Tek sayfa OCR testi."""

    # Gemini config
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    model = genai.GenerativeModel("gemini-2.0-flash")

    # OCR prompt - yapısal tanım + kolon sınırı
    ocr_prompt = """Sen Türkiye Ticaret Sicil Gazetesi OCR uzmanısın.

Gazete sayfası dikey kolonlardan oluşur. Kolonlar dikey çizgilerle ayrılır. Kolon 1 en solda, son kolon en sağda.

Bir kolonu okurken dikey sınırın ötesine geçilmez. Önce bir kolon tamamen bitirilir, sonra sağdaki kolona geçilir.

Metni bu sırayla aktarırsın."""

    # Load image
    image = Image.open(image_path)

    # OCR with low temperature for consistency
    start = time.time()
    response = model.generate_content(
        [ocr_prompt, image], generation_config={"temperature": 0.0}
    )
    duration = time.time() - start

    text = response.text if response.text else ""

    return {
        "image": os.path.basename(image_path),
        "duration_sec": round(duration, 2),
        "char_count": len(text),
        "text": text,
    }


async def test_all_pages(image_dir: str, output_dir: str):
    """Tüm sayfaları test et."""

    # Find images
    images = sorted(Path(image_dir).glob("*.png"))
    if not images:
        images = sorted(Path(image_dir).glob("*.jpg"))

    print(f"\n{'='*60}")
    print(f"GEMINI OCR TESTİ")
    print(f"{'='*60}")
    print(f"Görüntü sayısı: {len(images)}")
    print(f"Çıktı dizini: {output_dir}")
    print(f"{'='*60}\n")

    # Create output dir
    os.makedirs(output_dir, exist_ok=True)

    results = []
    total_start = time.time()

    for i, img_path in enumerate(images, 1):
        print(f"[{i}/{len(images)}] {img_path.name}...", end=" ", flush=True)

        result = await test_single_page_ocr(str(img_path))
        results.append(result)

        # Save OCR output
        output_file = os.path.join(output_dir, f"page_{i:03d}_ocr.md")
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(f"# Sayfa {i}\n\n")
            f.write(result["text"])

        print(f"✓ {result['duration_sec']}s, {result['char_count']} chars")

    total_duration = time.time() - total_start

    # Summary
    print(f"\n{'='*60}")
    print(f"ÖZET")
    print(f"{'='*60}")
    print(f"Toplam süre: {total_duration:.1f}s")
    print(f"Ortalama/sayfa: {total_duration/len(images):.1f}s")
    print(f"Toplam karakter: {sum(r['char_count'] for r in results)}")
    print(f"\nÇıktılar: {output_dir}")
    print(f"{'='*60}\n")

    return results


def main():
    # Default test path
    test_dir = "/workspace/celery_worker/output_celery/Aksa-09.03.2016-9028-GENEL KURUL TOPLANTIYA ÇAĞIRI"
    image_dir = os.path.join(test_dir, "images")
    output_dir = os.path.join(test_dir, "test_ocr_only")

    # Check if custom path provided
    if len(sys.argv) > 1:
        image_dir = sys.argv[1]
        output_dir = (
            sys.argv[2]
            if len(sys.argv) > 2
            else os.path.join(os.path.dirname(image_dir), "test_ocr_only")
        )

    if not os.path.exists(image_dir):
        print(f"Hata: {image_dir} bulunamadı")
        sys.exit(1)

    asyncio.run(test_all_pages(image_dir, output_dir))


if __name__ == "__main__":
    main()
