# Neo4j Schema Analiz Raporu ve Öneriler

## 📊 Mevcut Schema Yapısı

### Node'lar ve Property'leri:

1. **Policy Node:**
   - Properties: `id`, `policyNumber`, `currency`, `status`, `type`, `extraction_method`, `source_file`, `createdAt`, `updatedAt`
   - Relationships:
     - `HAS_COVERAGE` -> Coverage
     - `HAS_PREMIUM` -> Premium
     - `HAS_CLAUSE` -> Clause
     - `HAS_ADDRESS` -> RiskAddress
     - `HAS_GUARANTEE` -> Guarantee
     - `HAS_START_DATE` / `HAS_END_DATE` -> Date
     - `ISSUED_BY` -> InsuranceCompany
     - `DOCUMENTED_IN` -> Document
     - `FIRST_ENDORSEMENT` -> Endorsement

2. **Endorsement Node:**
   - Properties: `id`, `name`, `document_type`, `endorsement_type`, `effective_date`, `extraction_method`, `createdAt`
   - Relationships (MEVCUT):
     - `HAS_START_DATE` / `HAS_END_DATE` -> Date ✅
     - `DOCUMENTED_IN` -> Document ✅
   - Relationships (EKSİK - Policy'de var ama Endorsement'ta yok):
     - `HAS_COVERAGE` -> Coverage ❌
     - `HAS_PREMIUM` -> Premium ❌
     - `HAS_CLAUSE` -> Clause ❌
     - `HAS_ADDRESS` -> RiskAddress ❌
     - `HAS_GUARANTEE` -> Guarantee ❌
   - Relationships (VAR ama ters yön):
     - `APPLIED_TO` <- CoverageType ✅ (CoverageType -> Endorsement)

3. **Coverage Node:**
   - Properties: `id`, `limit_value`, `limit_unit`, `limit_count`, `scope`, `createdAt`
   - Purpose: Teminat limitleri ve kapsamı (sayısal değerler)

4. **CoverageType Node:**
   - Properties: `name`, `createdAt`, `updatedAt`
   - Purpose: Teminat türleri (Deprem, Yangın, vb.)
   - Relationships:
     - `APPLIED_TO` -> Policy
     - `APPLIED_TO` -> Endorsement

## 🔍 Analiz Sonuçları

### 1. Coverage ve CoverageType Ayrı Olması - GEREKLİ ✅

**Sebep:**
- **Coverage**: Sayısal değerler (limit_value, limit_unit, limit_count, scope) - bir poliçe/zeyilname için tek bir Coverage node'u
- **CoverageType**: Kategorik bilgiler (Deprem, Yangın, vb.) - bir poliçe/zeyilname için birden fazla CoverageType olabilir
- **Örnek:** Bir DASK poliçesi için:
  - 1 Coverage node: limit_value=526190, limit_unit=TL
  - 1+ CoverageType node: "Deprem", "Yangın", vb.

**Sonuç:** Ayrı kalmalılar, yapı doğru.

### 2. Endorsement'ın Eksik Relationship'leri - KRİTİK SORUN ❌

**Sorun:** Endorsement node'u Policy gibi aynı bilgilere sahip olmalı çünkü zeyilname bazı bilgileri güncelleyebilir.

**Eksik Relationship'ler:**
1. `(Endorsement)-[:HAS_COVERAGE]->(Coverage)` - Zeyilname teminat limitlerini güncelleyebilir
2. `(Endorsement)-[:HAS_PREMIUM]->(Premium)` - Zeyilname primi değişebilir
3. `(Endorsement)-[:HAS_CLAUSE]->(Clause)` - Zeyilname yeni klozlar ekleyebilir
4. `(Endorsement)-[:HAS_ADDRESS]->(RiskAddress)` - Zeyilname adres bilgilerini güncelleyebilir
5. `(Endorsement)-[:HAS_GUARANTEE]->(Guarantee)` - Zeyilname garanti bilgilerini güncelleyebilir

**Mevcut Kod:**
- `create_endorsement_entity` fonksiyonu bu node'ları oluşturuyor (line 4374-4414)
- Ama `_create_coverage_node`, `_create_premium_node`, vb. fonksiyonları sadece Policy için çalışıyor
- Endorsement için bu relationship'ler oluşturulmuyor!

### 3. Endorsement-Document Relationship Uyumsuzluğu - KRİTİK SORUN ❌

