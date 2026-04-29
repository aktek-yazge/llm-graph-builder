"""Cypher şablonları + system prompt testleri (Neo4j gerekmez)."""
from __future__ import annotations

import pytest

from kb_overlay.agent import (
    TEMPLATES,
    build_system_prompt,
    get_template,
    list_template_names,
    render_template,
)


def test_all_templates_have_required_fields():
    for name, tpl in TEMPLATES.items():
        assert tpl.name == name
        assert tpl.description
        assert tpl.cypher.strip()
        assert "canonical_id" in tpl.required_params or not tpl.required_params


def test_render_with_canonical_id():
    cypher, params = render_template("ceo_of_company", {"canonical_id": "COMP_abc"})
    assert "$canonical_id" in cypher
    assert params["canonical_id"] == "COMP_abc"
    assert params["limit"] == 25


def test_render_missing_required_param_raises():
    with pytest.raises(ValueError, match="eksik parametre"):
        render_template("ceo_of_company", {})


def test_render_unknown_template_raises():
    with pytest.raises(KeyError):
        render_template("nonexistent", {"canonical_id": "x"})


def test_all_templates_are_read_only():
    """Hiçbir şablon write operation içermemeli (CREATE/MERGE/SET/DELETE)."""
    forbidden = ("MERGE ", "CREATE ", "DELETE ", "SET ", "REMOVE ")
    for name, tpl in TEMPLATES.items():
        upper = tpl.cypher.upper()
        for f in forbidden:
            assert f not in upper, f"Template {name} contains forbidden op: {f}"


def test_all_templates_have_limit_clause():
    for name, tpl in TEMPLATES.items():
        if "MENTIONED_IN" in tpl.cypher and tpl.required_params == ("canonical_id",):
            # entity_by_id şablonu hariç (tek sonuç)
            pass
        if "RETURN" in tpl.cypher.upper():
            # Çoklu sonuç dönenler LIMIT içermeli (entity_by_id hariç)
            if name != "entity_by_id":
                assert "LIMIT" in tpl.cypher.upper(), f"{name} LIMIT yok"


def test_list_template_names_sorted():
    names = list_template_names()
    assert names == sorted(names)
    assert "ceo_of_company" in names


def test_build_system_prompt_includes_resolver_block():
    block = "abc bilişim | COMP_001 | ABC Bilişim A.Ş. | company | 1.00"
    prompt = build_system_prompt(query_resolver_block=block)
    assert "Çözülmüş Entity Tablosu" in prompt
    assert block in prompt
    assert "ceo_of_company" in prompt  # template list


def test_build_system_prompt_with_empty_block():
    prompt = build_system_prompt(query_resolver_block="")
    assert "(boş)" in prompt
