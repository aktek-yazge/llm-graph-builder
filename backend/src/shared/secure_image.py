# -*- coding: utf-8 -*-
"""
Secure Image Access - JWT Token ile Güvenli Resim Erişimi

Chat yanıtlarındaki resimlerin sadece oturum içinde görülebilmesini sağlar.
URL kopyalanıp başka yere yapıştırılınca çalışmaz.

Kullanım:
    from src.shared.secure_image import generate_secure_image_url, decode_secure_image_token
    
    # Token oluştur (chat yanıtında)
    url = generate_secure_image_url(
        session_id="sess_123",
        user_id="user_456", 
        image_filename="doc_page_001.png",
        base_url="http://localhost:8000"
    )
    
    # Token doğrula (endpoint'te)
    payload = decode_secure_image_token(token)
    s3_key = payload["s3_key"]
"""

import os
import jwt
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# Configuration
SECRET_KEY = os.getenv("SECURE_IMAGE_SECRET_KEY", "change-me-in-production-use-32-chars")
EXPIRE_MINUTES = int(os.getenv("SECURE_IMAGE_EXPIRE_MINUTES", "10"))


def generate_secure_image_url(
    session_id: str,
    user_id: str,
    image_filename: str,
    base_url: str,
    expire_minutes: Optional[int] = None,
) -> str:
    """
    Chat yanıtlarında kullanılacak güvenli resim URL'i oluşturur.
    
    Her çağrıda yeni token oluşturulur - kullanıcı geçmiş chat'e döndüğünde
    de çalışır (yeni token alır).
    
    Args:
        session_id: Kullanıcının oturum ID'si
        user_id: Kullanıcı ID'si
        image_filename: Chunk.page_link değeri (örn: "doc_page_001.png")
        base_url: API base URL (örn: "http://localhost:8000")
        expire_minutes: Token geçerlilik süresi (dakika), default: SECURE_IMAGE_EXPIRE_MINUTES
        
    Returns:
        Güvenli resim URL'i: {base_url}/secure-image/{jwt_token}
    """
    if expire_minutes is None:
        expire_minutes = EXPIRE_MINUTES
    
    # Dosya adından S3 key oluştur
    # Format: "Aksa-09.03.2016_page_001.png" → "documents/Aksa-09.03.2016/images/Aksa-09.03.2016_page_001.png"
    s3_key = _filename_to_s3_key(image_filename)
    
    payload = {
        "session_id": session_id,
        "user_id": user_id,
        "s3_key": s3_key,
        "filename": image_filename,
        "iat": datetime.utcnow(),
        "exp": datetime.utcnow() + timedelta(minutes=expire_minutes),
    }
    
    token = jwt.encode(payload, SECRET_KEY, algorithm="HS256")
    
    # URL'i oluştur
    secure_url = f"{base_url.rstrip('/')}/secure-image/{token}"
    
    logger.debug(f"Generated secure URL for {image_filename} (session: {session_id[:8]}...)")
    
    return secure_url


def decode_secure_image_token(token: str) -> dict:
    """
    JWT token'ı decode et ve doğrula.
    
    Args:
        token: JWT token string
        
    Returns:
        Payload dict: {session_id, user_id, s3_key, filename, iat, exp}
        
    Raises:
        jwt.ExpiredSignatureError: Token süresi dolmuş
        jwt.InvalidTokenError: Token geçersiz
    """
    return jwt.decode(token, SECRET_KEY, algorithms=["HS256"])


def _filename_to_s3_key(filename: str) -> str:
    """
    Dosya adından S3 key oluşturur.
    
    Args:
        filename: "Aksa-09.03.2016_page_001.png"
        
    Returns:
        S3 key: "documents/Aksa-09.03.2016/images/Aksa-09.03.2016_page_001.png"
    """
    # _page_ pattern'ını bul ve doc_name'i çıkar
    if "_page_" in filename:
        doc_name = filename.rsplit("_page_", 1)[0]
    else:
        # Fallback: uzantıyı çıkar
        doc_name = os.path.splitext(filename)[0]
    
    return f"documents/{doc_name}/images/{filename}"


def validate_session(token_session_id: str, request_session_id: str) -> bool:
    """
    Token'daki session ID'nin mevcut session ile eşleşip eşleşmediğini kontrol eder.
    
    Args:
        token_session_id: JWT token'dan gelen session ID
        request_session_id: Request'ten gelen session ID
        
    Returns:
        True eğer eşleşiyorsa
    """
    return token_session_id == request_session_id
