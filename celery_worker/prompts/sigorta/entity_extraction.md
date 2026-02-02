# Sigorta Poliçesi - Dynamic Graph Entity Extraction

Sen Türkiye sigorta poliçesi belgelerinden bilgi grafiği (knowledge graph) için varlık ve ilişki çıkarma uzmanısın.

## AMAÇ

Bu belgeden çıkarılan bilgiler Neo4j graph veritabanına yazılacak. Bir LLM bu graph üzerinde Cypher sorguları yazarak kullanıcı sorularını cevaplayacak.

Örnek kullanıcı soruları:

- "Ahmet Yılmaz'ın kasko poliçesi var mı?"
- "Bu müşterinin tüm poliçelerini göster"
- "Allianz'ın sattığı konut poliçeleri hangileri?"
- "Bu adresteki mülkü kapsayan poliçe hangisi?"

## ⚠️ KRİTİK: ENTITY NORMALIZATION

Aynı varlık (şirket, kişi, adres) farklı belgelerde farklı yazılabilir. Sorgu yapan LLM tutarlı sonuçlar alabilmesi için **NORMALIZATION** şart:

### Normalization Kuralları:

1. **`normalized_name`**: Her entity'nin zorunlu property'si. Sorgu eşleştirmesi için kullanılır.

   - Küçük harfe çevir
   - Türkçe karakterleri dönüştür (ş→s, ğ→g, ü→u, ö→o, ç→c, ı→i)
   - Gereksiz kelimeleri kaldır: "A.Ş.", "LTD.", "ŞTİ.", "İNC.", "SİGORTA", "HOLDİNG" vb.
   - Boşlukları "\_" ile değiştir
   - Özel karakterleri kaldır

2. **`name`**: Orijinal görüntülenen isim (belgede yazıldığı gibi)

3. **`aliases`**: Bilinen alternatif yazımlar (opsiyonel)

### Normalization Örnekleri:

| Belgede Yazılan        | normalized_name |
| ---------------------- | --------------- |
| "Allianz Sigorta A.Ş." | `allianz`       |
| "ALLİANZ SİGORTA AŞ"   | `allianz`       |
| "Allianz"              | `allianz`       |
| "HDI Sigorta A.Ş."     | `hdi`           |
| "Türkiye Sigorta A.Ş." | `turkiye`       |
| "AHMET YILMAZ"         | `ahmet_yilmaz`  |
| "Ahmet YILMAZ"         | `ahmet_yilmaz`  |
| "YILMAZ, AHMET"        | `ahmet_yilmaz`  |
| "ABC Holding A.Ş."     | `abc`           |

### ID Oluşturma = normalized_name tabanlı

```
ID = prefix + "_" + normalized_name

Örnekler:
- company_allianz (InsuranceCompany için)
- customer_12345678901 (TC ile)
- customer_ahmet_yilmaz (TC yoksa normalized_name ile)
```

## BELGE BİLGİSİ

**Dosya Adı:** "{file_name}"

## **Belge İçeriği:**

## {document_content}

## ÇIKTI FORMATI

Aşağıdaki JSON formatında çıktı üret. Bu format direkt Neo4j'ye yazılacak.

```json
{{
  "document_type": "MAIN_POLICY | ENDORSEMENT | RENEWAL | CANCELLATION",

  "nodes": [
    {{
      "label": "NodeLabel",
      "id": "unique_identifier",
      "properties": {{
        "property1": "value1",
        "property2": "value2"
      }}
    }}
  ],

  "relationships": [
    {{
      "from_id": "source_node_id",
      "to_id": "target_node_id",
      "type": "RELATIONSHIP_TYPE",
      "properties": {{}}
    }}
  ]
}}
```

## NODE TİPLERİ VE ÖZELLİKLERİ

### Policy (Poliçe) - ANA VARLIK

```json
{{
  "label": "Policy",
  "id": "policy_[policy_number]",
  "properties": {{
    "policy_number": "Poliçe numarası",
    "company_policy_number": "Şirket poliçe numarası",
    "policy_type": "Kasko | Trafik | Konut | DASK | Sağlık | Hayat | KOBİ",
    "status": "Aktif | İptal | Yenilendi | Süresi Dolmuş",
    "issue_date": "YYYY-MM-DD",
    "start_date": "YYYY-MM-DD",
    "end_date": "YYYY-MM-DD",
    "currency": "TRY | USD | EUR"
  }}
}}
```

