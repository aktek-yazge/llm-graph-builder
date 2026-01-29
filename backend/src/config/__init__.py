"""
Merkezi konfigürasyon modülü.

Tüm domain, prompt ve sistem ayarları burada tanımlanır.
"""

from .domains import (
    DOMAIN_REGISTRY,
    get_domain_config,
    get_valid_domains,
    get_domain_prompts_module,
    is_valid_domain,
)

__all__ = [
    "DOMAIN_REGISTRY",
    "get_domain_config",
    "get_valid_domains",
    "get_domain_prompts_module",
    "is_valid_domain",
]
