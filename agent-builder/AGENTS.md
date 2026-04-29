# Agent Builder

## Vizyon

Kullanicinin sohbet ederek bir alanda uzman agent yaratmasi. Agent'in
yapilandirilmamis belgelerden Knowledge Base (KB) olusturmak icin uygun
workflow'u tasarlayip calistirmasi. Olusturulan KB'nin ayri bir Chat Agent
tarafindan kullanilmasi.

Platform domain-agnostiktir: sigorta policeleri, ticaret sicil gazeteleri,
hastane kayitlari, hukuki sozlesmeler veya baska herhangi bir alan icin
ayni altyapi kullanilir. Domain'i agent degil, **kullanici ve agent
ortaklasa** belirler.

Tum domain bilgisi — entity (node) tanimlari, relationship tanimlari,
kurallar, oruntuler — **wiki'de** saklanir. Wiki, agent'in hem ontolojisi
hem de isleme hafizasidir.

---

## Mimari

```
                        ┌─────────────────────────────┐
                        │         Frontend             │
                        │  Dashboard · Chat · Workflow  │
                        │  Knowledges · Wiki · Izleme   │
                        └──────────┬──────────────────┘
                                   │ REST / SSE
                        ┌──────────▼──────────────────┐
                        │   Agent Builder Backend      │
                        │         (FastAPI)            │
                        │                              │
                        │  ┌──────────────────────┐    │
                        │  │   Builder Agent       │    │
                        │  │  (SelfEvolvingAgent)  │    │
                        │  │  17 workflow tool     │    │
                        │  │  OCR / batch tools    │    │
                        │  └──────────┬───────────┘    │
                        │             │                 │
                        │  ┌──────────▼───────────┐    │
                        │  │  Workflow Runtime     │    │
                        │  │  DSL compile+execute  │    │
                        │  │  Node Registry        │    │
                        │  └──────────┬───────────┘    │
                        └─────────────┼────────────────┘
                                      │ Celery bridge
                        ┌─────────────▼────────────────┐
                        │      Celery Workers           │
                        │  Gemini OCR (calisiyor)       │
                        │  Graph extraction (calisiyor) │
                        │  Neo4j writer (calisiyor)     │
                        │  Entity resolution            │
                        └──────────┬───────────────────┘
                                   │
                   ┌───────────────┼───────────────┐
                   ▼               ▼               ▼
              PostgreSQL        Neo4j          MinIO/S3
```

**Temel prensip:** Workflow orkestrasyon katmanidir, agir isleri yapmaz.
Celery calisan iscidir, ne yapilacagini bilmez. Workflow "ne" der,
Celery "nasil" yapar.

### Katmanlar

| Katman | Sorumluluk | Teknoloji |
|--------|-----------|-----------|
| Frontend | Chat, workflow canvas, izleme, wiki | React, assistant-ui |
| Agent Builder | Agent yasam dongusu, workflow CRUD, tool orkestrasyon | FastAPI, LangGraph |
| Workflow Runtime | DSL derleme, node calistirma, durum takibi | Python async, node registry |
| Celery Workers | OCR, extraction, Neo4j yazma, embedding | Celery, Gemini, Neo4j driver |
| Storage | Agent state, KG, belgeler | PostgreSQL, Neo4j, MinIO |

---

## Agent Tipleri

### 1. Builder Agent (SelfEvolvingAgent)

Kullaniciyla sohbet ederek domain'i anlar, ontoloji tasarlar, workflow
olusturur ve KB insasini yonetir.

**Sorumluluklar:**
- Kullaniciyla domain hakkinda konusma
- Ornek belgelerden domain anlama
- Wiki'ye domain bilgisi yazma (entity tanimlari, relationship tanimlari, constraint'ler)
- Domain'e uygun workflow tasarlama (hangi node'lar, hangi sira, kac asama)
- Workflow calistirma ve izleme
- Wiki'ye isleme sirasinda ogrenilenleri biriktirme