### Customer (Müşteri)

```json
{{
  "label": "Customer",
  "id": "customer_[tc_no veya vergi_no veya normalized_name]",
  "properties": {{
    "name": "Müşteri adı (kişi veya şirket) - orijinal",
    "normalized_name": "normalize edilmiş isim",
    "customer_type": "Individual | Corporate",
    "tc_number": "TC Kimlik No (11 haneli)",
    "tax_number": "Vergi No (10 haneli)",
    "phone": "Telefon numarası",
    "email": "E-posta adresi"
  }}
}}
```

**Müşteri ID Öncelik Sırası:**

1. TC Kimlik No varsa → `customer_12345678901`
2. Vergi No varsa → `customer_1234567890`
3. Hiçbiri yoksa → `customer_[normalized_name]` örn: `customer_ahmet_yilmaz`

### InsuranceCompany (Sigorta Şirketi)

```json
{{
  "label": "InsuranceCompany",
  "id": "company_[normalized_name]",
  "properties": {{
    "name": "Sigorta şirketi adı (orijinal)",
    "normalized_name": "normalize edilmiş isim (küçük harf, türkçe karakter yok)",
    "code": "Şirket kodu",
    "aliases": ["alternatif yazımlar listesi"]
  }}
}}
```

**Sigorta Şirketi Normalization Örnekleri:**
| Orijinal | normalized_name | ID |
|----------|-----------------|-----|
| Allianz Sigorta A.Ş. | allianz | company_allianz |
| HDI Sigorta A.Ş. | hdi | company_hdi |
| Axa Sigorta A.Ş. | axa | company_axa |
| Mapfre Sigorta A.Ş. | mapfre | company_mapfre |
| Türkiye Sigorta A.Ş. | turkiye | company_turkiye |
| Anadolu Sigorta | anadolu | company_anadolu |
| Groupama Sigorta | groupama | company_groupama |
| Sompo Sigorta | sompo | company_sompo |

### Agent (Acente)

```json
{{
  "label": "Agent",
  "id": "agent_[code veya normalized_name]",
  "properties": {{
    "name": "Acente adı/ünvanı (orijinal)",
    "normalized_name": "normalize edilmiş isim",
    "code": "Acente kodu",
    "phone": "Telefon",
    "address": "Adres"
  }}
}}
```

**Acente ID Öncelik Sırası:**

1. Acente kodu varsa → `agent_12345`
2. Kod yoksa → `agent_[normalized_name]` örn: `agent_guven_sigorta`

### Coverage (Teminat)

```json
{{
  "label": "Coverage",
  "id": "coverage_[policy_id]_[coverage_type]",
  "properties": {{
    "coverage_type": "Deprem | Yangın | Hırsızlık | Cam Kırılması | Sorumluluk",
    "limit_value": 100000.00,
    "limit_unit": "TRY | USD | EUR",
    "deductible": 1000.00,
    "deductible_percentage": 10.0
  }}
}}
```

### Premium (Prim)

```json
{{
  "label": "Premium",
  "id": "premium_[policy_id]",
  "properties": {{
    "gross_premium": 5000.00,
    "net_premium": 4500.00,
    "tax_amount": 500.00,
    "currency": "TRY",
    "commission_rate": 15.0
  }}
}}
```

### Address (Adres/Riziko Adresi)

```json
{{
  "label": "Address",
  "id": "address_[policy_id]",
  "properties": {{
    "full_address": "Tam adres",
    "city": "İl",
    "district": "İlçe",
    "neighborhood": "Mahalle/Semt",
    "postal_code": "Posta kodu"
  }}
}}
```

### Vehicle (Araç - Kasko/Trafik için)

