"""LlmNerBackend için birim testler.

Gerçek LLM çağırmaz — fake provider kullanır. Asıl test ettiğimiz şeyler:
  - LLM cevabını Pydantic doğrulayıp Mention'a çeviriyor mu
  - Span recovery (metinde bulamadığı surface'ı atlıyor mu / doğru offset
    veriyor mu)
  - Long text chunking offset'leri global text'e doğru map ediyor mu
  - entity_type normalize ediliyor mu (bilinmeyen → 'unknown', org+suffix → company)
  - Düşük confidence eleniyor mu
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from kb_overlay.discovery.llm_ner import (
    LlmEntity,
    LlmEntityResponse,
    LlmNerBackend,
    _dedup_by_span,
)
from kb_overlay.discovery.ner import Mention


# ---------------------------------------------------------------------------
# Fake provider — LLM çağrısını simüle eder
# ---------------------------------------------------------------------------


@dataclass
class FakeProvider:
    """LLM yerine sabit cevaplar dönen provider.

    `responses` list'i her chunk çağrısında sırayla kullanılır.
    Her response bir LlmEntityResponse dict'i.
    """

    responses: list[dict]
    name: str = "fake"
    calls: list[tuple[str, str]] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.calls is None:
            self.calls = []

    def complete_json(self, system_prompt, user_prompt, *, json_schema=None, temperature=0.0):
        self.calls.append((system_prompt, user_prompt))
        idx = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[idx]


# ---------------------------------------------------------------------------
# Smoke
# ---------------------------------------------------------------------------


def test_empty_text_returns_no_mentions():
    provider = FakeProvider(responses=[{"entities": []}])
    backend = LlmNerBackend(provider)
    assert backend.extract("") == []
    assert backend.extract("   \n   ") == []
    assert provider.calls == []


def test_single_chunk_company_entity_extracted():
    text = "Aksa Akrilik Kimya Sanayii A.Ş. 2016 yılında genel kurul yaptı."
    provider = FakeProvider(
        responses=[
            {
                "entities": [
                    {
                        "surface": "Aksa Akrilik Kimya Sanayii A.Ş.",
                        "entity_type": "company",
                        "confidence": 0.95,
                    }
                ]
            }
        ]
    )
    backend = LlmNerBackend(provider, chunk_chars=10000)
    mentions = backend.extract(text)
    assert len(mentions) == 1
    m = mentions[0]
    assert isinstance(m, Mention)
    assert m.surface == "Aksa Akrilik Kimya Sanayii A.Ş."
    assert m.entity_type_guess == "company"
    assert m.confidence == 0.95
    assert m.backend == "llm:fake"
    # Span doğru bulundu mu
    assert text[m.span_start:m.span_end] == m.surface


def test_person_extracted_with_correct_offsets():
    text = "Sayın Cengiz Solakoğlu kongrede konuştu."
    provider = FakeProvider(
        responses=[
            {
                "entities": [
                    {"surface": "Cengiz Solakoğlu", "entity_type": "person", "confidence": 0.9}
                ]
            }
        ]
    )
    backend = LlmNerBackend(provider)
    mentions = backend.extract(text)
    assert len(mentions) == 1
    assert mentions[0].entity_type_guess == "person"
    assert text[mentions[0].span_start:mentions[0].span_end] == "Cengiz Solakoğlu"


def test_low_confidence_filtered():
    text = "Belirsiz Yazı Şeyi A.Ş. metinde geçiyor."
    provider = FakeProvider(
        responses=[
            {
                "entities": [
                    {"surface": "Belirsiz Yazı Şeyi A.Ş.", "entity_type": "company", "confidence": 0.3}
                ]
            }
        ]
    )
    backend = LlmNerBackend(provider, min_confidence=0.6)
    assert backend.extract(text) == []


def test_unknown_entity_type_becomes_unknown():
    text = "Foo Bar Quux'un manifestosu."
    provider = FakeProvider(
        responses=[
            {
                "entities": [
                    {"surface": "Foo Bar Quux", "entity_type": "weapon", "confidence": 0.8}
                ]
            }
        ]
    )
    backend = LlmNerBackend(provider)
    mentions = backend.extract(text)
    assert len(mentions) == 1
    assert mentions[0].entity_type_guess == "unknown"


def test_organization_with_company_suffix_promoted_to_company():
    text = "Türk Telekomünikasyon A.Ş. 2024 raporu yayınlandı."
    provider = FakeProvider(
        responses=[
            {
                "entities": [
                    {
                        "surface": "Türk Telekomünikasyon A.Ş.",
                        "entity_type": "organization",
                        "confidence": 0.85,
                    }
                ]
            }
        ]
    )
    backend = LlmNerBackend(provider)
    mentions = backend.extract(text)
    assert len(mentions) == 1
    assert mentions[0].entity_type_guess == "company"


def test_surface_not_in_text_is_skipped():
    text = "Kısa metin."
    provider = FakeProvider(
        responses=[
            {
                "entities": [
                    {
                        "surface": "Hayali Şirket Holding A.Ş.",
                        "entity_type": "company",
                        "confidence": 0.9,
                    }
                ]
            }
        ]
    )
    backend = LlmNerBackend(provider)
    # Surface metinde yok → mention üretilmez
    assert backend.extract(text) == []


def test_multiple_occurrences_each_yield_mention():
    text = "Aksa A.Ş. ile Aksa A.Ş. aynı şirkettir; Aksa A.Ş. büyüktür."
    provider = FakeProvider(
        responses=[
            {
                "entities": [
                    {"surface": "Aksa A.Ş.", "entity_type": "company", "confidence": 0.9}
                ]
            }
        ]
    )
    backend = LlmNerBackend(provider)
    mentions = backend.extract(text)
    assert len(mentions) == 3
    for m in mentions:
        assert text[m.span_start:m.span_end] == "Aksa A.Ş."


def test_chunking_global_offsets_correct():
    """8000 karakterlik metin → 2 chunk; 2. chunk'taki entity'nin offset'i
    global text içinde doğru olmalı."""
    head = "İlk bölümde Aksa A.Ş. geçiyor. " * 100  # ~3100 chars
    tail_target = "Sonra Yıldız Holding A.Ş. ortaya çıkar."
    text = head + tail_target * 100  # tail repeats; we'll check first occurrence

    provider = FakeProvider(
        responses=[
            {"entities": [{"surface": "Aksa A.Ş.", "entity_type": "company", "confidence": 0.9}]},
            {
                "entities": [
                    {"surface": "Yıldız Holding A.Ş.", "entity_type": "company", "confidence": 0.9}
                ]
            },
            {"entities": []},
        ]
    )
    backend = LlmNerBackend(provider, chunk_chars=2000, chunk_overlap=100)
    mentions = backend.extract(text)
    surfaces = {m.surface for m in mentions}
    assert "Aksa A.Ş." in surfaces
    assert "Yıldız Holding A.Ş." in surfaces
    # Tüm offset'ler gerçek metni gösteriyor mu
    for m in mentions:
        assert text[m.span_start:m.span_end] == m.surface


def test_dedup_by_span_keeps_highest_confidence():
    mentions = [
        Mention(
            surface="X", span_start=10, span_end=11,
            entity_type_guess="company", confidence=0.6, backend="llm:a",
        ),
        Mention(
            surface="X", span_start=10, span_end=11,
            entity_type_guess="company", confidence=0.9, backend="llm:b",
        ),
        Mention(
            surface="Y", span_start=20, span_end=21,
            entity_type_guess="person", confidence=0.7, backend="llm:a",
        ),
    ]
    out = _dedup_by_span(mentions)
    assert len(out) == 2
    by_span = {m.span_start: m for m in out}
    assert by_span[10].confidence == 0.9
    assert by_span[10].backend == "llm:b"


def test_invalid_llm_response_returns_empty():
    """LLM şemaya uymayan dict döndürürse exception fırlatma, boş dön."""
    text = "Aksa A.Ş. metinde."
    provider = FakeProvider(responses=[{"foo": "bar"}])  # entities yok ama default boş
    backend = LlmNerBackend(provider)
    out = backend.extract(text)
    assert out == []


def test_pydantic_schema_serializable():
    """LLM provider'a vereceğimiz JSON schema gerçek schema (regression)."""
    schema = LlmEntityResponse.model_json_schema()
    assert "properties" in schema
    assert "entities" in schema["properties"]
    # entities array of LlmEntity
    items = schema["properties"]["entities"]["items"]
    # JSON schema ya inline ya $ref olarak verir; her ikisi de geçerli
    assert "$ref" in items or items.get("type") == "object"


