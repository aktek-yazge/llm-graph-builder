"""Phase 3 testleri — SHA-256 content-hash ingest cache.

graphify-adopted: aynı dosya + aynı extractor konfigürasyonu daha önce işlendiyse
LLM çağrısı atlanır. Cache key: ``(doc_id, content_hash, extractor_version)``.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from kb_overlay.cli.main import _build_extractor_version, _compute_content_hash
from kb_overlay.dictionary import AliasStore


@pytest.fixture
def store():
    """Temp SQLite store ile yeni schema (Phase 3 ingest_cache tablosu dahil)."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        s = AliasStore(db_path)
        s.init_schema()
        yield s
        s.close()


class TestSchemaMigration:
    def test_ingest_cache_table_exists(self, store):
        rows = store._connect().execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='ingest_cache'"
        ).fetchall()
        assert len(rows) == 1, "ingest_cache table should be created by init_schema"

    def test_schema_version_v2(self, store):
        row = store._connect().execute(
            "SELECT value FROM _schema_meta WHERE key='version'"
        ).fetchone()
        assert row["value"] == "2"


class TestCacheLookupAndSave:
    def test_miss_returns_none(self, store):
        assert store.has_ingest_cache(
            doc_id="D1", content_hash="abcd", extractor_version="v1",
        ) is None

    def test_save_then_hit(self, store):
        store.save_ingest_cache(
            doc_id="D1", content_hash="abcd", extractor_version="v1",
            mentions_count=5, relations_count=2,
        )
        hit = store.has_ingest_cache(
            doc_id="D1", content_hash="abcd", extractor_version="v1",
        )
        assert hit is not None
        assert hit["mentions_count"] == 5
        assert hit["relations_count"] == 2
        assert "ingested_at" in hit

    def test_idempotent_save_updates_counts(self, store):
        store.save_ingest_cache(
            doc_id="D1", content_hash="abcd", extractor_version="v1",
            mentions_count=5, relations_count=2,
        )
        store.save_ingest_cache(
            doc_id="D1", content_hash="abcd", extractor_version="v1",
            mentions_count=10, relations_count=4,
        )
        hit = store.has_ingest_cache(
            doc_id="D1", content_hash="abcd", extractor_version="v1",
        )
        assert hit["mentions_count"] == 10
        assert hit["relations_count"] == 4


class TestCacheInvalidation:
    """Key triple'ından herhangi biri değişirse MISS dönmeli."""

    def test_different_doc_id_misses(self, store):
        store.save_ingest_cache(
            doc_id="D1", content_hash="abcd", extractor_version="v1",
        )
        assert store.has_ingest_cache(
            doc_id="D2", content_hash="abcd", extractor_version="v1",
        ) is None

    def test_different_content_hash_misses(self, store):
        store.save_ingest_cache(
            doc_id="D1", content_hash="abcd", extractor_version="v1",
        )
        assert store.has_ingest_cache(
            doc_id="D1", content_hash="WXYZ", extractor_version="v1",
        ) is None

    def test_different_extractor_version_misses(self, store):
        # Kullanıcı NER backend güncellediğinde tüm eski cache invalidate olmalı
        store.save_ingest_cache(
            doc_id="D1", content_hash="abcd", extractor_version="ner=naive;schema=2",
        )
        assert store.has_ingest_cache(
            doc_id="D1", content_hash="abcd", extractor_version="ner=llm;schema=2",
        ) is None


class TestCacheClear:
    def test_clear_by_doc_id(self, store):
        store.save_ingest_cache(doc_id="D1", content_hash="a", extractor_version="v1")
        store.save_ingest_cache(doc_id="D2", content_hash="b", extractor_version="v1")
        n = store.clear_ingest_cache(doc_id="D1")
        assert n == 1
        assert store.has_ingest_cache(
            doc_id="D1", content_hash="a", extractor_version="v1"
        ) is None
        assert store.has_ingest_cache(
            doc_id="D2", content_hash="b", extractor_version="v1"
        ) is not None

    def test_clear_all(self, store):
        store.save_ingest_cache(doc_id="D1", content_hash="a", extractor_version="v1")
        store.save_ingest_cache(doc_id="D2", content_hash="b", extractor_version="v1")
        n = store.clear_ingest_cache()
        assert n == 2
        assert store.has_ingest_cache(
            doc_id="D1", content_hash="a", extractor_version="v1"
        ) is None

    def test_clear_returns_zero_for_missing_doc(self, store):
        assert store.clear_ingest_cache(doc_id="nonexistent") == 0


class TestCliHelpers:
    """CLI'daki cache key oluşturma helper'larının deterministik olduğunu doğrular."""

    def test_content_hash_is_deterministic(self):
        h1 = _compute_content_hash("hello world")
        h2 = _compute_content_hash("hello world")
        assert h1 == h2
        assert len(h1) == 16

    def test_content_hash_changes_with_content(self):
        h1 = _compute_content_hash("hello world")
        h2 = _compute_content_hash("hello world!")
        assert h1 != h2

    def test_extractor_version_naive(self):
        v = _build_extractor_version(ner_name="naive", llm_provider=None, llm_model=None)
        assert v == "ner=naive;schema=2"

    def test_extractor_version_with_llm(self):
        v = _build_extractor_version(
            ner_name="llm", llm_provider="ollama", llm_model="cosmos-gemma:9b",
        )
        assert "ner=llm" in v
        assert "llm=ollama" in v
        assert "model=cosmos-gemma:9b" in v
        assert "schema=2" in v

    def test_extractor_version_changes_invalidate_cache(self):
        # Bu critical: model güncellemesi cache invalidation tetiklemeli
        v_old = _build_extractor_version(
            ner_name="llm", llm_provider="ollama", llm_model="cosmos-gemma:9b",
        )
        v_new = _build_extractor_version(
            ner_name="llm", llm_provider="ollama", llm_model="qwen2.5:14b",
        )
        assert v_old != v_new
