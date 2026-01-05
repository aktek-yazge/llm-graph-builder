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
SORU: "Reasürans oranı en yüksek poliçe hangisi?"

DÜŞÜN: "reasürans" schema'da hangi node'da?
├── Policy property'leri: policyNumber, currency, source_file → YOK
├── Premium property'leri: amount, currency, commissionRate → YOK  
├── Coverage property'leri: name → Belki "reasürans" içerir?
├── CoverageLimit property'leri: limit_value, limit_unit → YOK
└── SONUÇ: Structured data'da direkt property yok!
```

**Varsa → FIND_BY_PROPERTY veya AGGREGATE_VALUES:**
```json
{
  "intent": "aggregate_values",
  "traversal": [{"from_node": "Policy", "relation": "HAS_X", "to_node": "X"}],
  "aggregate": {"function": "max", "node": "X", "property": "oran"},
  "order_by": {"node": "X", "property": "oran", "direction": "DESC"}
}
```

### ADIM 2: İlişkili Node'da Var mı?

```
DÜŞÜN: Coverage.name içinde "reasürans" olabilir mi?

DSL:
{
  "intent": "find_by_relationship",
  "start_node": "Policy",
  "traversal": [
    {"from_node": "Policy", "relation": "HAS_COVERAGE", "to_node": "Coverage"}
  ],
  "filters": [
    {"node": "Coverage", "property": "name", "operator": "contains", "value": "reasürans"}
  ],
  "return_spec": {"nodes": ["Policy", "Coverage"], "properties": {...}}
}
```

### ADIM 3: Chunk İçeriğinde Ara (Semantic Search)

```
Property'de ve ilişkili node'da yoksa → SEARCH_CONTENT

DSL:
{
  "intent": "search_content",
  "semantic_search": {
    "query_text": "reasürans oranı reinsurance rate",
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
│      Policy → Coverage/Clause/Guarantee.name CONTAINS terim?    │
│      ✓ Bulundu → Sonuç döndür                                   │
│      ✗ Bulunamadı → ADIM 3'e geç                                │
├─────────────────────────────────────────────────────────────────┤
│  ADIM 3: SEARCH_CONTENT (Semantic)                              │
│  └── Chunk.text içinde embedding similarity arama               │
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

NEDEN: Embedding bazen exact match'leri kaçırabilir.
       "reasürans" kelimesi belgede geçiyor ama embedding farklı anlamda yorumlamış olabilir.
```

### Durum 4: Traversal Hatası
```
"Relationship not found" veya boş path.

DÜŞÜN:
├── Relationship yönü doğru mu? → "outgoing" vs "incoming"
├── Relationship adı doğru mu? → Schema'yı kontrol et
├── Ara node gerekli mi? → Policy → X → Y şeklinde mi?
└── OPTIONAL MATCH gerekli mi? → "optional": true ekle
```

---

## 📋 INTENT → DSL ŞABLONLARI

### Müşteri Poliçelerini Bul
```json
{
  "intent": "find_by_relationship",
  "traversal": [
    {"from_node": "Customer", "relation": "HAS_POLICY", "to_node": "Policy"}
  ],
  "filters": [
    {"node": "Customer", "property": "name", "operator": "contains", "value": "..."}
  ],
  "return_spec": {
    "nodes": ["Customer", "Policy"],
    "properties": {"Customer": ["name"], "Policy": ["policyNumber"]}
  }
}
```

### En Yüksek Prim
```json
{
  "intent": "aggregate_values",
  "traversal": [
    {"from_node": "Policy", "relation": "HAS_PREMIUM", "to_node": "Premium"}
  ],
  "aggregate": {
    "function": "max",
    "node": "Premium",
    "property": "amount",
    "alias": "max_prim"
  },
  "order_by": {"node": "Premium", "property": "amount", "direction": "DESC"},
  "limit": 1
}
```

### İçerik/Detay Araması
```json
{
  "intent": "search_content",
  "semantic_search": {
    "query_text": "taksit ödeme planı vade tutarı",
    "similarity_threshold": 0.7,
    "limit": 10
  },
  "include_source_info": true
}
```

### Belirli Poliçenin İçeriğinde Ara (Hibrit)
```json
{
  "intent": "search_content",
  "traversal": [
    {"from_node": "Policy", "relation": "DOCUMENTED_IN", "to_node": "Document"},
    {"from_node": "Document", "relation": "FIRST_CHUNK", "to_node": "Chunk"}
  ],
  "filters": [
    {"node": "Policy", "property": "policyNumber", "operator": "equals", "value": "946006"}
  ],
  "semantic_search": {
    "query_text": "muafiyet istisna",
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
    {"node": "Chunk", "property": "text", "operator": "contains", "value": "reasürans"}
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
    {"node": "Chunk", "property": "text", "operator": "contains", "value": "reasürans"},
    {"node": "Document", "property": "fileName", "operator": "contains", "value": "policy_123"}
  ],
  "return_spec": {
    "nodes": ["Chunk", "Document"],
    "properties": {"Chunk": ["text", "page_link"], "Document": ["fileName"]}
  },
  "limit": 10
}
```

---

## 🎯 HIZLI KARAR TABLOSU

| Soru İçeriği | Intent | Traversal Başlangıcı |
|-------------|--------|---------------------|
| "X'in poliçeleri" | find_by_relationship | Customer → Policy |
| "Poliçenin primi" | find_by_relationship | Policy → Premium |
| "Kaç poliçe var" | count_nodes | Policy |
| "En yüksek prim" | aggregate_values | Policy → Premium |
| "Taksit detayları" | search_content | Chunk (semantic) |
| "Ne yazıyor" | search_content | Chunk (semantic) |
| "2024 poliçeleri" | find_by_relationship | Policy → Date |
| "İstanbul'daki" | find_by_relationship | Policy → RiskAddress |
| Semantic boş geldi | search_text | Chunk.text CONTAINS |
| "X kelimesi geçen" | search_text | Chunk.text CONTAINS |

---

## 💡 query_text SEÇİMİ (Semantic Search)

```
✅ DOĞRU: Sadece kavramsal terimler
   "taksit ödeme planı vade"
   "reasürans oranı reinsurance"
   "muafiyet istisna kapsam dışı"

❌ YANLIŞ: Metadata karıştırma
   "Ayşe Yılmaz 2024 kasko taksit"
   "946006 poliçe primi"
   "Zurich yangın"
   
⚠️ Metadata filtrelemesi → DSL filters[] içinde yap, query_text'e koyma!
```
