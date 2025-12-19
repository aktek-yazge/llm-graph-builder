# -*- coding: utf-8 -*-
"""
User models for authentication
"""
from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from datetime import datetime


class UserBase(BaseModel):
    """Base user model"""
    email: EmailStr
    username: str = Field(..., min_length=3, max_length=50)


class UserCreate(UserBase):
    """User registration model"""
    password: str = Field(..., min_length=6)


class UserLogin(BaseModel):
    """User login model"""
    email: EmailStr
    password: str


class User(UserBase):
    """User response model (without password)"""
    id: int
    is_active: bool = True
    created_at: datetime
    
    class Config:
        from_attributes = True


class UserInDB(UserBase):
    """User model with hashed password (for database)"""
    id: int
    hashed_password: str
    is_active: bool = True
    created_at: datetime


class TokenResponse(BaseModel):
    """JWT Token response"""
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds
    user: User


class TokenData(BaseModel):
    """Token payload data"""
    user_id: int
    email: str
    username: str
    exp: datetime

