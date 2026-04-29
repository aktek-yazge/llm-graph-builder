"""Pytest fixtures."""
from __future__ import annotations

from pathlib import Path

import pytest

from kb_overlay.dictionary import AliasStore, EntityType
from kb_overlay.review import ReviewQueue


@pytest.fixture()
def store(tmp_path: Path) -> AliasStore:
    db_path = tmp_path / "test_aliases.db"
    s = AliasStore(db_path)
    s.init_schema()
    yield s
    s.close()


@pytest.fixture()
def review_queue(store: AliasStore) -> ReviewQueue:
    return ReviewQueue(store)


@pytest.fixture()
def seeded_store(store: AliasStore) -> AliasStore:
    """Birkaç varlık + alias ile önceden doldurulmuş store."""
    store.create_entity(
        canonical_name="ABC Bilişim Teknolojileri A.Ş.",
        entity_type=EntityType.COMPANY,
        seed_aliases=[
            "ABC Bilişim Teknolojileri A.Ş.",
            "ABC Bilişim",
            "ABC Bilişim A.Ş.",
            "ABC Bilişim Anonim Şirketi",
            "ABC BT",
        ],
        canonical_id="COMP_abc001",
    )
    store.create_entity(
        canonical_name="Türkiye İş Bankası A.Ş.",
        entity_type=EntityType.COMPANY,
        seed_aliases=[
            "Türkiye İş Bankası A.Ş.",
            "İş Bankası",
            "İşbank",
            "TEB",
        ],
        canonical_id="COMP_isbank01",
    )
    store.create_entity(
        canonical_name="Mehmet Yılmaz",
        entity_type=EntityType.PERSON,
        seed_aliases=["Mehmet Yılmaz", "M. Yılmaz"],
        canonical_id="PERS_mehyl001",
    )
    return store
