"""
Auth middleware — FastAPI dependency that extracts + validates JWT tokens.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .security import decode_token

_bearer = HTTPBearer(auto_error=False)


@dataclass
class CurrentUser:
    user_id: UUID
    tenant_id: UUID
    role: str


async def get_current_user(
    request: Request,
    cred: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> CurrentUser:
    """Extract user from JWT Bearer token. Raises 401 when missing/invalid."""
    if cred is None:
        raise HTTPException(401, "Authentication required")
    try:
        payload = decode_token(cred.credentials)
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expired")
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid token")

    if payload.get("type") != "access":
        raise HTTPException(401, "Invalid token type")

    return CurrentUser(
        user_id=UUID(payload["sub"]),
        tenant_id=UUID(payload["tid"]),
        role=payload.get("role", "member"),
    )


async def get_optional_user(
    request: Request,
    cred: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> Optional[CurrentUser]:
    """Same as get_current_user but returns None instead of 401."""
    if cred is None:
        return None
    try:
        payload = decode_token(cred.credentials)
    except jwt.PyJWTError:
        return None
    if payload.get("type") != "access":
        return None
    return CurrentUser(
        user_id=UUID(payload["sub"]),
        tenant_id=UUID(payload["tid"]),
        role=payload.get("role", "member"),
    )
