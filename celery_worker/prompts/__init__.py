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


def load_skill(skill_name: str = "SKILL", domain: str = None) -> dict:
    """
    SKILL.md dosyasını yükler ve parse eder.
    
    Progressive disclosure: Skill sadece gerektiğinde yüklenir.
    
    SKILL.md formatı:
    ---
    name: skill-name
    description: Skill açıklaması
    ---
    
    # Skill içeriği...
    
    Args:
        skill_name: Skill dosya adı (varsayılan: "SKILL")
        domain: Domain adı (None ise get_domain() kullanılır)
    
    Returns:
        {
            "metadata": {"name": "...", "description": "..."},
            "content": "Skill içeriği"
        }
    """
    import yaml
    
    domain = domain or get_domain()
    
    # Domain-specific skill
    skill_path = PROMPTS_DIR / domain / f"{skill_name}.md"
    if not skill_path.exists():
        # Fallback to sigorta
        skill_path = PROMPTS_DIR / "sigorta" / f"{skill_name}.md"
    
    if not skill_path.exists():
        raise FileNotFoundError(f"Skill not found: {skill_name} (domain: {domain})")
    
    content = skill_path.read_text(encoding="utf-8")
    
    # Parse YAML frontmatter
    metadata = {}
    body = content
    
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            try:
                metadata = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                pass
            body = parts[2].strip()
    
    return {
        "metadata": metadata,
        "content": body,
        "path": str(skill_path),
    }
