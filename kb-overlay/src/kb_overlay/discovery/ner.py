"""NER backend'leri — pluggable mention spotting.

PoC olarak üç seçenek:
  - NaiveBackend: regex tabanlı, bağımlılıksız. Yüksek recall, düşük precision.
  - SpacyBackend: spaCy `tr_core_news_lg` veya `xx_sent_ud_sm` (opsiyonel install).
  - GlinerBackend: GLiNER multilingual (opsiyonel install).

Tüm backend'ler `Mention` listesi döndürür. Resolver bu mention'ları sözlüğe
sorar; backend'in entity_type kararı resolver tarafından override edilebilir.
"""
from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterable, Literal, Optional

logger = logging.getLogger(__name__)

EntityKindGuess = Literal["company", "person", "organization", "location", "unknown"]


@dataclass(slots=True, frozen=True)
class Mention:
    """NER tarafından bulunan aday mention."""

    surface: str
    span_start: int
    span_end: int
    entity_type_guess: EntityKindGuess = "unknown"
    confidence: float = 0.5
    backend: str = "unknown"


# ---------------------------------------------------------------------------
# Backend abstract
# ---------------------------------------------------------------------------


class NerBackend(ABC):
    name: str = "abstract"

    @abstractmethod
    def extract(self, text: str) -> list[Mention]: ...


# ---------------------------------------------------------------------------
# 1) Naive regex backend - bağımlılıksız default
# ---------------------------------------------------------------------------

# Şirket sinyalleri (varsa proper-noun yığınını company olarak etiketle)
_COMPANY_HINT = re.compile(
    r"\b(?:A\.?\u015e\.?|Anonim\s+\u015eirket(?:i)?|Ltd\.?\s*\u015eti\.?|Limited\s+\u015eirket(?:i)?|"
    r"Holding|San(?:ayi)?\.?\s*(?:ve\s+)?Tic(?:aret)?\.?|Sanayi|Ticaret|"
    r"Inc\.?|Corp\.?|LLC|GmbH|AG|S\.?A\.?|S\.?A\.?R\.?L\.?|S\.?p\.?A\.?|B\.?V\.?|N\.?V\.?)\b",
    re.IGNORECASE,
)

# Kişi unvan sinyalleri (önceki token unvansa person diye işaretle)
_PERSON_TITLE_BEFORE = re.compile(
    r"\b(?:Say\u0131n|Dr|Prof|Do\u00e7|Av|Mr|Mrs|Ms|Bay|Bayan|Sn)\.?\s+",
    re.IGNORECASE,
)

# Türkçe büyük harfler dahil — proper-noun pattern
# Ardışık 2-6 token: her biri büyük harfle başlar veya tamamen büyük harf veya
# içinde nokta/apostrof olabilir.
_PROPER_NOUN_TOKEN = r"[A-Z\u00c7\u00d6\u00dc\u011e\u0130\u015e][\w\u00e7\u00f6\u00fc\u011f\u0131\u015f\u00c7\u00d6\u00dc\u011e\u0130\u015e.&'-]*"
_PROPER_NOUN_SEQ = re.compile(
    rf"\b(?:{_PROPER_NOUN_TOKEN}(?:\s+(?:ve|of|de|der|van|von|du|de\s+la)\s+)?){{1,1}}"
    rf"(?:\s+{_PROPER_NOUN_TOKEN}){{0,5}}\b"
)

# Türkçe stop words (proper-noun gibi başlasa bile entity sayma)
_TR_STOPWORDS = {
    "Bu", "\u015eu", "O", "Bir", "Baz\u0131", "Her", "B\u00fct\u00fcn", "T\u00fcm",
    "\u00c7\u00fcnk\u00fc", "E\u011fer", "Ancak", "Ama", "Fakat", "Veya",
    "Sayfa", "Madde", "B\u00f6l\u00fcm", "K\u0131s\u0131m", "Tablo", "\u015eekil",
    "T\u00fcrkiye", "\u0130stanbul", "Ankara",
    "Ocak", "\u015eubat", "Mart", "Nisan", "May\u0131s", "Haziran",
    "Temmuz", "A\u011fustos", "Eyl\u00fcl", "Ekim", "Kas\u0131m", "Aral\u0131k",
    "Pazartesi", "Sal\u0131", "\u00c7ar\u015famba", "Per\u015fembe", "Cuma", "Cumartesi", "Pazar",
}


