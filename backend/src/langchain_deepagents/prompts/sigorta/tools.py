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
- Fuzzy arama: `'kelime~'` (yazım hatalarını bulur)
- OR operatörü: `'kelime1 OR kelime2'`
- Phrase arama: `'"tam eşleşme ifade"'`

**HIZLI MOD (Genel arama - dikkatli kullan!):**
```cypher
CALL db.index.fulltext.queryNodes('chunk_text_fulltext', 'fiyat OR tutar OR bedel~')
YIELD node AS c, score
WHERE score > 1.0
RETURN c.text, score, c.fileName
ORDER BY score DESC LIMIT 10
```

**FİLTRELİ MOD (Entity ile - ÖNERİLEN, ilişki adlarını ŞEMADAN al!):**
```cypher
CALL db.index.fulltext.queryNodes('chunk_text_fulltext', 'ödeme~ OR "ödeme planı"')
YIELD node AS c, score
WHERE score > 0.5
MATCH (c)-[:ILIŞKI_ADI]->(d:Document)<-[:ILIŞKI_ADI]-(e:EntityNode)
WHERE e.name CONTAINS 'değer'
RETURN c.text, score, d.fileName
ORDER BY score DESC LIMIT 10
```

⚠️ İlişki adlarını ŞEMADAN al!

---

### 💡 ARAMA STRATEJİSİ HATIRLATMASI
Hangi arama yöntemini kullanacağına SEN karar ver:
- **Metadata/sayısal veri** → Graph sorgusu (execute_cypher_query)
- **Spesifik kelime araması** → Fulltext (execute_cypher_query + fulltext)
- **Anlam/kavram araması** → Semantic (execute_cypher_query_with_embedding)

⚠️ Genel bir soru sorulmuşsa (örn: "X hakkında ne bilgi var?") sonuçları değerlendirip EN UYGUN yöntemi seç.

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

</tool_usage>
"""