**Sahip oldugu tool'lar:**
- Workflow CRUD: `create_workflow`, `add_node`, `connect_nodes`, `configure_node`, `run_full_workflow`, `validate_workflow`, `publish_workflow` vb.
- Wiki / Ontoloji: `wiki_write`, `wiki_read`, `wiki_search`, `add_entity_class`, `add_relationship_predicate`, `set_domain_info` (hepsi wiki'ye yazar)
- OCR: `run_ocr`, `read_ocr_pages`, `extract_records_from_ocr`
- Batch: `start_batch_processing`, `get_batch_progress`

**Otonomi seviyesi:** Agent tek basina karar vermez. Her kritik asamada
kullaniciyla tartisir ve onay alir. Ancak onaylanan isler (10K belge isleme
gibi) otomatik yurutur.

### 2. Extraction Agent (planlanmis)

Workflow icinde bir agent node olarak calisir. Wiki'deki entity/relationship
tanimlari ve oruntularden beslenerek belgelerden entity ve relationship cikarir.

**Sorumluluklar:**
- Wiki'deki entity tanimlarina gore belge icerigini analiz etme
- Wiki'deki oruntuleri ve gecmis tecruebeyi kullanma
- Cikarilan bilgileri yapilandirma
- Ogrenilenleri wiki'ye geri yazma (sonraki belgeler icin)

**Neden ayri agent:** Extraction islemi basit bir tool call'dan fazlasini
gerektirir. Belge tipine gore strateji degistirmeli, belirsiz durumlarda
wiki'ye danismali, birden fazla adimda calisabilmeli.

**Mimarisi:** Henuz netlestirilmedi. Secenekler:
- Workflow node icinde LLM-powered agent (wiki'deki tanimlar + oruntuler context olarak)
- MCP Gateway uzerinden A2A iletisim
- SelfEvolvingAgent'in subagent'i olarak

### 3. Chat Agent (planlanmis)

Olusturulmus KB'yi kullanarak kullanici sorularini yanitlar.

**Sorumluluklar:**
- GraphRAG endpoint uzerinden KG'ye soru sorma
- Kullaniciya domain-specific cevaplar verme
- KB'deki bilgiyi arama ve sentezleme

**Iliskisi:** Builder Agent ile ayni platformda yasir ama **ayri bir agent
instance'i** olarak calisir. Builder Agent KB olusturur ve yayinlar;
Chat Agent yayinlanmis KB'yi tuketir.

---

## Islem Akisi

### Asama 1: Kesif

```
Kullanici                    Builder Agent
    │                             │
    │  "Ticaret sicil gazeteleri  │
    │   uzerinde calisacagiz"     │
    ├────────────────────────────►│
    │                             │
    │  "Anliyorum. Ornek 2-3      │
    │   belge paylasir misiniz?   │
    │   Iceriklerini analiz       │
    │   edeyim."                  │
    │◄────────────────────────────┤
    │                             │
    │  [3 PDF yukler]             │
    ├────────────────────────────►│
    │                             │
    │             ┌───────────────┤
    │             │ OCR calistir  │
    │             │ Metni analiz  │
    │             │ et            │
    │             └───────────────┤
    │                             │
    │  "Bu belgelerde sunlar var: │
    │   - Sirket bilgileri        │
    │   - Genel kurul kararlari   │
    │   - Sermaye degisiklikleri  │
    │                             │
    │   Ne cikarilmasini          │
    │   istiyorsunuz?"            │
    │◄────────────────────────────┤
    │                             │
    │  "Sirket bilgileri ve       │
    │   sermaye degisikliklerini  │
    │   cikart"                   │
    ├────────────────────────────►│
    │                             │
```

Builder Agent ornek belgeleri OCR eder, icerigini analiz eder ve
kullaniciyla ne cikarilacagini tartisir. Bu asamada domain netlesir.

### Asama 2: Tasarim

```
Builder Agent
    │
    ├── Wiki'ye domain bilgisi yazar
    │   (entity tanimlari, relationship tanimlari)
    │
    ├── Belge tipine gore OCR stratejisi belirler
    │   (kac asamali OCR? layout analizi gerekli mi?)
    │
    ├── Workflow olusturur:
    │   [Kaynaklar] → [OCR] → [Extraction Agent] → [Kalite Kapisi] → [KG Yaz] → [Yayinla]
    │
    └── Kullaniciya planini sunar, onay alir
```

Agent, domain'e gore uygun node'lari secer. Bazi domain'ler icin
2 asamali OCR gerekebilir, bazilari icin tek asama yeterlidir.
Agent buna ornek belgelere bakarak karar verir.

**Workflow node'lari tool veya agent olabilir:**
- Tool node: Kaynaklar (dosya yukle), KG Yaz, Yayinla — basit, deterministik
- Agent node: Extraction Agent — akilli, wiki-fed (entity/relationship tanimlarina gore calisir)

### Asama 3: Isleme

```
Kullanici tum belgeleri yukler (ornegin 10.000 PDF)
    │
    ▼
Workflow calistirilir
    │
    ├── resources_input: Dosya listesini olusturur
    │
    ├── ocr_step: Her dosya icin Celery task tetikler
    │   └── process_gemini_ocr (mevcut Celery task, calisiyor)
    │
    ├── extraction_agent: Wiki'deki entity/relationship tanimlari ile cikarir
    │   └── Celery uzerinden LLM-based extraction
    │   └── Ogrenilenleri wiki'ye yazar
    │
    ├── quality_gate: Confidence threshold kontrolu
    │   └── Basarisizlari karantinaya alir
    │
    ├── kg_writer: Neo4j'ye yazar
    │   └── neo4j_write_task (mevcut Celery task, calisiyor)
    │
    └── publish_graphrag_endpoint: KB'yi yayinlar
```

**Celery bridge mekanizmasi:** Her workflow node agir isleri kendisi yapmaz.
Mevcut calisan Celery task'lari tetikler ve sonuclarini bekler. Boylece:
- Sifirdan yeni OCR/extraction kodu yazilmaz
- Celery'nin worker scaling avantajindan faydalanilir
- 10K belge icin zaten optimize edilmis kuyruk altyapisi kullanilir

### Asama 4: Yayinlama

Islem tamamlandiginda:
- Knowledge Graph Neo4j'de hazir
- GraphRAG endpoint yayinlanmis
- Chat Agent bu endpoint'i kullanarak sorulari yanitlayabilir
- Wiki'de isleme sirasinda biriken bilgiler mevcut

---

## Workflow Sistemi

### Node Tipleri

Iki tur node vardir:

**Tool Node — deterministik, basit islem:**
| Node | Gorev |
|------|-------|
| `resources_input` | Dosya listesi olusturur (DB'den veya parametre ile) |
| `quality_gate` | Confidence threshold kontrolu, pass/fail |
| `kg_writer` | Neo4j'ye entity/relationship yazar (Celery bridge) |
| `publish_graphrag_endpoint` | KB'yi GraphRAG endpoint olarak yayinlar |

**Agent Node — akilli, adaptif islem:**
| Node | Gorev |
|------|-------|
| `ocr_step` | Belge tipine gore OCR stratejisi uygular (Celery bridge) |
| `extraction_agent` | Wiki-fed entity/relation cikarimi (wiki'deki tanimlara gore) |
| `wiki_builder` | OCR sonuclarindan wiki sayfasi uretir (LLM ile) |
| `ontology_designer` | Mevcut bilgiden entity/relationship tanim onerisi uretir (wiki'ye yazar) |

### Builder Agent'in Workflow Tasarlama Sureci

1. Agent `list_node_types` tool'u ile mevcut node'lari gorur
2. Domain analizi ve ornek belgelere gore hangi node'larin gerektigine karar verir
3. `add_node` ile node'lari ekler
4. `connect_nodes` ile akilsi bastar
5. `configure_node` ile her node'un parametrelerini ayarlar
6. `validate_workflow` ile yapisal kontrol yapar
7. Kullaniciya sunar, onay alir
8. `publish_workflow` ile kesinlestirir

### Celery Bridge

Workflow node'lari agir isleri Celery'ye delege eder:

```
Workflow Node                    Celery
    │                              │
    │  task_id = send_task(        │
    │    "src.tasks.chunk_file",   │
    │    args=[file_id])           │
    ├─────────────────────────────►│
    │                              │ Gemini OCR calisir
    │                              │ ...
    │  poll / callback             │
    │◄─────────────────────────────┤
    │                              │
    │  sonuc al, bir sonraki       │
    │  node'a ilet                 │
    │                              │
```

Mevcut calisan Celery task'lar:
- `src.tasks.chunk_file_task` — Gemini OCR + markdown chunking
- `src.tasks.create_graph_task` — LLM graph extraction -> Neo4j
- `src.tasks.entity_resolution_task` — Entity resolution
- `src.workspace_tasks.workspace.ocr_pages` — Workspace OCR
- `src.neo4j_writer.neo4j_write_task` — Neo4j atomic writes

---

## Wiki Entegrasyonu

Wiki, agent'in **birincil bilgi deposu** ve **isleme hafizasi**dir.
Domain bilgisi, ontoloji (entity/node tanimlari ve relationship tanimlari),
oruntuler ve ogrenilen kurallar wiki'de yasar.

### Wiki'de Saklananlar

| Kategori | Icerik | Ornek |
|----------|--------|-------|
| Domain bilgisi | Alanin genel tanimi, amaci | "Ticaret sicil gazeteleri analizi" |
| Entity (node) tanimlari | Cikarilacak varlik tipleri ve property'leri | `Sirket {unvan, vergi_no, adres}` |
| Relationship tanimlari | Entity'ler arasi iliski tipleri | `SERMAYE_DEGISIKLIGI(Sirket, Tutar, Tarih)` |
| Constraint'ler | Gecerlilik kurallari | "vergi_no 10 haneli olmali" |
| Belge oruntuleri | Belge tiplerinin yapisi | "Genel kurul kararlari genelde 2. sayfada baslar" |
| Isleme taktikleri | OCR/extraction sirasinda ogrenilen yontemler | "Tablo satırları icin 2 asamali OCR daha basarili" |
| Hata kayitlari | Yapilan hatalar ve duzeltmeler | "X formatinda Y alani bos gelebilir, skip etme" |

### Yazma

Builder Agent domain kesfederken wiki'ye yazar:
- Kullaniciyla tartisilan entity ve relationship tanimlari
- Ornek belgelerden cikarilan oruntuler
- Belirlenen constraint'ler ve kurallar

Extraction Agent isleme sirasinda wiki'ye yazar:
- Belge tipi oruntuleri ("Bu tipteki belgelerde X alani genelde Y formatinda")
- Cikarilan entity ornekleri ve iliskileri
- Hata yapilan ve duzeltilen durumlar
- Domain-specific kurallar ve istisnalar

### Okuma

Wiki bilgisi su asamalarda okunur:
- **Workflow tasarimi:** Builder Agent entity/relationship tanimlarini wiki'den
  okur, workflow node'larini buna gore konfigure eder
- **Extraction:** Extraction Agent wiki'deki entity/relationship tanimlarini ve
  oruntuleri prompt'una dahil eder
- **Sonraki belgeler:** Onceki hatalardan ogrenilmis kurallar uygulanir,
  domain-specific baga gore cikarim stratejisi uyarlanir

Bu dongu sayesinde agent 10K belge islerken **giderek iyilesir**.
Ilk 100 belgedeki ogrenmeler kalan 9900 belgeye uygulanir.

### Ontoloji = Wiki

Geleneksel anlamda ayri bir "ontoloji deposu" yoktur. `add_entity_class`,
`add_relationship_predicate` gibi tool'lar aslinda wiki'ye yazan
tool'lardir. Ontoloji wiki sayfalari olarak saklanir ve versiyonlanir.

---

## Izleme

### Batch Ozet

Dashboard'da ve workflow UI'da gorunur:
- Toplam belge sayisi, islenen, bekleyen, hatali
- Yuzde ilerleme
- Tahmini kalan sure
- Asama bazli dagilim (OCR'da kac belge, extraction'da kac belge)

### Belge Bazli Detay

Her belge icin ayri durum:
- `pending` — Kuyrukta bekliyor
- `ocr_processing` — OCR isleniyor
- `ocr_completed` — OCR tamamlandi
- `extracting` — Entity/relation cikarimi yapiliyor
- `extracted` — Cikarim tamamlandi
- `writing_kg` — Neo4j'ye yaziliyor
- `completed` — Tamamlandi
- `quarantined` — Hata, karantinada

### Karantina

Hatali belgeler islem akisini DURDURMAZ:
- Basarisiz olan belgeler `quarantined` olarak isaretlenir
- Geri kalan belgeler islenmeye devam eder
- Isleme bittiginde karantina raporu olusturulur
- Kullanici karantinadaki belgeleri inceleyebilir: retry, skip veya manual fix

### Agent Uzerinden Izleme

Builder Agent tool'lariyla da durum sorgulanabilir:
- `get_batch_progress` — Batch durumu
- Chat icinden: "Isleme nasil gidiyor?" -> Agent batch durumunu sorgular ve ozetler

---

## Mimari Prensipler

### 1. DOMAIN-AGNOSTIC PRENSIBI (en kritik kural)

**Agent-builder herhangi bir spesifik domain icin hardcoded kod barindirmaz.**

Kullanici platformu sigorta policeleri, ticaret sicili gazeteleri, hastane
kayitlari, hukuki sozlesmeler veya bambaska bir alan icin kullanabilir.
Domain'i AGENT degil **KULLANICI + AGENT ortaklasa** belirler; bu karar
**wiki**'de yasar (entity tanimlari, relationship tanimlari, kurallar).

**Yasak:**
- Tool'larda, prompt'larda, helper fonksiyonlarda hardcoded domain terimi
- "Bu bir sirket ilanidir" gibi varsayim yapan prompt metinleri
- Tool descriptor'larinda domain-spesifik terim

**Zorunlu:**
- Tool argumanlari wiki'deki entity/relationship tanimlarindan dinamik beslenmeli
- Prompt sablonlari wiki'yi runtime'da okur ve semayi oradan insa eder
- Cikti formatlari wiki'deki entity property'lerine uyarlanir

**Test sorusu:** "Bu kod sigortacilik agent'inda da, hukuk agent'inda da,
saglik agent'inda da DEGISIKLIK YAPMADAN calisir mi?" Cevap "hayir"sa kod
yanlis yerdedir.

### 2. SORGU vs YAPILANDIRMA AYRIMI

- **Sorgu** ("ozet ver", "bu belgede ne yaziyor"): OCR metni oku, natural
  language cevap ver. Ontolojiye dokunma.
- **Yapilandirma** ("kayitlari cikart", "yapilandirilmis ver"):
  `extract_records_from_ocr` cagir. Wiki'deki entity tanimlarina gore .md
  dosyalari uretir.

### 3. ONTOLOJI MUTASYONU SADECE KULLANICI ONAYIYLA

Wiki'deki entity/relationship tanimlarini degistiren tool'lar (`add_entity_class`,
`add_relationship_predicate` vb.) agent'in kendi inisiyatifiyle CAGRILMAZ.
Yalnizca kullanici acikca istediginde veya kullanici onayli plan adimi olarak
cagrilir. Paralel wiki mutasyonu yasaktir (DB race condition).

### 4. PERSISTENCE & STREAMING

- Agent state `agent_knowledge` tablosunda versiyonlu saklanir
- Conversation state `langgraph.checkpointer` (postgres) icinde durur
- OCR sonuclari `knowledge_type='ocr_result'` altinda yasar
- Yapilandirilmis cikartmalar `knowledge_type='extracted_records'` altinda
- `.md` dosyalari `EXTRACTION_DIR` altinda tutulur

### 5. CHAT FORMAT

- Tool call'lar `tool -> thought -> response` siralamasinda yayinlanir
- Resource polling: bekleyen OCR varsa 8s, yoksa 60s; SSE notification
  geldiginde event-driven refresh
- Default mod **agent**; plan modu `request_plan_mode` ile acilir

### 6. SOFT DELETE

Agent silme default olarak soft delete'tir. `agent_lifecycle` tablosunda
`deleted_at` set edilir; data korunur, restore mumkundur. `?purge=true`
ile hard delete yapilir (geri alinamaz).

### 7. CONFIDENCE TRIPLE-LABELLING (graphify-adopted)

Numeric `confidence` (0.0–1.0) artik tek basina karar vermek icin yetersiz
sayilir. Her extraction kaydi (entity, relation, mention) ek olarak **kategorik**
bir `confidence_label` tasir: `EXTRACTED` / `INFERRED` / `AMBIGUOUS`.

**Anlamlari:**
- `EXTRACTED`: kaynaktan birebir geldi (gazetteer hit, exact-match lookup,
  JSON'dan dogrudan alinti). Dogrulamaya gerek yok.
- `INFERRED`: algoritmik tahmin (fuzzy match, vector similarity, LLM'in
  pattern'den cikardigi, NER'in ilk gordugu). Numeric esik uygulanir.
- `AMBIGUOUS`: birden fazla aday esit, celiski. Quality gate ne olursa olsun
  HUMAN REVIEW'a yonlenir.

**Quality gate iki politika destekler:**
- `numeric_only` (legacy): yalnizca `avg_confidence >= threshold`
- `label_aware` (default, onerilen): AMBIGUOUS sayisi `max_ambiguous` esigini
  asarsa veri ayri bir `ambiguous` output port'una gider; downstream
  `human_review` node'una baglanir.

**Wire format:** `ConfidenceLabel` enum'u string-based (str subclass), kb-overlay
ile JSON serialization wire-compatible. Iki taraf ayni 3 string degeri uretir
ve tuketir; paket bazli kopya enum, shared lib degil (paketler farkli
virtualenv'lerde calistigi icin tercih edildi).

**Dosya konumlari:**
- `kb-overlay/src/kb_overlay/confidence.py` — kaynak tanim
- `agent-builder/backend/src/agent/evolving/confidence.py` — kopya + helpers
  (`label_from_numeric`, `coerce_label`)

### 8. STRICT SCHEMA + RETRY POLICY (graphify-adopted)

LLM'den gelen yapilandirilmis JSON ciktilarinda **`extra="forbid"`** zorunlu.
LLM'in halusinasyon-eklediği field'lar sessizce kabul edilmez.

**3-asamali validation policy** (`RelationExtractor.extract`):
1. Strict full-response validation (`extra="forbid"`)
2. Fail olursa: per-relation salvage — gecerli relation'lari tut, fail
   olanlari discard et
3. Salvage de bos donerse: **1 retry** sıkilastirilmis prompt ile (system
   prompt'a "ekstra alan ekleme" feedback ekle)

`IngestionResult.discarded_relations` ve `validation_recovered` field'lari
quality monitoring'e telemetri saglar.

### 9. INGEST CACHE — SHA-256 + EXTRACTOR VERSION (graphify-adopted)

Belge yeniden ingest edildiginde **icerigi degismediyse VE extractor
konfigurasyonu degismediyse** LLM cagrisi tamamen atlanir. Cache key:

```
(doc_id, sha256(content)[:16], extractor_version)
```

`extractor_version` = `ner=...;schema=...;llm=...;model=...`. NER backend
veya LLM model guncellemesi otomatik invalidation tetikler. Manuel bypass:
`--force` flag.

**SQLite tablosu:** `ingest_cache` (kb-overlay schema v2). agent-builder
tarafinda henuz yok (Celery worker text-hash check'i bagimsiz olarak
implement edilebilir; bu konu acik soru).

---

## Acik Sorular

Asagidaki konular henuz kesinlesmedi, gelistirme sirasinda netlestirilecek:

### Extraction Agent Mimarisi
- Workflow node icinde inline LLM agent mi?
- MCP Gateway uzerinden A2A iletisim mi?
- SelfEvolvingAgent'in subagent'i mi?
- Wiki'deki entity/relationship tanimlari extraction prompt'una nasil enjekte edilecek?

### Chat Agent Entegrasyonu
- Ayri bir frontend sayfasi mi, mevcut chat mi?
- Builder Agent Chat Agent'i otomatik mi olusturacak?
- Chat Agent'in Builder Agent ile iliskisi nasil yonetilecek?

### MCP Gateway Rolu
- Extraction icin MCP tool mu kullanilacak?
- Mevcut A2A proxy'leri (Finans, Test) kalacak mi, kaldirilacak mi?
- Yeni agent'lar MCP Gateway'e nasil kayit olacak?

### Multi-stage OCR Detayi
- Kac asamali OCR sablonlari tanimlanacak mi?
- Yoksa tamamen agent'in kararima mi birakilacak?
- Layout analizi icin ayri bir model/tool gerekli mi?

### Olcekleme
- 10K+ belge icin Celery worker sayisi nasil yonetilecek?
- Rate limiting (Gemini API, Neo4j) nasil handle edilecek?
- Belge onceliklandirma (bazi belgeler once islenmeli) desteklenecek mi?
