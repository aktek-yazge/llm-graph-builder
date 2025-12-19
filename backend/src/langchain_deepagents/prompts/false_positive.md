# 🚨 FALSE POSITIVE REHBERİ

Embedding sonuçlarını doğrulama ve false positive tespiti.

## ⚠️ KRİTİK UYARI

**Yüksek embedding skoru (>0.85) ≠ Doğru sonuç!**

Embedding alan benzerliği yakalar (genel terminoloji), ama kavramsal farklılığı yakalayamaz (aranan terim ≠ alakasız içerik)

## 🔍 İÇERİK SONUÇ DOĞRULAMA

Embedding sonuç döndürse bile MUTLAKA DOĞRULA:

### 1. read_finding_dynamic ile chunk.text'leri oku
```python
read_finding_dynamic(step_name, result_type="success", include_query=False)
→ Dönen içerikleri incele
```

### 2. Aranan terim metinde GEÇİYOR MU?
Aranan kelime text'te var mı kontrol et.

### 3. Karar Tablosu

| Durum | Aksiyon |
|-------|---------|
| Text'te aranan terim VAR | ✅ Doğru sonuç, devam et |
| Text'te aranan terim YOK | ❌ FALSE POSITIVE! |

## 🛠️ FALSE POSITIVE TESPİT EDİLDİĞİNDE

### Adım 1: think_tool ile analiz et
```python
think_tool(reflection="Embedding sonuçları aranan terimi içermiyor. 
Score yüksek (0.82) ama bu domain benzerliğinden kaynaklanıyor.
FALSE POSITIVE - Text-based arama gerekli.")
```

### Adım 2: Text-based fallback dene
`execute_cypher_query` ile explicit text CONTAINS kullan

### Adım 3: Chunk ilişki yolunu değiştir
⚠️ **Chunk ilişki yolunu değiştir:** 
- `FIRST_CHUNK` sadece başlık chunk'ını getirir!
- `PART_OF` TÜM chunk'ları getirir: `(Chunk)-[:PART_OF]->(Document)`
- Örnek: `(d:Document)<-[:PART_OF]-(c:Chunk)` kullan

## 📊 ÖRNEK FALSE POSITIVE

```
Arama: "kira kaybı"
Embedding sonucu: "deprem hasarı teminatı" (score: 0.82)
Kontrol: "kira kaybı" text'te geçiyor mu? → HAYIR
Karar: ❌ FALSE POSITIVE 
       Her iki metin de sigorta terminolojisi içerdiği için 
       embedding benzer buldu ama kavramsal olarak farklılar.
Aksiyon: Text CONTAINS ile "kira kaybı" explicit ara
```

## 🔄 İÇERİK SONRASI DEĞERLENDİRME AKIŞI

### 1. Sonuç döndü mü?
- **0 sonuç** → Text CONTAINS ile fallback dene
- **N sonuç** → ⚠️ DOĞRULAMA GEREKLİ (aşağıya bak)

### 2. Dönen içerik GERÇEKTEN aranan terimi içeriyor mu?
```python
read_finding_dynamic(step_name, result_type="success", include_query=False)
→ Dönen chunk.text'leri incele
→ Aranan terim text'te geçiyor mu?
```

### 3. FALSE POSITIVE Durumunda Yeni Görev
```
→ Yeni İÇERİK görevi ver:
  - Embedding KULLANMA!
  - Sadece text CONTAINS kullan
  - chunk ilişki yolu (Chunk)-[:PART_OF]->(Document)
```

## 🚫 YAPMA

❌ Embedding sonuçlarını doğrulamadan kabul etme!
❌ Yüksek skor (>0.85) doğru sonuç DEMEK DEĞİL!
❌ chunk.text'te aranan terim geçiyor mu kontrol etmeden devam etme
❌ Geçmiyorsa FALSE POSITIVE - text CONTAINS ile tekrar ara

