"""Unified diff computation for code change tracking."""

from __future__ import annotations

import difflib


def compute_diff(old_text: str, new_text: str, filename: str = "") -> str:
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{filename}" if filename else "a",
        tofile=f"b/{filename}" if filename else "b",
    )
    return "".join(diff)


def has_changed(old_hash: str | None, new_hash: str) -> bool:
    return old_hash is None or old_hash != new_hash
