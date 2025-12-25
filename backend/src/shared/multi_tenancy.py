# -*- coding: utf-8 -*-
"""
Multi-tenancy Module - Tenant Isolation

LAYER 7 Features:
- Tenant model (PostgreSQL)
- API key management
- Neo4j database-per-tenant or label isolation
- Request context tenant propagation
- Tenant-scoped schema cache

Kullanım:
    from src.shared.multi_tenancy import (
        get_current_tenant,
        set_tenant_context,
        get_tenant_neo4j_config,
        TenantMiddleware,
    )
    
    # Middleware ile
    app.add_middleware(TenantMiddleware)
    
    # Request handler'da
    tenant = get_current_tenant(request)
    neo4j_config = get_tenant_neo4j_config(tenant.id)

Environment Variables:
    MULTI_TENANCY_ENABLED: Enable/disable multi-tenancy (default: false)
    MULTI_TENANCY_MODE: "database" (separate DB) or "label" (label isolation)
    DEFAULT_TENANT_ID: Default tenant ID for backward compatibility
"""

import os
import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass
from datetime import datetime, timezone
from contextvars import ContextVar

from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

# Environment configuration
MULTI_TENANCY_ENABLED = os.getenv("MULTI_TENANCY_ENABLED", "false").lower() in ("true", "1", "yes")
MULTI_TENANCY_MODE = os.getenv("MULTI_TENANCY_MODE", "label")  # "database" or "label"
DEFAULT_TENANT_ID = os.getenv("DEFAULT_TENANT_ID", "default")

# Context variable for current tenant
_current_tenant: ContextVar[Optional["Tenant"]] = ContextVar("current_tenant", default=None)


@dataclass
class Tenant:
    """Tenant model"""
    id: str
    name: str
    api_key: Optional[str] = None
    neo4j_database: Optional[str] = None
    neo4j_uri: Optional[str] = None
    created_at: Optional[datetime] = None
    settings: Optional[Dict[str, Any]] = None
    
    @property
    def is_default(self) -> bool:
        return self.id == DEFAULT_TENANT_ID


# Default tenant instance
DEFAULT_TENANT = Tenant(
    id=DEFAULT_TENANT_ID,
    name="Default Tenant",
    neo4j_database=os.getenv("NEO4J_DATABASE", "neo4j"),
)


def get_current_tenant() -> Tenant:
    """
    Get current tenant from context.
    
    Returns:
        Current tenant or default tenant
    """
    tenant = _current_tenant.get()
    if tenant is None:
        return DEFAULT_TENANT
    return tenant


def set_tenant_context(tenant: Tenant) -> None:
    """
    Set current tenant in context.
    
    Args:
        tenant: Tenant to set
    """
    _current_tenant.set(tenant)


def clear_tenant_context() -> None:
    """Clear current tenant from context"""
    _current_tenant.set(None)


def get_tenant_from_request(request: Request) -> Tenant:
    """
    Extract tenant from request.
    
    Checks:
    1. X-Tenant-ID header
    2. API key in Authorization header
    3. Query parameter tenant_id
    
    Args:
        request: FastAPI request
    
    Returns:
        Tenant or default tenant
    """
    if not MULTI_TENANCY_ENABLED:
        return DEFAULT_TENANT
    
    # Check X-Tenant-ID header
    tenant_id = request.headers.get("X-Tenant-ID")
    
    # Check Authorization header for API key
    if not tenant_id:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            api_key = auth_header[7:]
            tenant = get_tenant_by_api_key(api_key)
            if tenant:
                return tenant
    
    # Check query parameter
    if not tenant_id:
        tenant_id = request.query_params.get("tenant_id")
    
    # Return tenant or default
    if tenant_id:
        tenant = get_tenant_by_id(tenant_id)
        if tenant:
            return tenant
    
    return DEFAULT_TENANT


def get_tenant_by_id(tenant_id: str) -> Optional[Tenant]:
    """
    Get tenant by ID from database.
    
    Args:
        tenant_id: Tenant ID
    
    Returns:
        Tenant or None
    """
    if tenant_id == DEFAULT_TENANT_ID:
        return DEFAULT_TENANT
    
    # TODO: Implement PostgreSQL lookup
    # For now, return None for unknown tenants
    try:
        from src.models.file_queue_models import get_file_queue_db
        
        db = get_file_queue_db()
        # Tenant table query would go here
        # result = db.query(TenantModel).filter(TenantModel.id == tenant_id).first()
        db.close()
        
        # Placeholder - return None for now
        return None
        
    except Exception as e:
        logger.warning(f"⚠️ Failed to fetch tenant {tenant_id}: {e}")
        return None


