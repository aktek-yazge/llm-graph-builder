# Auth module
from .jwt_handler import (
    create_access_token,
    verify_token,
    get_current_user,
    get_current_user_optional,
    JWTBearer,
    TokenData,
)
from .models import User, UserCreate, UserLogin, TokenResponse
from .user_service import (
    create_user,
    authenticate_user,
    get_user_by_id,
    get_user_by_email,
)
from .router import router as auth_router

__all__ = [
    # JWT functions
    "create_access_token",
    "verify_token", 
    "get_current_user",
    "get_current_user_optional",
    "JWTBearer",
    "TokenData",
    # Models
    "User",
    "UserCreate",
    "UserLogin",
    "TokenResponse",
    # User service
    "create_user",
    "authenticate_user",
    "get_user_by_id",
    "get_user_by_email",
    # Router
    "auth_router",
]

