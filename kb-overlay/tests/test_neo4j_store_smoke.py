"""Neo4j store smoke testleri.

Sadece neo4j paketi yüklüyse VE çalışan bir Neo4j varsa çalıştırılır.
Aksi halde otomatik atlanır.

Çalıştırmak için:
    docker run -d --rm --name neo4j-test \
        -p 7687:7687 -p 7474:7474 \
        -e NEO4J_AUTH=neo4j/testpass1 \
        neo4j:5

    KBO_NEO4J_PASS=testpass1 pytest tests/test_neo4j_store_smoke.py
"""
from __future__ import annotations

import os

import pytest

neo4j_pkg = pytest.importorskip("neo4j")  # noqa: F841

from kb_overlay.dictionary import EntityRecord
from kb_overlay.neo4j_store import GraphRelation, Neo4jConfig, Neo4jStore


def _config_from_env() -> Neo4jConfig:
    return Neo4jConfig(
        uri=os.environ.get("KBO_NEO4J_URI", "bolt://localhost:7687"),
        user=os.environ.get("KBO_NEO4J_USER", "neo4j"),
        password=os.environ.get("KBO_NEO4J_PASS", "neo4j"),
        database=os.environ.get("KBO_NEO4J_DB", "neo4j"),
        create_vector_index=False,  # smoke için kapalı (5.13+ gerekiyor)
    )


@pytest.fixture(scope="module")
def graph():
    cfg = _config_from_env()
    try:
        store = Neo4jStore(cfg)
        if not store.ping():
            pytest.skip("Neo4j ping başarısız")
    except Exception as exc:
        pytest.skip(f"Neo4j erişilemedi: {exc}")
    store.init_schema()
    yield store
    # Cleanup: test entity'lerini sil
    try:
        with store._session() as s:
            s.run("MATCH (e:Entity {canonical_id: $cid}) DETACH DELETE e", cid="TEST_COMP_001")
            s.run("MATCH (e:Entity {canonical_id: $cid}) DETACH DELETE e", cid="TEST_PERS_001")
            s.run("MATCH (d:Document {doc_id: $did}) DETACH DELETE d", did="TEST_DOC_001")
    except Exception:
        pass
    store.close()


def test_upsert_entity_idempotent(graph):
    ent = EntityRecord(
        canonical_id="TEST_COMP_001",
        canonical_name="Test Company A.Ş.",
        entity_type="company",
        norm_strict="test company",
        norm_loose="test company",
        status="verified",
        source="test",
    )
    graph.upsert_entity(ent, aliases=["Test Co", "TestCorp"])
    graph.upsert_entity(ent, aliases=["Test Co", "TestCorp"])  # idempotent
    found = graph.find_by_canonical_id("TEST_COMP_001")
    assert found is not None
    assert found["canonical_name"] == "Test Company A.Ş."


def test_link_mention_creates_document(graph):
    ent = EntityRecord(
        canonical_id="TEST_COMP_001",
        canonical_name="Test Company A.Ş.",
        entity_type="company",
        norm_strict="test company",
        norm_loose="test company",
        status="verified",
        source="test",
    )
    graph.upsert_entity(ent)
    graph.link_mention(
        canonical_id="TEST_COMP_001",
        doc_id="TEST_DOC_001",
        span_start=0,
        span_end=12,
        surface="Test Company",
        confidence=0.95,
    )
    rows = graph.execute_cypher(
        "MATCH (e:Entity {canonical_id: $cid})-[m:MENTIONED_IN]->(d:Document) RETURN d.doc_id AS doc_id",
        {"cid": "TEST_COMP_001"},
    )
    assert any(r["doc_id"] == "TEST_DOC_001" for r in rows)


def test_upsert_relation_with_provenance(graph):
    for ent in [
        EntityRecord("TEST_COMP_001", "Test Company A.Ş.", "company", "test company", "test company"),
        EntityRecord("TEST_PERS_001", "Test Kişi", "person", "test kisi", "test kisi"),
    ]:
        graph.upsert_entity(ent)

    graph.upsert_relation(
        GraphRelation(
            subject_id="TEST_PERS_001",
            predicate="CEO_OF",
            object_id="TEST_COMP_001",
            source_doc_id="TEST_DOC_001",
            evidence_text="Test Kişi, Test Company'nin CEO'sudur.",
            confidence=0.92,
            span_start=0,
            span_end=50,
        )
    )

    rows = graph.execute_cypher(
        "MATCH (p:Entity {canonical_id: $pid})-[r:CEO_OF]->(c:Entity {canonical_id: $cid}) "
        "RETURN r.confidence AS conf, r.source_doc_id AS doc",
        {"pid": "TEST_PERS_001", "cid": "TEST_COMP_001"},
    )
    assert rows
    assert rows[0]["doc"] == "TEST_DOC_001"
    assert rows[0]["conf"] == pytest.approx(0.92)


def test_execute_cypher_rejects_writes(graph):
    with pytest.raises(ValueError, match="salt-okunur"):
        graph.execute_cypher("CREATE (:X {a:1}) RETURN 1", {})
    with pytest.raises(ValueError, match="salt-okunur"):
        graph.execute_cypher("MATCH (n) DELETE n", {})