class NaiveRegexBackend(NerBackend):
    """Regex tabanlı, hiçbir ML gerektirmez.

    Yüksek recall (her proper-noun yığını yakalanır), düşük precision (yer adları,
    aylar, başlıklar da yakalanır). Resolver kademeleri ve review kuyruğu bunu
    elemek için var.
    """

    name = "naive_regex"

    def __init__(self, *, min_token_count: int = 2, max_token_count: int = 6):
        self.min_token_count = min_token_count
        self.max_token_count = max_token_count

    def extract(self, text: str) -> list[Mention]:
        if not text:
            return []
        out: list[Mention] = []
        for m in _PROPER_NOUN_SEQ.finditer(text):
            surface = m.group(0).strip()
            if not surface:
                continue
            tokens = surface.split()
            if not (self.min_token_count <= len(tokens) <= self.max_token_count):
                continue
            if all(tok in _TR_STOPWORDS for tok in tokens):
                continue
            kind = self._guess_kind(surface, text, m.start())
            out.append(
                Mention(
                    surface=surface,
                    span_start=m.start(),
                    span_end=m.start() + len(surface),
                    entity_type_guess=kind,
                    confidence=0.5,
                    backend=self.name,
                )
            )
        return _dedup_overlapping(out)

    @staticmethod
    def _guess_kind(surface: str, full_text: str, span_start: int) -> EntityKindGuess:
        # 1. Şirket suffix/prefix var mı?
        if _COMPANY_HINT.search(surface):
            return "company"
        # 2. Önce kişi unvanı var mı?
        prefix = full_text[max(0, span_start - 30):span_start]
        if _PERSON_TITLE_BEFORE.search(prefix):
            return "person"
        # 3. Tek kelime + tamamen büyük harf → muhtemelen akronim/organizasyon
        tokens = surface.split()
        if len(tokens) == 1 and surface.isupper() and len(surface) >= 2:
            return "organization"
        # 4. Aksi → bilinmiyor
        return "unknown"


def _dedup_overlapping(mentions: list[Mention]) -> list[Mention]:
    if not mentions:
        return []
    sorted_m = sorted(mentions, key=lambda m: (m.span_start, -(m.span_end - m.span_start)))
    kept: list[Mention] = []
    for m in sorted_m:
        if any(
            k.span_start <= m.span_start and k.span_end >= m.span_end and k != m
            for k in kept
        ):
            continue
        kept.append(m)
    return kept


# ---------------------------------------------------------------------------
# 2) spaCy backend (opsiyonel)
# ---------------------------------------------------------------------------


class SpacyBackend(NerBackend):
    """spaCy ile NER. ``model`` adı runtime'da yüklenir.

    Önerilen modeller:
      - ``tr_core_news_lg`` — Türkçe spesifik
      - ``xx_sent_ud_sm`` — multilingual
    """

    name = "spacy"

    def __init__(self, model: str = "xx_sent_ud_sm"):
        try:
            import spacy
        except ImportError as exc:
            raise ImportError(
                "spaCy not installed. `pip install 'kb-overlay[ner]'` or `pip install spacy`."
            ) from exc
        try:
            self.nlp = spacy.load(model)
        except Exception as exc:
            raise RuntimeError(
                f"spaCy model '{model}' not found. Run: python -m spacy download {model}"
            ) from exc
        self.model = model

    SPACY_LABEL_TO_KIND: dict[str, EntityKindGuess] = {
        "ORG": "organization",
        "PERSON": "person",
        "PER": "person",
        "GPE": "location",
        "LOC": "location",
    }

    def extract(self, text: str) -> list[Mention]:
        if not text:
            return []
        doc = self.nlp(text)
        out: list[Mention] = []
        for ent in doc.ents:
            kind = self.SPACY_LABEL_TO_KIND.get(ent.label_, "unknown")
            # Şirket suffix varsa company'e yükselt
            if kind == "organization" and _COMPANY_HINT.search(ent.text):
                kind = "company"
            out.append(
                Mention(
                    surface=ent.text,
                    span_start=ent.start_char,
                    span_end=ent.end_char,
                    entity_type_guess=kind,
                    confidence=0.7,
                    backend=f"spacy:{self.model}",
                )
            )
        return out


# ---------------------------------------------------------------------------
# 3) GLiNER backend (opsiyonel) - prompt-based, multilingual
# ---------------------------------------------------------------------------


