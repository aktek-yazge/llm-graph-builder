"""
Bakım domain'i için prompt'lar.

WAT Motor, CMMS (Computerized Maintenance Management System) ve 
endüstriyel bakım süreçleri için özelleştirilmiş prompt'lar.

Sadece Cypher mode destekler (DSL yok).
"""

from .wat_motor import (
    WAT_SYSTEM_BASE,
    WAT_TOOL_USAGE,
    WAT_CONTENT,
)

__all__ = [
    "WAT_SYSTEM_BASE",
    "WAT_TOOL_USAGE",
    "WAT_CONTENT",
]