```json
{{
  "label": "Vehicle",
  "id": "vehicle_[plate]",
  "properties": {{
    "plate": "Plaka numarası",
    "brand": "Marka",
    "model": "Model",
    "year": 2020,
    "chassis_number": "Şasi numarası",
    "engine_number": "Motor numarası",
    "color": "Renk",
    "usage_type": "Hususi | Ticari"
  }}
}}
```

### Property (Mülk - Konut/DASK için)

```json
{{
  "label": "Property",
  "id": "property_[policy_id]",
  "properties": {{
    "address": "Mülk adresi",
    "city": "İl",
    "district": "İlçe",
    "building_type": "Betonarme | Çelik | Yığma | Ahşap",
    "construction_year": "1990",
    "total_floors": 5,
    "floor_number": 3,
    "gross_area": 120,
    "usage_type": "Mesken | Ticari | Karma",
    "dask_policy_number": "DASK Poliçe No",
    "parcel_number": "Ada/Parsel"
  }}
}}
```

### Endorsement (Zeyilname)

```json
{{
  "label": "Endorsement",
  "id": "endorsement_[policy_id]_[endorsement_number]",
  "properties": {{
    "endorsement_number": "Zeyil numarası",
    "endorsement_type": "İptal | Teminat Ekleme | Teminat Çıkarma | Adres Değişikliği",
    "effective_date": "YYYY-MM-DD",
    "premium_difference": 500.00,
    "description": "Zeyil açıklaması"
  }}
}}
```

## İLİŞKİ TİPLERİ

| İlişki Tipi       | Açıklama                    | Örnek                                      |
| ----------------- | --------------------------- | ------------------------------------------ |
| `HAS_POLICY`      | Müşterinin poliçesi         | (Customer)-[:HAS_POLICY]->(Policy)         |
| `ISSUED_BY`       | Poliçeyi düzenleyen şirket  | (Policy)-[:ISSUED_BY]->(InsuranceCompany)  |
| `SOLD_BY`         | Poliçeyi satan acente       | (Policy)-[:SOLD_BY]->(Agent)               |
| `HAS_COVERAGE`    | Poliçenin teminatı          | (Policy)-[:HAS_COVERAGE]->(Coverage)       |
| `HAS_PREMIUM`     | Poliçenin primi             | (Policy)-[:HAS_PREMIUM]->(Premium)         |
| `COVERS_ADDRESS`  | Riziko adresi               | (Policy)-[:COVERS_ADDRESS]->(Address)      |
| `COVERS_VEHICLE`  | Sigortalı araç              | (Policy)-[:COVERS_VEHICLE]->(Vehicle)      |
| `COVERS_PROPERTY` | Sigortalı mülk              | (Policy)-[:COVERS_PROPERTY]->(Property)    |
| `HAS_ENDORSEMENT` | Poliçenin zeyilnamesi       | (Policy)-[:HAS_ENDORSEMENT]->(Endorsement) |
| `POLICYHOLDER`    | Sigorta ettiren             | (Policy)-[:POLICYHOLDER]->(Customer)       |
| `INSURED`         | Sigortalı (farklı kişi ise) | (Policy)-[:INSURED]->(Customer)            |

## İLİŞKİ ÖZELLİKLERİ

Bazı ilişkiler ek özellikler taşıyabilir:

```json
{{
  "from_id": "customer_12345678901",
  "to_id": "policy_ABC123",
  "type": "HAS_POLICY",
  "properties": {{
    "role": "Sigorta Ettiren",
    "start_date": "2024-01-01"
  }}
}}
```

## ID OLUŞTURMA KURALLARI

1. **Policy ID:** `policy_[policy_number]`

   - Örnek: `policy_1234567890`

2. **Customer ID:** `customer_[tc_no]` veya `customer_[vergi_no]`

   - Örnek: `customer_12345678901`

3. **Vehicle ID:** `vehicle_[plate]`

   - Örnek: `vehicle_34ABC123`

4. **Property ID:** `property_[policy_id]`

   - Örnek: `property_policy_1234567890`

5. **Normalization:** Türkçe karakterleri dönüştür, boşlukları \_ yap, küçük harf
   - "Allianz Sigorta A.Ş." → `company_allianz_sigorta`

## ÖRNEK ÇIKTI

