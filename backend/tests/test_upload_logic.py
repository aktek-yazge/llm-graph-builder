#!/usr/bin/env python3
"""
Test the improved upload_file function with S3 image existence check
"""

import logging
import sys
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add src to path
sys.path.append("src")

def test_upload_logic():
    """Test upload logic with S3 image existence check"""
    
    logging.basicConfig(level=logging.INFO)
    
    # Mock document info
    normalized_filename = "test_document.pdf"
    doc_name = Path(normalized_filename).stem  # "test_document"
    
    # S3 config
    s3_bucket = os.environ.get("S3_BACKUP_BUCKET", "llm-graph-builder-backup")
    aws_access_key_id = os.environ.get("AWS_ACCESS_KEY_ID")
    aws_secret_access_key = os.environ.get("AWS_SECRET_ACCESS_KEY")
    
    print(f"📄 Testing document: {normalized_filename}")
    print(f"🔧 Document name (stem): {doc_name}")
    print(f"📦 S3 bucket: {s3_bucket}")
    
    if s3_bucket and aws_access_key_id and aws_secret_access_key:
        from src.document_sources.s3_upload_utils import check_document_images_exist_in_s3
        
        existing_images_in_s3, existing_image_names = check_document_images_exist_in_s3(
            doc_name, s3_bucket, aws_access_key_id, aws_secret_access_key
        )
        
        print(f"🔍 Images exist in S3: {existing_images_in_s3}")
        print(f"🖼️ Existing image count: {len(existing_image_names)}")
        
        if existing_images_in_s3:
            print("✅ Would skip image generation and use existing S3 images")
            print("📋 Image names:")
            for img_name in existing_image_names[:5]:
                print(f"   - {img_name}")
        else:
            print("🔄 Would generate new images")
            
        # Test with document "1" that exists
        print(f"\n🔄 Testing with existing document '1':")
        existing_images_in_s3_doc1, existing_image_names_doc1 = check_document_images_exist_in_s3(
            "1", s3_bucket, aws_access_key_id, aws_secret_access_key
        )
        
        print(f"🔍 Images exist in S3 for doc '1': {existing_images_in_s3_doc1}")
        print(f"🖼️ Existing image count for doc '1': {len(existing_image_names_doc1)}")
        
    else:
        print("❌ S3 credentials not configured")

if __name__ == "__main__":
    test_upload_logic()
