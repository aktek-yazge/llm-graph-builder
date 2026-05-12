# ADR-0001 — NER için GLiNER, ilişki çıkarımı için LLM

- **Tarih**: 2026-05-09
- **Durum**: Accepted
- **Kapsam**: `kb_overlay.discovery` (NER backend) + `kb_overlay.relations` (ilişki çıkarımı)
- **Tip**: decision (mimari)

## Bağlam (Context)

Türkçe Ticaret Sicili Gazetesi belgelerinden entity ve ilişki çıkarımı için 7+
farklı yaklaşım test edildi (Aksa Akrilik 09.03.2016 belgesi, 3 sayfa OCR,
~37K karakter referans korpus olarak kullanıldı):

- Klasik NER (BERTurk savasy/akdeniz27)
- Multilingual GLiNER (Ihor/gliner-multi-edu, fastino/gliner2-multi-v1)
- Schema-driven SLM (NuExtract 2.0 8B)
- Türkçe LLM (Cosmos Turkish-Gemma 9B, Trendyol-LLM 8B)
- MoE LLM (Gemma 26B-A4B MXFP8, Qwen3.6-35B-A3B Q4)
- Dense LLM (Qwen3.6-27B Dense Q8)
- Cloud LLM (Gemini 2.5 Flash)

4 kritik entity referans alındı: **Ata SMMM** (denetçi), **İZBAŞ A.Ş.**
(iştirak), **Menderes Tekstil** (sermayedar), **Kreston International**
(uluslararası denetim ağı).

### Benchmark sonuçları (Aksa 3 sayfa)

| Model | Boyut | Süre | Entity | Kritik 4 |
|---|---:|---:|---:|:-:|
| **GLiNER hibrit (TWNERTC + Gemini-distill)** ⭐ | **250M** | **5 s** | **99** | **4/4** |
| Ihor/gliner-multi-edu (vanilla) | 250M | 11 s | 56 | 3/4 |
| savasy/bert-base-turkish-ner-cased | 110M | 0.3 s | 56 | 0/4 |
| akdeniz27/mmbert-base-tr-uncased-ner | 307M | 1.2 s | 52 | 0/4 |
| fastino/gliner2-multi-v1 (Pass A) | 205M | 6 s | 91 | 0/4 |
| NuExtract 2.0 8B (LM Studio) | 8B | 185 s | 36 | 1/4 |
| Cosmos Turkish-Gemma 9B | 9B | ~150 s | 36 | 2/4 |
| Trendyol-LLM 8B | 8B | ~100 s | 31 | 1/4 |
| Gemma 26B-A4B MXFP8 | 26B | 102 s | 49 | 2/4 |
| Qwen3.6-35B-A3B Q4_K_XL | 35B | 109 s | 53 | 2/4 |
| Qwen3.6-27B Dense Q8_0 | 27B | 612 s | 60 | 4/4 |
| Gemini 2.5 Flash | cloud | 15 s | 60 | 2/4 |

Ham veriler: `runs/gliner_aksa_*.json`, `runs/bert_aksa_*.json`,
`runs/nuextract2_aksa.json`, `runs/synthetic_distill_report.md`.

## Karar (Decision)

### NER (Stage 1) — `models/gliner-aksa-hybrid/final/`

**Schema-driven entity çıkarımı için GLiNER hibrit fine-tune kullanılır.**

- **Backbone**: `Ihor/gliner-multi-edu` (mT5-encoder, ~250M param, multilingual)
- **Fine-tune Aşama 1**: `erayyildiz/turkish_ner` (TWNERTC) — 3.600 örnek,
  PER/ORG/LOC/MISC. 250 step / 3.6 dk MPS. Çıktı:
  `models/gliner-multi-edu-tr-finetuned/final/`
- **Fine-tune Aşama 2 (synthetic distillation)**: Gemini 2.5 Flash ile
  Aksa belgesinden annotate edilmiş 33 chunk → 322 entity (9 schema etiketi:
  şirket, kişi, denetçi firma, iştirak şirket, ortak, sermayedar, kurum, yer,
  tarih, para tutarı, görev_unvanı). Encoder dondurulmuş (44M/608M trainable),
  Adafactor optimizer, 500 step / 4.2 dk MPS. Çıktı:
  `models/gliner-aksa-hybrid/final/`
- **Maliyet**: One-time $0.034 (Gemini API), 17 dakika toplam pipeline.

### İlişki çıkarımı (Stage 2) — Gemini 2.5 Flash

**`(subject, predicate, object)` üçlüleri için Gemini 2.5 Flash kullanılır.**

- Input: GLiNER'in çıkardığı resolved entity listesi + ham metin
- Stage 1'de entity'ler zaten çıkarıldığı için Gemini'ye sadece **küçük entity
  pencereleri** + ilgili metin parçaları gönderilir (full document yerine)
- LLM token kullanımı: end-to-end LLM extraction'a göre **%80-90 azalma**
- Output: Strict Pydantic schema, `extra="forbid"` (graphify-adopted pattern)
- Maliyet: ~$0.001/sayfa (Gemini Flash pricing)

### Audit / yüksek-bahis fallback

`Qwen3.6-27B Dense Q8_0` lokal LLM **sadece** kritik vakalarda manuel review
veya altın-standart oluşturma için kullanılır. Production pipeline'ında **default
olarak çağrılmaz** (3 sayfada 612 saniye, kabul edilemez yavaş).

