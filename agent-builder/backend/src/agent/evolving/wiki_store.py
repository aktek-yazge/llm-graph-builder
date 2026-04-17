"""
Wiki Store
==========

Obsidian-style wiki layer built on top of KnowledgeStore.

Uses ``agent_knowledge`` table with ``knowledge_type = 'wiki_page'``.
Each wiki page is a versioned markdown document stored as JSONB::

    {
        "content": "# Sirket\\n...",
        "links": ["GercekKisi", "ORTAGI"],       # outgoing [[wikilink]] targets
        "category": "entities"                     # derived from path prefix
    }

Pages are addressed by *path* (the ``key`` column), e.g.
``entities/Sirket``, ``patterns/unvan-normalization``.

Log entries are stored as **separate rows** with
``knowledge_type = 'wiki_log'`` for O(1) append.

Index is generated **on-demand** (``get_index``) or persisted
explicitly via ``rebuild_index`` / ``lint``.

Graph traversal via ``traverse`` walks wikilinks N levels deep.
All link resolution is **case-insensitive**.
"""

from __future__ import annotations

import json
import logging
import re
import uuid as _uuid
from datetime import datetime
from typing import Any, Optional

from .knowledge_store import KnowledgeStore

logger = logging.getLogger(__name__)

WIKI_TYPE = "wiki_page"
WIKI_LOG_TYPE = "wiki_log"
WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")

WIKI_MIGRATION_SQL = """
CREATE INDEX IF NOT EXISTS idx_ak_wiki_fts
ON agent_knowledge USING gin (
    to_tsvector('simple', coalesce(value->>'content', ''))
)
WHERE knowledge_type = 'wiki_page';

CREATE INDEX IF NOT EXISTS idx_ak_wiki_log
ON agent_knowledge (agent_id, created_at DESC)
WHERE knowledge_type = 'wiki_log';
"""


# ─── Module-level helpers ────────────────────────────────────────────


def _extract_links(content: str) -> list[str]:
    """Parse ``[[Target]]`` and ``[[Target|alias]]`` from markdown."""
    return list(dict.fromkeys(WIKILINK_RE.findall(content)))


def _first_line_summary(content: str) -> str:
    """Return the first non-heading, non-empty line as a summary."""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped[:120]
    return ""


def _category_from_path(path: str) -> str:
    parts = path.split("/", 1)
    return parts[0] if len(parts) > 1 else "general"


