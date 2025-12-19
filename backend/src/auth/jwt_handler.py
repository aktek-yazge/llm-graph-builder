# -*- coding: utf-8 -*-
"""
JWT Token Handler for Authentication
"""
import os
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

import jwt
from fastapi import HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from .models import User, TokenData

# Configuration
JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "your-super-secret-key-change-in-production")
JWT_ALGORITHM = os.environ.get("JWT_ALGORITHM", "HS256")
JWT_EXPIRATION_HOURS = int(os.environ.get("JWT_EXPIRATION_HOURS", "24"))

logger = logging.getLogger(__name__)


def create_access_token(
    user_id: int,
    email: str,
    username: str,
    expires_delta: Optional[timedelta] = None
) -> str:
    """
    Create a JWT access token
    
    Args:
        user_id: User's database ID
        email: User's email
        username: User's username
        expires_delta: Optional custom expiration time
    
    Returns:
        Encoded JWT token string
    """
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRATION_HOURS)
    
    payload = {
        "user_id": user_id,
        "email": email,
        "username": username,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    
    token = jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return token


def verify_token(token: str) -> Optional[TokenData]:
    """
    Verify and decode a JWT token
    
    Args:
        token: JWT token string
    
    Returns:
        TokenData if valid, None if invalid
    """
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        
        token_data = TokenData(
            user_id=payload["user_id"],
            email=payload["email"],
            username=payload["username"],
            exp=datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
        )
        
        return token_data
        
    except jwt.ExpiredSignatureError:
        logger.warning("Token expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.warning(f"Invalid token: {e}")
        return None


class JWTBearer(HTTPBearer):
    """
    JWT Bearer token authentication for FastAPI
    
    Usage:
        @app.get("/protected", dependencies=[Depends(JWTBearer())])
        def protected_route():
            pass
    """
    
    def __init__(self, auto_error: bool = True):
        super().__init__(auto_error=auto_error)
    
    async def verify(self, request: Request) -> Optional[TokenData]:
        """Verify JWT token and return TokenData"""
        credentials: Optional[HTTPAuthorizationCredentials] = await super().__call__(request)
        
        if credentials:
            if credentials.scheme.lower() != "bearer":
                raise HTTPException(
                    status_code=403,
                    detail="Invalid authentication scheme"
                )
            
            token_data = verify_token(credentials.credentials)
            if not token_data:
                raise HTTPException(
                    status_code=401,
                    detail="Invalid or expired token"
                )
            
            return token_data
        
        raise HTTPException(
            status_code=403,
            detail="Invalid authorization"
        )


# Create a singleton instance for dependency injection
_jwt_bearer = JWTBearer()


async def _get_token_data(request: Request) -> TokenData:
    """Internal dependency to get TokenData from JWT"""
    token_data = await _jwt_bearer.verify(request)
    if not token_data:
        raise HTTPException(
            status_code=401,
            detail="Could not validate credentials"
        )
    return token_data


# Dependency functions for FastAPI
async def get_current_user(token_data: TokenData = Depends(_get_token_data)) -> TokenData:
    """
    Get current authenticated user from JWT token
    
    Usage:
        @app.get("/me")
        def get_me(current_user: TokenData = Depends(get_current_user)):
            return current_user
    """
    return token_data


async def get_current_user_optional(request: Request) -> Optional[TokenData]:
    """
    Get current user if authenticated, None otherwise
    
    Usage:
        @app.get("/public")
        def public_route(current_user: Optional[TokenData] = Depends(get_current_user_optional)):
            if current_user:
                return {"message": f"Hello {current_user.username}"}
            return {"message": "Hello guest"}
    """
    auth_header = request.headers.get("Authorization")
    
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    
    token = auth_header.split(" ")[1]
    return verify_token(token)

