"""
Sigorta domain'i için prompt'lar.

Sigorta poliçeleri, müşteriler, teminatlar vb. için özelleştirilmiş prompt'lar.
Cypher mode destekler.
"""

from .base import SHARED_SYSTEM_BASE
from .tools import CYPHER_TOOL_USAGE
from .content import SHARED_CONTENT

__all__ = [
    "SHARED_SYSTEM_BASE",
    "CYPHER_TOOL_USAGE",
    "SHARED_CONTENT",
]
