import boto3
from botocore.exceptions import ClientError
import os
import logging
import time
from pathlib import Path
from typing import List, Optional, Tuple, Union
import shutil

# S3 Upload Retry Configuration
S3_RETRY_ATTEMPTS = 3
S3_RETRY_WAIT_MIN = 1  # seconds
S3_RETRY_WAIT_MAX = 5  # seconds


def _upload_with_retry(s3_client, file_path: str, bucket_name: str, s3_key: str) -> bool:
    """
    S3'e dosya upload eder, başarısız olursa retry yapar.
    
    Args:
        s3_client: Boto3 S3 client
        file_path: Local dosya yolu
        bucket_name: S3 bucket adı
        s3_key: S3 key (path)
    
    Returns:
        bool: Başarılı ise True, değilse False
    
    Raises:
        Exception: Tüm retry'lar başarısız olursa
    """
    last_exception = None
    
    for attempt in range(1, S3_RETRY_ATTEMPTS + 1):
        try:
            with open(file_path, "rb") as file_data:
                s3_client.upload_fileobj(file_data, bucket_name, s3_key)
            return True
        except Exception as e:
            last_exception = e
            error_str = str(e).lower()
            
            # Retry yapılabilir hatalar
            is_retryable = any([
                "timeout" in error_str,
                "connection" in error_str,
                "503" in error_str,
                "500" in error_str,
                "slow" in error_str,
                "reset" in error_str,
                "broken pipe" in error_str,
            ])
            
            if attempt < S3_RETRY_ATTEMPTS and is_retryable:
                wait_time = S3_RETRY_WAIT_MIN * (2 ** (attempt - 1))  # Exponential backoff
                wait_time = min(wait_time, S3_RETRY_WAIT_MAX)
                logging.warning(
                    f"🔄 S3 upload failed for {os.path.basename(file_path)}, retrying in {wait_time}s... "
                    f"(attempt {attempt}/{S3_RETRY_ATTEMPTS}): {str(e)[:100]}"
                )
                time.sleep(wait_time)
            elif attempt < S3_RETRY_ATTEMPTS:
                # Non-retryable error but still have attempts - try once more
                wait_time = S3_RETRY_WAIT_MIN
                logging.warning(
                    f"🔄 S3 upload error for {os.path.basename(file_path)}, retrying in {wait_time}s... "
                    f"(attempt {attempt}/{S3_RETRY_ATTEMPTS}): {str(e)[:100]}"
                )
                time.sleep(wait_time)
            else:
                logging.error(
                    f"❌ S3 upload failed after {S3_RETRY_ATTEMPTS} attempts for {os.path.basename(file_path)}: {e}"
                )
    
    raise last_exception


