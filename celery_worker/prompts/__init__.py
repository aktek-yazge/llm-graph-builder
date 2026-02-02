# Celery Worker Prompts
# Tenant bazlı prompt yönetimi için

import os
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent


def load_prompt(prompt_name: str, domain: str = "sigorta") -> str:
    """
    Prompt dosyasını yükler.

    Öncelik sırası:
    1. prompts/{domain}/{prompt_name}.md
    2. prompts/sigorta/{prompt_name}.md (fallback)

    Args:
        prompt_name: Prompt dosya adı (uzantısız)
        domain: Domain adı (örn: "akkok-sicil", "sigorta")

    Returns:
        Prompt içeriği
    """
    # Domain-specific prompt
    domain_path = PROMPTS_DIR / domain / f"{prompt_name}.md"
    if domain_path.exists():
        return domain_path.read_text(encoding="utf-8")

    # Fallback to sigorta (default domain)
    fallback_path = PROMPTS_DIR / "sigorta" / f"{prompt_name}.md"
    if fallback_path.exists():
        return fallback_path.read_text(encoding="utf-8")

    raise FileNotFoundError(f"Prompt not found: {prompt_name} (domain: {domain})")


def get_domain() -> str:
    """
    Aktif domain'i environment'tan alır.

    Returns:
        Domain adı (örn: "sigorta", "akkok-sicil")
    """
    return os.environ.get("REACT_DOMAIN", "sigorta")
