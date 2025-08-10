# QA Tabanlı Entity Çıkarma Sistemi

## Genel Bakış

Geleneksel LLM tabanlı entity çıkarma yöntemi genellikle çok fazla gereksiz bilgi üretir. Bu yeni QA (Soru-Cevap) tabanlı yaklaşım, LLM'ye belgeden spesifik sorular sorarak daha odaklı ve alakalı entity'ler çıkarmayı hedefler.

## Sorun

Mevcut sistem şu problemlerle karşılaşıyor:

- ❌ Çok fazla gereksiz entity üretimi
- ❌ Belirsiz ve alakasız ilişkiler
- ❌ Genel amaçlı çıkarım nedeniyle bilgi kirliliği
- ❌ Domain'e özgü olmayan sonuçlar

## QA Tabanlı Çözüm

### Nasıl Çalışır?

1. **Soru Oluşturma**: LLM'ye belgeden çıkarılabilecek soru-cevap çiftleri oluşturması istenir
2. **Filtreleme**: Sadece anlamlı cevaplara sahip sorular seçilir
3. **Entity Çıkarma**: Soru-cevap çiftlerinden spesifik entity'ler çıkarılır
4. **İlişki Kurma**: Entity'ler arası mantıklı ilişkiler belirlenir

### Avantajlar

- ✅ **Odaklı Çıkarım**: Sadece soru-cevaplarda geçen bilgiler entity olur
- ✅ **Domain Desteği**: Insurance, legal, financial domain'lerine özel sorular
- ✅ **Özel Sorular**: Kullanıcı kendi sorularını tanımlayabilir
- ✅ **Az Gürültü**: Gereksiz bilgi üretimi minimuma iner
- ✅ **Yüksek Kalite**: Alakalı ve anlamlı entity'ler

## Kullanım

### 1. API Endpoint

```bash
POST /qa_extract
```

**Parametreler:**

- `file_name`: İşlenecek local file adı
- `model`: LLM model adı (örn: "openai-gpt-4o-mini")
- `domain`: Belge domain'i ("insurance", "legal", "financial", "general")
- `custom_questions`: Özel sorular (JSON formatında)
- `uri`, `userName`, `password`, `database`: Neo4j bağlantı bilgileri

### 2. Python API

```python
from src.qa_based_entity_extractor import QABasedEntityExtractor

# Extractor oluştur
extractor = QABasedEntityExtractor("openai-gpt-4o-mini")

# Entity'leri çıkar
graph_documents = await extractor.extract_entities_from_qa(
    document_chunks=chunks,
    file_name="dosya.pdf",
    custom_questions=sorular  # opsiyonel
)
```

### 3. Domain Desteği

#### Insurance Domain Örneği (Konut Poliçesi)

```python
insurance_questions = {
    "policy_basic_questions": [
        "Poliçe numarası nedir?",
        "Sigorta şirketi hangisidir?",
        "Poliçe sahibi/sigortalı kimdir?",
        "Tanzim tarihi ve yeri nedir?",
        "Poliçe başlangıç ve bitiş tarihleri nelerdir?"
    ],
    "coverage_questions": [
        "Hangi teminatlar sağlanmaktadır?",
        "Sigorta bedelleri nelerdir?",
        "Deprem teminatı var mı, oranı nedir?"
    ],
    "financial_questions": [
        "Net prim tutarı nedir?",
        "Brüt prim tutarı nedir?",
        "Taksit tutarları ve tarihleri nelerdir?"
    ],
    "property_questions": [
        "Riziko adresi nerededir?",
        "Bina özellikleri nelerdir (m², kat, daire)?",
        "Yapı tarzı nasıldır?"
    ]
}
```

#### Özel Sorular Örneği:

```python
custom_questions = {
    "kişi_bilgileri": [
        "Bu belgede hangi kişiler geçmektedir?",
        "TC kimlik numaraları nelerdir?"
    ],
    "tarih_bilgileri": [
        "Hangi tarihler belirtilmiştir?",
        "Başlangıç ve bitiş tarihleri nelerdir?"
    ]
}
```

## Dosya Yapısı

```
backend/src/
├── qa_based_entity_extractor.py    # Ana QA extractor sınıfı
├── llm.py                          # QA fonksiyonları ve domain tespiti
└── test_qa_based_extraction.py     # Test dosyası

backend/
├── score.py                        # API endpoint (/qa_extract)
└── test_qa_based_extraction.py     # Kapsamlı test paketi
```

## Test Etme

```bash
cd backend
python test_qa_based_extraction.py
```

Bu test şunları kontrol eder:

- Domain otomatik tespiti
- QA extractor oluşturma
- Domain'e özel soru setleri
- Entity çıkarma kalitesi
- Özel sorular ile test
- Geleneksel yöntem ile karşılaştırma

## Yapılandırma

### Desteklenen Entity Türleri

QA extractor şu entity türlerini destekler:

- `Person` - Kişi isimleri ("Ayça Dinçkök")
- `Company` - Firma/kuruluş isimleri ("Doğa Sigorta A.Ş.")
- `PolicyNumber` - Poliçe numaraları ("65789885")
- `IdentityNumber` - TC kimlik, vergi numarası ("415**\***480")
- `PhoneNumber` - Telefon numaraları ("0532\*\*\*\*112")
- `Address` - Adresler ("Galata Residence Daire No:3")
- `Date` - Spesifik tarihler ("12.02.2020")
- `Amount` - Tutarlar, ücretler ("350.000,00 TL")
- `Percentage` - Yüzde oranları ("10%")
- `BuildingInfo` - Bina bilgileri ("112 m²", "Tam Kagir")
- `CoverageType` - Teminat türleri ("Bina", "Yangın Mali Sorumluluk")
- `AgentCode` - Acente kodları ("302113")
- `UAVTCode` - UAVT kodları ("2321812485")
- `InstallmentInfo` - Taksit bilgileri ("12.03.2020 - 89,00 TL")

### Domain'ler

- `insurance` - Sigorta poliçeleri
- `legal` - Hukuki dökümanlar
- `financial` - Finansal belgeler
- `general` - Genel amaçlı

## Gelecek Geliştirmeler

- [ ] Web interface entegrasyonu
- [ ] Daha fazla domain desteği (medical, real estate)
- [ ] A/B test framework'u
- [ ] Performans metrikleri
- [ ] Batch processing desteği
- [ ] Özel entity türleri tanımlama

## Katkıda Bulunma

QA tabanlı yaklaşımı geliştirmek için:

1. Yeni domain'ler için soru setleri ekleyin
2. Entity türlerini genişletin
3. Test case'leri artırın
4. Performance optimizasyonları yapın

---

> **Not**: Bu sistem sadece local file'lar için optimize edilmiştir. S3 ve GCS desteği gelecek sürümlerde eklenecektir.

