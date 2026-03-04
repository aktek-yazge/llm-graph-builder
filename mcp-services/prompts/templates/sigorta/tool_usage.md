<tool_usage>
## TOOL KULLANIM REHBERI (CYPHER MODE)

### execute_cypher_query(cypher, step_name) - METADATA SORGUSU
**NE ZAMAN:** Graph node/iliski sorgulari, metadata, sayisal bilgiler

## STRING ARAMASI

**KURAL: Text aramasi yaparken HER ZAMAN `apoc.text.clean()` kullan!**

**NEDEN?** Unicode karakterler, buyuk/kucuk harf, fazla bosluklar sorun yaratir.

```cypher
-- DOGRU: apoc.text.clean() ile temizle
WHERE apoc.text.clean(n.name) CONTAINS apoc.text.clean('aranan terim')

-- YANLIS: Direkt CONTAINS
WHERE n.name CONTAINS 'aranan terim'
WHERE toLower(n.name) CONTAINS 'terim'  -- toLower Unicode'da guvenilmez!
```

**apoc.text.clean():** Kucuk harf + bosluk temizleme + Unicode normallestirme

**Sablonlar:**
```cypher
-- KESIF (node label'i SEMADAN al!)
MATCH (n:NodeLabel)
WHERE apoc.text.clean(n.name) CONTAINS apoc.text.clean('aranan')
RETURN DISTINCT n.name

-- ANA SORGU: Kesifte bulunan EXACT degeri kullan
WHERE n.name = 'Kesifte Bulunan Deger'  -- Exact match, clean gerekmez
```

---

### execute_cypher_query - FULLTEXT ARAMA (Hizli Kelime Aramasi)
**NE ZAMAN:** 
- Chunk iceriginde hizli kelime aramasi gerektiginde
- Yazim hatalarina toleransli arama (fuzzy ~)
- Spesifik kelime/terim aramasi (semantic'ten daha kesin)

**KRiTiK: KAVRAM VARYASYONLARI KULLAN!**

| Kullanici Terimi | Fulltext Query |
|------------------|----------------|
| fiyat | `fiyat OR tutar OR bedel OR ucret OR "toplam tutar"` |
| odeme | `odeme OR taksit OR "odeme plani" OR "aylik odeme"` |
| tarih | `tarih OR "baslangic tarihi" OR "bitis tarihi" OR sure` |
| detay | `detay OR icerik OR aciklama OR bilgi` |

**FULLTEXT SYNTAX:**
- Index adi: `'chunk_text_fulltext'`
- Normal arama: `'kelime1 kelime2'` (AND implicit)
- OR operatoru: `'kelime1 OR kelime2'`
- Phrase arama: `'"tam eslesme ifade"'`
- Fuzzy arama: `'kelime~'` **DIKKATLI KULLAN** - Gurultulu sonuc verebilir!

**ONERILEN KULLANIM (Normal + OR):**
```cypher
CALL db.index.fulltext.queryNodes('chunk_text_fulltext', 'terim1 OR terim2 OR "tam ifade"')
YIELD node AS c, score
WHERE score > 1.0
RETURN c.text, score, c.fileName
ORDER BY score DESC LIMIT 10
```

**FILTRELI MOD (Entity ile - ONERILEN, iliski adlarini SEMADAN al!):**
```cypher
CALL db.index.fulltext.queryNodes('chunk_text_fulltext', 'terim1 OR terim2')
YIELD node AS c, score
WHERE score > 0.5
MATCH (c)-[:ILISKI_ADI]->(d:Document)<-[:ILISKI_ADI]-(e:EntityNode)
WHERE e.name CONTAINS 'deger'
RETURN c.text, score, d.fileName
ORDER BY score DESC LIMIT 10
```

---

### ARAMA STRATEJISI - HANGI YONTEM NE ZAMAN?

| Soru Tipi | Yontem | Neden? |
|-----------|--------|--------|
| **Kavramsal** ("X nedir?", "X hakkinda bilgi") | **SEMANTIC** | Anlam bazli esleme, tablo/liste bulur |
| **Spesifik terim** (tarih, kod, numara, isim) | **FULLTEXT** | Kesin kelime eslesmesi gerekir |
| **Var mi/yok mu sorulari** | **FULLTEXT** | Keyword aramasi daha kesin |
| **Detay/liste/tablo istekleri** | **SEMANTIC** | Yapisal veriyi iyi bulur |

---

### execute_cypher_query_with_embedding(query_text, cypher, step_name) - SEMANTIK ARAMA
**NE ZAMAN:** Dokuman iceriginde arama, detay/liste/tablo istekleri

**KRiTiK KURALLAR:**
1. Vector index adi: `'vector'` (sabit)
2. `$embedding_vector` parametresi ZORUNLU
3. `YIELD node AS c, score` formati ZORUNLU
4. query_text = KAVRAM (isim, tarih, kod DEGIL!)

---

### expand_chunk_context(document_name, positions, window_size) - CONTEXT GENISLETME
**NE ZAMAN:** 
- Arama sonucunda tablo/liste kesilmis gorunuyorsa
- "devami var", "..." gibi ifadeler varsa
- Chunk'in etrafindaki context'i gormek istiyorsan

</tool_usage>
