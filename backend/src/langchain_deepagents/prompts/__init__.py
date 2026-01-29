"""
Prompt modülleri - Domain bazlı ayrılmış prompt'lar.

Kullanım:
    from .prompts import get_domain_prompts

    # Sigorta domain'i için
    prompts = get_domain_prompts("sigorta")

    # Bakım domain'i için (WAT Motor)
    prompts = get_domain_prompts("bakim")

    # Akkok Sicil domain'i için
    prompts = get_domain_prompts("akkok-sicil")

NOT: Domain tanımlamaları src/config/domains.py dosyasında merkezi olarak yönetilir.
     Yeni domain eklemek için önce oraya ekleme yapın.
"""

from typing import Dict, Any, Optional
from importlib import import_module

# Merkezi domain registry'den domain bilgilerini al
from src.config.domains import (
    get_domain_config,
    get_valid_domains,
    is_valid_domain,
)


def _load_prompt_module(prompt_module: str):
    """
    Prompt modülünü dinamik olarak yükler.

    Args:
        prompt_module: Modül adı (örn: "sigorta", "bakim", "akkok_sicil")

    Returns:
        Yüklenen modül
    """
    module_path = f"src.langchain_deepagents.prompts.{prompt_module}"
    return import_module(module_path)


def get_domain_prompts(
    domain: str, mode: str = "cypher"
) -> Dict[str, str]:  # noqa: ARG001
    """
    Domain bazlı prompt'ları döndürür.

    Args:
        domain: Domain adı (merkezi registry'den kontrol edilir)
        mode: "cypher" (sadece cypher mode destekleniyor)

    Returns:
        Dict with keys: system_base, tool_usage, thinking_guide (optional), content

    Raises:
        ValueError: Geçersiz domain
    """
    # Domain validation (merkezi registry'den)
    if not is_valid_domain(domain):
        valid_domains = ", ".join(sorted(get_valid_domains()))
        raise ValueError(f"Unknown domain: {domain}. Supported: {valid_domains}")

    # Domain config'ini al
    config = get_domain_config(domain)
    prompt_module_name = config.prompt_module

    # Prompt modülünü yükle
    try:
        prompt_module = _load_prompt_module(prompt_module_name)
    except ImportError as e:
        raise ValueError(
            f"Prompt modülü bulunamadı: {prompt_module_name}. "
            f"src/langchain_deepagents/prompts/{prompt_module_name}/ klasörünü oluşturun. "
            f"Hata: {e}"
        ) from e

    # Prompt'ları al (her domain için standart isimler kullanılır)
    # Sigorta: SHARED_SYSTEM_BASE, CYPHER_TOOL_USAGE, SHARED_CONTENT
    # Bakim: WAT_SYSTEM_BASE, WAT_TOOL_USAGE, WAT_CONTENT
    # Yeni domain'ler: SYSTEM_BASE, TOOL_USAGE, CONTENT

    # Önce yeni standart isimleri dene
    if hasattr(prompt_module, "SYSTEM_BASE"):
        return {
            "system_base": getattr(prompt_module, "SYSTEM_BASE"),
            "tool_usage": getattr(prompt_module, "TOOL_USAGE"),
            "thinking_guide": getattr(prompt_module, "THINKING_GUIDE", ""),
            "content": getattr(prompt_module, "CONTENT"),
        }

    # Sigorta domain'i için eski isimler
    if hasattr(prompt_module, "SHARED_SYSTEM_BASE"):
        return {
            "system_base": getattr(prompt_module, "SHARED_SYSTEM_BASE"),
            "tool_usage": getattr(prompt_module, "CYPHER_TOOL_USAGE"),
            "thinking_guide": "",
            "content": getattr(prompt_module, "SHARED_CONTENT"),
        }

    # Bakim domain'i için eski isimler
    if hasattr(prompt_module, "WAT_SYSTEM_BASE"):
        return {
            "system_base": getattr(prompt_module, "WAT_SYSTEM_BASE"),
            "tool_usage": getattr(prompt_module, "WAT_TOOL_USAGE"),
            "thinking_guide": "",
            "content": getattr(prompt_module, "WAT_CONTENT"),
        }

    raise ValueError(
        f"Prompt modülü '{prompt_module_name}' gerekli export'ları içermiyor. "
        f"Beklenen: SYSTEM_BASE, TOOL_USAGE, CONTENT (veya legacy isimler)"
    )


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
    CYPHER_TOOL_USAGE,
    SHARED_CONTENT,
)

__all__ = [
    "get_domain_prompts",
    "build_full_prompt",
    # Backward compatibility
    "SHARED_SYSTEM_BASE",
    "CYPHER_TOOL_USAGE",
    "SHARED_CONTENT",
]
