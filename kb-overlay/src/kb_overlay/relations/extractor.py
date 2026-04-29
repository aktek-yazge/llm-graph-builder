"""LLM tabanlı ilişki çıkarımı.

Belge metnini + resolved entity listesini alır, structured JSON ilişki listesi
döndürür. Pluggable LLM provider'lar:
  - OpenAIProvider (varsayılan, gpt-4o-mini)
  - OllamaProvider (lokal Llama/Qwen)
  - StubProvider   (test için, deterministik mock)

Hepsi `LLMProvider.complete_json(prompt) → dict` arayüzünü implement eder.
"""
from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from pydantic import ValidationError

from ..confidence import ConfidenceLabel
from .schemas import ExtractedRelation, RelationExtractionResponse, response_json_schema

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Provider arayüzü
# ---------------------------------------------------------------------------


class LLMProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        json_schema: Optional[dict] = None,
        temperature: float = 0.0,
    ) -> dict: ...


@dataclass(slots=True)
class ResolvedEntityInput:
    """Extractor'dan gelen resolved mention'ın LLM'e verilecek özeti.

    ``confidence_label`` field'ı subject/object çözümünün ne kadar emin olduğunu
    taşır. RelationPipeline relation çıktısını bu label'a göre yeniden etiketler:
    bir subject EXTRACTED ama object AMBIGUOUS ise relation AMBIGUOUS olur.
    """

    canonical_id: str
    canonical_name: str
    entity_type: str
    surface: str
    span_start: int
    span_end: int
    confidence_label: ConfidenceLabel = ConfidenceLabel.INFERRED


# ---------------------------------------------------------------------------
# OpenAI provider
# ---------------------------------------------------------------------------


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "openai paketi yok. `pip install 'kb-overlay[llm]'` veya `pip install openai`."
            ) from exc
        self.model = model
        self.client = OpenAI(
            api_key=api_key or os.environ.get("OPENAI_API_KEY"),
            base_url=base_url or os.environ.get("OPENAI_BASE_URL"),
        )

    def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        json_schema: Optional[dict] = None,
        temperature: float = 0.0,
    ) -> dict:
        kwargs: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
        }
        if json_schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "RelationExtractionResponse",
                    "schema": json_schema,
                    "strict": True,
                },
            }
        else:
            kwargs["response_format"] = {"type": "json_object"}

        resp = self.client.chat.completions.create(**kwargs)
        content = resp.choices[0].message.content or "{}"
        return json.loads(content)


# ---------------------------------------------------------------------------
# Ollama provider (lokal)
# ---------------------------------------------------------------------------


class OllamaProvider(LLMProvider):
    """Ollama HTTP API üzerinden lokal model (Qwen2.5, Gemma, Llama vb.).

    `ollama serve` çalışıyor olmalı. Varsayılan: http://localhost:11434
    """

    name = "ollama"

    def __init__(
        self,
        model: str = "qwen2.5:3b-instruct",
        *,
        base_url: Optional[str] = None,
    ):
        try:
            import httpx  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "httpx yok. `pip install httpx` veya `pip install 'kb-overlay[llm]'`."
            ) from exc
        self.model = model
        self.base_url = base_url or os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

    def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        json_schema: Optional[dict] = None,
        temperature: float = 0.0,
    ) -> dict:
        import httpx

        payload = {
            "model": self.model,
            "stream": False,
            "format": "json",
            "options": {"temperature": temperature},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        # Ollama 0.5+ format=schema destekliyor; eski sürümler için "json" stringi
        # de çalışır. JSON schema'yı prompt'a dahil ederek de yönlendirebiliriz.
        if json_schema is not None:
            payload["format"] = json_schema  # Ollama 0.5+

        resp = httpx.post(f"{self.base_url}/api/chat", json=payload, timeout=120.0)
        resp.raise_for_status()
        data = resp.json()
        content = data.get("message", {}).get("content", "{}")
        return json.loads(content)


# ---------------------------------------------------------------------------
# Stub provider (test için)
# ---------------------------------------------------------------------------


class StubProvider(LLMProvider):
    """Deterministik mock — testlerde ve LLM erişimi yokken kullanılır.

    Belge metninde basit pattern'ler ararak ilişki üretir:
      "X CEO'su Y" → CEO_OF(Y, X)
      "X, Y'nin yan kuruluşudur" → SUBSIDIARY_OF(X, Y)
    """

    name = "stub"

    def __init__(self, fixed_relations: Optional[list[dict]] = None):
        self.fixed_relations = fixed_relations or []

    def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        json_schema: Optional[dict] = None,
        temperature: float = 0.0,
    ) -> dict:
        return {"relations": list(self.fixed_relations), "notes": "stub_provider"}


# ---------------------------------------------------------------------------
# RelationExtractor — orkestratör
# ---------------------------------------------------------------------------