**Sorun:**
- Kod: `(Endorsement)-[:DOCUMENTED_IN]->(Document)` kullanıyor
- Query: `(Endorsement)-[:HAS_ENDORSEMENT]->(Document)` arıyor
- **Uyumsuzluk:** Query bulamıyor, "Endorsement node was not created" hatası veriyor

## 📋 Önerilen Çözümler

### 1. Endorsement Relationship'lerini Düzelt

**A. Coverage-Endorsement:**
- `_create_coverage_node` fonksiyonunu güncelle
- Policy için: `(Policy)-[:HAS_COVERAGE]->(Coverage)`
- Endorsement için: `(Endorsement)-[:HAS_COVERAGE]->(Coverage)`

**B. Diğer Relationship'ler:**
- `_create_premium_node`, `_create_clause_nodes`, `_create_risk_address_node`, `_create_guarantee_nodes` fonksiyonlarını güncelle
- Policy veya Endorsement ID'sine göre doğru relationship'i oluştur

**C. Endorsement-Document:**
- İki seçenek:
  1. Query'yi değiştir: `DOCUMENTED_IN` kullan
  2. Kodu değiştir: `HAS_ENDORSEMENT` kullan (daha mantıklı - Endorsement için özel relationship)

### 2. Prompt Güncellemeleri

**Yeni Alanlar Eklenecek:**
1. **Sigorta Bedeli ve Prim Bilgileri** (insurance_amount)
2. **Sigortalanan Yer Bilgileri** (insured_property) - bina, tapu, hasar durumu
3. **Acente Bilgileri** (insurance_company.agency)
4. **Poliçe Numaraları** (policy.policyNumber, companyPolicyNumber, daskPolicyNumber, vb.)
5. **Sigortalı/Sigorta Ettiren Detayları** (insured_person, policyholder)
6. **Zeyilname Özel Durumları** (endorsement_special.isPremiumFree)

## 🎯 Öncelik Sırası

1. **YÜKSEK ÖNCELİK (Kritik Hatalar):**
   - Endorsement-Document relationship uyumsuzluğu
   - Endorsement için Coverage, Premium, Clause, Address, Guarantee relationship'leri eksik

2. **ORTA ÖNCELİK (Önemli Bilgiler):**
   - Prompt'a sigorta bedeli ve prim bilgileri ekle
   - Prompt'a sigortalanan yer bilgileri ekle

3. **DÜŞÜK ÖNCELİK (Detaylar):**
   - Acente bilgileri
   - Sigortalı/Sigorta ettiren detayları
   - Tapu bilgileri

## 🔧 Kod Düzeltmeleri Detayları

### 1. Fonksiyonların Endorsement Desteği

**Sorun:** Tüm `_create_*_node` fonksiyonları sadece Policy için çalışıyor:
- `_create_premium_node` (line 5289): `MATCH (p:Policy {id: $policy_id})`
- `_create_coverage_node` (line 5338): `MATCH (p:Policy {id: $policy_id})`
- `_create_clause_nodes` (line 5470): `MATCH (p:Policy {id: $policy_id})`
- `_create_guarantee_nodes` (line 5423): `MATCH (p:Policy {id: $policy_id})`
- `_create_risk_address_node` (line 5643): `MATCH (p:Policy {id: $policy_id})`

**Çözüm:** 
- Fonksiyonlara `node_type` parametresi ekle ("Policy" veya "Endorsement")
- Query'de dinamik olarak doğru node tipini kullan
- Endorsement için aynı relationship'leri oluştur

### 2. Endorsement-Document Relationship

**Sorun:** 
- Kod: `(Endorsement)-[:DOCUMENTED_IN]->(Document)` ✅ (Doğru)
- Query: `(Endorsement)-[:HAS_ENDORSEMENT]->(Document)` arıyor ❌ (Yanlış)

**Açıklama:**
- `DOCUMENTED_IN`: Hem Policy hem Endorsement için hangi Document'ta dokümante edildiğini gösterir
- `HAS_ENDORSEMENT`: Policy node'unun bir Endorsement node'una bağlantısını temsil eder (Policy → Endorsement)
- `FIRST_ENDORSEMENT`: Policy'den Endorsement'a bağlantı (zincir başlangıcı)

**Çözüm:** 
- Query'yi düzelt: `(Endorsement)-[:DOCUMENTED_IN]->(Document)` kullan
- `HAS_ENDORSEMENT` Policy'den Endorsement'a olan bağlantı için kullanılır, Document'a değil

