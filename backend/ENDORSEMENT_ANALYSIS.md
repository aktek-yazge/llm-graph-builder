# Endorsement (Zeyilname) Analiz Raporu

## 🔍 Mevcut Durum Analizi

### 1. Endorsement Node Oluşturma - Uyumluluk Sorunu

**Sorun:** Loglarda Endorsement node oluşturuldu görünüyor (line 1454) ama sonra "Endorsement node was not created" hatası alınıyor (line 1494).

**Sebep:** 
- `node_check_query` (line 7062-7064) şu şekilde kontrol ediyor:
  ```cypher
  OPTIONAL MATCH (e:Endorsement)-[:HAS_ENDORSEMENT|HAS_RENEWAL|HAS_CANCELLATION]->(d)
  ```
- Ama `_create_endorsement_node` fonksiyonu (line 4569) şu relationship'i kullanıyor:
  ```cypher
  MERGE (e)-[r:DOCUMENTED_IN]->(d)
  ```
- **Uyumsuzluk:** Query `HAS_ENDORSEMENT` arıyor ama kod `DOCUMENTED_IN` kullanıyor!

### 2. Relationship Uyumsuzlukları

**Coverage Node:**
- Policy için: `(Policy)-[:HAS_COVERAGE]->(Coverage)` ✅
- Endorsement için: Aynı fonksiyon kullanılıyor ama Endorsement'a `applied_in` relationship'i olmalı
- **Sorun:** `_create_coverage_node` fonksiyonu sadece Policy için çalışıyor, Endorsement için `applied_in` relationship'i yok

**Date Node:**
- Endorsement için: `(Endorsement)-[:HAS_START_DATE|HAS_END_DATE]->(Date)` ✅
- Bu doğru çalışıyor

**CoverageType Node:**
- Endorsement için: `(CoverageType)-[:APPLIED_TO]->(Endorsement)` ✅
- Bu doğru çalışıyor

### 3. Prompt'ta Eksik Olan Önemli Bilgiler (Poliçe Görüntüsüne Göre)

Poliçe görüntüsünden çıkarılması gereken ama prompt'ta olmayan bilgiler:

#### A. Sigorta Bedeli ve Prim Bilgileri
- **Sigorta Bedeli** (Sigorta Bedeli): 526.190,00 TL
- **Poliçe Primi** (Poliçe Primi): 1.089,21 TL
- **Zeyil Sigorta Bedeli** (Zeyil Sigorta Bedeli): 0,00 TL
- **Zeyil Poliçe Primi** (Zeyil Poliçe Primi): 0,00 TL
- **Tarife Fiyatı** (Tarife Fiyatı): 0,000000

#### B. Sigortalanan Yer Bilgileri (Metadata)
- **İl/İlçe/Belde**: İSTANBUL / ÜSKÜDAR / MERKEZ
- **Adres**: MEHMET AKİF ERSOY Mah. ÇAMLICA YOLU Cad. NO:106 DR: 3
- **Tapu Bilgileri**:
  - Ada: 1151
  - Parsel: 56
  - Pafta: P.164
  - Tapu Bağımsız Bölüm No: (boş)
- **Bina Bilgileri**:
  - Bina İnşa Tarzı: ÇELİK, BETONARME
  - Bina İnşa Yılı: 1976 - 1999
  - Toplam Kat Sayısı: 01-03 ARASI
  - Daire Kullanım Şekli: MESKEN
  - Daire Yüzölçümü: 70 m²
- **Hasar Durumu**: HASARSIZ

#### C. Sigorta Şirketi ve Acente Bilgileri
- **Sigorta Şirketi**: HDI SİGORTA A.Ş.
- **Acente Adı**: 6532 DİNKAL SİGORTA ACENTELİĞİ A.Ş.
- **Acente Bilgileri**:
  - ADRES KODU: 1413448521
  - KOLAY HAT: 0850 222 8 434
  - Telefon: (212) 393 - 0111

#### D. Poliçe Numaraları
- **DASK Poliçe Numarası**: 80693665 00
- **Şirket Pol.No**: 165321005310 00
- **Poliçe Seri No**: 182597755
- **Ek Belge Numarası**: 01

#### E. Sigortalı ve Sigorta Ettiren Bilgileri
- **Sigortalının Adı Soyadı**: SÜRAHİ POLAT
- **Sigorta Ettirenin Adı**: SÜRAHİ POLAT
- **Sıfatı**: MAL SAHİBİ
- **TC Kimlik No**: 65*******50
- **İletişim Bilgileri**:
  - Cep Telefonu: (532)357-6173
  - Sabit Telefonu: (000)000-0000
  - İletişim Adresi: MEHMET AKİF ERSOY Mah. ÇAMLICA YOLU Cad. NO:106 DR:3 ÜSKÜDAR / İSTANBUL

#### F. Tarih Bilgileri
- **Tanzim Tarihi**: 7/10/2024
- **Başlangıç-Bitiş Tarihi**: 7/10/2024 - 7/10/2025
- **İndirim Tipi**: (boş)

