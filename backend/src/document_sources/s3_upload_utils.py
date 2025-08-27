import boto3
import os
import logging
from pathlib import Path
from typing import List, Optional, Tuple
import shutil


def upload_files_to_s3(
    file_paths: List[str], 
    bucket_name: str, 
    s3_prefix: str,
    aws_access_key_id: Optional[str] = None,
    aws_secret_access_key: Optional[str] = None,
    delete_local_after_upload: bool = True
) -> Tuple[List[str], List[str]]:
    """
    Multiple files'ı S3'e upload eder.
    
    Args:
        file_paths: Upload edilecek dosyaların local path'leri
        bucket_name: S3 bucket name
        s3_prefix: S3'te dosyaların konumlandırılacağı prefix (örn: "documents/doc1/")
        aws_access_key_id: AWS access key (None ise environment variable kullanılır)
        aws_secret_access_key: AWS secret key (None ise environment variable kullanılır)  
        delete_local_after_upload: Upload sonrası local dosyaları sil
    
    Returns:
        Tuple[List[str], List[str]]: (uploaded_s3_urls, failed_files)
    """
    try:
        # S3 client oluştur
        if aws_access_key_id and aws_secret_access_key:
            s3_client = boto3.client(
                's3',
                aws_access_key_id=aws_access_key_id,
                aws_secret_access_key=aws_secret_access_key
            )
        else:
            # Environment variables veya AWS profile kullan
            s3_client = boto3.client('s3')
        
        uploaded_urls = []
        failed_files = []
        
        logging.info(f"🚀 Starting S3 upload for {len(file_paths)} files to bucket: {bucket_name}")
        
        for file_path in file_paths:
            try:
                if not os.path.exists(file_path):
                    logging.warning(f"⚠️ File not found, skipping: {file_path}")
                    failed_files.append(file_path)
                    continue
                
                # S3 key oluştur (prefix + filename)
                file_name = os.path.basename(file_path)
                s3_key = f"{s3_prefix.rstrip('/')}/{file_name}" if s3_prefix else file_name
                
                # Upload file to S3
                logging.info(f"📤 Uploading {file_path} to s3://{bucket_name}/{s3_key}")
                
                with open(file_path, 'rb') as file_data:
                    s3_client.upload_fileobj(file_data, bucket_name, s3_key)
                
                # S3 URL oluştur
                s3_url = f"s3://{bucket_name}/{s3_key}"
                uploaded_urls.append(s3_url)
                
                logging.info(f"✅ Successfully uploaded: {s3_url}")
                
                # Local dosyayı sil (eğer isteniyorsa)
                if delete_local_after_upload:
                    try:
                        os.remove(file_path)
                        logging.info(f"🗑️ Deleted local file: {file_path}")
                    except Exception as delete_error:
                        logging.warning(f"⚠️ Could not delete local file {file_path}: {delete_error}")
                
            except Exception as upload_error:
                logging.error(f"❌ Failed to upload {file_path}: {upload_error}")
                failed_files.append(file_path)
        
        logging.info(f"✅ S3 upload completed: {len(uploaded_urls)} successful, {len(failed_files)} failed")
        return uploaded_urls, failed_files
        
    except Exception as e:
        logging.error(f"❌ S3 upload error: {e}")
        return [], file_paths


def upload_single_file_to_s3(
    file_path: str,
    bucket_name: str,
    s3_key: str,
    aws_access_key_id: Optional[str] = None,
    aws_secret_access_key: Optional[str] = None,
    delete_local_after_upload: bool = True
) -> Optional[str]:
    """
    Single file'ı S3'e upload eder.
    
    Args:
        file_path: Upload edilecek dosyanın local path'i
        bucket_name: S3 bucket name
        s3_key: S3'te dosyanın key'i (path)
        aws_access_key_id: AWS access key
        aws_secret_access_key: AWS secret key
        delete_local_after_upload: Upload sonrası local dosyayı sil
    
    Returns:
        Optional[str]: S3 URL (başarısızsa None)
    """
    try:
        # S3 client oluştur
        if aws_access_key_id and aws_secret_access_key:
            s3_client = boto3.client(
                's3',
                aws_access_key_id=aws_access_key_id,
                aws_secret_access_key=aws_secret_access_key
            )
        else:
            s3_client = boto3.client('s3')
        
        if not os.path.exists(file_path):
            logging.error(f"❌ File not found: {file_path}")
            return None
        
        # Upload file
        logging.info(f"📤 Uploading {file_path} to s3://{bucket_name}/{s3_key}")
        
        with open(file_path, 'rb') as file_data:
            s3_client.upload_fileobj(file_data, bucket_name, s3_key)
        
        s3_url = f"s3://{bucket_name}/{s3_key}"
        logging.info(f"✅ Successfully uploaded: {s3_url}")
        
        # Local dosyayı sil (eğer isteniyorsa)
        if delete_local_after_upload:
            try:
                os.remove(file_path)
                logging.info(f"🗑️ Deleted local file: {file_path}")
            except Exception as delete_error:
                logging.warning(f"⚠️ Could not delete local file {file_path}: {delete_error}")
        
        return s3_url
        
    except Exception as e:
        logging.error(f"❌ Failed to upload {file_path} to S3: {e}")
        return None