SYSTEM_PROMPT_TR = """\
Sen bir ilişki çıkarım uzmanısın. Verilen Türkçe/İngilizce belge metninden,
LİSTELENMİŞ entity'ler arasındaki ilişkileri çıkarırsın.

Kurallar (çok önemli):
1. Sadece sana verilen 'entities' listesindeki canonical_id'leri kullan.
   Listede olmayan entity'ler için ilişki üretme — onları görmezden gel.
2. Her ilişki için 'evidence_text' alanına belgeden ≤200 karakterlik
   doğrudan alıntı koy. Halüsinasyon yapma.
3. predicate alanı için tercih sırası:
   a) Whitelist: WORKS_AT, CEO_OF, CFO_OF, CTO_OF, CHAIRMAN_OF,
      BOARD_MEMBER_OF, FOUNDER_OF, OWNER_OF, SHAREHOLDER_OF,
      SUBSIDIARY_OF, PARENT_OF, PARTNER_OF, ACQUIRED_BY, MERGED_WITH,
      LOCATED_IN, HEADQUARTERED_IN.
   b) Uymuyorsa kısa, ALL_CAPS_SNAKE_CASE bir tip kullan (örn. SUPPLIER_OF).
4. confidence: 0.0–1.0 arası, LLM olarak senin kararına olan güvenin.
5. confidence_label (zorunlu) — şu üç değerden biri:
   - "EXTRACTED" : ilişki belgede AÇIKÇA, doğrudan ifade edilmiş
                   ("X, Y'nin CEO'sudur" gibi). Yorum gerektirmez.
   - "INFERRED"  : ilişki bağlamdan ÇIKARSANIYOR ama doğrudan denmemiş
                   (varsayılan; bağlamsal ipuçlarına dayanan tahmin).
   - "AMBIGUOUS" : metinde çelişki var ya da iki yorum mümkün; bağlamdan
                   net karar veremedin.
6. Hiçbir ilişki bulamazsan boş liste döndür. Uydurma!
7. Sadece JSON döndür, açıklama yazma. JSON şemasında OLMAYAN field ekleme;
   ek field'lar reddedilir (extra="forbid").
"""


_RETRY_FEEDBACK = (
    "\n\n## ONEMLI: Onceki cevabin JSON sema dogrulamasinda HATA verdi.\n"
    "Lutfen yalnizca semada listelenen alanlari kullan; EXTRA alan EKLEME.\n"
    "Tum zorunlu alanlari (subject_id, predicate, object_id, evidence_text)"
    " mutlaka doldur. confidence_label sadece su uc degerden biri olabilir:\n"
    " 'EXTRACTED', 'INFERRED', 'AMBIGUOUS'.\n"
)


