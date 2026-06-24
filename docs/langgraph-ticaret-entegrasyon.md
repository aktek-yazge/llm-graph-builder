# LangGraph Agent → Ticaret Sicili Backend Entegrasyon Spec'i

> Bu doküman **uzaktaki LangGraph sunucusundaki** Claude sohbetine verilmek üzere hazırlandı.
> Amaç: LangGraph agent'ı, kullanıcı **ticaret sicili gazeteleri** ile ilgili bir soru sorduğunda,
> bu işi kendi içinde yapmak yerine **ayrı bir backend servisindeki ReAct agent'ına** HTTP ile
> devretsin (tıpkı qdrant search tool'unu çağırdığı gibi, ama bu sefer bir HTTP tool olarak).

---

## 0. Bağlam (ne yapıyoruz)

- Elimizde, **Türkiye Ticaret Sicili Gazetesi** belgelerinden kurulmuş bir Neo4j bilgi grafiği var
  (şirketler, kişiler, görevler, sermaye/hisse, tarihler, adresler, kurumlar, noterler, sicil/vergi no).
- Bu grafiğin üzerinde çalışan, domain'e özel prompt'larla ince ayar yapılmış bir **ReAct agent**
  zaten bir backend uygulamasında **HTTP endpoint** olarak servis ediliyor:
  `POST /chat_bot_stream`. Bu endpoint Cypher üretimini, şema bilgisini, "teknik terim gösterme /
  embedding kullanma" kurallarını **kendi içinde** halleder.
- **Senin görevin (LangGraph tarafı):** Bu endpoint'i çağıran bir **tool** ekle ve agent'ın,
  ticaret sicili / gazete / şirket kuruluş-tescil türü sorularda bu tool'u seçmesini sağla.
- **ÖNEMLİ:** Bu sorularda Cypher/şema/embedding'i SEN üretme. Sadece kullanıcının doğal dildeki
  sorusunu bu endpoint'e ilet, dönen Türkçe cevabı kullanıcıya/akışa geri ver. Zekâ karşı tarafta.

---

## 1. HTTP Sözleşmesi (endpoint'in tam davranışı)

**İstek**

| Alan | Değer |
|---|---|
| Method | `POST` |
| URL | `{TICARET_BASE_URL}/chat_bot_stream` |
| Content-Type | `multipart/form-data` (form alanları) |
| Yanıt | **SSE** (Server-Sent Events) — `text/event-stream`, her satır `data: {json}\n\n` |

**Gönderilecek form alanları**

| Alan | Zorunlu | Ne göndermeli |
|---|---|---|
| `question` | ✅ | Kullanıcının doğal dildeki sorusu (Türkçe). Değiştirme, olduğu gibi ilet. |
| `session_id` | ✅ | Konuşma kimliği. LangGraph'taki thread/conversation id'sini geçir; yoksa `uuid4()` üret. |
| `domain` | ✅ | Sabit: `ticaret` |
| `agent_type` | ⬜ | Varsayılan `deep_agent` (ReAct'i tetikler). Göndermesen de olur, gönderirsen `deep_agent`. |
| `chat_history` | ⬜ | Tek-history (A2): `state.messages`'tan son N soru/cevap. JSON string: `[{"role":"user"/"assistant","content":"..."}]`, eskiden yeniye. Detay §4b. |
| `question_id` | ⬜ | Log korelasyonu için opsiyonel benzersiz id. |
| `user_id` | ⬜ | Varsa kullanıcı kimliği (izleme için). |

**❌ GÖNDERME (kritik):**
- `uri`, `userName`, `password`, `database` → **gönderme.** Neo4j kimlik bilgileri sunucunun kendi
  ortam değişkenlerinden gelir. Gönderirsen göz ardı edilir; karıştırma.
- `mode=graph` → **ASLA gönderme.** `mode="graph"` sunucuda APOC gerektiren bir şema yenilemesini
  tetikler ve bu veritabanında APOC yoktur → istek patlar. `mode` alanını ya hiç gönderme ya da
  `graph` dışında bir değer ver. **Boş bırak.**

**Yanıt (SSE event tipleri)** — her `data:` satırı bir JSON; `type` alanına bak:

| `type` | Anlamı | İlgili alanlar |
|---|---|---|
| `status` | İlerleme bildirimi | `message`, `status` |
| `thinking_step` | Ara akıl yürütme adımı (loglanabilir) | `message` |
| `message_chunk` | Cevabın token-token akışı | `content`, `full_message`, `is_final_answer` |
| `final_response` | **NİHAİ CEVAP** | `content` (cevap metni), `sources` `{documents:[], pages:[]}`, `metrics` |
| `timing` | Süre/tüketim özeti | `elapsed_time`, `total_tokens` |
| `error` | Hata | `message`, `error` |

### 1.1. Event'lerin tam JSON yapısı (gerçek koddan)

Akış sırası tipik olarak: birkaç `status` → birkaç `thinking_step` → çok sayıda `message_chunk`
→ **bir** `final_response` → bir `timing`. Her biri ayrı bir `data: {...}\n\n` satırıdır.

```jsonc
// status (sunucu ilerleme bildirimi)
{"type": "status", "message": "🚀 ReAct Agent ile işleniyor...", "status": "react_agent_processing"}

// thinking_step (kullanıcı-dostu ara adım; teknik değil)
{"type": "thinking_step", "message": "Belgeler aranıyor...", "session_id": "abc"}

// message_chunk (cevabın parça parça akışı — delta)
{"type": "message_chunk", "content": "Aksa'nın ", "full_message": "Aksa'nın ",
 "is_final_answer": true, "session_id": "abc"}

// final_response (NİHAİ CEVAP — entegrasyonda kullanacağın event budur)
{
  "type": "final_response",
  "content": "Aksa'nın 1990 yılına ait 12 belgesi bulunmaktadır. ...",
  "sources": {
    "documents": [
      {
        "filename": "Aksa-21.04.1980-382-ANONİM ŞİRKET (YÖNETİM - TEMSİL VE DİĞER)",
        "page": 1,
        "file_link": "https://.../images/aksa_..._page_1.png",
        "thumbnail": null,
        "firm": "Aksa",
        "year": "1980",
        "gazette_type": "ANONİM ŞİRKET (YÖNETİM - TEMSİL VE DİĞER)",
        "gazette_no": "382"
      }
    ]
  },
  "metrics": {
    "total_time": 4.21, "tool_calls": 2, "llm_calls": 3,
    "input_tokens": 5123, "output_tokens": 210, "cached_tokens": 0,
    "total_tokens": 5333, "cache_hit_rate": 0.0, "estimated_cost_usd": 0.01,
    "hallucination_warning": false, "redis_cache_active": false,
    "few_shot_examples_used": 0, "llm_judge": "background"
  },
  "tool_calls_detail": [ /* iç tool çağrı kayıtları — entegrasyonda gerekmez */ ],
  "session_id": "abc",
  "timestamp": "2026-06-22T12:58:14.123456"
}

// timing (akışın sonunda özet)
{"type": "timing", "status": "finished", "elapsed_time": "4.21",
 "total_tokens": 5333, "timestamp": "..."}

// error (hata olursa final_response yerine bu gelir)
{"type": "error", "status": "error", "message": "Streaming sırasında bir hata oluştu",
 "error": "<detay>", "timestamp": "..."}
```

> **Entegrasyon için yalnızca `final_response` yeterlidir:** `content` = değerlendirici LLM'in
> okuyacağı olgusal cevap, `sources.documents` = yapılandırılmış kaynak belge nesneleri.
> `metrics` ve `tool_calls_detail` opsiyoneldir (izleme/log için).

**`sources.documents` alan eşlemesi (ticaret domain):** her öğe bir kaynak sayfadır.

| Alan | İçerik | Sizdeki hedef |
|---|---|---|
| `file_link` | Sayfanın görsel URL'i — `Chunk.page_link` (tam URL, doğrudan) | `MultimodalDocItem.FileLink` — `null` olabilir (page_link boşsa) |
| `filename` | Belge adı | `Filename` / `Source` |
| `page` | Sayfa numarası | `Page` (`null` olabilir) |
| `thumbnail` | Küçük görsel (genelde `null` → `file_link` kullanın) | `Thumbnail` (opsiyonel) |
| `firm` | Belge adından parse edilmiş firma | `DocFilters.firm_keywords` |
| `year` | Belge adından parse edilmiş yıl | `DocFilters.year_keywords` |
| `gazette_type` | Belge adından parse edilmiş tip | `DocFilters.document_keywords` |
| `gazette_no` | Gazete sayısı | (ek meta) |

> Not: `file_link` doğrudan `Chunk.page_link`'ten gelir (tam URL olarak tutuluyor, dönüştürme yok);
> boşsa `null`. Backend `firm/year/gazette_type/gazette_no`'yu belge adından parse eder; LLM
> yalnızca `filename + page + page_link` taşır.

**Cevabı çıkarma kuralı:**
1. `type == "final_response"` event'inin `content` alanı = nihai cevap. **Bunu kullan.**
2. `sources.documents[]` = yapılandırılmış kaynaklar (yukarıdaki tablo) → DocFilters + panel görseli.
3. Yedek: `final_response` hiç gelmezse, tüm `message_chunk` event'lerinin `content`'lerini sırayla
   birleştir.
4. `error` event'i gelirse mesajı hata olarak yukarı taşı.

---

## 2. Referans HTTP istemcisi (Python, httpx — SSE topla → tek cevap döndür)

> LangGraph tool'unun gövdesinde bunu kullan. Streaming'i içeride yutar, dışarıya **tek bir
> nihai cevap** verir (tool çağrısı için en temizi budur).

```python
import os, json, uuid
import httpx

TICARET_BASE_URL = os.environ.get("TICARET_BASE_URL", "http://CANLI_SUNUCU_ADRESI:PORT")

async def ticaret_sicili_sorgula(
    soru: str,
    session_id: str | None = None,
    gecmis: list[dict] | None = None,  # state.messages'tan son N {role, content}
) -> dict:
    """Ticaret sicili gazeteleri backend'ine soruyu iletir, nihai cevabı döndürür."""
    session_id = session_id or str(uuid.uuid4())
    data = {
        "question": soru,
        "session_id": session_id,
        "domain": "ticaret",
        "agent_type": "deep_agent",
        # mode GÖNDERİLMİYOR (graph modunu tetiklememek için)
    }
    # Tek-history (A2): geçmişi backend LLM'i görsün diye gönder (bkz. §4b)
    if gecmis:
        data["chat_history"] = json.dumps(gecmis[-30:], ensure_ascii=False)
    cevap, kaynaklar, hata = "", {"documents": [], "pages": []}, None
    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream("POST", f"{TICARET_BASE_URL}/chat_bot_stream", data=data) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                try:
                    evt = json.loads(line[len("data:"):].strip())
                except json.JSONDecodeError:
                    continue
                t = evt.get("type")
                if t == "final_response":
                    cevap = evt.get("content") or cevap
                    kaynaklar = evt.get("sources") or kaynaklar
                elif t == "message_chunk" and not cevap:
                    cevap += evt.get("content") or ""
                elif t == "error":
                    hata = evt.get("error") or evt.get("message")
    if hata and not cevap:
        return {"ok": False, "error": hata}
    return {"ok": True, "cevap": cevap, "kaynaklar": kaynaklar, "session_id": session_id}
```

> Senkron LangGraph node'u kullanıyorsan aynı mantığı `httpx.Client().stream(...)` ile senkron yaz,
> ya da `asyncio.run(...)` ile sar. (`requests` SSE için uygun değil; `httpx` veya `sseclient` kullan.)

---

## 3. LangGraph tarafında ne ekleyeceksin

1. **Yeni bir tool** tanımla — qdrant search tool'unun yanına, **aynı kalıpta**. Tool gövdesi
   yukarıdaki `ticaret_sicili_sorgula` fonksiyonunu çağırsın.

2. **Tool açıklaması (description) — routing'in kalbi.** Agent'ın LLM'i hangi soruda bu tool'u
   seçeceğine bu metinle karar verir. Önerilen açıklama:

   > "Türkiye Ticaret Sicili Gazetesi belgeleriyle ilgili soruları yanıtlar: şirketlerin kuruluş,
   > tescil, genel kurul, yönetim/temsil, sermaye artırımı, adres değişikliği, fesih ilanları;
   > bir şirketin/kişinin gazete belgeleri, ortaklar, yöneticiler, görevler, sermaye/hisse,
   > sicil ve vergi numaraları, noter ve tarih bilgileri. Şirket, gazete, ticaret sicili,
   > tescil ilanı geçen sorularda bunu kullan. Girdi: kullanıcının sorusu (Türkçe, aynen)."

3. **Routing notu (sistem prompt'una ekleme, opsiyonel ama önerilir):**

   > "Ticaret sicili / gazete ilanı / şirket tescil-kuruluş türü sorularda `ticaret_sicili_sorgula`
   > tool'unu kullan; bu konuyu qdrant ile yanıtlamaya çalışma. Tool'un döndürdüğü Türkçe cevabı
   > olduğu gibi kullan, üzerine teknik terim/şema ekleme."

4. **Ortam değişkeni:** `TICARET_BASE_URL` = ticaret backend'inin canlı adresi
   (örn. `http://10.x.x.x:8000` veya `https://ticaret.dahili.alan`). Şu an dev'de; canlıya
   taşındığında bu değeri güncelle.

---

## 4. Hızlı doğrulama (entegrasyonu kurduktan sonra)

```bash
# 1) Ham endpoint canlı mı + ticaret hattı çalışıyor mu (curl ile SSE):
curl -N -X POST "$TICARET_BASE_URL/chat_bot_stream" \
  -F "question=Aksa'nın kaç belgesi var?" \
  -F "session_id=test-1" \
  -F "domain=ticaret"
# Beklenen: akışın sonunda 'type":"final_response"' içeren, 'content' alanında Türkçe cevap
# (ör. "Aksa'nın 313 belgesi var.") bulunan bir data: satırı.
```

- `final_response.content` doluysa ve doğru cevabı içeriyorsa HTTP sözleşmesi tamam.
- Sonra LangGraph agent'ına ticaret sicili sorusu sor → agent'ın `ticaret_sicili_sorgula`
  tool'unu seçtiğini ve cevabı döndürdüğünü gözle doğrula.
- Alakasız (ör. genel qdrant) bir soru sor → agent'ın bu tool'u **seçmediğini** doğrula
  (routing yanlış pozitif vermemeli).

---

## 4b. Sohbet geçmişi (tek-history, A2) — KİM tutuyor, NASIL gönderiliyor

**Karar: tek history, sahibi LangGraph/Mongo.** Backend kendi sohbet geçmişini Postgres'e
**YAZMAZ** (`REACT_DISABLE_HISTORY=true`). Ama backend'in LLM'i geçmişi **görsün** istiyoruz —
tıpkı eski Postgres mantığında olduğu gibi (her istekte son N soru/cevap mesaj olarak veriliyor).

**Bunu sen sağlıyorsun:** LangGraph kendi `state.messages`'ından **son N soru/cevabı** (örn. 15
soru + 15 cevap) alıp isteğe `chat_history` alanı olarak ekler. Backend bu listeyi yeni sorunun
**önüne** koyar → LLM tüm geçmişi bağlam olarak görür.

**`chat_history` formatı** (JSON string, multipart form alanı):

```json
[
  {"role": "user", "content": "Aksa'nın kaç belgesi var?"},
  {"role": "assistant", "content": "Aksa'nın 42 belgesi var."},
  {"role": "user", "content": "peki bunların kaçı 2023 yılına ait?"},
  {"role": "assistant", "content": "..."}
]
```

- Roller: `user`/`human` → kullanıcı, `assistant`/`ai` → asistan. `system` atlanır.
- **Sıralama: en eskiden en yeniye** (son tur en sonda). Yeni soruyu `chat_history`'e KOYMA;
  o `question` alanında gider.
- **Pencere:** son 15 soru + 15 cevap ≈ 30 mesaj gönder. Backend defansif tavanı
  `REACT_EXTERNAL_HISTORY_LIMIT` (vars. 40) ile son N'i alır.
- LangChain mesajlarından üretim örneği:
  ```python
  hist = [{"role": "user" if m.type == "human" else "assistant",
           "content": m.content}
          for m in state["messages"][-30:]
          if m.type in ("human", "ai")]
  data["chat_history"] = json.dumps(hist, ensure_ascii=False)
  ```
- Bu modda takip sorusunu yeniden formüle etmen ŞART değil; "bunların kaçı 2023?" gibi bağlamlı
  soruyu olduğu gibi gönderebilirsin çünkü backend geçmişi görür. (Yine de net soru hep daha iyi.)

**`session_id` = LangGraph konuşma/thread id'si.** Backend bunu hafıza için kullanmaz (geçmiş
artık `chat_history`'den geliyor); sadece loglarda 1:1 izlenebilirlik sağlar.

**Token cache'i bozmaz:** Cache'lenen pahalı kısım sistem prompt'u + DB şemasıdır ve `create_agent`
ile en başta sabit kalır. `chat_history` mesajları bu prefix'ten SONRA eklenir → prefix değişmez,
cache isabeti korunur. (gpt-5 otomatik prefix cache; sistem prompt'u her çağrıda aynı.)

## 5. Sık karşılaşılacak tuzaklar

| Belirti | Sebep | Çözüm |
|---|---|---|
| İstek APOC/`ProcedureNotFound` hatasıyla patlıyor | `mode=graph` gönderilmiş | `mode` alanını **hiç gönderme** |
| Cevap boş geliyor | Yanlış event tipinden okuma | Cevabı `final_response.content`'ten al (yedek: `message_chunk` birleştir) |
| Yanlış domain prompt'u devrede | `domain` gönderilmemiş ve sunucu varsayılanı farklı | Daima `domain=ticaret` gönder |
| Bağlantı reddi | `TICARET_BASE_URL` yanlış / ağ erişimi yok | Adresi ve iki sunucu arası erişimi doğrula |
| Yanıt çok uzun sürüyor / timeout | ReAct döngüsü ağır soru | İstemci timeout'unu artır (≥120 sn) |

---

## Özet

LangGraph tarafında **tek bir HTTP tool** ekliyorsun; o tool `POST {BASE_URL}/chat_bot_stream`'e
`question + session_id + domain=ticaret` gönderip SSE'den `final_response.content`'i topluyor.
Cypher/şema/embedding işini karşı taraf yapıyor — sen sadece doğru soruda doğru tool'u seçiyorsun.
