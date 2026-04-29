# kb-overlay

Discovery-first entity resolution overlay for Turkish (and multilingual) knowledge bases.

> **PoC** — proof of concept. Mevcut `agent-builder` / `celery_worker` sistemini bozmadan, yan yana çalışan bağımsız bir alt proje. Burada test edilen mimari onaylandığında, mevcut sisteme aşamalı olarak entegre edilir.

## Neyi çözüyor

Belgelerden çıkarılan şirket/kişi isimleri her belgede farklı yazımla geçer:

```
"ABC Bilişim A.Ş."
"ABC Bilişim Anonim Şirketi"
"abc bilisim a.s."
"ABC Bilişim'in"
"A.B.C. Bilişim"
```

Bu tek bir **canonical entity**'ye mi ait? Cevabı tutarlı, hızlı, açıklanabilir bir şekilde vermek bu projenin görevi.

Açıkça **agent-builder içindeki mevcut `entity_resolver.py`'nin yerini almaz** — onu tamamlar:


| Mevcut sistem (agent-builder) | kb-overlay                                                |
| ----------------------------- | --------------------------------------------------------- |
| Türkçe karakter folding       | + suffix temizleme (A.Ş., Ltd. Şti., Anonim Şirketi, ...) |
| Embedding + GDS clustering    | + deterministik gazetteer (Aho-Corasick)                  |
| Entity property `aliases[]`   | + kalıcı master alias dictionary (SQLite)                 |
| Auto-merge sonradan           | + her mention'ı önce sözlüğe sor (lookup-key ile O(1))    |
| LLM-driven extraction         | + NER discovery + open-world keşif kuyruğu                |
| Cypher: raw isimle search     | + sorgu-zamanı resolver (`canonical_id`'ye dön)           |


## Mimari

```
Belge metni
    │
    ▼
[1] Normalizer (TR/EN/DE/FR suffix + ek + fold) ──► strict_key + loose_key
    │
    ▼
[2] Gazetteer (Aho-Corasick) ──► bilinen mention spans (canonical_id ile)
    │
    ▼
[3] NER (naive/spaCy/GLiNER/LLM) ──► aday mention spans (henüz canonical'a bağlı değil)
    │
    ▼
[4] Cascading Resolver:
      Kademe 1: SQLite exact (strict_key) lookup       ──► confidence 1.0
      Kademe 2: SQLite loose lookup (fold edilmiş)     ──► confidence 0.95
      Kademe 3: RapidFuzz token_set_ratio (top-K aday) ──► confidence ~0.85
      Kademe 4: Vector similarity (Neo4j, opsiyonel)   ──► confidence ~0.80
      Kademe 5: Yeni canonical aday ──► review queue
    │
    ▼
[5] KB güncelle: alias_dictionary'ye varyasyonu yaz, Neo4j entity'yi MERGE et
    │
    ▼
[6] (Opsiyonel) LLM ilişki çıkarımı: resolved entity listesiyle (subj, pred, obj) çıkar
    Provenance (doc_id, span, evidence_text, confidence) ile Neo4j'ye MERGE et

[7] Sorgu-zamanı (agent için):
      Soru → QueryResolver → mention'ları canonical_id'ye çöz → parametreli Cypher şablonu
      Agent ASLA raw isimle Cypher yazmaz; her zaman canonical_id ile.
```

## Kurulum

```bash
cd kb-overlay
python -m venv .venv && source .venv/bin/activate
pip install -e ".[test]"

# Opsiyonel ek paketler:
pip install -e ".[ner]"      # spaCy / transformers / GLiNER
pip install -e ".[embed]"    # sentence-transformers
pip install -e ".[neo4j]"    # neo4j driver
pip install -e ".[llm]"      # openai + httpx (LLM relation/NER için)
pip install -e ".[all]"      # hepsi
```

### LLM NER için lokal Cosmos Turkish-Gemma kurulumu (önerilen)

`naive`/`spacy`/`gliner` backend'leri stopword listesi ile çalışır ve şirket/kişi
ayrımında zayıftır. **`llm` backend'i** lokal Türkçe LLM kullanarak prompt + JSON
schema ile entity çıkarır — stopword listesi şişirme derdi yok, Türkçe morfolojiyi
doğal anlar:

```bash
brew install ollama && brew services start ollama   # macOS

huggingface-cli download mradermacher/Turkish-Gemma-9b-v0.1-GGUF \
    Turkish-Gemma-9b-v0.1.Q4_K_M.gguf \
    --local-dir ~/.cache/huggingface/cosmos-gemma-t0
ollama create cosmos-gemma:9b -f scripts/ollama/Modelfile.cosmos-gemma-t0
```

Boyut: 5.5 GB disk, ~6 GB RAM. Apple Silicon'da ~1 dakikada 37K karakterlik bir
belgeyi ingest eder (M-serisi Mac'te test edilmiştir).

## Hızlı kullanım (CLI)

```bash
# 1. SQLite şemasını başlat
kbo init

# 2. Yüzlerce şirket+kişi listesini CSV'den seed et
python -m scripts.bootstrap_kb --csv data/seed_entities.example.csv --db data/aliases.db

# 3. (Opsiyonel) Neo4j'ye senkronize et + embedding hesapla
python -m scripts.bootstrap_kb --csv data/seed_entities.example.csv --db data/aliases.db \
    --neo4j --neo4j-pass <password> --embed

# 4. Bir OCR/metin dosyasını ingest et (NER + gazetteer + resolver)
# Naive regex (default, hızlı ama düşük precision):
kbo ingest --file data/sample_ocr.txt --doc-id POL_2024_001

# LLM NER ile (Cosmos lokal, önerilen — stopword listesi gerektirmez):
kbo ingest --file data/sample_ocr.txt --doc-id POL_2024_001 \
    --ner llm --llm-provider ollama --llm-model cosmos-gemma:9b

# 5. Pending review kuyruğunu gör
kbo review list

# 6. Bir adayı onayla → canonical entity yarat
kbo review approve <pending_id> --canonical-name "ABC Bilişim Teknolojileri A.Ş."

# 7. Sözlüğü ara (lookup-zamanı resolver demosu)
kbo lookup "abc bilişim'in"
# → COMP_xxxxx (confidence=1.00, source=exact_strict)

kbo lookup "ABC Bilişim Anonim Şirketi"
# → COMP_xxxxx (confidence=1.00, source=exact_strict)

# 8. Belgeden ilişki çıkar (LLM gerekir; OPENAI_API_KEY tanımlı olmalı)
kbo extract-relations --file data/sample.txt --doc-id POL_2024_001 \
    --neo4j --neo4j-pass <password>

# 9. Agent ask: doğal dilde soru → resolve → parametreli Cypher
kbo ask "ABC Bilişim Anonim Şirketi'nin CEO'su kim?" --no-run
# Cypher görüntülenir, --no-run kaldırılırsa Neo4j'de çalışır

# 10. KB istatistikleri
kbo stats
```

## Neo4j şeması

`src/kb_overlay/neo4j_store/schema.cypher` içinde tanımlı:

```cypher
CREATE CONSTRAINT entity_canonical_id IF NOT EXISTS
  FOR (e:Entity) REQUIRE e.canonical_id IS UNIQUE;

CREATE INDEX entity_norm_strict IF NOT EXISTS FOR (e:Entity) ON (e.norm_strict);

CREATE FULLTEXT INDEX entity_fulltext IF NOT EXISTS
  FOR (e:Entity) ON EACH [e.canonical_name, e.norm_strict];

CREATE VECTOR INDEX entity_vector IF NOT EXISTS
  FOR (e:Entity) ON (e.embedding)
  OPTIONS {indexConfig: {`vector.dimensions`: 768, `vector.similarity_function`: 'cosine'}};
```

İlişkiler her zaman provenance taşır (`source_doc_id`, `evidence_text`, `confidence`, `span_start`, `span_end`).

## Mevcut sisteme entegrasyon (sonradan)

Bu PoC olgunlaştığında, `celery_worker/src/`'a şu dokunuşlarla taşınır:

1. `text_normalizer.py` → mevcut `ocr_normalizer.py` ile birleştir (suffix listelerini ekle)
2. `alias_dictionary.py` → yeni Celery task `tasks.alias_resolve_task`
3. `gazetteer.py` → `entity_resolution_task` öncesi pre-pass olarak çağrılır
4. `cascading.py` → mevcut `EntityResolver.find_similar_entities`'in önüne kademe 1-3 eklenir
5. `query_resolver` → Chat Agent için MCP tool olarak expose edilir

PoC sırasında mevcut sistemden bağımsız çalışacağı için Neo4j'siz de SQLite + dosya tabanlı test edilebilir.

## Test

```bash
pytest tests/ -v
```

Türkçe varyasyon corpus'u: `tests/fixtures/turkish_variants.yaml` — bilinen tuzaklar (İş Bankası vs Iş Bankası, A.Ş. varyantları, ek temizleme vb.) burada listelenir.

Neo4j smoke testleri (canlı Neo4j gerekir):

```bash
docker run -d --rm --name neo4j-test -p 7687:7687 -p 7474:7474 \
    -e NEO4J_AUTH=neo4j/testpass1 neo4j:5

KBO_NEO4J_PASS=testpass1 pytest tests/test_neo4j_store_smoke.py
```

## Modül haritası


| Modül                    | Görevi                                                               |
| ------------------------ | -------------------------------------------------------------------- |
| `kb_overlay.normalize`   | Türkçe + multilingual normalizasyon (suffix, ek, fold)               |
| `kb_overlay.dictionary`  | SQLite alias master sözlük (AliasStore)                              |
| `kb_overlay.gazetteer`   | Aho-Corasick spotter (bilinen yazımları yakala)                      |
| `kb_overlay.discovery`   | NER backend'leri (naive / spaCy / GLiNER / LLM) + DocumentExtractor  |
| `kb_overlay.resolver`    | 5 kademeli cascading resolver (exact / loose / fuzzy / vector / new) |
| `kb_overlay.review`      | Pending entities + ambiguous review kuyruğu                          |
| `kb_overlay.neo4j_store` | Neo4j graph store (entity/relation MERGE + provenance)               |
| `kb_overlay.relations`   | LLM tabanlı ilişki çıkarımı (JSON schema)                            |
| `kb_overlay.agent`       | Sorgu-zamanı entity resolver + parametreli Cypher şablonları         |
| `kb_overlay.cli`         | `kbo` komutu (Click)                                                 |


## Tasarım kararları

- **Neden harici alias sözlüğü (SQLite), Neo4j'de değil?** Alias trie'sini RAM'e in-process build etmek <1ms latency veriyor. Neo4j'de surface_form lookup'ı için her seferinde driver round-trip = 5-50ms. SQLite tek dosya, atomik backup, schema migration kolay.
- **Neden gazetteer + NER hibrit?** Gazetteer = bilinen isimlerde %100 precision + <1ms. NER = open-world keşfi (yeni adaylar). Sadece NER kullanırsak nadir/akronim isimleri kaçırırız; sadece gazetteer kullanırsak yeni şirket göremeyiz.
- **Neden agent raw isimle Cypher yazmasın?** Yazım varyasyonu sorununu sorgu zamanında değil **lookup katmanında** çözüyoruz. Agent her zaman `canonical_id` ile sorar; bu sayede Cypher hem güvenli hem deterministik.

## graphify-adopted patterns

graphify (`safishamsi/graphify`) projesinden seçici olarak 3 desen adopt edildi.
Detaylı analiz ve red-listeler için bkz. `agent-builder/AGENTS.md` Bölüm 7-9.

### 1. Triple-labelling: `EXTRACTED` / `INFERRED` / `AMBIGUOUS`

Numeric `confidence` ile birlikte her extraction kaydı (mention, relation)
kategorik bir `confidence_label` taşır. Stage → label mapping'i deterministik:

| Stage                  | Label       |
| ---------------------- | ----------- |
| `EXACT_STRICT`         | `EXTRACTED` |
| `EXACT_LOOSE`          | `EXTRACTED` |
| `gazetteer_hit`        | `EXTRACTED` |
| `FUZZY`                | `INFERRED`  |
| `VECTOR`               | `INFERRED`  |
| `NEW_CANDIDATE`        | `INFERRED`  |
| `auto_created`         | `INFERRED`  |
| `AMBIGUOUS` (her stage)| `AMBIGUOUS` |

Relations için: subject/object'ten herhangi biri AMBIGUOUS → relation AMBIGUOUS
(en kötü kazanır). API: `kb_overlay.confidence.ConfidenceLabel`,
`kb_overlay.resolver.cascading.label_from_stage`,
`ResolutionResult.confidence_label` property.

### 2. Strict schema + 1 retry policy

LLM relation extraction'da `extra="forbid"` zorunlu. Validation 3 aşamalı:
strict full → per-relation salvage → 1 retry (sıkılaştırılmış prompt).
Telemetri `IngestionResult.discarded_relations` ve
`IngestionResult.validation_recovered` field'larında.

### 3. Content-hash ingest cache

`(doc_id, sha256(content)[:16], extractor_version)` anahtarı ile aynı dosya
+ aynı extractor konfigürasyonu re-ingest'te LLM çağrısı atlanır.
NER backend / LLM model / schema sürümü değişimi otomatik invalidation tetikler.
CLI: `kbo ingest --force` bypass, `kbo cache stats` / `kbo cache clear`.

