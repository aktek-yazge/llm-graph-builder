"""Bootstrap script — CSV → SQLite seeding testleri."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_bootstrap_module():
    """scripts/bootstrap_kb.py modülünü dinamik olarak yükle."""
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "bootstrap_kb.py"
    spec = importlib.util.spec_from_file_location("bootstrap_kb", script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def bootstrap_mod():
    return _load_bootstrap_module()


def test_stripped_company_variant_removes_turkish_suffix(bootstrap_mod):
    s = bootstrap_mod._stripped_company_variant("ABC Bilişim Teknolojileri A.Ş.")
    assert s and "A.Ş." not in s
    assert "ABC Bilişim Teknolojileri" in s


def test_stripped_company_variant_removes_english_suffix(bootstrap_mod):
    s = bootstrap_mod._stripped_company_variant("Microsoft Corporation")
    assert s and "Corporation" not in s
    assert s == "Microsoft"


def test_stripped_returns_none_when_nothing_to_strip(bootstrap_mod):
    s = bootstrap_mod._stripped_company_variant("Microsoft")
    # suffix yok, değişmez → None
    assert s is None


def test_parse_aliases_pipe_delimited(bootstrap_mod):
    out = bootstrap_mod._parse_aliases("X|Y|  Z  | ")
    assert out == ["X", "Y", "Z"]


def test_parse_aliases_empty(bootstrap_mod):
    assert bootstrap_mod._parse_aliases("") == []


def test_parse_metadata_valid_json(bootstrap_mod):
    out = bootstrap_mod._parse_metadata('{"sector":"IT","year":2025}')
    assert out == {"sector": "IT", "year": 2025}


def test_parse_metadata_invalid_returns_empty(bootstrap_mod):
    assert bootstrap_mod._parse_metadata("not-json") == {}


def test_bootstrap_from_csv(tmp_path, bootstrap_mod, store):
    csv_path = tmp_path / "seed.csv"
    csv_path.write_text(
        "name,type,aliases,metadata\n"
        'ABC Bilişim Teknolojileri A.Ş.,company,ABC Bilişim|ABC BT,{"sector":"IT"}\n'
        "Mehmet Yılmaz,person,M. Yılmaz,\n",
        encoding="utf-8",
    )
    counts = bootstrap_mod.bootstrap_from_csv(csv_path, store=store)
    assert counts["rows"] == 2
    assert counts["entities_created"] == 2
    assert counts["skipped"] == 0

    # Lookup ile doğrula
    hits = store.lookup("ABC Bilişim", kind="company")
    assert hits, "ABC Bilişim alias eklenmemiş"
    canonical_id = hits[0][0].canonical_id
    entity = store.get_entity(canonical_id)
    assert entity is not None
    assert entity.canonical_name == "ABC Bilişim Teknolojileri A.Ş."
    assert entity.metadata.get("sector") == "IT"


def test_bootstrap_skips_blank_rows(tmp_path, bootstrap_mod, store):
    csv_path = tmp_path / "seed_with_blanks.csv"
    csv_path.write_text(
        "name,type,aliases,metadata\n"
        ",company,,\n"
        "ABC,company,,\n"
        "Person Y,,,\n",
        encoding="utf-8",
    )
    counts = bootstrap_mod.bootstrap_from_csv(csv_path, store=store)
    assert counts["entities_created"] == 1
    assert counts["skipped"] == 2


def test_bootstrap_auto_strip_adds_suffix_free_alias(tmp_path, bootstrap_mod, store):
    csv_path = tmp_path / "seed_strip.csv"
    csv_path.write_text(
        "name,type,aliases,metadata\n"
        "Microsoft Corporation,company,,\n",
        encoding="utf-8",
    )
    bootstrap_mod.bootstrap_from_csv(csv_path, store=store, auto_strip_suffix=True)
    hits = store.lookup("Microsoft", kind="company")
    assert hits, "Suffix-stripped alias 'Microsoft' eklenmemiş"