#### G. Özel Durumlar
- **"BU POLİÇE PRİMSİZ BİR ZEYİLDİR"** - Bu önemli bir bilgi, zeyilname tipini belirliyor

## 📋 Önerilen Prompt İyileştirmeleri

### 1. Yeni Alanlar Eklenecek:

```json
{
  "policy": {
    "policyNumber": "...",
    "companyPolicyNumber": "...",  // Şirket Pol.No
    "daskPolicyNumber": "...",     // DASK Poliçe Numarası
    "policySerialNumber": "...",   // Poliçe Seri No
    "endorsementNumber": "...",    // Ek Belge Numarası
    "issueDate": "...",            // Tanzim Tarihi
    "currency": "...",
    "status": "...",
    "type": "..."
  },
  "insurance_amount": {            // YENİ
    "insuranceValue": 526190.00,   // Sigorta Bedeli
    "policyPremium": 1089.21,      // Poliçe Primi
    "endorsementInsuranceValue": 0.00,  // Zeyil Sigorta Bedeli
    "endorsementPremium": 0.00,    // Zeyil Poliçe Primi
    "tariffPrice": 0.00,           // Tarife Fiyatı
    "currency": "TL"
  },
  "insured_property": {            // YENİ - Sigortalanan Yer Bilgileri
    "city": "İSTANBUL",
    "district": "ÜSKÜDAR",
    "neighborhood": "MERKEZ",
    "address": "MEHMET AKİF ERSOY Mah. ÇAMLICA YOLU Cad. NO:106 DR: 3",
    "deed": {
      "ada": "1151",
      "parsel": "56",
      "pafta": "P.164",
      "independentSectionNumber": null
    },
    "building": {
      "constructionType": "ÇELİK, BETONARME",
      "constructionYear": "1976 - 1999",
      "totalFloors": "01-03 ARASI",
      "usageType": "MESKEN",
      "area": 70,
      "areaUnit": "m²"
    },
    "damageStatus": "HASARSIZ"     // Hasar Durumu
  },
  "insurance_company": {
    "name": "...",
    "responsible_person": "...",
    "agency": {                     // YENİ
      "name": "6532 DİNKAL SİGORTA ACENTELİĞİ A.Ş.",
      "addressCode": "1413448521",
      "easyLine": "0850 222 8 434",
      "phone": "(212) 393 - 0111"
    }
  },
  "insured_person": {              // YENİ - Sigortalı Bilgileri
    "name": "...",
    "nationality": "T.C.",
    "tcIdentityNumber": "...",
    "mobilePhone": "...",
    "landlinePhone": "...",
    "email": "...",
    "contactAddress": "..."
  },
  "policyholder": {                 // YENİ - Sigorta Ettiren Bilgileri
    "name": "...",
    "nationality": "T.C.",
    "tcIdentityNumber": "...",
    "mobilePhone": "...",
    "landlinePhone": "...",
    "email": "...",
    "role": "MAL SAHİBİ"           // Sıfatı
  },
  "endorsement_special": {         // YENİ - Zeyilname Özel Durumları
    "isPremiumFree": true,         // "BU POLİÇE PRİMSİZ BİR ZEYİLDİR" kontrolü
    "discountType": null            // İndirim Tipi
  }
}
```

## 🔧 Kod Düzeltmeleri Gereken Yerler

### 1. Endorsement-Document Relationship
**Dosya:** `graphDB_dataAccess.py`
**Satır:** 4569
**Sorun:** `DOCUMENTED_IN` kullanılıyor ama query `HAS_ENDORSEMENT` arıyor
**Çözüm:** Endorsement için `HAS_ENDORSEMENT` relationship'i kullanılmalı

### 2. Coverage-Endorsement Relationship
**Dosya:** `graphDB_dataAccess.py`
**Satır:** 4382, 5338
**Sorun:** `_create_coverage_node` sadece Policy için `HAS_COVERAGE` kullanıyor
**Çözüm:** Endorsement için `applied_in` relationship'i eklenmeli

### 3. Node Check Query
**Dosya:** `score.py`
**Satır:** 7062-7064
**Sorun:** Query `HAS_ENDORSEMENT` arıyor ama kod `DOCUMENTED_IN` kullanıyor
**Çözüm:** Query veya kod uyumlu hale getirilmeli

## 📊 Öncelik Sırası

1. **YÜKSEK ÖNCELİK:**
   - Endorsement-Document relationship uyumsuzluğu (node check hatası)
   - Coverage-Endorsement `applied_in` relationship eksikliği

2. **ORTA ÖNCELİK:**
   - Sigorta bedeli ve prim bilgileri prompt'a eklenmeli
   - Sigortalanan yer bilgileri (metadata) prompt'a eklenmeli

3. **DÜŞÜK ÖNCELİK:**
   - Acente bilgileri
   - Sigortalı/Sigorta ettiren detaylı bilgileri
   - Tapu bilgileri