class WikiStore:
    """Obsidian-style wiki CRUD over PostgreSQL ``agent_knowledge``."""

    def __init__(self, knowledge_store: KnowledgeStore):
        self.ks = knowledge_store

    async def ensure_indexes(self) -> None:
        """Create performance indexes for wiki operations (idempotent)."""
        try:
            await self.ks.pg.execute(WIKI_MIGRATION_SQL)
            logger.info("Wiki indexes ensured")
        except Exception as exc:
            logger.warning("Wiki index creation skipped: %s", exc)

    # ─── PAGE CRUD ───────────────────────────────────────────────────

    async def create_page(
        self,
        agent_id: str,
        path: str,
        content: str,
        *,
        source: str = "conversation",
        confidence: float = 1.0,
    ) -> dict[str, Any]:
        """Create or overwrite a wiki page (new version)."""
        links = _extract_links(content)
        value = {
            "content": content,
            "links": links,
            "category": _category_from_path(path),
        }
        result = await self.ks.upsert(
            agent_id, WIKI_TYPE, path, value,
            source=source, confidence=confidence,
        )
        await self._append_log(agent_id, "create", path)
        logger.info("Wiki page created: agent=%s path=%s v%d", agent_id, path, result["version"])
        return result

    async def update_page(
        self,
        agent_id: str,
        path: str,
        content: str,
        *,
        source: str = "conversation",
        confidence: float = 1.0,
    ) -> dict[str, Any]:
        """Update an existing page (appends new version)."""
        links = _extract_links(content)
        value = {
            "content": content,
            "links": links,
            "category": _category_from_path(path),
        }
        result = await self.ks.upsert(
            agent_id, WIKI_TYPE, path, value,
            source=source, confidence=confidence,
        )
        await self._append_log(agent_id, "update", path)
        return result

    async def get_page(self, agent_id: str, path: str) -> Optional[dict[str, Any]]:
        """Return latest version of a page, or *None*."""
        row = await self.ks.get(agent_id, WIKI_TYPE, path)
        if not row:
            return None
        return self._parse_row(row)

    async def delete_page(self, agent_id: str, path: str) -> bool:
        count = await self.ks.delete(agent_id, WIKI_TYPE, path)
        if count:
            await self._append_log(agent_id, "delete", path)
        return count > 0

    async def list_pages(
        self,
        agent_id: str,
        category: str = "",
    ) -> list[dict[str, Any]]:
        """List all pages (latest version each). Optionally filter by category."""
        rows = await self.ks.get_all(agent_id, WIKI_TYPE)
        pages = [self._parse_row(r) for r in rows]
        if category:
            pages = [p for p in pages if p.get("category") == category]
        return pages

    async def get_page_history(self, agent_id: str, path: str) -> list[dict[str, Any]]:
        """Full version history for a page."""
        return await self.ks.get_version_history(agent_id, WIKI_TYPE, path)

    # ─── SEARCH ──────────────────────────────────────────────────────

    async def search(self, agent_id: str, query: str) -> list[dict[str, Any]]:
        """
        Full-text search across wiki page content.

        Uses PostgreSQL ``to_tsvector`` / ``plainto_tsquery`` with
        ``simple`` config (no stemming — works for all languages).
        Falls back to ILIKE for partial-word matches.
        """
        rows = await self.ks.pg.fetch(
            """
            SELECT DISTINCT ON (key) *
            FROM agent_knowledge
            WHERE agent_id = $1
              AND knowledge_type = $2
              AND to_tsvector('simple', coalesce(value->>'content', ''))
                  @@ plainto_tsquery('simple', $3)
            ORDER BY key, version DESC
            """,
            agent_id, WIKI_TYPE, query,
        )
        if not rows:
            rows = await self.ks.pg.fetch(
                """
                SELECT DISTINCT ON (key) *
                FROM agent_knowledge
                WHERE agent_id = $1
                  AND knowledge_type = $2
                  AND value->>'content' ILIKE '%' || $3 || '%'
                ORDER BY key, version DESC
                LIMIT 20
                """,
                agent_id, WIKI_TYPE, query,
            )
        return [self._parse_row(dict(r)) for r in rows]

    # ─── LINKS & BACKLINKS ───────────────────────────────────────────

    async def get_backlinks(self, agent_id: str, target_page: str) -> list[str]:
        """
        Find all pages whose ``links`` array contains *target_page*
        (case-insensitive).
        """
        target_name = target_page.rsplit("/", 1)[-1]
        rows = await self.ks.pg.fetch(
            """
            SELECT DISTINCT ON (key) key
            FROM agent_knowledge
            WHERE agent_id = $1
              AND knowledge_type = $2
              AND EXISTS (
                  SELECT 1
                  FROM jsonb_array_elements_text(value->'links') AS link
                  WHERE lower(link) = lower($3)
              )
            ORDER BY key, version DESC
            """,
            agent_id, WIKI_TYPE, target_name,
        )
        return [r["key"] for r in rows]

    async def resolve_links(self, agent_id: str, content: str) -> dict[str, str | None]:
        """
        For every ``[[Target]]`` in *content*, check if a matching page exists
        (case-insensitive).

        Returns ``{link_target: page_path_or_None}``.
        """
        targets = _extract_links(content)
        if not targets:
            return {}
        all_pages = await self.list_pages(agent_id)
        name_index, _ = self._build_page_index(all_pages)
        return {t: self._resolve_path(t, name_index, {}) for t in targets}

    async def get_unresolved_links(self, agent_id: str) -> list[dict[str, str]]:
        """
        Scan all pages and return every wikilink that points to a
        non-existent page.

        Returns list of ``{"source": page_path, "target": link_text}``.
        """
        all_pages = await self.list_pages(agent_id)
        name_index, path_index = self._build_page_index(all_pages)

        broken: list[dict[str, str]] = []
        for page in all_pages:
            path = page["path"]
            if path.startswith("_"):
                continue
            for link in page.get("links", []):
                if not self._resolve_path(link, name_index, path_index):
                    broken.append({"source": path, "target": link})
        return broken

    # ─── GRAPH TRAVERSAL ─────────────────────────────────────────────

    async def traverse(
        self,
        agent_id: str,
        start_path: str,
        depth: int = 2,
    ) -> dict[str, dict[str, Any]]:
        """
        Walk wikilinks outward from *start_path* up to *depth* levels.

        Loads all pages once, then traverses in-memory (BFS).
        Returns ``{path: page_dict}`` for every reachable page.
        """
        all_pages = await self.list_pages(agent_id)
        name_index, path_index = self._build_page_index(all_pages)

        start_resolved = self._resolve_path(start_path, name_index, path_index)
        if not start_resolved:
            return {}

        visited: dict[str, dict[str, Any]] = {}
        queue: list[tuple[str, int]] = [(start_resolved, 0)]

        while queue:
            path, level = queue.pop(0)
            if path in visited or level > depth:
                continue
            page = path_index.get(path)
            if not page:
                continue
            visited[path] = page
            if level < depth:
                for link in page.get("links", []):
                    resolved = self._resolve_path(link, name_index, path_index)
                    if resolved and resolved not in visited:
                        queue.append((resolved, level + 1))

        return visited

    # ─── INDEX & LINT ────────────────────────────────────────────────

    async def get_index(self, agent_id: str) -> str:
        """
        Generate the wiki index on-demand (not persisted).

        Each entry shows backlink count so the agent sees page
        "importance" at a glance — like Obsidian's node size in
        graph view.
        """
        all_pages = await self.list_pages(agent_id)
        pages = [p for p in all_pages if not p["path"].startswith("_")]

        if not pages:
            return "Wiki bos. Henuz sayfa olusturulmamis."

        name_index, _ = self._build_page_index(pages)

        inbound: dict[str, int] = {p["path"]: 0 for p in pages}
        for p in pages:
            for link in p.get("links", []):
                resolved = self._resolve_path(link, name_index, {})
                if resolved and resolved in inbound:
                    inbound[resolved] += 1

        lines = ["# Wiki Index", f"Toplam {len(pages)} sayfa.", ""]

        by_cat: dict[str, list[dict]] = {}
        for p in pages:
            by_cat.setdefault(p.get("category", "general"), []).append(p)

        for cat in ("entities", "relationships", "patterns", "sources", "analysis", "general"):
            cat_pages = by_cat.get(cat, [])
            if not cat_pages:
                continue
            lines.append(f"## {cat.capitalize()}")
            for p in sorted(cat_pages, key=lambda x: x["path"]):
                summary = _first_line_summary(p.get("content", ""))
                refs = inbound.get(p["path"], 0)
                ref_str = f" [{refs} refs]" if refs else ""
                lines.append(f"- [[{p['path']}]]: {summary}{ref_str}")
            lines.append("")

        return "\n".join(lines)

    async def rebuild_index(self, agent_id: str) -> str:
        """Persist the wiki index as the ``_index`` system page."""
        content = await self.get_index(agent_id)
        links = _extract_links(content)
        value = {
            "content": content,
            "links": links,
            "category": "system",
        }
        await self.ks.upsert(
            agent_id, WIKI_TYPE, "_index", value,
            source="system", confidence=1.0,
        )
        return content

    async def lint(self, agent_id: str) -> dict[str, Any]:
        """
        Run health checks on the wiki (Karpathy-style lint operation).

        Checks:
        - **Orphan pages**: no other page links to them.
        - **Broken links**: ``[[Target]]`` that resolves to nothing.
        - **Unresolved targets**: unique set of missing link destinations.

        Also rebuilds the persisted ``_index`` page.
        """
        all_pages = await self.list_pages(agent_id)
        pages = [p for p in all_pages if not p["path"].startswith("_")]
        name_index, path_index = self._build_page_index(pages)

        orphan_pages: list[str] = []
        broken_links: list[dict[str, str]] = []
        unresolved_targets: set[str] = set()
        inbound: dict[str, int] = {p["path"]: 0 for p in pages}

        total_links = 0
        for page in pages:
            for link in page.get("links", []):
                total_links += 1
                resolved = self._resolve_path(link, name_index, path_index)
                if resolved:
                    if resolved in inbound:
                        inbound[resolved] += 1
                else:
                    broken_links.append({"source": page["path"], "target": link})
                    unresolved_targets.add(link)

        for path, count in inbound.items():
            if count == 0:
                orphan_pages.append(path)

        await self.rebuild_index(agent_id)

        return {
            "total_pages": len(pages),
            "total_links": total_links,
            "orphan_pages": sorted(orphan_pages),
            "broken_links": broken_links,
            "unresolved_targets": sorted(unresolved_targets),
            "issues_count": len(orphan_pages) + len(broken_links),
        }

    # ─── EXTRACTION CONTEXT ─────────────────────────────────────────

    async def build_extraction_context(
        self,
        agent_id: str,
        categories: list[str] | None = None,
    ) -> str:
        """
        Assemble wiki pages into a single markdown string suitable
        for injection into an LLM extraction prompt.

        Ordering: entities -> relationships -> patterns -> analysis -> rest.

        Args:
            agent_id: Agent identifier.
            categories: Optional filter. If provided, only pages whose
                ``category`` is in this list are included. System pages
                (path prefix ``_``) are always excluded.
        """
        pages = await self.list_pages(agent_id)
        if not pages:
            return ""

        category_order = {
            "entities": 0,
            "relationships": 1,
            "patterns": 2,
            "analysis": 3,
            "sources": 4,
            "general": 5,
        }
        pages.sort(key=lambda p: (category_order.get(p.get("category", "general"), 99), p["path"]))

        allowed = set(categories) if categories else None

        sections: list[str] = []
        current_cat = ""
        for page in pages:
            path = page["path"]
            if path.startswith("_"):
                continue
            cat = page.get("category", "general")
            if allowed is not None and cat not in allowed:
                continue
            if cat != current_cat:
                current_cat = cat
                sections.append(f"\n## {cat.upper()}\n")
            sections.append(page["content"])
            sections.append("")

        return "\n".join(sections)

    # ─── AUTO-GENERATE FROM ONTOLOGY ─────────────────────────────────

    async def sync_entity_page(
        self,
        agent_id: str,
        entity_name: str,
        description: str = "",
        properties: list[dict[str, Any]] | None = None,
        parent: str = "",
        related_relationships: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Create or update a wiki page for an entity class.

        Called automatically when ``add_entity_class`` fires.
        Merges new info into existing page if one exists.
        """
        path = f"entities/{entity_name}"
        existing = await self.get_page(agent_id, path)

        lines: list[str] = [f"# {entity_name}"]
        if description:
            lines.append(description)

        if parent:
            lines.append(f"\nAlt sinif: [[{parent}]]")

        if properties:
            lines.append("\n## Properties")
            for p in properties:
                constraint = f" [{p.get('constraint', 'optional')}]" if p.get("constraint") else ""
                ptype = p.get("type", "string")
                desc = p.get("description", "")
                lines.append(f"- **{p['name']}** [{ptype}{constraint}]: {desc}")

        if related_relationships:
            lines.append("\n## Relationships")
            for rel in related_relationships:
                lines.append(f"- [[{rel}]]")

        if existing:
            old_content = existing.get("content", "")
            for section_header in ("## Ogrenilenler", "## Edge Cases", "## Ornekler"):
                idx = old_content.find(section_header)
                if idx != -1:
                    lines.append("")
                    lines.append(old_content[idx:].split("\n## ")[0].rstrip())
        else:
            lines.extend([
                "\n## Ogrenilenler",
                "(henuz yok)",
                "\n## Edge Cases",
                "(henuz yok)",
                "\n## Ornekler",
                "(henuz yok)",
            ])

        content = "\n".join(lines)
        return await self.create_page(agent_id, path, content, source="ontology_sync")

    async def sync_relationship_page(
        self,
        agent_id: str,
        rel_name: str,
        source_entity: str = "",
        target_entity: str = "",
        edge_properties: list[str] | None = None,
        description: str = "",
    ) -> dict[str, Any]:
        """Create or update a wiki page for a relationship predicate."""
        path = f"relationships/{rel_name}"
        existing = await self.get_page(agent_id, path)

        lines: list[str] = [f"# {rel_name}"]
        if description:
            lines.append(description)

        if source_entity and target_entity:
            lines.append(f"\n[[{source_entity}]] --[{rel_name}]--> [[{target_entity}]]")

        if edge_properties:
            lines.append("\n## Edge Properties")
            for ep in edge_properties:
                lines.append(f"- {ep}")

        if existing:
            old_content = existing.get("content", "")
            for section_header in ("## Ogrenilenler", "## Edge Cases", "## Ornekler"):
                idx = old_content.find(section_header)
                if idx != -1:
                    lines.append("")
                    lines.append(old_content[idx:].split("\n## ")[0].rstrip())
        else:
            lines.extend([
                "\n## Ogrenilenler",
                "(henuz yok)",
                "\n## Edge Cases",
                "(henuz yok)",
                "\n## Ornekler",
                "(henuz yok)",
            ])

        content = "\n".join(lines)
        return await self.create_page(agent_id, path, content, source="ontology_sync")

    async def add_pattern_page(
        self,
        agent_id: str,
        pattern_name: str,
        description: str,
        examples: str = "",
        related_entities: list[str] | None = None,
        source: str = "feedback",
    ) -> dict[str, Any]:
        """Create a learned-pattern page from feedback or analysis."""
        path = f"patterns/{pattern_name}"

        lines: list[str] = [f"# {pattern_name}", description]

        if examples:
            lines.extend(["\n## Ornekler", examples])

        if related_entities:
            lines.append("\n## Ilgili")
            for e in related_entities:
                lines.append(f"- [[{e}]]")

        lines.append(f"\nKaynak: {source}")
        lines.append(f"Tarih: {datetime.utcnow().strftime('%Y-%m-%d')}")

        content = "\n".join(lines)
        return await self.create_page(agent_id, path, content, source=source)

    # ─── LOG ─────────────────────────────────────────────────────────

    async def get_log(self, agent_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """
        Return recent wiki mutation log entries (newest first).

        Each entry is a separate DB row — O(1) append, O(limit) read.
        """
        rows = await self.ks.pg.fetch(
            """
            SELECT key, value, created_at
            FROM agent_knowledge
            WHERE agent_id = $1 AND knowledge_type = $2
            ORDER BY created_at DESC
            LIMIT $3
            """,
            agent_id, WIKI_LOG_TYPE, limit,
        )
        entries: list[dict[str, Any]] = []
        for r in rows:
            val = r["value"]
            if isinstance(val, str):
                val = json.loads(val)
            entries.append({
                "action": val.get("action", ""),
                "path": val.get("path", ""),
                "timestamp": val.get("timestamp", str(r["created_at"])),
            })
        return entries

    async def _append_log(self, agent_id: str, action: str, path: str) -> None:
        """
        Store a single log entry as its own row.

        Unlike the old approach (read-entire-log → append → rewrite),
        this is a constant-time INSERT.
        """
        ts = datetime.utcnow()
        key = f"{ts.strftime('%Y%m%d_%H%M%S')}_{_uuid.uuid4().hex[:8]}"
        value = json.dumps({
            "action": action,
            "path": path,
            "timestamp": ts.isoformat(),
        }, ensure_ascii=False)
        await self.ks.pg.execute(
            """
            INSERT INTO agent_knowledge
                (id, agent_id, knowledge_type, key, value, version, confidence, source)
            VALUES ($1, $2, $3, $4, $5::jsonb, 1, 1.0, 'system')
            """,
            str(_uuid.uuid4()), agent_id, WIKI_LOG_TYPE, key, value,
        )

    # ─── INTERNAL HELPERS ────────────────────────────────────────────

    @staticmethod
    def _build_page_index(
        pages: list[dict[str, Any]],
    ) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
        """
        Build in-memory lookup tables from a page list.

        Returns ``(name_index, path_index)`` where:
        - *name_index*: ``{lowercase_name: canonical_path}``
          contains both full paths and last-segment names.
        - *path_index*: ``{canonical_path: page_dict}``
        """
        name_index: dict[str, str] = {}
        path_index: dict[str, dict[str, Any]] = {}

        for p in pages:
            path = p["path"]
            path_index[path] = p
            name_index[path.lower()] = path
            name = path.rsplit("/", 1)[-1].lower()
            if name not in name_index:
                name_index[name] = path

        return name_index, path_index

    @staticmethod
    def _resolve_path(
        target: str,
        name_index: dict[str, str],
        path_index: dict[str, dict[str, Any]],
    ) -> str | None:
        """Resolve a link target to a canonical page path (case-insensitive)."""
        if target in path_index:
            return target
        return name_index.get(target.strip().lower())

    @staticmethod
    def _parse_row(row: dict[str, Any]) -> dict[str, Any]:
        """Normalize a ``agent_knowledge`` row into a wiki page dict."""
        value = row.get("value", {})
        if isinstance(value, str):
            value = json.loads(value)

        return {
            "path": row.get("key", ""),
            "content": value.get("content", ""),
            "links": value.get("links", []),
            "category": value.get("category", "general"),
            "version": row.get("version", 1),
            "source": row.get("source", ""),
            "created_at": str(row["created_at"]) if row.get("created_at") else None,
        }
