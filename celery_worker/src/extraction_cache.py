"""extraction_cache — Celery worker icin content-hash cache (graphify-adopted).

`workspace.extract_entities` task'inin, ayni dosya + ayni extractor
konfigurasyonu daha once islendiyse LLM cagrisini ATLAMASI icin SHA-256 tabanli
cache. Cache key:

    (doc_id, sha256(content)[:16], extractor_version)

`extractor_version` = `agentic_ocr;domain=...;skill=...;schema=1`
NER backend / LLM model / domain / skill_id degisimi cache'i otomatik
invalidate eder.

Tasarim simetrik kb-overlay'in `kb_overlay/dictionary/alias_store.py`
`ingest_cache` tablosu ile. Iki taraf da ayni patern + ayni SQL semantigi
(PRIMARY KEY composite, UPSERT, optional agent_id filter).

API:
    init_extraction_cache_schema(dsn)
    check_extraction_cache(dsn, doc_id, content_hash, extractor_version) -> dict | None
    save_extraction_cache(dsn, doc_id, content_hash, extractor_version, ...)
    clear_extraction_cache(dsn, *, doc_id=None, agent_id=None) -> int
    compute_content_hash(text) -> str
    build_extractor_version(*, domain, skill_id, schema_version='1') -> str

Gevsek baglanti — psycopg lazy import (kullanim sirasinda yuklenir), test'lerde
in-memory SQLite ile de calisabilmek icin SQL standard tutulmustur.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

CACHE_SCHEMA_VERSION = "1"
"""Schema major sürümü; tablo şeması değişirse bump'lanır ve eski cache invalid sayılır."""


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS extraction_cache (
    doc_id              TEXT NOT NULL,
    content_hash        TEXT NOT NULL,
    extractor_version   TEXT NOT NULL,
    agent_id            TEXT,
    batch_id            TEXT,
    nodes_count         INTEGER DEFAULT 0,
    relationships_count INTEGER DEFAULT 0,
    extraction_result   JSONB,
    created_at          TIMESTAMP DEFAULT NOW(),
    updated_at          TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (doc_id, content_hash, extractor_version)
)
"""

_CREATE_INDEX_DOC = """
CREATE INDEX IF NOT EXISTS idx_extraction_cache_doc
    ON extraction_cache(doc_id)
"""

_CREATE_INDEX_AGENT = """
CREATE INDEX IF NOT EXISTS idx_extraction_cache_agent
    ON extraction_cache(agent_id)
