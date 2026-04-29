"""SQLite alias dictionary — kalıcı master sözlük.

Her surface_form (belgede görülen yazım) bir canonical_id'ye bağlanır.
Aynı norm_strict birden fazla canonical_id'ye bağlanabilir (ambiguous lookup).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from importlib import resources
from pathlib import Path
from typing import Iterable, Iterator, Optional

from ..normalize import normalize


class EntityType(str, Enum):
    COMPANY = "company"
    PERSON = "person"
    ORGANIZATION = "organization"
    LOCATION = "location"
    PRODUCT = "product"
    OTHER = "other"


class AliasSource(str, Enum):
    NER_FIRST = "ner_first_observed"
    FUZZY = "fuzzy_match"
    HUMAN = "human"
    SEED = "gazetteer_seed"
    EMBEDDING = "embedding"
    GAZETTEER_HIT = "gazetteer_hit"


class EntityStatus(str, Enum):
    VERIFIED = "verified"
    PENDING = "pending"
    AUTO_ADDED = "auto_added"
    REJECTED = "rejected"


@dataclass(slots=True)
class AliasRecord:
    surface_form: str
    norm_strict: str
    norm_loose: str
    canonical_id: str
    confidence: float = 1.0
    source: str = AliasSource.NER_FIRST.value
    source_doc_id: Optional[str] = None
    source_span_start: Optional[int] = None
    source_span_end: Optional[int] = None


@dataclass(slots=True)
class EntityRecord:
    canonical_id: str
    canonical_name: str
    entity_type: str
    norm_strict: str
    norm_loose: str
    status: str = EntityStatus.AUTO_ADDED.value
    source: str = "document_extraction"
    metadata: dict = field(default_factory=dict)


def _load_schema_sql() -> str:
    schema_path = Path(__file__).with_name("schema.sql")
    return schema_path.read_text(encoding="utf-8")


def _gen_canonical_id(canonical_name: str, entity_type: str) -> str:
    """Deterministik ID: type prefix + normalize edilmiş isim hash'i.

    Aynı isim aynı tipte iki kere gelirse aynı ID üretir; bu yüzden upsert
    güvenli. Çakışma olursa (farklı isimler aynı hash) suffix eklenir.
    """
    prefix = {
        EntityType.COMPANY.value: "COMP",
        EntityType.PERSON.value: "PERS",
        EntityType.ORGANIZATION.value: "ORG",
        EntityType.LOCATION.value: "LOC",
        EntityType.PRODUCT.value: "PROD",
    }.get(entity_type, "ENT")
    digest = hashlib.sha1(f"{entity_type}|{canonical_name.lower()}".encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{digest}"


class AliasStore:
    """SQLite tabanlı alias master sözlük.

    Kullanım:
        store = AliasStore("data/aliases.db")
        store.init_schema()
        cid = store.create_entity("ABC Bilişim A.Ş.", EntityType.COMPANY)
        store.add_alias("ABC Bilişim", cid, source=AliasSource.NER_FIRST)
        hits = store.lookup("abc bilişim'in")  # liste (ambiguous olabilir)
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None

    # ------------------------------------------------------------------ infra

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            self._conn = conn
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def init_schema(self) -> None:
        conn = self._connect()
        conn.executescript(_load_schema_sql())
        conn.commit()

    # ------------------------------------------------------------------ entity

    def create_entity(
        self,
        canonical_name: str,
        entity_type: EntityType | str,
        *,
        status: EntityStatus | str = EntityStatus.AUTO_ADDED,
        source: str = "document_extraction",
        metadata: Optional[dict] = None,
        canonical_id: Optional[str] = None,
        seed_aliases: Optional[Iterable[str]] = None,
    ) -> str:
        """Yeni canonical entity oluşturur (idempotent: aynı isim+tip → aynı ID).

        ``seed_aliases`` verilirse (canonical_name dahil) her biri için bir
        alias kaydı atılır. Sonradan eklenenler `add_alias` ile eklenir.
        """
        etype = entity_type.value if isinstance(entity_type, EntityType) else entity_type
        norm = normalize(canonical_name, kind="company" if etype == EntityType.COMPANY.value else "person")
        cid = canonical_id or _gen_canonical_id(canonical_name, etype)
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO entities
                  (canonical_id, canonical_name, entity_type, norm_strict, norm_loose,
                   status, source, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(canonical_id) DO UPDATE SET
                  canonical_name = excluded.canonical_name,
                  norm_strict    = excluded.norm_strict,
                  norm_loose     = excluded.norm_loose,
                  metadata_json  = excluded.metadata_json,
                  updated_at     = datetime('now')
                """,
                (
                    cid,
                    canonical_name,
                    etype,
                    norm.strict,
                    norm.loose,
                    status.value if isinstance(status, EntityStatus) else status,
                    source,
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
            seeds = list(seed_aliases) if seed_aliases else []
            if canonical_name not in seeds:
                seeds.insert(0, canonical_name)
            for surface in seeds:
                self._insert_alias(
                    conn,
                    AliasRecord(
                        surface_form=surface,
                        norm_strict=normalize(surface, kind=norm.kind).strict,
                        norm_loose=normalize(surface, kind=norm.kind).loose,
                        canonical_id=cid,
                        confidence=1.0,
                        source=AliasSource.SEED.value,
                    ),
                )
        return cid

    def get_entity(self, canonical_id: str) -> Optional[EntityRecord]:
        row = self._connect().execute(
            "SELECT * FROM entities WHERE canonical_id = ?",
            (canonical_id,),
        ).fetchone()
        if not row:
            return None
        return EntityRecord(
            canonical_id=row["canonical_id"],
            canonical_name=row["canonical_name"],
            entity_type=row["entity_type"],
            norm_strict=row["norm_strict"],
            norm_loose=row["norm_loose"],
            status=row["status"],
            source=row["source"],
            metadata=json.loads(row["metadata_json"] or "{}"),
        )

    def update_entity_status(self, canonical_id: str, status: EntityStatus | str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE entities SET status = ?, updated_at = datetime('now') WHERE canonical_id = ?",
                (status.value if isinstance(status, EntityStatus) else status, canonical_id),
            )

    def list_entities(
        self,
        *,
        entity_type: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 1000,
    ) -> list[EntityRecord]:
        sql = "SELECT * FROM entities WHERE 1=1"
        params: list = []
        if entity_type:
            sql += " AND entity_type = ?"
            params.append(entity_type)
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self._connect().execute(sql, params).fetchall()
        return [
            EntityRecord(
                canonical_id=r["canonical_id"],
                canonical_name=r["canonical_name"],
                entity_type=r["entity_type"],
                norm_strict=r["norm_strict"],
                norm_loose=r["norm_loose"],
                status=r["status"],
                source=r["source"],
                metadata=json.loads(r["metadata_json"] or "{}"),
            )
            for r in rows
        ]

    # ------------------------------------------------------------------ alias

    def add_alias(
        self,
        surface_form: str,
        canonical_id: str,
        *,
        kind: str = "any",
        confidence: float = 1.0,
        source: AliasSource | str = AliasSource.NER_FIRST,
        source_doc_id: Optional[str] = None,
        source_span: Optional[tuple[int, int]] = None,
    ) -> Optional[int]:
        """Bir surface_form'u canonical'a bağlar. Idempotent."""
        norm = normalize(surface_form, kind=kind)
        rec = AliasRecord(
            surface_form=surface_form,
            norm_strict=norm.strict,
            norm_loose=norm.loose,
            canonical_id=canonical_id,
            confidence=confidence,
            source=source.value if isinstance(source, AliasSource) else source,
            source_doc_id=source_doc_id,
            source_span_start=source_span[0] if source_span else None,
            source_span_end=source_span[1] if source_span else None,
        )
        with self.transaction() as conn:
            return self._insert_alias(conn, rec)

    @staticmethod
    def _insert_alias(conn: sqlite3.Connection, rec: AliasRecord) -> Optional[int]:
        # UNIQUE(surface_form, canonical_id): aynı yazım tekrar görüldüğünde
        # provenance bilgisi (doc_id, span) varsa güncellenir; mevcut değer null
        # ise yeni gelen yazılır, yeni gelen null ise mevcut korunur.
        cur = conn.execute(
            """
            INSERT INTO aliases
              (surface_form, norm_strict, norm_loose, canonical_id, confidence,
               source, source_doc_id, source_span_start, source_span_end)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(surface_form, canonical_id) DO UPDATE SET
              confidence       = MAX(aliases.confidence, excluded.confidence),
              source           = CASE
                                    WHEN excluded.source = 'human' THEN 'human'
                                    WHEN aliases.source  = 'human' THEN aliases.source
                                    ELSE excluded.source
                                 END,
              source_doc_id    = COALESCE(excluded.source_doc_id, aliases.source_doc_id),
              source_span_start= COALESCE(excluded.source_span_start, aliases.source_span_start),
              source_span_end  = COALESCE(excluded.source_span_end, aliases.source_span_end)
            """,
            (
                rec.surface_form,
                rec.norm_strict,
                rec.norm_loose,
                rec.canonical_id,
                rec.confidence,
                rec.source,
                rec.source_doc_id,
                rec.source_span_start,
                rec.source_span_end,
            ),
        )
        return cur.lastrowid

    def lookup(
        self,
        surface_form: str,
        *,
        kind: str = "any",
        entity_type: Optional[str] = None,
    ) -> list[tuple[AliasRecord, str]]:
        """Önce strict, sonra loose lookup yapar.

        Dönüş: [(alias_record, match_kind)]  match_kind 'strict' | 'loose'.
        Boş liste döndürürse bilinmiyor demektir.
        """
        norm = normalize(surface_form, kind=kind)
        rows = self._lookup_by_strict(norm.strict, entity_type)
        if rows:
            return [(self._row_to_alias(r), "strict") for r in rows]
        rows = self._lookup_by_loose(norm.loose, entity_type)
        return [(self._row_to_alias(r), "loose") for r in rows]

    def _lookup_by_strict(self, key: str, entity_type: Optional[str]) -> list[sqlite3.Row]:
        sql = (
            "SELECT a.* FROM aliases a JOIN entities e ON a.canonical_id = e.canonical_id "
            "WHERE a.norm_strict = ? AND e.status != 'rejected'"
        )
        params: list = [key]
        if entity_type:
            sql += " AND e.entity_type = ?"
            params.append(entity_type)
        return self._connect().execute(sql, params).fetchall()

    def _lookup_by_loose(self, key: str, entity_type: Optional[str]) -> list[sqlite3.Row]:
        sql = (
            "SELECT a.* FROM aliases a JOIN entities e ON a.canonical_id = e.canonical_id "
            "WHERE a.norm_loose = ? AND e.status != 'rejected'"
        )
        params: list = [key]
        if entity_type:
            sql += " AND e.entity_type = ?"
            params.append(entity_type)
        return self._connect().execute(sql, params).fetchall()

    @staticmethod
    def _row_to_alias(row: sqlite3.Row) -> AliasRecord:
        return AliasRecord(
            surface_form=row["surface_form"],
            norm_strict=row["norm_strict"],
            norm_loose=row["norm_loose"],
            canonical_id=row["canonical_id"],
            confidence=row["confidence"],
            source=row["source"],
            source_doc_id=row["source_doc_id"],
            source_span_start=row["source_span_start"],
            source_span_end=row["source_span_end"],
        )

    def get_aliases_for(self, canonical_id: str) -> list[AliasRecord]:
        rows = self._connect().execute(
            "SELECT * FROM aliases WHERE canonical_id = ? ORDER BY confidence DESC, added_at ASC",
            (canonical_id,),
        ).fetchall()
        return [self._row_to_alias(r) for r in rows]

    def iter_all_aliases(
        self,
        *,
        entity_type: Optional[str] = None,
        include_loose: bool = True,
    ) -> Iterator[tuple[str, str, str]]:
        """(key, canonical_id, key_kind) üçlüleri yield eder.

        Gazetteer trie inşası için. ``key_kind`` 'strict' veya 'loose'.
        """
        sql = (
            "SELECT a.norm_strict, a.norm_loose, a.canonical_id "
            "FROM aliases a JOIN entities e ON a.canonical_id = e.canonical_id "
            "WHERE e.status != 'rejected'"
        )
        params: list = []
        if entity_type:
            sql += " AND e.entity_type = ?"
            params.append(entity_type)
        for row in self._connect().execute(sql, params):
            strict = row["norm_strict"]
            loose = row["norm_loose"]
            if strict:
                yield strict, row["canonical_id"], "strict"
            if include_loose and loose and loose != strict:
                yield loose, row["canonical_id"], "loose"

    def iter_surface_forms(
        self,
        *,
        entity_type: Optional[str] = None,
    ) -> Iterator[tuple[str, str, str]]:
        """Yield (surface_form, canonical_id, entity_type) for gazetteer trie.

        Surface_form is the raw text as observed in documents; gazetteer needs
        these (not suffix-stripped keys) because that's what appears in text.
        """
        sql = (
            "SELECT a.surface_form, a.canonical_id, e.entity_type "
            "FROM aliases a JOIN entities e ON a.canonical_id = e.canonical_id "
            "WHERE e.status != 'rejected' AND a.surface_form IS NOT NULL "
            "AND length(trim(a.surface_form)) > 0"
        )
        params: list = []
        if entity_type:
            sql += " AND e.entity_type = ?"
            params.append(entity_type)
        for row in self._connect().execute(sql, params):
            yield row["surface_form"], row["canonical_id"], row["entity_type"]

    # ------------------------------------------------------------------ documents

    def register_document(
        self,
        doc_id: str,
        *,
        title: Optional[str] = None,
        sha256: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO documents (doc_id, title, sha256, metadata_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(doc_id) DO UPDATE SET
                  title = excluded.title,
                  sha256 = COALESCE(excluded.sha256, documents.sha256),
                  metadata_json = excluded.metadata_json,
                  processed_at = datetime('now')
                """,
                (doc_id, title, sha256, json.dumps(metadata or {}, ensure_ascii=False)),
            )

    # ------------------------------------------------------------------ ingest cache (Phase 3)

    def has_ingest_cache(
        self,
        *,
        doc_id: str,
        content_hash: str,
        extractor_version: str,
    ) -> Optional[dict]:
        """Cache hit varsa kayıt dict'ini döndürür, yoksa None.

        Hit varsa CLI / pipeline LLM çağrısını atlayabilir. graphify'ın
        SHA256-cache fikrinden adopt — content + extractor kombinasyonu daha
        önce işlendiyse re-run ücretsiz.
        """
        row = self._connect().execute(
            """
            SELECT relations_count, mentions_count, ingested_at
            FROM ingest_cache
            WHERE doc_id = ? AND content_hash = ? AND extractor_version = ?
            """,
            (doc_id, content_hash, extractor_version),
        ).fetchone()
        if row is None:
            return None
        return {
            "relations_count": row["relations_count"],
            "mentions_count": row["mentions_count"],
            "ingested_at": row["ingested_at"],
        }

    def save_ingest_cache(
        self,
        *,
        doc_id: str,
        content_hash: str,
        extractor_version: str,
        relations_count: int = 0,
        mentions_count: int = 0,
    ) -> None:
        """İngest tamamlandığında cache satırı yaz / güncelle."""
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO ingest_cache
                  (doc_id, content_hash, extractor_version,
                   relations_count, mentions_count)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(doc_id, content_hash, extractor_version) DO UPDATE SET
                  ingested_at     = datetime('now'),
                  relations_count = excluded.relations_count,
                  mentions_count  = excluded.mentions_count
                """,
                (doc_id, content_hash, extractor_version, relations_count, mentions_count),
            )

    def clear_ingest_cache(self, *, doc_id: Optional[str] = None) -> int:
        """Cache temizle (doc_id verilirse o belge, yoksa hepsi).

        Returns: silinen satır sayısı.
        """
        with self.transaction() as conn:
            if doc_id is None:
                cur = conn.execute("DELETE FROM ingest_cache")
            else:
                cur = conn.execute("DELETE FROM ingest_cache WHERE doc_id = ?", (doc_id,))
            return cur.rowcount

    # ------------------------------------------------------------------ stats

    def stats(self) -> dict:
        c = self._connect()
        out = {}
        out["entities_total"] = c.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        out["entities_verified"] = c.execute(
            "SELECT COUNT(*) FROM entities WHERE status='verified'"
        ).fetchone()[0]
        out["entities_pending"] = c.execute(
            "SELECT COUNT(*) FROM entities WHERE status='pending'"
        ).fetchone()[0]
        out["entities_auto_added"] = c.execute(
            "SELECT COUNT(*) FROM entities WHERE status='auto_added'"
        ).fetchone()[0]
        out["aliases_total"] = c.execute("SELECT COUNT(*) FROM aliases").fetchone()[0]
        out["documents_processed"] = c.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        by_type = c.execute(
            "SELECT entity_type, COUNT(*) AS n FROM entities GROUP BY entity_type"
        ).fetchall()
        out["by_type"] = {r["entity_type"]: r["n"] for r in by_type}
        by_source = c.execute(
            "SELECT source, COUNT(*) AS n FROM aliases GROUP BY source"
        ).fetchall()
        out["aliases_by_source"] = {r["source"]: r["n"] for r in by_source}
        return out
