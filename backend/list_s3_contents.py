#!/usr/bin/env python3
"""
List S3 bucket contents to see what's available
"""

import boto3
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def list_s3_contents():
    """S3 bucket içeriğini listele"""
    
    s3_bucket = os.environ.get("S3_BACKUP_BUCKET", "llm-graph-builder-backup")
    aws_access_key_id = os.environ.get("AWS_ACCESS_KEY_ID")
    aws_secret_access_key = os.environ.get("AWS_SECRET_ACCESS_KEY")
    
    if not s3_bucket or not aws_access_key_id or not aws_secret_access_key:
        print("❌ S3 credentials not configured")
        return
    
    try:
        s3_client = boto3.client(
            's3',
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key
        )
        
        print(f"📦 S3 Bucket: {s3_bucket}")
        print("📁 Contents:")
        
        # List all objects in the bucket
        response = s3_client.list_objects_v2(Bucket=s3_bucket)
        
        if 'Contents' not in response:
            print("❌ Bucket is empty or doesn't exist")
            return
        
        # Group by folders
        folders = {}
        for obj in response['Contents']:
            key = obj['Key']
            parts = key.split('/')
            
            if len(parts) > 1:
                folder = parts[0]
                if folder not in folders:
                    folders[folder] = []
                folders[folder].append(key)
            else:
                print(f"📄 {key}")
        
        # Print folder contents
        for folder, files in folders.items():
            print(f"\n📁 {folder}/")
            for file in files[:10]:  # Show first 10 files
                filename = file.split('/')[-1]
                print(f"   📄 {filename}")
            if len(files) > 10:
                print(f"   ... and {len(files) - 10} more files")
        
    except Exception as e:
        print(f"❌ Error listing S3 contents: {e}")

if __name__ == "__main__":
    list_s3_contents()