# ---------------------------------------------------------------------------
# Parallel mode
# ---------------------------------------------------------------------------


@dataclass
class IndexedFakeProvider:
    """Chunk içeriğine göre cevap döndüren thread-safe provider.

    Sıralı (sequential) mod'da call sırası deterministik; paralel mod'da
    sıra değişebilir. ``responses`` chunk substring'ine göre eşlenmiş bir dict:
    ``{substring_in_chunk: response_dict}``. İlk eşleşen kullanılır.
    """

    responses_by_keyword: dict
    name: str = "indexed-fake"
    call_count: int = 0

    def complete_json(self, system_prompt, user_prompt, *, json_schema=None, temperature=0.0):
        self.call_count += 1
        for keyword, response in self.responses_by_keyword.items():
            if keyword in user_prompt:
                return response
        return {"entities": []}


def test_parallel_extract_collects_all_chunks():
    """max_concurrency > 1 ile çoklu chunk paralel çağrılır, tüm sonuçlar toplanır."""
    head = "İlk bölümde Aksa A.Ş. geçiyor. " * 100  # ~3100 chars
    tail = "Sonra Yıldız Holding A.Ş. ortaya çıkar. " * 100
    text = head + tail

    provider = IndexedFakeProvider(
        responses_by_keyword={
            "Aksa A.Ş.": {
                "entities": [{"surface": "Aksa A.Ş.", "entity_type": "company", "confidence": 0.9}]
            },
            "Yıldız Holding A.Ş.": {
                "entities": [
                    {"surface": "Yıldız Holding A.Ş.", "entity_type": "company", "confidence": 0.9}
                ]
            },
        }
    )

    backend = LlmNerBackend(
        provider,
        chunk_chars=2000,
        chunk_overlap=100,
        max_concurrency=4,  # paralel
    )
    mentions = backend.extract(text)
    surfaces = {m.surface for m in mentions}

    assert "Aksa A.Ş." in surfaces
    assert "Yıldız Holding A.Ş." in surfaces
    # Tüm offset'ler gerçek metni gösteriyor
    for m in mentions:
        assert text[m.span_start:m.span_end] == m.surface