def upload_files_to_s3(
    file_paths: List[str],
    bucket_name: str,
    s3_prefix: str,
    aws_access_key_id: Optional[str] = None,
    aws_secret_access_key: Optional[str] = None,
    delete_local_after_upload: bool = True,
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
        from botocore.config import Config

        # S3 config with signature version 4
        config = Config(
            signature_version="s3v4", region_name="us-east-1"  # Default region
        )

        # S3 client oluştur
        if aws_access_key_id and aws_secret_access_key:
            s3_client = boto3.client(
                "s3",
                aws_access_key_id=aws_access_key_id,
                aws_secret_access_key=aws_secret_access_key,
                config=config,
            )
        else:
            # Environment variables veya AWS profile kullan
            s3_client = boto3.client("s3", config=config)

        uploaded_urls = []
        failed_files = []

        logging.info(
            f"🚀 Starting S3 upload for {len(file_paths)} files to bucket: {bucket_name}"
        )

        for file_path in file_paths:
            try:
                if not os.path.exists(file_path):
                    logging.warning(f"⚠️ File not found, skipping: {file_path}")
                    failed_files.append(file_path)
                    continue

                # S3 key oluştur (prefix + filename)
                file_name = os.path.basename(file_path)
                s3_key = (
                    f"{s3_prefix.rstrip('/')}/{file_name}" if s3_prefix else file_name
                )

                # Upload file to S3 with retry
                logging.info(f"📤 Uploading {file_path} to s3://{bucket_name}/{s3_key}")

                try:
                    _upload_with_retry(s3_client, file_path, bucket_name, s3_key)
                    
                    # S3 URL oluştur
                    s3_url = f"s3://{bucket_name}/{s3_key}"
                    uploaded_urls.append(s3_url)
                    logging.info(f"✅ Successfully uploaded: {s3_url}")
                except Exception as retry_error:
                    logging.error(f"❌ S3 upload failed after retries for {file_path}: {retry_error}")
                    failed_files.append(file_path)
                    continue

                # Local dosyayı sil (eğer isteniyorsa)
                if delete_local_after_upload:
                    try:
                        os.remove(file_path)
                        logging.info(f"🗑️ Deleted local file: {file_path}")
                    except Exception as delete_error:
                        logging.warning(
                            f"⚠️ Could not delete local file {file_path}: {delete_error}"
                        )

            except Exception as upload_error:
                logging.error(f"❌ Failed to upload {file_path}: {upload_error}")
                failed_files.append(file_path)

        logging.info(
            f"✅ S3 upload completed: {len(uploaded_urls)} successful, {len(failed_files)} failed"
        )
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
    delete_local_after_upload: bool = True,
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
                "s3",
                aws_access_key_id=aws_access_key_id,
                aws_secret_access_key=aws_secret_access_key,
            )
        else:
            s3_client = boto3.client("s3")

        if not os.path.exists(file_path):
            logging.error(f"❌ File not found: {file_path}")
            return None

        # Upload file with retry
        logging.info(f"📤 Uploading {file_path} to s3://{bucket_name}/{s3_key}")

        try:
            _upload_with_retry(s3_client, file_path, bucket_name, s3_key)
        except Exception as retry_error:
            logging.error(f"❌ S3 upload failed after retries for {file_path}: {retry_error}")
            return None

        s3_url = f"s3://{bucket_name}/{s3_key}"
        logging.info(f"✅ Successfully uploaded: {s3_url}")

        # Local dosyayı sil (eğer isteniyorsa)
        if delete_local_after_upload:
            try:
                os.remove(file_path)
                logging.info(f"🗑️ Deleted local file: {file_path}")
            except Exception as delete_error:
                logging.warning(
                    f"⚠️ Could not delete local file {file_path}: {delete_error}"
                )

        return s3_url

    except Exception as e:
        logging.error(f"❌ Failed to upload {file_path} to S3: {e}")
        return None


def create_document_output_structure(
    file_name: str, output_base_dir: str = "output"
) -> Tuple[str, str, str]:
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

    # Klasörleri oluştur (sadece yeni oluşturulduğunda log yaz)
    for directory in [document_dir, pdf_dir, images_dir]:
        if not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)
            logging.info(f"📁 Created directory: {directory}")
        # Zaten varsa sessizce devam et

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
            logging.info(
                f"ℹ️ Directory already cleaned or doesn't exist: {directory_path}"
            )
    except Exception as e:
        logging.error(f"❌ Failed to cleanup directory {directory_path}: {e}")


def get_bucket_region(
    bucket_name: str,
    aws_access_key_id: Optional[str] = None,
    aws_secret_access_key: Optional[str] = None,
) -> str:
    """
    S3 bucket'ın region'ını tespit eder.

    Args:
        bucket_name: S3 bucket name
        aws_access_key_id: AWS access key
        aws_secret_access_key: AWS secret key

    Returns:
        str: Bucket region (default: us-east-1)
    """
    try:
        # S3 client oluştur (region-agnostic)
        if aws_access_key_id and aws_secret_access_key:
            s3_client = boto3.client(
                "s3",
                aws_access_key_id=aws_access_key_id,
                aws_secret_access_key=aws_secret_access_key,
            )
        else:
            s3_client = boto3.client("s3")

        # Bucket location constraint al
        response = s3_client.get_bucket_location(Bucket=bucket_name)
        region = response.get("LocationConstraint")

        # us-east-1 için LocationConstraint None gelir
        if region is None:
            region = "us-east-1"

        logging.info(f"🌍 Detected bucket {bucket_name} region: {region}")
        return region

    except Exception as e:
        logging.warning(
            f"⚠️ Could not detect bucket region for {bucket_name}: {e}, using us-east-1"
        )
        return "us-east-1"


