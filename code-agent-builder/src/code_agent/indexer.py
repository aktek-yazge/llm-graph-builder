"""Project indexing pipeline: parse -> graph write -> temporal write."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from code_agent.graph.kuzu_client import KuzuClient
from code_agent.graph.schema import Edge
from code_agent.parser.base import ExtractionResult
from code_agent.parser.tree_sitter_parser import discover_files, parse_file
from code_agent.temporal.diff_engine import compute_diff, has_changed
from code_agent.temporal.event_store import EventStore
from code_agent.temporal.git_tracker import GitTracker

logger = logging.getLogger(__name__)


class Indexer:
    """Orchestrates parsing and storage for code graph indexing."""

    def __init__(self, graph: KuzuClient, events: EventStore, project_root: str):
        self.graph = graph
        self.events = events
        self.project_root = project_root
        self.git = GitTracker(project_root)

    def index_project(self, languages: list[str] | None = None) -> dict[str, int]:
        files = discover_files(self.project_root, languages)
        logger.info("Indexing %d files from %s", len(files), self.project_root)

        stats = {"files": 0, "modules": 0, "classes": 0, "functions": 0, "edges": 0, "errors": 0}

        for file_path in files:
            try:
                result = parse_file(file_path, self.project_root)
                if result:
                    self._write_result(result)
                    stats["files"] += 1
                    stats["modules"] += 1 if result.module else 0
                    stats["classes"] += len(result.classes)
                    stats["functions"] += len(result.functions)
                    stats["edges"] += len(result.edges)
            except Exception as e:
                logger.error("Error indexing %s: %s", file_path, e)
                stats["errors"] += 1

        self.events.register_project(self.project_root, languages or ["python", "typescript"])

        git_commit = self.git.get_current_commit()
        graph_stats = self.graph.get_stats()
        self.events.create_snapshot(
            name=f"index_{git_commit or 'initial'}",
            git_commit=git_commit,
            stats=graph_stats,
        )

        logger.info("Indexing complete: %s", stats)
        return stats

    def index_file(self, file_path: str) -> dict[str, Any]:
        """Re-index a single file (for incremental updates)."""
        result = parse_file(file_path, self.project_root)
        if not result or not result.module:
            return {"status": "skipped", "reason": "unparseable or unsupported"}

        old_functions = self.graph.get_module_functions(file_path)
        old_hashes = {f["qn"]: f["hash"] for f in old_functions}

        self.graph.delete_module_entities(result.module.path)

        self._write_result(result)

        changes = self._track_changes(result, old_hashes)
        return {
            "status": "indexed",
            "module": result.module.name,
            "functions": len(result.functions),
            "classes": len(result.classes),
            "edges": len(result.edges),
            "changes": changes,
        }

    def import_git_history(self, max_commits: int = 20) -> dict[str, int]:
        """Import function version history from git log."""
        if not self.git.is_available:
            return {"status": "no_git", "imported": 0}

        files = discover_files(self.project_root)
        imported = 0

        for file_path in files:
            result = parse_file(file_path, self.project_root)
            if not result:
                continue

            for fn in result.functions:
                versions = self.git.import_function_history(
                    file_path, fn.name, max_commits=max_commits
                )
                seen_hashes: set[str] = set()
                for v in reversed(versions):
                    if v["body_hash"] not in seen_hashes:
                        seen_hashes.add(v["body_hash"])
                        self.events.record_function_version(
                            function_id=fn.qualified_name,
                            module_path=file_path,
                            function_name=fn.name,
                            body_hash=v["body_hash"],
                            body_text=v["body_text"],
                            signature=fn.signature,
                            git_commit=v["git_commit"],
                            git_author=v["git_author"],
                            git_timestamp=v["git_timestamp"],
                        )
                        imported += 1

        return {"status": "done", "imported": imported}

    # ── Internal ──────────────────────────────────────────────

    def _write_result(self, result: ExtractionResult) -> None:
        if result.module:
            self.graph.upsert_module(result.module)
        for cls in result.classes:
            self.graph.upsert_class(cls)
        for fn in result.functions:
            self.graph.upsert_function(fn)
        for param in result.parameters:
            self.graph.upsert_parameter(param)
        for var in result.variables:
            self.graph.upsert_variable(var)
        for dec in result.decorators:
            self.graph.upsert_decorator(dec)
        for edge in result.edges:
            self.graph.upsert_edge(edge)

    def _track_changes(self, result: ExtractionResult, old_hashes: dict[str, str]) -> list[dict]:
        changes: list[dict] = []
        current_qns = set()

        for fn in result.functions:
            current_qns.add(fn.qualified_name)
            old_hash = old_hashes.get(fn.qualified_name)
            body_text = result.function_bodies.get(fn.qualified_name, "")

            if old_hash is None:
                self.events.record_change(
                    entity_type="function",
                    entity_id=fn.qualified_name,
                    event_type="created",
                    after_hash=fn.body_hash,
                    git_commit=self.git.get_current_commit(),
                )
                self.events.record_function_version(
                    function_id=fn.qualified_name,
                    module_path=result.module.path if result.module else "",
                    function_name=fn.name,
                    body_hash=fn.body_hash,
                    body_text=body_text,
                    signature=fn.signature,
                    docstring=fn.docstring,
                    complexity=fn.complexity,
                    git_commit=self.git.get_current_commit(),
                )
                changes.append({"type": "created", "function": fn.qualified_name})

            elif has_changed(old_hash, fn.body_hash):
                old_body = ""
                history = self.events.get_function_history(fn.qualified_name, limit=1)
                if history:
                    old_body = history[0].get("body_text", "")
                diff = compute_diff(old_body, body_text, fn.name)

                self.events.record_change(
                    entity_type="function",
                    entity_id=fn.qualified_name,
                    event_type="modified",
                    before_hash=old_hash,
                    after_hash=fn.body_hash,
                    diff_text=diff,
                    git_commit=self.git.get_current_commit(),
                )
                self.events.record_function_version(
                    function_id=fn.qualified_name,
                    module_path=result.module.path if result.module else "",
                    function_name=fn.name,
                    body_hash=fn.body_hash,
                    body_text=body_text,
                    signature=fn.signature,
                    docstring=fn.docstring,
                    complexity=fn.complexity,
                    git_commit=self.git.get_current_commit(),
                )
                changes.append({"type": "modified", "function": fn.qualified_name})

        for old_qn in old_hashes:
            if old_qn not in current_qns:
                self.events.record_change(
                    entity_type="function",
                    entity_id=old_qn,
                    event_type="deleted",
                    before_hash=old_hashes[old_qn],
                    git_commit=self.git.get_current_commit(),
                )
                changes.append({"type": "deleted", "function": old_qn})

        return changes
