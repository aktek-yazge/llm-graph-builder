"""Kuzu embedded graph database client for code graph operations."""

from __future__ import annotations

import logging
from pathlib import Path

import kuzu

from .schema import (
    NODE_TABLES,
    REL_TABLES,
    ClassNode,
    DecoratorNode,
    Edge,
    FunctionNode,
    ModuleNode,
    ParameterNode,
    VariableNode,
)

logger = logging.getLogger(__name__)


class KuzuClient:
    """Manages Kuzu database connection, schema, and CRUD operations."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = kuzu.Database(str(self.db_path))
        self._conn = kuzu.Connection(self._db)

    def init_schema(self) -> None:
        for stmt in NODE_TABLES:
            self._conn.execute(stmt)
        for stmt in REL_TABLES:
            self._conn.execute(stmt)
        logger.info("Kuzu schema initialized at %s", self.db_path)

    # ── Queries ────────────────────────────────────────────────

    def execute(self, cypher: str, params: dict | None = None) -> list[dict]:
        result = self._conn.execute(cypher, parameters=params or {})
        rows: list[dict] = []
        while result.has_next():
            row = result.get_next()
            columns = result.get_column_names()
            rows.append(dict(zip(columns, row)))
        return rows

    def query(self, cypher: str, params: dict | None = None) -> list[dict]:
        return self.execute(cypher, params)

    # ── Node Upserts ──────────────────────────────────────────

    def upsert_module(self, node: ModuleNode) -> None:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "MERGE (m:Module {path: $path}) "
            "SET m.name = $name, m.language = $lang, m.file_hash = $hash, "
            "m.line_count = $lines, m.last_analyzed_at = $ts, m.summary = $summary",
            parameters={
                "path": node.path,
                "name": node.name,
                "lang": node.language,
                "hash": node.file_hash,
                "lines": node.line_count,
                "ts": now,
                "summary": node.summary,
            },
        )

    def upsert_class(self, node: ClassNode) -> None:
        self._conn.execute(
            "MERGE (c:Class {qualified_name: $qn}) "
            "SET c.name = $name, c.docstring = $doc, c.start_line = $sl, "
            "c.end_line = $el, c.is_abstract = $abs, c.complexity = $cx, "
            "c.design_pattern = $dp",
            parameters={
                "qn": node.qualified_name,
                "name": node.name,
                "doc": node.docstring,
                "sl": node.start_line,
                "el": node.end_line,
                "abs": node.is_abstract,
                "cx": node.complexity,
                "dp": node.design_pattern,
            },
        )

    def upsert_function(self, node: FunctionNode) -> None:
        self._conn.execute(
            "MERGE (f:Function {qualified_name: $qn}) "
            "SET f.name = $name, f.signature = $sig, f.docstring = $doc, "
            "f.body_hash = $bh, f.start_line = $sl, f.end_line = $el, "
            "f.complexity = $cx, f.is_async = $async, f.param_count = $pc, "
            "f.purpose = $purpose, f.category = $cat",
            parameters={
                "qn": node.qualified_name,
                "name": node.name,
                "sig": node.signature,
                "doc": node.docstring,
                "bh": node.body_hash,
                "sl": node.start_line,
                "el": node.end_line,
                "cx": node.complexity,
                "async": node.is_async,
                "pc": node.param_count,
                "purpose": node.purpose,
                "cat": node.category,
            },
        )

    def upsert_parameter(self, node: ParameterNode) -> None:
        self._conn.execute(
            "MERGE (p:Parameter {qualified_name: $qn}) "
            "SET p.name = $name, p.type_hint = $th, p.default_value = $dv, "
            "p.position = $pos",
            parameters={
                "qn": node.qualified_name,
                "name": node.name,
                "th": node.type_hint,
                "dv": node.default_value,
                "pos": node.position,
            },
        )

    def upsert_variable(self, node: VariableNode) -> None:
        self._conn.execute(
            "MERGE (v:Variable {qualified_name: $qn}) "
            "SET v.name = $name, v.type_hint = $th, v.scope = $scope, "
            "v.is_class_field = $cf",
            parameters={
                "qn": node.qualified_name,
                "name": node.name,
                "th": node.type_hint,
                "scope": node.scope,
                "cf": node.is_class_field,
            },
        )

    def upsert_decorator(self, node: DecoratorNode) -> None:
        self._conn.execute(
            "MERGE (d:Decorator {qualified_name: $qn}) "
            "SET d.name = $name, d.has_args = $ha",
            parameters={
                "qn": node.qualified_name,
                "name": node.name,
                "ha": node.has_args,
            },
        )

    # ── Edge Upserts ──────────────────────────────────────────

    def upsert_edge(self, edge: Edge) -> None:
        if edge.from_table and edge.to_table:
            from_table, to_table = edge.from_table, edge.to_table
        else:
            from_table, to_table = self._resolve_edge_tables(edge.rel_type, edge.from_key, edge.to_key)

        props_set = ""
        if edge.properties:
            assignments = ", ".join(f"r.{k} = ${k}" for k in edge.properties)
            props_set = f" SET {assignments}"

        from_field = self._pk_field(from_table)
        to_field = self._pk_field(to_table)

        if edge.rel_type == "CALLS":
            to_field = "name"
        elif edge.rel_type == "IMPORTS" and to_table == "Module":
            to_field = "name"

        cypher = (
            f"MATCH (a:{from_table}), (b:{to_table}) "
            f"WHERE a.{from_field} = $fk "
            f"AND b.{to_field} = $tk "
            f"MERGE (a)-[r:{edge.rel_type}]->(b){props_set}"
        )
        params = {"fk": edge.from_key, "tk": edge.to_key, **edge.properties}
        try:
            self._conn.execute(cypher, parameters=params)
        except Exception as exc:
            logger.warning("Edge upsert failed: %s %s->%s (%s->%s): %s",
                           edge.rel_type, edge.from_key, edge.to_key,
                           from_table, to_table, exc)

    # ── Bulk Operations ───────────────────────────────────────

    def delete_module_entities(self, module_path: str) -> None:
        """Remove all nodes belonging to a module (for re-indexing)."""
        for label in ("Parameter", "Variable", "Decorator"):
            self._conn.execute(
                f"MATCH (n:{label}) WHERE n.qualified_name STARTS WITH $prefix DETACH DELETE n",
                parameters={"prefix": module_path + "."},
            )
        for label in ("Function", "Class"):
            self._conn.execute(
                f"MATCH (n:{label}) WHERE n.qualified_name STARTS WITH $prefix DETACH DELETE n",
                parameters={"prefix": module_path + "."},
            )

    def get_module_functions(self, module_path: str) -> list[dict]:
        return self.query(
            "MATCH (m:Module {path: $path})-[:CONTAINS]->(f:Function) "
            "RETURN f.qualified_name AS qn, f.name AS name, f.body_hash AS hash",
            {"path": module_path},
        )

    # ── Stats ─────────────────────────────────────────────────

    def get_stats(self) -> dict[str, int]:
        stats = {}
        for label in ("Module", "Class", "Function", "Parameter", "Variable", "Decorator"):
            rows = self.query(f"MATCH (n:{label}) RETURN count(n) AS cnt")
            stats[label] = rows[0]["cnt"] if rows else 0
        return stats

    # ── Helpers ───────────────────────────────────────────────

    @staticmethod
    def _pk_field(table: str) -> str:
        return "path" if table == "Module" else "qualified_name"

    def _resolve_edge_tables(self, rel_type: str, from_key: str, to_key: str) -> tuple[str, str]:
        """Infer source/target table names from the relationship type."""
        simple: dict[str, tuple[str, str]] = {
            "HAS_METHOD": ("Class", "Function"),
            "HAS_PROPERTY": ("Class", "Variable"),
            "INHERITS": ("Class", "Class"),
            "CALLS": ("Function", "Function"),
            "IMPORTS": ("Module", "Module"),
            "HAS_PARAMETER": ("Function", "Parameter"),
            "RETURNS_TYPE": ("Function", "Class"),
        }
        if rel_type in simple:
            return simple[rel_type]

        if rel_type == "CONTAINS":
            to_table = self._detect_node_table(to_key)
            return ("Module", to_table or "Function")

        if rel_type == "USES":
            to_table = self._detect_node_table(to_key)
            return ("Function", to_table or "Function")

        if rel_type == "DECORATED_BY":
            from_table = self._detect_node_table(from_key)
            return (from_table or "Function", "Decorator")

        return ("Function", "Function")

    def _detect_node_table(self, key: str) -> str | None:
        """Check which table contains a node with the given key."""
        for table in ("Function", "Class", "Variable", "Decorator", "Parameter"):
            rows = self._conn.execute(
                f"MATCH (n:{table}) WHERE n.qualified_name = $k RETURN count(n) AS c",
                parameters={"k": key},
            )
            if rows.has_next():
                row = rows.get_next()
                if row[0] > 0:
                    return table

        rows = self._conn.execute(
            "MATCH (n:Module) WHERE n.path = $k RETURN count(n) AS c",
            parameters={"k": key},
        )
        if rows.has_next() and rows.get_next()[0] > 0:
            return "Module"

        return None

    def close(self) -> None:
        self._conn = None
        self._db = None