def generate_s3_presigned_url(
    bucket_name: str,
    s3_key: str,
    aws_access_key_id: Optional[str] = None,
    aws_secret_access_key: Optional[str] = None,
    expiration: int = 3600,
    inline: bool = False,
    content_type: Optional[str] = None,
) -> Optional[str]:
    """
    S3 object için presigned URL oluşturur.

    Args:
        bucket_name: S3 bucket name
        s3_key: S3 object key
        aws_access_key_id: AWS access key
        aws_secret_access_key: AWS secret key
        expiration: URL'in geçerlilik süresi (saniye)
        inline: True ise tarayıcıda inline görüntülenir (download yerine)
        content_type: Response content type (örn: 'application/pdf')

    Returns:
        Optional[str]: Presigned URL (başarısızsa None)
    """
    try:
        from botocore.config import Config

        # Bucket'ın region'ını tespit et
        bucket_region = get_bucket_region(
            bucket_name, aws_access_key_id, aws_secret_access_key
        )

        # S3 config with signature version 4 and correct region
        config = Config(signature_version="s3v4", region_name=bucket_region)

        # S3 client oluştur
        if aws_access_key_id and aws_secret_access_key:
            s3_client = boto3.client(
                "s3",
                aws_access_key_id=aws_access_key_id,
                aws_secret_access_key=aws_secret_access_key,
                config=config,
            )
        else:
            s3_client = boto3.client("s3", config=config)

        # Presigned URL parametreleri
        params = {"Bucket": bucket_name, "Key": s3_key}
        
        # Inline viewing için response headers ekle
        if inline:
            params["ResponseContentDisposition"] = "inline"
        if content_type:
            params["ResponseContentType"] = content_type

        # Presigned URL oluştur
        presigned_url = s3_client.generate_presigned_url(
            "get_object",
            Params=params,
            ExpiresIn=expiration,
        )

        logging.info(f"🔗 Generated presigned URL for s3://{bucket_name}/{s3_key}")
        return presigned_url

    except Exception as e:
        logging.error(
            f"❌ Failed to generate presigned URL for s3://{bucket_name}/{s3_key}: {e}"
        )
        return None


def get_s3_file_info(
    bucket_name: str,
    s3_key: str,
    aws_access_key_id: Optional[str] = None,
    aws_secret_access_key: Optional[str] = None,
) -> Optional[dict]:
    """
    S3'te bir dosyanın bilgilerini getirir.

    Args:
        bucket_name: S3 bucket name
        s3_key: S3 key (file path)
        aws_access_key_id: AWS access key
        aws_secret_access_key: AWS secret key

    Returns:
        dict: Dosya bilgileri (size, last_modified, content_type, etag)
              Dosya yoksa None
    """
    try:
        # S3 client oluştur
        if aws_access_key_id and aws_secret_access_key:
            s3_client = boto3.client(
                "s3",
                aws_access_key_id=aws_access_key_id,
                aws_secret_access_key=aws_secret_access_key,
            )
        else:
            s3_client = boto3.client("s3")

        # Object metadata al
        response = s3_client.head_object(Bucket=bucket_name, Key=s3_key)

        return {
            "size": response.get("ContentLength", 0),
            "last_modified": response.get("LastModified"),
            "content_type": response.get("ContentType", "application/octet-stream"),
            "etag": response.get("ETag", "").strip('"'),
        }

    except Exception as e:
        logging.error(
            f"❌ Failed to get S3 file info for s3://{bucket_name}/{s3_key}: {e}"
        )
        return None


def check_s3_file_exists(
    bucket_name: str,
    s3_key: str,
    aws_access_key_id: Optional[str] = None,
    aws_secret_access_key: Optional[str] = None,
) -> bool:
    """
    S3'te bir dosyanın var olup olmadığını kontrol eder.

    Args:
        bucket_name: S3 bucket name
        s3_key: S3 key (file path)
        aws_access_key_id: AWS access key
        aws_secret_access_key: AWS secret key

    Returns:
        bool: Dosya varsa True, yoksa False
    """
    try:
        # S3 client oluştur
        if aws_access_key_id and aws_secret_access_key:
            s3_client = boto3.client(
                "s3",
                aws_access_key_id=aws_access_key_id,
                aws_secret_access_key=aws_secret_access_key,
            )
        else:
            s3_client = boto3.client("s3")

        # Object'in varlığını kontrol et
        s3_client.head_object(Bucket=bucket_name, Key=s3_key)
        return True

    except ClientError as e:
        if e.response["Error"]["Code"] == "404":
            return False
        else:
            logging.error(
                f"❌ Error checking S3 file existence for s3://{bucket_name}/{s3_key}: {e}"
            )
            return False
    except Exception as e:
        logging.error(
            f"❌ Error checking S3 file existence for s3://{bucket_name}/{s3_key}: {e}"
        )
        return False


