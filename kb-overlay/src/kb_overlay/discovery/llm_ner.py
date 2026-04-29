"""LLM tabanlı NER backend — schema-driven entity extraction.

Yaklaşım: regex/stopword listesi yerine, prompt + JSON schema ile LLM'e
"şu metinden ŞIRKET ve KIŞI ad varlıklarını JSON olarak döndür" dedirtiyoruz.
Avantajları:
  - Stopword listesi şişirme derdi yok (LLM "Madde 1" gibi yapısal token'ları
    kendi anlayıp atar).
  - Türkçe morfolojiyi (ek almış isimleri) doğal olarak handle eder.
  - Bağlamdan tip tahmini (PERSON vs ORG) yapar.

Provider: relations.extractor'daki LLMProvider arayüzü (OpenAI/Ollama/Stub).
Default Ollama + lokal Cosmos Turkish-Gemma kullanır.

Span recovery: LLM offset döndürmezse text.find() ile sıralı tarama yapıp
gerçek karakter offset'lerini hesaplarız (provenance için kritik).

Long text: ~6000 karakter chunk'lara böl, overlap ile birleştir, span offset'leri
global text'e map et.

Paralelleştirme: vMLX/mlx_lm.server gibi batched engine'lere birden fazla chunk'ı
aynı anda gönderebiliriz. ``max_concurrency > 1`` verilirse ``ThreadPoolExecutor``
+ ``asyncio.gather`` ile paralel çağrı yapılır. Default 1 (geriye uyumlu, sıralı).
"""
from __future__ import annotations

import asyncio
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError

from .ner import EntityKindGuess, Mention, NerBackend, _COMPANY_HINT, _dedup_overlapping

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# JSON schema (LLM çıkışı)
# ---------------------------------------------------------------------------


class LlmEntity(BaseModel):
    """LLM'in döndürdüğü tek entity kaydı.

    Alias'lar farklı LLM'lerin alışkanlıklarını tolere etmek için:
      - `entity_type` ↔ `type`, `kind`, `label`
      - `surface`     ↔ `text`, `name`, `mention`
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    surface: str = Field(
        ...,
        validation_alias=AliasChoices("surface", "text", "name", "mention"),
        description="Belgede geçtiği şekliyle TAM yazılış (kısaltma, ek dahil).",
        min_length=2,
        max_length=200,
    )
    entity_type: str = Field(
        ...,
        validation_alias=AliasChoices("entity_type", "type", "kind", "label"),
        description="company | person | organization | location",
    )
    confidence: float = Field(
        0.7,
        ge=0.0,
        le=1.0,
        description="LLM'in kararına olan güveni.",
    )


class LlmEntityResponse(BaseModel):
    """Topyekûn yanıt — entity listesi."""

    model_config = ConfigDict(extra="ignore")

    entities: list[LlmEntity] = Field(default_factory=list)


def _entity_response_schema() -> dict:
    """Ollama format=json_schema veya OpenAI structured output için."""
    return LlmEntityResponse.model_json_schema()


# ---------------------------------------------------------------------------
# System prompt (Türkçe)
# ---------------------------------------------------------------------------


SYSTEM_PROMPT_TR = """\
Sen bir Türkçe Adlandırılmış Varlık Tanıma (NER) uzmanısın.
Görevin: verilen Türkçe (veya karışık TR/EN) metinden SADECE aşağıdaki tipte
özel ad varlıklarını çıkarmak.

İzinli tipler:
  - company       → Ticari işletme adı (A.Ş., Ltd. Şti., Holding, Inc., GmbH vb.)
  - person        → Gerçek kişi adı (ad-soyad veya ad-soyad+unvan)
  - organization  → Şirket olmayan tüzel kişi (dernek, vakıf, kamu, üniversite)
  - location      → Coğrafi yer (şehir, ülke, bölge — ana yer adları)

ÇOK ÖNEMLİ KURALLAR:
1. ASLA uydurma. Sadece metinde GERÇEKTEN geçen yazılışı `surface` alanına
   yaz (kelime kelime aynısı).
2. Şirket isimlerini TAM olarak çıkar — "Aksa Akrilik Kimya Sanayii Anonim
   Şirketi" tek bir entity (parça parça değil).
