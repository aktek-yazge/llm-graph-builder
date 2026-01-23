"""
Sigorta domain'i - Tool kullanım rehberleri.
"""

# =============================================================================
# CYPHER MODE - Tool Usage Guide  
# =============================================================================
CYPHER_TOOL_USAGE = """
<tool_usage>
## 🔧 TOOL KULLANIM REHBERİ (CYPHER MODE)

### execute_cypher_query(cypher, step_name) - METADATA SORGUSU
**NE ZAMAN:** Graph node/ilişki sorguları, metadata, sayısal bilgiler

## 🔤 STRING ARAMASI (ÇOK ÖNEMLİ!)

⚠️ **KURAL: Text araması yaparken HER ZAMAN `apoc.text.clean()` kullan!**

**NEDEN?** Unicode karakterler, büyük/küçük harf, fazla boşluklar sorun yaratır.

```cypher
-- ✅ DOĞRU: apoc.text.clean() ile temizle
WHERE apoc.text.clean(n.name) CONTAINS apoc.text.clean('aranan terim')

-- ❌ YANLIŞ: Direkt CONTAINS
WHERE n.name CONTAINS 'aranan terim'
WHERE toLower(n.name) CONTAINS 'terim'  -- toLower Unicode'da güvenilmez!
```

**apoc.text.clean():** Küçük harf + boşluk temizleme + Unicode normalleştirme

**Şablonlar:**
```cypher
-- KEŞİF (node label'ı ŞEMADAN al!)
MATCH (n:NodeLabel)
WHERE apoc.text.clean(n.name) CONTAINS apoc.text.clean('aranan')
RETURN DISTINCT n.name

-- ANA SORGU: Keşifte bulunan EXACT değeri kullan
WHERE n.name = 'Keşifte Bulunan Değer'  -- Exact match, clean gerekmez
```

---

### execute_cypher_query - FULLTEXT ARAMA (Hızlı Kelime Araması)
**NE ZAMAN:** 
- Chunk içeriğinde hızlı kelime araması gerektiğinde
- Yazım hatalarına toleranslı arama (fuzzy ~)
- Spesifik kelime/terim araması (semantic'ten daha kesin)

⚠️ **KRİTİK: KAVRAM VARYASYONLARI KULLAN!**
Kullanıcının bahsettiği kavramın farklı versiyonlarını OR ile birleştir:

| Kullanıcı Terimi | Fulltext Query |
|------------------|----------------|
| fiyat | `fiyat OR tutar OR bedel OR ücret OR "toplam tutar"` |
| ödeme | `ödeme OR taksit OR "ödeme planı" OR "aylık ödeme"` |
| tarih | `tarih OR "başlangıç tarihi" OR "bitiş tarihi" OR süre` |
| detay | `detay OR içerik OR açıklama OR bilgi` |

**FULLTEXT SYNTAX:**
- Index adı: `'chunk_text_fulltext'`
- Normal arama: `'kelime1 kelime2'` (AND implicit)
- OR operatörü: `'kelime1 OR kelime2'`
- Phrase arama: `'"tam eşleşme ifade"'`
- Fuzzy arama: `'kelime~'` ⚠️ **DİKKATLİ KULLAN** - Gürültülü sonuç verebilir!

**ÖNERİLEN KULLANIM (Normal + OR):**
```cypher
CALL db.index.fulltext.queryNodes('chunk_text_fulltext', 'terim1 OR terim2 OR "tam ifade"')
YIELD node AS c, score
WHERE score > 1.0
RETURN c.text, score, c.fileName
ORDER BY score DESC LIMIT 10
```

**FİLTRELİ MOD (Entity ile - ÖNERİLEN, ilişki adlarını ŞEMADAN al!):**
```cypher
CALL db.index.fulltext.queryNodes('chunk_text_fulltext', 'terim1 OR terim2')
YIELD node AS c, score
WHERE score > 0.5
MATCH (c)-[:ILIŞKI_ADI]->(d:Document)<-[:ILIŞKI_ADI]-(e:EntityNode)
WHERE e.name CONTAINS 'değer'
RETURN c.text, score, d.fileName
ORDER BY score DESC LIMIT 10
```

⚠️ **FUZZY (~) DİKKAT:** Alakasız sonuçlar getirebilir! Sadece kullanıcı yazım hatası yaptığını düşünüyorsan kullan.
⚠️ İlişki adlarını ŞEMADAN al!

---

### 💡 ARAMA STRATEJİSİ - HANGİ YÖNTEM NE ZAMAN?

| Soru Tipi | Yöntem | Neden? |
|-----------|--------|--------|
| **Kavramsal** ("X nedir?", "X hakkında bilgi") | **SEMANTIC** | Anlam bazlı eşleşme, tablo/liste bulur |
| **Spesifik terim** (tarih, kod, numara, isim) | **FULLTEXT** | Kesin kelime eşleşmesi gerekir |
| **Var mı/yok mu soruları** | **FULLTEXT** | Keyword araması daha kesin |
| **Detay/liste/tablo istekleri** | **SEMANTIC** | Yapısal veriyi iyi bulur |

**KARAR AĞACI:**
```
SORU ANALİZİ:
├── Tarih, kod, numara, özel isim → FULLTEXT
├── "var mı?", "mevcut mu?" → FULLTEXT  
├── "nedir?", "neler?", "nasıl?" → SEMANTIC
├── Tablo/liste/detay istiyor → SEMANTIC
└── Emin değilsen → ÖNCE SEMANTIC, sonuç yoksa FULLTEXT
```

⚠️ **FUZZY (~) UYARISI:** Gürültülü sonuç verebilir! Sadece yazım hatası olasılığı varsa kullan.

---

### execute_cypher_query_with_embedding(query_text, cypher, step_name) - SEMANTİK ARAMA
**NE ZAMAN:** Doküman içeriğinde arama, detay/liste/tablo istekleri

⚠️ **KRİTİK KURALLAR:**
1. Vector index adı: `'vector'` (sabit)
2. `$embedding_vector` parametresi ZORUNLU
3. `YIELD node AS c, score` formatı ZORUNLU
4. query_text = KAVRAM (isim, tarih, kod DEĞİL!)

**HIZLI MOD (Filter yok):**
```python
execute_cypher_query_with_embedding(
    query_text="aranan kavram",
    cypher=\"\"\"
    CALL db.index.vector.queryNodes('vector', 50, $embedding_vector)
    YIELD node AS c, score
    WHERE score > 0.75
    RETURN c.text AS text, score, c.page_link, c.fileName
    ORDER BY score DESC
    \"\"\",
    step_name="semantic_search"
)
```

**FİLTRELİ MOD (Entity ile - ŞEMAYA GÖRE UYARLA):**
```python
execute_cypher_query_with_embedding(
    query_text="aranan kavram",
    cypher=\"\"\"
    CALL db.index.vector.queryNodes('vector', 100, $embedding_vector)
    YIELD node AS c, score
    WHERE score > 0.70
    MATCH (c)-[:CHUNK_REL]->(d:Document)<-[:DOC_REL]-(e:EntityNode)
    WHERE e.name CONTAINS 'değer'
    RETURN c.text AS text, score, d.fileName, e.name
    ORDER BY score DESC
    \"\"\",
    step_name="entity_search"
)
```

⚠️ İlişki ve node adlarını ŞEMADAN al! (CHUNK_REL, DOC_REL, EntityNode örnektir)

---

### expand_chunk_context(document_name, positions, window_size) - CONTEXT GENİŞLETME
**NE ZAMAN:** 
- Arama sonucunda tablo/liste kesilmiş görünüyorsa
- "devamı var", "..." gibi ifadeler varsa
- Chunk'ın etrafındaki context'i görmek istiyorsan

**PARAMETRELER:**
- `document_name`: Belge adı (fileName) - Arama sonucundan al
- `positions`: Chunk pozisyonları, virgülle ayrılmış (örn: "5,8,12")
- `window_size`: Her yöne kaç chunk genişlet (varsayılan: 2)

**ÖRNEK KULLANIM:**
```python
# Arama sonucunda pos:45 ve pos:47 bulundu, arası kesilmiş
expand_chunk_context(
    document_name="2024 POLİÇELER_Şirket_Poliçe.pdf",
    positions="45,47",
    window_size=2
)
# Sonuç: pos 43-49 arası tüm chunk'lar birleşik döner
```

**NE ZAMAN KULLANMA:**
- ❌ Arama sonucu zaten yeterli bilgi içeriyorsa
- ❌ Sadece merak ettiğin için (gereksiz token harcaması)
- ❌ Her arama sonucunda otomatik olarak

**NE ZAMAN KULLAN:**
- ✅ Tablo ortasından kesilmiş görünüyorsa
- ✅ Cümle yarım kalmışsa
- ✅ "devamı sonraki sayfada" gibi ifadeler varsa
- ✅ Kullanıcı "daha fazla detay" istiyorsa

</tool_usage>
"""