def test_parallel_and_sequential_produce_same_results():
    """Aynı girdiyi sıralı ve paralel çalıştırınca sonuç aynı olmalı."""
    text = ("Aksa A.Ş. ve Yıldız Holding A.Ş. " * 30) + ("Borusan Şirketi A.Ş. " * 30)

    keyword_responses = {
        "Aksa": {
            "entities": [
                {"surface": "Aksa A.Ş.", "entity_type": "company", "confidence": 0.9},
                {"surface": "Yıldız Holding A.Ş.", "entity_type": "company", "confidence": 0.9},
            ]
        },
        "Borusan": {
            "entities": [
                {"surface": "Borusan Şirketi A.Ş.", "entity_type": "company", "confidence": 0.9}
            ]
        },
    }

    seq_backend = LlmNerBackend(
        IndexedFakeProvider(responses_by_keyword=keyword_responses),
        chunk_chars=400,
        chunk_overlap=50,
        max_concurrency=1,
    )
    par_backend = LlmNerBackend(
        IndexedFakeProvider(responses_by_keyword=keyword_responses),
        chunk_chars=400,
        chunk_overlap=50,
        max_concurrency=4,
    )

    seq_mentions = seq_backend.extract(text)
    par_mentions = par_backend.extract(text)

    seq_keys = sorted((m.span_start, m.span_end, m.surface) for m in seq_mentions)
    par_keys = sorted((m.span_start, m.span_end, m.surface) for m in par_mentions)
    assert seq_keys == par_keys


def test_max_concurrency_clamped_to_one_when_invalid():
    """max_concurrency=0 veya negatifse 1'e clamp edilmeli (sıralı)."""
    backend = LlmNerBackend(
        FakeProvider(responses=[{"entities": []}]),
        max_concurrency=0,
    )
    assert backend.max_concurrency == 1
    backend2 = LlmNerBackend(
        FakeProvider(responses=[{"entities": []}]),
        max_concurrency=-3,
    )
    assert backend2.max_concurrency == 1
