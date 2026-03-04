"""tree-sitter based code parser that delegates to language-specific extractors."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import tree_sitter_python as tspython
import tree_sitter_typescript as tstypescript
from tree_sitter import Language, Parser

from .base import BaseExtractor, ExtractionResult
from .python_extractor import PythonExtractor
from .typescript_extractor import TypeScriptExtractor

logger = logging.getLogger(__name__)

PY_LANGUAGE = Language(tspython.language())
TS_LANGUAGE = Language(tstypescript.language_typescript())
TSX_LANGUAGE = Language(tstypescript.language_tsx())

EXTRACTORS: dict[str, tuple[Language, BaseExtractor]] = {
    ".py": (PY_LANGUAGE, PythonExtractor()),
    ".ts": (TS_LANGUAGE, TypeScriptExtractor()),
    ".tsx": (TSX_LANGUAGE, TypeScriptExtractor()),
}

SKIP_DIRS = {
    "node_modules", "__pycache__", ".git", ".venv", "venv", "env",
    ".mypy_cache", ".pytest_cache", "dist", "build", ".next", ".nuxt",
    "coverage", ".tox", "egg-info",
}


def file_hash(content: bytes) -> str:
    return hashlib.md5(content).hexdigest()


def path_to_module_qn(file_path: str, project_root: str) -> str:
    """Convert a file path to a dotted module qualified name."""
    rel = Path(file_path).relative_to(project_root)
    parts = list(rel.parts)
    if parts[-1] in ("__init__.py", "index.ts", "index.tsx"):
        parts = parts[:-1]
    else:
        parts[-1] = Path(parts[-1]).stem
    return ".".join(parts)


def parse_file(file_path: str, project_root: str) -> ExtractionResult | None:
    """Parse a single file and extract code graph entities."""
    p = Path(file_path)
    ext = p.suffix
    if ext not in EXTRACTORS:
        return None

    lang, extractor = EXTRACTORS[ext]
    try:
        source = p.read_bytes()
    except (OSError, IOError) as e:
        logger.warning("Cannot read %s: %s", file_path, e)
        return None

    parser = Parser(lang)
    tree = parser.parse(source)

    module_qn = path_to_module_qn(file_path, project_root)
    result = extractor.extract(source, file_path, module_qn)

    from code_agent.graph.schema import ModuleNode
    language = "python" if ext == ".py" else "typescript"
    line_count = source.count(b"\n") + 1
    result.module = ModuleNode(
        path=file_path,
        name=module_qn,
        language=language,
        file_hash=file_hash(source),
        line_count=line_count,
    )

    return result


def discover_files(project_root: str, languages: list[str] | None = None) -> list[str]:
    """Walk project tree and return parseable file paths."""
    root = Path(project_root)
    allowed_exts = set()
    if not languages or "python" in languages:
        allowed_exts.add(".py")
    if not languages or "typescript" in languages:
        allowed_exts.update({".ts", ".tsx"})

    files: list[str] = []
    for p in root.rglob("*"):
        if any(skip in p.parts for skip in SKIP_DIRS):
            continue
        if p.is_file() and p.suffix in allowed_exts:
            files.append(str(p))
    return sorted(files)