def create_document_output_structure(file_name: str, output_base_dir: str = "output") -> Tuple[str, str, str]:
    """
    Bir document için output klasör yapısını oluşturur.
    
    Args:
        file_name: Dosya adı (uzantılı)
        output_base_dir: Ana output klasörü
    
    Returns:
        Tuple[str, str, str]: (document_dir, pdf_dir, images_dir)
    """
    # Dosya adından uzantıyı çıkar
    doc_name = Path(file_name).stem
    
    # Klasör yapısını oluştur
    document_dir = os.path.join(output_base_dir, doc_name)
    pdf_dir = os.path.join(document_dir, "pdf")
    images_dir = os.path.join(document_dir, "images")
    
    # Klasörleri oluştur
    for directory in [document_dir, pdf_dir, images_dir]:
        os.makedirs(directory, exist_ok=True)
        logging.info(f"📁 Created directory: {directory}")
    
    return document_dir, pdf_dir, images_dir


def cleanup_local_files(directory_path: str):
    """
    Local klasörü ve içindeki tüm dosyaları siler.
    
    Args:
        directory_path: Silinecek klasörün path'i
    """
    try:
        if os.path.exists(directory_path):
            shutil.rmtree(directory_path)
            logging.info(f"🗑️ Cleaned up local directory: {directory_path}")
        else:
            logging.info(f"ℹ️ Directory already cleaned or doesn't exist: {directory_path}")
    except Exception as e:
        logging.error(f"❌ Failed to cleanup directory {directory_path}: {e}")


def generate_s3_presigned_url(
    bucket_name: str,
    s3_key: str,
    aws_access_key_id: Optional[str] = None,
    aws_secret_access_key: Optional[str] = None,
    expiration: int = 3600
) -> Optional[str]:
    """
    S3 object için presigned URL oluşturur.
    
    Args:
        bucket_name: S3 bucket name
        s3_key: S3 object key
        aws_access_key_id: AWS access key
        aws_secret_access_key: AWS secret key
        expiration: URL'in geçerlilik süresi (saniye)
    
    Returns:
        Optional[str]: Presigned URL (başarısızsa None)
    """
    try:
        # S3 client oluştur
        if aws_access_key_id and aws_secret_access_key:
            s3_client = boto3.client(
                's3',
                aws_access_key_id=aws_access_key_id,
                aws_secret_access_key=aws_secret_access_key
            )
        else:
            s3_client = boto3.client('s3')
        
        # Presigned URL oluştur
        presigned_url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket_name, 'Key': s3_key},
            ExpiresIn=expiration
        )
        
        logging.info(f"🔗 Generated presigned URL for s3://{bucket_name}/{s3_key}")
        return presigned_url
        
    except Exception as e:
        logging.error(f"❌ Failed to generate presigned URL for s3://{bucket_name}/{s3_key}: {e}")
        return None


def get_s3_file_info(
    bucket_name: str,
    s3_key: str,
    aws_access_key_id: Optional[str] = None,
    aws_secret_access_key: Optional[str] = None
) -> Optional[dict]:
    """
    S3 object hakkında bilgi alır.
    
    Args:
        bucket_name: S3 bucket name
        s3_key: S3 object key
        aws_access_key_id: AWS access key
        aws_secret_access_key: AWS secret key
    
    Returns:
        Optional[dict]: File bilgileri (başarısızsa None)
    """
    try:
        # S3 client oluştur
        if aws_access_key_id and aws_secret_access_key:
            s3_client = boto3.client(
                's3',
                aws_access_key_id=aws_access_key_id,
                aws_secret_access_key=aws_secret_access_key
            )
        else:
            s3_client = boto3.client('s3')
        
        # Object metadata al
        response = s3_client.head_object(Bucket=bucket_name, Key=s3_key)
        
        return {
            'size': response.get('ContentLength', 0),
            'last_modified': response.get('LastModified'),
            'content_type': response.get('ContentType', 'application/octet-stream'),
            'etag': response.get('ETag', '').strip('"')
        }
        
    except Exception as e:
        logging.error(f"❌ Failed to get S3 file info for s3://{bucket_name}/{s3_key}: {e}")
        return None
