"""
Sigorta domain'i - Tool kullanım rehberleri.
"""

# =============================================================================
# DSL MODE - Tool Usage Guide
# =============================================================================
DSL_TOOL_USAGE = """
<tool_usage>
## 🔧 TOOL KULLANIM REHBERİ (DSL MODE)

### execute_graph_dsl(dsl_json, step_name, compiler_mode) - ANA ARAÇ
DSL ile sorgu - hata yapmaya daha az müsait, otomatik validation ve Cypher derleme.

**NE ZAMAN:** Graph sorgularının %90'ı bu tool ile yapılabilir
- Entity keşfi, property araması
- İlişki takibi (traversal)
- Aggregation (COUNT, SUM, AVG, MAX, MIN)
- Chunk semantic araması (search_content)

**DSL FORMATI:**
```json
{
    "intent": "find_by_property | find_by_relationship | search_content | count_nodes | aggregate_values",
    "start_node": "NodeLabel",
    "traversal": [{"from_node": "A", "relation": "REL", "to_node": "B", "direction": "outgoing"}],
    "filters": [{"node": "A", "property": "name", "operator": "contains", "value": "aranan"}],
    "return_spec": {"nodes": ["A", "B"], "properties": {"A": ["name"]}, "distinct": true},
    "aggregate": {"function": "count", "node": "B", "alias": "toplam"},
    "semantic_search": {"query_text": "kavram", "similarity_threshold": 0.7, "limit": 10},
    "limit": 10
}
```

**INTENT TİPLERİ:**
| Intent | Kullanım | Örnek |
|--------|----------|-------|
| explore_node | Node örneklerini gör | Şemayı keşfet |
| find_by_property | Property ile ara | name CONTAINS "X" |
| find_by_relationship | İlişki takibi | A → B → C |
| search_content | Semantic arama | Chunk içerik araması |
| count_nodes | Sayma | Kaç tane X var? |
| aggregate_values | SUM/AVG/MAX/MIN | En yüksek değer |

**FILTER OPERATÖRLERİ:** equals, contains, starts_with, gt, lt, gte, lte, in, is_null

---

### execute_cypher_query(cypher, step_name) - FALLBACK
DSL desteklemeyen özel durumlar için doğrudan Cypher.

**NE ZAMAN:** 
- DSL ile ifade edilemeyen karmaşık sorgular
- UNION, CASE/WHEN gerektiren durumlar

⚠️ **TEXT ARAMASI:** `apoc.text.clean()` kullan! (Türkçe karakter sorunu önler)

</tool_usage>
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
