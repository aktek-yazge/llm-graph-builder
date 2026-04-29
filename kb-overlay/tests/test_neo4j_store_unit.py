"""Neo4jStore — Neo4j gerektirmeyen birim testler.

Sadece pure-python helper'lar (rel-type whitelist, schema parser, lucene escape).
"""
from __future__ import annotations

import pytest

neo4j_pkg = pytest.importorskip("neo4j")  # noqa: F841

from kb_overlay.neo4j_store import ALLOWED_REL_TYPES
from kb_overlay.neo4j_store.store import Neo4jStore, _escape_lucene, _label_for_type, _safe_rel_type


def test_safe_rel_type_whitelist_passes():
    rel, original = _safe_rel_type("CEO_OF")
    assert rel == "CEO_OF"
    assert original is None


def test_safe_rel_type_normalizes_lowercase():
    rel, original = _safe_rel_type("ceo_of")
    assert rel == "CEO_OF"
    assert original is None


def test_safe_rel_type_unknown_falls_back_to_related_to():
    rel, original = _safe_rel_type("Müşteri")
    assert rel == "RELATED_TO"
    assert original == "Müşteri"


def test_safe_rel_type_with_spaces():
    rel, original = _safe_rel_type("subsidiary of")
    assert rel == "SUBSIDIARY_OF"
    assert original is None


def test_label_for_type_known():
    assert _label_for_type("company") == "Company"
    assert _label_for_type("person") == "Person"
    assert _label_for_type("organization") == "Organization"


def test_label_for_type_unknown_falls_to_other():
    assert _label_for_type("alien-species") == "Other"


def test_escape_lucene_basic():
    assert _escape_lucene("hello") == "hello"


def test_escape_lucene_special_chars():
    out = _escape_lucene("foo+bar:baz")
    assert "\\+" in out
    assert "\\:" in out


def test_split_statements_handles_multiple():
    cypher = """
    CREATE CONSTRAINT a IF NOT EXISTS FOR (n:X) REQUIRE n.id IS UNIQUE;
    // comment line
    CREATE INDEX b IF NOT EXISTS FOR (n:X) ON (n.foo);
    """
    stmts = Neo4jStore._split_statements(cypher)
    assert len(stmts) == 2
    assert all("CREATE" in s for s in stmts)


def test_allowed_rel_types_includes_core_business_predicates():
    assert "CEO_OF" in ALLOWED_REL_TYPES
    assert "WORKS_AT" in ALLOWED_REL_TYPES
    assert "SUBSIDIARY_OF" in ALLOWED_REL_TYPES
    assert "MENTIONED_IN" in ALLOWED_REL_TYPES