3. Kişi adlarında unvanı ("Sayın", "Dr.", "Av.") DAHIL ETME — sadece ad-soyad.
4. Yapısal/şablon kelimeleri (Madde 1, Genel Kurul, Ortaklardan, Sayfa 3,
   Tarih, İmza, Yönetim Kurulu, Pazartesi, Ocak, vb.) ENTITY DEĞİLDİR — atla.
5. Aynı entity birden fazla geçiyorsa her geçişini ayrı kayıt olarak EKLEME;
   farklı yazılışları (kısa/uzun) varsa hepsini ekle, aynı yazılışı bir kez.
6. Belirsizse atla. Halüsinasyon yapmaktansa boş liste ver.
7. Şehir/ülke adları YALNIZCA bağımsız geçiyorlarsa entity (örn. "İstanbul'da"
   → location). Şirket adının parçasıysa AYRI çıkarma ("Türk Telekom A.Ş."
   tek company).
8. SADECE JSON döndür, açıklama veya yorum yazma."""


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------


# 'organization', 'product' tipleri NER tarafında geçici olarak desteklenir;
# resolver/extractor 'company' veya 'person' bekleyebiliyor.
_VALID_TYPES: set[str] = {"company", "person", "organization", "location"}


class LlmNerBackend(NerBackend):
    """LLM-driven NER. Provider üzerinden complete_json çağırır.

    Parameters
    ----------
    provider : LLMProvider
        relations.extractor'dan OllamaProvider/OpenAIProvider/StubProvider.
        İçeride sadece complete_json arayüzü kullanılır.
    chunk_chars : int
        Tek seferde LLM'e gönderilecek max metin uzunluğu. Default 6000 karakter
        ≈ 8K token civarı (Gemma 9B context'e rahat sığar).
    chunk_overlap : int
        Chunk'lar arası örtüşme — sınırlardaki entity'leri kaçırmamak için.
    temperature : float
        Sampling temperature. Cosmos Turkish-Gemma için 0.0 verme (sonsuz
        tekrar riski) — 0.1-0.3 arası iyi.
    min_confidence : float
        LLM çıkışı bu eşiğin altındaysa Mention üretme.
    """

    name = "llm"

    def __init__(
        self,
        provider,  # LLMProvider; circular import önlemek için type hint string olabilir
        *,
        chunk_chars: int = 6000,
        chunk_overlap: int = 250,
        temperature: float = 0.2,
        min_confidence: float = 0.5,
        max_concurrency: int = 1,
    ):
        self.provider = provider
        self.chunk_chars = chunk_chars
        self.chunk_overlap = chunk_overlap
        self.temperature = temperature
        self.min_confidence = min_confidence
        # max_concurrency > 1 ise vMLX/mlx_lm.server gibi batched engine'lere
        # chunk'lar paralel gönderilir. Default 1 = sıralı (geriye uyumlu).
        self.max_concurrency = max(1, int(max_concurrency))

    # ---------------------------------------------------------------- public

    def extract(self, text: str) -> list[Mention]:
        if not text or not text.strip():
            return []

        chunks = list(self._iter_chunks(text))

        # Tek chunk veya concurrency yoksa sıralı çalış
        if len(chunks) <= 1 or self.max_concurrency <= 1:
            all_mentions = self._extract_sequential(chunks, text)
        else:
            all_mentions = self._extract_parallel(chunks, text)

        # Chunk'lar örtüşebilir → aynı span birden çok kez gelebilir
        deduped = _dedup_by_span(all_mentions)
        return _dedup_overlapping(deduped)

    # ---------------------------------------------------------------- extraction strategies

    def _extract_sequential(
        self,
        chunks: list[tuple[str, int]],
        full_text: str,
    ) -> list[Mention]:
        """Chunk'ları tek tek sıralı LLM'e gönder (default davranış)."""
        all_mentions: list[Mention] = []
        for chunk_text, chunk_offset in chunks:
            all_mentions.extend(
                self._extract_one_chunk(chunk_text, chunk_offset, full_text)
            )
        return all_mentions

    def _extract_parallel(
        self,
        chunks: list[tuple[str, int]],
        full_text: str,
    ) -> list[Mention]:
        """Chunk'ları paralel LLM'e gönder (vMLX/mlx_lm batched engine için).

        Her chunk için ayrı thread'de senkron LLM çağrısı yapılır;
        ``asyncio.gather`` sonuçları toplar. Provider sync arayüzünde kalır.
        """
        return asyncio.run(self._gather_chunks(chunks, full_text))

    async def _gather_chunks(
        self,
        chunks: list[tuple[str, int]],
        full_text: str,
    ) -> list[Mention]:
        loop = asyncio.get_running_loop()
        max_workers = min(self.max_concurrency, len(chunks))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            tasks = [
                loop.run_in_executor(
                    executor,
                    self._extract_one_chunk,
                    chunk_text,
                    chunk_offset,
                    full_text,
                )
                for chunk_text, chunk_offset in chunks
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        all_mentions: list[Mention] = []
        for r in results:
            if isinstance(r, Exception):  # pragma: no cover — LLM hatası
                logger.warning("LLM NER chunk failed (parallel): %s", r)
                continue
            all_mentions.extend(r)
        return all_mentions

    def _extract_one_chunk(
        self,
        chunk_text: str,
        chunk_offset: int,
        full_text: str,
    ) -> list[Mention]:
        """Tek bir chunk için LLM çağrısı + Mention listesi (thread-safe)."""
        try:
            entities = self._call_llm(chunk_text)
        except Exception as exc:  # pragma: no cover — LLM hatası
            logger.warning("LLM NER chunk failed @offset=%d: %s", chunk_offset, exc)
            return []

        out: list[Mention] = []
        backend_name = f"llm:{getattr(self.provider, 'name', 'unknown')}"
        for ent in entities:
            if ent.confidence < self.min_confidence:
                continue
            kind = self._normalize_type(ent.entity_type, surface=ent.surface)
            for span in self._find_spans(chunk_text, ent.surface):
                span_start = chunk_offset + span[0]
                span_end = chunk_offset + span[1]
                out.append(
                    Mention(
                        surface=full_text[span_start:span_end],
                        span_start=span_start,
                        span_end=span_end,
                        entity_type_guess=kind,
                        confidence=float(ent.confidence),
                        backend=backend_name,
                    )
                )
        return out

    # ---------------------------------------------------------------- internals

    def _call_llm(self, chunk_text: str) -> list[LlmEntity]:
        user_prompt = (
            "## Metin:\n"
            f"{chunk_text}\n\n"
            "## Görev:\n"
            "Yukarıdaki metinden geçerli özel ad varlıklarını çıkar.\n\n"
            "## Beklenen JSON çıktı formatı (kesinlikle bu yapı):\n"
            '{\n'
            '  "entities": [\n'
            '    {"surface": "Aksa Akrilik Kimya Sanayii A.Ş.", "entity_type": "company", "confidence": 0.95},\n'
            '    {"surface": "Cengiz Solakoğlu", "entity_type": "person", "confidence": 0.9}\n'
            '  ]\n'
            '}\n\n'
            "Her varlığın `surface` alanı metinde geçtiği TAM yazılış olsun "
            "(kelime kelime aynısı, ek/suffix dahil). "
            "Cevap MUTLAKA üst seviyede `entities` anahtarı olan bir JSON objesi olsun, "
            "doğrudan dizi DEĞIL."
        )
        raw = self.provider.complete_json(
            system_prompt=SYSTEM_PROMPT_TR,
            user_prompt=user_prompt,
            json_schema=_entity_response_schema(),
            temperature=self.temperature,
        )
        # Defensive parsing — bazı LLM'ler şemayı tam takip etmez:
        #  - direkt list döndürür: [{...}, {...}]
        #  - tek entity dict döndürür: {"surface": ..., "type": ...}
        #  - "data" / "result" / "items" anahtarı kullanır
        normalized = _normalize_llm_payload(raw)
        try:
            parsed = LlmEntityResponse.model_validate(normalized)
        except ValidationError as exc:
            logger.warning("LLM çıkışı şemaya uymadı: %s", exc)
            return []
        return parsed.entities

    @staticmethod
    def _normalize_type(raw_type: str, *, surface: str) -> EntityKindGuess:
        t = (raw_type or "").strip().lower()
        if t in _VALID_TYPES:
            # 'organization' içinde şirket suffix varsa company'e yükselt
            if t == "organization" and _COMPANY_HINT.search(surface):
                return "company"
            return t  # type: ignore[return-value]
        # bilinmeyen / diğer
        return "unknown"

    def _iter_chunks(self, text: str):
        """Long text'i overlap'lı chunk'lara böl. (chunk_text, global_offset)."""
        if len(text) <= self.chunk_chars:
            yield text, 0
            return

        i = 0
        n = len(text)
        while i < n:
            end = min(i + self.chunk_chars, n)
            # Mümkünse cümle/paragraf sınırında kes
            if end < n:
                # son 300 karakterde bir nokta veya newline ara
                window_start = max(end - 300, i + self.chunk_chars // 2)
                cut = max(
                    text.rfind(". ", window_start, end),
                    text.rfind("\n", window_start, end),
                )
                if cut != -1:
                    end = cut + 1
            yield text[i:end], i
            if end >= n:
                break
            i = max(end - self.chunk_overlap, i + 1)

    @staticmethod
    def _find_spans(text: str, surface: str, *, max_hits: int = 50) -> list[tuple[int, int]]:
        """Surface'in chunk içindeki tüm geçişlerini bul (whole-word, fuzzy).

        Önce exact arama; bulamazsa whitespace-normalized arama yap.
        """
        if not surface or not text:
            return []
        spans: list[tuple[int, int]] = []

        # 1. Tam eşleşme — whole-word boundary'siz çünkü Türkçe'de ek alır
        cursor = 0
        while len(spans) < max_hits:
            idx = text.find(surface, cursor)
            if idx == -1:
                break
            spans.append((idx, idx + len(surface)))
            cursor = idx + len(surface)

        if spans:
            return spans

        # 2. Whitespace normalize fallback — LLM "ABC  Inc" döndürdü ama metinde
        # "ABC Inc" varsa
        norm_surface = re.sub(r"\s+", " ", surface).strip()
        if norm_surface != surface:
            cursor = 0
            while len(spans) < max_hits:
                idx = text.find(norm_surface, cursor)
                if idx == -1:
                    break
                spans.append((idx, idx + len(norm_surface)))
                cursor = idx + len(norm_surface)

        if spans:
            return spans

        # 3. Token-bazlı approximate search — surface'in ilk + son token'ı
        tokens = norm_surface.split()
        if len(tokens) >= 2:
            first, last = re.escape(tokens[0]), re.escape(tokens[-1])
            # max 80 char arası any-content
            pattern = re.compile(rf"\b{first}\b[^\n]{{0,80}}?\b{last}\b")
            for m in pattern.finditer(text):
                spans.append((m.start(), m.end()))
                if len(spans) >= max_hits:
                    break

        return spans


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_llm_payload(raw) -> dict:
    """LLM'in farklı şema sapmalarını ``{"entities": [...]}`` formatına çevir.

    Tolere ettiğimiz varyasyonlar:
      - ``[{...}, {...}]``                         → entities
      - ``{"entities": [...]}``                    → değişmez
      - ``{"data": [...]}`` / ``"items"`` / ...    → entities
      - ``{"surface": ..., "type": ...}`` (tek)    → entities=[that]
      - ``None`` veya tip uymazsa                  → boş liste
    """
    if raw is None:
        return {"entities": []}
    if isinstance(raw, list):
        return {"entities": raw}
    if not isinstance(raw, dict):
        return {"entities": []}

    if "entities" in raw and isinstance(raw["entities"], list):
        return raw

    for alt_key in ("data", "items", "results", "list", "extractions"):
        v = raw.get(alt_key)
        if isinstance(v, list):
            return {"entities": v}

    if any(k in raw for k in ("surface", "text", "name", "mention")):
        return {"entities": [raw]}

    return {"entities": []}


def _dedup_by_span(mentions: list[Mention]) -> list[Mention]:
    """Aynı (span_start, span_end) tek mention'a indirgensin."""
    seen: dict[tuple[int, int], Mention] = {}
    for m in mentions:
        key = (m.span_start, m.span_end)
        if key not in seen or m.confidence > seen[key].confidence:
            seen[key] = m
    return sorted(seen.values(), key=lambda x: x.span_start)
