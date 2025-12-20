# 🔍 KEŞİF REHBERİ

Entity keşfi ve varyasyon bulma stratejileri.

## 🎯 AMAÇ
Veritabanındaki entity'lerin yazım varyasyonlarını bulmak.
⛔ **CHUNK HARİÇ!** (Chunk → İÇERİK görevinde aranır)

## 📊 ŞEMADAN NODE TİPLERİNİ BELİRLE (KRİTİK!)

KEŞİF görevi vermeden ÖNCE yukardaki neo4j şemasını incele:
1. Aranan entity hangi node tiplerinde olabilir?
2. Aynı entity FARKLI node tiplerinde farklı ROLLER ile bulunabilir
3. **TÜM potansiyel node tiplerini Worker'a ver!**

```
❌ YANLIŞ: Sadece 1 node tipinde ara
✅ DOĞRU: yukardaki neo4j şemasını TÜM ilgili node tiplerinde ara
```

Aynı entity farklı rollerde → farklı node tipleri → HEPSİNDE ara!

<search_term_rules>
## 🚨 ARAMA TERİMLERİ OLUŞTURURKEN

- Tam ifadeyi ekle: "XYZ Company"
- Kısaltmalı versiyonları ekle: "XYZ Corp", "XYZ Ltd"
- **MARKA/ŞİRKET ADININ TAM HALİNİ EKLE:** "XYZ" ← lowercase versiyonu

⛔ **KELİMEYİ BÖLME!**
```
❌ YANLIŞ: "Akenerji" → "Aken" (anlamsız yarım kelime!)
✅ DOĞRU: "Akenerji" → "akenerji" (lowercase tam kelime)

❌ YANLIŞ: "Microsoft" → "Micro" 
✅ DOĞRU: "Microsoft" → "microsoft"
```
</search_term_rules>



<task_format>
## 📋 GÖREV FORMATI

```
spawn_worker(queries="""
## 🏷️ GÖREV TİPİ: KEŞİF
## 🎯 GÖREV: [Entity]'nin veritabanındaki yazım varyasyonlarını bul


## 📝 ARAMA TERİMLERİ:
- "[tam_ifade]"
- "[kısaltmalı_versiyon]"
- "[kök_kelime]"  ← MUTLAKA EKLE!

**Örnek:** "ABC Holding A.Ş." araması için:
```
## 📝 ARAMA TERİMLERİ ÖRNEK:
- "ABC Holding A.Ş."
- "ABC Holding"
- "abc"  ← lowercase marka adı

⛔ "AB" veya "A" gibi yarım kelimeler EKLEME!
```

## 🔧 TEKNİK NOT:
Tüm terimleri TEK SORGUDA OR ile birleştir (her node için):
WHERE toLower(n.[property]) CONTAINS 'term1' OR toLower(n.[property]) CONTAINS 'term2' OR ...

## 📤 RETURN KURALI:
- Sadece property değerlerini döndür (name, fullName vb.)
- elementId() DÖNME - sonraki sorgularda kullanılmaz
- DISTINCT kullan
Örnek: RETURN DISTINCT n.name AS name

## ⚠️ ÖNEMLİ - TÜM NODE'LARDA ARA!
Worker, verilen TÜM node'larda paralel arama yapmalı:
- Her node için ayrı step_name kullan (örn: step_1_nodeA, step_1_nodeB)
- Birinde sonuç bulunsa bile DİĞERLERİNİ ATLAMA - farklı varyasyonlar olabilir!
- Tüm sonuçlar worker tarafından blackboard'a yazılacak

## 📁 KAYIT:
- step_name: "[step_adı]_[node_label]"
""")
```
</task_format>

<post_discovery_evaluation>
## 🔄 KEŞİF SONRASI DEĞERLENDİRME

KEŞİF tamamlandığında:

1. **`read_finding_dynamic` ile sonuçları OKU**
2. **Veritabanından dönen EXACT değerleri NOT AL**

⛔ **EXACT DEĞERLERİ KULLAN - TAHMİN ETME!**
```
❌ YANLIŞ: "ABC Ltd" (kısaltma/tahmin)
✅ DOĞRU: "ABC LİMİTED ŞİRKETİ" (veritabanından dönen EXACT değer)
```

3. **Varyasyonlar aranan entity ile eşleşiyor mu?**
   - EVET → Sonraki adıma geç
   - HAYIR → Yeni KEŞİF görevi ver (farklı terimler/node'lar ile)

4. **🔑 KÖK KELİME BELİRLE (METADATA için):**
   
   ⚠️ **ÖNCE KONTROL ET:** Bulunan varyasyonlar AYNI entity'ye mi ait?
   
   ```
   ✅ AYNI ENTITY:
   "ABC ENERJİ ÜRETİM A.Ş.", "ABC ENERJİ TİCARET LTD"
   → Ortak kök: "abc enerji"
   → Worker'a: toLower(n.name) CONTAINS 'abc enerji' kullan
   
   ❌ FARKLI ENTITY'LER:
   "ABC ENERJİ A.Ş.", "ABC TEMİZLİK LTD"
   → Bunlar farklı şirketler! Sadece "abc" kökü YANLIŞ olur!
   → Kullanıcının sorduğu entity'yi belirle, sadece O entity'nin varyasyonlarını kullan
   ```
   
   **Kök belirleme kuralı:**
   - Varyasyonların ANLAMLI ortak kısmını bul (sadece baş harf değil!)
   - Farklı sektör/tip içeriyorsa → FARKLI entity'ler, ayır!

5. **⛔ `<result>` bloğundaki uzun değerleri KESİNLİKLE YAZMA!**
   
   ```
   ❌ YASAK: <result>'taki uzun değerleri ASLA kopyalama!
   
   ✅ SADECE: <query>'deki filtreleme koşullarını kelime ile aktar
   ```
</post_discovery_evaluation>

