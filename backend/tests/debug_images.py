#!/usr/bin/env python3
"""
Debug script for page image generation
"""

import logging
import sys
from pathlib import Path

# Add src to path
sys.path.append("src")

from src.document_sources.local_file import generate_page_images_from_converter
from docling_core.types.doc import ImageRefMode, PictureItem, TableItem
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption

def debug_image_generation():
    # logging.basicConfig(level=logging.INFO)  # main.py'de yapılıyor
    
    # Test PDF dosyası
    test_pdf_path = "/Users/mehmeterdogan/python-projects/llm-graph-builder/backend/merged_files/Asude Sitesi Yönetimi Ortak Alan Poliçesi 2020.pdf"
    
    if not Path(test_pdf_path).exists():
        print(f"❌ Test PDF file not found: {test_pdf_path}")
        return
    
    print(f"📄 Testing with file: {test_pdf_path}")
    
    try:
        # Pipeline options oluştur
        pipeline_options = PdfPipelineOptions(
            images_scale=2.0,
            generate_page_images=True,
            generate_picture_images=True
        )
        
        print(f"🔧 Pipeline options created:")
        print(f"  - images_scale: {pipeline_options.images_scale}")
        print(f"  - generate_page_images: {pipeline_options.generate_page_images}")
        print(f"  - generate_picture_images: {pipeline_options.generate_picture_images}")
        
        # Custom converter oluştur
        custom_converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            }
        )
        
        print(f"🚀 DocumentConverter created")
        
        # Document'i convert et ve debug bilgilerini yazdır
        print(f"📖 Converting document...")
        conv_res = custom_converter.convert(test_pdf_path)
        
        print(f"✅ Conversion completed")
        print(f"📑 Document pages: {len(conv_res.document.pages)}")
        
        # Her page'i kontrol et
        for page_no, page in conv_res.document.pages.items():
            print(f"\n📄 Page {page_no}:")
            print(f"  - Has 'image' attribute: {hasattr(page, 'image')}")
            
            if hasattr(page, 'image'):
                print(f"  - Image is not None: {page.image is not None}")
                
                if page.image:
                    print(f"  - Has 'pil_image' attribute: {hasattr(page.image, 'pil_image')}")
                    
                    if hasattr(page.image, 'pil_image'):
                        print(f"  - PIL image is not None: {page.image.pil_image is not None}")
                        
                        if page.image.pil_image:
                            print(f"  - PIL image size: {page.image.pil_image.size}")
                            print(f"  - PIL image mode: {page.image.pil_image.mode}")
        
        # Image generation test et
        print(f"\n🖼️  Testing image generation...")
        generated_images = generate_page_images_from_converter(
            custom_converter, test_pdf_path, "debug_output"
        )
        
        print(f"✅ Generated {len(generated_images)} images:")
        for img in generated_images:
            print(f"  - {img}")
            
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    debug_image_generation()