class GlinerBackend(NerBackend):
    """GLiNER ile NER. Etiket setini sen tanımlarsın.

    Model: ``urchade/gliner_multi-v2.1`` (~200MB, multilingual).
    """

    name = "gliner"

    DEFAULT_LABELS = ["company", "person", "organization", "location", "product"]

    def __init__(
        self,
        model: str = "urchade/gliner_multi-v2.1",
        *,
        labels: Optional[list[str]] = None,
        threshold: float = 0.5,
    ):
        try:
            from gliner import GLiNER  # type: ignore
        except ImportError as exc:
            raise ImportError(
                "gliner not installed. `pip install gliner`"
            ) from exc
        self.model_name = model
        self.labels = labels or self.DEFAULT_LABELS
        self.threshold = threshold
        self.model = GLiNER.from_pretrained(model)

    def extract(self, text: str) -> list[Mention]:
        if not text:
            return []
        ents = self.model.predict_entities(text, self.labels, threshold=self.threshold)
        out: list[Mention] = []
        for e in ents:
            label = e.get("label", "unknown")
            kind: EntityKindGuess = label if label in ("company", "person", "organization", "location") else "unknown"  # type: ignore[assignment]
            out.append(
                Mention(
                    surface=e["text"],
                    span_start=e["start"],
                    span_end=e["end"],
                    entity_type_guess=kind,
                    confidence=float(e.get("score", 0.5)),
                    backend=f"gliner:{self.model_name}",
                )
            )
        return out


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_ner_backend(name: str = "naive", **kwargs) -> NerBackend:
    """Backend factory.

    ``name`` ∈ {'naive', 'spacy', 'gliner', 'llm'}.

    LLM backend için ek kwargs:
      - provider: 'ollama' (default) | 'openai' | 'stub'
      - model: provider'a göre model adı (default: 'cosmos-gemma:9b')
      - base_url: Ollama/OpenAI endpoint (env'den de okunur)
      - temperature, chunk_chars, chunk_overlap, min_confidence (LlmNerBackend)
    """
    name = name.lower()
    if name == "naive":
        return NaiveRegexBackend(**kwargs)
    if name == "spacy":
        return SpacyBackend(**kwargs)
    if name == "gliner":
        return GlinerBackend(**kwargs)
    if name == "llm":
        return _build_llm_backend(**kwargs)
    raise ValueError(
        f"Unknown NER backend: {name!r}. "
        "Choose 'naive', 'spacy', 'gliner', or 'llm'."
    )


def _build_llm_backend(
    *,
    provider: str = "ollama",
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    temperature: float = 0.2,
    chunk_chars: int = 6000,
    chunk_overlap: int = 250,
    min_confidence: float = 0.5,
    max_concurrency: int = 1,
) -> NerBackend:
    """LlmNerBackend'i provider hazırlayıp döndür (lazy import).

    Provider'lar:
      - ``ollama``: Ollama HTTP API. Default port 11434, GGUF/MLX modelleri.
      - ``openai``: OpenAI-uyumlu API. ``base_url`` ile herhangi bir lokal
        servere yönlendirilebilir (vMLX, mlx_lm.server, vLLM, llama.cpp server).
      - ``mlx``: ``openai`` ile aynı, ama default ``base_url=http://127.0.0.1:8080/v1``
        ve ``api_key='not-needed'`` ile ön-konfigure (vMLX/mlx_lm.server
        kullanımını kolaylaştırma).
      - ``stub``: Test için boş cevap.
    """
    from .llm_ner import LlmNerBackend
    from ..relations.extractor import (
        OllamaProvider,
        OpenAIProvider,
        StubProvider,
    )

    provider_name = (provider or "ollama").lower()
    if provider_name == "ollama":
        prov = OllamaProvider(
            model=model or "cosmos-gemma:9b",
            base_url=base_url,
        )
    elif provider_name == "openai":
        prov = OpenAIProvider(
            model=model or "gpt-4o-mini",
            api_key=api_key,
            base_url=base_url,
        )
    elif provider_name == "mlx":
        # vMLX / mlx_lm.server için OpenAI-uyumlu shortcut
        prov = OpenAIProvider(
            model=model or "mlx-community/gemma-4-e4b-it-nvfp4",
            api_key=api_key or "not-needed",
            base_url=base_url or "http://127.0.0.1:8080/v1",
        )
    elif provider_name == "stub":
        prov = StubProvider()
    else:
        raise ValueError(
            f"Unknown LLM provider: {provider_name!r}. "
            "Choose 'ollama', 'openai', 'mlx', or 'stub'."
        )

    return LlmNerBackend(
        prov,
        chunk_chars=chunk_chars,
        chunk_overlap=chunk_overlap,
        temperature=temperature,
        min_confidence=min_confidence,
        max_concurrency=max_concurrency,
    )
