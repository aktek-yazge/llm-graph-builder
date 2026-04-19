# -*- coding: utf-8 -*-
"""
Auth Router - Login, Register, Profile endpoints
"""
import logging

from fastapi import APIRouter, HTTPException, Depends, status

from .models import UserCreate, UserLogin, TokenResponse, User
from .jwt_handler import (
    create_access_token,
    get_current_user,
    TokenData,
    JWT_EXPIRATION_HOURS,
)
from .user_service import (
    create_user,
    authenticate_user,
    get_user_by_id,
    get_all_users,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(user_data: UserCreate):
    """
    Register a new user
    
    Returns JWT token on successful registration
    """
    user = create_user(user_data)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    # Create access token
    access_token = create_access_token(
        user_id=user.id,
        email=user.email,
        username=user.username
    )
    
    logger.info(f"User registered: {user.email}")
    
    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=JWT_EXPIRATION_HOURS * 3600,
        user=user
    )


@router.post("/login", response_model=TokenResponse)
async def login(login_data: UserLogin):
    """
    Login with email and password
    
    Returns JWT token on successful authentication
    """
    user = authenticate_user(login_data)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    # Create access token
    access_token = create_access_token(
        user_id=user.id,
        email=user.email,
        username=user.username
    )
    
    logger.info(f"User logged in: {user.email}")
    
    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=JWT_EXPIRATION_HOURS * 3600,
        user=user
    )


@router.get("/me", response_model=User)
async def get_me(current_user: TokenData = Depends(get_current_user)):
    """
    Get current authenticated user profile
    
    Requires valid JWT token in Authorization header
    """
    user = get_user_by_id(current_user.user_id)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    return user


@router.post("/verify")
async def verify_token_endpoint(current_user: TokenData = Depends(get_current_user)):
    """
    Verify if the JWT token is valid
    
    Returns user info if token is valid
    """
    return {
        "valid": True,
        "user_id": current_user.user_id,
        "email": current_user.email,
        "username": current_user.username,
        "expires": current_user.exp.isoformat()
    }


@router.post("/logout")
async def logout(current_user: TokenData = Depends(get_current_user)):
    """
    Logout user
    
    Note: JWT tokens are stateless, so this just returns success.
    For true logout, implement token blacklisting or use short-lived tokens.
    """
    logger.info(f"User logged out: {current_user.email}")
    
    return {"message": "Successfully logged out"}


@router.get("/users", response_model=list[User])
async def list_users(current_user: TokenData = Depends(get_current_user)):
    """
    List all users (authenticated users only)
    
    In production, restrict this to admin users
    """
    return get_all_users()

