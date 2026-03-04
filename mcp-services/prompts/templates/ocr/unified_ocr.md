# Görev

Bu gazete sayfasından **{target_company}** şirketinin ilanını oku ve bilgi grafiği için yapılandırılmış veri çıkar.

Hedef şirket bu sayfada yoksa: `{{"target_company": "{target_company}", "found": false}}`

---

## Sayfa Bilgisi

Sayfa: {page_number}/{total_pages}
{continuation_context}

---

## Mevcut Graf Şeması

Aşağıda mevcut bilgi grafiğindeki node tipleri, property'leri ve ilişki pattern'leri yer alıyor.

**ÖNEMLİ**: 
- Aynı entity için mevcut ID'yi kullan
- Yeni entity için aynı label ve ID pattern'ini izle
- Mevcut property isimlerini kullan

{graph_schema}

---

## Çıktı

Aşağıdaki JSON formatında yanıt ver:

```json
{{
  "found": true,
  "document_type": "string (ilanın türü)",
  "target_company": "string (hedef şirket adı)",
  
  "chunks": [
    {{
      "id": "chunk_001",
      "text": "string (chunk içeriği - tam cümleler, anlam bütünlüğü)",
      "position": 1,
      "page": {page_number}
    }}
  ],
  
  "nodes": [
    {{
      "label": "string (mevcut şemadaki label kullan)",
      "id": "string (mevcut ID pattern'i kullan)",
      "properties": {{
        "name": "string (zorunlu)",
        "...": "diğer özellikler"
      }},
      "chunk_ids": ["chunk_001"]
    }}
  ],
  
  "relationships": [
    {{
      "from_id": "string (kaynak node ID)",
      "to_id": "string (hedef node ID)",
      "type": "string (mevcut relationship type kullan)",
      "properties": {{}}
    }}
  ]
}}
```
