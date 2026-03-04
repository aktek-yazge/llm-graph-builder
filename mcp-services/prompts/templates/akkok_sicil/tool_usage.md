## ARACLARIN

### execute_cypher_query
Graph'ta dogrudan Cypher sorgusu calistir.

**Kullanim Alanlari:**
- Sirket bilgisi sorgulama
- Yonetim kurulu uyelerini listeleme
- Ortaklik iliskilerini bulma
- Belge icerigi arama

**Ornek Sorgular:**

```cypher
// Sirket bilgisi
MATCH (s:Sirket {unvan: "AK-PA TEKSTIL"})
RETURN s.unvan, s.sicil_no, s.kurulus_tarihi

// Yonetim kurulu
MATCH (s:Sirket)-[:YONETIM_KURULU]->(y:YonetimUyesi)
WHERE s.unvan CONTAINS "AK-PA"
RETURN y.ad_soyad, y.gorev, y.baslangic_tarihi

// Ortaklik yapisi
MATCH (h:Sirket)-[o:ORTAK]->(s:Sirket)
WHERE h.unvan CONTAINS "AKKOK"
RETURN h.unvan, o.pay_orani, s.unvan
```

### execute_cypher_query_with_embedding
Semantik arama yap. Benzer kavramlari bulur.

### add_source
Kaynak belge ekle (PDF/gazete referansi).

### read_finding
Onceki bulgularini oku.
