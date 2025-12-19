# 📄 İÇERİK REHBERİ

Chunk'larda semantic (embedding) arama stratejileri.

## 🎯 AMAÇ
Belge içeriklerinde (Chunk node'larında) semantic arama yapmak.

## 🚫 İÇERİK GÖREVİ KULLANMA EĞER:
Şemada yapılandırılmış node varsa (Amount, Price, Date vb.) → Direkt METADATA ile node traversal yap!

## ⚠️ İÇERİK GÖREVİ VERMEDEN ÖNCE:
1. `read_blackboard_dynamic()` çağır
2. KEŞİF'te bulunan TÜM varyasyonları blackboard'dan al
3. Bu varyasyonları İÇERİK görevine AYNEN kopyala!

**ÖNEMLİ:** KEŞİF'ten gelen varyasyonları değerlendir!
- Varyasyonlar aranan entity ile eşleşiyor mu? → EVET ise İÇERİK'e geç
- Eşleşmiyor mu? → Yeni KEŞİF görevi ver

## 🌍 ÇOK DİLLİ ARAMA - KRİTİK!
Belgeler farklı dillerde olabilir! EMBEDDING QUERY'de HER İKİ DİLİ de ver:
- Türkçe terim + İngilizce karşılık (veya tersi)
- Örnek format: "türkçe_terim", "english_equivalent"
Worker önce embedding dener, 0 sonuç gelirse text CONTAINS ile arar.

## 📌 DARALTMA TİPLERİ

| Tip | Açıklama |
|-----|----------|
| ENTITY | KEŞİF'te bulunan varyasyonlar + node tipi + ilişki yolu |
| FİLTRE | Şemadan çıkardığın property/pattern (tarih, tip, vb.) |
| TÜM VERİ | Daraltma yok, tüm Chunk'lar taranacak (yavaş - bilinçli seç!) |

## 📋 GÖREV FORMATI - ENTITY DARALTMA

```
spawn_worker(queries="""
## 🏷️ GÖREV TİPİ: İÇERİK
## 🎯 GÖREV: [Aranan konu] hakkında içerik ara

## 📌 DARALTMA: ENTITY
### Bulunan Varyasyonlar (KEŞİF'ten - RAW AYNEN KOPYALA!):
| n.name (Veritabanındaki EXACT değer) | Node Tipi |
|--------------------------------------|-----------|
| [raw_value_1 - yazım hataları dahil] | [Label] |
| [raw_value_2 - yazım hataları dahil] | [Label] |

⚠️ Varyasyonları KEŞİF sonucundan AYNEN al, düzeltme yapma!

### İlişki Yolu (şemadan - OK YÖNÜNE DİKKAT!):
[Label]<-[:REL]-(Node) veya [Label]-[:REL]->(Node) - şemadaki gibi

## 📊 NODE PROPERTY'LERİ (şemadan):
- Chunk içeriği: `text` property'sinde (c.text CONTAINS ...)
- [Diğer ilgili property'ler şemadan]

## 📝 EMBEDDING QUERY (sadece konu):
- "[aranan konu - Türkçe]"
- "[aranan konu - İngilizce karşılık]"  ← ÖNEMLİ: Belgeler İngilizce olabilir!

## 🔧 TOOL HATIRLATMA:
1. ÖNCELİK: `execute_embedding_query` ile semantic arama yap
   - query_text: sadece konu (yukarıdaki terimler)
   - cypher_query: $embedding_vector + gds.similarity.cosine > 0.85 içermeli!
   - ⚠️ Eşik değeri EN AZ 0.85 olmalı (düşük değerler false positive verir)

2. **Hata yoksa ve 0 SONUÇ GELİRSE → TEXT FALLBACK ile yeni görev ver!**
   ```
   execute_cypher_query ile TEXT CONTAINS ara:
   WHERE toLower(c.text) CONTAINS 'terim1' OR toLower(c.text) CONTAINS 'terim2' ...
   ```
   - Türkçe ve İngilizce terimlerin HEPSİNİ ekle
   - İlişki yolunu PART_OF ile dene (FIRST_CHUNK sadece başlık getirir!)

3. **N SONUÇ GELİRSE → DOĞRULAMA ZORUNLU!**
   - chunk.text'te aranan terim geçiyor mu? (FALSE_POSITIVE rehberine bak!)

## 📁 KAYIT:
- step_name: "[step_adı]"
""")
```

## 📋 GÖREV FORMATI - FİLTRE DARALTMA

```
## 📌 DARALTMA: FİLTRE
### Filtre Koşulu (şemadan):
- [property] [operator] [value]
- Örnek: d.fileName STARTS WITH '2024'

### İlişki Yolu:
[Node]-[:REL]->...-[:PART_OF]->(Chunk)

## 📝 EMBEDDING QUERY (sadece konu):
- "[aranan konu]"
```

## 🔄 EMBEDDING FALLBACK AKIŞI

```
┌─────────────────────────────────────────────────────────────┐
│ 1. execute_embedding_query → score > 0.85                   │
│    ↓                                                         │
│ ┌─────────────────┐     ┌─────────────────────────────────┐ │
│ │ Sonuç 0 ise     │ →   │ TEXT CONTAINS fallback          │ │
│ │                 │     │ (execute_cypher_query ile)      │ │
│ └─────────────────┘     └─────────────────────────────────┘ │
│    ↓                                                         │
│ ┌─────────────────┐     ┌─────────────────────────────────┐ │
│ │ Sonuç N ise     │ →   │ DOĞRULAMA: Aranan terim         │ │
│ │                 │     │ chunk.text'te VAR mı?           │ │
│ └─────────────────┘     └─────────────────────────────────┘ │
│                              ↓                               │
│                    ┌─────────┴─────────┐                    │
│                    │                   │                    │
│               VAR → Devam        YOK → FALSE POSITIVE       │
│                                        Text fallback dene    │
└─────────────────────────────────────────────────────────────┘
```

**Text Fallback Örneği:**
```cypher
-- Embedding 0 sonuç döndü, text araması:
MATCH (n:Label)<-[:REL]-(other)-[:REL2]->(d)-[:PART_OF]->(c:Chunk)
WHERE n.name IN ['varyasyon1', 'varyasyon2']
AND (toLower(c.text) CONTAINS 'türkçe_terim' 
     OR toLower(c.text) CONTAINS 'english_term')
RETURN c.text, n.name AS source
LIMIT 10
```

