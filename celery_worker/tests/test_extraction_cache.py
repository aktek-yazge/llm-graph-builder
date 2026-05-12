"""Celery worker extraction_cache testleri (graphify-adopted).

Test stratejisi:

1. **Pure helpers** (compute_content_hash, build_extractor_version) — standart
   unit testler, deterministik.

2. **DB-baglantili helpers** (init/check/save/clear) — psycopg mock'u ile gercek
   SQL kompozisyonu + ON CONFLICT semantigi test edilir. Gercek PG gerektirmez.

3. **psycopg yokken graceful degradation** — driver import edilemezse
   tum fonksiyonlar guvenli False/None/0 doner, exception firlatmaz.

Calistirma:
    cd kb-overlay && source .venv/bin/activate
    python -m pytest /Users/.../celery_worker/tests/test_extraction_cache.py -v
"""
from __future__ import annotations

import importlib.util
import os
import sys
from types import ModuleType
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest


HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, "..", "src"))


def _load_extraction_cache() -> ModuleType:
    """`src.extraction_cache`i parent __init__'leri tetiklemeden yukle.

    celery_worker'da `src/__init__.py` celery_app'i import ediyor — ona dokunmak
    istemiyoruz. importlib ile dosya bazli yukleme.
    """
    spec = importlib.util.spec_from_file_location(
        "ec_test_module",
        os.path.join(SRC, "extraction_cache.py"),
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load extraction_cache.py from {SRC}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ec_test_module"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ec():
    return _load_extraction_cache()


# ===========================================================================
# Pure helpers — no DB
# ===========================================================================


class TestComputeContentHash:
    def test_returns_16_char_hex(self, ec):
        h = ec.compute_content_hash("hello world")
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)

    def test_deterministic(self, ec):
        h1 = ec.compute_content_hash("aynı içerik")
        h2 = ec.compute_content_hash("aynı içerik")
        assert h1 == h2

    def test_different_text_different_hash(self, ec):
        h1 = ec.compute_content_hash("foo")
        h2 = ec.compute_content_hash("bar")
        assert h1 != h2

    def test_empty_text_returns_sha256_of_empty(self, ec):
        # Bos text -> SHA-256 of empty string (kb-overlay ile birebir uyumlu)
        empty_sha256_prefix = "e3b0c44298fc1c14"
        assert ec.compute_content_hash("") == empty_sha256_prefix
        assert ec.compute_content_hash(None) == empty_sha256_prefix  # None safe

    def test_kb_overlay_compat(self, ec):
        """Aynı algoritma kb-overlay tarafıyla — cross-system audit garanti."""
        try:
            from kb_overlay.cli.main import _compute_content_hash as kbo_hash
        except ImportError:
            pytest.skip("kb-overlay not installed")
        assert ec.compute_content_hash("test") == kbo_hash("test")
        assert ec.compute_content_hash("Türkçe içerik") == kbo_hash("Türkçe içerik")


class TestBuildExtractorVersion:
    def test_minimal(self, ec):
        v = ec.build_extractor_version()
        assert v == "pipeline=agentic_ocr;schema=1"

    def test_with_domain_and_skill(self, ec):
        v = ec.build_extractor_version(domain="insurance", skill_id="policy_v2")
        assert "pipeline=agentic_ocr" in v
        assert "schema=1" in v
        assert "domain=insurance" in v
        assert "skill=policy_v2" in v

    def test_domain_change_invalidates(self, ec):
        # Domain degisimi -> farkli string -> cache invalidation
        v_a = ec.build_extractor_version(domain="legal", skill_id="contract")
        v_b = ec.build_extractor_version(domain="medical", skill_id="contract")
        assert v_a != v_b

    def test_skill_change_invalidates(self, ec):
        v_a = ec.build_extractor_version(domain="legal", skill_id="contract_v1")
        v_b = ec.build_extractor_version(domain="legal", skill_id="contract_v2")
        assert v_a != v_b

    def test_schema_bump_invalidates(self, ec):
        v_a = ec.build_extractor_version(schema_version="1")
        v_b = ec.build_extractor_version(schema_version="2")
        assert v_a != v_b


# ===========================================================================
# DB-bagli helpers — psycopg mock
# ===========================================================================