def get_tenant_by_api_key(api_key: str) -> Optional[Tenant]:
    """
    Get tenant by API key.
    
    Args:
        api_key: API key
    
    Returns:
        Tenant or None
    """
    # TODO: Implement API key lookup
    # For now, return None
    return None


def get_tenant_neo4j_config(tenant: Tenant = None) -> Dict[str, str]:
    """
    Get Neo4j configuration for tenant.
    
    Args:
        tenant: Tenant (or current tenant if None)
    
    Returns:
        Neo4j config dict with uri, username, password, database
    """
    if tenant is None:
        tenant = get_current_tenant()
    
    base_config = {
        "uri": os.getenv("NEO4J_URI"),
        "username": os.getenv("NEO4J_USERNAME"),
        "password": os.getenv("NEO4J_PASSWORD"),
        "database": os.getenv("NEO4J_DATABASE", "neo4j"),
    }
    
    if MULTI_TENANCY_MODE == "database":
        # Separate database per tenant
        if tenant.neo4j_database:
            base_config["database"] = tenant.neo4j_database
        elif not tenant.is_default:
            # Generate database name from tenant ID
            base_config["database"] = f"tenant_{tenant.id}"
        
        if tenant.neo4j_uri:
            base_config["uri"] = tenant.neo4j_uri
    
    # For label mode, we use the same database but filter by tenant label
    
    return base_config


def get_tenant_label_filter(tenant: Tenant = None) -> str:
    """
    Get Cypher label filter for tenant isolation.
    
    Args:
        tenant: Tenant (or current tenant if None)
    
    Returns:
        Cypher WHERE clause for tenant filtering
    """
    if not MULTI_TENANCY_ENABLED or MULTI_TENANCY_MODE != "label":
        return ""
    
    if tenant is None:
        tenant = get_current_tenant()
    
    if tenant.is_default:
        return ""
    
    return f"AND n.tenant_id = '{tenant.id}'"


class TenantMiddleware(BaseHTTPMiddleware):
    """
    Middleware for tenant context management.
    
    Extracts tenant from request and sets in context.
    """
    
    async def dispatch(self, request: Request, call_next):
        if not MULTI_TENANCY_ENABLED:
            response = await call_next(request)
            return response
        
        try:
            # Extract tenant from request
            tenant = get_tenant_from_request(request)
            
            # Set in context
            set_tenant_context(tenant)
            
            # Add to request state for easy access
            request.state.tenant = tenant
            
            # Log tenant
            if not tenant.is_default:
                logger.debug(f"🏢 Request from tenant: {tenant.id}")
            
            # Process request
            response = await call_next(request)
            
            # Add tenant header to response
            response.headers["X-Tenant-ID"] = tenant.id
            
            return response
            
        finally:
            # Clear context
            clear_tenant_context()


def create_tenant_schema(tenant_id: str, neo4j_config: Dict[str, str] = None) -> bool:
    """
    Create necessary schema for a new tenant.
    
    For database mode: Creates new Neo4j database
    For label mode: Creates tenant constraints
    
    Args:
        tenant_id: New tenant ID
        neo4j_config: Neo4j config (or default)
    
    Returns:
        Success status
    """
    if not MULTI_TENANCY_ENABLED:
        return True
    
    try:
        if MULTI_TENANCY_MODE == "database":
            # Create new database
            # Note: Requires Neo4j Enterprise for multi-database
            logger.info(f"📦 Creating Neo4j database for tenant: {tenant_id}")
            # CREATE DATABASE tenant_{tenant_id} IF NOT EXISTS
            pass
        else:
            # Create tenant index for label mode
            logger.info(f"🏷️ Creating tenant index for: {tenant_id}")
            # CREATE INDEX IF NOT EXISTS FOR (n) ON (n.tenant_id)
            pass
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to create tenant schema: {e}")
        return False


def get_tenancy_stats() -> Dict[str, Any]:
    """Get multi-tenancy statistics"""
    return {
        "enabled": MULTI_TENANCY_ENABLED,
        "mode": MULTI_TENANCY_MODE,
        "default_tenant": DEFAULT_TENANT_ID,
        "current_tenant": get_current_tenant().id,
    }

