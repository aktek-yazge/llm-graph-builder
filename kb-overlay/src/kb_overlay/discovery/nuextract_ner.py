"""NuExtract-native NER backend — template-driven extraction.

NuExtract 2.0 (numind/NuExtract-2.0-*) chat-style sohbet promptlarıyla
çalışmıyor; modelin training rejimi `# Template:` + `# Context:` segmentli
özel bir format bekliyor. Bizim ortak ``LlmNerBackend`` (chat + JSON schema)
NuExtract'i atıl bırakıyor — ampirik test: 19K karakter Aksa belgesinde
0 mention.

Bu backend NuExtract'in eğitildiği template formatını ve ChatML token'larını
manuel sarmalayarak Ollama ``/api/generate`` ``raw=true`` üzerinden çağırır.
Ollama'nın chat template path'ini bypass eder — yani Modelfile'daki ``template``
parametresinin bizim için olmaması veya farklı yorumlanması bizi etkilemez.

Yöntem: ``NerBackend`` arayüzü; ``LlmNerBackend``'in chunking, span recovery
ve dedup yardımcılarını yeniden kullanır.

Önerilen kullanım::

    from kb_overlay.discovery import get_ner_backend
    backend = get_ner_backend(
        "nuextract",
        model="hf.co/numind/NuExtract-2.0-8B-GGUF:Q8_0",
    )
    mentions = backend.extract(document_text)

CLI ile::

    kbo ingest --ner nuextract \\
        --llm-model hf.co/numind/NuExtract-2.0-8B-GGUF:Q8_0 \\
        --file ... --doc-id ...
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

from .llm_ner import (
    LlmEntity,
    LlmEntityResponse,
    LlmNerBackend,
    _normalize_llm_payload,
)
from .ner import Mention, _dedup_overlapping

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# NuExtract template (model'in training rejiminde beklediği şema syntax'ı)
# ---------------------------------------------------------------------------

NUEXTRACT_TEMPLATE: str = json.dumps(
    {
        "entities": [
            {
                "surface": "verbatim-string",
                "entity_type": [
                    "company",
                    "person",
                    "organization",
                    "location",
                ],
            }
        ]
    },
    ensure_ascii=False,
)


# NuExtract'in tokenizer.chat_template'i extraction modunda otomatik koyduğu
# system mesajı. Biz raw mode kullandığımız için manuel ekliyoruz.
_NUEXTRACT_SYSTEM_MSG: str = (
    "You are NuExtract, an information extraction tool created by NuMind."
)


def _build_prompt(template: str, context: str) -> str:
    """ChatML token'larıyla NuExtract chat formatını manuel sarmala."""
    return (
        f"<|im_start|>system\n{_NUEXTRACT_SYSTEM_MSG}<|im_end|>\n"
        f"<|im_start|>user\n"
        f"# Template:\n{template}\n"
        f"# Context:\n{context}<|im_end|>\n"
        f"<|im_start|>assistant\n"
    )


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------


