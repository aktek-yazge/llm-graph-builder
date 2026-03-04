"""Git history integration for temporal code tracking."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from git import Repo, InvalidGitRepositoryError

logger = logging.getLogger(__name__)


class GitTracker:
    """Extract function history from git log for temporal analysis."""

    def __init__(self, repo_path: str | Path):
        self.repo_path = Path(repo_path)
        try:
            self._repo = Repo(str(self.repo_path), search_parent_directories=True)
        except InvalidGitRepositoryError:
            logger.warning("Not a git repository: %s", repo_path)
            self._repo = None

    @property
    def is_available(self) -> bool:
        return self._repo is not None

    def get_current_commit(self) -> str:
        if not self._repo:
            return ""
        try:
            return self._repo.head.commit.hexsha[:12]
        except Exception:
            return ""

    def get_file_commits(self, file_path: str, max_count: int = 50) -> list[dict]:
        """Get commit history for a specific file."""
        if not self._repo:
            return []

        try:
            rel_path = str(Path(file_path).relative_to(self._repo.working_dir))
        except ValueError:
            rel_path = file_path

        commits = []
        try:
            for commit in self._repo.iter_commits(paths=rel_path, max_count=max_count):
                commits.append({
                    "hash": commit.hexsha[:12],
                    "author": str(commit.author),
                    "date": commit.committed_datetime.isoformat(),
                    "message": commit.message.strip().split("\n")[0],
                })
        except Exception as e:
            logger.warning("Error reading git log for %s: %s", file_path, e)

        return commits

    def get_file_at_commit(self, file_path: str, commit_hash: str) -> str | None:
        """Retrieve file content at a specific commit."""
        if not self._repo:
            return None
        try:
            rel_path = str(Path(file_path).relative_to(self._repo.working_dir))
        except ValueError:
            rel_path = file_path

        try:
            commit = self._repo.commit(commit_hash)
            blob = commit.tree / rel_path
            return blob.data_stream.read().decode("utf-8", errors="replace")
        except Exception:
            return None

    def get_changed_files_since(self, commit_hash: str) -> list[str]:
        """Get list of files changed since a given commit."""
        if not self._repo:
            return []
        try:
            diff = self._repo.commit(commit_hash).diff("HEAD")
            return [d.b_path for d in diff if d.b_path]
        except Exception:
            return []

    def get_recent_commits(self, max_count: int = 20) -> list[dict]:
        if not self._repo:
            return []
        commits = []
        try:
            for commit in self._repo.iter_commits(max_count=max_count):
                commits.append({
                    "hash": commit.hexsha[:12],
                    "author": str(commit.author),
                    "date": commit.committed_datetime.isoformat(),
                    "message": commit.message.strip().split("\n")[0],
                    "files_changed": len(commit.stats.files),
                })
        except Exception as e:
            logger.warning("Error reading git log: %s", e)
        return commits

    def import_function_history(
        self,
        file_path: str,
        function_name: str,
        max_commits: int = 20,
    ) -> list[dict[str, Any]]:
        """Extract historical versions of a function from git commits."""
        if not self._repo:
            return []

        versions: list[dict[str, Any]] = []
        commits = self.get_file_commits(file_path, max_count=max_commits)

        for commit_info in commits:
            content = self.get_file_at_commit(file_path, commit_info["hash"])
            if not content:
                continue

            fn_body = self._extract_function_body(content, function_name)
            if fn_body:
                versions.append({
                    "body_text": fn_body,
                    "body_hash": hashlib.md5(fn_body.encode()).hexdigest(),
                    "git_commit": commit_info["hash"],
                    "git_author": commit_info["author"],
                    "git_timestamp": commit_info["date"],
                })

        return versions

    @staticmethod
    def _extract_function_body(source: str, function_name: str) -> str | None:
        """Simple regex-free function body extraction for Python."""
        lines = source.split("\n")
        in_function = False
        indent_level = -1
        body_lines: list[str] = []

        for line in lines:
            stripped = line.lstrip()
            current_indent = len(line) - len(stripped)

            if stripped.startswith(f"def {function_name}(") or \
               stripped.startswith(f"async def {function_name}("):
                in_function = True
                indent_level = current_indent
                body_lines = [line]
                continue

            if in_function:
                if stripped == "" or current_indent > indent_level:
                    body_lines.append(line)
                else:
                    break

        return "\n".join(body_lines) if body_lines else None