@dataclass(slots=True)
class RelationExtractor:
    provider: LLMProvider
    max_evidence_chars: int = 400
    enable_retry_on_validation_error: bool = True
    """1 kez retry yap (sıkılaştırılmış prompt ile). Test'lerde False yapılabilir."""

    def extract(
        self,
        document_text: str,
        entities: list[ResolvedEntityInput],
        *,
        max_chars: int = 12000,
    ) -> RelationExtractionResponse:
        """Belgeyi + entity listesini LLM'e gönderir, doğrulanmış cevap döner.

        Validation policy (graphify-adopted):

        1. ``RelationExtractionResponse.model_validate(raw)`` strict (extra=forbid).
        2. Tüm response fail olursa **per-relation salvage**: her relation'ı
           ayrı validate et, fail olanları drop et, geçerlileri tut. Bu LLM'in
           sadece tek bir relation için extra field eklediği vakaları kurtarır.
        3. Hiçbir relation salvage edilemezse ve ``enable_retry_on_validation_error``
           True ise **1 retry**: system_prompt'a `_RETRY_FEEDBACK` ekleyip
           sıkılaştırılmış prompt ile tekrar çağır.
        4. Retry de fail ederse boş liste + ``notes`` alanında validation_error.

        Notes alanı statistics/diagnostics taşır:
          - ``salvaged_after_validation_error;discarded=N``
          - ``recovered_after_retry``
          - ``validation_error_after_retry: ...``
          - ``unknown_canonical_dropped=N`` (her zaman, varsa)
        """
        if not entities:
            return RelationExtractionResponse(relations=[], notes="no_entities")

        # Belge çok uzunsa kes (LLM context limit)
        text = document_text[:max_chars]

        entities_dump = [
            {
                "canonical_id": e.canonical_id,
                "canonical_name": e.canonical_name,
                "entity_type": e.entity_type,
                "surface_in_document": e.surface,
                "span_start": e.span_start,
                "span_end": e.span_end,
            }
            for e in entities
        ]
        user_prompt = (
            "## Çözülmüş Entity'ler (sadece bunları kullan):\n"
            f"{json.dumps(entities_dump, ensure_ascii=False, indent=2)}\n\n"
            "## Belge:\n"
            f"{text}\n\n"
            "## Görev:\n"
            "Yukarıdaki entity'ler arasındaki ilişkileri JSON şemasına uygun olarak çıkar."
        )

        parsed, validation_notes = self._call_and_validate(
            system_prompt=SYSTEM_PROMPT_TR,
            user_prompt=user_prompt,
        )
        if parsed is None:
            return RelationExtractionResponse(
                relations=[], notes=validation_notes or "validation_failed"
            )

        # Filtre: sadece bilinen canonical_id'leri tut (LLM halüsinasyonu önleme).
        # Her relation'ın confidence_label'ını subject/object'ün resolution
        # label'larına göre yükselt: bir uçtaki AMBIGUOUS relation'ı da
        # AMBIGUOUS yapar; üçü de EXTRACTED'sa relation EXTRACTED kalabilir.
        labels_by_id: dict[str, ConfidenceLabel] = {
            e.canonical_id: e.confidence_label for e in entities
        }
        kept: list[ExtractedRelation] = []
        dropped = 0
        for r in parsed.relations:
            if r.subject_id not in labels_by_id or r.object_id not in labels_by_id:
                dropped += 1
                continue
            r.confidence_label = _combine_labels(
                r.confidence_label,
                labels_by_id[r.subject_id],
                labels_by_id[r.object_id],
            )
            kept.append(r)
        if dropped:
            logger.info("LLM çıkarımından %d ilişki atıldı (bilinmeyen canonical_id)", dropped)

        parsed.relations = kept
        parsed.notes = _merge_notes(
            parsed.notes,
            validation_notes,
            f"unknown_canonical_dropped={dropped}" if dropped else None,
        )
        return parsed

    # --------------------------------------------------------------- helpers

    def _call_and_validate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[Optional[RelationExtractionResponse], Optional[str]]:
        """LLM çağrısı + 3 aşamalı validation. (parsed, notes) döner; parsed
        None ise tüm denemeler başarısız olmuş demektir.
        """
        try:
            raw = self.provider.complete_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                json_schema=response_json_schema(),
                temperature=0.0,
            )
        except Exception as exc:
            logger.error("LLM çağrısı başarısız: %s", exc)
            return None, f"llm_error: {exc}"

        # Aşama 1: Strict full-response validation
        try:
            parsed = RelationExtractionResponse.model_validate(raw)
            return parsed, None
        except ValidationError as exc:
            logger.info("Full response validation fail: %s", exc)

        # Aşama 2: Per-relation salvage
        salvaged, discarded_count = _salvage_relations(raw)
        if salvaged:
            logger.info(
                "Validation salvage: %d relation kurtarildi, %d discard",
                len(salvaged), discarded_count,
            )
            return (
                RelationExtractionResponse(relations=salvaged),
                f"salvaged_after_validation_error;discarded={discarded_count}",
            )

        # Aşama 3: 1 retry (sıkılaştırılmış prompt)
        if not self.enable_retry_on_validation_error:
            return None, "validation_error_no_retry"

        try:
            raw_retry = self.provider.complete_json(
                system_prompt=system_prompt + _RETRY_FEEDBACK,
                user_prompt=user_prompt,
                json_schema=response_json_schema(),
                temperature=0.0,
            )
        except Exception as exc:
            logger.error("Retry LLM çağrısı başarısız: %s", exc)
            return None, f"retry_llm_error: {exc}"

        try:
            parsed_retry = RelationExtractionResponse.model_validate(raw_retry)
            return parsed_retry, "recovered_after_retry"
        except ValidationError as exc:
            # Retry de tam fail; salvage'i bir kez daha dene
            salvaged_retry, discarded_retry = _salvage_relations(raw_retry)
            if salvaged_retry:
                return (
                    RelationExtractionResponse(relations=salvaged_retry),
                    f"recovered_partial_after_retry;discarded={discarded_retry}",
                )
            logger.warning("Validation retry sonrası da başarısız: %s", exc)
            return None, f"validation_error_after_retry: {exc}"


def _salvage_relations(raw: object) -> tuple[list[ExtractedRelation], int]:
    """raw response'tan tek tek relation'ları kurtarır. Geçerli olanları
    döndürür, fail olanları sayar."""
    if not isinstance(raw, dict):
        return [], 0
    raw_relations = raw.get("relations") or []
    if not isinstance(raw_relations, list):
        return [], 0
    salvaged: list[ExtractedRelation] = []
    discarded = 0
    for r in raw_relations:
        try:
            salvaged.append(ExtractedRelation.model_validate(r))
        except ValidationError:
            discarded += 1
    return salvaged, discarded


def _merge_notes(*parts: Optional[str]) -> Optional[str]:
    """Birden fazla notes string'i birleştir; ``;`` ile ayır."""
    cleaned = [p for p in parts if p]
    return ";".join(cleaned) if cleaned else None


def _combine_labels(*labels: ConfidenceLabel) -> ConfidenceLabel:
    """Bir relation için en düşük güven seviyesini seç.

    AMBIGUOUS > INFERRED > EXTRACTED hiyerarşisinde **en kötüsü** kazanır:
    relation ancak subject + object + LLM tahmininin hepsi EXTRACTED'sa
    EXTRACTED kalır; herhangi biri AMBIGUOUS ise relation AMBIGUOUS olur.
    """
    if ConfidenceLabel.AMBIGUOUS in labels:
        return ConfidenceLabel.AMBIGUOUS
    if ConfidenceLabel.INFERRED in labels:
        return ConfidenceLabel.INFERRED
    return ConfidenceLabel.EXTRACTED
