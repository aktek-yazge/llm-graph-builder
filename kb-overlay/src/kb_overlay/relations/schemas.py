"""LLM ilişki çıkarımı için Pydantic şemaları + JSON schema export.

LLM'e bu şema verilir; LLM JSON döndürür, Pydantic doğrular, sonra Neo4j'ye
yazılır. Şema deterministik — her ilişki canonical_id'ler üzerinden tanımlı.

ConfidenceLabel
---------------
graphify'ın 3-değerli etiketi (EXTRACTED / INFERRED / AMBIGUOUS) bizim
ilişkilerimize de eklenir. Numeric confidence "ne kadar emin" der; label
"emniyetin nereden geliyor" der:

- EXTRACTED  → kaynaktan birebir geldi (gazetteer hit, exact_strict / exact_loose
               lookup, JSON'dan direkt alıntı). Doğrulamaya gerek yok.
- INFERRED   → algoritmik tahmin (fuzzy match, vector similarity, LLM'in
               pattern'den çıkardığı, NER'in ilk gördüğü). Numeric confidence
               eşiğine göre quality_gate'ten geçebilir.
- AMBIGUOUS  → birden fazla aday eşit ya da çelişki. Quality gate ne olursa
               olsun human review'a yönlendirir.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

# ConfidenceLabel'ı top-level modülden re-export et (circular import'tan kaçınmak
# için tanım orada). Eski kod ``from kb_overlay.relations.schemas import
# ConfidenceLabel`` ile import ediyor olabilir; o uyumluluk korunur.
from ..confidence import ConfidenceLabel


class ExtractedRelation(BaseModel):
    """LLM tarafından çıkarılan tek bir ilişki kaydı."""

    # extra="forbid": LLM'in halüsinasyonlu ek field'ları sessizce kabul edilmez,
    # ValidationError patlar. RelationExtractor 1 retry yapar, hâlâ fail ederse
    # tek relation discard edilir (batch devam eder).
    model_config = ConfigDict(extra="forbid")

    subject_id: str = Field(
        ...,
        description="Subject canonical_id (extractor'ın çözdüğü mention'lardan biri).",
    )
    predicate: str = Field(
        ...,
        description=(
            "İlişki tipi. Tercihen şu whitelist'ten seç: WORKS_AT, CEO_OF, CFO_OF, "
            "CTO_OF, CHAIRMAN_OF, BOARD_MEMBER_OF, FOUNDER_OF, OWNER_OF, "
            "SHAREHOLDER_OF, SUBSIDIARY_OF, PARENT_OF, PARTNER_OF, ACQUIRED_BY, "
            "MERGED_WITH, LOCATED_IN, HEADQUARTERED_IN. Whitelist dışındaysa "
            "RELATED_TO'ya düşürülecek; o zaman bile anlamlı bir kısa string ver."
        ),
    )
    object_id: str = Field(
        ...,
        description="Object canonical_id.",
    )
    evidence_text: str = Field(
        ...,
        description="Belgeden bu ilişkinin temelini oluşturan kısa alıntı (≤200 karakter).",
        max_length=400,
    )
    confidence: float = Field(
        0.7,
        ge=0.0,
        le=1.0,
        description="LLM'in kendi kararına olan güven skoru (0..1).",
    )
    confidence_label: ConfidenceLabel = Field(
        ConfidenceLabel.INFERRED,
        description=(
            "Güvenin tipi. LLM ürettiyse default INFERRED. "
            "Subject veya object canonical_id resolver tarafından AMBIGUOUS "
            "işaretlendiyse AMBIGUOUS olarak yeniden etiketlenir."
        ),
    )
    span_start: Optional[int] = Field(
        None,
        description="Belgedeki span başlangıcı (varsa, char offset).",
    )
    span_end: Optional[int] = Field(
        None,
        description="Belgedeki span sonu (char offset).",
    )


class RelationExtractionResponse(BaseModel):
    """LLM'in topyekûn yanıt formatı."""

    # Top-level extra="forbid" — LLM ekstra anahtar uyduramasın.
    model_config = ConfigDict(extra="forbid")

    relations: list[ExtractedRelation] = Field(
        default_factory=list,
        description="Bulunan tüm ilişkiler. Hiç bulunmazsa boş liste.",
    )
    notes: Optional[str] = Field(
        None,
        description="Çıkarımın belirsiz olduğu noktalar veya ek açıklama.",
    )


def response_json_schema() -> dict:
    """LLM prompt'una eklenecek JSON schema (string).

    OpenAI/Anthropic structured-output API'larına doğrudan verilebilir.
    """
    return RelationExtractionResponse.model_json_schema()