def check_document_images_exist_in_s3(
    document_name: str,
    bucket_name: str,
    aws_access_key_id: Optional[str] = None,
    aws_secret_access_key: Optional[str] = None,
) -> Tuple[bool, List[str]]:
    """
    Belgenin S3'te page image'larının var olup olmadığını kontrol eder.

    Args:
        document_name: Belge adı (uzantısız)
        bucket_name: S3 bucket name
        aws_access_key_id: AWS access key
        aws_secret_access_key: AWS secret key

    Returns:
        Tuple[bool, List[str]]: (images_exist, existing_image_names)
    """
    try:
        # S3 client oluştur
        if aws_access_key_id and aws_secret_access_key:
            s3_client = boto3.client(
                "s3",
                aws_access_key_id=aws_access_key_id,
                aws_secret_access_key=aws_secret_access_key,
            )
        else:
            s3_client = boto3.client("s3")

        # S3 yapısı: documents/{doc_name}/images/{doc_name}_page_*.png
        # Sadece images/ alt klasöründe ara (standart yapı)
        s3_prefix_images = f"documents/{document_name}/images/"

        # S3'te bu prefix ile başlayan dosyaları listele
        response = s3_client.list_objects_v2(
            Bucket=bucket_name, Prefix=s3_prefix_images
        )

        existing_images = []

        if "Contents" in response:
            # Page image dosyalarını filtrele ve gerçekten var olup olmadığını kontrol et
            for obj in response["Contents"]:
                key = obj["Key"]
                filename = os.path.basename(key)

                # Page image pattern'ini kontrol et: doc_name_page_001.png
                # images/ klasörü içinde olmalı (folder'ları atla)
                if (
                    filename.startswith(f"{document_name}_page_")
                    and filename.endswith(".png")
                    and not key.endswith("/")  # Folder değil, dosya olmalı
                ):
                    # Gerçek dosya varlığını kontrol et (head_object ile)
                    # list_objects bazen yanlış pozitif sonuç verebilir
                    try:
                        s3_client.head_object(Bucket=bucket_name, Key=key)
                        # Full S3 key'i sakla (path bilgisi için)
                        existing_images.append({"filename": filename, "s3_key": key})
                        logging.debug(f"✅ Found image in S3: {key}")
                    except ClientError as e:
                        if e.response["Error"]["Code"] == "404":
                            # Dosya list_objects'te görünüyor ama gerçekte yok
                            logging.warning(
                                f"⚠️ Image listed in S3 but not found (404): {key}"
                            )
                        else:
                            # Diğer hatalar için de ekle (erişim sorunu olabilir)
                            logging.warning(
                                f"⚠️ Error checking image in S3: {key}, error: {e}"
                            )
                            existing_images.append({"filename": filename, "s3_key": key})
                    except Exception as ex:
                        # Hata durumunda ekle (erişim sorunu olabilir, download sırasında kontrol edilecek)
                        logging.warning(
                            f"⚠️ Exception checking image in S3: {key}, error: {ex}"
                        )
                        existing_images.append({"filename": filename, "s3_key": key})

        if existing_images:
            # Sadece filename'leri döndür (backward compatibility için)
            logging.info(
                f"🔍 Found {len(existing_images)} existing page images in S3 for document: {document_name}"
            )
            # Hem filename listesi hem de full key bilgisi döndür
            return True, existing_images  # Artık dict listesi döndürüyor
        else:
            logging.info(f"🔍 No page images found in S3 for document: {document_name}")
            return False, []

    except Exception as e:
        logging.error(
            f"❌ Error checking document images in S3 for {document_name}: {e}"
        )
        return False, []


