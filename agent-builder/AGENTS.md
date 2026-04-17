# Agent Builder — Mimari Prensipleri

Bu dosya agent-builder uygulamasinin **kalici mimari kurallarini** icerir. Hem
insan gelistiricilere hem de bu kod tabaninda calisan AI agent'lara yoneliktir.
Yeni bir feature/tool/prompt yazarken bu kurallari ihlal etmek yasak.

---

## 1. DOMAIN-AGNOSTIC PRENSIBI (en kritik kural)

**Agent-builder herhangi bir spesifik domain icin hardcoded kod barindirmaz.**

Kullanici platformu sigorta polçeleri, ticaret sicili gazeteleri, hastane kayitlari,
hukuki sozlesmeler veya bambaska bir alan icin kullanabilir. Domain'i AGENT degil
**KULLANICI + AGENT ortaklasa** belirler; bu karar `AgentOntology` icinde yasar.

### Yasak

- Tool'larda, prompt'larda, helper fonksiyonlarda **hardcoded domain terimi**
  kullanmak yasaktir. Ornekler (yapilmamasi gerekenler):
  - `company_name`, `sirket_unvani`, `policy_number`, `sicil_no`, `patient_id`,
    `genel_kurul`, `tasfiye`, `hisse_devri`, `Türk Ticaret Sicili Gazetesi` ...
- "Bu bir sirket ilanidir" / "bu bir police kaydidir" gibi varsayim yapan prompt
  metinleri yazmak yasaktir.
- Tool descriptor'larinda, function arg isimlerinde domain-spesifik terim yasaktir.

### Zorunlu

- Tool argumanlari ontoloji'den **dinamik** beslenmeli. Ornek:
  ```python
  extract_records_from_ocr(doc_key, target_class, filter_query)
  # target_class -> ontoloji'deki herhangi bir entity sinifi
  # filter_query -> serbest metin filtresi
  ```
- Prompt sablonlari ontolojiyi runtime'da okur ve sema'yi oradan insa eder
  (bkz. `tools/ocr_tools.py::_build_record_extraction_prompt`).
- Cikti formatlari **ontoloji property'lerine** uyarlanir, hardcoded alan
  isimlerine degil.

### Test sorusu

Bir feature yazarken kendine sor: "Bu kod sigortacilik agent'inda da, hukuk
agent'inda da, saglik agent'inda da DEGISIKLIK YAPMADAN calisir mi?" Cevap
"hayir"sa kod yanlis yerdedir.

### Domain'in dogal yasadigi yerler

- Kullanicinin agent'a verdigi `purpose` metni
- `agent.store.load_ontology(agent_id)` icindeki entity siniflari, relationship
  predicate'leri, inference rules, constraints
- Kullanicinin yukledigi orneklerden agent'in cikartip Wiki'ye kaydettigi
  pattern'ler

---

## 2. SORGU vs YAPILANDIRMA AYRIMI

Agent her belge istegini ayni sekilde isleemez:

- **Sorgu** ("ozet ver", "bu belgede X hakkinda ne yaziyor", "bilgi ver"):
  `read_ocr_pages(doc_key)` ile metni oku, dogrudan natural language cevap ver.
  Ontolojiye dokunma, .md dosyasi yaratma.
- **Yapilandirma** ("kayitlari cikart", "X'leri ayikla", "yapilandirilmis sekilde
  ver"): `extract_records_from_ocr(doc_key, target_class, filter_query)` cagir.
  Bu tool ontoloji semasina gore .md dosyalari uretir; sohbete uzun metin yazma.

---

## 3. ONTOLOJI MUTASYONU SADECE KULLANICI ONAYIYLA

`add_entity_class`, `add_relationship_predicate`, `add_inference_rule`,
`add_constraint`, `set_domain_info`, `remove_*` tool'lari agent'in kendi
inisiyatifiyle CAGRILMAZ. Yalnizca:

- Kullanici acikca istemis ("ontoloji kur", "X icin Company sinifi olustur"...)
- Kullanici onayli bir plan adimi olarak listelenmis

durumlarda cagrilir. "Belgeyi analiz et" / "haberi ozetle" gibi sorgular
ontoloji mutasyonu DEGILDIR.

Birden fazla ontoloji tool'unu paralel cagirmak yasaktir (DB race olusur;
bkz. `knowledge_store.upsert` icindeki retry mekanizmasi).

---

## 4. PERSISTENCE & STREAMING DETAYLARI

- Tum agent state'i `agent_knowledge` tablosunda versiyonlu sekilde saklanir.
- Conversation state `langgraph.checkpointer` (postgres) icinde durur. Yeni turn
  basinda `_yielded_tool_call_ids` ve `_yielded_tool_result_ids` set'leri
  checkpoint'ten doldurulur ki **gecmis tool call/result'lar tekrar yayinlanmaz**
  (`self_evolving_agent.py::chat`).
- OCR sonuclari `knowledge_type='ocr_result'`, yapilandirilmis cikartmalar
  `knowledge_type='extracted_records'` altinda yasar.
- `.md` cikartma dosyalari `EXTRACTION_DIR` (default: `/tmp/evolving-extractions/<agent_id>/`)
  altinda tutulur.

---

## 5. CHAT FORMAT KURALLARI

- Tool call'lar `tool -> thought -> response` siralamasinda yayinlanir; tool
  call'i once compact card olarak gosterilir, ustune basilinca acilir.
- Resource panelindeki polling adaptiftir: bekleyen OCR varsa `8s`, yoksa `60s`.
  SSE notification geldiginde event-driven refresh tetiklenir.
- Default mod **agent (uygulama)**'dir; plan modu sadece uzun/karmasik isler
  icin `request_plan_mode` ile kullanici onayindan sonra acilir.

---

## 6. SOFT DELETE

Agent silme islemleri default olarak **soft delete**'tir. `agent_lifecycle`
tablosunda `deleted_at` set edilir; data korunur, restore mumkundur. `?purge=true`
ile hard delete yapilir; bu geri alinamaz.
