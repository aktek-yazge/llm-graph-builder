"""
Ticaret Sicili Gazetesi domain'i için prompt'lar.

Türkiye Ticaret Sicili Gazetesi belgelerinden GLiNER ile oluşturulmuş bilgi grafiği
(Company → Document → Chunk → Entity) için özelleştirilmiş prompt'lar.

Sadece Cypher mode destekler (DSL ve embedding YOK).
"""

from .ticaret_sicili import (
    TICARET_SYSTEM_BASE,
    TICARET_TOOL_USAGE,
    TICARET_CONTENT,
)

__all__ = [
    "TICARET_SYSTEM_BASE",
    "TICARET_TOOL_USAGE",
    "TICARET_CONTENT",
]
