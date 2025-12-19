# 🔧 CYPHER KURALLARI REHBERİ

Cypher sorgusu yazarken uyulması gereken kritik kurallar.

## 🔧 WORKER TOOL SEÇİM TABLOSU

| Görev Tipi | Tool | Zorunlu Parametre |
|------------|------|-------------------|
| **KEŞİF** | `execute_cypher_query` | cypher, step_name |
| **METADATA** | `execute_cypher_query` | cypher, step_name |
| **İÇERİK (embedding)** | `execute_embedding_query` | query_text, cypher_query, step_name |
| **İÇERİK (text fallback)** | `execute_cypher_query` | cypher, step_name |

### ⛔ TOOL KARIŞTIRMA YASAK!
```
❌ execute_embedding_query + CONTAINS sorgusu → MCP Server hata verir!
❌ execute_cypher_query + $embedding_vector → Parametre bulunamaz!

✅ Embedding → execute_embedding_query + $embedding_vector + gds.similarity.cosine
✅ CONTAINS → execute_cypher_query + toLower(...) CONTAINS
```

## 🚨 İLİŞKİ YÖNÜ - EN KRİTİK KURAL!

```cypher
-- Şema: (A)-[:REL]->(B) ise
✅ MATCH (a:A)-[:REL]->(b:B)
❌ MATCH (b:B)-[:REL]->(a:A)  -- Ters yön çalışmaz!
```

**KURAL:** İlişki yönünü ŞEMADAN AYNEN KOPYALA!

## 🚨 İLİŞKİ ADLARI - ÇOK KRİTİK!

Şemada benzer isimli ama TAMAMEN FARKLI ilişkiler olabilir!

### KURALLAR:
1. İlişki adını şemadan **BİREBİR KOPYALA** - asla "benzer" olanı yazma
2. `HAS_X` ve `HAS_X_SOMETHING` FARKLI ilişkilerdir - dikkat!
3. Hedef node'un şemadaki pattern ile eşleştiğini kontrol et

### TEKNİK:
Şemada görmediğin bir ilişki adı YAZMA!
```cypher
-- Şemada: (A)-[:SOME_REL]->(B) → Aynen yaz: (A)-[:SOME_REL]->(B)
-- Şemada yoksa: (A)-[:SOME_OTHER_REL]->(B) → YAZMA!
```

### KONTROL:
İlişki yolu yazarken her adımı şemada GÖZÜNLE KONTROL ET:
1. `(NodeA)-[:REL1]->(NodeB)` şemada var mı? ✅
2. `(NodeB)-[:REL2]->(NodeC)` şemada var mı? ✅
3. Yoksa yanlış ilişki adı kullanıyorsun - ŞEMAYA BAK!

## 📝 PROPERTY KURALLARI

### Büyük/Küçük Harf Normalizasyonu
```cypher
-- HER ZAMAN toLower() kullan:
WHERE toLower(n.name) CONTAINS 'abc'
```

### CONTAINS vs STARTS WITH vs EQUALS
```cypher
-- Parçalı eşleşme (önerilen):
WHERE toLower(n.name) CONTAINS 'term'

-- Başlangıç eşleşmesi:
WHERE toLower(n.name) STARTS WITH 'term'

-- Tam eşleşme:
WHERE toLower(n.name) = 'term'
```

### OR ile Çoklu Terim
```cypher
WHERE toLower(n.name) CONTAINS 'term1' 
   OR toLower(n.name) CONTAINS 'term2'
   OR toLower(n.name) CONTAINS 'term3'
```

## 📤 RETURN KURALLARI

### DISTINCT Kullan
```cypher
RETURN DISTINCT n.name, n.fullName
```

### elementId() DÖNME!
```cypher
-- ❌ YANLIŞ:
RETURN elementId(n), n.name

-- ✅ DOĞRU:
RETURN DISTINCT n.name
```

### LIMIT Kullan
```cypher
RETURN DISTINCT n.name LIMIT 50
```

### KEŞİF Görevinde RETURN
```
❌ DÖNME: elementId(n), NULL değerler
✅ DÖNDÜR: RETURN DISTINCT n.name AS name
```

### İÇERİK (Chunk) Görevinde RETURN - KAYNAK BİLGİSİ ZORUNLU!
```cypher
-- fileName ve page_link MUTLAKA dahil et!
RETURN c.text, d.fileName, c.page_link, score LIMIT 10
```
⚠️ `d.fileName` → PDF dosyası linki (/files/{fileName})
⚠️ `c.page_link` → Sayfa görseli linki (/images/{page_link})

## 🔗 CHUNK İLİŞKİLERİ

### PART_OF vs FIRST_CHUNK
```cypher
-- TÜM chunk'lar:
(c:Chunk)-[:PART_OF]->(d:Document)

-- SADECE ilk chunk (başlık):
(c:Chunk)-[:FIRST_CHUNK]->(d:Document)
```

⚠️ İçerik ararken `PART_OF` kullan! `FIRST_CHUNK` sadece başlık getirir.

## 📅 TARİH FİLTRESİ SEÇİMİ

| Kullanıcı İfadesi | Doğru İlişki |
|-------------------|--------------|
| "X yılı için düzenlenen/oluşturulan/başlayan" | `HAS_START_DATE` |
| "X yılında biten/sona eren/kapanan" | `HAS_END_DATE` |

```cypher
-- ⛔ YASAK: "2024 için düzenlenen" sorulunca hem start hem end kullanmak!

-- ✅ "2024 için düzenlenen" = 2024'te BAŞLAYAN
MATCH (n)-[:HAS_START_DATE]->(d:Date) WHERE d.year = 2024

-- ✅ "2024'te biten" = 2024'te SONA EREN
MATCH (n)-[:HAS_END_DATE]->(d:Date) WHERE d.year = 2024
```

## 📊 AGGREGATE KURALLARI

Toplam, ortalama, sayı gibi sorularda Cypher aggregate fonksiyonları kullan:

| Soru Tipi | Cypher Fonksiyonu | Örnek |
|-----------|-------------------|-------|
| Toplam | `SUM(n.field)` | `RETURN SUM(p.amount) AS toplam` |
| Ortalama | `AVG(n.field)` | `RETURN AVG(p.price) AS ortalama` |
| Sayı | `COUNT(n)` | `RETURN COUNT(DISTINCT c) AS adet` |
| Minimum | `MIN(n.field)` | `RETURN MIN(d.date) AS en_eski` |
| Maksimum | `MAX(n.field)` | `RETURN MAX(d.date) AS en_yeni` |

```cypher
-- ✅ DOĞRU: Aggregate fonksiyon kullan
MATCH (p:Policy)-[:HAS_AMOUNT]->(a:Amount)
RETURN SUM(a.value) AS toplam_tutar

-- ❌ YANLIŞ: Her kaydı döndürüp manuel toplama bekleme
MATCH (p:Policy)-[:HAS_AMOUNT]->(a:Amount)
RETURN a.value  -- Orchestrator tek tek toplasın ← YASAK!
```

## 🚫 YAPMA

❌ Şemada olmayan ilişki adı yazma
❌ İlişki yönünü ters yazma
❌ elementId() döndürme
❌ toLower() kullanmadan string karşılaştırma
❌ LIMIT olmadan çok büyük sonuç döndürme
❌ Aynı sorguyu tekrar çalıştırma
❌ Aggregate soru için her kaydı ayrı döndürme

