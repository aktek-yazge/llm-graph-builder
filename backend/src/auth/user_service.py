# -*- coding: utf-8 -*-
"""
User Service - Password hashing and user management
"""
import os
import hashlib
import hmac
import secrets
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, List

from .models import UserCreate, UserLogin, User, UserInDB

logger = logging.getLogger(__name__)

# Simple in-memory user storage (can be replaced with PostgreSQL)
# Format: {email: UserInDB}
_users_db: Dict[str, UserInDB] = {}
_user_id_counter = 0


def _hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
    """
    Hash password with salt using PBKDF2
    
    Returns:
        Tuple of (hashed_password, salt)
    """
    if salt is None:
        salt = secrets.token_hex(16)
    
    # Use PBKDF2 with SHA256
    key = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode('utf-8'),
        salt.encode('utf-8'),
        100000  # iterations
    )
    
    hashed = key.hex()
    return f"{salt}${hashed}", salt


def _verify_password(password: str, hashed_password: str) -> bool:
    """
    Verify password against hashed password
    """
    try:
        salt, stored_hash = hashed_password.split('$')
        new_hash, _ = _hash_password(password, salt)
        return hmac.compare_digest(new_hash, hashed_password)
    except Exception as e:
        logger.error(f"Password verification error: {e}")
        return False


def create_user(user_data: UserCreate) -> Optional[User]:
    """
    Create a new user
    
    Args:
        user_data: User registration data
    
    Returns:
        Created User or None if email already exists
    """
    global _user_id_counter
    
    # Check if email already exists
    if user_data.email in _users_db:
        logger.warning(f"User with email {user_data.email} already exists")
        return None
    
    # Hash password
    hashed_password, _ = _hash_password(user_data.password)
    
    # Create user
    _user_id_counter += 1
    user_in_db = UserInDB(
        id=_user_id_counter,
        email=user_data.email,
        username=user_data.username,
        hashed_password=hashed_password,
        is_active=True,
        created_at=datetime.now(timezone.utc)
    )
    
    _users_db[user_data.email] = user_in_db
    
    logger.info(f"Created user: {user_data.email}")
    
    return User(
        id=user_in_db.id,
        email=user_in_db.email,
        username=user_in_db.username,
        is_active=user_in_db.is_active,
        created_at=user_in_db.created_at
    )


def authenticate_user(login_data: UserLogin) -> Optional[User]:
    """
    Authenticate user with email and password
    
    Args:
        login_data: User login credentials
    
    Returns:
        User if authenticated, None otherwise
    """
    user_in_db = _users_db.get(login_data.email)
    
    if not user_in_db:
        logger.warning(f"User not found: {login_data.email}")
        return None
    
    if not user_in_db.is_active:
        logger.warning(f"User is not active: {login_data.email}")
        return None
    
    if not _verify_password(login_data.password, user_in_db.hashed_password):
        logger.warning(f"Invalid password for: {login_data.email}")
        return None
    
    return User(
        id=user_in_db.id,
        email=user_in_db.email,
        username=user_in_db.username,
        is_active=user_in_db.is_active,
        created_at=user_in_db.created_at
    )


def get_user_by_id(user_id: int) -> Optional[User]:
    """Get user by ID"""
    for user_in_db in _users_db.values():
        if user_in_db.id == user_id:
            return User(
                id=user_in_db.id,
                email=user_in_db.email,
                username=user_in_db.username,
                is_active=user_in_db.is_active,
                created_at=user_in_db.created_at
            )
    return None


def get_user_by_email(email: str) -> Optional[User]:
    """Get user by email"""
    user_in_db = _users_db.get(email)
    if user_in_db:
        return User(
            id=user_in_db.id,
            email=user_in_db.email,
            username=user_in_db.username,
            is_active=user_in_db.is_active,
            created_at=user_in_db.created_at
        )
    return None


def get_all_users() -> List[User]:
    """Get all users (admin only)"""
    return [
        User(
            id=u.id,
            email=u.email,
            username=u.username,
            is_active=u.is_active,
            created_at=u.created_at
        )
        for u in _users_db.values()
    ]


# Initialize with a default admin user if configured
def _init_default_admin():
    """Initialize default admin user from environment variables"""
    admin_email = os.environ.get("ADMIN_EMAIL")
    admin_password = os.environ.get("ADMIN_PASSWORD")
    admin_username = os.environ.get("ADMIN_USERNAME", "admin")
    
    if admin_email and admin_password:
        if admin_email not in _users_db:
            create_user(UserCreate(
                email=admin_email,
                username=admin_username,
                password=admin_password
            ))
            logger.info(f"Default admin user created: {admin_email}")


# Run on module load
_init_default_admin()

