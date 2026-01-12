"""
Sigorta domain'i - DSL Mode Thinking Guide.

DSL-specific düşünme rehberi. Sadece DSL mode'da kullanılır.
"""

# =============================================================================
# DSL MODE - Thinking Guide (DSL-specific content)
# =============================================================================
DSL_THINKING_GUIDE = """
<thinking_guide>
📚 **DÜŞÜNME REHBERİ:** 
   - Hangi intent seçmeli, nereye bakmalı, bulamazsan ne yapmalı, hata alırsan nasıl çözmeli

# 🧠 ONTOLOGY-DRIVEN DSL DÜŞÜNME REHBERİ

## 🎯 SORU GELDİĞİNDE HANGİ INTENT?

```
SORU ANALİZİ → INTENT SEÇİMİ
├── "X'in Y'leri neler?" → FIND_BY_RELATIONSHIP
├── "X'i bul" → FIND_BY_PROPERTY
├── "Kaç tane X var?" → COUNT_NODES veya AGGREGATE_VALUES
├── "En yüksek/düşük X" → AGGREGATE_VALUES (MAX/MIN)
├── "X ile ilgili detay/içerik" → SEARCH_CONTENT (semantic)
├── "Ne yazıyor/tablo/liste" → SEARCH_CONTENT (semantic)
├── "X kelimesi geçen yerler" → SEARCH_TEXT (keyword)
├── Semantic boş gelirse → SEARCH_TEXT (fallback)
└── "X örnekleri/yapısı" → EXPLORE_NODE
```

---

## 🔍 TERİMİ NEREDE ARAMALIYIM?

### ADIM 1: Schema'da Property Var mı?

```
SORU: "X özelliği en yüksek Y hangisi?"

DÜŞÜN: "X" schema'da hangi node'da? (ŞEMAYA BAK!)
├── EntityA property'leri: id, name, type → VAR MI?
├── EntityB property'leri: value, amount → VAR MI?
├── AttributeNode property'leri: name, description → VAR MI?
└── SONUÇ: Şemadaki property'leri kontrol et!
```

**Varsa → FIND_BY_PROPERTY veya AGGREGATE_VALUES:**
```json
{
  "intent": "aggregate_values",
  "traversal": [{"from_node": "EntityA", "relation": "HAS_ATTRIBUTE", "to_node": "AttributeNode"}],
  "aggregate": {"function": "max", "node": "AttributeNode", "property": "value"},
  "order_by": {"node": "AttributeNode", "property": "value", "direction": "DESC"}
}
```
⚠️ Node ve ilişki adlarını ŞEMADAN al!

### ADIM 2: İlişkili Node'da Var mı?

```
DÜŞÜN: AttributeNode.name içinde "aranan_terim" olabilir mi?

DSL (node/ilişki adlarını ŞEMADAN al!):
{
  "intent": "find_by_relationship",
  "start_node": "EntityA",
  "traversal": [
    {"from_node": "EntityA", "relation": "HAS_ATTRIBUTE", "to_node": "AttributeNode"}
  ],
  "filters": [
    {"node": "AttributeNode", "property": "name", "operator": "contains", "value": "aranan_terim"}
  ],
  "return_spec": {"nodes": ["EntityA", "AttributeNode"], "properties": {...}}
}
```

### ADIM 3: Chunk İçeriğinde Ara (Semantic Search)

```
Property'de ve ilişkili node'da yoksa → SEARCH_CONTENT

DSL:
{
  "intent": "search_content",
  "semantic_search": {
    "query_text": "aranan kavram alternatif terimler",
    "target_node": "Chunk",
    "similarity_threshold": 0.7,
    "limit": 10
  },
  "include_source_info": true
}
```

---

## 🔄 ARAMA HİYERARŞİSİ (Fallback Zinciri)

```
┌─────────────────────────────────────────────────────────────────┐
│  ADIM 1: FIND_BY_PROPERTY                                       │
│  └── Terim direkt bir node property'si mi?                      │
│      ✓ Bulundu → Sonuç döndür                                   │
│      ✗ Bulunamadı → ADIM 2'ye geç                               │
├─────────────────────────────────────────────────────────────────┤
│  ADIM 2: FIND_BY_RELATIONSHIP                                   │
│  └── İlişkili node'ların property'lerinde var mı?               │
│      EntityA → RelatedNode.property CONTAINS terim?             │
│      ✓ Bulundu → Sonuç döndür                                   │
│      ✗ Bulunamadı → ADIM 3'e geç                                │
├─────────────────────────────────────────────────────────────────┤
│  ADIM 3: SEARCH_CONTENT (Semantic)                              │
│  └── Chunk.text içinde semantic arama                           │
│      query_text: "terim + ilgili kavramlar"                     │
│      ✓ Bulundu → Chunk + kaynak bilgisi döndür                  │
│      ✗ Bulunamadı → ADIM 4'e geç                                │
├─────────────────────────────────────────────────────────────────┤
│  ADIM 4: SEARCH_TEXT (Keyword/Exact Match)                      │
│  └── Chunk.text içinde direkt keyword araması                   │
│      Chunk.text CONTAINS "terim" (case-insensitive)             │
│      ✓ Bulundu → Chunk + kaynak bilgisi döndür                  │
│      ✗ Bulunamadı → ADIM 5'e geç                                │
├─────────────────────────────────────────────────────────────────┤
│  ADIM 5: EXPLORE_NODE                                           │
│  └── Schema keşfi yap, alternatif property'leri bul             │
│      ✓ Yeni property bulundu → ADIM 1'e dön                     │
│      ✗ Alternatif yok → Kullanıcıya bildir                      │
└─────────────────────────────────────────────────────────────────┘
```

---

## ⚠️ BOŞ SONUÇ ALDIĞINDA

### Durum 1: Property Bulunamadı
```
DSL verdin, sonuç boş geldi.

DÜŞÜN:
├── Filtre çok dar mı? → operator: "contains" kullan, "equals" değil
├── Türkçe karakter sorunu mu? → Alternatif yazımları dene
├── 🌐 DİL FARKI MI? → İngilizce karşılığını dene!
│   └── Örn: Türkçe terim → 0 sonuç? → İngilizce karşılığını dene!
│   └── Teknik terimler genelde İngilizce (specification, attribute, status, type)
├── Yanlış node mu? → Traversal'ı gözden geçir
└── Property yok mu? → SEARCH_CONTENT'e geç
```

### Durum 2: Semantic Search Boş
```
SEARCH_CONTENT verdin, sonuç boş.

DÜŞÜN:
├── query_text çok spesifik mi? → Daha genel terimler ekle
├── threshold çok yüksek mi? → 0.7 → 0.5'e düşür
├── Yanlış terimler mi? → İngilizce/Türkçe alternatifleri ekle
└── Hala boş mu? → SEARCH_TEXT ile keyword aramasına geç!
```

### Durum 3: Semantic → Text Fallback
```
Semantic arama boş geldi, keyword aramasına geç:

DSL:
{
  "intent": "search_text",
  "start_node": "Chunk",
  "filters": [
    {"node": "Chunk", "property": "text", "operator": "contains", "value": "orijinal_terim"}
  ],
  "return_spec": {...},
  "limit": 15
}

NEDEN: Semantic search bazen exact match'leri kaçırabilir.
       Aranan kelime belgede geçiyor ama farklı anlamda yorumlanmış olabilir.
```

### Durum 4: Traversal Hatası
```
"Relationship not found" veya boş path.

DÜŞÜN:
├── Relationship yönü doğru mu? → "outgoing" vs "incoming"
├── Relationship adı doğru mu? → Schema'yı kontrol et
├── Ara node gerekli mi? → EntityA → X → Y şeklinde mi?
└── OPTIONAL MATCH gerekli mi? → "optional": true ekle
```

---

## 📋 INTENT → DSL ŞABLONLARI (Node/İlişki adlarını ŞEMADAN al!)

### Entity İlişkisi Bul
```json
{
  "intent": "find_by_relationship",
  "traversal": [
    {"from_node": "EntityA", "relation": "HAS_RELATION", "to_node": "EntityB"}
  ],
  "filters": [
    {"node": "EntityA", "property": "name", "operator": "contains", "value": "..."}
  ],
  "return_spec": {
    "nodes": ["EntityA", "EntityB"],
    "properties": {"EntityA": ["name"], "EntityB": ["id", "value"]}
  }
}
```

### En Yüksek/Düşük Değer (Aggregate)
```json
{
  "intent": "aggregate_values",
  "traversal": [
    {"from_node": "EntityA", "relation": "HAS_ATTRIBUTE", "to_node": "AttributeNode"}
  ],
  "aggregate": {
    "function": "max",
    "node": "AttributeNode",
    "property": "value",
    "alias": "max_value"
  },
  "order_by": {"node": "AttributeNode", "property": "value", "direction": "DESC"},
  "limit": 1
}
```

### İçerik/Detay Araması
```json
{
  "intent": "search_content",
  "semantic_search": {
    "query_text": "aranan kavram alternatif terimler",
    "similarity_threshold": 0.7,
    "limit": 10
  },
  "include_source_info": true
}
```

### Belirli Entity'nin İçeriğinde Ara (Hibrit)
```json
{
  "intent": "search_content",
  "traversal": [
    {"from_node": "EntityA", "relation": "HAS_DOCUMENT", "to_node": "Document"},
    {"from_node": "Document", "relation": "FIRST_CHUNK", "to_node": "Chunk"}
  ],
  "filters": [
    {"node": "EntityA", "property": "id", "operator": "equals", "value": "123"}
  ],
  "semantic_search": {
    "query_text": "aranan kavram detay",
    "similarity_threshold": 0.6
  }
}
```

### Keyword/Text Araması (Semantic Başarısızsa Fallback)
```json
{
  "intent": "search_text",
  "start_node": "Chunk",
  "filters": [
    {"node": "Chunk", "property": "text", "operator": "contains", "value": "aranan_kelime"}
  ],
  "return_spec": {
    "nodes": ["Chunk"],
    "properties": {"Chunk": ["text", "page_link", "fileName"]}
  },
  "include_source_info": true,
  "limit": 10
}
```

### Keyword Araması + Document Filtresi
```json
{
  "intent": "search_text",
  "traversal": [
    {"from_node": "Chunk", "relation": "PART_OF", "to_node": "Document"}
  ],
  "filters": [
    {"node": "Chunk", "property": "text", "operator": "contains", "value": "aranan_kelime"},
    {"node": "Document", "property": "fileName", "operator": "contains", "value": "dosya_adi"}
  ],
  "return_spec": {
    "nodes": ["Chunk", "Document"],
    "properties": {"Chunk": ["text", "page_link"], "Document": ["fileName"]}
  },
  "limit": 10
}
```

---

## 🎯 HIZLI KARAR TABLOSU (Node adlarını ŞEMADAN al!)

| Soru İçeriği | Intent | Traversal Örneği |
|-------------|--------|---------------------|
| "X'in Y'leri neler" | find_by_relationship | EntityA → EntityB |
| "Y'nin Z'si" | find_by_relationship | EntityB → AttributeNode |
| "Kaç tane X var" | count_nodes | EntityA |
| "En yüksek/düşük Z" | aggregate_values | EntityA → AttributeNode |
| "Detayları neler" | search_content | Chunk (semantic) |
| "Ne yazıyor" | search_content | Chunk (semantic) |
| "Tarih filtreli" | find_by_relationship | EntityA → Date |
| "Lokasyon filtreli" | find_by_relationship | EntityA → Location |
| Semantic boş geldi | search_text | Chunk.text CONTAINS |
| "X kelimesi geçen" | search_text | Chunk.text CONTAINS |

---

## 💡 query_text SEÇİMİ (Semantic Search)

```
✅ DOĞRU: Sadece kavramsal terimler
   "ödeme planı vade detayları"
   "teknik özellikler spesifikasyon"
   "şartlar koşullar kapsam"

❌ YANLIŞ: Metadata karıştırma
   "Ahmet Yılmaz 2024 kayıt detay"
   "123456 numara özellik"
   "Şirket X rapor"
   
⚠️ Metadata filtrelemesi → DSL filters[] içinde yap, query_text'e koyma!
```
</thinking_guide>

<graph_dsl_mode>
`execute_graph_dsl` tool'unu kullan ve JSON DSL gönder:

```json
{
    "intent": "find_by_property",
    "description": "Entity ara",
    "start_node": "NodeLabel",
    "filters": [
        {"node": "NodeLabel", "property": "name", "operator": "contains", "value": "aranan_deger"}
    ],
    "return_spec": {
        "nodes": ["NodeLabel"],
        "properties": {"NodeLabel": ["name", "prop1", "prop2"]},
        "distinct": true
    },
    "limit": 10
}
```

## 🎯 INTENT TİPLERİ

| Intent | Açıklama | Örnek |
|--------|----------|-------|
| `explore_node` | Node örneklerini gör | start_node + return_spec |
| `find_by_property` | Property ile ara | filters ile |
| `find_by_relationship` | İlişki ile ara | traversal ile |
| `count_nodes` | Sayma | aggregate: count |
| `aggregate_values` | SUM/AVG/MIN/MAX | aggregate ile |
| `search_content` | Chunk semantic araması | semantic_search + traversal + filters |

## 🔎 SEMANTIC SEARCH (Chunk İçerik Araması)

⚠️ KRİTİK: Önceki sorgularda bulunan entity filtrelerini MUTLAKA kullan!

```json
{
    "intent": "search_content",
    "description": "Entity X için konu Y detayları",
    "step_name": "embed_search_topic",
    "traversal": [
        {"from_node": "EntityA", "relation": "REL_TO_B", "to_node": "EntityB", "direction": "outgoing"},
        {"from_node": "EntityB", "relation": "HAS_DOCUMENT", "to_node": "Document", "direction": "outgoing"},
        {"from_node": "Document", "relation": "HAS_CHUNK", "to_node": "Chunk", "direction": "outgoing"}
    ],
    "filters": [
        {"node": "EntityA", "property": "name", "operator": "in", "value": ["Önceki sorguda bulunan TÜM varyasyonlar..."]},
        {"node": "EntityC", "property": "name", "operator": "contains", "value": "aranan_konu"}
    ],
    "semantic_search": {
        "query_text": "aranan kavram veya konu",
        "similarity_threshold": 0.75,
        "limit": 10
    },
    "include_source_info": true
}
```

❌ YANLIŞ: Sadece semantic_search, filter olmadan → tüm veritabanını tarar!
✅ DOĞRU: traversal + filters + semantic_search → önceki bulgularla filtrelenmiş arama

## 🔗 TRAVERSAL (İlişki Takibi)

```json
{
    "intent": "find_by_relationship",
    "traversal": [
        {"from_node": "NodeA", "relation": "RELATES_TO", "to_node": "NodeB", "direction": "outgoing"},
        {"from_node": "NodeB", "relation": "HAS_CHILD", "to_node": "NodeC", "direction": "outgoing"}
    ],
    "filters": [
        {"node": "NodeA", "property": "name", "operator": "contains", "value": "aranan_deger"}
    ],
    "return_spec": {
        "nodes": ["NodeB", "NodeC"],
        "properties": {"NodeB": ["name", "prop1"], "NodeC": ["name", "prop2"]}
    }
}
```

⚠️ Star Pattern: Tüm traversal'ların from_node'u aynı ise (örn: NodeB), compiler otomatik olarak virgülle ayırır.

## 📊 AGGREGATION (Toplama/Sayma)

```json
{
    "intent": "count_nodes",
    "start_node": "NodeLabel",
    "filters": [
        {"node": "NodeLabel", "property": "status", "operator": "equals", "value": "active"}
    ],
    "aggregate": {
        "function": "count",
        "node": "NodeLabel",
        "alias": "toplam_sayisi"
    }
}
```

## 🔍 FILTER OPERATÖRLERİ

| Operatör | Açıklama | Örnek Değer |
|----------|----------|-------------|
| `equals` | Tam eşleşme | "active" |
| `contains` | İçerir (case-insensitive) | "arama_terimi" |
| `starts_with` | İle başlar | "POL-" |
| `gt`, `lt`, `gte`, `lte` | Sayısal karşılaştırma | 1000 |
| `in` | Liste içinde | ["active", "pending"] |
| `is_null`, `is_not_null` | Null kontrolü | - |

## ⚠️ NE ZAMAN CYPHER KULLAN?

DSL desteklemeyen durumlar için `execute_cypher_query` kullan:
- Çok karmaşık JOIN'ler
- UNION sorguları
- Özel fonksiyonlar (apoc.*)

Ama önce DSL dene! Çoğu sorgu DSL ile yapılabilir.

</graph_dsl_mode>
"""
