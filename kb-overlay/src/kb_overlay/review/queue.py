"""Pending entities + ambiguous resolutions için review kuyruğu.

`alias_store`'un kullandığı SQLite veritabanına ek bir tablo açılır. Aynı
bağlantı paylaşılır; review queue ayrı bir DB değildir.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from ..dictionary import AliasSource, AliasStore, EntityStatus, EntityType


class PendingKind(str, Enum):
    NEW_ENTITY = "new_entity"          # NER bulundu, hiç aday yok
    AMBIGUOUS = "ambiguous"            # birden fazla canonical aday
    LOW_CONFIDENCE = "low_confidence"  # fuzzy/vector skoru sınırın altında


class PendingStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


_REVIEW_SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_items (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    kind                 TEXT NOT NULL,
    surface              TEXT NOT NULL,
    entity_type_guess    TEXT,
    status               TEXT NOT NULL DEFAULT 'pending',
    payload_json         TEXT,
    candidate_ids        TEXT,         -- JSON array of canonical_ids
    source_doc_id        TEXT,
    created_at           TEXT NOT NULL DEFAULT (datetime('now')),
    decided_at           TEXT,
    decided_by           TEXT,
    decision_payload_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_pending_status ON pending_items(status);
CREATE INDEX IF NOT EXISTS idx_pending_kind   ON pending_items(kind);
CREATE INDEX IF NOT EXISTS idx_pending_doc    ON pending_items(source_doc_id);
"""


@dataclass(slots=True)
class PendingItem:
    id: int
    kind: str
    surface: str
    entity_type_guess: Optional[str]
    status: str
    payload: dict = field(default_factory=dict)
    candidate_ids: list[str] = field(default_factory=list)
    source_doc_id: Optional[str] = None
    created_at: Optional[str] = None
    decided_at: Optional[str] = None
    decided_by: Optional[str] = None
    decision_payload: dict = field(default_factory=dict)


