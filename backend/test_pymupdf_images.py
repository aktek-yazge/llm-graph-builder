#!/usr/bin/env python3
"""
Test script for PyMuPDF image generation functionality
"""

import logging
import sys
from pathlib import Path

# Add src to path
sys.path.append("src")

try:
    from src.document_sources.local_file import generate_page_images_with_pymupdf
    print("✅ PyMuPDF function import successful")
except ImportError as e:
    print(f"❌ Import error: {e}")
    sys.exit(1)

def test_pymupdf_image_generation():
    """Test PyMuPDF image generation specifically"""
    logging.basicConfig(level=logging.INFO)
    
    # Test PDF dosyasının yolu - gerçek bir PDF dosyası kullanın
    test_pdf_path = "/Users/mehmeterdogan/python-projects/llm-graph-builder/backend/merged_files/Asude Sitesi Yönetimi Ortak Alan Poliçesi 2020.pdf"
    
    if not Path(test_pdf_path).exists():
        print(f"Test PDF file not found: {test_pdf_path}")
        print("Please provide a valid PDF file path.")
        return False
    
    # Output klasörü
    output_dir = "test_pymupdf_output"
    
    try:
        print("🔧 Starting PyMuPDF page image generation test...")
        
        # PyMuPDF ile page image'ları generate et
        generated_images = generate_page_images_with_pymupdf(
            file_path=test_pdf_path,
            output_dir=output_dir
        )
        
        print(f"\n✅ PyMuPDF Test Success!")
        print(f"📄 Processed file: {test_pdf_path}")
        print(f"🖼️  Generated images: {len(generated_images)}")
        print(f"📁 Output directory: {output_dir}")
        
        if generated_images:
            print("\n🖼️  Generated PyMuPDF page images:")
            for i, img_path in enumerate(generated_images, 1):
                print(f"  {i}. {img_path}")
                # Check if file exists
                if Path(img_path).exists():
                    size = Path(img_path).stat().st_size
                    print(f"     ✅ File exists, size: {size} bytes")
                else:
                    print(f"     ❌ File not found!")
        else:
            print("\n⚠️  No images were generated.")
            return False
            
        return True
            
    except Exception as e:
        print(f"\n❌ PyMuPDF Test Error: {e}")
        logging.error(f"PyMuPDF test failed: {e}")
        return False

def test_pymupdf_import():
    """Test if PyMuPDF (fitz) is available"""
    try:
        import fitz
        print(f"✅ PyMuPDF (fitz) import successful, version: {fitz.version}")
        return True
    except ImportError:
        print("❌ PyMuPDF (fitz) is not available")
        return False

def main():
    print("🧪 PyMuPDF Image Generation Test Suite")
    print("="*50)
    
    # Test 1: PyMuPDF import
    print("\n1. Testing PyMuPDF import...")
    if not test_pymupdf_import():
        print("❌ PyMuPDF not available, stopping tests")
        return
    
    # Test 2: PyMuPDF image generation
    print("\n2. Testing PyMuPDF image generation...")
    success = test_pymupdf_image_generation()
    
    print("\n" + "="*50)
    if success:
        print("🎉 All PyMuPDF tests passed!")
    else:
        print("❌ Some PyMuPDF tests failed!")

if __name__ == "__main__":
    main()
