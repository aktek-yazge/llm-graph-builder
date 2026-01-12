"""
Sigorta domain'i için prompt'lar.

Sigorta poliçeleri, müşteriler, teminatlar vb. için özelleştirilmiş prompt'lar.
DSL ve Cypher mode destekler.
"""

from .base import SHARED_SYSTEM_BASE
from .tools import DSL_TOOL_USAGE, CYPHER_TOOL_USAGE
from .thinking import DSL_THINKING_GUIDE
from .content import SHARED_CONTENT

__all__ = [
    "SHARED_SYSTEM_BASE",
    "DSL_TOOL_USAGE",
    "CYPHER_TOOL_USAGE",
    "DSL_THINKING_GUIDE",
    "SHARED_CONTENT",
]