def download_images_from_s3(
    image_names: List[Union[str, dict]],
    bucket_name: str,
    document_name: str,
    local_images_dir: str,
    aws_access_key_id: Optional[str] = None,
    aws_secret_access_key: Optional[str] = None,
) -> List[str]:
    """
    Download page images from S3 to local directory

    Args:
        image_names: List of image file names or dicts with {"filename": str, "s3_key": str} to download
        bucket_name: S3 bucket name
        document_name: Document name (used to construct S3 prefix if s3_key not provided)
        local_images_dir: Local directory to save downloaded images
        aws_access_key_id: AWS access key (None ise environment variable kullanılır)
        aws_secret_access_key: AWS secret key (None ise environment variable kullanılır)

    Returns:
        List[str]: List of local file paths for downloaded images
    """
    try:
        from botocore.config import Config

        # S3 config with signature version 4
        config = Config(
            signature_version="s3v4", region_name="us-east-1"  # Default region
        )

        # S3 client oluştur
        if aws_access_key_id and aws_secret_access_key:
            s3_client = boto3.client(
                "s3",
                aws_access_key_id=aws_access_key_id,
                aws_secret_access_key=aws_secret_access_key,
                config=config,
            )
        else:
            # Environment variables veya AWS profile kullan
            s3_client = boto3.client("s3", config=config)

        # Ensure local directory exists
        os.makedirs(local_images_dir, exist_ok=True)

        downloaded_files = []
        base_s3_prefix = f"documents/{document_name}/images"

        logging.info(
            f"📥 Starting S3 download for {len(image_names)} images from bucket: {bucket_name}"
        )

        for image_item in image_names:
            try:
                # Eğer dict ise (s3_key içeriyorsa), direkt o key'i kullan
                if isinstance(image_item, dict):
                    image_name = image_item.get("filename", "")
                    s3_key = image_item.get("s3_key", "")
                    if not s3_key:
                        # Fallback: normal path oluştur
                        s3_key = f"{base_s3_prefix}/{image_name}"
                else:
                    # String ise (backward compatibility)
                    image_name = image_item
                    s3_key = f"{base_s3_prefix}/{image_name}"

                # Local file path
                local_file_path = os.path.join(local_images_dir, image_name)

                # Download file from S3
                # s3_key zaten check_document_images_exist_in_s3'ten geldiği için doğru olmalı
                logging.info(
                    f"📥 Downloading {image_name} from s3://{bucket_name}/{s3_key}"
                )

                # download_file kullan (head_object kontrolü zaten check_document_images_exist_in_s3'te yapıldı)
                s3_client.download_file(bucket_name, s3_key, local_file_path)

                downloaded_files.append(local_file_path)
                logging.info(f"✅ Successfully downloaded: {local_file_path}")

            except ClientError as e:
                if e.response["Error"]["Code"] == "NoSuchKey" or e.response["Error"]["Code"] == "404":
                    logging.warning(f"⚠️ Image not found in S3: {s3_key}")
                else:
                    logging.error(f"❌ Failed to download {image_name} from S3: {e}")
            except Exception as download_error:
                logging.error(
                    f"❌ Failed to download {image_name} from S3: {download_error}"
                )

        logging.info(
            f"✅ S3 download completed: {len(downloaded_files)} successful out of {len(image_names)}"
        )
        return downloaded_files

    except Exception as e:
        logging.error(f"❌ S3 download error: {e}")
        return []


def upload_files_to_s3_with_structure(
    file_paths: List[str],
    bucket_name: str,
    s3_prefix: str,
    aws_access_key_id: Optional[str] = None,
    aws_secret_access_key: Optional[str] = None,
    delete_local_after_upload: bool = True,
) -> Tuple[List[str], List[str]]:
    """
    Multiple files'ı organized S3 structure ile upload eder.
    Original upload_files_to_s3 ile aynı ama sadece isim farklı.

    Args:
        file_paths: Upload edilecek dosyaların local path'leri
        bucket_name: S3 bucket name
        s3_prefix: S3'te dosyaların konumlandırılacağı prefix (örn: "documents/doc1/images/")
        aws_access_key_id: AWS access key (None ise environment variable kullanılır)
        aws_secret_access_key: AWS secret key (None ise environment variable kullanılır)
        delete_local_after_upload: Upload sonrası local dosyaları sil

    Returns:
        Tuple[List[str], List[str]]: (uploaded_s3_urls, failed_files)
    """
    # Bu fonksiyon upload_files_to_s3 ile tamamen aynı - sadece organized structure için alias
    return upload_files_to_s3(
        file_paths=file_paths,
        bucket_name=bucket_name,
        s3_prefix=s3_prefix,
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
        delete_local_after_upload=delete_local_after_upload,
    )