def _make_mock_psycopg(fetched_row: Optional[tuple] = None, rowcount: int = 0) -> MagicMock:
    """Ortak psycopg mock — connect/cursor/execute/fetchone/rowcount sirasi."""
    mock_psycopg = MagicMock()

    cursor_mock = MagicMock()
    cursor_mock.__enter__ = MagicMock(return_value=cursor_mock)
    cursor_mock.__exit__ = MagicMock(return_value=False)
    cursor_mock.execute = MagicMock()
    cursor_mock.fetchone = MagicMock(return_value=fetched_row)
    cursor_mock.rowcount = rowcount

    conn_mock = MagicMock()
    conn_mock.__enter__ = MagicMock(return_value=conn_mock)
    conn_mock.__exit__ = MagicMock(return_value=False)
    conn_mock.cursor = MagicMock(return_value=cursor_mock)

    mock_psycopg.connect = MagicMock(return_value=conn_mock)
    mock_psycopg._cursor_mock = cursor_mock
    mock_psycopg._conn_mock = conn_mock
    return mock_psycopg


class TestSchemaInit:
    def test_creates_table_and_indexes(self, ec):
        mock_pg = _make_mock_psycopg()
        with patch.dict(sys.modules, {"psycopg": mock_pg}):
            ok = ec.init_extraction_cache_schema("dsn://test")
        assert ok is True
        # 3 SQL: CREATE TABLE + 2 CREATE INDEX
        executed = [call.args[0] for call in mock_pg._cursor_mock.execute.call_args_list]
        assert any("CREATE TABLE IF NOT EXISTS extraction_cache" in s for s in executed)
        assert any("CREATE INDEX IF NOT EXISTS idx_extraction_cache_doc" in s for s in executed)
        assert any("CREATE INDEX IF NOT EXISTS idx_extraction_cache_agent" in s for s in executed)

    def test_returns_false_when_psycopg_missing(self, ec):
        # ImportError simulasyonu
        with patch.dict(sys.modules, {"psycopg": None}):
            with patch("builtins.__import__", side_effect=ImportError("no psycopg")):
                ok = ec.init_extraction_cache_schema("dsn://test")
        assert ok is False


class TestCheckCache:
    def test_miss_returns_none(self, ec):
        mock_pg = _make_mock_psycopg(fetched_row=None)
        with patch.dict(sys.modules, {"psycopg": mock_pg}):
            result = ec.check_extraction_cache(
                "dsn://test", doc_id="D1", content_hash="abcd1234", extractor_version="v1",
            )
        assert result is None

    def test_hit_returns_full_dict(self, ec):
        # fetchone -> (nodes_count, rels_count, extraction_result, agent, batch, created, updated)
        cached_payload = {"nodes": [{"id": "N1"}], "relationships": []}
        mock_pg = _make_mock_psycopg(
            fetched_row=(5, 2, cached_payload, "agent-1", "batch-1", "2026-04-29", "2026-04-29"),
        )
        with patch.dict(sys.modules, {"psycopg": mock_pg}):
            result = ec.check_extraction_cache(
                "dsn://test", doc_id="D1", content_hash="abcd1234", extractor_version="v1",
            )
        assert result is not None
        assert result["nodes_count"] == 5
        assert result["relationships_count"] == 2
        assert result["extraction_result"] == cached_payload
        assert result["agent_id"] == "agent-1"
        assert result["batch_id"] == "batch-1"

    def test_correct_where_clause_params(self, ec):
        mock_pg = _make_mock_psycopg(fetched_row=None)
        with patch.dict(sys.modules, {"psycopg": mock_pg}):
            ec.check_extraction_cache(
                "dsn://test",
                doc_id="DOC-X", content_hash="H123", extractor_version="V1",
            )
        call = mock_pg._cursor_mock.execute.call_args
        sql, params = call.args
        assert "WHERE doc_id=%s AND content_hash=%s AND extractor_version=%s" in sql
        assert params == ("DOC-X", "H123", "V1")


