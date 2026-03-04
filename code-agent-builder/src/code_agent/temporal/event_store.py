"""SQLite append-only event store for temporal code tracking."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS function_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    function_id TEXT NOT NULL,
    module_path TEXT NOT NULL,
    function_name TEXT NOT NULL,
    signature TEXT,
    body_hash TEXT NOT NULL,
    body_text TEXT NOT NULL,
    docstring TEXT,
    complexity INTEGER DEFAULT 0,
    version INTEGER NOT NULL,
    git_commit TEXT,
    git_author TEXT,
    git_timestamp TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_fv_function_id ON function_versions(function_id);
CREATE INDEX IF NOT EXISTS idx_fv_module_path ON function_versions(module_path);
CREATE INDEX IF NOT EXISTS idx_fv_body_hash ON function_versions(body_hash);

CREATE TABLE IF NOT EXISTS change_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    before_hash TEXT,
    after_hash TEXT,
    diff_text TEXT,
    git_commit TEXT,
    git_message TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_ce_entity_id ON change_events(entity_id);
CREATE INDEX IF NOT EXISTS idx_ce_entity_type ON change_events(entity_type);
CREATE INDEX IF NOT EXISTS idx_ce_event_type ON change_events(event_type);
CREATE INDEX IF NOT EXISTS idx_ce_created_at ON change_events(created_at);

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    git_commit TEXT,
    node_count INTEGER,
    edge_count INTEGER,
    module_count INTEGER,
    function_count INTEGER,
    class_count INTEGER,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS watched_projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_path TEXT UNIQUE NOT NULL,
    languages TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
"""


class EventStore:
    """Append-only SQLite store for code change history."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA_SQL)
        logger.info("Event store initialized at %s", self.db_path)

    # ── Function Versions ─────────────────────────────────────

    def record_function_version(
        self,
        function_id: str,
        module_path: str,
        function_name: str,
        body_hash: str,
        body_text: str,
        signature: str = "",
        docstring: str = "",
        complexity: int = 0,
        git_commit: str = "",
        git_author: str = "",
        git_timestamp: str = "",
    ) -> int:
        current_version = self._get_latest_version(function_id)
        new_version = current_version + 1

        cursor = self._conn.execute(
            """INSERT INTO function_versions
            (function_id, module_path, function_name, signature, body_hash, body_text,
             docstring, complexity, version, git_commit, git_author, git_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (function_id, module_path, function_name, signature, body_hash,
             body_text, docstring, complexity, new_version, git_commit,
             git_author, git_timestamp),
        )
        self._conn.commit()
        return new_version

    def get_function_history(self, function_id: str, limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            """SELECT * FROM function_versions
            WHERE function_id = ? ORDER BY version DESC LIMIT ?""",
            (function_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_latest_function_hash(self, function_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT body_hash FROM function_versions WHERE function_id = ? ORDER BY version DESC LIMIT 1",
            (function_id,),
        ).fetchone()
        return row["body_hash"] if row else None

    # ── Change Events ─────────────────────────────────────────

    def record_change(
        self,
        entity_type: str,
        entity_id: str,
        event_type: str,
        before_hash: str = "",
        after_hash: str = "",
        diff_text: str = "",
        git_commit: str = "",
        git_message: str = "",
    ) -> int:
        cursor = self._conn.execute(
            """INSERT INTO change_events
            (entity_type, entity_id, event_type, before_hash, after_hash,
             diff_text, git_commit, git_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (entity_type, entity_id, event_type, before_hash, after_hash,
             diff_text, git_commit, git_message),
        )
        self._conn.commit()
        return cursor.lastrowid

    def get_recent_changes(
        self,
        since: str = "",
        entity_type: str = "",
        limit: int = 50,
    ) -> list[dict]:
        query = "SELECT * FROM change_events WHERE 1=1"
        params: list[Any] = []
        if since:
            query += " AND created_at >= ?"
            params.append(since)
        if entity_type:
            query += " AND entity_type = ?"
            params.append(entity_type)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_entity_history(self, entity_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM change_events WHERE entity_id = ? ORDER BY created_at DESC",
            (entity_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Snapshots ─────────────────────────────────────────────

    def create_snapshot(
        self,
        name: str,
        git_commit: str = "",
        stats: dict[str, int] | None = None,
    ) -> int:
        s = stats or {}
        cursor = self._conn.execute(
            """INSERT INTO snapshots
            (name, git_commit, node_count, edge_count, module_count, function_count, class_count)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (name, git_commit, s.get("total_nodes", 0), s.get("total_edges", 0),
             s.get("Module", 0), s.get("Function", 0), s.get("Class", 0)),
        )
        self._conn.commit()
        return cursor.lastrowid

    def list_snapshots(self, limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM snapshots ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Raw Query ─────────────────────────────────────────────

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        """Execute a read-only SQL query."""
        sql_upper = sql.strip().upper()
        if not sql_upper.startswith("SELECT"):
            raise ValueError("Only SELECT queries are allowed")
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # ── Watched Projects ──────────────────────────────────────

    def register_project(self, project_path: str, languages: list[str]) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO watched_projects (project_path, languages) VALUES (?, ?)",
            (project_path, ",".join(languages)),
        )
        self._conn.commit()

    def get_watched_projects(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM watched_projects").fetchall()
        return [dict(r) for r in rows]

    # ── Helpers ───────────────────────────────────────────────

    def _get_latest_version(self, function_id: str) -> int:
        row = self._conn.execute(
            "SELECT MAX(version) AS v FROM function_versions WHERE function_id = ?",
            (function_id,),
        ).fetchone()
        return row["v"] if row and row["v"] else 0

    def close(self) -> None:
        self._conn.close()
