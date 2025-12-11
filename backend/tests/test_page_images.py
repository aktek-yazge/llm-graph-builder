#!/usr/bin/env python3
"""
Test script for page image generation functionality in local_file.py
"""

import logging
import sys
from pathlib import Path

# Add src to path
sys.path.append("src")

try:
    from src.document_sources.local_file import get_documents_from_file_by_path
    print("✅ Import successful")
except ImportError as e:
    print(f"❌ Import error: {e}")
    # Fallback for different path structure
    try:
        from document_sources.local_file import get_documents_from_file_by_path
        print("✅ Fallback import successful")
    except ImportError as e2:
        print(f"❌ Fallback import also failed: {e2}")
        sys.exit(1)

def main():
    # logging.basicConfig(level=logging.INFO)  # main.py'de yapılıyor
    
    # Test PDF dosyasının yolu - gerçek bir PDF dosyası kullanın
    test_pdf_path = "/Users/mehmeterdogan/python-projects/llm-graph-builder/backend/merged_files/Asude Sitesi Yönetimi Ortak Alan Poliçesi 2020.pdf"
    
    if not Path(test_pdf_path).exists():
        print(f"Test PDF file not found: {test_pdf_path}")
        print("Please provide a valid PDF file path.")
        return
    
    # Output klasörü
    output_dir = "output"
    
    try:
        print("Starting page image generation test...")
        
        # Page image'ları generate et
        file_name, pages, file_extension, generated_images = get_documents_from_file_by_path(
            file_path=test_pdf_path,
            file_name="test.pdf",
            generate_images=True,
            output_dir=output_dir
        )
        
        print(f"\n✅ Success!")
        print(f"📄 Processed file: {file_name}")
        print(f"📑 Total pages: {len(pages)}")
        print(f"🖼️  Generated images: {len(generated_images)}")
        print(f"📁 Output directory: {output_dir}")
        
        if generated_images:
            print("\n🖼️  Generated page images:")
            for i, img_path in enumerate(generated_images, 1):
                print(f"  {i}. {img_path}")
        else:
            print("\n⚠️  No images were generated.")
            
    except Exception as e:
        print(f"\n❌ Error: {e}")
        logging.error(f"Test failed: {e}")

if __name__ == "__main__":
    main()