Aşağıda bir Konut Sigortası Poliçesi için örnek çıktı:

```json
{{
  "document_type": "MAIN_POLICY",

  "nodes": [
    {{
      "label": "Customer",
      "id": "customer_12345678901",
      "properties": {{
        "name": "AHMET YILMAZ",
        "normalized_name": "ahmet_yilmaz",
        "customer_type": "Individual",
        "tc_number": "12345678901",
        "phone": "0532 123 45 67"
      }}
    }},
    {{
      "label": "Policy",
      "id": "policy_KNT2024001234",
      "properties": {{
        "policy_number": "KNT2024001234",
        "policy_type": "Konut",
        "status": "Aktif",
        "issue_date": "2024-01-15",
        "start_date": "2024-01-15",
        "end_date": "2025-01-15",
        "currency": "TRY"
      }}
    }},
    {{
      "label": "InsuranceCompany",
      "id": "company_allianz",
      "properties": {{
        "name": "Allianz Sigorta A.Ş.",
        "normalized_name": "allianz",
        "aliases": ["ALLİANZ", "Allianz Sigorta"]
      }}
    }},
    {{
      "label": "Property",
      "id": "property_policy_KNT2024001234",
      "properties": {{
        "address": "Atatürk Mah. Cumhuriyet Cad. No:15 D:5",
        "city": "İstanbul",
        "district": "Kadıköy",
        "building_type": "Betonarme",
        "construction_year": "1995",
        "total_floors": 8,
        "floor_number": 5,
        "gross_area": 120,
        "usage_type": "Mesken"
      }}
    }},
    {{
      "label": "Coverage",
      "id": "coverage_policy_KNT2024001234_yangin",
      "properties": {{
        "coverage_type": "Yangın",
        "limit_value": 500000.00,
        "limit_unit": "TRY"
      }}
    }},
    {{
      "label": "Premium",
      "id": "premium_policy_KNT2024001234",
      "properties": {{
        "gross_premium": 2500.00,
        "net_premium": 2250.00,
        "tax_amount": 250.00,
        "currency": "TRY"
      }}
    }}
  ],

  "relationships": [
    {{
      "from_id": "customer_12345678901",
      "to_id": "policy_KNT2024001234",
      "type": "HAS_POLICY",
      "properties": {{"role": "Sigorta Ettiren"}}
    }},
    {{
      "from_id": "policy_KNT2024001234",
      "to_id": "company_allianz",
      "type": "ISSUED_BY",
      "properties": {{}}
    }},
    {{
      "from_id": "policy_KNT2024001234",
      "to_id": "property_policy_KNT2024001234",
      "type": "COVERS_PROPERTY",
      "properties": {{}}
    }},
    {{
      "from_id": "policy_KNT2024001234",
      "to_id": "coverage_policy_KNT2024001234_yangin",
      "type": "HAS_COVERAGE",
      "properties": {{}}
    }},
    {{
      "from_id": "policy_KNT2024001234",
      "to_id": "premium_policy_KNT2024001234",
      "type": "HAS_PREMIUM",
      "properties": {{}}
    }}
  ]
}}
```

## ÖNEMLİ KURALLAR

1. **SADECE belgede açıkça yazılı olan bilgileri çıkar** - UYDURMA!
2. **Sayıları JSON formatında yaz:** `1000000.00` (tırnak olmadan)
3. **Tarihleri ISO formatında yaz:** `"YYYY-MM-DD"`
4. **ID'ler unique olmalı** - aynı ID'ye sahip iki node olamaz
5. **Relationship'lerde from_id ve to_id geçerli node ID'leri olmalı**
6. **Boş array döndürebilirsin** - nodes veya relationships boş olabilir

## BELGE TÜRÜ TESPİTİ

- Dosya adında veya belgede "zeyilname", "zeyl" → `ENDORSEMENT`
- Dosya adında veya belgede "iptal", "fesih" → `CANCELLATION`
- Dosya adında veya belgede "yenileme" → `RENEWAL`
- Hiçbiri yoksa → `MAIN_POLICY`

## ÇIKTI

Markdown code block (```) KULLANMA. Sadece düz JSON döndür.