class ReviewQueue:
    def __init__(self, store: AliasStore):
        self.store = store
        self._init_schema()

    def _init_schema(self) -> None:
        conn = self.store._connect()  # ortak bağlantı
        conn.executescript(_REVIEW_SCHEMA)
        conn.commit()

    # ------------------------------------------------------------------ enqueue

    def enqueue(
        self,
        *,
        kind: str | PendingKind,
        surface: str,
        entity_type_guess: Optional[str] = None,
        source_doc_id: Optional[str] = None,
        candidate_ids: Optional[list[str]] = None,
        payload: Optional[dict] = None,
    ) -> int:
        kind_str = kind.value if isinstance(kind, PendingKind) else kind
        with self.store.transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO pending_items
                  (kind, surface, entity_type_guess, source_doc_id, candidate_ids, payload_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    kind_str,
                    surface,
                    entity_type_guess,
                    source_doc_id,
                    json.dumps(candidate_ids or [], ensure_ascii=False),
                    json.dumps(payload or {}, ensure_ascii=False),
                ),
            )
            return cur.lastrowid or 0

    # ------------------------------------------------------------------ query

    def list(
        self,
        *,
        status: str | PendingStatus = PendingStatus.PENDING,
        kind: Optional[str] = None,
        limit: int = 100,
    ) -> list[PendingItem]:
        status_str = status.value if isinstance(status, PendingStatus) else status
        sql = "SELECT * FROM pending_items WHERE status = ?"
        params: list = [status_str]
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self.store._connect().execute(sql, params).fetchall()
        return [self._row_to_item(r) for r in rows]

    def get(self, item_id: int) -> Optional[PendingItem]:
        row = self.store._connect().execute(
            "SELECT * FROM pending_items WHERE id = ?", (item_id,)
        ).fetchone()
        return self._row_to_item(row) if row else None

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> PendingItem:
        return PendingItem(
            id=row["id"],
            kind=row["kind"],
            surface=row["surface"],
            entity_type_guess=row["entity_type_guess"],
            status=row["status"],
            payload=json.loads(row["payload_json"] or "{}"),
            candidate_ids=json.loads(row["candidate_ids"] or "[]"),
            source_doc_id=row["source_doc_id"],
            created_at=row["created_at"],
            decided_at=row["decided_at"],
            decided_by=row["decided_by"],
            decision_payload=json.loads(row["decision_payload_json"] or "{}"),
        )

    # ------------------------------------------------------------------ decisions

    def approve_as_new_entity(
        self,
        item_id: int,
        *,
        canonical_name: Optional[str] = None,
        entity_type: Optional[EntityType | str] = None,
        decided_by: str = "human",
        seed_aliases: Optional[list[str]] = None,
    ) -> str:
        """Pending item'ı yeni canonical entity olarak yarat."""
        item = self.get(item_id)
        if item is None:
            raise ValueError(f"pending item {item_id} bulunamadı")
        if item.status != PendingStatus.PENDING.value:
            raise ValueError(f"pending item {item_id} zaten karara bağlanmış: {item.status}")

        name = canonical_name or item.surface
        etype_str = (entity_type.value if isinstance(entity_type, EntityType) else entity_type) or item.entity_type_guess or "other"
        try:
            etype_enum = EntityType(etype_str)
        except ValueError:
            etype_enum = EntityType.OTHER

        seeds = list(seed_aliases or [])
        if item.surface not in seeds:
            seeds.append(item.surface)

        cid = self.store.create_entity(
            canonical_name=name,
            entity_type=etype_enum,
            status=EntityStatus.VERIFIED,
            source="human_review",
            seed_aliases=seeds,
        )
        # Surface_form için provenance'? human olarak ekle
        self.store.add_alias(
            item.surface,
            cid,
            kind="company" if etype_enum == EntityType.COMPANY else "person" if etype_enum == EntityType.PERSON else "any",
            confidence=1.0,
            source=AliasSource.HUMAN,
            source_doc_id=item.source_doc_id,
        )
        self._mark_decided(
            item_id,
            new_status=PendingStatus.APPROVED,
            decided_by=decided_by,
            payload={"canonical_id": cid, "action": "create_new", "canonical_name": name},
        )
        return cid

    def approve_link_to_existing(
        self,
        item_id: int,
        *,
        canonical_id: str,
        decided_by: str = "human",
    ) -> None:
        """Pending item'ı mevcut bir canonical'a alias olarak bağla."""
        item = self.get(item_id)
        if item is None:
            raise ValueError(f"pending item {item_id} bulunamadı")
        if item.status != PendingStatus.PENDING.value:
            raise ValueError(f"pending item {item_id} zaten karara bağlanmış: {item.status}")
        entity = self.store.get_entity(canonical_id)
        if entity is None:
            raise ValueError(f"canonical_id {canonical_id} mevcut değil")

        kind = "company" if entity.entity_type == EntityType.COMPANY.value else (
            "person" if entity.entity_type == EntityType.PERSON.value else "any"
        )
        self.store.add_alias(
            item.surface,
            canonical_id,
            kind=kind,
            confidence=1.0,
            source=AliasSource.HUMAN,
            source_doc_id=item.source_doc_id,
        )
        self._mark_decided(
            item_id,
            new_status=PendingStatus.APPROVED,
            decided_by=decided_by,
            payload={"canonical_id": canonical_id, "action": "link_existing"},
        )

    def reject(self, item_id: int, *, reason: str = "", decided_by: str = "human") -> None:
        self._mark_decided(
            item_id,
            new_status=PendingStatus.REJECTED,
            decided_by=decided_by,
            payload={"reason": reason},
        )

    def _mark_decided(
        self,
        item_id: int,
        *,
        new_status: PendingStatus,
        decided_by: str,
        payload: dict,
    ) -> None:
        with self.store.transaction() as conn:
            conn.execute(
                """
                UPDATE pending_items
                SET status = ?, decided_at = datetime('now'),
                    decided_by = ?, decision_payload_json = ?
                WHERE id = ?
                """,
                (new_status.value, decided_by, json.dumps(payload, ensure_ascii=False), item_id),
            )

    def stats(self) -> dict:
        c = self.store._connect()
        rows = c.execute(
            "SELECT status, kind, COUNT(*) AS n FROM pending_items GROUP BY status, kind"
        ).fetchall()
        out: dict = {"by_status": {}, "by_status_kind": {}}
        for r in rows:
            status = r["status"]
            kind = r["kind"]
            out["by_status"][status] = out["by_status"].get(status, 0) + r["n"]
            out["by_status_kind"].setdefault(status, {})[kind] = r["n"]
        return out
