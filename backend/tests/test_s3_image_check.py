#!/usr/bin/env python3
"""
Test script for S3 image existence check functionality
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

try:
    from src.document_sources.s3_upload_utils import check_document_images_exist_in_s3, check_s3_file_exists
    print("✅ Import successful")
except ImportError as e:
    print(f"❌ Import error: {e}")
    sys.exit(1)

def test_s3_image_check():
    """S3'te image varlığını test et"""
    
    logging.basicConfig(level=logging.INFO)
    
    # Environment variables
    s3_bucket = os.environ.get("S3_BACKUP_BUCKET", "llm-graph-builder-backup")
    aws_access_key_id = os.environ.get("AWS_ACCESS_KEY_ID")
    aws_secret_access_key = os.environ.get("AWS_SECRET_ACCESS_KEY")
    
    if not s3_bucket or not aws_access_key_id or not aws_secret_access_key:
        print("❌ S3 credentials not configured in environment variables")
        print("Required: S3_BACKUP_BUCKET, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY")
        return
    
    print(f"🔧 Using S3 bucket: {s3_bucket}")
    
    # Test document names (replace with actual document names you have in S3)
    test_documents = [
        "1",  # Normalized document name
        "2",  # Another normalized document name  
        "test_document",  # Non-existing document
    ]
    
    for doc_name in test_documents:
        print(f"\n📄 Testing document: {doc_name}")
        
        # Check if images exist
        images_exist, image_names = check_document_images_exist_in_s3(
            doc_name, s3_bucket, aws_access_key_id, aws_secret_access_key
        )
        
        if images_exist:
            print(f"✅ Found {len(image_names)} page images in S3:")
            for img_name in image_names[:5]:  # Show first 5
                print(f"   - {img_name}")
            if len(image_names) > 5:
                print(f"   ... and {len(image_names) - 5} more")
        else:
            print(f"❌ No page images found in S3")
        
        # Also test individual file check
        test_key = f"documents/{doc_name}/{doc_name}_page_001.png"
        file_exists = check_s3_file_exists(
            s3_bucket, test_key, aws_access_key_id, aws_secret_access_key
        )
        print(f"🔍 Single file check ({test_key}): {'EXISTS' if file_exists else 'NOT FOUND'}")

if __name__ == "__main__":
    test_s3_image_check()
