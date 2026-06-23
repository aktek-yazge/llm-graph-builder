"""
Prompt modülleri - Domain bazlı ayrılmış prompt'lar.

Kullanım:
    from .prompts import get_domain_prompts
    
    # Sigorta domain'i için
    prompts = get_domain_prompts("sigorta")
    
    # Bakım domain'i için (WAT Motor)
    prompts = get_domain_prompts("bakim")
"""

from typing import Dict, Any, Optional


def get_domain_prompts(domain: str, mode: str = "cypher") -> Dict[str, str]:
    """
    Domain bazlı prompt'ları döndürür.
    
    Args:
        domain: "sigorta" veya "bakim"
        mode: "cypher" veya "dsl" (sadece sigorta için geçerli)
        
    Returns:
        Dict with keys: system_base, tool_usage, thinking_guide (optional), content
    """
    if domain == "sigorta":
        from .sigorta import (
            SHARED_SYSTEM_BASE,
            DSL_TOOL_USAGE,
            CYPHER_TOOL_USAGE,
            DSL_THINKING_GUIDE,
            SHARED_CONTENT,
        )
        
        if mode == "dsl":
            return {
                "system_base": SHARED_SYSTEM_BASE,
                "tool_usage": DSL_TOOL_USAGE,
                "thinking_guide": DSL_THINKING_GUIDE,
                "content": SHARED_CONTENT,
            }
        else:  # cypher mode
            return {
                "system_base": SHARED_SYSTEM_BASE,
                "tool_usage": CYPHER_TOOL_USAGE,
                "thinking_guide": "",  # Cypher mode'da thinking guide yok
                "content": SHARED_CONTENT,
            }
    
    elif domain == "bakim":
        from .bakim import (
            WAT_SYSTEM_BASE,
            WAT_TOOL_USAGE,
            WAT_CONTENT,
        )

        return {
            "system_base": WAT_SYSTEM_BASE,
            "tool_usage": WAT_TOOL_USAGE,
            "thinking_guide": "",  # Bakım domain'inde thinking guide yok
            "content": WAT_CONTENT,
        }

    elif domain == "ticaret":
        from .ticaret import (
            TICARET_SYSTEM_BASE,
            TICARET_TOOL_USAGE,
            TICARET_CONTENT,
        )

        return {
            "system_base": TICARET_SYSTEM_BASE,
            "tool_usage": TICARET_TOOL_USAGE,
            "thinking_guide": "",  # Ticaret domain'inde thinking guide yok
            "content": TICARET_CONTENT,
        }

    else:
        raise ValueError(f"Unknown domain: {domain}. Supported: sigorta, bakim, ticaret")


def build_full_prompt(domain: str, mode: str = "cypher") -> str:
    """
    Tam prompt'u oluşturur (schema placeholder ile).
    
    Args:
        domain: "sigorta" veya "bakim"
        mode: "cypher" veya "dsl"
        
    Returns:
        Full prompt string with {{schema_info}} placeholder
    """
    prompts = get_domain_prompts(domain, mode)
    
    parts = [
        prompts["system_base"],
        prompts["tool_usage"],
    ]
    
    if prompts.get("thinking_guide"):
        parts.append(prompts["thinking_guide"])
    
    parts.append(prompts["content"])
    
    return "".join(parts)


# Backward compatibility - mevcut import'lar için
from .sigorta import (
    SHARED_SYSTEM_BASE,
    DSL_TOOL_USAGE,
    CYPHER_TOOL_USAGE,
    DSL_THINKING_GUIDE,
    SHARED_CONTENT,
)

__all__ = [
    "get_domain_prompts",
    "build_full_prompt",
    # Backward compatibility
    "SHARED_SYSTEM_BASE",
    "DSL_TOOL_USAGE",
    "CYPHER_TOOL_USAGE",
    "DSL_THINKING_GUIDE",
    "SHARED_CONTENT",
]