class NuExtractNerBackend(LlmNerBackend):
    """NuExtract-native NER backend.

    ``LlmNerBackend``'den miras alır; chunking, span recovery, dedup ve
    ``_normalize_type`` aynen çalışır. Tek farklar:
      - ``__init__`` provider yerine ``model`` + ``base_url`` direkt alır
        (kendi httpx çağrımızı yapacağız, provider arayüzüne ihtiyaç yok).
      - ``_call_llm`` Ollama ``/api/generate`` ``raw=true`` ile NuExtract
        template formatını gönderir.
      - Default ``chunk_chars`` daha büyük (18000) — NuExtract 32K context
        destekliyor, daha az LLM turu = daha hızlı.
      - Default ``temperature=0.0`` — NuExtract dökümanı bunu öneriyor.

    Parameters
    ----------
    model : str
        Ollama model tag'i. Default ``hf.co/numind/NuExtract-2.0-8B-GGUF:Q8_0``.
    base_url : str
        Ollama HTTP endpoint. Default ``http://localhost:11434``.
    chunk_chars : int
        Tek seferde context'e konulacak max karakter (NuExtract 32K
        token = ~96K char teorik; pratikte 18K karakter pencere yeterli).
    chunk_overlap : int
        Sınırlardaki entity'leri kaçırmamak için chunk'lar arası örtüşme.
    temperature : float
        Sampling temperature (NuExtract için 0.0 önerilen).
    min_confidence : float
        NuExtract template'imizde ``confidence`` field'ı yok → tüm
        entity'lere ``default_confidence`` atanır. Bu eşik altındakiler
        elenir; default değerle atılmasın diye 0.0 default.
    default_confidence : float
        Template'te confidence istemediğimiz için tüm entity'lere atanan
        sabit güven puanı. Default 0.85.
    timeout : float
        HTTP request timeout (sn).
    """

    name = "nuextract"

    def __init__(
        self,
        *,
        model: str = "hf.co/numind/NuExtract-2.0-8B-GGUF:Q8_0",
        base_url: Optional[str] = None,
        chunk_chars: int = 18000,
        chunk_overlap: int = 400,
        temperature: float = 0.0,
        min_confidence: float = 0.0,
        default_confidence: float = 0.85,
        max_concurrency: int = 1,
        timeout: float = 180.0,
    ):
        try:
            import httpx  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "httpx yok. `pip install httpx` veya `pip install 'kb-overlay[llm]'`."
            ) from exc

        # LlmNerBackend.__init__'i çağırmıyoruz çünkü o `provider` bekliyor;
        # bizim provider'ımız yok (kendi HTTP çağrımız var). Onun yerine ortak
        # field'ları manuel set ediyoruz.
        self.model = model
        self.base_url = (
            base_url or os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        )
        self.chunk_chars = chunk_chars
        self.chunk_overlap = chunk_overlap
        self.temperature = temperature
        self.min_confidence = min_confidence
        self.default_confidence = default_confidence
        self.max_concurrency = max(1, int(max_concurrency))
        self.timeout = timeout
        # _extract_one_chunk'ın provenance string için provider.name'e ihtiyacı
        # olduğu için şu adapter:
        self.provider = _ProviderShim(name="nuextract")

    # ---------------------------------------------------------------- override

    def _call_llm(self, chunk_text: str) -> list[LlmEntity]:
        """NuExtract template format + Ollama /api/generate raw=true."""
        import httpx

        prompt = _build_prompt(NUEXTRACT_TEMPLATE, chunk_text)
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "raw": True,
            "options": {
                "temperature": self.temperature,
                # NuExtract çıktısı genellikle kısa; ama uzun listede
                # kesilmesini önlemek için cömert tut:
                "num_predict": 4096,
            },
        }

        resp = httpx.post(
            f"{self.base_url}/api/generate",
            json=payload,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        raw_text = (data.get("response") or "").strip()

        if not raw_text:
            return []

        # Bazen NuExtract assistant mesajının sonuna `<|im_end|>` ekler veya
        # extra whitespace bırakır — JSON dışındaki kısımları temizle.
        raw_text = _strip_chatml_artifacts(raw_text)

        try:
            parsed_json = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            logger.warning(
                "NuExtract çıktısı geçerli JSON değil (%d char): %s | preview=%r",
                len(raw_text),
                exc,
                raw_text[:200],
            )
            return []

        # Defensive normalize → list[LlmEntity]
        normalized = _normalize_llm_payload(parsed_json)
        try:
            response = LlmEntityResponse.model_validate(normalized)
        except Exception as exc:  # pragma: no cover — şema sapması
            logger.warning("NuExtract çıktısı şemaya uymadı: %s", exc)
            return []

        # Template'imizde `confidence` field'ı yok → her entity'ye default
        # confidence ata. (LlmEntity Pydantic default 0.7 zaten, biz onu
        # kendi default_confidence'mıza çekiyoruz.)
        for ent in response.entities:
            if ent.confidence == 0.7:  # default değer (set edilmemiş)
                ent.confidence = self.default_confidence
        return response.entities

    # extract(), _iter_chunks(), _find_spans(), _extract_one_chunk(),
    # _normalize_type(), _extract_sequential(), _extract_parallel()
    # → hepsi LlmNerBackend'den miras alınır, dokunmuyoruz.


# ---------------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------------


class _ProviderShim:
    """``LlmNerBackend._extract_one_chunk`` provenance string için
    ``provider.name`` okur. Bizim gerçek provider'ımız yok, bu shim onu
    karşılıyor."""

    __slots__ = ("name",)

    def __init__(self, name: str = "nuextract"):
        self.name = name


def _strip_chatml_artifacts(text: str) -> str:
    """NuExtract bazen `<|im_end|>`, `<|endoftext|>` gibi token artifactlarını
    JSON sonrası bırakır. JSON ayrıştırması için temizliyoruz.
    """
    # Önce sondaki ChatML/EOS token'larını sil
    for tok in ("<|im_end|>", "<|endoftext|>", "<|eot_id|>"):
        text = text.replace(tok, "")
    text = text.strip()

    # Bazen başında "```json" sarmalama olabilir
    if text.startswith("```"):
        # ilk newline'a kadar olan fence'i sil
        nl = text.find("\n")
        if nl != -1:
            text = text[nl + 1 :]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    return text
