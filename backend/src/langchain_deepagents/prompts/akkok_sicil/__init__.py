"""
Akkok Sicil domain'i için prompt'lar.

Akkok Holding ticaret sicil gazeteleri ve şirket bilgileri için
özelleştirilmiş prompt'lar.

Sadece Cypher mode destekler.
"""

from .prompts import (
    SYSTEM_BASE,
    TOOL_USAGE,
    CONTENT,
    THINKING_GUIDE,
)

__all__ = [
    "SYSTEM_BASE",
    "TOOL_USAGE",
    "CONTENT",
    "THINKING_GUIDE",
]