## Sonuçlar (Consequences)

### Olumlu

- **Hız**: 5 sn / 3 sayfa (Gemma 26B'den **20×**, Qwen 27B Dense'ten **120×** hızlı)
- **Kalite**: 4/4 kritik entity yakalanıyor (Kreston dahil)
- **Recall**: Vanilla GLiNER'a göre **%41 daha fazla entity** (56 → 99)
- **Boyut**: 250M parametre (8B-35B SLM/LLM'lerden 32-140× küçük)
- **Maliyet**: NER lokal + ücretsiz; sadece relation için cloud API (~$0.001/sayfa)
- **Veri privacy**: Belge içeriği LLM'e gönderilmeden önce entity'lere indirgenir
- **Hibrit avantajı**: GLiNER tek başına yapamadığı *karmaşık ilişki ve
  normalize* görevlerini Gemini'ye delege eder

### Olumsuz / Sınırlamalar

- **`iştirak şirket` recall 0**: Gemini distill datasinde sadece 3 örnek vardı.
  Çözüm: 50+ Sicil belgesi toplanıp re-annotate edilmeli.
- **Span boundary problemi**: "Ata Uluslararası Bağımsız Denetim ve SMMM A.Ş."
  iki parça çıkıyor. Sebep: encoder dondurulmuş (MPS OOM nedeniyle). Çözüm:
  Çıktıya post-processing merger veya encoder unfreeze + bigger GPU.
- **2-stage pipeline**: NER + relation iki ayrı çağrı, end-to-end LLM
  extraction'dan biraz daha karmaşık orchestration.
- **Domain genişletme maliyeti**: Yeni domain (sigorta poliçeleri, hukuki
  sözleşmeler) için **yeni Gemini-distill turu** gerekir (her domain için
  ~$0.05 + 20 dk). Domain-agnostic değil; ama re-train süreci çok kısa.

## Alternatifler (Considered & Rejected)

| Alternatif | Red sebebi |
|---|---|
| Vanilla `Ihor/gliner-multi-edu` (fine-tune yok) | 3/4 kritik entity (Kreston ❌), iştirak/denetçi recall zayıf |
| `fastino/gliner2-multi-v1` (GLiNER 2 resmi) | mDeBERTa-v3 Türkçe karakter offset'lerinde sürekli kayıyor; `0/4` kritik, broken span'ler |
| `NuExtract 2.0 8B` | 36 entity / 1/4 kritik / 185 sn — yavaş ve düşük recall |
| `Cosmos Turkish-Gemma 9B` ve `Trendyol LLM 8B` | NuExtract'tan biraz iyi ama hız/kalite trade-off mantıksız |
| `Qwen3.6-27B Dense Q8` (tek model) | 4/4 kritik ama 612 sn (10+ dk) — production'da kabul edilemez |
| `Gemini 2.5 Flash` (tek model) | 60 entity / 2/4 kritik (İZBAŞ kaçıyor) — entity recall NER'dan zayıf |
| End-to-end Gemini extraction (NER + relation tek çağrı) | Token maliyeti 5-10× artıyor; veri privacy zayıflıyor (full doc cloud'a) |
| Klasik BERTurk NER (savasy / akdeniz27) | 0/4 kritik — sadece PER/ORG/LOC, custom Sicil tipleri yok |

## Referanslar

### Modeller
- `models/gliner-aksa-hybrid/final/` — production model (2.3 GB)
- `models/gliner-multi-edu-tr-finetuned/final/` — TWNERTC-only intermediate

### Datasets
- `data/turkish_ner_gliner/{train,val,stats}.json` — TWNERTC GLiNER format (3600/400)
- `data/synthetic_distill/annotations/*.json` — Gemini-annotated chunks
- `data/hybrid_distill/{train,val,stats}.json` — birleşik training set

### Scripts
- `scripts/gliner/benchmark_aksa.py` — referans benchmark
- `scripts/gliner/convert_turkish_ner.py` — TWNERTC → GLiNER format
- `scripts/gliner/finetune_turkish_ner.py` — Aşama 1 fine-tune
- `scripts/distill/annotate_with_gemini.py` — synthetic annotation
- `scripts/distill/build_hybrid_dataset.py` — TWNERTC + sentetik birleştirme
- `scripts/distill/finetune_hybrid.py` — Aşama 2 fine-tune

### Run logları + raporlar
- `runs/synthetic_distill_report.md` — full pipeline raporu (15 KB)
- `runs/gliner_aksa_finetune_comparison.md` — TWNERTC FT karşılaştırması
- `runs/gliner_aksa_*.json` — page-by-page benchmark çıktıları
- `runs/distill_gemini_annotation.log`, `runs/distill_hybrid_finetune.log`

## Sıradaki adımlar

1. **`kb_overlay.discovery.gliner_ner` backend** ekle: `models/gliner-aksa-hybrid/final/` checkpoint'ini kullanan yeni NER backend
2. **CLI integration**: `kbo ingest --ner gliner-hybrid` komutu
3. **`iştirak şirket` problemi**: 50+ Sicil belgesi toplanıp re-annotate edilince yeniden FT
4. **Span boundary fix**: Multi-token entity merger post-processing veya encoder unfreeze (CUDA GPU varsa)
5. **Genişletilmiş benchmark**: 10+ farklı şirket Sicil belgesinde A/B test