class TestSaveCache:
    def test_upsert_called_with_correct_params(self, ec):
        mock_pg = _make_mock_psycopg()
        payload = {
            "nodes": [{"id": "n1"}, {"id": "n2"}],
            "relationships": [{"src": "n1", "tgt": "n2"}],
            "confidence_score": 0.85,
        }
        with patch.dict(sys.modules, {"psycopg": mock_pg}):
            ok = ec.save_extraction_cache(
                "dsn://test",
                doc_id="D1", content_hash="H", extractor_version="V",
                extraction_result=payload,
                agent_id="ag", batch_id="bt",
            )
        assert ok is True
        call = mock_pg._cursor_mock.execute.call_args
        sql, params = call.args
        assert "INSERT INTO extraction_cache" in sql
        assert "ON CONFLICT (doc_id, content_hash, extractor_version) DO UPDATE" in sql
        # params: (doc_id, hash, ver, agent, batch, nodes_count, rels_count, payload_json)
        assert params[0] == "D1"
        assert params[1] == "H"
        assert params[2] == "V"
        assert params[3] == "ag"
        assert params[4] == "bt"
        assert params[5] == 2  # nodes_count
        assert params[6] == 1  # rels_count
        # JSON payload
        import json
        decoded = json.loads(params[7])
        assert decoded["nodes"] == payload["nodes"]
        assert decoded["confidence_score"] == 0.85

    def test_handles_relations_alias(self, ec):
        """Bazi caller'lar 'relations' bazi'lari 'relationships' kullaniyor."""
        mock_pg = _make_mock_psycopg()
        payload = {"nodes": [], "relations": [{"a": 1}, {"b": 2}, {"c": 3}]}
        with patch.dict(sys.modules, {"psycopg": mock_pg}):
            ec.save_extraction_cache(
                "dsn://test",
                doc_id="D", content_hash="H", extractor_version="V",
                extraction_result=payload,
            )
        params = mock_pg._cursor_mock.execute.call_args.args[1]
        # 6=nodes_count, 7=rels_count
        assert params[5] == 0
        assert params[6] == 3, "should fall back to 'relations' key when 'relationships' missing"


class TestClearCache:
    def test_clear_by_doc_id_only(self, ec):
        mock_pg = _make_mock_psycopg(rowcount=3)
        with patch.dict(sys.modules, {"psycopg": mock_pg}):
            n = ec.clear_extraction_cache("dsn://test", doc_id="DOC-1")
        assert n == 3
        sql, params = mock_pg._cursor_mock.execute.call_args.args
        assert "DELETE FROM extraction_cache" in sql
        assert "WHERE doc_id = %s" in sql
        assert params == ["DOC-1"]

    def test_clear_by_agent_id_only(self, ec):
        mock_pg = _make_mock_psycopg(rowcount=12)
        with patch.dict(sys.modules, {"psycopg": mock_pg}):
            n = ec.clear_extraction_cache("dsn://test", agent_id="AGT-7")
        assert n == 12
        sql, params = mock_pg._cursor_mock.execute.call_args.args
        assert "WHERE agent_id = %s" in sql
        assert params == ["AGT-7"]

    def test_clear_by_both_filters(self, ec):
        mock_pg = _make_mock_psycopg(rowcount=1)
        with patch.dict(sys.modules, {"psycopg": mock_pg}):
            n = ec.clear_extraction_cache("dsn://test", doc_id="D", agent_id="A")
        assert n == 1
        sql = mock_pg._cursor_mock.execute.call_args.args[0]
        assert "WHERE doc_id = %s AND agent_id = %s" in sql

    def test_clear_all_with_no_filter(self, ec):
        mock_pg = _make_mock_psycopg(rowcount=42)
        with patch.dict(sys.modules, {"psycopg": mock_pg}):
            n = ec.clear_extraction_cache("dsn://test")
        assert n == 42
        sql, params = mock_pg._cursor_mock.execute.call_args.args
        assert sql == "DELETE FROM extraction_cache"
        assert params == []


# ===========================================================================
# Cross-system wire compat
# ===========================================================================


class TestKbOverlayWireCompat:
    """celery_worker ↔ kb-overlay arasında SHA-256 algoritması özdeş olmalı."""

    def test_same_hash_for_same_text(self, ec):
        try:
            from kb_overlay.cli.main import _compute_content_hash as kbo_hash
        except ImportError:
            pytest.skip("kb-overlay not installed")

        for sample in ["", "ascii", "Türkçe Şirket A.Ş.", "中文测试", "x" * 10000]:
            assert ec.compute_content_hash(sample) == kbo_hash(sample), (
                f"hash mismatch for sample: {sample[:30]!r}"
            )

    def test_extractor_version_format_distinct_from_kb_overlay(self, ec):
        """celery_worker = pipeline-based; kb-overlay = ner-based.

        Iki tarafın version string'leri farklı schemada (her bir kendi
        domain'ine özel) — bu kasıtlı, çünkü extractor'lar tamamen farklı
        runtime'larda. Cache'ler ayrı tablolarda yaşıyor zaten.
        """
        try:
            from kb_overlay.cli.main import _build_extractor_version as kbo_ver
        except ImportError:
            pytest.skip("kb-overlay not installed")

        celery_ver = ec.build_extractor_version(domain="ins", skill_id="s1")
        kbo_ver_str = kbo_ver(ner_name="naive", llm_provider=None, llm_model=None)
        assert "pipeline=" in celery_ver
        assert "ner=" in kbo_ver_str
        assert celery_ver != kbo_ver_str