"""


def init_extraction_cache_schema(dsn: str) -> bool:
    """Cache tablosunu (yoksa) yarat. Worker startup'ta veya ilk task'ta cagrilir.

    Returns:
        True yarat/var, False driver yoksa veya hata.
    """
    try:
        import psycopg
    except ImportError:
        logger.warning("psycopg yok; extraction_cache devre disi")
        return False

    try:
        with psycopg.connect(dsn, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(_CREATE_TABLE_SQL)
                cur.execute(_CREATE_INDEX_DOC)
                cur.execute(_CREATE_INDEX_AGENT)
        return True
    except Exception as exc:  # pragma: no cover — connection sorunu canli ortamda gorulur
        logger.warning("extraction_cache schema init failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Lookup / Save / Clear
# ---------------------------------------------------------------------------


def check_extraction_cache(
    dsn: str,
    *,
    doc_id: str,
    content_hash: str,
    extractor_version: str,
) -> Optional[dict]:
    """Cache hit varsa kayit dict'ini dondurur (extraction_result dahil), yoksa None.

    extraction_result alani None olabilir (eski kayitlarda); o zaman caller
    cache'i bypass etmeli (sadece "islenmisti" bilgisi yeterli degil).
    """
    try:
        import psycopg
    except ImportError:
        return None

    sql = """
        SELECT nodes_count, relationships_count, extraction_result,
               agent_id, batch_id, created_at, updated_at
        FROM extraction_cache
        WHERE doc_id=%s AND content_hash=%s AND extractor_version=%s
    """
    try:
        with psycopg.connect(dsn, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (doc_id, content_hash, extractor_version))
                row = cur.fetchone()
                if row is None:
                    return None
                return {
                    "nodes_count": row[0],
                    "relationships_count": row[1],
                    "extraction_result": row[2],  # psycopg dict olarak okur
                    "agent_id": row[3],
                    "batch_id": row[4],
                    "created_at": row[5],
                    "updated_at": row[6],
                }
    except Exception as exc:  # pragma: no cover
        logger.warning("check_extraction_cache failed for %s: %s", doc_id, exc)
        return None


def save_extraction_cache(
    dsn: str,
    *,
    doc_id: str,
    content_hash: str,
    extractor_version: str,
    extraction_result: dict[str, Any],
    agent_id: str = "",
    batch_id: str = "",
) -> bool:
    """Basarili extraction sonrasi cache'e UPSERT.

    `extraction_result` ayni anahtarlarla yeniden kayit edilirse (re-extract)
    sayilar ve payload guncellenir; created_at korunur, updated_at NOW().
    """
    try:
        import psycopg
    except ImportError:
        return False

    nodes = extraction_result.get("nodes") or []
    rels = extraction_result.get("relationships") or extraction_result.get("relations") or []

    sql = """
        INSERT INTO extraction_cache
          (doc_id, content_hash, extractor_version, agent_id, batch_id,
           nodes_count, relationships_count, extraction_result)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        ON CONFLICT (doc_id, content_hash, extractor_version) DO UPDATE SET
          agent_id            = EXCLUDED.agent_id,
          batch_id            = EXCLUDED.batch_id,
          nodes_count         = EXCLUDED.nodes_count,
          relationships_count = EXCLUDED.relationships_count,
          extraction_result   = EXCLUDED.extraction_result,
          updated_at          = NOW()
    """
    try:
        payload_json = json.dumps(extraction_result, ensure_ascii=False, default=str)
        with psycopg.connect(dsn, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    (
                        doc_id,
                        content_hash,
                        extractor_version,
                        agent_id or "",
                        batch_id or "",
                        len(nodes),
                        len(rels),
                        payload_json,
                    ),
                )
        return True
    except Exception as exc:  # pragma: no cover
        logger.warning("save_extraction_cache failed for %s: %s", doc_id, exc)
        return False


def clear_extraction_cache(
    dsn: str,
    *,
    doc_id: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> int:
    """Cache temizle (filter kombinasyonuyla). Returns silinen satir sayisi.

    Hem `doc_id` hem `agent_id` None ise: TUM cache'i siler (dikkat).
    """
    try:
        import psycopg
    except ImportError:
        return 0

    where = []
    params: list = []
    if doc_id is not None:
        where.append("doc_id = %s")
        params.append(doc_id)
    if agent_id is not None:
        where.append("agent_id = %s")
        params.append(agent_id)

    sql = "DELETE FROM extraction_cache"
    if where:
        sql += " WHERE " + " AND ".join(where)

    try:
        with psycopg.connect(dsn, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.rowcount
    except Exception as exc:  # pragma: no cover
        logger.warning("clear_extraction_cache failed: %s", exc)
        return 0


# ---------------------------------------------------------------------------
# Cache key helpers (hash + version) — pure, no DB
# ---------------------------------------------------------------------------


def compute_content_hash(text: str) -> str:
    """Content hash — SHA-256 ilk 16 hex char.

    kb-overlay `cli/main._compute_content_hash` ile birebir ayni algoritma;
    iki taraf ayni text icin ayni hash uretir (cross-system audit kolayligi).

    Bos input'ta SHA-256 of empty string ("e3b0c44...") doner — kb-overlay
    ile uyumlu. None ise empty string sayilir.
    """
    safe_text = text or ""
    return hashlib.sha256(safe_text.encode("utf-8", errors="ignore")).hexdigest()[:16]


def build_extractor_version(
    *,
    domain: str = "",
    skill_id: str = "",
    pipeline: str = "agentic_ocr",
    schema_version: str = CACHE_SCHEMA_VERSION,
) -> str:
    """Stabil extractor identity string'i — degisirse cache invalidate olur.

    Format: "pipeline=...;schema=...;domain=...;skill=..."

    Domain veya skill_id degisimi cache invalidation tetikler — cunku bu degerler
    `process_agentic_ocr_text_mode` cagrisina prompt-shape farki yansitir
    (entity tipleri, kurallar farklilasir).
    """
    parts = [f"pipeline={pipeline}", f"schema={schema_version}"]
    if domain:
        parts.append(f"domain={domain}")
    if skill_id:
        parts.append(f"skill={skill_id}")
    return ";".join(parts)


__all__ = [
    "CACHE_SCHEMA_VERSION",
    "build_extractor_version",
    "check_extraction_cache",
    "clear_extraction_cache",
    "compute_content_hash",
    "init_extraction_cache_schema",
    "save_extraction_cache",
]
