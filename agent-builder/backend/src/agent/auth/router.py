"""
Auth Router
===========

POST /api/v2/auth/register  — Register new tenant + admin user
POST /api/v2/auth/login     — Email/password -> access + refresh tokens
POST /api/v2/auth/refresh   — Refresh access token
GET  /api/v2/auth/me        — Current user profile
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, field_validator

from .middleware import CurrentUser, get_current_user
from .security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])


def _pg(request: Request):
    pg = getattr(request.app.state, "pg", None)
    if pg is None:
        raise HTTPException(503, "Database not available")
    return pg


# ── Request / Response models ─────────────────────────────────────────

class RegisterRequest(BaseModel):
    tenant_name: str
    email: EmailStr
    password: str
    full_name: str = ""

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 6:
            raise ValueError("Password must be at least 6 characters")
        return v

    @field_validator("tenant_name")
    @classmethod
    def tenant_name_valid(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Tenant name is required")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user_id: str
    tenant_id: str
    role: str


class UserInfo(BaseModel):
    user_id: str
    tenant_id: str
    tenant_name: str
    email: str
    full_name: str
    role: str


# ── Endpoints ─────────────────────────────────────────────────────────

@router.post("/register", response_model=TokenResponse)
async def register(body: RegisterRequest, request: Request):
    """Create a new tenant + admin user. Returns tokens."""
    pg = _pg(request)

    existing = await pg.fetchval(
        "SELECT user_id FROM users WHERE email = $1", body.email
    )
    if existing:
        raise HTTPException(409, "Email already registered")

    slug = re.sub(r"[^a-z0-9]+", "-", body.tenant_name.lower()).strip("-")
    slug_exists = await pg.fetchval(
        "SELECT tenant_id FROM tenants WHERE slug = $1", slug
    )
    if slug_exists:
        slug = f"{slug}-{str(existing or '')[:4] or 'x'}"

    tenant_id = await pg.fetchval(
        """
        INSERT INTO tenants (name, slug)
        VALUES ($1, $2)
        RETURNING tenant_id
        """,
        body.tenant_name.strip(),
        slug,
    )

    pw_hash = hash_password(body.password)
    user_id = await pg.fetchval(
        """
        INSERT INTO users (tenant_id, email, password_hash, full_name, role)
        VALUES ($1, $2, $3, $4, 'admin')
        RETURNING user_id
        """,
        tenant_id,
        body.email,
        pw_hash,
        body.full_name.strip(),
    )

    logger.info("Registered tenant=%s user=%s", tenant_id, user_id)

    return TokenResponse(
        access_token=create_access_token(user_id, tenant_id, "admin"),
        refresh_token=create_refresh_token(user_id, tenant_id),
        user_id=str(user_id),
        tenant_id=str(tenant_id),
        role="admin",
    )


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request):
    """Authenticate with email/password. Returns tokens."""
    pg = _pg(request)

    row = await pg.fetchrow(
        """
        SELECT u.user_id, u.tenant_id, u.password_hash, u.role, u.is_active
        FROM users u
        WHERE u.email = $1
        """,
        body.email,
    )
    if not row:
        raise HTTPException(401, "Invalid email or password")
    if not row["is_active"]:
        raise HTTPException(403, "Account deactivated")
    if not verify_password(body.password, row["password_hash"]):
        raise HTTPException(401, "Invalid email or password")

    user_id = row["user_id"]
    tenant_id = row["tenant_id"]
    role = row["role"]

    logger.info("Login user=%s tenant=%s", user_id, tenant_id)

    return TokenResponse(
        access_token=create_access_token(user_id, tenant_id, role),
        refresh_token=create_refresh_token(user_id, tenant_id),
        user_id=str(user_id),
        tenant_id=str(tenant_id),
        role=role,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest, request: Request):
    """Exchange a refresh token for new access + refresh tokens."""
    pg = _pg(request)

    import jwt as _jwt
    try:
        payload = decode_token(body.refresh_token)
    except _jwt.ExpiredSignatureError:
        raise HTTPException(401, "Refresh token expired")
    except _jwt.PyJWTError:
        raise HTTPException(401, "Invalid refresh token")

    if payload.get("type") != "refresh":
        raise HTTPException(401, "Invalid token type")

    user_id_str = payload["sub"]
    tenant_id_str = payload["tid"]

    row = await pg.fetchrow(
        "SELECT role, is_active FROM users WHERE user_id = $1", user_id_str
    )
    if not row or not row["is_active"]:
        raise HTTPException(401, "User not found or deactivated")

    from uuid import UUID
    uid = UUID(user_id_str)
    tid = UUID(tenant_id_str)
    role = row["role"]

    return TokenResponse(
        access_token=create_access_token(uid, tid, role),
        refresh_token=create_refresh_token(uid, tid),
        user_id=user_id_str,
        tenant_id=tenant_id_str,
        role=role,
    )


@router.get("/me", response_model=UserInfo)
async def me(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    """Return current user profile."""
    pg = _pg(request)

    row = await pg.fetchrow(
        """
        SELECT u.email, u.full_name, u.role,
               t.name AS tenant_name
        FROM users u
        JOIN tenants t ON t.tenant_id = u.tenant_id
        WHERE u.user_id = $1
        """,
        str(user.user_id),
    )
    if not row:
        raise HTTPException(404, "User not found")

    return UserInfo(
        user_id=str(user.user_id),
        tenant_id=str(user.tenant_id),
        tenant_name=row["tenant_name"],
        email=row["email"],
        full_name=row["full_name"],
        role=row["role"],
    )
