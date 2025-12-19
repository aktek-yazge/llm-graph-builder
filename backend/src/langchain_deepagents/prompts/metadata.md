# 📊 METADATA REHBERİ

Basit ilişki takibi, listeleme ve kaynak bilgisi alma stratejileri.

## 🎯 AMAÇ
Entity'lerin ilişkili node'larını listelemek.

## ⚠️ KRİTİK KURAL
İÇERİK görevinden sonra METADATA görevi veriyorsan, **ÖNCEKİ ADIMLARIN FİLTRELERİNİ MUTLAKA MİRAS AL!**

## 📋 GÖREV FORMATI

```
spawn_worker(queries="""
## 🏷️ GÖREV TİPİ: METADATA
## 🎯 GÖREV: [Entity]'nin [ilişkili entity]'lerini listele

## 📌 DARALTMA: ENTITY (ÖNCEKİ ADIMLARDAN MİRAS!)
| n.name (Veritabanındaki EXACT değer) | Node Tipi |
|--------------------------------------|-----------|
| [Önceki adımda bulunan varyasyon 1]  | [Node]    |
| [Önceki adımda bulunan varyasyon 2]  | [Node]    |

⛔ **EXACT DEĞERLERİ KULLAN - TAHMİN ETME!**
- `read_finding_dynamic` ile KEŞİF sonuçlarını OKU
- Veritabanından dönen **HAM** değerleri KOPYALA-YAPIŞTIR
- Kendi yorumunu ekleme, kısaltma!

❌ YANLIŞ: "ABC Ltd" (kısaltma/tahmin)
✅ DOĞRU: "ABC LİMİTED ŞİRKETİ" (veritabanından dönen EXACT değer)

## 🔎 ŞEMA BİLGİSİ:
- Kaynak node: [NodeA], property'ler: [prop1, prop2]
- Hedef node: [NodeB], property'ler: [prop1, prop2]
- Aralarındaki ilişki: REL

⛔ CYPHER SORGUSU YAZMA! Sadece node/ilişki isimlerini belirt.


## 📁 KAYIT:
- step_name: "[step_adı]"
""")
```

## ⛔ CYPHER KODU YAZMA!

Sen (Orchestrator) Cypher kodu yazmayacaksın! Sadece:
- Şema bilgisi (node'lar, ilişkiler)
- Entity varyasyonları
- Görev tanımı

**Worker** Cypher kodunu yazacak. 

## 🔗 ARDIŞIK GÖREVLERDE FİLTRE MİRASI (ÇOK KRİTİK!)

**KEŞİF → İÇERİK → METADATA** zincirinde:
- KEŞİF'te bulunan entity varyasyonları TÜM sonraki adımlarda kullanılmalı!
- İÇERİK'te entity filtresi kullandıysan, METADATA'da da AYNI filtreyi kullan!

**NEDEN?** Aksi halde:
- İÇERİK: "X entity'sinin Y konusu" → 2 chunk bulundu ✅
- METADATA: "Y konusu içeren chunk'ların ilişkili node'ları" → TÜM veritabanı tarandı ❌

**DOĞRU YAKLAŞIM:**
```
METADATA görevinde:
## 📌 ÖNCEKİ ADIMLARDAN MİRAS:
- Entity filtreleri: e.name IN ['KEŞİF varyasyonları...']
- İçerik filtresi: c.text CONTAINS 'aranan_terim'
→ HER İKİSİNİ DE KULLAN!
```
