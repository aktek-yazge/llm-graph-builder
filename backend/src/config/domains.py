"""
Merkezi Domain Registry

Tüm domain tanımlamaları tek bu dosyada yapılır.
Yeni domain eklemek için sadece DOMAIN_REGISTRY'ye ekleme yapın.

Kullanım:
    from src.config.domains import (
        DOMAIN_REGISTRY,
        get_domain_config,
        get_valid_domains,
        is_valid_domain,
    )

    # Tüm geçerli domain'leri al
    domains = get_valid_domains()  # {"sigorta", "bakim", "akkok-sicil"}

    # Belirli domain config'ini al
    config = get_domain_config("sigorta")

    # Domain geçerli mi kontrol et
    if is_valid_domain("akkok-sicil"):
        ...
"""

from typing import Dict, Any, Set, Optional
from dataclasses import dataclass, field
from typing import List


@dataclass
class DomainConfig:
    """Domain konfigürasyonu"""

    # Temel bilgiler
    name: str  # Domain adı (key)
    display_name: str  # Görüntüleme adı
    description: str  # Açıklama

    # Prompt ayarları
    prompt_module: str  # Prompt modülü: "sigorta", "bakim", "akkok_sicil"
    prompt_name_template: str  # Langfuse prompt adı: "react-agent-sigorta"

    # Neo4j index'leri
    vector_index: str  # Vector index adı
    fulltext_index: Optional[str]  # Fulltext index adı (opsiyonel)

    # Langfuse labels
    labels: List[str] = field(default_factory=list)

    # MCP ayarları (opsiyonel)
    mcp_port: int = 8002  # MCP server port
    mcp_container_name: str = ""  # Docker container adı

    def to_dict(self) -> Dict[str, Any]:
        """Dict formatına çevir (eski kod uyumluluğu için)"""
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "prompt_module": self.prompt_module,
            "prompt_name_template": self.prompt_name_template,
            "vector_index": self.vector_index,
            "fulltext_index": self.fulltext_index,
            "labels": self.labels,
            "mcp_port": self.mcp_port,
            "mcp_container_name": self.mcp_container_name,
            # Eski format uyumluluğu
            "modes": ["cypher"],
            "default_mode": "cypher",
        }


# =============================================================================
# DOMAIN REGISTRY - Yeni domain eklemek için buraya ekleme yapın
# =============================================================================

DOMAIN_REGISTRY: Dict[str, DomainConfig] = {
    # -------------------------------------------------------------------------
    # SIGORTA - Sigorta poliçeleri, müşteriler, teminatlar
    # -------------------------------------------------------------------------
    "sigorta": DomainConfig(
        name="sigorta",
        display_name="Sigorta",
        description="Sigorta poliçeleri, müşteriler, teminatlar",
        prompt_module="sigorta",
        prompt_name_template="react-agent-sigorta",
        vector_index="vector",
        fulltext_index="chunk_text_fulltext",
        labels=["production", "sigorta"],
        mcp_port=8002,
        mcp_container_name="mcp-neo4j-cypher",
    ),
    # -------------------------------------------------------------------------
    # BAKIM - WAT Motor bakım/arıza yönetimi (CMMS)
    # -------------------------------------------------------------------------
    "bakim": DomainConfig(
        name="bakim",
        display_name="Bakım (WAT Motor)",
        description="WAT Motor bakım/arıza yönetimi (CMMS)",
        prompt_module="bakim",
        prompt_name_template="react-agent-wat-motor",
        vector_index="task_embedding_index",
        fulltext_index=None,
        labels=["production", "bakim", "wat-motor"],
        mcp_port=8003,
        mcp_container_name="mcp-neo4j-cypher-bakim",
    ),
    # -------------------------------------------------------------------------
    # AKKOK-SİCİL - Akkok Holding ticaret sicil gazeteleri
    # -------------------------------------------------------------------------
    "akkok-sicil": DomainConfig(
        name="akkok-sicil",
        display_name="Akkok Sicil",
        description="Akkok Holding ticaret sicil gazeteleri ve şirket bilgileri",
        prompt_module="akkok_sicil",  # Python module adı (tire yerine alt çizgi)
        prompt_name_template="react-agent-akkok-sicil",
        vector_index="vector",  # veya özel index adı
        fulltext_index="chunk_text_fulltext",
        labels=["production", "akkok-sicil", "ticaret-sicil"],
        mcp_port=8004,
        mcp_container_name="mcp-neo4j-cypher-akkok-sicil",
    ),
}


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def get_valid_domains() -> Set[str]:
    """Tüm geçerli domain isimlerini döndürür"""
    return set(DOMAIN_REGISTRY.keys())


def is_valid_domain(domain: str) -> bool:
    """Domain geçerli mi kontrol eder"""
    return domain in DOMAIN_REGISTRY


def get_domain_config(domain: str) -> DomainConfig:
    """
    Domain konfigürasyonunu döndürür.

    Args:
        domain: Domain adı

    Returns:
        DomainConfig objesi

    Raises:
        ValueError: Geçersiz domain
    """
    if domain not in DOMAIN_REGISTRY:
        valid = ", ".join(sorted(DOMAIN_REGISTRY.keys()))
        raise ValueError(f"Geçersiz domain: '{domain}'. Geçerli domain'ler: {valid}")
    return DOMAIN_REGISTRY[domain]


def get_domain_prompts_module(domain: str) -> str:
    """
    Domain için prompt modül adını döndürür.

    Args:
        domain: Domain adı

    Returns:
        Prompt modül adı (örn: "sigorta", "bakim", "akkok_sicil")
    """
    config = get_domain_config(domain)
    return config.prompt_module


def get_domain_configs_dict() -> Dict[str, Dict[str, Any]]:
    """
    Tüm domain config'lerini dict formatında döndürür.
    (Eski kod uyumluluğu için)
    """
    return {name: config.to_dict() for name, config in DOMAIN_REGISTRY.items()}


# =============================================================================
# CLI: Domain listesini shell script'ler için export et
# =============================================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        command = sys.argv[1]

        if command == "list":
            # Shell script için domain listesi
            print(" ".join(sorted(DOMAIN_REGISTRY.keys())))

        elif command == "validate":
            # Domain validation
            if len(sys.argv) > 2:
                domain = sys.argv[2]
                if is_valid_domain(domain):
                    print(f"valid:{domain}")
                    sys.exit(0)
                else:
                    print(f"invalid:{domain}")
                    sys.exit(1)
            else:
                print("Usage: python domains.py validate <domain>")
                sys.exit(1)

        elif command == "info":
            # Domain bilgisi
            if len(sys.argv) > 2:
                domain = sys.argv[2]
                try:
                    config = get_domain_config(domain)
                    print(f"name={config.name}")
                    print(f"display_name={config.display_name}")
                    print(f"description={config.description}")
                    print(f"prompt_module={config.prompt_module}")
                    print(f"vector_index={config.vector_index}")
                    print(f"fulltext_index={config.fulltext_index or ''}")
                    print(f"mcp_port={config.mcp_port}")
                except ValueError as e:
                    print(f"error:{e}")
                    sys.exit(1)
            else:
                print("Usage: python domains.py info <domain>")
                sys.exit(1)

        else:
            print(f"Unknown command: {command}")
            print("Usage: python domains.py [list|validate|info] [domain]")
            sys.exit(1)
    else:
        # Default: domain listesi göster
        print("Available domains:")
        for name, config in sorted(DOMAIN_REGISTRY.items()):
            print(f"  {name:15} - {config.description}")
